"""Offline guards for the automatic per-voice-room TempVoice panel."""
import ast
from pathlib import Path
import re
import struct
import unittest

ROOT = Path(__file__).resolve().parents[1]
TV = ROOT / 'cogs' / 'tempvoice.py'

class AutoPanelIntegrity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = TV.read_text(encoding='utf-8')
        cls.tree = ast.parse(cls.text)
        cls.cog = next(n for n in cls.tree.body if isinstance(n, ast.ClassDef) and n.name=='TempVoice')

    def test_panel_image_is_bundled_valid_png(self):
        image = (ROOT/'assets'/'tempvoice_panel.png').read_bytes()
        self.assertTrue(image.startswith(b'\x89PNG\r\n\x1a\n'))
        width,height = struct.unpack('>II',image[16:24])
        self.assertGreaterEqual(width, 900)
        self.assertGreaterEqual(height, 300)
        self.assertLess(len(image), 1_000_000)

    def test_panel_still_has_fifteen_stable_action_ids(self):
        keys=re.findall(r'custom_id="mg3:tv:(\w+)"',self.text)
        self.assertEqual(len(keys),15)
        self.assertEqual(len(keys),len(set(keys)))
        self.assertEqual(set(keys),{
            'rename','limit','kick','transfer','delete',
            'lock','unlock','hide','show','region',
            'mute','unmute','block','allow','invite'})

    def test_room_creation_posts_after_successful_move(self):
        method=next(n for n in self.cog.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='_create_room')
        moves=[];panels=[]
        for node in ast.walk(method):
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute):
                if node.func.attr=='move_to':moves.append(node.lineno)
                if node.func.attr=='_send_panel':panels.append(node.lineno)
        self.assertGreaterEqual(len(moves),1)
        self.assertEqual(len(panels),1)
        self.assertLess(max(moves), panels[0])

    def test_manual_panel_and_media_fallback_exist(self):
        self.assertIn('avoid_duplicate=isinstance(channel, discord.VoiceChannel)',self.text)
        self.assertIn('PANEL_IMAGE.is_file()',self.text)
        self.assertIn('PANEL_TEXT',self.text)
        self.assertIn('"file"] = discord.File',self.text)
        self.assertIn('"view": self._panel_view()',self.text)

if __name__=='__main__':unittest.main()
