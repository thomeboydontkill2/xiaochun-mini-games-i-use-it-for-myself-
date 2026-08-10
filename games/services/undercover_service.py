# -*- coding: utf-8 -*-
"""
谁是卧底服务 —— 多人游戏
改版：公屏按钮触发 Modal 填描述 → 全员提交后统一展示 → 公屏按钮触发 ephemeral 投票 → 公屏展示淘汰
"""

import random
import asyncio
import logging
from src.chat.features.games.config.games_config import (
    UNDERCOVER_WORD_PAIRS, UNDERCOVER_MIN_PLAYERS, UNDERCOVER_MAX_PLAYERS,
    UNDERCOVER_DESC_TIME, UNDERCOVER_VOTE_TIME, UNDERCOVER_DESC_ROUNDS,
    MIN_BET, MAX_BET,
    LIFE_GAMBLE_MUTE_MINUTES, LIFE_GAMBLE_REWARD_MIN, LIFE_GAMBLE_REWARD_MAX,
    LIFE_GAMBLE_BRAVE_MULTIPLIER,
)

log = logging.getLogger(__name__)

# 16 条私发词的开场白（{word}=词），随机抽一条，所有人格式一样（卧底自己不知道是卧底）
WORD_DM_MESSAGES = [
    "你的词是「{word}」哦。描述的时候别太明显，会被发现的~<偷笑>",
    "拿着这个词——「{word}」。嘛，怎么描述就看你的了。<微笑>",
    "嘘，你的词是「{word}」。别直接说出来哦，我会看你们怎么表演的。<好奇>",
    "词给你了：「{word}」。哦哦，这个有意思，别露馅哦。<得意>",
    "你的词：「{word}」。能不能混过去就看你的嘴皮子了。<鬼脸>",
    "拿好——「{word}」。提示一下，别描述得太准，也别太离谱。<乖巧>",
    "词是「{word}」。嘛，我倒要看看你怎么描述它。<吃瓜>",
    "你的词：「{word}」。别紧张，越紧张越容易被怀疑哦~<偷笑>",
    "词给你：「{word}」。嘛，能不能蒙混过关，看你嘴皮子功夫了。<鬼脸>",
    "你的词：「{word}」。别太紧张，越紧张越容易被怀疑哦~<偷笑>",
    "拿着——「{word}」。哦哦，这个我熟，看你表演了。<得意>",
    "词是「{word}」。嘘，别让别人看到这条消息。<乖巧>",
    "你的词：「{word}」。嘛，描述得太好和太差都危险哦。<微笑>",
    "拿好这个词——「{word}」。我赌你能不能混过去。<吃瓜>",
    "词：「{word}」。诶嘿，我有点期待你被怀疑时的表情了。<偷笑>",
    "你的词是「{word}」。别直接说，别太离谱，别太准——就这三条。<好奇>",
]


class UndercoverGame:
    def __init__(self, channel_id: int, bet: int, min_players: int = 4, max_players: int = 10):
        self.channel_id = channel_id
        self.bet = bet
        self.min_players = min_players
        self.max_players = max_players
        self.players: list[int] = []
        self.player_life: dict[int, bool] = {}
        self.player_has_coins: dict[int, bool] = {}
        self.player_words: dict[int, str] = {}      # 每人的词
        self.undercover_ids: list[int] = []          # 卧底 ID 列表
        # 改版：descriptions[user_id] = 本轮描述（每轮重置）
        self.descriptions: dict[int, str] = {}
        self.current_round: int = 0
        self.phase: str = "joining"                   # joining/describing/voting/ended
        self.eliminated: list[int] = []               # 被淘汰的玩家
        self.votes: dict[int, int] = {}                # voter_id -> target_id
        self.submitted_this_round: set[int] = set()   # 本轮已提交描述的玩家


