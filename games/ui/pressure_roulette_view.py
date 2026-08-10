# -*- coding: utf-8 -*-
"""加压俄罗斯轮盘 UI —— 招募 + 操作界面（对齐原项目）。

界面规范：
    🔫 加压俄罗斯轮盘
    🔫 第 N 枪
    轮到 @玩家 了。

    弹巢 空 枪口 ? ? ?　│　枪内 2 发　│　赌注 💤 6 分钟

    中弹概率　2 / 5　≈ 40%

    ⏳ 60 秒内不动手，枪会自己响。

    存活（按行动顺序）　@玩家A、@玩家B、...
    已出局
    @玩家C　💥 中弹 · 💀 6 分钟

面板滚动窗口：保留 3 条，结束时清场只留 1 条。
"""

import logging
from datetime import timedelta

import discord
from discord.ui import View, Button, button

from src.chat.features.games.config.games_config import (
    PRESSURE_ROULETTE_JOIN_TIME, PRESSURE_ROULETTE_TURN_TIME,
    PRESSURE_ROULETTE_MAX_PLAYERS, PRESSURE_ROULETTE_MUTE_ROLE_ID,
    PRESSURE_ROULETTE_PANEL_HISTORY_LIMIT,
)
from src.chat.features.games.services import betting
from src.chat.features.games.services.pressure_roulette_service import (
    pressure_roulette_service,
)

log = logging.getLogger(__name__)


def _name(guild, uid: int) -> str:
    m = guild.get_member(uid) if guild else None
    return m.display_name if m else f"玩家 {uid}"


class PanelManager:
    """面板滚动窗口管理：保留 N 条，结束时清场。"""

    def __init__(self, channel):
        self.channel = channel
        self.panels: list[discord.Message] = []

    async def render(self, content: str, view: View | None = None) -> discord.Message | None:
        """发新面板，旧面板摘按钮，超出窗口删最旧。"""
        if self.channel is None:
            return None
        try:
            msg = await self.channel.send(content, view=view)
        except Exception:
            log.exception("加压轮盘发面板失败")
            return None
        self.panels.append(msg)
        # 摘旧面板按钮
        for old in self.panels[:-1]:
            try:
                await old.edit(view=None)
            except Exception:
                pass
        # 删超出窗口的
        while len(self.panels) > PRESSURE_ROULETTE_PANEL_HISTORY_LIMIT:
            old = self.panels.pop(0)
            try:
                await old.delete()
            except Exception:
                pass
        return msg

    async def prune_to_final(self) -> None:
        """结束时只留最后一条。"""
        if len(self.panels) <= 1:
            return
        doomed = self.panels[:-1]
        self.panels = self.panels[-1:]
        for msg in doomed:
            try:
                await msg.delete()
            except Exception:
                pass


def _render_board(game, guild) -> str:
    """渲染弹巢 + 概率 + 赌注 + 存活/出局列表。"""
    chamber_str = "　".join(game.chamber_display())
    bullets = game.bullets
    unknown = game.unknown_count()
    if unknown > 0:
        pct = round(bullets / unknown * 100)
        prob_line = f"\n中弹概率　{bullets} / {unknown}　≈ {pct}%"
    else:
        prob_line = ""

    alive = game.alive()
    alive_str = "、".join(_name(guild, p) for p in alive) if alive else "无"

    lines = [
        f"弹巢 {chamber_str}　│　枪内 {bullets} 发　│　赌注 💤 {game.current_stake()} 分钟",
        prob_line,
        f"\n存活（按行动顺序）　{alive_str}",
    ]
    if game.eliminated:
        dead_str = "\n".join(
            f"<@{e['user_id']}>　💥 中弹 · 💀 {e['minutes']} 分钟"
            for e in game.eliminated
        )
        lines.append(f"\n已出局\n{dead_str}")
    if game.cowards:
        coward_str = "\n".join(
            f"<@{c['user_id']}>　🤡 胆小鬼 · {c['penalty_minutes']} 分钟"
            for c in game.cowards
        )
        lines.append(f"\n胆小鬼\n{coward_str}")
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
                f"活下来后：传枪 / 再开一枪 / 加压 / 反手还击 / 抽弹 / 退出"
            ),
            view=None,
        )
        channel = interaction.channel
        guild = interaction.guild
        await _post_fire(channel, guild, self.channel_id)


