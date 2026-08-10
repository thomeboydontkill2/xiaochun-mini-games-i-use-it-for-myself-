# -*- coding: utf-8 -*-
"""
幸运轮盘 UI —— Discord 按钮 View。

修复：
- 重复点击不再静默无响应（Discord 会显示"交互失败"），改为 ephemeral 提示。
- 超时未转 → 取消对局并置灰按钮，玩家不再被"你已经有一个轮盘"卡死。
"""

import discord
from discord.ui import View, Button, button


class RouletteView(View):
    def __init__(self, user_id: int, bet: int, is_life_gamble: bool, has_coins: bool):
        super().__init__(timeout=120)
        self.user_id = user_id
        self.bet = bet
        self.is_life_gamble = is_life_gamble
        self.has_coins = has_coins
        self.spun = False
        self.message: discord.Message | None = None

    async def on_timeout(self):
        if self.spun:
            return
        from src.chat.features.games.services.roulette_service import roulette_service
        roulette_service.cancel_game(self.user_id)
        for c in self.children:
            c.disabled = True
        if self.message:
            try:
                await self.message.edit(content="🎡 轮盘等了你两分钟没等到，先收起来了。币没有变动。", view=self)
            except discord.HTTPException:
                pass

    @button(label="转动轮盘", style=discord.ButtonStyle.primary, emoji="🎡")
    async def spin_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("这不是你的轮盘哦。", ephemeral=True)
            return
        if self.spun:
            await interaction.response.send_message("已经在转了，别催。<鬼脸>", ephemeral=True)
            return
        self.spun = True
        for b in self.children:
            b.disabled = True
        await interaction.response.edit_message(view=self)

        from src.chat.features.games.cogs.roulette_cog import RouletteCog
        await RouletteCog._do_spin(interaction, self.user_id, self.bet, self.is_life_gamble, self.has_coins)
