import discord
from discord.ext import commands
import asyncio
import json
import logging
import os
import random

# Bot token & admin channel (you can also load from env for safety)
TOKEN = 'replace'  # Replace with a secure method in production
ADMIN_CHANNEL_ID = replace # ID for admin notifications

# Intents setup for accessing members, messages, etc.
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
intents.messages = True

bot = commands.Bot(command_prefix='!', intents=intents)

# Logging config (console + file)
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger('discord')
fmt = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
ch = logging.StreamHandler(); ch.setFormatter(fmt); logger.addHandler(ch)
fh = logging.FileHandler('bot_log.txt'); fh.setFormatter(fmt); logger.addHandler(fh)

# File to persist log/target channel IDs between restarts
SETTINGS_FILE = 'channel_settings.json'

# Delay between deletions to avoid hitting rate limits
BASE_DELETE_DELAY = 0.5
delete_delay = BASE_DELETE_DELAY
last_rate_limit = 0.0

# User IDs exempt from deletion
WHITELIST = {1257026449873960980}  # Replace with actual whitelisted user IDs

# Log and cleanup target channels
log_channel = None
target_channel = None

# Save selected channels to disk
def save_settings():
    with open(SETTINGS_FILE, 'w') as f:
        json.dump({
            'log_channel_id': log_channel.id if log_channel else None,
            'target_channel_id': target_channel.id if target_channel else None
        }, f)

# Load channel settings from disk
def load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, 'r') as f:
            d = json.load(f)
            return d.get('log_channel_id'), d.get('target_channel_id')
    return None, None

# Send message to admin channel if configured
async def send_admin(msg):
    ch = bot.get_channel(ADMIN_CHANNEL_ID)
    if ch:
        await ch.send(msg)

# Log and report errors to admin and log channel
async def log_error(exc, fn):
    logger.error(f"Error in {fn}: {exc}")
    if log_channel:
        e = discord.Embed(title="Error", description=f"❌ `{fn}`: {exc}", color=discord.Color.red())
        await log_channel.send(embed=e)
    await send_admin(f"❌ Error in `{fn}`: {exc}")

# Triggered once bot connects
@bot.event
async def on_ready():
    global log_channel, target_channel
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    lc, tc = load_settings()
    if lc and tc:
        log_channel = bot.get_channel(lc)
        target_channel = bot.get_channel(tc)
        if log_channel and target_channel:
            print(f"Loaded channels: log={log_channel.name}, target={target_channel.name}")
        else:
            await send_admin("❌ Failed to resolve saved channels; please re-run !setchannels.")
    else:
        await send_admin("⚠️ No saved channels—run !setchannels.")

# Admin command to set channels used by the bot
@bot.command()
@commands.has_permissions(administrator=True)
async def setchannels(ctx, log_ch: discord.TextChannel, target_ch: discord.TextChannel):
    global log_channel, target_channel
    if not log_ch or not target_ch:
        return await ctx.send(embed=discord.Embed(
            title="Error", description="Provide valid channels.", color=discord.Color.red()
        ))
    log_channel, target_channel = log_ch, target_ch
    save_settings()
    await ctx.send(embed=discord.Embed(
        title="Settings Updated",
        description=f"✅ Log: {log_channel.mention}\n✅ Target: {target_channel.mention}",
        color=discord.Color.green()
    ))

# Gateway rate limit detection (rare but logged just in case)
@bot.event
async def on_socket_response(data):
    global last_rate_limit, delete_delay
    if isinstance(data, dict) and data.get('t') == 'RATE_LIMIT':
        retry = data.get('d', {}).get('retry_after', 0.0)
        last_rate_limit = max(last_rate_limit, retry)
        delete_delay = max(delete_delay, last_rate_limit + 0.1)
        logger.warning(f"Gateway rate limit: retry_after={retry}s -> delete_delay={delete_delay:.1f}s")
        if log_channel:
            await log_channel.send(embed=discord.Embed(
                title="Rate Limit Detected",
                description=f"gateway retry_after={retry}s",
                color=discord.Color.orange()
            ))
        await send_admin(f"⚠️ Rate limit (gateway) retry_after={retry}s")


