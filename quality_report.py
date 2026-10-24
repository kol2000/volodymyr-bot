"""Bounded operational quality history without dialogue text or model drafts."""
import json
import math
import re
import time
from collections import Counter

LABELS = dict(misunderstood='не понял смысл', off_topic='потерял тему', incorrect='неверный ответ',
              too_long='слишком длинно', too_short='слишком кратко', too_rude='слишком грубо',
              style='манера ответа', repeat='повтор')
REJECTION_LABELS = dict(lost_uncertainty='усилил уверенность', response_too_long='ответ слишком длинный',
    repeated_response='повтор ответа', tone_mismatch='грубость вместо спокойного ответа',
    reported_presence_claim='выдумал местоположение', reported_action_reversal='присвоил чужое действие',
    address_role_reversal='перепутал адресата', event_role_reversal='перепутал участников события',
    unsupported_memory='выдумал воспоминание', unsupported_habit='выдумал привычку',
    unsupported_certainty='бездоказательная уверенность', length_limit='генерация оборвалась')


def init_quality(store):
    with store.db() as db:
        db.execute('CREATE TABLE IF NOT EXISTS quality_runs (id INTEGER PRIMARY KEY,chat_id INTEGER,created REAL,mode TEXT,outcome TEXT,metrics TEXT,rejections TEXT)')
        db.execute('CREATE INDEX IF NOT EXISTS quality_runs_chat ON quality_runs(chat_id,created)')


def record_quality(store, job, metrics):
    clean = {key:value for key,value in metrics.items() if re.fullmatch(r'[a-z_]+',key)
             and type(value) in (int,float) and math.isfinite(value) and value>=0}
    reasons = [reason for reason in job.get('_rejections',[]) if re.fullmatch(r'[a-z_]+',reason)]
    mode = 'automatic' if job.get('automatic') else 'question' if job.get('thoughtful') else 'dialogue'
    if job.get('utility'):
        mode = 'utility'
    with store.db() as db:
        db.execute('INSERT INTO quality_runs(chat_id,created,mode,outcome,metrics,rejections) VALUES (?,?,?,?,?,?)',
                   (job['chat_id'],time.time(),mode,job.get('_outcome','not_sent'),json.dumps(clean),json.dumps(reasons)))
        db.execute('DELETE FROM quality_runs WHERE created<?',(time.time()-7*86400,))
        db.execute('DELETE FROM quality_runs WHERE chat_id=? AND id NOT IN (SELECT id FROM quality_runs WHERE chat_id=? ORDER BY id DESC LIMIT 2000)',(job['chat_id'],job['chat_id']))


def quality_report(store, chat):
    with store.db() as db:
        rows = db.execute('SELECT mode,outcome,metrics,rejections FROM quality_runs WHERE chat_id=? AND created>=? ORDER BY id',
                          (chat,time.time()-7*86400)).fetchall()
        issues = db.execute('SELECT reason,count(*) FROM answer_issues WHERE chat_id=? AND timestamp>=? GROUP BY reason ORDER BY count(*) DESC LIMIT 5',
                            (chat,time.time()-7*86400)).fetchall()
    lines = ['Качество Бубуса · последние 7 дней (до 2000 обработок)',f'Обработок: {len(rows)}; отправлено: {sum(r[1]=="sent" for r in rows)}']
    lines.append(f'Отправка не подтверждена: {sum(r[1]=="send_uncertain" for r in rows)}; без отправки: {sum(r[1]=="not_sent" for r in rows)}')
    measurements=[json.loads(row[2]) for row in rows]
    lines.append(f'Ошибок вызова модели: {sum(m.get("model_errors",0) for m in measurements)}; резервных ответов: {sum(m.get("fallback_answers",0) for m in measurements)}')
    lines.append('Замечания участников: '+('; '.join(f'{LABELS.get(r,r)}: {n}' for r,n in issues) or 'нет'))
    rejects = Counter(reason for _,_,_,raw in rows for reason in json.loads(raw))
    lines.append('Отклонения при генерации: '+('; '.join(f'{REJECTION_LABELS.get(r,r)}: {n}' for r,n in rejects.most_common(5)) or 'нет'))
    for mode in ('question','dialogue','automatic','utility'):
        samples = [json.loads(m) for kind,_,m,_ in rows if kind==mode]
        if not samples:
            continue
        total = sorted(m.get('queue_seconds',0)+m.get('total_seconds',0) for m in samples)
        percentile = lambda p:total[max(0,math.ceil(len(total)*p)-1)]
        avg = lambda key:sum(m.get(key,0) for m in samples)/len(samples)
        mode_label={'question':'Вопросы','dialogue':'Диалог','automatic':'Автоподколы','utility':'Команды'}[mode]
        lines.append(f'{mode_label}: {len(samples)}; ожидание+обработка p50 {percentile(.5):.1f} с, p95 {percentile(.95):.1f} с; '
                     f'очередь ср. {avg("queue_seconds"):.1f} с; модель ср. {avg("model_seconds"):.1f} с; '
                     f'повторных вызовов: {sum(max(0,m.get("model_calls",0)-1) for m in samples)}')
    lines.append('Замечания — оценки участников; отклонения — проверки программы, не доказанные ошибки смысла.')
    return '\n'.join(lines)
