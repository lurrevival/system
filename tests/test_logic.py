import asyncio
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import DEFAULT_SETTINGS, Database, merge_defaults  # noqa: E402
from utils import automod  # noqa: E402
from utils.guards import parse_id_list, should_leave  # noqa: E402
from utils.profile import (clean_presence, sniff_image, to_data_uri, valid_stream_url,  # noqa: E402
                           validate_about, validate_name)
from utils.leveling_math import level_from_xp, total_xp_for_level, xp_for_level  # noqa: E402
from utils.text import format_duration, progress_bar, render_template, truncate  # noqa: E402


class LevelingMath(unittest.TestCase):
    def test_cost_curve(self):
        self.assertEqual(xp_for_level(0), 100)
        self.assertEqual(xp_for_level(1), 155)
        self.assertEqual(xp_for_level(10), 1100)

    def test_total_matches_loop(self):
        running = 0
        for level in range(0, 60):
            self.assertEqual(total_xp_for_level(level), running)
            running += xp_for_level(level)

    def test_level_from_xp_roundtrip(self):
        for level in (0, 1, 5, 20, 100):
            self.assertEqual(level_from_xp(total_xp_for_level(level))[0], level)
            if level:
                self.assertEqual(level_from_xp(total_xp_for_level(level) - 1)[0], level - 1)
        level, into, needed = level_from_xp(120)
        self.assertEqual((level, into, needed), (1, 20, 155))
        self.assertEqual(level_from_xp(-5)[0], 0)


class TextHelpers(unittest.TestCase):
    def test_template(self):
        out = render_template("Hi {mention} to {server} #{count} {unknown}", mention="@a", server="S", count=5)
        self.assertEqual(out, "Hi @a to S #5 {unknown}")
        self.assertEqual(render_template("{__class__}", user="x"), "{__class__}")

    def test_misc(self):
        self.assertEqual(truncate("abcdef", 4), "abc…")
        self.assertEqual(format_duration(65), "1:05")
        self.assertEqual(format_duration(3725), "1:02:05")
        self.assertEqual(format_duration(None), "live/unknown")
        self.assertEqual(progress_bar(0.5, 4), "▰▰▱▱")
        self.assertEqual(progress_bar(9, 4), "▰▰▰▰")


class AutoModLogic(unittest.TestCase):
    def test_invites(self):
        for text in ("join discord.gg/abc123", "https://discord.com/invite/xyz", "DISCORDAPP.COM/invite/Q"):
            self.assertTrue(automod.has_invite(text), text)
        self.assertFalse(automod.has_invite("discord is great, gg"))

    def test_links(self):
        self.assertEqual(automod.extract_domains("see https://www.Example.com/a?b=1 and www.foo.org"),
                         ["example.com", "foo.org"])
        self.assertEqual(automod.blocked_domains("https://youtube.com/x https://evil.io", ["youtube.com"]), ["evil.io"])
        self.assertEqual(automod.blocked_domains("https://m.youtube.com/x", ["youtube.com"]), [])
        self.assertEqual(automod.blocked_domains("https://notyoutube.com", ["youtube.com"]), ["notyoutube.com"])
        self.assertEqual(automod.normalize_domain("https://WWW.YouTube.com/watch?v=1"), "youtube.com")
        self.assertEqual(automod.extract_domains("no links here"), [])

    def test_caps(self):
        self.assertGreaterEqual(automod.caps_percent("STOP SHOUTING AT ME", 10), 99)
        self.assertEqual(automod.caps_percent("HI", 10), 0.0)
        self.assertLess(automod.caps_percent("This is a normal sentence", 10), 20)

    def test_badwords(self):
        self.assertEqual(automod.find_badword("you are a Jerk!", ["jerk"]), "jerk")
        self.assertIsNone(automod.find_badword("jerky is tasty", ["jerk"]))
        self.assertIsNone(automod.find_badword("hello", ["", "  "]))

    def test_spam_window(self):
        tracker = automod.SpamTracker()
        key = (1, 2)
        for i in range(5):
            items = tracker.record(key, float(i), i, window=5)
        self.assertEqual(len(items), 5)
        items = tracker.record(key, 20.0, "late", window=5)
        self.assertEqual(items, ["late"])
        tracker.clear(key)
        self.assertEqual(tracker.record(key, 21.0, "x", window=5), ["x"])


