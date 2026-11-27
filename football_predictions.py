"""Transparent pre-match Poisson estimates from dated FNK league results."""
import json
import math
import re
import time
import urllib.request
from datetime import datetime, date, timedelta
from html import escape
from urllib.parse import urljoin, urlsplit

from common import HTMLMessage, bubus_spelling
from live_football import Document, Node, MOSCOW, FootballError, football_request, select_matches, text

BASE = 'https://football.kulichki.net'
LEAGUES = ('ruschamp','england','italy','spain','germany','france','portugal','holland','turkey','ukraine')
CLUBS = r'манчестер|мю|шахта|шахтер|шахтёр|спортинг|рома|барселона|ливерпуль|псж|бавария|интер|фейеноорд|галатасарай|штутгарт|атлетико|краснодар|динамо|зенит|спартак|цска|локомотив|ростов|рубин|акрон|ахмат|балтика|факел|родина|оренбург|крылья'



# Forecast V8 integration mode.
# Reserved for future isolated shadow integration.
# All modes currently preserve the legacy forecast.
import os

V8_MODE = os.environ.get(
    "BUBUS_FORECAST_V8_MODE", "off"
).strip().lower()

if V8_MODE not in ("off", "shadow", "on"):
    V8_MODE = "off"

def canonical(name):
    value = ' '.join(re.findall(r'\w+', name.casefold().replace('ё','е')))
    return 'динамо' if value in ('динамо москва','динамо московское') else value


def followup(value):
    return bool(re.fullmatch(r'(?:а\s+|ну\s+|и\s+|так\s+)*(?:сч[её]т|какой сч[её]т|почему|например|кто победит|кто выиграет|победит кто|выиграет кто|на кого ставить|ставить на кого|в этом матче что|что в этом матче|какой исход|победа или ничья)[ ?!.,]*',value,re.I)
                or re.search(r'\bили\s+ничь[яю]\b',value,re.I)
                or re.fullmatch(r'\s*(?:на\s+)?(?:\d{1,2}\.\d{1,2}(?:\.20\d{2})?|\d{1,2}\s+(?:'+'|'.join(MONTHS)+r')(?:\s+20\d{2})?|завтра|сегодня)[.!? ]*',value,re.I))


MONTHS=('января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря')

def request(value, command='', addressed=False, context=None, now=None):
    if command not in ('','/bubus','/predict'):
        return None
    nickname=re.match(r'^(?:бубус|бубуська|володька)[ ,:!]+',value,re.I)
    addressed=addressed or bool(nickname) or command in ('/bubus','/predict')
    body=value[nickname.end():].strip() if nickname else value.strip()
    body=re.sub(r'\b(\d{1,2})\s+('+'|'.join(MONTHS)+r')(?:\s+(20\d{2}))?\b',lambda m:f'{int(m[1]):02d}.{MONTHS.index(m[2].lower())+1:02d}'+('.'+m[3] if m[3] else ''),body,flags=re.I)
    dated=bool(re.search(r'\d{1,2}\.\d{1,2}|\b(?:сегодня|завтра|вчера|сегодняшн\w*|седня)\b',body,re.I))
    if context and dated and not re.search(r'\b(?:'+CLUBS+r')\b',body,re.I):
        body+=' '+context['filter']
    explicit=bool(re.search(r'\b(?:прогноз\w*|предсказан\w*|предскажи)\b',body,re.I))
    if explicit and not re.search(r'\bматч\w*|\bфутбол\w*',body,re.I) and re.search(r'\b(?:погод\w*|курс\w*|валют\w*|биткоин\w*|акци[яий]\w*)\b',body,re.I):
        return None
    football=bool(re.search(r'\b(?:футбол\w*|матч\w*|'+CLUBS+r')\b',body,re.I))
    outcome=bool(re.search(r'\b(?:кто (?:победит|выиграет)|(?:победит|выиграет) кто|ставить на кого|на кого ставить|или ничья)\b',body,re.I))
    pair=bool((context or re.search(r'\bили\b',body,re.I)) and len(re.findall(r'\b(?:'+CLUBS+r')\b',body,re.I))>=2)
    if not (command=='/predict' or (addressed and (explicit or (context and (followup(body) or outcome or dated)) or (outcome and football) or pair))):
        return None
    if context and followup(body) and not explicit and not dated:
        return dict(kind='football_prediction',day=context['day'],filter=context['filter'],match_id=context['match_id'])
    if context and outcome and not football:
        return dict(kind='football_prediction',**context)
    all_matches=bool(explicit and re.search(r'\b(?:все|всем|каждий|каждый|каждому|каждий|каждого)\b',body,re.I))
    body=re.sub(r'\bсегодняшн\w*\b','сегодня',body,flags=re.I)
    body=re.sub(r'\bседня\b','сегодня',body,flags=re.I)
    body=re.sub(r'\b(?:все|всем|каждий|каждый|каждому|каждого|дня|со|по|а|пожалуй|скажи|говори|ставить|кого)\b',' ',body,flags=re.I)
    body=re.sub(r'\b(?:бубус|бубуська|вола|мозги|мозг|не|еби|ебать|нахуй|блять|блядь|ну|уже|просто|всё-таки|все-таки)\b',' ',body,flags=re.I)
    body=re.sub(r'^/predict\b','',body,flags=re.I)
    body=re.sub(r'\b(?:дай|давай|сделай|пожалуйста|свой|твой|прогноз\w*|предсказан\w*|предскажи|на|матч\w*|футбол\w*|с|точным|точний|точный|сч[её]т\w*|кто|победит|выиграет|или|ничья)\b',' ',body,flags=re.I)
    body=re.sub(r'\bдинамо[ -]+москва\b','Динамо',body,flags=re.I)
    parsed=football_request('/football '+body,'/football',now=now)
    if parsed['kind']!='football':
        return dict(kind='fixed',text='укажи одну дату и команди: /predict Краснодар Динамо сегодня')
    return dict(parsed,kind='football_prediction',all_matches=all_matches,explicit_day=dated)


