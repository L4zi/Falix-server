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
# SOPHISTICATED ELO SYSTEM
#
# Three compounding factors:
#
# 1. EXPECTED SCORE (classical Elo probability)
#    E = 1 / (1 + 10^((opponent_elo - your_elo) / 400))
#    Beating a much stronger player yields more; losing costs less.
#
# 2. SCORE DOMINANCE MULTIPLIER (match scoreline)
#    Based on how convincingly you won, inspired by MOV (margin of victory).
#    score_ratio = winner_rounds / total_rounds   (range 0.5 → 1.0)
#    dominance   = 0.5 + score_ratio              (range 1.0 → 1.5)
#    Examples:
#      10-1  → ratio=0.909 → dominance=1.41  (blowout, big bonus)
#      10-8  → ratio=0.556 → dominance=1.06  (close, near neutral)
#      10-9  → ratio=0.526 → dominance=1.03  (squeaker, tiny bonus)
#
# 3. K-FACTOR (confidence weight, decreases as you play more games)
#    K = 40  for first 10 games  (placement phase, volatile)
#    K = 28  for games 11-30     (calibration)
#    K = 20  for 31+ games       (settled rating)
#
# FINAL FORMULA:
#    raw_change = K * dominance * (1 - expected_score)
#    winner gains +raw_change, loser loses -raw_change
#    clamped to min 3, max 35
# ═══════════════════════════════════════════════════════════

def _k_factor(games_played: int) -> float:
    if games_played < 10:
        return 40.0
    if games_played < 30:
        return 28.0
    return 20.0

def _expected_score(player_elo: int, opponent_elo: int) -> float:
    """Probability [0,1] that player beats opponent given elo ratings."""
    return 1.0 / (1.0 + 10 ** ((opponent_elo - player_elo) / 400.0))

def _dominance_multiplier(winner_score: int, loser_score: int) -> float:
    """
    Returns 1.0 – 1.5 based on how dominant the win was.
    A perfect blowout approaches 1.5; a 1-round margin approaches 1.0.
    """
    total = winner_score + loser_score
    if total == 0:
        return 1.0
    ratio = winner_score / total        # 0.5 (closest) → 1.0 (shutout)
    return 0.5 + ratio                  # maps to 1.0 → 1.5

def calc_elo_change(
    winner_elo: int,
    loser_elo: int,
    winner_score: int,
    loser_score: int,
    winner_games_played: int,
    loser_games_played: int,
) -> tuple[int, int]:
    """
    Returns (winner_gain, loser_loss) as positive integers.
    The two values can differ slightly because each player has their own K-factor.
    """
    dom   = _dominance_multiplier(winner_score, loser_score)
    e_win = _expected_score(winner_elo, loser_elo)   # prob winner was expected to win
    e_los = 1.0 - e_win                              # prob loser was expected to win

    k_win = _k_factor(winner_games_played)
    k_los = _k_factor(loser_games_played)

    # Winner always gets (1 - expected) which is higher when they were the underdog
    winner_gain = round(k_win * dom * (1.0 - e_win))
    # Loser loses based on how much they were expected to win (hurts more if favoured)
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

@bot.tree.command(name="recordwin", description="[Admin] Record a skirmish result with score")
async def recordwin(
    interaction: discord.Interaction,
    winner: str,
    loser: str,
    winner_score: int,
    loser_score: int,
):
    """
    winner / loser : player names
    winner_score   : rounds won by winner  (e.g. 10)
    loser_score    : rounds won by loser   (e.g. 3)
    """
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
    data["players"][l]  = max(0, data["players"][l])   # floor at 0

    total_rounds = winner_score + loser_score
    closeness = round((loser_score / total_rounds) * 100)   # % how close the match was

    data["duels"].append({
        "winner": w, "loser": l,
        "score": f"{winner_score}-{loser_score}",
        "w_elo_change": w_gain,
        "l_elo_change": l_loss,
        # keep legacy key so old history still works
        "elo_change": w_gain,
        "date": str(date.today())
    })
    lb_save(data)

    # build match verdict label
    if closeness >= 47:
        verdict = "VERY CLOSE"
    elif closeness >= 35:
        verdict = "COMPETITIVE"
    elif closeness >= 20:
        verdict = "DOMINANT"
    else:
        verdict = "DOMINANT"

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

# ── Truth or Dare ─────────────────────────────────────────

TOD_FILE = "truthordare.json"

def tod_load() -> dict:
    if not os.path.exists(TOD_FILE):
        return {"truths": [], "dares": []}
    with open(TOD_FILE) as f:
        return json.load(f)

def tod_save(data: dict):
    with open(TOD_FILE, "w") as f:
        json.dump(data, f, indent=2)


