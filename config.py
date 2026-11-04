import os

from dotenv import load_dotenv

from utils.guards import parse_id_list

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _int(name, default=None):
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _color(name, default):
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw, 16)
    except ValueError:
        return default


TOKEN = (os.getenv("DISCORD_TOKEN") or "").strip()
GUILD_ID = _int("GUILD_ID")
# Servers the bot may stay in: GUILD_ID plus anything in ALLOWED_GUILDS (comma separated).
# If both are empty there is no restriction.
ALLOWED_GUILDS = parse_id_list(os.getenv("ALLOWED_GUILDS")) | ({GUILD_ID} if GUILD_ID else set())
DATA_DIR = os.getenv("DATA_DIR") or os.path.join(BASE_DIR, "data")
BOT_NAME = os.getenv("BOT_NAME") or "dor"
PREFIX = (os.getenv("BOT_PREFIX") or "!").strip() or "!"
EMBED_COLOR = _color("EMBED_COLOR", 0x5865F2)
