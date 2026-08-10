# -*- coding: utf-8 -*-
"""
两人死斗 UI —— 公屏按钮触发 ephemeral 出拳界面
修复：原版用 interaction.followup.send(ephemeral=True) 给非 interaction 用户发消息，
对方根本看不见。改为公屏按钮，每人各自触发自己的 ephemeral 出拳界面。
"""

import discord
from discord.ui import View, Select, Button, select, button

CHOICES = ["石头", "剪刀", "布"]
CHOICE_EMOJI = {"石头": "✊", "剪刀": "✌️", "布": "✋"}


class DuelReadyView(View):
    """公屏就位按钮：双方各自点击触发自己的 ephemeral 出拳界面"""
    def __init__(self, game, round_num: int):
        super().__init__(timeout=180)
        self.game = game
        self.round_num = round_num
        self._started: set[int] = set()  # 已点过按钮出拳的玩家

    @button(label="我要出拳", style=discord.ButtonStyle.primary, emoji="✊")
    async def ready_button(self, interaction: discord.Interaction, button: Button):
        game = self.game
        if interaction.user.id not in (game.p1_id, game.p2_id):
            await interaction.response.send_message("你不在这场死斗里。", ephemeral=True)
            return
        if interaction.user.id in self._started:
            await interaction.response.send_message("你已经点过出拳了，快选！<偷笑>", ephemeral=True)
            return
        if game.p1_choice or game.p2_choice:
            # 已有人出过拳，检查这个玩家是否已出
            uid = interaction.user.id
            if (uid == game.p1_id and game.p1_choice) or (uid == game.p2_id and game.p2_choice):
                await interaction.response.send_message("你这局已经出过了，等对手。<微笑>", ephemeral=True)
                return
        self._started.add(interaction.user.id)
        # 触发只有自己能看见的出拳界面
        await interaction.response.send_message(
            content=f"⚔️ **第{self.round_num}局** 出拳吧！只有你能看见这个界面。<鬼脸>",
            view=DuelSelectView(self.game, interaction.user.id, self.round_num),
            ephemeral=True,
        )


class DuelSelectView(View):
    """出拳选择界面（ephemeral，只有自己看见）"""
    def __init__(self, game, player_id: int, round_num: int):
        super().__init__(timeout=120)
        self.game = game
        self.player_id = player_id
        self.round_num = round_num

    @select(
        placeholder="选择你的出拳...",
        options=[
            discord.SelectOption(label="石头", emoji="🪨", value="石头"),
            discord.SelectOption(label="剪刀", emoji="✂️", value="剪刀"),
            discord.SelectOption(label="布", emoji="📄", value="布"),
        ],
    )
    async def choice_select(self, interaction: discord.Interaction, select: Select):
        if interaction.user.id != self.player_id:
            await interaction.response.send_message("这不是你的死斗。", ephemeral=True)
            return
        choice = select.values[0]
        from src.chat.features.games.services.duel_service import duel_service
        result = duel_service.submit_choice(self.player_id, choice)

        if "error" in result:
            await interaction.response.send_message(result["error"], ephemeral=True)
            return

        # 禁用当前出拳界面
        for c in self.children:
            c.disabled = True
        await interaction.response.edit_message(content=f"已出 **{choice}**，等待对手... ⏳", view=self)

        if result.get("waiting"):
            return

        # 双方都出了，结算回合——结果发到频道公开（用 followup，非 ephemeral）
        await self._resolve_round(interaction, result)

    async def _resolve_round(self, interaction: discord.Interaction, result: dict):
        from src.chat.features.games.services.duel_service import duel_service

        round_data = result["round_result"]
        game_over = result.get("game_over", False)

        c1, c2 = round_data["c1"], round_data["c2"]
        round_num = round_data["round"]
        e1 = CHOICE_EMOJI.get(c1, "")
        e2 = CHOICE_EMOJI.get(c2, "")

        if round_data.get("tie"):
            text = (
                f"⚔️ 第{round_num}轮结果\n\n"
                f"<@{self.game.p1_id}>：{e1} {c1}\n"
                f"<@{self.game.p2_id}>：{e2} {c2}\n\n"
                f"平局！双方都出了 {c1} 🤝\n\n"
                f"当前比分\n"
                f"<@{self.game.p1_id}> {round_data['p1_score']} : {round_data['p2_score']} <@{self.game.p2_id}>\n"
            )
        else:
            winner_id = round_data["winner_id"]
            text = (
                f"⚔️ 第{round_num}轮结果\n\n"
                f"<@{self.game.p1_id}>：{e1} {c1}\n"
                f"<@{self.game.p2_id}>：{e2} {c2}\n\n"
                f"<@{winner_id}> 获胜！\n\n"
                f"当前比分\n"
                f"<@{self.game.p1_id}> {round_data['p1_score']} : {round_data['p2_score']} <@{self.game.p2_id}>\n"
            )

        if game_over:
            winner_id = result["winner_id"]
            loser_id = result["loser_id"]
            settle = await duel_service.settle(self.game, winner_id, loser_id, interaction.guild)

            text += (
                f"\n⚔️ 死斗结束\n\n"
                f"胜者：<@{winner_id}>\n"
                f"败者：<@{loser_id}>\n\n"
                f"最终比分：{self.game.p1_score} : {self.game.p2_score}\n\n"
            )

            mode = settle["mode"]
            if mode == "coin_vs_coin":
                text += f"<@{winner_id}> 获得 {settle['winner_gain']} 春春币，<@{loser_id}> 失去 {settle['loser_loss']} 春春币"
            elif mode == "life_rich_vs_coin":
                text += f"<@{winner_id}> 赌命勇敢者！获得 {settle['winner_gain']} 春春币（含1.5x勇敢奖励），<@{loser_id}> 失去 {settle['loser_loss']} 春春币"
            elif mode == "coin_vs_life_rich":
                text += f"<@{winner_id}> 获得 {settle['winner_gain']} 春春币，<@{loser_id}> 赌命输掉 {settle['loser_loss']} 春春币，将接受禁言 {settle['loser_muted']} 分钟"
            elif mode == "life_poor_vs_coin":
                text += f"<@{winner_id}> 没钱赌命赢了！获得 {settle['winner_gain']} 春春币，<@{loser_id}> 失去 {settle['loser_loss']} 春春币"
            elif mode == "coin_vs_life_poor":
                text += f"<@{winner_id}> 获得 {settle['winner_gain']} 春春币，<@{loser_id}> 没钱赌命输了，将接受禁言 {settle['loser_muted']} 分钟"
            elif mode == "life_vs_life":
                text += f"双方赌命！<@{winner_id}> 获得 {settle['winner_gain']} 春春币（系统奖励），<@{loser_id}> 将接受禁言 {settle['loser_muted']} 分钟"

            await interaction.followup.send(content=text)
        else:
            # 继续下一局：公屏发新的就位按钮，双方各自点击触发自己的 ephemeral 出拳界面
            self.game.p1_choice = None
            self.game.p2_choice = None
            next_round = self.game.round
            text += f"\n第{next_round}轮开始！双方点下方按钮出拳 👇"
            await interaction.followup.send(content=text, view=DuelReadyView(self.game, next_round))
