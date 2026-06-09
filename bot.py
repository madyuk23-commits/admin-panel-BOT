import discord
from discord import app_commands
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
    print("✅ База данных инициализирована")

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
        print(f"[DB] Ранг {rank} выдан пользователю {user_id}")

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

# ========== DISCORD БОТ (СЛЭШ-КОМАНДЫ) ==========
class RankBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(command_prefix="!", intents=intents)
    
    async def setup_hook(self):
        await self.tree.sync()
        print(f"✅ Слэш-команды синхронизированы")

bot = RankBot()

# Список доступных рангов для автодополнения
RANK_CHOICES = [
    app_commands.Choice(name="🔧 Модератор", value="Moderator"),
    app_commands.Choice(name="🛡️ Администратор", value="Admin"),
    app_commands.Choice(name="⭐ Ст. Администратор", value="SeniorAdmin"),
    app_commands.Choice(name="👤 Игрок (снять ранг)", value="Default")
]

@bot.event
async def on_ready():
    print(f"✅ Бот запущен как {bot.user}")
    print(f"📡 API сервер на порту {API_PORT}")
    await init_db()
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="/setrank | Roblox Admin"
        )
    )

# ========== СЛЭШ-КОМАНДЫ ==========

@bot.tree.command(name="setrank", description="Выдать ранг игроку в Roblox")
@app_commands.describe(
    user_id="Roblox User ID игрока (число)",
    rank="Выберите ранг для выдачи"
)
@app_commands.choices(rank=RANK_CHOICES)
@app_commands.default_permissions(administrator=True)
async def setrank(interaction: discord.Interaction, user_id: str, rank: app_commands.Choice[str]):
    # Проверяем, что user_id - число
    try:
        user_id_int = int(user_id)
    except ValueError:
        embed = discord.Embed(
            title="❌ Ошибка",
            description="User ID должен быть числом!\n\nКак получить Roblox ID: https://www.roblox.com/users/ВАШ_НИК/profile",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    # Выдаём ранг (без поиска пользователя в Discord)
    await set_rank(user_id_int, rank.value, interaction.user.id)
    
    # Названия рангов для отображения
    rank_names = {
        "Moderator": "🔧 Модератор",
        "Admin": "🛡️ Администратор",
        "SeniorAdmin": "⭐ Ст. Администратор",
        "Default": "👤 Игрок"
    }
    
    embed = discord.Embed(
        title="✅ Ранг выдан",
        description=f"**Roblox ID:** `{user_id_int}`\n**Ранг:** {rank_names.get(rank.value, rank.value)}",
        color=discord.Color.green()
    )
    embed.add_field(name="Выдал", value=interaction.user.mention, inline=True)
    embed.add_field(name="Дата", value=datetime.now().strftime("%d.%m.%Y %H:%M"), inline=True)
    embed.set_footer(text="Изменения вступят в силу при следующем входе в игру")
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="getrank", description="Проверить ранг игрока в Roblox")
@app_commands.describe(
    user_id="Roblox User ID игрока"
)
async def getrank(interaction: discord.Interaction, user_id: str):
    try:
        user_id_int = int(user_id)
    except ValueError:
        embed = discord.Embed(
            title="❌ Ошибка",
            description="User ID должен быть числом!",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    rank = await get_rank(user_id_int)
    
    rank_names = {
        "Moderator": "🔧 Модератор",
        "Admin": "🛡️ Администратор",
        "SeniorAdmin": "⭐ Ст. Администратор",
        "Default": "👤 Игрок"
    }
    
    rank_display = rank_names.get(rank, rank)
    
    # Цвет для ранга
    colors = {
        "Moderator": discord.Color.green(),
        "Admin": discord.Color.blue(),
        "SeniorAdmin": discord.Color.red(),
        "Default": discord.Color.light_gray()
    }
    color = colors.get(rank, discord.Color.gray())
    
    embed = discord.Embed(
        title="🎮 Информация о ранге",
        description=f"**Roblox ID:** `{user_id_int}`\n**Ранг:** {rank_display}",
        color=color
    )
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="resetrank", description="Сбросить ранг игрока на Default")
@app_commands.describe(
    user_id="Roblox User ID игрока"
)
@app_commands.default_permissions(administrator=True)
async def resetrank(interaction: discord.Interaction, user_id: str):
    try:
        user_id_int = int(user_id)
    except ValueError:
        embed = discord.Embed(
            title="❌ Ошибка",
            description="User ID должен быть числом!",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    await set_rank(user_id_int, "Default", interaction.user.id)
    
    embed = discord.Embed(
        title="🔄 Ранг сброшен",
        description=f"**Roblox ID:** `{user_id_int}`\nРанг сброшен на **👤 Игрок**",
        color=discord.Color.orange()
    )
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="listranks", description="Показать список доступных рангов")
async def listranks(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📋 Доступные ранги",
        description="""
        **🔧 Модератор** - Полёт, телепорт, вылечить, заморозка
        **🛡️ Администратор** - Всё из Модератора + убить, кик
        **⭐ Ст. Администратор** - Всё из Администратора + бан
        **👤 Игрок** - Без прав
        """,
        color=discord.Color.purple()
    )
    embed.set_footer(text="Ранги выдаются через команду /setrank")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="findid", description="Как найти свой Roblox User ID")
