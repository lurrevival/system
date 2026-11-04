"""Pure logic behind `/help setup`: what is already configured and how far along the server is."""
# (Discord permission attribute, name shown to the admin)
REQUIRED_PERMISSIONS = [
    ("view_channel", "View Channels"), ("send_messages", "Send Messages"), ("embed_links", "Embed Links"),
    ("attach_files", "Attach Files"), ("read_message_history", "Read Message History"),
    ("manage_messages", "Manage Messages"), ("manage_channels", "Manage Channels"),
    ("manage_roles", "Manage Roles"), ("kick_members", "Kick Members"),
    ("moderate_members", "Moderate Members"), ("ban_members", "Ban Members"), ("move_members", "Move Members"),
    ("view_audit_log", "View Audit Log"), ("connect", "Connect"), ("speak", "Speak"),
]


def missing_permissions(perms):
    """Names of the permissions the bot lacks (an Administrator bot lacks nothing)."""
    if getattr(perms, "administrator", False):
        return []
    return [label for attr, label in REQUIRED_PERMISSIONS if not getattr(perms, attr, False)]


def progress_items(settings, missing_perms, bad_autoroles=0):
    """List of (key, label, done, how to fix it) for the whole setup."""
    logs = settings.get("log_channels", {})
    welcome = settings.get("welcome", {})
    autorole = settings.get("autorole", {})
    tickets = settings.get("tickets", {})
    security = settings.get("security", {})
    leveling = settings.get("leveling", {})
    tempvoice = settings.get("tempvoice", {})
    has_autorole = bool(autorole.get("members") or autorole.get("bots"))
    return [
        ("perms", "Bot permissions", not missing_perms,
         "Re-invite the bot with the permissions listed on page 2"),
        ("logs", "Log channel", bool(logs.get("default")),
         "`/logs setup channel:#logs`"),
        ("welcome", "Welcome message", bool(welcome.get("enabled") and welcome.get("channel_id")),
         "`/welcome set channel:#welcome`"),
        ("autorole", "Auto role", has_autorole and not bad_autoroles,
         "`/autorole add role:@Member` (the role must be below the bot's role)"),
        ("tickets_category", "Ticket category", bool(tickets.get("category_id")),
         "`/tickets config category:#Tickets`"),
        ("tickets_staff", "Ticket staff role", bool(tickets.get("staff_role_ids")),
         "`/tickets staff_add role:@Support`"),
        ("automod", "AutoMod", bool(security.get("enabled")),
         "`/automod toggle rule:master enabled:True`"),
        ("leveling", "Leveling", bool(leveling.get("enabled")),
         "`/levels toggle enabled:True`"),
        ("tempvoice", "Temp voice (join to create)", bool(tempvoice.get("hub_id")),
         "`/tempvoice setup hub:#create-room`"),
    ]


def percent(items):
    return round(100 * sum(1 for item in items if item[2]) / len(items)) if items else 0


def bar(pct, width=10):
    filled = round(width * pct / 100)
    return "▰" * filled + "▱" * (width - filled)
