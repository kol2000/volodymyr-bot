#!/usr/bin/env python3
"""Replay bounded regression dialogues locally; never send messages to Telegram."""
import argparse
import json
import tempfile
import time
from pathlib import Path

from bot import Bot, Store, answer_problem
from common import APIError, CONFIG, MODEL, ROOT, http_json
from answer_quality import concise_problem, concise_word_limit, grounding_problem, specific_clarification
from conversation import ambiguous_subject, inline_control, verification_query
from inference import generation_stats, runner_options
from question_answers import ends_question, thoughtful_request


class NoTelegram:
    def __getattr__(self, name):
        raise RuntimeError('Telegram is disabled in dialogue checks')


def route(case):
    thoughtful = bool(case.get('control') or case.get('dialogue') or case.get('updates') or thoughtful_request(case['request']))
    return 'web' if thoughtful and verification_query(case['request']) else 'question' if thoughtful else 'parody'


def uses_model(case):
    return route(case) != 'web' and not (ambiguous_subject(case['request'])
        and not (case.get('history') or case.get('dialogue') or case.get('updates')))


def assess(case, answer, elapsed, max_seconds):
    errors = []
    if not isinstance(answer, str) or not answer.strip() or answer == '__SILENCE__':
        return ['empty_answer']
    problem = answer_problem(answer, thoughtful=case['mode'] == 'question')
    if problem:
        errors.append(problem)
    lowered = answer.casefold()
    if any(word.casefold() in lowered for word in case.get('forbidden', [])):
        errors.append('forbidden_topic_or_phrase')
    if case.get('must_any') and not any(word.casefold() in lowered for word in case['must_any']):
        errors.append('missing_expected_subject')
    if case.get('max_words') and len(answer.split()) > case['max_words']:
        errors.append('too_many_words')
    if case.get('control') == 'concise' and case.get('dialogue'):
        if len(answer.split()) > concise_word_limit(case['dialogue'][-1]):
            errors.append('not_shorter_than_source')
        problem = concise_problem(answer, case['dialogue'][-1])
        if problem:
            errors.append(problem)
    if case.get('require_question') and not ends_question(answer):
        errors.append('clarification_not_asked')
    if case.get('require_clarification') and not specific_clarification(answer):
        errors.append('missing_specific_clarification')
    reference = case.get('dialogue', []) if case.get('control') else []
    problem = grounding_problem(answer, ' '.join([case['request']] + case.get('updates', []) + reference))
    if problem:
        errors.append(problem)
    if elapsed > max_seconds:
        errors.append('slow_answer')
    return errors


def evaluate(case, config, run_model=False, max_seconds=120, capture_rejections=False):
    mode = route(case)
    result = {'id': case['id'], 'expected_mode': case['mode'], 'actual_mode': mode,
              'errors': [] if mode == case['mode'] else ['wrong_route'], 'model_checked': False}
    if not run_model or mode == 'web':
        result['note'] = 'routing only; public network lookup disabled' if mode == 'web' else 'routing only'
        return result
    with tempfile.TemporaryDirectory(prefix='bubus-dialogue-') as folder:
        store = Store(Path(folder) / 'check.sqlite3')
        store.set('chat_id', -100123)
        bot = Bot(config, telegram=NoTelegram(), store=store)
        now = time.time()
        for number, text in enumerate(case.get('history', []), 1):
            store.add_message(dict(chat={'id': -100123}, message_id=number, date=now - 20 + number,
                                   text=text, **{'from': {'id': 7, 'first_name': 'Собеседник'}}))
        parent = None
        for number, text in enumerate(case.get('dialogue', []), 100):
            store.add_message(dict(chat={'id': -100123}, message_id=number, date=now - 10,
                                   text=text, _reply_to=parent, _answer_mode='question'), human=number % 2 == 0)
            parent = number
        job = dict(chat_id=-100123, text=case['request'], reply_to=200, automatic=False,
                   queued_at=now, dialogue_id=parent, thoughtful=mode == 'question', user_id=7,
                   response_control=case.get('control') or inline_control(case['request']),
                   request_updates=case.get('updates', []), _capture_rejections=capture_rejections)
        started = time.monotonic()
        try:
            answer = bot.generate(job)
        except (APIError, OSError, ValueError):
            answer = None
        elapsed = round(time.monotonic() - started, 3)
        result.update(model_checked=bool(job.get('_inference_calls')), answer_checked=True,
                      seconds=elapsed, answer=answer, local_reply=job.get('_local_reply'),
                      metrics=job.get('_metrics', {}), calls=job.get('_inference_calls', []),
                      rejections=job.get('_rejections', []), rewrite_fallback=job.get('_rewrite_fallback'))
        if capture_rejections:
            result['rejection_details'] = job.get('_rejection_details', [])
        result['errors'].extend(assess(case, answer, elapsed, max_seconds))
    return result


