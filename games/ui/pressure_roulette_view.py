# -*- coding: utf-8 -*-
"""加压俄罗斯轮盘 UI —— 招募 + 操作界面。

界面规范（用户示例风格）：
    🔫 加压俄罗斯轮盘
    🔫 第 N 枪
    轮到 @玩家 了。

    弹巢 空 [?] ? ? ?　│　枪内 2 发　│　赌注 💤 6 分钟

    中弹概率　2 / 5　≈ 40%

    ⏳ 60 秒内不动手，枪会自己响。

    存活（按行动顺序）　@玩家A、@玩家B、...
    已出局
    @玩家C　💥 中弹 · 💀 6 分钟
"""

import logging

import discord
from discord.ui import View, Button, button

from src.chat.features.games.config.games_config import (
    PRESSURE_ROULETTE_JOIN_TIME, PRESSURE_ROULETTE_TURN_TIME,
    PRESSURE_ROULETTE_MAX_PLAYERS, PRESSURE_ROULETTE_MUTE_ROLE_ID,
)
from src.chat.features.games.services import betting
from src.chat.features.games.services.pressure_roulette_service import (
    pressure_roulette_service,
)

log = logging.getLogger(__name__)


def _name(guild, uid: int) -> str:
    m = guild.get_member(uid) if guild else None
    return m.display_name if m else f"玩家 {uid}"


async def _say(channel, content: str, view: View | None = None):
    if channel is None:
        log.error("加压轮盘播报失败：没有频道对象")
        return None
    try:
        return await channel.send(content, view=view)
    except Exception:
        log.exception("加压轮盘公屏播报失败：%s", content[:80])
        return None


def _render_board(game, guild) -> str:
    """渲染弹巢 + 概率 + 赌注 + 存活/出局列表。"""
    chamber_str = "　".join(game.chamber_display())
    remaining_live = game.remaining_live()
    remaining_chamber = game.remaining_chamber()
    if remaining_chamber > 0:
        pct = round(remaining_live / remaining_chamber * 100)
        prob_line = f"\n中弹概率　{remaining_live} / {remaining_chamber}　≈ {pct}%"
    else:
        prob_line = ""

    alive = game.alive()
    alive_str = "、".join(_name(guild, p) for p in alive) if alive else "无"

    lines = [
        f"弹巢 {chamber_str}　│　枪内 {remaining_live} 发　│　赌注 💤 {game.stake_minutes} 分钟",
        prob_line,
        f"\n存活（按行动顺序）　{alive_str}",
    ]
    if game.dead:
        dead_str = "、".join(
            f"<@{p}>　💥 中弹 · 💀 {game.stake_minutes} 分钟" for p in game.dead
        )
        lines.append(f"\n已出局\n{dead_str}")
    return "\n".join(lines)


# ============================ 招募 ============================


