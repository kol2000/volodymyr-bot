"""Run bounded album work in its own process, independent of goal notifications."""
import logging
import os
import signal
import subprocess
import time

from common import ROOT, STATE
from goal_videos import KEY
from live_football import PREFIX

LOG = logging.getLogger('volodymyr')


def enabled(store, allowed, now):
    chat = store.get('chat_id')
    return (type(chat) is int and allowed(chat) and store.get(PREFIX+'enabled', False)
            and store.get(KEY+'enabled', True) and now >= store.get('blocked_until', 0))


def poll(store, allowed):
    if not enabled(store, allowed, time.time()):
        return
    python = STATE / 'video-tools-venv/bin/python'
    if not python.is_file():
        LOG.warning('Альбомы: среда обработки видео не установлена')
        return
    process = subprocess.Popen([str(python), '-u', str(ROOT/'attach_match_album.py'), '--auto'],
                               cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               start_new_session=True)
    try:
        output, _ = process.communicate(timeout=900)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()
        LOG.warning('Альбомы: превышен срок обработки; неподтверждённая отправка не повторяется')
        return
    # The child emits fixed diagnostics/public post IDs, never signed URLs or tokens.
    for line in output.decode('utf-8', 'replace').splitlines()[-15:]:
        LOG.info('Альбомы: %s', line[:500])
    if process.returncode:
        LOG.warning('Альбомы: обработчик завершился с кодом %s', process.returncode)
