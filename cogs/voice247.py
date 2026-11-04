"""24/7 voice: keep the bot sitting in one voice channel all the time.

/247 action:on [channel]  - join a channel and stay there (defaults to the channel you are in)
/247 action:off           - leave and stop staying
/247 action:status        - show where the bot is meant to stay and whether it is there

The chosen channel is saved, so the bot rejoins after a restart, after being
kicked/disconnected, or after being dragged to another channel.
"""
import asyncio
import logging
from typing import Literal, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils.helpers import GREEN, RED, is_manager, make_embed, say

log = logging.getLogger("mg3")

WATCHDOG_SECONDS = 45     # how often the bot double-checks it is still in its channel
RECONNECT_DELAY = 3       # wait a moment after a disconnect before rejoining
CONNECT_TIMEOUT = 30


def channel_problem(channel, me):
    """Why the bot can't stay in `channel`, or None if it can. `me` is the bot's member."""
    if not isinstance(channel, discord.VoiceChannel):
        return "That isn't a regular voice channel (stage channels aren't supported)."
    perms = channel.permissions_for(me)
    if not perms.view_channel:
        return f"I can't see {channel.mention}. Give me **View Channel** there."
    if not perms.connect:
        return f"I can't join {channel.mention}. Give me **Connect** there."
    if channel.user_limit and len(channel.members) >= channel.user_limit \
            and not (perms.move_members or perms.manage_channels or perms.administrator):
        return f"{channel.mention} is full. Raise its user limit or give me **Move Members**."
    return None


