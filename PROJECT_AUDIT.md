# dor — TempVoice empty-room cleanup patch

## Changes
- Fixed the core bug: channel IDs are removed from SQLite only **after Discord successfully deletes the voice channel** or confirms it was already deleted.
- Added an event-triggered delayed recheck so a room is removed after its last human member leaves, without deleting a room someone rejoined.
- Added an automatic cleanup sweep every 60 seconds (including after a restart) to recover missed voice events and transient delete failures.
- The bot no longer counts other bots as human occupants of temporary rooms.
- Tracks rooms during creation to avoid race conditions with the sweep.
- Added `/tempvoice cleanup channel:<channel>` for administrators to remove an empty orphan room left by an older bot version, while refusing the hub channel and occupied rooms.
- Kept the automatic Arabic TempVoice panel, 15 AI icons and button IDs unchanged.

## Testing
- 64 offline tests passed, including 12 additional TempVoice cleanup checks.
- Live Discord channel permissions and event delivery require testing after deploying.
- If deletion fails due to missing Manage Channels, the bot logs a warning and retries; it cannot override Discord permissions.
