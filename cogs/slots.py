import discord
from discord.ext import commands
from cogs.utils.dataIO import dataIO
from collections import namedtuple, defaultdict, deque
from datetime import datetime
from copy import deepcopy
from .utils import checks
from cogs.utils.chat_formatting import pagify, box
from enum import Enum
from __main__ import send_cmd_help
from random import shuffle
import os
import time
import logging
import random


default_settings = {"SLOT_MIN": 1, "SLOT_MAX": 1000000, "SLOT_TIME": 0,
                    "REGISTER_CREDITS": 100}


class SlotError(Exception):
    pass


class BankError(Exception):
    pass


class AccountAlreadyExists(BankError):
    pass


class NoAccount(BankError):
    pass


class InsufficientBalance(BankError):
    pass


class NegativeValue(BankError):
    pass


class SameSenderAndReceiver(BankError):
    pass


NUM_ENC = "\N{COMBINING ENCLOSING KEYCAP}"


class SMReel(Enum):
    wild      = "\N{GAME DIE}"
    cherries  = "\N{CHERRIES}"
    medal     = "\N{SPORTS MEDAL}"
    flc       = "\N{FOUR LEAF CLOVER}"
    dollar    = "\N{BANKNOTE WITH DOLLAR SIGN}"
    bell      = "\N{BELL}"
    moneystack= "\N{MONEY WITH WINGS}"
    heart     = "\N{HEAVY BLACK HEART}"
    spade     = "\N{BLACK SPADE SUIT}"
    gem       = "\N{GEM STONE}"
    moneybag  = "\N{MONEY BAG}"
    seven     = "\N{DIGIT SEVEN}" + NUM_ENC

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
    def getSymbolCount(in_line, i):
        count = list([1, SMReel.wild])
        line = list(in_line)

        if line[i] == SMReel.wild:
            for j in range(i, len(line) - 1):
                if line[j] != SMReel.wild:
                    line[i] = line[j]

        count[1] = line[i]

        for j in range(i, len(line) - 1):
            if (line[j] == line[j+1]) or (line[j+1] == SMReel.wild):
                line[j + 1] = line[j]
                count[0] += 1
            else:
                return count;
        return count

    def getMultiplierPayout(symbol, count, bet):
        return [SM_REEL_MULTIPLIERS[symbol][count] * bet, symbol, count]

    def getSkipCount(i, count, line):
        linePos = i + count - 1
        skip = 0

        while line[linePos] == SMReel.wild:
            skip += 1
            linePos -= 1

        return i + (count - skip)

    def getLinePayout(line, bet):
        payout = []
        skip = -1
        for i, symbol in enumerate(line):
            if skip > i or i == 4:
                continue
            count = Payout.getSymbolCount(line, i)

            if count[0] == 2 and (count[1] == SMReel.seven or count[1] == SMReel.cherries):
                payout.append(Payout.getMultiplierPayout(count[1], 2, bet))
            elif count[0] > 2:
                payout.append(Payout.getMultiplierPayout(count[1], count[0], bet))
            if line[i + count[0] - 1] == SMReel.wild:
                skip = Payout.getSkipCount(i, count[0], line)
            else:
                skip = i + count[0]
        return payout



class SetParser:
    def __init__(self, argument):
        allowed = ("+", "-")
        if argument and argument[0] in allowed:
            try:
                self.sum = int(argument)
            except:
                raise
            if self.sum < 0:
                self.operation = "withdraw"
            elif self.sum > 0:
                self.operation = "deposit"
            else:
                raise
            self.sum = abs(self.sum)
        elif argument.isdigit():
            self.sum = int(argument)
            self.operation = "set"
        else:
            raise


