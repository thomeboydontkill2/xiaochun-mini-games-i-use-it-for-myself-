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

# 死斗
DUEL_ROUND_TIME = 30        # 每轮出拳时限（秒），超时判弃权
DUEL_MAX_ROUNDS = 7         # 最多多少轮仍未分胜负则流局

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
    # ---- 以下为用户补充的词对 ----
    ("果粒橙", "鲜橙多"),
    ("小矮人", "葫芦娃"),
    ("土豆粉", "酸辣粉"),
    ("书本", "杂志"),
    ("自行车", "摩托车"),
    ("饭桶", "饭碗"),
    ("董永", "许仙"),
    ("双胞胎", "龙凤胎"),
    ("皇帝", "太子"),
    ("鸭舌帽", "遮阳帽"),
    ("绿茶", "苦茶"),
    ("金丝猴", "大白兔"),
    ("奖牌", "金牌"),
    ("卷发", "直发"),
    ("脸盆", "水桶"),
    ("流星花园", "花样男子"),
    ("情人节", "光棍节"),
    ("美人心计", "倾世皇妃"),
    ("白天", "晚上"),
    ("蝴蝶", "蜜蜂"),
    ("长城", "故宫"),
    ("玻璃", "镜子"),
    ("香港", "台湾"),
    ("口香糖", "木糖醇"),
    ("蝴蝶", "飞蛾"),
    ("披萨", "意大利面"),
    ("浴缸", "鱼缸"),
    ("状元", "冠军"),
    ("笤帚", "拖把"),
    ("玫瑰", "月季"),
    ("哈利波特", "伏地魔"),
    ("眉毛", "睫毛"),
    ("古筝", "吉他"),
    ("麦克风", "扩音器"),
    ("鱼香肉丝", "四喜丸子"),
    ("童话", "神话"),
    ("电动车", "摩托车"),
    ("饼干", "薯片"),
    ("冠军", "第一"),
    ("班主任", "辅导员"),
    ("近视眼镜", "隐形眼镜"),
    ("枕头", "抱枕"),
    ("龙凤呈祥", "鸳鸯戏水"),
    ("电脑", "ipad"),
    ("金庸", "古龙"),
    ("火车", "轮船"),
    ("降龙十八掌", "九阴白骨爪"),
    ("过山车", "碰碰车"),
    ("风扇", "空调"),
    ("魔术师", "魔法师"),
    ("福尔摩斯", "工藤新一"),
    ("江南style", "最炫民族风"),
    ("天天向上", "非诚勿扰"),
    ("汉堡包", "肉夹馍"),
    ("橙子", "橘子"),
    ("猫咪", "小狗"),
    ("保安", "保镖"),
    ("钻石", "红宝石"),
    ("餐巾纸", "湿巾"),
    ("包青天", "狄仁杰"),
    ("手", "脚"),
    ("班主任", "班长"),
    ("红烧牛肉面", "香辣牛肉面"),
    ("作文", "论文"),
    ("鸡蛋", "鸭蛋"),
    ("气泡", "水泡"),
    ("眉毛", "胡须"),
    ("婚纱", "喜服"),
    ("盒子", "箱子"),
    ("神雕侠侣", "天龙八部"),
    ("莎士比亚", "莫泊桑"),
    ("铁观音", "碧螺春"),
    ("手机", "座机"),
    ("作家", "编剧"),
    ("辣椒", "芥末"),
    ("胖子", "肥肉"),
    ("电话", "手机"),
    ("新年", "跨年"),
    ("泡泡糖", "棒棒糖"),
    ("杭州", "苏州"),
    ("纸巾", "手帕"),
    ("油条", "麻花"),
    ("同学", "同桌"),
    ("首尔", "东京"),
    ("海豚", "海狮"),
    ("唇膏", "口红"),
    ("电影", "电视剧"),
    ("森马", "以纯"),
    ("酸菜鱼", "水煮鱼"),
    ("纸巾", "湿巾"),
    ("烤肉", "涮肉"),
    ("楼梯", "电梯"),
    ("麻雀", "乌鸦"),
    ("盗墓笔记", "鬼吹灯"),
    ("手机", "电话座机"),
    ("脚踏车", "自行车"),
    ("哈密瓜", "西瓜"),
    ("裸婚", "闪婚"),
    ("夏家三千金", "爱情睡醒了"),
    ("反弹琵琶", "乱弹棉花"),
    ("妈妈", "娘"),
    ("豆浆", "牛奶"),
    ("菠萝蜜", "榴莲"),
    ("蜘蛛侠", "蜘蛛精"),
    ("宫锁心玉", "宫锁珠帘"),
    ("高跟鞋", "增高鞋"),
    ("白菜", "生菜"),
    ("量子力学", "相对论"),
    ("筷子", "竹签"),
    ("水盆", "水桶"),
    ("丑小鸭", "灰姑娘"),
    ("星星", "萤火虫"),
    ("老佛爷", "老天爷"),
    ("吉他", "琵琶"),
    ("节节高升", "票房大卖"),
    ("梁山伯与祝英台", "罗密欧与朱丽叶"),
    ("男朋友", "前男友"),
    ("端午节", "中秋节"),
    ("寿司", "泡菜"),
    ("生活费", "零花钱"),
    ("成吉思汗", "努尔哈赤"),
    ("电风扇", "空调"),
    ("晨光", "真彩"),
    ("鼠目寸光", "井底之蛙"),
    ("自行车", "电动车"),
    ("剩女", "御姐"),
    ("粉丝", "米线"),
    ("警察", "捕快"),
    ("小品", "话剧"),
    ("若曦", "晴川"),
    ("洗发露", "护发素"),
    ("钢铁侠", "美国队长"),
    ("被子", "床单"),
    ("干洗机", "甩干机"),
    ("太监", "人妖"),
    ("壁纸", "贴画"),
    ("贵妃醉酒", "黛玉葬花"),
    ("勇往直前", "全力以赴"),
    ("洗衣粉", "皂角粉"),
    ("甄嬛传", "红楼梦"),
    ("胡子", "眉毛"),
    ("鹅毛", "鸡毛"),
    ("包子", "饺子"),
    ("富二代", "高富帅"),
    ("铅笔", "钢笔"),
    ("两小无猜", "青梅竹马"),
    ("麻婆豆腐", "皮蛋豆腐"),
    ("葡萄", "提子"),
    ("直尺", "三角板"),
    ("图书馆", "图书店"),
    ("钢笔", "中性笔"),
    ("夜宵", "烧烤"),
    ("语无伦次", "词不达意"),
    ("十面埋伏", "四面楚歌"),
    ("薰衣草", "满天星"),
    ("小笼包", "灌汤包"),
    ("牛肉干", "猪肉脯"),
    ("结婚", "订婚"),
    ("动物", "植物"),
    ("芭蕾舞", "拉丁舞"),
]

