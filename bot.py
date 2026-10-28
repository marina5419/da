import asyncio
import csv
import io
import logging
import os
import sqlite3
import html
import time
from datetime import datetime, timezone
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, BufferedInputFile, FSInputFile,
    BotCommand, MenuButtonCommands,
)
from card_generator import render_card, TEMPLATES, DEFAULT_TEMPLATE, BASE_DIR
import track_maker as tm

try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
    load_dotenv(".env")
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
log = logging.getLogger("college_records")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {
    int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}
_raw_db = os.getenv("DB_PATH", "college_records.sqlite3").strip() or "college_records.sqlite3"
_db_p = Path(_raw_db)
DB_PATH = str(_db_p if _db_p.is_absolute() else (BASE_DIR / _db_p))

# Простой анти-флуд: user_id -> unix-время последнего шага
_last_step: dict[int, float] = {}
STEP_COOLDOWN = 0.7


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def check_throttle(user_id: int) -> bool:
    """True — можно продолжать, False — юзер спамит, надо подождать."""
    now = time.monotonic()
    if now - _last_step.get(user_id, 0.0) < STEP_COOLDOWN:
        return False
    _last_step[user_id] = now
    return True


def clean_text(text: str | None, limit: int) -> str | None:
    t = (text or "").strip().replace("\n", " ")
    t = " ".join(t.split())
    if not t or len(t) > limit:
        return None
    return t

if not BOT_TOKEN:
    raise RuntimeError(
        "Не найден BOT_TOKEN. Создайте бота через @BotFather и задайте переменную окружения BOT_TOKEN."
    )

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


class Registration(StatesGroup):
    consent = State()
    full_name = State()
    group_name = State()
    artist_name = State()
    album = State()
    genre = State()
    track = State()
    three_words = State()
    photo = State()
    style = State()
    confirm = State()
    edit_field = State()  # юзер выбрал «✏️ Изменить поле» и теперь вводит новое значение


FIELD_META = {
    "full_name": ("Имя", 80, "1/8. Как тебя зовут?"),
    "group_name": ("Группа", 80, "2/8. Из какой ты группы? Например: 2М 3П."),
    "artist_name": ("ARTIST", 80, "3/8. ARTIST — имя или псевдоним на карточке?"),
    "album": ("ALBUM", 120, "4/8. ALBUM — название дебютного альбома?"),
    "genre": ("GENRE", 100, "5/8. GENRE — жанр твоего характера?"),
    "track": ("TRACK", 160, "6/8. TRACK — песня + исполнитель?"),
    "three_words": ("Три слова", 100, "7/8. Три слова о тебе через пробел/запятую."),
}