class Bank:

    def __init__(self, bot, file_path):
        self.accounts = dataIO.load_json(file_path)
        self.bot = bot

    def create_account(self, user, *, initial_balance=0):
        server = user.server
        if not self.account_exists(user):
            if server.id not in self.accounts:
                self.accounts[server.id] = {}
            if user.id in self.accounts:  # Legacy account
                balance = self.accounts[user.id]["balance"]
            else:
                balance = initial_balance
            timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            account = {"name": user.name,
                       "balance": balance,
                       "created_at": timestamp
                       }
            self.accounts[server.id][user.id] = account
            self._save_bank()
            return self.get_account(user)
        else:
            raise AccountAlreadyExists()

    def account_exists(self, user):
        try:
            self._get_account(user)
        except NoAccount:
            return False
        return True

    def withdraw_credits(self, user, amount):
        server = user.server

        if amount < 0:
            raise NegativeValue()

        account = self._get_account(user)
        if account["balance"] >= amount:
            account["balance"] -= amount
            self.accounts[server.id][user.id] = account
            self._save_bank()
        else:
            raise InsufficientBalance()

    def deposit_credits(self, user, amount):
        server = user.server
        if amount < 0:
            raise NegativeValue()
        account = self._get_account(user)
        account["balance"] += amount
        self.accounts[server.id][user.id] = account
        self._save_bank()

    def set_credits(self, user, amount):
        server = user.server
        if amount < 0:
            raise NegativeValue()
        account = self._get_account(user)
        account["balance"] = amount
        self.accounts[server.id][user.id] = account
        self._save_bank()

    def transfer_credits(self, sender, receiver, amount):
        if amount < 0:
            raise NegativeValue()
        if sender is receiver:
            raise SameSenderAndReceiver()
        if self.account_exists(sender) and self.account_exists(receiver):
            sender_acc = self._get_account(sender)
            if sender_acc["balance"] < amount:
                raise InsufficientBalance()
            self.withdraw_credits(sender, amount)
            self.deposit_credits(receiver, amount)
        else:
            raise NoAccount()

    def can_spend(self, user, amount):
        account = self._get_account(user)
        if account["balance"] >= amount:
            return True
        else:
            return False

    def wipe_bank(self, server):
        self.accounts[server.id] = {}
        self._save_bank()

    def get_server_accounts(self, server):
        if server.id in self.accounts:
            raw_server_accounts = deepcopy(self.accounts[server.id])
            accounts = []
            for k, v in raw_server_accounts.items():
                v["id"] = k
                v["server"] = server
                acc = self._create_account_obj(v)
                accounts.append(acc)
            return accounts
        else:
            return []

    def get_all_accounts(self):
        accounts = []
        for server_id, v in self.accounts.items():
            server = self.bot.get_server(server_id)
            if server is None:
                # Servers that have since been left will be ignored
                # Same for users_id from the old bank format
                continue
            raw_server_accounts = deepcopy(self.accounts[server.id])
            for k, v in raw_server_accounts.items():
                v["id"] = k
                v["server"] = server
                acc = self._create_account_obj(v)
                accounts.append(acc)
        return accounts

    def get_balance(self, user):
        account = self._get_account(user)
        return account["balance"]

    def get_account(self, user):
        acc = self._get_account(user)
        acc["id"] = user.id
        acc["server"] = user.server
        return self._create_account_obj(acc)

    def _create_account_obj(self, account):
        account["member"] = account["server"].get_member(account["id"])
        account["created_at"] = datetime.strptime(account["created_at"],
                                                  "%Y-%m-%d %H:%M:%S")
        Account = namedtuple("Account", "id name balance "
                             "created_at server member")
        return Account(**account)

    def _save_bank(self):
        dataIO.save_json("data/slots/bank.json", self.accounts)

    def _get_account(self, user):
        server = user.server
        try:
            return deepcopy(self.accounts[server.id][user.id])
        except KeyError:
            raise NoAccount()


