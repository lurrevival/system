"""General commands and an interactive, accurate help menu for dor."""
from typing import Literal, Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.helpers import is_manager, make_embed, respond, say
from utils.setup_pages import SetupGuideView, build_embed as build_setup_embed, gather


HELP_PAGES = {
    "overview": ("Command Center", "Choose a category below to explore the commands."),
    "voice": ("Temporary Voice Rooms", "Create a personal voice room and manage its settings."),
    "levels": ("Levels & XP", "See your progress and check the leaderboard."),
    "tickets": ("Tickets", "Get help and manage support tickets."),
    "moderation": ("Moderation", "Kick, ban, timeout, warn and clean up (staff only)."),
    "security": ("AutoMod & Logs", "Automated protection and moderation logs."),
    "afk": ("Auto-AFK", "Move members who stay self-muted or deafened into an AFK room."),
    "admin": ("Server Settings", "Configure dor and review your server's setup."),
    "owner": ("Bot Profile", "Change dor's profile and presence (bot owner only)."),
}


def _chan(guild, channel_id):
    if not channel_id:
        return "not set"
    channel = guild.get_channel(int(channel_id)) if guild else None
    return channel.mention if channel else f"missing ({channel_id})"


def _onoff(value):
    return "on" if value else "off"


def _cmd(name):
    return f"`{config.PREFIX}{name}`"