def fetch(url):
    part=urlsplit(url)
    if part.scheme!='https' or part.netloc!='football.kulichki.net' or not re.fullmatch(r'/(?:'+ '|'.join(LEAGUES) +r')/(?:20\d{2}/teams/[a-z_]+\.htm)?',part.path) or part.query:
        raise FootballError('prediction_invalid_url')
    try:
        req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0','Cache-Control':'no-cache'})
        with urllib.request.urlopen(req,timeout=12) as response:
            if response.url!=url or 'text/html' not in response.headers.get('Content-Type',''):
                raise FootballError('prediction_invalid_response')
            raw=response.read(2000001)
            if len(raw)>2000000: raise FootballError('prediction_page_too_large')
            return raw.decode(response.headers.get_content_charset() or 'windows-1251')
    except (OSError,UnicodeError,LookupError):
        raise FootballError('prediction_source_unavailable') from None


def team_index(html, season):
    result={}
    for node in Document(html).root.walk():
        href=urljoin(BASE+'/',node.attrs.get('href',''))
        if node.tag=='a' and re.fullmatch(re.escape(BASE)+rf'/(?:{"|".join(LEAGUES)})/{season}/teams/[a-z_]+\.htm',href):
            name=canonical(text(node))
            if name and len(name)<50:
                if name in result and result[name]!=href: raise FootballError('prediction_ambiguous_team')
                result[name]=href
    return result


def results(html, team, before):
    found={}
    for row in Document(html).root.walk():
        if row.tag!='tr': continue
        cells=[text(n) for n in row.children if isinstance(n,Node) and n.tag=='td']
        if len(cells)!=4 or not re.fullmatch(r'\d{1,2}',cells[0]): continue
        if not any(n.tag=='a' and re.search(r'-obzor-matcha-(?:'+ '|'.join(LEAGUES) +r')-20\d{2}\.htm$',n.attrs.get('href','')) for n in row.walk()):
            continue
        try: day=datetime.strptime(cells[1],'%d.%m.%Y').date()
        except ValueError: continue
        # Never train on this match, same-day results or future information.
        if not 0<(before-day).days<=180: continue
        m=re.fullmatch(r'(.+?)\s+-\s+(.+?)\s+-\s+(\d{1,2}):(\d{1,2})',cells[2])
        if not m: continue
        home,away=canonical(m[1]),canonical(m[2]); score=(int(m[3]),int(m[4]))
        if canonical(team) not in (home,away) or max(score)>15: continue
        key=(day.isoformat(),home,away)
        if key in found and found[key]['score']!=score: raise FootballError('prediction_conflicting_result')
        found[key]=dict(day=day.isoformat(),home=home,away=away,score=score)
    return sorted(found.values(),key=lambda r:r['day'],reverse=True)[:8]


