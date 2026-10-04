"""Local configuration and Telegram HTTP client. No third-party dependencies."""
import json
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = ROOT / 'state'
CONFIG = STATE / 'config.json'
MODEL = 'qwen3:30b-a3b-instruct-2507-q4_K_M'


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
            if reply_to:
                payload['reply_parameters'] = {'message_id': reply_to,
                                               'allow_sending_without_reply': True}
            self.last_send = time.monotonic()
            # Deliberately no blind retry: a timeout can mean Telegram accepted it.
            return self.call('sendMessage', **payload)


def clean_text(text):
    text = re.sub(r'\b\d{6,12}:[A-Za-z0-9_-]{25,}\b', '[токен]', text)
    text = re.sub(r'https?://\S+', '[ссылка]', text)
    text = re.sub(r'\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b', '[почта]', text)
    text = re.sub(r'(?<!\w)\+?\d[\d ()-]{8,}\d(?!\w)', '[номер]', text)
    return text


def load_config():
    with CONFIG.open(encoding='utf-8') as file:
        return json.load(file)
