import logging
import time
from datetime import timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils.automod import (SpamTracker, blocked_domains, caps_percent, find_badword, has_invite,
                           normalize_domain)
from utils.helpers import RED, YELLOW, admin_group, make_embed, respond, send_log
from utils.text import truncate

log = logging.getLogger("mg3.security")

STRIKE_LIMIT = 3          # rule violations...
STRIKE_WINDOW = 600       # ...within this many seconds earn a timeout
RAID_MODE_SECONDS = 120

TOGGLES = {
    "master": "Everything (master switch)",
    "spam": "Message spam",
    "mentions": "Mass mentions",
    "invites": "Discord invite links",
    "links": "All links except allowed domains",
    "caps": "Excessive capital letters",
    "badwords": "Bad-word list",
    "raid": "Raid detection (alerts)",
    "raid_kick": "Kick new joiners during a raid",
}
TOGGLE_CHOICES = [app_commands.Choice(name=f"{k} — {v}"[:100], value=k) for k, v in TOGGLES.items()]

# option -> (section or None, key, min, max)
OPTIONS = {
    "spam_limit": ("spam", "limit", 2, 20),
    "spam_window": ("spam", "window", 2, 60),
    "mention_limit": ("mentions", "limit", 2, 50),
    "caps_percent": ("caps", "percent", 30, 100),
    "punish_minutes": (None, "punish_minutes", 0, 40320),
    "min_account_age_days": (None, "min_account_age_days", 0, 365),
    "raid_joins": ("raid", "joins", 3, 50),
    "raid_seconds": ("raid", "seconds", 3, 120),
}
OPTION_CHOICES = [app_commands.Choice(name=k, value=k) for k in OPTIONS]


