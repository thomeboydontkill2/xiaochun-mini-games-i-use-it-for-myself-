# -*- coding: utf-8 -*-
"""
幸运轮盘 UI —— Discord 按钮 View
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

    @button(label="转动轮盘", style=discord.ButtonStyle.primary, emoji="🎡")
    async def spin_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("这不是你的轮盘哦。", ephemeral=True)
            return
        if self.spun:
            return
        self.spun = True
        for b in self.children:
            b.disabled = True
        await interaction.response.edit_message(view=self)

        from src.chat.features.games.cogs.roulette_cog import RouletteCog
        await RouletteCog._do_spin(interaction, self.user_id, self.bet, self.is_life_gamble, self.has_coins)
