# -*- coding: utf-8 -*-
"""
传炸弹 UI —— 加入按钮 + 开始按钮 + 指向性传递界面。

新玩法：炸弹持有者从下拉菜单里自选传给谁（不能传自己）。
限时 BOMB_PASS_TIME 秒不传 → 炸弹当场爆炸。

修复：
- 招募超时（无人开始）自动取消对局并置灰按钮，频道不再被卡死。
- 传递界面每次换持有者都重建（旧版复用同一个 View 改 holder_id，选项/权限会错乱）。
- 结算结果如实展示失败信息，不再假装成功。
- 【体验修复】每次传递不再编辑同一条消息：旧消息编辑成静态记录（去掉可交互组件，避免误点旧按钮），
  新持有者的选人下拉菜单改为单独发一条新消息，更像"弹出提醒"，也不会因为消息一直编辑而被当成没反应。
  新消息发送失败会退回编辑旧消息兜底，绝不静默卡死。
"""

import logging

import discord
from discord.ui import View, Select, Button, button, select

from src.chat.features.games.config.games_config import BOMB_PASS_TIME
from src.chat.features.games.ui.embed_utils import (
    game_embed, COLOR_PLAYING, COLOR_LOSE, COLOR_MUTE, COLOR_DRAW, COLOR_WIN,
)

log = logging.getLogger(__name__)


def _explosion_embed(result: dict, settle: dict) -> discord.Embed:
    loser_id = result["loser_id"]
    is_timeout = result.get("timed_out")
    title = "⏰ 拿着发呆炸了！" if is_timeout else "💥 BOOOOOOM！"

    if settle.get("settle_failed"):
        outcome = "⚠️ 结算出了问题，币没有变动，请联系管理员。"
        color = COLOR_MUTE
    elif settle["mode"] == "life":
        outcome = f"处罚：**禁言 {settle['loser_muted']} 分钟**"
        if settle.get("reward_skipped"):
            outcome += "\n局太短，幸存者没有奖励。传起来才有的拿！"
        elif settle.get("survivor_reward"):
            outcome += f"\n幸存者各得 **{settle['survivor_reward']}** 春春币。"
        else:
            outcome += "\n幸存者今天的赌命奖励已达上限，没有奖励。"
        color = COLOR_MUTE
    else:
        outcome = (
            f"处罚：**扣除 {settle['loser_loss']} 春春币**\n"
            f"幸存者各分 **{settle['survivor_reward']}** 春春币。"
        )
        color = COLOR_LOSE

    desc = (
        f"很遗憾，炸弹最终选择了 <@{loser_id}>。\n\n"
        f"{outcome}\n\n"
        f"**本局统计**\n"
        f"- 总传递次数：**{result['pass_count']} 次**\n"
        f"- 本局持续时间：**{result['duration']} 秒**"
    )
    return game_embed(title=title, description=desc, color=color, footer=f"倒霉蛋 <@{loser_id}>")


class BombJoinView(View):
    """加入游戏界面"""

    def __init__(self, channel_id: int, bet: int, host_id: int, host_life: bool = False):
        super().__init__(timeout=120)
        self.channel_id = channel_id
        self.bet = bet
        self.host_id = host_id
        self.host_life = host_life
        self.started = False
        self.message: discord.Message | None = None

    async def on_timeout(self):
        if self.started:
            return
        from src.chat.features.games.services.bomb_service import bomb_service
        bomb_service.cancel_game(self.channel_id)
        for c in self.children:
            c.disabled = True
        if self.message:
            try:
                timeout_embed = game_embed(
                    title="💣 招募超时",
                    description="这局传炸弹取消了。想玩再开一局～",
                    color=COLOR_DRAW,
                    footer=f"房间 #{self.channel_id}",
                )
                await self.message.edit(embed=timeout_embed, content=None, view=self)
            except discord.HTTPException:
                pass

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
        self.started = True
        for c in self.children:
            c.disabled = True

        # 招募消息直接编辑成第一手选人界面，不再发新消息
        pass_view = BombPassView(self.channel_id, game.holder_id, interaction.guild,
                                  bomb_service.pass_targets(game, interaction.guild),
                                  token=game.message_token)
        pass_embed = game_embed(
            title="💣 传炸弹开始！",
            description=(
                f"参与：{', '.join(f'<@{p}>' for p in game.players)}\n"
                f"<@{game.holder_id}> 拿到炸弹了！\n"
                f"👉 从下拉菜单选一个人传出去，**{BOMB_PASS_TIME} 秒**不传就在你手上炸！\n"
                f"⏱️ 引信长度未知……"
            ),
            color=COLOR_PLAYING,
            footer=f"持有者 <@{game.holder_id}>",
        )
        pass_view.message = interaction.message
        try:
            await interaction.response.edit_message(embed=pass_embed, content=None, view=pass_view)
        except Exception:
            log.exception("传炸弹开局编辑消息失败，尝试频道直发兜底")
            channel = interaction.channel
            if channel is not None:
                try:
                    pass_view.message = await channel.send(embed=pass_embed, view=pass_view)
                except Exception:
                    log.exception("传炸弹开局兜底发送也失败")

    async def _join(self, interaction: discord.Interaction, is_life: bool):
        from src.chat.features.games.services.bomb_service import bomb_service
        from src.chat.features.games.services import betting
        bal = 0
        try:
            bal = await betting.get_balance(interaction.user.id)
        except Exception:
            pass
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
        game = bomb_service.get_game(self.channel_id)
        await interaction.response.send_message(
            f"{interaction.user.mention} {'赌命加入 🔥' if is_life else '赌币加入 💰'}"
            f"（当前 {len(game.players)} 人）",
        )


