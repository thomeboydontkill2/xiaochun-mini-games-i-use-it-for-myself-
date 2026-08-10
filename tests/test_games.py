# -*- coding: utf-8 -*-
import asyncio

import pytest

from games.config.games_config import ROULETTE_MULTIPLIERS, LIFE_GAMBLE_DAILY_LIMIT
from games.services import betting
from games.services.roulette_service import RouletteService
from games.services.bomb_service import BombService
from games.services.duel_service import DuelService
from games.services.undercover_service import UndercoverService


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------- 平衡 ----------

def test_roulette_ev_is_house_edge():
    total = sum(ROULETTE_MULTIPLIERS.values())
    ev = sum((0 if m == -2 else m) * w for m, w in ROULETTE_MULTIPLIERS.items()) / total
    assert -0.20 <= ev <= -0.05, f"轮盘期望值 {ev:+.2%} 不在庄家优势区间"


# ---------- betting ----------

def test_transfer_refunds_on_credit_failure(coin):
    coin.balances = {1: 100, 2: 0}
    coin.fail_add_users = {2}
    ok = run(betting.transfer(1, 2, 50, "测试"))
    assert not ok
    assert coin.balances[1] == 100  # 已退款
    assert coin.balances[2] == 0


def test_transfer_aborts_on_deduct_failure(coin):
    coin.balances = {1: 100, 2: 0}
    coin.fail_remove = True
    ok = run(betting.transfer(1, 2, 50, "测试"))
    assert not ok
    assert coin.balances[2] == 0


def test_life_reward_daily_limit(coin):
    total = 0
    for _ in range(LIFE_GAMBLE_DAILY_LIMIT + 3):
        total += 1 if run(betting.grant_life_reward(9, "测试")) > 0 else 0
    assert total == LIFE_GAMBLE_DAILY_LIMIT


# ---------- 轮盘 ----------

def test_roulette_double_spin_rejected():
    svc = RouletteService()
    svc.start_game(1, 100)
    r1 = svc.spin(1)
    assert "error" not in r1
    r2 = svc.spin(1)
    assert "error" in r2


def test_roulette_net_settlement_single_call(coin):
    svc = RouletteService()
    coin.balances = {1: 1000}
    svc.start_game(1, 100)
    result = {"multiplier": 2, "bet": 100, "net": 200, "is_mute": False}
    final = run(svc.settle(1, result, False, True))
    assert final["coins_change"] == 200
    assert coin.balances[1] == 1200
    assert len([e for e in coin.log if e[1] == 1]) == 1  # 单笔净额
    assert not svc.is_playing(1)


def test_roulette_loss_is_partial(coin):
    svc = RouletteService()
    coin.balances = {1: 1000}
    svc.start_game(1, 100)
    result = {"multiplier": -0.5, "bet": 100, "net": -50, "is_mute": False}
    final = run(svc.settle(1, result, False, True))
    assert final["loss"] == 50
    assert coin.balances[1] == 950


# ---------- 传炸弹（指向性传递） ----------

def _bomb_with_players(players):
    svc = BombService()
    svc.create_game(42, 100)
    for p in players:
        svc.add_player(42, p, False, True)
    ok, _, game = svc.start_game(42)
    assert ok
    game.timer = 9999  # 测试传递逻辑时不让它炸
    game.holder_since = 0  # 绕过防手快
    return svc, game


def test_bomb_pass_to_chosen_target():
    svc, game = _bomb_with_players([1, 2, 3])
    holder = game.holder_id
    target = [p for p in [1, 2, 3] if p != holder][1]
    r = svc.pass_bomb(42, holder, target)
    assert r["passed"] and r["new_holder"] == target
    assert game.holder_id == target


def test_bomb_cannot_pass_to_self_or_outsider():
    svc, game = _bomb_with_players([1, 2, 3])
    holder = game.holder_id
    assert "error" in svc.pass_bomb(42, holder, holder)
    assert "error" in svc.pass_bomb(42, holder, 999)


def test_bomb_only_holder_can_pass():
    svc, game = _bomb_with_players([1, 2, 3])
    not_holder = [p for p in [1, 2, 3] if p != game.holder_id][0]
    other = [p for p in [1, 2, 3] if p not in (not_holder, game.holder_id)][0]
    assert "error" in svc.pass_bomb(42, not_holder, other)


def test_bomb_explodes_when_fuse_runs_out():
    svc, game = _bomb_with_players([1, 2])
    game.timer = 1
    holder = game.holder_id
    target = [p for p in [1, 2] if p != holder][0]
    r = svc.pass_bomb(42, holder, target)
    assert r["exploded"] and r["loser_id"] == holder
    assert not svc.has_game(42)


def test_bomb_timeout_explodes_on_holder():
    svc, game = _bomb_with_players([1, 2, 3])
    r = svc.timeout_explode(42, game.holder_id)
    assert r["exploded"] and r["timed_out"] and r["loser_id"] == game.holder_id


def test_bomb_coin_settle_no_credit_on_deduct_failure(coin):
    svc, game = _bomb_with_players([1, 2, 3])
    coin.fail_remove = True
    r = svc.timeout_explode(42, game.holder_id)
    settle = run(svc.settle(r["game"], r["loser_id"]))
    assert settle["settle_failed"]
    assert all(e[0] != "add" for e in coin.log)  # 没派彩


