# -*- coding: utf-8 -*-
"""加压俄罗斯轮盘 —— 纯逻辑测试。"""

import asyncio

import pytest

from games.services.pressure_roulette_service import (
    pressure_roulette_service,
    PressureRouletteGame,
    _roll_chamber,
)


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


CH = 100


def _make_game(players=4, bet=50):
    """造一局已开打的 game。"""
    pressure_roulette_service._active_games._games.clear()
    game = PressureRouletteGame(CH, bet)
    for i in range(players):
        game.players.append(1000 + i)
        game.streak[1000 + i] = 0
    game.chamber = [False, False, True, False, False, False]  # 第3格实弹
    game.chamber_pos = 0
    game.phase = "turn"
    game.current_index = 0
    pressure_roulette_service._active_games.set(CH, game)
    return game


# ==================== 装弹 ====================


def test_roll_chamber_correct_live_count():
    chamber = _roll_chamber(6, 1)
    assert len(chamber) == 6
    assert sum(chamber) == 1


def test_roll_chamber_random_position():
    positions = set()
    for _ in range(50):
        chamber = _roll_chamber(6, 1)
        positions.add(chamber.index(True))
    assert len(positions) >= 3  # 实弹位置应该随机分布


def test_roll_chamber_multiple_live():
    chamber = _roll_chamber(6, 3)
    assert sum(chamber) == 3
    assert len(chamber) == 6


# ==================== 加入/退出 ====================


def test_add_player_during_joining():
    pressure_roulette_service._active_games._games.clear()
    pressure_roulette_service.create_game(CH, 50)
    ok, msg = pressure_roulette_service.add_player(CH, 1001)
    assert ok
    game = pressure_roulette_service.get_game(CH)
    assert 1001 in game.players


def test_add_player_duplicate_rejected():
    pressure_roulette_service._active_games._games.clear()
    pressure_roulette_service.create_game(CH, 50)
    pressure_roulette_service.add_player(CH, 1001)
    ok, msg = pressure_roulette_service.add_player(CH, 1001)
    assert not ok
    assert "已经上桌" in msg


def test_leave_player():
    pressure_roulette_service._active_games._games.clear()
    pressure_roulette_service.create_game(CH, 50)
    pressure_roulette_service.add_player(CH, 1001)
    ok, msg = pressure_roulette_service.leave_player(CH, 1001)
    assert ok
    game = pressure_roulette_service.get_game(CH)
    assert 1001 not in game.players


def test_start_game_below_min_rejected():
    pressure_roulette_service._active_games._games.clear()
    pressure_roulette_service.create_game(CH, 50)
    pressure_roulette_service.add_player(CH, 1001)
    ok, msg, game = pressure_roulette_service.start_game(CH)
    assert not ok
    assert "人不够" in msg


def test_start_game_loads_chamber():
    pressure_roulette_service._active_games._games.clear()
    pressure_roulette_service.create_game(CH, 50)
    pressure_roulette_service.add_player(CH, 1001)
    pressure_roulette_service.add_player(CH, 1002)
    ok, msg, game = pressure_roulette_service.start_game(CH)
    assert ok
    assert game.phase == "turn"
    assert len(game.chamber) == 6
    assert sum(game.chamber) == 1  # 开局1发实弹
    assert game.stake_minutes == 3  # 基础赌注


# ==================== 传枪 ====================


def test_pass_gun_advances_chamber_and_rotates():
    game = _make_game(players=4)
    result = pressure_roulette_service.pass_gun(CH, 1000)
    assert result["action"] == "pass"
    assert game.chamber_pos == 1  # 弹巢前进一格
    assert result["next"] == 1001  # 轮到下家
    assert game.streak[1000] == 0  # 蓄力清零


def test_pass_gun_not_your_turn():
    game = _make_game(players=4)
    result = pressure_roulette_service.pass_gun(CH, 1001)  # 不是当前玩家
    assert "error" in result


# ==================== 开枪 ====================


def test_shoot_self_blank_advances_and_streak():
    game = _make_game(players=4)
    # chamber = [F, F, T, F, F, F]，第0格是空
    result = pressure_roulette_service.shoot_self(CH, 1000)
    assert result["action"] == "shoot"
    assert result["hit"] is False
    assert game.chamber_pos == 1
    assert game.streak[1000] == 1  # 蓄力+1
    assert result["next"] == 1001  # 轮到下家


def test_shoot_self_hit_eliminates_and_resets_streak():
    game = _make_game(players=4)
    game.chamber_pos = 2  # 指向实弹
    result = pressure_roulette_service.shoot_self(CH, 1000)
    assert result["hit"] is True
    assert 1000 in game.dead
    assert game.streak[1000] == 0  # 蓄力清零
    assert result.get("mute_minutes") == 3  # 当前赌注


def test_shoot_self_not_your_turn():
    game = _make_game(players=4)
    result = pressure_roulette_service.shoot_self(CH, 1001)
    assert "error" in result


def test_streak_accumulates_on_consecutive_blanks():
    game = _make_game(players=4)
    # 连续两枪空弹（前两格都是空）
    pressure_roulette_service.shoot_self(CH, 1000)
    # 轮到1001，让1001也开空枪
    game.chamber = [False, False, True, False, False, False]
    game.chamber_pos = 1
    # 重新设当前玩家为1001
    game.current_index = 1
    result = pressure_roulette_service.shoot_self(CH, 1001)
    assert result["hit"] is False
    assert game.streak[1001] == 1


# ==================== 加压 ====================


