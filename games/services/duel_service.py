# -*- coding: utf-8 -*-
"""
两人死斗服务 —— 石头剪刀布三局两胜，1v1
支持赌命机制：没钱可赌命，输了禁言；有钱赌命有勇敢者奖励
"""

import random
import logging
import asyncio
from src.chat.features.games.config.games_config import (
    MIN_BET, MAX_BET,
    LIFE_GAMBLE_MUTE_MINUTES, LIFE_GAMBLE_DOUBLE_MUTE_MINUTES,
    LIFE_GAMBLE_REWARD_MIN, LIFE_GAMBLE_REWARD_MAX, LIFE_GAMBLE_BRAVE_MULTIPLIER,
)

log = logging.getLogger(__name__)

CHOICES = ["石头", "剪刀", "布"]
WIN_MAP = {"石头": "剪刀", "剪刀": "布", "布": "石头"}


class DuelGame:
    def __init__(self, p1_id: int, p2_id: int, bet: int, p1_life: bool, p2_life: bool, p1_has_coins: bool, p2_has_coins: bool):
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
        self.p1_choice = None
        self.p2_choice = None
        self.history = []

    def to_dict(self) -> dict:
        return {
            "p1_id": self.p1_id, "p2_id": self.p2_id, "bet": self.bet,
            "p1_life": self.p1_life, "p2_life": self.p2_life,
            "p1_score": self.p1_score, "p2_score": self.p2_score,
            "round": self.round,
        }


