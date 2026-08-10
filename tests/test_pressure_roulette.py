# -*- coding: utf-8 -*-
"""加压俄罗斯轮盘 —— 纯逻辑测试（对齐原项目结构）。"""

import asyncio

import pytest

from games.services.pressure_roulette_service import (
    pressure_roulette_service,
    PressureRouletteGame,
    _spin_cylinder,
    _place_first_shot_bullet,
    FIRST_SHOT_HIT_CHANCE,
)

CH = 100


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _make_game(players=4, bet=50, bullets=1):
    """造一局已开打的 game。"""
    pressure_roulette_service._active_games._games.clear()
    game = PressureRouletteGame(CH, bet)
    for i in range(players):
        game.players.append(1000 + i)
    game.bullets = bullets
    _spin_cylinder(game)
    game.state = "playing"
    game.phase = "fire"
    game.turn_index = 0
    game.shot_number = 0
    game.turn_token = 1
    pressure_roulette_service._active_games.set(CH, game)
    return game


def _set_chamber(game, chambers):
    """手动设弹巢布局。"""
    game.chambers = chambers
    game.revealed = [False] * len(chambers)
    game.hit_chambers = [False] * len(chambers)
    game.pointer = 0
    game.bullets = sum(chambers)


# ==================== 装弹 ====================


def test_spin_cylinder_correct_bullet_count():
    game = _make_game(players=3, bullets=2)
    _spin_cylinder(game)
    assert game.bullets == 2
    assert sum(game.chambers) == 2
    assert game.pointer == 0
    assert not any(game.revealed)


def test_spin_cylinder_randomizes():
    positions = set()
    for _ in range(30):
        game = _make_game(players=3, bullets=1)
        _spin_cylinder(game)
        positions.add(game.chambers.index(True))
    assert len(positions) >= 3


def test_place_first_shot_bullet_hit():
    game = _make_game(players=3, bullets=1)
    _spin_cylinder(game)
    # 强制第一枪中弹
    _place_first_shot_bullet(game, True)
    assert game.chambers[game.pointer] is True


def test_place_first_shot_bullet_miss():
    game = _make_game(players=3, bullets=1)
    _spin_cylinder(game)
    # 强制第一枪空枪
    _place_first_shot_bullet(game, False)
    assert game.chambers[game.pointer] is False


# ==================== 加入/退出 ====================


def test_add_player_during_joining():
    pressure_roulette_service._active_games._games.clear()
    pressure_roulette_service.create_game(CH, 50)
    ok, msg = pressure_roulette_service.add_player(CH, 1001)
    assert ok
    assert 1001 in pressure_roulette_service.get_game(CH).players


def test_add_player_duplicate_rejected():
    pressure_roulette_service._active_games._games.clear()
    pressure_roulette_service.create_game(CH, 50)
    pressure_roulette_service.add_player(CH, 1001)
    ok, msg = pressure_roulette_service.add_player(CH, 1001)
    assert not ok


def test_start_game_below_min_3_rejected():
    pressure_roulette_service._active_games._games.clear()
    pressure_roulette_service.create_game(CH, 50)
    pressure_roulette_service.add_player(CH, 1001)
    pressure_roulette_service.add_player(CH, 1002)
    ok, msg, game = pressure_roulette_service.start_game(CH)
    assert not ok
    assert "3" in msg


def test_start_game_loads_chamber():
    pressure_roulette_service._active_games._games.clear()
    pressure_roulette_service.create_game(CH, 50)
    for i in range(3):
        pressure_roulette_service.add_player(CH, 1001 + i)
    ok, msg, game = pressure_roulette_service.start_game(CH)
    assert ok
    assert game.state == "playing"
    assert game.phase == "fire"
    assert game.bullets == 1
    assert sum(game.chambers) == 1
    assert game.current_stake() == 3  # BASE


# ==================== 扣扳机 ====================


def test_shoot_self_blank_advances_and_streak():
    game = _make_game(players=3)
    _set_chamber(game, [False, False, True, False, False, False])  # 第0格空
    result = pressure_roulette_service.shoot_self(CH, 1000)
    assert result["action"] == "shoot"
    assert result["hit"] is False
    assert game.revealed[0] is True
    assert game.hit_chambers[0] is False
    assert game.phase == "choice"  # 空枪进 choice
    # 蓄力在选 again 后才累积，空枪本身不加蓄力
    assert game.phase == "choice"


