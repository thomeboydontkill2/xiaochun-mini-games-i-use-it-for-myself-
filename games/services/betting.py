# -*- coding: utf-8 -*-
"""
共用下注/结算封装 —— 所有游戏统一经这里走 coin_service。

原则：
- 结算失败不能静默吞掉：转账先扣输家，扣款失败则整笔中止；派彩失败自动把扣掉的退回去。
- 赌命系统奖励有每日次数上限（LIFE_GAMBLE_DAILY_LIMIT），防止没本金白嫖刷币。
"""

import time
import random
import logging
from src.chat.features.games.config.games_config import (
    MIN_BET, MAX_BET,
    LIFE_GAMBLE_REWARD_MIN, LIFE_GAMBLE_REWARD_MAX, LIFE_GAMBLE_DAILY_LIMIT,
)

log = logging.getLogger(__name__)


def validate_bet(bet: int) -> tuple[bool, str]:
    if not isinstance(bet, int):
        return False, "下注必须是整数。"
    if bet < MIN_BET:
        return False, f"最少下注 {MIN_BET} 春春币哦。"
    if bet > MAX_BET:
        return False, f"最多下注 {MAX_BET} 春春币，别上头。"
    return True, ""


async def get_balance(user_id: int) -> int:
    from src.chat.features.odysseia_coin.service.coin_service import coin_service
    bal = await coin_service.get_balance(user_id)
    return bal or 0


async def deduct(user_id: int, amount: int, reason: str) -> bool:
    """扣币。成功返回 True，失败返回 False（不抛出，但会记日志）。"""
    if amount <= 0:
        return True
    from src.chat.features.odysseia_coin.service.coin_service import coin_service
    try:
        await coin_service.remove_coins(user_id, amount, reason)
        return True
    except Exception:
        log.exception("扣币失败 user=%s amount=%s reason=%s", user_id, amount, reason)
        return False


async def credit(user_id: int, amount: int, reason: str) -> bool:
    if amount <= 0:
        return True
    from src.chat.features.odysseia_coin.service.coin_service import coin_service
    try:
        await coin_service.add_coins(user_id, amount, reason)
        return True
    except Exception:
        log.exception("派彩失败 user=%s amount=%s reason=%s", user_id, amount, reason)
        return False


async def transfer(loser_id: int, winner_id: int, amount: int, reason: str) -> bool:
    """输家 → 赢家转账。扣款失败整笔中止；派彩失败退还输家。返回是否成功。"""
    if amount <= 0:
        return True
    if not await deduct(loser_id, amount, f"{reason}（扣除）"):
        return False
    if not await credit(winner_id, amount, f"{reason}（获得）"):
        await credit(loser_id, amount, f"{reason}（派彩失败退回）")
        return False
    return True


class LifeGambleLimiter:
    """赌命系统奖励每日限次。内存版，重启清零（宿主是单进程，可接受）。"""

    def __init__(self, daily_limit: int = LIFE_GAMBLE_DAILY_LIMIT):
        self.daily_limit = daily_limit
        self._counts: dict[int, tuple[int, int]] = {}  # user_id -> (day, count)

    def _today(self) -> int:
        return int(time.time() // 86400)

    def can_reward(self, user_id: int) -> bool:
        day, count = self._counts.get(user_id, (self._today(), 0))
        if day != self._today():
            return True
        return count < self.daily_limit

    def record(self, user_id: int) -> None:
        today = self._today()
        day, count = self._counts.get(user_id, (today, 0))
        if day != today:
            day, count = today, 0
        self._counts[user_id] = (day, count + 1)


life_limiter = LifeGambleLimiter()


async def grant_life_reward(user_id: int, reason: str) -> int:
    """发赌命系统奖励，受每日限次约束。返回实际发放金额（0=当天已达上限）。"""
    if not life_limiter.can_reward(user_id):
        return 0
    reward = random.randint(LIFE_GAMBLE_REWARD_MIN, LIFE_GAMBLE_REWARD_MAX)
    if await credit(user_id, reward, reason):
        life_limiter.record(user_id)
        return reward
    return 0
