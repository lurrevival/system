import logging

import discord
from discord import app_commands
from discord.ext import commands

from utils.helpers import (BLUE, GREEN, GREY, LOG_CATEGORIES, RED, YELLOW, _warn_once, admin_group,
                           check_log_channel, deliver_log, find_actor, make_embed, respond)
from utils.text import truncate

log = logging.getLogger("mg3.logs")

CATEGORY_CHOICES = [app_commands.Choice(name=f"{k} — {v}"[:100], value=k) for k, v in LOG_CATEGORIES.items()]


async def send_log(bot, guild, category, *, embed=None, content=None, file=None):
    """Deliver a log and keep per-category counters for /logs debug. Never raises."""
    cog = bot.get_cog("Logs")
    stat = cog.stats.setdefault(category, {"sent": 0, "failed": 0, "last_error": None}) if cog else None
    try:
        sent, reason = await deliver_log(bot, guild, category, embed=embed, content=content, file=file)
    except Exception as exc:
        log.exception("Unexpected error while sending a log")
        sent, reason = False, f"internal error: {exc}"
    if stat is not None:
        stat["sent" if sent else "failed"] += 1
        if not sent:
            stat["last_error"] = reason
    if not sent and reason and not reason.startswith("no log channel"):
        _warn_once(guild.id, category, reason)
    return sent


def _fmt_user(user):
    return f"{user.mention} (`{user}`, {user.id})"


