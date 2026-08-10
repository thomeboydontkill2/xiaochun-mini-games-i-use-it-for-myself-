# -*- coding: utf-8 -*-
import asyncio

import pytest

from games.config.games_config import (
    WEREWOLF_ROLE_TABLE, WEREWOLF_MIN_PLAYERS, WEREWOLF_MAX_PLAYERS,
    ROLE_WOLF, ROLE_VILLAGER, ROLE_SEER, ROLE_WITCH, ROLE_HUNTER, ROLE_GUARD, ROLE_IDIOT,
)
from games.services.werewolf_service import WerewolfService, assign_roles

CH = 777


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def make_game(n=9, roles=None, bet=50):
    """建一局并强制指定身份，方便测规则。roles: {index: role}，index 从 0 起对应玩家 id 1..n"""
    svc = WerewolfService()
    svc.create_game(CH, bet)
    for i in range(1, n + 1):
        svc.add_player(CH, i)
    ok, msg, game = svc.start_game(CH)
    assert ok, msg
    if roles:
        game.roles = {i + 1: roles[i] for i in range(n)}
        game.guard_last_target = None
        svc._begin_night(game)
    return svc, game


# ---------- 角色配比 ----------

def test_role_table_covers_all_player_counts():
    for n in range(WEREWOLF_MIN_PLAYERS, WEREWOLF_MAX_PLAYERS + 1):
        assert n in WEREWOLF_ROLE_TABLE, f"{n} 人没有板子配置"


def test_assign_roles_never_overflows_and_fills_villagers():
    for n in range(WEREWOLF_MIN_PLAYERS, WEREWOLF_MAX_PLAYERS + 1):
        roles = assign_roles(n)
        assert len(roles) == n
        assert roles.count(ROLE_WOLF) >= 2
        assert roles.count(ROLE_WOLF) < n - roles.count(ROLE_WOLF)
        for special in (ROLE_SEER, ROLE_WITCH, ROLE_HUNTER, ROLE_GUARD, ROLE_IDIOT):
            assert roles.count(special) <= 1


def test_role_table_unlocks_by_player_count():
    assert ROLE_HUNTER not in assign_roles(6)
    assert ROLE_HUNTER in assign_roles(8)
    assert ROLE_GUARD in assign_roles(10)
    assert ROLE_IDIOT in assign_roles(12)
    assert assign_roles(6).count(ROLE_WOLF) == 2
    assert assign_roles(10).count(ROLE_WOLF) == 3
    assert assign_roles(12).count(ROLE_WOLF) == 4


def test_cannot_start_below_minimum():
    svc = WerewolfService()
    svc.create_game(CH, 50)
    for i in range(1, WEREWOLF_MIN_PLAYERS):
        svc.add_player(CH, i)
    ok, msg, game = svc.start_game(CH)
    assert not ok and game is None


def test_join_rules():
    svc = WerewolfService()
    svc.create_game(CH, 50)
    assert svc.add_player(CH, 1)[0]
    assert not svc.add_player(CH, 1)[0]          # 重复加入
    assert svc.leave_player(CH, 1)[0]
    assert not svc.leave_player(CH, 1)[0]        # 已经不在了


# ---------- 夜晚：阶段跳过 ----------

def test_night_skips_absent_roles():
    _, game = make_game(6)
    assert game.phase == "night_wolf"


def test_night_starts_with_guard_when_present():
    _, game = make_game(10)
    assert game.phase == "night_guard"


# ---------- 夜晚：守卫 ----------

def _std_roles():
    """10 人：1,2,3=狼，4=预言家，5=女巫，6=猎人，7=守卫，其余平民"""
    return [ROLE_WOLF, ROLE_WOLF, ROLE_WOLF, ROLE_SEER, ROLE_WITCH,
            ROLE_HUNTER, ROLE_GUARD, ROLE_VILLAGER, ROLE_VILLAGER, ROLE_VILLAGER]


def test_guard_cannot_protect_same_target_twice():
    svc, game = make_game(10, _std_roles())
    assert svc.guard_protect(CH, 7, 8).get("ok")
    assert game.phase == "night_wolf"
    svc._begin_night(game)
    assert "error" in svc.guard_protect(CH, 7, 8)
    assert svc.guard_protect(CH, 7, 9).get("ok")


def test_guard_rejects_non_guard_and_dead_target():
    svc, game = make_game(10, _std_roles())
    assert "error" in svc.guard_protect(CH, 8, 9)
    assert "error" in svc.guard_protect(CH, 7, 99)


