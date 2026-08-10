# -*- coding: utf-8 -*-
"""
狼人杀服务 —— 纯逻辑状态机，不依赖 discord.py，可脱离宿主 bot 单测。

阶段流转：
    joining
      → night_guard → night_wolf → night_witch → night_seer   （夜晚，按序，无人存活的角色自动跳过）
      → day_announce（公示夜间死者）
      → day_hunter（若猎人夜死且非被毒，等待开枪）
      → [sheriff_signup → sheriff_campaign → sheriff_vote]     （仅第一天，可选）
      → day_discuss（轮流发言）
      → day_vote（私密投票）
      → day_vote_hunter（若猎人被投出，等待开枪）
      → 回到 night_guard …
      → ended

规则（可在 games_config.py 里调）：
- 守卫不能连续两晚守同一人；默认允许自守。
- 狼人各自投刀，最高票为目标，平票随机破题。
- 女巫解药/毒药各限一次，同一晚不能既救又毒。
- 同守同救默认判死（WEREWOLF_SAVE_AND_GUARD_KILLS）。
- 猎人被毒死不能开枪；被刀/被投出可以开枪。
- 白痴被投出翻牌但不死，之后默认失去投票权。
- 投票平票默认不淘汰，直接入夜。
- 警长仅第一天竞选：简单多数当选，警长白天放逐投票算 1.5 票；平票警徽流失；
  警长死亡可移交警徽给存活的另一人，拒绝/超时则销毁。
"""

import random
import logging

from src.chat.features.games.config.games_config import (
    WEREWOLF_MIN_PLAYERS, WEREWOLF_MAX_PLAYERS, WEREWOLF_ROLE_TABLE,
    ROLE_WOLF, ROLE_VILLAGER, ROLE_SEER, ROLE_WITCH, ROLE_HUNTER, ROLE_GUARD, ROLE_IDIOT,
    WEREWOLF_GUARD_SAME_TARGET_TWICE, WEREWOLF_GUARD_CAN_PROTECT_SELF,
    WEREWOLF_SAVE_AND_GUARD_KILLS, WEREWOLF_TIE_ELIMINATES_NOBODY,
    WEREWOLF_IDIOT_KEEPS_VOTE, WEREWOLF_ELECT_SHERIFF, WEREWOLF_SHERIFF_VOTE_WEIGHT,
)
from src.chat.features.games.services import betting
from src.chat.features.games.services.registry import GameRegistry

log = logging.getLogger(__name__)

NIGHT_SEQUENCE: list[tuple[str, str]] = [
    ("night_guard", ROLE_GUARD),
    ("night_wolf", ROLE_WOLF),
    ("night_witch", ROLE_WITCH),
    ("night_seer", ROLE_SEER),
]

ROLE_INTROS = {
    ROLE_WOLF: "🐺 你是**狼人**。夜晚和队友一起选一个人刀，白天要装得像好人。",
    ROLE_VILLAGER: "👨‍🌾 你是**平民**。没有技能，靠嘴和脑子把狼人投出去。",
    ROLE_SEER: "🔮 你是**预言家**。每晚可以查验一人的阵营，是好人还是狼。",
    ROLE_WITCH: "🧪 你是**女巫**。有一瓶解药和一瓶毒药，各只能用一次；每晚会知道谁被刀了。",
    ROLE_HUNTER: "🔫 你是**猎人**。被狼刀或被票出局时可以开枪带走一个人（被女巫毒死则不能开枪）。",
    ROLE_GUARD: "🛡️ 你是**守卫**。每晚守护一人（不能连续两晚守同一人），被守的人当晚免疫狼刀。",
    ROLE_IDIOT: "🤪 你是**白痴**。被票出局时翻牌但不死，之后不能再投票。",
}