def test_bomb_life_reward_requires_enough_passes(coin, guard):
    svc = BombService()
    svc.create_game(42, 100)
    svc.add_player(42, 1, True, False)
    svc.add_player(42, 2, True, False)
    ok, _, game = svc.start_game(42)
    game.timer = 1
    game.holder_since = 0  # 绕过防手快
    holder = game.holder_id
    target = 2 if holder == 1 else 1
    r = svc.pass_bomb(42, holder, target)  # 第1次传递就炸（< 2人*1）
    settle = run(svc.settle(r["game"], r["loser_id"]))
    assert settle["reward_skipped"]
    assert settle["survivor_reward"] == 0
    assert guard.mutes  # 输家仍被禁言


# ---------- 死斗 ----------

def test_duel_full_flow_and_transfer(coin):
    svc = DuelService()
    coin.balances = {1: 500, 2: 500}
    game = svc.create_game(1, 2, 100, False, False, True, True)
    assert game
    assert svc.create_game(1, 3, 100, False, False, True, True) is None  # 已在局中

    svc.submit_choice(1, "石头")
    r = svc.submit_choice(2, "剪刀")
    assert r["round_result"]["winner_id"] == 1
    svc.submit_choice(1, "布")
    r = svc.submit_choice(2, "石头")
    assert r["game_over"] and r["winner_id"] == 1

    settle = run(svc.settle(game, 1, 2))
    assert settle["mode"] == "coin_vs_coin" and not settle["settle_failed"]
    assert coin.balances == {1: 600, 2: 400}
    # 幂等
    again = run(svc.settle(game, 1, 2))
    assert "error" in again


def test_duel_double_submit_rejected():
    svc = DuelService()
    svc.create_game(1, 2, 100, False, False, True, True)
    assert "waiting" in svc.submit_choice(1, "石头")
    assert "error" in svc.submit_choice(1, "布")


def test_duel_tie_replays_round():
    svc = DuelService()
    game = svc.create_game(1, 2, 100, False, False, True, True)
    svc.submit_choice(1, "石头")
    r = svc.submit_choice(2, "石头")
    assert r["round_result"]["tie"] and not r["game_over"]
    assert game.round == 1 and game.p1_choice is None


def test_duel_settle_failure_rolls_back(coin):
    svc = DuelService()
    coin.balances = {1: 500, 2: 500}
    game = svc.create_game(1, 2, 100, False, False, True, True)
    coin.fail_add_users = {1}
    settle = run(svc.settle(game, 1, 2))
    assert settle["settle_failed"]
    assert coin.balances == {1: 500, 2: 500}  # 已回滚


# ---------- 谁是卧底 ----------

def _uc_game(players=(1, 2, 3, 4)):
    svc = UndercoverService()
    svc.create_game(7, 100, min_players=4, max_players=10)
    for p in players:
        svc.add_player(7, p, False, True)
    ok, msg, game = svc.start_game(7)
    assert ok, msg
    return svc, game


def test_undercover_start_without_game_returns_tuple():
    svc = UndercoverService()
    ok, msg, game = svc.start_game(999)  # 旧版这里直接崩
    assert not ok and game is None


def test_undercover_word_leak_rejected():
    svc, game = _uc_game()
    word = game.player_words[1]
    r = svc.submit_desc_anytime(7, 1, f"我的词是{word}")
    assert "error" in r


def test_undercover_tie_no_elimination():
    svc, game = _uc_game()
    for p in game.players:
        svc.submit_desc_anytime(7, p, f"描述{p}")
    svc.enter_voting(game)
    # 2 票投 A，2 票投 B → 平票
    a, b, c, d = game.players
    svc.submit_vote(7, a, b)
    svc.submit_vote(7, b, a)
    svc.submit_vote(7, c, a)
    r = svc.submit_vote(7, d, b)
    assert r["vote_done"] and r["tie"] and r["eliminated"] is None
    assert game.eliminated == []


def test_undercover_force_tally_on_timeout():
    svc, game = _uc_game()
    svc.enter_voting(game)
    a, b = game.players[0], game.players[1]
    svc.submit_vote(7, a, b)
    r = svc.force_tally(7)
    assert r["vote_done"] and r["forced"]


def test_undercover_vote_validation():
    svc, game = _uc_game()
    svc.enter_voting(game)
    a = game.players[0]
    assert "error" in svc.submit_vote(7, a, a)        # 投自己
    assert "error" in svc.submit_vote(7, a, 999)      # 目标无效
    svc.submit_vote(7, a, game.players[1])
    assert "error" in svc.submit_vote(7, a, game.players[2])  # 重复投


def test_undercover_civilian_win_settlement(coin, guard):
    svc, game = _uc_game()
    coin.balances = {p: 500 for p in game.players}
    settle = run(svc.settle(game, "civilian"))
    assert settle["winner_side"] == "civilian"
    uc = game.undercover_ids[0]
    assert coin.balances[uc] == 400  # 卧底输 100
    civ_total = sum(coin.balances[p] - 500 for p in game.players if p != uc)
    assert civ_total <= 100  # 平分不超发（整除余数不补）
    # 幂等
    assert "error" in run(svc.settle(game, "civilian"))
