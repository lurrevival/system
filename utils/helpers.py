"""Discord-specific helpers shared by the cogs."""
import asyncio
import logging

import discord
from discord import app_commands

import config
from utils.text import embed_to_text

log = logging.getLogger("mg3")

GREEN = 0x57F287
RED = 0xED4245
YELLOW = 0xFEE75C
BLUE = 0x5865F2
GREY = 0x99AAB5

LOG_CATEGORIES = {
    "default": "Everything that has no own channel",
    "messages": "Deleted and edited messages",
    "members": "Joins, leaves, nickname and role changes",
    "voice": "Voice channel activity",
    "server": "Channel and role changes",
    "moderation": "Bans, kicks, timeouts and AutoMod actions",
    "tickets": "Ticket opened/closed and transcripts",
}


def make_embed(title=None, description=None, *, color=None):
    embed = discord.Embed(
        title=title,
        description=description,
        color=color if color is not None else config.EMBED_COLOR,
    )
    embed.timestamp = discord.utils.utcnow()
    return embed


def is_manager(member):
    perms = getattr(member, "guild_permissions", None)
    return bool(perms and (perms.manage_guild or perms.administrator))


class AdminGroup(app_commands.Group):
    """Slash-command group that only members with Manage Server can use."""

    async def interaction_check(self, interaction):
        if is_manager(interaction.user):
            return True
        raise app_commands.CheckFailure("You need the **Manage Server** permission to use this command.")


def admin_group(name, description):
    return AdminGroup(
        name=name,
        description=description,
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )


async def respond(interaction, content=None, *, embed=None, ephemeral=True, **kwargs):
    """Reply to an interaction whether or not it was already answered/deferred."""
    payload = dict(kwargs)
    if content is not None:
        payload["content"] = content
    if embed is not None:
        payload["embed"] = embed
    payload["ephemeral"] = ephemeral
    if interaction.response.is_done():
        return await interaction.followup.send(**payload)
    return await interaction.response.send_message(**payload)


async def say(ctx, content=None, *, embed=None, ephemeral=False, **kwargs):
    """Reply to a hybrid command (works for both /slash and prefix invocations)."""
    payload = dict(kwargs)
    if content is not None:
        payload["content"] = content
    if embed is not None:
        payload["embed"] = embed
    if ephemeral:
        payload["ephemeral"] = True  # ignored for prefix commands
    return await ctx.send(**payload)


_warned = set()


def _warn_once(guild_id, category, reason):
    key = (guild_id, category, reason)
    if key not in _warned:
        _warned.add(key)
        log.warning("Logs not delivered (server %s, '%s'): %s", guild_id, category, reason)


def check_log_channel(guild, channel_id):
    """Return (channel, reason). `channel` is usable, otherwise `reason` says exactly what is wrong."""
    channel = guild.get_channel(int(channel_id))
    if channel is None:
        return None, f"the log channel <#{channel_id}> no longer exists (or I can't see it)"
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        return None, f"{channel.mention} isn't a text channel"
    perms = channel.permissions_for(guild.me)
    if not perms.view_channel:
        return None, f"I can't see {channel.mention} (give me **View Channel** there)"
    if not perms.send_messages:
        return None, f"I can't send messages in {channel.mention} (give me **Send Messages** there)"
    return channel, None


async def deliver_log(bot, guild, category, *, embed=None, content=None, file=None):
    """Post a log. Tries the category's channel, then the 'default' one. Returns (sent, reason)."""
    channels = (await bot.db.get_settings(guild.id))["log_channels"]
    candidates = []
    for key in (category, "default"):
        channel_id = channels.get(key)
        if channel_id and channel_id not in candidates:
            candidates.append(channel_id)
    if not candidates:
        return False, "no log channel is set (use /logs setup)"
    reason = "unknown problem"
    for channel_id in candidates:
        channel, problem = check_log_channel(guild, channel_id)
        if channel is None:
            reason = problem
            continue
        perms = channel.permissions_for(guild.me)
        kwargs = {"allowed_mentions": discord.AllowedMentions.none()}
        text = content
        if embed is not None:
            if perms.embed_links:
                kwargs["embed"] = embed
            else:  # no Embed Links there: still deliver the log, as plain text
                text = f"{content}\n{embed_to_text(embed)}" if content else embed_to_text(embed)
        if text:
            kwargs["content"] = text
        if file is not None:
            if not perms.attach_files:
                reason = f"I can't attach files in {channel.mention} (give me **Attach Files**)"
                continue
            kwargs["file"] = file
        try:
            await channel.send(**kwargs)
            return True, None
        except discord.Forbidden:
            reason = f"Discord refused my message in {channel.mention} (missing permission)"
        except discord.HTTPException as exc:
            reason = f"Discord error {getattr(exc, 'status', '?')} while posting in {channel.mention}"
    return False, reason


async def send_log(bot, guild, category, *, embed=None, content=None, file=None):
    """Post to the log channel for `category` (falls back to 'default'). Never raises; returns True if sent."""
    try:
        sent, reason = await deliver_log(bot, guild, category, embed=embed, content=content, file=file)
    except Exception:
        log.exception("Unexpected error while sending a log")
        return False
    if not sent and reason and not reason.startswith("no log channel"):
        _warn_once(guild.id, category, reason)
    return sent


async def find_actor(guild, action, target_id, within=15):
    """Best-effort: who performed `action` on `target_id` recently (needs View Audit Log)."""
    if not guild.me.guild_permissions.view_audit_log:
        return None
    await asyncio.sleep(1)  # audit log entries can lag a moment behind the event
    try:
        async for entry in guild.audit_logs(limit=8, action=action):
            target = entry.target
            if target is not None and getattr(target, "id", None) == target_id:
                age = (discord.utils.utcnow() - entry.created_at).total_seconds()
                if age <= within:
                    return entry.user, entry.reason
    except Exception:
        return None
    return None


async def update_overwrite(channel, target, **perms):
    """Change some permissions for `target` on `channel` and keep its other overrides.

    (`channel.set_permissions(target, x=True)` on its own *replaces* the whole override.)"""
    overwrite = channel.overwrites_for(target)
    overwrite.update(**perms)
    if overwrite.is_empty():
        if target in channel.overwrites:
            await channel.set_permissions(target, overwrite=None)
        return
    await channel.set_permissions(target, overwrite=overwrite)


def can_manage_role(guild, role):
    """True if the bot may hand out `role` (not @everyone, not managed, below the bot)."""
    return not role.is_default() and not role.managed and role < guild.me.top_role
