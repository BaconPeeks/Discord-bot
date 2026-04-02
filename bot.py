import discord
from discord.ext import commands
import asyncio
import json
import logging
import os
from typing import Optional, Set, Dict
from dotenv import load_dotenv
import datetime

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

bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# --- ADVANCED LOGGING (Console + File) ---
logger = logging.getLogger('discord')
logger.setLevel(logging.DEBUG)
fmt = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

ch = logging.StreamHandler()
ch.setFormatter(fmt)
logger.addHandler(ch)

# fh = logging.FileHandler('bot_log.txt')
# fh.setFormatter(fmt)
# logger.addHandler(fh)

SETTINGS_FILE = 'channel_settings.json'
BASE_DELETE_DELAY = 0.5
delete_delay = BASE_DELETE_DELAY
last_rate_limit = 0.0

# User IDs exempt from deletion
WHITELIST: Set[int] = {1234567890}

# Log and cleanup target channels
log_channel: Optional[discord.TextChannel] = None
target_channel: Optional[discord.TextChannel] = None

# Image cleanup settings
image_cleanup_channel: Optional[discord.TextChannel] = None
image_delete_delay: Optional[int] = None


# --- HELPERS ---

def is_image_attachment(attachment: discord.Attachment) -> bool:
    """
    
    Return True if the attachment is an image.

    Checks both the MIME type (preferred) 
    and file extension as a fallback.

     Args:
        attachment (discord.Attachment): 
    The Attachment to evaluate.

     Returns:
        bool: True if the attachment is an image, otherwise False.
    """
    if attachment.content_type and attachment.content_type.startswith("image/"):
        return True

    filename = attachment.filename.lower()
    return filename.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff"))


# --- PERSISTENCE ---

def save_settings():
    """
    Saves bot configuration (channels and image cleanup) to a JSON file.

    Stores:
        - log channel ID
        - target cleanup channel ID
        - image cleanup channel ID
        - image delete delay
    This ensures persistance across bot restarts.
     """
    with open(SETTINGS_FILE, 'w') as f:
        json.dump({
            'log_channel_id': log_channel.id if log_channel else None,
            'target_channel_id': target_channel.id if target_channel else None,
            'image_cleanup_channel_id': image_cleanup_channel.id if image_cleanup_channel else None,
            'image_delete_delay': image_delete_delay
        }, f, indent=4)


def load_settings():
    """
    Load bot configuration from the JSON settings file.

    Returns:
        tuple:
            - log_channel_id(Optional[int])
            - target_channel_id(Optional[int])
            - image_cleanup_channel_id(Optional[int])
            - image_delete_delay(Optional[int])
    
    """
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, 'r') as f:
            d = json.load(f)
            return (
                d.get('log_channel_id'),
                d.get('target_channel_id'),
                d.get('image_cleanup_channel_id'),
                d.get('image_delete_delay')
            )
    return None, None, None, None


# --- COMMUNICATION ---

async def send_admin(msg: str):
    """
    Send a message to the configured admin channel.

    Args:
        msg (str): Message to send.
    """
    ch = bot.get_channel(ADMIN_CHANNEL_ID)
    if isinstance(ch, discord.abc.Messageable):
        await ch.send(msg)


async def log_error(exc, fn):
    """
    Log an error to console, log channel, and admin channel.

    Args:
        exc(Exception): The exception that occured.
        fn(str): Name of the function where the issue occured.

    """
    logger.error(f"Error in {fn}: {exc}")
    if log_channel:
        e = discord.Embed(
            title="Error",
            description=f"❌ `{fn}`: {exc}",
            color=discord.Color.red()
        )
        await log_channel.send(embed=e)
    await send_admin(f"❌ Error in `{fn}`: {exc}")


# --- EVENTS ---