# Deletes a message and adjusts delay if rate-limited
async def delete_with_backoff(msg):
    global last_rate_limit, delete_delay
    try:
        await msg.delete()
        logger.info(f"Deleted message ID: {msg.id}")  # <-- Log the deleted message ID here
        # if log_channel:
        #     await log_channel.send(embed=discord.Embed(
        #         title="Message Deleted",
        #         description=f"🗑️ Deleted message ID: `{msg.id}`",
        #         color=discord.Color.dark_grey()
        #     ))
    except discord.HTTPException as e:
        if e.status == 429:
            retry = getattr(e, 'retry_after', last_rate_limit)
            last_rate_limit = max(last_rate_limit, retry)
            delete_delay = max(delete_delay, last_rate_limit + 0.1)
            logger.warning(f"HTTP 429: retry={retry}s -> delete_delay={delete_delay:.1f}s")
            if log_channel:
                await log_channel.send(embed=discord.Embed(
                    title="Rate Limit Backoff",
                    description=f"waiting {delete_delay:.1f}s",
                    color=discord.Color.orange()
                ))
            await asyncio.sleep(delete_delay)
            return await delete_with_backoff(msg)
        else:
            raise  # Let other errors bubble up


@bot.command()
async def ping(ctx):
    await ctx.send(embed=discord.Embed(
        title="Pong!",
        description=f"I'm alive!",
        color=discord.Color.green()
    ))

# Clean messages from users who are no longer in the server
@bot.command()
@commands.has_permissions(administrator=True)
async def clean_left(ctx):
    global delete_delay
    delete_delay = BASE_DELETE_DELAY  # Reset pacing

    # Ensure required channels are configured
    if not (log_channel and target_channel):
        return await ctx.send(embed=discord.Embed(
            title="Error",
            description="Channels not set. Use !setchannels.",
            color=discord.Color.red()
        ))

    # Check bot permissions in the target channel
    perms = target_channel.permissions_for(ctx.guild.me)
    if not perms.manage_messages:
        return await ctx.send(embed=discord.Embed(
            title="Error",
            description="Missing **Manage Messages** permission in the target channel.",
            color=discord.Color.red()
        ))

    if not perms.read_message_history:
        return await ctx.send(embed=discord.Embed(
            title="Error",
            description="Missing **Read Message History** permission in the target channel. Cannot view or delete messages.",
            color=discord.Color.red()
        ))

    # Check if the bot can read messages (view) in the target channel
    if not perms.read_messages:
        return await ctx.send(embed=discord.Embed(
            title="Error",
            description="Bot cannot **view** the target channel. Please ensure the bot has permission to read messages in this channel.",
            color=discord.Color.red()
        ))

    # Try to read messages from the channel
    try:
        msgs = [m async for m in target_channel.history(limit=None)]
    except discord.Forbidden:
        return await ctx.send(embed=discord.Embed(
            title="Error",
            description="Bot was forbidden from reading messages. Please check channel permissions.",
            color=discord.Color.red()
        ))
    except Exception as e:
        await log_error(e, 'clean_left')
        return

    # Find messages from users who left (not in WHITELIST or bots)
    to_delete = [m for m in msgs if isinstance(m.author, discord.User) and m.author.id not in WHITELIST]

    # Nothing to do if there's nothing to delete
    if not to_delete:
        return await ctx.send(embed=discord.Embed(
            title="Nothing to Clean",
            description="All messages are from current members or bots—nothing to delete.",
            color=discord.Color.blue()
        ))

    # Send estimate of how long it will take
    count = len(to_delete)
    estimate = count * delete_delay
    await ctx.send(embed=discord.Embed(
        title="Estimate",
        description=f"~{estimate:.1f}s to delete {count} messages\n(current pace {delete_delay:.1f}s/msg)",
        color=discord.Color.blue()
    ))

    # Delete messages with delay and track how many succeed
    deleted = 0
    for m in to_delete:
        await delete_with_backoff(m)
        deleted += 1
        await asyncio.sleep(delete_delay)

    # Final report
    finish = discord.Embed(
        title="Cleanup Complete",
        description=f"Deleted {deleted}/{count} messages in ~{estimate:.1f}s",    
        color=discord.Color.green()
    )
    await ctx.send(embed=finish)
    if log_channel:
        await log_channel.send(embed=finish)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        # Send a user-friendly message to the person who tried to run the command
        await ctx.send(embed=discord.Embed(
            title="Missing Permissions",
            description="You need **Administrator** permissions to run this command.",
            color=discord.Color.red()
        ))
    else:
        # For other errors, you can log or handle them here
        await ctx.send(embed=discord.Embed(
            title="Error",
            description="An unexpected error occurred. Please try again later.",
            color=discord.Color.red()
        ))



# Run the bot
bot.run(TOKEN)
