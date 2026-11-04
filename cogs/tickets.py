import asyncio
import io
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils.helpers import (GREEN, RED, YELLOW, admin_group, is_manager, make_embed, respond, say, send_log,
                           update_overwrite)
from utils.text import truncate

log = logging.getLogger("mg3.tickets")

TRANSCRIPT_LIMIT = 2000


def is_staff(member, settings):
    if is_manager(member):
        return True
    staff = set(settings["tickets"]["staff_role_ids"])
    return any(role.id in staff for role in member.roles)


class PanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Open a ticket", emoji="🎫", style=discord.ButtonStyle.primary,
                       custom_id="mg3:ticket:open")
    async def open_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog("Tickets")
        if cog is None:
            return await respond(interaction, "Tickets are unavailable right now.")
        await cog.open_ticket(interaction)


class ConfirmCloseView(discord.ui.View):
    def __init__(self, cog, closer_id):
        super().__init__(timeout=30)
        self.cog = cog
        self.closer_id = closer_id

    @discord.ui.button(label="Yes, close it", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.closer_id:
            return await respond(interaction, "This confirmation isn't for you.")
        await interaction.response.edit_message(content="Closing this ticket…", view=None)
        self.stop()
        await self.cog.close_ticket(interaction.channel, interaction.user)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Cancelled.", view=None)
        self.stop()


class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim", emoji="🙋", style=discord.ButtonStyle.success, custom_id="mg3:ticket:claim")
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog("Tickets")
        if cog is not None:
            await cog.claim(interaction)

    @discord.ui.button(label="Close", emoji="🔒", style=discord.ButtonStyle.danger, custom_id="mg3:ticket:close")
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog("Tickets")
        if cog is not None:
            await cog.request_close(interaction)


class Tickets(commands.Cog):
    tickets = admin_group("tickets", "Ticket system settings")

    def __init__(self, bot):
        self.bot = bot
        self._locks = {}

    async def cog_load(self):
        # Persistent views: the buttons keep working after a restart.
        self.bot.add_view(PanelView())
        self.bot.add_view(TicketControlView())

    # ---- opening ------------------------------------------------------
    async def open_ticket(self, interaction: discord.Interaction):
        guild, member = interaction.guild, interaction.user
        if guild is None:
            return
        await interaction.response.defer(ephemeral=True)
        settings = await self.bot.db.get_settings(guild.id)
        cfg = settings["tickets"]
        lock = self._locks.setdefault(guild.id, asyncio.Lock())
        async with lock:
            alive = []
            for ticket in await self.bot.db.open_tickets_for(guild.id, member.id):
                if guild.get_channel(ticket["channel_id"]) is not None:
                    alive.append(ticket)
                else:
                    await self.bot.db.close_ticket(ticket["channel_id"])
            if len(alive) >= cfg["max_open"]:
                existing = guild.get_channel(alive[0]["channel_id"])
                return await interaction.followup.send(f"You already have an open ticket: {existing.mention}", ephemeral=True)

            allow = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True,
                                                attach_files=True, embed_links=True)
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                member: allow,
                guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True,
                                                      manage_messages=True, embed_links=True,
                                                      read_message_history=True, attach_files=True),
            }
            staff_roles = []
            for role_id in cfg["staff_role_ids"]:
                role = guild.get_role(role_id)
                if role is not None:
                    staff_roles.append(role)
                    overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                                                   read_message_history=True, attach_files=True,
                                                                   embed_links=True, manage_messages=True)
            category = guild.get_channel(cfg["category_id"]) if cfg["category_id"] else None
            if not isinstance(category, discord.CategoryChannel):
                category = None
            number = await self.bot.db.next_ticket_number(guild.id)
            try:
                channel = await guild.create_text_channel(
                    f"ticket-{number:04d}", category=category, overwrites=overwrites,
                    topic=f"Ticket #{number} • opened by {member} ({member.id})",
                    reason=f"Ticket opened by {member}")
            except (discord.Forbidden, discord.HTTPException):
                return await interaction.followup.send(
                    "I couldn't create the ticket channel. Ask an admin to give me **Manage Channels** and **Manage Roles**.",
                    ephemeral=True)
            await self.bot.db.create_ticket(guild.id, number, channel.id, member.id)

        embed = make_embed(f"Ticket #{number}", (
            f"Hi {member.mention}, a staff member will be with you shortly.\n"
            "Please describe your problem in as much detail as you can."), color=GREEN)
        pings = " ".join(r.mention for r in staff_roles)
        await channel.send(
            content=f"{member.mention} {pings}".strip(), embed=embed, view=TicketControlView(),
            allowed_mentions=discord.AllowedMentions(users=[member], roles=staff_roles))
        await interaction.followup.send(f"Your ticket is ready: {channel.mention}", ephemeral=True)
        log_embed = make_embed("Ticket opened", f"#{number} • {channel.mention}\nBy {member.mention}", color=GREEN)
        await send_log(self.bot, guild, "tickets", embed=log_embed)

    # ---- claim / close ------------------------------------------------
    async def claim(self, interaction: discord.Interaction):
        settings = await self.bot.db.get_settings(interaction.guild_id)
        ticket = await self.bot.db.get_ticket(interaction.channel_id)
        if ticket is None or ticket["status"] != "open":
            return await respond(interaction, "This isn't an open ticket.")
        if not is_staff(interaction.user, settings):
            return await respond(interaction, "Only staff can claim tickets.")
        if not await self.bot.db.claim_ticket(interaction.channel_id, interaction.user.id):
            return await respond(interaction, "This ticket has already been claimed.")
        await interaction.response.send_message(f"🙋 {interaction.user.mention} claimed this ticket.",
                                                allowed_mentions=discord.AllowedMentions.none())

    async def request_close(self, interaction: discord.Interaction):
        settings = await self.bot.db.get_settings(interaction.guild_id)
        ticket = await self.bot.db.get_ticket(interaction.channel_id)
        if ticket is None or ticket["status"] != "open":
            return await respond(interaction, "This isn't an open ticket.")
        if interaction.user.id != ticket["user_id"] and not is_staff(interaction.user, settings):
            return await respond(interaction, "Only the ticket owner or staff can close it.")
        await interaction.response.send_message("Close this ticket? A transcript will be saved.",
                                                view=ConfirmCloseView(self, interaction.user.id), ephemeral=True)

    async def close_ticket(self, channel: discord.TextChannel, closer: discord.Member):
        ticket = await self.bot.db.get_ticket(channel.id)
        if ticket is None or not await self.bot.db.close_ticket(channel.id):
            return
        guild = channel.guild

        lines = [f"Transcript of #{channel.name} (ticket #{ticket['number']})", ""]
        try:
            async for msg in channel.history(limit=TRANSCRIPT_LIMIT, oldest_first=True):
                text = msg.content or ""
                for attachment in msg.attachments:
                    text += f" [attachment: {attachment.url}]"
                for emb in msg.embeds:
                    if emb.title or emb.description:
                        text += f" [embed: {emb.title or ''} {emb.description or ''}]"
                stamp = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
                lines.append(f"[{stamp} UTC] {msg.author} ({msg.author.id}): {text}")
        except discord.HTTPException:
            lines.append("(transcript is incomplete: could not read the full history)")
        data = "\n".join(lines).encode("utf-8")

        opener = guild.get_member(ticket["user_id"])
        embed = make_embed("Ticket closed", color=RED)
        embed.add_field(name="Ticket", value=f"#{ticket['number']}")
        embed.add_field(name="Opened by", value=opener.mention if opener else f"<@{ticket['user_id']}>")
        embed.add_field(name="Closed by", value=closer.mention)
        if ticket["claimed_by"]:
            embed.add_field(name="Claimed by", value=f"<@{ticket['claimed_by']}>")
        filename = f"ticket-{ticket['number']:04d}.txt"
        delivered = await send_log(self.bot, guild, "tickets", embed=embed,
                                   file=discord.File(io.BytesIO(data), filename=filename))
        if not delivered:
            try:
                await closer.send("No tickets log channel is set, so here is the transcript:",
                                  file=discord.File(io.BytesIO(data), filename=filename))
            except (discord.Forbidden, discord.HTTPException):
                pass

        try:
            await channel.send("🔒 Closing this ticket in 5 seconds…")
            await asyncio.sleep(5)
            await channel.delete(reason=f"Ticket closed by {closer}")
        except (discord.Forbidden, discord.HTTPException):
            log.warning("Could not delete ticket channel %s", channel.id)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        # A ticket channel deleted by hand shouldn't count as "open" forever.
        await self.bot.db.close_ticket(channel.id)

    # ---- adduser / removeuser (work as /adduser and !adduser) ----------
    @commands.hybrid_command(name="adduser", description="Add someone to this ticket")
    @commands.guild_only()
    async def adduser(self, ctx: commands.Context, member: discord.Member):
        if not await self._staff_in_ticket(ctx):
            return
        await update_overwrite(ctx.channel, member, view_channel=True, send_messages=True,
                               read_message_history=True, attach_files=True)
        await say(ctx, f"Added {member.mention}.", allowed_mentions=discord.AllowedMentions(users=[member]))

    @commands.hybrid_command(name="removeuser", description="Remove someone from this ticket")
    @commands.guild_only()
    async def removeuser(self, ctx: commands.Context, member: discord.Member):
        if not await self._staff_in_ticket(ctx):
            return
        ticket = await self.bot.db.get_ticket(ctx.channel.id)
        if member.id == ticket["user_id"]:
            return await say(ctx, "You can't remove the person who opened the ticket.", ephemeral=True)
        await ctx.channel.set_permissions(member, overwrite=None)  # drops their override entirely
        await say(ctx, f"Removed {member.mention}.", allowed_mentions=discord.AllowedMentions.none())

    async def _staff_in_ticket(self, ctx):
        settings = await self.bot.db.get_settings(ctx.guild.id)
        ticket = await self.bot.db.get_ticket(ctx.channel.id)
        if ticket is None or ticket["status"] != "open":
            await say(ctx, "Use this inside an open ticket.", ephemeral=True)
            return False
        if not is_staff(ctx.author, settings):
            await say(ctx, "Only staff can do that.", ephemeral=True)
            return False
        return True

    # ---- /tickets (admin) ---------------------------------------------
    @tickets.command(name="config", description="Set the ticket category and per-user limit")
    @app_commands.describe(category="Category where new ticket channels are created",
                           max_open="How many open tickets one person may have (1-5)")
    async def tickets_config(self, interaction: discord.Interaction, category: Optional[discord.CategoryChannel] = None,
                             max_open: Optional[app_commands.Range[int, 1, 5]] = None):
        settings = await self.bot.db.get_settings(interaction.guild_id)
        cfg = settings["tickets"]
        if category is not None:
            cfg["category_id"] = category.id
        if max_open is not None:
            cfg["max_open"] = max_open
        await self.bot.db.save_settings(interaction.guild_id)
        cat = f"<#{cfg['category_id']}>" if cfg["category_id"] else "none (top of the channel list)"
        await respond(interaction, f"Category: {cat}\nMax open per person: **{cfg['max_open']}**")

    @tickets.command(name="staff_add", description="Let a role see and answer tickets")
    async def tickets_staff_add(self, interaction: discord.Interaction, role: discord.Role):
        settings = await self.bot.db.get_settings(interaction.guild_id)
        ids = settings["tickets"]["staff_role_ids"]
        if role.is_default():
            return await respond(interaction, "Pick a real staff role, not @everyone.")
        if role.id not in ids:
            ids.append(role.id)
            await self.bot.db.save_settings(interaction.guild_id)
        await respond(interaction, f"{role.mention} is now a ticket staff role.",
                      allowed_mentions=discord.AllowedMentions.none())

    @tickets.command(name="staff_remove", description="Remove a ticket staff role")
    async def tickets_staff_remove(self, interaction: discord.Interaction, role: discord.Role):
        settings = await self.bot.db.get_settings(interaction.guild_id)
        ids = settings["tickets"]["staff_role_ids"]
        if role.id in ids:
            ids.remove(role.id)
            await self.bot.db.save_settings(interaction.guild_id)
        await respond(interaction, "Removed.")

    @tickets.command(name="panel", description="Post the 'Open a ticket' button in a channel")
    @app_commands.describe(channel="Where to post it (default: here)", title="Panel title", description="Panel text")
    async def tickets_panel(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None,
                            title: str = "Need help?",
                            description: str = "Press the button below to open a private ticket with our staff."):
        channel = channel or interaction.channel
        perms = channel.permissions_for(interaction.guild.me)
        if not (perms.send_messages and perms.embed_links):
            return await respond(interaction, f"I need **Send Messages** and **Embed Links** in {channel.mention}.")
        settings = await self.bot.db.get_settings(interaction.guild_id)
        if not settings["tickets"]["staff_role_ids"]:
            warn = "\n⚠️ No staff role is set yet — only admins will see tickets. Use `/tickets staff_add`."
        else:
            warn = ""
        embed = make_embed(truncate(title, 200), truncate(description, 1500), color=YELLOW)
        await channel.send(embed=embed, view=PanelView())
        await respond(interaction, f"Panel posted in {channel.mention}.{warn}")

    @tickets.command(name="show", description="Show the ticket settings")
    async def tickets_show(self, interaction: discord.Interaction):
        cfg = (await self.bot.db.get_settings(interaction.guild_id))["tickets"]
        roles = " ".join(f"<@&{i}>" for i in cfg["staff_role_ids"]) or "none"
        cat = f"<#{cfg['category_id']}>" if cfg["category_id"] else "none"
        await respond(interaction, embed=make_embed("Ticket settings",
                      f"**Category:** {cat}\n**Staff roles:** {roles}\n**Max open per person:** {cfg['max_open']}"),
                      allowed_mentions=discord.AllowedMentions.none())


async def setup(bot):
    await bot.add_cog(Tickets(bot))
