# -*- coding: utf-8 -*-
"""
狼人杀 UI —— 招募、局初统计、夜晚（狼人私密频道 + 私信）、警长竞选、
轮流发言（可选按身份组禁言）、投票、死亡公开身份、猎人开枪、警徽移交。

两条铁律（沿用之前踩坑经验）：
1. 先响应交互，再做慢操作（发私信 / 建频道 / 改权限）。交互令牌只有 3 秒。
2. 所有公屏播报走 _say()、私信走 _dm()：失败记日志并降级，绝不让流程静默卡死。
"""

import logging

import discord
from discord.ui import View, Select, Button, Modal, TextInput, select, button

from src.chat.features.games.config.games_config import (
    WEREWOLF_MIN_PLAYERS, WEREWOLF_JOIN_TIME, WEREWOLF_NIGHT_ACTION_TIME,
    WEREWOLF_SPEAK_TIME, WEREWOLF_VOTE_TIME, WEREWOLF_HUNTER_SHOOT_TIME,
    WEREWOLF_CAMPAIGN_SPEAK_TIME, WEREWOLF_SHERIFF_VOTE_TIME,
    WEREWOLF_SHERIFF_SIGNUP_TIME, WEREWOLF_SHERIFF_TRANSFER_TIME,
    WEREWOLF_ELECT_SHERIFF, WEREWOLF_MUTE_ROLE_ID, WEREWOLF_MUTE_DURING_DISCUSSION,
    WEREWOLF_USE_WOLF_CHANNEL, WEREWOLF_WOLF_CHANNEL_CATEGORY_ID,
    ROLE_WOLF, ROLE_WITCH, ROLE_SEER, ROLE_GUARD,
)
from src.chat.features.games.services import betting
from src.chat.features.games.services.werewolf_service import (
    werewolf_service, ROLE_INTROS,
)
from src.chat.features.games.ui.embed_utils import (
    game_embed, COLOR_PLAYING, COLOR_WIN, COLOR_LOSE, COLOR_DRAW, COLOR_MUTE,
)

log = logging.getLogger(__name__)

# 每局临时建的狼人私密频道：公屏 channel_id -> 频道对象
_wolf_channels: dict[int, "discord.abc.GuildChannel"] = {}


def _name(guild, uid: int) -> str:
    m = guild.get_member(uid) if guild else None
    return m.display_name if m else f"玩家 {uid}"


def _options(guild, ids: list[int]) -> list[discord.SelectOption]:
    return [discord.SelectOption(label=_name(guild, pid), value=str(pid)) for pid in ids]


def _reveal(game, uid: int) -> str:
    """带身份（和警长标记）的死亡展示。"""
    badge = "👮 " if game and game.sheriff_id == uid else ""
    return f"{badge}<@{uid}>（{game.roles.get(uid, '？')}）"


async def _set_mute(guild, channel, mute: bool):
    """按身份组禁言/解禁当前频道。未配置身份组或权限不足则静默跳过。"""
    if not WEREWOLF_MUTE_DURING_DISCUSSION or not WEREWOLF_MUTE_ROLE_ID:
        return
    if guild is None or channel is None:
        return
    role = guild.get_role(WEREWOLF_MUTE_ROLE_ID)
    if role is None:
        log.warning("狼人杀禁言失败：找不到身份组 %s", WEREWOLF_MUTE_ROLE_ID)
        return
    try:
        ow = channel.overwrites_for(role)
        ow.send_messages = False if mute else None
        await channel.set_permissions(role, overwrite=ow,
                                      reason="狼人杀发言禁言" if mute else "狼人杀发言解禁")
    except Exception:
        log.exception("狼人杀改禁言权限失败（检查机器人「管理身份组」权限）")


async def _say(channel, content: str = "", embed: discord.Embed | None = None, view: View | None = None):
    """公屏播报，失败记日志并返回 None，绝不抛到调用方把流程打断。"""
    if channel is None:
        log.error("狼人杀播报失败：没有频道对象")
        return None
    try:
        return await channel.send(content=content or None, embed=embed, view=view)
    except Exception:
        log.exception("狼人杀公屏播报失败")
        return None


async def _dm(guild, uid: int, content: str, view: View | None = None) -> bool:
    member = guild.get_member(uid) if guild else None
    if member is None:
        log.warning("狼人杀私信失败：拿不到成员 %s（检查 members intent）", uid)
        return False
    try:
        await member.send(content, view=view)
        return True
    except Exception:
        log.warning("狼人杀私信发送失败 uid=%s（可能关闭了私信）", uid)
        return False


# ============================ 招募 ============================


