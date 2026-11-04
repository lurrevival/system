"""Server allow-list helpers (pure, no Discord imports)."""
import re


def parse_id_list(raw):
    """'123, 456 789' -> {123, 456, 789}; anything that is not a number is ignored."""
    return {int(part) for part in re.split(r"[\s,;]+", raw or "") if part.isdigit()}


def should_leave(guild_id, allowed):
    """True when an allow-list is active and this server is not on it."""
    return bool(allowed) and guild_id not in allowed
