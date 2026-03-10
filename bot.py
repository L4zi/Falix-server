import discord
from discord.ext import commands
import os

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

@bot.tree.command(name="startserver", description="Get the link to start the Minecraft server")
async def startserver(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Start the Server",
        description=(
            f"Click the button below, the subdomain is already pre-filled for you!\n\n"
            f"All you need to do:\n"
            f"1. Watch the short ad\n"
            f"2. Complete the CAPTCHA\n"
            f"3. Hit Start Server\n\n"
            f"Server address: {SUBDOMAIN}.falixsrv.me"
        ),
        color=discord.Color.green()
    )
    embed.set_footer(text="Server will stay online until manually stopped or timed out.")
    await interaction.response.send_message(embed=embed, view=ServerStartView())

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user}")

bot.run(DISCORD_TOKEN)