# ---------- 夜晚：狼刀 ----------

def test_wolf_votes_need_all_wolves_then_majority_wins():
    svc, game = make_game(10, _std_roles())
    svc.guard_protect(CH, 7, 9)
    r1 = svc.wolf_vote(CH, 1, 8)
    assert r1["all_done"] is False and r1["needed"] == 3
    svc.wolf_vote(CH, 2, 8)
    r3 = svc.wolf_vote(CH, 3, 10)
    assert r3["all_done"] and r3["target"] == 8
    assert game.phase == "night_witch"


def test_wolf_cannot_kill_teammate_or_vote_twice():
    svc, game = make_game(10, _std_roles())
    svc.guard_protect(CH, 7, 9)
    assert "error" in svc.wolf_vote(CH, 1, 2)
    assert svc.wolf_vote(CH, 1, 8)["ok"]
    assert "error" in svc.wolf_vote(CH, 1, 10)


def test_wolf_tie_picks_one_of_the_tied():
    svc, game = make_game(10, _std_roles())
    svc.guard_protect(CH, 7, 9)
    svc.wolf_vote(CH, 1, 8)
    svc.wolf_vote(CH, 2, 10)
    r = svc.wolf_vote(CH, 3, 4)
    assert r["target"] in (8, 10, 4)


def test_skip_wolf_phase_uses_existing_votes():
    svc, game = make_game(10, _std_roles())
    svc.guard_protect(CH, 7, 9)
    svc.wolf_vote(CH, 1, 8)
    svc.skip_night_phase(CH, "night_wolf")
    assert game.night["wolf_target"] == 8
    assert game.phase == "night_witch"


# ---------- 夜晚：女巫 / 预言家 / 结算 ----------

def _to_witch(svc, game, kill_target, guard_target=None):
    if game.phase == "night_guard":
        if guard_target is None:
            svc.skip_night_phase(CH, "night_guard")
        else:
            svc.guard_protect(CH, 7, guard_target)
    for wolf in game.holders(ROLE_WOLF):
        svc.wolf_vote(CH, wolf, kill_target)
    assert game.phase == "night_witch"


def test_night_kill_without_protection():
    svc, game = make_game(10, _std_roles())
    _to_witch(svc, game, 8)
    svc.witch_action(CH, 5, "none")
    svc.seer_check(CH, 4, 1)
    result = svc.resolve_night(CH)
    assert result["deaths"] == [8] and result["causes"][8] == "wolf"


def test_guard_alone_saves_target():
    svc, game = make_game(10, _std_roles())
    _to_witch(svc, game, 8, guard_target=8)
    svc.witch_action(CH, 5, "none")
    svc.seer_check(CH, 4, 1)
    assert svc.resolve_night(CH)["deaths"] == []


def test_witch_antidote_alone_saves_target():
    svc, game = make_game(10, _std_roles())
    _to_witch(svc, game, 8)
    assert svc.witch_action(CH, 5, "save")["ok"]
    svc.seer_check(CH, 4, 1)
    assert svc.resolve_night(CH)["deaths"] == []
    assert game.witch_antidote_used


def test_guard_plus_witch_still_kills():
    """同守同救判死（WEREWOLF_SAVE_AND_GUARD_KILLS=True）"""
    svc, game = make_game(10, _std_roles())
    _to_witch(svc, game, 8, guard_target=8)
    svc.witch_action(CH, 5, "save")
    svc.seer_check(CH, 4, 1)
    assert svc.resolve_night(CH)["deaths"] == [8]


def test_witch_antidote_and_poison_single_use():
    svc, game = make_game(10, _std_roles())
    _to_witch(svc, game, 8)
    svc.witch_action(CH, 5, "save")
    svc.seer_check(CH, 4, 1)
    svc.resolve_night(CH)
    svc.begin_discussion(game)
    svc.enter_voting(game)
    svc._next_day(game)
    _to_witch(svc, game, 9, guard_target=10)
    assert "error" in svc.witch_action(CH, 5, "save")
    assert svc.witch_action(CH, 5, "poison", 1)["ok"]
    svc.seer_check(CH, 4, 2)
    deaths = svc.resolve_night(CH)
    assert 9 in deaths["deaths"] and 1 in deaths["deaths"]
    assert deaths["causes"][1] == "poison"


