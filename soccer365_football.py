"""Supplement FNK with explicitly selected major competitions from Soccer365."""
import json
import re
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import datetime
from urllib.parse import urlsplit, urljoin
from live_football import Document,Node,Match,Goal,FootballError,MOSCOW,PREFIX,ACTIVE,text

BASE='https://soccer365.ru'
NAME='Soccer365'
COMPETITIONS={'19':'Лига чемпионов УЕФА','20':'Лига Европы УЕФА',
 '12':'Чемпионат Англии','13':'Премьер-лига России','15':'Чемпионат Италии',
 '16':'Чемпионат Испании','17':'Чемпионат Германии','18':'Чемпионат Франции',
 '742':'Чемпионат мира','24':'Чемпионат Европы','824':'Клубный чемпионат мира',
 '903':'Суперкубок УЕФА','786':'Кубок России',
 '1658':'Лига наций УЕФА','1659':'Лига наций УЕФА - Лига B',
 '439':'Товарищеские матчи (сборные)'}
ALIASES = {'аек афины': 'аек',
 'айнтрахт франкфурт': 'айнтрахт',
 'атлетик бильбао': 'атлетик',
 'атлетико мадрид': 'атлетико',
 'бавария мюнхен': 'бавария',
 'байер 04': 'байер',
 'боруссия дортмунд': 'боруссия д',
 'боруссия менхенгладбах': 'боруссия м',
 'вердер бремен': 'вердер',
 'депортиво а корунья': 'депортиво',
 'динамо москва': 'динамо',
 'кристал пэлас': 'кристал палас',
 'лидс юнайтед': 'лидс',
 'лилль': 'лиль',
 'майнц 05': 'майнц',
 'ман сити': 'манчестер сити',
 'ман юнайтед': 'манчестер юнайтед',
 'ньюкасл юнайтед': 'ньюкасл',
 'падерборн 07': 'падерборн',
 'псв эйндховен': 'псв',
 'расинг сантандер': 'расинг',
 'рб лейпциг': 'лейпциг',
 'реал бетис': 'бетис',
 'сабах масазыр': 'сабах',
 'спортинг лиссабон': 'спортинг',
 'тоттенхэм хотспур': 'тоттенхэм',
 'шальке 04': 'шальке',
 'шахтер донецк': 'шахтер'}

def identity(name):
 value=' '.join(re.findall(r'\w+',name.casefold().replace('ё','е')))
 return ALIASES.get(value,value)

def valid_url(url):
 p=urlsplit(url)
 return p.scheme=='https' and p.netloc=='soccer365.ru' and not p.fragment and bool(re.fullmatch(r'/live/(?:match[12]?|game)-[a-z0-9-]+/|/games/[1-9]\d*/',p.path))

def fetch(url):
 if url!=BASE+'/online/' and not re.fullmatch(re.escape(BASE)+r'/online/&date=20\d{2}-\d{2}-\d{2}',url) and not valid_url(url):raise FootballError('soccer365_invalid_url')
 try:
  req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0','Cache-Control':'no-cache'})
  with urllib.request.urlopen(req,timeout=12) as r:
   # Completed live pages can redirect to the same site's match card.
   # broadcast() independently verifies the immutable game id and both teams.
   if (r.url!=url and not (valid_url(url) and valid_url(r.url))) or 'text/html' not in r.headers.get('Content-Type',''):raise FootballError('soccer365_response')
   raw=r.read(3000001)
   if len(raw)>3000000:raise FootballError('soccer365_page_size')
   return raw.decode('utf-8')
 except (OSError,UnicodeError):raise FootballError('soccer365_unavailable') from None

def listing(html, include_regions=False):
 root=Document(html).root;found={}
 blocks=[n for n in root.walk() if n.has('live_comptt_bd')]
 if not blocks:raise FootballError('soccer365_listing_missing')
 for block in blocks:
  comp=COMPETITIONS.get(block.attrs.get('id','').removeprefix('cmp'))
  if not comp:
   header=block.find('block_header')
   title=text(header.find('img16')) if header else ''
   if re.fullmatch(r'Лига наций УЕФА(?: - Лига [A-DАВСД])?|(?:Чемпионат мира|Чемпионат Европы)(?:[ .–—-]+(?:Квалификация|Отборочный турнир)(?:[ .–—-]+(?:Европа|Азия|Африка|Южная Америка|Северная Америка|Океания))?)?',title,re.I):comp=title
  if not comp:continue
  for card in block.walk():
   if not card.has('game_block'):continue
   link=card.find('game_link')
   if not link:continue
   number=link.attrs.get('dt-id','');url=urljoin(BASE,link.attrs.get('href',''))
   home=text(card.find('ht').find('name')) if card.find('ht') else ''
   away=text(card.find('at').find('name')) if card.find('at') else ''
   if not number.isdigit() or not valid_url(url) or not home or not away:continue
   if not include_regions and comp=='Кубок России' and 'путь регионов' in text(card).casefold():continue
   if any(re.search(r'\bU\s*[-–]?\s*\d{1,2}\b|\(Ж\)|\bженщин',t,re.I) for t in (home,away)):continue
   found[number]=dict(id='s365:'+number,url=url,home=home,away=away,competition=comp)
 return list(found.values())

