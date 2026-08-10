# -*- coding: utf-8 -*-
"""加压俄罗斯轮盘 —— 服务层（纯逻辑，不依赖 discord.py）。

状态机（两层）：
    外层 state: joining → playing → ended
    内层 phase: fire → choice → fire → ... → ended

核心规则：
- 6 弹巢，开局 1 发实弹，位置随机。
- 第一枪特殊判定 15%，不走弹巢 1/6。
- fire 阶段：扣扳机 / 抽弹 / 退出
- choice 阶段：传枪 / 再开一枪 / 加压 / 反手还击
- 中弹 = 禁言（当前赌注分钟）+ 出局 + 扣局费。
- 子弹打光 = 游戏立刻结束；只剩 1 人 → 冠军；多人 → 平局。

弹巢结构（对齐原项目）：
- chambers: list[bool]  6 格，True=有子弹
- revealed: list[bool]  6 格，True=已验过
- hit_chambers: list[bool]  6 格，True=那一枪中弹了
- pointer: int  枪口位置（0-5）

赌注：BASE(3) + pressure_bullets(累计塞入子弹数) * 1 分钟

所有方法返回 dict：成功带数据，失败带 "error"。
"""

from __future__ import annotations

import random

from src.chat.features.games.config.games_config import (
    PRESSURE_ROULETTE_MIN_PLAYERS, PRESSURE_ROULETTE_MAX_PLAYERS,
    PRESSURE_ROULETTE_DEFAULT_BET, PRESSURE_ROULETTE_CHAMBER_SIZE,
    PRESSURE_ROULETTE_INITIAL_LIVE, PRESSURE_ROULETTE_BASE_STAKE,
    PRESSURE_ROULETTE_PRESS_STAKE,
)
from src.chat.features.games.services import betting
from src.chat.features.games.services.registry import GameRegistry
from src.chat.features.games.services.pressure_stats_recorder import (
    create_pressure_stats, record_shot, record_choice, record_elimination,
    record_quit, record_unload, record_riposte, record_riposte_kill,
    finalize_pressure_stats,
)

# 第一枪特殊判定概率（不走弹巢 1/6）
FIRST_SHOT_HIT_CHANCE = 0.15


