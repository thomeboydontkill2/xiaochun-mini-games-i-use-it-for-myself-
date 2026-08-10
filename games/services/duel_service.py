# -*- coding: utf-8 -*-
"""
两人死斗服务 —— 石头剪刀布三局两胜，1v1。

相比旧版的修复：
- coin↔coin 结算改用 betting.transfer：扣款失败整笔中止，派彩失败自动退款，
  不再出现"输家没扣到钱但赢家拿到钱"。
- 赌命系统奖励走每日限次。
- 情况4（没钱赌命赢了 vs 赌币输家）的注释与行为统一：输家确实要扣币。
- 对局注册表带 TTL，弃局不再把两个玩家永久锁死。
- submit_choice 幂等：同一局重复提交返回明确错误。
"""

import logging
from src.chat.features.games.config.games_config import (
    LIFE_GAMBLE_MUTE_MINUTES, LIFE_GAMBLE_DOUBLE_MUTE_MINUTES,
    LIFE_GAMBLE_BRAVE_MULTIPLIER, DUEL_ROUND_TIME, DUEL_MAX_ROUNDS,
)
from src.chat.features.games.services import betting
from src.chat.features.games.services.registry import GameRegistry

log = logging.getLogger(__name__)

CHOICES = ["石头", "剪刀", "布"]
WIN_MAP = {"石头": "剪刀", "剪刀": "布", "布": "石头"}


class DuelGame:
    def __init__(self, p1_id: int, p2_id: int, bet: int,
                 p1_life: bool, p2_life: bool, p1_has_coins: bool, p2_has_coins: bool):
        self.p1_id = p1_id
        self.p2_id = p2_id
        self.bet = bet
        self.p1_life = p1_life
        self.p2_life = p2_life
        self.p1_has_coins = p1_has_coins
        self.p2_has_coins = p2_has_coins
        self.p1_score = 0
        self.p2_score = 0
        self.round = 1
        self.p1_choice: str | None = None
        self.p2_choice: str | None = None
        self.history: list[tuple[int, str, str]] = []
        self.settled = False
        self.p1_timed_out: bool = False   # 单轮超时标记
        self.p2_timed_out: bool = False


