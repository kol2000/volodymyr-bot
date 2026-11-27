# AGENTS.md — правила продолжения разработки

## Начало работы
Прочитай PROJECT_STATUS.md и TODO.md. Для прогнозов затем FORECASTING.md. CHANGELOG.md нужен только для истории решений.
Не загружай всю переписку, state/ или все исходники: найди нужные модули через rg.
Документация проверена 2026-11-27; состояние служб и внешних источников проверяй заново.

## Где находится проект
- Рабочая ВМ: Ubuntu 24.04, Python 3.12.3; пользователь botadmin.
- Рабочий репозиторий: /home/botadmin/volodymyr_bot.
- Локальная копия: C:/Users/Admin/Documents/Codex/2026-10-23/ghj/work/volodymyr-bot.
- GitHub: https://github.com/kol2000/volodymyr-bot. Локальная копия, ВМ и GitHub НЕ синхронизированы. Перед правкой сравни файлы; не загружай устаревшую локальную копию поверх ВМ.
- SSH из Windows: ssh -F work/ssh-bubus/config bubus-vm (из C:/Users/Admin/Documents/Codex/2026-10-23/ghj). Ключи не читать и не включать в коммиты.

## Архитектура
- bot.py — Telegram long polling, маршрутизация и фоновые проверки; common.py — общие API/хранилище; conversation.py, question_answers.py, chat_services.py — ответы; inference.py — вызов настроенной модели.
- prompt.txt, manual_reply_prompt.txt, answer_quality.py — манера общения; learning.py/retrieval.py — архив и обучение. Не заменять короткую разговорную манеру канцелярскими отказами.
- live_football.py, kulichki_football.py, soccer365_football.py, football_posts.py — календарь, статусы, события и посты. Источники дополняют друг друга; объединять по идентичности команды/матча, а не буквальному названию.
- goal_videos.py — сопоставление/очередь видео; goal_caption_language.py и goal_caption_semantics.py — подписи; broadcast_video_evidence.py — кадры/табло; goal_video_media.py — проверка файлов.
- desktop_reader.py, telegram_reader*.py, desktop worker — получение видео через резервный аккаунт. Реальная служба загрузчика запускает /usr/local/bin/bubus-desktop-worker.py: редактирование одной копии в репозитории не гарантирует обновление службы.
- attach_match_album.py, match_album_worker.py — проверка альбомов, склейка и обновление итогового поста.
- football_club_names*.{py,json}, national_team_names*.{py,json} — общий словарь одиночных голов и альбомов; football_name_audit.py — журнал unknown_football_name.
- football_predictions.py, prediction_buttons.py — пользовательские прогнозы. football_forecast_*, football_europe_*, football_ucl_*, football_rpl_* — отдельные исследовательские/календарные цепочки. Номер версии не означает, что предыдущий файл не используется: проверь импорты и systemd.
- admin_panel.py/web_panel/ — веб-панель; currency_settings.py — настройки валют.

## Ограничения и сохранность данных
- Не менять логику при задаче только на документацию. Не публиковать в Telegram без соответствующего задания; проверки по умолчанию без отправки.
- state/ содержит конфигурацию, SQLite, очереди, исходные прогнозы и исторические снимки. Не удалять, не коммитить и не выгружать целиком. Не выводить токены, Telegram-сессии, пароли или приватную переписку.
- Прогнозы V8 и result_events неизменяемы: проверять исходный пакет, SHA-256 и регистрацию до матча. Нельзя пересчитывать старый прогноз задним числом или считать исследовательские production_eligible=False готовыми для публикации.
- Франция: время начала UNVERIFIED; не выдумывать часовой пояс. Результат допускается через 48 часов после конца календарного дня. Изменение даты даёт DATE_CHANGED и пропуск, а не автоматическую оценку.
- Гол РПЛ/Кубка России запускает индивидуальный поиск в МАТЧ ПРЕМЬЕР; окно проверки 30 минут. Не требовать фамилии автора/обеих команд в подписи. При неоднозначности использовать существующий разбор подписи и проверку табло/времени. Не прикреплять любой ближайший ролик лишь по времени.
- Не смешивать отменённый гол и «на грани офсайда». Не учитывать цитату предыдущего поста как текущую подпись. Альбом не присваивать одному голу.
- Сохранять защиту от повторной отправки и неопределённого результата API. Не сбрасывать uncertain/editing без проверки Telegram.
- Перед отправкой проверять полное декодирование видео. Размер файла/наличие MP4-заголовка не доказывает исправность.
- Не объединять неоднозначные названия: Париж ≠ ПСЖ, Динамо Москва ≠ Киев, Ирландия ≠ Северная Ирландия. 261 запись стран/территорий не означает 261 сборную FIFA.

