from __future__ import annotations

import os
import random
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = Path(os.getenv("CARD_TEMPLATE", str(BASE_DIR / "template_grunge.png")))
# Stadium.otf — сверхузкий шрифт без строчных букв, «—» и «№», поэтому для карточки
# используется card_font.ttf (DejaVu Sans Condensed, есть вся кириллица).
# Свой шрифт можно подставить через переменную окружения CARD_FONT.
FONT_PATH = Path(os.getenv("CARD_FONT", str(BASE_DIR / "card_font.ttf")))

# ─────────────────────────── НАСТРОЙКИ ───────────────────────────
# Все координаты заданы в пикселях ОРИГИНАЛЬНОГО макета 2110 x 2982.
# Если макет другого размера, всё масштабируется автоматически.
REF_W, REF_H = 2110, 2982

# Внешний край рамки фото на макете. Сама рамка остаётся от макета, фото кладётся внутрь.
PHOTO_FRAME = (145, 417, 1319, 1425)
PHOTO_INSET = 11            # насколько фото заходит внутрь от внешнего края рамки
PHOTO_FOCUS = (0.5, 0.40)   # какую часть фото сохранять при обрезке (x, y): 0.40 по высоте = лица не режутся

LABELS = ["ИМЯ / ПСЕВДОНИМ", "ГРУППА", "НАЗВАНИЕ АЛЬБОМА", "ЖАНР", "ГЛАВНЫЙ ТРЕК"]
VALUE_KEYS = ["artist_name", "group_name", "album", "genre", "track"]

# Базовая линия текста каждой строки (низ заглавных букв), как у подписей в макете.
BASELINES = [1589, 1774, 1952, 2131, 2315]

LABEL_LEFT = 160            # левый край подписей (как в макете)
VALUE_GAP = 22              # отступ значения от двоеточия
VALUE_RIGHT = 1940          # правый край значений (конец горизонтальных линий макета)
COLON_MODE = "attached"     # "attached": двоеточие сразу после подписи (как в исходном макете)
                            # "aligned": двоеточия на одной вертикали (у коротких подписей будет пробел)
VALUE_MODE = "follow"       # "follow": значение сразу после своей подписи; "column": все значения с одной линии
COLON_GAP = 16              # отступ двоеточия от самой длинной подписи

VALUE_LINE_GAP = 14         # межстрочный интервал при переносе


# Макеты на выбор. Геометрия у всех одинаковая (разные только цвета/стиль), поэтому вёрстка общая.
# color — цвет текста на этом макете. Файлы лежат в папке templates/ (см. make_templates.py).
TEMPLATES = {
    1: dict(name="ОРИГИНАЛ",   path=TEMPLATE_PATH,                         color=(218, 206, 159),
            font="LiberationSans-Bold.ttf",      size=55, track=1),
    2: dict(name="НЕОН",       path=BASE_DIR / "templates/template_2.jpg", color=(123, 236, 255),
            font="Carlito-Bold.ttf",             size=62, track=2),
    3: dict(name="ПАНК",       path=BASE_DIR / "templates/template_3.jpg", color=(243, 129, 112),
            font="DejaVuSansCondensed-Bold.ttf", size=55, track=0),
    4: dict(name="ВЕЙПОРВЕЙВ", path=BASE_DIR / "templates/template_4.jpg", color=(255, 162, 228),
            font="DejaVuSans-Bold.ttf",          size=48, track=4),
    5: dict(name="ТЕРМИНАЛ",   path=BASE_DIR / "templates/template_5.jpg", color=(143, 255, 164),
            font="DejaVuSansMono-Bold.ttf",      size=52, track=0),
    6: dict(name="ЗАКАТ",      path=BASE_DIR / "templates/template_6.jpg", color=(255, 168, 111),
            font="LiberationSans-Bold.ttf",      size=55, track=1),
    7: dict(name="ОКЕАН",      path=BASE_DIR / "templates/template_7.jpg", color=(125, 199, 255),
            font="Carlito-Bold.ttf",             size=60, track=2),
    8: dict(name="ЛАВАНДА",    path=BASE_DIR / "templates/template_8.jpg", color=(204, 172, 255),
            font="DejaVuSansCondensed-Bold.ttf", size=55, track=1),
    9: dict(name="ЗОЛОТО",     path=BASE_DIR / "templates/template_9.jpg", color=(241, 210, 132),
            font="DejaVuSans-Bold.ttf",          size=50, track=2),
    10: dict(name="РОЗА",      path=BASE_DIR / "templates/template_10.jpg", color=(255, 146, 186),
            font="Carlito-Bold.ttf",             size=60, track=2),
    11: dict(name="ЛЬДИНА",    path=BASE_DIR / "templates/template_11.jpg", color=(215, 237, 252),
            font="DejaVuSansCondensed-Bold.ttf", size=54, track=1),
    12: dict(name="СЛИВА",     path=BASE_DIR / "templates/template_12.jpg", color=(226, 157, 240),
            font="DejaVuSans-Bold.ttf",          size=50, track=2),
    13: dict(name="КИСЛОТА",   path=BASE_DIR / "templates/template_13.jpg", color=(213, 255, 124),
            font="DejaVuSansMono-Bold.ttf",      size=52, track=1),
    14: dict(name="ВИШНЯ",     path=BASE_DIR / "templates/template_14.jpg", color=(255, 130, 159),
            font="Carlito-Bold.ttf",             size=60, track=2),
    15: dict(name="БИРЮЗА",    path=BASE_DIR / "templates/template_15.jpg", color=(132, 234, 240),
            font="DejaVuSansCondensed-Bold.ttf", size=55, track=1),
}
# font — файл шрифта из папки fonts/, size — размер подписей и значений, track — разрядка между буквами (px)
DEFAULT_TEMPLATE = 1
# ─────────────────────────────────────────────────────────────────


