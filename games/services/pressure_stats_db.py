# -*- coding: utf-8 -*-
"""加压轮盘 + 狼人杀统计 —— SQLite 持久化。

设计（对齐原项目 mysteryStatsDatabase.js）：
- 每游戏一张 <game>_player_stats 表，主键 (guild_id, user_id)
- 累加列 UPSERT: column = column + excluded.column
- MAX 列 UPSERT: column = MAX(column, excluded.column)
- WAL 模式 + synchronous=NORMAL + busy_timeout=5s
- 整局一次事务写库

文件路径：data/mystery/mysteryStats.sqlite
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time

log = logging.getLogger(__name__)

# 默认路径（相对项目根）
_DEFAULT_DB_DIR = os.path.join("data", "mystery")
_DEFAULT_DB_PATH = os.path.join(_DEFAULT_DB_DIR, "mysteryStats.sqlite")

# ==================== 加压轮盘表 ====================

_PRESSURE_ADDITIVE = [
    "games_played", "wins", "survived", "shots_fired", "hits_taken", "blanks",
    "loads", "bullets_loaded", "again_count", "pass_count", "quits",
    "timeout_minutes", "coward_minutes", "expected_hits",
    "unloads", "ripostes", "riposte_kills", "riposted_count",
]
_PRESSURE_MAX = ["max_charge", "max_bullets_faced"]

_PRESSURE_CREATE = """
CREATE TABLE IF NOT EXISTS pressure_player_stats (
    guild_id          TEXT NOT NULL,
    user_id           TEXT NOT NULL,
    games_played      INTEGER NOT NULL DEFAULT 0,
    wins              INTEGER NOT NULL DEFAULT 0,
    survived          INTEGER NOT NULL DEFAULT 0,
    shots_fired       INTEGER NOT NULL DEFAULT 0,
    hits_taken        INTEGER NOT NULL DEFAULT 0,
    blanks            INTEGER NOT NULL DEFAULT 0,
    loads             INTEGER NOT NULL DEFAULT 0,
    bullets_loaded    INTEGER NOT NULL DEFAULT 0,
    again_count       INTEGER NOT NULL DEFAULT 0,
    pass_count        INTEGER NOT NULL DEFAULT 0,
    quits             INTEGER NOT NULL DEFAULT 0,
    max_charge        INTEGER NOT NULL DEFAULT 0,
    max_bullets_faced INTEGER NOT NULL DEFAULT 0,
    timeout_minutes   INTEGER NOT NULL DEFAULT 0,
    coward_minutes    INTEGER NOT NULL DEFAULT 0,
    expected_hits     REAL NOT NULL DEFAULT 0,
    unloads           INTEGER NOT NULL DEFAULT 0,
    ripostes          INTEGER NOT NULL DEFAULT 0,
    riposte_kills     INTEGER NOT NULL DEFAULT 0,
    riposted_count    INTEGER NOT NULL DEFAULT 0,
    first_played_at   INTEGER NOT NULL,
    last_played_at    INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id)
)
"""

_PRESSURE_INDEX = "CREATE INDEX IF NOT EXISTS idx_pressure_guild ON pressure_player_stats (guild_id)"

# ==================== 狼人杀表 ====================

_WEREWOLF_ADDITIVE = [
    "games_played", "wins", "survived",
    "role_wolf", "role_seer", "role_witch", "role_hunter",
    "role_guard", "role_idiot", "role_villager",
    "sheriff_elected", "votes_cast", "votes_received",
    "wolf_kills", "wolf_wins",
]
_WEREWOLF_MAX: list[str] = []

_WEREWOLF_CREATE = """
CREATE TABLE IF NOT EXISTS werewolf_player_stats (
    guild_id          TEXT NOT NULL,
    user_id           TEXT NOT NULL,
    games_played      INTEGER NOT NULL DEFAULT 0,
    wins              INTEGER NOT NULL DEFAULT 0,
    survived          INTEGER NOT NULL DEFAULT 0,
    role_wolf         INTEGER NOT NULL DEFAULT 0,
    role_seer         INTEGER NOT NULL DEFAULT 0,
    role_witch        INTEGER NOT NULL DEFAULT 0,
    role_hunter       INTEGER NOT NULL DEFAULT 0,
    role_guard        INTEGER NOT NULL DEFAULT 0,
    role_idiot        INTEGER NOT NULL DEFAULT 0,
    role_villager     INTEGER NOT NULL DEFAULT 0,
    sheriff_elected   INTEGER NOT NULL DEFAULT 0,
    votes_cast        INTEGER NOT NULL DEFAULT 0,
    votes_received    INTEGER NOT NULL DEFAULT 0,
    wolf_kills        INTEGER NOT NULL DEFAULT 0,
    wolf_wins         INTEGER NOT NULL DEFAULT 0,
    first_played_at   INTEGER NOT NULL,
    last_played_at    INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id)
)
"""

_WEREWOLF_INDEX = "CREATE INDEX IF NOT EXISTS idx_werewolf_guild ON werewolf_player_stats (guild_id)"


def _build_upsert(table: str, additive: list[str], max_cols: list[str]) -> str:
    """构造 UPSERT 语句。"""
    all_cols = additive + max_cols
    col_list = ", ".join(all_cols)
    val_list = ", ".join(f"@{c}" for c in all_cols)
    add_set = ", ".join(f"{c} = {c} + excluded.{c}" for c in additive)
    max_set = ", ".join(f"{c} = MAX({c}, excluded.{c})" for c in max_cols)
    set_clause = ", ".join(filter(None, [add_set, max_set]))
    return f"""
    INSERT INTO {table} (guild_id, user_id, {col_list}, first_played_at, last_played_at)
    VALUES (@guild_id, @user_id, {val_list}, @played_at, @played_at)
    ON CONFLICT(guild_id, user_id) DO UPDATE SET
        {set_clause},
        last_played_at = excluded.last_played_at
    """


class MysteryStatsDB:
    """统计数据库。线程安全。"""

    def __init__(self, db_path: str | None = None):
        path = db_path or _DEFAULT_DB_PATH
        self._db_path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(_PRESSURE_CREATE)
            self._conn.executescript(_WEREWOLF_CREATE)
            self._conn.execute(_PRESSURE_INDEX)
            self._conn.execute(_WEREWOLF_INDEX)
            self._conn.commit()

    # ==================== 加压轮盘 ====================

    def record_pressure_game(self, guild_id: int, rows: list[dict],
                              played_at: int | None = None) -> int:
        """整局一次事务写库。返回写入行数。"""
        if not guild_id or not rows:
            return 0
        ts = played_at or int(time.time())
        valid = [r for r in rows if r.get("user_id")]
        if not valid:
            return 0
        sql = _build_upsert("pressure_player_stats", _PRESSURE_ADDITIVE, _PRESSURE_MAX)
        with self._lock:
            cur = self._conn.cursor()
            try:
                for row in valid:
                    params = {"guild_id": str(guild_id), "user_id": str(row["user_id"]),
                              "played_at": ts}
                    for c in _PRESSURE_ADDITIVE:
                        params[c] = row.get(c, 0)
                    for c in _PRESSURE_MAX:
                        params[c] = row.get(c, 0)
                    cur.execute(sql, params)
                self._conn.commit()
                return len(valid)
            except Exception:
                self._conn.rollback()
                log.exception("加压轮盘统计写库失败")
                return 0

    def get_pressure_player(self, guild_id: int, user_id: int) -> dict | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM pressure_player_stats WHERE guild_id = ? AND user_id = ?",
                (str(guild_id), str(user_id)))
            row = cur.fetchone()
            return dict(row) if row else None

    def list_pressure_stats(self, guild_id: int) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM pressure_player_stats WHERE guild_id = ?",
                (str(guild_id),))
            return [dict(r) for r in cur.fetchall()]

    def get_pressure_guild_summary(self, guild_id: int) -> dict:
        with self._lock:
            cur = self._conn.execute("""
                SELECT COUNT(*) AS players,
                       COALESCE(SUM(games_played),0) AS games_played,
                       COALESCE(SUM(wins),0) AS wins,
                       COALESCE(SUM(shots_fired),0) AS shots_fired,
                       COALESCE(SUM(hits_taken),0) AS hits_taken,
                       COALESCE(SUM(blanks),0) AS blanks,
                       COALESCE(SUM(loads),0) AS loads,
                       COALESCE(SUM(bullets_loaded),0) AS bullets_loaded,
                       COALESCE(SUM(quits),0) AS quits,
                       COALESCE(SUM(unloads),0) AS unloads,
                       COALESCE(SUM(ripostes),0) AS ripostes,
                       COALESCE(SUM(riposte_kills),0) AS riposte_kills,
                       COALESCE(SUM(timeout_minutes),0) AS timeout_minutes,
                       COALESCE(SUM(coward_minutes),0) AS coward_minutes
                FROM pressure_player_stats WHERE guild_id = ?
            """, (str(guild_id),))
            row = cur.fetchone()
            return dict(row) if row else {}

    def reset_pressure_user(self, guild_id: int, user_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM pressure_player_stats WHERE guild_id = ? AND user_id = ?",
                (str(guild_id), str(user_id)))
            self._conn.commit()
            return cur.rowcount > 0

    # ==================== 狼人杀 ====================

    def record_werewolf_game(self, guild_id: int, rows: list[dict],
                             played_at: int | None = None) -> int:
        if not guild_id or not rows:
            return 0
        ts = played_at or int(time.time())
        valid = [r for r in rows if r.get("user_id")]
        if not valid:
            return 0
        sql = _build_upsert("werewolf_player_stats", _WEREWOLF_ADDITIVE, _WEREWOLF_MAX)
        with self._lock:
            cur = self._conn.cursor()
            try:
                for row in valid:
                    params = {"guild_id": str(guild_id), "user_id": str(row["user_id"]),
                              "played_at": ts}
                    for c in _WEREWOLF_ADDITIVE:
                        params[c] = row.get(c, 0)
                    self._conn.execute(sql, params)
                self._conn.commit()
                return len(valid)
            except Exception:
                self._conn.rollback()
                log.exception("狼人杀统计写库失败")
                return 0

    def get_werewolf_player(self, guild_id: int, user_id: int) -> dict | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM werewolf_player_stats WHERE guild_id = ? AND user_id = ?",
                (str(guild_id), str(user_id)))
            row = cur.fetchone()
            return dict(row) if row else None

    def list_werewolf_stats(self, guild_id: int) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM werewolf_player_stats WHERE guild_id = ?",
                (str(guild_id),))
            return [dict(r) for r in cur.fetchall()]

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# 模块级单例（延迟初始化，避免 import 时就建文件）
_stats_db: MysteryStatsDB | None = None


def get_stats_db() -> MysteryStatsDB:
    global _stats_db
    if _stats_db is None:
        _stats_db = MysteryStatsDB()
    return _stats_db