# ============================ fire 阶段 ====================


async def _post_fire(channel, guild, channel_id: int):
    """渲染 fire 阶段界面。"""
    game = pressure_roulette_service.get_game(channel_id)
    if game is None or game.state != "playing" or game.phase != "fire":
        return
    cur = game.current_player()
    if cur is None:
        return
    view = FireView(channel_id, cur, channel, guild)
    board = _render_board(game, guild)
    view.message = await _say(
        channel,
        f"🔫 **第 {game.shot_number + 1} 枪**\n轮到 <@{cur}> 了。\n\n{board}\n\n"
        f"⏳ {PRESSURE_ROULETTE_TURN_TIME} 秒内不动手，枪会自己响。",
        view=view,
    )


class FireView(View):
    """fire 阶段：扣扳机 / 抽弹 / 退出。"""

    def __init__(self, channel_id: int, current_player: int, channel, guild):
        super().__init__(timeout=PRESSURE_ROULETTE_TURN_TIME)
        self.channel_id = channel_id
        self.current_player = current_player
        self.channel = channel
        self.guild = guild
        self.acted = False
        self.message: discord.Message | None = None

        game = pressure_roulette_service.get_game(channel_id)
        # 抽弹按钮：枪里≥3发 + 没用过 + 不在反手序列 + 不是戴罪者
        if game:
            can_unload = (not game.riposte and game.bullets >= 3
                          and current_player not in game.unload_used)
            if not can_unload:
                self.remove_item(self.unload_btn)
            # 退出按钮：不是戴罪者 + 不在反手序列
            can_quit = (not game.riposte and not game.is_redeemer(current_player))
            if not can_quit:
                self.remove_item(self.quit_btn)

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
        result = pressure_roulette_service.timeout_fire(self.channel_id)
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

    async def _after(self, interaction: discord.Interaction, result: dict):
        self.acted = True
        self.stop()
        for c in self.children:
            c.disabled = True
        try:
            await interaction.response.edit_message(view=self)
        except Exception:
            log.exception("加压轮盘按钮编辑失败")
        await _announce(self.channel, self.guild, self.channel_id, result)

    @button(label="扣扳机", style=discord.ButtonStyle.primary, emoji="🔫")
    async def shoot(self, interaction: discord.Interaction, button: Button):
        if not await self._guard(interaction):
            return
        result = pressure_roulette_service.shoot_self(self.channel_id, interaction.user.id)
        if "error" in result:
            await interaction.response.send_message(result["error"], ephemeral=True)
            return
        await self._after(interaction, result)

    @button(label="抽弹开枪", style=discord.ButtonStyle.secondary, emoji="🔧")
    async def unload_btn(self, interaction: discord.Interaction, button: Button):
        if not await self._guard(interaction):
            return
        result = pressure_roulette_service.unload(self.channel_id, interaction.user.id)
        if "error" in result:
            await interaction.response.send_message(result["error"], ephemeral=True)
            return
        await self._after(interaction, result)

    @button(label="胆小鬼退出", style=discord.ButtonStyle.secondary, emoji="🤡")
    async def quit_btn(self, interaction: discord.Interaction, button: Button):
        if not await self._guard(interaction):
            return
        result = pressure_roulette_service.quit(self.channel_id, interaction.user.id)
        if "error" in result:
            await interaction.response.send_message(result["error"], ephemeral=True)
            return
        await self._after(interaction, result)


# ============================ choice 阶段 ====================


async def _post_choice(channel, guild, channel_id: int):
    """渲染 choice 阶段界面。"""
    game = pressure_roulette_service.get_game(channel_id)
    if game is None or game.state != "playing" or game.phase != "choice":
        return
    cur = game.current_player()
    if cur is None:
        return
    view = ChoiceView(channel_id, cur, channel, guild)
    board = _render_board(game, guild)
    streak = game.charge_for(cur)
    load_bullets = game.load_bullets_for(cur)
    load_stake = game.current_stake() + load_bullets
    msg = (
        f"😮‍💨 空枪！<@{cur}> 撑过了这一枪，连开蓄力 {streak} 层。\n\n{board}\n\n"
        f"选择：\n"
        f"　🔫 传枪 — 弹巢前进一格，交给下一个人\n"
        f"　🔁 再开一枪 — 继续对自己开，攒蓄力（当前 {streak} 层）\n"
        f"　💥 加压 — 装 {load_bullets} 发，赌注涨到 {load_stake} 分钟\n"
    )
    if game.riposte_holder_id == cur and game.riposte_target_id:
        target = game.riposte_target_id
        msg += f"　🔙 反手还击 — 把枪扔回给 <@{target}>（加压者）\n"
    msg += f"\n⏳ {PRESSURE_ROULETTE_TURN_TIME} 秒内不动手，默认传枪。"
    view.message = await _say(channel, msg, view=view)


