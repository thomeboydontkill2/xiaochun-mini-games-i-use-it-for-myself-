# -*- coding: utf-8 -*-
"""
传炸弹服务 —— 多人游戏，炸弹持有者自行选择传给谁，随机倒计时爆炸。

相比旧版的修复与改动：
- 【新玩法】传递不再固定顺序轮转：持有者从存活玩家里自选目标（不能传自己）。
  持有者超时不传（BOMB_PASS_TIME 秒）→ 炸弹当场爆炸。
- pass_bomb 校验目标合法性，并发点击由持有者校验天然挡掉。
- 赌命局幸存者奖励要求传递次数 ≥ 人数（BOMB_MIN_PASSES_FOR_REWARD 倍），防止两人开局秒爆刷系统奖励；
  且奖励走每日限次。
- 结算失败不再静默：逐人记录成功/失败，返回给 UI 展示。
- 对局注册表带 TTL。
"""

import random
import time
import logging
from src.chat.features.games.config.games_config import (
    BOMB_MIN_PLAYERS, BOMB_MAX_PLAYERS, BOMB_MIN_TIMER, BOMB_MAX_TIMER,
    BOMB_MIN_PASSES_FOR_REWARD, LIFE_GAMBLE_MUTE_MINUTES,
)
from src.chat.features.games.services import betting
from src.chat.features.games.services.registry import GameRegistry

log = logging.getLogger(__name__)

# 传递文案模板（{holder}=当前持有者，{next}=下一位，{count}=已传递次数）
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
    "📱 <@{holder}> 手机响了，来电显示“鬼”。\n塞给：<@{next}>（已传递 {count} 次）",
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
        self.players: list[int] = []
        self.player_life: dict[int, bool] = {}
        self.player_has_coins: dict[int, bool] = {}
        self.holder_id: int = 0            # 当前持炸弹者（user_id，不再用 index）
        self.timer: int = 0                # 剩余虚拟秒数（引信）
        self.exploded: bool = False
        self.started: bool = False
        self.pass_count: int = 0
        self.start_time: float = 0.0
        self.message_token: int = 0        # 每次传递递增，作废旧按钮
        self.holder_since: float = 0.0     # 持有者拿到炸弹的时刻，防手快


