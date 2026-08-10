# -*- coding: utf-8 -*-
"""
狼人杀 Cog —— /狼人杀 命令入口。

只在服务器频道里可用（需要 guild 才能私信成员、按频道管理对局）。
发起人自动上桌，需要先有足够春春币交局费。
"""

import discord
from discord import app_commands
from discord.ext import commands
import logging

from src.chat.features.games.services.werewolf_service import werewolf_service
from src.chat.features.games.services import betting
from src.chat.features.games.config.games_config import (
    WEREWOLF_DEFAULT_BET, WEREWOLF_MIN_PLAYERS, WEREWOLF_MAX_PLAYERS,
)
from src.chat.features.games.ui.werewolf_view import WerewolfJoinView

log = logging.getLogger(__name__)


class WerewolfCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="狼人杀", description="多人狼人杀！扩展板，机器人当法官，轮流发言")
    @app_commands.describe(bet="每人局费（春春币），不填默认 50")
    async def werewolf(self, interaction: discord.Interaction, bet: int = WEREWOLF_DEFAULT_BET):
        channel_id = interaction.channel_id
        if channel_id is None or interaction.guild is None:
            await interaction.response.send_message("狼人杀只能在服务器频道里玩。", ephemeral=True)
            return

        if werewolf_service.has_game(channel_id):
            await interaction.response.send_message("这个频道已经有一局狼人杀了，等它结束。", ephemeral=True)
            return

        ok, msg = werewolf_service.validate_bet(bet)
        if not ok:
            await interaction.response.send_message(msg, ephemeral=True)
            return

        try:
            bal = await betting.get_balance(interaction.user.id)
        except Exception:
            log.exception("狼人杀查余额失败")
            bal = 0
        if bal < bet:
            await interaction.response.send_message(
                f"你只有 {bal} 春春币，不够交 {bet} 的局费。降低局费再开。", ephemeral=True)
            return

        werewolf_service.create_game(channel_id, bet)
        werewolf_service.add_player(channel_id, interaction.user.id)

        view = WerewolfJoinView(channel_id, bet, interaction.user.id)
        await interaction.response.send_message(
            f"🐺 **狼人杀开始招募！**\n"
            f"发起人：<@{interaction.user.id}>\n"
            f"局费：**{bet} 春春币**/人（胜方阵营平分败方局费）\n"
            f"人数：**{WEREWOLF_MIN_PLAYERS}-{WEREWOLF_MAX_PLAYERS} 人**，已上桌 1 人\n\n"
            f"板子按人数自动配：狼人 / 预言家 / 女巫 / 猎人 / 守卫 / 白痴 / 平民\n"
            f"第一天先竞选警长（白天投票 1.5 票）。夜晚狼人有私密频道商量，白天**按顺序轮流发言**（点按钮在私密框里写，机器人代发公屏）。死亡公开身份。\n"
            f"⚠️ 记得开启\"允许服务器成员私信\"，否则收不到身份。\n\n"
            f"点下方按钮上桌，发起人点「开始游戏」发身份！",
            view=view,
        )
        view.message = await interaction.original_response()


async def setup(bot: commands.Bot):
    await bot.add_cog(WerewolfCog(bot))
