# -*- coding: utf-8 -*-
"""狼人杀统计系统测试。"""

from games.services.werewolf_stats_recorder import (
    create_werewolf_stats, create_player_row,
    record_vote_cast, record_vote_received, record_sheriff_elected,
    record_wolf_kill, finalize_werewolf_stats,
)


def test_create_player_row_with_role():
    row = create_player_row(1001, "狼人")
    assert row["games_played"] == 1
    assert row["role_wolf"] == 1
    assert row["role_seer"] == 0


def test_create_player_row_villager():
    row = create_player_row(1001, "平民")
    assert row["role_villager"] == 1


def test_create_stats_with_roles():
    roles = {1001: "狼人", 1002: "预言家", 1003: "平民"}
    stats = create_werewolf_stats([1001, 1002, 1003], roles)
    assert stats["players"][1001]["role_wolf"] == 1
    assert stats["players"][1002]["role_seer"] == 1
    assert stats["players"][1003]["role_villager"] == 1


def test_record_vote_cast():
    stats = create_werewolf_stats([1001])
    record_vote_cast(stats, 1001)
    record_vote_cast(stats, 1001)
    assert stats["players"][1001]["votes_cast"] == 2


def test_record_vote_received():
    stats = create_werewolf_stats([1001])
    record_vote_received(stats, 1001)
    assert stats["players"][1001]["votes_received"] == 1


def test_record_sheriff_elected():
    stats = create_werewolf_stats([1001])
    record_sheriff_elected(stats, 1001)
    assert stats["players"][1001]["sheriff_elected"] == 1


def test_record_wolf_kill():
    stats = create_werewolf_stats([1001])
    record_wolf_kill(stats, 1001)
    assert stats["players"][1001]["wolf_kills"] == 1


def test_finalize_good_wins():
    stats = create_werewolf_stats([1001, 1002, 1003],
                                   {1001: "狼人", 1002: "预言家", 1003: "平民"})
    rows = finalize_werewolf_stats(stats, winner="good",
                                    wolves=[1001], goods=[1002, 1003])
    row_map = {r["user_id"]: r for r in rows}
    # 好人赢
    assert row_map[1002]["wins"] == 1
    assert row_map[1002]["survived"] == 1
    assert row_map[1003]["wins"] == 1
    # 狼人不算赢
    assert row_map[1001]["wins"] == 0
    assert row_map[1001]["wolf_wins"] == 0


def test_finalize_wolf_wins():
    stats = create_werewolf_stats([1001, 1002, 1003],
                                   {1001: "狼人", 1002: "预言家", 1003: "平民"})
    rows = finalize_werewolf_stats(stats, winner="wolf",
                                    wolves=[1001], goods=[1002, 1003])
    row_map = {r["user_id"]: r for r in rows}
    # 狼人赢
    assert row_map[1001]["wins"] == 1
    assert row_map[1001]["survived"] == 1
    assert row_map[1001]["wolf_wins"] == 1
    # 好人不算赢
    assert row_map[1002]["wins"] == 0
    assert row_map[1002]["survived"] == 0


def test_finalize_no_winner():
    stats = create_werewolf_stats([1001, 1002],
                                   {1001: "狼人", 1002: "平民"})
    rows = finalize_werewolf_stats(stats, winner=None,
                                    wolves=[1001], goods=[1002])
    row_map = {r["user_id"]: r for r in rows}
    # 没有赢家
    assert row_map[1001]["wins"] == 0
    assert row_map[1002]["wins"] == 0
