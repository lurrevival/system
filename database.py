"""SQLite storage. Uses only the standard library; calls run in worker threads."""
import asyncio
import copy
import json
import os
import sqlite3
import threading
import time

DEFAULT_SETTINGS = {
    "log_channels": {},
    "welcome": {
        "enabled": False,
        "channel_id": None,
        "message": "Welcome {mention} to **{server}**! You are member #{count}.",
        "embed": True,
    },
    "autorole": {"members": [], "bots": []},
    "tickets": {
        "category_id": None,
        "staff_role_ids": [],
        "max_open": 1,
    },
    "security": {
        "enabled": True,
        "spam": {"enabled": True, "limit": 6, "window": 5},
        "mentions": {"enabled": True, "limit": 5},
        "invites": {"enabled": True},
        "links": {"enabled": False, "whitelist": []},
        "caps": {"enabled": False, "percent": 70, "min_length": 12},
        "badwords": {"enabled": False, "words": []},
        "raid": {"enabled": True, "joins": 8, "seconds": 10, "kick": False},
        "punish_minutes": 10,
        "min_account_age_days": 0,
        "exempt_roles": [],
        "exempt_channels": [],
    },
    "leveling": {
        "enabled": True,
        "announce": True,
        "channel_id": None,
        "xp_min": 15,
        "xp_max": 25,
        "cooldown": 60,
        "roles": {},
        "ignored_channels": [],
    },
    "afk": {
        "enabled": False,
        "channel_id": None,
        "minutes": 10,
        "exempt_roles": [],
        "exempt_channels": [],
    },
    "voice247": {"channel_id": None},
    "tempvoice": {
        "hub_id": None,
        "category_id": None,
        "name": "{user}'s room",
        "default_limit": 0,
    },
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id INTEGER PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS levels (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    xp INTEGER NOT NULL DEFAULT 0,
    messages INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    number INTEGER NOT NULL,
    channel_id INTEGER NOT NULL UNIQUE,
    user_id INTEGER NOT NULL,
    claimed_by INTEGER,
    status TEXT NOT NULL DEFAULT 'open',
    created_at REAL NOT NULL,
    closed_at REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_number ON tickets (guild_id, number);
CREATE TABLE IF NOT EXISTS bot_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS temp_rooms (
    channel_id INTEGER PRIMARY KEY,
    guild_id INTEGER NOT NULL,
    owner_id INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS warnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    moderator_id INTEGER NOT NULL,
    reason TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_warnings_user ON warnings (guild_id, user_id);
"""


def merge_defaults(defaults, saved):
    result = copy.deepcopy(defaults)
    if isinstance(saved, dict):
        for key, value in saved.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = merge_defaults(result[key], value)
            else:
                result[key] = value
    return result


class Database:
    def __init__(self, path):
        self.path = path
        self._conn = None
        self._lock = threading.Lock()
        self._settings = {}
        self._settings_lock = None

    # ---- connection -------------------------------------------------
    def _connect_sync(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        conn.commit()
        self._conn = conn

    async def connect(self):
        self._settings_lock = asyncio.Lock()
        await asyncio.to_thread(self._connect_sync)

    async def close(self):
        if self._conn is not None:
            await asyncio.to_thread(self._conn.close)
            self._conn = None

    def _run_sync(self, sql, params=(), mode="none"):
        with self._lock:
            cur = self._conn.execute(sql, params)
            if mode == "one":
                row = cur.fetchone()
                result = dict(row) if row else None
            elif mode == "all":
                result = [dict(r) for r in cur.fetchall()]
            elif mode == "rowcount":
                result = cur.rowcount
            elif mode == "lastrowid":
                result = cur.lastrowid
            else:
                result = None
            self._conn.commit()
            return result

    async def _run(self, sql, params=(), mode="none"):
        return await asyncio.to_thread(self._run_sync, sql, params, mode)

    # ---- settings ---------------------------------------------------
    async def get_settings(self, guild_id):
        cached = self._settings.get(guild_id)
        if cached is not None:
            return cached
        async with self._settings_lock:
            cached = self._settings.get(guild_id)
            if cached is not None:
                return cached
            row = await self._run(
                "SELECT data FROM guild_settings WHERE guild_id=?", (guild_id,), "one"
            )
            try:
                saved = json.loads(row["data"]) if row else {}
            except ValueError:
                saved = {}
            self._settings[guild_id] = merge_defaults(DEFAULT_SETTINGS, saved)
            return self._settings[guild_id]

    async def save_settings(self, guild_id):
        data = self._settings.get(guild_id)
        if data is None:
            return
        await self._run(
            "INSERT INTO guild_settings(guild_id, data) VALUES(?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET data=excluded.data",
            (guild_id, json.dumps(data)),
        )

    # ---- levels -----------------------------------------------------
    def _add_xp_sync(self, guild_id, user_id, amount):
        with self._lock:
            row = self._conn.execute(
                "SELECT xp FROM levels WHERE guild_id=? AND user_id=?", (guild_id, user_id)
            ).fetchone()
            old = row["xp"] if row else 0
            new = old + amount
            self._conn.execute(
                "INSERT INTO levels(guild_id, user_id, xp, messages) VALUES(?, ?, ?, 1) "
                "ON CONFLICT(guild_id, user_id) DO UPDATE SET xp=excluded.xp, messages=messages+1",
                (guild_id, user_id, new),
            )
            self._conn.commit()
            return old, new

    async def add_xp(self, guild_id, user_id, amount):
        return await asyncio.to_thread(self._add_xp_sync, guild_id, user_id, amount)

    async def set_xp(self, guild_id, user_id, xp):
        await self._run(
            "INSERT INTO levels(guild_id, user_id, xp, messages) VALUES(?, ?, ?, 0) "
            "ON CONFLICT(guild_id, user_id) DO UPDATE SET xp=excluded.xp",
            (guild_id, user_id, max(0, int(xp))),
        )

    async def get_level_row(self, guild_id, user_id):
        row = await self._run(
            "SELECT xp, messages FROM levels WHERE guild_id=? AND user_id=?",
            (guild_id, user_id), "one",
        )
        if row is None:
            return {"xp": 0, "messages": 0, "rank": None}
        ahead = await self._run(
            "SELECT COUNT(*) AS n FROM levels WHERE guild_id=? AND xp>?",
            (guild_id, row["xp"]), "one",
        )
        row["rank"] = ahead["n"] + 1
        return row

    async def leaderboard(self, guild_id, limit=10, offset=0):
        return await self._run(
            "SELECT user_id, xp, messages FROM levels WHERE guild_id=? "
            "ORDER BY xp DESC, user_id ASC LIMIT ? OFFSET ?",
            (guild_id, limit, offset), "all",
        )

    async def count_ranked(self, guild_id):
        row = await self._run(
            "SELECT COUNT(*) AS n FROM levels WHERE guild_id=?", (guild_id,), "one"
        )
        return row["n"]

    async def reset_user(self, guild_id, user_id):
        await self._run("DELETE FROM levels WHERE guild_id=? AND user_id=?", (guild_id, user_id))

    # ---- tickets ----------------------------------------------------
    async def next_ticket_number(self, guild_id):
        row = await self._run(
            "SELECT COALESCE(MAX(number), 0) + 1 AS n FROM tickets WHERE guild_id=?",
            (guild_id,), "one",
        )
        return row["n"]

    async def create_ticket(self, guild_id, number, channel_id, user_id):
        await self._run(
            "INSERT INTO tickets(guild_id, number, channel_id, user_id, created_at) "
            "VALUES(?, ?, ?, ?, ?)",
            (guild_id, number, channel_id, user_id, time.time()),
        )

    async def get_ticket(self, channel_id):
        return await self._run("SELECT * FROM tickets WHERE channel_id=?", (channel_id,), "one")

    async def open_tickets_for(self, guild_id, user_id):
        return await self._run(
            "SELECT * FROM tickets WHERE guild_id=? AND user_id=? AND status='open'",
            (guild_id, user_id), "all",
        )

    async def claim_ticket(self, channel_id, user_id):
        return await self._run(
            "UPDATE tickets SET claimed_by=? WHERE channel_id=? AND status='open' AND claimed_by IS NULL",
            (user_id, channel_id), "rowcount",
        )

    async def close_ticket(self, channel_id):
        return await self._run(
            "UPDATE tickets SET status='closed', closed_at=? WHERE channel_id=? AND status='open'",
            (time.time(), channel_id), "rowcount",
        )

    # ---- bot-wide state (e.g. the saved status) --------------------
    async def get_state(self, key, default=None):
        row = await self._run("SELECT value FROM bot_state WHERE key=?", (key,), "one")
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except ValueError:
            return default

    async def set_state(self, key, value):
        await self._run(
            "INSERT INTO bot_state(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    # ---- temp voice rooms ------------------------------------------
    async def add_room(self, channel_id, guild_id, owner_id):
        await self._run(
            "INSERT OR REPLACE INTO temp_rooms(channel_id, guild_id, owner_id) VALUES(?, ?, ?)",
            (channel_id, guild_id, owner_id),
        )

    async def get_room(self, channel_id):
        return await self._run("SELECT * FROM temp_rooms WHERE channel_id=?", (channel_id,), "one")

    async def set_room_owner(self, channel_id, owner_id):
        await self._run("UPDATE temp_rooms SET owner_id=? WHERE channel_id=?", (owner_id, channel_id))

    async def remove_room(self, channel_id):
        await self._run("DELETE FROM temp_rooms WHERE channel_id=?", (channel_id,))

    async def all_rooms(self):
        return await self._run("SELECT * FROM temp_rooms", (), "all")

    # ---- moderation warnings ---------------------------------------
    async def add_warning(self, guild_id, user_id, moderator_id, reason):
        """Save a warning and return its number."""
        return await self._run(
            "INSERT INTO warnings(guild_id, user_id, moderator_id, reason, created_at) VALUES(?, ?, ?, ?, ?)",
            (guild_id, user_id, moderator_id, reason, int(time.time())), "lastrowid",
        )

    async def get_warnings(self, guild_id, user_id):
        """A member's warnings, newest first."""
        return await self._run(
            "SELECT * FROM warnings WHERE guild_id=? AND user_id=? ORDER BY id DESC",
            (guild_id, user_id), "all",
        )

    async def delete_warning(self, guild_id, warning_id):
        """Delete one warning of this server; returns True if it existed."""
        count = await self._run("DELETE FROM warnings WHERE guild_id=? AND id=?", (guild_id, warning_id), "rowcount")
        return bool(count)

    async def clear_warnings(self, guild_id, user_id):
        """Delete all of a member's warnings; returns how many were removed."""
        return await self._run("DELETE FROM warnings WHERE guild_id=? AND user_id=?", (guild_id, user_id), "rowcount")