class WerewolfJoinView(View):
    def __init__(self, channel_id: int, bet: int, host_id: int):
        super().__init__(timeout=WEREWOLF_JOIN_TIME)
        self.channel_id = channel_id
        self.bet = bet
        self.host_id = host_id
        self.started = False
        self.message: discord.Message | None = None

    async def on_timeout(self):
        if self.started:
            return
        werewolf_service.cancel_game(self.channel_id)
        for c in self.children:
            c.disabled = True
        if self.message:
            try:
                timeout_embed = game_embed(
                    title="🐺 招募超时",
                    description="这局狼人杀取消了。人齐了再开～",
                    color=COLOR_DRAW,
                    footer=f"房间 #{self.channel_id}",
                )
                await self.message.edit(embed=timeout_embed, content=None, view=self)
            except discord.HTTPException:
                pass

    @button(label="加入", style=discord.ButtonStyle.success, emoji="🙋")
    async def join(self, interaction: discord.Interaction, button: Button):
        try:
            bal = await betting.get_balance(interaction.user.id)
        except Exception:
            log.exception("狼人杀查余额失败")
            bal = 0
        if bal < self.bet:
            await interaction.response.send_message(
                f"你只有 {bal} 春春币，不够交 {self.bet} 的局费。", ephemeral=True)
            return
        ok, msg = werewolf_service.add_player(self.channel_id, interaction.user.id)
        if not ok:
            await interaction.response.send_message(msg, ephemeral=True)
            return
        game = werewolf_service.get_game(self.channel_id)
        await interaction.response.send_message(
            f"{interaction.user.mention} 上桌了 🙋（当前 {len(game.players)} 人）")

    @button(label="退出", style=discord.ButtonStyle.secondary, emoji="🚪")
    async def leave(self, interaction: discord.Interaction, button: Button):
        ok, msg = werewolf_service.leave_player(self.channel_id, interaction.user.id)
        await interaction.response.send_message(msg, ephemeral=not ok)

    @button(label="开始游戏", style=discord.ButtonStyle.primary, emoji="🐺")
    async def start(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.host_id:
            await interaction.response.send_message("只有发起人能开始。", ephemeral=True)
            return
        ok, msg, game = werewolf_service.start_game(self.channel_id)
        if not ok:
            await interaction.response.send_message(msg, ephemeral=True)
            return
        self.started = True
        for c in self.children:
            c.disabled = True

        start_embed = game_embed(
            title="🐺 狼人杀开局！",
            description=(
                f"共 {len(game.players)} 人\n"
                f"参与：{', '.join(f'<@{p}>' for p in game.players)}\n"
                f"📋 本局配置：**{game.role_summary()}**\n"
                f"身份正在私信发送中……"
            ),
            color=COLOR_PLAYING,
            footer=f"房间 #{self.channel_id}",
        )
        await interaction.response.edit_message(embed=start_embed, content=None, view=None)
        channel = interaction.channel
        guild = interaction.guild

        dm_failed: list[int] = []
        for pid in game.players:
            role = game.roles[pid]
            body = f"🎭 **本局你的身份：{role}**\n{ROLE_INTROS.get(role, '')}"
            if role == ROLE_WOLF:
                mates = [p for p in game.holders(ROLE_WOLF, alive_only=False) if p != pid]
                body += ("\n\n你的狼队友：" + "、".join(_name(guild, m) for m in mates)) if mates else "\n\n你是本局唯一的狼。"
            if not await _dm(guild, pid, body):
                dm_failed.append(pid)

        if dm_failed:
            await _say(channel, (
                f"⚠️ {', '.join(f'<@{p}>' for p in dm_failed)} 私信发不出去（可能关了私信），"
                f"请开启对本服务器成员的私信后重开一局。"
            ))
        await advance_night(channel, guild, self.channel_id)


# ============================ 夜晚驱动 ============================


async def advance_night(channel, guild, channel_id: int):
    game = werewolf_service.get_game(channel_id)
    if game is None:
        return
    phase = game.phase
    if phase.startswith("night_"):
        await _set_mute(guild, channel, True)
    if phase == "night_guard":
        await _prompt_guard(channel, guild, game)
    elif phase == "night_wolf":
        await _prompt_wolves(channel, guild, game)
    elif phase == "night_witch":
        await _prompt_witch(channel, guild, game)
    elif phase == "night_seer":
        await _prompt_seer(channel, guild, game)
    elif phase in ("night_resolve", "day_announce"):
        await announce_dawn(channel, guild, channel_id)
    else:
        log.warning("狼人杀 advance_night 遇到意外阶段：%s", phase)


async def _prompt_guard(channel, guild, game):
    guard = game.holders(ROLE_GUARD)[0]
    await _say(channel, f"🌙 **第 {game.day} 夜** 天黑请闭眼。\n🛡️ 守卫请睁眼，正在私信里选择守护对象……")
    targets = [p for p in game.alive() if p != game.guard_last_target]
    view = GuardView(game.channel_id, guard, channel, guild, targets)
    if not await _dm(guild, guard, "🛡️ 守卫，今晚守护谁？", view):
        werewolf_service.skip_night_phase(game.channel_id, "night_guard")
        await advance_night(channel, guild, game.channel_id)


async def _create_wolf_channel(guild, base_channel, game):
    """给狼人建临时私密频道。失败返回 None（回退到私信）。"""
    if not WEREWOLF_USE_WOLF_CHANNEL or guild is None:
        return None
    try:
        me = guild.me
        overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=False)}
        if me is not None:
            overwrites[me] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
        for w in game.holders(ROLE_WOLF):
            member = guild.get_member(w)
            if member is not None:
                overwrites[member] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
        category = None
        if WEREWOLF_WOLF_CHANNEL_CATEGORY_ID:
            category = guild.get_channel(WEREWOLF_WOLF_CHANNEL_CATEGORY_ID)
        elif getattr(base_channel, "category", None) is not None:
            category = base_channel.category
        return await guild.create_text_channel(
            name=f"狼人频道-{game.channel_id % 10000}", overwrites=overwrites,
            category=category, reason="狼人杀狼人私密讨论")
    except Exception:
        log.exception("狼人杀建狼人频道失败（检查机器人「管理频道」权限），回退到私信")
        return None


async def _cleanup_wolf_channel(channel_id: int):
    ch = _wolf_channels.pop(channel_id, None)
    if ch is not None:
        try:
            await ch.delete(reason="狼人杀狼人频道回收")
        except Exception:
            log.warning("狼人杀狼人频道删除失败")


async def _prompt_wolves(channel, guild, game):
    wolves = game.holders(ROLE_WOLF)
    targets = [p for p in game.alive() if not game.is_wolf(p)]
    mates = "、".join(_name(guild, w) for w in wolves)

    wolf_ch = await _create_wolf_channel(guild, channel, game)
    if wolf_ch is not None:
        _wolf_channels[game.channel_id] = wolf_ch
        await _say(channel, "🐺 狼人请睁眼，去你们的私密频道商量今晚刀谁……")
        view = WolfVoteView(game.channel_id, None, channel, guild, targets, shared=True)
        await _say(wolf_ch, f"🐺 狼队：{mates}\n在这里讨论，然后**每个狼各自从下拉里投一刀**（平票随机）：", view)
        return

    # 回退：私信各自投
    await _say(channel, "🐺 狼人请睁眼，正在私信里商量今晚刀谁……")
    reachable = 0
    for wolf in wolves:
        view = WolfVoteView(game.channel_id, wolf, channel, guild, targets)
        if await _dm(guild, wolf, f"🐺 狼队：{mates}\n今晚刀谁？（所有狼投完票才生效，平票随机）", view):
            reachable += 1
    if reachable == 0:
        werewolf_service.skip_night_phase(game.channel_id, "night_wolf")
        await advance_night(channel, guild, game.channel_id)


async def _prompt_witch(channel, guild, game):
    witch = game.holders(ROLE_WITCH)[0]
    await _say(channel, "🧪 女巫请睁眼，正在私信里决定用药……")
    wolf_target = game.night.get("wolf_target")
    if wolf_target is None:
        info = "今晚**平安夜**，没有人被刀。"
    else:
        info = f"今晚被刀的是：**{_name(guild, wolf_target)}**。"
    info += (
        f"\n解药：{'已用完' if game.witch_antidote_used else '可用'}"
        f"　毒药：{'已用完' if game.witch_poison_used else '可用'}"
    )
    view = WitchView(game.channel_id, witch, channel, guild, game)
    if not await _dm(guild, witch, f"🧪 女巫，{info}", view):
        werewolf_service.skip_night_phase(game.channel_id, "night_witch")
        await advance_night(channel, guild, game.channel_id)


