"""Thumbnails / covers for both 16:9 long gameplay parts (1280x720) and 9:16 vertical Shorts/Reels (1080x1920)."""
import os, re, shutil, tempfile, textwrap, functools
from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageFilter, ImageStat
from . import core, render

W, H = 1280, 720
STYLES = {
    "bold-left": "Big title on the left, red PART box, art on the right (great with key-art style backgrounds)",
    "center-banner": "Title across the top, PART banner at the bottom, full-bleed background",
    "minimal": "Small clean label in the corner, lets the frame speak",
}

@functools.lru_cache(maxsize=32)
def _font(size, heavy=True):
    return ImageFont.truetype(core.FONT_HEAVY if heavy else core.FONT_BOLD, size)

def _bg(path):
    img = Image.open(path).convert("RGB")
    r = max(W / img.width, H / img.height)
    img = img.resize((int(img.width * r + 0.5), int(img.height * r + 0.5)), Image.LANCZOS)
    x, y = (img.width - W) // 2, (img.height - H) // 2
    return img.crop((x, y, x + W, y + H))

@functools.lru_cache(maxsize=8)
def _grad_mask(side="left", strength=150, reach=0.5):
    if side == "left":
        row = bytes(int(strength * max(0.0, 1.0 - x / (W * reach)) ** 1.5) for x in range(W))
        return Image.frombytes("L", (W, 1), row).resize((W, H), Image.NEAREST)
    col = bytes(int(strength * min(1.0, max(0.0, 1.0 - y / (H * reach)) ** 1.5 + max(0.0, 1.0 - (H - y) / (H * reach)) ** 1.5)) for y in range(H))
    return Image.frombytes("L", (1, H), col).resize((W, H), Image.NEAREST)

def _shade(img, side="left", strength=150, reach=0.5):
    shade = Image.new("RGB", (W, H), (30, 10, 45))
    return Image.composite(shade, img, _grad_mask(side, strength, reach))

def _text(d, xy, s, f, fill, stroke, anchor="la"):
    d.text(xy, s, font=f, fill=fill, stroke_width=stroke, stroke_fill=(0, 0, 0), anchor=anchor)

def _box(d, x, y, label, f, center=False):
    tw = d.textlength(label, font=f)
    bw, bh = tw + 64, f.size + 48
    if center: x = (W - bw) / 2
    d.rectangle([x - 6, y - 6, x + bw + 6, y + bh + 6], fill=(255, 255, 255))
    d.rectangle([x, y, x + bw, y + bh], fill=(226, 28, 52))
    _text(d, (x + 32, y + 8), label, f, (255, 255, 255), 6)

def make(background, part, out, s, style=None, subtitle="FULL GAMEPLAY"):
    style = style or s.get("cover_style", "bold-left")
    img = ImageEnhance.Contrast(_bg(background)).enhance(1.05)
    title = s.get("game", "GTA VI").upper()
    if style == "bold-left":
        img = _shade(img, "left")
        d = ImageDraw.Draw(img)
        f = _font(200)
        while d.textlength(title, font=f) > 560 and f.size > 90: f = _font(f.size - 10)
        _text(d, (50, 55), title, f, (255, 255, 255), 9)
        _text(d, (54, 305), subtitle, _font(80), (245, 196, 20), 6)
        _box(d, 54, 430, f"PART {part}", _font(112))
    elif style == "center-banner":
        img = _shade(img, "top", 170, 0.45)
        d = ImageDraw.Draw(img)
        _text(d, (W / 2, 30), f"{title} {subtitle}", _font(118), (255, 255, 255), 8, anchor="ma")
        _box(d, 0, 540, f"PART {part}", _font(96), center=True)
    else:  # minimal
        d = ImageDraw.Draw(img)
        label = f"{title} · PART {part}"
        f = _font(64)
        tw = d.textlength(label, font=f)
        d.rectangle([30, H - 130, 30 + tw + 50, H - 30], fill=(0, 0, 0))
        _text(d, (55, H - 122), label, f, (255, 255, 255), 0)
    img.save(out, quality=92)
    return out

