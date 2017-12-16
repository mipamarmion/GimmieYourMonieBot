from cogs.bank import Bank, InsufficientBalance, NoAccount
from discord.ext import commands
from cogs.utils.dataIO import dataIO
from collections import defaultdict, deque
from datetime import datetime
from .utils import checks
from enum import Enum
from random import shuffle
import os
import logging
import random

default_settings = {"SLOT_MIN": 1, "SLOT_MAX": 999999999999999999, "SLOT_TIME": 0}


class SlotError(Exception):
    pass


class InvalidBid(SlotError):
    pass


NUM_ENC = "\N{COMBINING ENCLOSING KEYCAP}"


class SMReel(Enum):
    wild = "\N{GAME DIE}"
    cherries = "\N{CHERRIES}"
    medal = "\N{SPORTS MEDAL}"
    flc = "\N{FOUR LEAF CLOVER}"
    dollar = "\N{BANKNOTE WITH DOLLAR SIGN}"
    bell = "\N{BELL}"
    moneystack = "\N{MONEY WITH WINGS}"
    heart = "\N{HEAVY BLACK HEART}"
    spade = "\N{BLACK SPADE SUIT}"
    gem = "\N{GEM STONE}"
    moneybag = "\N{MONEY BAG}"
    seven = "\N{DIGIT SEVEN}" + NUM_ENC


SM_REEL_MULTIPLIERS = {
    SMReel.cherries: [0, 0, 2, 5, 10, 100],
    SMReel.medal: [0, 0, 0, 5, 10, 100],
    SMReel.flc: [0, 0, 0, 5, 20, 100],
    SMReel.dollar: [0, 0, 0, 5, 20, 100],
    SMReel.bell: [0, 0, 0, 10, 50, 100],
    SMReel.moneystack: [0, 0, 0, 10, 50, 100],
    SMReel.heart: [0, 0, 0, 20, 80, 120],
    SMReel.spade: [0, 0, 0, 20, 80, 120],
    SMReel.gem: [0, 0, 0, 50, 100, 150],
    SMReel.moneybag: [0, 0, 0, 50, 100, 150],
    SMReel.seven: [0, 0, 10, 50, 100, 300]}


class Payout:
    @staticmethod
    def getsymbolcount(in_line, i):
        count = list([1, SMReel.wild])
        line = list(in_line)

        if line[i] == SMReel.wild:
            for j in range(i, len(line) - 1):
                if line[j] != SMReel.wild:
                    line[i] = line[j]

        count[1] = line[i]

        for j in range(i, len(line) - 1):
            if (line[j] == line[j + 1]) or (line[j + 1] == SMReel.wild):
                line[j + 1] = line[j]
                count[0] += 1
            else:
                return count
        return count

    @staticmethod
    def getmultiplierpayout(symbol, count, bet):
        return [SM_REEL_MULTIPLIERS[symbol][count] * bet, symbol, count]

    @staticmethod
    def getskipcount(i, count, line):
        linePos = i + count - 1
        skip = 0

        while line[linePos] == SMReel.wild:
            skip += 1
            linePos -= 1

        return i + (count - skip)

    @staticmethod
    def getlinepayout(line, bet):
        payout = []
        skip = -1
        for i, symbol in enumerate(line):
            if skip > i or i == 4:
                continue
            count = Payout.getsymbolcount(line, i)

            if count[0] == 2 and (count[1] == SMReel.seven or count[1] == SMReel.cherries):
                payout.append(Payout.getmultiplierpayout(count[1], 2, bet))
            elif count[0] > 2:
                payout.append(Payout.getmultiplierpayout(count[1], count[0], bet))
            if line[i + count[0] - 1] == SMReel.wild:
                skip = Payout.getskipcount(i, count[0], line)
            else:
                skip = i + count[0]
        return payout


