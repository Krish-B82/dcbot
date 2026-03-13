import discord
from discord.ext import commands, tasks
import asyncio
from datetime import datetime
import json
import pytz
import os
import aiohttp

# Bot Configuration — token loaded from environment variable, never hardcoded
TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 1475833068865454174

# Pacific Time zone
PT = pytz.timezone("America/Los_Angeles")

# Normal dealer restocks every 4 hours at these PT hours
NORMAL_RESTOCK_HOURS_PT = {0, 4, 8, 12, 16, 20}

# Mirage dealer restocks every 2 hours at these PT hours
MIRAGE_RESTOCK_HOURS_PT = {0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22}

# Rare fruits to alert on
ALERT_FRUITS = [
    "Dragon", "Kitsune", "Dough", "Venom", "Spirit",
    "Control", "Shadow", "Blizzard", "Gravity",
    "T-Rex", "Mammoth", "Portal", "Rumble", "Phoenix"
]

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Store current stock
current_stock = {
    "normal": [],
    "mirage": [],
    "last_update": None
}

last_alerted_fruits = {
    "normal": [],
    "mirage": []
}

# Track which restock hours we've already posted for
last_posted_normal_hour = None
last_posted_mirage_hour = None


# ========================================
# STOCK FETCHING (no Playwright needed!)
# ========================================

