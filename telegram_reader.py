"""Optional isolated MTProto downloader; no account messaging operations."""
import json
import os
import signal
import subprocess
import tempfile
from pathlib import Path
from common import STATE

ROOT=Path(__file__).resolve().parent
CREDENTIALS=STATE/'telegram-reader'/'config.json'

def ready():
    try:
        return json.loads(CREDENTIALS.read_text()).get('enabled') is True
    except (OSError,ValueError):
        return False

def download(clip):
    from goal_videos import VideoError,MAX_VIDEO
    python=STATE/'video-tools-venv'/'bin'/'python'
    if not ready() or not python.is_file():
        raise VideoError('telegram_reader_not_configured')
    with tempfile.TemporaryDirectory(prefix='telegram-media-',dir=STATE) as folder:
        output=Path(folder)/'video.mp4'
        payload=dict(channel=clip.channel,post_id=clip.post_id,text=clip.text,timestamp=clip.timestamp,output=str(output.resolve()))
        try:
            process=subprocess.Popen([str(python),str(ROOT/'telegram_reader_worker.py')],stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
            try:
                stdout,_=process.communicate(json.dumps(payload),timeout=480)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid,signal.SIGKILL)
                process.communicate()
                raise
        except (OSError,subprocess.TimeoutExpired):
            raise VideoError('telegram_reader_timeout') from None
        if process.returncode:
            # Never surface raw exceptions, credentials, or Telegram session data.
            code=stdout.strip()
            allowed={'reader_unauthorized','reader_busy','reader_flood_wait','reader_changed_post',
                     'reader_unsupported_media','reader_media_too_large','reader_failed'}
            raise VideoError(code if code in allowed else 'reader_failed')
        if not output.is_file() or not 12<=output.stat().st_size<=MAX_VIDEO:
            raise VideoError('reader_media_too_large')
        data=output.read_bytes()
        if data[4:8]!=b'ftyp': raise VideoError('not_mp4')
        return data
