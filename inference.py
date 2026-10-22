"""Stable Ollama runner settings and non-sensitive per-call timing records."""
import math

MODEL_CONTEXT = 12288
MODEL_THREADS = 16


def runner_options(**sampling):
    # NumCtx is a runner option: alternating sizes may unload/reload the model.
    return dict(sampling, num_ctx=MODEL_CONTEXT, num_thread=MODEL_THREADS)


def numeric(value):
    try:
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def generation_stats(result, elapsed, context=MODEL_CONTEXT):
    stats = dict(seconds=round(elapsed, 3), context=context)
    if not isinstance(result, dict):
        return stats
    for source, target in [('load_duration', 'load_seconds'), ('prompt_eval_duration', 'prompt_seconds'),
                           ('eval_duration', 'generation_seconds'), ('total_duration', 'ollama_seconds')]:
        value = result.get(source)
        if numeric(value):
            stats[target] = round(value / 1_000_000_000, 3)
    for source, target in [('prompt_eval_count', 'prompt_tokens'), ('prompt_eval_cached_count', 'cached_prompt_tokens'),
                           ('eval_count', 'output_tokens')]:
        value = result.get(source)
        if type(value) is int and value >= 0:
            stats[target] = value
    if stats.get('generation_seconds', 0) > 0 and 'output_tokens' in stats:
        stats['tokens_per_second'] = round(stats['output_tokens'] / stats['generation_seconds'], 2)
    measured = ('load_seconds', 'prompt_seconds', 'generation_seconds')
    if all(key in stats for key in measured):
        # This includes scheduling, transport and other overhead; it is not an
        # exact server queue measurement. Do not label it as queue time.
        stats['other_seconds'] = round(max(0, elapsed - sum(stats[key] for key in measured)), 3)
    return stats


def record_generation(job, result, elapsed, context=MODEL_CONTEXT):
    stats = generation_stats(result, elapsed, context)
    job.setdefault('_inference_calls', []).append(stats)
    metrics = job.setdefault('_metrics', {})
    metrics['model_calls'] = metrics.get('model_calls', 0) + 1
    metrics['model_seconds'] = round(metrics.get('model_seconds', 0) + elapsed, 3)
    metrics['runner_context'] = context
    for key in ('load_seconds', 'prompt_seconds', 'generation_seconds', 'other_seconds',
                'prompt_tokens', 'cached_prompt_tokens', 'output_tokens'):
        if key in stats:
            metrics[key] = round(metrics.get(key, 0) + stats[key], 3)
    return stats