class BombPassView(View):
    """指向性传递界面：持有者从下拉菜单选目标。超时 → 炸弹在手上爆炸。"""

    def __init__(self, channel_id: int, holder_id: int, guild, target_ids: list[int],
                 token: int = 0):
        super().__init__(timeout=BOMB_PASS_TIME)
        self.channel_id = channel_id
        self.holder_id = holder_id
        self.token = token
        self.message: discord.Message | None = None
        self.done = False

        options = []
        for pid in target_ids:
            member = guild.get_member(pid) if guild else None
            name = member.display_name if member else f"玩家 {pid}"
            options.append(discord.SelectOption(label=name, value=str(pid), emoji="💣"))
        self.children[0].options = options
        self.children[0].placeholder = "把炸弹传给……"

    async def on_timeout(self):
        if self.done:
            return
        # PANEL_SKIPPED 哨兵：写入前再检查 game 状态
        from src.chat.features.games.services.bomb_service import bomb_service
        game = bomb_service.get_game(self.channel_id)
        if game is None or game.exploded or game.holder_id != self.holder_id:
            return  # 状态已变，跳过
        self.done = True
        result = bomb_service.timeout_explode(self.channel_id, self.holder_id)
        if "error" in result:
            return
        settle = await bomb_service.settle(result["game"], result["loser_id"],
                                            self.message.guild if self.message else None)
        for c in self.children:
            c.disabled = True
        if self.message:
            try:
                await self.message.edit(embed=_explosion_embed(result, settle), content=None, view=self)
            except discord.HTTPException:
                pass

    @select(placeholder="把炸弹传给……", options=[])
    async def pass_select(self, interaction: discord.Interaction, select_: Select):
        if interaction.user.id != self.holder_id:
            await interaction.response.send_message("炸弹不在你手上！", ephemeral=True)
            return
        if self.done:
            await interaction.response.send_message("这一手已经传出去了。", ephemeral=True)
            return
        target_id = int(select_.values[0])

        from src.chat.features.games.services.bomb_service import bomb_service
        result = bomb_service.pass_bomb(self.channel_id, self.holder_id, target_id,
                                        expected_token=self.token)
        if "error" in result:
            if result["error"] == "stale_token":
                await interaction.response.defer()
                return
            await interaction.response.send_message(result["error"], ephemeral=True)
            return
        self.done = True
        self.stop()

        if result.get("exploded"):
            settle = await bomb_service.settle(result["game"], result["loser_id"], interaction.guild)
            for c in self.children:
                c.disabled = True
            await interaction.response.edit_message(embed=_explosion_embed(result, settle), content=None, view=self)
            return

        # 新持有者的选人界面：直接编辑当前消息，不再发新消息
        game = bomb_service.get_game(self.channel_id)
        if game is None:
            log.warning("传炸弹传递后对局已消失（可能已超时/取消），跳过发新界面")
            return
        # 持有者失效重分配
        reassign_desc = ""
        if bomb_service.reassign_if_holder_invalid(game, interaction.guild):
            reassign_desc = f"\n⚠️ 原持有者已不在场，炸弹随机重分配给了 <@{game.holder_id}>！\n"
        next_token = game.message_token
        next_view = BombPassView(self.channel_id, game.holder_id, interaction.guild,
                                 bomb_service.pass_targets(game, interaction.guild),
                                 token=next_token)
        next_embed = game_embed(
            title="💣 接到炸弹！",
            description=(
                f"{result['message']}\n"
                f"<@{game.holder_id}> 接到炸弹了！{reassign_desc}\n"
                f"👉 从下拉菜单选一个人传出去，**{BOMB_PASS_TIME} 秒**不传就在你手上炸！⏱️"
            ),
            color=COLOR_PLAYING,
            footer=f"持有者 <@{game.holder_id}>",
        )
        next_view.message = interaction.message
        self.done = True
        self.stop()
        try:
            await interaction.response.edit_message(embed=next_embed, content=None, view=next_view)
        except Exception:
            log.exception("传炸弹编辑消息成新面板失败，尝试频道直发兜底")
            channel = interaction.channel
            if channel is not None:
                try:
                    next_view.message = await channel.send(embed=next_embed, view=next_view)
                except Exception:
                    log.exception("传炸弹兜底发送也失败，游戏会卡在这里，请管理员介入")