@bot.event
async def on_ready():
    """
    Event triggered when the bot connects to discord.

    Loads saved configuration and initilizes channel references.
    """
    global log_channel, target_channel, image_cleanup_channel, image_delete_delay

    print(f"Logged in as {bot.user} (ID: {bot.user.id})")  # type: ignore

    lc_id, tc_id, ic_id, saved_img_delay = load_settings()

    if lc_id:
        temp_lc = bot.get_channel(lc_id)
        if isinstance(temp_lc, discord.TextChannel):
            log_channel = temp_lc

    if tc_id:
        temp_tc = bot.get_channel(tc_id)
        if isinstance(temp_tc, discord.TextChannel):
            target_channel = temp_tc

    if ic_id:
        temp_ic = bot.get_channel(ic_id)
        if isinstance(temp_ic, discord.TextChannel):
            image_cleanup_channel = temp_ic

    image_delete_delay = saved_img_delay

    print(
        f"Loaded settings | "
        f"log={getattr(log_channel, 'name', None)} | "
        f"target={getattr(target_channel, 'name', None)} | "
        f"image_cleanup={getattr(image_cleanup_channel, 'name', None)} | "
        f"image_delay={image_delete_delay}"
    )

    if not (log_channel and target_channel):
        await send_admin("⚠️ No saved channels—run !setchannels.")

    if image_cleanup_channel is None:
        await send_admin("⚠️ No image cleanup channel set—run !setimagechannel.")

    if image_delete_delay is None:
        await send_admin("⚠️ No image delete delay set—run !setimagedelay.")


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


@bot.event
async def on_message(message: discord.Message):
    """
    Event triggered for EVERY message sent in guilds the bot can see.

    Handles automatic deletion of image messages in the configured image cleanup channel after specified delay.

    Also ensures commands continue to function properly.
    """
    # Keep commands working
    await bot.process_commands(message)

    # Ignore messages from bot
    if message.author.bot:
        return

    if message.author.id in WHITELIST:
        return

    if image_cleanup_channel is None or image_delete_delay is None:
        return

    if not isinstance(message.channel, discord.TextChannel):
        return

    # Only operate in configured image cleanup channel
    if message.channel.id != image_cleanup_channel.id:
        return

    if not message.attachments:
        return

    if not any(is_image_attachment(att) for att in message.attachments):
        return

    logger.info(
        f"Queued image message {message.id} from {message.author} "
        f"in #{message.channel.name} for deletion in {image_delete_delay}s"
    )

    await asyncio.sleep(image_delete_delay)

    try:
        await delete_with_backoff(message)
        if log_channel:
            await log_channel.send(embed=discord.Embed(
                title="Image Deleted",
                description=(
                    f"Deleted image post from {message.author.mention} "
                    f"in {message.channel.mention} after {image_delete_delay} seconds."
                ),
                color=discord.Color.blurple()
            ))
    except Exception as e:
        await log_error(e, 'on_message image cleanup')


# --- BACKOFF LOGIC ---

async def delete_with_backoff(msg: discord.Message):
    """
    Adds backoff delay when rate limited by discord.

    Args:
        msg: Message being deleted.
    """
    global last_rate_limit, delete_delay
    try:
        await msg.delete()
        logger.info(f"Deleted message ID: {msg.id}")
    except discord.NotFound:
        logger.info(f"Message already deleted: {msg.id}")
    except discord.Forbidden:
        logger.warning(f"Missing permissions to delete message: {msg.id}")
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
    """
    Shows the bot's latency.
    """
    latency = round(bot.latency * 1000)
    await ctx.send(embed=discord.Embed(
        title="Pong!",
        description=f"Latency: {latency}ms",
        color=discord.Color.green()
    ))


@bot.command()
@commands.has_permissions(administrator=True)
async def setchannels(ctx, log_ch: discord.TextChannel, target_ch: discord.TextChannel):
    """
    set the log channel and target cleanup channel

    Args:
        ctx: Command Context.
        log_ch(discord.TextChannel):
        Channel for logs/errors.
        target_ch(discord.TextChannel):
        Channel specified for cleaning
    """

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
async def setimagechannel(ctx, channel: discord.TextChannel):
    """
    set the channel where images will be automatically deleted.

    Requires image delete delay to be configured first.

    Args:
        ctx: Command Context.
        channel(discord.TextChannel):
        Channel to monitor for images.
    """
    global image_cleanup_channel

    if image_delete_delay is None:
        return await ctx.send("❌ You must set the image delay first with `!setimagedelay`.")

    image_cleanup_channel = channel
    save_settings()

    await ctx.send(embed=discord.Embed(
        title="Image Channel Setup",
        description=f"🖼️ Images in {channel.mention} will be auto-deleted after {image_delete_delay} seconds.",
        color=discord.Color.green()
    ))


