# 🧹 Discord Cleanup Bot

A powerful moderation bot for Discord that automatically deletes messages from users who have left the server. Features logging, intelligent rate-limit handling, console tracking of deleted message IDs, and admin-only configuration.

## 🚀 Features

- 🗑️ **Clean messages** from users no longer in the server
- 🧠 **Rate limit aware** with automatic backoff logic
- 🧾 **Logs deleted message IDs** to console (not to file or Discord)
- 📁 Saves and loads log/target channel settings between restarts
- ⚙️ Admin-only commands for setup and management
- 🔒 Whitelist support to protect specific users
- 🔍 Includes a basic `!ping` health check command

---

## 📦 Requirements

- Python 3.8+
- `discord.py` v2.x

Install dependencies:

```bash
pip install -U discord.py
```

---

## 🛠 Setup

1. Clone this repo or copy the script.
2. Replace the bot token in the script (for production, use environment variables instead).
3. Run the bot:

```bash
python bot.py
```

---

## 🔐 Recommended Security Setup

Use a `.env` file to store your token:

```env
DISCORD_TOKEN=your_token_here
```

And replace in script:

```python
from dotenv import load_dotenv
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
```

Add `.env` to your `.gitignore`.

---

## 📜 Commands

### `!setchannels #log-channel #target-channel`
Set the channels for logging events and cleaning messages.

### `!clean_left`
Delete messages from users no longer in the server (excluding whitelisted users and bots). Logs estimated runtime and result to both console and Discord.

### `!ping`
Simple heartbeat check to confirm the bot is online.

---

## ⚙️ Configuration

- `WHITELIST`: Add user IDs of exempt users.
- `BASE_DELETE_DELAY`: Controls pacing between deletions. Default is `0.5s` (recommended for safety).
- Channel IDs and settings are saved in `channel_settings.json`.

---

## 📈 Logging

- Message deletions are logged in the **console only**, using Python's `logging` module.
- Additional bot activity and errors are logged to `bot_log.txt`.

---

## ⏳ Rate Limit Handling

The bot detects `429 Too Many Requests` responses and auto-adjusts the deletion delay. This helps prevent Discord API bans.

---

## 🛑 Permissions Required

Ensure the bot has the following permissions **in the target channel**:

- View Channel
- Read Message History
- Manage Messages

---

## ⚠️ Limitations

- Discord does not allow deletion of messages older than 14 days via bulk methods.
- All messages are deleted one-by-one to maintain precision and avoid batch constraints.

---

## 🧪 Example `.gitignore`

```gitignore
__pycache__/
.env
bot_log.txt
channel_settings.json
```

---

## 📄 License

MIT License

---

Made with 🧼 by a lazy piece of bacon!
