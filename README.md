# 🧹 Discord Cleanup Bot

A Discord bot built with `discord.py` to automate the deletion of messages from users who have left the server, with features for logging, rate-limit handling, and admin control.

## Features

- ✅ Delete messages from users no longer in the server
- 📁 Save and load log/target channel settings
- 🕒 Rate limit detection and backoff
- 🗑️ Optional message ID logging (to console)
- ⚙️ Admin-only setup commands
- 🔒 Whitelist support for exempt users
- 🧠 Intelligent pacing to avoid Discord bans

## Requirements

- Python 3.8+
- `discord.py` v2.x

Install with:

```bash
pip install -U discord.py
```

## Setup

1. Clone the repo or copy the script.
2. Replace the bot `TOKEN` at the top of the script (use environment variables or `.env` for production).
3. Run the bot:

```bash
python bot.py
```

## Commands

### `!setchannels #log-channel #target-channel`
Sets the channels used for logging and cleanup.

### `!clean_left`
Deletes messages in the target channel from users who are no longer in the server.

> ⚠️ Requires **Administrator** permissions.

## Configuration

You can adjust:

- `WHITELIST`: Add user IDs you want to exempt from deletion.
- `BASE_DELETE_DELAY`: Controls pacing between deletions (avoid lowering too far to prevent bans).

Channel IDs are saved to `channel_settings.json`.

## Logging

- Logs errors and events to both console and `bot_log.txt`
- Deleted message IDs are printed to console only

## Rate Limit Handling

The bot automatically detects and backs off if Discord responds with `429 Too Many Requests`.

## Known Limitations

- Messages older than 14 days can't be batch-deleted due to Discord API restrictions.
- You must ensure the bot has the following permissions:
  - View Channel
  - Read Message History
  - Manage Messages

## License

MIT License

---

Made with ❤️ using `discord.py`