def estimate(home, away, home_rows, away_rows):
    if min(len(home_rows),len(away_rows))<5:
        raise FootballError('prediction_insufficient_results')
    def rates(team, rows, venue):
        values=[(r['score'] if r['home']==canonical(team) else r['score'][::-1],r['home']==canonical(team)) for r in rows]
        gf=sum(s[0] for s,_ in values)/len(values); ga=sum(s[1] for s,_ in values)/len(values)
        subset=[s for s,is_home in values if is_home==venue]
        # Smooth small home/away samples toward this team's overall recent form.
        return ((sum(s[0] for s in subset)+4*gf)/(len(subset)+4),
                (sum(s[1] for s in subset)+4*ga)/(len(subset)+4))
    ha,hd=rates(home,home_rows,True); aa,ad=rates(away,away_rows,False)
    lh=max(.15,min(5,(ha+ad)/2)); la=max(.15,min(5,(aa+hd)/2))
    grid={(i,j):math.exp(-lh-la)*lh**i*la**j/(math.factorial(i)*math.factorial(j)) for i in range(21) for j in range(21)}
    total=sum(grid.values())
    chances=[sum(p for (i,j),p in grid.items() if (i>j if k==0 else i==j if k==1 else i<j))/total for k in range(3)]
    rounded=[int(100*p) for p in chances]
    for k in sorted(range(3),key=lambda k:100*chances[k]-rounded[k],reverse=True)[:100-sum(rounded)]: rounded[k]+=1
    return dict(probabilities=rounded,score=max(grid,key=grid.get),winner=max(range(3),key=lambda k:chances[k]))


