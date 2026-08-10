# 小春娘小游戏模块 (Xiaochun Mini-Games)

Discord 机器人小游戏模块，包含 5 个小游戏 + 1 个网页版 21 点。

## 游戏列表

| 游戏 | 命令 | 说明 |
|---|---|---|
| 🎰 幸运轮盘 | `/roulette` | 下注抽倍率，负倍率赔钱/禁言，正倍率赚钱，庄家优势约 -15% |
| 🧨 爆炸猫 | `/bomb` | 多人轮流翻牌，翻到炸弹即输 |
| 🕵️ 谁是卧底 | `/undercover` | 3-8 人拿词，描述+投票找出卧底 |
| ⚔️ 决斗 | `/duel` | 双人对决，可下注、可赌命（输了禁言） |
| 🃏 21 点 | `/blackjack` | 经典 21 点，对赌 AI 庄家 |
| 🌐 21 点网页版 | `blackjack-web/` | Vue + FastAPI 实现，含完整扑克牌图库 |

## 目录结构

```
games/
├── cogs/        # Discord 命令入口（每个游戏一个 Cog）
├── services/    # 游戏核心逻辑（发牌、概率、计分、下注结算）
├── ui/          # Discord 交互界面（按钮 View）
├── config/      # 配置（下注限制、轮盘倍率、卧底词库、21点配置）
└── blackjack-web/  # 网页版 21 点（Vue 3 + Vite 前端 + FastAPI 后端）
```

## 外部依赖（重要）

本模块**不能独立运行**，它是宿主 Discord 机器人（Odysseia-Guidance）的一部分，依赖以下外部模块：

| 依赖 | 用途 |
|---|---|
| `src.chat.features.odysseia_coin.service.coin_service` | 金币系统（下注、结算、余额） |
| `src.chat.utils.database` (`chat_db_manager`) | 数据库访问（用户数据、全局设置、黑名单等） |
| `src.chat.features.work` (`work_db_service`) | 工作/打工数据服务 |
| `discord.py` | Discord bot 框架 |
| `fastapi` / `httpx` / `cachetools` | 网页版 21 点后端（`app.py`） |

设计约束：
- 所有下注相关的函数都应通过 `coin_service` 结算，不要直接操作数据库余额
- 游戏状态可存内存（单进程），但涉及持久化的数据（余额、禁言记录）必须走宿主服务
- 修改时保持 `services`（纯逻辑）与 `ui`（界面）分离，方便测试和复用

## 主要配置

- `config/games_config.py`：下注限制（MIN_BET=10 / MAX_BET=1000 / DEFAULT_BET=50）、轮盘倍率权重表、卧底词库、赌命机制（禁言时长、奖励范围）
- `config/blackjack_config.py`：21 点规则配置

## 修复与优化提示

- 轮盘倍率在 `games_config.py` 的 `ROULETTE_MULTIPLIERS`（字典：倍率→权重），期望值为负（庄家优势）
- 赌命机制：`LIFE_GAMBLE_*` 常量控制（输了小春娘不理人、Discord 真禁言、勇敢者奖励倍率）
- 网页版 21 点前后端通过 HTTP API 通信（见 `blackjack-web/app.py`），前端在 `blackjack-web/src/`