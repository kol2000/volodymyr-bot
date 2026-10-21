"""Local configuration and Telegram HTTP client. No third-party dependencies."""
import json
import contextlib
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = ROOT / 'state'
CONFIG = STATE / 'config.json'
MODEL = 'qwen3:30b-a3b-instruct-2507-q4_K_M'


class HTMLMessage(str):
    """Explicitly formatted text produced by a trusted local template."""


class APIError(Exception):
    def __init__(self, message, code=0, retry_after=0):
        super().__init__(message)
        self.code = code
        self.retry_after = retry_after


def http_json(url, payload, timeout=60):
    body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    request = urllib.request.Request(url, data=body,
                                     headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        # Never print an exception URL: Telegram URLs contain the bot token.
        code = error.code
        try:
            body = json.loads(error.read())
            wait = body.get('parameters', {}).get('retry_after', 0)
        except (ValueError, AttributeError):
            wait = 0
        raise APIError(f'HTTP {code}', code, wait) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise APIError('Сетевая ошибка или тайм-аут') from None
    except ValueError:
        raise APIError('Некорректный JSON от сервера') from None


def http_video_edit(url, chat_id, message_id, video, caption):
    """Bounded multipart edit; URLs and exception bodies may contain credentials."""
    if not isinstance(video, bytes) or not 12 <= len(video) <= 45 * 1024 * 1024 or video[4:8] != b'ftyp':
        raise APIError('Некорректное видео')
    if len(caption.encode('utf-16-le')) // 2 > 1024:
        raise APIError('Слишком длинная подпись')
    boundary = 'bubus' + secrets.token_hex(16)
    fields = {'chat_id': str(chat_id), 'message_id': str(message_id),
              'media': json.dumps({'type': 'video', 'media': 'attach://clip',
                                   'caption': caption, 'supports_streaming': True}, ensure_ascii=False)}
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
             for key, value in fields.items()]
    parts.extend([f'--{boundary}\r\nContent-Disposition: form-data; name="clip"; filename="goal.mp4"\r\n'
                  'Content-Type: video/mp4\r\n\r\n'.encode(), video,
                  f'\r\n--{boundary}--\r\n'.encode()])
    request = urllib.request.Request(url, data=b''.join(parts), headers={
        'Content-Type': 'multipart/form-data; boundary=' + boundary})
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        try:
            retry = json.loads(error.read()).get('parameters', {}).get('retry_after', 0)
        except (ValueError, AttributeError):
            retry = 0
        raise APIError(f'HTTP {error.code}', error.code, retry) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise APIError('Сетевая ошибка или тайм-аут') from None
    except ValueError:
        raise APIError('Некорректный JSON от сервера') from None
    if not isinstance(result, dict) or not result.get('ok'):
        result = result if isinstance(result, dict) else {}
        raise APIError('Добавление видео не подтверждено', result.get('error_code', 0),
                       result.get('parameters', {}).get('retry_after', 0))
    if 'result' not in result:
        raise APIError('Некорректный ответ при добавлении видео')
    return result['result']


class Telegram:
    def __init__(self, token):
        self.base = f'https://api.telegram.org/bot{token}/'
        self.send_lock = threading.Lock()
        self.last_send = 0

    def call(self, method, **payload):
        result = http_json(self.base + method, payload, timeout=60)
        if not result.get('ok'):
            raise APIError(f'Telegram API {result.get("error_code", "error")}',
                           result.get('error_code', 0),
                           result.get('parameters', {}).get('retry_after', 0))
        return result['result']

    def send(self, chat_id, text, reply_to=None):
        with self.send_lock:
            delay = 1.1 - (time.monotonic() - self.last_send)
            if delay > 0:
                time.sleep(delay)
            payload = {'chat_id': chat_id, 'text': text,
                       'link_preview_options': {'is_disabled': True}}
            if isinstance(text, HTMLMessage):
                payload['parse_mode'] = 'HTML'
            if reply_to:
                payload['reply_parameters'] = {'message_id': reply_to,
                                               'allow_sending_without_reply': True}
            self.last_send = time.monotonic()
            # Deliberately no blind retry: a timeout can mean Telegram accepted it.
            return self.call('sendMessage', **payload)

    def edit_video(self, chat_id, message_id, video, caption):
        with self.send_lock:
            delay = 1.1 - (time.monotonic() - self.last_send)
            if delay > 0:
                time.sleep(delay)
            self.last_send = time.monotonic()
        # Reserve request spacing, but do not hold the text-send lock throughout
        # a potentially slow video upload.
        return http_video_edit(self.base + 'editMessageMedia', chat_id, message_id, video, caption)

    @contextlib.contextmanager
    def typing(self, chat_id):
        stopped = threading.Event()
        def refresh():
            while not stopped.is_set():
                try:
                    http_json(self.base + 'sendChatAction', {'chat_id': chat_id, 'action': 'typing'}, timeout=5)
                except APIError:
                    return
                if stopped.wait(4):
                    return
        thread = threading.Thread(target=refresh, daemon=True)
        thread.start()
        try:
            yield
        finally:
            stopped.set()
            thread.join(timeout=0.1)


