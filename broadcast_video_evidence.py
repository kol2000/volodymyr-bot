from goal_caption_language import goal_evidence_text
"""Conservative scoreboard evidence for recent anonymous domestic goal clips."""
import hashlib,re,subprocess,tempfile
from pathlib import Path
from functools import lru_cache

# Broadcast abbreviations, including Latin lookalikes after OCR normalization.
TEAMS={'оренбург':('ОРЕ',),'балтика':('БАЛ','ВАЛ','ВАА','БАА'),'ростов':('РОС',),'крылья советов':('КС','КРС'),
'краснодар':('КРА',),'цска':('ЦСКА',),'зенит':('ЗЕН','3ЕН','SЕН'),'спартак':('СПА',),
'локомотив':('ЛОК',),'рубин':('РУБ',),'динамо':('ДИН',),'динамо махачкала':('ДМХ','МАХ'),
'ахмат':('АХМ',),'факел':('ФАК','ФКЛ','ФКА','ОКА'),'родина':('РОД',),'акрон':('АКР',),
'сочи':('СОЧ',),'торпедо':('ТОР',),'пари нн':('ПАР','НН'),'нижний новгород':('ПАР','НН')}
_TRANSLATE=str.maketrans('ABCEHKMOPTX','АВСЕНКМОРТХ')
_CACHE={}

def aliases(team):
    name=' '.join(team.casefold().replace('ё','е').split())
    if name=='динамо москва':name='динамо'
    return TEAMS.get(name,())

def candidate(clip,job,now):
    from goal_videos import domestic_broadcast,goal_match_reason
    if clip.channel!='matchpremier' or not domestic_broadcast(job.get('competition','')):return False
    if not 0<=clip.timestamp-job['created']<=1800 or now-clip.timestamp<45:return False
    if not all(aliases(job[k]) for k in ('home','away')):return False
    reason=goal_match_reason(clip,job,now)
    semantic=False
    if reason=='narrative_not_goal_clip':
        from goal_caption_semantics import confirms_goal
        semantic=confirms_goal(clip.text)
    if reason not in ('','insufficient_goal_identity','insufficient_narrative_identity') and not semantic:return False
    if re.search(r'интервью|эфир|размин|трениров|архив|вчера|обзор|повтор|разбор|отмен|офсайд|не засчит|почти|мимо|сейв|сэйв|анонс|реклам|не заб|не попал|спас',goal_evidence_text(clip.text),re.I) and not semantic:return False
    previous=(0,0)
    for g in job.get('match_goals',[]):
        score=tuple(g['score'])
        if tuple(a-b for a,b in zip(score,previous)) not in ((1,0),(0,1)):return False
        previous=score
    return bool(job.get('match_goals') and previous==tuple(job.get('match_score',())))

def read_frames(data):
    digest=hashlib.sha256(data).hexdigest()
    if digest in _CACHE:return _CACHE[digest]
    texts=[]
    try:
        with tempfile.TemporaryDirectory(prefix='bubus-scoreboard-') as tmp:
            root=Path(tmp);video=root/'clip.mp4';video.write_bytes(data)
            for second in (1,2,3,4,6,8):
                frame=root/'frame.png'
                subprocess.run(['ffmpeg','-v','error','-y','-ss',str(second),'-i',str(video),
                    '-vf','crop=iw*0.24:ih*0.17:0:0,scale=iw*4:ih*4','-frames:v','1',str(frame)],
                    check=True,capture_output=True,timeout=12)
                if not frame.exists():continue
                out=subprocess.run(['tesseract',str(frame),'stdout','-l','rus+eng','--psm','6'],
                    capture_output=True,text=True,check=True,timeout=12)
                texts.append(out.stdout)
    except (OSError,subprocess.SubprocessError):return []
    if len(_CACHE)>=32:_CACHE.pop(next(iter(_CACHE)))
    _CACHE[digest]=texts
    return texts

def evidence(texts,job):
    minute=re.fullmatch(r'(\d+)(?:\+(\d+))?[^\d]*',job['goal']['minute'])
    if not minute:return False
    target=int(minute[1])+int(minute[2] or 0)
    home,away=aliases(job['home']),aliases(job['away'])
    if not home or not away:return False
    hits=[]
    for text in texts:
        normalized=text.upper().translate(_TRANSLATE)
        h=next((m for a in home if (m:=re.search(r'(?<!\w)'+re.escape(a)+r'(?!\w)',normalized))),None)
        a=next((m for v in away if (m:=re.search(r'(?<!\w)'+re.escape(v)+r'(?!\w)',normalized))),None)
        if not h or not a or h.start()>=a.start():continue
        times=[int(m)*60+int(s) for m,s in re.findall(r'\b(\d{1,3}):([0-5]\d)\b',text)]
        hits.extend(t for t in times if max(0,target-1)*60<=t<=target*60+20)
    # Separate frames must corroborate the broadcast clock, not one OCR guess.
    return len(set(hits))>=2

def matches(clip,job,data,now):
    return candidate(clip,job,now) and evidence(read_frames(data),job)

