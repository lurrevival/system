import logging
from typing import Literal, Optional

import discord
from discord import app_commands
from discord.ext import commands
from discord.http import Route

import config
from utils.helpers import make_embed, say
from utils.profile import (MAX_IMAGE_BYTES, clean_presence, to_data_uri, valid_stream_url,
                           validate_about, validate_name)
from utils.text import truncate

log = logging.getLogger("mg3.profile")

STATUSES = {
    "online": discord.Status.online,
    "idle": discord.Status.idle,
    "dnd": discord.Status.dnd,
    "offline": discord.Status.invisible,  # shows as offline, but the bot still works
}
ACTIVITY_TYPES = {
    "watching": discord.ActivityType.watching,
    "listening": discord.ActivityType.listening,
    "competing": discord.ActivityType.competing,
}


def build_activity(kind, text, url=""):
    if kind == "none" or not text:
        return None
    if kind == "streaming":
        return discord.Streaming(name=text, url=url)
    if kind == "playing":
        return discord.Game(name=text)
    if kind == "custom":
        return discord.CustomActivity(name=text)
    return discord.Activity(type=ACTIVITY_TYPES[kind], name=text)


class Profile(commands.Cog):
    """Owner-only commands that change the bot's Discord profile."""

    def __init__(self, bot):
        self.bot = bot

    # ---- saved status ---------------------------------------------------
    async def _state(self):
        return clean_presence(await self.bot.db.get_state("presence"), config.BOT_NAME)

    async def apply_presence(self, state=None):
        state = state or await self._state()
        await self.bot.change_presence(status=STATUSES[state["status"]],
                                       activity=build_activity(state["kind"], state["text"], state.get("url", "")))

    @commands.Cog.listener()
    async def on_ready(self):
        try:
            await self.apply_presence()
        except Exception:
            log.exception("Could not apply the saved status")

    # ---- helpers --------------------------------------------------------
    async def _read_image(self, attachment):
        if attachment.size > MAX_IMAGE_BYTES:
            raise ValueError("That image is too big (limit 8 MB).")
        return await attachment.read()

    async def _patch(self, path, payload):
        # Sent straight to Discord's REST API so it works whatever the library version.
        await self.bot.http.request(Route("PATCH", path), json=payload)

    @staticmethod
    def _discord_error(exc):
        return f"Discord refused that: {getattr(exc, 'text', None) or exc}"

    # ---- /bot ... (owner only) -----------------------------------------
    @commands.hybrid_group(name="bot", description="Change the bot's own profile (owner only)",
                           invoke_without_command=True, fallback="show")
    @commands.is_owner()
    @app_commands.default_permissions(administrator=True)
    async def profile_group(self, ctx: commands.Context):
        state = await self._state()
        embed = make_embed(f"{self.bot.user.name}", f"ID `{self.bot.user.id}`")
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.add_field(name="Status", value=state["status"])
        embed.add_field(name="Activity", value=f"{state['kind']}: {state['text']}" if state["text"] else "none")
        try:
            info = await self.bot.application_info()
            embed.add_field(name="About me", value=truncate(info.description or "not set", 400), inline=False)
        except discord.HTTPException:
            pass
        embed.add_field(name="Change it with", value=(
            f"`{config.PREFIX}bot name|avatar|banner|about|status|activity|stream` (or `/bot …`)\n"
            f"Reset images: `{config.PREFIX}bot avatar_reset`, `{config.PREFIX}bot banner_reset`"), inline=False)
        await say(ctx, embed=embed, ephemeral=True)

    @profile_group.command(name="name", description="Change the bot's username (Discord allows ~2 changes per hour)")
    @app_commands.describe(name="New username, 2-32 characters")
    async def profile_name(self, ctx: commands.Context, *, name: str):
        try:
            name = validate_name(name)
            await self.bot.user.edit(username=name)
        except ValueError as exc:
            return await say(ctx, str(exc), ephemeral=True)
        except discord.HTTPException as exc:
            return await say(ctx, self._discord_error(exc), ephemeral=True)
        await say(ctx, f"✅ My name is now **{name}**.", ephemeral=True)

    @profile_group.command(name="avatar", description="Change the bot's profile picture")
    @app_commands.describe(image="PNG, JPEG, GIF or WebP, up to 8 MB")
    async def profile_avatar(self, ctx: commands.Context, image: discord.Attachment):
        await ctx.defer(ephemeral=True)
        try:
            data = await self._read_image(image)
            to_data_uri(data)  # checks that it really is an image
            await self.bot.user.edit(avatar=data)
        except ValueError as exc:
            return await say(ctx, str(exc), ephemeral=True)
        except discord.HTTPException as exc:
            return await say(ctx, self._discord_error(exc), ephemeral=True)
        await say(ctx, "✅ New profile picture set.", ephemeral=True)

    @profile_group.command(name="avatar_reset", description="Remove the bot's profile picture")
    async def profile_avatar_reset(self, ctx: commands.Context):
        try:
            await self.bot.user.edit(avatar=None)
        except discord.HTTPException as exc:
            return await say(ctx, self._discord_error(exc), ephemeral=True)
        await say(ctx, "✅ Profile picture removed.", ephemeral=True)

    @profile_group.command(name="banner", description="Change the bot's profile banner")
    @app_commands.describe(image="PNG, JPEG, GIF or WebP, up to 8 MB (wide images look best)")
    async def profile_banner(self, ctx: commands.Context, image: discord.Attachment):
        await ctx.defer(ephemeral=True)
        try:
            uri = to_data_uri(await self._read_image(image))
            await self._patch("/users/@me", {"banner": uri})
        except ValueError as exc:
            return await say(ctx, str(exc), ephemeral=True)
        except discord.HTTPException as exc:
            return await say(ctx, self._discord_error(exc), ephemeral=True)
        await say(ctx, "✅ New banner set. It can take a minute to show up.", ephemeral=True)

    @profile_group.command(name="banner_reset", description="Remove the bot's profile banner")
    async def profile_banner_reset(self, ctx: commands.Context):
        try:
            await self._patch("/users/@me", {"banner": None})
        except discord.HTTPException as exc:
            return await say(ctx, self._discord_error(exc), ephemeral=True)
        await say(ctx, "✅ Banner removed.", ephemeral=True)

    @profile_group.command(name="about", description="Change the bot's About Me text (up to 400 characters)")
    @app_commands.describe(text="The new About Me text")
    async def profile_about(self, ctx: commands.Context, *, text: str):
        try:
            text = validate_about(text)
            await self._patch("/applications/@me", {"description": text})
        except ValueError as exc:
            return await say(ctx, str(exc), ephemeral=True)
        except discord.HTTPException as exc:
            return await say(ctx, self._discord_error(exc), ephemeral=True)
        await say(ctx, "✅ About Me updated. Discord can take a few minutes to refresh it on profiles.", ephemeral=True)

    @profile_group.command(name="status", description="Set the bot's status: online, idle, dnd or offline")
    @app_commands.describe(status="online, idle, dnd (do not disturb) or offline (the bot still works)")
    async def profile_status(self, ctx: commands.Context, status: Literal["online", "idle", "dnd", "offline"]):
        state = await self._state()
        state["status"] = status
        await self.bot.db.set_state("presence", state)
        await self.apply_presence(state)
        await say(ctx, f"✅ Status set to **{status}**.", ephemeral=True)

    @profile_group.command(name="activity", description="Set the text under the bot's name (Playing, Watching, …)")
    @app_commands.describe(kind="playing, watching, listening, competing, custom, or none to clear it",
                           text="What to show, e.g. 'over the server'")
    async def profile_activity(self, ctx: commands.Context,
                           kind: Literal["playing", "watching", "listening", "competing", "custom", "none"],
                           *, text: Optional[str] = None):
        state = await self._state()
        if kind == "none":
            state["kind"], state["text"], state["url"] = "none", "", ""
        else:
            text = (text or "").strip()
            if not text:
                return await say(ctx, "Add the text to show, e.g. `watching over the server`.", ephemeral=True)
            state["kind"], state["text"], state["url"] = kind, text[:128], ""
        await self.bot.db.set_state("presence", state)
        await self.apply_presence(state)
        shown = "cleared" if kind == "none" else f"**{kind}** {state['text']}"
        await say(ctx, f"✅ Activity {shown}.", ephemeral=True)

    @profile_group.command(name="stream", description="Show the purple 'Streaming' status with a Twitch/YouTube link")
    @app_commands.describe(url="A Twitch or YouTube link, e.g. https://twitch.tv/yourname",
                           text="What the stream is called")
    async def profile_stream(self, ctx: commands.Context, url: str, *, text: str):
        if not valid_stream_url(url):
            return await say(ctx, "Use a full Twitch or YouTube link, e.g. `https://twitch.tv/yourname`.", ephemeral=True)
        state = await self._state()
        state["kind"], state["text"], state["url"] = "streaming", text.strip()[:128], url.strip()
        await self.bot.db.set_state("presence", state)
        await self.apply_presence(state)
        await say(ctx, f"✅ Now showing **Streaming {state['text']}**. (Hidden while the status is offline.)", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Profile(bot))