_TEMPLATE_CACHE: dict = {}


def _template_id(data: dict) -> int:
    try:
        tid = int(data.get("template_id") or data.get("style") or DEFAULT_TEMPLATE)
    except (TypeError, ValueError):
        return DEFAULT_TEMPLATE
    return tid if tid in TEMPLATES and TEMPLATES[tid]["path"].exists() else DEFAULT_TEMPLATE


def _load_template(path: Path) -> Image.Image:
    """Макет читается с диска один раз (большие файлы декодируются долго), дальше берётся копия."""
    key = (str(path), path.stat().st_mtime)
    cached = _TEMPLATE_CACHE.get(path)
    if not cached or cached[0] != key:
        _TEMPLATE_CACHE[path] = (key, Image.open(path).convert("RGB"))
    return _TEMPLATE_CACHE[path][1].copy()


def _font(size: int, name: str | None = None) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [BASE_DIR / "fonts" / name] if name else []
    candidates += [
        FONT_PATH,
        BASE_DIR / "card_font.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("/Library/Fonts/Arial.ttf"),
    ]
    for p in candidates:
        try:
            if p.exists():
                return ImageFont.truetype(str(p), size)
        except OSError:
            continue
    return ImageFont.load_default()


def _clean(value) -> str:
    """Верхний регистр, без эмодзи/невидимых символов, без лишних пробелов."""
    text = " ".join(str(value or "").split()).upper()
    text = "".join(
        ch for ch in text
        if not (ord(ch) >= 0x1F000 or 0x2600 <= ord(ch) <= 0x27BF or ord(ch) in (0xFE0F, 0x200D))
    )
    return text.strip() or "—"


def _width(draw, text, font, track=0) -> float:
    """Ширина текста с учётом разрядки (расстояния между буквами)."""
    return draw.textlength(text, font=font) + track * max(len(text) - 1, 0)


def _put(draw, xy, text, font, fill, track=0):
    """Рисует текст с базовой линией в точке xy; при разрядке — по одной букве."""
    x, y = xy
    if not track:
        draw.text((x, y), text, font=font, fill=fill, anchor="ls")
        return
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill, anchor="ls")
        x += draw.textlength(ch, font=font) + track


def _wrap(draw, text, font, max_w, track=0) -> list[str]:
    lines, cur = [], ""
    for word in text.split(" "):
        # слово длиннее строки — режем по символам
        while _width(draw, word, font, track) > max_w and len(word) > 1:
            k = len(word)
            while k > 1 and _width(draw, word[:k], font, track) > max_w:
                k -= 1
            if cur:
                lines.append(cur)
                cur = ""
            lines.append(word[:k])
            word = word[k:]
        trial = f"{cur} {word}".strip()
        if cur and _width(draw, trial, font, track) > max_w:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def _layout_value(draw, text, max_w, sx, tpl):
    """Возвращает (шрифт, строки). Сначала сжимаем, потом переносим на 2 строки."""
    name, track = tpl["font"], tpl["track"] * sx
    max_size = round(tpl["size"] * sx)
    min_size = round(max_size * 0.70)
    for size in range(max_size, min_size - 1, -1):
        font = _font(size, name)
        if _width(draw, text, font, track) <= max_w:
            return font, [text]
    # не влезло даже в минимальном размере: 2 строки, размер подбираем от крупного
    for size in range(max_size, min_size - 1, -1):
        font = _font(size, name)
        lines = _wrap(draw, text, font, max_w, track)
        if len(lines) <= 2:
            return font, lines
    font = _font(min_size, name)
    lines = _wrap(draw, text, font, max_w, track)[:2]
    while lines[-1] and _width(draw, lines[-1] + "…", font, track) > max_w:
        lines[-1] = lines[-1][:-1].rstrip()
    lines[-1] += "…"
    return font, lines


