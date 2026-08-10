# -*- coding: utf-8 -*-
"""
两人死斗 Cog —— /死斗 命令，邀请对手进行石头剪刀布三局两胜
"""

import discord
from discord import app_commands
from discord.ext import commands
import logging
import random

from src.chat.features.games.services.duel_service import duel_service
from src.chat.features.games.config.games_config import DEFAULT_BET
from src.chat.features.games.ui.duel_view import DuelReadyView

log = logging.getLogger(__name__)

# 20 条小春娘围观台词，死斗开场随机抽一条
DUEL_QUOTES = [
    "哦哦，要打起来了嘛，我盯着呢。<吃瓜>",
    "嘛，又是你们两个。赌注我帮你们记好了哦。<偷笑>",
    "诶嘿，这次谁赢？我押...不告诉你。<得意>",
    "呀，这么认真？那我认真围观。<微笑>",
    "哼，打起来才好，省得闲着。<鬼脸>",
    "嘛，输的人别哭鼻子哦。<尴尬赞>",
    "哦哦！这个阵仗，我有预感今天有好戏。<好奇>",
    "你们两个，别打太狠，我还要陪你们聊天呢。<委屈>",
    "呀，赌命的？胆子挺大嘛。<惊讶>",
    "嘛，我见过大场面的，但这个还是有点意思。<吃瓜>",
    "诶？都赌命？那我可要好好看了。<疑惑>",
    "哼，赢了请我吃火锅哦。<抱抱>",
    "哦哦，三局两胜，别第一局就泄气。<赞>",
    "呀，你俩的表情都好严肃，我忍不住想笑。<偷笑>",
    "嘛，不管谁赢，我都陪输的人聊两句。<微笑>",
    "诶嘿，我偷偷猜了个赢家，不告诉你们。<得意>",
    "哦哦，开始吧开始吧，我等不及了。<开心>",
    "呀，赌命的人真勇敢，还是真傻？<鄙视>",
    "嘛，输赢都是朋友，但赢了更好对吧？<乖巧>",
    "哼，我赌小春娘赢——哦，我不参与，围观。<鬼脸>",
]


class DuelCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="死斗", description="两人死斗！石头剪刀布三局两胜，赌币或赌命")
    @app_commands.describe(
        opponent="你的对手（@对方）",
        bet="下注金额（春春币），不填默认 50",
        life_gamble="赌命模式：输了禁言，赢了得奖励",
    )
    async def duel(
        self,
        interaction: discord.Interaction,
        opponent: discord.Member,
        bet: int = DEFAULT_BET,
        life_gamble: bool = False,
    ):
        user_id = interaction.user.id
        opp_id = opponent.id

        if opp_id == user_id:
            await interaction.response.send_message("不能和自己死斗哦。", ephemeral=True)
            return
        if opp_id == self.bot.user.id:
            await interaction.response.send_message("小春娘不参与死斗，但她会围观。<偷笑>", ephemeral=True)
            return
        if duel_service.is_playing(user_id) or duel_service.is_playing(opp_id):
            await interaction.response.send_message("有人已经在游戏中了，等这局结束。", ephemeral=True)
            return

        # 查余额
        try:
            from src.chat.features.odysseia_coin.service.coin_service import coin_service
            user_bal = await coin_service.get_balance(user_id) or 0
            opp_bal = await coin_service.get_balance(opp_id) or 0
        except Exception:
            user_bal, opp_bal = 0, 0

        user_has_coins = user_bal >= bet
        opp_has_coins = opp_bal >= bet

        if not life_gamble:
            if not user_has_coins:
                await interaction.response.send_message(
                    f"你只有 {user_bal} 币，不够下注 {bet}。试试赌命模式？",
                    ephemeral=True,
                )
                return

        # 发起挑战邀请
        view = DuelInviteView(
            challenger_id=user_id, opponent_id=opp_id, bet=bet,
            challenger_life=life_gamble, challenger_has_coins=user_has_coins,
        )
        await interaction.response.send_message(
            f"⚔️ <@{user_id}> 向 <@{opp_id}> 发起死斗！\n"
            f"下注：**{bet} 春春币**\n"
            f"挑战方模式：{'赌命 🔥' if life_gamble else '赌币 💰'}\n\n"
            f"<@{opp_id}>，接受挑战吗？",
            view=view,
        )


class DuelInviteView(discord.ui.View):
    """对手接受/拒绝邀请"""
    def __init__(self, challenger_id, opponent_id, bet, challenger_life, challenger_has_coins):
        super().__init__(timeout=60)
        self.challenger_id = challenger_id
        self.opponent_id = opponent_id
        self.bet = bet
        self.challenger_life = challenger_life
        self.challenger_has_coins = challenger_has_coins

    @discord.ui.button(label="接受挑战（赌币）", style=discord.ButtonStyle.success, emoji="💰")
    async def accept_coin(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.opponent_id:
            await interaction.response.send_message("只有被挑战的人能接受。", ephemeral=True)
            return
        await self._start_game(interaction, opp_life=False)

    @discord.ui.button(label="接受挑战（赌命）", style=discord.ButtonStyle.danger, emoji="🔥")
    async def accept_life(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.opponent_id:
            await interaction.response.send_message("只有被挑战的人能接受。", ephemeral=True)
            return
        await self._start_game(interaction, opp_life=True)

    @discord.ui.button(label="拒绝", style=discord.ButtonStyle.secondary, emoji="❌")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.opponent_id:
            await interaction.response.send_message("只有被挑战的人能拒绝。", ephemeral=True)
            return
        for c in self.children:
            c.disabled = True
        await interaction.response.edit_message(content="💀 死斗邀请被拒绝。", view=self)

    async def _start_game(self, interaction: discord.Interaction, opp_life: bool):
        # 查对手余额
        try:
            from src.chat.features.odysseia_coin.service.coin_service import coin_service
            opp_bal = await coin_service.get_balance(self.opponent_id) or 0
        except Exception:
            opp_bal = 0
        opp_has_coins = opp_bal >= self.bet

        if not opp_life and not opp_has_coins:
            await interaction.response.send_message(
                f"你只有 {opp_bal} 币，不够下注 {self.bet}。试试赌命接受？",
                ephemeral=True,
            )
            return

        game = duel_service.create_game(
            self.challenger_id, self.opponent_id, self.bet,
            self.challenger_life, opp_life,
            self.challenger_has_coins, opp_has_coins,
        )

        for c in self.children:
            c.disabled = True
        quote = random.choice(DUEL_QUOTES)
        await interaction.response.edit_message(
            content=(
                f"⚔️ **死斗开始！**\n"
                f"擂台已就位，围观群众（小春娘）搬好小板凳。\n\n"
                f"<@{self.challenger_id}> vs <@{self.opponent_id}>\n"
                f"下注：**{self.bet} 币**\n"
                f"<@{self.challenger_id}>：{'赌命 🔥' if self.challenger_life else '赌币 💰'}\n"
                f"<@{self.opponent_id}>：{'赌命 🔥' if opp_life else '赌币 💰'}\n\n"
                f"小春娘：{quote}\n\n"
                f"三局两胜，双方点下方按钮出拳 👇"
            ),
            view=None,
        )

        # 公屏发就位按钮，双方各自点击触发只有自己能看见的 ephemeral 出拳界面
        await interaction.followup.send(
            content=f"第1轮开始！双方点下方按钮出拳 👇",
            view=DuelReadyView(game, 1),
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(DuelCog(bot))