class PressureRouletteJoinView(View):
    def __init__(self, channel_id: int, bet: int, host_id: int):
        super().__init__(timeout=PRESSURE_ROULETTE_JOIN_TIME)
        self.channel_id = channel_id
        self.bet = bet
        self.host_id = host_id
        self.started = False
        self.message: discord.Message | None = None

    async def on_timeout(self):
        if self.started:
            return
        pressure_roulette_service.cancel_game(self.channel_id)
        for c in self.children:
            c.disabled = True
        if self.message:
            try:
                await self.message.edit(
                    content="🔫 招募超时，这局加压轮盘取消了。人齐了再开～",
                    view=self,
                )
            except discord.HTTPException:
                pass

    @button(label="加入", style=discord.ButtonStyle.success, emoji="🙋")
    async def join(self, interaction: discord.Interaction, button: Button):
        try:
            bal = await betting.get_balance(interaction.user.id)
        except Exception:
            log.exception("加压轮盘查余额失败")
            bal = 0
        if bal < self.bet:
            await interaction.response.send_message(
                f"你只有 {bal} 春春币，不够交 {self.bet} 的局费。", ephemeral=True)
            return
        ok, msg = pressure_roulette_service.add_player(self.channel_id, interaction.user.id)
        if not ok:
            await interaction.response.send_message(msg, ephemeral=True)
            return
        game = pressure_roulette_service.get_game(self.channel_id)
        await interaction.response.send_message(
            f"{interaction.user.mention} 上桌了 🙋（当前 {len(game.players)} 人）")

    @button(label="退出", style=discord.ButtonStyle.secondary, emoji="🚪")
    async def leave(self, interaction: discord.Interaction, button: Button):
        ok, msg = pressure_roulette_service.leave_player(self.channel_id, interaction.user.id)
        await interaction.response.send_message(msg, ephemeral=not ok)

    @button(label="开始游戏", style=discord.ButtonStyle.primary, emoji="🔫")
    async def start(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.host_id:
            await interaction.response.send_message("只有发起人能开始。", ephemeral=True)
            return
        ok, msg, game = pressure_roulette_service.start_game(self.channel_id)
        if not ok:
            await interaction.response.send_message(msg, ephemeral=True)
            return
        self.started = True
        for c in self.children:
            c.disabled = True

        await interaction.response.edit_message(
            content=(
                f"🔫 **加压俄罗斯轮盘开局！** 共 {len(game.players)} 人\n"
                f"参与：{', '.join(f'<@{p}>' for p in game.players)}\n"
                f"6 弹巢开局 1 发实弹，轮流对自己扣扳机。中弹 = 禁言 + 出局 + 扣局费。\n"
                f"三选一：传枪 / 再开一枪 / 加压。子弹打光游戏结束，最后一人赢。"
            ),
            view=None,
        )
        channel = interaction.channel
        guild = interaction.guild
        await _post_turn(channel, guild, self.channel_id)


# ============================ 回合操作 ====================


async def _post_turn(channel, guild, channel_id: int):
    """渲染当前回合界面。"""
    game = pressure_roulette_service.get_game(channel_id)
    if game is None or game.phase != "turn":
        return
    cur = game.current_player()
    if cur is None:
        return
    view = PressureRouletteTurnView(channel_id, cur, channel, guild)
    board = _render_board(game, guild)
    view.message = await _say(
        channel,
        f"🔫 **第 {game.shot_count + 1} 枪**\n轮到 <@{cur}> 了。\n\n{board}\n\n"
        f"⏳ {PRESSURE_ROULETTE_TURN_TIME} 秒内不动手，枪会自己响。",
        view=view,
    )


class PressureRouletteTurnView(View):
    """当前玩家的操作按钮。"""

    def __init__(self, channel_id: int, current_player: int, channel, guild):
        super().__init__(timeout=PRESSURE_ROULETTE_TURN_TIME)
        self.channel_id = channel_id
        self.current_player = current_player
        self.channel = channel
        self.guild = guild
        self.acted = False
        self.message: discord.Message | None = None

    async def on_timeout(self):
        if self.acted:
            return
        self.acted = True
        for c in self.children:
            c.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass
        # 超时自动开枪
        result = pressure_roulette_service.timeout_shoot(self.channel_id)
        if "error" in result:
            return
        await _announce(self.channel, self.guild, self.channel_id, result)

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.current_player:
            await interaction.response.send_message("还没轮到你。", ephemeral=True)
            return False
        if self.acted:
            await interaction.response.send_message("这一枪已经响了。", ephemeral=True)
            return False
        return True

    async def _after_action(self, interaction: discord.Interaction, result: dict):
        self.acted = True
        self.stop()
        for c in self.children:
            c.disabled = True
        try:
            await interaction.response.edit_message(view=self)
        except Exception:
            log.exception("加压轮盘按钮编辑失败")
        await _announce(self.channel, self.guild, self.channel_id, result)

    @button(label="传枪", style=discord.ButtonStyle.secondary, emoji="🔫")
    async def pass_gun(self, interaction: discord.Interaction, button: Button):
        if not await self._guard(interaction):
            return
        result = pressure_roulette_service.pass_gun(self.channel_id, interaction.user.id)
        if "error" in result:
            await interaction.response.send_message(result["error"], ephemeral=True)
            return
        await interaction.response.send_message(
            f"🔫 你选择了传枪，弹巢前进一格，交给下一个人。", ephemeral=True)
        await self._after_action(interaction, result)

    @button(label="再开一枪", style=discord.ButtonStyle.primary, emoji="🔁")
    async def shoot_self(self, interaction: discord.Interaction, button: Button):
        if not await self._guard(interaction):
            return
        result = pressure_roulette_service.shoot_self(self.channel_id, interaction.user.id)
        if "error" in result:
            await interaction.response.send_message(result["error"], ephemeral=True)
            return
        await self._after_action(interaction, result)

    @button(label="加压", style=discord.ButtonStyle.danger, emoji="💥")
    async def press(self, interaction: discord.Interaction, button: Button):
        if not await self._guard(interaction):
            return
        result = pressure_roulette_service.press(self.channel_id, interaction.user.id)
        if "error" in result:
            await interaction.response.send_message(result["error"], ephemeral=True)
            return
        loaded = result.get("loaded", 1)
        await interaction.response.send_message(
            f"💥 你加压了！装了 {loaded} 发子弹，赌注涨到 {result['stake_minutes']} 分钟。"
            f"现在对自己开一枪吧（点「再开一枪」）。",
            ephemeral=True)
        # 加压不消耗回合，仍轮到自己，重新渲染按钮
        # 但要更新消息让其他人看到状态
        game = pressure_roulette_service.get_game(self.channel_id)
        if game is not None:
            board = _render_board(game, self.guild)
            try:
                await self.message.edit(
                    content=(
                        f"🔫 **第 {game.shot_count + 1} 枪**\n轮到 <@{self.current_player}> 了。\n\n"
                        f"{board}\n\n⏳ {PRESSURE_ROULETTE_TURN_TIME} 秒内不动手，枪会自己响。"
                    ),
                    view=self,
                )
            except Exception:
                log.exception("加压后更新消息失败")


# ============================ 结果播报 ====================


async def _announce(channel, guild, channel_id: int, result: dict):
    """根据操作结果播报。"""
    game = pressure_roulette_service.get_game(channel_id)
    action = result.get("action")

    if action == "pass":
        next_p = result.get("next")
        await _say(channel, (
            f"🔫 <@{result['by']}> 选择了传枪，弹巢前进一格。\n"
            f"下一个是 <@{next_p}>。"
        ))
        if not result.get("game_over"):
            await _post_turn(channel, guild, channel_id)
        return

    if action == "shoot" and not result.get("game_over"):
        if result.get("hit"):
            victim = result["victim"]
            mute = result.get("mute_minutes", 0)
            await _say(channel, (
                f"💥 砰！\n<@{victim}> 已被消音 {mute} 分钟。\n原因：手气。"
            ))
            await _apply_mute(guild, victim, mute)
        else:
            victim = result["victim"]
            streak = result.get("streak", 0)
            await _say(channel, (
                f"😮‍💨 空枪\n没响。\n枪：下次一定。\n"
                f"<@{victim}> 撑过了这一枪，连开蓄力 {streak} 层。"
            ))
        await _post_turn(channel, guild, channel_id)
        return

    if result.get("game_over"):
        await _announce_end(channel, guild, channel_id, result)
        return


async def _announce_end(channel, guild, channel_id: int, result: dict):
    game = pressure_roulette_service.get_game(channel_id)
    if game is None:
        return

    # 最后一枪的播报
    if result.get("last_hit"):
        victim = result.get("last_victim")
        mute = result.get("mute_minutes", 0)
        await _say(channel, (
            f"💥 砰！\n<@{victim}> 已被消音 {mute} 分钟。\n原因：手气。"
        ))
        await _apply_mute(guild, victim, mute)
    elif result.get("last_victim") is not None and not result.get("last_hit"):
        await _say(channel, "😮‍💨 空枪\n没响。")

    # 结局播报
    winner = result.get("winner")
    if winner is not None:
        await _say(channel, f"🔫 子弹打光，游戏结束！\n🏆 冠军：<@{winner}>！零禁言。")
    else:
        alive = result.get("alive", [])
        names = "、".join(f"<@{p}>" for p in alive)
        await _say(channel, f"🔫 子弹打光，游戏结束！\n🤝 平局，谁也没赢。存活：{names}")

    # 结算
    settle = await pressure_roulette_service.settle(game)
    if settle.get("error"):
        await _say(channel, "（结算已处理过，不重复扣派）")
    else:
        if settle.get("winner") is not None:
            share = settle.get("share", 0)
            if share > 0:
                await _say(channel, f"💰 败方每人交 {game.bet} 币，冠军分得 **{share}** 春春币。")
            else:
                await _say(channel, "💰 败方局费没有扣到，本局不派彩。")
            if settle.get("deduct_failed"):
                names = "、".join(f"<@{p}>" for p in settle["deduct_failed"])
                await _say(channel, f"⚠️ {names} 局费扣款失败，已跳过。")


async def _apply_mute(guild, user_id: int, minutes: int):
    """中弹禁言。复用狼人杀的身份组机制。"""
    if not PRESSURE_ROULETTE_MUTE_ROLE_ID or guild is None or minutes <= 0:
        return
    try:
        member = guild.get_member(user_id)
        if member is None:
            return
        await member.timeout_for(discord.utils.timedelta(minutes=minutes),
                                 reason="加压轮盘中弹")
    except Exception:
        log.exception("加压轮盘禁言失败（检查机器人「管理身份组/超时成员」权限）")