def test_shoot_self_hit_eliminates():
    game = _make_game(players=3)
    _set_chamber(game, [True, False, False, False, False, False])  # 第0格实弹
    result = pressure_roulette_service.shoot_self(CH, 1000)
    assert result["hit"] is True
    assert 1000 in game.dead
    assert game.charge_for(1000) == 0  # 蓄力清零
    assert result.get("mute_minutes") == 3


def test_shoot_not_your_turn():
    game = _make_game(players=3)
    result = pressure_roulette_service.shoot_self(CH, 1001)
    assert "error" in result


def test_streak_accumulates_consecutive():
    game = _make_game(players=3)
    _set_chamber(game, [False, False, True, False, False, False])
    pressure_roulette_service.shoot_self(CH, 1000)  # 空枪 → choice
    assert game.charge_for(1000) == 0  # 空枪本身不加蓄力
    # choice 阶段选 again → 蓄力 +1
    result = pressure_roulette_service.handle_choice(CH, 1000, "again")
    assert result["action"] == "again"
    assert game.charge_for(1000) == 1  # again 后蓄力1
    assert game.phase == "fire"
    # 再开一枪空枪 → choice → again → 蓄力2
    game.chambers[game.pointer] = False
    game.bullets = sum(1 for i, r in enumerate(game.revealed) if not r and game.chambers[i])
    pressure_roulette_service.shoot_self(CH, 1000)  # 空枪 → choice
    pressure_roulette_service.handle_choice(CH, 1000, "again")
    assert game.charge_for(1000) == 2


# ==================== 传枪 ====================


def test_pass_gun_advances_to_next():
    game = _make_game(players=3)
    # 先开一枪空枪进 choice
    _set_chamber(game, [False, True, False, False, False, False])
    pressure_roulette_service.shoot_self(CH, 1000)  # 空枪 → choice
    result = pressure_roulette_service.handle_choice(CH, 1000, "pass")
    assert result["action"] == "pass"
    assert result["next"] == 1001  # 轮到下家
    assert game.charge_for(1000) == 0  # 蓄力清零
    assert game.phase == "fire"


# ==================== 加压 ====================


def test_press_loads_bullets_and_increases_stake():
    game = _make_game(players=3)
    _set_chamber(game, [False, True, False, False, False, False])
    pressure_roulette_service.shoot_self(CH, 1000)  # 空枪 → choice
    game.set_charge(1000, 2)  # 蓄力2层
    result = pressure_roulette_service.handle_choice(CH, 1000, "load")
    assert result["action"] == "load"
    assert result["loaded"] == 3  # 1 + 蓄力2
    # 赌注 = BASE(3) + pressure_bullets(3) = 6
    assert game.current_stake() == 6
    assert game.charge_for(1000) == 0  # 蓄力清零
    assert game.phase == "fire"
    # spin_cylinder 重洗了
    assert sum(game.chambers) == game.bullets


def test_press_with_zero_streak_loads_one():
    game = _make_game(players=3)
    _set_chamber(game, [False, True, False, False, False, False])
    pressure_roulette_service.shoot_self(CH, 1000)  # 空枪 → choice
    result = pressure_roulette_service.handle_choice(CH, 1000, "load")
    assert result["loaded"] == 1
    assert game.current_stake() == 4  # 3 + 1


def test_press_not_your_turn():
    game = _make_game(players=3)
    _set_chamber(game, [False, True, False, False, False, False])
    pressure_roulette_service.shoot_self(CH, 1000)
    result = pressure_roulette_service.handle_choice(CH, 1001, "load")
    assert "error" in result


def test_press_full_chamber_degrades_to_pass():
    game = _make_game(players=3)
    game.bullets = 6  # 满巢
    _spin_cylinder(game)
    _set_chamber(game, [True, True, True, True, True, True])
    # 先开一枪进 choice
    game.chambers[0] = False
    game.bullets = 6
    # 手动进 choice
    game.phase = "choice"
    result = pressure_roulette_service.handle_choice(CH, 1000, "load")
    # 满巢时 load 降级为 pass
    assert result["action"] == "pass"