class Slots:
    """Slots

    Get rich and have fun with imaginary currency!"""

    def __init__(self, bot):
        global default_settings
        self.bot = bot
        self.bank = Bank(bot, "data/slots/bank.json")
        self.file_path = "data/slots/settings.json"
        self.settings = dataIO.load_json(self.file_path)
        self.settings = defaultdict(lambda: default_settings, self.settings)
        self.slot_register = defaultdict(dict)

    @commands.group(name="bank", pass_context=True)
    async def _bank(self, ctx):
        """Bank operations"""
        if ctx.invoked_subcommand is None:
            await send_cmd_help(ctx)

    @_bank.command(pass_context=True, no_pm=True)
    async def register(self, ctx):
        """Registers an account at the GetLucky bank"""
        settings = self.settings[ctx.message.server.id]
        author = ctx.message.author
        credits = 0
        if ctx.message.server.id in self.settings:
            credits = settings.get("REGISTER_CREDITS", 0)
        try:
            account = self.bank.create_account(author, initial_balance=credits)
            await self.bot.say("{} Account opened. Current balance: {}"
                               "".format(author.mention, account.balance))
        except AccountAlreadyExists:
            await self.bot.say("{} You already have an account at the"
                               " GetLucky bank.".format(author.mention))

    @_bank.command(pass_context=True)
    async def balance(self, ctx, user: discord.Member=None):
        """Shows balance of user.

        Defaults to yours."""
        if not user:
            user = ctx.message.author
            try:
                await self.bot.say("{} Your balance is: {}".format(
                    user.mention, self.bank.get_balance(user)))
            except NoAccount:
                await self.bot.say("{} You don't have an account at the"
                                   " GetLucky bank. Type `{}bank register`"
                                   " to open one.".format(user.mention,
                                                          ctx.prefix))
        else:
            try:
                await self.bot.say("{}'s balance is {}".format(
                    user.name, self.bank.get_balance(user)))
            except NoAccount:
                await self.bot.say("That user has no bank account.")

    @_bank.command(pass_context=True)
    async def transfer(self, ctx, user: discord.Member, sum: int):
        """Transfer credits to other users"""
        author = ctx.message.author
        try:
            self.bank.transfer_credits(author, user, sum)
            logger.info("{}({}) transferred {} credits to {}({})".format(
                author.name, author.id, sum, user.name, user.id))
            await self.bot.say("{} credits have been transferred to {}'s"
                               " account.".format(sum, user.name))
        except NegativeValue:
            await self.bot.say("You need to transfer at least 1 credit.")
        except SameSenderAndReceiver:
            await self.bot.say("You can't transfer credits to yourself.")
        except InsufficientBalance:
            await self.bot.say("You don't have that sum in your bank account.")
        except NoAccount:
            await self.bot.say("That user has no bank account.")

    @_bank.command(name="set", pass_context=True)
    @checks.admin_or_permissions(manage_server=True)
    async def _set(self, ctx, user: discord.Member, credits: SetParser):
        """Sets credits of user's bank account. See help for more operations

        Passing positive and negative values will add/remove credits instead

        Examples:
            bank set @GetLucky 26 - Sets 26 credits
            bank set @GetLucky +2 - Adds 2 credits
            bank set @GetLucky -6 - Removes 6 credits"""
        author = ctx.message.author
        try:
            if credits.operation == "deposit":
                self.bank.deposit_credits(user, credits.sum)
                logger.info("{}({}) added {} credits to {} ({})".format(
                    author.name, author.id, credits.sum, user.name, user.id))
                await self.bot.say("{} credits have been added to {}"
                                   "".format(credits.sum, user.name))
            elif credits.operation == "withdraw":
                self.bank.withdraw_credits(user, credits.sum)
                logger.info("{}({}) removed {} credits to {} ({})".format(
                    author.name, author.id, credits.sum, user.name, user.id))
                await self.bot.say("{} credits have been withdrawn from {}"
                                   "".format(credits.sum, user.name))
            elif credits.operation == "set":
                self.bank.set_credits(user, credits.sum)
                logger.info("{}({}) set {} credits to {} ({})"
                            "".format(author.name, author.id, credits.sum,
                                      user.name, user.id))
                await self.bot.say("{}'s credits have been set to {}".format(
                    user.name, credits.sum))
        except InsufficientBalance:
            await self.bot.say("User doesn't have enough credits.")
        except NoAccount:
            await self.bot.say("User has no bank account.")


    @_bank.command(pass_context=True, no_pm=True)
    @checks.serverowner_or_permissions(administrator=True)
    async def reset(self, ctx, confirmation: bool=False):
        """Deletes all server's bank accounts"""
        if confirmation is False:
            await self.bot.say("This will delete all bank accounts on "
                               "this server.\nIf you're sure, type "
                               "{}bank reset yes".format(ctx.prefix))
        else:
            self.bank.wipe_bank(ctx.message.server)
            await self.bot.say("All bank accounts of this server have been "
                               "deleted.")

    @commands.group(pass_context=True)
    async def leaderboard(self, ctx):
        """Server / global leaderboard

        Defaults to server"""
        if ctx.invoked_subcommand is None:
            await ctx.invoke(self._server_leaderboard)


    @leaderboard.command(name="server", pass_context=True)
    async def _server_leaderboard(self, ctx, top: int=10):
        """Prints out the server's leaderboard

        Defaults to top 10"""
        # Originally coded by Airenkun - edited by irdumb
        server = ctx.message.server
        if top < 1:
            top = 10
        bank_sorted = sorted(self.bank.get_server_accounts(server),
                             key=lambda x: x.balance, reverse=True)
        bank_sorted = [a for a in bank_sorted if a.member] #  exclude users who left
        if len(bank_sorted) < top:
            top = len(bank_sorted)
        topten = bank_sorted[:top]
        highscore = ""
        place = 1
        for acc in topten:
            highscore += str(place).ljust(len(str(top)) + 1)
            highscore += (str(acc.member.display_name) + " ").ljust(23 - len(str(acc.balance)))
            highscore += str(acc.balance) + "\n"
            place += 1
        if highscore != "":
            for page in pagify(highscore, shorten_by=12):
                await self.bot.say(box(page, lang="py"))
        else:
            await self.bot.say("There are no accounts in the bank.")

    @leaderboard.command(name="global")
    async def _global_leaderboard(self, top: int=10):
        """Prints out the global leaderboard

        Defaults to top 10"""
        if top < 1:
            top = 10
        bank_sorted = sorted(self.bank.get_all_accounts(),
                             key=lambda x: x.balance, reverse=True)
        bank_sorted = [a for a in bank_sorted if a.member] #  exclude users who left
        unique_accounts = []
        for acc in bank_sorted:
            if not self.already_in_list(unique_accounts, acc):
                unique_accounts.append(acc)
        if len(unique_accounts) < top:
            top = len(unique_accounts)
        topten = unique_accounts[:top]
        highscore = ""
        place = 1
        for acc in topten:
            highscore += str(place).ljust(len(str(top)) + 1)
            highscore += ("{} |{}| ".format(acc.member, acc.server)
                          ).ljust(23 - len(str(acc.balance)))
            highscore += str(acc.balance) + "\n"
            place += 1
        if highscore != "":
            for page in pagify(highscore, shorten_by=12):
                await self.bot.say(box(page, lang="py"))
        else:
            await self.bot.say("There are no accounts in the bank.")

    def already_in_list(self, accounts, user):
        for acc in accounts:
            if user.id == acc.id:
                return True
        return False

    @commands.command()
    async def payouts(self):
        """Shows slot machine payouts"""
        await self.bot.whisper(SLOT_PAYOUTS_MSG)

    @commands.command(pass_context=True, no_pm=True)
    async def multislot(self, ctx, bid:int):
        await self.playslot(ctx, bid * 3, 1)

    @commands.command(pass_context=True, no_pm=True)
    async def slot(self, ctx, bid: int):
        await self.playslot(ctx, bid, 0)

    async def playslot(self, ctx, bid: int, multislotbool):
        """Play the slot machine"""
        author = ctx.message.author
        server = author.server
        settings = self.settings[server.id]
        valid_bid = settings["SLOT_MIN"] <= bid and bid <= settings["SLOT_MAX"]
        slot_time = settings["SLOT_TIME"]
        last_slot = self.slot_register.get(author.id)
        now = datetime.utcnow()
        try:
            if last_slot:
                if (now - last_slot).seconds < slot_time:
                    raise OnCooldown()
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
        except OnCooldown:
            await self.bot.say("Slot machine is still cooling off! Wait {} "
                               "seconds between each pull".format(slot_time))
        except InvalidBid:
            await self.bot.say("Bid must be between {} and {}."
                               "".format(settings["SLOT_MIN"],
                                         settings["SLOT_MAX"]))

    async def slot_machine(self, author, bid, multislot):
        default_reel = deque(SMReel)
        reels = []
        self.slot_register[author.id] = datetime.utcnow()
        for i in range(5):
            default_reel = deque(SMReel)
            if i < 1 or i > 3:
                default_reel.remove(SMReel.wild)

            shuffle(default_reel)
            default_reel.rotate(random.randint(-999, 999)) # weeeeee
            new_reel = deque(default_reel, maxlen=5) # we need only 5 symbols
            reels.append(new_reel)                   # for each reel
        rows = ((reels[0][0], reels[1][0], reels[2][0], reels[3][0], reels[4][0]),
                (reels[0][1], reels[1][1], reels[2][1], reels[3][1], reels[4][1]),
                (reels[0][2], reels[1][2], reels[2][2], reels[3][2], reels[4][2]))

        slot = "WALCUM TO THE SLOTS\n"
        for i, row in enumerate(rows): # Let's build the slot to show
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
            payout = list(Payout.getLinePayout(rows[0], int(bid/3)))
            payout.extend(Payout.getLinePayout(rows[1], int(bid/3)))
            payout.extend(Payout.getLinePayout(rows[2], int(bid/3)))
        else:
            payout = Payout.getLinePayout(rows[1], bid)

        if payout != []:
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
            await send_cmd_help(ctx)
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

    f = "data/slots/bank.json"
    if not dataIO.is_valid_json(f):
        print("Creating empty bank.json...")
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
    bot.add_cog(Slots(bot))