# 卧底游戏配置
UNDERCOVER_MIN_PLAYERS = 4
UNDERCOVER_MAX_PLAYERS = 10
UNDERCOVER_DESC_TIME = 60      # 描述环节秒数
UNDERCOVER_VOTE_TIME = 60      # 投票环节秒数
UNDERCOVER_MAX_ROUNDS = 6      # 最多描述轮数，用完卧底还没被抓出来算卧底赢

# 兼容旧名（现有 undercover_service 仍 import，等卧底组升级后删除）
UNDERCOVER_DESC_ROUNDS = UNDERCOVER_MAX_ROUNDS

# ===== 狼人杀（新增段落）=====

WEREWOLF_MIN_PLAYERS = 6
WEREWOLF_MAX_PLAYERS = 16
WEREWOLF_DEFAULT_BET = 50          # 报名局费（春春币），胜方阵营平分败方下注池

# 角色标识（内部用中文字符串，直接就是展示名）
ROLE_WOLF = "狼人"
ROLE_VILLAGER = "平民"
ROLE_SEER = "预言家"
ROLE_WITCH = "女巫"
ROLE_HUNTER = "猎人"
ROLE_GUARD = "守卫"
ROLE_IDIOT = "白痴"

# 神职（用于配比表可读性；平民不列，按剩余人数自动补齐）
WEREWOLF_GOD_ROLES = (ROLE_SEER, ROLE_WITCH, ROLE_HUNTER, ROLE_GUARD, ROLE_IDIOT)

