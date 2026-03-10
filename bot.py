import discord
from discord.ext import commands
import os
import asyncio
import aiohttp

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
SUBDOMAIN = os.getenv("FALIX_SUBDOMAIN")
FULL_IP = f"{SUBDOMAIN}.falixsrv.me"

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

async def start_falix_server():
    url = "https://falixnodes.net/startserver"
    data = {
        "IP": FULL_IP,
        "cf-turnstile-response": ""
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": "https://falixnodes.net/startserver",
        "Origin": "https://falixnodes.net",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, data=data, headers=headers, allow_redirects=False) as r:
            location = r.headers.get("location", "")
            return "success" in location

@bot.tree.command(name="startserver", description="Start the Minecraft server")
async def startserver(interaction: discord.Interaction):
    await interaction.response.defer()
    success = await start_falix_server()
    if success:
        embed = discord.Embed(
            description=f"Server is starting!\n`{FULL_IP}`",
            color=discord.Color.green()
        )
    else:
        embed = discord.Embed(
            description="Failed to start the server. Try again.",
            color=discord.Color.red()
        )
    await interaction.followup.send(embed=embed)
    await asyncio.sleep(15)
    await interaction.delete_original_response()

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user}")

bot.run(DISCORD_TOKEN)