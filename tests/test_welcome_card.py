"""Offline checks for the personalised welcome banner."""
import io
import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from PIL import Image
    from utils import welcome_card as wc
except ImportError:  # Pillow not installed in this environment
    wc = None


def _avatar(color=(200, 80, 80), size=128):
    buf = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buf, "PNG")
    return buf.getvalue()


@unittest.skipIf(wc is None, "Pillow not installed")
class WelcomeCard(unittest.TestCase):
    def _open(self, data):
        return Image.open(io.BytesIO(data)).convert("RGB")

    def test_assets_are_bundled(self):
        self.assertTrue(wc.TEMPLATE_PATH.exists())
        self.assertTrue(wc.FONT_BOLD.exists())

    def test_output_is_png_same_size_as_template(self):
        out = wc.render_welcome_card(_avatar(), "Sara")
        self.assertTrue(out.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(struct.unpack(">II", out[16:24]), wc._template().size)
        self.assertLess(len(out), 8_000_000)  # Discord's upload limit is 8 MB

    def test_avatar_colour_lands_in_the_circle(self):
        img = self._open(wc.render_welcome_card(_avatar((200, 40, 40)), "Sara"))
        r, g, b = img.getpixel((wc.AVATAR_CENTER[0], wc.AVATAR_CENTER[1] - 60))
        self.assertGreater(r, 150)
        self.assertLess(g, 90)

    def test_different_members_give_different_cards(self):
        a = wc.render_welcome_card(_avatar((200, 40, 40)), "Sara")
        b = wc.render_welcome_card(_avatar((40, 200, 40)), "Omar")
        self.assertNotEqual(a, b)

    def test_name_changes_the_pill(self):
        a = self._open(wc.render_welcome_card(None, "Sara")).crop(wc.NAME_BOX)
        b = self._open(wc.render_welcome_card(None, "Omar")).crop(wc.NAME_BOX)
        self.assertNotEqual(a.tobytes(), b.tobytes())

    def test_placeholder_name_is_wiped(self):
        # an empty-ish name must not leave the old "Member Name" text behind
        card = self._open(wc.render_welcome_card(None, "i")).crop((1100, 420, 1300, 490))
        self.assertLess(card.convert("L").getextrema()[1], 120)

    def test_no_avatar_or_bad_avatar_still_renders(self):
        self.assertTrue(wc.render_welcome_card(None, "Sara"))
        self.assertTrue(wc.render_welcome_card(b"not an image", "Sara"))

    def test_very_long_name_is_shortened_not_overflowing(self):
        font, text = wc._fit_name("W" * 200, wc.NAME_BOX[2] - wc.NAME_BOX[0])
        self.assertLessEqual(font.getlength(text), wc.NAME_BOX[2] - wc.NAME_BOX[0])
        self.assertTrue(text.endswith("…"))

    def test_emoji_only_name_falls_back(self):
        self.assertEqual(wc.pick_name(["😀🔥", "plain_user"]), "plain_user")
        self.assertEqual(wc.pick_name(["😀", ""]), "Member")

    def test_transparent_avatar_does_not_show_placeholder(self):
        buf = io.BytesIO()
        Image.new("RGBA", (64, 64), (0, 0, 0, 0)).save(buf, "PNG")
        img = self._open(wc.render_welcome_card(buf.getvalue(), "Sara"))
        # where the grey placeholder head used to be: now dark
        self.assertLess(sum(img.getpixel((480, 370))), 120)


class CogWiring(unittest.TestCase):
    def test_cog_uses_card_and_setting_has_a_default(self):
        cog = (ROOT / "cogs" / "welcome.py").read_text(encoding="utf-8")
        self.assertIn("render_welcome_card", cog)
        self.assertIn("asyncio.to_thread", cog)  # rendering must not block the event loop
        self.assertIn('"card": True', (ROOT / "database.py").read_text(encoding="utf-8"))
        self.assertIn("Pillow", (ROOT / "requirements.txt").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
