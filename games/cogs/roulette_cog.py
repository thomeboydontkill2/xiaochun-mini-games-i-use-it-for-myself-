# -*- coding: utf-8 -*-
"""
幸运轮盘 Cog —— /幸运轮盘 命令
"""

import discord
from discord import app_commands
from discord.ext import commands
import logging

from src.chat.features.games.services.roulette_service import roulette_service
from src.chat.features.games.config.games_config import MIN_BET, MAX_BET, DEFAULT_BET
from src.chat.features.games.ui.roulette_view import RouletteView

log = logging.getLogger(__name__)


class RouletteCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="幸运轮盘", description="转动幸运轮盘，赢取春春币！血本无归/亏一半/亏三成/回本/赚一半/翻倍/三倍/六倍/禁言/十倍")
    @app_commands.describe(
        bet="下注金额（春春币），不填默认 50",
        life_gamble="赌命模式：没钱也能玩，输了被禁言2分钟，赢了得系统奖励",
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

        if not life_gamble:
            ok, msg = roulette_service.validate_bet(bet)
            if not ok:
                await interaction.response.send_message(msg, ephemeral=True)
                return

        # 检查余额（赌命模式不强制要求余额）
        has_coins = True
        try:
            from src.chat.features.odysseia_coin.service.coin_service import coin_service
            balance = await coin_service.get_balance(user_id)
            if balance is None:
                balance = 0
            has_coins = balance >= bet
            if not life_gamble and balance < bet:
                await interaction.response.send_message(
                    f"你只有 {balance} 春春币，不够下注 {bet}。试试赌命模式？`life_gamble=True`",
                    ephemeral=True,
                )
                return
        except Exception:
            log.exception("轮盘查余额失败")

        roulette_service.start_game(user_id, bet if not life_gamble else 0)

        mode_text = "🎲 **赌命模式**：输了禁言2分钟，赢了得系统奖励 30-100 币" if life_gamble else f"💰 **下注 {bet} 币**"
        view = RouletteView(user_id, bet, life_gamble, has_coins)

        await interaction.response.send_message(
            f"🎡 **幸运轮盘**\n{mode_text}\n倍率：血本无归 / 亏一半 / 亏三成 / 回本 / 赚一半 / 翻倍 / 三倍 / 六倍 / 禁言 / 十倍\n\n点击下方按钮转动轮盘！",
            view=view,
        )

    @staticmethod
    def _describe_multiplier(m: float) -> str:
        """把倍率转成中文描述"""
        if m == -2:
            return "禁言"
        if m == -1.0:
            return "血本无归"
        if m == -0.5:
            return "亏一半"
        if m == -0.3:
            return "亏三成"
        if m == 0:
            return "回本"
        if m == 0.5:
            return "赚一半"
        if m == 1:
            return "翻倍"
        if m == 2:
            return "三倍"
        if m == 5:
            return "六倍"
        if m == 10:
            return "十倍"
        return f"{m}x"

    @staticmethod
    async def _do_spin(interaction: discord.Interaction, user_id: int, bet: int, is_life_gamble: bool, has_coins: bool):
        """实际转盘逻辑，由按钮回调触发。"""
        result = roulette_service.spin(user_id)
        if "error" in result:
            await interaction.followup.send(result["error"], ephemeral=True)
            return

        final = await roulette_service.settle(user_id, result, is_life_gamble, has_coins, interaction.guild)
        multiplier = final["multiplier"]
        desc = RouletteCog._describe_multiplier(multiplier)

        if final.get("life_gamble"):
            if final.get("muted"):
                await interaction.followup.send(
                    f"🎡 轮盘停在了 **{desc}**\n💥 赌命失败！小春娘不理你 {final['muted']} 分钟，好好反省。<生气>",
                )
            elif multiplier == 0:
                await interaction.followup.send(
                    f"🎡 轮盘停在了 **{desc}**\n😐 赌命回本！这次放过你，币没扣也没奖。<微笑>",
                )
            else:
                await interaction.followup.send(
                    f"🎡 轮盘停在了 **{desc}**\n🎉 赌命成功！勇敢者奖励 **+{final['reward']}** 春春币！<得意>",
                )
        else:
            if final.get("muted"):
                # 禁言档
                await interaction.followup.send(
                    f"🎡 轮盘停在了 **{desc}**\n😶 小春娘罚你闭嘴 {final['muted']} 分钟，币没扣，反省一下。<鬼脸>",
                )
            elif multiplier < 0:
                # 部分亏损
                loss = final.get("loss", int(bet * abs(multiplier)))
                await interaction.followup.send(
                    f"🎡 轮盘停在了 **{desc}**\n💸 亏了 {loss} 春春币（扣 {int(abs(multiplier)*100)}%）。下次再来。<委屈>",
                )
            elif multiplier == 0:
                # 回本
                await interaction.followup.send(
                    f"🎡 轮盘停在了 **{desc}**\n😐 不亏不赚，币保住了。<微笑>",
                )
            else:
                # 赚钱
                net = final["net"]
                sign = "+" if net >= 0 else ""
                await interaction.followup.send(
                    f"🎡 轮盘停在了 **{desc}**\n💰 赢了！净收益 **{sign}{int(net)}** 春春币。<开心>",
                )


async def setup(bot: commands.Bot):
    await bot.add_cog(RouletteCog(bot))
