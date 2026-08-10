# -*- coding: utf-8 -*-
"""
谁是卧底 Cog —— /谁是卧底 命令
改版：描述环节改为公屏按钮触发 Modal（不再用 on_message 监听）
"""

import discord
from discord import app_commands
from discord.ext import commands
import logging

from src.chat.features.games.services.undercover_service import undercover_service
from src.chat.features.games.config.games_config import DEFAULT_BET
from src.chat.features.games.ui.undercover_view import UndercoverJoinView

log = logging.getLogger(__name__)


class UndercoverCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="谁是卧底", description="多人谁是卧底！每人发一个词，找出卧底！")
    @app_commands.describe(
        bet="每人下注金额，不填默认 50",
        min_players="最少几人开始（4-10），不填默认 4",
        max_players="最多几人加入（4-10），不填默认 10",
    )
    async def undercover(
        self,
        interaction: discord.Interaction,
        bet: int = DEFAULT_BET,
        min_players: int = 4,
        max_players: int = 10,
    ):
        channel_id = interaction.channel_id

        # 校验人数范围
        if min_players < 4 or min_players > 10:
            await interaction.response.send_message("最少人数必须在 4-10 之间。", ephemeral=True)
            return
        if max_players < 4 or max_players > 10:
            await interaction.response.send_message("最多人数必须在 4-10 之间。", ephemeral=True)
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

        game = undercover_service.create_game(channel_id, bet, min_players, max_players)

        # 发起人自动加入
        try:
            from src.chat.features.odysseia_coin.service.coin_service import coin_service
            bal = await coin_service.get_balance(interaction.user.id) or 0
        except Exception:
            bal = 0
        has_coins = bal >= bet
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


async def setup(bot: commands.Bot):
    await bot.add_cog(UndercoverCog(bot))