class Security(commands.Cog):
    automod = admin_group("automod", "AutoMod and server security")

    def __init__(self, bot):
        self.bot = bot
        self.spam = SpamTracker()
        self.strikes = SpamTracker()
        self.joins = SpamTracker()
        self.raid_until = {}
        self.last_raid_alert = {}

    # ---- message scanning ----------------------------------------------
    def evaluate(self, message, text, sec):
        """Return (rule, reason, messages_to_delete) for the first violation, else None."""
        now = time.monotonic()
        recent = []
        if sec["spam"]["enabled"]:
            recent = self.spam.record((message.guild.id, message.author.id), now, message, sec["spam"]["window"])
        if sec["invites"]["enabled"] and has_invite(text):
            return "invites", "Invite links aren't allowed here.", [message]
        if sec["links"]["enabled"] and blocked_domains(text, sec["links"]["whitelist"]):
            return "links", "Links aren't allowed here.", [message]
        if sec["badwords"]["enabled"] and find_badword(text, sec["badwords"]["words"]):
            return "badwords", "Please watch your language.", [message]
        if sec["mentions"]["enabled"]:
            total = len({m.id for m in message.mentions}) + len(message.role_mentions)
            if total >= sec["mentions"]["limit"]:
                return "mentions", "Too many mentions in one message.", [message]
        if sec["caps"]["enabled"] and caps_percent(text, sec["caps"]["min_length"]) >= sec["caps"]["percent"]:
            return "caps", "Please don't shout (too many capital letters).", [message]
        if sec["spam"]["enabled"] and len(recent) >= sec["spam"]["limit"]:
            self.spam.clear((message.guild.id, message.author.id))
            return "spam", "Slow down — you're sending messages too fast.", recent
        return None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot or message.webhook_id is not None:
            return
        member = message.author
        if not isinstance(member, discord.Member):
            return
        sec = (await self.bot.db.get_settings(message.guild.id))["security"]
        if not sec["enabled"]:
            return
        perms = member.guild_permissions
        if perms.manage_messages or perms.administrator:
            return
        if message.channel.id in sec["exempt_channels"] or any(r.id in sec["exempt_roles"] for r in member.roles):
            return
        verdict = self.evaluate(message, message.content or "", sec)
        if verdict is None:
            return
        rule, reason, to_delete = verdict
        await self._punish(message, member, sec, rule, reason, to_delete)

    async def _punish(self, message, member, sec, rule, reason, to_delete):
        guild = message.guild
        content = message.content or ""
        for target in {m.id: m for m in to_delete + [message]}.values():
            try:
                await target.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
        try:
            await message.channel.send(f"{member.mention} {reason}", delete_after=6,
                                       allowed_mentions=discord.AllowedMentions(users=[member]))
        except (discord.Forbidden, discord.HTTPException):
            pass

        timed_out = False
        strikes = self.strikes.record((guild.id, member.id), time.monotonic(), rule, STRIKE_WINDOW)
        if sec["punish_minutes"] > 0 and (rule == "spam" or len(strikes) >= STRIKE_LIMIT):
            self.strikes.clear((guild.id, member.id))
            timed_out = await self._timeout(member, sec["punish_minutes"], f"AutoMod: {rule}")

        embed = make_embed(f"AutoMod: {rule}", reason, color=YELLOW)
        embed.add_field(name="User", value=f"{member.mention} (`{member}`, {member.id})")
        embed.add_field(name="Channel", value=message.channel.mention)
        if content:
            embed.add_field(name="Message", value=truncate(content, 700), inline=False)
        if timed_out:
            embed.add_field(name="Action", value=f"Timed out for {sec['punish_minutes']} min")
        await send_log(self.bot, guild, "moderation", embed=embed)

    async def _timeout(self, member, minutes, reason):
        guild = member.guild
        me = guild.me
        if member == guild.owner or member.top_role >= me.top_role or not me.guild_permissions.moderate_members:
            return False
        try:
            await member.timeout(timedelta(minutes=minutes), reason=reason)
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

    # ---- join screening (called by the Welcome cog) --------------------
    async def screen_member(self, member: discord.Member) -> bool:
        """Return True if the member was removed (account too new, or raid protection)."""
        guild = member.guild
        sec = (await self.bot.db.get_settings(guild.id))["security"]
        if not sec["enabled"] or member.bot:
            return False
        now = time.monotonic()

        min_age = sec["min_account_age_days"]
        if min_age > 0:
            age_days = (discord.utils.utcnow() - member.created_at).days
            if age_days < min_age:
                return await self._remove(member, f"AutoMod: account younger than {min_age} day(s)",
                                          f"Your account is too new to join **{guild.name}** (minimum {min_age} days).")

        raid = sec["raid"]
        if raid["enabled"]:
            joined = self.joins.record(guild.id, now, member.id, raid["seconds"])
            if len(joined) >= raid["joins"]:
                self.raid_until[guild.id] = now + RAID_MODE_SECONDS
                if now - self.last_raid_alert.get(guild.id, -999) > 60:
                    self.last_raid_alert[guild.id] = now
                    embed = make_embed("Possible raid detected",
                                       f"{len(joined)} members joined within {raid['seconds']} seconds.", color=RED)
                    if raid["kick"]:
                        embed.add_field(name="Action", value=f"Kicking new joiners for the next {RAID_MODE_SECONDS} s")
                    await send_log(self.bot, guild, "moderation", embed=embed)
            if raid["kick"] and now < self.raid_until.get(guild.id, 0):
                return await self._remove(member, "AutoMod: raid protection",
                                          f"**{guild.name}** is temporarily not accepting new members. Please try again later.")
        return False

    async def _remove(self, member, reason, dm_text):
        try:
            await member.send(dm_text)
        except (discord.Forbidden, discord.HTTPException):
            pass
        try:
            await member.kick(reason=reason)
        except (discord.Forbidden, discord.HTTPException):
            return False
        embed = make_embed("AutoMod removed a member", reason, color=RED)
        embed.add_field(name="User", value=f"{member.mention} (`{member}`, {member.id})")
        await send_log(self.bot, member.guild, "moderation", embed=embed)
        return True

    # ---- /automod -----------------------------------------------------
    @automod.command(name="status", description="Show every AutoMod setting")
    async def automod_status(self, interaction: discord.Interaction):
        sec = (await self.bot.db.get_settings(interaction.guild_id))["security"]
        onoff = lambda v: "✅" if v else "❌"
        lines = [
            f"{onoff(sec['enabled'])} **AutoMod master switch**",
            f"{onoff(sec['spam']['enabled'])} Spam: {sec['spam']['limit']} messages / {sec['spam']['window']} s",
            f"{onoff(sec['mentions']['enabled'])} Mentions: max {sec['mentions']['limit']} per message",
            f"{onoff(sec['invites']['enabled'])} Invite links",
            f"{onoff(sec['links']['enabled'])} Links (allowed: {', '.join(sec['links']['whitelist']) or 'none'})",
            f"{onoff(sec['caps']['enabled'])} Caps: {sec['caps']['percent']}% (min {sec['caps']['min_length']} letters)",
            f"{onoff(sec['badwords']['enabled'])} Bad words: {len(sec['badwords']['words'])} in list",
            f"{onoff(sec['raid']['enabled'])} Raid alert: {sec['raid']['joins']} joins / {sec['raid']['seconds']} s"
            f" • kick during raid: {onoff(sec['raid']['kick'])}",
            f"Timeout length: {sec['punish_minutes']} min • Minimum account age: {sec['min_account_age_days']} day(s)",
            f"Exempt roles: {' '.join(f'<@&{i}>' for i in sec['exempt_roles']) or 'none'}",
            f"Exempt channels: {' '.join(f'<#{i}>' for i in sec['exempt_channels']) or 'none'}",
            "Members with **Manage Messages** or **Administrator** are always exempt.",
        ]
        await respond(interaction, embed=make_embed("AutoMod settings", "\n".join(lines)),
                      allowed_mentions=discord.AllowedMentions.none())

    @automod.command(name="toggle", description="Turn an AutoMod rule on or off")
    @app_commands.choices(rule=TOGGLE_CHOICES)
    async def automod_toggle(self, interaction: discord.Interaction, rule: app_commands.Choice[str], enabled: bool):
        sec = (await self.bot.db.get_settings(interaction.guild_id))["security"]
        if rule.value == "master":
            sec["enabled"] = enabled
        elif rule.value == "raid_kick":
            sec["raid"]["kick"] = enabled
        else:
            sec[rule.value]["enabled"] = enabled
        await self.bot.db.save_settings(interaction.guild_id)
        await respond(interaction, f"`{rule.value}` is now **{'on' if enabled else 'off'}**.")

    @automod.command(name="set", description="Change a number such as the spam limit")
    @app_commands.choices(option=OPTION_CHOICES)
    async def automod_set(self, interaction: discord.Interaction, option: app_commands.Choice[str], value: int):
        section, key, low, high = OPTIONS[option.value]
        if not low <= value <= high:
            return await respond(interaction, f"`{option.value}` must be between {low} and {high}.")
        sec = (await self.bot.db.get_settings(interaction.guild_id))["security"]
        (sec[section] if section else sec)[key] = value
        await self.bot.db.save_settings(interaction.guild_id)
        await respond(interaction, f"`{option.value}` set to **{value}**.")

    @automod.command(name="badword_add", description="Add a word to the bad-word list")
    async def automod_badword_add(self, interaction: discord.Interaction, word: str):
        word = word.strip().lower()
        if not word or len(word) > 50:
            return await respond(interaction, "Give a word between 1 and 50 characters.")
        sec = (await self.bot.db.get_settings(interaction.guild_id))["security"]
        if word not in sec["badwords"]["words"]:
            sec["badwords"]["words"].append(word)
            await self.bot.db.save_settings(interaction.guild_id)
        await respond(interaction, "Added. Remember to turn the rule on: `/automod toggle rule:badwords enabled:True`.")

    @automod.command(name="badword_remove", description="Remove a word from the bad-word list")
    async def automod_badword_remove(self, interaction: discord.Interaction, word: str):
        sec = (await self.bot.db.get_settings(interaction.guild_id))["security"]
        word = word.strip().lower()
        if word in sec["badwords"]["words"]:
            sec["badwords"]["words"].remove(word)
            await self.bot.db.save_settings(interaction.guild_id)
            return await respond(interaction, "Removed.")
        await respond(interaction, "That word isn't in the list.")

    @automod.command(name="badword_list", description="Show the bad-word list (only you can see it)")
    async def automod_badword_list(self, interaction: discord.Interaction):
        words = (await self.bot.db.get_settings(interaction.guild_id))["security"]["badwords"]["words"]
        await respond(interaction, ", ".join(words) if words else "The list is empty.")

    @automod.command(name="link_allow", description="Allow links to a domain when the link rule is on")
    async def automod_link_allow(self, interaction: discord.Interaction, domain: str):
        domain = normalize_domain(domain)
        if "." not in domain:
            return await respond(interaction, "That doesn't look like a domain, e.g. `youtube.com`.")
        sec = (await self.bot.db.get_settings(interaction.guild_id))["security"]
        if domain not in sec["links"]["whitelist"]:
            sec["links"]["whitelist"].append(domain)
            await self.bot.db.save_settings(interaction.guild_id)
        await respond(interaction, f"Links to `{domain}` (and its subdomains) are allowed.")

    @automod.command(name="link_disallow", description="Remove a domain from the allowed list")
    async def automod_link_disallow(self, interaction: discord.Interaction, domain: str):
        domain = normalize_domain(domain)
        sec = (await self.bot.db.get_settings(interaction.guild_id))["security"]
        if domain in sec["links"]["whitelist"]:
            sec["links"]["whitelist"].remove(domain)
            await self.bot.db.save_settings(interaction.guild_id)
            return await respond(interaction, "Removed.")
        await respond(interaction, "That domain wasn't in the list.")

    @automod.command(name="exempt_role", description="Exempt (or un-exempt) a role from AutoMod")
    async def automod_exempt_role(self, interaction: discord.Interaction, role: discord.Role, exempt: bool = True):
        sec = (await self.bot.db.get_settings(interaction.guild_id))["security"]
        ids = sec["exempt_roles"]
        if exempt and role.id not in ids:
            ids.append(role.id)
        elif not exempt and role.id in ids:
            ids.remove(role.id)
        await self.bot.db.save_settings(interaction.guild_id)
        await respond(interaction, f"{role.mention} is {'now exempt' if exempt else 'no longer exempt'}.",
                      allowed_mentions=discord.AllowedMentions.none())

    @automod.command(name="exempt_channel", description="Exempt (or un-exempt) a channel from AutoMod")
    async def automod_exempt_channel(self, interaction: discord.Interaction, channel: discord.TextChannel,
                                     exempt: bool = True):
        sec = (await self.bot.db.get_settings(interaction.guild_id))["security"]
        ids = sec["exempt_channels"]
        if exempt and channel.id not in ids:
            ids.append(channel.id)
        elif not exempt and channel.id in ids:
            ids.remove(channel.id)
        await self.bot.db.save_settings(interaction.guild_id)
        await respond(interaction, f"{channel.mention} is {'now exempt' if exempt else 'no longer exempt'}.")


async def setup(bot):
    await bot.add_cog(Security(bot))
