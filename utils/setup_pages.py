"""Interactive server setup guide for /help setup."""

import discord

import config
from utils.helpers import is_manager


STEPS = [
    ("logs", "📋", "Logs", "/logs setup channel:#logs",
     "Choose a channel to receive server events."),
    ("welcome", "👋", "Welcome", "/welcome set channel:#welcome",
     "Enable a welcome message for new members. Use `/welcome test` to preview it."),
    ("autorole", "🏷️", "Auto Roles", "/autorole add role:@Member",
     "Choose a role for new members. The bot role must be above the role it gives."),
    ("tickets", "🎫", "Tickets", "/tickets config category:<category>",
     "Then add a staff role with `/tickets staff_add` and publish `/tickets panel`."),
    ("security", "🛡️", "AutoMod", "/automod status",
     "AutoMod starts enabled with baseline protections. Review or tune rules with `/automod toggle`."),
    ("levels", "⭐", "Leveling", "/levels toggle enabled:True",
     "Leveling is enabled by default. Configure rewards with `/levels role_add`."),
    ("voice", "🔊", "Temp Voice", "/tempvoice setup hub:<voice channel>",
     "Create a join-to-create voice channel first. dor automatically posts an Arabic panel in each new room's voice chat. Use `/tempvoice panel` for manual recovery."),
    ("afk", "💤", "Auto-AFK", "/afk setup channel:<AFK voice channel> minutes:10",
     "Moves self-muted/deafened members to your AFK room after a delay. Optional."),
]


async def gather(bot, guild):
    """Refresh saved settings to report actual setup progress."""
    s = await bot.db.get_settings(guild.id)
    def channel_exists(channel_id):
        return bool(channel_id and guild.get_channel(int(channel_id)))
    return {
        "logs": bool(s["log_channels"]),
        "welcome": bool(s["welcome"]["enabled"] and channel_exists(s["welcome"]["channel_id"])),
        "autorole": bool(s["autorole"]["members"] or s["autorole"]["bots"]),
        "tickets": bool(channel_exists(s["tickets"]["category_id"]) and s["tickets"]["staff_role_ids"]),
        "security": bool(s["security"]["enabled"]),
        "levels": bool(s["leveling"]["enabled"]),
        "voice": channel_exists(s["tempvoice"]["hub_id"]),
        "afk": bool(s["afk"]["enabled"] and channel_exists(s["afk"]["channel_id"])),
    }


def build_embed(state, guild, page="overview"):
    """Show a specific setup step and its saved completion state."""
    keys = {step[0] for step in STEPS}
    page = page if page in keys else "overview"
    complete = sum(bool(state.get(key)) for key in keys)
    total = len(STEPS)
    embed = discord.Embed(
        title="dor  •  Server Setup Guide",
        description=f"**{guild.name}**  •  {complete}/{total} features configured or enabled\n"
                    "Select a step below. **Refresh** re-checks saved settings.",
        color=config.EMBED_COLOR,
    )
    if page == "overview":
        lines = [f"{'✅' if state.get(key) else '▫️'} {emoji} **{label}**"
                 for key, emoji, label, _, _ in STEPS]
        embed.add_field(name="Setup checklist", value="\n".join(lines), inline=False)
        embed.add_field(name="Where to start", value=(
            "Select any step to see its command and explanation. "
            "AutoMod and leveling are enabled by default; AFK and 24/7 voice are optional."
        ), inline=False)
    else:
        key, emoji, label, command, explanation = next(s for s in STEPS if s[0] == page)
        embed.add_field(name=f"{emoji} {label}", value=explanation, inline=False)
        embed.add_field(name="Run this command", value=f"`{command}`", inline=False)
        embed.add_field(name="Current status", value=(
            "✅ Configured / enabled" if state.get(key) else "▫️ Not configured / disabled"
        ), inline=False)
        if page == "tickets":
            embed.add_field(name="Finish tickets", value=(
                "`/tickets staff_add role:@Staff` then `/tickets panel channel:#support`"
            ), inline=False)
    embed.set_footer(text="dor • Only server managers can open and control this guide")
    return embed


class SetupGuideView(discord.ui.View):
    def __init__(self, bot, guild, author_id):
        super().__init__(timeout=600)
        self.bot = bot
        self.guild = guild
        self.author_id = author_id
        self.page = "overview"
        self.message = None

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id == self.author_id and is_manager(interaction.user):
            return True
        await interaction.response.send_message(
            "This setup guide belongs to another server manager. Run `/help setup` to open your own.",
            ephemeral=True,
        )
        return False

    async def _show(self, interaction, page=None):
        if page is not None:
            self.page = page
        state = await gather(self.bot, self.guild)
        await interaction.response.edit_message(
            embed=build_embed(state, self.guild, self.page), view=self,
        )

    @discord.ui.select(
        placeholder="🧭 Choose a setup step…",
        options=[
            discord.SelectOption(label="Overview", value="overview", emoji="🏠"),
            discord.SelectOption(label="Logs", value="logs", emoji="📋"),
            discord.SelectOption(label="Welcome", value="welcome", emoji="👋"),
            discord.SelectOption(label="Auto Roles", value="autorole", emoji="🏷️"),
            discord.SelectOption(label="Tickets", value="tickets", emoji="🎫"),
            discord.SelectOption(label="AutoMod", value="security", emoji="🛡️"),
            discord.SelectOption(label="Leveling", value="levels", emoji="⭐"),
            discord.SelectOption(label="Temp Voice", value="voice", emoji="🔊"),
            discord.SelectOption(label="Auto-AFK", value="afk", emoji="💤"),
        ],
        row=0,
    )
    async def select_step(self, interaction: discord.Interaction, select: discord.ui.Select):
        await self._show(interaction, select.values[0])

    @discord.ui.button(label="Home", emoji="🏠", style=discord.ButtonStyle.secondary, row=1)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show(interaction, "overview")

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.primary, row=1)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show(interaction)

    async def on_timeout(self):
        if self.message is None:
            return
        for child in self.children:
            child.disabled = True
        try:
            await self.message.edit(view=self)
        except (discord.Forbidden, discord.HTTPException):
            pass