class PressureRouletteGame:
    """单局状态。"""

    def __init__(self, channel_id: int, bet: int):
        self.channel_id = channel_id
        self.bet = bet
        self.players: list[int] = []
        self.dead: list[int] = []
        self.state: str = "joining"      # 外层：joining/playing/ended
        self.phase: str = "idle"         # 内层：idle/fire/choice/resolving/ended
        self.shot_number: int = 0        # 第几枪（0=第一枪）

        # 弹巢四字段（对齐原项目）
        self.chambers: list[bool] = [False] * PRESSURE_ROULETTE_CHAMBER_SIZE
        self.revealed: list[bool] = [False] * PRESSURE_ROULETTE_CHAMBER_SIZE
        self.hit_chambers: list[bool] = [False] * PRESSURE_ROULETTE_CHAMBER_SIZE
        self.pointer: int = 0
        self.bullets: int = 0            # 当前子弹数

        # 当前轮到 alive 中谁（索引）
        self.turn_index: int = 0
        self.turn_token: int = 0          # 轮次令牌（防过期点击）

        # 连开蓄力（对齐原项目：连着持有人记）
        self.charge: int = 0
        self.charge_owner_id: int | None = None

        # 加压统计
        self.pressure: int = 0            # 加压次数
        self.pressure_bullets: int = 0    # 累计塞入子弹数（用于赌注）

        # 赌注（分钟）：BASE + pressure_bullets * 1
        self.stake_minutes: int = PRESSURE_ROULETTE_BASE_STAKE

        # 抽弹开枪
        self.unload_used: list[int] = []   # 用过抽弹的人
        self.unload_shot_owner: int | None = None  # 抽弹枪一次性标记

        # 反手还击
        self.riposte_holder_id: int | None = None   # 反手权持有者
        self.riposte_target_id: int | None = None   # 反手目标（加压者）
        self.riposte: dict | None = None             # 进行中的反手序列

        # 胆小鬼退出
        self.cowards: list[dict] = []      # [{user_id, stake_minutes, penalty_minutes}]
        self.redeemers: list[int] = []     # 戴罪上桌名单（开局快照）

        # 出局记录
        self.eliminated: list[dict] = []   # [{user_id, minutes, virtual}]

        self.settled: bool = False
        self.winner: int | None = None     # None = 平局或未结束

        # 统计累加器（开局创建，settle 时落库）
        self.stats: dict | None = None
        self.guild_id: int | None = None   # 用于落库（cog 里设）

    # ---- 便捷查询 ----
    def alive(self) -> list[int]:
        return [p for p in self.players if p not in self.dead]

    def current_player(self) -> int | None:
        alive = self.alive()
        if not alive:
            return None
        idx = self.turn_index % len(alive)
        return alive[idx]

    def unknown_count(self) -> int:
        """未验过的格子数。"""
        return sum(1 for r in self.revealed if not r)

    def remaining_live(self) -> int:
        """弹巢里还没打掉的实弹数 = bullets（因为中弹会扣 bullets）。"""
        return self.bullets

    def remaining_chamber(self) -> int:
        """未验过的格数。"""
        return self.unknown_count()

    def hit_chance(self) -> float:
        """中弹概率 = bullets / unknown_count。"""
        unknown = self.unknown_count()
        return self.bullets / unknown if unknown > 0 else 0.0

    def charge_for(self, user_id: int) -> int:
        """某人的蓄力层数。"""
        if self.charge_owner_id != user_id:
            return 0
        return self.charge

    def set_charge(self, user_id: int | None, value: int) -> None:
        """设置蓄力。value=0 时清空 owner。"""
        self.charge = max(0, value)
        self.charge_owner_id = user_id if self.charge > 0 else None

    def load_bullets_for(self, user_id: int) -> int:
        """这次加压实际能塞几发：min(1 + charge, CHAMBER - bullets)。"""
        return min(1 + self.charge_for(user_id),
                   PRESSURE_ROULETTE_CHAMBER_SIZE - self.bullets)

    def current_stake(self) -> int:
        """当前赌注分钟 = BASE + pressure_bullets。"""
        return PRESSURE_ROULETTE_BASE_STAKE + self.pressure_bullets * PRESSURE_ROULETTE_PRESS_STAKE

    def chamber_display(self) -> list[str]:
        """弹巢可视化：next/hit/spent/unknown → 枪口/砰/空/?。"""
        out: list[str] = []
        for i in range(len(self.chambers)):
            if self.state == "playing" and i == self.pointer and not self.revealed[i]:
                out.append("枪口")
            elif self.revealed[i]:
                out.append("砰" if self.hit_chambers[i] else "空")
            else:
                out.append("?")
        return out

    def is_redeemer(self, user_id: int) -> bool:
        """是否戴罪上桌。"""
        return user_id in self.redeemers


def _spin_cylinder(game: PressureRouletteGame) -> None:
    """重洗弹巢：把 bullets 发子弹随机放到 6 格。"""
    positions = list(range(PRESSURE_ROULETTE_CHAMBER_SIZE))
    random.shuffle(positions)
    game.chambers = [False] * PRESSURE_ROULETTE_CHAMBER_SIZE
    for i in range(min(game.bullets, PRESSURE_ROULETTE_CHAMBER_SIZE)):
        game.chambers[positions[i]] = True
    game.revealed = [False] * PRESSURE_ROULETTE_CHAMBER_SIZE
    game.hit_chambers = [False] * PRESSURE_ROULETTE_CHAMBER_SIZE
    game.pointer = 0


