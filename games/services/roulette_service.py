# -*- coding: utf-8 -*-
"""
幸运轮盘服务 —— 单人游戏，转盘随机停在不同倍率。

相比旧版的修复：
- 结算改为单笔净额（净赢 add 一次 / 净亏 remove 一次），不再"先扣注再派彩"两笔，
  杜绝扣款成功派彩失败（或反之）造成的币凭空消失/凭空产生。
- 对局在 settle 成功后才移除，spin 用 spun 标记防双击重复转。
- 赌命奖励走 betting.grant_life_reward，受每日限次约束。
- 对局注册表带 TTL，中途弃局不再永久占用。
"""

import random
import logging
from src.chat.features.games.config.games_config import (
    ROULETTE_MULTIPLIERS, ROULETTE_MUTE_MINUTES, LIFE_GAMBLE_MUTE_MINUTES,
)
from src.chat.features.games.services import betting
from src.chat.features.games.services.registry import GameRegistry

log = logging.getLogger(__name__)


class RouletteService:
    def __init__(self):
        self._active_games: GameRegistry[dict] = GameRegistry()

    def validate_bet(self, bet: int) -> tuple[bool, str]:
        return betting.validate_bet(bet)

    def is_playing(self, user_id: int) -> bool:
        return user_id in self._active_games

    def start_game(self, user_id: int, bet: int) -> None:
        self._active_games.set(user_id, {"bet": bet, "spun": False})

    def cancel_game(self, user_id: int) -> bool:
        return self._active_games.pop(user_id) is not None

    def spin(self, user_id: int) -> dict:
        """转盘。返回 {multiplier, bet, net, is_mute} 或 {error}。
        对局保留到 settle 完成，防止结算半路失败后状态丢失。"""
        game = self._active_games.get(user_id)
        if not game or game["spun"]:
            return {"error": "没有进行中的轮盘游戏"}
        game["spun"] = True

        multipliers = list(ROULETTE_MULTIPLIERS.keys())
        weights = list(ROULETTE_MULTIPLIERS.values())
        multiplier = random.choices(multipliers, weights=weights, k=1)[0]
        bet = game["bet"]
        is_mute = (multiplier == -2)
        net = 0 if is_mute else int(bet * multiplier)

        return {"multiplier": multiplier, "bet": bet, "net": net, "is_mute": is_mute}

    async def settle(self, user_id: int, result: dict, is_life_gamble: bool, has_coins: bool, guild=None) -> dict:
        """结算币和禁言。返回给 UI 的最终信息。"""
        from src.chat.features.abuse_guard.service.abuse_guard_service import abuse_guard_service

        multiplier = result["multiplier"]
        net = result["net"]
        is_mute = result.get("is_mute", False)

        try:
            if is_life_gamble:
                if is_mute:
                    await abuse_guard_service.punish_with_mute(user_id, ROULETTE_MUTE_MINUTES, guild)
                    return {**result, "life_gamble": True, "muted": ROULETTE_MUTE_MINUTES, "coins_change": 0}
                if multiplier < 0:
                    await abuse_guard_service.punish_with_mute(user_id, LIFE_GAMBLE_MUTE_MINUTES, guild)
                    return {**result, "life_gamble": True, "muted": LIFE_GAMBLE_MUTE_MINUTES, "coins_change": 0}
                if multiplier == 0:
                    return {**result, "life_gamble": True, "muted": 0, "coins_change": 0}
                reward = await betting.grant_life_reward(user_id, "幸运轮盘赌命勇敢者奖励")
                return {
                    **result, "life_gamble": True, "reward": reward, "coins_change": reward,
                    "reward_capped": reward == 0,
                }

            if is_mute:
                await abuse_guard_service.punish_with_mute(user_id, ROULETTE_MUTE_MINUTES, guild)
                return {**result, "life_gamble": False, "muted": ROULETTE_MUTE_MINUTES, "coins_change": 0}

            if net < 0:
                ok = await betting.deduct(user_id, -net, f"幸运轮盘亏{int(abs(multiplier) * 100)}%")
                if not ok:
                    return {**result, "life_gamble": False, "coins_change": 0, "settle_failed": True}
                return {**result, "life_gamble": False, "coins_change": net, "loss": -net}

            if net == 0:
                return {**result, "life_gamble": False, "coins_change": 0}

            ok = await betting.credit(user_id, net, "幸运轮盘派彩")
            if not ok:
                return {**result, "life_gamble": False, "coins_change": 0, "settle_failed": True}
            return {**result, "life_gamble": False, "coins_change": net}
        finally:
            self._active_games.pop(user_id)


roulette_service = RouletteService()
