import asyncio
import logging
import os
import sys

import discord
from discord import app_commands
from discord.ext import commands

import config
from database import Database
from utils.guards import should_leave

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("mg3")
discord.VoiceClient.warn_nacl = False  # no audio is played; PyNaCl/davey (discord.py[voice]) are only needed to sit in a voice channel for /247

COGS = [
    "cogs.general",
    "cogs.welcome",
    "cogs.logs",
    "cogs.tickets",
    "cogs.security",
    "cogs.moderation",
    "cogs.leveling",
    "cogs.tempvoice",
    "cogs.profile",
    "cogs.afk",
    "cogs.voice247",
]


class DorBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True          # Server Members Intent (privileged)
        intents.message_content = True  # Message Content Intent (privileged)
        super().__init__(
            command_prefix=commands.when_mentioned_or(config.PREFIX),
            case_insensitive=True,
            intents=intents,
            help_command=None,
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True, replied_user=False),
        )
        self.db = Database(os.path.join(config.DATA_DIR, "bot.db"))

    async def setup_hook(self):
        await self.db.connect()
        for name in COGS:
            try:
                await self.load_extension(name)
                log.info("Loaded %s", name)
            except Exception:
                log.exception("Could not load %s (the rest of the bot keeps running)", name)
        try:
            if config.GUILD_ID:
                guild = discord.Object(id=config.GUILD_ID)
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                log.info("Synced %d commands to guild %s: %s", len(synced), config.GUILD_ID,
                         ", ".join(sorted(c.name for c in synced)))
                if not any(c.name == "247" for c in synced):
                    log.warning("/247 was NOT synced. Is cogs/voice247.py there and listed in COGS in main.py?")
            else:
                synced = await self.tree.sync()
                log.info("Synced %d global commands (can take up to an hour to appear): %s", len(synced),
                         ", ".join(sorted(c.name for c in synced)))
                if not any(c.name == "247" for c in synced):
                    log.warning("/247 was NOT synced. Is cogs/voice247.py there and listed in COGS in main.py?")
        except Exception:
            log.exception("Command sync failed")

    async def _leave(self, guild, why):
        log.warning("Leaving %s (%s): %s", guild.name, guild.id, why)
        try:
            await guild.leave()
        except discord.HTTPException:
            log.exception("Could not leave %s", guild.id)

    async def on_guild_join(self, guild):
        if should_leave(guild.id, config.ALLOWED_GUILDS):
            await self._leave(guild, "not on the allowed server list (GUILD_ID / ALLOWED_GUILDS)")

    async def on_ready(self):
        log.info("Logged in as %s (%s) in %d server(s)", self.user, self.user.id, len(self.guilds))
        allowed = config.ALLOWED_GUILDS
        if not allowed:
            log.warning("Server protection is OFF: set GUILD_ID so the bot leaves any other server it is added to.")
        elif {g.id for g in self.guilds} & allowed:
            for guild in list(self.guilds):
                if should_leave(guild.id, allowed):
                    await self._leave(guild, "not on the allowed server list (GUILD_ID / ALLOWED_GUILDS)")
        else:
            log.warning("The bot is not in any allowed server yet, so it won't leave anything. "
                        "Check GUILD_ID is the right server ID.")

    async def close(self):
        await super().close()
        await self.db.close()


bot = DorBot()
_answered_errors = set()  # interaction ids that already got an error message


def _unwrap(error):
    for _ in range(6):
        inner = getattr(error, "original", None)
        if inner is None:
            break
        error = inner
    return error


def friendly_error(error):
    """Return the message to show for `error`, or None to stay silent."""
    err = _unwrap(error)
    if isinstance(err, commands.CommandNotFound):
        return None
    if isinstance(err, commands.NoPrivateMessage):
        return "Use this command inside a server."
    if isinstance(err, (app_commands.CheckFailure, commands.CheckFailure)):
        return str(err) or "You can't use this command."
    if isinstance(err, commands.MissingRequiredArgument):
        return f"Missing `{err.param.name}`. Example: `{config.PREFIX}help`"
    if isinstance(err, commands.UserInputError):
        return f"That doesn't look right: {err}" if str(err) else "That doesn't look right."
    if isinstance(err, (commands.CommandOnCooldown, app_commands.CommandOnCooldown)):
        return f"Slow down — try again in {err.retry_after:.0f}s."
    log.error("Command error", exc_info=err)
    return "Something went wrong running that command."


async def _reply_to_interaction(interaction, message):
    if interaction.id in _answered_errors:
        return
    _answered_errors.add(interaction.id)
    if len(_answered_errors) > 500:
        _answered_errors.clear()
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        pass


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    message = friendly_error(error)
    if message:
        await _reply_to_interaction(interaction, message)


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    message = friendly_error(error)
    if not message:
        return
    if ctx.interaction is not None:
        return await _reply_to_interaction(ctx.interaction, message)
    try:
        await ctx.send(message, delete_after=10)
    except discord.HTTPException:
        pass


def main():
    if not config.TOKEN:
        print("DISCORD_TOKEN is not set. Add it to the environment variables and restart.")
        sys.exit(1)
    try:
        bot.run(config.TOKEN, log_handler=None)
    except discord.PrivilegedIntentsRequired:
        print(
            "Privileged intents are not enabled. In the Discord Developer Portal open your "
            "application > Bot and turn on SERVER MEMBERS INTENT and MESSAGE CONTENT INTENT, then restart."
        )
        sys.exit(1)
    except discord.LoginFailure:
        print("Discord rejected the token. Reset it in the Developer Portal and update DISCORD_TOKEN.")
        sys.exit(1)


if __name__ == "__main__":
    main()
