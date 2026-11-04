"""Small pure helpers for the moderation commands (durations, role-hierarchy rules)."""
import re

MAX_TIMEOUT_SECONDS = 28 * 24 * 3600   # Discord's limit for a timeout

_UNITS = {
    "w": 604800, "week": 604800, "weeks": 604800,
    "d": 86400, "day": 86400, "days": 86400,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
}
_TOKEN = re.compile(r"(\d+)\s*([a-z]+)", re.I)


def parse_duration(text):
    """'10m' / '1h30m' / '2 days' / '45' (minutes) -> seconds, or None if it can't be understood."""
    text = (text or "").strip().lower()
    if not text:
        return None
    if text.isdigit():
        return int(text) * 60                     # a bare number means minutes
    total, pos = 0, 0
    for match in _TOKEN.finditer(text):
        if text[pos:match.start()].strip(" ,+and"):
            return None                           # junk between the parts
        unit = _UNITS.get(match.group(2))
        if unit is None:
            return None
        total += int(match.group(1)) * unit
        pos = match.end()
    if text[pos:].strip(" ,+and") or pos == 0:
        return None
    return total or None


def format_seconds(seconds):
    """3725 -> '1h 2m 5s'."""
    seconds = int(seconds)
    parts = []
    for name, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        value, seconds = divmod(seconds, size)
        if value:
            parts.append(f"{value}{name}")
    return " ".join(parts) or "0s"


def hierarchy_error(*, actor_pos, target_pos, bot_pos, actor_is_owner=False, target_is_owner=False,
                    same_person=False, target_is_bot_self=False):
    """Why this moderator may not act on this member (None = allowed). Positions are top-role positions."""
    if same_person:
        return "You can't do that to yourself."
    if target_is_bot_self:
        return "I'm not going to do that to myself. 🙂"
    if target_is_owner:
        return "You can't do that to the server owner."
    if not actor_is_owner and actor_pos <= target_pos:
        return "That member's highest role is the same as or higher than yours."
    if bot_pos <= target_pos:
        return "That member's highest role is the same as or higher than mine — move my role up in Server Settings → Roles."
    return None
