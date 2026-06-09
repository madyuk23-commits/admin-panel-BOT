import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
import asyncio
import os
from flask import Flask, request, jsonify
import threading
from datetime import datetime

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
SECRET_KEY = os.environ.get("SECRET_KEY", "X7kL9pQ2mR4nV8wY1zC3bA6sD5fG9hJ2")
API_PORT = int(os.environ.get("PORT", 5000))

DB_PATH = "ranks.db"

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS ranks (
                user_id INTEGER PRIMARY KEY,
                rank TEXT NOT NULL,
                updated_by INTEGER,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Таблица для отслеживания принудительных обновлений
        await db.execute('''
            CREATE TABLE IF NOT EXISTS force_updates (
                user_id INTEGER PRIMARY KEY,
                force_update INTEGER DEFAULT 0,
                last_force TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.commit()

async def get_rank(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT rank FROM ranks WHERE user_id = ?', (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else "Default"

async def set_rank(user_id, rank, executor_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            INSERT INTO ranks (user_id, rank, updated_by) 
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET 
                rank = excluded.rank,
                updated_by = excluded.updated_by,
                updated_at = CURRENT_TIMESTAMP
        ''', (user_id, rank, executor_id))
        await db.commit()
        
        # Устанавливаем флаг принудительного обновления
        await db.execute('''
            INSERT INTO force_updates (user_id, force_update) 
            VALUES (?, 1)
            ON CONFLICT(user_id) DO UPDATE SET 
                force_update = 1,
                last_force = CURRENT_TIMESTAMP
        ''', (user_id,))
        await db.commit()
        
    print(f"[BOT] Ранг {rank} выдан пользователю {user_id} (флаг обновления установлен)")

async def get_force_update_flag(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT force_update FROM force_updates WHERE user_id = ?', (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

async def clear_force_update_flag(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('UPDATE force_updates SET force_update = 0 WHERE user_id = ?', (user_id,))
        await db.commit()

app = Flask(__name__)

@app.route('/api/get_rank', methods=['POST'])
def api_get_rank():
    data = request.json
    api_key = request.headers.get('X-API-Key')
    if api_key != SECRET_KEY:
        return jsonify({"error": "Unauthorized"}), 401
    user_id = data.get('userId')
    if not user_id:
        return jsonify({"error": "Missing userId"}), 400
    
    rank = asyncio.run_coroutine_threadsafe(get_rank(user_id), loop).result()
    return jsonify({"rank": rank, "success": True})

@app.route('/api/check_update', methods=['POST'])
def api_check_update():
    data = request.json
    api_key = request.headers.get('X-API-Key')
    if api_key != SECRET_KEY:
        return jsonify({"error": "Unauthorized"}), 401
    user_id = data.get('userId')
    if not user_id:
        return jsonify({"error": "Missing userId"}), 400
    
    need_update = asyncio.run_coroutine_threadsafe(get_force_update_flag(user_id), loop).result()
    
    # Если нужно обновление, возвращаем текущий ранг и очищаем флаг
    if need_update:
        rank = asyncio.run_coroutine_threadsafe(get_rank(user_id), loop).result()
        asyncio.run_coroutine_threadsafe(clear_force_update_flag(user_id), loop).result()
        return jsonify({"need_update": True, "rank": rank, "success": True})
    
    return jsonify({"need_update": False, "success": True})

@app.route('/api/health', methods=['GET'])
def health_check():
    return jsonify({"status": "alive"})

def run_flask():
    app.run(host='0.0.0.0', port=API_PORT, debug=False, use_reloader=False)

class RankBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(command_prefix="!", intents=intents)
    
    async def setup_hook(self):
        await self.tree.sync()
        print("✅ Слэш-команды синхронизированы")

bot = RankBot()

@bot.event
async def on_ready():
    print(f"✅ Бот запущен как {bot.user}")
    await init_db()
    await bot.change_presence(activity=discord.Game(name="/setrank | /update"))

@bot.tree.command(name="setrank", description="Выдать ранг в Roblox")
@app_commands.describe(user_id="Roblox User ID", rank="Ранг")
@app_commands.choices(rank=[
    app_commands.Choice(name="🔧 Модератор", value="Moderator"),
    app_commands.Choice(name="🛡️ Администратор", value="Admin"),
    app_commands.Choice(name="⭐ Ст. Администратор", value="SeniorAdmin"),
    app_commands.Choice(name="👤 Снять ранг", value="Default")
])
@app_commands.default_permissions(administrator=True)
async def setrank(interaction: discord.Interaction, user_id: str, rank: app_commands.Choice[str]):
    try:
        user_id_int = int(user_id)
    except:
        await interaction.response.send_message("❌ User ID должен быть числом!", ephemeral=True)
        return
    
    await set_rank(user_id_int, rank.value, interaction.user.id)
    
    rank_names = {"Moderator": "🔧 Модератор", "Admin": "🛡️ Администратор", "SeniorAdmin": "⭐ Ст. Администратор", "Default": "👤 Игрок"}
    await interaction.response.send_message(
        f"✅ Ранг **{rank_names[rank.value]}** выдан пользователю `{user_id_int}`\n"
        f"🔄 Игрок получит ранг в течение 10 секунд или используйте `/update` для мгновенного обновления"
    )

@bot.tree.command(name="update", description="Принудительно обновить ранг у игрока в игре")
@app_commands.describe(user_id="Roblox User ID игрока")
@app_commands.default_permissions(administrator=True)
async def force_update(interaction: discord.Interaction, user_id: str):
    try:
        user_id_int = int(user_id)
    except:
        await interaction.response.send_message("❌ User ID должен быть числом!", ephemeral=True)
        return
    
    # Устанавливаем флаг принудительного обновления
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            INSERT INTO force_updates (user_id, force_update) 
            VALUES (?, 1)
            ON CONFLICT(user_id) DO UPDATE SET 
                force_update = 1,
                last_force = CURRENT_TIMESTAMP
        ''', (user_id_int,))
        await db.commit()
    
    rank = await get_rank(user_id_int)
    rank_names = {"Moderator": "🔧 Модератор", "Admin": "🛡️ Администратор", "SeniorAdmin": "⭐ Ст. Администратор", "Default": "👤 Игрок"}
    
    await interaction.response.send_message(
        f"🔄 Запрос на обновление ранга отправлен для `{user_id_int}`\n"
        f"🏆 Текущий ранг: **{rank_names.get(rank, rank)}**\n"
        f"✅ Игрок получит обновление в течение 5 секунд"
    )

@bot.tree.command(name="getrank", description="Проверить ранг")
async def getrank(interaction: discord.Interaction, user_id: str):
    try:
        user_id_int = int(user_id)
    except:
        await interaction.response.send_message("❌ User ID должен быть числом!", ephemeral=True)
        return
    
    rank = await get_rank(user_id_int)
    rank_names = {"Moderator": "🔧 Модератор", "Admin": "🛡️ Администратор", "SeniorAdmin": "⭐ Ст. Администратор", "Default": "👤 Игрок"}
    await interaction.response.send_message(f"🎮 Ранг пользователя `{user_id_int}`: **{rank_names.get(rank, rank)}**")

@bot.tree.command(name="findid", description="Как найти свой Roblox ID")
async def findid(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🔍 Как найти Roblox ID",
        description="1. Зайдите на roblox.com\n2. Откройте свой профиль\n3. Посмотрите на URL: `roblox.com/users/123456789/profile`\n4. Число 123456789 - ваш ID",
        color=discord.Color.blue()
    )
    await interaction.response.send_message(embed=embed)

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    threading.Thread(target=run_flask, daemon=True).start()
    bot.run(DISCORD_TOKEN)
