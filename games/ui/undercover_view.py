# -*- coding: utf-8 -*-
"""
谁是卧底 UI。

修复：
- 私信发词失败（对方关了 DM）不再静默吞掉：描述界面与投票界面都有「查看我的词」按钮，全程随时查。
- 投票超时不再卡死游戏：View 超时自动按已有票强制结算并公屏公告。
- 招募超时自动取消对局。
- 平票文案明确说明"不是 bug，本轮无人淘汰，游戏继续"。
"""

import discord
from discord.ui import View, Select, Button, Modal, TextInput, select, button

from src.chat.features.games.config.games_config import UNDERCOVER_VOTE_TIME, UNDERCOVER_DESC_TIME


def _member_name(guild, uid: int) -> str:
    m = guild.get_member(uid) if guild else None
    return m.display_name if m else f"<@{uid}>"


async def _show_my_word(interaction: discord.Interaction, channel_id: int):
    """ephemeral 回显自己的词，描述阶段与投票阶段共用。"""
    from src.chat.features.games.services.undercover_service import undercover_service
    game = undercover_service.get_game(channel_id)
    if not game or interaction.user.id not in game.players:
        await interaction.response.send_message("你不在这场游戏里。", ephemeral=True)
        return
    word = game.player_words.get(interaction.user.id, "？")
    await interaction.response.send_message(f"🤫 你的词是「{word}」。别说出去。", ephemeral=True)


async def _announce_tally(channel, guild, channel_id: int, result: dict):
    """公屏公告一次计票结果（正常投完与超时强制结算共用）。"""
    from src.chat.features.games.services.undercover_service import undercover_service
    game = undercover_service.get_game(channel_id)

    vote_lines = [f"{_member_name(guild, tid)}：{cnt} 票" for tid, cnt in result.get("vote_count", {}).items()]
    text = "🗳️ **投票结束！**\n" + ("\n".join(vote_lines) if vote_lines else "一票都没有……你们在干嘛。")
    if result.get("forced"):
        text = "⏰ 投票超时，按已有票数结算。\n" + text

    if result.get("eliminated"):
        text += f"\n{_member_name(guild, result['eliminated'])} 被淘汰出局。"
    elif result.get("tie"):
        text += "\n🤝 平票！这不是 bug——按规则本轮无人淘汰，游戏继续。"
    else:
        text += "\n本轮无人淘汰。"

    if result.get("game_over"):
        names = ", ".join(_member_name(guild, uid) for uid in result["undercover_ids"])
        if result["winner"] == "civilian":
            text += f"\n🎉 **平民胜利！** 卧底是：{names}"
        else:
            text += f"\n😈 **卧底胜利！** 卧底是：{names}"
            if result.get("max_rounds_reached"):
                text += "（轮数用完还没抓出卧底）"
        if game:
            settle = await undercover_service.settle(game, result["winner"], guild)
            if not settle.get("error"):
                text += "\n结算完成，春春币/禁言已生效。"
        await channel.send(text)
    else:
        next_round = result.get("next_round", 1)
        text += f"\n\n**第{next_round}轮描述**开始，点下方按钮填写描述 👇"
        desc_view = UndercoverDescView(channel_id, next_round)
        desc_view.message = await channel.send(text, view=desc_view)


