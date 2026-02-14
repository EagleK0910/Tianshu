import discord
from discord import app_commands, ui
from discord.ext import commands
from typing import Union
from datetime import datetime, timedelta
import logging

# --- 1. 管理權限設定 View ---
class AdminSetupView(ui.View):
    def __init__(self, cog, target: Union[discord.Member, discord.Role]):
        super().__init__(timeout=60)
        self.cog = cog
        self.target = target

    @ui.button(label="✅ 給予權限", style=discord.ButtonStyle.green)
    async def grant_perm(self, interaction: discord.Interaction, button: ui.Button):
        async with self.cog.bot.db_pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE guilds 
                SET admin_list = ARRAY(SELECT DISTINCT UNNEST(array_append(admin_list, $1)))
                WHERE guild_id = $2
                """,
                self.target.id, interaction.guild_id
            )
        
        type_str = "成員" if isinstance(self.target, discord.Member) else "身分組"
        await interaction.response.edit_message(
            content=f"✅ 已成功將管理權限給予 {type_str}：{self.target.mention}", 
            embed=None, view=None
        )

    @ui.button(label="🗑️ 刪除權限", style=discord.ButtonStyle.red)
    async def revoke_perm(self, interaction: discord.Interaction, button: ui.Button):
        async with self.cog.bot.db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE guilds SET admin_list = array_remove(admin_list, $1) WHERE guild_id = $2",
                self.target.id, interaction.guild_id
            )
        
        type_str = "成員" if isinstance(self.target, discord.Member) else "身分組"
        await interaction.response.edit_message(
            content=f"🗑️ 已撤銷 {type_str} {self.target.mention} 的管理權限。", 
            embed=None, view=None
        )

    @ui.button(label="取消操作", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.edit_message(content="已取消權限管理操作。", embed=None, view=None)

# --- 2. 警告與嘉獎彈出視窗 ---
class ModModal(ui.Modal):
    def __init__(self, title: str, member: discord.Member, mod_type: str, cog):
        super().__init__(title=title)
        self.member = member
        self.mod_type = mod_type
        self.cog = cog

        self.count = ui.TextInput(
            label="變動次數",
            placeholder="請輸入數字（預設為 1）",
            min_length=1,
            max_length=2,
            default="1"
        )
        self.reason = ui.TextInput(
            label="詳細原因",
            style=discord.TextStyle.paragraph,
            placeholder="請描述原因...",
            required=False,
            max_length=200
        )
        self.add_item(self.count)
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            val = int(self.count.value)
            if val <= 0: raise ValueError
        except ValueError:
            return await interaction.response.send_message("❌ 請輸入有效的正整數數字。", ephemeral=True)

        reason_text = self.reason.value or "管理員未註明原因"
        type_cn = "警告" if self.mod_type == "warn" else "嘉獎"
        
        async with self.cog.bot.db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO member_records 
                (guild_id, user_id, user_name, type, count, reason, operator_id, operator_name) 
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                interaction.guild_id, self.member.id, self.member.display_name, 
                type_cn, val, reason_text, interaction.user.id, interaction.user.display_name
            )

        color = discord.Color.red() if self.mod_type == "warn" else discord.Color.gold()
        emoji = "⚠️" if self.mod_type == "warn" else "✨"

        log_embed = discord.Embed(title=f"{emoji} {type_cn}異動紀錄", color=color, timestamp=datetime.now())
        log_embed.add_field(name="對象成員", value=self.member.mention, inline=True)
        log_embed.add_field(name="變動次數", value=f"**{val}** 次", inline=True)
        log_embed.add_field(name="執行管理員", value=interaction.user.mention, inline=True)
        log_embed.add_field(name="原因細節", value=reason_text, inline=False)
        log_embed.set_footer(text=f"User ID: {self.member.id}")

        await self.cog.log_to_channel(interaction.guild, log_embed)
        await interaction.response.send_message(f"✅ 已成功為 {self.member.display_name} 登記了 {val} 次 {type_cn}。")
        
        await self.cog.check_auto_actions(interaction.guild, self.member, type_cn)

# --- 3. 核心 Cog ---
class ModerationCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # 中文動作對應字典
        self.action_names_zh = {
            'timeout': '執行禁言 (Timeout)',
            'kick': '執行踢出伺服器',
            'ban': '執行封鎖帳號',
            'add_role': '給予特定身分組'
        }

    async def has_mod_permission(self, interaction: discord.Interaction):
        if interaction.user.id == interaction.guild.owner_id: return True
        if interaction.user.id == int(self.bot.config['DEVELOPER_ID']): return True
        
        async with self.bot.db_pool.acquire() as conn:
            admin_list = await conn.fetchval("SELECT admin_list FROM guilds WHERE guild_id = $1", interaction.guild_id)
            if admin_list:
                if interaction.user.id in admin_list: return True
                user_role_ids = [role.id for role in interaction.user.roles]
                if any(rid in admin_list for rid in user_role_ids): return True
        return False

    async def check_auto_actions(self, guild, member, record_type):
        """檢查並執行自動化懲處邏輯"""
        async with self.bot.db_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT type, SUM(count) as total FROM member_records WHERE guild_id = $1 AND user_id = $2 GROUP BY type",
                guild.id, member.id
            )
            stats = {r['type']: r['total'] for r in rows}
            w_total = stats.get('警告', 0)
            r_total = stats.get('嘉獎', 0)

            offset_enabled = await conn.fetchval("SELECT offset_enabled FROM guilds WHERE guild_id = $1", guild.id)
            
            if offset_enabled:
                current_count = max(0, w_total - r_total) if record_type == "警告" else max(0, r_total - w_total)
            else:
                current_count = w_total if record_type == "警告" else r_total

            action = await conn.fetchrow(
                "SELECT * FROM auto_actions WHERE guild_id = $1 AND type = $2 AND threshold <= $3 ORDER BY threshold DESC LIMIT 1", 
                guild.id, record_type, current_count
            )
            
            if action:
                try:
                    action_type = action['action_type']
                    threshold = action['threshold']
                    action_text_zh = self.action_names_zh.get(action_type, action_type)
                    
                    # 執行動作
                    if action_type == 'kick':
                        await member.kick(reason=f"自動懲處：{record_type}達標 {threshold} 次")
                    elif action_type == 'ban':
                        await member.ban(reason=f"自動懲處：{record_type}達標 {threshold} 次")
                    elif action_type == 'timeout':
                        duration = action.get('timeout_duration', 60)
                        await member.timeout(timedelta(minutes=duration), reason=f"自動懲處：{record_type}達標 {threshold} 次")
                    elif action_type == 'add_role':
                        role = guild.get_role(action['role_id'])
                        if role: await member.add_roles(role)

                    # 發送中文 Embed 通知
                    log_embed = discord.Embed(
                        title="🛡️ 系統自動化處置通知",
                        description=f"成員 {member.mention} 已達到自動處分門檻。",
                        color=discord.Color.red() if record_type == "警告" else discord.Color.green(),
                        timestamp=datetime.now()
                    )
                    log_embed.add_field(name="觸發原因", value=f"累積 {record_type} 達 **{threshold}** 次", inline=True)
                    log_embed.add_field(name="執行動作", value=f"**{action_text_zh}**", inline=True)
                    
                    if action_type == 'timeout':
                        log_embed.add_field(name="時長", value=f"{action.get('timeout_duration', 60)} 分鐘", inline=True)
                    elif action_type == 'add_role':
                        role = guild.get_role(action['role_id'])
                        log_embed.add_field(name="身分組", value=f"@{role.name if role else '未知'}", inline=True)

                    log_embed.set_thumbnail(url=member.display_avatar.url)
                    log_embed.set_footer(text="自動化管理系統 | 兩端同步運作中")
                    
                    await self.log_to_channel(guild, log_embed)
                    
                except Exception as e:
                    logging.error(f"自動化執行異常: {e}")

    async def log_to_channel(self, guild, embed):
        async with self.bot.db_pool.acquire() as conn:
            channel_id = await conn.fetchval("SELECT log_channel_id FROM guilds WHERE guild_id = $1", guild.id)
        if channel_id:
            channel = guild.get_channel(channel_id)
            if channel:
                try: await channel.send(embed=embed)
                except: pass

    # --- 指令區 ---
    @app_commands.command(name="admin", description="授權成員或身分組使用管理指令 (限擁有者使用)")
    @app_commands.describe(member_or_role="要授權或取消的對象")
    async def admin_setup(self, interaction: discord.Interaction, member_or_role: Union[discord.Member, discord.Role]):
        if interaction.user.id != interaction.guild.owner_id and interaction.user.id != int(self.bot.config['DEVELOPER_ID']):
            return await interaction.response.send_message("❌ 此指令僅限伺服器擁有者使用。", ephemeral=True)

        async with self.bot.db_pool.acquire() as conn:
            admin_list = await conn.fetchval("SELECT admin_list FROM guilds WHERE guild_id = $1", interaction.guild_id)
        
        is_authorized = admin_list and member_or_role.id in admin_list
        status_text = "🟢 已擁有管理權限" if is_authorized else "⚪ 目前無管理權限"
        embed_color = discord.Color.green() if is_authorized else discord.Color.light_gray()

        embed = discord.Embed(
            title="🛡️ 管理權限狀態設定",
            description=f"**設定對象：** {member_or_role.mention}\n**目前狀態：** {status_text}\n\n請選擇下方的按鈕進行操作：",
            color=embed_color
        )
        await interaction.response.send_message(embed=embed, view=AdminSetupView(self, member_or_role), ephemeral=True)

    @app_commands.command(name="warn", description="給予成員警告")
    async def warn(self, interaction: discord.Interaction, member: discord.Member):
        if not await self.has_mod_permission(interaction):
            return await interaction.response.send_message("❌ 您沒有管理權限。", ephemeral=True)
        await interaction.response.send_modal(ModModal(f"登記警告：{member.display_name}", member, 'warn', self))

    @app_commands.command(name="reward", description="給予成員嘉獎")
    async def reward(self, interaction: discord.Interaction, member: discord.Member):
        if not await self.has_mod_permission(interaction):
            return await interaction.response.send_message("❌ 您沒有管理權限。", ephemeral=True)
        await interaction.response.send_modal(ModModal(f"登記嘉獎：{member.display_name}", member, 'reward', self))

    @app_commands.command(name="record", description="查詢獎懲累積紀錄")
    async def record(self, interaction: discord.Interaction, member: discord.Member = None):
        target = member or interaction.user
        async with self.bot.db_pool.acquire() as conn:
            rows = await conn.fetch("SELECT type, SUM(count) as total FROM member_records WHERE guild_id = $1 AND user_id = $2 GROUP BY type", interaction.guild_id, target.id)
            offset_enabled = await conn.fetchval("SELECT offset_enabled FROM guilds WHERE guild_id = $1", interaction.guild_id)
        
        stats = {r['type']: r['total'] for r in rows}
        w, r = stats.get('警告', 0), stats.get('嘉獎', 0)
        
        embed = discord.Embed(title=f"📊 成員獎懲統計庫", color=discord.Color.blue(), timestamp=datetime.now())
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.set_author(name=f"{target.display_name} 的數據清單")

        if offset_enabled:
            real_warn, real_reward = max(0, w - r), max(0, r - w)
            embed.add_field(name="📉 實質警告 (抵消後)", value=f"```fix\n{real_warn} 次\n```", inline=True)
            embed.add_field(name="📈 實質嘉獎 (抵消後)", value=f"```yaml\n{real_reward} 次\n```", inline=True)
            embed.set_footer(text=f"原始數值：{w} 警告 / {r} 嘉獎 (抵消功能開啟)")
        else:
            embed.add_field(name="⚠️ 累積警告", value=f"```diff\n- {w} 次\n```", inline=True)
            embed.add_field(name="✨ 累積嘉獎", value=f"```diff\n+ {r} 次\n```", inline=True)

        await interaction.response.send_message(embed=embed)

async def setup(bot):
    await bot.add_cog(ModerationCog(bot))