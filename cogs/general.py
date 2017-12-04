import discord
from discord.ext import commands


class General:
    """General commands."""

    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def hello(self):
            await self.bot.say('Hello World!')

def setup(bot):
    n = General(bot)
    bot.add_cog(n)
