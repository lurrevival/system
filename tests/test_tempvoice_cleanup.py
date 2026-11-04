"""Unit tests exercising real cleanup methods without connecting to Discord."""
import ast
import asyncio
from pathlib import Path
import types
import unittest
from unittest.mock import AsyncMock, Mock

ROOT = Path(__file__).resolve().parents[1]
TREE = ast.parse((ROOT / "cogs" / "tempvoice.py").read_text(encoding="utf-8"))
COG = next(node for node in TREE.body if isinstance(node, ast.ClassDef) and node.name == "TempVoice")
METHODS = {node.name: node for node in COG.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}

class Forbidden(Exception):
    pass

class NotFound(Exception):
    pass

class HTTPException(Exception):
    pass

class FakeVoiceChannel:
    def __init__(self, *, channel_id=123, members=None, error=None):
        self.id = channel_id
        self.members = members if members is not None else []
        self.guild = types.SimpleNamespace(id=777)
        self.error = error
        self.delete_count = 0

    async def delete(self, *, reason):
        self.delete_count += 1
        if self.error:
            raise self.error

class Member:
    def __init__(self, bot=False):
        self.bot = bot

class FakeGuild:
    def __init__(self, room=None, missing_error=None):
        self.id = 777
        self.room = room
        self.missing_error = missing_error
    def get_channel(self, channel_id):
        return self.room if self.room and self.room.id == channel_id else None
    async def fetch_channel(self, channel_id):
        if self.missing_error:
            raise self.missing_error
        return self.get_channel(channel_id)

class FakeDB:
    def __init__(self, guild_id=777, room_id=123):
        self.rooms = {room_id: {'channel_id': room_id, 'guild_id': guild_id}}
        self.removals = []
    async def all_rooms(self):
        return list(self.rooms.values())
    async def remove_room(self, room_id):
        self.removals.append(room_id)
        self.rooms.pop(room_id,None)
    async def get_room(self, room_id):
        return self.rooms.get(room_id)

names = ['_has_human_members', '_delete_room', '_cleanup_stale', '_delete_after_leave']
extracted = []
for name in names:
    node = METHODS[name]
    # Preserve the actual code, including the staticmethod decorator.
    extracted.append(node)
module = ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(name="TestCog", bases=[], keywords=[], body=extracted, decorator_list=[])], type_ignores=[]))
ns = {
    'asyncio': asyncio,
    'discord': types.SimpleNamespace(VoiceChannel=FakeVoiceChannel, Forbidden=Forbidden, NotFound=NotFound, HTTPException=HTTPException),
    'log': types.SimpleNamespace(warning=Mock(), info=Mock()),
    'ROOM_LEAVE_RECHECK_SECONDS': 0,
}
exec(compile(module, filename="cogs/tempvoice.py", mode="exec"), ns)

class RoomCleanupTests(unittest.IsolatedAsyncioTestCase):
    def init_cog(self, room=None, *, missing_error=None):
        cog = ns['TestCog']()
        cog._pending_creation = set()
        cog._deleting_rooms = set()
        guild = FakeGuild(room, missing_error=missing_error)
        if room is not None:
            room.guild = guild
        cog.bot = types.SimpleNamespace(db=FakeDB(), get_guild=lambda gid: guild)
        return cog

    async def test_successful_deletion_removes_record(self):
        channel = FakeVoiceChannel()
        cog = self.init_cog(channel)
        self.assertTrue(await cog._delete_room(channel))
        self.assertNotIn(channel.id, cog.bot.db.rooms)
        self.assertEqual(channel.delete_count, 1)

    async def test_permission_failure_keeps_room_for_retry(self):
        channel = FakeVoiceChannel(error=Forbidden('Missing Manage Channels'))
        cog = self.init_cog(channel)
        self.assertFalse(await cog._delete_room(channel))
        self.assertIn(channel.id, cog.bot.db.rooms)
        self.assertFalse(cog.bot.db.removals)
        channel.error = None
        await cog._cleanup_stale()
        self.assertNotIn(channel.id, cog.bot.db.rooms)
        self.assertEqual(channel.delete_count,2)

    async def test_not_found_clears_stale_record(self):
        channel = FakeVoiceChannel(error=NotFound('already deleted'))
        cog = self.init_cog(channel)
        self.assertTrue(await cog._delete_room(channel))
        self.assertFalse(cog.bot.db.rooms)

    async def test_sweep_deletes_empty_room(self):
        channel = FakeVoiceChannel()
        cog = self.init_cog(channel)
        await cog._cleanup_stale()
        self.assertEqual(channel.delete_count,1)
        self.assertFalse(cog.bot.db.rooms)

    async def test_sweep_deletes_bot_only_room(self):
        channel = FakeVoiceChannel(members=[Member(bot=True)])
        cog = self.init_cog(channel)
        await cog._cleanup_stale()
        self.assertEqual(channel.delete_count,1)

    async def test_sweep_preserves_room_with_human(self):
        channel = FakeVoiceChannel(members=[Member()])
        cog = self.init_cog(channel)
        await cog._cleanup_stale()
        self.assertEqual(channel.delete_count,0)
        self.assertTrue(cog.bot.db.rooms)

    async def test_sweep_skips_room_still_being_created(self):
        channel = FakeVoiceChannel()
        cog = self.init_cog(channel)
        cog._pending_creation.add(channel.id)
        await cog._cleanup_stale()
        self.assertEqual(channel.delete_count,0)

    async def test_stale_missing_room_record_removed_only_if_confirmed(self):
        cog = self.init_cog(missing_error=NotFound())
        await cog._cleanup_stale()
        self.assertFalse(cog.bot.db.rooms)

    async def test_failed_fetch_keeps_room_record(self):
        cog = self.init_cog(missing_error=Forbidden())
        await cog._cleanup_stale()
        self.assertIn(123,cog.bot.db.rooms)

    async def test_rejoin_cancels_scheduled_delete(self):
        channel = FakeVoiceChannel(members=[Member()])
        cog = self.init_cog(channel)
        await cog._delete_after_leave(channel)
        self.assertEqual(channel.delete_count,0)

    async def test_duplicate_inflight_delete_is_blocked(self):
        channel = FakeVoiceChannel()
        cog = self.init_cog(channel)
        cog._deleting_rooms.add(channel.id)
        self.assertFalse(await cog._delete_room(channel))
        self.assertEqual(channel.delete_count,0)

    async def test_manual_cleanup_command_exists(self):
        cmd = METHODS['tempvoice_cleanup']
        as_text = ast.unparse(cmd)
        self.assertIn('_has_human_members',as_text)
        self.assertIn('channel.id == cfg[\'hub_id\']',as_text)
        self.assertIn('await self._delete_room(channel)', as_text)

if __name__ == '__main__':
    unittest.main()
