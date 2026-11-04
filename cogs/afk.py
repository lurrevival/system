import logging
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils.helpers import BLUE, admin_group, make_embed, respond, send_log

log = logging.getLogger("mg3.afk")

CHECK_SECONDS = 30


class Afk(commands.Cog):
    """Moves people who stay muted or deafened in a voice channel to an AFK channel."""

    afk = admin_group("afk", "Auto-move people who stay muted or deafened to an AFK channel")

    def __init__(self, bot):
        self.bot = bot
        self.since = {}   # (guild id, user id) -> time.monotonic() when they became muted/deafened

    async def cog_load(self):
        self.check_afk.start()

    async def cog_unload(self):
        self.check_afk.cancel()

    @staticmethod
    def is_idle_state(state):
        """In a voice channel and muted or deafened by themselves (not by a moderator)."""
        return bool(state is not None and state.channel is not None and (state.self_mute or state.self_deaf))

    # ---- tracking --------------------------------------------------------
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if member.bot:
            return
        key = (member.guild.id, member.id)
        if not self.is_idle_state(after):
            self.since.pop(key, None)       # unmuted, undeafened or left: the clock resets
            return
        self.since.setdefault(key, time.monotonic())

    @tasks.loop(seconds=CHECK_SECONDS)
    async def check_afk(self):
        try:
            await self._scan()
        except Exception:
            log.exception("AFK check failed")

    @check_afk.before_loop
    async def _before_check_afk(self):
        await self.bot.wait_until_ready()
        now = time.monotonic()   # people already muted when the bot starts: start their clock now
        for guild in self.bot.guilds:
            for channel in guild.voice_channels:
                for member in channel.members:
                    if not member.bot and self.is_idle_state(member.voice):
                        self.since.setdefault((guild.id, member.id), now)

    async def _scan(self, now=None):
        now = time.monotonic() if now is None else now
        for key, start in list(self.since.items()):
            guild_id, user_id = key
            guild = self.bot.get_guild(guild_id)
            member = guild.get_member(user_id) if guild else None
            if member is None or not self.is_idle_state(member.voice):
                self.since.pop(key, None)
                continue
            cfg = (await self.bot.db.get_settings(guild_id))["afk"]
            if not cfg["enabled"] or not cfg["channel_id"]:
                continue
            channel = member.voice.channel
            if channel.id == cfg["channel_id"]:
                self.since.pop(key, None)    # already in the AFK channel
                continue
            if now - start < cfg["minutes"] * 60:
                continue
            if channel.id in cfg["exempt_channels"] or any(r.id in cfg["exempt_roles"] for r in member.roles):
                continue
            target = guild.get_channel(cfg["channel_id"])
            if not isinstance(target, (discord.VoiceChannel, discord.StageChannel)):
                continue
            self.since.pop(key, None)        # whatever happens next, don't retry every 30 seconds
            try:
                await member.move_to(target, reason=f"Auto AFK: muted or deafened for {cfg['minutes']} minutes")
            except (discord.Forbidden, discord.HTTPException) as exc:
                log.warning("Could not move %s to the AFK channel: %s", user_id, exc)
                continue
            embed = make_embed("Moved to AFK", f"{member.mention} was muted or deafened in {channel.mention} for "
                               f"{cfg['minutes']} minutes and was moved to {target.mention}.", color=BLUE)
            await send_log(self.bot, guild, "voice", embed=embed)

    # ---- /afk (admin) -----------------------------------------------------
    @afk.command(name="setup", description="Turn on auto-AFK and choose the AFK voice channel")
    @app_commands.describe(channel="The AFK voice channel (default: the server's own AFK channel)",
                           minutes="Minutes of being muted/deafened before the move (default 10)")
    async def afk_setup(self, interaction: discord.Interaction, channel: Optional[discord.VoiceChannel] = None,
                        minutes: Optional[app_commands.Range[int, 1, 600]] = None):
        guild = interaction.guild
        channel = channel or guild.afk_channel
        if channel is None:
            return await respond(interaction, "Pick an AFK voice channel (the server has no built-in one set).")
        if not guild.me.guild_permissions.move_members:
            return await respond(interaction, "I need the **Move Members** permission to move people.")
        if not channel.permissions_for(guild.me).connect:
            return await respond(interaction, f"I need **Connect** in {channel.mention}.")
        cfg = (await self.bot.db.get_settings(interaction.guild_id))["afk"]
        cfg["enabled"], cfg["channel_id"] = True, channel.id
        if minutes is not None:
            cfg["minutes"] = minutes
        await self.bot.db.save_settings(interaction.guild_id)
        await respond(interaction, f"✅ Auto-AFK is **on**: anyone muted or deafened for **{cfg['minutes']} minutes** "
                                   f"is moved to {channel.mention}.")

    @afk.command(name="off", description="Turn off auto-AFK")
    async def afk_off(self, interaction: discord.Interaction):
        cfg = (await self.bot.db.get_settings(interaction.guild_id))["afk"]
        cfg["enabled"] = False
        await self.bot.db.save_settings(interaction.guild_id)
        await respond(interaction, "Auto-AFK is **off**.")

    @afk.command(name="exempt_role", description="Never move members who have this role")
    async def afk_exempt_role(self, interaction: discord.Interaction, role: discord.Role, exempt: bool = True):
        ids = (await self.bot.db.get_settings(interaction.guild_id))["afk"]["exempt_roles"]
        if exempt and role.id not in ids:
            ids.append(role.id)
        elif not exempt and role.id in ids:
            ids.remove(role.id)
        await self.bot.db.save_settings(interaction.guild_id)
        await respond(interaction, f"{role.mention} is {'now exempt' if exempt else 'no longer exempt'}.",
                      allowed_mentions=discord.AllowedMentions.none())

    @afk.command(name="exempt_channel", description="Never move people out of this voice channel")
    async def afk_exempt_channel(self, interaction: discord.Interaction, channel: discord.VoiceChannel,
                                 exempt: bool = True):
        ids = (await self.bot.db.get_settings(interaction.guild_id))["afk"]["exempt_channels"]
        if exempt and channel.id not in ids:
            ids.append(channel.id)
        elif not exempt and channel.id in ids:
            ids.remove(channel.id)
        await self.bot.db.save_settings(interaction.guild_id)
        await respond(interaction, f"{channel.mention} is {'now exempt' if exempt else 'no longer exempt'}.")

    @afk.command(name="show", description="Show the auto-AFK settings")
    async def afk_show(self, interaction: discord.Interaction):
        cfg = (await self.bot.db.get_settings(interaction.guild_id))["afk"]
        channel = f"<#{cfg['channel_id']}>" if cfg["channel_id"] else "not set"
        roles = " ".join(f"<@&{i}>" for i in cfg["exempt_roles"]) or "none"
        chans = " ".join(f"<#{i}>" for i in cfg["exempt_channels"]) or "none"
        await respond(interaction, embed=make_embed("Auto-AFK", (
            f"**Enabled:** {cfg['enabled']}\n**AFK channel:** {channel}\n**After:** {cfg['minutes']} minutes muted/deafened\n"
            f"**Exempt roles:** {roles}\n**Exempt channels:** {chans}")),
            allowed_mentions=discord.AllowedMentions.none())


async def setup(bot):
    await bot.add_cog(Afk(bot))
