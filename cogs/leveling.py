import logging
import random
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils.helpers import GREEN, admin_group, can_manage_role, make_embed, respond, say
from utils.leveling_math import level_from_xp, total_xp_for_level
from utils.text import progress_bar

log = logging.getLogger("mg3.leveling")

PAGE_SIZE = 10


class Leveling(commands.Cog):
    levels = admin_group("levels", "Leveling settings")

    def __init__(self, bot):
        self.bot = bot
        self._last_xp = {}

    # ---- earning XP ---------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot or message.webhook_id is not None:
            return
        if not message.content and not message.attachments:
            return
        member = message.author
        if not isinstance(member, discord.Member):
            return
        cfg = (await self.bot.db.get_settings(message.guild.id))["leveling"]
        if not cfg["enabled"] or message.channel.id in cfg["ignored_channels"]:
            return
        key = (message.guild.id, member.id)
        now = time.monotonic()
        if now - self._last_xp.get(key, -1e9) < cfg["cooldown"]:
            return
        self._last_xp[key] = now
        low, high = sorted((cfg["xp_min"], cfg["xp_max"]))
        old_xp, new_xp = await self.bot.db.add_xp(message.guild.id, member.id, random.randint(low, high))
        old_level, new_level = level_from_xp(old_xp)[0], level_from_xp(new_xp)[0]
        if new_level <= old_level:
            return
        await self._apply_roles(member, new_level, cfg)
        if cfg["announce"]:
            channel = message.guild.get_channel(cfg["channel_id"]) if cfg["channel_id"] else message.channel
            if channel is None:
                channel = message.channel
            try:
                await channel.send(f"🎉 {member.mention} reached **level {new_level}**!",
                                   allowed_mentions=discord.AllowedMentions(users=[member]))
            except (discord.Forbidden, discord.HTTPException):
                pass

    async def _apply_roles(self, member, level, cfg):
        to_add = []
        for level_key, role_id in cfg["roles"].items():
            try:
                needed = int(level_key)
            except ValueError:
                continue
            role = member.guild.get_role(role_id)
            if needed <= level and role is not None and role not in member.roles and can_manage_role(member.guild, role):
                to_add.append(role)
        if to_add:
            try:
                await member.add_roles(*to_add, reason="Level reward")
            except (discord.Forbidden, discord.HTTPException):
                log.warning("Could not give level roles in %s", member.guild.id)

    # ---- user commands ------------------------------------------------
    @commands.hybrid_command(name="rank", aliases=["level", "r"], description="Show your level and XP (or someone else's)")
    @commands.guild_only()
    @app_commands.describe(member="Whose rank to show (default: you)")
    async def rank(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        member = member or ctx.author
        if member.bot:
            return await say(ctx, "Bots don't earn XP.")
        row = await self.bot.db.get_level_row(ctx.guild.id, member.id)
        level, into, needed = level_from_xp(row["xp"])
        embed = make_embed(f"{member.display_name}'s rank", color=GREEN)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Level", value=str(level))
        embed.add_field(name="Rank", value=f"#{row['rank']}" if row["rank"] else "unranked")
        embed.add_field(name="Messages", value=str(row["messages"]))
        embed.add_field(name=f"Progress to level {level + 1}",
                        value=f"{progress_bar(into / needed)}\n{into:,} / {needed:,} XP • {row['xp']:,} total",
                        inline=False)
        await say(ctx, embed=embed)

    @commands.hybrid_command(name="leaderboard", aliases=["lb", "top"], description="Top members by XP")
    @commands.guild_only()
    @app_commands.describe(page="Page number")
    async def leaderboard(self, ctx: commands.Context, page: commands.Range[int, 1, 1000] = 1):
        total = await self.bot.db.count_ranked(ctx.guild.id)
        pages = max(1, -(-total // PAGE_SIZE))
        page = min(page, pages)
        rows = await self.bot.db.leaderboard(ctx.guild.id, PAGE_SIZE, (page - 1) * PAGE_SIZE)
        if not rows:
            return await say(ctx, "Nobody has earned XP yet. Start chatting!")
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = []
        for offset, row in enumerate(rows):
            position = (page - 1) * PAGE_SIZE + offset + 1
            lines.append(f"{medals.get(position, f'`#{position}`')} <@{row['user_id']}> — level "
                         f"**{level_from_xp(row['xp'])[0]}** • {row['xp']:,} XP")
        embed = make_embed("Leaderboard", "\n".join(lines))
        embed.set_footer(text=f"Page {page}/{pages}")
        await say(ctx, embed=embed, allowed_mentions=discord.AllowedMentions.none())

    # ---- admin --------------------------------------------------------
    @levels.command(name="toggle", description="Turn leveling on or off")
    async def levels_toggle(self, interaction: discord.Interaction, enabled: bool):
        cfg = (await self.bot.db.get_settings(interaction.guild_id))["leveling"]
        cfg["enabled"] = enabled
        await self.bot.db.save_settings(interaction.guild_id)
        await respond(interaction, f"Leveling is **{'on' if enabled else 'off'}**.")

    @levels.command(name="announce", description="Choose where level-up messages go")
    @app_commands.describe(announce="Post level-up messages", channel="Fixed channel (empty = where they chatted)")
    async def levels_announce(self, interaction: discord.Interaction, announce: bool,
                              channel: Optional[discord.TextChannel] = None):
        cfg = (await self.bot.db.get_settings(interaction.guild_id))["leveling"]
        cfg["announce"] = announce
        cfg["channel_id"] = channel.id if channel else None
        await self.bot.db.save_settings(interaction.guild_id)
        await respond(interaction, "Saved.")

    @levels.command(name="tune", description="Change XP per message and the cooldown")
    async def levels_tune(self, interaction: discord.Interaction,
                          xp_min: Optional[app_commands.Range[int, 1, 500]] = None,
                          xp_max: Optional[app_commands.Range[int, 1, 500]] = None,
                          cooldown: Optional[app_commands.Range[int, 0, 3600]] = None):
        cfg = (await self.bot.db.get_settings(interaction.guild_id))["leveling"]
        if xp_min is not None:
            cfg["xp_min"] = xp_min
        if xp_max is not None:
            cfg["xp_max"] = xp_max
        if cooldown is not None:
            cfg["cooldown"] = cooldown
        await self.bot.db.save_settings(interaction.guild_id)
        await respond(interaction, f"XP per message: {cfg['xp_min']}–{cfg['xp_max']} • cooldown {cfg['cooldown']} s")

    @levels.command(name="role_add", description="Give a role when members reach a level")
    async def levels_role_add(self, interaction: discord.Interaction, level: app_commands.Range[int, 1, 500],
                              role: discord.Role):
        if role.is_default() or role.managed or role >= interaction.guild.me.top_role:
            return await respond(interaction, "I can't hand out that role (it must be below my highest role).")
        if role.permissions.administrator:
            return await respond(interaction, "Level roles with **Administrator** are blocked for safety.")
        cfg = (await self.bot.db.get_settings(interaction.guild_id))["leveling"]
        cfg["roles"][str(level)] = role.id
        await self.bot.db.save_settings(interaction.guild_id)
        await respond(interaction, f"Members reaching level **{level}** get {role.mention}.",
                      allowed_mentions=discord.AllowedMentions.none())

    @levels.command(name="role_remove", description="Remove a level reward")
    async def levels_role_remove(self, interaction: discord.Interaction, level: app_commands.Range[int, 1, 500]):
        cfg = (await self.bot.db.get_settings(interaction.guild_id))["leveling"]
        if cfg["roles"].pop(str(level), None) is None:
            return await respond(interaction, "No reward is set for that level.")
        await self.bot.db.save_settings(interaction.guild_id)
        await respond(interaction, "Removed.")

    @levels.command(name="role_list", description="List level rewards")
    async def levels_role_list(self, interaction: discord.Interaction):
        roles = (await self.bot.db.get_settings(interaction.guild_id))["leveling"]["roles"]
        lines = [f"Level **{k}** → <@&{v}>" for k, v in sorted(roles.items(), key=lambda kv: int(kv[0]))]
        await respond(interaction, "\n".join(lines) or "No level rewards set.",
                      allowed_mentions=discord.AllowedMentions.none())

    @levels.command(name="set_xp", description="Set a member's total XP")
    async def levels_set_xp(self, interaction: discord.Interaction, member: discord.Member,
                            xp: app_commands.Range[int, 0, 100000000]):
        await self.bot.db.set_xp(interaction.guild_id, member.id, xp)
        await respond(interaction, f"{member.mention} now has **{xp:,}** XP (level {level_from_xp(xp)[0]}).",
                      allowed_mentions=discord.AllowedMentions.none())

    @levels.command(name="set_level", description="Set a member's level")
    async def levels_set_level(self, interaction: discord.Interaction, member: discord.Member,
                               level: app_commands.Range[int, 0, 500]):
        await self.bot.db.set_xp(interaction.guild_id, member.id, total_xp_for_level(level))
        await respond(interaction, f"{member.mention} is now level **{level}**.",
                      allowed_mentions=discord.AllowedMentions.none())

    @levels.command(name="reset", description="Erase a member's XP")
    async def levels_reset(self, interaction: discord.Interaction, member: discord.Member):
        await self.bot.db.reset_user(interaction.guild_id, member.id)
        await respond(interaction, f"Reset {member.mention}.", allowed_mentions=discord.AllowedMentions.none())

    @levels.command(name="ignore_channel", description="Stop (or resume) earning XP in a channel")
    async def levels_ignore_channel(self, interaction: discord.Interaction, channel: discord.TextChannel,
                                    ignore: bool = True):
        cfg = (await self.bot.db.get_settings(interaction.guild_id))["leveling"]
        ids = cfg["ignored_channels"]
        if ignore and channel.id not in ids:
            ids.append(channel.id)
        elif not ignore and channel.id in ids:
            ids.remove(channel.id)
        await self.bot.db.save_settings(interaction.guild_id)
        await respond(interaction, f"{channel.mention}: XP {'ignored' if ignore else 'counts again'}.")


async def setup(bot):
    await bot.add_cog(Leveling(bot))