class BombService:
    def __init__(self):
        self._active_games: GameRegistry[BombGame] = GameRegistry()

    def validate_bet(self, bet: int) -> tuple[bool, str]:
        return betting.validate_bet(bet)

    def has_game(self, channel_id: int) -> bool:
        return channel_id in self._active_games

    def get_game(self, channel_id: int) -> BombGame | None:
        return self._active_games.get(channel_id)

    def create_game(self, channel_id: int, bet: int) -> BombGame:
        game = BombGame(bet, channel_id)
        self._active_games.set(channel_id, game)
        return game

    def add_player(self, channel_id: int, user_id: int, is_life: bool, has_coins: bool) -> tuple[bool, str]:
        game = self._active_games.get(channel_id)
        if not game:
            return False, "没有进行中的炸弹游戏"
        if game.started:
            return False, "游戏已开始，不能加入"
        if user_id in game.players:
            return False, "你已经加入了"
        if len(game.players) >= BOMB_MAX_PLAYERS:
            return False, f"人满了（最多 {BOMB_MAX_PLAYERS} 人）"
        game.players.append(user_id)
        game.player_life[user_id] = is_life
        game.player_has_coins[user_id] = has_coins
        return True, f"加入成功（当前 {len(game.players)} 人）"

    def start_game(self, channel_id: int) -> tuple[bool, str, BombGame | None]:
        game = self._active_games.get(channel_id)
        if not game:
            return False, "没有炸弹游戏", None
        if game.started:
            return False, "游戏已经开始了", None
        if len(game.players) < BOMB_MIN_PLAYERS:
            return False, f"至少要 {BOMB_MIN_PLAYERS} 人才能开始", None
        game.started = True
        game.holder_id = random.choice(game.players)
        game.timer = random.randint(BOMB_MIN_TIMER, BOMB_MAX_TIMER)
        game.start_time = time.time()
        game.holder_since = time.time()
        game.message_token = 0
        return True, "游戏开始", game

    def pass_targets(self, game: BombGame, guild=None) -> list[int]:
        """当前持有者可选的传递目标：除自己外的存活玩家。
        如果传入 guild，过滤掉已离线/不在频道的成员。"""
        targets = []
        for p in game.players:
            if p == game.holder_id:
                continue
            if guild is not None:
                member = guild.get_member(p)
                if member is None:
                    continue  # 不在频道
            targets.append(p)
        return targets

    def reassign_if_holder_invalid(self, game: BombGame, guild) -> bool:
        """持有者失效（退群/离线）时随机重分配给存活成员。返回是否重分配。"""
        if guild is None:
            return False
        member = guild.get_member(game.holder_id)
        if member is not None:
            return False  # 持有者还在
        alive = [p for p in game.players if guild.get_member(p) is not None]
        if not alive:
            return False  # 没人能接
        game.holder_id = random.choice(alive)
        game.holder_since = time.time()
        game.message_token += 1
        return True

    def pass_bomb(self, channel_id: int, from_user: int, target_id: int,
                  expected_token: int | None = None) -> dict:
        """持有者把炸弹传给指定目标。
        返回 {passed, new_holder, message, pass_count} 或 {exploded, ...} 或 {error}。
        expected_token 用于作废旧按钮（None 则不校验）。"""
        game = self._active_games.get(channel_id)
        if not game or not game.started or game.exploded:
            return {"error": "游戏没在进行中"}
        if game.holder_id != from_user:
            return {"error": "炸弹不在你手上！"}
        # messageToken 校验：旧 token 的点击一律拒绝
        if expected_token is not None and expected_token != game.message_token:
            return {"error": "stale_token"}
        # 防手快：炸弹刚到手 1 秒内不能传
        if time.time() - game.holder_since < 1.0:
            return {"error": "手太快了，稍等一下再传"}
        if target_id == from_user:
            return {"error": "不能传给自己，别耍赖。"}
        if target_id not in game.players:
            return {"error": "这个人不在游戏里。"}

        # 每次传递消耗 3-8 秒虚拟引信
        game.timer -= random.randint(3, 8)
        game.pass_count += 1
        game.message_token += 1

        if game.timer <= 0:
            return self._explode(game, exploded_on=from_user)

        game.holder_id = target_id
        game.holder_since = time.time()
        message = random.choice(PASS_MESSAGES).format(
            holder=from_user, next=target_id, count=game.pass_count
        )
        return {
            "passed": True,
            "new_holder": target_id,
            "message": message,
            "pass_count": game.pass_count,
            "token": game.message_token,
        }

    def timeout_explode(self, channel_id: int, holder_id: int) -> dict:
        """持有者超时没传：炸弹当场爆炸。UI 的超时回调调用。"""
        game = self._active_games.get(channel_id)
        if not game or not game.started or game.exploded:
            return {"error": "游戏没在进行中"}
        if game.holder_id != holder_id:
            return {"error": "持有者已变化"}
        return self._explode(game, exploded_on=holder_id, timed_out=True)

    def _explode(self, game: BombGame, exploded_on: int, timed_out: bool = False) -> dict:
        game.exploded = True
        duration = int(time.time() - game.start_time)
        self._active_games.pop(game.channel_id)
        return {
            "exploded": True, "loser_id": exploded_on, "game": game,
            "pass_count": game.pass_count, "duration": duration, "timed_out": timed_out,
        }

    def cancel_game(self, channel_id: int) -> bool:
        return self._active_games.pop(channel_id) is not None

    async def settle(self, game: BombGame, loser_id: int, guild=None) -> dict:
        """结算输家与幸存者。"""
        from src.chat.features.abuse_guard.service.abuse_guard_service import abuse_guard_service

        loser_life = game.player_life.get(loser_id, False)
        bet = game.bet
        survivors = [p for p in game.players if p != loser_id]
        result = {"loser_id": loser_id, "bet": bet}

        if loser_life:
            await abuse_guard_service.punish_with_mute(loser_id, LIFE_GAMBLE_MUTE_MINUTES, guild)
            # 防刷：局太短（传递次数 < 人数 × 系数）不发幸存奖励
            min_passes = len(game.players) * BOMB_MIN_PASSES_FOR_REWARD
            survivor_reward = 0
            if game.pass_count >= min_passes:
                for pid in survivors:
                    granted = await betting.grant_life_reward(pid, "传炸弹幸存奖励")
                    survivor_reward = max(survivor_reward, granted)
            return {
                **result, "mode": "life",
                "loser_muted": LIFE_GAMBLE_MUTE_MINUTES,
                "survivor_reward": survivor_reward,
                "reward_skipped": game.pass_count < min_passes,
            }

        # 赌币：扣输家，幸存者平分；扣款失败则不派彩（防凭空造币）
        if not await betting.deduct(loser_id, bet, "传炸弹爆炸"):
            return {**result, "mode": "coin", "settle_failed": True, "loser_loss": 0, "survivor_reward": 0, "loser_muted": 0}
        survivor_share = bet // max(1, len(survivors))
        for pid in survivors:
            await betting.credit(pid, survivor_share, "传炸弹幸存分赃")
        return {
            **result, "mode": "coin",
            "loser_loss": bet, "survivor_reward": survivor_share, "loser_muted": 0,
        }


bomb_service = BombService()