def _frame_quality(path):
    """Scores a candidate thumbnail frame by sharpness + color vibrancy + balanced exposure."""
    try:
        im = Image.open(path).convert("RGB").resize((320, 180), Image.BILINEAR)
        hsv = im.convert("HSV")
        _, sat, val = hsv.split()
        mean_v = ImageStat.Stat(val).mean[0]
        if mean_v < 35 or mean_v > 235:
            return -1.0
        mean_s = ImageStat.Stat(sat).mean[0]
        edges = ImageStat.Stat(im.convert("L").filter(ImageFilter.FIND_EDGES)).mean[0]
        exposure_ok = 1.0 - abs(mean_v - 135.0) / 160.0
        return edges * 1.4 + mean_s * 0.5 + exposure_ok * 25.0
    except Exception:
        return -1.0

def background_for(project, s, render_still):
    """User's chosen art from 'Cover backgrounds', else the sharpest/most vibrant frame sampled across the video."""
    name = s.get("cover_background")
    if name:
        p = os.path.join(core.DIRS["covers"], name)
        if os.path.exists(p): return p
    work = os.path.join(os.path.dirname(project["video"]), "_analysis")
    os.makedirs(work, exist_ok=True)
    out = os.path.join(work, "cover_bg_v2.jpg")
    if not os.path.exists(out):
        dur = max(10.0, float(project.get("duration") or 60.0))
        best_p, best_q = None, -999.0
        for idx, frac in enumerate((0.15, 0.28, 0.42, 0.56, 0.70, 0.84)):
            cand = os.path.join(work, f"_bg_cand_{idx}.jpg")
            try:
                render_still(project["video"], dur * frac, cand)
                q = _frame_quality(cand)
                if q > best_q:
                    best_q, best_p = q, cand
            except Exception:
                continue
        if best_p and os.path.exists(best_p):
            os.replace(best_p, out)
        else:
            render_still(project["video"], dur * 0.3, out)
        for idx in range(6):
            c = os.path.join(work, f"_bg_cand_{idx}.jpg")
            if os.path.exists(c):
                try: os.remove(c)
                except OSError: pass
    return out


# ---------------- 9:16 Vertical Shorts / Reels Thumbnails (1080x1920, 4:5 IG-grid safe) ----------------
RW, RH = 1080, 1920
_HYPE_WORDS = {
    "5-STAR", "FIGHTER", "JET", "WASTED", "EXPLODES", "EXPLOSION", "PILEUP", "CRASH", "CRASHES",
    "CHASE", "PURSUIT", "ROBBERY", "SHOOTOUT", "HIJACKING", "MONTAGE", "K9", "HELICOPTER", "FLIP", "FLIPS",
}

@functools.lru_cache(maxsize=16)
def _deva_font(size):
    for p in ("/System/Library/Fonts/Kohinoor.ttc", "/System/Library/Fonts/Supplemental/Devanagari Sangam MN.ttc"):
        if os.path.exists(p):
            try: return ImageFont.truetype(p, size)
            except Exception: pass
    return _font(size, heavy=False)

@functools.lru_cache(maxsize=4)
def _reel_vignette():
    col = []
    for y in range(RH):
        if y < 340:
            a = int(125 * (1.0 - y / 340.0) ** 1.6)
        elif y > 960:
            t = min(1.0, (y - 960.0) / 680.0)
            a = int(215 * (t ** 1.25))
        else:
            a = 0
        col.append(a)
    return Image.frombytes("L", (1, RH), bytes(col)).resize((RW, RH), Image.BILINEAR)

_TRAILING_STOP = {
    "&", "and", "or", "into", "the", "a", "an", "in", "of", "to", "with", "as", "at", "on", "by", "for", "from", "under", "over",
}

