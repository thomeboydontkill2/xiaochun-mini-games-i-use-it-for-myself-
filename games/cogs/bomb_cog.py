# -*- coding: utf-8 -*-
"""
传炸弹 Cog —— /传炸弹 命令。

修复：
- DM 里没有 channel_id 时直接拒绝（旧版会用 None 当 key 建游戏）。
- 发起人余额不足时不再默默按"没钱"加入，而是提示先用赌命或降低下注。
"""

import discord
from discord import app_commands
from discord.ext import commands
import logging

from src.chat.features.games.services.bomb_service import bomb_service
from src.chat.features.games.services import betting
from src.chat.features.games.config.games_config import DEFAULT_BET, BOMB_MIN_PLAYERS, BOMB_PASS_TIME
from src.chat.features.games.ui.bomb_view import BombJoinView

log = logging.getLogger(__name__)


class BombCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="传炸弹", description="多人传炸弹！持有者自选传给谁，在谁手上爆炸谁输")
    @app_commands.describe(bet="每人下注金额，不填默认 50")
    async def pass_bomb(self, interaction: discord.Interaction, bet: int = DEFAULT_BET):
        channel_id = interaction.channel_id
        if channel_id is None or interaction.guild is None:
            await interaction.response.send_message("这个游戏只能在服务器频道里玩。", ephemeral=True)
            return

        if bomb_service.has_game(channel_id):
            await interaction.response.send_message("这个频道已经有一场炸弹游戏了，等它结束。", ephemeral=True)
            return

        ok, msg = bomb_service.validate_bet(bet)
        if not ok:
            await interaction.response.send_message(msg, ephemeral=True)
            return

        try:
            bal = await betting.get_balance(interaction.user.id)
        except Exception:
            log.exception("传炸弹查余额失败")
            bal = 0
        has_coins = bal >= bet
        if not has_coins:
            await interaction.response.send_message(
                f"你只有 {bal} 币，不够下注 {bet}。降低下注，或者开局后用赌命方式加入。",
                ephemeral=True,
            )
            return

        bomb_service.create_game(channel_id, bet)
        bomb_service.add_player(channel_id, interaction.user.id, False, has_coins)

        view = BombJoinView(channel_id, bet, interaction.user.id)
        await interaction.response.send_message(
            f"💣 **传炸弹游戏开始招募！**\n"
            f"发起人：<@{interaction.user.id}>\n"
            f"下注：**{bet} 春春币**/人\n"
            f"已加入 1 人，至少 {BOMB_MIN_PLAYERS} 人才能开始\n\n"
            f"🆕 新玩法：接到炸弹的人**自己选**传给谁，{BOMB_PASS_TIME} 秒不传就在手上炸！\n"
            f"点击下方按钮加入（赌币或赌命），发起人点「开始游戏」启动！",
            view=view,
        )
        view.message = await interaction.original_response()


async def setup(bot: commands.Bot):
    await bot.add_cog(BombCog(bot))
