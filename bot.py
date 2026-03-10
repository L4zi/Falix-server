import discord
from discord.ext import commands
import os
import asyncio

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
SUBDOMAIN = os.getenv("FALIX_SUBDOMAIN")
START_URL = f"https://falixnodes.net/startserver?ip={SUBDOMAIN}.falixsrv.me"

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

class ServerStartView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Start Server",
            style=discord.ButtonStyle.link,
            url=START_URL,
            emoji="🚀"
        ))

@bot.tree.command(name="startserver", description="Start the Minecraft server")
async def startserver(interaction: discord.Interaction):
    embed = discord.Embed(
        description="Watch the ad, solve the CAPTCHA, hit **Start**.\n\n*This message deletes in 15 seconds.*",
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed, view=ServerStartView())
    await asyncio.sleep(15)
    await interaction.delete_original_response()

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user}")

bot.run(DISCORD_TOKEN)