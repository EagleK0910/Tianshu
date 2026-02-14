import discord
from discord import app_commands, ui
from discord.ext import commands
from datetime import datetime
import math
import logging

# --- 核心發送邏輯 (用於立即或預約) ---
async def send_global_announcement(bot, content, is_scheduled=False):
    title = "📢 來自開發者的預約公告" if is_scheduled else "📢 來自開發者的公告"
    embed = discord.Embed(
        title=title,
        description=content,
        color=discord.Color.blue(),
        timestamp=datetime.now()
    )
    embed.set_footer(text="系統自動發送")

    success_count = 0
    # 從資料庫抓取所有有設定 log 頻道的伺服器
    async with bot.db_pool.acquire() as conn:
        guilds_data = await conn.fetch("SELECT guild_id, log_channel_id FROM guilds WHERE log_channel_id IS NOT NULL")
        
    for record in guilds_data:
        guild = bot.get_guild(record['guild_id'])
        if guild:
            channel = guild.get_channel(record['log_channel_id'])
            if channel:
                try:
                    await channel.send(embed=embed)
                    success_count += 1
                except:
                    continue
    logging.info(f"公告發送完畢，成功送達 {success_count} 個伺服器。")

# --- /message 用的確認視窗 ---
class ConfirmSendView(ui.View):
    def __init__(self, bot, content, target_time):
        super().__init__(timeout=60)
        self.bot = bot
        self.content = content
        self.target_time = target_time # None 為立即發送

    @ui.button(label="確認執行", style=discord.ButtonStyle.danger, emoji="🚀")
    async def confirm(self, interaction: discord.Interaction, button: ui.Button):
        # 再次檢查資料庫連線
        if not hasattr(self.bot, 'db_pool') or self.bot.db_pool is None:
            return await interaction.response.send_message("❌ 資料庫未連線，請重新啟動機器人。", ephemeral=True)

        if self.target_time is None:
            # 立即發送
            await interaction.response.defer(ephemeral=True)
            await send_global_announcement(self.bot, self.content)
            await interaction.followup.send("✅ 公告已成功立即發送！", ephemeral=True)
        else:
            # 預約發送
            self.bot.scheduler.add_job(
                send_global_announcement,
                'date',
                run_date=self.target_time,
                args=[self.bot, self.content, True]
            )
            await interaction.response.edit_message(
                content=f"⏰ 預約成功！訊息將於 `{self.target_time.strftime('%Y-%m-%d %H:%M')}` 自動發布。",
                embed=None, view=None
            )

    @ui.button(label="取消操作", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.edit_message(content="❌ 操作已取消。", embed=None, view=None)

# --- /message 用的填寫視窗 ---
class MessageModal(ui.Modal, title="全域公告發布系統"):
    msg_content = ui.TextInput(label="消息內容", style=discord.TextStyle.paragraph, required=True, placeholder="請輸入欲發布的內容...")
    send_time = ui.TextInput(
        label="預約發布時間 (格式: YYYY-MM-DD HH:MM)", 
        placeholder="例如: 2026-02-14 15:30 (留空則立即發送)",
        required=False
    )

    def __init__(self, bot):
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction):
        content = self.msg_content.value
        time_str = self.send_time.value

        if not time_str:
            # 立即發送流程
            embed = discord.Embed(title="❓ 立即發送確認", description=f"內容：\n{content}", color=discord.Color.orange())
            await interaction.response.send_message(embed=embed, view=ConfirmSendView(self.bot, content, None), ephemeral=True)
        else:
            # 預約發送流程
            try:
                target_time = datetime.strptime(time_str, "%Y-%m-%d %H:%M")
                if target_time < datetime.now():
                    return await interaction.response.send_message("❌ 錯誤：預約時間不能早於現在時間！", ephemeral=True)
                
                embed = discord.Embed(title="⏳ 預約發送預覽", color=discord.Color.green())
                embed.add_field(name="內容", value=content, inline=False)
                embed.add_field(name="預定時間", value=time_str, inline=False)
                await interaction.response.send_message(embed=embed, view=ConfirmSendView(self.bot, content, target_time), ephemeral=True)
            except ValueError:
                await interaction.response.send_message("❌ 格式錯誤！請確保格式為 `2026-02-14 15:30`。", ephemeral=True)

