"""XP / level maths. Level n -> n+1 costs 5n^2 + 50n + 100 XP."""


def xp_for_level(level):
    """XP needed to go from `level` to `level + 1`."""
    return 5 * level * level + 50 * level + 100


def total_xp_for_level(level):
    """Total XP needed to reach `level` from zero."""
    n = max(0, int(level))
    return 5 * (n - 1) * n * (2 * n - 1) // 6 + 50 * n * (n - 1) // 2 + 100 * n


def level_from_xp(xp):
    """Return (level, xp_into_level, xp_needed_for_next_level)."""
    xp = max(0, int(xp))
    level = 0
    while xp >= xp_for_level(level):
        xp -= xp_for_level(level)
        level += 1
    return level, xp, xp_for_level(level)