async def get_stock():
    """
    Directly call the fruityblox.com/stock POST endpoint using aiohttp.
    No browser needed — just a lightweight HTTP request.
    """
    url = "https://fruityblox.com/stock"
    headers = {
        "Accept": "text/x-component",
        "Accept-Encoding": "gzip, deflate, br",
        "Accept-Language": "en-US,en;q=0.9",
        "Content-Type": "text/plain;charset=UTF-8",
        "Next-Action": "000e834c372ac1b9cdffe4f36d95a76c33c66cbd36",
        "Origin": "https://fruityblox.com",
        "Referer": "https://fruityblox.com/stock",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, data="{}", timeout=aiohttp.ClientTimeout(total=15)) as resp:
                print(f"  📡 Status: {resp.status}")
                text = await resp.text()
                print(f"  📥 Response ({len(text)} bytes)")

                # Parse the React Flight format: find line starting with "1:"
                for line in text.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    colon_idx = line.find(":")
                    if colon_idx == -1:
                        continue
                    prefix = line[:colon_idx]
                    # We want the line with prefix "1" which has the stock data
                    if prefix != "1":
                        continue
                    try:
                        payload = json.loads(line[colon_idx + 1:])
                        if isinstance(payload, dict) and ("normal" in payload or "mirage" in payload):
                            normal = [f["name"] for f in payload.get("normal", []) if isinstance(f, dict) and "name" in f]
                            mirage = [f["name"] for f in payload.get("mirage", []) if isinstance(f, dict) and "name" in f]
                            print(f"  ✅ Normal: {normal}")
                            print(f"  ✅ Mirage: {mirage}")
                            return normal, mirage
                    except (json.JSONDecodeError, ValueError) as e:
                        print(f"  ⚠️ Parse error: {e}")
                        continue

                print("  ❌ No stock data found in response")
                print(f"  Raw response: {text[:500]}")
                return [], []

    except aiohttp.ClientError as e:
        print(f"  ❌ HTTP error: {e}")
        return [], []
    except Exception as e:
        print(f"  ❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return [], []


# ========================================
# SMART SCHEDULING
# ========================================

def get_current_pt_hour():
    return datetime.now(PT).hour


def should_check_normal():
    hour = get_current_pt_hour()
    return hour in NORMAL_RESTOCK_HOURS_PT and hour != last_posted_normal_hour


def should_check_mirage():
    hour = get_current_pt_hour()
    return hour in MIRAGE_RESTOCK_HOURS_PT and hour != last_posted_mirage_hour


async def post_stock_update(channel, normal, mirage, post_normal=True, post_mirage=True):
    """Post stock embed and rare fruit alert"""
    global last_alerted_fruits

    embed = discord.Embed(
        title="📊 Blox Fruits Stock Update",
        color=discord.Color.blue(),
        timestamp=datetime.now()
    )
    if post_normal:
        embed.add_field(
            name="🟢 Normal Dealer",
            value="\n".join([f"• {f}" for f in normal]) if normal else "Empty",
            inline=False
        )
    if post_mirage:
        embed.add_field(
            name="🟣 Mirage Dealer",
            value="\n".join([f"• {f}" for f in mirage]) if mirage else "Empty",
            inline=False
        )
    embed.set_footer(text=f"Restocked at {datetime.now(PT).strftime('%I:%M %p PT')}")

    try:
        await channel.send(embed=embed)
        print(f"✅ Stock update posted!")
    except Exception as e:
        print(f"❌ Failed to post: {e}")

    # Rare alert
    normal_rare = [f for f in normal if f in ALERT_FRUITS] if post_normal else last_alerted_fruits["normal"]
    mirage_rare = [f for f in mirage if f in ALERT_FRUITS] if post_mirage else last_alerted_fruits["mirage"]

    if (post_normal and normal_rare != last_alerted_fruits["normal"]) or \
       (post_mirage and mirage_rare != last_alerted_fruits["mirage"]):
        if post_normal:
            last_alerted_fruits["normal"] = normal_rare
        if post_mirage:
            last_alerted_fruits["mirage"] = mirage_rare

        if normal_rare or mirage_rare:
            alert = discord.Embed(title="🚨 RARE FRUIT ALERT! 🚨", color=discord.Color.red(), timestamp=datetime.now())
            if normal_rare and post_normal:
                alert.add_field(name="🟢 Normal Dealer", value="\n".join(f"🔥 **{f}**" for f in normal_rare), inline=False)
            if mirage_rare and post_mirage:
                alert.add_field(name="🟣 Mirage Dealer", value="\n".join(f"🔥 **{f}**" for f in mirage_rare), inline=False)
            try:
                await channel.send(embed=alert)
                print("✅ Rare alert sent!")
            except Exception as e:
                print(f"❌ Rare alert failed: {e}")


# ========================================
# BACKGROUND TASK
# ========================================

@tasks.loop(minutes=1)
async def smart_stock_checker():
    global last_posted_normal_hour, last_posted_mirage_hour, current_stock

    check_normal = should_check_normal()
    check_mirage = should_check_mirage()

    if not check_normal and not check_mirage:
        return

    hour = get_current_pt_hour()
    print(f"\n[{datetime.now()}] Restock hour {hour} PT | Normal={check_normal} Mirage={check_mirage}")

    try:
        normal, mirage = await get_stock()

        if not normal and not mirage:
            print("⚠️ Got empty stock, skipping post")
            return

        if check_normal:
            current_stock["normal"] = normal
            last_posted_normal_hour = hour
        if check_mirage:
            current_stock["mirage"] = mirage
            last_posted_mirage_hour = hour

        current_stock["last_update"] = datetime.now().strftime("%H:%M:%S")

        channel = bot.get_channel(CHANNEL_ID)
        if channel:
            await post_stock_update(channel, current_stock["normal"], current_stock["mirage"],
                                    post_normal=check_normal, post_mirage=check_mirage)
        else:
            print(f"❌ Channel {CHANNEL_ID} not found!")

    except Exception as e:
        print(f"❌ Scheduler error: {e}")
        import traceback
        traceback.print_exc()


@smart_stock_checker.before_loop
async def before_check():
    await bot.wait_until_ready()


# ========================================
# COMMANDS
# ========================================

@bot.command(name="test")
async def test_cmd(ctx):
    await ctx.send("✅ Bot is working!")


@bot.command(name="stock")
async def stock_cmd(ctx):
    normal = current_stock["normal"]
    mirage = current_stock["mirage"]
    if not normal and not mirage:
        await ctx.send("❌ No data yet. Use `!check` to force a fetch.")
        return
    embed = discord.Embed(title="📦 Current Stock", color=discord.Color.blue())
    embed.add_field(name="🟢 Normal", value="\n".join(f"• {f}" for f in normal) if normal else "Empty", inline=False)
    embed.add_field(name="🟣 Mirage", value="\n".join(f"• {f}" for f in mirage) if mirage else "Empty", inline=False)
    if current_stock["last_update"]:
        embed.set_footer(text=f"Last updated: {current_stock['last_update']}")
    await ctx.send(embed=embed)


@bot.command(name="check")
async def check_cmd(ctx):
    await ctx.send("⏳ Fetching stock now...")
    normal, mirage = await get_stock()
    if normal or mirage:
        current_stock["normal"] = normal or []
        current_stock["mirage"] = mirage or []
        current_stock["last_update"] = datetime.now().strftime("%H:%M:%S")
        channel = bot.get_channel(CHANNEL_ID)
        if channel:
            await post_stock_update(channel, current_stock["normal"], current_stock["mirage"])
        await ctx.send("✅ Done!")
    else:
        await ctx.send("❌ Failed to fetch. Check Railway logs.")


@bot.command(name="rare")
async def rare_cmd(ctx):
    await ctx.send("🔥 **Rare Fruits:**\n" + "\n".join(f"- {f}" for f in ALERT_FRUITS))


@bot.command(name="status")
async def status_cmd(ctx):
    now_pt = datetime.now(PT)
    h = now_pt.hour
    next_n = next((x for x in sorted(NORMAL_RESTOCK_HOURS_PT) if x > h), min(NORMAL_RESTOCK_HOURS_PT))
    next_m = next((x for x in sorted(MIRAGE_RESTOCK_HOURS_PT) if x > h), min(MIRAGE_RESTOCK_HOURS_PT))
    embed = discord.Embed(title="✅ Bot Status", color=discord.Color.green())
    embed.add_field(name="Time (PT)", value=now_pt.strftime("%I:%M %p"), inline=False)
    embed.add_field(name="Next Normal Restock", value=f"{next_n:02d}:00 PT", inline=True)
    embed.add_field(name="Next Mirage Restock", value=f"{next_m:02d}:00 PT", inline=True)
    embed.add_field(name="Commands", value="`!stock` `!check` `!rare` `!status` `!test`", inline=False)
    await ctx.send(embed=embed)


# ========================================
# EVENTS
# ========================================

@bot.event
async def on_ready():
    global last_posted_normal_hour, last_posted_mirage_hour

    print(f"✅ {bot.user} online | PT: {datetime.now(PT).strftime('%I:%M %p')}")
    print("🧪 Initial fetch on startup...")

    normal, mirage = await get_stock()
    current_stock["normal"] = normal or []
    current_stock["mirage"] = mirage or []
    current_stock["last_update"] = datetime.now().strftime("%H:%M:%S")

    print(f"✅ Normal: {current_stock['normal']}")
    print(f"✅ Mirage: {current_stock['mirage']}")

    # Mark the current hour as already posted so the scheduler
    # doesn't immediately post again right after startup
    hour = get_current_pt_hour()
    if hour in NORMAL_RESTOCK_HOURS_PT:
        last_posted_normal_hour = hour
        print(f"  ℹ️ Marked normal hour {hour} as posted (startup)")
    if hour in MIRAGE_RESTOCK_HOURS_PT:
        last_posted_mirage_hour = hour
        print(f"  ℹ️ Marked mirage hour {hour} as posted (startup)")

    channel = bot.get_channel(CHANNEL_ID)
    if channel and (current_stock["normal"] or current_stock["mirage"]):
        await post_stock_update(channel, current_stock["normal"], current_stock["mirage"])
    elif not channel:
        print(f"❌ Could not find channel {CHANNEL_ID}")

    if not smart_stock_checker.is_running():
        smart_stock_checker.start()


@bot.event
async def on_resumed():
    """Fires when Discord reconnects after a drop — restart scheduler if needed"""
    print("🔄 Discord session resumed — checking scheduler...")
    if not smart_stock_checker.is_running():
        smart_stock_checker.start()
        print("▶️ Scheduler restarted after resume")


@bot.event
async def on_error(event, *args, **kwargs):
    import traceback
    print(f"❌ Error in {event}:")
    traceback.print_exc()


try:
    bot.run(TOKEN)
except KeyboardInterrupt:
    print("\n⏹️ Stopped")