def _place_first_shot_bullet(game: PressureRouletteGame, should_hit: bool) -> None:
    """把子弹摆成「枪口那一格是否有子弹」的指定结果（第一枪特殊判定）。"""
    pointer = game.pointer
    if game.chambers[pointer] == should_hit:
        return
    candidates = [i for i in range(PRESSURE_ROULETTE_CHAMBER_SIZE)
                  if i != pointer and game.chambers[i] == should_hit]
    if not candidates:
        return
    target = random.choice(candidates)
    game.chambers[pointer] = should_hit
    game.chambers[target] = not should_hit


def _release_riposte(game: PressureRouletteGame, user_id: int) -> None:
    """出局/退出时收尾反手权。"""
    if game.riposte:
        if game.riposte.get("initiator_id") == user_id:
            game.riposte = None
        elif game.riposte.get("target_id") == user_id and game.riposte.get("stage") == "target":
            game.riposte = None

    if game.riposte_holder_id == user_id:
        alive = game.alive()
        if alive:
            game.riposte_holder_id = alive[game.turn_index % len(alive)]
        else:
            game.riposte_holder_id = None
        if not game.riposte_holder_id:
            game.riposte_target_id = None
        if game.riposte_holder_id == game.riposte_target_id:
            game.riposte_holder_id = None
            game.riposte_target_id = None

    if game.riposte_target_id == user_id:
        game.riposte_holder_id = None
        game.riposte_target_id = None


