# -*- coding: utf-8 -*-
"""加压轮盘称号系统（对齐原项目 pressureTitles.js）。

- 9 个王座称号（全服独占，数据第一的人拿，可被抢走）
- 13 个成就称号（达标即得，不会失去）
- 称号查询时现算，不入库

运气值算法：expected_hits - hits_taken
  expected_hits 是每一枪的真实中弹概率的累加
  比单纯数空枪次数靠谱得多
"""

from __future__ import annotations


def luck_of(row: dict) -> float:
    """运气值 = 期望中弹数 - 实际中弹数。正=欧皇，负=非酋。"""
    return float(row.get("expected_hits", 0)) - float(row.get("hits_taken", 0))


# ==================== 王座称号（全服独占，数据第一）====================

THRONES = [
    {
        "key": "luckiest", "label": "🍀 欧皇",
        "desc": "运气值最高（期望中弹数 - 实际中弹数）",
        "value": luck_of,
        "format": lambda v: f"运气值 {v:+.2f}",
        "eligible": lambda r: r.get("shots_fired", 0) >= 10,
        "sort": "desc",
    },
    {
        "key": "unluckiest", "label": "😵 非酋",
        "desc": "运气值最低",
        "value": luck_of,
        "format": lambda v: f"运气值 {v:+.2f}",
        "eligible": lambda r: r.get("shots_fired", 0) >= 10,
        "sort": "asc",
    },
    {
        "key": "top_shooter", "label": "🔫 枪王",
        "desc": "开枪次数最多",
        "value": lambda r: r.get("shots_fired", 0),
        "format": lambda v: f"{v} 枪",
        "eligible": lambda r: r.get("shots_fired", 0) >= 10,
        "sort": "desc",
    },
    {
        "key": "pressure_master", "label": "💥 压力大师",
        "desc": "加压塞入子弹总数最多",
        "value": lambda r: r.get("bullets_loaded", 0),
        "format": lambda v: f"塞了 {v} 发",
        "eligible": lambda r: r.get("bullets_loaded", 0) >= 5,
        "sort": "desc",
    },
    {
        "key": "fearless", "label": "🦁 不怕死之人",
        "desc": "存活局数最多",
        "value": lambda r: r.get("survived", 0),
        "format": lambda v: f"存活 {v} 局",
        "eligible": lambda r: r.get("survived", 0) >= 3,
        "sort": "desc",
    },
    {
        "key": "chain_maniac", "label": "🔁 连开狂魔",
        "desc": "单局最高蓄力层数",
        "value": lambda r: r.get("max_charge", 0),
        "format": lambda v: f"连开 {v} 层",
        "eligible": lambda r: r.get("max_charge", 0) >= 3,
        "sort": "desc",
    },
    {
        "key": "iron_head", "label": "🛡️ 铁头娃",
        "desc": "单局面对最多子弹数",
        "value": lambda r: r.get("max_bullets_faced", 0),
        "format": lambda v: f"面对 {v} 发",
        "eligible": lambda r: r.get("max_bullets_faced", 0) >= 4,
        "sort": "desc",
    },
    {
        "key": "jailbird", "label": "⛓️ 牢底坐穿",
        "desc": "累计被禁言分钟数最多",
        "value": lambda r: r.get("timeout_minutes", 0),
        "format": lambda v: f"坐了 {v} 分钟",
        "eligible": lambda r: r.get("timeout_minutes", 0) >= 10,
        "sort": "desc",
    },
    {
        "key": "coward", "label": "🤡 逃跑艺术家",
        "desc": "胆小鬼退出次数最多",
        "value": lambda r: r.get("quits", 0),
        "format": lambda v: f"跑了 {v} 次",
        "eligible": lambda r: r.get("quits", 0) >= 2,
        "sort": "desc",
    },
]

# ==================== 成就称号（达标即得）====================

ACHIEVEMENTS = [
    {
        "key": "first_blood", "label": "🩸 初次上膛",
        "desc": "打完第一局",
        "check": lambda r: r.get("games_played", 0) >= 1,
    },
    {
        "key": "regular", "label": "🎲 常客",
        "desc": "打完 10 局",
        "check": lambda r: r.get("games_played", 0) >= 10,
    },
    {
        "key": "veteran", "label": "🎖️ 百枪老兵",
        "desc": "开枪 100 次",
        "check": lambda r: r.get("shots_fired", 0) >= 100,
    },
    {
        "key": "pacifist", "label": "🕊️ 和平主义者",
        "desc": "打 10 局从不加压",
        "check": lambda r: r.get("games_played", 0) >= 10 and r.get("loads", 0) == 0,
    },
    {
        "key": "full_chamber", "label": "🎯 六发全满",
        "desc": "单局面对 6 发子弹",
        "check": lambda r: r.get("max_bullets_faced", 0) >= 6,
    },
    {
        "key": "triple_crown", "label": "👑 三冠王",
        "desc": "赢 3 次",
        "check": lambda r: r.get("wins", 0) >= 3,
    },
    {
        "key": "survivor", "label": "🌿 老兵不死",
        "desc": "存活 10 局",
        "check": lambda r: r.get("survived", 0) >= 10,
    },
    {
        "key": "near_death", "label": "💀 向死而生",
        "desc": "中弹 10 次还活着打",
        "check": lambda r: r.get("hits_taken", 0) >= 10 and r.get("games_played", 0) >= 10,
    },
    {
        "key": "five_chain", "label": "⚡ 五连开",
        "desc": "单局连开 5 层蓄力",
        "check": lambda r: r.get("max_charge", 0) >= 5,
    },
    {
        "key": "gambler", "label": "🃏 赌徒",
        "desc": "加压 20 次",
        "check": lambda r: r.get("loads", 0) >= 20,
    },
    {
        "key": "hour_jail", "label": "⏰ 一小时监禁",
        "desc": "累计被禁言 60 分钟",
        "check": lambda r: r.get("timeout_minutes", 0) >= 60,
    },
    {
        "key": "demolition", "label": "🔧 拆弹专家",
        "desc": "抽弹开枪 5 次",
        "check": lambda r: r.get("unloads", 0) >= 5,
    },
    {
        "key": "mutual_destruction", "label": "💥 玉石俱焚",
        "desc": "反手击杀 3 次",
        "check": lambda r: r.get("riposte_kills", 0) >= 3,
    },
]


