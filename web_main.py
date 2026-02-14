import json
import httpx
import discord
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from discord import Permissions
from fastapi import Form, HTTPException
from fastapi.responses import RedirectResponse
from views import TemplateReviewView
from datetime import datetime

# 讀取設定
with open('config.json', 'r', encoding='utf-8') as f:
    config = json.load(f)

app = FastAPI()

# --- 在路由開始之前加入此函式 ---
async def get_user_role_text(bot, user_id: int):
    """
    取得全域身分文字
    """
    # 檢查是否為開發者 (從 config 讀取 ID)
    if user_id == config.get('DEVELOPER_ID'):
        return "開發者"
    
    async with bot.db_pool.acquire() as conn:
        # 檢查是否為模板管理員 (假設您有 managers 資料表)
        is_manager = await conn.fetchval("SELECT user_id FROM managers WHERE user_id = $1", user_id)
        if is_manager:
            return "模板管理員"
            
    return "一般使用者"

# 啟用 Session 功能來儲存登入狀態
# 這裡的 secret_key 請換成一段隨機的長字串
app.add_middleware(SessionMiddleware, secret_key="YOUR_SECRET_KEY_HERE")

templates = Jinja2Templates(directory="templates")

# Discord OAuth2 資訊
CLIENT_ID = config['CLIENT_ID']
CLIENT_SECRET = config['CLIENT_SECRET']
REDIRECT_URI = "http://localhost:8000/callback"  # 在 Discord Developer Portal 也要設定這個 URL
DISCORD_API_BASE = "https://discord.com/api/v10"

async def check_user_access(bot, guild_id: int, user_id: int):
    guild = bot.get_guild(guild_id)
    if not guild: return "none"
    
    # 判斷是否為擁有者
    if guild.owner_id == user_id: return "owner"
    
    # 判斷是否為資料庫授權的管理員 (admin_list)
    async with bot.db_pool.acquire() as conn:
        admin_list = await conn.fetchval("SELECT admin_list FROM guilds WHERE guild_id = $1", guild_id)
        if admin_list and user_id in admin_list:
            return "admin"
            
    return "member"

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    user = request.session.get("user")
    return templates.TemplateResponse("index.html", {"request": request, "user": user})

@app.get("/login")
async def login():
    """跳轉到 Discord 登入頁面"""
    # 請求權限包含 identify (基本資料) 和 guilds (查看加入的伺服器)
    scope = "identify guilds"
    auth_url = (
        f"https://discord.com/api/oauth2/authorize?client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}&response_type=code&scope={scope}"
    )
    return RedirectResponse(auth_url)