def _short_punchline(raw_title, game="GTA VI"):
    t = re.sub(r"^\d+\s*-\s*(?:\[[^\]]+\]\s*)?", "", str(raw_title or "")).strip()
    t = re.sub(r"^\d{4,6}s?\s*[-_]?\s*", "", t)
    t = re.sub(rf"^\s*(?:in\s+{re.escape(game)}|in\s+gta\s*(?:vi|6))\s*[,:\-—]*\s*", "", t, flags=re.I)
    if "—" in t and any(k in t.split("—", 1)[0].lower() for k in ("facecam", "hud", "cutout", "crisis")):
        parts = [p.strip() for p in t.split("—", 1)]
        t = parts[1] if len(parts[1]) >= 8 else f"{parts[0]} {parts[1]}"
    t = re.sub(r"\s*\[score[^\]]*\]\s*", "", t, flags=re.I)
    t = re.sub(r"#shorts\b", "", t, flags=re.I).strip(" .,!-—")
    words = t.split()
    if len(words) > 9:
        words = words[:9]
    while len(words) > 2 and words[-1].lower().strip(".,!?()-") in _TRAILING_STOP:
        words.pop()
    t = " ".join(words).rstrip(" ,;-&")
    return t or f"{game} HIGHLIGHT"

def _compose_vertical_frame(img):
    """Turn either a 16:9 raw frame OR a 9:16 black-bar/blurred-bar frame into a clean full-bleed 1080x1920 canvas."""
    img = img.convert("RGB")
    w, h = img.size
    if w >= h:
        # Landscape source frame -> blurred color-matched vertical backdrop + large 1080x1020 center-action zoom
        bg_s = max(RW / w, RH / h)
        bg = img.resize((int(w * bg_s + 0.5), int(h * bg_s + 0.5)), Image.BILINEAR)
        bx, by = (bg.width - RW) // 2, (bg.height - RH) // 2
        bg = bg.crop((bx, by, bx + RW, by + RH)).filter(ImageFilter.GaussianBlur(24))
        bg = ImageEnhance.Brightness(bg).enhance(0.55)
        fg_h = 980
        fg_s = max(RW / w, fg_h / h)
        fg = img.resize((int(w * fg_s + 0.5), int(h * fg_s + 0.5)), Image.LANCZOS)
        fx, fy = (fg.width - RW) // 2, (fg.height - fg_h) // 2
        fg = fg.crop((fx, fy, fx + RW, fy + fg_h))
        mask = Image.new("L", (RW, fg_h), 255)
        for y in range(90):
            v = int(255 * (y / 90.0))
            mask.paste(v, (0, y, RW, y + 1))
            mask.paste(v, (0, fg_h - 1 - y, RW, fg_h - y))
        bg.paste(fg, (0, 300), mask)
        return bg

    # Already 9:16: check if it's a center-window reel (black/blurred bars top & bottom with 16:9 game in y=656..1260)
    scaled = img.resize((RW, RH), Image.LANCZOS) if (w, h) != (RW, RH) else img
    edges_full = scaled.convert("L").filter(ImageFilter.FIND_EDGES)
    mid_e = ImageStat.Stat(edges_full.crop((40, 680, RW - 40, 1240))).mean[0]
    bot_e = ImageStat.Stat(edges_full.crop((40, 1440, RW - 40, 1860))).mean[0]
    if mid_e > 3.2 and bot_e < mid_e * 0.58:
        game_crop = scaled.crop((0, 662, RW, 1252))
        return _compose_vertical_frame(game_crop)
    return scaled