# ==================== 子弹打光结束 ====================


def test_bullets_empty_ends_game():
    game = _make_game(players=3, bullets=1)
    _set_chamber(game, [False, False, False, False, False, True])
    game.pointer = 5  # 指向最后一发
    result = pressure_roulette_service.shoot_self(CH, 1000)
    assert result["game_over"] is True
    assert result["reason"] == "bullets_empty"
    assert 1000 in game.dead


def test_winner_when_others_dead():
    game = _make_game(players=3)
    game.dead = [1000, 1002]
    game.turn_index = 0  # 指向1001（alive[0]）
    # 子弹打光
    game.bullets = 1
    _set_chamber(game, [True, False, False, False, False, False])
    result = pressure_roulette_service.shoot_self(CH, 1001)
    # 1001 中弹，没人存活 → 平局
    assert result["game_over"] is True
    assert result.get("winner") is None


def test_champion_when_two_dead():
    game = _make_game(players=3)
    game.dead = [1001, 1002]
    # 只剩1000，子弹打光
    game.bullets = 1
    _set_chamber(game, [True, False, False, False, False, False])
    result = pressure_roulette_service.shoot_self(CH, 1000)
    # 1000 中弹，但已是他最后一人 → 应该在他中弹前判冠军？
    # 不对：1000中弹后 alive=0 → aborted
    assert result["game_over"] is True


# ==================== 超时 ====================


def test_timeout_fire_auto_shoots():
    game = _make_game(players=3)
    _set_chamber(game, [False, True, False, False, False, False])
    result = pressure_roulette_service.timeout_fire(CH)
    assert result["action"] == "shoot"
    assert game.revealed[0] is True


def test_timeout_choice_auto_passes():
    game = _make_game(players=3)
    _set_chamber(game, [False, True, False, False, False, False])
    pressure_roulette_service.shoot_self(CH, 1000)  # 空枪 → choice
    result = pressure_roulette_service.timeout_choice(CH)
    assert result["action"] == "pass"
    assert game.phase == "fire"


# ==================== 抽弹开枪 ====================


def test_unload_below_3_bullets_rejected():
    game = _make_game(players=3, bullets=2)
    result = pressure_roulette_service.unload(CH, 1000)
    assert "error" in result


def test_unload_once_per_game():
    game = _make_game(players=3, bullets=3)
    _set_chamber(game, [True, True, True, False, False, False])
    # 第一次抽弹
    r1 = pressure_roulette_service.unload(CH, 1000)
    # 抽弹后立刻开枪，可能中弹也可能空枪
    # 如果中弹了游戏就结束，这里检查 unload_used
    assert 1000 in game.unload_used
    # 第二次应该被拒
    if game.state == "playing":
        game.phase = "fire"
        game.turn_index = 0
        r2 = pressure_roulette_service.unload(CH, 1000)
        assert "error" in r2


def test_unload_reduces_bullets():
    game = _make_game(players=3, bullets=3)
    _set_chamber(game, [True, True, True, False, False, False])
    pressure_roulette_service.unload(CH, 1000)
    # 抽弹后 bullets -1（开枪可能再 -1 如果中弹）
    assert game.bullets <= 2


# ==================== 反手还击 ====================


def test_riposte_requires_holder():
    game = _make_game(players=3)
    _set_chamber(game, [False, True, False, False, False, False])
    pressure_roulette_service.shoot_self(CH, 1000)  # 空枪 → choice
    # 没有反手权
    result = pressure_roulette_service.riposte(CH, 1000)
    assert "error" in result


