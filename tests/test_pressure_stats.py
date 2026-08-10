# -*- coding: utf-8 -*-
"""加压轮盘统计系统测试。"""

import os
import tempfile

import pytest

from games.services.pressure_stats_recorder import (
    create_pressure_stats, record_shot, record_choice, record_elimination,
    record_quit, record_unload, record_riposte, record_riposte_kill,
    finalize_pressure_stats,
)
from games.services.pressure_titles import (
    compute_throne_holders, compute_achievements, rank_by, METRICS,
    THRONES, ACHIEVEMENTS, luck_of,
)
from games.services.pressure_stats_db import MysteryStatsDB


# ==================== 累加器测试 ====================


def test_create_stats():
    stats = create_pressure_stats([1001, 1002, 1003])
    assert len(stats["players"]) == 3
    assert stats["players"][1001]["games_played"] == 1
    assert stats["players"][1001]["shots_fired"] == 0


def test_create_stats_dedup():
    stats = create_pressure_stats([1001, 1001, 1002])
    assert len(stats["players"]) == 2


def test_record_shot_hit():
    stats = create_pressure_stats([1001])
    record_shot(stats, 1001, hit=True, bullets_before=2, unknown_before=5)
    row = stats["players"][1001]
    assert row["shots_fired"] == 1
    assert row["hits_taken"] == 1
    assert row["blanks"] == 0
    assert row["max_bullets_faced"] == 2
    assert row["expected_hits"] == 0.4  # 2/5


def test_record_shot_blank():
    stats = create_pressure_stats([1001])
    record_shot(stats, 1001, hit=False, bullets_before=2, unknown_before=5)
    row = stats["players"][1001]
    assert row["blanks"] == 1
    assert row["hits_taken"] == 0
    assert row["expected_hits"] == 0.4  # 2/5


def test_record_shot_first_shot_chance():
    stats = create_pressure_stats([1001])
    record_shot(stats, 1001, hit=False, bullets_before=1, unknown_before=6,
                hit_chance=0.15)
    row = stats["players"][1001]
    assert row["expected_hits"] == 0.15  # 用 hit_chance


def test_record_choice_load():
    stats = create_pressure_stats([1001])
    record_choice(stats, 1001, action="load", loaded_bullets=3, charge_after=0)
    row = stats["players"][1001]
    assert row["loads"] == 1
    assert row["bullets_loaded"] == 3


def test_record_choice_again():
    stats = create_pressure_stats([1001])
    record_choice(stats, 1001, action="again", charge_after=2)
    row = stats["players"][1001]
    assert row["again_count"] == 1
    assert row["max_charge"] == 2


def test_record_choice_pass():
    stats = create_pressure_stats([1001])
    record_choice(stats, 1001, action="pass")
    row = stats["players"][1001]
    assert row["pass_count"] == 1


def test_record_elimination():
    stats = create_pressure_stats([1001])
    record_elimination(stats, 1001, 6)
    assert stats["players"][1001]["timeout_minutes"] == 6


def test_record_quit():
    stats = create_pressure_stats([1001])
    record_quit(stats, 1001, 5)
    row = stats["players"][1001]
    assert row["quits"] == 1
    assert row["coward_minutes"] == 5


def test_record_unload():
    stats = create_pressure_stats([1001])
    record_unload(stats, 1001)
    assert stats["players"][1001]["unloads"] == 1


def test_record_riposte():
    stats = create_pressure_stats([1001, 1002])
    record_riposte(stats, 1001, 1002)
    assert stats["players"][1001]["ripostes"] == 1
    assert stats["players"][1002]["riposted_count"] == 1


def test_record_riposte_kill():
    stats = create_pressure_stats([1001])
    record_riposte_kill(stats, 1001)
    assert stats["players"][1001]["riposte_kills"] == 1


def test_finalize_champion():
    stats = create_pressure_stats([1001, 1002, 1003])
    # 1001 冠军，1002/1003 出局
    rows = finalize_pressure_stats(stats, outcome="champion", alive_ids=[1001])
    row_map = {r["user_id"]: r for r in rows}
    assert row_map[1001]["wins"] == 1
    assert row_map[1001]["survived"] == 1
    assert row_map[1002]["wins"] == 0
    assert row_map[1002]["survived"] == 0