# --- /server_info 分頁瀏覽 View ---
class ServerInfoView(ui.View):
    def __init__(self, bot, guilds, page=0):
        super().__init__(timeout=180)
        self.bot, self.guilds, self.page = bot, guilds, page
        self.per_page = 5
        self.total_pages = math.ceil(len(guilds) / self.per_page)

        # 下拉選單 (顯示當前分頁的伺服器)
        start = self.page * self.per_page
        end = start + self.per_page
        options = [discord.SelectOption(label=g.name, value=str(g.id), description=f"成員: {g.member_count}") for g in self.guilds[start:end]]
        
        if options:
            select = ui.Select(placeholder="選擇伺服器查看詳細資訊...", options=options)
            select.callback = self.select_callback
            self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        guild_id = int(interaction.data['values'][0])
        guild = self.bot.get_guild(guild_id)
        if not guild: return await interaction.response.send_message("找不到該伺服器。", ephemeral=True)

        embed = discord.Embed(title=f"🏰 {guild.name} 詳細資料", color=discord.Color.blue())
        embed.add_field(name="ID", value=f"`{guild.id}`", inline=True)
        embed.add_field(name="成員數", value=f"`{guild.member_count}`", inline=True)
        embed.add_field(name="擁有者", value=f"{guild.owner.mention} (`{guild.owner_id}`)", inline=False)
        embed.add_field(name="加入日期", value=f"<t:{int(guild.me.joined_at.timestamp())}:F>", inline=False)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="上一頁", style=discord.ButtonStyle.gray)
    async def prev(self, interaction: discord.Interaction, button: ui.Button):
        self.page = max(0, self.page - 1)
        await self.update_msg(interaction)

    @ui.button(label="下一頁", style=discord.ButtonStyle.gray)
    async def next(self, interaction: discord.Interaction, button: ui.Button):
        self.page = min(self.total_pages - 1, self.page + 1)
        await self.update_msg(interaction)

    async def update_msg(self, interaction: discord.Interaction):
        embed = DevCog.generate_list_embed(self.guilds, self.page)
        await interaction.response.edit_message(embed=embed, view=ServerInfoView(self.bot, self.guilds, self.page))

# --- Cog 主體 ---
class DevCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @staticmethod
    def generate_list_embed(guilds, page):
        per_page = 5
        start = page * per_page
        embed = discord.Embed(title="🌐 機器人所在伺服器清單", color=discord.Color.dark_magenta())
        for g in guilds[start:start+per_page]:
            embed.add_field(name=g.name, value=f"ID: `{g.id}` | 成員: `{g.member_count}`", inline=False)
        embed.set_footer(text=f"第 {page+1} / {math.ceil(len(guilds)/per_page)} 頁")
        return embed

    @app_commands.command(name="server_info", description="[開發者限定] 查看所有伺服器資訊")
    async def server_info(self, interaction: discord.Interaction):
        if interaction.user.id != self.bot.config['DEVELOPER_ID']:
            return await interaction.response.send_message("❌ 無權限", ephemeral=True)
        guilds = sorted(list(self.bot.guilds), key=lambda x: x.member_count, reverse=True)
        await interaction.response.send_message(embed=self.generate_list_embed(guilds, 0), view=ServerInfoView(self.bot, guilds, 0), ephemeral=True)

    @app_commands.command(name="message", description="[開發者限定] 全域廣播消息")
    async def message(self, interaction: discord.Interaction):
        if interaction.user.id != self.bot.config['DEVELOPER_ID']:
            return await interaction.response.send_message("❌ 無權限", ephemeral=True)
        await interaction.response.send_modal(MessageModal(self.bot))

async def setup(bot):
    await bot.add_cog(DevCog(bot))