class ChoiceView(View):
    """choice 阶段：传枪 / 再开 / 加压 / 反手。"""

    def __init__(self, channel_id: int, current_player: int, channel, guild):
        super().__init__(timeout=PRESSURE_ROULETTE_TURN_TIME)
        self.channel_id = channel_id
        self.current_player = current_player
        self.channel = channel
        self.guild = guild
        self.acted = False
        self.message: discord.Message | None = None

        game = pressure_roulette_service.get_game(channel_id)
        if game:
            # 反手按钮：有反手权 + 有目标 + 目标在场
            can_riposte = (game.riposte_holder_id == current_player
                           and game.riposte_target_id
                           and game.riposte_target_id in game.alive())
            if not can_riposte:
                self.remove_item(self.riposte_btn)
            # 加压按钮：不满巢
            if game.bullets >= 6:
                self.remove_item(self.press_btn)

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
        result = pressure_roulette_service.timeout_choice(self.channel_id)
        if "error" in result:
            return
        await _announce(self.channel, self.guild, self.channel_id, result)

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.current_player:
            await interaction.response.send_message("还没轮到你。", ephemeral=True)
            return False
        if self.acted:
            await interaction.response.send_message("这一轮已经结束了。", ephemeral=True)
            return False
        return True

    async def _after(self, interaction: discord.Interaction, result: dict):
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
    async def pass_btn(self, interaction: discord.Interaction, button: Button):
        if not await self._guard(interaction):
            return
        result = pressure_roulette_service.handle_choice(
            self.channel_id, interaction.user.id, "pass")
        if "error" in result:
            await interaction.response.send_message(result["error"], ephemeral=True)
            return
        await self._after(interaction, result)

    @button(label="再开一枪", style=discord.ButtonStyle.primary, emoji="🔁")
    async def again_btn(self, interaction: discord.Interaction, button: Button):
        if not await self._guard(interaction):
            return
        result = pressure_roulette_service.handle_choice(
            self.channel_id, interaction.user.id, "again")
        if "error" in result:
            await interaction.response.send_message(result["error"], ephemeral=True)
            return
        await self._after(interaction, result)

    @button(label="加压", style=discord.ButtonStyle.danger, emoji="💥")
    async def press_btn(self, interaction: discord.Interaction, button: Button):
        if not await self._guard(interaction):
            return
        result = pressure_roulette_service.handle_choice(
            self.channel_id, interaction.user.id, "load")
        if "error" in result:
            await interaction.response.send_message(result["error"], ephemeral=True)
            return
        await self._after(interaction, result)

    @button(label="反手还击", style=discord.ButtonStyle.danger, emoji="🔙")
    async def riposte_btn(self, interaction: discord.Interaction, button: Button):
        if not await self._guard(interaction):
            return
        result = pressure_roulette_service.riposte(self.channel_id, interaction.user.id)
        if "error" in result:
            await interaction.response.send_message(result["error"], ephemeral=True)
            return
        await self._after(interaction, result)


# ============================ 结果播报 ====================


async def _say(channel, content: str, view: View | None = None):
    if channel is None:
        log.error("加压轮盘播报失败：没有频道对象")
        return None
    try:
        return await channel.send(content, view=view)
    except Exception:
        log.exception("加压轮盘公屏播报失败：%s", content[:80])
        return None