class Slots:
    """Slots

    Get rich and have fun with imaginary currency!"""

    def __init__(self, bot):
        self.bot = bot
        self.bank = Bank(bot)
        self.file_path = "data/slots/settings.json"
        self.settings = dataIO.load_json(self.file_path)
        self.settings = defaultdict(lambda: default_settings)
        self.slot_register = defaultdict(dict)

    @commands.group(name="slots", pass_context=True, no_pm=True)
    async def _slots(self, ctx):
        """Slots operations"""
        if ctx.invoked_subcommand is None:
            await self.bot.send_cmd_help(ctx)

    @_slots.command(pass_context=True, no_pm=True)
    async def multislot(self, ctx, bid: int):
        await self.playslot(ctx, bid * 3, True)

    @_slots.command(pass_context=True, no_pm=True)
    async def slot(self, ctx, bid: int):
        await self.playslot(ctx, bid, False)

    async def playslot(self, ctx, bid: int, multislotbool):
        """Play the slot machine"""
        author = ctx.message.author
        server = author.server
        settings = self.settings[server.id]
        valid_bid = settings["SLOT_MIN"] <= bid <= settings["SLOT_MAX"]

        try:
            if not valid_bid:
                raise InvalidBid()
            if not self.bank.can_spend(author, bid):
                raise InsufficientBalance
            await self.slot_machine(author, bid, multislotbool)
        except NoAccount:
            await self.bot.say("{} You need an account to use the slot "
                               "machine. Type `{}bank register` to open one."
                               "".format(author.mention, ctx.prefix))
        except InsufficientBalance:
            await self.bot.say("{} You need an account with enough funds to "
                               "play the slot machine.".format(author.mention))
        except InvalidBid:
            await self.bot.say("Bid must be between {} and {}."
                               "".format(settings["SLOT_MIN"],
                                         settings["SLOT_MAX"]))

    async def slot_machine(self, author, bid, multislot):
        reels = []
        self.slot_register[author.id] = datetime.utcnow()
        for i in range(5):
            default_reel = deque(SMReel)
            if i < 1 or i > 3:
                default_reel.remove(SMReel.wild)

            shuffle(default_reel)
            default_reel.rotate(random.randint(-999, 999))  # weeeeee
            new_reel = deque(default_reel, maxlen=5)  # we need only 5 symbols
            reels.append(new_reel)  # for each reel
        rows = ((reels[0][0], reels[1][0], reels[2][0], reels[3][0], reels[4][0]),
                (reels[0][1], reels[1][1], reels[2][1], reels[3][1], reels[4][1]),
                (reels[0][2], reels[1][2], reels[2][2], reels[3][2], reels[4][2]))

        slot = "WALCUM TO THE SLOTS\n"
        for i, row in enumerate(rows):  # Let's build the slot to show
            sign = "||"
            signi = "||"
            if multislot:
                sign = ">"
                signi = "<"
            elif i == 1:
                sign = ">"
                signi = "<"

            slot += "{}{} {} {} {} {}{}\n".format(sign, *[c.value for c in row], signi)

        if multislot:
            payout = list(Payout.getlinepayout(rows[0], int(bid / 3)))
            payout.extend(Payout.getlinepayout(rows[1], int(bid / 3)))
            payout.extend(Payout.getlinepayout(rows[2], int(bid / 3)))
        else:
            payout = Payout.getlinepayout(rows[1], bid)

        if payout:
            then = self.bank.get_balance(author)
            pay = 0
            for win in payout:
                pay += win[0]
            now = then - bid + pay
            self.bank.set_credits(author, now)
            await self.bot.say("{}\n{} \n{}\nYour total win: {}\nYour bid: {}\n{} → {}!"
                               "".format(slot, Slots.getpayoutsymbols(payout), author.mention, pay, bid, then, now))
        else:
            then = self.bank.get_balance(author)
            self.bank.withdraw_credits(author, bid)
            now = then - bid
            await self.bot.say("{}\n{} Nothing!\nYour bid: {}\n{} → {}!"
                               "".format(slot, author.mention, bid, then, now))

    @staticmethod
    def getpayoutsymbols(payout):
        out = ""
        for i, win in enumerate(payout):
            for j in range(win[2]):
                out += win[1].value
            out += " = multiplier of " + str(SM_REEL_MULTIPLIERS[win[1]][win[2]]) + "\n"
        return out

    @commands.group(pass_context=True, no_pm=True)
    @checks.admin_or_permissions(manage_server=True)
    async def slotsset(self, ctx):
        """Changes slots module settings"""
        server = ctx.message.server
        settings = self.settings[server.id]
        if ctx.invoked_subcommand is None:
            msg = "```"
            for k, v in settings.items():
                msg += "{}: {}\n".format(k, v)
            msg += "```"
            await self.bot.send_cmd_help(ctx)
            await self.bot.say(msg)

    @slotsset.command(pass_context=True)
    async def slotmin(self, ctx, bid: int):
        """Minimum slot machine bid"""
        server = ctx.message.server
        self.settings[server.id]["SLOT_MIN"] = bid
        await self.bot.say("Minimum bid is now {} credits.".format(bid))
        dataIO.save_json(self.file_path, self.settings)

    @slotsset.command(pass_context=True)
    async def slotmax(self, ctx, bid: int):
        """Maximum slot machine bid"""
        server = ctx.message.server
        self.settings[server.id]["SLOT_MAX"] = bid
        await self.bot.say("Maximum bid is now {} credits.".format(bid))
        dataIO.save_json(self.file_path, self.settings)

    @slotsset.command(pass_context=True)
    async def slottime(self, ctx, seconds: int):
        """Seconds between each slots use"""
        server = ctx.message.server
        self.settings[server.id]["SLOT_TIME"] = seconds
        await self.bot.say("Cooldown is now {} seconds.".format(seconds))
        dataIO.save_json(self.file_path, self.settings)


def check_folders():
    if not os.path.exists("data/slots"):
        print("Creating data/slots folder...")
        os.makedirs("data/slots")


def check_files():
    f = "data/slots/settings.json"
    if not dataIO.is_valid_json(f):
        print("Creating default slots's settings.json...")
        dataIO.save_json(f, {})


def setup(bot):
    global logger
    check_folders()
    check_files()
    logger = logging.getLogger("red.slots")
    if logger.level == 0:
        # Prevents the logger from being loaded again in case of module reload
        logger.setLevel(logging.INFO)
        handler = logging.FileHandler(
            filename='data/slots/slots.log', encoding='utf-8', mode='a')
        handler.setFormatter(logging.Formatter(
            '%(asctime)s %(message)s', datefmt="[%d/%m/%Y %H:%M]"))
        logger.addHandler(handler)
    n = Slots(bot)
    bot.add_cog(n)