def build_help_embed(bot, guild, category="overview"):
    """Produce a short page for the requested category; only mention actual commands."""
    category = category if category in HELP_PAGES else "overview"
    title, description = HELP_PAGES[category]
    embed = discord.Embed(title=f"dor  •  {title}", description=description, color=config.EMBED_COLOR)
    icon = guild.icon if guild and guild.icon else (bot.user.display_avatar if bot.user else None)
    if icon:
        embed.set_thumbnail(url=icon.url)

    if category == "overview":
        embed.description = (
            "**Your server, one command center.**\n"
            "Pick a category using the menu below. Each page explains its commands.\n\n"
            f"**Prefix** `{config.PREFIX}`   •   **Slash commands** `/`"
        )
        for name, summary in (
            ("🔊 Voice Rooms", "Personal room controls"),
            ("⭐ Levels", "Ranks and leaderboard"),
            ("🎫 Tickets", "Member support"),
            ("🔨 Moderation", "Kick, ban, timeout, warn, purge"),
            ("🛡️ AutoMod & Logs", "Automatic server protection"),
            ("💤 Auto-AFK", "Move idle voice members"),
            ("⚙️ Server Settings", "Configuration and setup"),
            ("👑 Bot Profile", "Owner-only customization"),
        ):
            embed.add_field(name=name, value=summary, inline=True)
        embed.add_field(name="Quick start", value="Use `/help setup` for the guided server setup (Manage Server permission).", inline=False)

    elif category == "voice":
        embed.add_field(name="🔊 Getting started", value=(
            "Join the **create-room** voice channel set by an admin. A temporary room is made for you. "
            "The Arabic control panel appears automatically in the temporary room’s voice chat. Use it or these `vc` commands."
        ), inline=False)
        embed.add_field(name="🔒 Privacy", value=(
            f"{_cmd('vc lock')}  {_cmd('vc unlock')}\n"
            f"{_cmd('vc hide')}  {_cmd('vc show')}\n"
            f"{_cmd('vc block')}  {_cmd('vc allow')}"
        ), inline=False)
        embed.add_field(name="⚙️ Customize", value=(
            f"{_cmd('vc name <name>')}  {_cmd('vc limit <0-99>')}\n"
            f"{_cmd('vc invite @member')}"
        ), inline=False)
        embed.add_field(name="👥 Members & ownership", value=(
            f"{_cmd('vc kick @member')}  {_cmd('vc mute @member')}\n"
            f"{_cmd('vc unmute @member')}  {_cmd('vc transfer @member')}\n"
            f"{_cmd('vc claim')}  {_cmd('vc')}"
        ), inline=False)
        embed.add_field(name="📌 24/7 voice (Manage Server)", value=(
            f"{_cmd('247 on [channel]')} — Keep the bot in a voice channel all the time\n"
            f"{_cmd('247 off')}  {_cmd('247 status')}"
        ), inline=False)

    elif category == "levels":
        embed.add_field(name="⭐ Ranks", value=(
            f"{_cmd('rank')} — View your own rank\n"
            f"{_cmd('rank @member')} — View someone else's rank"
        ), inline=False)
        embed.add_field(name="🏆 Leaderboard", value=f"{_cmd('leaderboard')} — See the top members", inline=False)
        embed.add_field(name="⚙️ Admin settings", value=(
            "`/levels toggle`  `/levels tune`  `/levels announce`\n"
            "`/levels role_add`  `/levels role_remove`  `/levels role_list`\n"
            "`/levels set_xp`  `/levels set_level`  `/levels reset`\n"
            "`/levels ignore_channel`"
        ), inline=False)

    elif category == "tickets":
        embed.add_field(name="🎫 Getting help", value="Use your server's ticket panel to open a private ticket.", inline=False)
        embed.add_field(name="🧑‍💼 Ticket staff", value=(
            f"{_cmd('adduser @member')} — Add someone to a ticket\n"
            f"{_cmd('removeuser @member')} — Remove someone from a ticket\n"
            "Close and claim buttons are available inside tickets."
        ), inline=False)
        embed.add_field(name="⚙️ Admin settings", value=(
            "`/tickets config`  `/tickets staff_add`\n"
            "`/tickets staff_remove`  `/tickets panel`  `/tickets show`"
        ), inline=False)

    elif category == "moderation":
        embed.add_field(name="🔨 Members", value=(
            f"{_cmd('kick @member [reason]')}  {_cmd('ban <user or ID> [reason]')}\n"
            f"{_cmd('unban <user ID> [reason]')}\n"
            f"{_cmd('timeout @member <10m|2h|1d> [reason]')} (alias `{config.PREFIX}to`)\n"
            f"{_cmd('mute @member <time> [reason]')}  {_cmd('untimeout @member')}  {_cmd('unmute @member')}"
        ), inline=False)
        embed.add_field(name="⚠️ Warnings", value=(
            f"{_cmd('warn @member <reason>')}  {_cmd('warnings [@member]')}\n"
            f"{_cmd('delwarn <number>')}  {_cmd('clearwarnings @member')}"
        ), inline=False)
        embed.add_field(name="🧹 Channels", value=(
            f"{_cmd('purge <1-100> [@member]')}  {_cmd('slowmode <seconds> [#channel]')}\n"
            f"{_cmd('lock [#channel]')}  {_cmd('unlock [#channel]')}"
        ), inline=False)
        embed.add_field(name="Access", value=(
            "Each command needs the matching Discord permission (Kick, Ban, Timeout Members, Manage Messages, "
            "Manage Channels) for you **and** the bot. All of them also work as slash commands."
        ), inline=False)

    elif category == "security":
        embed.add_field(name="🛡️ AutoMod (Manage Server)", value=(
            "`/automod status`  `/automod toggle`  `/automod set`\n"
            "`/automod badword_add`  `/automod badword_remove`\n"
            "`/automod badword_list`  `/automod link_allow`\n"
            "`/automod link_disallow`  `/automod exempt_role`\n"
            "`/automod exempt_channel`"
        ), inline=False)
        embed.add_field(name="📋 Audit logs (Manage Server)", value=(
            "`/logs setup`  `/logs set`  `/logs clear`  `/logs show`  `/logs test`\n"
            "Track message, member, voice, server, and moderation events."
        ), inline=False)
        embed.add_field(name="Manual moderation", value="See the **Moderation** page for kick, ban, timeout and warn.", inline=False)

    elif category == "afk":
        embed.add_field(name="💤 AFK setup (Manage Server)", value=(
            "`/afk setup` — Choose an AFK voice channel and time\n"
            "`/afk off` — Turn auto-AFK off\n"
            "`/afk show` — Check current settings"
        ), inline=False)
        embed.add_field(name="🛡️ Exceptions", value=(
            "`/afk exempt_role` — Exclude a role\n"
            "`/afk exempt_channel` — Exclude a voice channel"
        ), inline=False)
        embed.add_field(name="How it works", value=(
            "Members who are **self-muted or self-deafened** for the configured time are moved "
            "to the AFK room. Moderator mutes do not count."
        ), inline=False)

    elif category == "admin":
        embed.add_field(name="⚙️ General (Manage Server)", value=(
            "`/config` — Overview of all server settings\n"
            "`/help setup` — Guided setup with progress checks"
        ), inline=False)
        embed.add_field(name="📌 Setup commands", value=(
            "`/welcome set`  `/welcome off`  `/welcome test`\n"
            "`/autorole add`  `/autorole remove`  `/autorole list`\n"
            "`/tickets config`  `/tickets panel`\n"
            "`/tempvoice setup`  `/tempvoice panel`  `/tempvoice off`\n"
            "`/247 on`  `/247 off`  `/247 status`\n"
            "`/logs setup`  `/automod status`  `/levels toggle`\n"
            "`/afk setup`"
        ), inline=False)
        embed.add_field(name="🔧 Utility", value=f"{_cmd('ping')} — Bot latency", inline=False)
        embed.add_field(name="Access", value="Server settings need **Manage Server** permission.", inline=False)

    elif category == "owner":
        embed.add_field(name="👑 Bot profile (application owner only)", value=(
            f"{_cmd('bot')} — Show the current profile\n"
            f"{_cmd('bot name <name>')}  {_cmd('bot about <text>')}\n"
            f"{_cmd('bot avatar')}  {_cmd('bot avatar_reset')}\n"
            f"{_cmd('bot banner')}  {_cmd('bot banner_reset')}\n"
            f"{_cmd('bot status <status>')}\n"
            f"{_cmd('bot activity <type> <text>')}\n"
            f"{_cmd('bot stream <url> <title>')}"
        ), inline=False)
        embed.add_field(name="Tip", value=(
            "Profile commands also work as `/bot …`. Use them only if you own the bot application."
        ), inline=False)

    embed.set_footer(text=f"dor Help  •  {title}  •  Prefix: {config.PREFIX}")
    return embed


