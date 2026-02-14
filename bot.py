import discord
from discord.ext import commands
import json
import asyncio
import os
import asyncpg
from web_main import app

with open('config.json', 'r', encoding='utf-8') as f:
    config = json.load(f)

class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True          
        intents.message_content = True  
        intents.guilds = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # 1. 建立資料庫連線池
        self.db_pool = await asyncpg.create_pool(dsn=config.get("DATABASE_URL"))
        self.config = config # 讓 Cog 可以讀取 config
        
        # 2. 自動載入 commands 資料夾下的所有 Cog
        for filename in os.listdir('./commands'):
            if filename.endswith('.py'):
                await self.load_extension(f'commands.{filename[:-3]}')
                print(f"✅ 已載入模組: {filename}")

    async def on_ready(self):
        # 3. 將 bot 注入 FastAPI
        app.state.bot = self
        # 4. 同步斜線指令
        await self.tree.sync()
        print(f"✅ 機器人已就緒: {self.user}，指令已同步")

bot = MyBot()

async def main():
    import uvicorn
    config_uvicorn = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(config_uvicorn)
    
    # 同時啟動網頁與 Bot
    await asyncio.gather(
        server.serve(),
        bot.start(config['TOKEN'])
    )

if __name__ == "__main__":
    asyncio.run(main())