def test_witch_cannot_poison_self_or_save_when_nobody_killed():
    svc, game = make_game(10, _std_roles())
    svc.guard_protect(CH, 7, 9)
    svc.skip_night_phase(CH, "night_wolf")
    assert "error" in svc.witch_action(CH, 5, "save")
    assert "error" in svc.witch_action(CH, 5, "poison", 5)


def test_seer_reports_camp():
    svc, game = make_game(10, _std_roles())
    _to_witch(svc, game, 8)
    svc.witch_action(CH, 5, "none")
    assert svc.seer_check(CH, 4, 1)["camp"] == "狼人"
    svc._begin_night(game)
    _to_witch(svc, game, 9)
    svc.witch_action(CH, 5, "none")
    assert svc.seer_check(CH, 4, 10)["camp"] == "好人"


# ---------- 猎人 ----------

def test_hunter_shoots_when_knifed():
    svc, game = make_game(10, _std_roles())
    _to_witch(svc, game, 6)
    svc.witch_action(CH, 5, "none")
    svc.seer_check(CH, 4, 1)
    result = svc.resolve_night(CH)
    assert result["pending_hunter"] == 6 and game.phase == "day_hunter"
    shot = svc.hunter_shoot(CH, 6, 1)
    assert shot["shot"] == 1 and 1 in game.dead
    assert game.phase == "day_discuss"


def test_hunter_poisoned_cannot_shoot():
    svc, game = make_game(10, _std_roles())
    _to_witch(svc, game, 8)
    svc.witch_action(CH, 5, "poison", 6)
    svc.seer_check(CH, 4, 1)
    result = svc.resolve_night(CH)
    assert 6 in result["deaths"]
    assert result.get("pending_hunter") is None
    assert game.phase == "day_discuss"        # 不给枪，直接进讨论


def test_hunter_can_hold_fire():
    svc, game = make_game(10, _std_roles())
    _to_witch(svc, game, 6)
    svc.witch_action(CH, 5, "none")
    svc.seer_check(CH, 4, 1)
    svc.resolve_night(CH)
    r = svc.hunter_shoot(CH, 6, None)
    assert r["shot"] is None and game.phase == "day_discuss"


# ---------- 白天：轮流发言 ----------

def _to_discussion(svc, game, kill_target=9):
    _to_witch(svc, game, kill_target)
    svc.witch_action(CH, 5, "none")
    svc.seer_check(CH, 4, 1)
    svc.resolve_night(CH)
    assert game.phase == "day_discuss"


def test_speech_rotates_in_order():
    svc, game = make_game(10, _std_roles())
    _to_discussion(svc, game)
    order = list(game.speak_order)
    assert set(order) == set(game.alive())
    first = svc.current_speaker(game)
    assert first == order[0]
    assert "error" in svc.submit_speech(CH, order[1], "抢麦")
    r = svc.submit_speech(CH, first, "我是好人")
    assert r["next_speaker"] == order[1] and r["index"] == 1


def test_speech_skip_on_timeout_advances():
    svc, game = make_game(10, _std_roles())
    _to_discussion(svc, game)
    first = svc.current_speaker(game)
    r = svc.skip_speech(CH, first)
    assert r["skipped"] and r["next_speaker"] == game.speak_order[1]


def test_all_speeches_done_enters_voting():
    svc, game = make_game(10, _std_roles())
    _to_discussion(svc, game)
    last = None
    for _ in range(len(game.speak_order)):
        speaker = svc.current_speaker(game)
        last = svc.submit_speech(CH, speaker, f"发言{speaker}")
    assert last["all_done"] and game.phase == "day_vote"
    assert len(game.speeches) == len(game.speak_order)


def test_empty_speech_rejected():
    svc, game = make_game(10, _std_roles())
    _to_discussion(svc, game)
    assert "error" in svc.submit_speech(CH, svc.current_speaker(game), "   ")


def test_speak_order_rotates_between_days():
    svc, game = make_game(10, _std_roles())
    _to_discussion(svc, game)
    day1_first = game.speak_order[0]
    game.day = 2
    svc.begin_discussion(game)
    assert game.speak_order[0] != day1_first


# ---------- 白天：投票 ----------

def _to_voting(svc, game):
    _to_discussion(svc, game)
    svc.enter_voting(game)


def test_vote_validation():
    svc, game = make_game(10, _std_roles())
    _to_voting(svc, game)
    voter = game.alive()[0]
    assert "error" in svc.submit_vote(CH, voter, voter)
    assert "error" in svc.submit_vote(CH, voter, 999)
    other = game.alive()[1]
    assert svc.submit_vote(CH, voter, other)["ok"]
    assert "error" in svc.submit_vote(CH, voter, game.alive()[2])