@bot.command()
@commands.has_permissions(administrator=True)
async def setimagedelay(ctx, seconds: int):
    """
    Set the delay before images are automatically deleted.

    Args:
        ctx: Command Context.
        Seconds(int): Delay in seconds(minimum 5).
    """
    global image_delete_delay

    if seconds < 5:
        return await ctx.send("❌ Delay must be at least 5 seconds.")

    image_delete_delay = seconds
    save_settings()

    minutes = seconds / 60

    await ctx.send(embed=discord.Embed(
        title="Image Delete Delay Updated",
        description=f"⏱️ New delay: **{seconds} seconds** ({minutes:.1f} minutes)",
        color=discord.Color.green()
    ))


@bot.command()
@commands.has_permissions(administrator=True)
async def imagecleanupstatus(ctx):
    """
    Display the current image cleanup configuration.

    Shows:
        - Configured channel
        - delete delay
    """
    channel_text = image_cleanup_channel.mention if image_cleanup_channel else "Not set"
    delay_text = f"{image_delete_delay} seconds" if image_delete_delay is not None else "Not set"

    await ctx.send(embed=discord.Embed(
        title="Image Cleanup Status",
        description=f"**Channel:** {channel_text}\n**Delay:** {delay_text}",
        color=discord.Color.blue()
    ))


@bot.command()
@commands.has_permissions(administrator=True)
async def clean_left(ctx):
    """
    Remove messages from users who are no longer in the server.

    Scans full channel history, deletes messages, and reports a summary of who and how many images.
    """
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
        return await ctx.send(embed=discord.Embed(
            title="Clean!",
            description="No messages from former members.",
            color=discord.Color.blue()
        ))

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


@bot.command()
@commands.has_permissions(administrator=True)
async def help(ctx, command_name: str = None):  # type: ignore

    command_info = {
        "ping": {
            "usage": "!ping",
            "description": "Check whether the bot is online and view latency.",
            "permissions": "Everyone"
        }
    }

    if command_name is None:
        embed = discord.Embed(
            title="Bot Help",
            description="Use `!help <command>` to view more details about a specific command.",
            color=discord.Color.blurple(),
            timestamp= datetime.datetime.now(datetime.UTC)
        )

        for name, info in command_info.items():
            embed.add_field(
                name=f"!{name}",
                value=info["description"],
                inline=False
            )

        await ctx.send(embed=embed)
        return

    if command_name not in command_info:
        return await ctx.send(embed=discord.Embed(
            title="Error",
            description=f"❌ Command `{command_name}` not found.",
            color=discord.Color.red()
        ))

    info = command_info[command_name]

    embed = discord.Embed(
        title=f"Help: !{command_name}",
        color=discord.Color.purple(),
        timestamp=datetime.datetime.now(datetime.UTC)
    )

    embed.add_field(name="Description", value=info["description"], inline=False)
    embed.add_field(name="Usage", value=f"`{info['usage']}`", inline=False)
    embed.add_field(name="Permissions", value=info["permissions"], inline=False)
    embed.set_footer(text= "Help command")

    await ctx.send(embed=embed)

    


@bot.event
async def on_command_error(ctx, error):
    """
    Handle command errors globally.

    Currently Handles:
        - MissingPermissions

    Logs all other errors.
    """
    if isinstance(error, commands.MissingPermissions):
        await ctx.send(embed=discord.Embed(
            title="Access Denied",
            description="Admin permissions required.",
            color=discord.Color.red()
        ))
    else:
        logger.error(f"Unhandled Error: {error}")


if TOKEN:
    bot.run(TOKEN)
