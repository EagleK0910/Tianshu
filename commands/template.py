import discord
from discord import app_commands, ui
from discord.ext import commands
import logging
import math
from datetime import datetime

# 定義分類清單 (需與 web_main.py 保持一致)
CATEGORIES = ["技術開發", "遊戲社群", "休閒娛樂", "學術教育", "商務辦公", "其他"]

# --- 1. 審核系統：不通過理由視窗 ---
class RejectReasonModal(ui.Modal, title='請輸入不通過原因'):
    reason = ui.TextInput(label='原因', style=discord.TextStyle.paragraph, placeholder='請輸入拒絕理由...', required=True, min_length=5)

    def __init__(self, template_id, user_id, bot, template_name):
        super().__init__()
        self.template_id, self.user_id, self.bot = template_id, user_id, bot
        self.template_name = template_name

    async def on_submit(self, interaction: discord.Interaction):
        async with self.bot.db_pool.acquire() as conn:
            await conn.execute("UPDATE templates SET status = '未通過' WHERE id = $1", self.template_id)
        
        user = await self.bot.fetch_user(self.user_id)
        if user:
            embed = discord.Embed(title="❌ 模板申請未通過", color=discord.Color.red())
            embed.add_field(name="模板名稱", value=self.template_name, inline=False)
            embed.add_field(name="原因", value=self.reason.value, inline=False)
            try: await user.send(embed=embed)
            except: pass
        
        await interaction.response.send_message("✅ 已拒絕並通知使用者。", ephemeral=True)

