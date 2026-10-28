"""COLLEGE RECORDS — генератор афиши и короткого видео для главного трека.

Ссылка Яндекс.Музыки → название + автор + обложка → JPG-афиша → MP4-клип (медленный зум).

Зависимости: Pillow, imageio-ffmpeg (бинар ffmpeg без установки системного).
"""
from __future__ import annotations

import io
import json
import math
import random
import re
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

BASE_DIR = Path(__file__).resolve().parent
OLIVE = (102, 104, 64)
OLIVE_BRIGHT = (138, 141, 88)
OLIVE_DIM = (72, 74, 46)
INK = (201, 203, 184)

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
FONT_PATH = BASE_DIR / "card_font.ttf"
FONT_COND = BASE_DIR / "fonts" / "DejaVuSansCondensed-Bold.ttf"

# SSL-контекст с certifi (у системного Python могут не стоять сертификаты)
try:
    import ssl
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    _SSL_CTX = None

YANDEX_TRACK_RE = re.compile(
    r"music\.yandex\.ru/(?:album/\d+/)?track/(\d+)", re.I
)


# ─────────────────────────── ЯНДЕКС МУЗЫКА ───────────────────────────

def parse_track_id(url: str) -> str | None:
    m = YANDEX_TRACK_RE.search(url or "")
    return m.group(1) if m else None


def _get_json(url: str, params: dict | None = None) -> dict:
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_track(url_or_text: str) -> dict | None:
    """Вернуть {id,title,artists,album,cover_url} по ссылке Яндекс.Музыки."""
    tid = parse_track_id(url_or_text)
    if not tid:
        return None
    try:
        data = _get_json(f"https://api.music.yandex.net/tracks/{tid}")
        track = (data.get("result") or [None])[0]
        if not track:
            return None
        album = (track.get("albums") or [{}])[0]
        title = track.get("title") or ""
        version = track.get("version") or ""
        full_title = f"{title} ({version})" if version else title
        artists = [a.get("name") for a in (track.get("artists") or []) if a.get("name")]
        cover_tpl = album.get("coverUri") or ""
        cover_url = None
        if cover_tpl:
            cover_url = "https://" + cover_tpl.replace("%%", "600x600")
        return {
            "id": tid,
            "title": full_title,
            "artists": artists,
            "artist": ", ".join(artists) or "—",
            "album": album.get("title") or "",
            "cover_url": cover_url,
            "text": f"{full_title} — {', '.join(artists)}" if artists else full_title,
        }
    except Exception:
        return None


def _download(url: str) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=20, context=_SSL_CTX) as resp:
            return resp.read()
    except Exception:
        return None


# ─────────────────────────── ГРАНЖ-ФОН ───────────────────────────