class DuelService:
    def __init__(self):
        self._active_games: GameRegistry[DuelGame] = GameRegistry()

    def validate_bet(self, bet: int) -> tuple[bool, str]:
        return betting.validate_bet(bet)

    def is_playing(self, user_id: int) -> bool:
        return user_id in self._active_games

    def get_game(self, user_id: int) -> DuelGame | None:
        return self._active_games.get(user_id)

    def create_game(self, p1_id, p2_id, bet, p1_life, p2_life, p1_has_coins, p2_has_coins) -> DuelGame | None:
        # 二次校验：接受邀请的瞬间任意一方可能已进了别的局
        if self.is_playing(p1_id) or self.is_playing(p2_id):
            return None
        game = DuelGame(p1_id, p2_id, bet, p1_life, p2_life, p1_has_coins, p2_has_coins)
        self._active_games.set(p1_id, game)
        self._active_games.set(p2_id, game)
        return game

    def submit_choice(self, user_id: int, choice: str) -> dict:
        game = self._active_games.get(user_id)
        if not game:
            return {"error": "没有进行中的死斗"}
        if choice not in CHOICES:
            return {"error": "只能选 石头/剪刀/布"}

        if user_id == game.p1_id:
            if game.p1_choice:
                return {"error": "你这局已经出过了"}
            game.p1_choice = choice
        elif user_id == game.p2_id:
            if game.p2_choice:
                return {"error": "你这局已经出过了"}
            game.p2_choice = choice
        else:
            return {"error": "你不在这场死斗里"}

        if game.p1_choice and game.p2_choice:
            return self._resolve_round(game)
        return {"waiting": True, "message": "已出拳，等待对手..."}

    def _resolve_round(self, game: DuelGame) -> dict:
        c1, c2 = game.p1_choice, game.p2_choice
        game.history.append((game.round, c1, c2))
        game.p1_choice = None
        game.p2_choice = None

        if c1 == c2:
            result = {"round": game.round, "tie": True, "c1": c1, "c2": c2,
                      "p1_score": game.p1_score, "p2_score": game.p2_score}
            # 7 轮上限：仍未分胜负 → 流局
            if game.round >= DUEL_MAX_ROUNDS:
                self._cleanup(game)
                return {"round_result": result, "game_over": True, "draw": True}
            game.round += 1
            return {"round_result": result, "game_over": False}

        if WIN_MAP[c1] == c2:
            game.p1_score += 1
            winner = game.p1_id
        else:
            game.p2_score += 1
            winner = game.p2_id

        result = {
            "round": game.round, "tie": False, "c1": c1, "c2": c2,
            "winner_id": winner, "p1_score": game.p1_score, "p2_score": game.p2_score,
        }

        if game.p1_score >= 2 or game.p2_score >= 2:
            final_winner = game.p1_id if game.p1_score >= 2 else game.p2_id
            final_loser = game.p2_id if final_winner == game.p1_id else game.p1_id
            self._cleanup(game)
            return {"round_result": result, "game_over": True,
                    "winner_id": final_winner, "loser_id": final_loser}

        game.round += 1
        return {"round_result": result, "game_over": False}

    def _cleanup(self, game: DuelGame):
        self._active_games.pop(game.p1_id)
        self._active_games.pop(game.p2_id)

    def timeout_round(self, user_id: int) -> dict:
        """单轮某方超时未出拳。
        - 双方都超时 → 取消对局（流局）
        - 单方超时 → 另一方判赢本轮
        """
        game = self._active_games.get(user_id)
        if not game:
            return {"error": "没有进行中的死斗"}
        if user_id == game.p1_id:
            game.p1_timed_out = True
        elif user_id == game.p2_id:
            game.p2_timed_out = True
        else:
            return {"error": "你不在这场死斗里"}

        # 双方都超时 → 取消
        if game.p1_timed_out and game.p2_timed_out:
            self._cleanup(game)
            return {"both_timeout": True}

        # 单方超时 → 另一方判赢本轮
        if game.p1_timed_out and not game.p2_timed_out and game.p2_choice is None:
            # p1 超时，p2 还没出 → p2 赢本轮
            game.p2_score += 1
            game.p1_timed_out = False
            game.p2_timed_out = False
            result = {"round": game.round, "timeout_win": True,
                      "winner_id": game.p2_id, "loser_id": game.p1_id,
                      "p1_score": game.p1_score, "p2_score": game.p2_score}
            if game.p2_score >= 2:
                self._cleanup(game)
                return {"round_result": result, "game_over": True,
                        "winner_id": game.p2_id, "loser_id": game.p1_id}
            game.round += 1
            return {"round_result": result, "game_over": False}

        if game.p2_timed_out and not game.p1_timed_out and game.p1_choice is None:
            # p2 超时，p1 还没出 → p1 赢本轮
            game.p1_score += 1
            game.p1_timed_out = False
            game.p2_timed_out = False
            result = {"round": game.round, "timeout_win": True,
                      "winner_id": game.p1_id, "loser_id": game.p2_id,
                      "p1_score": game.p1_score, "p2_score": game.p2_score}
            if game.p1_score >= 2:
                self._cleanup(game)
                return {"round_result": result, "game_over": True,
                        "winner_id": game.p1_id, "loser_id": game.p2_id}
            game.round += 1
            return {"round_result": result, "game_over": False}

        return {"waiting_other": True}

    def cancel_game(self, user_id: int) -> bool:
        game = self._active_games.get(user_id)
        if game:
            self._cleanup(game)
            return True
        return False

    async def settle(self, game: DuelGame, winner_id: int, loser_id: int, guild=None) -> dict:
        """结算币和禁言，按赌命组合处理。幂等：重复调用直接返回。"""
        from src.chat.features.abuse_guard.service.abuse_guard_service import abuse_guard_service

        if game.settled:
            return {"error": "这场死斗已经结算过了"}
        game.settled = True

        winner_life = game.p1_life if winner_id == game.p1_id else game.p2_life
        loser_life = game.p1_life if loser_id == game.p1_id else game.p2_life
        winner_has_coins = game.p1_has_coins if winner_id == game.p1_id else game.p2_has_coins
        loser_has_coins = game.p1_has_coins if loser_id == game.p1_id else game.p2_has_coins
        bet = game.bet

        result = {"winner_id": winner_id, "loser_id": loser_id, "bet": bet}

        # 情况1：双方都赌币
        if not winner_life and not loser_life:
            ok = await betting.transfer(loser_id, winner_id, bet, "死斗")
            return {**result, "mode": "coin_vs_coin", "settle_failed": not ok,
                    "winner_gain": bet if ok else 0, "loser_loss": bet if ok else 0, "loser_muted": 0}

        # 情况2：赢家赌命(有钱)，输家赌币 —— 赢家额外拿 1.5x 勇敢者奖励（系统出）
        if winner_life and winner_has_coins and not loser_life:
            ok = await betting.transfer(loser_id, winner_id, bet, "死斗")
            brave = 0
            if ok:
                brave = int(bet * (LIFE_GAMBLE_BRAVE_MULTIPLIER - 1))
                if not await betting.credit(winner_id, brave, "死斗赌命勇敢者奖励"):
                    brave = 0
            return {**result, "mode": "life_rich_vs_coin", "settle_failed": not ok,
                    "winner_gain": (bet + brave) if ok else 0, "loser_loss": bet if ok else 0, "loser_muted": 0}

        # 情况3：赢家赌币，输家赌命(有钱) —— 输家扣币 + 禁言
        if not winner_life and loser_life and loser_has_coins:
            ok = await betting.transfer(loser_id, winner_id, bet, "死斗赌命")
            await abuse_guard_service.punish_with_mute(loser_id, LIFE_GAMBLE_MUTE_MINUTES, guild)
            return {**result, "mode": "coin_vs_life_rich", "settle_failed": not ok,
                    "winner_gain": bet if ok else 0, "loser_loss": bet if ok else 0,
                    "loser_muted": LIFE_GAMBLE_MUTE_MINUTES}

        # 情况4：赢家赌命(没钱)，输家赌币 —— 输家照常扣币给赢家
        if winner_life and not winner_has_coins and not loser_life:
            ok = await betting.transfer(loser_id, winner_id, bet, "死斗赌命赢取")
            return {**result, "mode": "life_poor_vs_coin", "settle_failed": not ok,
                    "winner_gain": bet if ok else 0, "loser_loss": bet if ok else 0, "loser_muted": 0}

        # 情况5：赢家赌币，输家赌命(没钱) —— 输家只禁言
        if not winner_life and loser_life and not loser_has_coins:
            await abuse_guard_service.punish_with_mute(loser_id, LIFE_GAMBLE_MUTE_MINUTES, guild)
            return {**result, "mode": "coin_vs_life_poor",
                    "winner_gain": 0, "loser_loss": 0, "loser_muted": LIFE_GAMBLE_MUTE_MINUTES}

        # 情况6：双方都赌命 —— 赢家系统奖励（限次），输家禁言
        reward = await betting.grant_life_reward(winner_id, "死斗双方赌命系统奖励")
        await abuse_guard_service.punish_with_mute(loser_id, LIFE_GAMBLE_DOUBLE_MUTE_MINUTES, guild)
        return {**result, "mode": "life_vs_life",
                "winner_gain": reward, "loser_loss": 0, "loser_muted": LIFE_GAMBLE_DOUBLE_MUTE_MINUTES}


duel_service = DuelService()
