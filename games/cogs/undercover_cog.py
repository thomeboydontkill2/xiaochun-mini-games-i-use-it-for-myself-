# -*- coding: utf-8 -*-
"""
谁是卧底 Cog —— /谁是卧底 命令。

修复：
- 人数范围统一从配置读（UNDERCOVER_MIN_PLAYERS / UNDERCOVER_MAX_PLAYERS），
  不再散落硬编码 4/10。
- DM 里没有 channel_id 时直接拒绝。
- 发起人余额不足时给出明确提示。
"""

import discord
from discord import app_commands
from discord.ext import commands
import logging

from src.chat.features.games.services.undercover_service import undercover_service
from src.chat.features.games.services import betting
from src.chat.features.games.config.games_config import (
    DEFAULT_BET, UNDERCOVER_MIN_PLAYERS, UNDERCOVER_MAX_PLAYERS,
)
from src.chat.features.games.ui.undercover_view import UndercoverJoinView

log = logging.getLogger(__name__)


class UndercoverCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="谁是卧底", description="多人谁是卧底！每人发一个词，找出卧底！")
    @app_commands.describe(
        bet="每人下注金额，不填默认 50",
        min_players=f"最少几人开始（{UNDERCOVER_MIN_PLAYERS}-{UNDERCOVER_MAX_PLAYERS}）",
        max_players=f"最多几人加入（{UNDERCOVER_MIN_PLAYERS}-{UNDERCOVER_MAX_PLAYERS}）",
    )
    async def undercover(
        self,
        interaction: discord.Interaction,
        bet: int = DEFAULT_BET,
        min_players: int = UNDERCOVER_MIN_PLAYERS,
        max_players: int = UNDERCOVER_MAX_PLAYERS,
    ):
        channel_id = interaction.channel_id
        if channel_id is None or interaction.guild is None:
            await interaction.response.send_message("这个游戏只能在服务器频道里玩。", ephemeral=True)
            return

        lo, hi = UNDERCOVER_MIN_PLAYERS, UNDERCOVER_MAX_PLAYERS
        if not (lo <= min_players <= hi) or not (lo <= max_players <= hi):
            await interaction.response.send_message(f"人数必须在 {lo}-{hi} 之间。", ephemeral=True)
            return
        if min_players > max_players:
            await interaction.response.send_message("最少人数不能大于最多人数。", ephemeral=True)
            return

        if undercover_service.has_game(channel_id):
            await interaction.response.send_message("这个频道已经有一场卧底游戏了。", ephemeral=True)
            return

        ok, msg = undercover_service.validate_bet(bet)
        if not ok:
            await interaction.response.send_message(msg, ephemeral=True)
            return

        try:
            bal = await betting.get_balance(interaction.user.id)
        except Exception:
            log.exception("卧底查余额失败")
            bal = 0
        has_coins = bal >= bet
        if not has_coins:
            await interaction.response.send_message(
                f"你只有 {bal} 币，不够下注 {bet}。降低下注，或者开局后用赌命方式加入。",
                ephemeral=True,
            )
            return

        undercover_service.create_game(channel_id, bet, min_players, max_players)
        undercover_service.add_player(channel_id, interaction.user.id, False, has_coins)

        view = UndercoverJoinView(channel_id, bet, interaction.user.id)
        await interaction.response.send_message(
            f"🎭 **谁是卧底招募！**\n"
            f"发起人：<@{interaction.user.id}>\n"
            f"下注：**{bet} 春春币**/人\n"
            f"人数范围：**{min_players}-{max_players} 人**\n"
            f"已加入 1 人，至少 {min_players} 人才能开始\n\n"
            f"点击下方按钮加入，发起人点「开始游戏」启动！",
            view=view,
        )
        view.message = await interaction.original_response()


async def setup(bot: commands.Bot):
    await bot.add_cog(UndercoverCog(bot))
