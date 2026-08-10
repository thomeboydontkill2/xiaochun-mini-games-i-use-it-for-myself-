# -*- coding: utf-8 -*-
"""
幸运轮盘服务 —— 单人游戏，转盘随机停在不同倍率
下注 → 转盘 → 按倍率结算（0x/1x/2x/5x/10x）
"""

import random
import logging
from src.chat.features.games.config.games_config import (
    ROULETTE_MULTIPLIERS, MIN_BET, MAX_BET,
    LIFE_GAMBLE_MUTE_MINUTES, LIFE_GAMBLE_REWARD_MIN, LIFE_GAMBLE_REWARD_MAX,
    ROULETTE_MUTE_MINUTES,
)

log = logging.getLogger(__name__)


class RouletteService:
    def __init__(self):
        self._active_games: dict[int, dict] = {}

    def validate_bet(self, bet: int) -> tuple[bool, str]:
        if bet < MIN_BET:
            return False, f"最少下注 {MIN_BET} 春春币哦。"
        if bet > MAX_BET:
            return False, f"最多下注 {MAX_BET} 春春币，别上头。"
        return True, ""

    def is_playing(self, user_id: int) -> bool:
        return user_id in self._active_games

    def start_game(self, user_id: int, bet: int) -> None:
        self._active_games[user_id] = {"bet": bet, "spun": False}

    def spin(self, user_id: int) -> dict:
        """转盘，返回 {multiplier, bet, payout, net, is_mute}
        multiplier 可为负/小数；is_mute 表示禁言惩罚档（不扣币）"""
        game = self._active_games.get(user_id)
        if not game or game["spun"]:
            return {"error": "没有进行中的轮盘游戏"}
        game["spun"] = True

        multipliers = list(ROULETTE_MULTIPLIERS.keys())
        weights = list(ROULETTE_MULTIPLIERS.values())
        multiplier = random.choices(multipliers, weights=weights, k=1)[0]
        bet = game["bet"]
        is_mute = (multiplier == -2)

        if is_mute:
            payout = 0
            net = 0
        else:
            payout = bet * multiplier
            net = payout - bet  # multiplier<0 时 net 为负

        result = {
            "multiplier": multiplier,
            "bet": bet,
            "payout": payout,
            "net": net,
            "is_mute": is_mute,
        }
        del self._active_games[user_id]
        return result

    async def settle(self, user_id: int, result: dict, is_life_gamble: bool, has_coins: bool, guild=None) -> dict:
        """结算币和禁言。返回给 UI 的最终信息。"""
        from src.chat.features.odysseia_coin.service.coin_service import coin_service
        from src.chat.features.abuse_guard.service.abuse_guard_service import abuse_guard_service

        bet = result["bet"]
        multiplier = result["multiplier"]
        net = result["net"]
        is_mute = result.get("is_mute", False)

        # 赌命模式
        if is_life_gamble:
            if is_mute:
                # 禁言档：禁言
                await abuse_guard_service.punish_with_mute(user_id, ROULETTE_MUTE_MINUTES, guild)
                return {**result, "life_gamble": True, "muted": ROULETTE_MUTE_MINUTES, "coins_change": 0}
            elif multiplier < 0:
                # 负档（血本无归/亏一半/亏三成）：禁言
                mute_min = LIFE_GAMBLE_MUTE_MINUTES
                await abuse_guard_service.punish_with_mute(user_id, mute_min, guild)
                return {**result, "life_gamble": True, "muted": mute_min, "coins_change": 0}
            elif multiplier == 0:
                # 回本：不输不赢
                return {**result, "life_gamble": True, "muted": 0, "coins_change": 0}
            else:
                # 赢了：系统奖励
                reward = random.randint(LIFE_GAMBLE_REWARD_MIN, LIFE_GAMBLE_REWARD_MAX)
                if has_coins:
                    try:
                        await coin_service.add_coins(user_id, reward, "幸运轮盘赌命勇敢者奖励")
                    except Exception:
                        log.exception("轮盘赌命奖励发放失败")
                    return {**result, "life_gamble": True, "reward": reward, "coins_change": reward}
                else:
                    try:
                        await coin_service.add_coins(user_id, reward, "幸运轮盘赌命启动资金")
                    except Exception:
                        log.exception("轮盘赌命启动资金发放失败")
                    return {**result, "life_gamble": True, "reward": reward, "coins_change": reward}

        # 正常下注模式
        if is_mute:
            # 禁言档：不扣币，禁言
            await abuse_guard_service.punish_with_mute(user_id, ROULETTE_MUTE_MINUTES, guild)
            return {**result, "life_gamble": False, "muted": ROULETTE_MUTE_MINUTES, "coins_change": 0}

        if multiplier < 0:
            # 部分亏损：扣 bet * abs(multiplier)
            loss = int(bet * abs(multiplier))
            try:
                await coin_service.remove_coins(user_id, loss, f"幸运轮盘亏{int(abs(multiplier)*100)}%")
            except Exception:
                log.exception("轮盘扣币失败")
            return {**result, "life_gamble": False, "coins_change": -loss, "loss": loss}

        if multiplier == 0:
            # 回本：不扣不派
            return {**result, "life_gamble": False, "coins_change": 0}

        # 正倍率：派彩（扣赌注 + 给 bet*(1+multiplier)）
        try:
            payout_total = int(bet * (1 + multiplier))
            await coin_service.remove_coins(user_id, bet, "幸运轮盘下注")
            await coin_service.add_coins(user_id, payout_total, "幸运轮盘派彩")
        except Exception:
            log.exception("轮盘派彩失败")
        return {**result, "life_gamble": False, "coins_change": net}


roulette_service = RouletteService()
