import discord
from discord.ext import commands
import os
import asyncio
import io
import random
import aiohttp
from concurrent.futures import ThreadPoolExecutor
from huggingface_hub import InferenceClient
from PIL import Image
from sounds import SOUNDS

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
HF_TOKEN = os.getenv("HF_TOKEN")
SUBDOMAIN = os.getenv("FALIX_SUBDOMAIN")
FULL_IP = f"{SUBDOMAIN}.falixsrv.me"

SOUND_CHANNEL_ID = 1477710142425403523

image_client = InferenceClient(provider="hf-inference", api_key=HF_TOKEN)
chat_client = InferenceClient(api_key=HF_TOKEN)
executor = ThreadPoolExecutor()

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

SIXTYSEVEN_GIFS = [
    "https://giphy.com/gifs/67-six-seven-john-chungus-08uBcURaMq6vA93TGc",
    "https://giphy.com/gifs/rigby-rigbycat-67-8rN9VXNb7dfU792YQt",
    "https://giphy.com/gifs/cat-nub-nubcat-TKa7fQzChHylCQ89to",
    "https://giphy.com/gifs/brainrot-67-hate-MKUOUJrFldIyi2hJyT",
    "https://giphy.com/gifs/argentina-vtuber-mialygosa-B4VWevk4w1a95oBHqv",
]

# ── Soundboard state ──────────────────────────────────────

soundboard_locked = False       # Global lock flag
soundboard_locked_by = None     # Who locked it (user display name)
SOUNDS_PER_PAGE = 12            # 4 rows × 3 buttons
LOCK_ALLOWED_IDS = {1176877411934154806, 1338451898952581165}  # Users who can lock/unlock

SOUND_NAMES = list(SOUNDS.keys())
TOTAL_PAGES = (len(SOUND_NAMES) + SOUNDS_PER_PAGE - 1) // SOUNDS_PER_PAGE

# Button style rotation so pages look colourful & organised
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

    # "67" gif trigger
    if "67" in message.content:
        gif = random.choice(SIXTYSEVEN_GIFS)
        await message.channel.send(gif)

    await bot.process_commands(message)

# ── Falix server start ────────────────────────────────────

async def start_falix_server():
    url = "https://falixnodes.net/startserver"
    data = {"IP": FULL_IP, "cf-turnstile-response": ""}
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": "https://falixnodes.net/startserver",
        "Origin": "https://falixnodes.net",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, data=data, headers=headers, allow_redirects=False) as r:
            location = r.headers.get("location", "")
            if "success" in location:
                return "started"
            elif "queued" in location:
                return "queued"
            else:
                return "failed"

@bot.tree.command(name="startserver", description="Start the Minecraft server")
async def startserver(interaction: discord.Interaction):
    await interaction.response.defer()
    result = await start_falix_server()
    if result == "started":
        embed = discord.Embed(description=f"✅ Server is starting!\n`{FULL_IP}`", color=discord.Color.green())
    elif result == "queued":
        embed = discord.Embed(description=f"⏳ Server is queued and will start shortly!\n`{FULL_IP}`", color=discord.Color.yellow())
    else:
        embed = discord.Embed(description="❌ Failed to start the server. Try again.", color=discord.Color.red())
    await interaction.followup.send(embed=embed)

# ── Soundboard helpers ────────────────────────────────────

async def play_sound(guild, sound_name):
    """Connect if needed, stop any current audio, then play the requested sound."""
    source = SOUNDS[sound_name]
    vc = guild.voice_client

    if vc is None:
        channel = guild.get_channel(SOUND_CHANNEL_ID)
        if channel is None:
            raise Exception("Bot is not in a voice channel! Use /joinvoice first.")
        vc = await channel.connect()

    # Stop whatever is currently playing so the new sound starts immediately
    if vc.is_playing():
        vc.stop()

    if source.startswith("http"):
        audio = discord.FFmpegPCMAudio(
            source,
            before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
        )
    else:
        audio = discord.FFmpegPCMAudio(source)

    vc.play(audio)


def make_soundboard_embed(page: int) -> discord.Embed:
    """Build the embed for a given soundboard page."""
    lock_status = (
        f"🔒 **Locked** by {soundboard_locked_by}" if soundboard_locked
        else "🔓 Unlocked"
    )
    embed = discord.Embed(
        title="🎵 Soundboard",
        description=(
            f"Click a button to play a sound in voice.\n"
            f"Status: {lock_status}"
        ),
        color=discord.Color.red() if soundboard_locked else discord.Color.orange()
    )
    # List sounds on this page in the embed for clarity
    start = page * SOUNDS_PER_PAGE
    end = min(start + SOUNDS_PER_PAGE, len(SOUND_NAMES))
    names_on_page = SOUND_NAMES[start:end]
    embed.add_field(
        name=f"Page {page + 1} / {TOTAL_PAGES}",
        value="\n".join(f"• {n.title()}" for n in names_on_page),
        inline=False
    )
    embed.set_footer(text=f"Showing {len(names_on_page)} sounds  •  Page {page + 1} of {TOTAL_PAGES}")
    return embed