def test_vote_tie_eliminates_nobody_and_enters_night():
    svc, game = make_game(10, _std_roles())
    _to_voting(svc, game)
    alive = game.alive()
    a, b = alive[0], alive[1]
    svc.submit_vote(CH, alive[2], a)
    svc.submit_vote(CH, alive[3], a)
    svc.submit_vote(CH, alive[4], b)
    svc.submit_vote(CH, alive[5], b)
    r = svc.force_tally(CH)
    assert r["vote_done"] and r["tie"] and r["eliminated"] is None
    assert a not in game.dead and b not in game.dead
    assert game.phase.startswith("night_")


def test_force_tally_on_timeout():
    svc, game = make_game(10, _std_roles())
    _to_voting(svc, game)
    alive = game.alive()
    svc.submit_vote(CH, alive[0], alive[1])
    r = svc.force_tally(CH)
    assert r["vote_done"] and r["forced"] and r["eliminated"] == alive[1]


def test_idiot_revealed_not_eliminated_and_loses_vote():
    roles = _std_roles()
    roles[9] = ROLE_IDIOT
    svc, game = make_game(10, roles)
    _to_voting(svc, game)
    for voter in list(game.alive()):
        if voter == 10:
            continue
        svc.submit_vote(CH, voter, 10)
    if game.phase == "day_vote":
        svc.force_tally(CH)
    assert game.idiot_revealed
    assert 10 not in game.dead
    assert not game.can_vote(10)
    assert 10 not in game.voters()


def test_voted_hunter_gets_to_shoot():
    svc, game = make_game(10, _std_roles())
    _to_voting(svc, game)
    for voter in list(game.alive()):
        if voter == 6:
            continue
        svc.submit_vote(CH, voter, 6)
    if game.phase == "day_vote":
        svc.force_tally(CH)
    assert game.pending_hunter == 6 and game.phase == "day_vote_hunter"
    shot = svc.hunter_shoot(CH, 6, 1)
    assert 1 in game.dead and game.phase.startswith("night_")


# ---------- 胜负判定 ----------

def test_good_wins_when_all_wolves_dead():
    svc, game = make_game(10, _std_roles())
    game.dead.extend([1, 2])
    _to_voting(svc, game)
    for voter in list(game.alive()):
        if voter == 3:
            continue
        svc.submit_vote(CH, voter, 3)
    if game.phase == "day_vote":
        svc.force_tally(CH)
    assert game.winner == "good"


def test_wolf_wins_when_wolves_reach_parity():
    svc, game = make_game(10, _std_roles())
    game.dead.extend([8, 9, 10])
    _to_witch(svc, game, 6)
    svc.witch_action(CH, 5, "none")
    svc.seer_check(CH, 4, 1)
    result = svc.resolve_night(CH)
    assert result["game_over"] and result["winner"] == "wolf"


def test_check_winner_none_mid_game():
    svc, game = make_game(10, _std_roles())
    assert svc.check_winner(game) is None


# ---------- 经济结算 ----------

def test_settle_pays_winners_from_losers(coin):
    svc, game = make_game(10, _std_roles(), bet=50)
    for i in range(1, 11):
        coin.balances[i] = 500
    result = run(svc.settle(game, "good"))
    assert result["pool"] == 150
    assert result["share"] == 150 // 7
    for wolf in (1, 2, 3):
        assert coin.balances[wolf] == 450
    assert coin.balances[8] == 500 + result["share"]
    assert not svc.has_game(CH)


def test_settle_is_idempotent(coin):
    svc, game = make_game(10, _std_roles())
    for i in range(1, 11):
        coin.balances[i] = 500
    run(svc.settle(game, "wolf"))
    assert "error" in run(svc.settle(game, "wolf"))


def test_settle_skips_failed_deductions(coin):
    svc, game = make_game(10, _std_roles(), bet=50)
    for i in range(1, 11):
        coin.balances[i] = 500
    coin.fail_remove_users = {1}
    result = run(svc.settle(game, "good"))
    assert result["deduct_failed"] == [1]
    assert result["pool"] == 100
    assert coin.balances[1] == 500


def test_settle_pays_nothing_when_all_deductions_fail(coin):
    svc, game = make_game(10, _std_roles(), bet=50)
    for i in range(1, 11):
        coin.balances[i] = 500
    coin.fail_remove = True
    result = run(svc.settle(game, "good"))
    assert result["pool"] == 0 and result["share"] == 0
    assert all(e[0] != "add" for e in coin.log)


