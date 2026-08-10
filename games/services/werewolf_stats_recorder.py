# -*- coding: utf-8 -*-
"""狼人杀统计 —— 内存累加器。

字段：games_played/wins/survived/role_*/sheriff_elected/votes_cast/votes_received/wolf_kills/wolf_wins
设计同加压轮盘：内存累加 + 终局单事务写库。
"""

from __future__ import annotations

from typing import Iterable


# 中文角色名 → 统计字段后缀
_ROLE_KEY_MAP = {
    "狼人": "wolf", "平民": "villager", "预言家": "seer",
    "女巫": "witch", "猎人": "hunter", "守卫": "guard", "白痴": "idiot",
}


def create_player_row(user_id: int, role: str = "") -> dict:
    """每个玩家的累加器行。开局即记 games_played=1 + 角色次数。"""
    row = {
        "user_id": user_id,
        "games_played": 1,
        "wins": 0, "survived": 0,
        "role_wolf": 0, "role_seer": 0, "role_witch": 0,
        "role_hunter": 0, "role_guard": 0, "role_idiot": 0,
        "role_villager": 0,
        "sheriff_elected": 0,
        "votes_cast": 0, "votes_received": 0,
        "wolf_kills": 0, "wolf_wins": 0,
    }
    if role:
        key = f"role_{_ROLE_KEY_MAP.get(role, role)}"
        if key in row:
            row[key] = 1
    return row


def create_werewolf_stats(player_ids: Iterable[int],
                          roles: dict[int, str] | None = None) -> dict:
    """开局时创建内存累加器。roles: {user_id: role_name}"""
    players: dict[int, dict] = {}
    roles = roles or {}
    for uid in player_ids:
        if uid and uid not in players:
            players[uid] = create_player_row(uid, roles.get(uid, ""))
    return {"players": players}


def _row_for(stats: dict, user_id: int) -> dict | None:
    return stats.get("players", {}).get(user_id)


def record_vote_cast(stats: dict, user_id: int) -> None:
    """记一次投票。"""
    row = _row_for(stats, user_id)
    if row is not None:
        row["votes_cast"] += 1


def record_vote_received(stats: dict, user_id: int) -> None:
    """记一次被投票。"""
    row = _row_for(stats, user_id)
    if row is not None:
        row["votes_received"] += 1


def record_sheriff_elected(stats: dict, user_id: int) -> None:
    """记一次当选警长。"""
    row = _row_for(stats, user_id)
    if row is not None:
        row["sheriff_elected"] += 1


def record_wolf_kill(stats: dict, wolf_id: int) -> None:
    """记一次狼人击杀（狼人视角）。"""
    row = _row_for(stats, wolf_id)
    if row is not None:
        row["wolf_kills"] += 1


def finalize_werewolf_stats(stats: dict, *,
                             winner: str | None,
                             wolves: list[int],
                             goods: list[int]) -> list[dict]:
    """终局补 wins/survived/wolf_wins，返回待写库的行数组。

    winner: "good" / "wolf" / None
    - winner=good: goods wins+1 survived+1, wolves survived+0
    - winner=wolf: wolves wins+1 survived+1, goods survived+0
    - None: 什么都不算
    """
    if not stats or not stats.get("players"):
        return []
    if winner == "good":
        for uid in goods:
            row = _row_for(stats, uid)
            if row is not None:
                row["wins"] += 1
                row["survived"] += 1
    elif winner == "wolf":
        for uid in wolves:
            row = _row_for(stats, uid)
            if row is not None:
                row["wins"] += 1
                row["survived"] += 1
                row["wolf_wins"] += 1
    return list(stats["players"].values())
