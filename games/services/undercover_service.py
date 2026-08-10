# -*- coding: utf-8 -*-
"""
谁是卧底服务 —— 多人游戏。
公屏按钮触发 Modal 填描述 → 全员提交后统一展示 → 公屏按钮触发 ephemeral 投票 → 公屏展示淘汰。

相比旧版的修复：
- start_game 无游戏时返回残缺元组（`return False`）导致解包崩溃 → 修正。
- 平票不再随机淘汰：本轮无人出局直接进下一轮描述（更公平）。
- 有人挂机不投票会把游戏卡死 → 新增 force_tally（UI 超时调用），按已有票强制结算；一票没有则跳过本轮。
- 描述里直接写出自己的词 → 拒绝提交。
- 轮数上限 UNDERCOVER_MAX_ROUNDS：用完卧底没被抓出来算卧底赢，杜绝无限循环。
- 结算走 betting：赌命奖励每日限次，转账失败可见。
- 对局注册表带 TTL。
"""

import random
import logging
from src.chat.features.games.config.games_config import (
    UNDERCOVER_WORD_PAIRS, UNDERCOVER_MIN_PLAYERS, UNDERCOVER_MAX_PLAYERS,
    UNDERCOVER_MAX_ROUNDS, LIFE_GAMBLE_MUTE_MINUTES, LIFE_GAMBLE_BRAVE_MULTIPLIER,
)
from src.chat.features.games.services import betting
from src.chat.features.games.services.registry import GameRegistry

log = logging.getLogger(__name__)

# 私发词的开场白（{word}=词），随机抽一条，所有人格式一样（卧底自己不知道是卧底）
WORD_DM_MESSAGES = [
    "你的词是「{word}」哦。描述的时候别太明显，会被发现的~<偷笑>",
    "拿着这个词——「{word}」。嘛，怎么描述就看你的了。<微笑>",
    "嘘，你的词是「{word}」。别直接说出来哦，我会看你们怎么表演的。<好奇>",
    "词给你了：「{word}」。哦哦，这个有意思，别露馅哦。<得意>",
    "你的词：「{word}」。能不能混过去就看你的嘴皮子了。<鬼脸>",
    "拿好——「{word}」。提示一下，别描述得太准，也别太离谱。<乖巧>",
    "词是「{word}」。嘛，我倒要看看你怎么描述它。<吃瓜>",
    "你的词：「{word}」。别紧张，越紧张越容易被怀疑哦~<偷笑>",
    "拿着——「{word}」。哦哦，这个我熟，看你表演了。<得意>",
    "词是「{word}」。嘘，别让别人看到这条消息。<乖巧>",
    "你的词：「{word}」。嘛，描述得太好和太差都危险哦。<微笑>",
    "拿好这个词——「{word}」。我赌你能不能混过去。<吃瓜>",
    "词：「{word}」。诶嘿，我有点期待你被怀疑时的表情了。<偷笑>",
    "你的词是「{word}」。别直接说，别太离谱，别太准——就这三条。<好奇>",
]


class UndercoverGame:
    def __init__(self, channel_id: int, bet: int,
                 min_players: int = UNDERCOVER_MIN_PLAYERS,
                 max_players: int = UNDERCOVER_MAX_PLAYERS):
        self.channel_id = channel_id
        self.bet = bet
        self.min_players = min_players
        self.max_players = max_players
        self.players: list[int] = []
        self.player_life: dict[int, bool] = {}
        self.player_has_coins: dict[int, bool] = {}
        self.player_words: dict[int, str] = {}
        self.undercover_ids: list[int] = []
        self.descriptions: dict[int, str] = {}
        self.current_round: int = 0
        self.phase: str = "joining"                   # joining/describing/voting/ended
        self.eliminated: list[int] = []
        self.votes: dict[int, int] = {}               # voter_id -> target_id
        self.submitted_this_round: set[int] = set()
        self.settled = False


