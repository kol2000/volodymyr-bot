"""Nonblocking, verified-file bridge to the isolated official Desktop worker."""
import hashlib
import json
import os
from pathlib import Path
import re
import time

REQUESTS = Path('/var/lib/bubus-desktop-requests')
OUTPUT = Path('/var/lib/bubus-media/verified')


def ready():
    try:
        beat = json.loads((OUTPUT/'heartbeat.json').read_text())
        return (beat.get('version', 0) >= 3 and 0 <= time.time()-beat['checked'] < 600
                and os.access(REQUESTS, os.W_OK))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def download(clip):
    from goal_videos import VideoError, MAX_VIDEO
    if clip.channel not in ('matchpremier','idmanvideo') or not re.fullmatch(r'[1-9][0-9]{0,11}',str(clip.post_id)):
        raise VideoError('desktop_invalid_source')
    stem = f'{clip.channel}-{clip.post_id}'
    source = f'{clip.channel}/{clip.post_id}'
    video, meta = OUTPUT/(stem+'.mp4'), OUTPUT/(stem+'.json')
    try:
        if meta.exists():
            if meta.is_symlink() or video.is_symlink() or meta.stat().st_size > 5000:
                raise VideoError('desktop_invalid_manifest')
            proof = json.loads(meta.read_text())
            if (proof.get('verified_for_automatic_publish') is not True
                    or proof.get('source') != source
                    or proof.get('verified_link') != 'https://t.me/'+source):
                raise VideoError('desktop_unverified_video')
            if not 12 <= video.stat().st_size <= MAX_VIDEO:
                raise VideoError('desktop_invalid_size')
            data = video.read_bytes()
            if (len(data) != proof.get('size') or data[4:8] != b'ftyp'
                    or hashlib.sha256(data).hexdigest() != proof.get('sha256')):
                raise VideoError('desktop_file_changed')
            return data
        if not ready():
            raise VideoError('desktop_unavailable')
        status = OUTPUT/(stem+'.status.json')
        if status.exists():
            # Failed UI verification requires investigation, not blind retries.
            raise VideoError('desktop_check_required')
        target = REQUESTS/(stem+'.json')
        if not target.exists():
            temporary = target.with_suffix('.tmp')
            temporary.write_text(json.dumps(dict(source=source, caption=clip.text[:3000]),ensure_ascii=False), encoding='utf-8')
            temporary.chmod(0o640)
            temporary.replace(target)
    except (OSError, ValueError, TypeError):
        raise VideoError('desktop_bridge_error') from None
    raise VideoError('desktop_pending')
