"""Root-owned fixed allowlist. Never restart active jobs or touch Telegram queues."""
import json,os,subprocess,time
from pathlib import Path

UNITS=('volodymyr-bot.service','bubus-desktop-worker.service','bubus-desktop-export.service')


def allowed(attempts,now):
    recent=[t for t in attempts if now-t<86400]
    return len(recent)<3 and (not recent or now-max(recent)>=3600)


def main():
    directory=Path('/var/lib/bubus-watchdog');directory.mkdir(mode=0o700,exist_ok=True)
    path=directory/'recovery.json'
    try:state=json.loads(path.read_text())
    except FileNotFoundError:state={}
    for unit in UNITS:
        status=subprocess.run(['/usr/bin/systemctl','is-active',unit],capture_output=True,text=True,timeout=10).stdout.strip()
        now=time.time();attempts=state.get(unit,[])
        if status!='failed' or not allowed(attempts,now):continue
        state[unit]=[t for t in attempts if now-t<86400]+[now]
        temp=directory/'recovery.tmp'
        with temp.open('w') as stream:
            json.dump(state,stream);stream.flush();os.fsync(stream.fileno())
        temp.chmod(0o600);os.replace(temp,path)
        subprocess.run(['/usr/bin/systemctl','reset-failed',unit],timeout=15,check=True)
        subprocess.run(['/usr/bin/systemctl','start','--no-block',unit],timeout=15,check=True)
        print('Recovery start requested:',unit,flush=True)


if __name__=='__main__':main()
