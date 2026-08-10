# -*- coding: utf-8 -*-
"""
谁是卧底 UI —— 改版
描述环节：公屏按钮触发 Modal（只有自己看见的文本输入框）→ 全员提交后统一公屏展示
投票环节：公屏按钮触发 ephemeral 投票界面（只有自己看见）→ 投完后公屏展示结果
"""

import discord
from discord.ui import View, Select, Button, Modal, TextInput, select, button


class UndercoverJoinView(View):
    """加入游戏界面"""
    def __init__(self, channel_id: int, bet: int, host_id: int):
        super().__init__(timeout=180)
        self.channel_id = channel_id
        self.bet = bet
        self.host_id = host_id

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
        from src.chat.features.games.services.undercover_service import undercover_service
        ok, msg, game = undercover_service.start_game(self.channel_id)
        if not ok:
            await interaction.response.send_message(msg, ephemeral=True)
            return
        for c in self.children:
            c.disabled = True

        # 私信发词（随机开场白，所有人格式一样，卧底自己不知道是卧底）
        from src.chat.features.games.services.undercover_service import WORD_DM_MESSAGES
        import random as _random
        for pid in game.players:
            try:
                member = interaction.guild.get_member(pid)
                if member:
                    template = _random.choice(WORD_DM_MESSAGES)
                    msg = template.format(word=game.player_words[pid])
                    await member.send(f"🎭 {msg}")
            except Exception:
                pass

        await interaction.response.edit_message(
            content=(
                f"🎭 **谁是卧底开始！**\n"
                f"参与：{', '.join(f'<@{p}>' for p in game.players)}\n"
                f"每人已私信收到自己的词，别直接说出来！\n\n"
                f"**第{game.current_round}轮描述**\n"
                f"点下方按钮填写你的描述（只有你能看见的输入框），全员提交后统一展示！"
            ),
            view=UndercoverDescView(self.channel_id, game.current_round),
        )

    async def _join(self, interaction: discord.Interaction, is_life: bool):
        from src.chat.features.games.services.undercover_service import undercover_service
        try:
            from src.chat.features.odysseia_coin.service.coin_service import coin_service
            bal = await coin_service.get_balance(interaction.user.id) or 0
        except Exception:
            bal = 0
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
        desc = self.desc_input.value
        result = undercover_service.submit_desc_anytime(self.channel_id, self.user_id, desc)
        if "error" in result:
            await interaction.response.send_message(result["error"], ephemeral=True)
            return

        if result.get("all_done"):
            # 全员提交完毕，进入投票
            game = undercover_service.get_game(self.channel_id)
            undercover_service.enter_voting(game)
            # 公屏统一展示所有描述（显示用户名）
            display = undercover_service.get_descriptions_for_display(game)
            lines = [f"**第{self.round_num}轮描述展示**"]
            for uid, d in display:
                member = interaction.guild.get_member(uid)
                name = member.display_name if member else f"<@{uid}>"
                lines.append(f"**{name}**：{d}")
            lines.append(f"\n全员已提交！进入投票环节，点下方按钮投票 👇")
            await interaction.response.send_message(
                "\n".join(lines),
                view=UndercoverVoteEntryView(self.channel_id),
            )
        else:
            count = result["submitted_count"]
            total = result["total"]
            await interaction.response.send_message(
                f"✅ 描述已提交（{count}/{total}）。等其他人也填好，小春娘会统一展示。<乖巧>",
                ephemeral=True,
            )


class UndercoverDescView(View):
    """公屏描述按钮：每人点击触发自己的 Modal（只有自己看见）"""
    def __init__(self, channel_id: int, round_num: int):
        super().__init__(timeout=180)
        self.channel_id = channel_id
        self.round_num = round_num

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
        # 弹出只有自己能看见的 Modal
        await interaction.response.send_modal(
            UndercoverDescModal(self.channel_id, interaction.user.id, self.round_num)
        )


