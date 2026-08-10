# -*- coding: utf-8 -*-
"""
共用对局注册表 —— 带 TTL 的内存对局存储。

修的问题：原来每个 service 各自维护 dict，对局如果没走到结算就永远留在内存里，
频道/玩家会被"已有一场游戏"卡死。这里统一加创建时间，访问时惰性清理过期对局。
"""

import time
from typing import Generic, TypeVar

from src.chat.features.games.config.games_config import GAME_TTL_SECONDS

T = TypeVar("T")


class GameRegistry(Generic[T]):
    def __init__(self, ttl_seconds: int = GAME_TTL_SECONDS):
        self.ttl = ttl_seconds
        self._games: dict[int, tuple[float, T]] = {}

    def _sweep(self) -> None:
        now = time.time()
        expired = [k for k, (ts, _) in self._games.items() if now - ts > self.ttl]
        for k in expired:
            del self._games[k]

    def get(self, key: int) -> T | None:
        self._sweep()
        entry = self._games.get(key)
        return entry[1] if entry else None

    def set(self, key: int, game: T) -> None:
        self._sweep()
        self._games[key] = (time.time(), game)

    def pop(self, key: int) -> T | None:
        entry = self._games.pop(key, None)
        return entry[1] if entry else None

    def __contains__(self, key: int) -> bool:
        return self.get(key) is not None
