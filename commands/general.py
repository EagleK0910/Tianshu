import discord
from discord import app_commands, ui
from discord.ext import commands
import time

class GeneralCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # 設定版本號與更新日期 (建議與企劃書同步)
        self.version = "1.0.0"
        self.update_date = "2026-02-13"

    @app_commands.command(name="about", description="查看機器人的相關資訊與開發者資料")
    async def about(self, interaction: discord.Interaction):
        """
        企劃書功能：回傳包含機器人相關資訊的嵌入訊息 (Embed)
        包含：邀請連結、版本資訊、開發者資訊、延遲資訊
        """
        
        # 取得開發者資訊 (從 config.json)
        dev_id = self.bot.config.get('DEVELOPER_ID')
        
        # 建立嵌入訊息
        embed = discord.Embed(
            title="🤖 機器人資訊面板",
            description="感謝您使用管理員機器人！本機器人致力於提供伺服器模板分享與高效的成員管理功能。",
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow()
        )
        
        # 1. 版本資訊
        embed.add_field(name="📌 版本資訊", value=f"目前版本：`v{self.version}`\n更新日期：`{self.update_date}`", inline=True)
        
        # 2. 延遲資訊
        latency = round(self.bot.latency * 1000)
        embed.add_field(name="⚡ 系統延遲", value=f"`{latency}ms`", inline=True)
        
        # 3. 開發者資訊
        embed.add_field(
            name="👨‍💻 開發者資訊", 
            value=f"開發者：<@{dev_id}>\nDiscord聯繫方式 : s_h_star", 
            inline=False
        )
        
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.set_footer(text=f"由 {self.bot.user.name} 系統自動生成", icon_url=self.bot.user.display_avatar.url)

        # 4. 互動按鈕 (邀請連結與群組連結)
        # 權限建議設定為管理員 (8) 或基本的指令權限
        invite_url = discord.utils.oauth_url(self.bot.user.id, permissions=discord.Permissions(8))
        
        view = ui.View()
        view.add_item(ui.Button(label="邀請機器人", url="https://discord.com/oauth2/authorize?client_id=1471837038126039073&permissions=8&integration_type=0&scope=bot", style=discord.ButtonStyle.link, emoji="🔗"))
        view.add_item(ui.Button(label="機器人專屬社群", url="https://discord.gg/8kmfFvy8WN", style=discord.ButtonStyle.link, emoji="👥"))

        await interaction.response.send_message(embed=embed, view=view)

async def setup(bot):
    await bot.add_cog(GeneralCog(bot))