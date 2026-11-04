"""Renders the personalised welcome banner (no Discord imports, so it is easy to test).

Takes assets/welcome_template.png and swaps in the new member's avatar and name.
The template is never modified on disk; everything happens on a copy in memory.
"""
import io
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

ASSETS = Path(__file__).resolve().parent.parent / "assets"
TEMPLATE_PATH = ASSETS / "welcome_template.png"
FONT_REGULAR = ASSETS / "fonts" / "Outfit-Regular.ttf"
FONT_BOLD = ASSETS / "fonts" / "Outfit-Bold.ttf"

# ---- layout, measured from the 1774x887 template -----------------------
AVATAR_CENTER = (483, 431)
AVATAR_RADIUS = 194            # ring's inner edge is ~205, leaves a thin dark gap
BADGE_CENTER = (478, 630)      # the diamond badge on the ring (re-pasted over the avatar)
BADGE_HALF = 62

NAME_BOX = (922, 408, 1500, 506)    # x0, y0, x1, y1 where "Member Name" used to be
NAME_CLEAR = (900, 398, 1518, 514)  # area wiped before drawing (the "@" at x<895 stays)
NAME_MAX_SIZE = 62
NAME_MIN_SIZE = 30

TEXT_TOP = (246, 244, 255)       # silver-white
TEXT_BOTTOM = (178, 170, 214)    # soft lavender, matches the template glow
GLOW = (150, 135, 230)


@lru_cache(maxsize=1)
def _template():
    return Image.open(TEMPLATE_PATH).convert("RGB")


@lru_cache(maxsize=1)
def _clean_template():
    """Template with the placeholder name wiped.

    The area right of the old text is clean pill, so its rows are tiled across the wiped box.
    """
    img = _template().copy()
    x0, y0, x1, y1 = NAME_CLEAR
    w, h = x1 - x0, y1 - y0
    strip = img.crop((1345, y0, 1475, y1))                      # clean pill, no text or glow
    column = strip.resize((1, h), Image.BOX)                     # average each row
    patch = column.resize((w, h), Image.NEAREST)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rectangle((5, 5, w - 6, h - 6), fill=255)
    img.paste(patch, (x0, y0), mask.filter(ImageFilter.GaussianBlur(3)))
    return img


def _font(path, size):
    try:
        return ImageFont.truetype(str(path), size)
    except OSError:  # font file missing: Pillow's built-in font keeps the bot alive
        return ImageFont.load_default()


def _has_glyph(font, ch):
    """False when the font would draw its 'missing glyph' box for this character."""
    if ch.isspace():
        return True
    try:
        missing = font.getmask("\U0010FFFF")
        mask = font.getmask(ch)
        return not (mask.size == missing.size and bytes(mask) == bytes(missing))
    except Exception:
        return True


def clean_name(name, font):
    """Drop control chars and characters the font can't draw; collapse whitespace."""
    kept = "".join(ch for ch in (name or "") if ch.isprintable() and _has_glyph(font, ch))
    return " ".join(kept.split())