class ServerGuard(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_id_list("123, 456 789;abc,"), {123, 456, 789})
        self.assertEqual(parse_id_list(None), set())
        self.assertEqual(parse_id_list(""), set())

    def test_should_leave(self):
        self.assertFalse(should_leave(5, set()))        # no list = no restriction
        self.assertFalse(should_leave(5, {5, 6}))
        self.assertTrue(should_leave(7, {5, 6}))


class ProfileHelpers(unittest.TestCase):
    PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20

    def test_image_sniffing(self):
        self.assertEqual(sniff_image(self.PNG), "image/png")
        self.assertEqual(sniff_image(b"\xff\xd8\xff\xe0abc"), "image/jpeg")
        self.assertEqual(sniff_image(b"GIF89a...."), "image/gif")
        self.assertEqual(sniff_image(b"RIFF\x00\x00\x00\x00WEBPVP8 "), "image/webp")
        self.assertIsNone(sniff_image(b"<html>not an image</html>"))
        self.assertTrue(to_data_uri(self.PNG).startswith("data:image/png;base64,"))
        with self.assertRaises(ValueError):
            to_data_uri(b"MZ\x90\x00 an exe")

    def test_validation(self):
        self.assertEqual(validate_name("  MG3 Bot "), "MG3 Bot")
        for bad in ("", "a", "x" * 33):
            with self.assertRaises(ValueError):
                validate_name(bad)
        self.assertEqual(validate_about(" hi "), "hi")
        with self.assertRaises(ValueError):
            validate_about("x" * 401)
        with self.assertRaises(ValueError):
            validate_about("   ")

    def test_stream_urls(self):
        for ok in ("https://twitch.tv/name", "https://www.twitch.tv/name", "https://youtube.com/watch?v=1", "https://youtu.be/abc"):
            self.assertTrue(valid_stream_url(ok), ok)
        for bad in ("", "twitch.tv/name", "http://twitch.tv/name", "https://example.com/x", "https://twitch.tv.evil.com/x"):
            self.assertFalse(valid_stream_url(bad), bad)

    def test_presence_cleaning(self):
        d = clean_presence(None, "MG3")
        self.assertEqual((d["status"], d["kind"], d["text"]), ("online", "playing", "MG3 | /help"))
        s = clean_presence({"status": "dnd", "kind": "watching", "text": " over the server "}, "MG3")
        self.assertEqual((s["status"], s["kind"], s["text"]), ("dnd", "watching", "over the server"))
        self.assertEqual(clean_presence({"status": "bogus", "kind": "nope", "text": "x"}, "MG3")["status"], "online")
        self.assertEqual(clean_presence({"status": "idle", "kind": "none", "text": ""}, "MG3")["kind"], "none")
        st = clean_presence({"status": "online", "kind": "streaming", "text": "Live!", "url": "https://twitch.tv/me"}, "MG3")
        self.assertEqual((st["kind"], st["url"]), ("streaming", "https://twitch.tv/me"))
        bad = clean_presence({"kind": "streaming", "text": "Live!", "url": "https://evil.example/x"}, "MG3")
        self.assertEqual(bad["kind"], "playing")   # streaming without a valid link falls back


