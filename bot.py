import discord
from discord.ext import commands
import asyncio
import json
import logging
import os
from typing import Optional, Union, Set, Dict
from dotenv import load_dotenv

# --- SECURE CONFIG LOADING ---
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
ADMIN_CHANNEL_ID = int(os.getenv('ADMIN_ID', 0))

# Intents setup
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
intents.messages = True

bot = commands.Bot(command_prefix='!', intents=intents)

# --- ADVANCED LOGGING (Console + File) ---
logger = logging.getLogger('discord')
logger.setLevel(logging.DEBUG) 
fmt = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

ch = logging.StreamHandler()
ch.setFormatter(fmt)
logger.addHandler(ch)

fh = logging.FileHandler('bot_log.txt')
fh.setFormatter(fmt)
logger.addHandler(fh)

SETTINGS_FILE = 'channel_settings.json'
BASE_DELETE_DELAY = 0.5
delete_delay = BASE_DELETE_DELAY
last_rate_limit = 0.0

# User IDs exempt from deletion
WHITELIST: Set[int] = {1234567890}  

# Log and cleanup target channels
log_channel: Optional[discord.TextChannel] = None
target_channel: Optional[discord.TextChannel] = None

# --- PERSISTENCE ---

def save_settings():
    if log_channel and target_channel:
        with open(SETTINGS_FILE, 'w') as f:
            json.dump({
                'log_channel_id': log_channel.id,
                'target_channel_id': target_channel.id
            }, f)

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, 'r') as f:
            d = json.load(f)
            return d.get('log_channel_id'), d.get('target_channel_id')
    return None, None

# --- COMMUNICATION ---

async def send_admin(msg: str):
    ch = bot.get_channel(ADMIN_CHANNEL_ID)
    if isinstance(ch, discord.abc.Messageable):
        await ch.send(msg)

async def log_error(exc, fn):
    logger.error(f"Error in {fn}: {exc}")
    if log_channel:
        e = discord.Embed(title="Error", description=f"❌ `{fn}`: {exc}", color=discord.Color.red())
        await log_channel.send(embed=e)
    await send_admin(f"❌ Error in `{fn}`: {exc}")

# --- EVENTS ---

@bot.event
async def on_ready():
    global log_channel, target_channel
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")  # type: ignore
    lc_id, tc_id = load_settings()
    if lc_id and tc_id:
        temp_lc = bot.get_channel(lc_id)
        temp_tc = bot.get_channel(tc_id)
        if isinstance(temp_lc, discord.TextChannel) and isinstance(temp_tc, discord.TextChannel):
            log_channel, target_channel = temp_lc, temp_tc
            print(f"Loaded: log={log_channel.name}, target={target_channel.name}")
        else:
            await send_admin("❌ Saved channels invalid. Run !setchannels.")
    else:
        await send_admin("⚠️ No saved channels—run !setchannels.")

@bot.event
async def on_socket_response(data):
    global last_rate_limit, delete_delay
    if isinstance(data, dict) and data.get('t') == 'RATE_LIMIT':
        retry = data.get('d', {}).get('retry_after', 0.0)
        last_rate_limit = max(last_rate_limit, retry)
        delete_delay = max(delete_delay, last_rate_limit + 0.1)
        logger.warning(f"Gateway rate limit: retry={retry}s -> delete_delay={delete_delay:.1f}s")
        if log_channel:
            await log_channel.send(embed=discord.Embed(
                title="Rate Limit Detected",
                description=f"Gateway retry_after={retry}s",
                color=discord.Color.orange()
            ))

# --- BACKOFF LOGIC ---