async def _prompt_seer(channel, guild, game):
    seer = game.holders(ROLE_SEER)[0]
    await _say(channel, "🔮 预言家请睁眼，正在私信里查验身份……")
    targets = [p for p in game.alive() if p != seer]
    view = SeerView(game.channel_id, seer, channel, guild, targets)
    if not await _dm(guild, seer, "🔮 预言家，今晚查验谁？", view):
        werewolf_service.skip_night_phase(game.channel_id, "night_seer")
        await advance_night(channel, guild, game.channel_id)


class _NightActionView(View):
    """夜晚操作 View 基类：超时自动跳过本阶段并推进夜晚。"""

    expect_phase = ""

    def __init__(self, channel_id: int, actor_id: int, channel, guild):
        super().__init__(timeout=WEREWOLF_NIGHT_ACTION_TIME)
        self.channel_id = channel_id
        self.actor_id = actor_id
        self.channel = channel
        self.guild = guild
        self.done = False

    async def on_timeout(self):
        if self.done:
            return
        self.done = True
        result = werewolf_service.skip_night_phase(self.channel_id, self.expect_phase)
        if "error" in result:
            return
        await advance_night(self.channel, self.guild, self.channel_id)

    async def _finish(self, interaction: discord.Interaction, text: str):
        self.done = True
        self.stop()
        for c in self.children:
            c.disabled = True
        try:
            await interaction.response.edit_message(content=text, view=self)
        except Exception:
            log.exception("狼人杀夜晚操作回执编辑失败")
        await advance_night(self.channel, self.guild, self.channel_id)


class GuardView(_NightActionView):
    expect_phase = "night_guard"

    def __init__(self, channel_id, actor_id, channel, guild, targets):
        super().__init__(channel_id, actor_id, channel, guild)
        self.children[0].options = _options(guild, targets)

    @select(placeholder="今晚守护谁……", options=[])
    async def pick(self, interaction: discord.Interaction, select_: Select):
        if interaction.user.id != self.actor_id:
            await interaction.response.send_message("这不是你的界面。", ephemeral=True)
            return
        target = int(select_.values[0])
        result = werewolf_service.guard_protect(self.channel_id, self.actor_id, target)
        if "error" in result:
            await interaction.response.send_message(result["error"], ephemeral=True)
            return
        await self._finish(interaction, f"🛡️ 今晚你守护了 **{_name(self.guild, target)}**。")


class WolfVoteView(View):
    """狼刀投票。shared=True 发在狼人频道（任一存活狼可点）；否则私信给单个狼。"""

    def __init__(self, channel_id, actor_id, channel, guild, targets, shared=False):
        super().__init__(timeout=WEREWOLF_NIGHT_ACTION_TIME)
        self.channel_id = channel_id
        self.actor_id = actor_id
        self.channel = channel
        self.guild = guild
        self.shared = shared
        self.done = False
        self.children[0].options = _options(guild, targets)

    async def on_timeout(self):
        if self.done:
            return
        self.done = True
        result = werewolf_service.skip_night_phase(self.channel_id, "night_wolf")
        if "error" in result:
            return
        await _cleanup_wolf_channel(self.channel_id)
        await advance_night(self.channel, self.guild, self.channel_id)

    @select(placeholder="今晚刀谁……", options=[])
    async def pick(self, interaction: discord.Interaction, select_: Select):
        uid = interaction.user.id
        game = werewolf_service.get_game(self.channel_id)
        if game is None or not game.is_wolf(uid) or uid in game.dead:
            await interaction.response.send_message("只有存活的狼人能投刀。", ephemeral=True)
            return
        if not self.shared and uid != self.actor_id:
            await interaction.response.send_message("这不是你的界面。", ephemeral=True)
            return
        target = int(select_.values[0])
        result = werewolf_service.wolf_vote(self.channel_id, uid, target)
        if "error" in result:
            await interaction.response.send_message(result["error"], ephemeral=True)
            return

        if result.get("all_done"):
            self.done = True
            self.stop()
            final = result.get("target")
            try:
                await interaction.response.send_message(f"🐺 狼队最终决定刀 **{_name(self.guild, final)}**。")
            except Exception:
                pass
            await _cleanup_wolf_channel(self.channel_id)
            await advance_night(self.channel, self.guild, self.channel_id)
        else:
            await interaction.response.send_message(
                f"🐺 你投了 **{_name(self.guild, target)}**"
                f"（{result['voted']}/{result['needed']} 已投，等队友）。", ephemeral=True)


