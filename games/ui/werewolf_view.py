# -*- coding: utf-8 -*-
"""
狼人杀 UI —— 招募、夜晚私信操作、白天轮流发言、投票、猎人开枪。

设计要点（沿用之前踩坑后的两条铁律）：
1. 先响应交互，再做慢操作（发私信、遍历成员）。Discord 交互令牌只有 3 秒，
   任何"先发一圈私信再更新公屏"的写法都会静默失败。
2. 所有公屏播报都走 _say()：失败会逐层降级并记日志，绝不允许流程静默卡死。

夜晚是一个由 advance_night() 驱动的状态机：
每个角色的操作 View 完成（或超时）后都会再调一次 advance_night()，推进到下一个角色，
直到 night_resolve 时统一结算并公示。
"""

import logging

import discord
from discord.ui import View, Select, Button, Modal, TextInput, select, button

from src.chat.features.games.config.games_config import (
    WEREWOLF_MIN_PLAYERS, WEREWOLF_JOIN_TIME, WEREWOLF_NIGHT_ACTION_TIME,
    WEREWOLF_SPEAK_TIME, WEREWOLF_VOTE_TIME, WEREWOLF_HUNTER_SHOOT_TIME,
    ROLE_WOLF, ROLE_WITCH, ROLE_SEER, ROLE_GUARD,
)
from src.chat.features.games.services import betting
from src.chat.features.games.services.werewolf_service import (
    werewolf_service, ROLE_INTROS,
)

log = logging.getLogger(__name__)


def _name(guild, uid: int) -> str:
    m = guild.get_member(uid) if guild else None
    return m.display_name if m else f"玩家 {uid}"


def _options(guild, ids: list[int]) -> list[discord.SelectOption]:
    return [discord.SelectOption(label=_name(guild, pid), value=str(pid)) for pid in ids]


async def _say(channel, content: str, view: View | None = None):
    """公屏播报，失败记日志并返回 None，绝不抛到调用方把流程打断。"""
    if channel is None:
        log.error("狼人杀播报失败：没有频道对象")
        return None
    try:
        return await channel.send(content, view=view)
    except Exception:
        log.exception("狼人杀公屏播报失败：%s", content[:80])
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
                await self.message.edit(content="🐺 招募超时，这局狼人杀取消了。人齐了再开～", view=self)
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

        # 先响应，再发身份私信（私信是慢操作，放在响应之后）
        await interaction.response.edit_message(
            content=(
                f"🐺 **狼人杀开局！** 共 {len(game.players)} 人\n"
                f"参与：{', '.join(f'<@{p}>' for p in game.players)}\n"
                f"身份正在私信发送中……"
            ),
            view=None,
        )
        channel = interaction.channel
        guild = interaction.guild

        dm_failed: list[int] = []
        for pid in game.players:
            role = game.roles[pid]
            intro = ROLE_INTROS.get(role, f"你的身份：{role}")
            body = f"🎭 **本局你的身份：{role}**\n{intro}"
            if role == ROLE_WOLF:
                mates = [p for p in game.holders(ROLE_WOLF, alive_only=False) if p != pid]
                if mates:
                    body += "\n\n你的狼队友：" + "、".join(_name(guild, m) for m in mates)
                else:
                    body += "\n\n你是本局唯一的狼。"
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
    """把夜晚推到下一个需要人操作的角色；没有人可操作时直接结算。"""
    game = werewolf_service.get_game(channel_id)
    if game is None:
        return
    phase = game.phase

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


async def _prompt_wolves(channel, guild, game):
    wolves = game.holders(ROLE_WOLF)
    await _say(channel, "🐺 狼人请睁眼，正在私信里商量今晚刀谁……")
    targets = [p for p in game.alive() if not game.is_wolf(p)]
    reachable = 0
    mates = "、".join(_name(guild, w) for w in wolves)
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


class WolfVoteView(_NightActionView):
    expect_phase = "night_wolf"

    def __init__(self, channel_id, actor_id, channel, guild, targets):
        super().__init__(channel_id, actor_id, channel, guild)
        self.children[0].options = _options(guild, targets)

    @select(placeholder="今晚刀谁……", options=[])
    async def pick(self, interaction: discord.Interaction, select_: Select):
        if interaction.user.id != self.actor_id:
            await interaction.response.send_message("这不是你的界面。", ephemeral=True)
            return
        target = int(select_.values[0])
        result = werewolf_service.wolf_vote(self.channel_id, self.actor_id, target)
        if "error" in result:
            await interaction.response.send_message(result["error"], ephemeral=True)
            return

        self.done = True
        self.stop()
        for c in self.children:
            c.disabled = True
        if result.get("all_done"):
            final = result.get("target")
            text = f"🐺 狼队最终决定刀 **{_name(self.guild, final)}**。"
        else:
            text = (f"🐺 你投了 **{_name(self.guild, target)}**"
                    f"（{result['voted']}/{result['needed']} 已投，等队友）。")
        try:
            await interaction.response.edit_message(content=text, view=self)
        except Exception:
            log.exception("狼人杀狼刀回执编辑失败")
        if result.get("all_done"):
            await advance_night(self.channel, self.guild, self.channel_id)


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
        text = f"☀️ **第 {game.day} 天** 天亮了。\n昨晚是**平安夜**，没有人死亡。"
    else:
        text = (f"☀️ **第 {game.day} 天** 天亮了。\n"
                f"昨晚死亡：{', '.join(f'<@{p}>' for p in deaths)}（身份不公开）")
    await _say(channel, text)

    if result.get("game_over"):
        await finish_game(channel, guild, game, result)
        return

    if result.get("pending_hunter"):
        await _prompt_hunter(channel, guild, game, result["pending_hunter"])
        return

    await start_discussion(channel, guild, channel_id)


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
        await start_discussion(channel, guild, channel_id)
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


# ============================ 白天：轮流发言 ============================


async def start_discussion(channel, guild, channel_id: int):
    """播报讨论顺序并把麦克风交给第一位。发言顺序由服务层维护，这里只负责播报。"""
    game = werewolf_service.get_game(channel_id)
    if game is None:
        return
    if game.phase != "day_discuss" or not game.speak_order:
        werewolf_service.begin_discussion(game)
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
    text = "🗳️ **投票结束**\n" + ("\n".join(lines) if lines else "一票都没有……")
    if result.get("forced"):
        text = "⏰ 投票超时，按已有票数结算。\n" + text

    if result.get("idiot_revealed"):
        text += (f"\n\n🤪 <@{result['idiot_revealed']}> 是**白痴**！翻牌但不出局，"
                 f"之后不能再投票。本轮无人淘汰。")
    elif result.get("tie"):
        text += "\n\n🤝 平票！这不是 bug——按规则本轮无人淘汰，直接入夜。"
    elif result.get("eliminated"):
        text += f"\n\n⚰️ <@{result['eliminated']}> 被投票出局（身份不公开）。"
    else:
        text += "\n\n本轮无人淘汰。"

    await _say(channel, text)

    if result.get("game_over"):
        if game is not None:
            await finish_game(channel, guild, game, result)
        return

    if result.get("pending_hunter"):
        await _prompt_hunter(channel, guild, game, result["pending_hunter"])
        return

    await advance_night(channel, guild, channel_id)


# ============================ 收场 ============================


async def finish_game(channel, guild, game, result: dict):
    winner = result.get("winner")
    roles = result.get("roles") or dict(game.roles)
    banner = "🎉 **好人阵营胜利！**" if winner == "good" else "🐺 **狼人阵营胜利！**"
    lines = [banner, "", "**身份公布**"]
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

    await _say(channel, "\n".join(lines))