# --- 2. 審核系統：基礎審核按鈕 ---
class TemplateReviewView(ui.View):
    def __init__(self, template_id, user_id, bot, template_name, link, desc, category):
        super().__init__(timeout=None)
        self.template_id, self.user_id, self.bot = template_id, user_id, bot
        self.template_name, self.link, self.desc, self.category = template_name, link, desc, category

    @ui.button(label="通過 ✅", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: ui.Button):
        async with self.bot.db_pool.acquire() as conn:
            await conn.execute("UPDATE templates SET status = '已通過' WHERE id = $1", self.template_id)
        
        user = await self.bot.fetch_user(self.user_id)
        if user:
            embed = discord.Embed(title="🎉 模板審核通過！", color=discord.Color.green())
            embed.add_field(name="模板名稱", value=self.template_name, inline=True)
            embed.add_field(name="分類", value=self.category, inline=True)
            embed.add_field(name="連結", value=f"[點我查看]({self.link})", inline=False)
            try: await user.send(embed=embed)
            except: pass
        
        for child in self.children: child.disabled = True
        await interaction.response.edit_message(content=f"✅ **此模板 ({self.category}) 已核准**", view=self)

    @ui.button(label="不通過 ❌", style=discord.ButtonStyle.danger)
    async def reject(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(RejectReasonModal(self.template_id, self.user_id, self.bot, self.template_name))

# --- 3. 審核系統：開發者專屬按鈕 ---
class DevReviewView(TemplateReviewView):
    def __init__(self, template_id, user_id, bot, template_name, link, desc, category):
        super().__init__(template_id, user_id, bot, template_name, link, desc, category)

    @ui.button(label="下放給管理員 📢", style=discord.ButtonStyle.secondary)
    async def delegate(self, interaction: discord.Interaction, button: ui.Button):
        async with self.bot.db_pool.acquire() as conn:
            managers = await conn.fetch("SELECT user_id FROM managers")
            await conn.execute("UPDATE templates SET status = '已下放' WHERE id = $1", self.template_id)
            
        if not managers: return await interaction.response.send_message("❌ 無管理員。", ephemeral=True)

        for m in managers:
            m_user = await self.bot.fetch_user(m['user_id'])
            if m_user:
                embed = discord.Embed(title="🔔 領取審核任務", color=discord.Color.blue())
                embed.add_field(name="名稱", value=self.template_name, inline=True)
                embed.add_field(name="分類", value=self.category, inline=True)
                try: await m_user.send(embed=embed, view=TemplateReviewView(self.template_id, self.user_id, self.bot, self.template_name, self.link, self.desc, self.category))
                except: continue
        
        button.disabled = True
        await interaction.response.edit_message(view=self)

# --- 4. 分類選擇下拉選單 ---
class CategorySelectView(ui.View):
    def __init__(self, bot, name, link, desc):
        super().__init__(timeout=60)
        self.bot, self.name, self.link, self.desc = bot, name, link, desc

    @ui.select(placeholder="請選擇模板分類...", options=[discord.SelectOption(label=cat) for cat in CATEGORIES])
    async def select_category(self, interaction: discord.Interaction, select: ui.Select):
        category = select.values[0]
        async with self.bot.db_pool.acquire() as conn:
            tid = await conn.fetchval(
                "INSERT INTO templates (uploader_id, uploader_name, template_name, description, link, status, category) VALUES ($1, $2, $3, $4, $5, '待審核', $6) RETURNING id",
                interaction.user.id, interaction.user.display_name, self.name, self.desc or "無描述", self.link, category
            )
        
        await interaction.response.edit_message(content=f"✅ 模板 **{self.name}** ({category}) 已提交審核！", view=None)
        
        # 通知開發者
        dev = await self.bot.fetch_user(self.bot.config['DEVELOPER_ID'])
        if dev:
            embed = discord.Embed(title="🛡️ 新模板待審核 (來自機器人)", color=discord.Color.blue())
            embed.add_field(name="名稱", value=self.name, inline=True)
            embed.add_field(name="分類", value=category, inline=True)
            view = DevReviewView(tid, interaction.user.id, self.bot, self.name, self.link, self.desc, category)
            try: await dev.send(embed=embed, view=view)
            except: pass

# --- 5. 主 Cog ---
class TemplateCog(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @app_commands.command(name="template", description="上傳模板並選擇分類")
    async def template(self, interaction: discord.Interaction):
        class UploadModal(ui.Modal, title='1. 輸入模板資訊'):
            n = ui.TextInput(label='名稱', required=True)
            l = ui.TextInput(label='連結', placeholder='https://discord.new/...', required=True)
            d = ui.TextInput(label='描述', style=discord.TextStyle.paragraph, required=False)

            def __init__(self, bot):
                super().__init__()
                self.bot = bot

            async def on_submit(self, inter: discord.Interaction):
                if not self.l.value.startswith("https://discord.new/"):
                    return await inter.response.send_message("❌ 連結無效，必須是 Discord 模板連結。", ephemeral=True)
                
                # 進入第二步：選擇分類
                await inter.response.send_message("請選擇此模板的分類：", view=CategorySelectView(self.bot, self.n.value, self.l.value, self.d.value), ephemeral=True)

        await interaction.response.send_modal(UploadModal(self.bot))

    @app_commands.command(name="my_template", description="管理您的模板")
    async def my_template(self, interaction: discord.Interaction):
        async with self.bot.db_pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM templates WHERE uploader_id = $1 ORDER BY created_at DESC", interaction.user.id)
        
        if not rows: return await interaction.response.send_message("您目前沒有任何模板紀錄。", ephemeral=True)

        embed = discord.Embed(title="📂 我的模板清單", color=discord.Color.blue())
        options = [discord.SelectOption(label=f"[{r['category']}] {r['template_name']}", value=str(r['id'])) for r in rows[:25]]

        class MyView(ui.View):
            def __init__(self, bot, opts):
                super().__init__(timeout=180)
                sel = ui.Select(options=opts, placeholder="選擇要管理的模板...")
                sel.callback = self.sel_cb
                self.add_item(sel)
                self.bot = bot

            async def sel_cb(self, inter: discord.Interaction):
                tid = int(inter.data['values'][0])
                async with self.bot.db_pool.acquire() as conn:
                    t = await conn.fetchrow("SELECT * FROM templates WHERE id = $1", tid)
                
                emb = discord.Embed(title=f"管理模板: {t['template_name']}", color=discord.Color.green())
                emb.add_field(name="分類", value=t['category'], inline=True)
                emb.add_field(name="狀態", value=t['status'], inline=True)
                emb.add_field(name="連結", value=t['link'], inline=False)
                
                # 此處可以加入刪除或修改的按鈕
                await inter.response.edit_message(embed=emb, view=None)

        await interaction.response.send_message(embed=embed, view=MyView(self.bot, options), ephemeral=True)

async def setup(bot): await bot.add_cog(TemplateCog(bot))