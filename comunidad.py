import asyncio
import json
import os
import random
import re
import time
from datetime import datetime, timezone, timedelta
from collections import defaultdict
import math

import discord
from discord import app_commands
from discord.ext import commands

# Importamos nuestros helpers y configuraciones
from bot import track_command
from emojis import EMOJIS
from config import Config

# --- CONSTANTES Y CONFIGURACIÓN INICIAL ---
# IDs de servidores importantes
NEXUS_PRO_GUILD_ID = 1518751703019552948  # ID del servidor exclusivo de Nexus Pro

# --- FUNCIONES DE PERSISTENCIA (LOCAL) ---
# En un futuro, idealmente migrar a Supabase, pero por ahora usamos archivos JSON locales.
# Nota: Para comandos de economía, esto es suficiente para empezar, pero para producción grande es mejor Supabase.

_BASE_PATH = "data/"
os.makedirs(_BASE_PATH, exist_ok=True)

def _load_data(filename: str) -> dict:
    """Carga un archivo JSON desde data/"""
    path = os.path.join(_BASE_PATH, filename)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return {}

def _save_data(filename: str, data: dict) -> None:
    """Guarda un archivo JSON en data/"""
    path = os.path.join(_BASE_PATH, filename)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, path)


# --- 1. SISTEMA DE ECONOMÍA ---
# Archivos de datos
ECONOMY_FILE = "economy.json"
SHOP_FILE = "shop.json"

def _get_economy(guild_id: int) -> dict:
    data = _load_data(ECONOMY_FILE)
    return data.get(str(guild_id), {})

def _set_economy(guild_id: int, data: dict) -> None:
    all_data = _load_data(ECONOMY_FILE)
    all_data[str(guild_id)] = data
    _save_data(ECONOMY_FILE, all_data)

def _get_user_balance(guild_id: int, user_id: int) -> dict:
    eco = _get_economy(guild_id)
    return eco.get(str(user_id), {"coins": 0, "last_daily": 0, "job_cooldown": 0})

def _set_user_balance(guild_id: int, user_id: int, data: dict) -> None:
    eco = _get_economy(guild_id)
    eco[str(user_id)] = data
    _set_economy(guild_id, eco)

def _get_shop(guild_id: int) -> list:
    data = _load_data(SHOP_FILE)
    return data.get(str(guild_id), [])

def _set_shop(guild_id: int, shop_items: list) -> None:
    all_data = _load_data(SHOP_FILE)
    all_data[str(guild_id)] = shop_items
    _save_data(SHOP_FILE, all_data)


# --- 2. JUEGOS ---
# Lista de preguntas para trivia (puede ser más extensa)
TRIVIA_QUESTIONS = [
    {"question": "¿Cuál es el planeta más grande del sistema solar?", "answers": ["Júpiter"], "category": "Ciencia"},
    {"question": "¿Quién escribió 'Cien años de soledad'?", "answers": ["Gabriel García Márquez"], "category": "Literatura"},
    {"question": "¿En qué año llegó el hombre a la Luna?", "answers": ["1969"], "category": "Historia"},
    {"question": "¿Qué país tiene la mayor población del mundo?", "answers": ["India", "China"], "category": "Geografía"}, # Respuesta múltiple
    {"question": "¿Cuál es el lenguaje de programación más utilizado?", "answers": ["Python", "JavaScript"], "category": "Tecnología"},
    {"question": "¿Qué símbolo químico tiene el agua?", "answers": ["H2O"], "category": "Ciencia"},
    {"question": "¿Cuál es el océano más grande?", "answers": ["Pacífico"], "category": "Geografía"},
    {"question": "¿En qué continente está Egipto?", "answers": ["África"], "category": "Geografía"},
]

# Estado de juegos en curso por servidor
game_sessions: dict[int, dict] = {}