def compute_throne_holders(rows: list[dict]) -> list[dict]:
    """计算每个王座称号的持有者。返回 [{key, label, desc, holder_id, value_text}]。"""
    result = []
    for throne in THRONES:
        eligible = [r for r in rows if throne["eligible"](r)]
        if not eligible:
            result.append({**throne, "holder_id": None, "value_text": "无人达标"})
            continue
        # 排序
        reverse = throne["sort"] == "desc"
        eligible.sort(key=throne["value"], reverse=reverse)
        best = eligible[0]
        val = throne["value"](best)
        result.append({
            "key": throne["key"], "label": throne["label"], "desc": throne["desc"],
            "holder_id": int(best["user_id"]) if best.get("user_id") else None,
            "value_text": throne["format"](val),
        })
    return result


def compute_achievements(row: dict) -> list[dict]:
    """计算玩家达成的成就。返回 [{key, label, desc}]。"""
    result = []
    for ach in ACHIEVEMENTS:
        if ach["check"](row):
            result.append({"key": ach["key"], "label": ach["label"], "desc": ach["desc"]})
    return result


# ==================== 排行榜口径 ====================

METRICS = [
    {"key": "wins", "label": "👑 冠军次数", "value": lambda r: r.get("wins", 0),
     "format": lambda v: f"{v} 胜", "eligible": lambda r: r.get("wins", 0) >= 1},
    {"key": "survived", "label": "🌿 存活局数", "value": lambda r: r.get("survived", 0),
     "format": lambda v: f"{v} 局", "eligible": lambda r: r.get("survived", 0) >= 1},
    {"key": "games_played", "label": "🎮 总局数", "value": lambda r: r.get("games_played", 0),
     "format": lambda v: f"{v} 局", "eligible": lambda r: r.get("games_played", 0) >= 1},
    {"key": "shots_fired", "label": "🔫 开枪次数", "value": lambda r: r.get("shots_fired", 0),
     "format": lambda v: f"{v} 枪", "eligible": lambda r: r.get("shots_fired", 0) >= 1},
    {"key": "blanks", "label": "😮‍💨 空枪次数", "value": lambda r: r.get("blanks", 0),
     "format": lambda v: f"{v} 次", "eligible": lambda r: r.get("blanks", 0) >= 1},
    {"key": "hits_taken", "label": "💥 中弹次数", "value": lambda r: r.get("hits_taken", 0),
     "format": lambda v: f"{v} 次", "eligible": lambda r: r.get("hits_taken", 0) >= 1},
    {"key": "bullets_loaded", "label": "💥 加压子弹数", "value": lambda r: r.get("bullets_loaded", 0),
     "format": lambda v: f"{v} 发", "eligible": lambda r: r.get("bullets_loaded", 0) >= 1},
    {"key": "loads", "label": "💥 加压次数", "value": lambda r: r.get("loads", 0),
     "format": lambda v: f"{v} 次", "eligible": lambda r: r.get("loads", 0) >= 1},
    {"key": "again_count", "label": "🔁 再开次数", "value": lambda r: r.get("again_count", 0),
     "format": lambda v: f"{v} 次", "eligible": lambda r: r.get("again_count", 0) >= 1},
    {"key": "unloads", "label": "🔧 抽弹次数", "value": lambda r: r.get("unloads", 0),
     "format": lambda v: f"{v} 次", "eligible": lambda r: r.get("unloads", 0) >= 1},
    {"key": "ripostes", "label": "🔙 反手次数", "value": lambda r: r.get("ripostes", 0),
     "format": lambda v: f"{v} 次", "eligible": lambda r: r.get("ripostes", 0) >= 1},
    {"key": "riposte_kills", "label": "💀 反手击杀", "value": lambda r: r.get("riposte_kills", 0),
     "format": lambda v: f"{v} 次", "eligible": lambda r: r.get("riposte_kills", 0) >= 1},
]


def rank_by(rows: list[dict], metric_key: str, limit: int = 10) -> list[dict]:
    """按某口径排行。返回 [{user_id, value_text}]。"""
    metric = next((m for m in METRICS if m["key"] == metric_key), None)
    if metric is None:
        return []
    eligible = [r for r in rows if metric["eligible"](r)]
    eligible.sort(key=metric["value"], reverse=True)
    result = []
    for r in eligible[:limit]:
        val = metric["value"](r)
        result.append({
            "user_id": int(r["user_id"]) if r.get("user_id") else None,
            "value_text": metric["format"](val),
        })
    return result
