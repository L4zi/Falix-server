import discord
from discord.ext import commands
import os
import sys
import asyncio
import random
import json
import aiohttp
import cloudscraper
import concurrent.futures
from dotenv import load_dotenv
from sounds import SOUNDS

load_dotenv()

DISCORD_TOKEN = os.environ["BOT_TOKEN"]

SOUND_CHANNEL_ID = 1477710142425403523
LEADERBOARD_FILE = "leaderboard.json"
ADMIN_ID = 1176877411934154806

DEFAULT_ELO = 100
BASE_GAIN   = 10

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

SIXTYSEVEN_GIFS = [
    "https://giphy.com/gifs/67-six-seven-john-chungus-08uBcURaMq6vA93TGc",
    "https://giphy.com/gifs/rigby-rigbycat-67-8rN9VXNb7dfU792YQt",
    "https://giphy.com/gifs/cat-nub-nubcat-TKa7fQzChHylCQ89to",
    "https://giphy.com/gifs/brainrot-67-hate-MKUOUJrFldIY2hJyT",
    "https://giphy.com/gifs/argentina-vtuber-mialygosa-B4VWevk4w1a95oBHqv",
]

# ═══════════════════════════════════════════════════════════
# ELO SYSTEM
# ═══════════════════════════════════════════════════════════

def _k_factor(games_played: int) -> float:
    if games_played < 10:
        return 40.0
    if games_played < 30:
        return 28.0
    return 20.0

def _expected_score(player_elo: int, opponent_elo: int) -> float:
    return 1.0 / (1.0 + 10 ** ((opponent_elo - player_elo) / 400.0))

def _dominance_multiplier(winner_score: int, loser_score: int) -> float:
    total = winner_score + loser_score
    if total == 0:
        return 1.0
    ratio = winner_score / total
    return 0.5 + ratio

def calc_elo_change(
    winner_elo: int,
    loser_elo: int,
    winner_score: int,
    loser_score: int,
    winner_games_played: int,
    loser_games_played: int,
) -> tuple[int, int]:
    dom   = _dominance_multiplier(winner_score, loser_score)
    e_win = _expected_score(winner_elo, loser_elo)
    e_los = 1.0 - e_win

    k_win = _k_factor(winner_games_played)
    k_los = _k_factor(loser_games_played)

    winner_gain = round(k_win * dom * (1.0 - e_win))
    loser_loss  = round(k_los * dom * e_los)

    winner_gain = max(3, min(35, winner_gain))
    loser_loss  = max(3, min(35, loser_loss))

    return winner_gain, loser_loss

# ── JSON helpers ──────────────────────────────────────────

def lb_load() -> dict:
    if not os.path.exists(LEADERBOARD_FILE):
        return {"players": {}, "duels": []}
    with open(LEADERBOARD_FILE, "r") as f:
        return json.load(f)

def lb_save(data: dict):
    with open(LEADERBOARD_FILE, "w") as f:
        json.dump(data, f, indent=2)

def lb_find(data: dict, name: str):
    for p in data["players"]:
        if p.lower() == name.lower():
            return p
    return None

def lb_sorted(data: dict) -> list:
    return sorted(data["players"].items(), key=lambda x: -x[1])

# ── Embed builders ────────────────────────────────────────