async def delete_with_backoff(msg: discord.Message):
    global last_rate_limit, delete_delay
    try:
        await msg.delete()
        logger.info(f"Deleted message ID: {msg.id}")
    except discord.HTTPException as e:
        if e.status == 429:
            retry = getattr(e, 'retry_after', 1.0)
            last_rate_limit = max(last_rate_limit, retry)
            delete_delay = max(delete_delay, last_rate_limit + 0.1)
            logger.warning(f"HTTP 429: retry={retry}s -> delay={delete_delay:.1f}s")
            if log_channel:
                await log_channel.send(embed=discord.Embed(
                    title="Rate Limit Backoff",
                    description=f"Waiting {delete_delay:.1f}s",
                    color=discord.Color.orange()
                ))
            await asyncio.sleep(delete_delay)
            return await delete_with_backoff(msg)
        else:
            raise 

# --- COMMANDS ---

@bot.command()
async def ping(ctx):
    latency = round(bot.latency * 1000)
    await ctx.send(embed=discord.Embed(
        title="Pong!",
        description=f"Latency: {latency}ms",
        color=discord.Color.green()
    ))

@bot.command()
@commands.has_permissions(administrator=True)
async def setchannels(ctx, log_ch: discord.TextChannel, target_ch: discord.TextChannel):
    global log_channel, target_channel
    log_channel, target_channel = log_ch, target_ch
    save_settings()
    await ctx.send(embed=discord.Embed(
        title="Settings Updated",
        description=f"✅ Log: {log_channel.mention}\n✅ Target: {target_channel.mention}",
        color=discord.Color.green()
    ))

@bot.command()
@commands.has_permissions(administrator=True)
async def clean_left(ctx):
    global delete_delay
    delete_delay = BASE_DELETE_DELAY 

    if not (log_channel and target_channel):
        return await ctx.send("❌ Channels not set. Use !setchannels.")

    perms = target_channel.permissions_for(ctx.guild.me)
    if not (perms.manage_messages and perms.read_message_history):
        return await ctx.send("❌ Missing permissions (Manage Messages/History) in target channel.")

    await ctx.send("🔍 Scanning history and building user tally...")

    user_tally: Dict[str, int] = {}
    to_delete = []

    try:
        async for m in target_channel.history(limit=None):
            if ctx.guild.get_member(m.author.id) is None and not m.author.bot and m.author.id not in WHITELIST:
                to_delete.append(m)
                user_name = f"{m.author.name} ({m.author.id})"
                user_tally[user_name] = user_tally.get(user_name, 0) + 1
                
    except Exception as e:
        return await log_error(e, 'clean_left')

    if not to_delete:
        return await ctx.send(embed=discord.Embed(title="Clean!", description="No messages from former members.", color=discord.Color.blue()))

    count = len(to_delete)
    estimate = count * delete_delay
    await ctx.send(embed=discord.Embed(
        title="Estimate",
        description=f"Deleting {count} messages from {len(user_tally)} users.\nEst: {estimate:.1f}s",
        color=discord.Color.blue()
    ))

    deleted = 0
    for m in to_delete:
        await delete_with_backoff(m)
        deleted += 1
        await asyncio.sleep(delete_delay)

    # Sort tally by most messages
    sorted_tally = sorted(user_tally.items(), key=lambda x: x[1], reverse=True)
    summary_text = ""
    for user, amt in sorted_tally[:15]:
        summary_text += f"• **{user}**: {amt} messages\n"
    
    if len(sorted_tally) > 15:
        summary_text += f"\n*...and {len(sorted_tally) - 15} more users.*"

    finish_embed = discord.Embed(
        title="✅ Cleanup Complete",
        description=f"Deleted **{deleted}/{count}** messages.\n\n**User Breakdown:**\n{summary_text}",
        color=discord.Color.green()
    )
    
    await ctx.send(embed=finish_embed)
    if log_channel:
        await log_channel.send(embed=finish_embed)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send(embed=discord.Embed(title="Access Denied", description="Admin permissions required.", color=discord.Color.red()))
    else:
        logger.error(f"Unhandled Error: {error}")

if TOKEN:
    bot.run(TOKEN)
