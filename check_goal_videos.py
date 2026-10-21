#!/usr/bin/env python3
"""Read public source channels and optionally test one MP4 per channel. No Telegram sends."""
import argparse
import hashlib
from datetime import datetime

from common import STATE
from goal_videos import CHANNELS, VideoError, fetch_public, parse_channel
from live_football import MOSCOW


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--channel', choices=CHANNELS, help='Default: check both sources')
    parser.add_argument('--download-test', action='store_true', help='Download one clip per source to memory; send/save nothing')
    parser.add_argument('--replay-date', type=lambda s: datetime.strptime(s, '%Y-%m-%d').date(),
                        help='Replay confirmed bot goal posts for this Moscow kickoff date; never edit Telegram')
    parser.add_argument('--db', default=str(STATE / 'bot.sqlite3'), help='Bot SQLite path; replay opens it read-only')
    parser.add_argument('--pages', type=int, default=4, choices=range(1, 9), help='Replay: at most 1–8 public pages per source')
    parser.add_argument('--team', default='', help='Replay: team name filter')
    args = parser.parse_args()
    if args.replay_date:
        if args.channel:
            parser.error('--replay-date проверяет оба канала; убери --channel.')
        from replay_goal_videos import run_replay
        return run_replay(args.replay_date, args.db, args.pages, args.download_test, args.team)
    failed = False
    for channel in ([args.channel] if args.channel else CHANNELS):
        print(f'@{channel}:', flush=True)
        try:
            clips = parse_channel(fetch_public('https://t.me/s/' + channel), channel)
            print(f'  Публичная лента прочитана; отдельных видеопостов: {len(clips)}', flush=True)
            for clip in clips[:3]:
                date = datetime.fromtimestamp(clip.timestamp, MOSCOW).strftime('%d.%m %H:%M МСК')
                print(f'  {date} · {clip.link} · {" ".join(clip.text.split())[:140]}', flush=True)
            if args.download_test:
                if not clips:
                    failed = True
                    print('  Скачать нечего: в публичном превью нет отдельных доступных видео.', flush=True)
                else:
                    data = fetch_public(clips[0].url, video=True)
                    print(f'  MP4 получен: {len(data) / 1024 / 1024:.2f} МБ; SHA256 {hashlib.sha256(data).hexdigest()[:16]}', flush=True)
                    del data
        except VideoError as error:
            failed = True
            print(f'  Недоступно: {error}. Проверь доступ к t.me через VPN этой VM.', flush=True)
    print('Проверка завершена. Сообщения в Telegram не отправлялись; видео на диск не сохранялись.')
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