def broadcast(html,selected):
 root=Document(html).root
 box=next((n for n in root.walk() if n.attrs.get('id')=='game_events'),None)
 if box is None:raise FootballError('soccer365_match_missing')
 home=text(box.find('live_game_ht'));away=text(box.find('live_game_at'))
 if (identity(home),identity(away))!=(identity(selected['home']),identity(selected['away'])):raise FootballError('soccer365_wrong_teams')
 home,away=selected['home'],selected['away']
 number=re.search(r'\bvar\s+game_id\s*=\s*(\d+)\s*;',html)
 if not number or selected.get('id','s365:'+number[1])!='s365:'+number[1]:raise FootballError('soccer365_wrong_id')
 events=[]
 for raw in re.findall(r'<script[^>]*type=["\x27]application/ld\+json["\x27][^>]*>(.*?)</script>',html,re.S):
  try:value=json.loads(raw)
  except ValueError:continue
  if isinstance(value,dict) and value.get('@type')=='Event':events.append(value)
 if not events:
  starts=re.findall(r'\bvar\s+game_start\s*=\s*(\d{10})\s*;',html)
  if len(starts)==1 and 946684800 <= int(starts[0]) <= 4102444800:
   events=[{'startDate':datetime.fromtimestamp(int(starts[0]),MOSCOW).isoformat()}]
 if len(events)!=1:
  if len(events)==0 and isinstance(selected.get('kickoff'),(int,float)):
   events=[{'startDate':datetime.fromtimestamp(selected['kickoff'],MOSCOW).isoformat()}]
  else:raise FootballError('soccer365_date_missing')
 try:
  dt=datetime.fromisoformat(events[0]['startDate'])
  if dt.tzinfo is None:raise ValueError
  kickoff=dt.timestamp()
 except (KeyError,ValueError):raise FootballError('soccer365_invalid_date') from None
 label=text(box.find('live_game_status'));lower=label.casefold()
 if 'заверш' in lower:status='finished'
 elif 'перерыв' in lower:status='break'
 elif re.search(r'\d+.*мин',lower):status='live'
 elif 'пенальти' in lower:status='penalties'
 elif any(w in lower for w in ('перенес','отмен','прерван')):status='paused'
 elif not lower or any(w in lower for w in ('не начал','начало','ожида')):status='scheduled'
 else:status='unknown'
 values=[text(n) for n in box.walk() if n.has('live_game_goal')]
 scores=tuple(int(v) if v.isdigit() else None for v in values[:2])
 if len(scores)!=2:raise FootballError('soccer365_score_missing')
 if status=='scheduled':scores=(None,None)
 goals=[];score=[0,0];rows=[]
 for row in box.walk():
  children=[c for c in row.children if isinstance(c,Node)]
  minute=next((text(c) for c in children if c.has('event_min')),None)
  if not minute:continue
  minute=re.sub(r'\s+','',minute.replace("'",'′'))
  if not re.fullmatch(r'\d{1,3}(?:\+\d{1,2})?′?',minute):continue
  minute=minute.rstrip('′')+'′'
  for side,cls in enumerate(('event_ht','event_at')):
   cell=next((c for c in children if c.has(cls)),None)
   if not cell:continue
   icons={k for c in cell.walk() for k in c.attrs.get('class','').split()}
   kind=next((kind for icon,kind in [('live_owngoal','own-goal'),('live_pengoal','penalty-goal'),('live_goal','goal')] if icon in icons),None)
   if not kind:continue
   player=next((text(c) for c in cell.walk() if c.tag=='a' and c.attrs.get('href','').startswith('/players/')),None)
   if not player:player=' '.join(c.strip() for c in cell.children if isinstance(c,str) and c.strip())
   if not player:continue
   parts=list(map(int,re.findall(r'\d+',minute)))
   rows.append(((parts[0],parts[1] if len(parts)>1 else 0),minute,side,player,kind))
 for _,minute,side,player,kind in sorted(rows,key=lambda x:x[0]):
  score[side]+=1;description='Гол: '+player+'.'
  for comment in root.walk():
   if comment.has('text_report_item') and text(comment.find('tmin'))==minute.rstrip('′'):
    detail=text(comment.find('ttext'))
    if player.casefold() in detail.casefold() and re.search(r'гол|забил|счет',detail,re.I) and not re.search(r'отмен|не забил',detail,re.I):description=detail;break
  goals.append(Goal(f's365:{number[1]}:{score[0]}:{score[1]}',tuple(score),minute,player,kind,description))
 # Partial event lists must not invent the missing score progression.
 if tuple(score)!=scores:goals=[]
 return Match('s365:'+number[1],selected['competition'],home,away,kickoff,status,label,*scores,selected['url'],goals=tuple(goals),source=NAME)

