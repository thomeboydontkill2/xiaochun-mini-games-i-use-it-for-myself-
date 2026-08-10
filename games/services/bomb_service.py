# -*- coding: utf-8 -*-
"""
传炸弹服务 —— 多人游戏，轮流传递炸弹，随机倒计时爆炸
炸弹在谁手上爆炸谁输，扣币或禁言
"""

import random
import asyncio
import time
import logging
from src.chat.features.games.config.games_config import (
    MIN_BET, MAX_BET,
    LIFE_GAMBLE_MUTE_MINUTES, LIFE_GAMBLE_REWARD_MIN, LIFE_GAMBLE_REWARD_MAX,
)

log = logging.getLogger(__name__)

MIN_PLAYERS = 2
MAX_PLAYERS = 8
MIN_TIMER = 15   # 最短倒计时秒
MAX_TIMER = 45   # 最长倒计时秒

# 25 个传递文案模板（{holder}=当前持有者，{next}=下一位，{count}=已传递次数）
PASS_MESSAGES = [
    "📦 <@{holder}> 收到了一笔特殊快递。\n包裹内容：💣\n签收人：<@{next}>（已传递 {count} 次）",
    "🍲 这锅毛肚快糊了，<@{holder}> 夹不住了！\n筷子伸向：<@{next}>（已传递 {count} 次）",
    "🥔 这山芋烫得拿不住，<@{holder}> 手都红了。\n赶紧扔给：<@{next}>（已传递 {count} 次）",
    "👻 鬼在追 <@{holder}>，符快烧完了！\n传符给：<@{next}>（已传递 {count} 次）",
    "🎤 《死了都要爱》高音来了，<@{holder}> 唱不上去！\n麦克风塞给：<@{next}>（已传递 {count} 次）",
    "📋 老板刚派的活，<@{holder}> 不想干。\n甩锅给：<@{next}>（已传递 {count} 次）",
    "🧨 红线还是蓝线？<@{holder}> 选不出来。\n先传给：<@{next}> 让他选（已传递 {count} 次）",
    "🦠 <@{holder}> 被感染了，症状显现中。\n传染给：<@{next}>（已传递 {count} 次）",
    "📚 这本书借了三年了，<@{holder}> 终于想起来要还。\n下一个受害者：<@{next}>（已传递 {count} 次）",
    "💬 前任发来消息，<@{holder}> 不想回。\n推给兄弟处理：<@{next}>（已传递 {count} 次）",
    "🧋 这杯奶茶烫嘴，<@{holder}> 喝不下去。\n递给：<@{next}>（已传递 {count} 次）",
    "📱 <@{holder}> 手机响了，来电显示\"鬼\"。\n塞给：<@{next}>（已传递 {count} 次）",
    "🥛 这瓶酸奶过期三天了，<@{holder}> 闻了一下。\n传给：<@{next}>（已传递 {count} 次）",
    "📐 这道积分题不会做，<@{holder}> 看不懂。\n抄给：<@{next}>（已传递 {count} 次）",
    "😳 <@{holder}> 在群里发错消息了，截图被截。\n传给：<@{next}> 当证据（已传递 {count} 次）",
    "🐱 这只猫跟着 <@{holder}> 走了三条街。\n塞给：<@{next}>（已传递 {count} 次）",
    "🧧 老板群里发红包了，<@{holder}> 抢到 0.01。\n转给：<@{next}>（已传递 {count} 次）",
    "💳 这张健身卡办了两年没去过，<@{holder}> 心虚。\n转让给：<@{next}>（已传递 {count} 次）",
    "🪳 桌上出现蟑螂，<@{holder}> 最怕这个。\n甩给：<@{next}>（已传递 {count} 次）",
    "🛗 电梯卡在 13 楼了，<@{holder}> 在里面。\n换 <@{next}> 进去（已传递 {count} 次）",
    "🎁 前任送的围巾，<@{holder}> 看着心烦。\n送给：<@{next}>（已传递 {count} 次）",
    "⏰ 作业今晚 23:59 截止，<@{holder}> 还没开始。\n推给：<@{next}>（已传递 {count} 次）",
    "🍱 外卖送错到 <@{holder}> 这了，不是他点的。\n转交：<@{next}>（已传递 {count} 次）",
    "📶 这家店 WiFi 连不上，<@{holder}> 问不到。\n传给：<@{next}> 试试（已传递 {count} 次）",
    "📖 讲到一半不敢讲了，<@{holder}> 声音发抖。\n换 <@{next}> 接着讲（已传递 {count} 次）",
]


class BombGame:
    def __init__(self, bet: int, channel_id: int):
        self.bet = bet
        self.channel_id = channel_id
        self.players: list[int] = []          # 参与者列表
        self.player_life: dict[int, bool] = {}   # 是否赌命
        self.player_has_coins: dict[int, bool] = {}
        self.current_holder: int = 0          # 当前持炸弹者的 index
        self.timer: int = 0                   # 剩余秒数
        self.exploded: bool = False
        self.started: bool = False
        self.passing: bool = False            # 是否正在传递中
        self.pass_count: int = 0              # 总传递次数
        self.start_time: float = 0.0          # 游戏开始时间戳


