import discord
from discord.ext import commands
import aiosqlite
import asyncio
import os
from flask import Flask, request, jsonify
import threading
from datetime import datetime

# ========== НАСТРОЙКИ ==========
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
SECRET_KEY = os.environ.get("SECRET_KEY", "X7kL9pQ2mR4nV8wY1zC3bA6sD5fG9hJ2")
API_PORT = int(os.environ.get("PORT", 5000))

# ========== БАЗА ДАННЫХ ==========
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
        await db.execute('''
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                rank TEXT,
                executor_id INTEGER,
                action TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
        
        await db.execute('''
            INSERT INTO logs (user_id, rank, executor_id, action)
            VALUES (?, ?, ?, 'set_rank')
        ''', (user_id, rank, executor_id))
        await db.commit()

# ========== FLASK API ==========
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

@app.route('/api/set_rank', methods=['POST'])
def api_set_rank():
    data = request.json
    api_key = request.headers.get('X-API-Key')
    
    if api_key != SECRET_KEY:
        return jsonify({"error": "Unauthorized"}), 401
    
    user_id = data.get('userId')
    rank = data.get('rank')
    executor_id = data.get('executorId')
    
    if not user_id or not rank:
        return jsonify({"error": "Missing required fields"}), 400
    
    valid_ranks = ["Moderator", "Admin", "SeniorAdmin", "Default"]
    if rank not in valid_ranks:
        return jsonify({"error": f"Invalid rank"}), 400
    
    asyncio.run_coroutine_threadsafe(set_rank(user_id, rank, executor_id), loop).result()
    return jsonify({"success": True, "rank": rank})

@app.route('/api/health', methods=['GET'])
def health_check():
    return jsonify({"status": "alive", "timestamp": datetime.now().isoformat()})

def run_flask():
    app.run(host='0.0.0.0', port=API_PORT, debug=False, use_reloader=False)

# ========== DISCORD БОТ ==========
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Бот запущен как {bot.user}")
    await init_db()
    await bot.change_presence(activity=discord.Game(name="!setrank | Roblox Admin"))

@bot.command(name="setrank")
@commands.has_permissions(administrator=True)
async def set_rank_cmd(ctx, user_id: int, rank: str):
    valid_ranks = ["Moderator", "Admin", "SeniorAdmin", "Default"]
    if rank not in valid_ranks:
        embed = discord.Embed(title="❌ Ошибка", description=f"Доступные ранги: {', '.join(valid_ranks)}", color=discord.Color.red())
        await ctx.send(embed=embed)
        return
    
    await set_rank(user_id, rank, ctx.author.id)
    
    embed = discord.Embed(title="✅ Ранг выдан", description=f"Пользователь {user_id} получил ранг **{rank}**", color=discord.Color.green())
    embed.add_field(name="Выдал", value=ctx.author.mention)
    await ctx.send(embed=embed)

@bot.command(name="getrank")
async def get_rank_cmd(ctx, user_id: int):
    rank = await get_rank(user_id)
    embed = discord.Embed(title="🎮 Ранг", description=f"**{user_id}** → **{rank}**", color=discord.Color.blue())
    await ctx.send(embed=embed)

@bot.command(name="resetrank")
@commands.has_permissions(administrator=True)
async def reset_rank_cmd(ctx, user_id: int):
    await set_rank(user_id, "Default", ctx.author.id)
    embed = discord.Embed(title="🔄 Ранг сброшен", description=f"Ранг {user_id} сброшен на Default", color=discord.Color.orange())
    await ctx.send(embed=embed)

@bot.command(name="userid")
async def get_user_id_cmd(ctx, user: discord.User = None):
    target = user or ctx.author
    embed = discord.Embed(title="🆔 User ID", description=f"**{target.name}** → `{target.id}`", color=discord.Color.blue())
    await ctx.send(embed=embed)

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    threading.Thread(target=run_flask, daemon=True).start()
    bot.run(DISCORD_TOKEN)
