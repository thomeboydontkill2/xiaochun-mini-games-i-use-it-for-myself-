# -*- coding: utf-8 -*-
"""
两人死斗 UI —— 公屏按钮触发 ephemeral 出拳界面。

修复：
- 就位按钮超时（有人挂机）→ 自动判弃权取消对局并公告，不再把双方永久锁死。
- 结算失败如实展示。
- 双方同时提交最后一拳时只有一条 interaction 走结算路径（service 层 _resolve_round 原子返回 game_over）。
- 【关键修复】双方都出拳后偶发"卡住不显示输赢"：旧版结果只通过 followup.send 播报，
  一旦这条请求失败（token 过期/网络抖动/Discord 侧错误）就什么都不显示，双方永远等下去。
  现在公屏播报走多重兜底（followup → 频道直发 → 至少给操作者一条 ephemeral 提示），绝不静默。
"""

import logging

import discord
from discord.ui import View, Select, Button, select, button

log = logging.getLogger(__name__)

CHOICE_EMOJI = {"石头": "✊", "剪刀": "✌️", "布": "✋"}


async def _publish(interaction: discord.Interaction, content: str, view: View | None = None):
    """尽最大努力把回合/终局结果发到公屏，任何一层失败都退到下一层。返回发出的消息（拿不到则 None）。"""
    try:
        return await interaction.followup.send(content=content, view=view, wait=True)
    except Exception:
        log.exception("死斗结果 followup 发送失败，尝试频道直发")
    channel = interaction.channel
    if channel is not None:
        try:
            return await channel.send(content=content, view=view)
        except Exception:
            log.exception("死斗结果频道直发也失败")
    try:
        await interaction.followup.send(
            content="⚠️ 结果播报失败了，请截图联系管理员或重开一局。本局币未变动。",
            ephemeral=True,
        )
    except Exception:
        log.exception("死斗结果连 ephemeral 兜底都失败")
    return None


class DuelReadyView(View):
    """公屏就位按钮：双方各自点击触发自己的 ephemeral 出拳界面"""

    def __init__(self, game, round_num: int):
        super().__init__(timeout=180)
        self.game = game
        self.round_num = round_num
        self.message: discord.Message | None = None

    async def on_timeout(self):
        from src.chat.features.games.services.duel_service import duel_service
        # 对局还在（没人打完）→ 判超时流局
        if duel_service.get_game(self.game.p1_id) is self.game:
            duel_service.cancel_game(self.game.p1_id)
            for c in self.children:
                c.disabled = True
            if self.message:
                try:
                    await self.message.edit(
                        content="⚔️ 死斗超时流局：有人迟迟不出拳，小春娘把擂台收了。币没有变动。<:bi_shi:1536138114366701698>",
                        view=self,
                    )
                except discord.HTTPException:
                    pass

    @button(label="我要出拳", style=discord.ButtonStyle.primary, emoji="✊")
    async def ready_button(self, interaction: discord.Interaction, button: Button):
        game = self.game
        uid = interaction.user.id
        if uid not in (game.p1_id, game.p2_id):
            await interaction.response.send_message("你不在这场死斗里。", ephemeral=True)
            return
        if (uid == game.p1_id and game.p1_choice) or (uid == game.p2_id and game.p2_choice):
            await interaction.response.send_message("你这局已经出过了，等对手。<:xianhua:1536148788228522075>", ephemeral=True)
            return
        await interaction.response.send_message(
            content=f"⚔️ **第{self.round_num}局** 出拳吧！只有你能看见这个界面。<:ghost_face:1536141788065038468>",
            view=DuelSelectView(self.game, uid, self.round_num),
            ephemeral=True,
        )