class HelpMenuView(discord.ui.View):
    """Change the help category without posting additional messages."""

    def __init__(self, bot, guild):
        super().__init__(timeout=300)
        self.bot = bot
        self.guild = guild
        self.message = None

    @discord.ui.select(
        placeholder="📚 Select a command category…",
        min_values=1,
        max_values=1,
        options=[
            discord.SelectOption(label="Overview", value="overview", emoji="🏠", description="All categories"),
            discord.SelectOption(label="Voice Rooms", value="voice", emoji="🔊", description="Temporary voice channels"),
            discord.SelectOption(label="Levels", value="levels", emoji="⭐", description="XP and leaderboard"),
            discord.SelectOption(label="Tickets", value="tickets", emoji="🎫", description="Support and staff tools"),
            discord.SelectOption(label="Moderation", value="moderation", emoji="🔨", description="Kick, ban, timeout, warn"),
            discord.SelectOption(label="AutoMod & Logs", value="security", emoji="🛡️", description="Safety and audit logs"),
            discord.SelectOption(label="Auto-AFK", value="afk", emoji="💤", description="Idle voice members"),
            discord.SelectOption(label="Server Settings", value="admin", emoji="⚙️", description="Admin configuration"),
            discord.SelectOption(label="Bot Profile", value="owner", emoji="👑", description="Owner customization"),
        ],
        row=0,
    )
    async def select_category(self, interaction: discord.Interaction, select: discord.ui.Select):
        await interaction.response.edit_message(
            embed=build_help_embed(self.bot, interaction.guild or self.guild, select.values[0]),
            view=self,
        )

    @discord.ui.button(label="Home", emoji="🏠", style=discord.ButtonStyle.secondary, row=1)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            embed=build_help_embed(self.bot, interaction.guild or self.guild), view=self,
        )

    async def on_timeout(self):
        if self.message is None:
            return
        for child in self.children:
            child.disabled = True
        try:
            await self.message.edit(view=self)
        except (discord.Forbidden, discord.HTTPException):
            pass