class UndercoverService:
    def __init__(self):
        self._active_games: GameRegistry[UndercoverGame] = GameRegistry()

    def validate_bet(self, bet: int) -> tuple[bool, str]:
        return betting.validate_bet(bet)

    def has_game(self, channel_id: int) -> bool:
        return channel_id in self._active_games

    def get_game(self, channel_id: int) -> UndercoverGame | None:
        return self._active_games.get(channel_id)

    def create_game(self, channel_id: int, bet: int,
                    min_players: int = UNDERCOVER_MIN_PLAYERS,
                    max_players: int = UNDERCOVER_MAX_PLAYERS) -> UndercoverGame:
        game = UndercoverGame(channel_id, bet, min_players, max_players)
        self._active_games.set(channel_id, game)
        return game

    def add_player(self, channel_id: int, user_id: int, is_life: bool, has_coins: bool) -> tuple[bool, str]:
        game = self._active_games.get(channel_id)
        if not game:
            return False, "没有进行中的卧底游戏"
        if game.phase != "joining":
            return False, "游戏已开始，不能加入"
        if user_id in game.players:
            return False, "你已经加入了"
        if len(game.players) >= game.max_players:
            return False, f"人满了（最多 {game.max_players} 人）"
        game.players.append(user_id)
        game.player_life[user_id] = is_life
        game.player_has_coins[user_id] = has_coins
        return True, f"加入成功（当前 {len(game.players)} 人）"

    def start_game(self, channel_id: int) -> tuple[bool, str, UndercoverGame | None]:
        game = self._active_games.get(channel_id)
        if not game:
            return False, "没有卧底游戏", None
        if game.phase != "joining":
            return False, "游戏已经开始", None
        if len(game.players) < game.min_players:
            return False, f"至少要 {game.min_players} 人才能开始", None

        civilian_word, undercover_word = random.choice(UNDERCOVER_WORD_PAIRS)

        num_undercover = 1 if len(game.players) <= 6 else 2
        game.undercover_ids = random.sample(game.players, num_undercover)

        for pid in game.players:
            game.player_words[pid] = undercover_word if pid in game.undercover_ids else civilian_word

        game.phase = "describing"
        game.current_round = 1
        game.submitted_this_round = set()
        game.descriptions = {}
        return True, "游戏开始", game

    def active_players(self, game: UndercoverGame) -> list[int]:
        return [p for p in game.players if p not in game.eliminated]

    def submit_desc_anytime(self, channel_id: int, user_id: int, desc: str) -> dict:
        game = self._active_games.get(channel_id)
        if not game or game.phase != "describing":
            return {"error": "不在描述环节"}
        if user_id in game.eliminated or user_id not in game.players:
            return {"error": "你不在这场游戏里"}
        if user_id in game.submitted_this_round:
            return {"error": "你本轮已经提交过描述了"}
        if not desc or not desc.strip():
            return {"error": "描述不能为空"}
        desc = desc.strip()[:200]
        word = game.player_words.get(user_id, "")
        if word and word in desc:
            return {"error": "描述里不能直接出现你的词！换个说法。"}
        game.descriptions[user_id] = desc
        game.submitted_this_round.add(user_id)

        active = self.active_players(game)
        return {
            "accepted": True,
            "all_done": len(game.submitted_this_round) >= len(active),
            "submitted_count": len(game.submitted_this_round),
            "total": len(active),
        }

    def get_descriptions_for_display(self, game: UndercoverGame) -> list[tuple[int, str]]:
        return [(pid, game.descriptions.get(pid, "（未提交）")) for pid in self.active_players(game)]

    def enter_voting(self, game: UndercoverGame) -> None:
        game.phase = "voting"
        game.votes = {}

    def submit_vote(self, channel_id: int, voter_id: int, target_id: int) -> dict:
        game = self._active_games.get(channel_id)
        if not game or game.phase != "voting":
            return {"error": "不在投票环节"}
        if voter_id not in game.players or voter_id in game.eliminated:
            return {"error": "你不在这场游戏里"}
        if voter_id in game.votes:
            return {"error": "你已经投过票了"}
        if target_id not in game.players or target_id in game.eliminated:
            return {"error": "目标无效"}
        if target_id == voter_id:
            return {"error": "不能投自己"}

        game.votes[voter_id] = target_id

        if len(game.votes) >= len(self.active_players(game)):
            return self._tally_votes(game)
        return {"accepted": True, "votes_count": len(game.votes), "needed": len(self.active_players(game))}

    def force_tally(self, channel_id: int) -> dict:
        """投票超时：按现有票强制结算。一票没有 → 本轮无人淘汰。"""
        game = self._active_games.get(channel_id)
        if not game or game.phase != "voting":
            return {"error": "不在投票环节"}
        return self._tally_votes(game, forced=True)

    def _tally_votes(self, game: UndercoverGame, forced: bool = False) -> dict:
        vote_count: dict[int, int] = {}
        for target in game.votes.values():
            vote_count[target] = vote_count.get(target, 0) + 1
        game.votes = {}

        eliminated = None
        if vote_count:
            max_votes = max(vote_count.values())
            candidates = [p for p, v in vote_count.items() if v == max_votes]
            if len(candidates) == 1:
                eliminated = candidates[0]
                game.eliminated.append(eliminated)
            # 平票：无人淘汰

        active = self.active_players(game)
        active_undercover = [p for p in game.undercover_ids if p not in game.eliminated]
        active_civilians = [p for p in active if p not in game.undercover_ids]

        base = {
            "vote_done": True, "eliminated": eliminated, "vote_count": vote_count,
            "tie": eliminated is None and bool(vote_count), "forced": forced,
        }

        if len(active_undercover) == 0:
            game.phase = "ended"
            return {**base, "game_over": True, "winner": "civilian", "undercover_ids": game.undercover_ids}
        if len(active_undercover) >= len(active_civilians):
            game.phase = "ended"
            return {**base, "game_over": True, "winner": "undercover", "undercover_ids": game.undercover_ids}
        if game.current_round >= UNDERCOVER_MAX_ROUNDS:
            game.phase = "ended"
            return {**base, "game_over": True, "winner": "undercover",
                    "undercover_ids": game.undercover_ids, "max_rounds_reached": True}

        game.phase = "describing"
        game.current_round += 1
        game.submitted_this_round = set()
        game.descriptions = {}
        return {**base, "game_over": False, "next_phase": "describing", "next_round": game.current_round}

    def cancel_game(self, channel_id: int) -> bool:
        return self._active_games.pop(channel_id) is not None

    async def settle(self, game: UndercoverGame, winner_side: str, guild=None) -> dict:
        """结算。winner_side: 'civilian' 或 'undercover'。幂等。"""
        from src.chat.features.abuse_guard.service.abuse_guard_service import abuse_guard_service

        if game.settled:
            return {"error": "已经结算过了"}
        game.settled = True
        self._active_games.pop(game.channel_id)

        result = {"winner_side": winner_side}

        if winner_side == "civilian":
            winners = [p for p in game.players if p not in game.undercover_ids]
            losers = list(game.undercover_ids)
        else:
            winners = list(game.undercover_ids)
            losers = [p for p in game.players if p not in game.undercover_ids]

        coin_losers = [p for p in losers if not game.player_life.get(p, False)]
        coin_winners = [w for w in winners if not game.player_life.get(w, False)]

        # 先扣输家（钱到位了再分），失败的从奖池里剔除
        collected = 0
        for loser_id in losers:
            if game.player_life.get(loser_id, False):
                await abuse_guard_service.punish_with_mute(loser_id, LIFE_GAMBLE_MUTE_MINUTES, guild)
            else:
                if await betting.deduct(loser_id, game.bet, "卧底输掉"):
                    collected += game.bet

        # 赌币赢家平分实际收到的奖池
        if coin_winners and collected > 0:
            share = collected // len(coin_winners)
            for winner_id in coin_winners:
                await betting.credit(winner_id, share, "卧底赢取分赃")

        # 赌命赢家：系统奖励（限次）；有钱赌命且有赌币输家 → 额外勇敢奖励
        life_winners = [w for w in winners if game.player_life.get(w, False)]
        rich_life_winners = [w for w in life_winners if game.player_has_coins.get(w, False)]
        for winner_id in life_winners:
            reward = await betting.grant_life_reward(winner_id, "卧底赌命系统奖励")
            if winner_id in rich_life_winners and coin_losers and reward > 0:
                brave_pool = int(game.bet * (LIFE_GAMBLE_BRAVE_MULTIPLIER - 1) * len(coin_losers))
                brave_share = brave_pool // max(1, len(rich_life_winners))
                await betting.credit(winner_id, brave_share, "卧底赌命勇敢者奖励")

        return result


undercover_service = UndercoverService()
