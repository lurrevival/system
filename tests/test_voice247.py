"""Offline checks for the 24/7 voice cog; no Discord login and no discord.py needed."""
import ast
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database import DEFAULT_SETTINGS, merge_defaults  # noqa: E402


class Voice247(unittest.TestCase):
    def test_cog_is_loaded_by_main(self):
        self.assertIn('"cogs.voice247"', (ROOT / "main.py").read_text(encoding="utf-8"))
        self.assertTrue((ROOT / "cogs" / "voice247.py").is_file())

    def test_voice_libraries_are_required(self):
        self.assertIn("discord.py[voice]", (ROOT / "requirements.txt").read_text(encoding="utf-8"))

    def test_default_setting_and_old_saves_still_load(self):
        self.assertEqual(DEFAULT_SETTINGS["voice247"], {"channel_id": None})
        old_save = {"welcome": {"enabled": True}}  # saved before this feature existed
        self.assertEqual(merge_defaults(DEFAULT_SETTINGS, old_save)["voice247"]["channel_id"], None)
        saved = merge_defaults(DEFAULT_SETTINGS, {"voice247": {"channel_id": 123}})
        self.assertEqual(saved["voice247"]["channel_id"], 123)

    def test_command_is_defined(self):
        text = (ROOT / "cogs" / "voice247.py").read_text(encoding="utf-8")
        self.assertIn('name="247"', text)
        self.assertIn('Literal["on", "off", "status"]', text)

    def test_no_fallback_clashes_with_a_subcommand(self):
        """fallback="status" plus a real "status" subcommand makes discord.py refuse to load the cog."""
        src = (ROOT / "cogs" / "voice247.py").read_text(encoding="utf-8")
        self.assertNotIn("fallback=", src)

    def test_help_mentions_the_command(self):
        self.assertIn("247 on", (ROOT / "cogs" / "general.py").read_text(encoding="utf-8"))

    def test_off_saves_before_disconnecting(self):
        """Otherwise the reconnect listener would rejoin right after /247 off."""
        src = (ROOT / "cogs" / "voice247.py").read_text(encoding="utf-8")
        off = src[src.index("async def _turn_off"):src.index("async def _send_status")]
        self.assertLess(off.index("save_settings"), off.index("disconnect"))


if __name__ == "__main__":
    unittest.main()