def test_press_loads_bullets_and_increases_stake():
    game = _make_game(players=4)
    game.streak[1000] = 2  # 蓄力2层
    result = pressure_roulette_service.press(CH, 1000)
    assert result["action"] == "press"
    assert result["loaded"] == 3  # 1 + 蓄力2
    # 赌注 +3 分钟（3发 × 1分钟/发）
    assert result["stake_minutes"] == 6
    assert game.streak[1000] == 0  # 蓄力清零


def test_press_with_zero_streak_loads_one():
    game = _make_game(players=4)
    result = pressure_roulette_service.press(CH, 1000)
    assert result["loaded"] == 1
    assert game.stake_minutes == 4  # 3 + 1


def test_press_not_your_turn():
    game = _make_game(players=4)
    result = pressure_roulette_service.press(CH, 1001)
    assert "error" in result


def test_press_adds_live_bullets():
    game = _make_game(players=4)
    game.streak[1000] = 1
    live_before = game.remaining_live()
    pressure_roulette_service.press(CH, 1000)
    live_after = game.remaining_live()
    assert live_after == live_before + 2  # 加了2发（1+蓄力1）


# ==================== 子弹打光结束 ====================


def test_bullets_empty_ends_game():
    game = _make_game(players=4)
    # 把弹巢设为只剩1发实弹，打完就光
    game.chamber = [False, False, False, False, False, True]
    game.chamber_pos = 5  # 指向最后一发实弹
    result = pressure_roulette_service.shoot_self(CH, 1000)
    assert result["game_over"] is True
    assert result["reason"] == "bullets_empty"
    assert 1000 in game.dead


def test_bullets_empty_by_passing():
    game = _make_game(players=4)
    # 只剩1发实弹在最后一格，传枪把它转过去就打光
    game.chamber = [False, False, False, False, False, True]
    game.chamber_pos = 5
    result = pressure_roulette_service.pass_gun(CH, 1000)
    assert result["game_over"] is True
    assert result["reason"] == "bullets_empty"


def test_last_man_wins():
    game = _make_game(players=4)
    # 让3个人出局，只剩1000
    game.dead = [1001, 1002, 1003]
    game.chamber = [False, False, False, False, False, True]
    game.chamber_pos = 5
    result = pressure_roulette_service.shoot_self(CH, 1000)
    # 1000中弹了，但没人存活 → 平局
    assert result["game_over"] is True
    assert result.get("winner") is None  # 平局


def test_winner_when_others_dead():
    game = _make_game(players=4)
    game.dead = [1000, 1002, 1003]
    # 当前玩家是1001（因为1000死了）
    game.current_index = 1
    # 子弹打光（传枪把最后一发转过去）
    game.chamber = [False, False, False, False, False, True]
    game.chamber_pos = 5
    result = pressure_roulette_service.pass_gun(CH, 1001)
    assert result["game_over"] is True
    assert result["winner"] == 1001  # 唯一存活者


# ==================== 超时 ====================


def test_timeout_shoot_auto_fires():
    game = _make_game(players=4)
    result = pressure_roulette_service.timeout_shoot(CH)
    assert result["action"] == "shoot"
    assert game.chamber_pos == 1


# ==================== 结算 ====================


def test_settle_winner_takes_pool(coin):
    coin.balances[1000] = 1000
    coin.balances[1001] = 1000
    coin.balances[1002] = 1000
    coin.balances[1003] = 1000
    coin.fail_remove_users = {1003}  # 1003 扣款会失败

    game = _make_game(players=4, bet=50)
    game.dead = [1001, 1002, 1003]
    game.winner = 1000
    game.phase = "ended"
    game.settled = False

    result = run(pressure_roulette_service.settle(game))
    assert result["winner"] == 1000
    assert result["pool"] == 100  # 2个败方成功扣款，每人50
    assert result["share"] == 100
    assert 1003 in result["deduct_failed"]
    assert coin.balances[1000] == 1100  # 1000 + 100
    assert coin.balances[1001] == 950
    assert coin.balances[1002] == 950
    assert coin.balances[1003] == 1000  # 扣款失败，余额不变


def test_settle_draw_no_deduction(coin):
    coin.balances[1000] = 1000
    coin.balances[1001] = 1000

    game = _make_game(players=4, bet=50)
    game.winner = None  # 平局
    game.phase = "ended"
    game.settled = False

    result = run(pressure_roulette_service.settle(game))
    assert result["winner"] is None
    assert result["pool"] == 0
    assert coin.balances[1000] == 1000  # 没扣
    assert coin.balances[1001] == 1000


def test_settle_idempotent(coin):
    game = _make_game(players=4, bet=50)
    game.winner = 1000
    game.phase = "ended"
    game.settled = True  # 已结算

    result = run(pressure_roulette_service.settle(game))
    assert "error" in result


# ==================== 弹巢可视化 ====================


def test_chamber_display():
    game = _make_game(players=4)
    game.chamber = [False, True, False, False, False, False]
    game.chamber_pos = 2
    display = game.chamber_display()
    assert display == ["空", "砰", "?", "?", "?", "?"]


def test_remaining_live_and_chamber():
    game = _make_game(players=4)
    game.chamber = [False, False, True, False, False, False]
    game.chamber_pos = 2
    assert game.remaining_live() == 1
    assert game.remaining_chamber() == 4


# ==================== current_player ====================


def test_current_player_skips_dead():
    game = _make_game(players=4)
    game.dead = [1000]
    # current_index=0 指向1000，但1000死了，应跳到1001
    game.current_index = 0
    assert game.current_player() == 1001


def test_current_player_none_when_all_dead():
    game = _make_game(players=4)
    game.dead = list(game.players)
    assert game.current_player() is None