class UndercoverJoinView(View):
    """加入游戏界面"""

    def __init__(self, channel_id: int, bet: int, host_id: int):
        super().__init__(timeout=180)
        self.channel_id = channel_id
        self.bet = bet
        self.host_id = host_id
        self.started = False
        self.message: discord.Message | None = None

    async def on_timeout(self):
        if self.started:
            return
        from src.chat.features.games.services.undercover_service import undercover_service
        undercover_service.cancel_game(self.channel_id)
        for c in self.children:
            c.disabled = True
        if self.message:
            try:
                await self.message.edit(content="🎭 招募超时，这局卧底取消了。人齐了再开～", view=self)
            except discord.HTTPException:
                pass

    @button(label="加入（赌币）", style=discord.ButtonStyle.success, emoji="💰")
    async def join_coin(self, interaction: discord.Interaction, button: Button):
        await self._join(interaction, is_life=False)

    @button(label="加入（赌命）", style=discord.ButtonStyle.danger, emoji="🔥")
    async def join_life(self, interaction: discord.Interaction, button: Button):
        await self._join(interaction, is_life=True)

    @button(label="开始游戏", style=discord.ButtonStyle.primary, emoji="🎭")
    async def start(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.host_id:
            await interaction.response.send_message("只有发起人能开始。", ephemeral=True)
            return
        from src.chat.features.games.services.undercover_service import undercover_service, WORD_DM_MESSAGES
        import random as _random
        ok, msg, game = undercover_service.start_game(self.channel_id)
        if not ok:
            await interaction.response.send_message(msg, ephemeral=True)
            return
        self.started = True
        for c in self.children:
            c.disabled = True

        dm_failed: list[int] = []
        for pid in game.players:
            try:
                member = interaction.guild.get_member(pid)
                if member:
                    template = _random.choice(WORD_DM_MESSAGES)
                    await member.send(f"🎭 {template.format(word=game.player_words[pid])}")
                else:
                    dm_failed.append(pid)
            except Exception:
                dm_failed.append(pid)

        content = (
            f"🎭 **谁是卧底开始！**\n"
            f"参与：{', '.join(f'<@{p}>' for p in game.players)}\n"
            f"每人已私信收到自己的词，别直接说出来！\n"
        )
        if dm_failed:
            content += (
                f"⚠️ {', '.join(f'<@{p}>' for p in dm_failed)} 私信发不出去（可能关了 DM），"
                f"点下方「查看我的词」偷偷看。\n"
            )
        content += (
            f"\n**第{game.current_round}轮描述**\n"
            f"点下方按钮填写你的描述（只有你能看见的输入框），全员提交后统一展示！"
        )

        desc_view = UndercoverDescView(self.channel_id, game.current_round)
        await interaction.response.edit_message(content=content, view=desc_view)
        desc_view.message = await interaction.original_response()

    async def _join(self, interaction: discord.Interaction, is_life: bool):
        from src.chat.features.games.services.undercover_service import undercover_service
        from src.chat.features.games.services import betting
        bal = 0
        try:
            bal = await betting.get_balance(interaction.user.id)
        except Exception:
            pass
        has_coins = bal >= self.bet
        if not is_life and not has_coins:
            await interaction.response.send_message(
                f"你只有 {bal} 币，不够下注 {self.bet}。试试赌命加入？", ephemeral=True
            )
            return
        ok, msg = undercover_service.add_player(self.channel_id, interaction.user.id, is_life, has_coins)
        if not ok:
            await interaction.response.send_message(msg, ephemeral=True)
            return
        game = undercover_service.get_game(self.channel_id)
        await interaction.response.send_message(
            f"{interaction.user.mention} {'赌命加入 🔥' if is_life else '赌币加入 💰'}（当前 {len(game.players)} 人）",
        )


class UndercoverDescModal(Modal):
    """描述输入弹窗（只有自己看见）"""

    def __init__(self, channel_id: int, user_id: int, round_num: int):
        super().__init__(title=f"第{round_num}轮描述你的词", timeout=120)
        self.channel_id = channel_id
        self.user_id = user_id
        self.round_num = round_num
        self.desc_input = TextInput(
            label="描述你的词（别直接说出来）",
            placeholder="一句话描述，别太准也别太离谱...",
            max_length=200,
            required=True,
        )
        self.add_item(self.desc_input)

    async def on_submit(self, interaction: discord.Interaction):
        from src.chat.features.games.services.undercover_service import undercover_service
        result = undercover_service.submit_desc_anytime(self.channel_id, self.user_id, self.desc_input.value)
        if "error" in result:
            await interaction.response.send_message(result["error"], ephemeral=True)
            return

        if result.get("all_done"):
            game = undercover_service.get_game(self.channel_id)
            undercover_service.enter_voting(game)
            display = undercover_service.get_descriptions_for_display(game)
            lines = [f"**第{self.round_num}轮描述展示**"]
            for uid, d in display:
                lines.append(f"**{_member_name(interaction.guild, uid)}**：{d}")
            lines.append(f"\n全员已提交！进入投票环节（限时 {UNDERCOVER_VOTE_TIME} 秒），点下方按钮投票 👇")
            vote_view = UndercoverVoteEntryView(self.channel_id)
            await interaction.response.send_message("\n".join(lines), view=vote_view)
            vote_view.message = await interaction.original_response()
        else:
            await interaction.response.send_message(
                f"✅ 描述已提交（{result['submitted_count']}/{result['total']}）。"
                f"等其他人也填好，小春娘会统一展示。<乖巧>",
                ephemeral=True,
            )


class UndercoverDescView(View):
    """公屏描述按钮：每人点击触发自己的 Modal + 查词按钮"""

    def __init__(self, channel_id: int, round_num: int):
        super().__init__(timeout=max(UNDERCOVER_DESC_TIME * 3, 180))
        self.channel_id = channel_id
        self.round_num = round_num
        self.message: discord.Message | None = None

    @button(label="填写描述", style=discord.ButtonStyle.primary, emoji="✍️")
    async def desc_button(self, interaction: discord.Interaction, button: Button):
        from src.chat.features.games.services.undercover_service import undercover_service
        game = undercover_service.get_game(self.channel_id)
        if not game or game.phase != "describing":
            await interaction.response.send_message("不在描述环节。", ephemeral=True)
            return
        if interaction.user.id not in game.players or interaction.user.id in game.eliminated:
            await interaction.response.send_message("你不在这场游戏里。", ephemeral=True)
            return
        if interaction.user.id in game.submitted_this_round:
            await interaction.response.send_message("你本轮已经提交过描述了。", ephemeral=True)
            return
        await interaction.response.send_modal(
            UndercoverDescModal(self.channel_id, interaction.user.id, self.round_num)
        )

    @button(label="查看我的词", style=discord.ButtonStyle.secondary, emoji="👀")
    async def my_word_button(self, interaction: discord.Interaction, button: Button):
        await _show_my_word(interaction, self.channel_id)


class UndercoverVoteEntryView(View):
    """公屏投票入口按钮。超时 → 按已有票强制结算。"""

    def __init__(self, channel_id: int):
        super().__init__(timeout=UNDERCOVER_VOTE_TIME)
        self.channel_id = channel_id
        self.message: discord.Message | None = None
        self.finished = False

    async def on_timeout(self):
        if self.finished:
            return
        self.finished = True
        from src.chat.features.games.services.undercover_service import undercover_service
        result = undercover_service.force_tally(self.channel_id)
        if "error" in result:
            return
        for c in self.children:
            c.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
                await _announce_tally(self.message.channel, self.message.guild, self.channel_id, result)
            except discord.HTTPException:
                pass

    @button(label="我要投票", style=discord.ButtonStyle.danger, emoji="🗳️")
    async def vote_button(self, interaction: discord.Interaction, button: Button):
        from src.chat.features.games.services.undercover_service import undercover_service
        game = undercover_service.get_game(self.channel_id)
        if not game or game.phase != "voting":
            await interaction.response.send_message("不在投票环节。", ephemeral=True)
            return
        if interaction.user.id not in game.players or interaction.user.id in game.eliminated:
            await interaction.response.send_message("你不在这场游戏里。", ephemeral=True)
            return
        if interaction.user.id in game.votes:
            await interaction.response.send_message("你已经投过票了。", ephemeral=True)
            return
        candidates = [p for p in game.players if p not in game.eliminated and p != interaction.user.id]
        options = [
            discord.SelectOption(label=_member_name(interaction.guild, pid), value=str(pid))
            for pid in candidates
        ]
        if not options:
            await interaction.response.send_message("没有可投的候选人。", ephemeral=True)
            return
        view = UndercoverVoteView(self.channel_id, interaction.user.id, self)
        view.children[0].options = options
        await interaction.response.send_message(
            content="🗳️ 选择你认为是卧底的人（只有你能看见）<偷笑>",
            view=view,
            ephemeral=True,
        )

    @button(label="查看我的词", style=discord.ButtonStyle.secondary, emoji="👀")
    async def my_word_button(self, interaction: discord.Interaction, button: Button):
        await _show_my_word(interaction, self.channel_id)


class UndercoverVoteView(View):
    """投票界面（ephemeral，只有自己看见）"""

    def __init__(self, channel_id: int, voter_id: int, entry_view: UndercoverVoteEntryView):
        super().__init__(timeout=UNDERCOVER_VOTE_TIME)
        self.channel_id = channel_id
        self.voter_id = voter_id
        self.entry_view = entry_view

    @select(placeholder="选择你认为是卧底的人...", options=[])
    async def vote_select(self, interaction: discord.Interaction, select_: Select):
        if interaction.user.id != self.voter_id:
            await interaction.response.send_message("这不是你的投票。", ephemeral=True)
            return
        target_id = int(select_.values[0])
        from src.chat.features.games.services.undercover_service import undercover_service
        result = undercover_service.submit_vote(self.channel_id, self.voter_id, target_id)

        if "error" in result:
            await interaction.response.send_message(result["error"], ephemeral=True)
            return

        for c in self.children:
            c.disabled = True

        if result.get("vote_done"):
            self.entry_view.finished = True
            self.entry_view.stop()
            await interaction.response.edit_message(content="✅ 你已投票，等结果公屏展示。", view=self)
            await _announce_tally(interaction.channel, interaction.guild, self.channel_id, result)
        else:
            await interaction.response.edit_message(
                content=f"🗳️ 已投票（{result['votes_count']}/{result['needed']}），"
                        f"等其他人投完，结果会公屏展示。<乖巧>",
                view=self,
            )