class TruthOrDareView(discord.ui.View):
    def __init__(self, player1: str, player2: str):
        super().__init__(timeout=120)
        self.player1 = player1
        self.player2 = player2

    @discord.ui.button(label="Truth", style=discord.ButtonStyle.primary)
    async def truth_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = tod_load()
        if not data["truths"]:
            await interaction.response.send_message(
                "No truths added yet. Use `/addtruth` to add some.", ephemeral=True
            )
            return
        question = random.choice(data["truths"])
        embed = discord.Embed(color=0x4361ee)
        embed.add_field(name="TRUTH", value=question, inline=False)
        embed.set_footer(text=f"{self.player1} must answer  ·  big funnys bot")
        await interaction.response.send_message(embed=embed)

    @discord.ui.button(label="Dare", style=discord.ButtonStyle.danger)
    async def dare_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = tod_load()
        if not data["dares"]:
            await interaction.response.send_message(
                "No dares added yet. Use `/adddare` to add some.", ephemeral=True
            )
            return
        # {player1} and {player2} can be used as placeholders in the dare text
        dare = random.choice(data["dares"])
        dare = dare.replace("{player1}", self.player1).replace("{player2}", self.player2)
        embed = discord.Embed(color=0xe5383b)
        embed.add_field(name="DARE", value=dare, inline=False)
        embed.set_footer(text=f"{self.player1} must do this  ·  big funnys bot")
        await interaction.response.send_message(embed=embed)


@bot.tree.command(name="truthordare", description="Start a truth or dare between two players")
async def truthordare(interaction: discord.Interaction, player1: str, player2: str):
    data = tod_load()
    t_count = len(data["truths"])
    d_count = len(data["dares"])
    embed = discord.Embed(
        title="TRUTH OR DARE",
        description=f"**{player1}** — pick your poison.\n\nTruth: answer honestly.\nDare: involves **{player2}**.",
        color=0x1a1a2e
    )
    embed.set_footer(text=f"{t_count} truths  ·  {d_count} dares in pool  ·  big funnys bot")
    await interaction.response.send_message(embed=embed, view=TruthOrDareView(player1, player2))


@bot.tree.command(name="addtruth", description="Add a truth question to the pool")
async def addtruth(interaction: discord.Interaction, question: str):
    data = tod_load()
    data["truths"].append(question)
    tod_save(data)
    await interaction.response.send_message(
        f"Added to truths. Pool now has **{len(data['truths'])}** questions.", ephemeral=True
    )


@bot.tree.command(name="adddare", description="Add a dare to the pool — use {player1} and {player2} as placeholders")
async def adddare(interaction: discord.Interaction, dare: str):
    data = tod_load()
    data["dares"].append(dare)
    tod_save(data)
    await interaction.response.send_message(
        f"Added to dares. Pool now has **{len(data['dares'])}** dares.", ephemeral=True
    )


@bot.tree.command(name="listtruthsdares", description="See all truths and dares in the pool")
async def listtruthsdares(interaction: discord.Interaction):
    data = tod_load()
    embed = discord.Embed(title="TRUTH OR DARE  ·  POOL", color=0x1a1a2e)

    if data["truths"]:
        embed.add_field(
            name=f"TRUTHS ({len(data['truths'])})",
            value="\n".join(f"`{i+1}.` {t}" for i, t in enumerate(data["truths"][:10])) +
                  (f"\n*... and {len(data['truths'])-10} more*" if len(data["truths"]) > 10 else ""),
            inline=False
        )
    else:
        embed.add_field(name="TRUTHS", value="None added yet.", inline=False)

    if data["dares"]:
        embed.add_field(
            name=f"DARES ({len(data['dares'])})",
            value="\n".join(f"`{i+1}.` {d}" for i, d in enumerate(data["dares"][:10])) +
                  (f"\n*... and {len(data['dares'])-10} more*" if len(data["dares"]) > 10 else ""),
            inline=False
        )
    else:
        embed.add_field(name="DARES", value="None added yet.", inline=False)

    embed.set_footer(text="big funnys bot")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="removetruth", description="Remove a truth by its number (use /listtruthsdares to see numbers)")
async def removetruth(interaction: discord.Interaction, number: int):
    data = tod_load()
    if number < 1 or number > len(data["truths"]):
        await interaction.response.send_message(
            f"Invalid number. There are {len(data['truths'])} truths.", ephemeral=True
        )
        return
    removed = data["truths"].pop(number - 1)
    tod_save(data)
    await interaction.response.send_message(f"Removed: *{removed}*", ephemeral=True)


@bot.tree.command(name="removedare", description="Remove a dare by its number (use /listtruthsdares to see numbers)")
async def removedare(interaction: discord.Interaction, number: int):
    data = tod_load()
    if number < 1 or number > len(data["dares"]):
        await interaction.response.send_message(
            f"Invalid number. There are {len(data['dares'])} dares.", ephemeral=True
        )
        return
    removed = data["dares"].pop(number - 1)
    tod_save(data)
    await interaction.response.send_message(f"Removed: *{removed}*", ephemeral=True)


# ── Start Falix Server ───────────────────────────────────
#
# ── Bot ready ─────────────────────────────────────────────




@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user}")

bot.run(DISCORD_TOKEN)