class UndercoverService:
    def __init__(self):
        self._active_games: dict[int, UndercoverGame] = {}

    def validate_bet(self, bet: int) -> tuple[bool, str]:
        if bet < MIN_BET:
            return False, f"最少下注 {MIN_BET} 币。"
        if bet > MAX_BET:
            return False, f"最多下注 {MAX_BET} 币。"
        return True, ""

    def has_game(self, channel_id: int) -> bool:
        return channel_id in self._active_games

    def get_game(self, channel_id: int) -> UndercoverGame | None:
        return self._active_games.get(channel_id)

    def create_game(self, channel_id: int, bet: int, min_players: int = 4, max_players: int = 10) -> UndercoverGame:
        game = UndercoverGame(channel_id, bet, min_players, max_players)
        self._active_games[channel_id] = game
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
        if len(game.players) < game.min_players:
            return False, f"至少要 {game.min_players} 人才能开始", None
        if game.phase != "joining":
            return False, "游戏已经开始", None

        # 选词对
        civilian_word, undercover_word = random.choice(UNDERCOVER_WORD_PAIRS)

        # 选卧底（4-6人=1卧底，7-10人=2卧底）
        num_undercover = 1 if len(game.players) <= 6 else 2
        undercover_ids = random.sample(game.players, num_undercover)

        # 发词
        for pid in game.players:
            if pid in undercover_ids:
                game.player_words[pid] = undercover_word
            else:
                game.player_words[pid] = civilian_word
        game.undercover_ids = undercover_ids
        game.phase = "describing"
        game.current_round = 1
        game.submitted_this_round = set()
        game.descriptions = {}
        return True, "游戏开始", game

    def active_players(self, game: UndercoverGame) -> list[int]:
        """未淘汰的玩家"""
        return [p for p in game.players if p not in game.eliminated]

    def submit_desc_anytime(self, channel_id: int, user_id: int, desc: str) -> dict:
        """改版：随时提交描述（不按顺序）。返回 {accepted, all_done, error}"""
        game = self._active_games.get(channel_id)
        if not game or game.phase != "describing":
            return {"error": "不在描述环节"}
        if user_id in game.eliminated or user_id not in game.players:
            return {"error": "你不在这场游戏里"}
        if user_id in game.submitted_this_round:
            return {"error": "你本轮已经提交过描述了"}
        if not desc or not desc.strip():
            return {"error": "描述不能为空"}
        desc = desc.strip()[:200]  # 限制长度
        game.descriptions[user_id] = desc
        game.submitted_this_round.add(user_id)

        active = self.active_players(game)
        all_done = len(game.submitted_this_round) >= len(active)
        return {
            "accepted": True,
            "all_done": all_done,
            "submitted_count": len(game.submitted_this_round),
            "total": len(active),
        }

    def get_descriptions_for_display(self, game: UndercoverGame) -> list[tuple[int, str]]:
        """返回 [(user_id, desc)] 供公屏展示（显示用户名）"""
        active = self.active_players(game)
        return [(pid, game.descriptions.get(pid, "（未提交）")) for pid in active]

    def enter_voting(self, game: UndercoverGame) -> None:
        """进入投票环节"""
        game.phase = "voting"
        game.votes = {}

    def submit_vote(self, channel_id: int, voter_id: int, target_id: int) -> dict:
        game = self._active_games.get(channel_id)
        if not game or game.phase != "voting":
            return {"error": "不在投票环节"}
        if voter_id in game.eliminated:
            return {"error": "你已被淘汰，不能投票"}
        if voter_id in game.votes:
            return {"error": "你已经投过票了"}
        if target_id not in game.players or target_id in game.eliminated:
            return {"error": "目标无效"}
        if target_id == voter_id:
            return {"error": "不能投自己"}

        game.votes[voter_id] = target_id

        # 检查是否所有人投完
        active_voters = self.active_players(game)
        if len(game.votes) >= len(active_voters):
            return self._tally_votes(game)
        return {"accepted": True, "votes_count": len(game.votes), "needed": len(active_voters)}

    def _tally_votes(self, game: UndercoverGame) -> dict:
        vote_count: dict[int, int] = {}
        for target in game.votes.values():
            vote_count[target] = vote_count.get(target, 0) + 1

        max_votes = max(vote_count.values())
        candidates = [p for p, v in vote_count.items() if v == max_votes]

        if len(candidates) > 1:
            # 平票，随机选一个淘汰
            eliminated = random.choice(candidates)
        else:
            eliminated = candidates[0]

        game.eliminated.append(eliminated)
        game.votes = {}

        # 检查胜负
        active_players = [p for p in game.players if p not in game.eliminated]
        active_undercover = [p for p in game.undercover_ids if p not in game.eliminated]
        active_civilians = [p for p in active_players if p not in game.undercover_ids]

        if len(active_undercover) == 0:
            # 平民赢
            game.phase = "ended"
            return {
                "vote_done": True, "eliminated": eliminated,
                "vote_count": vote_count,
                "game_over": True, "winner": "civilian",
                "undercover_ids": game.undercover_ids,
            }
        if len(active_undercover) >= len(active_civilians):
            # 卧底赢
            game.phase = "ended"
            return {
                "vote_done": True, "eliminated": eliminated,
                "vote_count": vote_count,
                "game_over": True, "winner": "undercover",
                "undercover_ids": game.undercover_ids,
            }

        # 继续下一轮描述
        game.phase = "describing"
        game.current_round += 1
        game.submitted_this_round = set()
        game.descriptions = {}
        return {
            "vote_done": True, "eliminated": eliminated,
            "vote_count": vote_count,
            "game_over": False, "next_phase": "describing",
            "next_round": game.current_round,
        }

    def cancel_game(self, channel_id: int) -> bool:
        if channel_id in self._active_games:
            del self._active_games[channel_id]
            return True
        return False

    def _cleanup(self, channel_id: int):
        self._active_games.pop(channel_id, None)

    async def settle(self, game: UndercoverGame, winner_side: str, guild=None) -> dict:
        """结算。winner_side: 'civilian' 或 'undercover'
        赌命规则（和死斗一致，禁言统一2分钟不翻倍）：
        - 赌币赢家：平分输家赌注池
        - 赌命赢家(有钱) + 输家赌币：系统奖励 + 1.5x 输家赌注（勇敢奖励）
        - 赌命赢家(有钱) + 输家赌命：系统奖励
        - 赌命赢家(没钱)：系统奖励
        - 赌币输家：扣币
        - 赌命输家：禁言2分钟
        """
        from src.chat.features.odysseia_coin.service.coin_service import coin_service
        from src.chat.features.abuse_guard.service.abuse_guard_service import abuse_guard_service

        result = {"winner_side": winner_side}

        # 赢家/输家列表
        if winner_side == "civilian":
            winners = [p for p in game.players if p not in game.undercover_ids]
            losers = [p for p in game.players if p in game.undercover_ids]
        else:
            winners = game.undercover_ids.copy()
            losers = [p for p in game.players if p not in game.undercover_ids]

        # 统计输家中有多少是赌币的（用于勇敢奖励计算）
        coin_losers = [p for p in losers if not game.player_life.get(p, False)]

        # 结算每个赢家
        for winner_id in winners:
            winner_life = game.player_life.get(winner_id, False)
            winner_has_coins = game.player_has_coins.get(winner_id, False)

            if not winner_life:
                # 赌币赢家：平分输家的赌注
                total_pool = game.bet * len(coin_losers)
                share = total_pool // max(1, len([w for w in winners if not game.player_life.get(w, False)]))
                try:
                    await coin_service.add_coins(winner_id, share, "卧底赢取分赃")
                except Exception:
                    log.exception("卧底分赃失败")
            else:
                # 赌命赢家：系统奖励
                reward = random.randint(LIFE_GAMBLE_REWARD_MIN, LIFE_GAMBLE_REWARD_MAX)
                # 有钱赌命 + 输家有赌币的：额外 1.5x 勇敢奖励（每个赌币输家的赌注 × 1.5 ÷ 赌命赢家数）
                if winner_has_coins and coin_losers:
                    life_winners = [w for w in winners if game.player_life.get(w, False) and game.player_has_coins.get(w, False)]
                    brave_pool = int(game.bet * LIFE_GAMBLE_BRAVE_MULTIPLIER * len(coin_losers))
                    brave_share = brave_pool // max(1, len(life_winners))
                    try:
                        await coin_service.add_coins(winner_id, reward + brave_share, "卧底赌命勇敢者奖励")
                    except Exception:
                        log.exception("卧底勇敢奖励失败")
                else:
                    # 没钱赌命 或 输家都赌命：只拿系统奖励
                    try:
                        await coin_service.add_coins(winner_id, reward, "卧底赌命系统奖励")
                    except Exception:
                        log.exception("卧底奖励失败")

        # 结算每个输家
        for loser_id in losers:
            loser_life = game.player_life.get(loser_id, False)
            if loser_life:
                # 赌命输家：禁言2分钟（统一，不翻倍）
                await abuse_guard_service.punish_with_mute(loser_id, LIFE_GAMBLE_MUTE_MINUTES, guild)
            else:
                # 赌币输家：扣币
                try:
                    await coin_service.remove_coins(loser_id, game.bet, "卧底输掉")
                except Exception:
                    log.exception("卧底扣币失败")

        self._cleanup(game.channel_id)
        return result


undercover_service = UndercoverService()
