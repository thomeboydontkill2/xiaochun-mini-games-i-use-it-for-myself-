# -*- coding: utf-8 -*-
"""
传炸弹 Cog —— /传炸弹 命令
"""

import discord
from discord import app_commands
from discord.ext import commands
import logging

from src.chat.features.games.services.bomb_service import bomb_service
from src.chat.features.games.config.games_config import DEFAULT_BET
from src.chat.features.games.ui.bomb_view import BombJoinView

log = logging.getLogger(__name__)


class BombCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="传炸弹", description="多人传炸弹游戏！炸弹在谁手上爆炸谁输，扣币或禁言")
    @app_commands.describe(bet="每人下注金额，不填默认 50")
    async def pass_bomb(self, interaction: discord.Interaction, bet: int = DEFAULT_BET):
        channel_id = interaction.channel_id

        if bomb_service.has_game(channel_id):
            await interaction.response.send_message("这个频道已经有一场炸弹游戏了，等它结束。", ephemeral=True)
            return

        ok, msg = bomb_service.validate_bet(bet)
        if not ok:
            await interaction.response.send_message(msg, ephemeral=True)
            return

        game = bomb_service.create_game(channel_id, bet)

        # 发起人自动加入（赌币）
        try:
            from src.chat.features.odysseia_coin.service.coin_service import coin_service
            bal = await coin_service.get_balance(interaction.user.id) or 0
        except Exception:
            bal = 0
        has_coins = bal >= bet
        bomb_service.add_player(channel_id, interaction.user.id, False, has_coins)

        view = BombJoinView(channel_id, bet, interaction.user.id)
        await interaction.response.send_message(
            f"💣 **传炸弹游戏开始招募！**\n"
            f"发起人：<@{interaction.user.id}>\n"
            f"下注：**{bet} 春春币**/人\n"
            f"已加入 1 人，至少 2 人才能开始\n\n"
            f"点击下方按钮加入（赌币或赌命），发起人点「开始游戏」启动！",
            view=view,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(BombCog(bot))
