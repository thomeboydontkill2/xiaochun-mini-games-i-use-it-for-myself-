# -*- coding: utf-8 -*-
"""加压俄罗斯轮盘 —— 服务层（纯逻辑，不依赖 discord.py）。

状态机：
    joining → loading → turn → resolving → turn → ... → ended

核心规则：
- 6 弹巢，开局 1 发实弹，位置随机。
- 轮到当前玩家三选一：传枪 / 再开一枪 / 加压。
- 中弹 = 禁言（当前赌注分钟）+ 出局 + 扣局费。
- 子弹打光 = 游戏立刻结束；只剩 1 人 → 冠军；多人 → 平局。

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


class PressureRouletteGame:
    """单局状态。"""

    def __init__(self, channel_id: int, bet: int):
        self.channel_id = channel_id
        self.bet = bet
        self.players: list[int] = []
        self.dead: list[int] = []
        self.phase: str = "joining"
        self.shot_count: int = 0           # 第几枪（展示用）

        # 弹巢：True=实弹，False=空弹。chamber_pos 是下一发要打的位置。
        self.chamber: list[bool] = []
        self.chamber_pos: int = 0

        # 当前轮到 players 中谁（索引）
        self.current_index: int = 0

        # 每人连开蓄力层数；传枪/加压/中弹清零
        self.streak: dict[int, int] = {}

        # 当前赌注（分钟）：基础 3，每加压 1 发 +1
        self.stake_minutes: int = PRESSURE_ROULETTE_BASE_STAKE

        self.settled: bool = False
        self.winner: int | None = None     # None = 平局或未结束

    # ---- 便捷查询 ----
    def alive(self) -> list[int]:
        return [p for p in self.players if p not in self.dead]

    def current_player(self) -> int | None:
        alive = self.alive()
        if not alive:
            return None
        # current_index 可能因为有人出局而越界，按存活列表取
        idx = self.current_index % len(self.players)
        # 找下一个存活的人
        for _ in range(len(self.players)):
            p = self.players[idx]
            if p not in self.dead:
                return p
            idx = (idx + 1) % len(self.players)
        return None

    def remaining_live(self) -> int:
        """弹巢里还没打掉的实弹数。"""
        return sum(1 for i in range(self.chamber_pos, len(self.chamber)) if self.chamber[i])

    def remaining_chamber(self) -> int:
        """弹巢里还没打掉的格数。"""
        return len(self.chamber) - self.chamber_pos

    def chamber_display(self) -> list[str]:
        """弹巢可视化：已开的显示 空/砰，未开的显示 ?。"""
        out: list[str] = []
        for i, live in enumerate(self.chamber):
            if i < self.chamber_pos:
                out.append("砰" if live else "空")
            else:
                out.append("?")
        return out


def _roll_chamber(size: int, live: int) -> list[bool]:
    """装填弹巢：live 发实弹 + (size-live) 发空弹，随机洗牌。"""
    chamber = [True] * live + [False] * (size - live)
    random.shuffle(chamber)
    return chamber


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
        if game.phase != "joining":
            return False, "已经开局了，等下一局吧。"
        if user_id in game.players:
            return False, "你已经上桌了。"
        if len(game.players) >= PRESSURE_ROULETTE_MAX_PLAYERS:
            return False, f"桌满了（最多 {PRESSURE_ROULETTE_MAX_PLAYERS} 人）。"
        game.players.append(user_id)
        game.streak[user_id] = 0
        return True, "上桌了"

    def leave_player(self, channel_id: int, user_id: int) -> tuple[bool, str]:
        game = self.get_game(channel_id)
        if game is None:
            return False, "这局已经没了。"
        if game.phase != "joining":
            return False, "已经开局了，退不了。"
        if user_id not in game.players:
            return False, "你根本没上桌。"
        game.players.remove(user_id)
        game.streak.pop(user_id, None)
        return True, "退了"

    def start_game(self, channel_id: int) -> tuple[bool, str, PressureRouletteGame | None]:
        game = self.get_game(channel_id)
        if game is None:
            return False, "这局已经没了。", None
        if game.phase != "joining":
            return False, "已经开局了。", None
        if len(game.players) < PRESSURE_ROULETTE_MIN_PLAYERS:
            return False, f"人不够，至少 {PRESSURE_ROULETTE_MIN_PLAYERS} 人。", None
        # 装弹
        game.chamber = _roll_chamber(PRESSURE_ROULETTE_CHAMBER_SIZE, PRESSURE_ROULETTE_INITIAL_LIVE)
        game.chamber_pos = 0
        game.phase = "turn"
        game.current_index = 0
        game.shot_count = 0
        game.stake_minutes = PRESSURE_ROULETTE_BASE_STAKE
        return True, "开局", game

    # ==================== 操作 ====================

    def pass_gun(self, channel_id: int, user_id: int) -> dict:
        """传枪：弹巢前进一格，交给下一个人。"""
        game = self.get_game(channel_id)
        if game is None:
            return {"error": "这局已经没了。"}
        if game.phase != "turn":
            return {"error": "现在不是你的回合。"}
        cur = game.current_player()
        if cur != user_id:
            return {"error": "还没轮到你。"}
        # 弹巢前进一格（消耗一发空弹的位置——传枪不扣扳机但弹巢转动）
        # 按你规则：传枪 = 弹巢前进一格，交给下一个人
        if game.chamber_pos < len(game.chamber):
            game.chamber_pos += 1
        # 蓄力清零
        game.streak[user_id] = 0
        # 检查子弹打光
        if game.remaining_live() == 0:
            return self._end_game(game, reason="bullets_empty")
        # 轮到下家
        self._advance_to_next_alive(game)
        return {
            "action": "pass",
            "by": user_id,
            "next": game.current_player(),
            "chamber": game.chamber_display(),
            "remaining_live": game.remaining_live(),
            "remaining_chamber": game.remaining_chamber(),
            "stake_minutes": game.stake_minutes,
            "game_over": False,
        }

    def shoot_self(self, channel_id: int, user_id: int) -> dict:
        """再开一枪：对自己扣扳机。"""
        return self._shoot(channel_id, user_id, target=None, pressed=True)

    def shoot_target(self, channel_id: int, user_id: int, target_id: int) -> dict:
        """对别人开枪（第一阶段不开放，保留接口）。"""
        return self._shoot(channel_id, user_id, target=target_id, pressed=False)

    def _shoot(self, channel_id: int, user_id: int, target: int | None, pressed: bool) -> dict:
        game = self.get_game(channel_id)
        if game is None:
            return {"error": "这局已经没了。"}
        if game.phase != "turn":
            return {"error": "现在不是你的回合。"}
        cur = game.current_player()
        if cur != user_id:
            return {"error": "还没轮到你。"}
        if game.chamber_pos >= len(game.chamber):
            # 弹巢空了，理论上不会到这里（子弹打光应该已结束）
            return {"error": "弹巢空了。"}

        # 实际开枪对象：第一阶段只对自己开
        victim = user_id if target is None else target
        live = game.chamber[game.chamber_pos]
        game.chamber_pos += 1
        game.shot_count += 1

        if live:
            # 中弹
            game.dead.append(victim)
            game.streak[victim] = 0
            # 子弹打光检查
            if game.remaining_live() == 0:
                return self._end_game(game, reason="bullets_empty", last_victim=victim, hit=True)
            # 检查是否只剩 1 人
            alive = game.alive()
            if len(alive) <= 1:
                return self._end_game(game, reason="last_man", last_victim=victim, hit=True)
            # 中弹者出局，轮到下家
            self._advance_to_next_alive(game)
            return {
                "action": "shoot",
                "by": user_id,
                "victim": victim,
                "hit": True,
                "shot_count": game.shot_count,
                "chamber": game.chamber_display(),
                "remaining_live": game.remaining_live(),
                "remaining_chamber": game.remaining_chamber(),
                "stake_minutes": game.stake_minutes,
                "next": game.current_player(),
                "game_over": False,
                "mute_minutes": game.stake_minutes,
            }
        else:
            # 空弹
            # 蓄力 +1（只有对自己开枪才累积；对别人开枪不累积）
            if target is None:
                game.streak[user_id] = game.streak.get(user_id, 0) + 1
            # 子弹打光检查
            if game.remaining_live() == 0:
                return self._end_game(game, reason="bullets_empty", last_victim=victim, hit=False)
            # 轮到下家
            self._advance_to_next_alive(game)
            return {
                "action": "shoot",
                "by": user_id,
                "victim": victim,
                "hit": False,
                "shot_count": game.shot_count,
                "chamber": game.chamber_display(),
                "remaining_live": game.remaining_live(),
                "remaining_chamber": game.remaining_chamber(),
                "stake_minutes": game.stake_minutes,
                "next": game.current_player(),
                "streak": game.streak.get(user_id, 0),
                "game_over": False,
            }

    def press(self, channel_id: int, user_id: int) -> dict:
        """加压：装 1+蓄力层数 发子弹并滚动弹巢，赌注 +1 分钟/发。

        蓄力清零。加压后仍轮到自己（要对自己开一枪）。
        """
        game = self.get_game(channel_id)
        if game is None:
            return {"error": "这局已经没了。"}
        if game.phase != "turn":
            return {"error": "现在不是你的回合。"}
        cur = game.current_player()
        if cur != user_id:
            return {"error": "还没轮到你。"}

        streak = game.streak.get(user_id, 0)
        new_live = 1 + streak  # 装 1 + 蓄力层数 发

        # 把新子弹塞进弹巢剩余空位 + 已开过的位置重置
        # 按你规则：装 1+蓄力层数 发子弹并滚动弹巢
        # 实现：重置弹巢为 (剩余空弹数 + 新实弹数) 的全新洗牌
        # 剩余空弹 = 弹巢总长 - 已开格数 - 剩余实弹
        used = game.chamber_pos
        remaining_live_before = game.remaining_live()
        remaining_empty_before = (len(game.chamber) - used) - remaining_live_before
        total_live = remaining_live_before + new_live
        total_empty = max(0, remaining_empty_before)  # 空弹可能被新实弹挤掉
        # 新弹巢：把已开过的固定为结果，未开的重新洗
        # 简化：整个弹巢重新装填（已开过的位置保持原结果，未开的重新随机）
        new_size = len(game.chamber)
        # 已开部分固定
        opened = game.chamber[:used]
        # 未开部分重新装填
        unopened_live = total_live
        unopened_empty = new_size - used - unopened_live
        if unopened_empty < 0:
            # 实弹比剩余格数多，挤掉空弹
            unopened_live = new_size - used
            unopened_empty = 0
        unopened = [True] * unopened_live + [False] * unopened_empty
        random.shuffle(unopened)
        game.chamber = opened + unopened
        # chamber_pos 不变（已开过的位置固定）

        # 赌注 +1 分钟/发
        game.stake_minutes += new_live * PRESSURE_ROULETTE_PRESS_STAKE
        # 蓄力清零
        game.streak[user_id] = 0

        return {
            "action": "press",
            "by": user_id,
            "loaded": new_live,
            "stake_minutes": game.stake_minutes,
            "chamber": game.chamber_display(),
            "remaining_live": game.remaining_live(),
            "remaining_chamber": game.remaining_chamber(),
            "game_over": False,
        }

    def timeout_shoot(self, channel_id: int) -> dict:
        """超时自动开枪：对当前玩家自己开一枪。"""
        game = self.get_game(channel_id)
        if game is None:
            return {"error": "这局已经没了。"}
        if game.phase != "turn":
            return {"error": "现在不是回合阶段。"}
        cur = game.current_player()
        if cur is None:
            return {"error": "没有当前玩家。"}
        return self._shoot(channel_id, cur, target=None, pressed=True)

    # ==================== 内部 ====================

    def _advance_to_next_alive(self, game: PressureRouletteGame) -> None:
        """轮到下一个存活玩家。"""
        if not game.alive():
            return
        idx = game.current_index
        for _ in range(len(game.players)):
            idx = (idx + 1) % len(game.players)
            if game.players[idx] not in game.dead:
                game.current_index = idx
                return
        # 兜底
        game.current_index = idx

    def _end_game(self, game: PressureRouletteGame, reason: str,
                  last_victim: int | None = None, hit: bool = False) -> dict:
        """结束游戏。reason: bullets_empty / last_man。"""
        game.phase = "ended"
        alive = game.alive()
        if len(alive) == 1:
            game.winner = alive[0]
        else:
            game.winner = None  # 平局

        result = {
            "action": "shoot" if hit else "end",
            "game_over": True,
            "reason": reason,
            "winner": game.winner,
            "alive": alive,
            "dead": list(game.dead),
            "last_victim": last_victim,
            "last_hit": hit,
            "hit": hit,
            "stake_minutes": game.stake_minutes,
            "chamber": game.chamber_display(),
            "remaining_live": game.remaining_live(),
        }
        if last_victim is not None and hit:
            result["mute_minutes"] = game.stake_minutes
        return result

    # ==================== 结算 ====================

    async def settle(self, game: PressureRouletteGame) -> dict:
        """结算局费：败方每人扣 bet，胜方平分实际收到的池。平局不结算。"""
        if game.settled:
            return {"error": "这局已经结算过了。"}
        game.settled = True
        self._active_games.pop(game.channel_id)

        if game.winner is None:
            # 平局，不扣币
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


pressure_roulette_service = PressureRouletteService()
