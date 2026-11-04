import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils.helpers import GREEN, can_manage_role, admin_group, make_embed, respond
from utils.text import render_template, truncate

log = logging.getLogger("mg3.welcome")


class Welcome(commands.Cog):
    welcome = admin_group("welcome", "Welcome message settings")
    autorole = admin_group("autorole", "Roles given automatically to new members")

    def __init__(self, bot):
        self.bot = bot

    # ---- rendering ----------------------------------------------------
    def _render(self, cfg, member):
        text = render_template(
            cfg["message"],
            user=member.display_name,
            mention=member.mention,
            server=member.guild.name,
            count=member.guild.member_count,
        )
        return truncate(text, 1900)

    async def _post(self, channel, cfg, member):
        text = self._render(cfg, member)
        if cfg["embed"]:
            embed = make_embed("Welcome!", text, color=GREEN)
            embed.set_thumbnail(url=member.display_avatar.url)
            await channel.send(content=member.mention, embed=embed,
                               allowed_mentions=discord.AllowedMentions(users=[member]))
        else:
            await channel.send(text, allowed_mentions=discord.AllowedMentions(users=[member]))

    # ---- events -------------------------------------------------------
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        security = self.bot.get_cog("Security")
        if security is not None and await security.screen_member(member):
            return  # member was removed by AutoMod; do not welcome or give roles

        settings = await self.bot.db.get_settings(member.guild.id)
        ids = settings["autorole"]["bots" if member.bot else "members"]
        roles = []
        for role_id in ids:
            role = member.guild.get_role(role_id)
            if role is not None and can_manage_role(member.guild, role):
                roles.append(role)
        if roles:
            try:
                await member.add_roles(*roles, reason="Auto role")
            except (discord.Forbidden, discord.HTTPException):
                log.warning("Could not give auto roles in %s", member.guild.id)

        cfg = settings["welcome"]
        if cfg["enabled"] and cfg["channel_id"] and not member.bot:
            channel = member.guild.get_channel(cfg["channel_id"])
            if isinstance(channel, discord.TextChannel):
                perms = channel.permissions_for(member.guild.me)
                if perms.send_messages and perms.embed_links:
                    try:
                        await self._post(channel, cfg, member)
                    except discord.HTTPException:
                        log.warning("Could not send welcome message in %s", member.guild.id)

    # ---- /welcome -----------------------------------------------------
    @welcome.command(name="set", description="Turn on welcome messages")
    @app_commands.describe(
        channel="Where to post the welcome message",
        message="Use {mention} {user} {server} {count}. Leave empty to keep the current text",
        embed="Send it as an embed (default yes)",
    )
    async def welcome_set(self, interaction: discord.Interaction, channel: discord.TextChannel,
                          message: Optional[str] = None, embed: bool = True):
        perms = channel.permissions_for(interaction.guild.me)
        if not (perms.send_messages and perms.embed_links):
            return await respond(interaction, f"I need **Send Messages** and **Embed Links** in {channel.mention}.")
        settings = await self.bot.db.get_settings(interaction.guild_id)
        cfg = settings["welcome"]
        cfg.update(enabled=True, channel_id=channel.id, embed=embed)
        if message:
            cfg["message"] = truncate(message, 1500, "")
        await self.bot.db.save_settings(interaction.guild_id)
        await respond(interaction, f"Welcome messages are **on** in {channel.mention}. Use `/welcome test` to preview.")

    @welcome.command(name="off", description="Turn off welcome messages")
    async def welcome_off(self, interaction: discord.Interaction):
        settings = await self.bot.db.get_settings(interaction.guild_id)
        settings["welcome"]["enabled"] = False
        await self.bot.db.save_settings(interaction.guild_id)
        await respond(interaction, "Welcome messages are **off**.")

    @welcome.command(name="test", description="Preview the welcome message here, using you as the new member")
    async def welcome_test(self, interaction: discord.Interaction):
        settings = await self.bot.db.get_settings(interaction.guild_id)
        cfg = settings["welcome"]
        text = self._render(cfg, interaction.user)
        embed = make_embed("Welcome! (preview)", text, color=GREEN)
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        await respond(interaction, embed=embed)

    @welcome.command(name="show", description="Show the current welcome settings")
    async def welcome_show(self, interaction: discord.Interaction):
        cfg = (await self.bot.db.get_settings(interaction.guild_id))["welcome"]
        channel = f"<#{cfg['channel_id']}>" if cfg["channel_id"] else "not set"
        await respond(interaction, embed=make_embed("Welcome settings", (
            f"**Enabled:** {cfg['enabled']}\n**Channel:** {channel}\n**Embed:** {cfg['embed']}\n"
            f"**Message:**\n{cfg['message']}")))

    # ---- /autorole ----------------------------------------------------
    @autorole.command(name="add", description="Give this role to every new member")
    @app_commands.describe(role="The role to give", bots="Give it to bots instead of humans")
    async def autorole_add(self, interaction: discord.Interaction, role: discord.Role, bots: bool = False):
        if role.is_default() or role.managed:
            return await respond(interaction, "That role can't be handed out automatically.")
        if role >= interaction.guild.me.top_role:
            return await respond(interaction, "That role is above my highest role. Move my role higher in Server Settings > Roles.")
        if role.permissions.administrator:
            return await respond(interaction, "Auto roles with **Administrator** are blocked for safety.")
        settings = await self.bot.db.get_settings(interaction.guild_id)
        target = settings["autorole"]["bots" if bots else "members"]
        if role.id in target:
            return await respond(interaction, "That role is already an auto role.")
        target.append(role.id)
        await self.bot.db.save_settings(interaction.guild_id)
        await respond(interaction, f"New {'bots' if bots else 'members'} will now get {role.mention}.",
                      allowed_mentions=discord.AllowedMentions.none())

    @autorole.command(name="remove", description="Stop giving this role to new members")
    async def autorole_remove(self, interaction: discord.Interaction, role: discord.Role):
        settings = await self.bot.db.get_settings(interaction.guild_id)
        removed = False
        for key in ("members", "bots"):
            if role.id in settings["autorole"][key]:
                settings["autorole"][key].remove(role.id)
                removed = True
        await self.bot.db.save_settings(interaction.guild_id)
        await respond(interaction, "Removed." if removed else "That role wasn't an auto role.")

    @autorole.command(name="list", description="List the auto roles")
    async def autorole_list(self, interaction: discord.Interaction):
        ar = (await self.bot.db.get_settings(interaction.guild_id))["autorole"]
        fmt = lambda ids: ", ".join(f"<@&{i}>" for i in ids) or "none"
        await respond(interaction, embed=make_embed("Auto roles", f"**Members:** {fmt(ar['members'])}\n**Bots:** {fmt(ar['bots'])}"),
                      allowed_mentions=discord.AllowedMentions.none())


async def setup(bot):
    await bot.add_cog(Welcome(bot))