class DatabaseTests(unittest.TestCase):
    def run_db(self, coro_fn):
        async def runner():
            with tempfile.TemporaryDirectory() as tmp:
                db = Database(os.path.join(tmp, "sub", "bot.db"))
                await db.connect()
                try:
                    return await coro_fn(db, tmp)
                finally:
                    await db.close()
        return asyncio.run(runner())

    def test_merge_defaults(self):
        merged = merge_defaults(DEFAULT_SETTINGS, {"security": {"spam": {"limit": 9}}, "extra": 1})
        self.assertEqual(merged["security"]["spam"]["limit"], 9)
        self.assertEqual(merged["security"]["spam"]["window"], 5)
        self.assertEqual(DEFAULT_SETTINGS["security"]["spam"]["limit"], 6)  # defaults untouched

    def test_settings_persist(self):
        async def go(db, tmp):
            s = await db.get_settings(10)
            s["welcome"]["enabled"] = True
            s["leveling"]["roles"]["5"] = 123
            await db.save_settings(10)
            db2 = Database(db.path)
            await db2.connect()
            s2 = await db2.get_settings(10)
            await db2.close()
            other = await db.get_settings(11)
            return s2, other
        s2, other = self.run_db(go)
        self.assertTrue(s2["welcome"]["enabled"])
        self.assertEqual(s2["leveling"]["roles"], {"5": 123})
        self.assertFalse(other["welcome"]["enabled"])

    def test_levels(self):
        async def go(db, tmp):
            self.assertEqual(await db.add_xp(1, 100, 20), (0, 20))
            self.assertEqual(await db.add_xp(1, 100, 30), (20, 50))
            await db.add_xp(1, 200, 80)
            await db.add_xp(2, 100, 999)  # other guild
            row = await db.get_level_row(1, 100)
            top = await db.leaderboard(1, 10)
            none = await db.get_level_row(1, 555)
            await db.set_xp(1, 100, 500)
            await db.reset_user(1, 200)
            return row, top, none, await db.count_ranked(1), (await db.get_level_row(1, 100))["xp"]
        row, top, none, count, xp = self.run_db(go)
        self.assertEqual((row["xp"], row["messages"], row["rank"]), (50, 2, 2))
        self.assertEqual([r["user_id"] for r in top], [200, 100])
        self.assertIsNone(none["rank"])
        self.assertEqual((count, xp), (1, 500))

    def test_tickets(self):
        async def go(db, tmp):
            n1 = await db.next_ticket_number(1)
            await db.create_ticket(1, n1, 900, 5)
            n2 = await db.next_ticket_number(1)
            await db.create_ticket(1, n2, 901, 6)
            open_for_5 = await db.open_tickets_for(1, 5)
            claimed1 = await db.claim_ticket(900, 77)
            claimed2 = await db.claim_ticket(900, 78)
            closed1 = await db.close_ticket(900)
            closed2 = await db.close_ticket(900)
            return n1, n2, len(open_for_5), claimed1, claimed2, closed1, closed2, await db.get_ticket(900), await db.next_ticket_number(2)
        n1, n2, count, c1, c2, x1, x2, ticket, other_guild = self.run_db(go)
        self.assertEqual((n1, n2, count, c1, c2, x1, x2, other_guild), (1, 2, 1, 1, 0, 1, 0, 1))
        self.assertEqual((ticket["status"], ticket["claimed_by"]), ("closed", 77))

    def test_bot_state(self):
        async def go(db, tmp):
            self.assertIsNone(await db.get_state("presence"))
            await db.set_state("presence", {"status": "idle"})
            await db.set_state("presence", {"status": "dnd", "text": "é"})
            return await db.get_state("presence")
        state = self.run_db(go)
        self.assertEqual(state, {"status": "dnd", "text": "é"})

    def test_rooms(self):
        async def go(db, tmp):
            await db.add_room(10, 1, 5)
            await db.set_room_owner(10, 6)
            room = await db.get_room(10)
            await db.remove_room(10)
            return room, await db.get_room(10), await db.all_rooms()
        room, gone, rooms = self.run_db(go)
        self.assertEqual(room["owner_id"], 6)
        self.assertIsNone(gone)
        self.assertEqual(rooms, [])


if __name__ == "__main__":
    unittest.main()