# ── Soundboard UI components ──────────────────────────────

class SoundButton(discord.ui.Button):
    def __init__(self, sound_name: str, style: discord.ButtonStyle):
        label = sound_name.title()
        if len(label) > 80:
            label = label[:77] + "…"
        super().__init__(label=label, style=style, custom_id=f"sb_{sound_name[:80]}")
        self.sound_name = sound_name

    async def callback(self, interaction: discord.Interaction):
        if soundboard_locked:
            await interaction.response.send_message(
                f"🔒 Soundboard is locked by **{soundboard_locked_by}**!", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await play_sound(interaction.guild, self.sound_name)
            await interaction.followup.send(f"▶️ Now playing **{self.label}**!", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Failed: `{e}`", ephemeral=True)


class NavButton(discord.ui.Button):
    def __init__(self, arrow: str, target_page: int):
        super().__init__(label=arrow, style=discord.ButtonStyle.secondary)
        self.target_page = target_page

    async def callback(self, interaction: discord.Interaction):
        view = SoundboardView(self.target_page)
        embed = make_soundboard_embed(self.target_page)
        await interaction.response.edit_message(embed=embed, view=view)


class LockToggleButton(discord.ui.Button):
    def __init__(self):
        label = "🔓 Unlock" if soundboard_locked else "🔒 Lock"
        style = discord.ButtonStyle.danger if not soundboard_locked else discord.ButtonStyle.success
        super().__init__(label=label, style=style)

    async def callback(self, interaction: discord.Interaction):
        global soundboard_locked, soundboard_locked_by

        if interaction.user.id not in LOCK_ALLOWED_IDS:
            await interaction.response.send_message(
                "❌ You don't have permission to lock/unlock the soundboard!", ephemeral=True
            )
            return

        soundboard_locked = not soundboard_locked
        if soundboard_locked:
            soundboard_locked_by = interaction.user.display_name
            msg = f"🔒 Soundboard locked by **{soundboard_locked_by}**"
        else:
            msg = f"🔓 Soundboard unlocked by **{interaction.user.display_name}**"
            soundboard_locked_by = None

        # Detect current page from sibling NavButtons
        current_page = 0
        for item in self.view.children:
            if isinstance(item, NavButton) and item.label == "◀":
                current_page = item.target_page + 1
                break

        view = SoundboardView(current_page)
        embed = make_soundboard_embed(current_page)
        await interaction.response.edit_message(embed=embed, view=view)
        await interaction.followup.send(msg, ephemeral=True)


class SoundboardView(discord.ui.View):
    def __init__(self, page: int = 0):
        super().__init__(timeout=300)
        self.page = page

        start = page * SOUNDS_PER_PAGE
        end = min(start + SOUNDS_PER_PAGE, len(SOUND_NAMES))
        page_sounds = SOUND_NAMES[start:end]

        for i, name in enumerate(page_sounds):
            style = BUTTON_STYLES[i % len(BUTTON_STYLES)]
            self.add_item(SoundButton(name, style))

        # Navigation row: ◀  🔒/🔓  ▶
        nav_row = []
        if page > 0:
            nav_row.append(NavButton("◀", page - 1))
        nav_row.append(LockToggleButton())
        if page < TOTAL_PAGES - 1:
            nav_row.append(NavButton("▶", page + 1))

        for btn in nav_row:
            self.add_item(btn)

# ── Soundboard commands ───────────────────────────────────

@bot.tree.command(name="soundboard", description="Open the soundboard")
async def soundboard(interaction: discord.Interaction):
    view = SoundboardView(page=0)
    embed = make_soundboard_embed(page=0)
    await interaction.response.send_message(embed=embed, view=view)

@bot.tree.command(name="joinvoice", description="Make the bot join a voice channel")
async def joinvoice(interaction: discord.Interaction, channel_id: str = None):
    if channel_id is not None:
        channel = bot.get_channel(int(channel_id))
        if channel is None or not isinstance(channel, discord.VoiceChannel):
            await interaction.response.send_message("❌ Invalid channel ID!", ephemeral=True)
            return
    else:
        channel = bot.get_channel(SOUND_CHANNEL_ID)
        if channel is None:
            await interaction.response.send_message("❌ Default voice channel not found!", ephemeral=True)
            return
    if interaction.guild.voice_client:
        await interaction.guild.voice_client.move_to(channel)
        await interaction.response.send_message(f"✅ Moved to **{channel.name}**!", ephemeral=True)
        return
    await channel.connect()
    await interaction.response.send_message(f"✅ Joined **{channel.name}**!", ephemeral=True)

@bot.tree.command(name="leavevoice", description="Make the bot leave the voice channel")
async def leavevoice(interaction: discord.Interaction):
    if interaction.guild.voice_client is None:
        await interaction.response.send_message("❌ Not in a voice channel!", ephemeral=True)
        return
    await interaction.guild.voice_client.disconnect()
    await interaction.response.send_message("✅ Left the voice channel!", ephemeral=True)

# ── Image generation ──────────────────────────────────────

def generate_image_text(prompt):
    image = image_client.text_to_image(prompt, model="black-forest-labs/FLUX.1-schnell")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    buf.seek(0)
    return buf

def generate_image_text(prompt):
    image = image_client.text_to_image(
        prompt,
        model="stabilityai/stable-diffusion-xl-base-1.0"
    )
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    buf.seek(0)
    return buf

class ImagineView(discord.ui.View):
    def __init__(self, prompt):
        super().__init__(timeout=60)
        self.prompt = prompt

    @discord.ui.button(label="🖼️ Text to Image", style=discord.ButtonStyle.primary)
    async def text_to_image_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        try:
            loop = asyncio.get_event_loop()
            buf = await loop.run_in_executor(executor, generate_image_text, self.prompt)
            file = discord.File(fp=buf, filename="image.png")
            embed = discord.Embed(description=f"**{self.prompt}**", color=discord.Color.purple())
            embed.set_image(url="attachment://image.png")
            embed.set_footer(text="Text-to-Image •")
            await interaction.followup.send(embed=embed, file=file)
        except Exception as e:
            await interaction.followup.send(f"❌ Failed: `{e}`", ephemeral=True)

    @discord.ui.button(label="🔄 Image to Image", style=discord.ButtonStyle.secondary)
    async def image_to_image_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("📎 Send an image in this channel and I'll edit it!", ephemeral=True)

        def check(m):
            return m.author.id == interaction.user.id and len(m.attachments) > 0

        try:
            msg = await bot.wait_for("message", check=check, timeout=60)
            await msg.add_reaction("⏳")
            image_bytes = await msg.attachments[0].read()
            loop = asyncio.get_event_loop()
            buf = await loop.run_in_executor(executor, generate_image_from_image, self.prompt, image_bytes)
            file = discord.File(fp=buf, filename="image.png")
            embed = discord.Embed(description=f"**{self.prompt}**", color=discord.Color.purple())
            embed.set_image(url="attachment://image.png")
            embed.set_footer(text="Image-to-Image •")
            await msg.reply(embed=embed, file=file)
            await msg.remove_reaction("⏳", bot.user)
        except asyncio.TimeoutError:
            await interaction.followup.send("⏱️ Timed out waiting for an image.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Failed: `{e}`", ephemeral=True)

@bot.tree.command(name="imagine", description="Generate an AI image")
async def imagine(interaction: discord.Interaction, prompt: str):
    embed = discord.Embed(
        description=f"**{prompt}**\n\nChoose a mode:",
        color=discord.Color.purple()
    )
    await interaction.response.send_message(embed=embed, view=ImagineView(prompt))

# ── Ask AI ────────────────────────────────────────────────

@bot.tree.command(name="ask", description="Ask an AI a question")
async def ask(interaction: discord.Interaction, prompt: str):
    await interaction.response.defer()
    try:
        result = chat_client.chat.completions.create(
            model="deepseek-ai/DeepSeek-V3-0324",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1024
        )
        answer = result.choices[0].message.content
        embed = discord.Embed(description=f"**{prompt}**\n\n{answer}", color=discord.Color.blue())
        embed.set_footer(text="Powered by FALIX BOT")
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ Failed: `{e}`", ephemeral=True)

# ── Coinflip ──────────────────────────────────────────────

@bot.tree.command(name="coinflip", description="Flip a coin")
async def coinflip(interaction: discord.Interaction):
    await interaction.response.defer()
    suspense = ["🪙 Flipping the coin...", "🌀 It's spinning in the air...", "😮 Almost there..."]
    msg = await interaction.followup.send(suspense[0])
    await asyncio.sleep(1.2)
    await msg.edit(content=suspense[1])
    await asyncio.sleep(1.2)
    await msg.edit(content=suspense[2])
    await asyncio.sleep(1.2)
    result = random.choice(["Heads", "Tails"])
    emoji = "👑" if result == "Heads" else "✨"
    embed = discord.Embed(title=f"{emoji} {result}!", color=discord.Color.gold())
    await msg.edit(content=None, embed=embed)

# ── Bot ready ─────────────────────────────────────────────

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user}")

bot.run(DISCORD_TOKEN)