# 按人数分配角色（不含平民，平民 = 总人数 - 表内数量之和）
# 想调整板子只改这张表；改完跑 tests/test_werewolf.py 里的配比测试确认没有人数溢出。
WEREWOLF_ROLE_TABLE: dict[int, dict[str, int]] = {
    6:  {ROLE_WOLF: 2, ROLE_SEER: 1, ROLE_WITCH: 1},
    7:  {ROLE_WOLF: 2, ROLE_SEER: 1, ROLE_WITCH: 1},
    8:  {ROLE_WOLF: 2, ROLE_SEER: 1, ROLE_WITCH: 1, ROLE_HUNTER: 1},
    9:  {ROLE_WOLF: 2, ROLE_SEER: 1, ROLE_WITCH: 1, ROLE_HUNTER: 1},
    10: {ROLE_WOLF: 3, ROLE_SEER: 1, ROLE_WITCH: 1, ROLE_HUNTER: 1, ROLE_GUARD: 1},
    11: {ROLE_WOLF: 3, ROLE_SEER: 1, ROLE_WITCH: 1, ROLE_HUNTER: 1, ROLE_GUARD: 1},
    12: {ROLE_WOLF: 4, ROLE_SEER: 1, ROLE_WITCH: 1, ROLE_HUNTER: 1, ROLE_GUARD: 1, ROLE_IDIOT: 1},
    13: {ROLE_WOLF: 4, ROLE_SEER: 1, ROLE_WITCH: 1, ROLE_HUNTER: 1, ROLE_GUARD: 1, ROLE_IDIOT: 1},
    14: {ROLE_WOLF: 4, ROLE_SEER: 1, ROLE_WITCH: 1, ROLE_HUNTER: 1, ROLE_GUARD: 1, ROLE_IDIOT: 1},
    15: {ROLE_WOLF: 4, ROLE_SEER: 1, ROLE_WITCH: 1, ROLE_HUNTER: 1, ROLE_GUARD: 1, ROLE_IDIOT: 1},
    16: {ROLE_WOLF: 4, ROLE_SEER: 1, ROLE_WITCH: 1, ROLE_HUNTER: 1, ROLE_GUARD: 1, ROLE_IDIOT: 1},
}

# 各阶段时限（秒）—— 快节奏
WEREWOLF_NIGHT_ACTION_TIME = 30    # 每个夜晚角色的操作时限，超时视为不使用技能
WEREWOLF_SPEAK_TIME = 60           # 单人发言时限，超时自动跳过
WEREWOLF_VOTE_TIME = 60            # 投票时限，超时按已有票强制计票
WEREWOLF_HUNTER_SHOOT_TIME = 30    # 猎人开枪时限，超时视为不开枪
WEREWOLF_JOIN_TIME = 180           # 招募时限，超时取消对局

# 可调规则开关
WEREWOLF_GUARD_SAME_TARGET_TWICE = False  # 守卫能否连续两晚守同一人（经典规则：不能）
WEREWOLF_GUARD_CAN_PROTECT_SELF = True    # 守卫能否自守
WEREWOLF_SAVE_AND_GUARD_KILLS = True      # 同守同救是否判死（True=经典"奶穿"，False=救活）
WEREWOLF_TIE_ELIMINATES_NOBODY = True     # 投票平票是否不淘汰（True=直接入夜）
WEREWOLF_IDIOT_KEEPS_VOTE = False         # 白痴翻牌后能否继续投票（经典：不能）