class WerewolfGame:
    def __init__(self, channel_id: int, bet: int):
        self.channel_id = channel_id
        self.bet = bet
        self.players: list[int] = []
        self.roles: dict[int, str] = {}
        self.dead: list[int] = []
        self.phase: str = "joining"
        self.day: int = 0

        # 赌命模式
        self.player_life: dict[int, bool] = {}

        self.guard_last_target: int | None = None
        self.witch_antidote_used = False
        self.witch_poison_used = False
        self.hunter_used = False
        self.idiot_revealed = False

        self.night: dict = {}
        self.speak_order: list[int] = []
        self.speak_index: int = 0
        self.speeches: list[tuple[int, str]] = []
        self.votes: dict[int, int] = {}
        self.pending_hunter: int | None = None
        self.last_deaths: list[int] = []
        self.death_causes: dict[int, str] = {}
        self.settled = False
        self.winner: str | None = None

        # 警长
        self.sheriff_id: int | None = None
        self.sheriff_done: bool = False            # 本局是否已经进行过警长竞选
        self.campaign: dict = {}             # 竞选过程数据
        self.pending_sheriff_transfer: int | None = None  # 待移交警徽的（已死）警长

        # 统计累加器（开局创建，settle 时落库）
        self.stats: dict | None = None
        self.guild_id: int | None = None

    # --- 便捷查询 ---

    def alive(self) -> list[int]:
        return [p for p in self.players if p not in self.dead]

    def holders(self, role: str, alive_only: bool = True) -> list[int]:
        pool = self.alive() if alive_only else self.players
        return [p for p in pool if self.roles.get(p) == role]

    def is_wolf(self, uid: int) -> bool:
        return self.roles.get(uid) == ROLE_WOLF

    def can_vote(self, uid: int) -> bool:
        if uid in self.dead or uid not in self.players:
            return False
        if self.roles.get(uid) == ROLE_IDIOT and self.idiot_revealed and not WEREWOLF_IDIOT_KEEPS_VOTE:
            return False
        return True

    def voters(self) -> list[int]:
        return [p for p in self.alive() if self.can_vote(p)]

    def vote_weight(self, uid: int) -> float:
        return WEREWOLF_SHERIFF_VOTE_WEIGHT if uid == self.sheriff_id else 1.0

    def role_summary(self) -> str:
        """本局角色构成的数量统计（不点名具体是谁）。"""
        counts: dict[str, int] = {}
        for r in self.roles.values():
            counts[r] = counts.get(r, 0) + 1
        order = [ROLE_WOLF, ROLE_SEER, ROLE_WITCH, ROLE_HUNTER, ROLE_GUARD, ROLE_IDIOT, ROLE_VILLAGER]
        parts = [f"{r}×{counts[r]}" for r in order if counts.get(r)]
        return " / ".join(parts)


def assign_roles(count: int) -> list[str]:
    """按人数生成角色列表（未打乱）。平民补齐剩余名额。"""
    table = WEREWOLF_ROLE_TABLE.get(count)
    if table is None:
        raise ValueError(f"没有 {count} 人的板子配置")
    roles: list[str] = []
    for role, n in table.items():
        roles.extend([role] * n)
    if len(roles) > count:
        raise ValueError(f"{count} 人板子配置的角色数({len(roles)})超过人数，请检查 WEREWOLF_ROLE_TABLE")
    roles.extend([ROLE_VILLAGER] * (count - len(roles)))
    return roles


