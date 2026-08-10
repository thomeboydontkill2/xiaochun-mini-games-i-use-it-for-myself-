# -*- coding: utf-8 -*-
"""
幸运轮盘 Cog —— /轮盘 命令。

修复：
- 赌命模式也校验 bet 合法性（旧版跳过校验，赌命可传任意数字）。
- 结算失败如实告知玩家。
"""

import discord
from discord import app_commands
from discord.ext import commands
import logging

from src.chat.features.games.services.roulette_service import roulette_service
from src.chat.features.games.services import betting
from src.chat.features.games.config.games_config import DEFAULT_BET
from src.chat.features.games.ui.roulette_view import RouletteView

log = logging.getLogger(__name__)


class RouletteCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="轮盘", description="幸运轮盘！下注抽倍率，赚钱赔钱看命")
    @app_commands.describe(
        bet="下注金额（春春币），不填默认 50",
        life_gamble="赌命模式：输了禁言，赢了得系统奖励",
    )
    async def roulette(
        self,
        interaction: discord.Interaction,
        bet: int = DEFAULT_BET,
        life_gamble: bool = False,
    ):
        user_id = interaction.user.id

        if roulette_service.is_playing(user_id):
            await interaction.response.send_message("你已经有一个轮盘在转了，先转完再说。", ephemeral=True)
            return

        ok, msg = roulette_service.validate_bet(bet)
        if not ok:
            await interaction.response.send_message(msg, ephemeral=True)
            return

        has_coins = True
        if not life_gamble:
            try:
                balance = await betting.get_balance(user_id)
            except Exception:
                log.exception("轮盘查余额失败")
                await interaction.response.send_message("余额查询失败，稍后再试。", ephemeral=True)
                return
            has_coins = balance >= bet
            if not has_coins:
                await interaction.response.send_message(
                    f"你只有 {balance} 春春币，不够下注 {bet}。试试赌命模式？`life_gamble=True`",
                    ephemeral=True,
                )
                return
        else:
            try:
                has_coins = await betting.get_balance(user_id) >= bet
            except Exception:
                has_coins = False

        roulette_service.start_game(user_id, bet if not life_gamble else 0)

        mode_text = (
            "🎲 **赌命模式**：输了禁言，赢了得系统奖励（每日限次）"
            if life_gamble else f"💰 **下注 {bet} 币**"
        )
        view = RouletteView(user_id, bet, life_gamble, has_coins)
        await interaction.response.send_message(
            f"🎡 **幸运轮盘**\n{mode_text}\n"
            f"倍率：血本无归 / 亏一半 / 亏三成 / 回本 / 赚一半 / 翻倍 / 三倍 / 六倍 / 禁言 / 十倍\n\n"
            f"点击下方按钮转动轮盘！",
            view=view,
        )
        view.message = await interaction.original_response()

    @staticmethod
    def _describe_multiplier(m: float) -> str:
        names = {
            -2: "禁言", -1.0: "血本无归", -0.5: "亏一半", -0.3: "亏三成",
            0: "回本", 0.5: "赚一半", 1: "翻倍", 2: "三倍", 5: "六倍", 10: "十倍",
        }
        return names.get(m, f"{m}x")

    @staticmethod
    async def _do_spin(interaction: discord.Interaction, user_id: int, bet: int,
                       is_life_gamble: bool, has_coins: bool):
        result = roulette_service.spin(user_id)
        if "error" in result:
            await interaction.followup.send(result["error"], ephemeral=True)
            return

        final = await roulette_service.settle(user_id, result, is_life_gamble, has_coins, interaction.guild)
        multiplier = final["multiplier"]
        desc = RouletteCog._describe_multiplier(multiplier)
        prefix = f"🎡 轮盘停在了 **{desc}**\n"

        if final.get("settle_failed"):
            await interaction.followup.send(prefix + "⚠️ 结算出了问题，币没有变动，请联系管理员。")
            return

        if final.get("life_gamble"):
            if final.get("muted"):
                await interaction.followup.send(
                    prefix + f"💥 赌命失败！小春娘不理你 {final['muted']} 分钟，好好反省。<生气>")
            elif multiplier == 0:
                await interaction.followup.send(
                    prefix + "😐 赌命回本！这次放过你，币没扣也没奖。<微笑>")
            elif final.get("reward_capped"):
                await interaction.followup.send(
                    prefix + "🎉 赌命成功！但你今天的赌命奖励已经领满了，只赢了面子。<鬼脸>")
            else:
                await interaction.followup.send(
                    prefix + f"🎉 赌命成功！勇敢者奖励 **+{final['reward']}** 春春币！<得意>")
            return

        if final.get("muted"):
            await interaction.followup.send(
                prefix + f"😶 小春娘罚你闭嘴 {final['muted']} 分钟，币没扣，反省一下。<鬼脸>")
        elif multiplier < 0:
            await interaction.followup.send(
                prefix + f"💸 亏了 {final['loss']} 春春币（扣 {int(abs(multiplier) * 100)}%）。下次再来。<委屈>")
        elif multiplier == 0:
            await interaction.followup.send(prefix + "😐 不亏不赚，币保住了。<微笑>")
        else:
            await interaction.followup.send(
                prefix + f"💰 赢了！净收益 **+{final['coins_change']}** 春春币。<开心>")


async def setup(bot: commands.Bot):
    await bot.add_cog(RouletteCog(bot))