def warm_model(config):
    started = time.monotonic()
    result = http_json(config['ollama_url'] + '/api/chat',
                       dict(model=config['model'], messages=[], stream=False, keep_alive='24h',
                            options=runner_options()), timeout=180)
    if not isinstance(result, dict) or result.get('error'):
        raise APIError('Модель не загрузилась')
    return generation_stats(result, time.monotonic() - started)


def timing_text(result):
    if not result.get('answer_checked'):
        return 'только маршрут'
    if result.get('local_reply'):
        return f'локальное уточнение · {result["seconds"]:.2f} сек. · без Ollama'
    calls = result.get('calls', [])
    metrics = result.get('metrics', {})
    parts = [f'{result["seconds"]:.2f} сек.', f'запросов к модели: {len(calls)}']
    for key, label in [('load_seconds', 'загрузка'), ('prompt_seconds', 'чтение запроса'),
                       ('generation_seconds', 'ответ'), ('other_seconds', 'прочее/ожидание')]:
        if key in metrics:
            parts.append(f'{label}: {metrics[key]:.2f} сек.')
    if metrics.get('generation_seconds', 0) > 0 and 'output_tokens' in metrics:
        parts.append(f'{metrics["output_tokens"] / metrics["generation_seconds"]:.2f} ток/сек')
    if 'prompt_tokens' in metrics:
        parts.append(f'вход: {metrics["prompt_tokens"]} ток.')
    if 'cached_prompt_tokens' in metrics:
        parts.append(f'из кэша: {metrics["cached_prompt_tokens"]} ток.')
    if 'load_seconds' not in metrics:
        parts.append('Ollama не вернула подробные метрики')
    if result.get('rewrite_fallback'):
        parts.append('сокращение из полных предложений исходного ответа')
    return ' · '.join(parts)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, default=ROOT / 'dialogue_cases.json')
    parser.add_argument('--run-model', action='store_true', help='also run local Ollama inference')
    parser.add_argument('--ollama-url', default=None)
    parser.add_argument('--model', default=None)
    parser.add_argument('--max-seconds', type=float, default=120)
    parser.add_argument('--case', action='append', default=[])
    parser.add_argument('--report', type=Path, help='optional JSON report, including generated answers')
    parser.add_argument('--show-rejections', action='store_true',
                        help='include redacted rejected answers and rule IDs in console and report')
    args = parser.parse_args(argv)
    saved = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
    config = dict(owner_id=42, model=args.model or saved.get('model', MODEL),
                  ollama_url=args.ollama_url or saved.get('ollama_url', 'http://127.0.0.1:11434'))
    cases = json.loads(args.cases.read_text(encoding='utf-8'))
    if not isinstance(cases, list) or not 1 <= len(cases) <= 50:
        parser.error('cases must contain 1–50 dialogues')
    if args.case:
        cases = [case for case in cases if case['id'] in args.case]
        if len(cases) != len(set(args.case)):
            parser.error('unknown case id')
    results = []
    print('Telegram отключён. Основная база и банк не изменяются. Веб-поиск отключён.', flush=True)
    if args.run_model and any(uses_model(case) for case in cases):
        try:
            initial = warm_model(config)
        except APIError as error:
            print(f'Не удалось подготовить Ollama; HTTP={error.code}. Тест ответов не запускался.', flush=True)
            return 1
        print(f'Подготовка модели отдельно от сценариев: {initial["seconds"]:.2f} сек. '
              f'· постоянный контекст {initial["context"]} ток.', flush=True)
    for case in cases:
        result = evaluate(case, config, args.run_model, args.max_seconds, capture_rejections=args.show_rejections)
        results.append(result)
        status = 'FAIL' if result['errors'] else 'OK'
        print(f'{status} {case["id"]}: {result["actual_mode"]} · {timing_text(result)}', flush=True)
        if result.get('answer'):
            print(result['answer'], flush=True)
        if result['errors']:
            print('Причины: ' + ', '.join(result['errors']), flush=True)
        if result.get('rejections'):
            print('Отклонения до готового ответа: ' + ', '.join(result['rejections']), flush=True)
        for detail in result.get('rejection_details', []):
            print(f'Отклонённая попытка {detail["attempt"]}: {detail["reason"]} · правило={detail["rule"]}', flush=True)
            if detail.get('words') is not None:
                print(f'Слов: {detail["words"]}; предел: {detail["max_words"]}', flush=True)
            print(detail.get('answer') or '[нет текстового ответа]', flush=True)
            if detail.get('fragment'):
                print('Сработавший фрагмент: ' + detail['fragment'], flush=True)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    failures = sum(bool(result['errors']) for result in results)
    checked = sum(result['model_checked'] for result in results)
    local = sum(bool(result.get('local_reply')) for result in results)
    print(f'Проверено: {len(results)}; с моделью: {checked}; локальных уточнений: {local}; ошибок: {failures}. '
          'Автопроверки не заменяют оценку смысла ответов человеком.')
    return int(bool(failures))


if __name__ == '__main__':
    raise SystemExit(main())
