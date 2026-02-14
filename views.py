import discord
from discord import ui

class TemplateReviewView(ui.View):
    def __init__(self, t_id, db_pool, u_name, bot=None):
        super().__init__(timeout=None) 
        self.t_id = t_id
        self.db_pool = db_pool
        self.u_name = u_name
        self.bot = bot 

    @ui.button(label="✅ 通過", style=discord.ButtonStyle.green)
    async def approve(self, interaction: discord.Interaction, button: ui.Button):
        async with self.db_pool.acquire() as conn:
            # 🚀 [修正關鍵] 將 '已通過' 改為 'approved'，對應網頁端的查詢條件
            await conn.execute("UPDATE templates SET status = 'approved' WHERE id = $1", self.t_id)
        
        # 移除按鈕並更新訊息
        await interaction.response.edit_message(content=f"✅ 模板 (ID: {self.t_id}) 已由 {interaction.user.name} 審核通過！", view=None, embed=None)

    @ui.button(label="🔵 下放管理員", style=discord.ButtonStyle.blurple)
    async def delegate(self, interaction: discord.Interaction, button: ui.Button):
        async with self.db_pool.acquire() as conn:
            # 這裡保持 '已下放'，因為 web_main.py 的審核中心是查詢中文狀態
            await conn.execute("UPDATE templates SET status = '已下放' WHERE id = $1", self.t_id)
            
            managers = await conn.fetch("SELECT user_id FROM managers")
        
        await interaction.response.edit_message(content=f"🔵 模板 (ID: {self.t_id}) 已下放給 {len(managers)} 位管理員審核。", view=None)
        
        if self.bot and managers:
            for m in managers:
                try:
                    m_user = await self.bot.fetch_user(m['user_id'])
                    if m_user:
                        await m_user.send(f"🔔 有新的下放審核任務 (模板 ID: {self.t_id})，請至網頁後台或使用指令處理。")
                except:
                    continue

    @ui.button(label="❌ 駁回", style=discord.ButtonStyle.red)
    async def reject(self, interaction: discord.Interaction, button: ui.Button):
        async with self.db_pool.acquire() as conn:
            # 建議統一改為 'rejected'，方便未來管理
            await conn.execute("UPDATE templates SET status = 'rejected' WHERE id = $1", self.t_id)
        
        await interaction.response.edit_message(content=f"❌ 模板 (ID: {self.t_id}) 已被駁回。", view=None, embed=None)