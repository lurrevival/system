"""Offline integrity checks for user-supplied V3 PNG icons in the dor TempVoice panel."""
import ast
from pathlib import Path
import re
import struct
import unittest

ROOT = Path(__file__).resolve().parents[1]
CODE = (ROOT / 'cogs' / 'tempvoice.py').read_text(encoding='utf-8')
ICON_DIR = ROOT / 'assets' / 'tempvoice_icons'
KEYS = ('rename', 'limit', 'kick', 'transfer', 'delete',
        'lock', 'unlock', 'hide', 'show', 'region',
        'mute', 'unmute', 'block', 'allow', 'invite')


class TempVoiceIconTests(unittest.TestCase):
    def test_all_fifteen_custom_icons_bundled_as_discord_compatible_pngs(self):
        self.assertEqual({path.name for path in ICON_DIR.glob('*.png')},
                         {f'{key}.png' for key in KEYS})
        for key in KEYS:
            data = (ICON_DIR / f'{key}.png').read_bytes()
            self.assertTrue(data.startswith(b'\x89PNG\r\n\x1a\n'))
            self.assertEqual(struct.unpack('>II', data[16:24]), (128, 128))
            self.assertLess(len(data), 256 * 1024)

    def test_icons_are_optically_centered(self):
        from PIL import Image
        import numpy as np
        for key in KEYS:
            image=Image.open(ICON_DIR / f'{key}.png').convert('RGBA')
            a=np.asarray(image.getchannel('A'),dtype=np.float64)
            self.assertGreater(a.sum(),0,key)
            y,x=np.indices(a.shape)
            mx=(x*a).sum()/a.sum()
            my=(y*a).sum()/a.sum()
            self.assertLess(abs(mx-63.5), 2.5, key)
            self.assertLess(abs(my-63.5), 2.5, key)
            # Icons have sufficient padding on all sides.
            yy,xx=np.where(a>35)
            self.assertGreaterEqual(int(xx.min()), 15,key)
            self.assertLessEqual(int(xx.max()),112,key)
            self.assertGreaterEqual(int(yy.min()),15,key)
            self.assertLessEqual(int(yy.max()),112,key)

    def test_original_user_pngs_and_panel_are_preserved(self):
        originals = ROOT / 'assets' / 'original_tempo_pngs'
        if not originals.is_dir():
            self.skipTest('assets/original_tempo_pngs was not included in this copy of the project')
        self.assertEqual({p.name for p in originals.glob('*.png')},
                         {f'icon-{n:02}.png' for n in range(1, 16)} | {'tempvoice-panel.png'})
        self.assertNotEqual((originals/'tempvoice-panel.png').read_bytes(),
                         (ROOT/'assets'/'tempvoice_panel.png').read_bytes())
        for idx, key in enumerate(KEYS, 1):
            raw=(originals / f'icon-{idx:02}.png').read_bytes()
            self.assertEqual(struct.unpack('>II', raw[16:24]), (58,58))
            self.assertTrue(raw.startswith(b'\x89PNG\r\n\x1a\n'))
            self.assertTrue((ICON_DIR/f'{key}.png').is_file())

    def test_stable_buttons_are_not_replaced(self):
        actual = re.findall(r'custom_id="mg3:tv:(\w+)"', CODE)
        self.assertEqual(tuple(actual), KEYS)

    def test_auto_upload_reuses_existing_emojis_without_duplicate_creation(self):
        self.assertIn('fetch_application_emojis()', CODE)
        self.assertIn('found.get(name)', CODE)
        self.assertIn('if emoji is None:', CODE)
        self.assertIn('create_application_emoji(name=name, image=path.read_bytes())', CODE)
        self.assertIn('PANEL_APP_PREFIX = "dor_v6_ai_"', CODE)

    def test_runtime_uses_the_user_provided_assets(self):
        self.assertIn('PANEL_ICON_DIR = PANEL_IMAGE.parent / "tempvoice_icons"', CODE)
        self.assertIn('PANEL_FILENAME = "dor-tempvoice-panel.png"', CODE)
        self.assertIn('"view": self._panel_view()', CODE)

    def test_existing_panels_are_edited_not_resent(self):
        self.assertIn('await msg.edit(view=self._panel_view())', CODE)
        self.assertIn('await self._upgrade_existing_panels()', CODE)
        self.assertIn('self.bot.user.id', CODE)

    def test_missing_emojis_fall_back_to_working_unicode_buttons(self):
        self.assertIn('self.bot.add_view(TempVoicePanel())', CODE)
        self.assertIn('if key in (icon_emojis or {}):', CODE)
        self.assertIn('self._panel_emojis = updated', CODE)

    def test_all_local_python_modules_compile(self):
        for path in ROOT.rglob('*.py'):
            if path.is_relative_to(ROOT / 'data'):
                continue
            ast.parse(path.read_text(encoding='utf-8'), filename=str(path))


if __name__ == '__main__':
    unittest.main()
