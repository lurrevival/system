import asyncio
import logging
import time
from pathlib import Path
from typing import Optional, Union

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils.helpers import BLUE, admin_group, make_embed, respond, say, update_overwrite
from utils.text import render_template, truncate

log = logging.getLogger("mg3.tempvoice")

CREATE_COOLDOWN = 5  # seconds between rooms for one person
ROOM_SWEEP_SECONDS = 60  # periodic recovery if a voice event is missed
ROOM_LEAVE_RECHECK_SECONDS = 1.5  # allow Discord voice state to settle

REGIONS = [
    ("Automatic", None), ("Rotterdam (Europe)", "rotterdam"), ("Singapore", "singapore"),
    ("India", "india"), ("Japan", "japan"), ("Hong Kong", "hongkong"), ("Sydney", "sydney"),
    ("South Africa", "southafrica"), ("Brazil", "brazil"), ("Russia", "russia"),
    ("US East", "us-east"), ("US Central", "us-central"), ("US West", "us-west"),
]

# panel button key -> what it needs
NEEDS_MEMBER = {"kick", "transfer", "mute", "unmute", "block", "allow", "invite"}


class RenameModal(discord.ui.Modal, title="Rename your room"):
    name = discord.ui.TextInput(label="New name", max_length=100)

    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        message = await self.cog.do(interaction.user, "rename", str(self.name))
        await interaction.response.send_message(message, ephemeral=True)


class LimitModal(discord.ui.Modal, title="Set the user limit"):
    limit = discord.ui.TextInput(label="Number of people (0 = unlimited)", max_length=2, placeholder="0")

    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        text = str(self.limit).strip()
        if not text.isdigit():
            return await interaction.response.send_message("Type a number from 0 to 99.", ephemeral=True)
        message = await self.cog.do(interaction.user, "limit", int(text))
        await interaction.response.send_message(message, ephemeral=True)


class MemberPicker(discord.ui.UserSelect):
    def __init__(self, cog, key):
        super().__init__(placeholder="Pick a member…", min_values=1, max_values=1)
        self.cog = cog
        self.key = key

    async def callback(self, interaction: discord.Interaction):
        user = self.values[0]
        member = interaction.guild.get_member(user.id)
        if member is None:
            return await interaction.response.edit_message(content="I can't find that member.", view=None)
        message = await self.cog.do(interaction.user, self.key, member)
        await interaction.response.edit_message(content=message, view=None)


class RegionPicker(discord.ui.Select):
    def __init__(self, cog):
        options = [discord.SelectOption(label=label, value=value or "auto") for label, value in REGIONS]
        super().__init__(placeholder="Pick a voice region…", options=options)
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]
        message = await self.cog.do(interaction.user, "region", None if value == "auto" else value)
        await interaction.response.edit_message(content=message, view=None)


class PickerView(discord.ui.View):
    def __init__(self, item):
        super().__init__(timeout=60)
        self.add_item(item)


