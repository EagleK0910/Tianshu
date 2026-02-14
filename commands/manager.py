import discord
from discord import app_commands, ui
from discord.ext import commands
import logging

class ManagerCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="manager", description="[開發者限定] 指定或刪除審核管理員")
    @app_commands.describe(user="欲設定權限的成員或輸入使用者ID")
    async def manager(self, interaction: discord.Interaction, user: discord.User):
        """
        企劃書功能：讓開發者可以快速指定其他成員是否為開發者指定的成員，
        指定的成員可以共同審核使用者上傳的Discord模板。
        """
        # 1. 權限檢查：比對 config.json 中的 DEVELOPER_ID
        if interaction.user.id != self.bot.config.get('DEVELOPER_ID'):
            return await interaction.response.send_message("❌ 此指令為開發者限定指令，不開放一般成員使用。", ephemeral=True)

        # 2. 從資料庫檢查該成員目前的狀態
        async with self.bot.db_pool.acquire() as conn:
            is_manager = await conn.fetchval("SELECT user_id FROM managers WHERE user_id = $1", user.id)

        # 3. 建立嵌入訊息 (Embed) 顯示使用者資訊
        status_label = "🟢 審核管理員" if is_manager else "⚪ 一般成員"
        embed = discord.Embed(
            title="👤 審核權限管理",
            description=f"**成員：** {user.mention}\n**ID：** `{user.id}`\n**目前身分：** {status_label}",
            color=discord.Color.blue() if is_manager else discord.Color.light_gray()
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.set_footer(text="請選擇下方的按鈕來變更權限或取消操作")

        # 4. 傳送互動視窗
        view = ManagerControlView(user, bool(is_manager), self.bot)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

# --- 按鈕互動 View ---
class ManagerControlView(ui.View):
    def __init__(self, target_user, is_manager, bot):
        super().__init__(timeout=60)
        self.target_user = target_user
        self.is_manager = is_manager
        self.bot = bot

        # 根據企劃書：提供指定、刪除與取消按鈕
        if not self.is_manager:
            # 如果目前不是管理員，顯示「指定」按鈕
            add_btn = ui.Button(label="指定為管理員", style=discord.ButtonStyle.success, emoji="✅")
            add_btn.callback = self.add_callback
            self.add_item(add_btn)
        else:
            # 如果目前是管理員，顯示「刪除」按鈕
            remove_btn = ui.Button(label="刪除管理權限", style=discord.ButtonStyle.danger, emoji="🗑️")
            remove_btn.callback = self.remove_callback
            self.add_item(remove_btn)

        # 企劃書要求的取消按鈕
        cancel_btn = ui.Button(label="取消操作", style=discord.ButtonStyle.secondary)
        cancel_btn.callback = self.cancel_callback
        self.add_item(cancel_btn)

    async def add_callback(self, interaction: discord.Interaction):
        async with self.bot.db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO managers (user_id) VALUES ($1) ON CONFLICT DO NOTHING", 
                self.target_user.id
            )
        await interaction.response.edit_message(
            content=f"✅ 已成功將 {self.target_user.mention} 指定為審核管理員。",
            embed=None, view=None
        )

    async def remove_callback(self, interaction: discord.Interaction):
        async with self.bot.db_pool.acquire() as conn:
            await conn.execute("DELETE FROM managers WHERE user_id = $1", self.target_user.id)
        await interaction.response.edit_message(
            content=f"🗑️ 已成功移除 {self.target_user.mention} 的審核權限。",
            embed=None, view=None
        )

    async def cancel_callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(content="已取消權限管理操作。", embed=None, view=None)

async def setup(bot):
    await bot.add_cog(ManagerCog(bot))