class Predictions:
    def __init__(self, football, fetcher=fetch, clock=time.time):
        self.football,self.fetch,self.clock=football,fetcher,clock
        self.cache={}

    def page(self,url):
        cached=self.cache.get(url)
        if cached and self.clock()-cached[0]<900: return cached[1]
        html=self.fetch(url)
        self.cache[url]=(self.clock(),html)
        if len(self.cache)>40: self.cache.pop(next(iter(self.cache)))
        return html

    def fallback(self, req, model=None, match=None):
        home,away=(match.home,match.away) if match else ('','')
        score=None
        if model:
            try:
                raw=model(dict(query=req['filter'],day=req['day'],home=home,away=away))
                value=json.loads(raw) if isinstance(raw,str) else raw
                if isinstance(value,dict):
                    candidate=value.get('score')
                    if (isinstance(candidate,list) and len(candidate)==2
                            and all(type(n) is int and 0<=n<=12 for n in candidate)):
                        score=candidate
                    if not match:
                        names=[value.get(k) for k in ('home','away')]
                        if all(isinstance(n,str) and 1<len(n.strip())<=70 for n in names):
                            home,away=names
            except (ValueError,TypeError,KeyError,OSError):
                pass
        if not home or not away:
            parts=re.split(r'\s+[—–-]\s+|\s+против\s+|\s+vs\.?\s+',req['filter'],flags=re.I)
            if len(parts)==2:home,away=parts
        if not home or not away:
            return 'назови обе команди и дам свой вариант со счётом'
        # Keep a usable explicitly neutral prior even if the model is unavailable.
        method='оценка модели' if score is not None else 'нейтральний базовий сценарий'
        score=score if score is not None else [1,1]
        pick='ничья' if score[0]==score[1] else 'победа '+(home if score[0]>score[1] else away)
        req['_context']=dict(day=req['day'],filter=f'{home} — {away}',match_id=match.id if match else None)
        heading=f'⚽ <b>Прогноз: {escape(home)} — {escape(away)}</b>'
        if match:
            heading+=' · '+datetime.fromtimestamp(match.kickoff,MOSCOW).strftime('%d.%m %H:%M МСК')
        note='Дата и наличие матча в календаре не подтверждени. Оценка условной встречи.' if not match else ''
        if match and (match.status!='scheduled' or self.clock()>=match.kickoff):
            note='Оценка по ходу матча без учёта текущего счёта.'
        return bubus_spelling(HTMLMessage(heading+f'\n\nМой вариант: <b>{escape(pick)}</b>\nПредполагаемий счёт: <b>{score[0]}:{score[1]}</b>\n\n'+
            method+'. Уверенность низкая. Ето предположение без проверенной свежей форми и составов. Без гарантий.'+('\n\n'+note if note else '')))

    def answer(self, req, model=None):
        import logging
        v8log = logging.getLogger(__name__)
        v8log.warning(
            'BUBUS_V8_TRACE_ENTER day=%s match_id=%s all=%s',
            req.get('day'), req.get('match_id'),
            req.get('all_matches'))
        day=date.fromisoformat(req['day'])
        query=re.sub(r'\bдинамо[ -]+москва\b','Динамо',req['filter'],flags=re.I)

        # BUBUS_FORECAST_QUERY_CLEANUP_V1
        # Remove date grammar residue and exact
        # duplicated match-pair fragments.

        query = re.sub(
            r"(?<!\w)года(?!\w)",
            " ",
            query,
            flags=re.I,
        )

        query = re.sub(
            r"\s+",
            " ",
            query,
        ).strip(" .,-—–")

        # Detect a complete duplicated phrase.
        # Example: "A — B. A — B"
        # The match is still selected by the
        # original source and selection logic.

        parts = re.split(
            r"\s*[.;]\s*",
            query,
        )

        if len(parts) == 2 and canonical(parts[0]) == canonical(parts[1]):
            query = parts[0]

        # Also handle the already-cleaned form
        # "A — B A — B" when the separator was
        # removed by punctuation normalization.

        words = query.split()

        if (
            len(words) >= 4
            and len(words) % 2 == 0
        ):
            half = len(words) // 2

            left = " ".join(words[:half])
            right = " ".join(words[half:])

            if canonical(left) == canonical(right):
                query = left
        # BUBUS_FORECAST_QUERY_CLEANUP_V1_END

        source=self.football.source
        if hasattr(source,'prediction_matches'):
            try:matches=source.prediction_matches(day,query)
            except FootballError:matches=[]
            if not matches and not req.get('explicit_day',True) and query and not req.get('match_id'):
                from concurrent.futures import ThreadPoolExecutor
                days=[day+timedelta(days=i) for i in range(1,8)]
                def lookup(d):
                    try:return source.prediction_matches(d,query)
                    except FootballError:return []
                with ThreadPoolExecutor(max_workers=3) as pool:future=[m for group in pool.map(lookup,days) for m in group]
                future=[m for m in future if m.status=='scheduled' and m.kickoff>self.clock()]
                if future:
                    first=min(future,key=lambda m:m.kickoff)
                    day=datetime.fromtimestamp(first.kickoff,MOSCOW).date()
                    req['day']=day.isoformat();matches=[m for m in future if datetime.fromtimestamp(m.kickoff,MOSCOW).date()==day]
        else:
            try:matches,_=source.day(day)
            except FootballError:matches=[]
        # BUBUS_V8_TRACE_SOURCE
        v8log.warning(
            "BUBUS_V8_TRACE_SOURCE day=%s query=%r "
            "source_count=%s source_matches=%s",
            day,
            query,
            len(matches),
            [
                (m.id, m.home, m.away)
                for m in matches[:15]
            ],
        )
        selected=select_matches(matches,query)
        if not selected and not re.search(r'куб|лига|чемпионат',query,re.I):
            words=canonical(re.sub(r'\bмю\b','манчестер юнайтед',re.sub(r'\bшахта\b','шахтер',query,flags=re.I),flags=re.I)).split()
            selected=[m for m in matches if all(all(w in words for w in canonical(t).split()) for t in (m.home,m.away)) and select_matches([m],'')]
        if req.get('match_id'): selected=[m for m in selected if m.id==req['match_id']]
        v8log.warning(
            'BUBUS_V8_TRACE_SELECTED count=%s ids=%s',
            len(selected), [m.id for m in selected[:12]])
        if req.get('all_matches'):
            if not selected:return 'на ету дату матчей в расписании нет'
            blocks=[]
            for match in selected[:12]:
                item=dict(req,all_matches=False,filter=f'{match.home} {match.away}',match_id=match.id)
                try:answer=self.answer(item,model=model) if model else self.answer(item)
                except FootballError:answer=self.fallback(item,model,match)
                lines=[line for line in str(answer).split('\n') if line.strip()]
                blocks.append(lines[0]+'\n\n'+'\n'.join(lines[1:4]) if lines[0].startswith('⚽') else f'<b>{escape(match.home)} — {escape(match.away)}</b>\n{escape(str(answer))}')
            return bubus_spelling(HTMLMessage('⚽ <b>Прогнози на '+day.strftime('%d.%m.%Y')+'</b>\n\n'+'\n\n'.join(blocks)+'\n\nРасчёт по результатам или оценка модели указани в каждом прогнозе. Гарантий нет.'))
        if len(selected)!=1:
            req['_context']=dict(day=req['day'],filter=req['filter'],match_id=None)
            return 'нашёл несколько матчей уточни дату' if selected else self.fallback(req,model)
        match=selected[0]
        req['_context']=dict(day=req['day'],filter=f'{match.home} {match.away}',match_id=match.id)
        heading=f'⚽ <b>Прогноз: {escape(match.home)} — {escape(match.away)}</b> · '+datetime.fromtimestamp(match.kickoff,MOSCOW).strftime('%d.%m %H:%M МСК')
        if match.status=='finished':
            return bubus_spelling(HTMLMessage(heading+'\nматч уже завершён. Прогноз задним числом не выдаю'))
        if match.status!='scheduled' or self.clock()>=match.kickoff:
            return self.fallback(req,model,match)


        # BUBUS_V8_EXPERIMENTAL_INTEGRATION_BEGIN
        # Explicitly enabled experimental V8 responses.
        # Shadow cache eligibility and verification flags
        # must never be changed by the Telegram integration.

        if V8_MODE == "on":
            try:
                from football_forecast_data_live_v1 import latest_snapshot
                from football_forecast_v8_shadow import load_shadow

                _, snapshot_path, _ = latest_snapshot()

                forecast = load_shadow(
                    match,
                    snapshot_path.name,
                )

                if forecast.get("production_eligible") is not False:
                    raise RuntimeError(
                        "Unexpected V8 eligibility"
                    )

                message = forecast["message"]

                if not isinstance(message, str) or not message.strip():
                    raise RuntimeError("Empty V8 message")

                v8log.warning(
                    "BUBUS_V8_EXPERIMENTAL_USED match=%s "
                    "batch=%s fingerprint=%s",
                    match.id,
                    forecast.get("batch_id"),
                    forecast.get("data_fingerprint"),
                )

                return bubus_spelling(
                    HTMLMessage(
                        escape(message)
                        + "\n\n"
                        + "🧪 Экспериментальная модель V8. "
                        + "Точность и качество исходных данных "
                        + "ещё проходят проверку."
                    )
                )

            except Exception as exc:
                v8log.warning(
                    "BUBUS_V8_EXPERIMENTAL_FALLBACK "
                    "match=%s reason=%s: %s",
                    match.id,
                    type(exc).__name__,
                    exc,
                )

        # BUBUS_V8_EXPERIMENTAL_INTEGRATION_END

        # BUBUS_V8_HOOK_BEGIN
        # The V8 single-match interface is verified
        # in isolated tests, but production activation
        # is intentionally disabled.
        #
        # "shadow" and "on" are reserved values.
        # They do not execute V8 at this stage.
        #
        # Legacy predictions remain authoritative.
        # BUBUS_V8_HOOK_END
        # Shared 90-minute engine for clubs, cups and national teams.
        from forecast_service import competition_kind, build, render, register
        if competition_kind(match.competition):
            try:
                forecast = build(self, match, model)
                register(forecast)
                return render(forecast)
            except (OSError, ValueError) as exc:
                v8log.warning('Forecast journal unavailable: %s', type(exc).__name__)
                if 'forecast' in locals():
                    return render(forecast)
                return self.fallback(req, model, match)

        season=day.year+int(day.month>=7)
        try:index=team_index(self.page(BASE+'/ruschamp/'),season)
        except FootballError:index={}
        if any(canonical(t) not in index for t in (match.home,match.away)):
            from concurrent.futures import ThreadPoolExecutor
            def load(league):
                try:return team_index(self.page(BASE+'/'+league+'/'),season)
                except FootballError:return {}
            with ThreadPoolExecutor(max_workers=4) as pool:
                for other in pool.map(load,LEAGUES[1:]):index.update(other)
        urls=[index.get(canonical(t)) for t in (match.home,match.away)]
        if not all(urls):
            v8log.warning(
                'BUBUS_V8_TRACE_NO_HISTORY match=%s urls=%s',
                match.id, [bool(u) for u in urls])

            # BUBUS_V8_OBSERVE_NO_HISTORY
            if V8_MODE == "shadow":
                try:
                    from football_forecast_data_live_v1 import latest_snapshot
                    from football_forecast_v8_observe import (
                        observe_unavailable_legacy,
                        append_observation,
                    )

                    _, snapshot_path, _ = latest_snapshot()

                    observation = observe_unavailable_legacy(
                        match,
                        snapshot_path.name,
                        reason="LEGACY_TEAM_HISTORY_NOT_FOUND",
                    )

                    append_observation(observation)

                    v8log.warning(
                        "BUBUS_V8_OBSERVATION match=%s reason=%s",
                        match.id,
                        observation["legacy_reason"],
                    )

                except Exception as exc:
                    v8log.warning(
                        "BUBUS_V8_OBSERVATION_SKIP match=%s reason=%s: %s",
                        match.id,
                        type(exc).__name__,
                        exc,
                    )
            # BUBUS_V8_OBSERVE_NO_HISTORY_END

            return self.fallback(req,model,match)
        cutoff=min(day,datetime.fromtimestamp(self.clock(),MOSCOW).date())
        try:
            histories=[results(self.page(url),team,cutoff) for url,team in zip(urls,(match.home,match.away))]
            calculated=estimate(match.home,match.away,*histories)
        except FootballError as exc:
            v8log.warning(
                'BUBUS_V8_TRACE_LEGACY_FAIL match=%s error=%s',
                match.id, type(exc).__name__)
            return self.fallback(req,model,match)
        h,d,a=calculated['probabilities']; score=calculated['score']

        # BUBUS_V8_SHADOW_BEGIN
        # Runs only when explicitly enabled.
        # Never replaces the legacy user response.
        if V8_MODE == "shadow":
            v8log.warning(
                "BUBUS_V8_TRACE_SHADOW_ENTER match=%s legacy=%s",
                match.id, (h, d, a))
            try:
                from football_forecast_data_live_v1 import latest_snapshot
                from football_forecast_v8_compare import (
                    compare as compare_v8,
                    append_comparison,
                    ShadowUnavailable,
                )

                _, snapshot_path, _ = latest_snapshot()

                comparison = compare_v8(
                    match=match,
                    legacy_probabilities=(h, d, a),
                    snapshot_id=snapshot_path.name,
                )

                append_comparison(comparison)

            except Exception as exc:
                # Shadow failures must never interrupt
                # the existing user-facing forecast.
                import logging
                logging.getLogger(__name__).warning(
                    "BUBUS_V8_SHADOW_SKIP match=%s reason=%s: %s",
                    match.id,
                    type(exc).__name__,
                    exc,
                )
        # BUBUS_V8_SHADOW_END

        pick=(f'победа {match.home}','ничья',f'победа {match.away}')[calculated['winner']]
        if sorted(calculated['probabilities'],reverse=True)[0]-sorted(calculated['probabilities'],reverse=True)[1]<5:
            pick='явного фаворита по расчёту нет'
        def form(team, rows):
            scores=[r['score'] if r['home']==canonical(team) else r['score'][::-1] for r in rows]
            return f'{escape(team)}: {len(rows)} матчей, забито {sum(s[0] for s in scores)}, пропущено {sum(s[1] for s in scores)}'
        checked=datetime.fromtimestamp(min(self.cache[u][0] for u in urls),MOSCOW).strftime('%d.%m %H:%M МСК')
        lines=[heading,'',f'Мой вариант по исходу: <b>{escape(pick)}</b>.',
               f'{escape(match.home)} {h}% · ничья {d}% · {escape(match.away)} {a}%',
               f'Самий вероятний отдельний счёт: {score[0]}:{score[1]}.',
               '',form(match.home,histories[0]),form(match.away,histories[1]),
               '', 'Расчёт Пуассона по последним матчам чемпионатов с учётом дома/в гостях. Разница сили лиг, состави, травми и кубковая ротация не учтени.',
               '', 'Ето оценка простой модели, не проверенная точность. Мяч мои проценти не читал, так шо без гарантий.',
               '\n'+ ' · '.join(f'<a href="{escape(url,quote=True)}">Результати {escape(team)}</a>' for url,team in zip(urls,(match.home,match.away))),
               'Проверено '+checked]
        return bubus_spelling(HTMLMessage('\n'.join(lines)))
