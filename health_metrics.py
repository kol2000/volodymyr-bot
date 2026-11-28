"""Small non-sensitive heartbeat files. Telemetry must never break bot traffic."""
import json,os,sys,tempfile,time
from pathlib import Path


def atomic(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,name=tempfile.mkstemp(dir=path.parent,prefix='.health-')
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            json.dump(value,stream,ensure_ascii=False);stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)


def telegram(method,ok):
    if Path(sys.argv[0]).name!='bot.py':return
    category='receive' if method=='getUpdates' else 'send' if method in ('sendMessage','sendSticker','sendVideo','sendMediaGroup','editMessageMedia') else None
    if not category:return
    try:atomic(Path(__file__).resolve().parent/'state/health'/('telegram_'+category+'.json'),dict(checked=time.time(),ok=ok))
    except OSError:pass