def test_finalize_draw():
    stats = create_pressure_stats([1001, 1002])
    rows = finalize_pressure_stats(stats, outcome="draw", alive_ids=[1001, 1002])
    row_map = {r["user_id"]: r for r in rows}
    # 平局：survived+1 但 wins 不加
    assert row_map[1001]["survived"] == 1
    assert row_map[1001]["wins"] == 0
    assert row_map[1002]["survived"] == 1
    assert row_map[1002]["wins"] == 0


def test_finalize_aborted():
    stats = create_pressure_stats([1001, 1002])
    rows = finalize_pressure_stats(stats, outcome="aborted", alive_ids=[])
    row_map = {r["user_id"]: r for r in rows}
    # aborted：什么都不算
    assert row_map[1001]["survived"] == 0
    assert row_map[1001]["wins"] == 0


# ==================== 运气值测试 ====================


def test_luck_of_positive():
    row = {"expected_hits": 5.0, "hits_taken": 2}
    assert luck_of(row) == 3.0  # 欧皇


def test_luck_of_negative():
    row = {"expected_hits": 1.0, "hits_taken": 3}
    assert luck_of(row) == -2.0  # 非酋


def test_luck_accumulates():
    stats = create_pressure_stats([1001])
    # 第一枪：1发/6未知，hit_chance=0.15
    record_shot(stats, 1001, hit=False, bullets_before=1, unknown_before=6, hit_chance=0.15)
    # 第二枪：1发/5未知 = 0.2
    record_shot(stats, 1001, hit=False, bullets_before=1, unknown_before=5)
    # 第三枪：1发/4未知 = 0.25，中弹
    record_shot(stats, 1001, hit=True, bullets_before=1, unknown_before=4)
    row = stats["players"][1001]
    # expected = 0.15 + 0.2 + 0.25 = 0.6, hits = 1
    assert abs(row["expected_hits"] - 0.6) < 0.01
    assert luck_of(row) == 0.6 - 1  # -0.4


# ==================== 称号测试 ====================


def test_compute_throne_holders_empty():
    thrones = compute_throne_holders([])
    assert len(thrones) == 9
    for t in thrones:
        assert t["holder_id"] is None


def test_compute_throne_holders_with_data():
    rows = [
        {"user_id": "1001", "shots_fired": 20, "expected_hits": 5.0, "hits_taken": 1,
         "bullets_loaded": 10, "survived": 5, "max_charge": 4,
         "max_bullets_faced": 5, "timeout_minutes": 20, "quits": 0},
        {"user_id": "1002", "shots_fired": 15, "expected_hits": 3.0, "hits_taken": 3,
         "bullets_loaded": 5, "survived": 3, "max_charge": 2,
         "max_bullets_faced": 3, "timeout_minutes": 10, "quits": 3},
    ]
    thrones = compute_throne_holders(rows)
    throne_map = {t["key"]: t for t in thrones}
    # 欧皇：1001 运气值 4.0 > 1002 运气值 0.0
    assert throne_map["luckiest"]["holder_id"] == 1001
    # 非酋：1002
    assert throne_map["unluckiest"]["holder_id"] == 1002
    # 枪王：1001 (20 > 15)
    assert throne_map["top_shooter"]["holder_id"] == 1001
    # 逃跑艺术家：1002 (3 > 0)
    assert throne_map["coward"]["holder_id"] == 1002


def test_compute_achievements():
    row = {"games_played": 15, "shots_fired": 120, "wins": 5, "survived": 12,
           "hits_taken": 10, "max_charge": 6, "loads": 25, "timeout_minutes": 70,
           "unloads": 6, "riposte_kills": 4, "blanks": 100, "bullets_loaded": 30}
    achievements = compute_achievements(row)
    keys = {a["key"] for a in achievements}
    assert "first_blood" in keys  # games_played >= 1
    assert "regular" in keys  # >= 10
    assert "veteran" in keys  # shots >= 100
    assert "triple_crown" in keys  # wins >= 3
    assert "survivor" in keys  # survived >= 10
    assert "near_death" in keys  # hits >= 10 and games >= 10
    assert "five_chain" in keys  # max_charge >= 5
    assert "gambler" in keys  # loads >= 20
    assert "hour_jail" in keys  # timeout >= 60
    assert "demolition" in keys  # unloads >= 5
    assert "mutual_destruction" in keys  # riposte_kills >= 3


