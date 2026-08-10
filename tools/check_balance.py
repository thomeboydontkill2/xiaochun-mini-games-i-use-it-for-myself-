# -*- coding: utf-8 -*-
"""
轮盘期望值自检 —— 改了 ROULETTE_MULTIPLIERS 就跑一次：
    python tools/check_balance.py
期望值必须落在 -20% ~ -5%（庄家优势区间），否则退出码非 0。
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from games.config.games_config import ROULETTE_MULTIPLIERS  # noqa: E402

EV_MIN, EV_MAX = -0.20, -0.05


def roulette_ev() -> float:
    total_weight = sum(ROULETTE_MULTIPLIERS.values())
    # -2 是禁言档：币不变，按 0 计
    ev = sum((0 if m == -2 else m) * w for m, w in ROULETTE_MULTIPLIERS.items())
    return ev / total_weight


def main() -> int:
    ev = roulette_ev()
    print(f"轮盘每币期望值: {ev:+.4f} ({ev * 100:+.2f}%)")
    for m, w in sorted(ROULETTE_MULTIPLIERS.items()):
        total = sum(ROULETTE_MULTIPLIERS.values())
        print(f"  倍率 {m:>5}: 权重 {w:>3} ({w / total * 100:.1f}%)")
    if not (EV_MIN <= ev <= EV_MAX):
        print(f"❌ 期望值超出允许区间 [{EV_MIN:+.0%}, {EV_MAX:+.0%}]，请重新调整权重！")
        return 1
    print(f"✅ 期望值在允许区间 [{EV_MIN:+.0%}, {EV_MAX:+.0%}] 内（庄家优势）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