async def findid(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🔍 Как найти свой Roblox User ID",
        description="""
        **Способ 1 (через профиль):**
        1. Зайдите на roblox.com
        2. Откройте свой профиль
        3. Посмотрите на URL: `roblox.com/users/123456789/profile`
        4. Цифры 123456789 - это ваш ID

        **Способ 2 (в игре):**
        1. Откройте Roblox Studio
        2. Запустите игру (F5)
        3. В консоли Output будет ваш User ID
        """,
        color=discord.Color.blue()
    )
    embed.set_footer(text="Используйте этот ID в команде /setrank")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="stats", description="Показать статистику бота")
async def stats(interaction: discord.Interaction):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT COUNT(*) FROM ranks') as cursor:
            total = (await cursor.fetchone())[0]
        
        async with db.execute('SELECT COUNT(*) FROM logs') as cursor:
            logs = (await cursor.fetchone())[0]
        
        # Статистика по рангам
        rank_stats = {}
        for rank in ["Moderator", "Admin", "SeniorAdmin", "Default"]:
            async with db.execute('SELECT COUNT(*) FROM ranks WHERE rank = ?', (rank,)) as cursor:
                rank_stats[rank] = (await cursor.fetchone())[0]
    
    rank_names = {
        "Moderator": "🔧 Модераторы",
        "Admin": "🛡️ Администраторы",
        "SeniorAdmin": "⭐ Ст. Администраторы",
        "Default": "👤 Игроки"
    }
    
    embed = discord.Embed(
        title="📊 Статистика бота",
        description=f"**Всего игроков в БД:** {total}\n**Всего действий:** {logs}",
        color=discord.Color.gold()
    )
    
    for rank_key, rank_name in rank_names.items():
        embed.add_field(name=rank_name, value=str(rank_stats[rank_key]), inline=True)
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="help", description="Помощь по командам")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🤖 Помощь по командам",
        description="Все команды бота:",
        color=discord.Color.green()
    )
    embed.add_field(
        name="/setrank <roblox_id> <ранг>",
        value="Выдать ранг игроку (требует права администратора)",
        inline=False
    )
    embed.add_field(
        name="/getrank <roblox_id>",
        value="Проверить ранг игрока",
        inline=False
    )
    embed.add_field(
        name="/resetrank <roblox_id>",
        value="Сбросить ранг игрока (требует права администратора)",
        inline=False
    )
    embed.add_field(
        name="/listranks",
        value="Показать список доступных рангов",
        inline=False
    )
    embed.add_field(
        name="/findid",
        value="Как найти свой Roblox User ID",
        inline=False
    )
    embed.add_field(
        name="/stats",
        value="Показать статистику бота",
        inline=False
    )
    
    await interaction.response.send_message(embed=embed)

# Обработчик ошибок
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        embed = discord.Embed(
            title="❌ Нет прав",
            description="У вас нет прав администратора для выполнения этой команды!",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        embed = discord.Embed(
            title="❌ Ошибка",
            description=str(error),
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

# ========== ЗАПУСК ==========
if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    threading.Thread(target=run_flask, daemon=True).start()
    bot.run(DISCORD_TOKEN)