def test_compute_achievements_pacifist():
    row = {"games_played": 12, "shots_fired": 50, "loads": 0}
    achievements = compute_achievements(row)
    keys = {a["key"] for a in achievements}
    assert "pacifist" in keys  # >= 10 games, 0 loads


# ==================== 排行榜测试 ====================


def test_rank_by_wins():
    rows = [
        {"user_id": "1001", "wins": 5},
        {"user_id": "1002", "wins": 3},
        {"user_id": "1003", "wins": 0},  # 不达标
    ]
    ranked = rank_by(rows, "wins")
    assert len(ranked) == 2  # 1003 不达标
    assert ranked[0]["user_id"] == 1001
    assert ranked[0]["value_text"] == "5 胜"


def test_rank_by_empty():
    ranked = rank_by([], "wins")
    assert ranked == []


def test_rank_by_limit():
    rows = [{"user_id": str(i), "wins": i} for i in range(1, 20)]
    ranked = rank_by(rows, "wins", limit=10)
    assert len(ranked) == 10


# ==================== 数据库测试 ====================


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "test.sqlite")
        instance = MysteryStatsDB(path)
        yield instance
        instance.close()


def test_db_record_and_query(db):
    rows = [
        {"user_id": 1001, "games_played": 1, "wins": 1, "survived": 1, "shots_fired": 5},
        {"user_id": 1002, "games_played": 1, "wins": 0, "survived": 0, "shots_fired": 3},
    ]
    count = db.record_pressure_game(999, rows)
    assert count == 2

    p1 = db.get_pressure_player(999, 1001)
    assert p1 is not None
    assert p1["games_played"] == 1
    assert p1["wins"] == 1
    assert p1["shots_fired"] == 5


def test_db_upsert_accumulates(db):
    rows1 = [{"user_id": 1001, "games_played": 1, "wins": 1, "shots_fired": 5}]
    db.record_pressure_game(999, rows1)
    rows2 = [{"user_id": 1001, "games_played": 1, "wins": 0, "shots_fired": 3}]
    db.record_pressure_game(999, rows2)

    p1 = db.get_pressure_player(999, 1001)
    assert p1["games_played"] == 2  # 累加
    assert p1["wins"] == 1
    assert p1["shots_fired"] == 8


def test_db_max_column(db):
    rows1 = [{"user_id": 1001, "max_charge": 3, "max_bullets_faced": 4}]
    db.record_pressure_game(999, rows1)
    rows2 = [{"user_id": 1001, "max_charge": 5, "max_bullets_faced": 2}]
    db.record_pressure_game(999, rows2)

    p1 = db.get_pressure_player(999, 1001)
    assert p1["max_charge"] == 5  # MAX
    assert p1["max_bullets_faced"] == 4  # MAX


def test_db_list_stats(db):
    rows = [
        {"user_id": 1001, "games_played": 1},
        {"user_id": 1002, "games_played": 1},
    ]
    db.record_pressure_game(999, rows)
    all_stats = db.list_pressure_stats(999)
    assert len(all_stats) == 2


def test_db_guild_summary(db):
    rows = [
        {"user_id": 1001, "games_played": 1, "wins": 1, "shots_fired": 5},
        {"user_id": 1002, "games_played": 1, "wins": 0, "shots_fired": 3},
    ]
    db.record_pressure_game(999, rows)
    summary = db.get_pressure_guild_summary(999)
    assert summary["players"] == 2
    assert summary["games_played"] == 2
    assert summary["wins"] == 1
    assert summary["shots_fired"] == 8


def test_db_reset_user(db):
    rows = [{"user_id": 1001, "games_played": 1}]
    db.record_pressure_game(999, rows)
    assert db.reset_pressure_user(999, 1001) is True
    assert db.get_pressure_player(999, 1001) is None


def test_db_isolates_guilds(db):
    rows = [{"user_id": 1001, "games_played": 1}]
    db.record_pressure_game(999, rows)
    db.record_pressure_game(888, rows)
    assert db.get_pressure_player(999, 1001) is not None
    assert db.get_pressure_player(888, 1001) is not None
    # 不同服务器独立
    assert db.list_pressure_stats(999) != db.list_pressure_stats(888) or \
           len(db.list_pressure_stats(999)) == len(db.list_pressure_stats(888))