@app.get("/callback")
async def callback(request: Request, code: str):
    """處理 Discord 授權回傳"""
    async with httpx.AsyncClient() as client:
        # 1. 交換 Token
        data = {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        token_res = await client.post(f"{DISCORD_API_BASE}/oauth2/token", data=data, headers=headers)
        token_data = token_res.json()
        
        if "access_token" not in token_data:
            return HTTPException(status_code=400, detail="授權失敗")

        token = token_data["access_token"]
        
        # 2. 獲取使用者資料
        user_headers = {"Authorization": f"Bearer {token}"}
        user_res = await client.get(f"{DISCORD_API_BASE}/users/@me", headers=user_headers)
        user_info = user_res.json()

        # 3. 儲存 Session
        request.session["user"] = {
            "id": user_info["id"],
            "username": user_info["username"],
            "avatar": f"https://cdn.discordapp.com/avatars/{user_info['id']}/{user_info['avatar']}.png",
            "token": token
        }

    return RedirectResponse(url="/")

@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/")

@app.get("/guilds", response_class=HTMLResponse)
async def guild_list(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/")

    bot = request.app.state.bot
    user_id = int(user['id']) # 這是使用者的 Discord ID，用來比對權限

        # 在你的 guild_list 路由中
    user_role = "一般使用者" # 預設 [cite: 92]
    if int(user['id']) == config['DEVELOPER_ID']: # 判定開發者 [cite: 83, 90]
        user_role = "開發者"
    else:
        async with bot.db_pool.acquire() as conn:
            # 判定是否為模板管理員 [cite: 70, 91]
            is_manager = await conn.fetchval("SELECT user_id FROM managers WHERE user_id = $1", int(user['id']))
            if is_manager:
                user_role = "模板管理員"

    bot = app.state.bot
    async with httpx.AsyncClient() as client:
        # 取得使用者所在的 Discord 伺服器清單
        headers = {"Authorization": f"Bearer {user['token']}"}
        res = await client.get(f"{DISCORD_API_BASE}/users/@me/guilds", headers=headers)
        user_guilds = res.json()

    # 權限與排序處理
    installed_guilds = []
    not_installed_guilds = []

    for g in user_guilds:
        # 檢查機器人是否在該伺服器
        bot_guild = bot.get_guild(int(g['id']))
        
        # 檢查使用者是否有 Discord 管理員權限 (ADMINISTRATOR)
        # Discord 的 permissions 是一個 bitmask，0x8 是管理員
        is_admin = (int(g['permissions']) & 0x8) == 0x8 or g['owner']
        
        guild_info = {
            "id": g['id'],
            "name": g['name'],
            "icon": f"https://cdn.discordapp.com/icons/{g['id']}/{g['icon']}.png" if g['icon'] else None,
            "is_admin": is_admin
        }

        if bot_guild:
            installed_guilds.append(guild_info)
        else:
            not_installed_guilds.append(guild_info)

    return templates.TemplateResponse("guilds.html", {
        "request": request,
        "user": user,            # 內含 username (Discord 名字)
        "user_role": user_role,  # 傳遞身分文字給前端
        "installed": installed_guilds,
        "not_installed": not_installed_guilds,
        "config": config
    })

@app.get("/templates", response_class=HTMLResponse)
async def list_templates(request: Request, mine: bool = False, search: str = None, category: str = None):
    user = request.session.get("user")
    current_user_id = int(user['id']) if user else None
    
    # 🚀 [修正關鍵] 補上這行，定義 bot 變數
    bot = request.app.state.bot  

    async with bot.db_pool.acquire() as conn:

        base_query = "SELECT * FROM templates"
        conditions = []
        params = []
        
        # 邏輯 1: 如果是「我的模板」，篩選 uploader_id
        if mine and current_user_id:
            conditions.append(f"uploader_id = ${len(params) + 1}")
            params.append(current_user_id)
        else:
            # 顯示已審核通過的模板
            conditions.append("status = 'approved'")
            
        # 邏輯 2: 關鍵字搜尋
        if search:
            conditions.append(f"(template_name ILIKE ${len(params) + 1} OR description ILIKE ${len(params) + 1})")
            params.append(f"%{search}%")

        # 邏輯 3: 分類篩選
        if category and category != "全部":
            conditions.append(f"category = ${len(params) + 1}")
            params.append(category)
            
        final_query = base_query
        if conditions:
            final_query += " WHERE " + " AND ".join(conditions)
        final_query += " ORDER BY created_at DESC"
        
        templates_data = await conn.fetch(final_query, *params)

    user_role = await get_user_role_text(bot, current_user_id) if current_user_id else "一般使用者"

    return templates.TemplateResponse("templates_list.html", {
        "request": request,
        "user": user,
        "templates": templates_data,
        "current_user_id": current_user_id,
        "show_mine": mine,
        "user_role": user_role,
        "current_category": category or "全部"
    })

@app.post("/templates/delete/{template_id}")
async def delete_template(template_id: int, request: Request):
    user = request.session.get("user")
    if not user: return RedirectResponse("/login")
    
    user_id = int(user['id'])
    bot = request.app.state.bot
    async with bot.db_pool.acquire() as conn:
        # 修正 403 關鍵：確保 uploader_id 比較邏輯正確
        template = await conn.fetchrow("SELECT uploader_id FROM templates WHERE id = $1", template_id)
        if not template:
            raise HTTPException(status_code=404, detail="找不到此模板")
            
        # 權限檢查：必須是本人或開發者
        if int(template['uploader_id']) != user_id and user_id != config['DEVELOPER_ID']:
            raise HTTPException(status_code=403, detail="權限不足：您只能刪除自己的模板")
            
        await conn.execute("DELETE FROM templates WHERE id = $1", template_id)
        
    return RedirectResponse("/templates?mine=true", status_code=303)

@app.post("/templates/upload")
async def upload_template(
    request: Request,
    template_name: str = Form(...),
    link: str = Form(...),
    category: str = Form(...),
    description: str = Form(None)
):
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    user_id = int(user['id'])
    user_name = user['username']
    bot = request.app.state.bot # 確保從 state 取得

    async with bot.db_pool.acquire() as conn:
        t_id = await conn.fetchval(
            """
            INSERT INTO templates (template_name, link, category, description, uploader_id, uploader_name, status)
            VALUES ($1, $2, $3, $4, $5, $6, '待審核')
            RETURNING id
            """,
            template_name, link, category, description, user_id, user_name
        )

    # 發送 Discord 審核訊息
    dev_id = config['DEVELOPER_ID']
    dev_user = await bot.fetch_user(dev_id)
    
    if dev_user:
        embed = discord.Embed(title="🛡️ 新模板審核申請 (網頁端)", color=discord.Color.gold())
        embed.add_field(name="模板名稱", value=template_name, inline=True)
        embed.add_field(name="分類", value=category, inline=True)
        embed.add_field(name="上傳者", value=user_name, inline=False)
        embed.add_field(name="連結", value=link, inline=False)
        embed.description = f"描述：{description}"
        
        # 使用 views.py 裡的 View
        view = TemplateReviewView(t_id, bot.db_pool, user_name)
        await dev_user.send(embed=embed, view=view)

    return RedirectResponse(url="/templates", status_code=303)

# 1. 審核專區頁面
@app.get("/templates/review", response_class=HTMLResponse)
async def review_page(request: Request):
    user = request.session.get("user")
    bot = request.app.state.bot
    
    # 權限檢查：只有開發者與管理員能進來
    user_id = int(user['id']) if user else None
    is_admin_user = False
    if user_id == config['DEVELOPER_ID']:
        is_admin_user = True
    else:
        async with bot.db_pool.acquire() as conn:
            is_manager = await conn.fetchval("SELECT user_id FROM managers WHERE user_id = $1", user_id)
            if is_manager:
                is_admin_user = True

    if not is_admin_user:
        return RedirectResponse(url="/templates")

    # 撈取待處理的模板
    async with bot.db_pool.acquire() as conn:
        reviews = await conn.fetch(
            "SELECT * FROM templates WHERE status IN ('待審核', '已下放') ORDER BY created_at ASC"
        )

    return templates.TemplateResponse("review_center.html", {
        "request": request,
        "user": user,
        "reviews": reviews
    })

# 2. 審核動作 API
@app.post("/templates/action/{t_id}")
async def template_action(t_id: int, request: Request, action: str = Form(...)):
    bot = request.app.state.bot
    status_map = {"approve": "已通過", "reject": "未通過"}
    new_status = status_map.get(action)

    if not new_status:
        return {"error": "無效的操作"}

    async with bot.db_pool.acquire() as conn:
        await conn.execute("UPDATE templates SET status = $1 WHERE id = $2", new_status, t_id)
    
    return RedirectResponse(url="/templates/review", status_code=303)

# 1. 刪除模板 API
@app.post("/templates/delete/{t_id}")
async def delete_template(t_id: int, request: Request):
    user = request.session.get("user")
    bot = request.app.state.bot
    user_id = int(user['id']) if user else None

    # 權限檢查：開發者或模板管理員
    async with bot.db_pool.acquire() as conn:
        is_manager = await conn.fetchval("SELECT user_id FROM managers WHERE user_id = $1", user_id)
        is_dev = (user_id == config['DEVELOPER_ID'])

        if is_dev or is_manager:
            await conn.execute("DELETE FROM templates WHERE id = $1", t_id)
            return RedirectResponse(url="/templates", status_code=303)
        
    raise HTTPException(status_code=403, detail="權限不足")

# 2. 修改模板 API (更新資料)
@app.post("/templates/edit/{t_id}")
async def edit_template(
    t_id: int, 
    request: Request,
    template_name: str = Form(...),
    link: str = Form(...),
    category: str = Form(...),
    description: str = Form(None)
):
    user = request.session.get("user")
    bot = request.app.state.bot
    user_id = int(user['id']) if user else None

    async with bot.db_pool.acquire() as conn:
        is_manager = await conn.fetchval("SELECT user_id FROM managers WHERE user_id = $1", user_id)
        is_dev = (user_id == config['DEVELOPER_ID'])

        if is_dev or is_manager:
            await conn.execute(
                """
                UPDATE templates 
                SET template_name = $1, link = $2, category = $3, description = $4 
                WHERE id = $5
                """,
                template_name, link, category, description, t_id
            )
            return RedirectResponse(url="/templates", status_code=303)

    raise HTTPException(status_code=403, detail="權限不足")

# 伺服器成員管理頁面
@app.get("/guild/{guild_id}") # 建議改為 /guilds 保持路徑風格統一
async def guild_entry_point(guild_id: int, request: Request):
    user = request.session.get("user")
    if not user: 
        return RedirectResponse("/login")
    
    bot = request.app.state.bot
    user_id = int(user['id'])
    
    # 取得身份等級 (owner, admin, member, none)
    access = await check_user_access(bot, guild_id, user_id)

    if access == "none":
        # 機器人不在該伺服器，導回列表
        return RedirectResponse("/guilds")
    
    elif access in ["owner", "admin"]:
        # 呼叫下方的管理面板處理函式，並傳入目前的權限等級
        return await guild_members_page(guild_id, request, access)
    
    else:
        # 一般成員導向個人狀態頁面
        return RedirectResponse(url=f"/guild/{guild_id}/my-status")

# 3. 獨立的管理面板處理函式
# --- web_main.py ---

# --- web_main.py ---

# --- web_main.py ---

async def guild_members_page(guild_id: int, request: Request, access_level: str):
    bot = request.app.state.bot
    guild = bot.get_guild(guild_id)
    
    if not guild:
        return RedirectResponse("/guilds")

    # 🚀 [新增 1] 獲取使用者資料與身分，以供頂部導航列使用
    user = request.session.get("user")
    if not user: return RedirectResponse("/login")
    user_id = int(user['id'])
    user_role = await get_user_role_text(bot, user_id)
    
    async with bot.db_pool.acquire() as conn:
        # 1. 獲取管理員名單
        admin_list = await conn.fetchval("SELECT admin_list FROM guilds WHERE guild_id = $1", guild_id) or []
        
        # 2. 獲取伺服器設定
        settings = await conn.fetchrow("SELECT offset_enabled FROM guilds WHERE guild_id = $1", guild_id)
        
        # 3. 獲取自動化規則
        raw_rules = await conn.fetch("SELECT * FROM auto_actions WHERE guild_id = $1 ORDER BY type, threshold ASC", guild_id)
        rules_list = []
        for r in raw_rules:
            rules_list.append({
                "type": r["type"],
                "threshold": r["threshold"],
                "action_type": r["action_type"]
            })
            
        # 4. 獲取獎懲統計
        stats = await conn.fetch("""
            SELECT user_id, 
                   SUM(CASE WHEN type = '警告' THEN count ELSE 0 END) as warning_points,
                   SUM(CASE WHEN type = '嘉獎' THEN count ELSE 0 END) as commend_points
            FROM member_records 
            WHERE guild_id = $1 
            GROUP BY user_id
        """, guild_id)
    
    # 排序邏輯
    def sort_key(m):
        if m.id == guild.owner_id: return 0
        if m.id in admin_list: return 1
        return 2

    sorted_members = sorted(guild.members, key=sort_key)
    
    return templates.TemplateResponse("member_management.html", {
        "request": request,
        # 🚀 [新增 2] 傳遞 user 與 user_role 給模板
        "user": user,
        "user_role": user_role,
        "guild": guild,
        "members": sorted_members,
        "admin_list": admin_list,
        "settings": settings or {"offset_enabled": False},
        "rules": rules_list,
        "stats": {s['user_id']: s for s in stats},
        "is_owner": access_level == "owner"
    })

async def get_user_guild_role(bot, guild_id: int, user_id: int):
    """
    返回身分等級：0 (成員), 1 (授權管理員), 2 (擁有者)
    """
    guild = bot.get_guild(guild_id)
    if not guild:
        return 0
    
    # 1. 檢查是否為擁有者
    if guild.owner_id == user_id:
        return 2
        
    # 2. 檢查是否在資料庫的 admin_list 中
    async with bot.db_pool.acquire() as conn:
        admin_list = await conn.fetchval(
            "SELECT admin_list FROM guilds WHERE guild_id = $1", 
            guild_id
        )
        # admin_list 是 BIGINT[]
        if admin_list and user_id in admin_list:
            return 1
            
    return 0

# --- 更新後的成員列表路由 ---
@app.get("/guilds/{guild_id}/members")
async def guild_members(guild_id: int, request: Request):
    user = request.session.get("user")
    if not user: return RedirectResponse("/")
    
    bot = request.app.state.bot
    user_id = int(user['id'])
    
    # 檢查權限
    role_level = await get_user_guild_role(bot, guild_id, user_id)
    
    if role_level == 0:
        # 一般成員：導向「我的信用頁面」而非管理面板
        return RedirectResponse(f"/guilds/{guild_id}/my-status")
    
    # 擁有者或管理員：允許進入
    guild = bot.get_guild(guild_id)
    # ... 原有的抓取成員與統計資料邏輯 ...
    return templates.TemplateResponse("member_management.html", {
        "request": request,
        "guild": guild,
        "role_level": role_level, # 2 為擁有者，1 為管理員
        "is_owner": role_level == 2
    })

@app.get("/guilds/{guild_id}")
async def guild_entry_point(guild_id: int, request: Request):
    user = request.session.get("user")
    if not user: return RedirectResponse("/login")
    
    bot = request.app.state.bot
    user_id = int(user['id'])
    
    # 取得身份等級
    access_level = await check_user_access(bot, guild_id, user_id)
    
    if access_level in ["owner", "admin"]:
        # ✨ 注意：這裡要呼叫正確的函式名稱，並傳入 is_owner 判斷
        return await member_management_page(guild_id, request, is_owner=(access_level == "owner"))
    elif access_level == "member":
        return RedirectResponse(url=f"/guilds/{guild_id}/my-status")
    else:
        # 機器人不在該伺服器或找不到伺服器
        return RedirectResponse("/guilds")
    
# 修改個人信用頁面的數據抓取
# --- web_main.py ---

@app.get("/guild/{guild_id}/my-status", response_class=HTMLResponse)
async def my_status(guild_id: int, request: Request):
    """成員端：個人信用中心"""
    user = request.session.get("user")
    if not user: return RedirectResponse("/")
    
    bot = request.app.state.bot
    user_id = int(user['id'])
    guild = bot.get_guild(guild_id)
    if not guild: return RedirectResponse("/guilds")

    # 🚀 [新增 1] 獲取使用者身分 (供頂部導航列使用)
    user_role = await get_user_role_text(bot, user_id)

    async with bot.db_pool.acquire() as conn:
        # 1. 抓取個人獎懲統計
        stats = await conn.fetchrow("""
            SELECT SUM(CASE WHEN type = '警告' THEN count ELSE 0 END) as warning_points,
                   SUM(CASE WHEN type = '嘉獎' THEN count ELSE 0 END) as commend_points
            FROM member_records 
            WHERE guild_id = $1 AND user_id = $2
        """, guild_id, user_id)
        
        # 2. 抓取伺服器設定
        settings = await conn.fetchrow("SELECT offset_enabled FROM guilds WHERE guild_id = $1", guild_id)
        
        # 3. 抓取自動化門檻規則
        auto_rules = await conn.fetch("SELECT * FROM auto_actions WHERE guild_id = $1 ORDER BY threshold ASC", guild_id)
        
        # 4. 抓取管理員 ID 列表
        admin_ids = await conn.fetchval("SELECT admin_list FROM guilds WHERE guild_id = $1", guild_id) or []

    # 5. 處理管理員顯示資訊
    processed_admins = []
    for aid in admin_ids:
        if aid == guild.owner_id: continue
        
        member = guild.get_member(aid)
        if not member:
            try:
                member = await guild.fetch_member(aid)
            except:
                continue
        
        if member:
            processed_admins.append({
                "name": member.display_name,
                "avatar": member.display_avatar.url
            })

    return templates.TemplateResponse("my_status.html", {
        "request": request,
        "user": user,
        "user_role": user_role, # 🚀 [新增 2] 傳遞變數給前端
        "guild": guild,
        "stats": stats or {"warning_points": 0, "commend_points": 0},
        "settings": settings or {"offset_enabled": False},
        "auto_rules": auto_rules,
        "processed_admins": processed_admins
    })

@app.post("/guild/{guild_id}/member/{target_id}/action")
async def member_action(
    guild_id: int, 
    target_id: int, 
    request: Request,
    action_type: str = Form(...), 
    count: int = Form(...),
    reason: str = Form(None)
):
    user = request.session.get("user")
    if not user: return RedirectResponse("/login")
    
    bot = request.app.state.bot
    operator_id = int(user['id'])
    guild = bot.get_guild(guild_id)
    target_member = guild.get_member(target_id)
    
    if not guild or not target_member:
        return {"success": False, "message": "找不到成員"}

    async with bot.db_pool.acquire() as conn:
        # 1. 權限檢查
        admin_list = await conn.fetchval("SELECT admin_list FROM guilds WHERE guild_id = $1", guild_id) or []
        is_owner = (operator_id == guild.owner_id)
        is_admin = (operator_id in admin_list)
        target_is_admin = (target_id in admin_list or target_id == guild.owner_id)
        
        if is_admin and not is_owner and target_is_admin:
            return {"success": False, "message": "管理員無法對管理員執行獎懲"}

        # 2. 寫入資料庫：統一使用 member_records
        type_cn = "警告" if action_type == "warn" else "嘉獎"
        reason_text = reason or "網頁操作未註明原因"
        
        await conn.execute(
            """
            INSERT INTO member_records 
            (guild_id, user_id, user_name, type, count, reason, operator_id, operator_name) 
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            guild_id, target_id, target_member.display_name, 
            type_cn, count, reason_text, operator_id, user['username']
        )

    # 🚀 3. 同步至 Discord (關鍵連動區)
    cog = bot.get_cog("ModerationCog")
    if cog:
        try:
            # 建立日誌 Embed
            color = discord.Color.red() if action_type == "warn" else discord.Color.gold()
            emoji = "⚠️" if action_type == "warn" else "✨"
            
            embed = discord.Embed(
                title=f"{emoji} {type_cn}異動紀錄 (網頁端)", 
                color=color, 
                timestamp=datetime.now()
            )
            embed.add_field(name="對象", value=target_member.mention, inline=True)
            embed.add_field(name="變動次數", value=f"**{count}** 次", inline=True)
            embed.add_field(name="管理員", value=f"<@{operator_id}>", inline=True)
            embed.add_field(name="原因", value=reason_text, inline=False)
            embed.set_footer(text=f"User ID: {target_id}")

            # 呼叫機器人方法
            await cog.log_to_channel(guild, embed)
            await cog.check_auto_actions(guild, target_member, type_cn)
            print(f"✅ 已成功連動 Discord 發送 {type_cn} 日誌")

            print("DEBUG: 發送函式已呼叫")
        except Exception as e:
            print(f"DEBUG: 發送過程中發生異常: {e}")
    else:
        # 如果沒找到 Cog，會在控制台噴出這行
        print(f"DEBUG: 找不到 ModerationCog。目前可用的 Cog 有: {list(bot.cogs.keys())}")

    return RedirectResponse(f"/guild/{guild_id}", status_code=303)

# --- 伺服器自動化設定頁面 ---

# 2. 儲存/更新規則 API (含衝突提醒邏輯)
@app.post("/guild/{guild_id}/settings/add_rule")
async def add_rule(
    guild_id: int, 
    request: Request,
    type: str = Form(...),
    threshold: int = Form(...),
    action_type: str = Form(...),
    timeout_duration: int = Form(None),
    role_id: int = Form(None)
):
    bot = request.app.state.bot
    async with bot.db_pool.acquire() as conn:
        # 使用 ON CONFLICT 達成「覆蓋舊規則」的效果，並解決衝突問題
        await conn.execute("""
            INSERT INTO auto_actions (guild_id, type, threshold, action_type, timeout_duration, role_id)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (guild_id, type, threshold) 
            DO UPDATE SET action_type = $4, timeout_duration = $5, role_id = $6
        """, guild_id, type, threshold, action_type, timeout_duration, role_id)
        
    return RedirectResponse(url=f"/guild/{guild_id}/settings", status_code=303)

# --- [新增] 自動化設定頁面路由 ---
@app.get("/guild/{guild_id}/settings", response_class=HTMLResponse)
async def server_settings(guild_id: int, request: Request):
    user = request.session.get("user")
    if not user: return RedirectResponse("/login")
    
    bot = request.app.state.bot
    user_id = int(user['id']) # 確保轉為 int
    
    # 1. 權限檢查
    access = await check_user_access(bot, guild_id, user_id)
    if access not in ["owner", "admin"]:
        return RedirectResponse(f"/guild/{guild_id}")

    # 🚀 [新增 1] 獲取使用者身分 (供頂部導航列使用)
    user_role = await get_user_role_text(bot, user_id)

    async with bot.db_pool.acquire() as conn:
        settings = await conn.fetchrow("SELECT offset_enabled FROM guilds WHERE guild_id = $1", guild_id)
        
        raw_rules = await conn.fetch("SELECT id, type, threshold, action_type, timeout_duration, role_id FROM auto_actions WHERE guild_id = $1", guild_id)
        rules_list = []
        for r in raw_rules:
            rules_list.append({
                "id": r["id"],
                "type": r["type"],
                "threshold": r["threshold"],
                "action_type": r["action_type"],
                "timeout_duration": r["timeout_duration"],
                "role_id": r["role_id"]
            })

    guild = bot.get_guild(guild_id)
    
    return templates.TemplateResponse("server_settings.html", {
        "request": request,
        "user": user,           # 🚀 [新增 2] 傳遞使用者資料
        "user_role": user_role, # 🚀 [新增 3] 傳遞身分文字
        "guild": guild,
        "settings": settings or {"offset_enabled": False},
        "rules": rules_list,
        "roles": [r for r in guild.roles if not r.managed and r.name != "@everyone"]
    })

# --- [新增] 全局開關切換 API ---
@app.post("/guild/{guild_id}/settings/toggle-offset")
async def toggle_offset(guild_id: int, request: Request, enabled: bool = Form(...)):
    bot = request.app.state.bot
    async with bot.db_pool.acquire() as conn:
        await conn.execute("UPDATE guilds SET offset_enabled = $1 WHERE guild_id = $2", enabled, guild_id)
    return RedirectResponse(f"/guild/{guild_id}/settings", status_code=303)

# --- [新增] 新增或修改規則 API (處理衝突) ---
@app.post("/guild/{guild_id}/settings/rule/save")
async def save_rule(
    guild_id: int, 
    request: Request,
    rule_type: str = Form(...), # '警告' 或 '嘉獎'
    threshold: int = Form(...),
    action_type: str = Form(...),
    timeout_duration: int = Form(None),
    role_id: int = Form(None)
):
    bot = request.app.state.bot
    async with bot.db_pool.acquire() as conn:
        # 使用 ON CONFLICT：如果 (guild_id, type, threshold) 重複，則更新現有動作
        await conn.execute("""
            INSERT INTO auto_actions (guild_id, type, threshold, action_type, timeout_duration, role_id)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (guild_id, type, threshold) 
            DO UPDATE SET action_type = $4, timeout_duration = $5, role_id = $6
        """, guild_id, rule_type, threshold, action_type, timeout_duration, role_id)
        
    return RedirectResponse(f"/guild/{guild_id}/settings", status_code=303)

# --- [新增] 刪除規則 API ---
@app.post("/guild/{guild_id}/settings/rule/delete/{rule_id}")
async def delete_rule(guild_id: int, rule_id: int, request: Request):
    bot = request.app.state.bot
    async with bot.db_pool.acquire() as conn:
        await conn.execute("DELETE FROM auto_actions WHERE id = $1 AND guild_id = $2", rule_id, guild_id)
    return RedirectResponse(f"/guild/{guild_id}/settings", status_code=303)

@app.get("/developer/dashboard", response_class=HTMLResponse)
async def dev_dashboard(request: Request):
    # 權限檢查 (Discord ID: 882991365351420005)
    user = request.session.get("user")
    if not user or int(user['id']) != config['DEVELOPER_ID']:
        raise HTTPException(status_code=403, detail="存取拒絕：僅限系統開發者")

    bot = request.app.state.bot
    guild_data_list = []

    for guild in bot.guilds:
        # 計算成員與機器人數量
        bot_count = sum(1 for m in guild.members if m.bot)
        human_count = guild.member_count - bot_count
        
        # 取得管理員清單 (具有 administrator 權限的成員)
        admins = [m.display_name for m in guild.members if m.guild_permissions.administrator and not m.bot]
        
        # 頻道列表分類
        channels = {
            "text": [c.name for c in guild.text_channels],
            "voice": [c.name for c in guild.voice_channels]
        }

        guild_data_list.append({
            "name": guild.name,
            "id": guild.id,
            "owner": f"{guild.owner} ({guild.owner_id})",
            "member_count": guild.member_count,
            "bot_count": bot_count,
            "human_count": human_count,
            "admins": admins,
            "channels": channels,
            "created_at": guild.created_at.strftime('%Y-%m-%d')
        })

    return templates.TemplateResponse("dev_dashboard.html", {
        "request": request,
        "guilds": guild_data_list,
        "total_stats": {
            "server_count": len(bot.guilds),
            "total_users": sum(g.member_count for g in bot.guilds)
        }
    })