class BombService:
    def __init__(self):
        self._active_games: dict[int, BombGame] = {}  # key: channel_id

    def validate_bet(self, bet: int) -> tuple[bool, str]:
        if bet < MIN_BET:
            return False, f"最少下注 {MIN_BET} 币。"
        if bet > MAX_BET:
            return False, f"最多下注 {MAX_BET} 币。"
        return True, ""

    def has_game(self, channel_id: int) -> bool:
        return channel_id in self._active_games

    def get_game(self, channel_id: int) -> BombGame | None:
        return self._active_games.get(channel_id)

    def create_game(self, channel_id: int, bet: int) -> BombGame:
        game = BombGame(bet, channel_id)
        self._active_games[channel_id] = game
        return game

    def add_player(self, channel_id: int, user_id: int, is_life: bool, has_coins: bool) -> tuple[bool, str]:
        game = self._active_games.get(channel_id)
        if not game:
            return False, "没有进行中的炸弹游戏"
        if game.started:
            return False, "游戏已开始，不能加入"
        if user_id in game.players:
            return False, "你已经加入了"
        if len(game.players) >= MAX_PLAYERS:
            return False, f"人满了（最多 {MAX_PLAYERS} 人）"
        game.players.append(user_id)
        game.player_life[user_id] = is_life
        game.player_has_coins[user_id] = has_coins
        return True, f"加入成功（当前 {len(game.players)} 人）"

    def start_game(self, channel_id: int) -> tuple[bool, str, BombGame | None]:
        game = self._active_games.get(channel_id)
        if not game:
            return False, "没有炸弹游戏", None
        if len(game.players) < MIN_PLAYERS:
            return False, f"至少要 {MIN_PLAYERS} 人才能开始", None
        game.started = True
        game.current_holder = random.randint(0, len(game.players) - 1)
        game.timer = random.randint(MIN_TIMER, MAX_TIMER)
        game.start_time = time.time()
        return True, "游戏开始", game

    def pass_bomb(self, channel_id: int, from_user: int) -> dict:
        """传炸弹给下一个人，返回 {passed, new_holder, timer, exploded, message, pass_count, duration}"""
        game = self._active_games.get(channel_id)
        if not game or not game.started or game.exploded:
            return {"error": "游戏没在进行中"}
        if game.players[game.current_holder] != from_user:
            return {"error": "炸弹不在你手上"}

        # 消耗时间（3-8秒）
        elapsed = random.randint(3, 8)
        game.timer -= elapsed
        game.pass_count += 1

        if game.timer <= 0:
            # 爆炸
            game.exploded = True
            loser_id = game.players[game.current_holder]
            duration = int(time.time() - game.start_time)
            self._cleanup(channel_id)
            return {
                "exploded": True, "loser_id": loser_id, "game": game,
                "pass_count": game.pass_count, "duration": duration,
            }

        # 传给下一个人
        game.current_holder = (game.current_holder + 1) % len(game.players)
        new_holder = game.players[game.current_holder]
        message = random.choice(PASS_MESSAGES).format(
            holder=from_user, next=new_holder, count=game.pass_count
        )
        return {
            "passed": True,
            "new_holder": new_holder,
            "timer": game.timer,
            "message": message,
            "pass_count": game.pass_count,
        }

    def cancel_game(self, channel_id: int) -> bool:
        if channel_id in self._active_games:
            del self._active_games[channel_id]
            return True
        return False

    def _cleanup(self, channel_id: int):
        self._active_games.pop(channel_id, None)

    async def settle(self, game: BombGame, loser_id: int, guild=None) -> dict:
        """结算输家。"""
        from src.chat.features.odysseia_coin.service.coin_service import coin_service
        from src.chat.features.abuse_guard.service.abuse_guard_service import abuse_guard_service

        loser_life = game.player_life.get(loser_id, False)
        loser_has_coins = game.player_has_coins.get(loser_id, False)
        bet = game.bet

        result = {"loser_id": loser_id, "bet": bet}

        if loser_life:
            # 赌命：禁言2分钟，其他人得系统奖励
            await abuse_guard_service.punish_with_mute(loser_id, LIFE_GAMBLE_MUTE_MINUTES, guild)
            reward = random.randint(LIFE_GAMBLE_REWARD_MIN, LIFE_GAMBLE_REWARD_MAX)
            for pid in game.players:
                if pid != loser_id:
                    try:
                        await coin_service.add_coins(pid, reward // max(1, len(game.players) - 1), "传炸弹幸存奖励")
                    except Exception:
                        log.exception("炸弹幸存奖励失败")
            return {**result, "mode": "life", "loser_muted": LIFE_GAMBLE_MUTE_MINUTES, "survivor_reward": reward // max(1, len(game.players) - 1)}
        else:
            # 赌币：扣币，其他人平分
            try:
                await coin_service.remove_coins(loser_id, bet, "传炸弹爆炸")
            except Exception:
                log.exception("炸弹扣币失败")
            survivor_share = bet // max(1, len(game.players) - 1)
            for pid in game.players:
                if pid != loser_id:
                    try:
                        await coin_service.add_coins(pid, survivor_share, "传炸弹幸存分赃")
                    except Exception:
                        log.exception("炸弹分赃失败")
            return {**result, "mode": "coin", "loser_loss": bet, "survivor_reward": survivor_share, "loser_muted": 0}


bomb_service = BombService()