# ---------- 警长竞选 ----------

def _to_sheriff_signup(svc, game, kill_target=9):
    """走到第一天天亮后的警长报名阶段。"""
    _to_witch(svc, game, kill_target)
    svc.witch_action(CH, 5, "none")
    svc.seer_check(CH, 4, 1)
    svc.resolve_night(CH)
    # 天亮后应进 sheriff_signup（day==1, WEREWOLF_ELECT_SHERIFF=True）
    if game.phase == "day_discuss":
        # 没有启用竞选或已跳过，手动开
        svc.begin_sheriff_election(game)
    assert game.phase == "sheriff_signup"


def test_sheriff_signup_and_close_with_candidates():
    svc, game = make_game(10, _std_roles())
    _to_sheriff_signup(svc, game)
    assert svc.sheriff_signup(CH, 1, True)["ok"]
    assert svc.sheriff_signup(CH, 4, True)["ok"]
    assert svc.sheriff_signup(CH, 7, True)["ok"]
    # 其他人不上警
    for uid in (2, 3, 5, 6, 8, 9):
        if uid in game.alive():
            svc.sheriff_signup(CH, uid, False)
    r = svc.close_signup(CH)
    assert r["candidates"] == [1, 4, 7]
    assert game.phase == "sheriff_campaign"


def test_sheriff_signup_no_one_runs_no_sheriff():
    svc, game = make_game(10, _std_roles())
    _to_sheriff_signup(svc, game)
    for uid in list(game.alive()):
        svc.sheriff_signup(CH, uid, False)
    r = svc.close_signup(CH)
    assert r["no_sheriff"] and game.phase == "day_discuss"


def test_sheriff_campaign_speak_in_order():
    svc, game = make_game(10, _std_roles())
    _to_sheriff_signup(svc, game)
    for uid in (1, 4, 7):
        svc.sheriff_signup(CH, uid, True)
    svc.close_signup(CH)
    speaker = svc.campaign_current_speaker(game)
    assert speaker == 1
    r = svc.campaign_speak(CH, 1, "我是好人")
    assert r["spoke"] == 1 and r["campaign_done"] is False
    assert r["next_speaker"] == 4


def test_sheriff_campaign_withdraw():
    svc, game = make_game(10, _std_roles())
    _to_sheriff_signup(svc, game)
    for uid in (1, 4, 7):
        svc.sheriff_signup(CH, uid, True)
    svc.close_signup(CH)
    # 1 号退水
    r = svc.campaign_withdraw(CH, 1)
    assert 1 in game.campaign["withdrawn"]
    # 下一个发言人是 4
    assert svc.campaign_current_speaker(game) == 4


def test_sheriff_vote_simple_majority():
    svc, game = make_game(10, _std_roles())
    _to_sheriff_signup(svc, game)
    for uid in (1, 4, 7):
        svc.sheriff_signup(CH, uid, True)
    svc.close_signup(CH)
    # 候选人发言
    for uid in (1, 4, 7):
        svc.campaign_speak(CH, uid, "拉票")
    assert game.phase == "sheriff_vote"
    # 非候选人：2,3,5,6,8,10（9号被刀死了）
    voters = svc.sheriff_voters(game)
    assert set(voters) == {2, 3, 5, 6, 8, 10}
    svc.sheriff_vote(CH, 2, 4)
    svc.sheriff_vote(CH, 3, 4)
    svc.sheriff_vote(CH, 5, 1)
    svc.sheriff_vote(CH, 6, 4)
    svc.sheriff_vote(CH, 8, 7)
    r = svc.sheriff_vote(CH, 8, 7)  # 重复投
    assert "error" in r
    # 最后一票触发计票
    r = svc.sheriff_vote(CH, 10, 4)
    assert r["sheriff_done"] and r["sheriff"] == 4
    assert r["sheriff"] == 4
    assert game.sheriff_id == 4
    assert game.phase == "day_discuss"


def test_sheriff_vote_tie_no_sheriff():
    svc, game = make_game(10, _std_roles())
    _to_sheriff_signup(svc, game)
    for uid in (1, 4, 7):
        svc.sheriff_signup(CH, uid, True)
    svc.close_signup(CH)
    for uid in (1, 4, 7):
        svc.campaign_speak(CH, uid, "拉票")
    svc.sheriff_vote(CH, 2, 1)
    svc.sheriff_vote(CH, 3, 4)
    svc.sheriff_vote(CH, 5, 1)
    svc.sheriff_vote(CH, 6, 4)
    svc.sheriff_vote(CH, 8, 7)
    r = svc.sheriff_vote(CH, 10, 7)  # 最后一票触发计票
    if "error" in r or not r.get("sheriff_done"):
        r = svc.force_sheriff_tally(CH)
    assert r["tie"] is True
    assert game.sheriff_id is None