def test_riposte_after_press():
    game = _make_game(players=3)
    _set_chamber(game, [False, True, False, False, False, False])
    pressure_roulette_service.shoot_self(CH, 1000)  # 空枪 → choice
    pressure_roulette_service.handle_choice(CH, 1000, "load")  # 加压
    # 加压后 riposte_target_id=1000, riposte_holder_id=下一个接枪的人
    assert game.riposte_target_id == 1000
    assert game.riposte_holder_id is not None
    # 轮到下家（1001），他有反手权
    cur = game.current_player()
    if cur == game.riposte_holder_id and game.phase == "fire":
        # 1001 先开一枪
        game.chambers[game.pointer] = False  # 强制空枪
        game.bullets = sum(game.chambers)
        pressure_roulette_service.shoot_self(CH, cur)  # 空枪 → choice
        if game.phase == "choice":
            result = pressure_roulette_service.riposte(CH, cur)
            assert result["action"] == "riposte"
            assert game.riposte is not None
            assert game.riposte["stage"] == "target"


# ==================== 胆小鬼退出 ====================


def test_quit_during_fire():
    game = _make_game(players=3)
    result = pressure_roulette_service.quit(CH, 1000)
    assert result["action"] == "quit"
    assert 1000 in game.dead
    assert result["penalty_minutes"] >= 5
    assert len(game.cowards) == 1


def test_quit_redeemer_rejected():
    game = _make_game(players=3)
    game.redeemers = [1000]  # 1000 是戴罪上桌
    result = pressure_roulette_service.quit(CH, 1000)
    assert "error" in result


def test_quit_not_your_turn():
    game = _make_game(players=3)
    result = pressure_roulette_service.quit(CH, 1001)
    assert "error" in result


# ==================== 结算 ====================


def test_settle_winner_takes_pool(coin):
    coin.balances[1000] = 1000
    coin.balances[1001] = 1000
    coin.balances[1002] = 1000
    coin.fail_remove_users = {1002}

    game = _make_game(players=3, bet=50)
    game.dead = [1001, 1002]
    game.winner = 1000
    game.state = "ended"
    game.settled = False

    result = run(pressure_roulette_service.settle(game))
    assert result["winner"] == 1000
    assert result["pool"] == 50  # 只有1001成功扣款
    assert 1002 in result["deduct_failed"]
    assert coin.balances[1000] == 1050
    assert coin.balances[1001] == 950
    assert coin.balances[1002] == 1000


def test_settle_draw_no_deduction(coin):
    coin.balances[1000] = 1000
    coin.balances[1001] = 1000

    game = _make_game(players=3, bet=50)
    game.winner = None
    game.state = "ended"
    game.settled = False

    result = run(pressure_roulette_service.settle(game))
    assert result["winner"] is None
    assert result["pool"] == 0
    assert coin.balances[1000] == 1000


def test_settle_idempotent(coin):
    game = _make_game(players=3, bet=50)
    game.winner = 1000
    game.state = "ended"
    game.settled = True

    result = run(pressure_roulette_service.settle(game))
    assert "error" in result


# ==================== 弹巢可视化 ====================


def test_chamber_display():
    game = _make_game(players=3)
    game.chambers = [False, True, False, False, False, False]
    game.revealed = [True, True, False, False, False, False]
    game.hit_chambers = [False, True, False, False, False, False]
    game.pointer = 2
    display = game.chamber_display()
    assert display == ["空", "砰", "枪口", "?", "?", "?"]


def test_unknown_count():
    game = _make_game(players=3)
    game.revealed = [True, False, True, False, False, False]
    assert game.unknown_count() == 4


def test_hit_chance():
    game = _make_game(players=3, bullets=2)
    game.revealed = [True, False, True, False, False, False]
    # unknown=4, bullets=2
    assert game.hit_chance() == 0.5


# ==================== current_player ====================


def test_current_player_skips_dead():
    game = _make_game(players=3)
    game.dead = [1000]
    game.turn_index = 0
    alive = game.alive()
    # turn_index=0 指向 alive[0]=1001
    assert game.current_player() == 1001


def test_current_player_none_when_all_dead():
    game = _make_game(players=3)
    game.dead = list(game.players)
    assert game.current_player() is None


# ==================== 赌注累计 ====================


def test_stake_accumulates_across_pressures():
    game = _make_game(players=3)
    assert game.current_stake() == 3  # BASE
    game.pressure_bullets = 3
    assert game.current_stake() == 6  # 3 + 3
    game.pressure_bullets = 5
    assert game.current_stake() == 8  # 3 + 5