class DuelService:
    def __init__(self):
        self._active_games: dict[int, DuelGame] = {}  # key: player id

    def validate_bet(self, bet: int) -> tuple[bool, str]:
        if bet < MIN_BET:
            return False, f"最少下注 {MIN_BET} 春春币。"
        if bet > MAX_BET:
            return False, f"最多下注 {MAX_BET} 春春币。"
        return True, ""

    def is_playing(self, user_id: int) -> bool:
        return user_id in self._active_games

    def get_game(self, user_id: int) -> DuelGame | None:
        return self._active_games.get(user_id)

    def create_game(self, p1_id, p2_id, bet, p1_life, p2_life, p1_has_coins, p2_has_coins) -> DuelGame:
        game = DuelGame(p1_id, p2_id, bet, p1_life, p2_life, p1_has_coins, p2_has_coins)
        self._active_games[p1_id] = game
        self._active_games[p2_id] = game
        return game

    def submit_choice(self, user_id: int, choice: str) -> dict:
        """提交选择，返回 {round_result, game_over, winner_id, reason}"""
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

        if c1 == c2:
            result = {"round": game.round, "tie": True, "c1": c1, "c2": c2}
            game.p1_choice = None
            game.p2_choice = None
            return {"round_result": result, "game_over": False}

        if WIN_MAP[c1] == c2:
            game.p1_score += 1
            winner = game.p1_id
        else:
            game.p2_score += 1
            winner = game.p2_id

        game.p1_choice = None
        game.p2_choice = None

        result = {
            "round": game.round, "tie": False, "c1": c1, "c2": c2,
            "winner_id": winner, "p1_score": game.p1_score, "p2_score": game.p2_score,
        }

        if game.p1_score >= 2 or game.p2_score >= 2:
            final_winner = game.p1_id if game.p1_score >= 2 else game.p2_id
            final_loser = game.p2_id if final_winner == game.p1_id else game.p1_id
            self._cleanup(game)
            return {"round_result": result, "game_over": True, "winner_id": final_winner, "loser_id": final_loser}

        game.round += 1
        return {"round_result": result, "game_over": False}

    def _cleanup(self, game: DuelGame):
        self._active_games.pop(game.p1_id, None)
        self._active_games.pop(game.p2_id, None)

    def cancel_game(self, user_id: int) -> bool:
        game = self._active_games.get(user_id)
        if game:
            self._cleanup(game)
            return True
        return False

    async def settle(self, game: DuelGame, winner_id: int, loser_id: int, guild=None) -> dict:
        """结算币和禁言，按赌命组合处理。"""
        from src.chat.features.odysseia_coin.service.coin_service import coin_service
        from src.chat.features.abuse_guard.service.abuse_guard_service import abuse_guard_service

        winner_life = (game.p1_life if winner_id == game.p1_id else game.p2_life)
        loser_life = (game.p1_life if loser_id == game.p1_id else game.p2_life)
        winner_has_coins = (game.p1_has_coins if winner_id == game.p1_id else game.p2_has_coins)
        loser_has_coins = (game.p1_has_coins if loser_id == game.p1_id else game.p2_has_coins)
        bet = game.bet

        result = {"winner_id": winner_id, "loser_id": loser_id, "bet": bet}

        # 情况1：双方都赌币
        if not winner_life and not loser_life:
            try:
                await coin_service.remove_coins(loser_id, bet, "死斗输掉")
                await coin_service.add_coins(winner_id, bet, "死斗赢取")
            except Exception:
                log.exception("死斗结算失败")
            return {**result, "mode": "coin_vs_coin", "winner_gain": bet, "loser_loss": bet, "loser_muted": 0}

        # 情况2：赢家赌命(有钱)，输家赌币 —— 赢家拿 1.5x 勇敢者奖励
        if winner_life and winner_has_coins and not loser_life:
            reward = int(bet * LIFE_GAMBLE_BRAVE_MULTIPLIER)
            try:
                await coin_service.remove_coins(loser_id, bet, "死斗输掉")
                await coin_service.add_coins(winner_id, bet + reward, "死斗赌命勇敢者奖励")
            except Exception:
                log.exception("死斗结算失败")
            return {**result, "mode": "life_rich_vs_coin", "winner_gain": bet + reward, "loser_loss": bet, "loser_muted": 0}

        # 情况3：赢家赌币，输家赌命(有钱) —— 输家禁言2分钟
        if not winner_life and loser_life and loser_has_coins:
            try:
                await coin_service.remove_coins(loser_id, bet, "死斗赌命输掉")
                await coin_service.add_coins(winner_id, bet, "死斗赢取")
            except Exception:
                log.exception("死斗结算失败")
            await abuse_guard_service.punish_with_mute(loser_id, LIFE_GAMBLE_MUTE_MINUTES, guild)
            return {**result, "mode": "coin_vs_life_rich", "winner_gain": bet, "loser_loss": bet, "loser_muted": LIFE_GAMBLE_MUTE_MINUTES}

        # 情况4：赢家赌命(没钱)，输家赌币 —— 赢家拿对方下注，输家不扣币
        if winner_life and not winner_has_coins and not loser_life:
            try:
                await coin_service.remove_coins(loser_id, bet, "死斗输给赌命")
                await coin_service.add_coins(winner_id, bet, "死斗赌命赢取")
            except Exception:
                log.exception("死斗结算失败")
            return {**result, "mode": "life_poor_vs_coin", "winner_gain": bet, "loser_loss": bet, "loser_muted": 0}

        # 情况5：赢家赌币，输家赌命(没钱) —— 输家禁言2分钟
        if not winner_life and loser_life and not loser_has_coins:
            await abuse_guard_service.punish_with_mute(loser_id, LIFE_GAMBLE_MUTE_MINUTES, guild)
            return {**result, "mode": "coin_vs_life_poor", "winner_gain": 0, "loser_loss": 0, "loser_muted": LIFE_GAMBLE_MUTE_MINUTES}

        # 情况6：双方都赌命 —— 赢家系统奖励，输家禁言4分钟
        if winner_life and loser_life:
            reward = random.randint(LIFE_GAMBLE_REWARD_MIN, LIFE_GAMBLE_REWARD_MAX)
            try:
                await coin_service.add_coins(winner_id, reward, "死斗双方赌命系统奖励")
            except Exception:
                log.exception("死斗赌命奖励失败")
            await abuse_guard_service.punish_with_mute(loser_id, LIFE_GAMBLE_DOUBLE_MUTE_MINUTES, guild)
            return {**result, "mode": "life_vs_life", "winner_gain": reward, "loser_loss": 0, "loser_muted": LIFE_GAMBLE_DOUBLE_MUTE_MINUTES}

        return {**result, "mode": "unknown", "winner_gain": 0, "loser_loss": 0, "loser_muted": 0}


duel_service = DuelService()
