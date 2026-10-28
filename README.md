# COLLEGE RECORDS — Telegram-бот open call

Бот регистрирует студентов как музыкальные релизы: анкета из 8 шагов → выбор из 6 макетов → готовая JPG-карточка → заявка админам.

## Быстрый старт (локально)

```bash
cd /Users/mark/college-records
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # и впиши BOT_TOKEN + ADMIN_IDS
python bot.py
```

`.env`:
```
BOT_TOKEN=токен_от_BotFather
ADMIN_IDS=5539170430          # свой TG id, через запятую если несколько
DB_PATH=college_records.sqlite3
```

Узнать свой id: напиши @userinfobot в Telegram.

## Команды юзера

- `/start` — начать, `/help` — помощь
- 🎧 Зарегистрироваться — анкета (имя → группа → artist → album → genre → track → 3 слова → фото → макет)
- `/status` — номер заявки CR-001 + статус
- `/mycard` — прислать готовую карточку ещё раз
- `/cancel` — отмена в любом месте (работает даже на шаге фото/макета)

На экране проверки: ✏️ Изменить поле (без перезаполнения), 🎨 Другой макет, 📸 Другое фото.

## Команды организатора

- `/count` — всего + по группам + сколько принято
- `/groups` — список групп
- `/group НАЗВАНИЕ` — все карточки группы (пачками, с анти-флудом)
- `/export` — CSV (открывается в Excel, разделитель `;`)
- `/broadcast ТЕКСТ` — рассылка всем участникам
- `/delete CR-001` — удалить заявку
- Кнопки под каждой новой заявкой: ✅ Принять / ❌ Отклонить (юзеру уходит уведомление)
- 🛠 Админ-панель — кнопка в меню у админов

## Файлы

```
bot.py               — весь бот (aiogram 3, FSM)
card_generator.py    — рендер JPG-карточки (Pillow)
template_grunge.png  — макет №1 (оригинал)
templates/           — макеты №2–6 + choose.jpg (коллаж выбора)
fonts/ + card_font.ttf — шрифты для карточек
make_templates.py    — пересобрать макеты из template_grunge.png
requirements.txt     — aiogram + Pillow + python-dotenv
Dockerfile, docker-compose.yml — деплой
```

## Деплой (VPS, Timeweb/Beget/Aeza)

```bash
docker compose up -d --build
docker compose logs -f
```

Переменные задаются в `docker-compose.yml` или `.env` рядом. База — SQLite-файл (volume `sqlite_data`).

## Что исправлено в этой версии

- `/cancel` работает из любого состояния (раньше шаги фото/макета его перехватывали)
- Команды `/start /help /cancel` больше не глотаются шагами анкеты
- Живое превью карточки перед отправкой + точечное «Изменить поле»
- `/mycard`, `/help`, админ-панель кнопками, `/broadcast`, `/delete`, модерация ✅/❌ со статусами
- Анти-флуд шагов + лимит фото 10 МБ + чистка пробелов/переносов
- `logging` вместо `print`, `delete_webhook`, `set_my_commands`, индексы в БД
- Токен больше не зашит в код — только `.env` (старый токен отозвать в @BotFather → /revoke!)