def pick_name(candidates, font=None):
    """First candidate that still has readable text after cleaning, else 'Member'."""
    font = font or _font(FONT_BOLD, NAME_MAX_SIZE)
    for cand in candidates:
        cleaned = clean_name(cand, font)
        if len(cleaned) >= max(1, len((cand or "").strip()) // 2):
            return cleaned
    return "Member"


def _fit_name(text, max_width):
    """Largest font size that fits max_width; ellipsis as a last resort."""
    for size in range(NAME_MAX_SIZE, NAME_MIN_SIZE - 1, -2):
        font = _font(FONT_BOLD, size)
        if font.getlength(text) <= max_width:
            return font, text
    font = _font(FONT_BOLD, NAME_MIN_SIZE)
    while len(text) > 1 and font.getlength(text + "…") > max_width:
        text = text[:-1]
    return font, text.rstrip() + "…"


def _draw_name(canvas, text, font):
    """Draw the gradient name + soft glow onto canvas (RGB), working only on a small crop."""
    pad = 30
    x0, y0, x1, y1 = NAME_BOX
    box = (x0 - pad, y0 - pad, x1 + pad, y1 + pad)
    size = (box[2] - box[0], box[3] - box[1])
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).text((pad, pad + (y1 - y0) // 2), text, font=font, fill=255, anchor="lm")
    grad = Image.linear_gradient("L").resize(size)  # black top -> white bottom
    fill = Image.composite(Image.new("RGB", size, TEXT_BOTTOM), Image.new("RGB", size, TEXT_TOP), grad)
    region = canvas.crop(box)
    glow_alpha = mask.filter(ImageFilter.GaussianBlur(9)).point(lambda v: int(v * 0.55))
    region.paste(Image.new("RGB", size, GLOW), (0, 0), glow_alpha)
    region.paste(fill, (0, 0), mask)
    canvas.paste(region, box[:2])


def _circle_avatar(avatar, diameter):
    avatar = avatar.convert("RGBA")
    side = min(avatar.size)
    left, top = (avatar.width - side) // 2, (avatar.height - side) // 2
    avatar = avatar.crop((left, top, left + side, top + side))
    scale = 4  # supersample the mask for smooth edges
    avatar = avatar.resize((diameter, diameter), Image.LANCZOS)
    mask = Image.new("L", (diameter * scale, diameter * scale), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, diameter * scale - 1, diameter * scale - 1), fill=255)
    avatar.putalpha(mask.resize((diameter, diameter), Image.LANCZOS))
    return avatar


def render_welcome_card(avatar_bytes, display_name, fallback_names=()):
    """Return PNG bytes of the welcome banner for one member.

    avatar_bytes: raw image bytes (any Pillow-readable format) or None for the default placeholder.
    display_name: what to print in the name pill.
    fallback_names: tried in order if display_name has no characters the font can draw (e.g. only emoji).
    """
    canvas = _clean_template().copy()
    original = _template()

    # ---- avatar ----
    if avatar_bytes:
        try:
            with Image.open(io.BytesIO(avatar_bytes)) as src:
                src.seek(0)  # first frame of animated avatars
                avatar = src.convert("RGBA")
            cx, cy = AVATAR_CENTER
            r = AVATAR_RADIUS
            circ = _circle_avatar(avatar, r * 2)
            # dark backing disc so transparent avatars don't show the grey placeholder silhouette
            backing = Image.new("L", (r * 2, r * 2), 0)
            ImageDraw.Draw(backing).ellipse((0, 0, r * 2 - 1, r * 2 - 1), fill=255)
            canvas.paste(Image.new("RGB", (r * 2, r * 2), (14, 13, 20)), (cx - r, cy - r), backing)
            canvas.paste(circ, (cx - r, cy - r), circ.getchannel("A"))
            # put the diamond badge back on top of the avatar
            bx, by, bh = BADGE_CENTER[0], BADGE_CENTER[1], BADGE_HALF
            box = (bx - bh - 6, by - bh - 6, bx + bh + 6, by + bh + 6)
            size = (box[2] - box[0], box[3] - box[1])
            badge_mask = Image.new("L", size, 0)
            ImageDraw.Draw(badge_mask).polygon(
                [(size[0] // 2, 6), (size[0] - 6, size[1] // 2), (size[0] // 2, size[1] - 6), (6, size[1] // 2)], fill=255)
            canvas.paste(original.crop(box), box[:2], badge_mask.filter(ImageFilter.GaussianBlur(1.2)))
        except Exception:
            canvas = _clean_template().copy()  # unreadable avatar: keep the placeholder silhouette

    # ---- name ----
    probe = _font(FONT_BOLD, NAME_MAX_SIZE)
    name = pick_name([display_name, *fallback_names], probe)
    font, name = _fit_name(name, NAME_BOX[2] - NAME_BOX[0])
    _draw_name(canvas, name, font)

    out = io.BytesIO()
    canvas.save(out, format="PNG", compress_level=4)
    return out.getvalue()