## Окружение и команды
Основной код использует stdlib/SQLite FTS5; дополнительные инструменты видео: ffmpeg/ffprobe и Tesseract rus+eng. Модель вызывается через настроенный Ollama-совместимый endpoint: README про исключительно локальную модель может быть устаревшим; не менять endpoint наугад.

```bash
cd /home/botadmin/volodymyr_bot
systemctl is-active volodymyr-bot
systemctl list-timers --all --no-pager
journalctl -u volodymyr-bot --since '10 minutes ago' --no-pager -n 50
PYTHONPATH=.:tests python3 -B -m unittest tests.test_name_audit tests.test_club_names tests.test_national_goals tests.test_expanded_names tests.test_match_album tests.test_venezia_album tests.test_weekend_albums tests.test_havre_album
python3 -B football_forecast_v8_europe_results.py
python3 -B football_forecast_v8_ligue1_register_v2.py
```
Последние две команды по умолчанию читают данные без записи; --write имеет последствия. Полный unittest discover не считать заведомо зелёным: см. TODO.

Развёртывание: сверить текущие файлы ВМ → резервная копия изменяемых файлов → проверка на изолированных данных → атомарная замена → адресная проверка. Не копировать весь локальный проект поверх ВМ. Импортируемый основным ботом код требует перезапуска; отдельный скрипт таймера подхватится при следующем запуске.

```bash
sudo systemctl restart volodymyr-bot
```
Ранее у подключения не было права sudo; пароль вводит пользователь. Не просить повторно, если право уже предоставлено; не заявлять об успешном перезапуске без проверки. Для документации перезапуск не нужен.

Службы: volodymyr-bot, volodymyr-panel (8088), bubus-desktop-worker, bubus-desktop-export, bubus-telegram-desktop. Загрузчик работает от bubus-reader; медиа — /var/lib/bubus-media/verified. Проверяй systemd ReadWritePaths вместе с UNIX-правами: обычного chmod может быть недостаточно.

Windows: явная UTF-8 при чтении/записи; PowerShell может добавить BOM. Для удаления/переноса проверять абсолютный путь, не смешивать PowerShell и cmd. На ВМ кэши можно восстановить; архивы исследований и старые версии модулей — не мусор.

Общие прогнозы: forecast_service.py + forecast_cycle.py; запускаются отдельно от исторических V8-архивов. Не смешивать экспериментальные метрики с доказанной точностью. Журнал новых прогнозов также исключён из Git.

Еженедельный отчёт: forecast_weekly.py; отдельный bubus-forecast-weekly.timer (пн 10:00 МСК). Без --send только предпросмотр. Не удалять weekly_reports/*/attempt.json ради повторного запуска: возможна уже успешная отправка. Детали FORECASTING.md. Актуальная опубликованная локальная ветка — work/forecast-release, не старая work/volodymyr-bot.

Развлекательные рубрики: chat_features.py и CHAT_FEATURES.md. Таблица chat_archive хранит привязанный чат бессрочно; триггеры отделяют архив от 30-дневного контекста. Не публиковать архив/предпросмотры в Git. Удаление данных пользователя должно учитывать chat_archive.

Зрение: visual_context.py, VISION.md. Gemma даёт описание основной Qwen. Не логировать URL Telegram/картинки, не превращать текст на изображении в инструкции. TGS пока только превью.

Личная память: personal_memory.py, PERSONAL_MEMORY.md. По умолчанию off; profile/history/banter отдельно. Использовать только self-reported confirmed записи текущих chat_id/user_id. Не импортировать догадки/архив в факты и не публиковать личные данные в Git.

Обновление по прямому указанию владельца: память/history теперь default-on. Используются observed с явной неопределённостью и confirmed с приоритетом; pending конфликты не используются. Явный opt-out сохраняется. PERSONAL_MEMORY.md содержит актуальные правила, прежний opt-in отменён.