def make_reel_cover(video_path, hook, out_jpg, s=None, source_video=None, start_sec=None, length_sec=None):
    """Generate a vibrant, 4:5-grid-safe 1080x1920 vertical thumbnail JPEG for any Short/Reel."""
    s = s or core.load_settings()
    src = source_video if (source_video and os.path.isfile(source_video) and start_sec is not None) else video_path
    dur = float(length_sec) if (src == source_video and length_sec) else max(3.0, core.duration(src))
    base_t = float(start_sec) if (src == source_video and start_sec is not None) else 0.0

    tmp_dir = tempfile.mkdtemp(prefix="gvcf_rcover_")
    best_frame = None
    best_q = -999.0
    try:
        for i, frac in enumerate((0.18, 0.34, 0.50, 0.66, 0.82)):
            ts = base_t + max(0.2, min(dur - 0.3, dur * frac))
            cand = os.path.join(tmp_dir, f"c{i}.jpg")
            try:
                render.still(src, ts, cand)
                im_test = Image.open(cand)
                # Score center gameplay region if input is already 9:16 letterboxed
                if im_test.height > im_test.width:
                    sub = os.path.join(tmp_dir, f"sub{i}.jpg")
                    im_test.crop((0, int(im_test.height * 0.34), im_test.width, int(im_test.height * 0.66))).save(sub)
                    q = _frame_quality(sub)
                else:
                    q = _frame_quality(cand)
                if q > best_q:
                    best_q, best_frame = q, cand
            except Exception:
                continue
        if not best_frame or not os.path.exists(best_frame):
            best_frame = os.path.join(tmp_dir, "fallback.jpg")
            render.still(src, base_t + dur * 0.4, best_frame)

        canvas = _compose_vertical_frame(Image.open(best_frame))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    canvas = ImageEnhance.Color(ImageEnhance.Contrast(canvas).enhance(1.10)).enhance(1.18)
    dark = Image.new("RGB", (RW, RH), (10, 8, 18))
    canvas = Image.composite(dark, canvas, _reel_vignette())
    d = ImageDraw.Draw(canvas)

    game = (s.get("game") or "GTA VI").upper()
    punch = _short_punchline(hook, s.get("game", "GTA VI"))
    is_deva = any(0x0900 <= ord(c) <= 0x097F for c in punch)
    display_text = punch if is_deva else punch.upper()

    for wrap_w, fsize, line_h in ((15, 98, 110), (18, 84, 96), (22, 72, 84)):
        lines = textwrap.wrap(display_text, wrap_w)
        if len(lines) <= 3:
            break
    else:
        lines = textwrap.wrap(display_text, 22)[:3]
        fsize, line_h = 72, 84

    tf = _deva_font(fsize) if is_deva else _font(fsize, heavy=True)
    while any(d.textlength(ln, font=tf) > RW - 110 for ln in lines) and fsize > 54:
        fsize -= 6
        line_h = int(fsize * 1.14)
        tf = _deva_font(fsize) if is_deva else _font(fsize, heavy=True)

    # Keep badge + headline strictly inside y=1120..1575 so IG's 4:5 profile grid crop (y=285..1635) NEVER clips text!
    block_h = len(lines) * line_h
    text_top = min(1565 - block_h, 1265)
    badge_f = _font(44, heavy=True)
    bw = d.textlength(game, font=badge_f) + 46
    bh = 62
    bx, by = (RW - bw) / 2, text_top - bh - 26
    d.rounded_rectangle([bx - 4, by - 4, bx + bw + 4, by + bh + 4], radius=12, fill=(255, 255, 255))
    d.rounded_rectangle([bx, by, bx + bw, by + bh], radius=10, fill=(226, 28, 52))
    _text(d, (RW / 2, by + 7), game, badge_f, (255, 255, 255), 4, anchor="ma")

    for i, ln in enumerate(lines):
        has_hype = any(w.strip(".,!?()'-") in _HYPE_WORDS for w in ln.split())
        color = (255, 214, 10) if (has_hype or (i == len(lines) - 1 and len(lines) > 1)) else (255, 255, 255)
        _text(d, (RW / 2, text_top + i * line_h), ln, tf, color, 8, anchor="ma")

    os.makedirs(os.path.dirname(os.path.abspath(out_jpg)), exist_ok=True)
    canvas.save(out_jpg, quality=92)
    return out_jpg
