# -*- coding: utf-8 -*-
"""
小游戏配置 —— 下注限制、禁言时长、轮盘倍率、卧底词库、传炸弹

改权重/倍率后必须重新跑 tools/check_balance.py 校验期望值！
"""

# 下注限制
MIN_BET = 10
MAX_BET = 1000
DEFAULT_BET = 50

# 赌命机制
LIFE_GAMBLE_MUTE_MINUTES = 5          # 赌命输了：小春娘不理他时长（分钟）
LIFE_GAMBLE_DOUBLE_MUTE_MINUTES = 5   # 双方都赌命：小春娘不理他时长（统一，不再翻倍）
DISCORD_TIMEOUT_MINUTES = 1           # Discord 服务器真禁言时长（分钟），与上面叠加
LIFE_GAMBLE_REWARD_MIN = 30           # 赌命赢了系统奖励最小币
LIFE_GAMBLE_REWARD_MAX = 100          # 赌命赢了系统奖励最大币
LIFE_GAMBLE_BRAVE_MULTIPLIER = 1.5    # 有钱赌命 vs 对手赌币，赢的勇敢者奖励倍率
LIFE_GAMBLE_DAILY_LIMIT = 3           # 每人每天最多拿几次赌命系统奖励（防白嫖刷币）

# 幸运轮盘倍率配置（倍率: 权重）
# 负倍率=赔钱，0=回本，正倍率=赚钱；-2 为禁言惩罚档（不扣币，禁言 ROULETTE_MUTE_MINUTES 分钟）
# 当前期望值 ≈ -12.1%（庄家优势）。改任何数字后跑 `python tools/check_balance.py` 复核！
ROULETTE_MULTIPLIERS = {
    -1.0: 30,   # -100%：血本无归
    -0.5: 24,   # -50%：亏一半
    -0.3: 12,   # -30%：亏三成
    0:    9,    # 0x：回本
    0.5:  11,   # +50%：赚一半
    1:    7,    # +100%：翻倍
    2:    3,    # +200%：三倍
    5:    1,    # +500%：六倍
    -2:   2,    # 禁言档：不扣币，禁言
    10:   1,    # +900%：十倍
}
# 轮盘禁言档的禁言时长（分钟）
ROULETTE_MUTE_MINUTES = 2

# 对局兜底回收时长（秒）：超过这个时间没结束的对局自动清理，防止内存泄漏/频道被卡死
GAME_TTL_SECONDS = 30 * 60

# 传炸弹
BOMB_MIN_PLAYERS = 2
BOMB_MAX_PLAYERS = 8
BOMB_MIN_TIMER = 15        # 引信最短秒数（虚拟倒计时）
BOMB_MAX_TIMER = 45        # 引信最长秒数
BOMB_PASS_TIME = 20        # 持有者选择传给谁的限时（秒），超时炸弹在手上直接爆炸
BOMB_MIN_PASSES_FOR_REWARD = 1  # 赌命局至少传递多少次幸存者才有奖励（防两人开局秒爆刷奖励），按人数倍数

# 谁是卧底词库（每对词：平民词, 卧底词）
UNDERCOVER_WORD_PAIRS = [
    ("苹果", "梨子"),
    ("咖啡", "奶茶"),
    ("太阳", "月亮"),
    ("猫", "狗"),
    ("汉堡", "三明治"),
    ("篮球", "足球"),
    ("钢琴", "吉他"),
    ("玫瑰", "百合"),
    ("夏天", "冬天"),
    ("地铁", "公交"),
    ("手机", "平板"),
    ("蛋糕", "面包"),
    ("海", "湖"),
    ("雨", "雪"),
    ("白天", "黑夜"),
    ("老师", "学生"),
    ("医生", "护士"),
    ("警察", "保安"),
    ("厨师", "服务员"),
    ("画家", "摄影师"),
    ("沙发", "椅子"),
    ("冰箱", "空调"),
    ("牙刷", "梳子"),
    ("眼镜", "墨镜"),
    ("手套", "袜子"),
    ("围巾", "领带"),
    ("书包", "手提包"),
    ("日记", "信"),
    ("字典", "百科"),
    ("小说", "漫画"),
]

# 卧底游戏配置
UNDERCOVER_MIN_PLAYERS = 4
UNDERCOVER_MAX_PLAYERS = 10
UNDERCOVER_DESC_TIME = 60      # 描述环节秒数
UNDERCOVER_VOTE_TIME = 60      # 投票环节秒数
UNDERCOVER_MAX_ROUNDS = 6      # 最多描述轮数，用完卧底还没被抓出来算卧底赢

# 兼容旧名（现有 undercover_service 仍 import，等卧底组升级后删除）
UNDERCOVER_DESC_ROUNDS = UNDERCOVER_MAX_ROUNDS
