# -*- coding: utf-8 -*-
"""游戏 Embed 工具 —— 统一颜色语义 + 简洁紧凑风格。

颜色语义表：
    蓝  COLOR_PLAYING   → 进行中 / 招募 / 面板
    绿  COLOR_WIN       → 胜利 / 成功 / 赢钱
    红  COLOR_LOSE      → 中弹 / 出局 / 失败 / 亏钱
    橙  COLOR_MUTE      → 禁言 / 胆小鬼 / 警告
    金  COLOR_CHAMPION  → 冠军 / 高光时刻
    灰  COLOR_DRAW      → 平局 / 无效
    紫  COLOR_QUIT      → 撤销 / 退出
"""

import discord

# ---- 颜色常量（与 The-Parliament-Bot 语义色对齐）----
COLOR_PLAYING = 0x0099FF
COLOR_WIN = 0x00FF00
COLOR_LOSE = 0xED4245
COLOR_MUTE = 0xF39C12
COLOR_CHAMPION = 0xFFD700
COLOR_DRAW = 0x808080
COLOR_QUIT = 0xD56CFF


def game_embed(
    title: str,
    description: str = "",
    color: int = COLOR_PLAYING,
    footer: str | None = None,
    fields: list[dict] | None = None,
) -> discord.Embed:
    """构造一个简洁紧凑的游戏 Embed。

    Args:
        title:   标题（建议带 emoji 前缀，如 "🔫 第 2 枪"）
        description: 主体内容（支持 Markdown / <@id> / <t:unix:F>）
        color:   颜色常量
        footer:  页脚文字（建议带对局/房间 ID 便于排查）
        fields:  可选，[{name, value, inline}] 列表
    """
    embed = discord.Embed(title=title, description=description, color=color)
    if footer:
        embed.set_footer(text=footer)
    embed.timestamp = discord.utils.utcnow()
    if fields:
        for f in fields:
            embed.add_field(
                name=f.get("name", "\u200b"),
                value=f.get("value", "\u200b"),
                inline=f.get("inline", False),
            )
    return embed