def _grunge_bg(w: int, h: int, seed: int = 7) -> Image.Image:
    """Чёрный фон: серые трещины + потёртости (как в бренде)."""
    rng = np.random.default_rng(seed)
    base = np.full((h, w, 3), 5, dtype=float)
    base += rng.normal(0, 3, (h, w, 1))
    img = Image.fromarray(base.clip(0, 255).astype("uint8"))
    d = ImageDraw.Draw(img, "RGBA")

    def crack(x, y, ang, ln, width, col, depth=0):
        steps = max(3, int(ln) // 8)
        px, py = x, y
        for _ in range(steps):
            ang += rng.uniform(-0.35, 0.35)
            nx = px + math.cos(ang) * 8
            ny = py + math.sin(ang) * 8
            d.line([(px, py), (nx, ny)], fill=col, width=width)
            px, py = nx, ny
            if depth < 2 and rng.random() < 0.10:
                crack(px, py, ang + rng.uniform(-1.2, 1.2),
                      ln * rng.uniform(0.25, 0.55), max(1, width - 1), col, depth + 1)

    for _ in range(int(w * h / 90000) + 8):
        g = int(rng.integers(70, 155))
        crack(rng.integers(0, w), rng.integers(0, h), rng.uniform(0, 2 * math.pi),
              rng.integers(90, 400), int(rng.integers(1, 4)),
              (g, g, g, int(rng.integers(150, 225))))

    for _ in range(int(w * h / 4500)):
        x, y = rng.integers(0, w), rng.integers(0, h)
        ln, ang = rng.integers(6, 60), rng.uniform(0, 2 * math.pi)
        g = int(rng.integers(35, 105))
        d.line([(x, y), (x + ln * math.cos(ang), y + ln * math.sin(ang))],
               fill=(g, g, g, int(rng.integers(30, 110))),
               width=int(rng.integers(1, 3)))

    for _ in range(int(w * h / 2600)):
        x, y = rng.integers(0, w), rng.integers(0, h)
        r = rng.integers(1, 3)
        g = int(rng.integers(40, 120))
        d.ellipse([x - r, y - r, x + r, y + r], fill=(g, g, g, int(rng.integers(40, 120))))

    a = np.asarray(img.convert("RGB")).astype(float)
    yy, xx = np.ogrid[:h, :w]
    dx, dy = (xx - w / 2) / (w / 2), (yy - h / 2) / (h / 2)
    a *= (1 - 0.4 * (dx ** 2 + dy ** 2).clip(0, 1))[..., None]
    return Image.fromarray(a.clip(0, 255).astype("uint8"))


def _font(size: int) -> ImageFont.FreeTypeFont:
    path = FONT_COND if FONT_COND.exists() else FONT_PATH
    return ImageFont.truetype(str(path), size)


def _fit_text(draw, text: str, font, max_w: int, start: int, min_size: int = 34) -> tuple:
    """Подобрать размер шрифта и разбить на строки под ширину."""
    size = start
    while size > min_size:
        f = _font(size)
        lines, cur = [], ""
        for word in text.split():
            trial = (cur + " " + word).strip()
            if draw.textlength(trial, font=f) <= max_w:
                cur = trial
            else:
                if cur:
                    lines.append(cur)
                cur = word
        if cur:
            lines.append(cur)
        if len(lines) <= 3:
            return f, lines
        size -= 4
    f = _font(min_size)
    return f, [text]


def make_poster(info: dict, size: tuple[int, int] = (1080, 1350)) -> bytes:
    """JPG-афиша 1080x1350: чёрный гранж, обложка, трек/автор, оливковый бренд."""
    W, H = size
    img = _grunge_bg(W, H, seed=abs(hash(info.get("id", "0"))) % 10000)
    d = ImageDraw.Draw(img, "RGBA")

    # рамка как на карточке (двойная)
    d.rectangle([46, 46, W - 46, H - 46], outline=OLIVE, width=5)
    d.rectangle([58, 58, W - 58, H - 58], outline=OLIVE_DIM, width=2)

    # над «СЛУШАТЬ» — маленький заголовок
    d.text((W // 2, 110), "ГЛАВНЫЙ ТРЕК", font=_font(36), fill=OLIVE_DIM, anchor="mm")

    # обложка 640x640 по центру
    cover_box = (W // 2 - 320, 170, W // 2 + 320, 810)
    cover_bytes = _download(info["cover_url"]) if info.get("cover_url") else None
    if cover_bytes:
        try:
            cover = Image.open(io.BytesIO(cover_bytes)).convert("RGB")
            cover = cover.resize((640, 640), Image.Resampling.LANCZOS)
            mask = Image.new("L", (640, 640), 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, 639, 639], radius=26, fill=255)
            img.paste(cover, (cover_box[0], cover_box[1]), mask)
            # оливковая рамка вокруг обложки
            d.rounded_rectangle(cover_box, radius=26, outline=OLIVE, width=4)
        except Exception:
            cover_bytes = None
    if not cover_bytes:
        # фолбэк: пустая рамка с глобусом
        d.rounded_rectangle(cover_box, radius=26, outline=OLIVE, width=4)
        cx, cy = W // 2, 490
        rx, ry = 180, 120
        d.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], outline=OLIVE, width=8)
        for frac in (0.45, 1.0, 0.45):
            w2 = rx * frac * 0.62
            d.ellipse([cx - w2, cy - ry * 0.96, cx + w2, cy + ry * 0.96],
                      outline=OLIVE, width=5)
        d.line([(cx, cy - ry), (cx, cy + ry)], fill=OLIVE, width=5)
        for fy in (-0.5, 0, 0.5):
            y = cy + ry * fy
            half = rx * math.sqrt(max(0.05, 1 - fy ** 2)) * 0.98
            d.line([(cx - half, y), (cx + half, y)], fill=OLIVE, width=5)

    # НАЗВАНИЕ трека (авто-перенос, оливковый)
    title = info.get("title") or "БЕЗ НАЗВАНИЯ"
    f_title, lines = _fit_text(d, title.upper(), None, W - 200, 76, 44)
    y = 880
    for ln in lines[:3]:
        d.text((W // 2, y), ln, font=f_title, fill=OLIVE, anchor="mm")
        y += int(f_title.size * 1.14)

    # автор(ы)
    artist = info.get("artist") or "—"
    f_art, alines = _fit_text(d, artist, None, W - 240, 52, 34)
    y += 22
    for ln in alines[:2]:
        d.text((W // 2, y), ln, font=f_art, fill=INK, anchor="mm")
        y += int(f_art.size * 1.2)

    # альбом (если есть)
    if info.get("album"):
        f_alb = _font(34)
        d.text((W // 2, y + 26), f"альбом · {info['album']}", font=f_alb,
               fill=OLIVE_DIM, anchor="mm")

    # подвал: глобус + Яндекс Музыка + COLLEGE RECORDS
    foot_y = H - 150
    d.line([(140, foot_y - 34), (W - 140, foot_y - 34)], fill=OLIVE_DIM, width=3)
    gx, gy = 175, foot_y + 30
    d.ellipse([gx - 30, gy - 20, gx + 30, gy + 20], outline=OLIVE_DIM, width=3)
    d.line([(gx, gy - 20), (gx, gy + 20)], fill=OLIVE_DIM, width=2)
    d.line([(gx - 29, gy), (gx + 29, gy)], fill=OLIVE_DIM, width=2)
    d.text((gx + 48, gy), "ЯНДЕКС МУЗЫКА", font=_font(30), fill=OLIVE_DIM, anchor="lm")
    d.text((W - 150, gy), "COLLEGE RECORDS", font=_font(30), fill=OLIVE, anchor="rm")

    # лёгкая потёртость поверх (прожилки)
    a = np.asarray(img.convert("RGB")).astype(float)
    rng = np.random.default_rng(3)
    n = rng.random((H, W))
    nb = np.asarray(Image.fromarray((n * 255).astype("uint8"))
                    .filter(ImageFilter.GaussianBlur(3))).astype(float) / 255
    a *= (0.92 + 0.08 * nb[..., None])
    img = Image.fromarray(a.clip(0, 255).astype("uint8"))

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=90, subsampling=0, optimize=True)
    return out.getvalue()


def make_video(poster_jpg: bytes, duration: float = 4.0, fps: int = 24) -> bytes:
    """MP4 из афиши: медленный зум внутрь (Ken Burns)."""
    import imageio_ffmpeg
    import tempfile

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    w, h = 1080, 1350
    total = max(1, int(duration * fps))
    scale_end = 1.10  # на сколько увеличиваем к концу
    z_delta = scale_end - 1

    # zoompan: плавный зум от 1.0 до scale_end по центру
    z_expr = f"1+({z_delta}*on/{total})"
    vf = (
        f"zoompan=z='{z_expr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d={total}:s={w}x{h}:fps={fps},"
        f"format=yuv420p"
    )
    # пишем во временный файл: mp4 + faststart требует seek (в stdout нельзя)
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tf:
        tmp_path = tf.name
    try:
        cmd = [
            ffmpeg, "-y",
            "-f", "image2pipe", "-vcodec", "mjpeg", "-i", "-",
            "-vf", vf,
            "-t", f"{duration:.2f}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "23",
            "-movflags", "+faststart",
            tmp_path,
        ]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE)
        _out, err = proc.communicate(input=poster_jpg, timeout=90)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {err.decode('utf-8', 'ignore')[-400:]}")
        with open(tmp_path, "rb") as f:
            video = f.read()
        if not video:
            raise RuntimeError("ffmpeg produced empty file")
        return video
    finally:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass


def build_track_assets(url: str) -> dict | None:
    """Ссылка → {info, poster_jpg}. Видео больше не генерируется."""
    info = fetch_track(url)
    if not info:
        return None
    try:
        poster = make_poster(info)
    except Exception:
        poster = None
    if poster is None:
        return {"info": info, "poster_jpg": None}
    return {"info": info, "poster_jpg": poster}
