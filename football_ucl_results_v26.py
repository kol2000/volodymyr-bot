#!/usr/bin/env python3
"""V26 strict observation continuity for V22, without changing live V22.

A confirmed UEFA+ESPN score must remain continuously verifiable across
observations. Missing source, conflicting scores, changed kickoff or absence
of full-time status invalidates the earlier observation timestamp.

Runs V22's full validation / ESPN / SQLite paths with a hardened evaluator.
Default is DRY RUN; --observe touches only V22 observation JSON.
Do not enable --commit unattended before live acceptance review.
"""
from datetime import datetime, timedelta
import football_ucl_results_v22 as v22


def evaluate(fixtures, published, espn, observations, now):
    """Drop any stale continuity record on verification failure.

    The first confirmed score merely starts the >=30 minute clock. After a
    failure, the clock starts from scratch when both sources agree again.
    """
    ready = []
    updated = dict(observations)
    messages = []
    for fid, rnd, kickoff, home, away, display_home, display_away, done in fixtures:
        label = f'#{fid} R{rnd} {home} - {away}'
        token = '|'.join((kickoff.date().isoformat(), home, away))

        def reset(reason):
            if token in updated:
                del updated[token]
                messages.append(label + ' RESET prior observation: ' + reason)
            else:
                messages.append(label + ' ' + reason)

        if done:
            updated.pop(token, None)
            messages.append(label + ' ALREADY RECORDED')
            continue
        key = (rnd, kickoff.date().isoformat(), display_home, display_away)
        score = published.get(key)
        if score is None:
            reset('WAIT UEFA score')
            continue
        if now < kickoff + timedelta(hours=12):
            reset('WAIT >=12h after kickoff')
            continue
        provider = espn.get((kickoff.date().isoformat(), display_home, display_away))
        if provider is None:
            reset('WAIT ESPN STATUS_FULL_TIME')
            continue
        espn_kickoff, espn_score, event_id = provider
        if abs((espn_kickoff - kickoff).total_seconds()) > 30 * 60:
            reset('BLOCK kickoff discrepancy')
            continue
        if score != espn_score:
            reset(f'BLOCK score discrepancy UEFA={score}, ESPN={espn_score}')
            continue
        prior = updated.get(token)
        since = None
        if isinstance(prior, dict) and prior.get('score') == list(score):
            try:
                since = datetime.fromisoformat(prior['first_seen_utc'])
                if since.tzinfo is None or since > now:
                    since = None
            except (KeyError, TypeError, ValueError):
                since = None
        if since is None:
            updated[token] = {'score': list(score), 'first_seen_utc': now.isoformat()}
            messages.append(label + ' OBSERVE first matching final score; wait >=30m')
            continue
        if now - since < timedelta(minutes=30):
            messages.append(label + ' WAIT >=30m stable score')
            continue
        ready.append((fid, score[0], score[1], str(event_id), rnd))
        messages.append(label + f' READY {score[0]}:{score[1]} ESPN={event_id}')
    return ready, updated, messages


def main():
    # In this process only: retain the original V22 CLI, validators and writer.
    v22.evaluate = evaluate
    v22.main()


if __name__ == '__main__':
    main()
