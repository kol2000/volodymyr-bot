#!/usr/bin/env python3
"""Bubus Football Forecast Formatter V1.

Format existing V8 research predictions.

OFFLINE / READ ONLY.
No network, database, or Telegram operations.
"""

from datetime import date


LEAGUE_NAMES = {
    "EPL": "Английская Премьер-лига",
    "LaLiga": "Ла Лига",
    "SerieA": "Серия А",
    "Bundesliga": "Бундеслига",
    "Ligue1": "Лига 1",
}


def pct(value):
    return f"{value * 100:.1f}%".replace(".", ",")


def number(value):
    return f"{value:.2f}".replace(".", ",")


def render_forecast(
    league,
    match_date,
    home,
    away,
    probabilities,
    goals,
    verified=False,
):
    """Return Telegram-ready plain text."""

    p_home, p_draw, p_away = probabilities

    expected_home = goals["expected_home"]
    expected_away = goals["expected_away"]

    expected_total = expected_home + expected_away

    over = goals["over"]

    score = goals["most_likely_score"]

    lines = [
        "⚽ БУБУС | ПРОГНОЗ МАТЧА",
        "",
        f"🏆 {LEAGUE_NAMES.get(league, league)}",
        f"📅 {match_date:%d.%m.%Y}",
        f"⚽ {home} — {away}",
        "",
        "📊 ИСХОД МАТЧА",
        "",
        f"П1 — {pct(p_home)}",
        f"Х — {pct(p_draw)}",
        f"П2 — {pct(p_away)}",
        "",
        "🥅 ОЖИДАЕМЫЕ ГОЛЫ",
        "",
        f"{home}: {number(expected_home)}",
        f"{away}: {number(expected_away)}",
        f"Всего: {number(expected_total)}",
        "",
        "📈 ТОТАЛЫ",
        "",
    ]

    for threshold in (1.5, 2.5, 3.5, 4.5):
        if threshold not in over:
            continue

        p = over[threshold]

        lines.extend([
            f"ТБ {threshold:.1f} — {pct(p)}",
            f"ТМ {threshold:.1f} — {pct(1 - p)}",
            "",
        ])

    lines.extend([
        "⚽ ДОПОЛНИТЕЛЬНО",
        "",
        f"Обе забьют — {pct(goals['btts'])}",
        f"Вероятный счёт — {score[0]}:{score[1]}",
        "",
    ])

    # Describe statistical tendencies,
    # not guaranteed outcomes.

    if expected_total >= 3.5:
        tendency = "Ожидается высокая результативность."
    elif expected_total <= 2.2:
        tendency = "Модель ожидает относительно мало голов."
    else:
        tendency = "Ожидается умеренная результативность."

    lines.extend([
        "🎯 ОЦЕНКА МОДЕЛИ",
        "",
        tendency,
        "",
    ])

    if not verified:
        lines.append(
            "⚠️ Исследовательский прогноз. "
            "Время начала матча не подтверждено."
        )

    return "\n".join(lines)


def main():

    print("=== BUBUS FORMATTER V1 ===")
    print("MODE: OFFLINE / READ ONLY")

    # Sample taken from previously calculated
    # V8 research output.
    # No new forecast is generated.

    sample_goals = {
        "expected_home": 2.27,
        "expected_away": 1.15,
        "over": {
            1.5: 0.862,
            2.5: 0.665,
            3.5: 0.446,
        },
        "btts": 0.621,
        "most_likely_score": (2, 1),
    }

    message = render_forecast(
        league="Bundesliga",
        match_date=date(2026, 11, 27),
        home="Вердер",
        away="Боруссия Мёнхенгладбах",
        probabilities=(0.599, 0.207, 0.194),
        goals=sample_goals,
        verified=False,
    )

    print()
    print("=" * 55)
    print(message)
    print("=" * 55)

    print("\n=== STATUS ===")
    print("FORMATTER TEST COMPLETED")
    print("SAMPLE VALUES FROM PREVIOUS V8 OUTPUT")
    print("NO LIVE FORECASTS GENERATED")
    print("NO DATABASE WRITES")
    print("NO TELEGRAM MESSAGES")


if __name__ == "__main__":
    main()