def make_leaderboard_embed(data: dict) -> discord.Embed:
    embed = discord.Embed(
        title="SKIRMISH  ·  ELO STANDINGS",
        color=0x1a1a2e
    )

    if not data["players"]:
        embed.description = "No players registered.\nUse `/addplayer` to add someone."
        embed.set_footer(text="big funnys bot")
        return embed

    standings = lb_sorted(data)
    rows = []

    for i, (name, elo) in enumerate(standings, 1):
        wins   = sum(1 for d in data["duels"] if d["winner"] == name)
        losses = sum(1 for d in data["duels"] if d["loser"]  == name)
        total  = wins + losses
        wr     = f"{round(wins / total * 100)}%" if total > 0 else "—"

        history = [d for d in data["duels"] if d["winner"] == name or d["loser"] == name]
        recent  = history[-5:]
        form    = "".join("W" if d["winner"] == name else "L" for d in recent) if recent else "—"

        rows.append(
            f"`#{str(i).zfill(2)}`  **{name}**\n"
            f">>> ELO **{elo}**   ·   {wins}W – {losses}L   ·   {wr} WR   ·   Form: `{form}`"
        )

    embed.description = "\n".join(rows)
    embed.set_footer(text=f"{len(data['duels'])} duels on record  ·  big funnys bot")
    return embed

def make_history_embed(data: dict, name: str):
    canonical = lb_find(data, name)
    if not canonical:
        return None

    elo    = data["players"][canonical]
    wins   = sum(1 for d in data["duels"] if d["winner"] == canonical)
    losses = sum(1 for d in data["duels"] if d["loser"]  == canonical)
    total  = wins + losses
    wr     = f"{round(wins / total * 100)}%" if total > 0 else "—"

    history = [d for d in data["duels"] if d["winner"] == canonical or d["loser"] == canonical]
    recent  = history[-5:]

    embed = discord.Embed(
        title=f"{canonical.upper()}  ·  DUEL HISTORY",
        color=0x1a1a2e
    )
    embed.add_field(name="ELO",       value=str(elo),            inline=True)
    embed.add_field(name="Record",    value=f"{wins}W – {losses}L", inline=True)
    embed.add_field(name="Win Rate",  value=wr,                  inline=True)

    if not recent:
        embed.add_field(name="Last 5 Duels", value="No duels recorded yet.", inline=False)
    else:
        lines = []
        for d in reversed(recent):
            result = "W" if d["winner"] == canonical else "L"
            opp    = d["loser"] if d["winner"] == canonical else d["winner"]
            score  = d.get("score", "?-?")
            if result == "W":
                delta = f"+{d.get('w_elo_change', d.get('elo_change', '?'))}"
            else:
                delta = f"-{d.get('l_elo_change', d.get('elo_change', '?'))}"
            lines.append(f"{result}   {opp:<14} {score:<7} {delta:<7} {d['date']}")
        embed.add_field(
            name="Last 5 Duels",
            value=f"```\n{chr(10).join(lines)}\n```",
            inline=False
        )

    embed.set_footer(text="big funnys bot")
    return embed

# ── Soundboard state ──────────────────────────────────────

soundboard_locked    = False
soundboard_locked_by = None
SOUNDS_PER_PAGE      = 12
LOCK_ALLOWED_IDS     = {1176877411934154806, 1338451898952581165}

SOUND_NAMES  = list(SOUNDS.keys())
TOTAL_PAGES  = (len(SOUND_NAMES) + SOUNDS_PER_PAGE - 1) // SOUNDS_PER_PAGE
BUTTON_STYLES = [
    discord.ButtonStyle.primary,
    discord.ButtonStyle.success,
    discord.ButtonStyle.secondary,
]

# ── on_message ────────────────────────────────────────────

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    if "67" in message.content:
        await message.channel.send(random.choice(SIXTYSEVEN_GIFS))
    await bot.process_commands(message)

# ── Soundboard helpers ────────────────────────────────────

async def play_sound(guild, sound_name):
    source = SOUNDS[sound_name]
    vc = guild.voice_client
    if vc is None:
        channel = guild.get_channel(SOUND_CHANNEL_ID)
        if channel is None:
            raise Exception("Bot is not in a voice channel! Use /joinvoice first.")
        vc = await channel.connect()
    if vc.is_playing():
        vc.stop()
    opts = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5" if source.startswith("http") else None
    vc.play(discord.FFmpegPCMAudio(source, before_options=opts))

