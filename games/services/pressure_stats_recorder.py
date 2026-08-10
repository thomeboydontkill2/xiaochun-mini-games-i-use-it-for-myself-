# -*- coding: utf-8 -*-
"""加压轮盘统计 —— 内存累加器（对齐原项目 pressureStatsRecorder.js）。

设计：
- 整局过程只在内存累加，settleGame 时一次性写库（单事务）
- 所有函数纯内存操作、同步、不抛异常，可安全放进临界区
- 字段名直接对齐表列名，写库不用映射

运气值：expected_hits 累加每一枪的真实中弹概率
        expected_hits - hits_taken = 运气值（正=欧皇，负=非酋）
"""

from __future__ import annotations

from typing import Iterable


def create_player_row(user_id: int) -> dict:
    """每个玩家的累加器行。开局即记 games_played=1。"""
    return {
        "user_id": user_id,
        "games_played": 1,
        "wins": 0, "survived": 0,
        "shots_fired": 0, "hits_taken": 0, "blanks": 0,
        "loads": 0, "bullets_loaded": 0,
        "again_count": 0, "pass_count": 0, "quits": 0,
        "max_charge": 0, "max_bullets_faced": 0,
        "timeout_minutes": 0, "coward_minutes": 0,
        "expected_hits": 0.0,
        "unloads": 0,
        "ripostes": 0, "riposte_kills": 0, "riposted_count": 0,
    }


def create_pressure_stats(player_ids: Iterable[int]) -> dict:
    """开局时创建内存累加器。"""
    players: dict[int, dict] = {}
    for uid in player_ids:
        if uid and uid not in players:
            players[uid] = create_player_row(uid)
    return {"players": players}


def _row_for(stats: dict, user_id: int) -> dict | None:
    return stats.get("players", {}).get(user_id)


def record_shot(stats: dict, user_id: int, *,
                hit: bool, bullets_before: int, unknown_before: int,
                hit_chance: float | None = None) -> None:
    """记一次扣扳机。"""
    row = _row_for(stats, user_id)
    if row is None:
        return
    row["shots_fired"] += 1
    if hit:
        row["hits_taken"] += 1
    else:
        row["blanks"] += 1
    row["max_bullets_faced"] = max(row["max_bullets_faced"], bullets_before or 0)
    # 运气值：累加这一枪的真实中弹概率
    if hit_chance is not None and hit_chance == hit_chance:  # NaN 检查
        row["expected_hits"] += hit_chance
    elif unknown_before and unknown_before > 0:
        row["expected_hits"] += (bullets_before or 0) / unknown_before


def record_choice(stats: dict, user_id: int, *,
                  action: str, loaded_bullets: int = 0,
                  charge_after: int = 0) -> None:
    """记一次空枪后的选择。action: pass/again/load。"""
    row = _row_for(stats, user_id)
    if row is None:
        return
    if action == "load":
        row["loads"] += 1
        row["bullets_loaded"] += loaded_bullets or 0
    elif action == "again":
        row["again_count"] += 1
    else:  # pass
        row["pass_count"] += 1
    row["max_charge"] = max(row["max_charge"], charge_after or 0)


def record_elimination(stats: dict, user_id: int, minutes: int) -> None:
    """中弹淘汰的禁言时长。"""
    row = _row_for(stats, user_id)
    if row is None:
        return
    row["timeout_minutes"] += minutes or 0


def record_quit(stats: dict, user_id: int, penalty_minutes: int) -> None:
    """逃跑。"""
    row = _row_for(stats, user_id)
    if row is None:
        return
    row["quits"] += 1
    row["coward_minutes"] += penalty_minutes or 0


def record_unload(stats: dict, user_id: int) -> None:
    """抽弹开枪。"""
    row = _row_for(stats, user_id)
    if row is None:
        return
    row["unloads"] += 1


def record_riposte(stats: dict, initiator_id: int, target_id: int) -> None:
    """反手还击。initiator.ripostes+1, target.riposted_count+1。"""
    init_row = _row_for(stats, initiator_id)
    if init_row is not None:
        init_row["ripostes"] += 1
    target_row = _row_for(stats, target_id)
    if target_row is not None:
        target_row["riposted_count"] += 1


def record_riposte_kill(stats: dict, initiator_id: int) -> None:
    """反手击杀。"""
    row = _row_for(stats, initiator_id)
    if row is not None:
        row["riposte_kills"] += 1


def finalize_pressure_stats(stats: dict, *,
                             outcome: str, alive_ids: list[int]) -> list[dict]:
    """终局补 wins/survived，返回待写库的行数组。

    outcome: champion / draw / aborted / cancelled
    - champion: 存活者 wins+1 survived+1
    - draw: 存活者 survived+1（不算 wins，否则平局刷胜场榜）
    - aborted/cancelled: 什么都不算
    """
    if not stats or not stats.get("players"):
        return []
    for uid in alive_ids:
        row = _row_for(stats, uid)
        if row is None:
            continue
        row["survived"] += 1
        if outcome == "champion":
            row["wins"] += 1
    return list(stats["players"].values())