class PressureRouletteService:
    def __init__(self):
        self._active_games: GameRegistry[PressureRouletteGame] = GameRegistry()

    # ==================== 生命周期 ====================

    def validate_bet(self, bet: int) -> tuple[bool, str]:
        return betting.validate_bet(bet)

    def has_game(self, channel_id: int) -> bool:
        return self.get_game(channel_id) is not None

    def get_game(self, channel_id: int) -> PressureRouletteGame | None:
        return self._active_games.get(channel_id)

    def create_game(self, channel_id: int, bet: int) -> PressureRouletteGame:
        game = PressureRouletteGame(channel_id, bet)
        self._active_games.set(channel_id, game)
        return game

    def cancel_game(self, channel_id: int) -> bool:
        return self._active_games.pop(channel_id) is not None

    def add_player(self, channel_id: int, user_id: int) -> tuple[bool, str]:
        game = self.get_game(channel_id)
        if game is None:
            return False, "这局已经没了。"
        if game.state != "joining":
            return False, "已经开局了，等下一局吧。"
        if user_id in game.players:
            return False, "你已经上桌了。"
        if len(game.players) >= PRESSURE_ROULETTE_MAX_PLAYERS:
            return False, f"桌满了（最多 {PRESSURE_ROULETTE_MAX_PLAYERS} 人）。"
        game.players.append(user_id)
        return True, "上桌了"

    def leave_player(self, channel_id: int, user_id: int) -> tuple[bool, str]:
        game = self.get_game(channel_id)
        if game is None:
            return False, "这局已经没了。"
        if game.state != "joining":
            return False, "已经开局了，退不了。"
        if user_id not in game.players:
            return False, "你根本没上桌。"
        game.players.remove(user_id)
        return True, "退了"

    def start_game(self, channel_id: int) -> tuple[bool, str, PressureRouletteGame | None]:
        game = self.get_game(channel_id)
        if game is None:
            return False, "这局已经没了。", None
        if game.state != "joining":
            return False, "已经开局了。", None
        if len(game.players) < PRESSURE_ROULETTE_MIN_PLAYERS:
            return False, f"人不够，至少 {PRESSURE_ROULETTE_MIN_PLAYERS} 人。", None
        # 装弹
        game.bullets = PRESSURE_ROULETTE_INITIAL_LIVE
        _spin_cylinder(game)
        # 第一枪特殊判定
        _place_first_shot_bullet(game, random.random() < FIRST_SHOT_HIT_CHANCE)
        game.state = "playing"
        game.phase = "fire"
        game.turn_index = 0
        game.shot_number = 0
        game.pressure = 0
        game.pressure_bullets = 0
        game.set_charge(None, 0)
        game.turn_token += 1
        # 创建统计累加器
        game.stats = create_pressure_stats(game.players)
        return True, "开局", game

    # ==================== fire 阶段：扣扳机 / 抽弹 / 退出 ====================

    def shoot_self(self, channel_id: int, user_id: int) -> dict:
        """对自己扣扳机。"""
        return self._perform_shot(channel_id, user_id)

    def _perform_shot(self, channel_id: int, user_id: int) -> dict:
        game = self.get_game(channel_id)
        if game is None:
            return {"error": "这局已经没了。"}
        if game.state != "playing" or game.phase != "fire":
            return {"error": "现在不是开枪阶段。"}
        cur = game.current_player()
        if cur != user_id:
            return {"error": "还没轮到你。"}

        shooter_id = user_id
        index = game.pointer
        hit = game.chambers[index] is True
        bullets_before = game.bullets
        unknown_before = game.unknown_count()
        first_shot = game.shot_number == 0

        game.revealed[index] = True
        if hit:
            game.chambers[index] = False
            game.hit_chambers[index] = True
            game.bullets = max(0, game.bullets - 1)
            game.set_charge(shooter_id, 0)
        game.pointer = (index + 1) % PRESSURE_ROULETTE_CHAMBER_SIZE
        game.shot_number += 1
        game.turn_token += 1

        # 统计：记一次扣扳机
        if game.stats is not None:
            hit_chance = FIRST_SHOT_HIT_CHANCE if first_shot else None
            record_shot(game.stats, shooter_id,
                        hit=hit, bullets_before=bullets_before,
                        unknown_before=unknown_before, hit_chance=hit_chance)

        # 抽弹枪一次性标记消费
        unload_shot = game.unload_shot_owner == shooter_id
        if unload_shot:
            game.unload_shot_owner = None

        # 反手序列推进
        riposte_stage = game.riposte.get("stage") if game.riposte else None
        if riposte_stage == "target":
            initiator_id = game.riposte["initiator_id"]
            game.riposte["stage"] = "return"
            alive = game.alive()
            if initiator_id in alive and shooter_id in alive:
                initiator_idx = alive.index(initiator_id)
                target_idx = alive.index(shooter_id)
                if hit:
                    game.turn_index = initiator_idx - 1 if initiator_idx > target_idx else initiator_idx
                    # 统计：反手击杀
                    if game.stats is not None:
                        record_riposte_kill(game.stats, initiator_id)
                else:
                    game.turn_index = initiator_idx
            else:
                game.riposte = None
        elif riposte_stage == "return":
            game.riposte = None
        else:
            pass

        if hit:
            game.phase = "resolving"
            return self._resolve_hit(game, shooter_id, unload_shot, riposte_stage)
        else:
            # 空枪
            if riposte_stage == "target":
                # 加压者空枪 → 发起人补枪（return 阶段）
                game.phase = "fire"
                return {
                    "action": "shoot", "by": shooter_id, "hit": False,
                    "shot_count": game.shot_number,
                    "chamber": game.chamber_display(),
                    "bullets": game.bullets, "unknown_count": game.unknown_count(),
                    "stake_minutes": game.current_stake(),
                    "next": game.current_player(),
                    "riposte_stage": "target",
                    "game_over": False,
                }
            if unload_shot:
                # 抽弹活下来 → 强制传枪
                return self._handle_choice_internal(game, "pass")
            game.phase = "choice"
            game.turn_token += 1
            return {
                "action": "shoot", "by": shooter_id, "hit": False,
                "shot_count": game.shot_number,
                "chamber": game.chamber_display(),
                "bullets": game.bullets, "unknown_count": game.unknown_count(),
                "stake_minutes": game.current_stake(),
                "next": game.current_player(),
                "streak": game.charge_for(shooter_id),
                "game_over": False,
            }

    def _resolve_hit(self, game: PressureRouletteGame, victim_id: int,
                     unload_shot: bool, riposte_stage: str | None) -> dict:
        """中弹结算。"""
        stake_minutes = game.current_stake()
        game.dead.append(victim_id)
        game.eliminated.append({"user_id": victim_id, "minutes": stake_minutes})
        _release_riposte(game, victim_id)
        # 统计：中弹淘汰
        if game.stats is not None:
            record_elimination(game.stats, victim_id, stake_minutes)

        # 子弹打光检查
        if game.bullets <= 0:
            return self._end_game(game, reason="bullets_empty",
                                  last_victim=victim_id, hit=True)
        # 只剩 1 人
        alive = game.alive()
        if len(alive) <= 1:
            return self._end_game(game, reason="last_man",
                                  last_victim=victim_id, hit=True)

        # 轮到下家
        alive = game.alive()
        if game.turn_index >= len(alive):
            game.turn_index = 0
        game.phase = "fire"
        game.turn_token += 1

        return {
            "action": "shoot", "by": victim_id, "victim": victim_id, "hit": True,
            "shot_count": game.shot_number,
            "chamber": game.chamber_display(),
            "bullets": game.bullets, "unknown_count": game.unknown_count(),
            "stake_minutes": stake_minutes,
            "next": game.current_player(),
            "mute_minutes": stake_minutes,
            "game_over": False,
        }

    # ==================== choice 阶段：传枪 / 再开 / 加压 / 反手 ====================

    def handle_choice(self, channel_id: int, user_id: int, action: str) -> dict:
        """choice 阶段操作。action: pass/again/load/riposte。"""
        game = self.get_game(channel_id)
        if game is None:
            return {"error": "这局已经没了。"}
        if game.state != "playing" or game.phase != "choice":
            return {"error": "现在不是选择阶段。"}
        cur = game.current_player()
        if cur != user_id:
            return {"error": "还没轮到你。"}
        return self._handle_choice_internal(game, action)

    def _handle_choice_internal(self, game: PressureRouletteGame, action: str) -> dict:
        """内部 choice 处理（不校验 phase，用于强制传枪等）。"""
        actor_id = game.current_player()
        if actor_id is None:
            return {"error": "没有当前玩家。"}

        effective_action = action
        # 满巢时 load 降级为 pass
        if effective_action == "load" and game.bullets >= PRESSURE_ROULETTE_CHAMBER_SIZE:
            effective_action = "pass"

        charge = game.charge_for(actor_id)
        loaded_bullets = 0
        cleared_charge = 0

        if effective_action == "load":
            loaded_bullets = game.load_bullets_for(actor_id)
            game.bullets += loaded_bullets
            game.pressure += 1
            game.pressure_bullets += loaded_bullets
            _spin_cylinder(game)

        if effective_action == "again":
            game.set_charge(actor_id, charge + 1)
        else:
            cleared_charge = charge
            game.set_charge(actor_id, 0)

        # turn_index 推进（again 不推进）
        if effective_action != "again":
            alive = game.alive()
            game.turn_index = (game.turn_index + 1) % len(alive)

        # 反手权联动
        if effective_action == "load":
            game.riposte_target_id = actor_id
            alive = game.alive()
            game.riposte_holder_id = alive[game.turn_index % len(alive)]
        elif effective_action == "pass" and game.riposte_holder_id == actor_id:
            game.riposte_holder_id = None
            game.riposte_target_id = None

        game.phase = "fire"
        game.turn_token += 1

        return {
            "action": effective_action, "by": actor_id,
            "loaded": loaded_bullets, "cleared_charge": cleared_charge,
            "stake_minutes": game.current_stake(),
            "chamber": game.chamber_display(),
            "bullets": game.bullets, "unknown_count": game.unknown_count(),
            "next": game.current_player(),
            "game_over": False,
        }

    # ==================== fire 阶段：抽弹开枪 ====================

    def unload(self, channel_id: int, user_id: int) -> dict:
        """🔧 抽弹开枪：卸 1 发 → 重洗 → 立刻扣扳机。"""
        game = self.get_game(channel_id)
        if game is None:
            return {"error": "这局已经没了。"}
        if game.state != "playing" or game.phase != "fire":
            return {"error": "现在不是开枪阶段。"}
        cur = game.current_player()
        if cur != user_id:
            return {"error": "还没轮到你。"}
        if game.riposte:
            return {"error": "反手序列中不能抽弹。"}
        if game.bullets < 3:
            return {"error": "枪里至少 3 发才能抽弹。"}
        if user_id in game.unload_used:
            return {"error": "你这局已经抽过弹了。"}

        game.bullets = max(0, game.bullets - 1)
        _spin_cylinder(game)
        game.unload_used.append(user_id)
        game.set_charge(user_id, 0)
        game.unload_shot_owner = user_id
        game.turn_token += 1
        # 统计：抽弹开枪
        if game.stats is not None:
            record_unload(game.stats, user_id)

        # 立刻扣扳机
        return self._perform_shot(channel_id, user_id)

    # ==================== choice 阶段：反手还击 ====================

    def riposte(self, channel_id: int, user_id: int) -> dict:
        """🔙 反手还击：把枪扔回给加压者。"""
        game = self.get_game(channel_id)
        if game is None:
            return {"error": "这局已经没了。"}
        if game.state != "playing" or game.phase != "choice":
            return {"error": "现在不是选择阶段。"}
        cur = game.current_player()
        if cur != user_id:
            return {"error": "还没轮到你。"}
        if game.riposte:
            return {"error": "已经有反手序列在进行。"}
        if game.riposte_holder_id != user_id:
            return {"error": "你没有反手权。"}
        if not game.riposte_target_id or game.riposte_target_id not in game.alive():
            return {"error": "反手目标已不在场。"}

        target_id = game.riposte_target_id
        game.riposte = {"initiator_id": user_id, "target_id": target_id, "stage": "target"}
        game.riposte_holder_id = None
        game.riposte_target_id = None
        game.set_charge(user_id, 0)
        alive = game.alive()
        game.turn_index = alive.index(target_id)
        game.phase = "fire"
        game.turn_token += 1
        # 统计：反手还击
        if game.stats is not None:
            record_riposte(game.stats, user_id, target_id)

        return {
            "action": "riposte", "by": user_id, "target": target_id,
            "chamber": game.chamber_display(),
            "bullets": game.bullets, "unknown_count": game.unknown_count(),
            "stake_minutes": game.current_stake(),
            "next": game.current_player(),
            "game_over": False,
        }

    # ==================== fire 阶段：胆小鬼退出 ====================

    def quit(self, channel_id: int, user_id: int) -> dict:
        """🤡 胆小鬼退出。"""
        game = self.get_game(channel_id)
        if game is None:
            return {"error": "这局已经没了。"}
        if game.state != "playing" or game.phase != "fire":
            return {"error": "现在不是开枪阶段。"}
        cur = game.current_player()
        if cur != user_id:
            return {"error": "还没轮到你。"}
        if game.is_redeemer(user_id):
            return {"error": "你是戴罪上桌的，没有第二次。"}
        if game.riposte:
            return {"error": "反手序列中不能逃。"}

        game.dead.append(user_id)
        stake_minutes = game.current_stake()
        penalty_minutes = max(2, stake_minutes)  # 至少 2 分钟
        game.cowards.append({
            "user_id": user_id, "stake_minutes": stake_minutes,
            "penalty_minutes": penalty_minutes,
        })
        _release_riposte(game, user_id)
        game.turn_token += 1
        # 统计：胆小鬼退出
        if game.stats is not None:
            record_quit(game.stats, user_id, penalty_minutes)

        # 检查是否结束
        alive = game.alive()
        if len(alive) <= 1:
            return self._end_game(game, reason="last_man",
                                   last_victim=user_id, hit=False,
                                   coward=True, penalty_minutes=penalty_minutes)

        if game.turn_index >= len(alive):
            game.turn_index = 0
        game.phase = "fire"
        return {
            "action": "quit", "by": user_id,
            "penalty_minutes": penalty_minutes,
            "stake_minutes": stake_minutes,
            "next": game.current_player(),
            "chamber": game.chamber_display(),
            "bullets": game.bullets, "unknown_count": game.unknown_count(),
            "game_over": False,
        }

    # ==================== 超时 ====================

    def timeout_fire(self, channel_id: int) -> dict:
        """fire 阶段超时：自动开枪。"""
        game = self.get_game(channel_id)
        if game is None:
            return {"error": "这局已经没了。"}
        if game.state != "playing" or game.phase != "fire":
            return {"error": "现在不是开枪阶段。"}
        cur = game.current_player()
        if cur is None:
            return {"error": "没有当前玩家。"}
        return self._perform_shot(channel_id, cur)

    def timeout_choice(self, channel_id: int) -> dict:
        """choice 阶段超时：自动传枪。"""
        game = self.get_game(channel_id)
        if game is None:
            return {"error": "这局已经没了。"}
        if game.state != "playing" or game.phase != "choice":
            return {"error": "现在不是选择阶段。"}
        cur = game.current_player()
        if cur is None:
            return {"error": "没有当前玩家。"}
        return self._handle_choice_internal(game, "pass")

    # ==================== 内部 ====================

    def _end_game(self, game: PressureRouletteGame, reason: str,
                  last_victim: int | None = None, hit: bool = False,
                  coward: bool = False, penalty_minutes: int = 0) -> dict:
        """结束游戏。reason: bullets_empty / last_man / aborted。"""
        game.state = "ended"
        game.phase = "ended"
        alive = game.alive()
        if len(alive) == 1:
            game.winner = alive[0]
        else:
            game.winner = None  # 平局

        result = {
            "action": "shoot" if hit else ("quit" if coward else "end"),
            "game_over": True,
            "reason": reason,
            "winner": game.winner,
            "alive": alive,
            "dead": list(game.dead),
            "last_victim": last_victim,
            "last_hit": hit,
            "hit": hit,
            "stake_minutes": game.current_stake(),
            "chamber": game.chamber_display(),
            "bullets": game.bullets,
        }
        if last_victim is not None and hit:
            result["mute_minutes"] = game.current_stake()
        if coward:
            result["penalty_minutes"] = penalty_minutes
        return result

    # ==================== 结算 ====================

    async def settle(self, game: PressureRouletteGame) -> dict:
        """结算局费 + 落库统计。败方每人扣 bet，胜方平分实际收到的池。平局不结算。"""
        if game.settled:
            return {"error": "这局已经结算过了。"}
        game.settled = True
        self._active_games.pop(game.channel_id)

        # 落库统计
        self._flush_stats(game)

        if game.winner is None:
            return {"winner": None, "pool": 0, "share": 0, "deduct_failed": []}

        winners = [game.winner]
        losers = [p for p in game.players if p != game.winner]

        collected = 0
        failed: list[int] = []
        for pid in losers:
            if await betting.deduct(pid, game.bet, "加压轮盘败方局费"):
                collected += game.bet
            else:
                failed.append(pid)

        share = collected // len(winners) if winners and collected > 0 else 0
        if share > 0:
            for pid in winners:
                await betting.credit(pid, share, "加压轮盘胜方分池")

        return {
            "winner": game.winner,
            "winners": winners,
            "losers": losers,
            "pool": collected,
            "share": share,
            "deduct_failed": failed,
        }

    def _flush_stats(self, game: PressureRouletteGame) -> None:
        """把内存统计落库。失败只记日志，不影响结算。"""
        if game.stats is None or not game.guild_id:
            return
        try:
            from src.chat.features.games.services.pressure_stats_db import get_stats_db
            alive_ids = game.alive()
            outcome = "champion" if game.winner is not None else "draw"
            rows = finalize_pressure_stats(game.stats, outcome=outcome, alive_ids=alive_ids)
            get_stats_db().record_pressure_game(game.guild_id, rows)
        except Exception:
            import logging
            logging.getLogger(__name__).exception("加压轮盘统计落库失败")
        finally:
            game.stats = None


pressure_roulette_service = PressureRouletteService()
