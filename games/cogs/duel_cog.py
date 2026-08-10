# -*- coding: utf-8 -*-
"""
两人死斗 Cog —— /死斗 命令，邀请对手进行石头剪刀布三局两胜。

修复：
- 下注金额校验（旧版完全没校验，可下 0 或负数）。
- 邀请超时自动置灰并公告，不再悬挂。
- 接受挑战瞬间重新校验双方是否已在别的对局（create_game 内部二次校验）。
"""

import discord
from discord import app_commands
from discord.ext import commands
import logging
import random

from src.chat.features.games.services.duel_service import duel_service
from src.chat.features.games.services import betting
from src.chat.features.games.config.games_config import DEFAULT_BET
from src.chat.features.games.ui.duel_view import DuelReadyView

log = logging.getLogger(__name__)

# 小春娘围观台词，死斗开场随机抽一条
DUEL_QUOTES = [
    "哦哦，要打起来了嘛，我盯着呢。<:chi_gua:1536139781875179592>",
    "嘛，又是你们两个。赌注我帮你们记好了哦。<:tou_xiao:1536150632426111078>",
    "诶嘿，这次谁赢？我押...不告诉你。<:ao_jiao:1536137032932532254>",
    "呀，这么认真？那我认真围观。<:xianhua:1536148788228522075>",
    "哼，打起来才好，省得闲着。<:ghost_face:1536141788065038468>",
    "嘛，输的人别哭鼻子哦。<:ganga_zan:1536142415864402041>",
    "哦哦！这个阵仗，我有预感今天有好戏。<:hao_qi:1536144194073133148>",
    "你们两个，别打太狠，我还要陪你们聊天呢。<:wei_qu:1536146943493800077>",
    "呀，赌命的？胆子挺大嘛。<:jing_ya:1536144838523748402>",
    "嘛，我见过大场面的，但这个还是有点意思。<:chi_gua:1536139781875179592>",
    "诶？都赌命？那我可要好好看了。<:yi_huo:1536149404455538695>",
    "哼，赢了请我吃火锅哦。<:bao_bao:1536137369881681951>",
    "哦哦，三局两胜，别第一局就泄气。<:good:1536142959295201510>",
    "呀，你俩的表情都好严肃，我忍不住想笑。<:tou_xiao:1536150632426111078>",
    "嘛，不管谁赢，我都陪输的人聊两句。<:xianhua:1536148788228522075>",
    "诶嘿，我偷偷猜了个赢家，不告诉你们。<:ao_jiao:1536137032932532254>",
    "哦哦，开始吧开始吧，我等不及了。<:kai_xin:1536137054306705599>",
    "呀，赌命的人真勇敢，还是真傻？<:bi_shi:1536138114366701698>",
    "嘛，输赢都是朋友，但赢了更好对吧？<:guai_qiao:1536143570120085655>",
    "哼，我赌小春娘赢——哦，我不参与，围观。<:ghost_face:1536141788065038468>",
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

        if interaction.guild is None:
            await interaction.response.send_message("死斗只能在服务器频道里打。", ephemeral=True)
            return
        if opp_id == user_id:
            await interaction.response.send_message("不能和自己死斗哦。", ephemeral=True)
            return
        if opponent.bot:
            await interaction.response.send_message("机器人不参与死斗，但小春娘会围观。<:tou_xiao:1536150632426111078>", ephemeral=True)
            return
        if duel_service.is_playing(user_id) or duel_service.is_playing(opp_id):
            await interaction.response.send_message("有人已经在游戏中了，等这局结束。", ephemeral=True)
            return

        ok, msg = duel_service.validate_bet(bet)
        if not ok:
            await interaction.response.send_message(msg, ephemeral=True)
            return

        try:
            user_bal = await betting.get_balance(user_id)
        except Exception:
            log.exception("死斗查余额失败")
            user_bal = 0
        user_has_coins = user_bal >= bet

        if not life_gamble and not user_has_coins:
            await interaction.response.send_message(
                f"你只有 {user_bal} 币，不够下注 {bet}。试试赌命模式？",
                ephemeral=True,
            )
            return

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
        view.message = await interaction.original_response()


class DuelInviteView(discord.ui.View):
    """对手接受/拒绝邀请"""

    def __init__(self, challenger_id, opponent_id, bet, challenger_life, challenger_has_coins):
        super().__init__(timeout=60)
        self.challenger_id = challenger_id
        self.opponent_id = opponent_id
        self.bet = bet
        self.challenger_life = challenger_life
        self.challenger_has_coins = challenger_has_coins
        self.answered = False
        self.message: discord.Message | None = None

    async def on_timeout(self):
        if self.answered:
            return
        for c in self.children:
            c.disabled = True
        if self.message:
            try:
                await self.message.edit(
                    content=f"⚔️ <@{self.opponent_id}> 没有应战，邀请过期了。<:chi_gua:1536139781875179592>", view=self)
            except discord.HTTPException:
                pass

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
        self.answered = True
        for c in self.children:
            c.disabled = True
        await interaction.response.edit_message(content="💀 死斗邀请被拒绝。", view=self)

    async def _start_game(self, interaction: discord.Interaction, opp_life: bool):
        if self.answered:
            await interaction.response.send_message("这个邀请已经处理过了。", ephemeral=True)
            return

        try:
            opp_bal = await betting.get_balance(self.opponent_id)
        except Exception:
            log.exception("死斗查对手余额失败")
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
        if game is None:
            await interaction.response.send_message("有人刚进了别的对局，这场先取消。", ephemeral=True)
            return

        self.answered = True
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

        ready = DuelReadyView(game, 1)
        msg = await interaction.followup.send(
            content="第1轮开始！双方点下方按钮出拳 👇",
            view=ready,
            wait=True,
        )
        ready.message = msg


async def setup(bot: commands.Bot):
    await bot.add_cog(DuelCog(bot))