def clean_text(text):
    text = re.sub(r'\b\d{6,12}:[A-Za-z0-9_-]{25,}\b', '[токен]', text)
    text = re.sub(r'https?://\S+', '[ссылка]', text)
    text = re.sub(r'\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b', '[почта]', text)
    text = re.sub(r'(?<!\w)\+?\d[\d ()-]{8,}\d(?!\w)', '[номер]', text)
    return text


def surzhyk_text(text):
    """Enforce recurrent archive spellings without removing every soft sign."""
    text = text.translate(str.maketrans({'і': 'и', 'І': 'И', 'є': 'е', 'Є': 'Е'}))
    # Latin i can replace a Cyrillic letter in mixed spellings; English names
    # such as Linux, WiFi and iPhone must retain their original spelling.
    latin_i = str.maketrans({'i': 'и', 'I': 'И'})
    text = re.sub(r'(?<!\w)[а-яёїґi]+(?!\w)',
                  lambda m: m.group().translate(latin_i) if re.search(r'[а-яёїґ]', m.group(), re.I)
                  else m.group(), text, flags=re.I)
    text = re.sub(r'(?<!\S)i(?=$|[\s,.!?;:])', lambda m: m.group().translate(latin_i), text, flags=re.I)
    spellings = {'ты': 'ти', 'это': 'ето', 'ещё': 'ещо', 'еще': 'ещо',
                 'было': 'било', 'бы': 'би', 'сейчас': 'сечас',
                 'непонятного': 'непонятново'}

    def replace(match):
        original = match.group()
        word = original.lower()
        changed = spellings.get(word, word)
        if changed.endswith(('ешь', 'ишь')):
            changed = changed[:-1]
        if changed == word:
            return original
        if original.isupper():
            return changed.upper()
        return changed.capitalize() if original[0].isupper() else changed

    return re.sub(r'\b[а-яё]+\b', replace, text, flags=re.I)


def parody_text(text):
    """Archive speech in prose; preserve numbers, quotations and code literals."""
    parts = re.split(r'(```[\s\S]*?```|`[^`\n]+`|«[^»\n]*»|"[^"\n]*"|https?://\S+)', text)
    for index in range(0, len(parts), 2):
        prose = surzhyk_text(parts[index])
        def phrase(match):
            original = match.group()
            return 'БОЛЬЯ МЕНЬЯ' if original.isupper() else 'Болья менья' if original[0].isupper() else 'болья менья'
        prose = re.sub(r'\bболее[\s\-–—]+менее\b', phrase, prose, flags=re.I)
        prose = re.sub(
            r'\b(?:(?:большое|огромное|заранее)\s+)?спасибо'
            r'(?:\s+(?:тебе|вам))?(?:\s+за\s+(?:вопрос|ответ|помощь|оценку|уточнение|информацию|обращение|подсказку))?\b'
            r'|\b(?:пожалуйста|благодарю|благодарим|извините|извини|простите)\b'
            r'|\b(?:будьте\s+добры|будь\s+добр|рад\s+помочь|рады\s+помочь|буду\s+рад\s+помочь)\b',
            '', prose, flags=re.I)
        # A decimal comma is data, unlike a pause in ordinary speech.
        prose = re.sub(r'(?<!\d),|,(?!\d)', ' ', prose)
        prose = re.sub(r'[^\S\n]+', ' ', prose)
        prose = re.sub(r' +([.!?;:])', r'\1', prose)
        prose = re.sub(r'(?m)^ *[.!;:,]+ *', '', prose)
        parts[index] = prose
    return ''.join(parts).strip()


def load_config():
    with CONFIG.open(encoding='utf-8') as file:
        return json.load(file)