class TempVoicePanel(discord.ui.View):
    """Icon buttons like a control panel. Persistent, so it keeps working after a restart."""

    def __init__(self, icon_emojis=None):
        super().__init__(timeout=None)
        # Emoji visuals are configured at runtime, but the stable custom_id on
        # each button is unchanged. Old panels remain clickable after a restart.
        for child in self.children:
            key = child.custom_id.rsplit(":", 1)[-1] if child.custom_id else None
            if key in (icon_emojis or {}):
                child.emoji = icon_emojis[key]

    async def _press(self, interaction: discord.Interaction, key: str):
        cog = interaction.client.get_cog("TempVoice")
        if cog is None:
            return await interaction.response.send_message("Temp voice is unavailable right now.", ephemeral=True)
        channel, room, error = await cog.resolve_room(interaction.user)
        if error:
            return await interaction.response.send_message(error, ephemeral=True)
        if key == "rename":
            return await interaction.response.send_modal(RenameModal(cog))
        if key == "limit":
            return await interaction.response.send_modal(LimitModal(cog))
        if key == "region":
            return await interaction.response.send_message("Choose a region:", view=PickerView(RegionPicker(cog)),
                                                           ephemeral=True)
        if key in NEEDS_MEMBER:
            return await interaction.response.send_message("Choose a member:", view=PickerView(MemberPicker(cog, key)),
                                                           ephemeral=True)
        await interaction.response.send_message(await cog.do(interaction.user, key), ephemeral=True)

    # row 0
    @discord.ui.button(emoji="✏️", style=discord.ButtonStyle.secondary, custom_id="mg3:tv:rename", row=0)
    async def b_rename(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._press(interaction, "rename")

    @discord.ui.button(emoji="👥", style=discord.ButtonStyle.secondary, custom_id="mg3:tv:limit", row=0)
    async def b_limit(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._press(interaction, "limit")

    @discord.ui.button(emoji="🔨", style=discord.ButtonStyle.secondary, custom_id="mg3:tv:kick", row=0)
    async def b_kick(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._press(interaction, "kick")

    @discord.ui.button(emoji="👑", style=discord.ButtonStyle.secondary, custom_id="mg3:tv:transfer", row=0)
    async def b_transfer(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._press(interaction, "transfer")

    @discord.ui.button(emoji="🗑️", style=discord.ButtonStyle.secondary, custom_id="mg3:tv:delete", row=0)
    async def b_delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._press(interaction, "delete")

    # row 1
    @discord.ui.button(emoji="🔒", style=discord.ButtonStyle.secondary, custom_id="mg3:tv:lock", row=1)
    async def b_lock(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._press(interaction, "lock")

    @discord.ui.button(emoji="🔓", style=discord.ButtonStyle.secondary, custom_id="mg3:tv:unlock", row=1)
    async def b_unlock(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._press(interaction, "unlock")

    @discord.ui.button(emoji="🙈", style=discord.ButtonStyle.secondary, custom_id="mg3:tv:hide", row=1)
    async def b_hide(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._press(interaction, "hide")

    @discord.ui.button(emoji="👁️", style=discord.ButtonStyle.secondary, custom_id="mg3:tv:show", row=1)
    async def b_show(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._press(interaction, "show")

    @discord.ui.button(emoji="🌐", style=discord.ButtonStyle.secondary, custom_id="mg3:tv:region", row=1)
    async def b_region(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._press(interaction, "region")

    # row 2
    @discord.ui.button(emoji="🔇", style=discord.ButtonStyle.secondary, custom_id="mg3:tv:mute", row=2)
    async def b_mute(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._press(interaction, "mute")

    @discord.ui.button(emoji="🎙️", style=discord.ButtonStyle.secondary, custom_id="mg3:tv:unmute", row=2)
    async def b_unmute(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._press(interaction, "unmute")

    @discord.ui.button(emoji="⛔", style=discord.ButtonStyle.secondary, custom_id="mg3:tv:block", row=2)
    async def b_block(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._press(interaction, "block")

    @discord.ui.button(emoji="✅", style=discord.ButtonStyle.secondary, custom_id="mg3:tv:allow", row=2)
    async def b_allow(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._press(interaction, "allow")

    @discord.ui.button(emoji="➕", style=discord.ButtonStyle.secondary, custom_id="mg3:tv:invite", row=2)
    async def b_invite(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._press(interaction, "invite")


# This PNG is a static, bundled control legend designed to match the requested
# Arabic MG3-style card. There is no image-generation dependency at runtime.
PANEL_IMAGE = Path(__file__).resolve().parent.parent / "assets" / "tempvoice_panel.png"
PANEL_FILENAME = "dor-tempvoice-panel.png"
# Application emojis, uploaded once on startup (then reused on every restart).
# This versioned prefix makes Discord fetch the new AI icon set instead of
# its cached previous icons.
# These are the redesigned AI-generated icons, segmented and geometrically
# centered within 128x128 transparent canvases for clickable buttons.
PANEL_ICON_DIR = PANEL_IMAGE.parent / "tempvoice_icons"
PANEL_ICON_KEYS = (
    "rename", "limit", "kick", "transfer", "delete",
    "lock", "unlock", "hide", "show", "region",
    "mute", "unmute", "block", "allow", "invite",
)
PANEL_APP_PREFIX = "dor_v6_ai_"
PANEL_TEXT = (
    "**لوحة تحكم الروم المؤقت**\n"
    "استخدم الأزرار أدناه للتحكم في رومك الخاص.\n\n"
    "✏️ اسم الروم • 👥 حد الأعضاء • 🔨 طرد • 👑 نقل الملكية • 🗑️ حذف\n"
    "🔒 قفل • 🔓 فتح • 🙈 إخفاء • 👁️ إظهار • 🌐 الريجن\n"
    "🔇 ميوت • 🎙️ فك الميوت • ⛔ منع • ✅ سماح • ➕ دعوة"
)


class TempVoice(commands.Cog):
    tempvoice = admin_group("tempvoice", "Join-to-create voice rooms")

    def __init__(self, bot):
        self.bot = bot
        self._last_create = {}
        self._panel_emojis = {}
        self._icons_complete = False
        self._icon_task = None
        self._deleting_rooms = set()
        self._pending_creation = set()

    def _panel_view(self):
        return TempVoicePanel(self._panel_emojis)

    @commands.Cog.listener()
    async def on_ready(self):
        # Create/fetch icons only after Discord has authenticated the bot.
        # on_ready can fire repeatedly; don't launch overlapping uploads.
        if self._icons_complete or (self._icon_task and not self._icon_task.done()):
            return
        self._icon_task = asyncio.create_task(self._ensure_panel_icons())

    async def _ensure_panel_icons(self):
        """Reuse bot application emojis; upload missing ones only once.

        If Discord denies an upload or the library/API is unavailable, the
        ordinary emoji fallback remains clickable and no panel is lost.
        """
        try:
            current = await self.bot.fetch_application_emojis()
        except Exception as exc:
            log.warning("Can't fetch dor button icons; using fallback emoji: %s", exc)
            return
        found = {emoji.name: emoji for emoji in current}
        updated = {}
        for key in PANEL_ICON_KEYS:
            name = f"{PANEL_APP_PREFIX}{key}"
            emoji = found.get(name)
            if emoji is None:
                path = PANEL_ICON_DIR / f"{key}.png"
                if not path.is_file():
                    log.error("Missing Temp Voice icon asset: %s", path)
                    continue
                try:
                    emoji = await self.bot.create_application_emoji(name=name, image=path.read_bytes())
                    log.info("Uploaded application emoji %s", name)
                except Exception as exc:
                    log.warning("Unable to upload application emoji %s: %s", name, exc)
                    if getattr(exc, "status", None) in (401, 403, 429):
                        break
                    continue
            updated[key] = emoji
        self._panel_emojis = updated
        self._icons_complete = len(updated) == len(PANEL_ICON_KEYS)
        if updated:
            log.info("Temp Voice custom PNG icons ready: %s/%s", len(updated), len(PANEL_ICON_KEYS))
            await self._upgrade_existing_panels()

    async def _upgrade_existing_panels(self):
        """Edit active temporary-room panels instead of posting duplicates."""
        try:
            rows = await self.bot.db.all_rooms()
        except Exception as exc:
            log.warning("Could not list rooms for panel icon refresh: %s", exc)
            return
        for row in rows:
            channel = self.bot.get_channel(row["channel_id"])
            if not isinstance(channel, discord.VoiceChannel):
                continue
            if not channel.guild.me or not channel.permissions_for(channel.guild.me).read_message_history:
                continue
            try:
                async for msg in channel.history(limit=50):
                    if self._is_panel_message(msg):
                        await msg.edit(view=self._panel_view())
                        break
            except (discord.Forbidden, discord.HTTPException) as exc:
                log.warning("Could not refresh Temp Voice panel in %s: %s", channel.id, exc)

    def _is_panel_message(self, msg):
        if self.bot.user is None or msg.author.id != self.bot.user.id:
            return False
        if any(att.filename == PANEL_FILENAME for att in msg.attachments):
            return True
        return ("لوحة تحكم الروم المؤقت" in msg.content or
                any(embed.title == "لوحة تحكم الروم المؤقت" for embed in msg.embeds))

    async def cog_load(self):
        # Stable IDs keep existing panels functional while custom icons load.
        self.bot.add_view(TempVoicePanel())
        self.room_sweeper.start()

    async def cog_unload(self):
        self.room_sweeper.cancel()
        if self._icon_task and not self._icon_task.done():
            self._icon_task.cancel()

    @staticmethod
    def _has_human_members(channel):
        # The bot (or other bots) should not keep an abandoned temp room alive.
        return any(not member.bot for member in channel.members)

    @tasks.loop(seconds=ROOM_SWEEP_SECONDS)
    async def room_sweeper(self):
        try:
            await self._cleanup_stale()
        except Exception:
            log.exception("Temp Voice cleanup sweep failed")

    @room_sweeper.before_loop
    async def before_room_sweeper(self):
        await self.bot.wait_until_ready()

    async def _cleanup_stale(self):
        """Recover missed leave events, failed deletes, and empty rooms after restarts."""
        rows = await self.bot.db.all_rooms()
        for room in rows:
            room_id = room["channel_id"]
            if room_id in self._pending_creation or room_id in self._deleting_rooms:
                continue
            guild = self.bot.get_guild(room["guild_id"])
            if guild is None:
                # Don't erase a record just because the guild is temporarily absent.
                continue
            channel = guild.get_channel(room_id)
            if channel is None:
                # A cache miss is not proof the channel no longer exists.
                try:
                    channel = await guild.fetch_channel(room_id)
                except discord.NotFound:
                    await self.bot.db.remove_room(room_id)
                    continue
                except (discord.Forbidden, discord.HTTPException) as exc:
                    log.warning("Unable to verify Temp Voice room %s: %s", room_id, exc)
                    continue
            if not isinstance(channel, discord.VoiceChannel):
                # The original voice room is gone or was replaced.
                await self.bot.db.remove_room(room_id)
                continue
            if not self._has_human_members(channel):
                await self._delete_room(channel)

    async def _delete_room(self, channel):
        """Return True only if Discord deleted the room (or it is already gone).

        Keep the database entry on permission/API failures so later sweeps can retry.
        """
        if channel.id in self._deleting_rooms:
            return False
        self._deleting_rooms.add(channel.id)
        try:
            await channel.delete(reason="Empty temporary voice room")
        except discord.NotFound:
            pass  # Already gone from Discord; clear the stale record.
        except (discord.Forbidden, discord.HTTPException) as exc:
            log.warning(
                "Could not delete Temp Voice room %s in guild %s (check Manage Channels): %s",
                channel.id, channel.guild.id, exc,
            )
            return False
        else:
            log.info("Deleted empty Temp Voice room %s", channel.id)
        finally:
            self._deleting_rooms.discard(channel.id)
        await self.bot.db.remove_room(channel.id)
        return True

    async def _delete_after_leave(self, channel):
        """Recheck after Discord updates member lists; don't delete a rejoined room."""
        await asyncio.sleep(ROOM_LEAVE_RECHECK_SECONDS)
        if channel.id in self._pending_creation:
            return
        if not await self.bot.db.get_room(channel.id):
            return
        refreshed = channel.guild.get_channel(channel.id)
        if isinstance(refreshed, discord.VoiceChannel):
            if not self._has_human_members(refreshed):
                await self._delete_room(refreshed)

    async def _send_panel(self, channel, *, avoid_duplicate=False):
        """Post a persistent control panel to a text channel or a voice channel's chat.

        A newly created temporary voice channel gets one post, automatically.
        The manual command checks recent messages to avoid duplicate panels.
        """
        me = channel.guild.me
        if me is None:
            log.warning("Temp voice panel not sent in %s: bot member is unavailable", channel.id)
            return None
        perms = channel.permissions_for(me)
        if not perms.send_messages:
            log.warning("Temp voice panel not sent in %s: missing Send Messages", channel.id)
            return None

        if avoid_duplicate and perms.read_message_history:
            try:
                async for msg in channel.history(limit=40):
                    if msg.author.id != self.bot.user.id or not self._is_panel_message(msg):
                        continue
                    # Refresh in place; do not duplicate an existing panel.
                    await msg.edit(view=self._panel_view())
                    return msg
            except (discord.Forbidden, discord.HTTPException):
                pass

        kwargs = {
            "view": self._panel_view(),
            "allowed_mentions": discord.AllowedMentions.none(),
        }
        if perms.embed_links and perms.attach_files and PANEL_IMAGE.is_file():
            embed = discord.Embed(color=0x292B31)
            embed.set_image(url=f"attachment://{PANEL_FILENAME}")
            kwargs["embed"] = embed
            kwargs["file"] = discord.File(str(PANEL_IMAGE), filename=PANEL_FILENAME)
        elif perms.embed_links:
            kwargs["embed"] = make_embed("لوحة تحكم الروم المؤقت", PANEL_TEXT, color=BLUE)
        else:
            kwargs["content"] = PANEL_TEXT
        try:
            return await channel.send(**kwargs)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
            log.warning("Could not send Temp Voice panel in %s: %s", channel.id, exc)
            return None

    # ---- events -------------------------------------------------------
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if member.bot or before.channel == after.channel:
            return
        if before.channel is not None:
            room = await self.bot.db.get_room(before.channel.id)
            if room is not None:
                # Always recheck after the event: member lists can be briefly stale.
                # The task only deletes the room if no human is still inside.
                asyncio.create_task(self._delete_after_leave(before.channel))
        if after.channel is not None:
            cfg = (await self.bot.db.get_settings(member.guild.id))["tempvoice"]
            if cfg["hub_id"] and after.channel.id == cfg["hub_id"]:
                await self._create_room(member, after.channel, cfg)

    async def _create_room(self, member, hub, cfg):
        guild = member.guild
        now = time.monotonic()
        if now - self._last_create.get(member.id, -1e9) < CREATE_COOLDOWN:
            try:
                await member.move_to(None, reason="Creating rooms too quickly")
            except (discord.Forbidden, discord.HTTPException):
                pass
            return
        self._last_create[member.id] = now
        category = guild.get_channel(cfg["category_id"]) if cfg["category_id"] else None
        if not isinstance(category, discord.CategoryChannel):
            category = hub.category
        name = truncate(render_template(cfg["name"], user=member.display_name, name=member.display_name), 100, "")
        overwrites = {member: discord.PermissionOverwrite(
            view_channel=True, connect=True, speak=True, manage_channels=True, move_members=True,
            send_messages=True, read_message_history=True)}
        # The bot must be allowed to post in the *voice channel's text chat*, even
        # when the category denies text permissions to ordinary users.
        if guild.me is not None:
            overwrites[guild.me] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True,
                embed_links=True, attach_files=True)
        try:
            channel = await guild.create_voice_channel(
                name or "Voice room", category=category, overwrites=overwrites,
                user_limit=cfg["default_limit"], reason=f"Temporary room for {member}")
        except (discord.Forbidden, discord.HTTPException):
            log.warning("Could not create a temp voice room in %s (check Manage Channels)", guild.id)
            return
        self._pending_creation.add(channel.id)
        try:
            await self.bot.db.add_room(channel.id, guild.id, member.id)
            try:
                await member.move_to(channel, reason="Temporary room created")
            except (discord.Forbidden, discord.HTTPException):
                await self._delete_room(channel)  # Preserve record on failed delete.
                return
        finally:
            self._pending_creation.discard(channel.id)
        # One panel per new room. No reposts when people join, leave, or the bot restarts.
        await self._send_panel(channel)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        await self.bot.db.remove_room(channel.id)

    # ---- the one place where room actions happen -----------------------
    async def resolve_room(self, member, *, need_owner=True):
        """Return (channel, room_row, error). The member must be inside a temp room (and own it)."""
        voice = getattr(member, "voice", None)
        channel = voice.channel if voice else None
        room = await self.bot.db.get_room(channel.id) if channel else None
        if room is None:
            return None, None, "Join your temporary voice room first."
        if need_owner and room["owner_id"] != member.id:
            return None, None, (f"Only the room owner (<@{room['owner_id']}>) can do that. "
                                "If they left, use the claim command.")
        return channel, room, None

    async def do(self, actor, key, arg=None):
        """Run a room action for `actor` and return the text to show them."""
        channel, room, error = await self.resolve_room(actor, need_owner=key not in ("claim", "info"))
        if error:
            return error
        guild = channel.guild
        try:
            if key == "info":
                return (f"**{channel.name}** • owner <@{room['owner_id']}> • {len(channel.members)} in room • "
                        f"limit {channel.user_limit or 'unlimited'}")
            if key == "claim":
                owner = guild.get_member(room["owner_id"])
                if owner is not None and owner in channel.members:
                    return "The owner is still in the room."
                await self._set_owner(channel, owner, actor)
                return "👑 You now own this room."
            if key == "rename":
                name = truncate(str(arg).strip(), 100, "")
                if not name:
                    return "Give the room a name."
                await channel.edit(name=name, reason="Room owner rename")
                return f"✏️ Renamed to **{name}**."
            if key == "limit":
                await channel.edit(user_limit=int(arg), reason="Room owner limit")
                return f"👥 Limit set to **{int(arg) or 'unlimited'}**."
            if key == "lock":
                await update_overwrite(channel, guild.default_role, connect=False)
                return "🔒 Room locked. Use ✅ / ➕ to let people in."
            if key == "unlock":
                await update_overwrite(channel, guild.default_role, connect=None)
                return "🔓 Room unlocked."
            if key == "hide":
                await update_overwrite(channel, guild.default_role, view_channel=False)
                return "🙈 Room hidden."
            if key == "show":
                await update_overwrite(channel, guild.default_role, view_channel=None)
                return "👁️ Room visible."
            if key == "region":
                await channel.edit(rtc_region=arg, reason="Room owner region")
                return f"🌐 Region set to **{arg or 'automatic'}**."
            if key == "delete":
                deleted = await self._delete_room(channel)
                return ("🗑️ Room deleted." if deleted else
                        "I couldn't delete the room. Check my **Manage Channels** permission.")
            if key in NEEDS_MEMBER:
                return await self._member_action(channel, actor, key, arg)
        except (discord.Forbidden, discord.HTTPException) as exc:
            log.warning("Room action %s failed: %s", key, exc)
            return "I couldn't do that. Check that I have **Manage Channels**, **Manage Roles** and **Move Members**."
        return "Unknown action."

    async def _member_action(self, channel, owner, key, target):
        if not isinstance(target, discord.Member):
            return "Pick a member."
        if target.id == owner.id:
            return "You can't do that to yourself."
        if target.bot:
            return "That doesn't work on bots."
        in_room = target in channel.members
        protected = target.guild_permissions.administrator or target.guild_permissions.manage_guild
        if key == "kick":
            if protected:
                return "You can't kick a server admin."
            if not in_room:
                return "They aren't in your room."
            await target.move_to(None, reason=f"Removed from room by {owner}")
            return f"🔨 {target.mention} was removed from the room."
        if key == "block":
            if protected:
                return "You can't block a server admin."
            await update_overwrite(channel, target, connect=False)
            if in_room:
                await target.move_to(None, reason=f"Blocked from room by {owner}")
            return f"⛔ {target.mention} is blocked from this room."
        if key == "allow":
            await update_overwrite(channel, target, connect=True, view_channel=True)
            return f"✅ {target.mention} can join this room."
        if key == "mute":
            if protected:
                return "You can't mute a server admin."
            await update_overwrite(channel, target, speak=False)
            return f"🔇 {target.mention} is muted in this room."
        if key == "unmute":
            await update_overwrite(channel, target, speak=None)
            return f"🎙️ {target.mention} can speak again."
        if key == "transfer":
            if not in_room:
                return "Pick someone who is in your room."
            await self._set_owner(channel, owner, target)
            return f"👑 {target.mention} now owns this room."
        if key == "invite":
            await update_overwrite(channel, target, connect=True, view_channel=True)
            try:
                await target.send(f"{owner.display_name} invited you to their voice room: {channel.jump_url}")
                return f"➕ Invited {target.mention} (they got a message)."
            except (discord.Forbidden, discord.HTTPException):
                return f"➕ {target.mention} can join now (I couldn't DM them)."
        return "Unknown action."

    async def _set_owner(self, channel, old_owner, new_owner):
        if old_owner is not None:
            await update_overwrite(channel, old_owner, manage_channels=None, move_members=None)
        await update_overwrite(channel, new_owner, view_channel=True, connect=True, speak=True,
                               manage_channels=True, move_members=True)
        await self.bot.db.set_room_owner(channel.id, new_owner.id)

    # ---- text/slash commands: /vc lock  and  !vc lock --------------------
    @commands.hybrid_group(name="vc", aliases=["room"], description="Control your temporary voice room",
                           invoke_without_command=True, fallback="info")
    @commands.guild_only()
    async def vc(self, ctx: commands.Context):
        await say(ctx, await self.do(ctx.author, "info"), ephemeral=True)

    @vc.command(name="lock", description="Nobody new can join")
    async def vc_lock(self, ctx: commands.Context):
        await say(ctx, await self.do(ctx.author, "lock"), ephemeral=True)

    @vc.command(name="unlock", description="Let everyone join again")
    async def vc_unlock(self, ctx: commands.Context):
        await say(ctx, await self.do(ctx.author, "unlock"), ephemeral=True)

    @vc.command(name="hide", description="Hide the room from people outside it")
    async def vc_hide(self, ctx: commands.Context):
        await say(ctx, await self.do(ctx.author, "hide"), ephemeral=True)

    @vc.command(name="show", description="Make the room visible again")
    async def vc_show(self, ctx: commands.Context):
        await say(ctx, await self.do(ctx.author, "show"), ephemeral=True)

    @vc.command(name="name", description="Rename your room")
    @app_commands.describe(name="The new room name")
    async def vc_name(self, ctx: commands.Context, *, name: str):
        await say(ctx, await self.do(ctx.author, "rename", name), ephemeral=True)

    @vc.command(name="limit", description="Set the user limit (0 = unlimited)")
    @app_commands.describe(limit="0 to 99")
    async def vc_limit(self, ctx: commands.Context, limit: commands.Range[int, 0, 99]):
        await say(ctx, await self.do(ctx.author, "limit", limit), ephemeral=True)

    @vc.command(name="claim", description="Take over a room whose owner left")
    async def vc_claim(self, ctx: commands.Context):
        await say(ctx, await self.do(ctx.author, "claim"), ephemeral=True)

    @vc.command(name="kick", description="Remove someone from your room")
    async def vc_kick(self, ctx: commands.Context, member: discord.Member):
        await say(ctx, await self.do(ctx.author, "kick", member), ephemeral=True)

    @vc.command(name="block", description="Remove someone and stop them rejoining")
    async def vc_block(self, ctx: commands.Context, member: discord.Member):
        await say(ctx, await self.do(ctx.author, "block", member), ephemeral=True)

    @vc.command(name="allow", description="Let someone join (even when locked or hidden)")
    async def vc_allow(self, ctx: commands.Context, member: discord.Member):
        await say(ctx, await self.do(ctx.author, "allow", member), ephemeral=True)

    @vc.command(name="mute", description="Mute someone in your room")
    async def vc_mute(self, ctx: commands.Context, member: discord.Member):
        await say(ctx, await self.do(ctx.author, "mute", member), ephemeral=True)

    @vc.command(name="unmute", description="Unmute someone in your room")
    async def vc_unmute(self, ctx: commands.Context, member: discord.Member):
        await say(ctx, await self.do(ctx.author, "unmute", member), ephemeral=True)

    @vc.command(name="transfer", description="Give your room to someone inside it")
    async def vc_transfer(self, ctx: commands.Context, member: discord.Member):
        await say(ctx, await self.do(ctx.author, "transfer", member), ephemeral=True)

    @vc.command(name="invite", description="Invite someone to your room")
    async def vc_invite(self, ctx: commands.Context, member: discord.Member):
        await say(ctx, await self.do(ctx.author, "invite", member), ephemeral=True)

    # ---- /tempvoice (admin) -------------------------------------------
    @tempvoice.command(name="setup", description="Choose the 'join to create' voice channel")
    @app_commands.describe(hub="Members who join this voice channel get their own room",
                           category="Where rooms are created (default: same category as the hub)",
                           name="Room name; {user} is the owner's name",
                           default_limit="User limit for new rooms (0 = unlimited)")
    async def tempvoice_setup(self, interaction: discord.Interaction, hub: discord.VoiceChannel,
                              category: Optional[discord.CategoryChannel] = None, name: Optional[str] = None,
                              default_limit: Optional[app_commands.Range[int, 0, 99]] = None):
        me = interaction.guild.me
        target = category or hub.category
        perms = target.permissions_for(me) if target else me.guild_permissions
        if not (perms.manage_channels and perms.move_members):
            return await respond(interaction, "I need **Manage Channels** and **Move Members** there.")
        cfg = (await self.bot.db.get_settings(interaction.guild_id))["tempvoice"]
        cfg["hub_id"] = hub.id
        if category is not None:
            cfg["category_id"] = category.id
        if name:
            cfg["name"] = truncate(name, 90, "")
        if default_limit is not None:
            cfg["default_limit"] = default_limit
        await self.bot.db.save_settings(interaction.guild_id)
        await respond(interaction, f"Join {hub.mention} to get a room. The Arabic control panel is posted **automatically in the new room's voice chat**.")

    @tempvoice.command(name="panel", description="Post or restore the control panel in text or voice chat")
    @app_commands.describe(channel="Where to post it (text channel or voice channel; default: here)")
    async def tempvoice_panel(self, interaction: discord.Interaction,
                              channel: Optional[Union[discord.TextChannel, discord.VoiceChannel]] = None):
        channel = channel or interaction.channel
        if not isinstance(channel, (discord.TextChannel, discord.VoiceChannel)):
            return await respond(interaction, "Choose a text or voice channel.")
        message = await self._send_panel(channel, avoid_duplicate=isinstance(channel, discord.VoiceChannel))
        if message is None:
            return await respond(interaction, "I couldn't post the panel. Check **View Channel**, **Send Messages**, and (for the image) **Embed Links** + **Attach Files**.")
        await respond(interaction, f"Panel is available in {channel.mention}.")

    @tempvoice.command(name="cleanup", description="Remove a stuck empty temporary voice room")
    @app_commands.describe(channel="Empty temporary voice channel that failed to delete")
    async def tempvoice_cleanup(self, interaction: discord.Interaction, channel: discord.VoiceChannel):
        guild = interaction.guild
        cfg = (await self.bot.db.get_settings(guild.id))["tempvoice"]
        stored = await self.bot.db.get_room(channel.id)
        hub = guild.get_channel(cfg["hub_id"]) if cfg["hub_id"] else None
        temp_category_id = cfg["category_id"] or (hub.category_id if hub else None)
        if channel.id == cfg["hub_id"]:
            return await respond(interaction, "The create-room hub can't be removed by cleanup.")
        if stored is None and (temp_category_id is None or channel.category_id != temp_category_id):
            return await respond(interaction, "This channel is not a tracked Temp Voice room or in its configured category.")
        if self._has_human_members(channel):
            return await respond(interaction, "That room still has members. Cleanup only works on empty rooms.")
        deleted = await self._delete_room(channel)
        if deleted:
            await respond(interaction, "✅ Empty room deleted.")
        else:
            await respond(interaction, "Couldn't delete that room. Check my **Manage Channels** permission; I will retry tracked rooms automatically.")

    @tempvoice.command(name="off", description="Disable join-to-create")
    async def tempvoice_off(self, interaction: discord.Interaction):
        cfg = (await self.bot.db.get_settings(interaction.guild_id))["tempvoice"]
        cfg["hub_id"] = None
        await self.bot.db.save_settings(interaction.guild_id)
        await respond(interaction, "Join-to-create is **off**. Existing rooms disappear when they empty.")


async def setup(bot):
    await bot.add_cog(TempVoice(bot))