class General(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="ping", description="Check the bot's latency")
    async def ping(self, ctx: commands.Context):
        await say(ctx, f"Pong! {round(self.bot.latency * 1000)} ms")

    @commands.hybrid_command(name="help", description="Browse dor commands and the setup guide")
    @app_commands.describe(topic="Select 'setup' for the step-by-step admin guide")
    async def help(self, ctx: commands.Context, topic: Optional[Literal["setup"]] = None):
        if topic == "setup":
            return await self._setup_guide(ctx)
        view = HelpMenuView(self.bot, ctx.guild)
        view.message = await say(
            ctx, embed=build_help_embed(self.bot, ctx.guild), view=view,
            ephemeral=ctx.interaction is not None,
        )

    async def _setup_guide(self, ctx: commands.Context):
        if ctx.guild is None:
            return await say(ctx, "Run this inside your server.", ephemeral=True)
        if not is_manager(ctx.author):
            return await say(ctx, "🔒 You need **Manage Server** to use `/help setup`.", ephemeral=True)
        await ctx.defer(ephemeral=True)
        view = SetupGuideView(self.bot, ctx.guild, ctx.author.id)
        state = await gather(self.bot, ctx.guild)
        view.message = await say(
            ctx, embed=build_setup_embed(state, ctx.guild, "overview"), view=view,
            ephemeral=True,
        )

    @app_commands.command(name="config", description="Show how this server is configured")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def show_config(self, interaction: discord.Interaction):
        if not is_manager(interaction.user):
            raise app_commands.CheckFailure("You need **Manage Server** permission to use this command.")
        guild = interaction.guild
        s = await self.bot.db.get_settings(guild.id)
        embed = make_embed(f"{guild.name}  •  dor Configuration")
        logs = s["log_channels"]
        embed.add_field(name="📋 Logs", value="\n".join(
            f"`{k}` → {_chan(guild, v)}" for k, v in logs.items()
        ) or "not set — `/logs setup`", inline=False)
        w = s["welcome"]
        embed.add_field(name="👋 Welcome", value=f"{_onoff(w['enabled'])} • {_chan(guild, w['channel_id'])}", inline=True)
        ar = s["autorole"]
        embed.add_field(name="🏷️ Auto roles", value=f"{len(ar['members'])} member, {len(ar['bots'])} bot roles", inline=True)
        t = s["tickets"]
        embed.add_field(name="🎫 Tickets", value=(
            f"Category: {_chan(guild, t['category_id'])}\nStaff roles: {len(t['staff_role_ids'])}"
        ), inline=True)
        sec = s["security"]
        rules = [name for name in ("spam", "mentions", "invites", "links", "caps", "badwords", "raid")
                 if sec[name]["enabled"]]
        embed.add_field(name="🛡️ AutoMod", value=f"{_onoff(sec['enabled'])} • rules: {', '.join(rules) or 'none'}", inline=False)
        lv = s["leveling"]
        embed.add_field(name="⭐ Levels", value=f"{_onoff(lv['enabled'])} • {len(lv['roles'])} rewards", inline=True)
        afk = s["afk"]
        embed.add_field(name="💤 Auto-AFK", value=(
            f"{_onoff(afk['enabled'])} • {afk['minutes']} min • {_chan(guild, afk['channel_id'])}"
        ), inline=True)
        tv = s["tempvoice"]
        embed.add_field(name="🔊 Temp voice", value=f"Hub: {_chan(guild, tv['hub_id'])}", inline=True)
        v247 = s.get("voice247", {}).get("channel_id")
        embed.add_field(name="📌 24/7 voice", value=_chan(guild, v247) if v247 else "off", inline=True)
        await respond(interaction, embed=embed)


async def setup(bot):
    await bot.add_cog(General(bot))