class HangmanGame:
    def __init__(self, word: str):
        self.word = word.upper()
        self.hidden_word = ["_" for _ in self.word]
        self.attempts_left = 6
        self.guessed_letters = set()
        self.finished = False

    def guess(self, letter: str) -> tuple[bool, str]:
        """Intenta adivinar una letra. Devuelve (acertó, mensaje)."""
        if self.finished:
            return False, "El juego ya terminó."
        if len(letter) != 1 or not letter.isalpha():
            return False, "Solo puedes adivinar una letra a la vez."
        letter = letter.upper()
        if letter in self.guessed_letters:
            return False, f"Ya intentaste la letra '{letter}'."

        self.guessed_letters.add(letter)
        if letter in self.word:
            for i, l in enumerate(self.word):
                if l == letter:
                    self.hidden_word[i] = letter
            if "_" not in self.hidden_word:
                self.finished = True
                return True, "¡Felicidades! Has adivinado la palabra."
            return True, f"¡Correcto! La letra '{letter}' está en la palabra."
        else:
            self.attempts_left -= 1
            if self.attempts_left == 0:
                self.finished = True
                return False, f"¡Oh no! Te has quedado sin intentos. La palabra era `{self.word}`."
            return False, f"Incorrecto. La letra '{letter}' no está en la palabra. Te quedan {self.attempts_left} intentos."

    def get_display(self) -> str:
        return " ".join(self.hidden_word)


# --- 3. SISTEMA DE TICKETS Y MODERACIÓN EXTRA (Ya lo tienes, pero lo incluimos aquí para mantener todo junto) ---

# Las funciones de tickets, jail, blacklist, anti-scam, etc. YA ESTÁN EN moderacion_extra.py
# Solo las mencionamos para que sepas que están cubiertas.

# --- 4. FUNCIONES DE VERIFICACIÓN PARA COMANDOS PRIVADOS ---

def is_nexus_pro_guild(interaction: discord.Interaction) -> bool:
    """True solo si el comando se está usando en el server oficial de Nexus Pro."""
    return interaction.guild_id == NEXUS_PRO_GUILD_ID