def db_connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with db_connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS registrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                username TEXT,
                full_name TEXT NOT NULL,
                group_name TEXT NOT NULL,
                artist_name TEXT NOT NULL,
                album TEXT NOT NULL,
                genre TEXT NOT NULL,
                track TEXT NOT NULL,
                about TEXT NOT NULL DEFAULT '',
                three_words TEXT NOT NULL DEFAULT '',
                photo_file_id TEXT,
                created_at TEXT NOT NULL,
                template_id INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'new'
            )
        """)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(registrations)").fetchall()}
        if "three_words" not in columns:
            conn.execute("ALTER TABLE registrations ADD COLUMN three_words TEXT NOT NULL DEFAULT ''")
        if "template_id" not in columns:
            conn.execute(f"ALTER TABLE registrations ADD COLUMN template_id INTEGER NOT NULL DEFAULT {DEFAULT_TEMPLATE}")
        if "status" not in columns:
            conn.execute("ALTER TABLE registrations ADD COLUMN status TEXT NOT NULL DEFAULT 'new'")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_reg_group ON registrations(group_name)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_reg_user ON registrations(telegram_id)")
        # Миграция со старой схемы (одна карточка на человека): снимаем UNIQUE если он есть
        try:
            cols = conn.execute("SELECT sql FROM sqlite_master WHERE name='registrations'").fetchone()[0] or ""
            if "UNIQUE" in cols.upper():
                conn.execute("""CREATE TABLE IF NOT EXISTS registrations_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER NOT NULL,
                    username TEXT,
                    full_name TEXT NOT NULL,
                    group_name TEXT NOT NULL,
                    artist_name TEXT NOT NULL,
                    album TEXT NOT NULL,
                    genre TEXT NOT NULL,
                    track TEXT NOT NULL,
                    about TEXT NOT NULL DEFAULT '',
                    three_words TEXT NOT NULL DEFAULT '',
                    photo_file_id TEXT,
                    created_at TEXT NOT NULL,
                    template_id INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'new'
                )""")
                conn.execute("INSERT OR IGNORE INTO registrations_new SELECT * FROM registrations")
                conn.execute("DROP TABLE registrations")
                conn.execute("ALTER TABLE registrations_new RENAME TO registrations")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_reg_group ON registrations(group_name)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_reg_user ON registrations(telegram_id)")
        except Exception as exc:
            log.warning("migration unique->multi failed: %s", exc)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_reg_created ON registrations(created_at)")


def main_menu(is_admin_user: bool = False):
    rows = [[KeyboardButton(text="🎧 Зарегистрироваться")]]
    if is_admin_user:
        rows.append([KeyboardButton(text="🛠 Админ-панель")])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        input_field_placeholder="🎧 выбери действие…",
        is_persistent=True,
    )


def admin_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="📚 Группы")],
            [KeyboardButton(text="📄 Экспорт CSV"), KeyboardButton(text="📣 Рассылка")],
            [KeyboardButton(text="⬅️ Назад")],
        ],
        resize_keyboard=True,
        input_field_placeholder="🛠 админ-команда…",
        is_persistent=True,
    )


def consent_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🖤 Участвую", callback_data="consent_yes")],
        [InlineKeyboardButton(text="🚫 Пока не хочу", callback_data="consent_no")],
    ])


STYLE_PER_PAGE = 6
STYLE_PAGES = (len(TEMPLATES) + STYLE_PER_PAGE - 1) // STYLE_PER_PAGE


def style_keyboard(page: int = 0):
    page = max(0, min(page, STYLE_PAGES - 1))
    keys = sorted(TEMPLATES)[page * STYLE_PER_PAGE:(page + 1) * STYLE_PER_PAGE]
    rows = [
        [InlineKeyboardButton(text=f"{n} · {TEMPLATES[n]['name'].capitalize()}", callback_data=f"style_{n}")]
        for n in keys
    ]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"styles_{page - 1}"))
    if page < STYLE_PAGES - 1:
        nav.append(InlineKeyboardButton(text=f"Вперёд ({page + 1}/{STYLE_PAGES}) ➡️", callback_data=f"styles_{page + 1}"))
    elif page > 0:
        nav.append(InlineKeyboardButton(text=f"• {page + 1}/{STYLE_PAGES} •", callback_data="styles_noop"))
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)


_style_sheet_file_id = None  # после первой отправки коллажа Telegram запоминает файл, повторно не грузим


async def send_style_choice(message: Message, page: int = 0):
    page = max(0, min(page, STYLE_PAGES - 1))
    caption = (
        f"🎨 <b>Выбери макет своей карточки ({page + 1}/{STYLE_PAGES})</b>\n"
        "Нажми на номер макета. Листай кнопкой «Вперёд» — там ещё."
    )
    sheet = FSInputFile(BASE_DIR / "templates" / f"choose_{page + 1}.jpg")
    await message.answer_photo(sheet, caption=caption, parse_mode="HTML", reply_markup=style_keyboard(page))


def confirm_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отправить заявку", callback_data="submit_yes")],
        [InlineKeyboardButton(text="✏️ Изменить поле", callback_data="edit_menu"),
         InlineKeyboardButton(text="🎨 Другой макет", callback_data="restyle")],
        [InlineKeyboardButton(text="📸 Другое фото", callback_data="rephoto"),
         InlineKeyboardButton(text="🔄 Заново", callback_data="submit_restart")],
        [InlineKeyboardButton(text="❌ Отменить", callback_data="submit_cancel")],
    ])


def edit_menu_keyboard():
    rows = [
        [InlineKeyboardButton(text=f"✏️ {label}", callback_data=f"edit_{key}")]
        for key, (label, _lim, _q) in FIELD_META.items()
    ]
    rows.append([InlineKeyboardButton(text="📸 Другое фото", callback_data="rephoto")])
    rows.append([InlineKeyboardButton(text="🎨 Другой макет", callback_data="restyle")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад к проверке", callback_data="edit_back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


STATUS_LABEL = {"new": "✅ участник", "approved": "✅ участник", "rejected": "❌ отклонена"}


def safe(value):
    return value if value else "—"


def make_summary(data):
    tid = data.get("template_id") if data.get("template_id") in TEMPLATES else DEFAULT_TEMPLATE
    return (
        "🎧 <b>ТВОЯ КАРТОЧКА COLLEGE RECORDS</b>\n\n"
        f"<b>Имя:</b> {html.escape(safe(data.get('full_name')))}\n"
        f"<b>Группа:</b> {html.escape(safe(data.get('group_name')))}\n"
        f"<b>ARTIST:</b> {html.escape(safe(data.get('artist_name')))}\n"
        f"<b>ALBUM:</b> {html.escape(safe(data.get('album')))}\n"
        f"<b>GENRE:</b> {html.escape(safe(data.get('genre')))}\n"
        f"<b>TRACK:</b> {html.escape(safe(data.get('track')))}\n\n"
        f"<b>Три слова:</b> {html.escape(safe(data.get('three_words')))}\n\n"
        f"<b>Макет:</b> {TEMPLATES[tid]['name'].capitalize()} (№{tid})\n\n"
        "Проверь всё. Можешь поправить одно поле кнопкой «✏️ Изменить поле» — заново вводить всё не нужно."
    )


async def preview_card_bytes(data: dict, reg_id: int = 0) -> bytes | None:
    """Собрать JPG-превью из FSM-данных (фото уже загружено как file_id)."""
    file_id = data.get("photo_file_id")
    if not file_id:
        return None
    try:
        tg_file = await bot.get_file(file_id)
        buf = io.BytesIO()
        await bot.download_file(tg_file.file_path, destination=buf)
        return await asyncio.to_thread(render_card, buf.getvalue(), data, reg_id or None)
    except Exception as exc:
        log.warning("preview card failed: %s", exc)
        return None


async def show_confirm(message: Message, state: FSMContext):
    """Показать саммари + живое превью карточки + кнопки."""
    data = await state.get_data()
    await state.set_state(Registration.confirm)
    await message.answer(make_summary(data), parse_mode="HTML", reply_markup=confirm_keyboard())
    preview = await preview_card_bytes(data)
    if preview:
        await message.answer_photo(
            BufferedInputFile(preview, filename="college_records_preview.jpg"),
            caption="👆 Так будет выглядеть твоя карточка. Если что-то не так — жми «✏️ Изменить поле».",
        )


async def begin_registration(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(Registration.consent)
    await message.answer(
        "🎧 <b>COLLEGE RECORDS — OPEN CALL</b>\n\n"
        "Представь, что ты выпускаешь свой первый музыкальный релиз.\n"
        "\n"
        "Бот спросит имя, группу, псевдоним, альбом, жанр, трек и три слова о тебе. "
        "Фото можно отправить в конце.\n\n"
        "Участие добровольное. Перед отправкой заявки проверь данные.",
        reply_markup=consent_keyboard()
    )


@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    admin = is_admin(message.from_user.id)
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT id FROM registrations WHERE telegram_id = ? ORDER BY id", (message.from_user.id,)
        ).fetchall()
    if rows:
        nums = ", ".join(f"CR-{r['id']:03d}" for r in rows)
        extra = f"\n\nУ тебя уже есть карточки: {nums} 🖤 Каждая новая регистрация = новая карточка, лимита нет. Команды: /mycards — все мои карточки, /status — список."
    else:
        extra = ""
    banner = BASE_DIR / "brand" / "banner.jpg"
    caption = (
        "Это регистрация в <b>COLLEGE RECORDS</b> — проекте, где каждый студент "
        f"становится отдельным музыкальным релизом.\n\nГотов представить себя?{extra}"
    )
    try:
        if banner.exists():
            await message.answer_photo(
                FSInputFile(banner),
                caption=caption,
                parse_mode="HTML",
                reply_markup=main_menu(admin),
            )
        else:
            await message.answer(caption, parse_mode="HTML", reply_markup=main_menu(admin))
    except Exception as exc:
        log.warning("banner send failed: %s", exc)
        await message.answer(caption, parse_mode="HTML", reply_markup=main_menu(admin))


@dp.message(Command("help"))
async def help_cmd(message: Message):
    admin = is_admin(message.from_user.id)
    text = (
        "🎧 <b>COLLEGE RECORDS — помощь</b>\n\n"
        "/start — начать\n"
        "🎧 Зарегистрироваться — пройти анкету (8 шагов)\n"
        "/status — мой номер\n"
        "/mycards — все мои карточки\n"
        "/mycard CR-001 — одну карточку по номеру\n"
        "/cancel — отменить ввод"
    )
    if admin:
        text += (
            "\n\n🛠 <b>Организаторам:</b>\n"
            "/count — всего + по группам\n"
            "/groups — список групп\n"
            "/group НАЗВАНИЕ — карточки группы\n"
            "/export — CSV всех заявок\n"
            "/broadcast ТЕКСТ — рассылка всем участникам\n"
            "/delete CR-001 — удалить заявку\n"
            "/help — это сообщение"
        )
    await message.answer(text, parse_mode="HTML", reply_markup=main_menu(admin))


@dp.message(Command("cancel"), StateFilter("*"))
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Регистрация отменена. Если захочешь вписаться — нажми «🎧 Зарегистрироваться».",
        reply_markup=main_menu(is_admin(message.from_user.id)),
    )


@dp.message(Command("status"))
async def status(message: Message):
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT id, artist_name, group_name, status FROM registrations WHERE telegram_id = ? ORDER BY id",
            (message.from_user.id,)
        ).fetchall()
    if rows:
        lines = [f"Ты в лейбле 🖤 Карточек: {len(rows)}", ""]
        for r in rows:
            st = STATUS_LABEL.get(r["status"] or "new", r["status"])
            lines.append(f"• <b>CR-{r['id']:03d}</b> — {html.escape(r['artist_name'] or '—')} ({html.escape(r['group_name'] or '—')}) — {st}")
        lines += ["", "Новая карточка — кнопка «🎧 Зарегистрироваться». Все мои карточки — /mycards."]
        await message.answer("\n".join(lines), parse_mode="HTML")
    else:
        await message.answer("Пока карточек нет. Нажми «🎧 Зарегистрироваться», чтобы создать первую.")


@dp.message(Command("mycard"))
async def mycard(message: Message):
    """Одна карточка: /mycard CR-001. Без номера — последняя созданная."""
    arg = (message.text or "").partition(" ")[2].strip().upper().replace("CR-", "").replace("CR", "").strip()
    with db_connect() as conn:
        if arg.isdigit():
            row = conn.execute(
                "SELECT * FROM registrations WHERE id = ? AND telegram_id = ?", (int(arg), message.from_user.id)
            ).fetchone()
            if not row:
                await message.answer("У тебя нет карточки с таким номером. Посмотри свои: /mycards.")
                return
        else:
            row = conn.execute(
                "SELECT * FROM registrations WHERE telegram_id = ? ORDER BY id DESC LIMIT 1", (message.from_user.id,)
            ).fetchone()
            if not row:
                await message.answer("Карточек пока нет — нажми «🎧 Зарегистрироваться».")
                return
    data = dict(row)
    await message.answer("Собираю твою карточку… 🖤")
    card = await preview_card_bytes(data, data["id"])
    if card:
        await message.answer_photo(
            BufferedInputFile(card, filename=f"college_records_CR-{data['id']:03d}.jpg"),
            caption=f"🎧 <b>CR-{data['id']:03d}</b> — {html.escape(data.get('artist_name') or '—')} · {html.escape(data.get('album') or '—')}",
            parse_mode="HTML",
        )
    else:
        log.warning("mycard failed for %s", message.from_user.id)
        await message.answer("Не получилось собрать карточку 😢 Напиши организаторам.")


@dp.message(Command("mycards"))
async def mycards(message: Message):
    """Все карточки юзера."""
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT * FROM registrations WHERE telegram_id = ? ORDER BY id", (message.from_user.id,)
        ).fetchall()
    if not rows:
        await message.answer("Карточек пока нет — нажми «🎧 Зарегистрироваться».")
        return
    await message.answer(f"У тебя карточек: <b>{len(rows)}</b>. Отправляю…", parse_mode="HTML")
    for row in rows:
        data = dict(row)
        caption = (
            f"🎧 <b>CR-{data['id']:03d}</b>\n"
            f"<b>ARTIST:</b> {html.escape(data.get('artist_name') or '—')}\n"
            f"<b>ALBUM:</b> {html.escape(data.get('album') or '—')}\n"
            f"<b>GENRE:</b> {html.escape(data.get('genre') or '—')}"
        )
        try:
            card = await preview_card_bytes(data, data["id"])
            if card:
                await message.answer_photo(
                    BufferedInputFile(card, filename=f"college_records_CR-{data['id']:03d}.jpg"),
                    caption=caption, parse_mode="HTML")
            else:
                await message.answer(caption + "\n⚠️ Карточку собрать не вышло.", parse_mode="HTML")
        except Exception as exc:
            log.warning("mycards CR-%03d failed: %s", data["id"], exc)
            await message.answer(caption + "\n⚠️ Карточку собрать не вышло.", parse_mode="HTML")
        await asyncio.sleep(0.15)


@dp.message(F.text == "🎧 Зарегистрироваться")
async def register_button(message: Message, state: FSMContext):
    await begin_registration(message, state)


@dp.callback_query(F.data == "consent_yes")
async def consent_yes(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(consent=True)
    await state.set_state(Registration.full_name)
    await callback.message.answer("1/8. Как тебя зовут?")


@dp.callback_query(F.data == "consent_no")
async def consent_no(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await callback.message.answer("Без проблем 🖤 Если передумаешь, регистрация будет ждать тебя.")


@dp.message(Registration.full_name, ~F.text.startswith("/"))
async def get_full_name(message: Message, state: FSMContext):
    if not await state.get_state():
        return
    if not check_throttle(message.from_user.id):
        return
    text = clean_text(message.text, 80)
    if not text:
        await message.answer("Напиши имя текстом (до 80 символов).")
        return
    await state.update_data(full_name=text)
    await state.set_state(Registration.group_name)
    await message.answer("2/8. Из какой ты группы? Например: 2М 3П.")


@dp.message(Registration.group_name, ~F.text.startswith("/"))
async def get_group(message: Message, state: FSMContext):
    if not await state.get_state():
        return
    if not check_throttle(message.from_user.id):
        return
    text = clean_text(message.text, 80)
    if not text:
        await message.answer("Напиши название группы (до 80 символов).")
        return
    await state.update_data(group_name=text)
    await state.set_state(Registration.artist_name)
    await message.answer("3/8. ARTIST — какое имя или псевдоним будет на твоей карточке?")


@dp.message(Registration.artist_name, ~F.text.startswith("/"))
async def get_artist(message: Message, state: FSMContext):
    if not await state.get_state():
        return
    if not check_throttle(message.from_user.id):
        return
    text = clean_text(message.text, 80)
    if not text:
        await message.answer("Напиши имя или псевдоним (до 80 символов).")
        return
    await state.update_data(artist_name=text)
    await state.set_state(Registration.album)
    await message.answer("4/8. ALBUM — как называется твой дебютный альбом? Можно серьёзно, можно иронично.")


@dp.message(Registration.album, ~F.text.startswith("/"))
async def get_album(message: Message, state: FSMContext):
    if not await state.get_state():
        return
    if not check_throttle(message.from_user.id):
        return
    text = clean_text(message.text, 120)
    if not text:
        await message.answer("Название альбома должно быть до 120 символов.")
        return
    await state.update_data(album=text)
    await state.set_state(Registration.genre)
    await message.answer("5/8. GENRE — какой музыкальный жанр описывает твой характер? Например: постпанк, инди-поп, хип-хоп, техно.")


@dp.message(Registration.genre, ~F.text.startswith("/"))
async def get_genre(message: Message, state: FSMContext):
    if not await state.get_state():
        return
    if not check_throttle(message.from_user.id):
        return
    text = clean_text(message.text, 100)
    if not text:
        await message.answer("Напиши жанр (до 100 символов).")
        return
    await state.update_data(genre=text)
    await state.set_state(Registration.track)
    await message.answer(
        "6/8. TRACK — какая одна песня ассоциируется с тобой сейчас?\n"
        "Напиши название и исполнителя."
    )


@dp.message(Registration.track, ~F.text.startswith("/"))
async def get_track(message: Message, state: FSMContext):
    if not await state.get_state():
        return
    if not check_throttle(message.from_user.id):
        return
    text = clean_text(message.text, 160)
    if not text:
        await message.answer("Напиши песню и исполнителя (до 160 символов).")
        return
    await state.update_data(track=text)
    await state.set_state(Registration.three_words)
    await message.answer("7/8. Напиши три слова, которые тебя описывают. Например: хаос, кофе, бессонница.")


@dp.message(Registration.three_words, ~F.text.startswith("/"))
async def get_three_words(message: Message, state: FSMContext):
    if not await state.get_state():
        return
    if not check_throttle(message.from_user.id):
        return
    text = clean_text(message.text, 100)
    words = (text or "").replace(",", " ").split()
    if not text or len(words) != 3:
        await message.answer("Нужно ровно три слова. Например: хаос, кофе, бессонница.")
        return
    await state.update_data(three_words=" ".join(words), photo_file_id=None, about="")
    await state.set_state(Registration.photo)
    await message.answer(
        "8/8. Теперь пришли фотографию для своей карточки. 🖤\n"
        "Фото обязательно: отправь его именно как фотографию в Telegram, а не как файл. "
        "Используй только снимок, который согласен(на) опубликовать."
    )


@dp.message(Registration.photo, F.photo)
async def get_photo(message: Message, state: FSMContext):
    if not await state.get_state():
        return
    biggest = message.photo[-1]
    if biggest.file_size and biggest.file_size > 10 * 1024 * 1024:
        await message.answer("Фото слишком большое (больше 10 МБ). Пришли другое 🖤")
        return
    await state.update_data(photo_file_id=biggest.file_id)
    await state.set_state(Registration.style)
    await send_style_choice(message)


@dp.callback_query(Registration.style, F.data.startswith("styles_"))
async def flip_style_page(callback: CallbackQuery, state: FSMContext):
    if not await state.get_state():
        await callback.answer()
        return
    arg = callback.data.removeprefix("styles_")
    if arg == "noop":
        await callback.answer()
        return
    try:
        page = int(arg)
    except ValueError:
        await callback.answer()
        return
    await callback.answer()
    await send_style_choice(callback.message, page)


@dp.callback_query(Registration.style, F.data.regexp(r"^style_\d+$"))
async def choose_style(callback: CallbackQuery, state: FSMContext):
    if not await state.get_state():
        await callback.answer()
        return
    try:
        tid = int(callback.data.split("_", 1)[1])
    except ValueError:
        tid = 0
    if tid not in TEMPLATES:
        await callback.answer("Выбери макет из списка", show_alert=True)
        return
    await callback.answer(f"Макет «{TEMPLATES[tid]['name'].capitalize()}» выбран")
    await state.update_data(template_id=tid)
    await show_confirm(callback.message, state)


@dp.message(Registration.style, ~F.text.startswith("/"))
async def style_invalid(message: Message, state: FSMContext):
    if not await state.get_state():
        return
    await message.answer("Выбери макет кнопками под картинкой выше 🎨")


@dp.callback_query(Registration.confirm, F.data == "restyle")
async def restyle(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(Registration.style)
    await send_style_choice(callback.message)


@dp.callback_query(F.data == "rephoto")
async def rephoto(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(Registration.photo)
    await callback.message.answer(
        "📸 Пришли другое фото — именно как фотографию в Telegram, а не как файл."
    )


@dp.message(Registration.photo, ~F.text.startswith("/"))
async def photo_invalid(message: Message, state: FSMContext):
    if not await state.get_state():
        return
    if (message.text or "").startswith("/"):
        return
    await message.answer("Фото обязательно для участия 🖤 Пришли изображение именно как фотографию в Telegram, не как файл.")


@dp.callback_query(Registration.confirm, F.data == "edit_menu")
async def edit_menu(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.answer("Что правим?", reply_markup=edit_menu_keyboard())


@dp.callback_query(Registration.confirm, F.data.startswith("edit_"))
async def edit_choice(callback: CallbackQuery, state: FSMContext):
    key = callback.data.removeprefix("edit_")
    if key == "back":
        await callback.answer()
        await show_confirm(callback.message, state)
        return
    if key not in FIELD_META:
        await callback.answer("Неизвестное поле", show_alert=True)
        return
    label, _lim, question = FIELD_META[key]
    await callback.answer()
    await state.update_data(edit_key=key)
    await state.set_state(Registration.edit_field)
    await callback.message.answer(f"✏️ {label}. {question}\n\nНапиши новое значение (или /cancel для отмены).")


@dp.message(Registration.edit_field, ~F.text.startswith("/"))
async def edit_save(message: Message, state: FSMContext):
    data = await state.get_data()
    key = data.get("edit_key")
    if key not in FIELD_META:
        await state.set_state(Registration.confirm)
        return
    label, limit, _q = FIELD_META[key]
    if key == "three_words":
        text = clean_text(message.text, limit)
        words = (text or "").replace(",", " ").split()
        if not text or len(words) != 3:
            await message.answer("Нужно ровно три слова. Например: хаос, кофе, бессонница.")
            return
        await state.update_data(three_words=" ".join(words))
    else:
        text = clean_text(message.text, limit)
        if not text:
            await message.answer(f"Напиши значение для «{label}» (до {limit} символов).")
            return
        await state.update_data(**{key: text})
    await state.update_data(edit_key=None)
    await message.answer(f"Готово, поле «{label}» обновлено ✅")
    await show_confirm(message, state)


@dp.callback_query(Registration.confirm, F.data == "submit_restart")
async def restart(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await begin_registration(callback.message, state)


@dp.callback_query(Registration.confirm, F.data == "submit_cancel")
async def cancel_confirm(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await callback.message.answer("Заявка отменена. Если передумаешь — можешь начать заново.", reply_markup=main_menu())


@dp.callback_query(Registration.confirm, F.data == "submit_yes")
async def submit(callback: CallbackQuery, state: FSMContext):
    await callback.answer("Сохраняю…")
    data = await state.get_data()
    user = callback.from_user
    for key in ("full_name", "group_name", "artist_name", "album", "genre", "track", "three_words", "photo_file_id"):
        if not data.get(key):
            await callback.message.answer("Что-то потерялось, давай заново 🙏", reply_markup=main_menu(is_admin(user.id)))
            await begin_registration(callback.message, state)
            return
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tid = data.get("template_id") if data.get("template_id") in TEMPLATES else DEFAULT_TEMPLATE
    with db_connect() as conn:
        cursor = conn.execute("""
            INSERT INTO registrations
            (telegram_id, username, full_name, group_name, artist_name, album, genre, track, about, three_words, photo_file_id, created_at, template_id, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'approved')
        """, (
            user.id, user.username, data["full_name"], data["group_name"],
            data["artist_name"], data["album"], data["genre"], data["track"],
            "", data.get("three_words", ""), data.get("photo_file_id"), now, tid,
        ))
        reg_id = cursor.lastrowid
    data["template_id"] = tid
    card_bytes = await preview_card_bytes(data, reg_id)
    if card_bytes is None:
        log.warning("card render failed for CR-%03d", reg_id)
    admin_text = (
        f"✅ <b>НОВЫЙ УЧАСТНИК CR-{reg_id:03d}</b>\n"
        f"<b>Группа:</b> {html.escape(safe(data.get('group_name')))}\n"
        f"<b>Имя:</b> {html.escape(safe(data.get('full_name')))}\n"
        f"<b>ARTIST:</b> {html.escape(safe(data.get('artist_name')))}\n"
        f"<b>ALBUM:</b> {html.escape(safe(data.get('album')))}\n"
        f"<b>GENRE:</b> {html.escape(safe(data.get('genre')))}\n"
        f"<b>TRACK:</b> {html.escape(safe(data.get('track')))}\n"
        f"<b>Три слова:</b> {html.escape(safe(data.get('three_words')))}\n"
        f"<b>Макет:</b> {TEMPLATES[tid]['name'].capitalize()} (№{tid})\n"
        f"<b>Telegram:</b> @{html.escape(user.username) if user.username else 'хандл не указан'} (<code>{user.id}</code>)"
    )
    card_ref = None
    for admin_id in ADMIN_IDS:
        try:
            if card_bytes:
                sent = await bot.send_photo(admin_id, card_ref or BufferedInputFile(card_bytes, filename=f"college_records_CR-{reg_id:03d}.jpg"), caption=admin_text, parse_mode="HTML")
                if card_ref is None and sent.photo:
                    card_ref = sent.photo[-1].file_id
            elif data.get("photo_file_id"):
                await bot.send_photo(admin_id, data["photo_file_id"], caption=admin_text, parse_mode="HTML")
            else:
                await bot.send_message(admin_id, admin_text, parse_mode="HTML")
            await asyncio.sleep(0.05)
        except Exception as exc:
            log.warning("admin %s notify failed: %s", admin_id, exc)
    await state.clear()
    kb = main_menu(is_admin(user.id))
    if card_bytes:
        await callback.message.answer_photo(card_ref or BufferedInputFile(card_bytes, filename=f"college_records_CR-{reg_id:03d}.jpg"), caption=(f"ГОТОВО 🖤 Карточка <b>CR-{reg_id:03d}</b> создана!\nХочешь ещё — жми «🎧 Зарегистрироваться» снова, лимита нет. Все твои карточки — /mycards."), parse_mode="HTML", reply_markup=kb)
    else:
        await callback.message.answer(f"Карточка CR-{reg_id:03d} сохранена, но файл собрать не вышло. Напиши организаторам — они помогут.", reply_markup=kb)


def _require_admin(message: Message) -> bool:
    return is_admin(message.from_user.id)


@dp.message(Command("export"))
async def export_csv(message: Message):
    if not _require_admin(message):
        await message.answer("Эта команда доступна только организаторам.")
        return
    with db_connect() as conn:
        rows = conn.execute("""
            SELECT id, full_name, group_name, artist_name, album, genre, track, three_words,
                   photo_file_id, username, telegram_id, template_id, status, created_at
            FROM registrations ORDER BY group_name, id
        """).fetchall()
    if not rows:
        await message.answer("Пока нет зарегистрированных участников.")
        return
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(["ID", "Имя", "Группа", "ARTIST", "ALBUM", "GENRE", "TRACK", "THREE WORDS", "photo_file_id", "username", "telegram_id", "template_id", "status", "created_at"])
    for row in rows:
        writer.writerow([row[k] for k in row.keys()])
    content = buffer.getvalue().encode("utf-8-sig")
    await message.answer_document(BufferedInputFile(content, filename="college_records_registrations.csv"), caption=f"Заявки COLLEGE RECORDS: {len(rows)} (открывается в Excel)")


@dp.message(Command("groups"))
async def list_groups(message: Message):
    if not _require_admin(message):
        await message.answer("Эта команда доступна только организаторам.")
        return
    with db_connect() as conn:
        rows = conn.execute("SELECT group_name, COUNT(*) AS n FROM registrations GROUP BY group_name ORDER BY group_name").fetchall()
    if not rows:
        await message.answer("Пока нет зарегистрированных участников.")
        return
    lines = ["📚 <b>ГРУППЫ COLLEGE RECORDS</b>", "", "Чтобы получить карточки одной группы, напиши:"]
    lines += [f"• <code>{html.escape(row['group_name'])}</code> — {row['n']} чел." for row in rows]
    lines += ["", "Команда: <code>/group название_группы</code>"]
    await message.answer("\n".join(lines), parse_mode="HTML")


@dp.message(Command("group"))
async def export_group(message: Message):
    if not _require_admin(message):
        await message.answer("Эта команда доступна только организаторам.")
        return
    group_name = (message.text or "").partition(" ")[2].strip()
    if not group_name:
        await message.answer("Укажи название группы. Например: <code>/group 2М 3П</code>", parse_mode="HTML")
        return
    with db_connect() as conn:
        rows = conn.execute("""SELECT id, full_name, group_name, artist_name, album, genre, track, three_words, photo_file_id, username, template_id, status
                FROM registrations WHERE lower(group_name) = lower(?) ORDER BY id""", (group_name,)).fetchall()
    if not rows:
        await message.answer(f"В группе «{html.escape(group_name)}» пока нет заявок.")
        return
    await message.answer(f"📂 <b>{html.escape(group_name)}</b> — карточек: {len(rows)}. Отправляю пачками, это займёт время…", parse_mode="HTML")
    batch: list = []
    for row in rows:
        caption = (f"🎧 <b>CR-{row['id']:03d}</b> ({STATUS_LABEL.get(row['status'] or 'new', row['status'])})\n" f"<b>Группа:</b> {html.escape(row['group_name'])}\n" f"<b>Имя:</b> {html.escape(row['full_name'])}\n" f"<b>ARTIST:</b> {html.escape(row['artist_name'])}\n" f"<b>ALBUM:</b> {html.escape(row['album'])}\n" f"<b>GENRE:</b> {html.escape(row['genre'])}\n" f"<b>TRACK:</b> {html.escape(row['track'])}\n" f"<b>Три слова:</b> {html.escape(row['three_words'] or '—')}\n" f"<b>Telegram:</b> @{html.escape(row['username']) if row['username'] else 'хандл не указан'}")
        media = None
        if row["photo_file_id"]:
            try:
                tg_file = await bot.get_file(row["photo_file_id"])
                buf = io.BytesIO()
                await bot.download_file(tg_file.file_path, destination=buf)
                card = await asyncio.to_thread(render_card, buf.getvalue(), dict(row), row["id"])
                media = BufferedInputFile(card, filename=f"college_records_CR-{row['id']:03d}.jpg")
            except Exception as exc:
                log.warning("group card CR-%03d failed: %s", row["id"], exc)
        try:
            if media:
                await message.answer_photo(media, caption=caption, parse_mode="HTML")
            else:
                await message.answer(caption, parse_mode="HTML")
        except Exception as exc:
            log.warning("group send CR-%03d failed: %s", row["id"], exc)
            await message.answer(caption + "\n⚠️ Не удалось создать карточку.", parse_mode="HTML")
        await asyncio.sleep(0.15)
        batch.append(row["id"])
        if len(batch) >= 20:
            await message.answer(f"…отправлено {len(batch)} из {len(rows)}, продолжаю…")
            batch = []


@dp.message(Command("count"))
async def count_registrations(message: Message):
    if not _require_admin(message):
        await message.answer("Эта команда доступна только организаторам.")
        return
    with db_connect() as conn:
        total = conn.execute("SELECT COUNT(*) AS n FROM registrations").fetchone()["n"]
        groups = conn.execute("SELECT group_name, COUNT(*) AS n FROM registrations GROUP BY group_name ORDER BY group_name").fetchall()
        approved = conn.execute("SELECT COUNT(*) AS n FROM registrations WHERE status='approved'").fetchone()["n"]
    lines = [f"Всего участников: {total} (принято: {approved})", "", "По группам:"]
    lines += [f"• {row['group_name']}: {row['n']}" for row in groups]
    await message.answer("\n".join(lines))


@dp.message(Command("broadcast"))
async def broadcast(message: Message, state: FSMContext):
    if not _require_admin(message):
        await message.answer("Эта команда доступна только организаторам.")
        return
    text = (message.text or "").partition(" ")[2].strip()
    if not text:
        await message.answer("Использование: <code>/broadcast Текст рассылки</code>", parse_mode="HTML")
        return
    if len(text) > 1000:
        await message.answer("Текст слишком длинный (макс 1000 символов).")
        return
    with db_connect() as conn:
        rows = conn.execute("SELECT DISTINCT telegram_id FROM registrations").fetchall()
    if not rows:
        await message.answer("Некому рассылать — база пуста.")
        return
    await message.answer(f"📣 Рассылка {len(rows)} участникам…")
    ok, fail = 0, 0
    for (uid,) in rows:
        try:
            await bot.send_message(uid, f"📣 <b>COLLEGE RECORDS</b>\n\n{html.escape(text)}", parse_mode="HTML")
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)
    await message.answer(f"Готово: отправлено {ok}, не доставлено {fail}.")


@dp.message(Command("delete"))
async def delete_reg(message: Message):
    if not _require_admin(message):
        await message.answer("Эта команда доступна только организаторам.")
        return
    arg = (message.text or "").partition(" ")[2].strip().upper().replace("CR-", "").replace("CR", "").strip()
    if not arg.isdigit():
        await message.answer("Использование: <code>/delete CR-001</code>", parse_mode="HTML")
        return
    reg_id = int(arg)
    with db_connect() as conn:
        row = conn.execute("SELECT telegram_id FROM registrations WHERE id = ?", (reg_id,)).fetchone()
        if not row:
            await message.answer(f"CR-{reg_id:03d} не найдена.")
            return
        conn.execute("DELETE FROM registrations WHERE id = ?", (reg_id,))
    await message.answer(f"🗑 CR-{reg_id:03d} удалена.")


@dp.message(F.text == "🛠 Админ-панель")
async def admin_panel(message: Message):
    if not _require_admin(message):
        return
    await message.answer("🛠 Админ-панель. Выбирай:", reply_markup=admin_menu())


@dp.message(F.text == "⬅️ Назад")
async def admin_back(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Главное меню:", reply_markup=main_menu(is_admin(message.from_user.id)))


@dp.message(F.text == "📊 Статистика")
async def admin_btn_count(message: Message):
    if not _require_admin(message):
        return
    await count_registrations(message)


@dp.message(F.text == "📚 Группы")
async def admin_btn_groups(message: Message):
    if not _require_admin(message):
        return
    await list_groups(message)


@dp.message(F.text == "📄 Экспорт CSV")
async def admin_btn_export(message: Message):
    if not _require_admin(message):
        return
    await export_csv(message)


@dp.message(F.text == "📣 Рассылка")
async def admin_btn_broadcast(message: Message):
    if not _require_admin(message):
        return
    await message.answer("Напиши так: <code>/broadcast Текст для всех участников</code>", parse_mode="HTML")


async def setup_commands():
    await bot.set_my_commands([
        BotCommand(command="start", description="Начать / главное меню"),
        BotCommand(command="help", description="Помощь"),
        BotCommand(command="status", description="Моя заявка"),
        BotCommand(command="mycard", description="Карточка по номеру"),
        BotCommand(command="mycards", description="Все мои карточки"),
        BotCommand(command="cancel", description="Отменить ввод"),
    ])
    # Кнопка ☰ рядом с полем ввода — открывает список команд
    try:
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    except Exception as exc:
        log.warning("set_chat_menu_button failed: %s", exc)


async def main():
    init_db()
    if not ADMIN_IDS:
        log.warning("ADMIN_IDS пуст! Админ-команды работать не будут. Задай ADMIN_IDS в .env")
    log.info("COLLEGE RECORDS запускается. Админов: %d. База: %s", len(ADMIN_IDS), DB_PATH)
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await setup_commands()
    except Exception as exc:
        log.warning("set_my_commands failed: %s", exc)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        try:
            asyncio.run(bot.session.close())
        except RuntimeError:
            pass