class Voice247(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._locks = {}  # guild_id -> asyncio.Lock, so two reconnects never overlap

    async def cog_load(self):
        self.watchdog.start()

    async def cog_unload(self):
        self.watchdog.cancel()

    # ---- settings -----------------------------------------------------
    async def _cfg(self, guild_id):
        return (await self.bot.db.get_settings(guild_id)).setdefault("voice247", {"channel_id": None})

    async def _target(self, guild):
        """The channel id this server wants the bot in, or None."""
        return (await self._cfg(guild.id))["channel_id"]

    # ---- connecting ---------------------------------------------------
    def _lock(self, guild_id):
        return self._locks.setdefault(guild_id, asyncio.Lock())

    async def ensure_connected(self, guild):
        """Make the bot be in this server's 24/7 channel. Returns an error string or None."""
        channel_id = await self._target(guild)
        if not channel_id:
            return None
        async with self._lock(guild.id):
            channel = guild.get_channel(int(channel_id))
            if channel is None:
                return "The 24/7 channel no longer exists."
            problem = channel_problem(channel, guild.me)
            if problem:
                return problem
            client = guild.voice_client
            try:
                if client is not None and client.is_connected():
                    if client.channel.id != channel.id:
                        await client.move_to(channel)
                    return None
                if client is not None:  # half-dead leftover connection
                    await client.disconnect(force=True)
                await channel.connect(self_deaf=True, reconnect=True, timeout=CONNECT_TIMEOUT)
            except discord.ClientException as exc:
                if "already connected" in str(exc).lower():
                    return None
                log.warning("24/7 voice: could not join %s: %s", channel.id, exc)
                return f"Could not join: {exc}"
            except (asyncio.TimeoutError, discord.HTTPException) as exc:
                log.warning("24/7 voice: could not join %s: %r", channel.id, exc)
                return "Discord didn't let me connect (timed out). I'll keep retrying."
            except RuntimeError as exc:  # e.g. PyNaCl / davey missing
                log.error("24/7 voice: voice support is not installed: %s", exc)
                return f"Voice support is missing on the host: {exc}"
        log.info("24/7 voice: connected to %s in %s", channel.id, guild.id)
        return None

    # ---- keeping it there ---------------------------------------------
    @tasks.loop(seconds=WATCHDOG_SECONDS)
    async def watchdog(self):
        for guild in list(self.bot.guilds):
            try:
                await self.ensure_connected(guild)
            except Exception:
                log.exception("24/7 voice watchdog failed for %s", guild.id)

    @watchdog.before_loop
    async def _before_watchdog(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if self.bot.user is None or member.id != self.bot.user.id:
            return
        guild = member.guild
        channel_id = await self._target(guild)
        if not channel_id:
            return
        # Kicked/disconnected, or moved somewhere else: go back to the 24/7 channel.
        if after.channel is None or after.channel.id != int(channel_id):
            await asyncio.sleep(RECONNECT_DELAY)
            try:
                await self.ensure_connected(guild)
            except Exception:
                log.exception("24/7 voice reconnect failed for %s", guild.id)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        cfg = await self._cfg(channel.guild.id)
        if cfg["channel_id"] == channel.id:
            cfg["channel_id"] = None
            await self.bot.db.save_settings(channel.guild.id)
            log.info("24/7 voice: channel %s was deleted, turned 24/7 off", channel.id)

    # ---- command: /247 [on|off|status] [channel] ----------------------
    @commands.hybrid_command(name="247", aliases=["stay"], description="Keep the bot in a voice channel 24/7")
    @app_commands.describe(action="on = join and stay, off = leave, status = check",
                           channel="Voice channel to stay in (default: the one you are in)")
    @commands.guild_only()
    async def stay_247(self, ctx: commands.Context, action: Literal["on", "off", "status"] = "status",
                       channel: Optional[discord.VoiceChannel] = None):
        if ctx.guild is None:
            return await say(ctx, "Use this inside a server.", ephemeral=True)
        if not is_manager(ctx.author):
            return await say(ctx, "🔒 You need the **Manage Server** permission to use this command.",
                             ephemeral=True)
        if action == "on":
            await self._turn_on(ctx, channel)
        elif action == "off":
            await self._turn_off(ctx)
        else:
            await self._send_status(ctx)

    async def _turn_on(self, ctx, channel):
        if channel is None:
            voice = getattr(ctx.author, "voice", None)
            channel = voice.channel if voice else None
        if channel is None:
            return await say(ctx, "Join a voice channel first, or pick one: `/247 action:on channel:<voice channel>`.",
                             ephemeral=True)
        settings = await self.bot.db.get_settings(ctx.guild.id)
        settings.setdefault("voice247", {"channel_id": None})
        if channel.id == settings["tempvoice"]["hub_id"] or await self.bot.db.get_room(channel.id) is not None:
            return await say(ctx, "Pick a normal voice channel. Temp-voice rooms and the create-room hub "
                                  "are managed automatically.", ephemeral=True)
        problem = channel_problem(channel, ctx.guild.me)
        if problem:
            return await say(ctx, f"❌ {problem}", ephemeral=True)
        if ctx.interaction is not None:
            await ctx.defer(ephemeral=True)
        settings["voice247"]["channel_id"] = channel.id
        await self.bot.db.save_settings(ctx.guild.id)
        error = await self.ensure_connected(ctx.guild)
        if error:
            return await say(ctx, f"⚠️ Saved {channel.mention} as the 24/7 channel, but I couldn't join yet: "
                                  f"{error}\nI'll keep retrying every {WATCHDOG_SECONDS}s.", ephemeral=True)
        await say(ctx, embed=make_embed("24/7 voice is on", f"I'll stay in {channel.mention} and rejoin "
                                        "automatically if I get disconnected or the bot restarts.", color=GREEN),
                  ephemeral=True)

    async def _turn_off(self, ctx):
        cfg = await self._cfg(ctx.guild.id)
        was_on = cfg["channel_id"] is not None
        cfg["channel_id"] = None  # saved first so the reconnect listener doesn't bring us back
        await self.bot.db.save_settings(ctx.guild.id)
        client = ctx.guild.voice_client
        if client is not None:
            try:
                await client.disconnect(force=True)
            except Exception:
                log.exception("24/7 voice: disconnect failed in %s", ctx.guild.id)
        await say(ctx, embed=make_embed("24/7 voice is off",
                                        "I left the voice channel." if was_on else "It wasn't on.",
                                        color=RED), ephemeral=True)

    async def _send_status(self, ctx):
        channel_id = await self._target(ctx.guild)
        if not channel_id:
            return await say(ctx, "24/7 voice is **off**. Turn it on with `/247 action:on`.", ephemeral=True)
        channel = ctx.guild.get_channel(int(channel_id))
        client = ctx.guild.voice_client
        connected = client is not None and client.is_connected() and client.channel.id == int(channel_id)
        embed = make_embed("24/7 voice")
        embed.add_field(name="Channel", value=channel.mention if channel else f"missing ({channel_id})")
        embed.add_field(name="Connected", value="✅ yes" if connected else "❌ not right now (retrying)")
        await say(ctx, embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Voice247(bot))
    log.info("24/7 voice: cog ready (command: /247)")