async def block_if_not_nexus_pro_guild(interaction: discord.Interaction) -> bool:
    """Si el comando NO se está usando en el server de Nexus Pro, responde con un error."""
    if not is_nexus_pro_guild(interaction):
        embed = discord.Embed(
            title=f"{EMOJIS['ban']} Comando exclusivo de Nexus Pro",
            description=(
                "Este comando es exclusivo del servidor oficial de **Nexus Pro** "
                "y no está disponible en este server."
            ),
            color=discord.Color.red(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return True
    return False

def has_admin_or_owner_perms(member: discord.Member) -> bool:
    """Verifica si un miembro es admin, dueño del servidor, owner del bot o admin designado."""
    if member.id == Config.OWNER_ID:
        return True
    if hasattr(Config, 'ADMIN_USER_ID') and member.id == Config.ADMIN_USER_ID:
        return True
    if member.guild.owner_id == member.id:
        return True
    return member.guild_permissions.administrator

# --- FUNCIÓN PRINCIPAL PARA REGISTRAR TODO ---

def setup_comunidad(bot: commands.Bot, build_embed, colors: dict, footer_text: str):
    """
    Registra todos los comandos de comunidad en el bot.
    """

    # --- COMANDOS DE ECONOMÍA ---

    @bot.tree.command(name="daily", description="💰 Reclama tus monedas diarias gratis.")
    @app_commands.checks.cooldown(1, 5.0) # Evitar spam
    async def daily(interaction: discord.Interaction):
        track_command(interaction.guild_id, "daily")  # Si tienes esta función en bot.py
        user_data = _get_user_balance(interaction.guild_id, interaction.user.id)
        now = int(time.time())

        if user_data["last_daily"] > now - 86400:  # 24 horas en segundos
            remaining = 86400 - (now - user_data["last_daily"])
            hours, remainder = divmod(remaining, 3600)
            minutes, seconds = divmod(remainder, 60)
            embed = build_embed(
                title=f"{EMOJIS['ban']} ¡Ya reclamaste tus monedas hoy!",
                description=f"Puedes reclamar de nuevo en **{hours}h {minutes}m {seconds}s**.",
                color=colors["WARN"],
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        # Cantidad aleatoria entre 50 y 150
        reward = random.randint(50, 150)
        user_data["coins"] = user_data.get("coins", 0) + reward
        user_data["last_daily"] = now
        _set_user_balance(interaction.guild_id, interaction.user.id, user_data)

        embed = build_embed(
            title=f"{EMOJIS['checkmark']} ¡Recompensa diaria reclamada!",
            description=f"Has recibido **{reward} monedas**. Ahora tienes **{user_data['coins']} monedas**.",
            color=colors["OK"],
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.tree.command(name="trabajar", description="💼 Trabaja para ganar monedas.")
    @app_commands.checks.cooldown(1, 5.0)
    async def trabajar(interaction: discord.Interaction):
        track_command(interaction.guild_id, "trabajar")
        user_data = _get_user_balance(interaction.guild_id, interaction.user.id)
        now = int(time.time())

        if user_data.get("job_cooldown", 0) > now:
            remaining = user_data["job_cooldown"] - now
            minutes, seconds = divmod(remaining, 60)
            embed = build_embed(
                title=f"{EMOJIS['terminal']} Aún no puedes trabajar",
                description=f"Tienes que esperar **{minutes}m {seconds}s** para trabajar de nuevo.",
                color=colors["WARN"],
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        # Simular trabajo con resultados variables
        job_results = [
            (f"Trabajaste como **programador** y {EMOJIS['develop']}", random.randint(30, 80)),
            (f"Fuiste **streamer** y {EMOJIS['discorddevelopers']}", random.randint(20, 60)),
            (f"Trabajaste en **soporte técnico**", random.randint(15, 45)),
            (f"Fuiste **moderador** en un servidor", random.randint(10, 40)),
            (f"Trabajaste como **diseñador gráfico**", random.randint(25, 70)),
            (f"Fuiste **constructor** en Minecraft", random.randint(20, 50)),
            (f"Trabajaste como **escritor** de contenido", random.randint(15, 55)),
            (f"Fuiste **agente de viajes** espaciales", random.randint(40, 100)),
        ]
        desc, reward = random.choice(job_results)

        user_data["coins"] = user_data.get("coins", 0) + reward
        user_data["job_cooldown"] = now + 60 * 10  # 10 minutos de cooldown
        _set_user_balance(interaction.guild_id, interaction.user.id, user_data)

        embed = build_embed(
            title=f"{EMOJIS['checkmark']} ¡Has trabajado!",
            description=f"{desc} y ganaste **{reward} monedas**.\nAhora tienes **{user_data['coins']} monedas**.",
            color=colors["OK"],
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.tree.command(name="apostar", description="🎲 Apuesta monedas en un juego simple (50% de probabilidad).")
    @app_commands.describe(cantidad="Cantidad de monedas a apostar")
    @app_commands.checks.cooldown(1, 5.0)
    async def apostar(interaction: discord.Interaction, cantidad: app_commands.Range[int, 1, 10000]):
        track_command(interaction.guild_id, "apostar")
        user_data = _get_user_balance(interaction.guild_id, interaction.user.id)
        balance = user_data.get("coins", 0)

        if cantidad > balance:
            embed = build_embed(
                title=f"{EMOJIS['ban']} No tienes suficientes monedas",
                description=f"Tienes **{balance} monedas**, pero estás apostando **{cantidad}**.",
                color=colors["WARN"],
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        # 50% de probabilidad de ganar
        win = random.choice([True, False])
        if win:
            reward = cantidad * 2
            user_data["coins"] = balance + reward
            _set_user_balance(interaction.guild_id, interaction.user.id, user_data)
            embed = build_embed(
                title=f"{EMOJIS['checkmark']} ¡Has ganado!",
                description=f"¡Felicidades! Has apostado **{cantidad}** y has ganado **{reward} monedas**.\nAhora tienes **{user_data['coins']} monedas**.",
                color=colors["OK"],
            )
        else:
            user_data["coins"] = balance - cantidad
            _set_user_balance(interaction.guild_id, interaction.user.id, user_data)
            embed = build_embed(
                title=f"{EMOJIS['ban']} Has perdido.",
                description=f"Lo siento, has apostado **{cantidad}** y lo has perdido.\nAhora tienes **{user_data['coins']} monedas**.",
                color=colors["WARN"],
            )
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="monedas", description="💰 Muestra tu balance o el de otro usuario.")
    @app_commands.describe(usuario="Usuario a consultar (opcional)")
    async def monedas(interaction: discord.Interaction, usuario: discord.Member | None = None):
        track_command(interaction.guild_id, "monedas")
        target = usuario or interaction.user
        user_data = _get_user_balance(interaction.guild_id, target.id)
        balance = user_data.get("coins", 0)

        embed = build_embed(
            title=f"{EMOJIS['develop']} Balance de {target.display_name}",
            description=f"Tiene **{balance} monedas**.",
            color=colors["MAIN"],
            thumbnail=target.display_avatar.url,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.tree.command(name="tienda", description="🛒 Muestra los artículos disponibles en la tienda del servidor.")
    async def tienda(interaction: discord.Interaction):
        track_command(interaction.guild_id, "tienda")
        shop_items = _get_shop(interaction.guild_id)
        if not shop_items:
            embed = build_embed(
                title="🛒 Tienda vacía",
                description="No hay artículos en la tienda. Un administrador puede agregarlos con `/shop-agregar`.",
                color=colors["WARN"],
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        desc = "\n".join([f"**{item['name']}** — {item['price']} monedas\n{item['description'] or ''}" for item in shop_items])
        embed = build_embed(
            title="🛒 Tienda del servidor",
            description=desc,
            color=colors["MAIN"],
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.tree.command(name="comprar", description="🛒 Compra un artículo de la tienda.")
    @app_commands.describe(item_index="Número del artículo en la tienda (ver /tienda)")
    async def comprar(interaction: discord.Interaction, item_index: app_commands.Range[int, 1, 100]):
        track_command(interaction.guild_id, "comprar")
        shop_items = _get_shop(interaction.guild_id)
        if not shop_items or item_index > len(shop_items):
            embed = build_embed(
                title=f"{EMOJIS['ban']} Artículo no encontrado",
                description=f"El artículo `{item_index}` no existe en la tienda.",
                color=colors["WARN"],
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        item = shop_items[item_index - 1]
        user_data = _get_user_balance(interaction.guild_id, interaction.user.id)
        balance = user_data.get("coins", 0)

        if balance < item["price"]:
            embed = build_embed(
                title=f"{EMOJIS['ban']} No tienes suficientes monedas",
                description=f"Necesitas **{item['price']} monedas** para comprar {item['name']}. Tienes **{balance}**.",
                color=colors["WARN"],
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        # Deduct coins and "give" item (for now, just a success message. In the future, you could add roles or custom actions)
        user_data["coins"] = balance - item["price"]
        _set_user_balance(interaction.guild_id, interaction.user.id, user_data)

        # Here you could implement role assignment, custom actions, etc.
        embed = build_embed(
            title=f"{EMOJIS['checkmark']} ¡Compra exitosa!",
            description=f"Has comprado **{item['name']}** por **{item['price']} monedas**.\nTe quedan **{user_data['coins']} monedas**.",
            color=colors["OK"],
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.tree.command(name="shop-agregar", description="[Admin] Agrega un artículo a la tienda.")
    @app_commands.describe(nombre="Nombre del artículo", precio="Precio en monedas", descripcion="Descripción del artículo")
    @app_commands.checks.has_permissions(administrator=True)
    async def shop_agregar(interaction: discord.Interaction, nombre: str, precio: app_commands.Range[int, 1], descripcion: str | None = None):
        track_command(interaction.guild_id, "shop-agregar")
        shop_items = _get_shop(interaction.guild_id)
        shop_items.append({
            "name": nombre,
            "price": precio,
            "description": descripcion or "Sin descripción"
        })
        _set_shop(interaction.guild_id, shop_items)

        embed = build_embed(
            title=f"{EMOJIS['checkmark']} Artículo agregado a la tienda",
            description=f"**{nombre}** por **{precio}** monedas.",
            color=colors["OK"],
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


    # --- COMANDOS DE JUEGOS ---

    @bot.tree.command(name="trivia", description="🧠 Juega a una trivia de preguntas aleatorias.")
    @app_commands.checks.cooldown(1, 10.0)
    async def trivia(interaction: discord.Interaction):
        track_command(interaction.guild_id, "trivia")
        question_data = random.choice(TRIVIA_QUESTIONS)
        correct_answers = question_data["answers"]
        # Convertir a conjunto para facilitar la comprobación
        correct_set = set(a.upper() for a in correct_answers)

        # Esperar la respuesta en el mismo canal
        await interaction.response.send_message(
            embed=build_embed(
                title=f"🧠 Trivia: {question_data.get('category', 'General')}",
                description=f"**{question_data['question']}**\n\nTienes 15 segundos para responder.",
                color=colors["MAIN"],
            )
        )

        def check(m: discord.Message):
            return m.author == interaction.user and m.channel == interaction.channel

        try:
            msg = await bot.wait_for("message", check=check, timeout=15.0)
            user_answer = msg.content.strip().upper()

            if user_answer in correct_set:
                await interaction.followup.send(
                    embed=build_embed(
                        title=f"{EMOJIS['checkmark']} ¡Correcto!",
                        description=f"La respuesta era **{correct_answers[0]}**.",
                        color=colors["OK"],
                    )
                )
            else:
                await interaction.followup.send(
                    embed=build_embed(
                        title=f"{EMOJIS['ban']} ¡Incorrecto!",
                        description=f"La respuesta correcta era **{correct_answers[0]}**.",
                        color=colors["WARN"],
                    )
                )
        except asyncio.TimeoutError:
            await interaction.followup.send(
                embed=build_embed(
                    title="⏰ Tiempo agotado",
                    description=f"La respuesta correcta era **{correct_answers[0]}**.",
                    color=colors["WARN"],
                )
            )


    @bot.tree.command(name="ahorcado", description="🔤 Juega al ahorcado. Adivina la palabra.")
    @app_commands.checks.cooldown(1, 10.0)
    async def ahorcado(interaction: discord.Interaction):
        track_command(interaction.guild_id, "ahorcado")
        # Palabras predefinidas
        words = ["PYTHON", "DISCORD", "BOT", "NEXUS", "ECONOMIA", "TRIVIA", "JUEGO", "COMUNIDAD", "SERVIDOR", "CANAL"]
        game = HangmanGame(random.choice(words))

        # Empezar el juego
        await interaction.response.send_message(
            embed=build_embed(
                title="🔤 Ahorcado",
                description=f"**Palabra:** {game.get_display()}\n**Intentos restantes:** {game.attempts_left}",
                color=colors["MAIN"],
            )
        )

        while not game.finished:
            def check(m: discord.Message):
                return m.author == interaction.user and m.channel == interaction.channel and len(m.content) == 1 and m.content.isalpha()

            try:
                msg = await bot.wait_for("message", check=check, timeout=60.0)
                letter = msg.content
                success, result = game.guess(letter)

                # Actualizar el embed
                embed = discord.Embed(
                    title="🔤 Ahorcado",
                    description=f"**Palabra:** {game.get_display()}\n**Intentos restantes:** {game.attempts_left}",
                    color=colors["MAIN"] if not game.finished else colors["OK"] if "_" not in game.hidden_word else colors["WARN"],
                )
                embed.add_field(name="Resultado", value=result, inline=False)
                await interaction.edit_original_response(embed=embed)

                # Eliminar el mensaje de la letra para mantener el canal limpio
                try:
                    await msg.delete()
                except:
                    pass

                if game.finished:
                    break

            except asyncio.TimeoutError:
                game.finished = True
                embed = discord.Embed(
                    title="⏰ Tiempo agotado",
                    description=f"El juego ha terminado. La palabra era `{game.word}`.",
                    color=colors["WARN"],
                )
                await interaction.edit_original_response(embed=embed)
                break


    @bot.tree.command(name="ppt", description="✊ Piedra, Papel o Tijera contra el bot.")
    @app_commands.describe(opcion="Tu elección")
    @app_commands.choices(opcion=[
        app_commands.Choice(name="✊ Piedra", value="piedra"),
        app_commands.Choice(name="📄 Papel", value="papel"),
        app_commands.Choice(name="✂️ Tijera", value="tijera"),
    ])
    @app_commands.checks.cooldown(1, 5.0)
    async def ppt(interaction: discord.Interaction, opcion: app_commands.Choice[str]):
        track_command(interaction.guild_id, "ppt")
        elecciones = ["piedra", "papel", "tijera"]
        bot_choice = random.choice(elecciones)

        if opcion.value == bot_choice:
            resultado = f"🤝 ¡Empate! Ambos eligieron **{opcion.name}**."
            color = colors["WARN"]
        elif (opcion.value == "piedra" and bot_choice == "tijera") or \
             (opcion.value == "papel" and bot_choice == "piedra") or \
             (opcion.value == "tijera" and bot_choice == "papel"):
            resultado = f"✅ ¡Ganaste! **{opcion.name}** vence a **{bot_choice.capitalize()}**."
            color = colors["OK"]
        else:
            resultado = f"❌ Perdiste. **{bot_choice.capitalize()}** vence a **{opcion.name}**."
            color = colors["WARN"]

        embed = build_embed(
            title="✊ 📄 ✂️ Piedra, Papel o Tijera",
            description=resultado,
            color=color,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


    # --- COMANDOS DE MODERACIÓN Y UTILIDAD (Ya existentes en moderacion_extra.py) ---

    # Nota: Los comandos como /blacklist-palabra, /jail, /ticket-config, etc., ya están en moderacion_extra.py.
    # Solo asegurémonos de que se estén importando y configurando correctamente en bot.py.
    # Por ahora, no los duplicamos aquí.

    # --- UTILIDAD: EMBEDS MEJORADOS CON EMOJIS ---

    # Ya tenemos la función `build_embed` inyectada, y los emojis están en EMOJIS.
    # Solo hay que asegurarse de usarlos en los títulos, descripciones, etc.

    print("✅ Módulo de comunidad cargado correctamente.")