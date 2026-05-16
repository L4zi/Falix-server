import discord
from discord.ext import commands
import os
import sys
import asyncio
import random
import json
import aiohttp
from dotenv import load_dotenv
from sounds import SOUNDS

load_dotenv()

DISCORD_TOKEN = os.environ["BOT_TOKEN"]
HF_TOKEN = os.environ.get("HF_TOKEN", "")

SOUND_CHANNEL_ID = 1477710142425403523
LEADERBOARD_FILE = "leaderboard.json"
ADMIN_ID = 1176877411934154806

DEFAULT_ELO = 100
BASE_GAIN   = 10   # elo gained/lost in a perfectly even match

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
#
# Default ELO: 100
# Base gain/loss: ±10 for an even match
# Adjustment: scaled by elo difference between the two players
#
#   diff = winner_elo - loser_elo
#   change = BASE_GAIN + round(diff * -0.04)
#   clamped to min 4, max 18
#
# So beating someone way above you = ~+14-18
#    losing to someone way below you = ~-14-18
#    beating someone way below you  = ~+4
#    losing to someone way above you = ~-4
# ═══════════════════════════════════════════════════════════

def calc_elo_change(winner_elo: int, loser_elo: int) -> int:
    diff = winner_elo - loser_elo          # positive = winner is stronger
    change = BASE_GAIN + round(diff * -0.04)
    return max(4, min(18, change))

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
    """Case-insensitive lookup; returns canonical name or None."""
    for p in data["players"]:
        if p.lower() == name.lower():
            return p
    return None

def lb_sorted(data: dict) -> list:
    """Returns list of (name, elo) tuples sorted by elo desc."""
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
            sign   = "+" if result == "W" else "-"
            lines.append(f"{result}   {opp:<16} {sign}{d['elo_change']} ELO   {d['date']}")
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
    embed.set_footer(text=f"Showing {end-start} sounds  •  Page {page+1} of {TOTAL_PAGES}")
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

@bot.tree.command(name="recordwin", description="[Admin] Record a skirmish result")
async def recordwin(interaction: discord.Interaction, winner: str, loser: str):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this.", ephemeral=True)
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
    change = calc_elo_change(data["players"][w], data["players"][l])
    data["players"][w] += change
    data["players"][l] -= change
    data["duels"].append({
        "winner": w, "loser": l,
        "elo_change": change,
        "date": str(date.today())
    })
    lb_save(data)

    embed = discord.Embed(title="DUEL RECORDED", color=0x00d26a)
    embed.add_field(name="Winner",         value=w,                              inline=True)
    embed.add_field(name="Loser",          value=l,                              inline=True)
    embed.add_field(name="ELO Shift",      value=f"+{change} / -{change}",       inline=True)
    embed.add_field(name=f"{w} — New ELO", value=str(data["players"][w]),        inline=True)
    embed.add_field(name=f"{l} — New ELO", value=str(data["players"][l]),        inline=True)
    embed.add_field(name="\u200b",        value="\u200b",                      inline=True)
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
    w, l, change = last["winner"], last["loser"], last["elo_change"]
    if w in data["players"]: data["players"][w] -= change
    if l in data["players"]: data["players"][l] += change
    lb_save(data)
    await interaction.response.send_message(
        f"↩️ Undone: **{w}** beat **{l}** on {last['date']}  (ELO reversed ±{change})", ephemeral=True
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

# ── Ask AI ────────────────────────────────────────────────

async def hf_chat(prompt: str) -> str:
    url = "https://api-inference.huggingface.co/models/deepseek-ai/DeepSeek-V3-0324/v1/chat/completions"
    headers = {"Authorization": f"Bearer {HF_TOKEN}", "Content-Type": "application/json"}
    payload = {"model": "deepseek-ai/DeepSeek-V3-0324", "messages": [{"role": "user", "content": prompt}], "max_tokens": 1024}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload) as resp:
            data = await resp.json()
            if resp.status != 200:
                raise Exception(data.get("error", {}).get("message", str(data)))
            return data["choices"][0]["message"]["content"]

@bot.tree.command(name="ask", description="Ask an AI a question")
async def ask(interaction: discord.Interaction, prompt: str):
    await interaction.response.defer()
    try:
        answer = await hf_chat(prompt)
        if len(answer) > 3900: answer = answer[:3900] + "…"
        embed = discord.Embed(description=f"**{prompt}**\n\n{answer}", color=discord.Color.blue())
        embed.set_footer(text="big funnys bot")
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ Failed: `{e}`", ephemeral=True)

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

# ── Bot ready ─────────────────────────────────────────────

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user}")

bot.run(DISCORD_TOKEN)
