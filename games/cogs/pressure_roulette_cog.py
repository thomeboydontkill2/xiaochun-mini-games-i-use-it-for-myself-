# -*- coding: utf-8 -*-
"""加压俄罗斯轮盘 Cog —— /加压轮盘 命令。"""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from src.chat.features.games.services.pressure_roulette_service import pressure_roulette_service
from src.chat.features.games.services import betting
from src.chat.features.games.config.games_config import (
    PRESSURE_ROULETTE_DEFAULT_BET, PRESSURE_ROULETTE_MAX_PLAYERS,
)
from src.chat.features.games.ui.pressure_roulette_view import PressureRouletteJoinView

log = logging.getLogger(__name__)


class PressureRouletteCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="加压轮盘", description="加压俄罗斯轮盘！6弹巢1发实弹，轮流开枪，中弹禁言+出局")
    @app_commands.describe(
        bet="局费（春春币），不填默认 50",
    )
    async def pressure_roulette(
        self,
        interaction: discord.Interaction,
        bet: int = PRESSURE_ROULETTE_DEFAULT_BET,
    ):
        channel_id = interaction.channel_id
        user_id = interaction.user.id

        if pressure_roulette_service.has_game(channel_id):
            await interaction.response.send_message("这个频道已经有一局在进行中了。", ephemeral=True)
            return

        ok, msg = pressure_roulette_service.validate_bet(bet)
        if not ok:
            await interaction.response.send_message(msg, ephemeral=True)
            return

        try:
            balance = await betting.get_balance(user_id)
        except Exception:
            log.exception("加压轮盘查余额失败")
            await interaction.response.send_message("余额查询失败，稍后再试。", ephemeral=True)
            return
        if balance < bet:
            await interaction.response.send_message(
                f"你只有 {balance} 春春币，不够交 {bet} 的局费。", ephemeral=True)
            return

        game = pressure_roulette_service.create_game(channel_id, bet)
        ok, msg = pressure_roulette_service.add_player(channel_id, user_id)
        if not ok:
            pressure_roulette_service.cancel_game(channel_id)
            await interaction.response.send_message(msg, ephemeral=True)
            return

        view = PressureRouletteJoinView(channel_id, bet, user_id)
        await interaction.response.send_message(
            content=(
                f"🔫 **加压俄罗斯轮盘**\n"
                f"🔫 <@{user_id}> 开了一局，缺几个不怕死的\n"
                f"@{interaction.user.display_name} 掏出了一把左轮，并且已经报名。\n"
                f"现在他需要几个愿意陪他一起后悔的人。\n\n"
                f"**规则**\n"
                f"6 个弹巢，开局 1 发子弹，位置随机\n"
                f"轮到你，自己点按钮扣扳机。中弹就出局，然后闭嘴\n"
                f"活下来后三选一：\n"
                f"　🔫 传枪 — 弹巢前进一格，交给下一个人\n"
                f"　🔁 再开一枪 — 继续对自己开，每撑过一次攒 1 层连开蓄力\n"
                f"　💥 加压 — 装 1 + 蓄力层数 发子弹并滚动弹巢，每发让赌注 +1 分钟\n\n"
                f"连开蓄力只在连着对自己开枪时累积\n"
                f"　一旦传枪 / 加压 / 中弹就清零，攒了就得当场兑现\n\n"
                f"枪里子弹打光 = 游戏立刻结束\n"
                f"　只剩 1 人 → 他是冠军，零禁言\n"
                f"　还剩多人 → 平局，谁也没赢\n"
                f"基础赌注 3 分钟，每加压一次 +1 分钟\n\n"
                f"不加压的话，最多只会倒一个人，也就没有冠军。\n"
                f"想赢，得自己往枪里塞子弹。\n\n"
                f"当前人数：1 / {PRESSURE_ROULETTE_MAX_PLAYERS}\n"
                f"⏳ 预计开始：3 分钟内"
            ),
            view=view,
        )
        view.message = await interaction.original_response()


async def setup(bot: commands.Bot):
    await bot.add_cog(PressureRouletteCog(bot))