class WitchView(_NightActionView):
    expect_phase = "night_witch"

    def __init__(self, channel_id, actor_id, channel, guild, game):
        super().__init__(channel_id, actor_id, channel, guild)
        self.wolf_target = game.night.get("wolf_target")
        self.antidote_used = game.witch_antidote_used
        self.poison_used = game.witch_poison_used
        self.poison_targets = [p for p in game.alive() if p != actor_id]

    @button(label="用解药救人", style=discord.ButtonStyle.success, emoji="💚")
    async def save(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.actor_id:
            await interaction.response.send_message("这不是你的界面。", ephemeral=True)
            return
        result = werewolf_service.witch_action(self.channel_id, self.actor_id, "save")
        if "error" in result:
            await interaction.response.send_message(result["error"], ephemeral=True)
            return
        await self._finish(interaction, f"💚 你救了 **{_name(self.guild, self.wolf_target)}**，解药用完了。")

    @button(label="用毒药杀人", style=discord.ButtonStyle.danger, emoji="☠️")
    async def poison(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.actor_id:
            await interaction.response.send_message("这不是你的界面。", ephemeral=True)
            return
        if self.poison_used:
            await interaction.response.send_message("毒药已经用过了。", ephemeral=True)
            return
        view = WitchPoisonView(self.channel_id, self.actor_id, self.channel, self.guild,
                               self.poison_targets, parent=self)
        await interaction.response.send_message("☠️ 毒谁？", view=view, ephemeral=True)

    @button(label="今晚不用药", style=discord.ButtonStyle.secondary, emoji="🙅")
    async def nothing(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.actor_id:
            await interaction.response.send_message("这不是你的界面。", ephemeral=True)
            return
        result = werewolf_service.witch_action(self.channel_id, self.actor_id, "none")
        if "error" in result:
            await interaction.response.send_message(result["error"], ephemeral=True)
            return
        await self._finish(interaction, "🙅 今晚你没有用药。")


class WitchPoisonView(View):
    def __init__(self, channel_id, actor_id, channel, guild, targets, parent: WitchView):
        super().__init__(timeout=WEREWOLF_NIGHT_ACTION_TIME)
        self.channel_id = channel_id
        self.actor_id = actor_id
        self.channel = channel
        self.guild = guild
        self.parent = parent
        self.children[0].options = _options(guild, targets)

    @select(placeholder="毒谁……", options=[])
    async def pick(self, interaction: discord.Interaction, select_: Select):
        if interaction.user.id != self.actor_id:
            await interaction.response.send_message("这不是你的界面。", ephemeral=True)
            return
        target = int(select_.values[0])
        result = werewolf_service.witch_action(self.channel_id, self.actor_id, "poison", target)
        if "error" in result:
            await interaction.response.send_message(result["error"], ephemeral=True)
            return
        self.parent.done = True
        self.parent.stop()
        for c in self.children:
            c.disabled = True
        try:
            await interaction.response.edit_message(
                content=f"☠️ 你毒死了 **{_name(self.guild, target)}**，毒药用完了。", view=self)
        except Exception:
            log.exception("狼人杀下毒回执编辑失败")
        await advance_night(self.channel, self.guild, self.channel_id)


class SeerView(_NightActionView):
    expect_phase = "night_seer"

    def __init__(self, channel_id, actor_id, channel, guild, targets):
        super().__init__(channel_id, actor_id, channel, guild)
        self.children[0].options = _options(guild, targets)

    @select(placeholder="查验谁……", options=[])
    async def pick(self, interaction: discord.Interaction, select_: Select):
        if interaction.user.id != self.actor_id:
            await interaction.response.send_message("这不是你的界面。", ephemeral=True)
            return
        target = int(select_.values[0])
        result = werewolf_service.seer_check(self.channel_id, self.actor_id, target)
        if "error" in result:
            await interaction.response.send_message(result["error"], ephemeral=True)
            return
        icon = "🐺" if result["camp"] == "狼人" else "👍"
        await self._finish(
            interaction,
            f"🔮 **{_name(self.guild, target)}** 的身份是 {icon} **{result['camp']}**。别急着报，想清楚怎么用。",
        )


# ============================ 天亮 ============================


async def announce_dawn(channel, guild, channel_id: int):
    game = werewolf_service.get_game(channel_id)
    if game is None:
        return
    result = werewolf_service.resolve_night(channel_id)
    if "error" in result:
        log.warning("狼人杀夜晚结算失败：%s", result["error"])
        return

    deaths = result.get("deaths") or []
    if not deaths:
        dawn_desc = f"第 {game.day} 天 天亮了。\n昨晚是**平安夜**，没有人死亡。"
        dawn_color = COLOR_PLAYING
    else:
        dawn_desc = f"第 {game.day} 天 天亮了。\n昨晚死亡：{'、'.join(_reveal(game, p) for p in deaths)}"
        dawn_color = COLOR_LOSE
    dawn_embed = game_embed(
        title="☀️ 天亮了",
        description=dawn_desc,
        color=dawn_color,
        footer=f"房间 #{channel_id}",
    )
    await _say(channel, embed=dawn_embed)

    if result.get("game_over"):
        await finish_game(channel, guild, game, result)
        return

    if result.get("sheriff_died"):
        await _handle_sheriff_death(channel, guild, game, result["sheriff_died"])

    if result.get("pending_hunter"):
        await _prompt_hunter(channel, guild, game, result["pending_hunter"])
        return

    await enter_day(channel, guild, channel_id)


async def enter_day(channel, guild, channel_id: int):
    """第一天先选警长，其余天直接讨论。"""
    game = werewolf_service.get_game(channel_id)
    if game is None:
        return
    if WEREWOLF_ELECT_SHERIFF and game.day == 1 and not game.sheriff_done and len(game.alive()) >= 3:
        await start_sheriff_election(channel, guild, channel_id)
    else:
        await start_discussion(channel, guild, channel_id)


async def _handle_sheriff_death(channel, guild, game, sheriff_id: int):
    """警长死亡：私信让他指定移交对象，拒绝/超时则销毁警徽。不阻塞主流程。"""
    await _say(channel, f"👮 警长 <@{sheriff_id}> 出局了，正在私信里决定警徽移交给谁……")
    candidates = [p for p in game.alive()]
    view = SheriffTransferView(game.channel_id, sheriff_id, channel, guild, candidates)
    if not await _dm(guild, sheriff_id, "👮 你是警长，要把警徽移交给谁？（不移交则销毁）", view):
        werewolf_service.sheriff_transfer(game.channel_id, sheriff_id, None)
        await _say(channel, "👮 警徽无人接收，已销毁。")


class SheriffTransferView(View):
    def __init__(self, channel_id, from_id, channel, guild, candidates):
        super().__init__(timeout=WEREWOLF_SHERIFF_TRANSFER_TIME)
        self.channel_id = channel_id
        self.from_id = from_id
        self.channel = channel
        self.guild = guild
        self.done = False
        self.children[0].options = _options(guild, candidates)

    async def on_timeout(self):
        if self.done:
            return
        self.done = True
        werewolf_service.sheriff_transfer(self.channel_id, self.from_id, None)
        await _say(self.channel, "👮 警长没有及时移交，警徽销毁。")

    @select(placeholder="警徽给谁……", options=[])
    async def pick(self, interaction: discord.Interaction, select_: Select):
        if interaction.user.id != self.from_id:
            await interaction.response.send_message("这不是你的界面。", ephemeral=True)
            return
        target = int(select_.values[0])
        r = werewolf_service.sheriff_transfer(self.channel_id, self.from_id, target)
        if "error" in r:
            await interaction.response.send_message(r["error"], ephemeral=True)
            return
        self.done = True
        self.stop()
        for c in self.children:
            c.disabled = True
        await interaction.response.edit_message(content=f"👮 警徽移交给了 **{_name(self.guild, target)}**。", view=self)
        await _say(self.channel, f"👮 警徽移交给了 <@{target}>！")

    @button(label="不移交（销毁警徽）", style=discord.ButtonStyle.secondary, emoji="💥")
    async def destroy(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.from_id:
            await interaction.response.send_message("这不是你的界面。", ephemeral=True)
            return
        werewolf_service.sheriff_transfer(self.channel_id, self.from_id, None)
        self.done = True
        self.stop()
        for c in self.children:
            c.disabled = True
        await interaction.response.edit_message(content="👮 你选择销毁警徽。", view=self)
        await _say(self.channel, "👮 警长选择销毁警徽，本局不再有警长。")


async def _prompt_hunter(channel, guild, game, hunter_id: int):
    await _say(channel, f"🔫 <@{hunter_id}> 是猎人，正在私信里决定要不要开枪……")
    view = HunterView(game.channel_id, hunter_id, channel, guild,
                      [p for p in game.alive() if p != hunter_id])
    if not await _dm(guild, hunter_id, "🔫 你出局了，要开枪带走谁吗？", view):
        werewolf_service.hunter_shoot(game.channel_id, hunter_id, None)
        await _continue_after_hunter(channel, guild, game.channel_id)


async def _continue_after_hunter(channel, guild, channel_id: int):
    game = werewolf_service.get_game(channel_id)
    if game is None:
        return
    if game.phase == "day_discuss":
        await enter_day(channel, guild, channel_id)
    elif game.phase.startswith("night_"):
        await advance_night(channel, guild, channel_id)


class HunterView(View):
    def __init__(self, channel_id, hunter_id, channel, guild, targets):
        super().__init__(timeout=WEREWOLF_HUNTER_SHOOT_TIME)
        self.channel_id = channel_id
        self.hunter_id = hunter_id
        self.channel = channel
        self.guild = guild
        self.done = False
        self.children[0].options = _options(guild, targets)

    async def on_timeout(self):
        if self.done:
            return
        self.done = True
        await self._resolve(None, None)

    async def _resolve(self, interaction: discord.Interaction | None, target: int | None):
        result = werewolf_service.hunter_shoot(self.channel_id, self.hunter_id, target)
        if "error" in result:
            if interaction is not None:
                await interaction.response.send_message(result["error"], ephemeral=True)
            return
        for c in self.children:
            c.disabled = True
        if interaction is not None:
            try:
                await interaction.response.edit_message(
                    content=("🔫 你开枪带走了 " + _name(self.guild, target)) if target else "🔫 你选择了不开枪。",
                    view=self)
            except Exception:
                log.exception("狼人杀猎人回执编辑失败")

        if target is not None:
            await _say(self.channel, f"🔫 猎人 <@{self.hunter_id}> 开枪带走了 <@{target}>！")
        else:
            await _say(self.channel, f"🔫 猎人 <@{self.hunter_id}> 没有开枪。")

        game = werewolf_service.get_game(self.channel_id)
        if result.get("game_over"):
            if game is not None:
                await finish_game(self.channel, self.guild, game, result)
            return
        await _continue_after_hunter(self.channel, self.guild, self.channel_id)

    @select(placeholder="开枪带走谁……", options=[])
    async def pick(self, interaction: discord.Interaction, select_: Select):
        if interaction.user.id != self.hunter_id:
            await interaction.response.send_message("这不是你的界面。", ephemeral=True)
            return
        self.done = True
        self.stop()
        await self._resolve(interaction, int(select_.values[0]))

    @button(label="不开枪", style=discord.ButtonStyle.secondary, emoji="🙅")
    async def hold(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.hunter_id:
            await interaction.response.send_message("这不是你的界面。", ephemeral=True)
            return
        self.done = True
        self.stop()
        await self._resolve(interaction, None)


# ============================ 警长竞选 ============================


async def start_sheriff_election(channel, guild, channel_id: int):
    game = werewolf_service.get_game(channel_id)
    if game is None:
        return
    werewolf_service.begin_sheriff_election(game)
    view = SheriffSignupView(channel_id, channel, guild)
    view.message = await _say(channel, (
        f"👮 **警长竞选** 存活玩家 {WEREWOLF_SHERIFF_SIGNUP_TIME} 秒内选择是否上警。\n"
        f"警长白天放逐投票算 **1.5 票**。"
    ), view=view)


class SheriffSignupView(View):
    def __init__(self, channel_id, channel, guild):
        super().__init__(timeout=WEREWOLF_SHERIFF_SIGNUP_TIME)
        self.channel_id = channel_id
        self.channel = channel
        self.guild = guild
        self.finished = False
        self.message: discord.Message | None = None

    async def _close(self):
        if self.finished:
            return
        self.finished = True
        for c in self.children:
            c.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass
        r = werewolf_service.close_signup(self.channel_id)
        if r.get("no_sheriff"):
            await _say(self.channel, "👮 没有人上警，本局无警长，直接进入讨论。")
            await start_discussion(self.channel, self.guild, self.channel_id)
        else:
            cands = "、".join(_name(self.guild, c) for c in r["candidates"])
            await _say(self.channel, f"👮 上警的有：{cands}。按顺序竞选发言。")
            await _post_campaign_prompt(self.channel, self.guild, self.channel_id)

    async def on_timeout(self):
        await self._close()

    @button(label="上警", style=discord.ButtonStyle.primary, emoji="👮")
    async def run(self, interaction: discord.Interaction, button: Button):
        r = werewolf_service.sheriff_signup(self.channel_id, interaction.user.id, True)
        if "error" in r:
            await interaction.response.send_message(r["error"], ephemeral=True)
            return
        await interaction.response.send_message("👮 你选择**上警**。", ephemeral=True)
        await self._maybe_all_signed(r)

    @button(label="不上警", style=discord.ButtonStyle.secondary, emoji="🙅")
    async def stay(self, interaction: discord.Interaction, button: Button):
        r = werewolf_service.sheriff_signup(self.channel_id, interaction.user.id, False)
        if "error" in r:
            await interaction.response.send_message(r["error"], ephemeral=True)
            return
        await interaction.response.send_message("🙅 你选择**不上警**。", ephemeral=True)
        await self._maybe_all_signed(r)

    async def _maybe_all_signed(self, r: dict):
        if r.get("signed") and r.get("total") and r["signed"] >= r["total"]:
            await self._close()


async def _post_campaign_prompt(channel, guild, channel_id: int):
    game = werewolf_service.get_game(channel_id)
    if game is None or game.phase != "sheriff_campaign":
        return
    speaker = werewolf_service.campaign_current_speaker(game)
    if speaker is None:
        return
    view = CampaignSpeakView(channel_id, speaker, channel, guild)
    view.message = await _say(channel, (
        f"🎤 候选人 <@{speaker}> 竞选发言，限时 {WEREWOLF_CAMPAIGN_SPEAK_TIME} 秒。"
    ), view=view)


async def _after_campaign(channel, guild, channel_id: int, result: dict):
    if result.get("campaign_done"):
        if result.get("no_sheriff"):
            await _say(channel, "👮 候选人都退水了，本局无警长。")
            await start_discussion(channel, guild, channel_id)
        elif result.get("auto"):
            await _say(channel, f"👮 只剩一名候选人，<@{result['sheriff']}> 自动当选警长！")
            await start_discussion(channel, guild, channel_id)
        elif result.get("vote"):
            await _start_sheriff_vote(channel, guild, channel_id, result["candidates"])
    else:
        await _post_campaign_prompt(channel, guild, channel_id)


class CampaignSpeakModal(Modal):
    def __init__(self, channel_id, speaker_id, channel, guild, parent):
        super().__init__(title="竞选发言", timeout=WEREWOLF_CAMPAIGN_SPEAK_TIME)
        self.channel_id = channel_id
        self.speaker_id = speaker_id
        self.channel = channel
        self.guild = guild
        self.parent = parent
        self.text = TextInput(label="拉票发言（会公开）", style=discord.TextStyle.paragraph,
                              max_length=900, required=True)
        self.add_item(self.text)

    async def on_submit(self, interaction: discord.Interaction):
        r = werewolf_service.campaign_speak(self.channel_id, self.speaker_id, self.text.value)
        if "error" in r:
            await interaction.response.send_message(r["error"], ephemeral=True)
            return
        self.parent.done = True
        self.parent.stop()
        await interaction.response.send_message("✅ 已发言。", ephemeral=True)
        await _say(self.channel, f"🎤 **候选人 <@{self.speaker_id}>**\n{r['text']}")
        await _after_campaign(self.channel, self.guild, self.channel_id, r)


class CampaignSpeakView(View):
    def __init__(self, channel_id, speaker_id, channel, guild):
        super().__init__(timeout=WEREWOLF_CAMPAIGN_SPEAK_TIME)
        self.channel_id = channel_id
        self.speaker_id = speaker_id
        self.channel = channel
        self.guild = guild
        self.done = False
        self.message: discord.Message | None = None

    async def on_timeout(self):
        if self.done:
            return
        self.done = True
        r = werewolf_service.campaign_skip(self.channel_id, self.speaker_id)
        if "error" in r:
            return
        await _say(self.channel, f"⏰ <@{self.speaker_id}> 竞选超时未发言，跳过。")
        await _after_campaign(self.channel, self.guild, self.channel_id, r)

    @button(label="竞选发言", style=discord.ButtonStyle.primary, emoji="🎤")
    async def speak(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.speaker_id:
            await interaction.response.send_message("还没轮到你。", ephemeral=True)
            return
        if self.done:
            await interaction.response.send_message("这一轮已经结束了。", ephemeral=True)
            return
        await interaction.response.send_modal(
            CampaignSpeakModal(self.channel_id, self.speaker_id, self.channel, self.guild, self))

    @button(label="退水", style=discord.ButtonStyle.danger, emoji="💧")
    async def withdraw(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.speaker_id:
            await interaction.response.send_message("只有当前发言的候选人能在此退水。", ephemeral=True)
            return
        r = werewolf_service.campaign_withdraw(self.channel_id, self.speaker_id)
        if "error" in r:
            await interaction.response.send_message(r["error"], ephemeral=True)
            return
        self.done = True
        self.stop()
        for c in self.children:
            c.disabled = True
        await interaction.response.edit_message(content="💧 你退水了，不再参与警长竞选。", view=self)
        await _say(self.channel, f"💧 <@{self.speaker_id}> 退水，退出警长竞选。")
        await _after_campaign(self.channel, self.guild, self.channel_id, r)


async def _start_sheriff_vote(channel, guild, channel_id: int, candidates: list[int]):
    cands = "、".join(_name(guild, c) for c in candidates)
    view = SheriffVoteEntryView(channel_id, channel, guild)
    view.message = await _say(channel, (
        f"🗳️ **警长投票** 候选人：{cands}\n"
        f"非候选人限时 {WEREWOLF_SHERIFF_VOTE_TIME} 秒私密投票，平票则警徽流失。"
    ), view=view)


class SheriffVoteEntryView(View):
    def __init__(self, channel_id, channel, guild):
        super().__init__(timeout=WEREWOLF_SHERIFF_VOTE_TIME)
        self.channel_id = channel_id
        self.channel = channel
        self.guild = guild
        self.finished = False
        self.message: discord.Message | None = None

    async def on_timeout(self):
        if self.finished:
            return
        self.finished = True
        r = werewolf_service.force_sheriff_tally(self.channel_id)
        if "error" in r:
            return
        for c in self.children:
            c.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass
        await _announce_sheriff(self.channel, self.guild, self.channel_id, r)

    @button(label="投警长", style=discord.ButtonStyle.primary, emoji="🗳️")
    async def vote(self, interaction: discord.Interaction, button: Button):
        game = werewolf_service.get_game(self.channel_id)
        if game is None or game.phase != "sheriff_vote":
            await interaction.response.send_message("现在不是警长投票阶段。", ephemeral=True)
            return
        uid = interaction.user.id
        if uid not in werewolf_service.sheriff_voters(game):
            await interaction.response.send_message("候选人和出局者不能投警长。", ephemeral=True)
            return
        if uid in game.campaign["votes"]:
            await interaction.response.send_message("你已经投过了。", ephemeral=True)
            return
        view = SheriffVoteView(self.channel_id, uid, self.channel, self.guild,
                               werewolf_service._effective_candidates(game), self)
        await interaction.response.send_message("🗳️ 投给哪个候选人？（只有你能看见）", view=view, ephemeral=True)


class SheriffVoteView(View):
    def __init__(self, channel_id, voter_id, channel, guild, candidates, entry):
        super().__init__(timeout=WEREWOLF_SHERIFF_VOTE_TIME)
        self.channel_id = channel_id
        self.voter_id = voter_id
        self.channel = channel
        self.guild = guild
        self.entry = entry
        self.children[0].options = _options(guild, candidates)

    @select(placeholder="选你支持的警长……", options=[])
    async def pick(self, interaction: discord.Interaction, select_: Select):
        if interaction.user.id != self.voter_id:
            await interaction.response.send_message("这不是你的投票。", ephemeral=True)
            return
        target = int(select_.values[0])
        r = werewolf_service.sheriff_vote(self.channel_id, self.voter_id, target)
        if "error" in r:
            await interaction.response.send_message(r["error"], ephemeral=True)
            return
        for c in self.children:
            c.disabled = True
        if r.get("sheriff_done"):
            self.entry.finished = True
            self.entry.stop()
            await interaction.response.edit_message(content="✅ 已投票，等结果。", view=self)
            await _announce_sheriff(self.channel, self.guild, self.channel_id, r)
        else:
            await interaction.response.edit_message(
                content=f"✅ 已投 **{_name(self.guild, target)}**"
                        f"（{r['votes_count']}/{r['needed']}），等其他人。", view=self)


async def _announce_sheriff(channel, guild, channel_id: int, result: dict):
    if result.get("sheriff"):
        await _say(channel, f"👮 **<@{result['sheriff']}> 当选警长！** 白天投票算 1.5 票。")
    elif result.get("tie"):
        await _say(channel, "👮 警长投票平票，警徽流失，本局无警长。")
    else:
        await _say(channel, "👮 本局无警长。")
    await start_discussion(channel, guild, channel_id)


# ============================ 白天：轮流发言 ============================


async def start_discussion(channel, guild, channel_id: int):
    """播报讨论顺序并把麦克风交给第一位。发言顺序由服务层维护，这里只负责播报。"""
    game = werewolf_service.get_game(channel_id)
    if game is None:
        return
    if game.phase != "day_discuss" or not game.speak_order:
        werewolf_service.begin_discussion(game)
    await _set_mute(guild, channel, True)
    order = "　".join(f"{i + 1}.{_name(guild, p)}" for i, p in enumerate(game.speak_order))
    await _say(channel, (
        f"🗣️ **讨论环节** 按顺序轮流发言，每人 {WEREWOLF_SPEAK_TIME} 秒。\n"
        f"发言顺序：{order}\n"
        f"（正式发言点按钮、在私密输入框里写，小春娘会代你发到公屏；公屏闲聊不影响流程）"
    ))
    await _post_speaker_prompt(channel, guild, channel_id)


async def _post_speaker_prompt(channel, guild, channel_id: int):
    game = werewolf_service.get_game(channel_id)
    if game is None or game.phase != "day_discuss":
        return
    speaker = werewolf_service.current_speaker(game)
    if speaker is None:
        await start_voting(channel, guild, channel_id)
        return
    view = SpeakView(channel_id, speaker, channel, guild)
    view.message = await _say(channel, (
        f"🎤 轮到 <@{speaker}> 发言（{len(game.speeches) + 1}/{len(game.speak_order)}）"
        f"，限时 {WEREWOLF_SPEAK_TIME} 秒。"
    ), view=view)


class SpeakModal(Modal):
    def __init__(self, channel_id: int, speaker_id: int, channel, guild, parent: "SpeakView"):
        super().__init__(title="你的发言", timeout=WEREWOLF_SPEAK_TIME)
        self.channel_id = channel_id
        self.speaker_id = speaker_id
        self.channel = channel
        self.guild = guild
        self.parent = parent
        self.text = TextInput(
            label="想说什么（会被公开到公屏）",
            style=discord.TextStyle.paragraph,
            placeholder="过、我是好人、我昨晚验了谁……",
            max_length=900,
            required=True,
        )
        self.add_item(self.text)

    async def on_submit(self, interaction: discord.Interaction):
        result = werewolf_service.submit_speech(self.channel_id, self.speaker_id, self.text.value)
        if "error" in result:
            await interaction.response.send_message(result["error"], ephemeral=True)
            return
        self.parent.done = True
        self.parent.stop()
        await interaction.response.send_message("✅ 已发言。", ephemeral=True)
        await _say(self.channel, (
            f"🎤 **【{result['index']}/{result['total']}】<@{self.speaker_id}>**\n"
            f"{result['text']}"
        ))
        await _after_speech(self.channel, self.guild, self.channel_id, result)


async def _after_speech(channel, guild, channel_id: int, result: dict):
    if result.get("all_done"):
        await start_voting(channel, guild, channel_id)
    else:
        await _post_speaker_prompt(channel, guild, channel_id)


class SpeakView(View):
    def __init__(self, channel_id: int, speaker_id: int, channel, guild):
        super().__init__(timeout=WEREWOLF_SPEAK_TIME)
        self.channel_id = channel_id
        self.speaker_id = speaker_id
        self.channel = channel
        self.guild = guild
        self.done = False
        self.message: discord.Message | None = None

    async def on_timeout(self):
        if self.done:
            return
        self.done = True
        result = werewolf_service.skip_speech(self.channel_id, self.speaker_id)
        if "error" in result:
            return
        for c in self.children:
            c.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass
        await _say(self.channel, f"⏰ <@{self.speaker_id}> 超时没发言，跳过。")
        await _after_speech(self.channel, self.guild, self.channel_id, result)

    @button(label="发言", style=discord.ButtonStyle.primary, emoji="🎤")
    async def speak(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.speaker_id:
            await interaction.response.send_message("还没轮到你，等等。", ephemeral=True)
            return
        if self.done:
            await interaction.response.send_message("这一轮发言已经结束了。", ephemeral=True)
            return
        await interaction.response.send_modal(
            SpeakModal(self.channel_id, self.speaker_id, self.channel, self.guild, self))

    @button(label="过（不发言）", style=discord.ButtonStyle.secondary, emoji="⏭️")
    async def pass_turn(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.speaker_id:
            await interaction.response.send_message("还没轮到你，等等。", ephemeral=True)
            return
        result = werewolf_service.skip_speech(self.channel_id, self.speaker_id)
        if "error" in result:
            await interaction.response.send_message(result["error"], ephemeral=True)
            return
        self.done = True
        self.stop()
        for c in self.children:
            c.disabled = True
        await interaction.response.edit_message(content=f"⏭️ <@{self.speaker_id}> 选择了过。", view=self)
        await _after_speech(self.channel, self.guild, self.channel_id, result)


# ============================ 白天：投票 ============================


async def start_voting(channel, guild, channel_id: int):
    game = werewolf_service.get_game(channel_id)
    if game is None:
        return
    await _set_mute(guild, channel, False)
    if game.phase != "day_vote":
        werewolf_service.enter_voting(game)
    view = VoteEntryView(channel_id, channel, guild)
    view.message = await _say(channel, (
        f"🗳️ **投票环节** 限时 {WEREWOLF_VOTE_TIME} 秒，点按钮私密投票。\n"
        f"存活 {len(game.alive())} 人，可投票 {len(game.voters())} 人。平票则本轮不淘汰。"
    ), view=view)


class VoteEntryView(View):
    def __init__(self, channel_id: int, channel, guild):
        super().__init__(timeout=WEREWOLF_VOTE_TIME)
        self.channel_id = channel_id
        self.channel = channel
        self.guild = guild
        self.finished = False
        self.message: discord.Message | None = None

    async def on_timeout(self):
        if self.finished:
            return
        self.finished = True
        result = werewolf_service.force_tally(self.channel_id)
        if "error" in result:
            return
        for c in self.children:
            c.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass
        await announce_vote(self.channel, self.guild, self.channel_id, result)

    @button(label="我要投票", style=discord.ButtonStyle.danger, emoji="🗳️")
    async def vote(self, interaction: discord.Interaction, button: Button):
        game = werewolf_service.get_game(self.channel_id)
        if game is None or game.phase != "day_vote":
            await interaction.response.send_message("现在不是投票阶段。", ephemeral=True)
            return
        uid = interaction.user.id
        if uid not in game.players or uid in game.dead:
            await interaction.response.send_message("你不在这场游戏里。", ephemeral=True)
            return
        if not game.can_vote(uid):
            await interaction.response.send_message("你已经失去投票权了。", ephemeral=True)
            return
        if uid in game.votes:
            await interaction.response.send_message("你已经投过票了。", ephemeral=True)
            return
        candidates = [p for p in game.alive() if p != uid]
        view = VoteView(self.channel_id, uid, self.channel, self.guild, candidates, self)
        await interaction.response.send_message("🗳️ 投给谁？（只有你能看见）", view=view, ephemeral=True)


class VoteView(View):
    def __init__(self, channel_id, voter_id, channel, guild, candidates, entry: VoteEntryView):
        super().__init__(timeout=WEREWOLF_VOTE_TIME)
        self.channel_id = channel_id
        self.voter_id = voter_id
        self.channel = channel
        self.guild = guild
        self.entry = entry
        self.children[0].options = _options(guild, candidates)

    @select(placeholder="投出你怀疑的人……", options=[])
    async def pick(self, interaction: discord.Interaction, select_: Select):
        if interaction.user.id != self.voter_id:
            await interaction.response.send_message("这不是你的投票。", ephemeral=True)
            return
        target = int(select_.values[0])
        result = werewolf_service.submit_vote(self.channel_id, self.voter_id, target)
        if "error" in result:
            await interaction.response.send_message(result["error"], ephemeral=True)
            return
        for c in self.children:
            c.disabled = True

        if result.get("vote_done"):
            self.entry.finished = True
            self.entry.stop()
            await interaction.response.edit_message(content="✅ 已投票，等公屏出结果。", view=self)
            await announce_vote(self.channel, self.guild, self.channel_id, result)
        else:
            await interaction.response.edit_message(
                content=f"✅ 已投 **{_name(self.guild, target)}**"
                        f"（{result['votes_count']}/{result['needed']}），等其他人。",
                view=self)


async def announce_vote(channel, guild, channel_id: int, result: dict):
    game = werewolf_service.get_game(channel_id)
    lines = [f"{_name(guild, tid)}：{cnt} 票" for tid, cnt in (result.get("vote_count") or {}).items()]
    desc = "\n".join(lines) if lines else "一票都没有……"
    title = "🗳️ 投票结束"
    color = COLOR_PLAYING
    if result.get("forced"):
        title = "⏰ 投票超时"
        desc = "按已有票数结算。\n" + desc

    if result.get("idiot_revealed"):
        desc += (f"\n\n🤪 <@{result['idiot_revealed']}> 是**白痴**！翻牌但不出局，"
                 f"之后不能再投票。本轮无人淘汰。")
        color = COLOR_DRAW
    elif result.get("tie"):
        desc += "\n\n🤝 平票！这不是 bug——按规则本轮无人淘汰，直接入夜。"
        color = COLOR_DRAW
    elif result.get("eliminated"):
        desc += f"\n\n⚰️ {_reveal(game, result['eliminated'])} 被投票出局。"
        color = COLOR_LOSE
    else:
        desc += "\n\n本轮无人淘汰。"

    vote_embed = game_embed(title=title, description=desc, color=color, footer=f"房间 #{channel_id}")
    await _say(channel, embed=vote_embed)

    if result.get("game_over"):
        if game is not None:
            await finish_game(channel, guild, game, result)
        return

    if result.get("sheriff_died") and game is not None:
        await _handle_sheriff_death(channel, guild, game, result["sheriff_died"])

    if result.get("pending_hunter"):
        await _prompt_hunter(channel, guild, game, result["pending_hunter"])
        return

    await advance_night(channel, guild, channel_id)


# ============================ 收场 ============================


async def finish_game(channel, guild, game, result: dict):
    winner = result.get("winner")
    roles = result.get("roles") or dict(game.roles)
    if winner == "good":
        banner = "🎉 好人阵营胜利！"
        color = COLOR_WIN
    else:
        banner = "🐺 狼人阵营胜利！"
        color = COLOR_LOSE
    lines = ["**身份公布**"]
    for pid, role in roles.items():
        mark = "☠️" if pid in game.dead else "🙂"
        lines.append(f"{mark} {_name(guild, pid)} —— {role}")

    settle = await werewolf_service.settle(game, winner)
    if settle.get("error"):
        lines.append("\n（结算已处理过，不重复扣派）")
    else:
        if settle["share"] > 0:
            lines.append(f"\n💰 败方每人交 {game.bet} 币，胜方每人分得 **{settle['share']}** 春春币。")
        else:
            lines.append("\n💰 败方局费没有扣到，本局不派彩。")
        if settle["deduct_failed"]:
            lines.append(f"⚠️ {', '.join(f'<@{p}>' for p in settle['deduct_failed'])} 局费扣款失败，已跳过。")

    await _set_mute(guild, channel, False)
    await _cleanup_wolf_channel(game.channel_id)
    end_embed = game_embed(
        title=banner,
        description="\n".join(lines),
        color=color,
        footer=f"房间 #{game.channel_id}",
    )
    await _say(channel, embed=end_embed)