class UndercoverVoteEntryView(View):
    """公屏投票入口按钮：每人点击触发自己的 ephemeral 投票界面"""
    def __init__(self, channel_id: int):
        super().__init__(timeout=120)
        self.channel_id = channel_id

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
        # 构建候选人选项（除自己外的未淘汰玩家）
        candidates = [p for p in game.players if p not in game.eliminated and p != interaction.user.id]
        options = []
        for pid in candidates:
            m = interaction.guild.get_member(pid)
            name = m.display_name if m else f"<@{pid}>"
            options.append(discord.SelectOption(label=name, value=str(pid)))
        if not options:
            await interaction.response.send_message("没有可投的候选人。", ephemeral=True)
            return
        # 触发只有自己能看见的投票界面
        view = UndercoverVoteView(self.channel_id, interaction.user.id)
        view.children[0].options = options
        await interaction.response.send_message(
            content=f"🗳️ 选择你认为是卧底的人（只有你能看见）<偷笑>",
            view=view,
            ephemeral=True,
        )


class UndercoverVoteView(View):
    """投票界面（ephemeral，只有自己看见）"""
    def __init__(self, channel_id: int, voter_id: int):
        super().__init__(timeout=60)
        self.channel_id = channel_id
        self.voter_id = voter_id

    @select(
        placeholder="选择你认为是卧底的人...",
        options=[],
    )
    async def vote_select(self, interaction: discord.Interaction, select: Select):
        if interaction.user.id != self.voter_id:
            await interaction.response.send_message("这不是你的投票。", ephemeral=True)
            return
        target_id = int(select.values[0])
        from src.chat.features.games.services.undercover_service import undercover_service
        result = undercover_service.submit_vote(self.channel_id, self.voter_id, target_id)

        if "error" in result:
            await interaction.response.send_message(result["error"], ephemeral=True)
            return

        if result.get("vote_done"):
            # 投票结束，公屏展示结果
            game = undercover_service.get_game(self.channel_id)
            eliminated_name = interaction.guild.get_member(result["eliminated"])
            eliminated_name = eliminated_name.display_name if eliminated_name else f"<@{result['eliminated']}>"

            # 展示票数
            vote_count = result.get("vote_count", {})
            vote_lines = []
            for tid, cnt in vote_count.items():
                m = interaction.guild.get_member(tid)
                tname = m.display_name if m else f"<@{tid}>"
                vote_lines.append(f"{tname}：{cnt} 票")
            vote_text = "\n".join(vote_lines) if vote_lines else "无票数"

            text = f"🗳️ **投票结束！**\n{vote_text}\n{eliminated_name} 被淘汰出局。\n"

            if result.get("game_over"):
                winner = result["winner"]
                undercover_ids = result["undercover_ids"]
                undercover_names = []
                for uid in undercover_ids:
                    m = interaction.guild.get_member(uid)
                    undercover_names.append(m.display_name if m else f"<@{uid}>")

                if winner == "civilian":
                    text += f"🎉 **平民胜利！** 卧底是：{', '.join(undercover_names)}\n"
                else:
                    text += f"😈 **卧底胜利！** 卧底是：{', '.join(undercover_names)}\n"

                settle = await undercover_service.settle(game, winner, interaction.guild)
                text += "结算完成，春春币/禁言已生效。"
            else:
                text += f"\n**第{result.get('next_round', 1)}轮描述**开始，点下方按钮填写描述 👇"

            for c in self.children:
                c.disabled = True
            await interaction.response.edit_message(content="✅ 你已投票，等结果公屏展示。", view=self)
            # 公屏展示结果
            if result.get("game_over"):
                await interaction.followup.send(content=text)
            else:
                await interaction.followup.send(
                    content=text,
                    view=UndercoverDescView(self.channel_id, result.get("next_round", 1)),
                )
        else:
            # 投票已记录，更新进度显示
            votes_count = result["votes_count"]
            needed = result["needed"]
            for c in self.children:
                c.disabled = True
            await interaction.response.edit_message(
                content=f"🗳️ 已投票（{votes_count}/{needed}），等其他人投完，结果会公屏展示。<乖巧>",
                view=self,
            )
