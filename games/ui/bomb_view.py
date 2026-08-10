# -*- coding: utf-8 -*-
"""
传炸弹 UI —— 加入按钮 + 开始按钮 + 传递按钮
"""

import discord
from discord.ui import View, Button, button


class BombJoinView(View):
    """加入游戏界面"""
    def __init__(self, channel_id: int, bet: int, host_id: int):
        super().__init__(timeout=120)
        self.channel_id = channel_id
        self.bet = bet
        self.host_id = host_id

    @button(label="加入（赌币）", style=discord.ButtonStyle.success, emoji="💰")
    async def join_coin(self, interaction: discord.Interaction, button: Button):
        await self._join(interaction, is_life=False)

    @button(label="加入（赌命）", style=discord.ButtonStyle.danger, emoji="🔥")
    async def join_life(self, interaction: discord.Interaction, button: Button):
        await self._join(interaction, is_life=True)

    @button(label="开始游戏", style=discord.ButtonStyle.primary, emoji="💣")
    async def start(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.host_id:
            await interaction.response.send_message("只有发起人能开始游戏。", ephemeral=True)
            return
        from src.chat.features.games.services.bomb_service import bomb_service
        ok, msg, game = bomb_service.start_game(self.channel_id)
        if not ok:
            await interaction.response.send_message(msg, ephemeral=True)
            return
        for c in self.children:
            c.disabled = True
        holder_id = game.players[game.current_holder]
        await interaction.response.edit_message(
            content=(
                f"💣 **传炸弹开始！**\n"
                f"参与：{', '.join(f'<@{p}>' for p in game.players)}\n"
                f"炸弹现在在 <@{holder_id}> 手上！快传给下一个人！\n"
                f"⏱️ 倒计时中... 不知道什么时候爆炸！"
            ),
            view=BombPassView(self.channel_id, holder_id),
        )

    async def _join(self, interaction: discord.Interaction, is_life: bool):
        from src.chat.features.games.services.bomb_service import bomb_service
        try:
            from src.chat.features.odysseia_coin.service.coin_service import coin_service
            bal = await coin_service.get_balance(interaction.user.id) or 0
        except Exception:
            bal = 0
        has_coins = bal >= self.bet
        if not is_life and not has_coins:
            await interaction.response.send_message(
                f"你只有 {bal} 币，不够下注 {self.bet}。试试赌命加入？", ephemeral=True
            )
            return
        ok, msg = bomb_service.add_player(self.channel_id, interaction.user.id, is_life, has_coins)
        if not ok:
            await interaction.response.send_message(msg, ephemeral=True)
            return
        await interaction.response.send_message(
            f"{interaction.user.mention} {'赌命加入 🔥' if is_life else '赌币加入 💰'}（当前 {bomb_service.get_game(self.channel_id).players.__len__()} 人）",
            ephemeral=False,
        )


class BombPassView(View):
    """传递炸弹界面"""
    def __init__(self, channel_id: int, holder_id: int):
        super().__init__(timeout=60)
        self.channel_id = channel_id
        self.holder_id = holder_id

    @button(label="传给下一个人！", style=discord.ButtonStyle.danger, emoji="💣")
    async def pass_bomb(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.holder_id:
            await interaction.response.send_message("炸弹不在你手上！", ephemeral=True)
            return
        from src.chat.features.games.services.bomb_service import bomb_service
        result = bomb_service.pass_bomb(self.channel_id, self.holder_id)
        if "error" in result:
            await interaction.response.send_message(result["error"], ephemeral=True)
            return

        if result.get("exploded"):
            loser_id = result["loser_id"]
            game = result["game"]
            pass_count = result["pass_count"]
            duration = result["duration"]
            settle = await bomb_service.settle(game, loser_id, interaction.guild)
            for c in self.children:
                c.disabled = True

            # 奖励描述
            if settle["mode"] == "life":
                reward_text = f"禁言 {settle['loser_muted']} 分钟"
                extra_text = f"\n幸存者各得 **{settle['survivor_reward']}** 春春币。"
            else:
                reward_text = f"扣除 {settle['loser_loss']} 春春币"
                extra_text = f"\n幸存者各分 **{settle['survivor_reward']}** 春春币。"

            text = (
                f"💥 **BOOOOOOM！！**\n\n"
                f"很遗憾，炸弹最终选择了 <@{loser_id}>。\n\n"
                f"奖励：**{reward_text}**{extra_text}\n\n"
                f"**本局统计**\n"
                f"- 总传递次数：**{pass_count} 次**\n"
                f"- 本局持续时间：**{duration} 秒**\n"
                f"- 最终持有者：<@{loser_id}>"
            )
            await interaction.response.edit_message(content=text, view=self)
        else:
            new_holder = result["new_holder"]
            self.holder_id = new_holder
            message = result["message"]
            await interaction.response.edit_message(
                content=f"{message}\n\n💣 快传！⏱️",
                view=self,
            )