class WerewolfService:
    def __init__(self):
        self._active_games: GameRegistry[WerewolfGame] = GameRegistry()

    # --- 生命周期 ---

    def validate_bet(self, bet: int) -> tuple[bool, str]:
        return betting.validate_bet(bet)

    def has_game(self, channel_id: int) -> bool:
        return channel_id in self._active_games

    def get_game(self, channel_id: int) -> WerewolfGame | None:
        return self._active_games.get(channel_id)

    def create_game(self, channel_id: int, bet: int) -> WerewolfGame:
        game = WerewolfGame(channel_id, bet)
        self._active_games.set(channel_id, game)
        return game

    def cancel_game(self, channel_id: int) -> bool:
        return self._active_games.pop(channel_id) is not None

    def add_player(self, channel_id: int, user_id: int, is_life: bool = False) -> tuple[bool, str]:
        game = self._active_games.get(channel_id)
        if not game:
            return False, "没有进行中的狼人杀"
        if game.phase != "joining":
            return False, "游戏已开始，不能加入"
        if user_id in game.players:
            return False, "你已经加入了"
        if len(game.players) >= WEREWOLF_MAX_PLAYERS:
            return False, f"人满了（最多 {WEREWOLF_MAX_PLAYERS} 人）"
        game.players.append(user_id)
        game.player_life[user_id] = is_life
        return True, f"加入成功（当前 {len(game.players)} 人）"

    def leave_player(self, channel_id: int, user_id: int) -> tuple[bool, str]:
        game = self._active_games.get(channel_id)
        if not game or game.phase != "joining":
            return False, "现在不能退出"
        if user_id not in game.players:
            return False, "你没在这局里"
        game.players.remove(user_id)
        return True, f"已退出（当前 {len(game.players)} 人）"

    def start_game(self, channel_id: int) -> tuple[bool, str, WerewolfGame | None]:
        game = self._active_games.get(channel_id)
        if not game:
            return False, "没有狼人杀对局", None
        if game.phase != "joining":
            return False, "游戏已经开始了", None
        if len(game.players) < WEREWOLF_MIN_PLAYERS:
            return False, f"至少要 {WEREWOLF_MIN_PLAYERS} 人才能开始（当前 {len(game.players)} 人）", None

        roles = assign_roles(len(game.players))
        random.shuffle(roles)
        game.roles = dict(zip(game.players, roles))
        game.day = 1
        # 创建统计累加器
        from src.chat.features.games.services.werewolf_stats_recorder import (
            create_werewolf_stats,
        )
        game.stats = create_werewolf_stats(game.players, game.roles)
        self._begin_night(game)
        return True, "游戏开始", game

    # --- 夜晚 ---

    def _begin_night(self, game: WerewolfGame) -> None:
        game.night = {
            "guard_target": None,
            "wolf_votes": {},
            "wolf_target": None,
            "witch_save": False,
            "witch_poison": None,
            "seer_target": None,
        }
        game.phase = "night_guard"
        self._skip_absent_night_roles(game)

    def _skip_absent_night_roles(self, game: WerewolfGame) -> None:
        """把 phase 推到第一个仍有存活玩家的夜晚角色；全都没有则直接结算夜晚。"""
        names = [p for p, _ in NIGHT_SEQUENCE]
        while game.phase in names:
            role = dict(NIGHT_SEQUENCE)[game.phase]
            if game.holders(role):
                return
            self._advance_night_phase(game, force=True)

    def _advance_night_phase(self, game: WerewolfGame, force: bool = False) -> None:
        names = [p for p, _ in NIGHT_SEQUENCE]
        idx = names.index(game.phase)
        if idx + 1 < len(names):
            game.phase = names[idx + 1]
            if not force:
                self._skip_absent_night_roles(game)
        else:
            game.phase = "night_resolve"

    def current_night_role(self, game: WerewolfGame) -> str | None:
        return dict(NIGHT_SEQUENCE).get(game.phase)

    def guard_protect(self, channel_id: int, uid: int, target: int) -> dict:
        game = self._active_games.get(channel_id)
        if not game or game.phase != "night_guard":
            return {"error": "现在不是守卫行动阶段"}
        if game.roles.get(uid) != ROLE_GUARD or uid in game.dead:
            return {"error": "你不是存活的守卫"}
        if target not in game.alive():
            return {"error": "目标无效"}
        if target == uid and not WEREWOLF_GUARD_CAN_PROTECT_SELF:
            return {"error": "不能自守"}
        if target == game.guard_last_target and not WEREWOLF_GUARD_SAME_TARGET_TWICE:
            return {"error": "不能连续两晚守同一个人"}
        game.night["guard_target"] = target
        game.guard_last_target = target
        self._advance_night_phase(game)
        return {"ok": True, "target": target, "phase": game.phase}

    def skip_night_phase(self, channel_id: int, expect_phase: str) -> dict:
        """超时/主动放弃：跳过当前夜晚阶段。expect_phase 防止误跳到别人的回合。"""
        game = self._active_games.get(channel_id)
        if not game or game.phase != expect_phase:
            return {"error": "阶段已推进"}
        if expect_phase == "night_wolf":
            self._decide_wolf_target(game)
        if expect_phase == "night_guard":
            game.guard_last_target = None
        self._advance_night_phase(game)
        return {"ok": True, "skipped": expect_phase, "phase": game.phase}

    def wolf_vote(self, channel_id: int, uid: int, target: int) -> dict:
        game = self._active_games.get(channel_id)
        if not game or game.phase != "night_wolf":
            return {"error": "现在不是狼人行动阶段"}
        if not game.is_wolf(uid) or uid in game.dead:
            return {"error": "你不是存活的狼人"}
        if uid in game.night["wolf_votes"]:
            return {"error": "你已经投过刀了"}
        if target not in game.alive():
            return {"error": "目标无效"}
        if game.is_wolf(target):
            return {"error": "不能刀自己的队友"}
        game.night["wolf_votes"][uid] = target

        wolves = game.holders(ROLE_WOLF)
        if len(game.night["wolf_votes"]) >= len(wolves):
            target_id = self._decide_wolf_target(game)
            self._advance_night_phase(game)
            return {"ok": True, "all_done": True, "target": target_id, "phase": game.phase}
        return {
            "ok": True, "all_done": False,
            "voted": len(game.night["wolf_votes"]), "needed": len(wolves),
        }

    def _decide_wolf_target(self, game: WerewolfGame) -> int | None:
        votes = game.night.get("wolf_votes") or {}
        if not votes:
            return None
        counts: dict[int, int] = {}
        for t in votes.values():
            counts[t] = counts.get(t, 0) + 1
        top = max(counts.values())
        candidates = [t for t, c in counts.items() if c == top]
        target = random.choice(candidates)
        game.night["wolf_target"] = target
        return target

    def witch_action(self, channel_id: int, uid: int, action: str, target: int | None = None) -> dict:
        """action: save / poison / none"""
        game = self._active_games.get(channel_id)
        if not game or game.phase != "night_witch":
            return {"error": "现在不是女巫行动阶段"}
        if game.roles.get(uid) != ROLE_WITCH or uid in game.dead:
            return {"error": "你不是存活的女巫"}

        if action == "save":
            if game.witch_antidote_used:
                return {"error": "解药已经用过了"}
            if game.night.get("wolf_target") is None:
                return {"error": "今晚没人被刀，没什么可救的"}
            game.night["witch_save"] = True
            game.witch_antidote_used = True
        elif action == "poison":
            if game.witch_poison_used:
                return {"error": "毒药已经用过了"}
            if target not in game.alive():
                return {"error": "目标无效"}
            if target == uid:
                return {"error": "不能毒自己"}
            game.night["witch_poison"] = target
            game.witch_poison_used = True
        elif action != "none":
            return {"error": "只能选择 救人 / 下毒 / 不用"}

        self._advance_night_phase(game)
        return {"ok": True, "action": action, "target": target, "phase": game.phase}

    def seer_check(self, channel_id: int, uid: int, target: int) -> dict:
        game = self._active_games.get(channel_id)
        if not game or game.phase != "night_seer":
            return {"error": "现在不是预言家行动阶段"}
        if game.roles.get(uid) != ROLE_SEER or uid in game.dead:
            return {"error": "你不是存活的预言家"}
        if target not in game.alive():
            return {"error": "目标无效"}
        if target == uid:
            return {"error": "不用查自己"}
        game.night["seer_target"] = target
        camp = "狼人" if game.is_wolf(target) else "好人"
        self._advance_night_phase(game)
        return {"ok": True, "target": target, "camp": camp, "phase": game.phase}

    def resolve_night(self, channel_id: int) -> dict:
        """结算夜晚，返回死者与死因。"""
        game = self._active_games.get(channel_id)
        if not game:
            return {"error": "没有对局"}
        if game.phase not in ("night_resolve",) and not game.phase.startswith("night_"):
            return {"error": "不在夜晚阶段"}

        night = game.night
        deaths: list[int] = []
        causes: dict[int, str] = {}

        wolf_target = night.get("wolf_target")
        if wolf_target is not None:
            guarded = night.get("guard_target") == wolf_target
            saved = bool(night.get("witch_save"))
            if guarded and saved:
                dies = WEREWOLF_SAVE_AND_GUARD_KILLS
            else:
                dies = not (guarded or saved)
            if dies:
                deaths.append(wolf_target)
                causes[wolf_target] = "wolf"

        poison_target = night.get("witch_poison")
        if poison_target is not None and poison_target not in deaths:
            deaths.append(poison_target)
            causes[poison_target] = "poison"

        for pid in deaths:
            if pid not in game.dead:
                game.dead.append(pid)

        game.last_deaths = deaths
        game.death_causes = causes
        game.phase = "day_announce"

        sheriff_died = game.sheriff_id if game.sheriff_id in deaths else None
        if sheriff_died is not None:
            game.pending_sheriff_transfer = sheriff_died
            game.sheriff_id = None

        winner = self.check_winner(game)
        if winner:
            return self._end_game(game, winner, deaths=deaths, causes=causes)

        hunter = next(
            (p for p in deaths if game.roles.get(p) == ROLE_HUNTER
             and causes.get(p) != "poison" and not game.hunter_used),
            None,
        )
        if hunter is not None:
            game.pending_hunter = hunter
            game.phase = "day_hunter"
        else:
            # 没有待开枪的猎人就直接进讨论，状态机自己把阶段推到位，
            # UI 只负责按 phase 播报，不需要额外调 begin_discussion。
            self.begin_discussion(game)

        return {
            "deaths": deaths, "causes": causes, "day": game.day,
            "phase": game.phase, "pending_hunter": game.pending_hunter,
            "sheriff_died": sheriff_died,
            "game_over": False,
        }

    # --- 猎人开枪 ---

    def hunter_shoot(self, channel_id: int, uid: int, target: int | None) -> dict:
        game = self._active_games.get(channel_id)
        if not game or game.phase not in ("day_hunter", "day_vote_hunter"):
            return {"error": "现在不是猎人开枪阶段"}
        if game.pending_hunter != uid:
            return {"error": "不是你开枪"}
        was_vote_phase = game.phase == "day_vote_hunter"
        game.pending_hunter = None
        game.hunter_used = True

        shot = None
        sheriff_died = None
        if target is not None:
            if target not in game.alive():
                game.pending_hunter = uid
                game.hunter_used = False
                return {"error": "目标无效"}
            game.dead.append(target)
            shot = target
            if target == game.sheriff_id:
                sheriff_died = target
                game.pending_sheriff_transfer = target
                game.sheriff_id = None

        winner = self.check_winner(game)
        if winner:
            return self._end_game(game, winner, shot=shot, sheriff_died=sheriff_died)

        if was_vote_phase:
            self._next_day(game)
        else:
            self.begin_discussion(game)
        return {"ok": True, "shot": shot, "sheriff_died": sheriff_died,
                "phase": game.phase, "game_over": False}

    # --- 警长竞选 ---

    def begin_sheriff_election(self, game: WerewolfGame) -> dict:
        game.sheriff_done = True
        game.campaign = {
            "signed": {},           # uid -> True(上警)/False(不上警)
            "candidates": [],       # 上警者顺序
            "withdrawn": set(),     # 退水者
            "idx": 0,               # 竞选发言指针
            "speeches": [],
            "votes": {},            # voter -> target
        }
        game.phase = "sheriff_signup"
        return {"phase": game.phase, "eligible": game.alive()}

    def sheriff_signup(self, channel_id: int, uid: int, run: bool) -> dict:
        game = self._active_games.get(channel_id)
        if not game or game.phase != "sheriff_signup":
            return {"error": "现在不是上警报名阶段"}
        if uid not in game.alive():
            return {"error": "你不在场或已出局"}
        if uid in game.campaign["signed"]:
            return {"error": "你已经选过了"}
        game.campaign["signed"][uid] = bool(run)
        return {"ok": True, "run": bool(run),
                "signed": len(game.campaign["signed"]), "total": len(game.alive())}

    def close_signup(self, channel_id: int) -> dict:
        """报名结束：确定候选人。0 人上警→无警长直接讨论；否则进入竞选发言。"""
        game = self._active_games.get(channel_id)
        if not game or game.phase != "sheriff_signup":
            return {"error": "现在不是上警报名阶段"}
        candidates = [u for u in game.alive() if game.campaign["signed"].get(u)]
        game.campaign["candidates"] = candidates
        if not candidates:
            self.begin_discussion(game)
            return {"no_sheriff": True, "reason": "没有人上警", "phase": game.phase}
        game.campaign["idx"] = 0
        game.phase = "sheriff_campaign"
        return {"candidates": candidates, "speaker": self.campaign_current_speaker(game),
                "phase": game.phase}

    def campaign_current_speaker(self, game: WerewolfGame) -> int | None:
        c = game.campaign
        while c["idx"] < len(c["candidates"]):
            cand = c["candidates"][c["idx"]]
            if cand not in game.dead and cand not in c["withdrawn"]:
                return cand
            c["idx"] += 1
        return None

    def campaign_withdraw(self, channel_id: int, uid: int) -> dict:
        game = self._active_games.get(channel_id)
        if not game or game.phase != "sheriff_campaign":
            return {"error": "现在不是竞选阶段"}
        if uid not in game.campaign["candidates"] or uid in game.campaign["withdrawn"]:
            return {"error": "你不在竞选里"}
        game.campaign["withdrawn"].add(uid)
        # 若正好轮到他发言，指针前移
        if self.campaign_current_speaker(game) == uid:
            game.campaign["idx"] += 1
        return self._after_campaign_step(game, withdrew=uid)

    def campaign_speak(self, channel_id: int, uid: int, text: str) -> dict:
        game = self._active_games.get(channel_id)
        if not game or game.phase != "sheriff_campaign":
            return {"error": "现在不是竞选阶段"}
        if self.campaign_current_speaker(game) != uid:
            return {"error": "还没轮到你发言"}
        text = (text or "").strip()[:900]
        if not text:
            return {"error": "发言不能是空的"}
        game.campaign["speeches"].append((uid, text))
        game.campaign["idx"] += 1
        return self._after_campaign_step(game, spoke=uid, text=text)

    def campaign_skip(self, channel_id: int, uid: int) -> dict:
        game = self._active_games.get(channel_id)
        if not game or game.phase != "sheriff_campaign":
            return {"error": "现在不是竞选阶段"}
        if self.campaign_current_speaker(game) != uid:
            return {"error": "发言人已经变了"}
        game.campaign["idx"] += 1
        return self._after_campaign_step(game, spoke=uid, skipped=True)

    def _after_campaign_step(self, game: WerewolfGame, spoke=None, text=None,
                             skipped=False, withdrew=None) -> dict:
        nxt = self.campaign_current_speaker(game)
        result = {"ok": True, "spoke": spoke, "text": text, "skipped": skipped, "withdrew": withdrew}
        if nxt is None:
            return {**result, "campaign_done": True, **self._begin_sheriff_vote(game)}
        return {**result, "campaign_done": False, "next_speaker": nxt, "phase": game.phase}

    def _effective_candidates(self, game: WerewolfGame) -> list[int]:
        c = game.campaign
        return [u for u in c["candidates"] if u not in game.dead and u not in c["withdrawn"]]

    def _begin_sheriff_vote(self, game: WerewolfGame) -> dict:
        cands = self._effective_candidates(game)
        if not cands:
            self.begin_discussion(game)
            return {"no_sheriff": True, "reason": "候选人都退水了", "phase": game.phase}
        if len(cands) == 1:
            game.sheriff_id = cands[0]
            self.begin_discussion(game)
            return {"sheriff": cands[0], "auto": True, "phase": game.phase}
        game.phase = "sheriff_vote"
        game.campaign["votes"] = {}
        return {"candidates": cands, "phase": game.phase, "vote": True}

    def sheriff_voters(self, game: WerewolfGame) -> list[int]:
        """非候选人的存活玩家投票选警长。"""
        return [u for u in game.alive() if u not in game.campaign["candidates"]]

    def sheriff_vote(self, channel_id: int, voter: int, target: int) -> dict:
        game = self._active_games.get(channel_id)
        if not game or game.phase != "sheriff_vote":
            return {"error": "现在不是警长投票阶段"}
        if voter not in self.sheriff_voters(game):
            return {"error": "候选人和出局者不能投票"}
        if voter in game.campaign["votes"]:
            return {"error": "你已经投过票了"}
        if target not in self._effective_candidates(game):
            return {"error": "目标不是有效候选人"}
        game.campaign["votes"][voter] = target
        needed = len(self.sheriff_voters(game))
        if len(game.campaign["votes"]) >= needed:
            return self._sheriff_tally(game)
        return {"ok": True, "votes_count": len(game.campaign["votes"]), "needed": needed}

    def force_sheriff_tally(self, channel_id: int) -> dict:
        game = self._active_games.get(channel_id)
        if not game or game.phase != "sheriff_vote":
            return {"error": "现在不是警长投票阶段"}
        return self._sheriff_tally(game, forced=True)

    def _sheriff_tally(self, game: WerewolfGame, forced: bool = False) -> dict:
        counts: dict[int, int] = {}
        for t in game.campaign["votes"].values():
            counts[t] = counts.get(t, 0) + 1
        elected = None
        tie = False
        if counts:
            top = max(counts.values())
            winners = [t for t, c in counts.items() if c == top]
            if len(winners) == 1:
                elected = winners[0]
            else:
                tie = True
        if elected is not None:
            game.sheriff_id = elected
        self.begin_discussion(game)
        return {"sheriff_done": True, "sheriff": elected, "tie": tie,
                "vote_count": counts, "forced": forced, "phase": game.phase}

    # --- 警徽移交 ---

    def sheriff_transfer(self, channel_id: int, from_uid: int, to_uid: int | None) -> dict:
        game = self._active_games.get(channel_id)
        if not game:
            return {"error": "没有对局"}
        if game.pending_sheriff_transfer != from_uid:
            return {"error": "现在没有你要移交的警徽"}
        game.pending_sheriff_transfer = None
        if to_uid is None or to_uid not in game.alive():
            game.sheriff_id = None
            return {"ok": True, "transferred_to": None, "destroyed": True}
        game.sheriff_id = to_uid
        return {"ok": True, "transferred_to": to_uid, "destroyed": False}

    # --- 白天：轮流发言 ---

    def begin_discussion(self, game: WerewolfGame) -> dict:
        alive = game.alive()
        # 每天从不同的人起跳，避免永远同一个人先说
        offset = (game.day - 1) % max(1, len(alive))
        game.speak_order = alive[offset:] + alive[:offset]
        game.speak_index = 0
        game.speeches = []
        game.phase = "day_discuss"
        return {"phase": game.phase, "speaker": self.current_speaker(game), "order": list(game.speak_order)}

    def current_speaker(self, game: WerewolfGame) -> int | None:
        while game.speak_index < len(game.speak_order):
            candidate = game.speak_order[game.speak_index]
            if candidate not in game.dead:
                return candidate
            game.speak_index += 1
        return None

    def submit_speech(self, channel_id: int, uid: int, text: str) -> dict:
        game = self._active_games.get(channel_id)
        if not game or game.phase != "day_discuss":
            return {"error": "现在不是发言阶段"}
        speaker = self.current_speaker(game)
        if speaker != uid:
            return {"error": "还没轮到你发言"}
        text = (text or "").strip()[:900]
        if not text:
            return {"error": "发言不能是空的"}
        game.speeches.append((uid, text))
        game.speak_index += 1
        return self._after_speech(game, uid, text)

    def skip_speech(self, channel_id: int, uid: int) -> dict:
        """超时自动跳过当前发言人。"""
        game = self._active_games.get(channel_id)
        if not game or game.phase != "day_discuss":
            return {"error": "现在不是发言阶段"}
        if self.current_speaker(game) != uid:
            return {"error": "发言人已经变了"}
        game.speeches.append((uid, "（超时未发言）"))
        game.speak_index += 1
        return self._after_speech(game, uid, None, skipped=True)

    def _after_speech(self, game: WerewolfGame, uid: int, text: str | None, skipped: bool = False) -> dict:
        nxt = self.current_speaker(game)
        result = {
            "ok": True, "speaker": uid, "text": text, "skipped": skipped,
            "index": len(game.speeches), "total": len(game.speak_order),
        }
        if nxt is None:
            self.enter_voting(game)
            return {**result, "all_done": True, "phase": game.phase}
        return {**result, "all_done": False, "next_speaker": nxt, "phase": game.phase}

    # --- 白天：投票 ---

    def enter_voting(self, game: WerewolfGame) -> None:
        game.phase = "day_vote"
        game.votes = {}

    def submit_vote(self, channel_id: int, voter: int, target: int) -> dict:
        game = self._active_games.get(channel_id)
        if not game or game.phase != "day_vote":
            return {"error": "现在不是投票阶段"}
        if voter not in game.players or voter in game.dead:
            return {"error": "你不在这场游戏里"}
        if not game.can_vote(voter):
            return {"error": "你已经失去投票权了"}
        if voter in game.votes:
            return {"error": "你已经投过票了"}
        if target not in game.alive():
            return {"error": "目标无效"}
        if target == voter:
            return {"error": "不能投自己"}
        game.votes[voter] = target

        needed = len(game.voters())
        if len(game.votes) >= needed:
            return self._tally(game)
        return {"ok": True, "votes_count": len(game.votes), "needed": needed}

    def force_tally(self, channel_id: int) -> dict:
        game = self._active_games.get(channel_id)
        if not game or game.phase != "day_vote":
            return {"error": "现在不是投票阶段"}
        return self._tally(game, forced=True)

    def _tally(self, game: WerewolfGame, forced: bool = False) -> dict:
        counts: dict[int, float] = {}
        for voter, t in game.votes.items():
            counts[t] = counts.get(t, 0) + game.vote_weight(voter)
        game.votes = {}

        eliminated = None
        tie = False
        if counts:
            top = max(counts.values())
            candidates = [t for t, c in counts.items() if c == top]
            if len(candidates) == 1:
                eliminated = candidates[0]
            elif WEREWOLF_TIE_ELIMINATES_NOBODY:
                tie = True
            else:
                eliminated = random.choice(candidates)

        base = {
            "vote_done": True, "vote_count": counts, "forced": forced,
            "tie": tie, "day": game.day,
        }

        if eliminated is None:
            self._next_day(game)
            return {**base, "eliminated": None, "game_over": False, "phase": game.phase}

        # 白痴翻牌：不死，失去投票权
        if game.roles.get(eliminated) == ROLE_IDIOT and not game.idiot_revealed:
            game.idiot_revealed = True
            self._next_day(game)
            return {
                **base, "eliminated": None, "idiot_revealed": eliminated,
                "game_over": False, "phase": game.phase,
            }

        game.dead.append(eliminated)
        if eliminated == game.sheriff_id:
            base["sheriff_died"] = eliminated
            game.pending_sheriff_transfer = eliminated
            game.sheriff_id = None
        winner = self.check_winner(game)
        if winner:
            return {**self._end_game(game, winner), **base, "eliminated": eliminated}

        if game.roles.get(eliminated) == ROLE_HUNTER and not game.hunter_used:
            game.pending_hunter = eliminated
            game.phase = "day_vote_hunter"
            return {**base, "eliminated": eliminated, "pending_hunter": eliminated,
                    "game_over": False, "phase": game.phase}

        self._next_day(game)
        return {**base, "eliminated": eliminated, "game_over": False, "phase": game.phase}

    def _next_day(self, game: WerewolfGame) -> None:
        game.day += 1
        self._begin_night(game)

    # --- 胜负与结算 ---

    def check_winner(self, game: WerewolfGame) -> str | None:
        wolves = game.holders(ROLE_WOLF)
        goods = [p for p in game.alive() if not game.is_wolf(p)]
        if not wolves:
            return "good"
        if len(wolves) >= len(goods):
            return "wolf"
        return None

    def _end_game(self, game: WerewolfGame, winner: str, **extra) -> dict:
        game.phase = "ended"
        game.winner = winner
        game.pending_hunter = None
        return {
            "game_over": True, "winner": winner, "phase": "ended",
            "wolf_ids": game.holders(ROLE_WOLF, alive_only=False),
            "roles": dict(game.roles),
            **extra,
        }

    async def settle(self, game: WerewolfGame, winner: str) -> dict:
        """结算：赌币败方扣局费，赌命败方不扣币。
        赌币胜方分败方局费池，赌命胜方拿系统奖励（每日限次）。幂等。"""
        if game.settled:
            return {"error": "这局已经结算过了"}
        game.settled = True
        self._active_games.pop(game.channel_id)

        # 落库统计
        self._flush_stats(game, winner)

        wolves = game.holders(ROLE_WOLF, alive_only=False)
        goods = [p for p in game.players if p not in wolves]
        winners, losers = (goods, wolves) if winner == "good" else (wolves, goods)

        # 赌币败方扣局费，赌命败方不扣
        collected = 0
        failed: list[int] = []
        coin_losers = [p for p in losers if not game.player_life.get(p, False)]
        for pid in coin_losers:
            if await betting.deduct(pid, game.bet, "狼人杀败方局费"):
                collected += game.bet
            else:
                failed.append(pid)

        # 赌币胜方分池
        coin_winners = [p for p in winners if not game.player_life.get(p, False)]
        share = collected // len(coin_winners) if coin_winners and collected > 0 else 0
        if share > 0:
            for pid in coin_winners:
                await betting.credit(pid, share, "狼人杀胜方分池")

        # 赌命胜方拿系统奖励（每日限次）
        life_winners = [p for p in winners if game.player_life.get(p, False)]
        life_rewards: dict[int, int] = {}
        for pid in life_winners:
            reward = await betting.grant_life_reward(pid, "狼人杀赌命勇敢者奖励")
            life_rewards[pid] = reward

        return {
            "winner": winner, "winners": winners, "losers": losers,
            "pool": collected, "share": share, "deduct_failed": failed,
            "life_rewards": life_rewards,
        }

    def _flush_stats(self, game: WerewolfGame, winner: str) -> None:
        """把内存统计落库。失败只记日志，不影响结算。"""
        if game.stats is None or not game.guild_id:
            return
        try:
            from src.chat.features.games.services.werewolf_stats_recorder import (
                finalize_werewolf_stats,
            )
            from src.chat.features.games.services.pressure_stats_db import get_stats_db
            wolves = game.holders(ROLE_WOLF, alive_only=False)
            goods = [p for p in game.players if p not in wolves]
            rows = finalize_werewolf_stats(
                game.stats, winner=winner, wolves=wolves, goods=goods)
            get_stats_db().record_werewolf_game(game.guild_id, rows)
        except Exception:
            import logging
            logging.getLogger(__name__).exception("狼人杀统计落库失败")
        finally:
            game.stats = None


werewolf_service = WerewolfService()