class SupplementedScoreboard:
 name='Футбол на Куличках + Soccer365'
 def __init__(self,store=None,clock=time.time,primary=None,fetcher=fetch):
  from kulichki_football import KulichkiScoreboard
  self.store=store;self.clock=clock;self.primary=primary or KulichkiScoreboard(store,clock=clock);self.fetch=fetcher
  self.lock=threading.Lock();self.matches={};self.selected=[];self.list_at=0;self.last_error='';self.follow_updates=False
  if store:
   for row in store.get(PREFIX+'soccer365_selection',[]):
    try:
     if valid_url(row['url']):self.matches[row['id']]=Match(**row)
    except (TypeError,KeyError):continue
 def prediction_matches(self,day,query=''):
  # Separate read-only calendar lookup: never change the live monitoring selection.
  from live_football import select_matches
  url=BASE+'/online/&date='+day.isoformat()
  rows=listing(self.fetch(url), include_regions=True)
  candidates=[r for r in rows if select_matches([Match(r['id'],r['competition'],r['home'],r['away'],0,'scheduled','',None,None,r['url'])],query)]
  def load(row):return broadcast(self.fetch(row['url']),row)
  with ThreadPoolExecutor(max_workers=4) as pool:matches=list(pool.map(load,candidates))
  return [m for m in matches if datetime.fromtimestamp(m.kickoff,MOSCOW).date()==day]

 def day(self,day):
  with self.lock:
   now=self.clock();errors=[]
   self.primary.follow_updates=self.follow_updates
   try:main,checked=self.primary.day(day)
   except FootballError as error:main=[];checked=now;errors.append(str(error))
   try:
    if now-self.list_at>=300 or not self.list_at:self.selected=listing(self.fetch(BASE+'/online/'));self.list_at=now
   except FootballError as error:errors.append(str(error))
   selected={r['id']:r for r in self.selected}
   for m in self.matches.values():
    if 0<=now-m.kickoff<6*3600 and m.status in ACTIVE|{'finished'}:selected.setdefault(m.id,dict(id=m.id,url=m.url,home=m.home,away=m.away,competition=m.competition))
   seen=self.store.get(PREFIX+f'seen:{self.store.get("chat_id")}',{}) if self.store else {}
   def same(a,b):return identity(a.home)==identity(b['home']) and identity(a.away)==identity(b['away'])
   known=list(main)+[m for m in getattr(self.primary,'matches',{}).values()
                     if datetime.fromtimestamp(m.kickoff,MOSCOW).date()==day
                     or 0<=now-m.kickoff<6*3600]
   # FNK is preferred, except a previously announced supplemental match keeps its identity.
   wanted=[r for r in selected.values() if r['id'] in seen or not any(same(m,r) for m in known)]
   # A temporary failure must not switch an announced game to a new FNK id.
   main=[m for m in main if not any(r['id'] in seen and same(m,r) for r in wanted)]
   failed=set()
   def load(row):
    previous=self.matches.get(row['id'])
    if previous and now-previous.checked<10:return previous,None
    selection=dict(row,**({'kickoff':previous.kickoff} if previous else {}))
    try:return replace(broadcast(self.fetch(row['url']),selection),checked=self.clock()),None
    except FootballError as error:
     # Retry the known game, not today's listing: late games survive midnight
     # and redirects/removal of the live URL without guessing a final status.
     number=re.fullmatch(r's365:([1-9]\d*)',row['id'])
     canonical=BASE+'/games/'+number[1]+'/' if number else ''
     if previous and canonical and canonical!=row['url']:
      try:return replace(broadcast(self.fetch(canonical),selection),checked=self.clock()),None
      except FootballError as fallback:return row['id'],str(fallback)
     return row['id'],str(error)
   with ThreadPoolExecutor(max_workers=4) as pool:
    for match,error in pool.map(load,wanted):
     if error:failed.add(match);errors.append(error)
     else:self.matches[match.id]=match
   self.matches={k:m for k,m in self.matches.items() if now-m.kickoff<8*86400}
   extra=[self.matches[r['id']] for r in wanted if r['id'] in self.matches and r['id'] not in failed and datetime.fromtimestamp(self.matches[r['id']].kickoff,MOSCOW).date()==day]
   main=[m for m in main if not any(identity(m.home)==identity(e.home) and identity(m.away)==identity(e.away) and abs(m.kickoff-e.kickoff)<6*3600 for e in extra)]
   if self.store:
    snapshots=[{k:v for k,v in asdict(m).items() if k not in ('goals','cancellations')} for m in self.matches.values()]
    self.store.set(PREFIX+'soccer365_selection',snapshots)
    original=[r for r in self.store.get(PREFIX+'fnk_selection',[]) if not r['id'].startswith('s365:')]
    self.store.set(PREFIX+'fnk_selection',original+snapshots)
   self.last_error='; '.join(sorted(set(errors)))
   if not main and not extra and errors:raise FootballError(self.last_error)
   return sorted(main+extra,key=lambda m:(m.kickoff,m.id)),self.clock()