def test_sheriff_single_candidate_auto_elected():
    svc, game = make_game(10, _std_roles())
    _to_sheriff_signup(svc, game)
    svc.sheriff_signup(CH, 1, True)
    svc.sheriff_signup(CH, 4, True)
    svc.close_signup(CH)
    # 1 号发言后退水，4 号发言后退水 → 只剩 7? 不，只有 1 和 4 候选
    svc.campaign_speak(CH, 1, "拉票")
    svc.campaign_withdraw(CH, 4)
    # 4 退水后只剩 1
    r = svc.campaign_speak(CH, 4, "x")  # 4 已退水
    assert "error" in r
    # 1 号发言完
    # 只剩一个候选人 → 自动当选
    # 手动推进
    r = svc._begin_sheriff_vote(game) if game.phase == "sheriff_campaign" else {}
    # campaign_current_speaker 应为 None（1 已发言，4 已退水）
    assert svc.campaign_current_speaker(game) is None
    r = svc._begin_sheriff_vote(game)
    assert r.get("auto") and r.get("sheriff") == 1
    assert game.sheriff_id == 1


def test_sheriff_vote_weight_1_5_in_day_vote():
    svc, game = make_game(10, _std_roles())
    _to_sheriff_signup(svc, game)
    svc.sheriff_signup(CH, 1, True)
    svc.close_signup(CH)
    # 单候选人自动当选
    r = svc._begin_sheriff_vote(game) if game.phase == "sheriff_campaign" else {}
    if game.phase == "sheriff_campaign":
        # 1 号发言
        svc.campaign_speak(CH, 1, "我是好人")
    assert game.sheriff_id == 1
    assert game.phase == "day_discuss"
    # 进投票
    svc.enter_voting(game)
    # 1（警长）投 2，2 投 1，3 投 2，其余投 2
    alive = game.alive()
    svc.submit_vote(CH, 1, 2)   # 警长票权 1.5
    svc.submit_vote(CH, 2, 1)
    svc.submit_vote(CH, 3, 2)
    svc.submit_vote(CH, 5, 2)
    svc.submit_vote(CH, 6, 2)
    svc.submit_vote(CH, 7, 2)
    svc.submit_vote(CH, 8, 1)
    r = svc.force_tally(CH)
    # 2 号得票：1.5(警长) + 1(3) + 1(5) + 1(6) + 1(7) = 5.5
    # 1 号得票：1(2) + 1(8) = 2
    assert r["eliminated"] == 2
    assert game.vote_weight(1) == 1.5


def test_sheriff_death_triggers_transfer():
    svc, game = make_game(10, _std_roles())
    _to_sheriff_signup(svc, game)
    svc.sheriff_signup(CH, 1, True)
    svc.close_signup(CH)
    if game.phase == "sheriff_campaign":
        svc.campaign_speak(CH, 1, "我是好人")
    assert game.sheriff_id == 1
    # 警长被投出
    svc.enter_voting(game)
    for voter in list(game.alive()):
        if voter == 1:
            continue
        svc.submit_vote(CH, voter, 1)
    r = svc.force_tally(CH)
    assert r["eliminated"] == 1
    assert r.get("sheriff_died") == 1
    assert game.pending_sheriff_transfer == 1
    assert game.sheriff_id is None
    # 移交给 4
    r = svc.sheriff_transfer(CH, 1, 4)
    assert r["transferred_to"] == 4
    assert game.sheriff_id == 4


def test_sheriff_transfer_destroy():
    svc, game = make_game(10, _std_roles())
    _to_sheriff_signup(svc, game)
    svc.sheriff_signup(CH, 1, True)
    svc.close_signup(CH)
    if game.phase == "sheriff_campaign":
        svc.campaign_speak(CH, 1, "我是好人")
    assert game.sheriff_id == 1
    svc.enter_voting(game)
    for voter in list(game.alive()):
        if voter == 1:
            continue
        svc.submit_vote(CH, voter, 1)
    svc.force_tally(CH)
    r = svc.sheriff_transfer(CH, 1, None)
    assert r["destroyed"] is True
    assert game.sheriff_id is None
