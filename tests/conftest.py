# -*- coding: utf-8 -*-
"""
测试桩：把本仓库的 games 包挂到宿主的 src.chat.features.games 路径上，
并 stub 掉 coin_service / abuse_guard_service，使纯逻辑可以脱离宿主 bot 跑测试。
"""

import sys
import types
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _ensure_pkg(name: str) -> types.ModuleType:
    mod = sys.modules.get(name)
    if mod is None:
        mod = types.ModuleType(name)
        mod.__path__ = []
        sys.modules[name] = mod
    return mod


for pkg in ("src", "src.chat", "src.chat.features"):
    _ensure_pkg(pkg)

import games  # noqa: E402

sys.modules["src.chat.features.games"] = games


class FakeCoinService:
    def __init__(self):
        self.balances: dict[int, int] = {}
        self.fail_add = False
        self.fail_add_users: set[int] = set()
        self.fail_remove = False
        self.fail_remove_users: set[int] = set()
        self.log: list[tuple] = []

    async def get_balance(self, user_id: int):
        return self.balances.get(user_id, 0)

    async def add_coins(self, user_id: int, amount: int, reason: str = ""):
        if self.fail_add or user_id in self.fail_add_users:
            raise RuntimeError("add_coins failed")
        self.balances[user_id] = self.balances.get(user_id, 0) + amount
        self.log.append(("add", user_id, amount, reason))

    async def remove_coins(self, user_id: int, amount: int, reason: str = ""):
        if self.fail_remove or user_id in self.fail_remove_users:
            raise RuntimeError("remove_coins failed")
        self.balances[user_id] = self.balances.get(user_id, 0) - amount
        self.log.append(("remove", user_id, amount, reason))


class FakeAbuseGuard:
    def __init__(self):
        self.mutes: list[tuple[int, int]] = []

    async def punish_with_mute(self, user_id: int, minutes: int, guild=None):
        self.mutes.append((user_id, minutes))


fake_coin = FakeCoinService()
fake_guard = FakeAbuseGuard()

coin_mod = types.ModuleType("src.chat.features.odysseia_coin.service.coin_service")
coin_mod.coin_service = fake_coin
for pkg in ("src.chat.features.odysseia_coin", "src.chat.features.odysseia_coin.service"):
    _ensure_pkg(pkg)
sys.modules["src.chat.features.odysseia_coin.service.coin_service"] = coin_mod

guard_mod = types.ModuleType("src.chat.features.abuse_guard.service.abuse_guard_service")
guard_mod.abuse_guard_service = fake_guard
for pkg in ("src.chat.features.abuse_guard", "src.chat.features.abuse_guard.service"):
    _ensure_pkg(pkg)
sys.modules["src.chat.features.abuse_guard.service.abuse_guard_service"] = guard_mod


@pytest.fixture(autouse=True)
def reset_stubs():
    fake_coin.balances.clear()
    fake_coin.log.clear()
    fake_coin.fail_add = False
    fake_coin.fail_add_users.clear()
    fake_coin.fail_remove = False
    fake_coin.fail_remove_users.clear()
    fake_guard.mutes.clear()
    from games.services import betting
    betting.life_limiter._counts.clear()
    yield


@pytest.fixture
def coin():
    return fake_coin


@pytest.fixture
def guard():
    return fake_guard