class DuelSelectView(View):
    """出拳选择界面（ephemeral，只有自己看见）"""

    def __init__(self, game, player_id: int, round_num: int):
        super().__init__(timeout=120)
        self.game = game
        self.player_id = player_id
        self.round_num = round_num

    @select(
        placeholder="选择你的出拳...",
        options=[
            discord.SelectOption(label="石头", emoji="🪨", value="石头"),
            discord.SelectOption(label="剪刀", emoji="✂️", value="剪刀"),
            discord.SelectOption(label="布", emoji="📄", value="布"),
        ],
    )
    async def choice_select(self, interaction: discord.Interaction, select_: Select):
        if interaction.user.id != self.player_id:
            await interaction.response.send_message("这不是你的死斗。", ephemeral=True)
            return
        choice = select_.values[0]
        from src.chat.features.games.services.duel_service import duel_service
        result = duel_service.submit_choice(self.player_id, choice)

        if "error" in result:
            await interaction.response.send_message(result["error"], ephemeral=True)
            return

        for c in self.children:
            c.disabled = True
        await interaction.response.edit_message(content=f"已出 **{choice}**，等待对手... ⏳", view=self)

        if result.get("waiting"):
            return
        await self._resolve_round(interaction, result)

    async def _resolve_round(self, interaction: discord.Interaction, result: dict):
        from src.chat.features.games.services.duel_service import duel_service

        round_data = result["round_result"]
        game_over = result.get("game_over", False)

        c1, c2 = round_data["c1"], round_data["c2"]
        round_num = round_data["round"]
        e1 = CHOICE_EMOJI.get(c1, "")
        e2 = CHOICE_EMOJI.get(c2, "")

        header = (
            f"⚔️ 第{round_num}轮结果\n\n"
            f"<@{self.game.p1_id}>：{e1} {c1}\n"
            f"<@{self.game.p2_id}>：{e2} {c2}\n\n"
        )
        if round_data.get("tie"):
            header += f"平局！双方都出了 {c1} 🤝\n\n"
        else:
            header += f"<@{round_data['winner_id']}> 获胜！\n\n"
        header += (
            f"当前比分\n"
            f"<@{self.game.p1_id}> {round_data['p1_score']} : {round_data['p2_score']} <@{self.game.p2_id}>\n"
        )

        if game_over:
            winner_id = result["winner_id"]
            loser_id = result["loser_id"]
            settle = await duel_service.settle(self.game, winner_id, loser_id, interaction.guild)

            text = header + (
                f"\n⚔️ 死斗结束\n\n"
                f"胜者：<@{winner_id}>\n"
                f"败者：<@{loser_id}>\n\n"
                f"最终比分：{self.game.p1_score} : {self.game.p2_score}\n\n"
            )

            if settle.get("error"):
                text += "（重复结算已拦截）"
            elif settle.get("settle_failed"):
                text += "⚠️ 币结算出了问题，本局币没有变动，请联系管理员。"
            else:
                mode = settle["mode"]
                if mode == "coin_vs_coin":
                    text += f"<@{winner_id}> 获得 {settle['winner_gain']} 春春币，<@{loser_id}> 失去 {settle['loser_loss']} 春春币"
                elif mode == "life_rich_vs_coin":
                    text += f"<@{winner_id}> 赌命勇敢者！共获得 {settle['winner_gain']} 春春币（含勇敢奖励），<@{loser_id}> 失去 {settle['loser_loss']} 春春币"
                elif mode == "coin_vs_life_rich":
                    text += f"<@{winner_id}> 获得 {settle['winner_gain']} 春春币，<@{loser_id}> 赌命输掉 {settle['loser_loss']} 春春币，禁言 {settle['loser_muted']} 分钟"
                elif mode == "life_poor_vs_coin":
                    text += f"<@{winner_id}> 没钱赌命赢了！获得 {settle['winner_gain']} 春春币，<@{loser_id}> 失去 {settle['loser_loss']} 春春币"
                elif mode == "coin_vs_life_poor":
                    text += f"<@{winner_id}> 赢了面子，<@{loser_id}> 没钱赌命输了，禁言 {settle['loser_muted']} 分钟"
                elif mode == "life_vs_life":
                    if settle["winner_gain"] > 0:
                        text += f"双方赌命！<@{winner_id}> 获得 {settle['winner_gain']} 春春币（系统奖励），<@{loser_id}> 禁言 {settle['loser_muted']} 分钟"
                    else:
                        text += f"双方赌命！<@{winner_id}> 今日赌命奖励已达上限（只赢了面子），<@{loser_id}> 禁言 {settle['loser_muted']} 分钟"

            await _publish(interaction, text)
        else:
            next_round = self.game.round
            ready = DuelReadyView(self.game, next_round)
            ready.message = await _publish(interaction, header + f"\n第{next_round}轮开始！双方点下方按钮出拳 👇", view=ready)