def make_soundboard_embed(page: int) -> discord.Embed:
    lock_status = f"🔒 **Locked** by {soundboard_locked_by}" if soundboard_locked else "🔓 Unlocked"
    embed = discord.Embed(
        title="🎵 Soundboard",
        description=f"Click a button to play a sound in voice.\nStatus: {lock_status}",
        color=discord.Color.red() if soundboard_locked else discord.Color.orange()
    )
    start = page * SOUNDS_PER_PAGE
    end   = min(start + SOUNDS_PER_PAGE, len(SOUND_NAMES))
    embed.add_field(
        name=f"Page {page+1} / {TOTAL_PAGES}",
        value="\n".join(f"• {n.title()}" for n in SOUND_NAMES[start:end]),
        inline=False
    )
    embed.set_footer(text=f"Showing {end-start} sounds  ·  Page {page+1} of {TOTAL_PAGES}")
    return embed

class SoundButton(discord.ui.Button):
    def __init__(self, sound_name, style):
        label = sound_name.title()
        if len(label) > 80: label = label[:77] + "…"
        super().__init__(label=label, style=style, custom_id=f"sb_{sound_name[:80]}")
        self.sound_name = sound_name

    async def callback(self, interaction: discord.Interaction):
        if soundboard_locked:
            await interaction.response.send_message(f"🔒 Locked by **{soundboard_locked_by}**!", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await play_sound(interaction.guild, self.sound_name)
            await interaction.followup.send(f"▶️ Now playing **{self.label}**!", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Failed: `{e}`", ephemeral=True)

class NavButton(discord.ui.Button):
    def __init__(self, arrow, target_page):
        super().__init__(label=arrow, style=discord.ButtonStyle.secondary)
        self.target_page = target_page

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(
            embed=make_soundboard_embed(self.target_page),
            view=SoundboardView(self.target_page)
        )

class LockToggleButton(discord.ui.Button):
    def __init__(self):
        label = "🔓 Unlock" if soundboard_locked else "🔒 Lock"
        style = discord.ButtonStyle.success if soundboard_locked else discord.ButtonStyle.danger
        super().__init__(label=label, style=style)

    async def callback(self, interaction: discord.Interaction):
        global soundboard_locked, soundboard_locked_by
        if interaction.user.id not in LOCK_ALLOWED_IDS:
            await interaction.response.send_message("No permission!", ephemeral=True)
            return
        soundboard_locked = not soundboard_locked
        soundboard_locked_by = interaction.user.display_name if soundboard_locked else None
        msg = f"🔒 Locked by **{soundboard_locked_by}**" if soundboard_locked else f"🔓 Unlocked by **{interaction.user.display_name}**"
        current_page = 0
        for item in self.view.children:
            if isinstance(item, NavButton) and item.label == "◀":
                current_page = item.target_page + 1
                break
        await interaction.response.edit_message(embed=make_soundboard_embed(current_page), view=SoundboardView(current_page))
        await interaction.followup.send(msg, ephemeral=True)

class SoundboardView(discord.ui.View):
    def __init__(self, page=0):
        super().__init__(timeout=300)
        self.page = page
        start = page * SOUNDS_PER_PAGE
        end   = min(start + SOUNDS_PER_PAGE, len(SOUND_NAMES))
        for i, name in enumerate(SOUND_NAMES[start:end]):
            self.add_item(SoundButton(name, BUTTON_STYLES[i % len(BUTTON_STYLES)]))
        nav = []
        if page > 0:              nav.append(NavButton("◀", page - 1))
        nav.append(LockToggleButton())
        if page < TOTAL_PAGES-1:  nav.append(NavButton("▶", page + 1))
        for btn in nav: self.add_item(btn)

# ── Soundboard commands ───────────────────────────────────

@bot.tree.command(name="soundboard", description="Open the soundboard")
async def soundboard(interaction: discord.Interaction):
    await interaction.response.send_message(embed=make_soundboard_embed(0), view=SoundboardView(0))

@bot.tree.command(name="joinvoice", description="Make the bot join a voice channel")
async def joinvoice(interaction: discord.Interaction, channel_id: str = None):
    channel = bot.get_channel(int(channel_id)) if channel_id else bot.get_channel(SOUND_CHANNEL_ID)
    if channel is None or not isinstance(channel, discord.VoiceChannel):
        await interaction.response.send_message("❌ Channel not found!", ephemeral=True)
        return
    if interaction.guild.voice_client:
        await interaction.guild.voice_client.move_to(channel)
    else:
        await channel.connect()
    await interaction.response.send_message(f"✅ Joined **{channel.name}**!", ephemeral=True)

@bot.tree.command(name="leavevoice", description="Make the bot leave the voice channel")
async def leavevoice(interaction: discord.Interaction):
    if not interaction.guild.voice_client:
        await interaction.response.send_message("❌ Not in a voice channel!", ephemeral=True)
        return
    await interaction.guild.voice_client.disconnect()
    await interaction.response.send_message("✅ Left!", ephemeral=True)

# ── Leaderboard commands (admin only) ────────────────────

def is_admin(interaction: discord.Interaction) -> bool:
    return interaction.user.id == ADMIN_ID

@bot.tree.command(name="addplayer", description="[Admin] Add a player to the leaderboard")
async def addplayer(interaction: discord.Interaction, name: str):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this.", ephemeral=True)
        return
    data = lb_load()
    if lb_find(data, name):
        await interaction.response.send_message(f"❌ **{name}** is already on the leaderboard.", ephemeral=True)
        return
    data["players"][name] = DEFAULT_ELO
    lb_save(data)
    await interaction.response.send_message(f"✅ Added **{name}** with {DEFAULT_ELO} ELO.", ephemeral=True)

@bot.tree.command(name="removeplayer", description="[Admin] Remove a player from the leaderboard")
async def removeplayer(interaction: discord.Interaction, name: str):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this.", ephemeral=True)
        return
    data = lb_load()
    canonical = lb_find(data, name)
    if not canonical:
        await interaction.response.send_message(f"❌ Player **{name}** not found.", ephemeral=True)
        return
    del data["players"][canonical]
    data["duels"] = [d for d in data["duels"] if d["winner"] != canonical and d["loser"] != canonical]
    lb_save(data)
    await interaction.response.send_message(f"✅ Removed **{canonical}** and all their duels.", ephemeral=True)

@bot.tree.command(name="recordwin", description="[Admin] Record a skirmish result with score")
async def recordwin(
    interaction: discord.Interaction,
    winner: str,
    loser: str,
    winner_score: int,
    loser_score: int,
):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this.", ephemeral=True)
        return

    if winner_score <= loser_score:
        await interaction.response.send_message(
            "❌ Winner score must be higher than loser score.", ephemeral=True
        )
        return

    data = lb_load()
    w = lb_find(data, winner)
    l = lb_find(data, loser)
    if not w:
        await interaction.response.send_message(f"❌ Player **{winner}** not found.", ephemeral=True)
        return
    if not l:
        await interaction.response.send_message(f"❌ Player **{loser}** not found.", ephemeral=True)
        return
    if w == l:
        await interaction.response.send_message("❌ Winner and loser can't be the same person!", ephemeral=True)
        return

    from datetime import date

    w_games = sum(1 for d in data["duels"] if d["winner"] == w or d["loser"] == w)
    l_games = sum(1 for d in data["duels"] if d["winner"] == l or d["loser"] == l)

    w_elo_before = data["players"][w]
    l_elo_before = data["players"][l]

    w_gain, l_loss = calc_elo_change(
        w_elo_before, l_elo_before,
        winner_score, loser_score,
        w_games, l_games
    )

    data["players"][w] += w_gain
    data["players"][l] -= l_loss
    data["players"][l]  = max(0, data["players"][l])

    total_rounds = winner_score + loser_score
    closeness = round((loser_score / total_rounds) * 100)

    data["duels"].append({
        "winner": w, "loser": l,
        "score": f"{winner_score}-{loser_score}",
        "w_elo_change": w_gain,
        "l_elo_change": l_loss,
        "elo_change": w_gain,
        "date": str(date.today())
    })
    lb_save(data)

    if closeness >= 47:
        verdict = "VERY CLOSE"
    elif closeness >= 35:
        verdict = "COMPETITIVE"
    elif closeness >= 20:
        verdict = "DOMINANT"
    else:
        verdict = "DECISIVE"

    embed = discord.Embed(title="DUEL RECORDED", color=0x00d26a)
    embed.add_field(name="Winner",           value=w,                                  inline=True)
    embed.add_field(name="Loser",            value=l,                                  inline=True)
    embed.add_field(name="Score",            value=f"{winner_score} – {loser_score}  [{verdict}]", inline=True)
    embed.add_field(name=f"{w}",             value=f"{w_elo_before} → **{data['players'][w]}** (+{w_gain})", inline=True)
    embed.add_field(name=f"{l}",             value=f"{l_elo_before} → **{data['players'][l]}** (-{l_loss})", inline=True)
    embed.add_field(name="\u200b",          value="\u200b",                          inline=True)
    embed.set_footer(text="big funnys bot")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="undolast", description="[Admin] Undo the last recorded duel")
async def undolast(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this.", ephemeral=True)
        return
    data = lb_load()
    if not data["duels"]:
        await interaction.response.send_message("❌ No duels to undo.", ephemeral=True)
        return
    last = data["duels"].pop()
    w, l = last["winner"], last["loser"]
    w_gain = last.get("w_elo_change", last.get("elo_change", 10))
    l_loss = last.get("l_elo_change", last.get("elo_change", 10))
    if w in data["players"]: data["players"][w] -= w_gain
    if l in data["players"]: data["players"][l] += l_loss
    lb_save(data)
    score = last.get("score", "?")
    await interaction.response.send_message(
        f"Undone: **{w}** beat **{l}** ({score}) on {last['date']}  —  ELO reversed (+{w_gain} / -{l_loss})",
        ephemeral=True
    )

@bot.tree.command(name="clearleaderboard", description="[Admin] Wipe all players and duels")
async def clearleaderboard(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this.", ephemeral=True)
        return
    lb_save({"players": {}, "duels": []})
    await interaction.response.send_message("🗑️ Leaderboard cleared.", ephemeral=True)

# ── Public leaderboard commands ───────────────────────────

@bot.tree.command(name="valorantleaderboard", description="Show the 1v1 skirmish leaderboard")
async def valorantleaderboard(interaction: discord.Interaction):
    await interaction.response.send_message(embed=make_leaderboard_embed(lb_load()))

@bot.tree.command(name="duelhistory", description="Show a player's last 5 duels")
async def duelhistory(interaction: discord.Interaction, player: str):
    data = lb_load()
    embed = make_history_embed(data, player)
    if embed is None:
        players = ", ".join(data["players"].keys()) or "none"
        await interaction.response.send_message(
            f"❌ **{player}** not found.\nPlayers: {players}", ephemeral=True
        )
        return
    await interaction.response.send_message(embed=embed)

# ── Coinflip ──────────────────────────────────────────────

@bot.tree.command(name="coinflip", description="Flip a coin")
async def coinflip(interaction: discord.Interaction):
    await interaction.response.defer()
    suspense = ["🪙 Flipping the coin...", "🌀 It's spinning in the air...", "😮 Almost there..."]
    msg = await interaction.followup.send(suspense[0])
    for line in suspense[1:]:
        await asyncio.sleep(1.2)
        await msg.edit(content=line)
    await asyncio.sleep(1.2)
    result = random.choice(["Heads", "Tails"])
    emoji  = "👑" if result == "Heads" else "✨"
    await msg.edit(content=None, embed=discord.Embed(title=f"{emoji} {result}!", color=discord.Color.gold()))

# ── Falix Server Start ───────────────────────────────────

def start_falix_server_sync(subdomain: str) -> tuple[bool, str]:
    """
    Start a Falix Minecraft server using cloudscraper to bypass Cloudflare.
    Returns (success, message/ip).
    """
    # Clean the subdomain
    clean_subdomain = subdomain.replace(".falix.gg", "").replace(".falixsrv.me", "")
    full_ip = f"{clean_subdomain}.falix.gg"
    url = "https://falixnodes.net/startserver"
    
    # Create cloudscraper session
    scraper = cloudscraper.create_scraper(
        browser={
            'browser': 'chrome',
            'platform': 'windows',
            'mobile': False
        }
    )
    
    # Data to send
    data = {
        "IP": full_ip,
        "cf-turnstile-response": ""
    }
    
    # Headers
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": "https://falixnodes.net/startserver",
        "Origin": "https://falixnodes.net",
    }
    
    try:
        # First GET to bypass Cloudflare
        scraper.get("https://falixnodes.net/startserver", headers=headers)
        
        # POST to start server
        response = scraper.post(url, data=data, headers=headers, allow_redirects=False)
        
        if response.status_code == 303:
            location = response.headers.get("location", "")
            if "queued" in location or "success" in location:
                return True, full_ip
            else:
                return False, f"Unexpected redirect: {location}"
        elif response.status_code == 200:
            if "queued" in response.text or "success" in response.text:
                return True, full_ip
            else:
                return False, "Server responded but status unknown"
        else:
            return False, f"HTTP {response.status_code}"
            
    except Exception as e:
        print(f"Error starting Falix server: {e}")
        return False, str(e)


@bot.tree.command(name="startserver", description="Start a Falix Minecraft server")
async def startserver(interaction: discord.Interaction, subdomain: str):
    """
    Start a Minecraft server hosted on FalixNodes.
    
    Parameters
    ----------
    subdomain : str
        Your server subdomain (e.g., 'serverforall')
    """
    await interaction.response.defer()
    
    # Clean the input
    clean_subdomain = subdomain.replace(".falix.gg", "").replace(".falixsrv.me", "")
    
    if not clean_subdomain or len(clean_subdomain) < 3:
        embed = discord.Embed(
            description="❌ Please provide a valid subdomain.\nExample: `/startserver serverforall`",
            color=discord.Color.red()
        )
        await interaction.followup.send(embed=embed)
        return
    
    full_ip = f"{clean_subdomain}.falix.gg"
    
    # Send initial status
    status_embed = discord.Embed(
        description=f"🔄 Starting server at `{full_ip}`...",
        color=discord.Color.orange()
    )
    await interaction.followup.send(embed=status_embed)
    
    # Run the sync function in a thread pool to avoid blocking
    loop = asyncio.get_event_loop()
    with concurrent.futures.ThreadPoolExecutor() as pool:
        success, message = await loop.run_in_executor(pool, start_falix_server_sync, clean_subdomain)
    
    if success:
        embed = discord.Embed(
            title="✅ Server Starting!",
            description=f"Server at **`{message}`** has been queued for startup.\n\nIt may take 1-2 minutes to come online.",
            color=discord.Color.green()
        )
        embed.add_field(name="Server Address", value=f"`{message}`", inline=False)
        embed.set_footer(text="Join and play!")
    else:
        embed = discord.Embed(
            title="❌ Failed to Start Server",
            description=f"Could not start `{full_ip}`.\n\n**Error:** {message}",
            color=discord.Color.red()
        )
        embed.add_field(
            name="Manual Start",
            value="Try starting it manually:\nhttps://falixnodes.net/dashboard",
            inline=False
        )
    
    await interaction.edit_original_response(embed=embed)

# ── Bot ready ─────────────────────────────────────────────

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user}")

bot.run(DISCORD_TOKEN)
