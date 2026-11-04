"""Pure helpers for the bot-profile commands (no Discord imports, so they are easy to test)."""
import base64
import re

STATUS_NAMES = ("online", "idle", "dnd", "offline")
ACTIVITY_KINDS = ("playing", "watching", "listening", "competing", "custom", "streaming")
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_ABOUT = 400


def sniff_image(data):
    """Return the MIME type of PNG/JPEG/GIF/WebP bytes, or None. Never trusts the file name."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def to_data_uri(data):
    mime = sniff_image(data)
    if mime is None:
        raise ValueError("That file isn't a PNG, JPEG, GIF or WebP image.")
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def validate_name(name):
    name = (name or "").strip()
    if not 2 <= len(name) <= 32:
        raise ValueError("A bot name must be 2 to 32 characters long.")
    return name


def validate_about(text):
    text = (text or "").strip()
    if not text:
        raise ValueError("Write something for the About Me text.")
    if len(text) > MAX_ABOUT:
        raise ValueError(f"About Me can be at most {MAX_ABOUT} characters (yours is {len(text)}).")
    return text


_STREAM_URL = re.compile(r"^https://(?:www\.|m\.)?(?:twitch\.tv|youtube\.com|youtu\.be)/\S+$", re.I)


def valid_stream_url(url):
    """Discord only shows the purple 'Streaming' status for Twitch and YouTube links."""
    return bool(_STREAM_URL.match((url or "").strip()))


def default_presence(bot_name):
    return {"status": "online", "kind": "playing", "text": f"{bot_name} | /help", "url": ""}


def clean_presence(saved, bot_name):
    """Merge a saved presence dict with the defaults and drop anything invalid."""
    state = default_presence(bot_name)
    if isinstance(saved, dict):
        if saved.get("status") in STATUS_NAMES:
            state["status"] = saved["status"]
        if "text" in saved:
            kind, text = saved.get("kind"), saved.get("text")
            if kind in ACTIVITY_KINDS and isinstance(text, str) and text.strip():
                if kind != "streaming":
                    state["kind"], state["text"] = kind, text.strip()[:128]
                elif valid_stream_url(saved.get("url")):
                    state["kind"], state["text"], state["url"] = kind, text.strip()[:128], saved["url"].strip()
            elif kind == "none" or not text:
                state["kind"], state["text"] = "none", ""
    return state