async def _announce(channel, guild, channel_id: int, result: dict):
    """根据操作结果播报并推进。"""
    action = result.get("action")
    game = pressure_roulette_service.get_game(channel_id)

    if result.get("game_over"):
        await _announce_end(channel, guild, channel_id, result)
        return

    if action == "shoot" and result.get("hit"):
        victim = result["victim"]
        mute = result.get("mute_minutes", 0)
        await _say(channel, f"💥 砰！\n<@{victim}> 已被消音 {mute} 分钟。\n原因：手气。")
        await _apply_mute(guild, victim, mute)
        await _post_fire(channel, guild, channel_id)
        return

    if action == "shoot" and not result.get("hit"):
        shooter = result["by"]
        streak = result.get("streak", 0)
        riposte_stage = result.get("riposte_stage")
        if riposte_stage == "target":
            await _say(channel, f"😮‍💨 空枪！加压者 <@{shooter}> 撑过了反手一枪，现在轮到发起人补枪。")
            await _post_fire(channel, guild, channel_id)
        else:
            await _say(channel, f"😮‍💨 空枪\n没响。\n枪：下次一定。")
            await _post_choice(channel, guild, channel_id)
        return

    if action == "pass":
        by = result["by"]
        nxt = result.get("next")
        await _say(channel, f"🔫 <@{by}> 选择了传枪，弹巢前进一格，交给下一个人。")
        await _post_fire(channel, guild, channel_id)
        return

    if action == "again":
        by = result["by"]
        await _say(channel, f"🔁 <@{by}> 选择再开一枪，继续对自己扣扳机。")
        await _post_fire(channel, guild, channel_id)
        return

    if action == "load":
        by = result["by"]
        loaded = result.get("loaded", 1)
        stake = result.get("stake_minutes", 0)
        await _say(channel, f"💥 <@{by}> 加压了！装了 {loaded} 发子弹，赌注涨到 {stake} 分钟。")
        await _post_fire(channel, guild, channel_id)
        return

    if action == "riposte":
        by = result["by"]
        target = result.get("target")
        await _say(channel, f"🔙 <@{by}> 反手还击！把枪扔回给 <@{target}>，加压者必须开一枪。")
        await _post_fire(channel, guild, channel_id)
        return

    if action == "quit":
        by = result["by"]
        penalty = result.get("penalty_minutes", 5)
        await _say(channel, f"🤡 <@{by}> 选择了胆小鬼退出，名字将被挂上 🤡 {penalty} 分钟。")
        await _apply_coward_penalty(guild, by, penalty)
        await _post_fire(channel, guild, channel_id)
        return

    if action == "unload":
        by = result["by"]
        await _say(channel, f"🔧 <@{by}> 抽弹开枪！卸掉 1 发，重转弹巢，立刻扣扳机。")
        # 抽弹后 _perform_shot 的结果会走 _announce
        return


async def _announce_end(channel, guild, channel_id: int, result: dict):
    game = pressure_roulette_service.get_game(channel_id)
    if game is None:
        return

    # 最后一枪播报
    if result.get("last_hit"):
        victim = result.get("last_victim")
        mute = result.get("mute_minutes", 0)
        await _say(channel, f"💥 砰！\n<@{victim}> 已被消音 {mute} 分钟。\n原因：手气。")
        await _apply_mute(guild, victim, mute)
    elif result.get("last_victim") is not None and not result.get("last_hit"):
        if result.get("action") == "quit":
            pass  # 退出已在 _announce 播报
        else:
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
    """中弹禁言。"""
    if guild is None or minutes <= 0:
        return
    try:
        member = guild.get_member(user_id)
        if member is None:
            return
        await member.timeout(timedelta(minutes=minutes),
                                 reason="加压轮盘中弹")
    except Exception:
        log.exception("加压轮盘禁言失败（检查机器人「超时成员」权限）")


async def _apply_coward_penalty(guild, user_id: int, minutes: int):
    """胆小鬼惩罚：改昵称挂🤡 + 禁言。"""
    if guild is None:
        return
    try:
        member = guild.get_member(user_id)
        if member is None:
            return
        # 改昵称
        old_nick = member.display_name
        if not old_nick.startswith("🤡"):
            try:
                await member.edit(nick=f"🤡 {old_nick}", reason="加压轮盘胆小鬼")
            except Exception:
                log.warning("加压轮盘改昵称失败（检查机器人「管理昵称」权限）")
        # 禁言
        await member.timeout(timedelta(minutes=minutes),
                                 reason="加压轮盘胆小鬼")
    except Exception:
        log.exception("加压轮盘胆小鬼惩罚失败")