def _cover(photo: Image.Image, size) -> Image.Image:
    """Фото заполняет область целиком, пропорции сохраняются (лишнее обрезается, без растяжения)."""
    photo = ImageOps.exif_transpose(photo).convert("RGB")
    return ImageOps.fit(photo, size, method=Image.Resampling.LANCZOS, centering=PHOTO_FOCUS)


def _erase_region(image: Image.Image, box, src_x: int) -> None:
    """Затирает текст макета кусочком чистого фона из той же строки (с мягкими краями)."""
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    patch = image.crop((src_x, y1, src_x + w, y2))
    if random.Random(x1 + y1).random() < 0.5:
        patch = ImageOps.mirror(patch)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rectangle((10, 8, w - 10, h - 8), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(7))
    image.paste(patch, (x1, y1), mask)


def render_card(photo_bytes: bytes, data: dict, reg_id: int | None = None) -> bytes:
    if not TEMPLATE_PATH.exists():
        raise FileNotFoundError(f"Не найден шаблон карточки: {TEMPLATE_PATH}")
    tpl = TEMPLATES[_template_id(data)]
    color = tpl["color"]
    image = _load_template(tpl["path"])
    sx, sy = image.width / REF_W, image.height / REF_H

    # ── Фото: внутрь существующей рамки макета, без растягивания ──
    fx1, fy1, fx2, fy2 = PHOTO_FRAME
    px1, py1 = round((fx1 + PHOTO_INSET) * sx), round((fy1 + PHOTO_INSET) * sy)
    px2, py2 = round((fx2 - PHOTO_INSET) * sx), round((fy2 - PHOTO_INSET) * sy)
    with Image.open(BytesIO(photo_bytes)) as uploaded:
        photo = _cover(uploaded, (max(1, px2 - px1), max(1, py2 - py1)))
    image.paste(photo, (px1, py1))

    draw = ImageDraw.Draw(image)
    track = tpl["track"] * sx
    label_font = _font(round(tpl["size"] * sx), tpl["font"])
    colon_w = _width(draw, ":", label_font)

    # Подписи стоят у левого края (как в макете). Двоеточия — на общей вертикали справа
    # от самой длинной подписи, значения начинаются строго за двоеточием.
    left_x = round(LABEL_LEFT * sx)
    max_label_w = max(_width(draw, lab, label_font, track) for lab in LABELS)
    colon_x = round(left_x + max_label_w + COLON_GAP * sx)
    value_x = round(colon_x + colon_w + VALUE_GAP * sx)
    value_max_w = round(VALUE_RIGHT * sx) - value_x

    # ── Стираем оригинальные подписи макета (линии остаются нетронутыми) ──
    erase_right = round(left_x + max_label_w + colon_w + 60 * sx)
    for base in BASELINES:
        b = round(base * sy)
        box = (round(120 * sx), b - round(62 * sy), erase_right, b + round(18 * sy))
        _erase_region(image, box, src_x=round(1100 * sx))
    draw = ImageDraw.Draw(image)

    # ── Подписи и значения ──
    for base, key in zip(BASELINES, VALUE_KEYS):
        b = round(base * sy)
        label = LABELS[VALUE_KEYS.index(key)]
        _put(draw, (left_x, b), label, label_font, color, track)
        if COLON_MODE == "attached":
            cx = round(left_x + _width(draw, label, label_font, track) + 3 * sx + track)
        else:
            cx = colon_x
        _put(draw, (cx, b), ":", label_font, color)

        # значение: сразу после своей подписи ("follow") или с общей линии ("column")
        row_x = round(cx + colon_w + VALUE_GAP * sx) if VALUE_MODE == "follow" else value_x
        font, lines = _layout_value(draw, _clean(data.get(key)), round(VALUE_RIGHT * sx) - row_x, sx, tpl)
        step = round(font.size * 0.73) + round(VALUE_LINE_GAP * sy)  # высота капсов + интервал
        # последняя строка стоит на базовой линии подписи, предыдущие — выше
        for n, line in enumerate(reversed(lines)):
            _put(draw, (row_x, b - n * step), line, font, color, track)

    out = BytesIO()
    # JPEG высокого качества: ~1 МБ вместо ~7 МБ у PNG, Telegram всё равно пережимает фото.
    image.save(out, format="JPEG", quality=93, subsampling=0, optimize=True)
    return out.getvalue()
