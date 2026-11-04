"""Pure auto-moderation checks (no Discord imports, so they are easy to test)."""
import re
from collections import deque
from urllib.parse import urlparse

INVITE_RE = re.compile(
    r"(?:discord(?:app)?\.com/invite|discord\.gg|dsc\.gg|discord\.me)/[\w-]+", re.I
)
URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>()\[\]]+", re.I)


def has_invite(text):
    return bool(INVITE_RE.search(text or ""))


def normalize_domain(value):
    v = (value or "").strip().lower()
    v = re.sub(r"^[a-z]+://", "", v)
    v = re.split(r"[/?#:]", v, maxsplit=1)[0]
    if v.startswith("www."):
        v = v[4:]
    return v


def extract_domains(text):
    domains = []
    for found in URL_RE.findall(text or ""):
        raw = found if "://" in found else "http://" + found
        try:
            host = (urlparse(raw).hostname or "").lower()
        except ValueError:
            continue
        if host.startswith("www."):
            host = host[4:]
        if host:
            domains.append(host)
    return domains


def domain_allowed(domain, whitelist):
    return any(domain == w or domain.endswith("." + w) for w in whitelist)


def blocked_domains(text, whitelist):
    return [d for d in extract_domains(text) if not domain_allowed(d, whitelist)]


def caps_percent(text, min_letters=10):
    letters = [c for c in (text or "") if c.isalpha()]
    if len(letters) < min_letters:
        return 0.0
    return 100.0 * sum(1 for c in letters if c.isupper()) / len(letters)


def find_badword(text, words):
    lowered = (text or "").lower()
    for word in words:
        w = (word or "").strip().lower()
        if w and re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", lowered):
            return w
    return None


class SpamTracker:
    """Sliding-window event counter keyed by anything hashable."""

    def __init__(self):
        self._events = {}

    def record(self, key, now, item, window):
        events = self._events.setdefault(key, deque())
        events.append((now, item))
        while events and now - events[0][0] > window:
            events.popleft()
        if len(self._events) > 5000:
            self._prune(now, window)
        return [i for _, i in events]

    def clear(self, key):
        self._events.pop(key, None)

    def _prune(self, now, window):
        for key in list(self._events):
            events = self._events[key]
            if not events or now - events[-1][0] > window:
                del self._events[key]
