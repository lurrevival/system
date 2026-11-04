"""Pure text helpers (no Discord imports, so they are easy to test)."""
import re

_TEMPLATE_RE = re.compile(r"\{(user|mention|server|count|level|name)\}", re.I)


def render_template(template, **values):
    """Replace only the known {placeholders}; anything else is left untouched."""
    def repl(match):
        value = values.get(match.group(1).lower())
        return str(value) if value is not None else match.group(0)
    return _TEMPLATE_RE.sub(repl, template or "")


def embed_to_text(embed, limit=1900):
    """Plain-text version of an embed (anything with title/description/fields), for channels that block embeds."""
    lines = []
    if getattr(embed, "title", None):
        lines.append(f"**{embed.title}**")
    if getattr(embed, "description", None):
        lines.append(str(embed.description))
    for field in getattr(embed, "fields", None) or []:
        lines.append(f"**{field.name}:** {field.value}")
    text = "\n".join(lines) or "(empty log)"
    return text if len(text) <= limit else text[: limit - 1] + "…"


def truncate(text, limit, suffix="…"):
    text = str(text if text is not None else "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(suffix))] + suffix


def format_duration(seconds):
    if seconds is None:
        return "live/unknown"
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def progress_bar(fraction, width=12):
    fraction = max(0.0, min(1.0, float(fraction)))
    filled = int(round(fraction * width))
    return "▰" * filled + "▱" * (width - filled)
