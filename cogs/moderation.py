"""Manual moderation. Every command works as a slash command and with the prefix: /mute and !mute."""
import logging
from datetime import timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils.helpers import GREEN, RED, YELLOW, make_embed, say, send_log, update_overwrite
from utils.modtools import MAX_TIMEOUT_SECONDS, format_seconds, hierarchy_error, parse_duration
from utils.text import truncate

log = logging.getLogger("mg3.moderation")

NO_REASON = "No reason given"
DURATION_HELP = "Use a duration like `10m`, `2h`, `1d` or `1h30m` (a bare number means minutes; max 28 days)."


def _mod_perms(**perms):
    """Both checks every command needs: the moderator has the permission and so does the bot."""
    def decorator(func):
        func = commands.bot_has_permissions(**perms)(func)
        func = commands.has_permissions(**perms)(func)
        return commands.guild_only()(func)
    return decorator


class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ---- helpers ---------------------------------------------------------
    @staticmethod
    def _audit(ctx, reason):
        return truncate(f"{ctx.author} ({ctx.author.id}): {reason}", 512)

    @staticmethod
    def _hierarchy(ctx, member, needs_bot_role=True):
        guild = ctx.guild
        return hierarchy_error(
            actor_pos=ctx.author.top_role.position, target_pos=member.top_role.position,
            bot_pos=guild.me.top_role.position if needs_bot_role else float("inf"), actor_is_owner=ctx.author.id == guild.owner_id,
            target_is_owner=member.id == guild.owner_id, same_person=member.id == ctx.author.id,
            target_is_bot_self=member.id == guild.me.id)

    async def _dm(self, user, guild, title, reason, color=RED, extra=None):
        description = f"**Server:** {guild.name}\n**Reason:** {truncate(reason, 500)}"
        if extra:
            description += f"\n{extra}"
        try:
            await user.send(embed=make_embed(title, description, color=color))
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

    @staticmethod
    def _done(ctx, text, color=GREEN, note=""):
        return make_embed(None, f"{text}{note}", color=color)

    async def _log(self, ctx, title, description, color=YELLOW):
        embed = make_embed(title, description + f"\n**Moderator:** {ctx.author.mention}", color=color)
        await send_log(self.bot, ctx.guild, "moderation", embed=embed)

    # ---- kick / ban / unban ------------------------------------------------
    @commands.hybrid_command(name="kick", description="Remove a member from the server (they can rejoin)")
    @_mod_perms(kick_members=True)
    @app_commands.describe(member="Who to kick", reason="Why")
    async def kick(self, ctx: commands.Context, member: discord.Member, *, reason: Optional[str] = None):
        error = self._hierarchy(ctx, member)
        if error:
            return await say(ctx, error, ephemeral=True)
        reason = reason or NO_REASON
        dmed = await self._dm(member, ctx.guild, "You were kicked", reason)
        try:
            await member.kick(reason=self._audit(ctx, reason))
        except discord.HTTPException:
            return await say(ctx, "I couldn't kick that member.", ephemeral=True)
        await say(ctx, embed=self._done(ctx, f"👢 **{member}** was kicked.\n**Reason:** {truncate(reason, 300)}",
                                        note="" if dmed else "\n*(I couldn't DM them.)*"))

    @commands.hybrid_command(name="ban", description="Ban a member or any user by ID")
    @_mod_perms(ban_members=True)
    @app_commands.describe(user="Who to ban (a member, or paste a user ID)", reason="Why")
    async def ban(self, ctx: commands.Context, user: discord.User, *, reason: Optional[str] = None):
        member = ctx.guild.get_member(user.id)
        if member is not None:
            error = self._hierarchy(ctx, member)
        else:
            error = "You can't do that to yourself." if user.id == ctx.author.id else None
        if error:
            return await say(ctx, error, ephemeral=True)
        reason = reason or NO_REASON
        dmed = await self._dm(user, ctx.guild, "You were banned", reason) if member else True
        try:
            await ctx.guild.ban(user, reason=self._audit(ctx, reason), delete_message_seconds=0)
        except discord.HTTPException:
            return await say(ctx, "I couldn't ban that user.", ephemeral=True)
        await say(ctx, embed=self._done(ctx, f"🔨 **{user}** was banned.\n**Reason:** {truncate(reason, 300)}",
                                        note="" if dmed else "\n*(I couldn't DM them.)*"))

    @commands.hybrid_command(name="unban", description="Unban a user (use their ID)")
    @_mod_perms(ban_members=True)
    @app_commands.describe(user="The banned user (paste their ID)", reason="Why")
    async def unban(self, ctx: commands.Context, user: discord.User, *, reason: Optional[str] = None):
        try:
            await ctx.guild.unban(user, reason=self._audit(ctx, reason or NO_REASON))
        except discord.NotFound:
            return await say(ctx, "That user isn't banned.", ephemeral=True)
        except discord.HTTPException:
            return await say(ctx, "I couldn't unban that user.", ephemeral=True)
        await say(ctx, embed=self._done(ctx, f"✅ **{user}** was unbanned."))

    # ---- timeout / mute ----------------------------------------------------------
    async def _timeout(self, ctx, member, duration, reason):
        seconds = parse_duration(duration)
        if seconds is None:
            return await say(ctx, DURATION_HELP, ephemeral=True)
        if seconds > MAX_TIMEOUT_SECONDS:
            return await say(ctx, "Discord only allows timeouts up to **28 days**.", ephemeral=True)
        error = self._hierarchy(ctx, member)
        if error:
            return await say(ctx, error, ephemeral=True)
        if member.guild_permissions.administrator:
            return await say(ctx, "Discord doesn't allow timing out an Administrator.", ephemeral=True)
        reason = reason or NO_REASON
        try:
            await member.timeout(timedelta(seconds=seconds), reason=self._audit(ctx, reason))
        except discord.HTTPException:
            return await say(ctx, "I couldn't time that member out.", ephemeral=True)
        until = discord.utils.utcnow() + timedelta(seconds=seconds)
        dmed = await self._dm(member, ctx.guild, "You were timed out", reason,
                              extra=f"**Ends:** {discord.utils.format_dt(until, 'R')}")
        await say(ctx, embed=self._done(
            ctx, f"🔇 **{member}** is timed out for **{format_seconds(seconds)}**.\n**Reason:** {truncate(reason, 300)}",
            note="" if dmed else "\n*(I couldn't DM them.)*"))

    async def _untimeout(self, ctx, member, reason):
        if member.timed_out_until is None:
            return await say(ctx, "That member isn't timed out.", ephemeral=True)
        try:
            await member.timeout(None, reason=self._audit(ctx, reason or NO_REASON))
        except discord.HTTPException:
            return await say(ctx, "I couldn't remove that timeout.", ephemeral=True)
        await say(ctx, embed=self._done(ctx, f"🔊 **{member}** can talk again."))

    @commands.hybrid_command(name="timeout", aliases=["to"], description="Time a member out (they can't chat or talk)")
    @_mod_perms(moderate_members=True)
    @app_commands.describe(member="Who to time out", duration="e.g. 10m, 2h, 1d, 1h30m (max 28 days)", reason="Why")
    async def timeout(self, ctx: commands.Context, member: discord.Member, duration: str, *,
                      reason: Optional[str] = None):
        await self._timeout(ctx, member, duration, reason)

    @commands.hybrid_command(name="mute", description="Mute a member for a while (same as /timeout)")
    @_mod_perms(moderate_members=True)
    @app_commands.describe(member="Who to mute", duration="e.g. 10m, 2h, 1d, 1h30m (max 28 days)", reason="Why")
    async def mute(self, ctx: commands.Context, member: discord.Member, duration: str, *,
                   reason: Optional[str] = None):
        await self._timeout(ctx, member, duration, reason)

    @commands.hybrid_command(name="untimeout", description="Remove a member's timeout")
    @_mod_perms(moderate_members=True)
    @app_commands.describe(member="Who to free", reason="Why")
    async def untimeout(self, ctx: commands.Context, member: discord.Member, *, reason: Optional[str] = None):
        await self._untimeout(ctx, member, reason)

    @commands.hybrid_command(name="unmute", description="Unmute a member (same as /untimeout)")
    @_mod_perms(moderate_members=True)
    @app_commands.describe(member="Who to unmute", reason="Why")
    async def unmute(self, ctx: commands.Context, member: discord.Member, *, reason: Optional[str] = None):
        await self._untimeout(ctx, member, reason)

    # ---- warnings -------------------------------------------------------------------
    @commands.hybrid_command(name="warn", description="Give a member a warning (saved, and they are DMed)")
    @commands.guild_only()
    @commands.has_permissions(moderate_members=True)
    @app_commands.describe(member="Who to warn", reason="Why")
    async def warn(self, ctx: commands.Context, member: discord.Member, *, reason: str):
        if member.bot:
            return await say(ctx, "Bots can't be warned.", ephemeral=True)
        error = self._hierarchy(ctx, member, needs_bot_role=False)
        if error:
            return await say(ctx, error, ephemeral=True)
        reason = truncate(reason, 500, "")
        warning_id = await self.bot.db.add_warning(ctx.guild.id, member.id, ctx.author.id, reason)
        total = len(await self.bot.db.get_warnings(ctx.guild.id, member.id))
        dmed = await self._dm(member, ctx.guild, "You were warned", reason, color=YELLOW,
                              extra=f"This is warning **#{total}** on your record.")
        await say(ctx, embed=self._done(
            ctx, f"⚠️ **{member}** was warned (warning `#{warning_id}`, **{total}** in total).\n"
                 f"**Reason:** {truncate(reason, 300)}", color=YELLOW,
            note="" if dmed else "\n*(I couldn't DM them.)*"))
        await self._log(ctx, "Member warned", f"{member.mention} — warning `#{warning_id}`\n**Reason:** {reason}")

    @commands.hybrid_command(name="warnings", aliases=["warns", "infractions"],
                             description="Show a member's warnings (default: yours)")
    @commands.guild_only()
    @app_commands.describe(member="Whose warnings (moderators can look up anyone)")
    async def warnings(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        member = member or ctx.author
        if member.id != ctx.author.id and not ctx.author.guild_permissions.moderate_members:
            return await say(ctx, "You can only see your own warnings.", ephemeral=True)
        rows = await self.bot.db.get_warnings(ctx.guild.id, member.id)
        if not rows:
            return await say(ctx, f"**{member}** has no warnings. ✅", ephemeral=True)
        lines = [f"`#{row['id']}` <t:{row['created_at']}:d> — {truncate(row['reason'], 150)} "
                 f"(by <@{row['moderator_id']}>)" for row in rows[:10]]
        if len(rows) > 10:
            lines.append(f"…and {len(rows) - 10} older")
        embed = make_embed(f"Warnings for {member.display_name} ({len(rows)})", "\n".join(lines), color=YELLOW)
        await say(ctx, embed=embed, ephemeral=True)

    @commands.hybrid_command(name="delwarn", aliases=["unwarn"], description="Delete one warning by its number")
    @commands.guild_only()
    @commands.has_permissions(moderate_members=True)
    @app_commands.describe(warning_id="The #number shown in /warnings")
    async def delwarn(self, ctx: commands.Context, warning_id: int):
        removed = await self.bot.db.delete_warning(ctx.guild.id, warning_id)
        await say(ctx, f"🗑️ Warning `#{warning_id}` deleted." if removed else "There's no warning with that number.",
                  ephemeral=not removed)

    @commands.hybrid_command(name="clearwarnings", aliases=["clearwarns"], description="Delete all of a member's warnings")
    @commands.guild_only()
    @commands.has_permissions(moderate_members=True)
    @app_commands.describe(member="Whose warnings to erase")
    async def clearwarnings(self, ctx: commands.Context, member: discord.Member):
        removed = await self.bot.db.clear_warnings(ctx.guild.id, member.id)
        await say(ctx, f"🗑️ Cleared **{removed}** warning(s) for **{member}**." if removed
                  else f"**{member}** has no warnings.", ephemeral=not removed)

    # ---- channel tools -----------------------------------------------------------------
    @commands.hybrid_command(name="purge", aliases=["prune"], description="Delete recent messages (1-100)")
    @_mod_perms(manage_messages=True, read_message_history=True)
    @app_commands.describe(amount="How many messages to look at (1-100)", member="Only delete this member's messages")
    async def purge(self, ctx: commands.Context, amount: commands.Range[int, 1, 100],
                    member: Optional[discord.Member] = None):
        if ctx.interaction is not None:
            await ctx.defer(ephemeral=True)
        else:
            try:
                await ctx.message.delete()           # don't count the command itself
            except discord.HTTPException:
                pass
        check = (lambda m: m.author.id == member.id) if member else (lambda m: True)  # discord.py needs a callable
        try:
            deleted = await ctx.channel.purge(limit=amount, check=check)
        except discord.HTTPException:
            return await say(ctx, "I couldn't delete those messages.", ephemeral=True)
        text = f"🧹 Deleted **{len(deleted)}** message(s)" + (f" from **{member}**." if member else ".")
        if ctx.interaction is None:
            await say(ctx, text, delete_after=5)
        else:
            await say(ctx, text, ephemeral=True)
        await self._log(ctx, "Messages purged", f"{len(deleted)} in {ctx.channel.mention}"
                        + (f" from {member.mention}" if member else ""))

    @commands.hybrid_command(name="slowmode", aliases=["slow"], description="Set a channel's slowmode (0 = off)")
    @_mod_perms(manage_channels=True)
    @app_commands.describe(seconds="Seconds between messages (0-21600)", channel="Channel (default: this one)")
    async def slowmode(self, ctx: commands.Context, seconds: commands.Range[int, 0, 21600],
                       channel: Optional[discord.TextChannel] = None):
        channel = channel or ctx.channel
        try:
            await channel.edit(slowmode_delay=seconds, reason=self._audit(ctx, "slowmode"))
        except (discord.Forbidden, discord.HTTPException):
            return await say(ctx, "I couldn't change the slowmode there.", ephemeral=True)
        await say(ctx, f"🐢 Slowmode in {channel.mention} is **{format_seconds(seconds) if seconds else 'off'}**.")

    @commands.hybrid_command(name="lock", description="Stop everyone from chatting in a channel")
    @_mod_perms(manage_channels=True)
    @app_commands.describe(channel="Channel (default: this one)", reason="Why")
    async def lock(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None, *,
                   reason: Optional[str] = None):
        channel = channel or ctx.channel
        try:
            await update_overwrite(channel, ctx.guild.default_role, send_messages=False)
        except (discord.Forbidden, discord.HTTPException):
            return await say(ctx, "I couldn't lock that channel.", ephemeral=True)
        await say(ctx, f"🔒 {channel.mention} is locked." + (f"\n**Reason:** {truncate(reason, 300)}" if reason else ""))
        await self._log(ctx, "Channel locked", f"{channel.mention}\n**Reason:** {reason or NO_REASON}")

    @commands.hybrid_command(name="unlock", description="Let everyone chat in a channel again")
    @_mod_perms(manage_channels=True)
    @app_commands.describe(channel="Channel (default: this one)")
    async def unlock(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        channel = channel or ctx.channel
        try:
            await update_overwrite(channel, ctx.guild.default_role, send_messages=None)
        except (discord.Forbidden, discord.HTTPException):
            return await say(ctx, "I couldn't unlock that channel.", ephemeral=True)
        await say(ctx, f"🔓 {channel.mention} is unlocked.")
        await self._log(ctx, "Channel unlocked", f"{channel.mention}", color=GREEN)


async def setup(bot):
    await bot.add_cog(Moderation(bot))