class Logs(commands.Cog):
    logs = admin_group("logs", "Choose where the bot posts its logs")

    def __init__(self, bot):
        self.bot = bot
        self.seen = {}    # event name -> how many times Discord delivered it to the bot
        self.stats = {}   # log category -> {"sent", "failed", "last_error"}

    def _seen(self, event):
        self.seen[event] = self.seen.get(event, 0) + 1

    # ---- /logs --------------------------------------------------------
    @logs.command(name="setup", description="Quick start: send ALL logs to one channel and test it")
    @app_commands.describe(channel="The channel for every log (you can split categories later with /logs set)")
    async def logs_setup(self, interaction: discord.Interaction, channel: discord.TextChannel):
        found, problem = check_log_channel(interaction.guild, channel.id)
        if found is None:
            return await respond(interaction, f"I can't use that channel: {problem}.")
        settings = await self.bot.db.get_settings(interaction.guild_id)
        settings["log_channels"]["default"] = channel.id
        await self.bot.db.save_settings(interaction.guild_id)
        sent, reason = await deliver_log(self.bot, interaction.guild, "default",
                                         embed=make_embed("Logs are working ✅", "Everything will be logged here. "
                                                          "Split categories with `/logs set`, check with `/logs test`.", color=GREEN))
        note = "" if channel.permissions_for(interaction.guild.me).embed_links else (
            "\n⚠️ I don't have **Embed Links** there, so logs will be plain text. Give me that permission for nicer logs.")
        await respond(interaction, (f"✅ All logs go to {channel.mention}. A test message was posted.{note}") if sent
                      else f"Saved, but the test message failed: {reason}.")

    @logs.command(name="set", description="Send one log category to its own channel")
    @app_commands.describe(category="What to log there ('default' catches everything not set separately)",
                           channel="The log channel")
    @app_commands.choices(category=CATEGORY_CHOICES)
    async def logs_set(self, interaction: discord.Interaction, category: app_commands.Choice[str],
                       channel: discord.TextChannel):
        found, problem = check_log_channel(interaction.guild, channel.id)
        if found is None:
            return await respond(interaction, f"I can't use that channel: {problem}.")
        settings = await self.bot.db.get_settings(interaction.guild_id)
        settings["log_channels"][category.value] = channel.id
        await self.bot.db.save_settings(interaction.guild_id)
        extra = ""
        if "default" not in settings["log_channels"] and category.value != "default":
            extra = "\nOnly this category is logged so far. Use `/logs setup` (or `category:default`) to log everything."
        await respond(interaction, f"`{category.value}` logs will go to {channel.mention}.{extra}")

    @logs.command(name="clear", description="Stop logging a category")
    @app_commands.choices(category=CATEGORY_CHOICES)
    async def logs_clear(self, interaction: discord.Interaction, category: app_commands.Choice[str]):
        settings = await self.bot.db.get_settings(interaction.guild_id)
        removed = settings["log_channels"].pop(category.value, None)
        await self.bot.db.save_settings(interaction.guild_id)
        await respond(interaction, "Cleared." if removed else "That category wasn't set.")

    def _status_lines(self, guild, channels):
        lines = []
        for category in LOG_CATEGORIES:
            target_id = channels.get(category) or (channels.get("default") if category != "default" else None)
            if not target_id:
                lines.append(f"❌ `{category}` — not logged (no channel and no default)")
                continue
            channel, problem = check_log_channel(guild, target_id)
            via = "" if channels.get(category) else " (default)"
            if channel is None:
                lines.append(f"❌ `{category}` → {problem}")
            elif not channel.permissions_for(guild.me).embed_links:
                lines.append(f"⚠️ `{category}` → {channel.mention}{via} — plain text only (no **Embed Links**)")
            else:
                lines.append(f"✅ `{category}` → {channel.mention}{via}")
        return lines

    @logs.command(name="show", description="Show where each log category goes and whether it can work")
    async def logs_show(self, interaction: discord.Interaction):
        channels = (await self.bot.db.get_settings(interaction.guild_id))["log_channels"]
        if not channels:
            return await respond(interaction, "Nothing is set yet. Start with `/logs setup channel:#logs`.")
        lines = self._status_lines(interaction.guild, channels)
        if not interaction.guild.me.guild_permissions.view_audit_log:
            lines.append("\nℹ️ I don't have **View Audit Log**, so ban/kick logs won't say who did it.")
        await respond(interaction, embed=make_embed("Log channels", "\n".join(lines)))

    @logs.command(name="debug", description="Show what the bot has seen and sent since it started")
    async def logs_debug(self, interaction: discord.Interaction):
        guild = interaction.guild
        channels = (await self.bot.db.get_settings(guild.id))["log_channels"]
        lines = [f"**Server Members intent:** {'on' if self.bot.intents.members else '❌ OFF'}",
                 f"**Log channels set:** {len(channels)}"]
        lines.append("\n**Events Discord delivered to me since the last restart:**")
        lines += [f"`{k}`: {v}" for k, v in sorted(self.seen.items())] or ["none yet — if you just did something and this is empty, "
                                                                           "Discord isn't sending events (check intents/invite)"]
        lines.append("\n**Logs per category:**")
        for category, st in sorted(self.stats.items()):
            tail = f" — last problem: {st['last_error']}" if st["last_error"] else ""
            lines.append(f"`{category}`: {st['sent']} sent, {st['failed']} failed{tail}")
        if not self.stats:
            lines.append("none attempted yet")
        await respond(interaction, embed=make_embed("Logs debug", "\n".join(lines)))

    @logs.command(name="test", description="Send a test log for every category and report what fails")
    async def logs_test(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        channels = (await self.bot.db.get_settings(guild.id))["log_channels"]
        if not channels:
            return await respond(interaction, "No log channel is set. Run `/logs setup channel:#logs` first.")
        lines = []
        for category in LOG_CATEGORIES:
            if category == "default" and "default" not in channels:
                continue
            sent, reason = await deliver_log(self.bot, guild, category, embed=make_embed(
                f"Log test: {category}", "If you can read this, this category works.", color=BLUE))
            lines.append(f"✅ `{category}` delivered" if sent else f"❌ `{category}` — {reason}")
        intents = self.bot.intents
        if not intents.members:
            lines.append("❌ The Server Members intent is off: join/leave logs can't work.")
        await respond(interaction, "\n".join(lines))

    async def _log_channel_ids(self, guild_id):
        return set((await self.bot.db.get_settings(guild_id))["log_channels"].values())

    # ---- messages -----------------------------------------------------
    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        self._seen("message_delete")
        if payload.guild_id is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None or payload.channel_id in await self._log_channel_ids(guild.id):
            return
        msg = payload.cached_message
        if msg is not None and msg.author.bot:
            return
        embed = make_embed("Message deleted", color=RED)
        embed.add_field(name="Channel", value=f"<#{payload.channel_id}>")
        if msg is not None:
            embed.add_field(name="Author", value=_fmt_user(msg.author))
            text = msg.content or "*no text*"
            if msg.attachments:
                text += "\n" + "\n".join(a.filename for a in msg.attachments)
            embed.add_field(name="Content", value=truncate(text, 1000), inline=False)
        else:
            embed.description = "The message wasn't in the bot's cache, so the content is unknown."
        await send_log(self.bot, guild, "messages", embed=embed)

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent):
        self._seen("message_bulk_delete")
        guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
        if guild is None or payload.channel_id in await self._log_channel_ids(guild.id):
            return
        embed = make_embed("Bulk delete", f"**{len(payload.message_ids)}** messages deleted in <#{payload.channel_id}>", color=RED)
        await send_log(self.bot, guild, "messages", embed=embed)

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent):
        self._seen("message_edit")
        if payload.guild_id is None:
            return
        data = payload.data or {}
        if not data.get("edited_timestamp"):
            return  # not a real edit (e.g. a link preview appeared)
        if (data.get("author") or {}).get("bot"):
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None or payload.channel_id in await self._log_channel_ids(guild.id):
            return
        before = payload.cached_message
        if before is not None and before.author.bot:
            return
        after_text = data.get("content")
        if after_text is None or (before is not None and before.content == after_text):
            return
        author = (data.get("author") or {}).get("id")
        embed = make_embed("Message edited", color=YELLOW)
        embed.add_field(name="Author", value=_fmt_user(before.author) if before is not None else (f"<@{author}>" if author else "unknown"))
        embed.add_field(name="Channel", value=f"<#{payload.channel_id}> • [jump](https://discord.com/channels/{payload.guild_id}/{payload.channel_id}/{payload.message_id})")
        embed.add_field(name="Before", value=truncate(before.content or "*empty*", 1000) if before is not None
                        else "*unknown (sent before the bot last started)*", inline=False)
        embed.add_field(name="After", value=truncate(after_text or "*empty*", 1000), inline=False)
        await send_log(self.bot, guild, "messages", embed=embed)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        self._seen("member_join")
        embed = make_embed("Member joined", _fmt_user(member), color=GREEN)
        embed.add_field(name="Account created", value=discord.utils.format_dt(member.created_at, "R"))
        embed.add_field(name="Members now", value=str(member.guild.member_count))
        embed.set_thumbnail(url=member.display_avatar.url)
        await send_log(self.bot, member.guild, "members", embed=embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        self._seen("member_remove")
        kicked = await find_actor(member.guild, discord.AuditLogAction.kick, member.id)
        if kicked:
            embed = make_embed("Member kicked", _fmt_user(member), color=RED)
            embed.add_field(name="By", value=_fmt_user(kicked[0]))
            if kicked[1]:
                embed.add_field(name="Reason", value=truncate(kicked[1], 500), inline=False)
            await send_log(self.bot, member.guild, "moderation", embed=embed)
        embed = make_embed("Member left", _fmt_user(member), color=RED)
        if member.joined_at:
            embed.add_field(name="Joined", value=discord.utils.format_dt(member.joined_at, "R"))
        roles = [r.mention for r in member.roles if not r.is_default()]
        if roles:
            embed.add_field(name="Roles", value=truncate(" ".join(roles), 1000), inline=False)
        await send_log(self.bot, member.guild, "members", embed=embed)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        self._seen("member_update")
        guild = after.guild
        if before.nick != after.nick:
            embed = make_embed("Nickname changed", _fmt_user(after), color=BLUE)
            embed.add_field(name="Before", value=before.nick or "*none*")
            embed.add_field(name="After", value=after.nick or "*none*")
            await send_log(self.bot, guild, "members", embed=embed)
        added = [r for r in after.roles if r not in before.roles]
        removed = [r for r in before.roles if r not in after.roles]
        if added or removed:
            embed = make_embed("Roles changed", _fmt_user(after), color=BLUE)
            if added:
                embed.add_field(name="Added", value=" ".join(r.mention for r in added))
            if removed:
                embed.add_field(name="Removed", value=" ".join(r.mention for r in removed))
            await send_log(self.bot, guild, "members", embed=embed)
        if before.timed_out_until != after.timed_out_until:
            if after.timed_out_until:
                embed = make_embed("Member timed out", _fmt_user(after), color=YELLOW)
                embed.add_field(name="Until", value=discord.utils.format_dt(after.timed_out_until, "R"))
            else:
                embed = make_embed("Timeout removed", _fmt_user(after), color=GREEN)
            await send_log(self.bot, guild, "moderation", embed=embed)

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        self._seen("member_ban")
        embed = make_embed("Member banned", _fmt_user(user), color=RED)
        actor = await find_actor(guild, discord.AuditLogAction.ban, user.id)
        if actor:
            embed.add_field(name="By", value=_fmt_user(actor[0]))
            if actor[1]:
                embed.add_field(name="Reason", value=truncate(actor[1], 500), inline=False)
        await send_log(self.bot, guild, "moderation", embed=embed)

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        self._seen("member_unban")
        embed = make_embed("Member unbanned", _fmt_user(user), color=GREEN)
        actor = await find_actor(guild, discord.AuditLogAction.unban, user.id)
        if actor:
            embed.add_field(name="By", value=_fmt_user(actor[0]))
        await send_log(self.bot, guild, "moderation", embed=embed)

    # ---- voice --------------------------------------------------------
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        self._seen("voice_state")
        if before.channel == after.channel or member.bot:
            return
        if before.channel is None:
            embed = make_embed("Joined voice", f"{member.mention} joined {after.channel.mention}", color=GREEN)
        elif after.channel is None:
            embed = make_embed("Left voice", f"{member.mention} left {before.channel.mention}", color=RED)
        else:
            embed = make_embed("Moved voice", f"{member.mention}: {before.channel.mention} → {after.channel.mention}", color=BLUE)
        await send_log(self.bot, member.guild, "voice", embed=embed)

    # ---- server -------------------------------------------------------
    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        self._seen("channel_create")
        embed = make_embed("Channel created", f"{channel.mention} (`{channel.name}`)", color=GREEN)
        await send_log(self.bot, channel.guild, "server", embed=embed)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        self._seen("channel_delete")
        embed = make_embed("Channel deleted", f"`#{channel.name}`", color=RED)
        await send_log(self.bot, channel.guild, "server", embed=embed)

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        self._seen("role_create")
        await send_log(self.bot, role.guild, "server", embed=make_embed("Role created", role.mention, color=GREEN))

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        self._seen("role_delete")
        await send_log(self.bot, role.guild, "server", embed=make_embed("Role deleted", f"`{role.name}`", color=RED))


async def setup(bot):
    await bot.add_cog(Logs(bot))