# ===== 狼人杀·警长 / 禁言 / 狼人频道（追加段）=====

WEREWOLF_ELECT_SHERIFF = True          # 是否启用警长竞选
WEREWOLF_SHERIFF_VOTE_WEIGHT = 1.5     # 警长在白天放逐投票里的票权
WEREWOLF_CAMPAIGN_SPEAK_TIME = 60      # 竞选发言单人时限（秒）
WEREWOLF_SHERIFF_VOTE_TIME = 60        # 警长投票时限（秒）
WEREWOLF_SHERIFF_SIGNUP_TIME = 30      # 上警/不上警报名时限（秒）
WEREWOLF_SHERIFF_TRANSFER_TIME = 30    # 警长死亡移交警徽时限（秒）

# 公屏禁言：填入普通玩家所属身份组 ID（int），None 则不禁言。
# 覆盖阶段：夜晚 + 警长竞选 + 白天讨论；投票和游戏结束时解禁。
# 需要机器人身份组有「管理身份组」权限，且排在该身份组之上。
WEREWOLF_MUTE_ROLE_ID: int | None = 1525913296090173500
WEREWOLF_MUTE_DURING_DISCUSSION = True

# 狼人私密频道：建在哪个分类（category）下；None 则建在触发频道同分类。
# 需要机器人有「管理频道」权限。创建失败会自动回退到私信投票模式。
WEREWOLF_WOLF_CHANNEL_CATEGORY_ID: int | None = None
WEREWOLF_USE_WOLF_CHANNEL = True


# ===== 加压俄罗斯轮盘 =====
#
# 玩法：6 弹巢开局 1 发实弹，轮流对自己扣扳机。中弹 = 禁言（当前赌注分钟）+ 出局 + 扣局费。
# 三选一：传枪（弹巢前进一格交下家）/ 再开一枪（攒连开蓄力）/ 加压（装 1+蓄力层数 发，赌注 +1 分钟/发）。
# 子弹打光 = 游戏结束；只剩 1 人 → 冠军，多人 → 平局。
# 败方平分给胜方（复用 werewolf settle 模式）。

PRESSURE_ROULETTE_MIN_PLAYERS = 3
PRESSURE_ROULETTE_MAX_PLAYERS = 6
PRESSURE_ROULETTE_DEFAULT_BET = 50          # 局费（春春币），败方每人扣这么多给胜方平分
PRESSURE_ROULETTE_CHAMBER_SIZE = 6          # 弹巢容量
PRESSURE_ROULETTE_INITIAL_LIVE = 1          # 开局装填实弹数
PRESSURE_ROULETTE_BASE_STAKE = 3           # 基础赌注（分钟），中弹禁言时长下限
PRESSURE_ROULETTE_PRESS_STAKE = 1           # 每加压 1 发子弹，赌注 +多少分钟
PRESSURE_ROULETTE_JOIN_TIME = 180           # 招募时限（秒）
PRESSURE_ROULETTE_TURN_TIME = 60            # 每回合操作时限（秒），超时自动开枪/传枪
PRESSURE_ROULETTE_FIRST_SHOT_HIT_CHANCE = 0.15  # 第一枪中弹率（不走弹巢 1/6）
PRESSURE_ROULETTE_UNLOAD_MIN_BULLETS = 3    # 抽弹开枪门槛：枪里至少 3 发
PRESSURE_ROULETTE_PANEL_HISTORY_LIMIT = 3   # 面板滚动窗口保留条数

# 中弹禁言用的身份组 ID；None 则不禁言（只扣局费）。
# 复用狼人杀的普通玩家身份组即可。
PRESSURE_ROULETTE_MUTE_ROLE_ID: int | None = 1525913296090173500
