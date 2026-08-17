import os
import json
import time
import shutil
import threading
from datetime import datetime, timezone, timedelta
from collections import Counter

import discord
from discord import app_commands
from discord.ext import commands
from flask import Flask
from dotenv import load_dotenv
from config import Config
import asyncio
import functools
import random
import re
from datetime import datetime, timedelta

import yt_dlp
from PIL import Image, ImageDraw, ImageFont
import string
import io

load_dotenv()

# ──────────────────────────────────────────────────────────────
#  CONEXIÓN A SUPABASE
# ──────────────────────────────────────────────────────────────

import os
from supabase import create_client, Client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase: Client | None = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        print("✅ Conectado a Supabase")
    except Exception as e:
        print(f"⚠️ Error conectando a Supabase: {e}")
        supabase = None
else:
    print("⚠️ Supabase no configurado. Usando archivos locales.")

def supabase_get(guild_id: int, table: str) -> dict | None:
    """Obtiene datos de Supabase"""
    if not supabase:
        return None
    try:
        result = supabase.table(table).select("*").eq("guild_id", str(guild_id)).execute()
        if result.data and len(result.data) > 0:
            return result.data[0].get("data", {})
        return None
    except Exception as e:
        print(f"❌ Error Supabase GET ({table}): {e}")
        return None

def supabase_set(guild_id: int, table: str, data: dict) -> bool:
    """Guarda datos en Supabase"""
    if not supabase:
        return False
    try:
        supabase.table(table).upsert({
            "guild_id": str(guild_id),
            "data": data
        }).execute()
        return True
    except Exception as e:
        print(f"❌ Error Supabase SET ({table}): {e}")
        return False

# ──────────────────────────────────────────────────────────────
#  CONFIGURACIÓN BÁSICA
# ──────────────────────────────────────────────────────────────

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")

CONFIG_PATH = "config.json"
STATS_PATH = "stats.json"
LOGS_PATH = "logs.json"
MUTED_PATH = "muted.json"
BANNED_IPS_PATH = "banned_ips.json"
USER_IPS_PATH = "user_ips.json"

config_lock = threading.Lock()
stats_lock = threading.Lock()
logs_lock = threading.Lock()

MAX_LOGS_PER_GUILD = 300

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)
BOT_START_TIME = time.time()

# ──────────────────────────────────────────────────────────────
#  ESTADO EN MEMORIA (no persiste al reiniciar, es intencional)
# ──────────────────────────────────────────────────────────────

afk_users: dict[int, dict] = {}            # user_id -> {"reason": str, "since": datetime}
spam_tracker: dict[int, list] = {}          # user_id -> [timestamps recientes]
pending_captchas: dict[int, dict] = {}      # user_id -> {"code", "guild_id", "level", "attempts", "expires"}
active_reminders: dict[int, int] = {}       # solo para trackear cantidad, opcional

# ──────────────────────────────────────────────────────────────
#  PERSISTENCIA
# ──────────────────────────────────────────────────────────────

DEFAULT_GUILD_CONFIG = {
    "current_link": Config.DOWNLOAD_LINK,
    "trap_channel_id": None,
    "log_channels": {
        "enlace": None,
        "editados": None,
        "borrados": None,
        "aceptaciones": None,
        "bienvenidas": None,
        "moderacion": None,
    },
    "roles_enlace": [],
    "welcome_enabled": False,
    "welcome_channel": None,
    "welcome_message": "¡Bienvenido/a {mention} a **{server}**! Ahora somos **{membercount}** miembros. 🎉",
    "welcome_banner": None,
    "farewell_enabled": False,
    "farewell_channel": None,
    "farewell_message": "**{username}** ha abandonado el servidor. Ahora somos **{membercount}** miembros. 👋",
    "farewell_banner": None,
    "autorole_id": None,
    "autorole_panels": [],
    "antilink_enabled": False,
    "antispam_enabled": False,
    "suggestions_channel": None,
    "verify_enabled": False,
    "verify_role_id": None,
    "verify_unverified_role_id": None,
    "verify_level": 1,
    "verify_channel_id": None,
}


def _deep_merge_defaults(data: dict, defaults: dict) -> dict:
    for key, value in defaults.items():
        if key not in data:
            data[key] = json.loads(json.dumps(value))
        elif isinstance(value, dict) and isinstance(data[key], dict):
            _deep_merge_defaults(data[key], value)
    return data


def load_config() -> dict:
    with config_lock:
        if not os.path.exists(CONFIG_PATH):
            return {}
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}


def save_config(data: dict) -> None:
    with config_lock:
        tmp_path = CONFIG_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, CONFIG_PATH)


def get_guild_config(guild_id: int) -> dict:
    """Carga configuración desde Supabase o archivo local"""
    # Intentar desde Supabase
    data = supabase_get(guild_id, "config")
    if data:
        return data
    
    # Fallback a archivo local
    data = load_config()
    key = str(guild_id)
    if key not in data:
        default = json.loads(json.dumps(DEFAULT_GUILD_CONFIG))
        if Config.INSIDER_ROLE_ID:
            default["roles_enlace"].append(Config.INSIDER_ROLE_ID)
        if Config.NEXUS_PLUS_ROLE_ID:
            default["roles_enlace"].append(Config.NEXUS_PLUS_ROLE_ID)
        if Config.VIP_ROLE_ID:
            default["roles_enlace"].append(Config.VIP_ROLE_ID)
        data[key] = default
        save_config(data)
        return data[key]
    data[key] = _deep_merge_defaults(data[key], DEFAULT_GUILD_CONFIG)
    return data[key]


def update_guild_config(guild_id: int, guild_cfg: dict) -> None:
    """Guarda configuración en Supabase y local"""
    # Guardar en Supabase
    if supabase:
        supabase_set(guild_id, "config", guild_cfg)
    
    # Guardar en archivo local (fallback)
    data = load_config()
    data[str(guild_id)] = guild_cfg
    save_config(data)


# ──────────────────────────────────────────────────────────────
#  STATS
# ──────────────────────────────────────────────────────────────

DEFAULT_GUILD_STATS = {
    "commands_used": {},
    "messages_edited": 0,
    "messages_deleted": 0,
    "joins": 0,
    "leaves": 0,
    "terms_accepted": 0,
    "terms_rejected": 0,
    "errors_logged": 0,
    "mutes": 0,
    "kicks": 0,
    "bans": 0,
}


def load_stats() -> dict:
    with stats_lock:
        if not os.path.exists(STATS_PATH):
            return {}
        with open(STATS_PATH, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}


def save_stats(data: dict) -> None:
    """Guarda estadísticas en Supabase y local"""
    # Guardar en Supabase (cada guild por separado)
    if supabase:
        for guild_id, stats_data in data.items():
            supabase_set(int(guild_id), "stats", stats_data)
    
    # Guardar en archivo local
    with stats_lock:
        tmp_path = STATS_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, STATS_PATH)


def get_guild_stats(guild_id: int) -> dict:
    """Carga estadísticas desde Supabase o archivo local"""
    # Intentar desde Supabase
    data = supabase_get(guild_id, "stats")
    if data:
        return data
    
    # Fallback a archivo local
    data = load_stats()
    key = str(guild_id)
    if key not in data:
        data[key] = json.loads(json.dumps(DEFAULT_GUILD_STATS))
        save_stats(data)
        return data[key]
    data[key] = _deep_merge_defaults(data[key], DEFAULT_GUILD_STATS)
    return data[key]

def bump_stat(guild_id: int, field: str, amount: int = 1) -> None:
    data = load_stats()
    key = str(guild_id)
    if key not in data:
        data[key] = json.loads(json.dumps(DEFAULT_GUILD_STATS))
    data[key] = _deep_merge_defaults(data[key], DEFAULT_GUILD_STATS)
    data[key][field] = data[key].get(field, 0) + amount
    save_stats(data)


def track_command(guild_id: int | None, command_name: str) -> None:
    if guild_id is None:
        return
    data = load_stats()
    key = str(guild_id)
    if key not in data:
        data[key] = json.loads(json.dumps(DEFAULT_GUILD_STATS))
    data[key] = _deep_merge_defaults(data[key], DEFAULT_GUILD_STATS)
    used = data[key].setdefault("commands_used", {})
    used[command_name] = used.get(command_name, 0) + 1
    save_stats(data)


# ──────────────────────────────────────────────────────────────
#  LOGS
# ──────────────────────────────────────────────────────────────

def load_logs() -> dict:
    with logs_lock:
        if not os.path.exists(LOGS_PATH):
            return {}
        with open(LOGS_PATH, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}


def save_logs(data: dict) -> None:
    """Guarda logs en Supabase y local"""
    # Guardar en Supabase
    if supabase:
        for guild_id, logs_data in data.items():
            supabase_set(int(guild_id), "logs", logs_data)
    
    # Guardar en archivo local
    with logs_lock:
        tmp_path = LOGS_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, LOGS_PATH)


def record_log(guild_id: int, tipo: str, descripcion: str, autor: str | None = None) -> None:
    data = load_logs()
    key = str(guild_id)
    entries = data.get(key, [])
    entries.append({
        "tipo": tipo,
        "descripcion": descripcion,
        "autor": autor,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    entries = entries[-MAX_LOGS_PER_GUILD:]
    data[key] = entries
    save_logs(data)


def get_logs(guild_id: int, tipo: str | None = None) -> list[dict]:
    """Carga logs desde Supabase o archivo local"""
    # Intentar desde Supabase
    data = supabase_get(guild_id, "logs")
    if data and isinstance(data, list):
        entries = data
    else:
        # Fallback a archivo local
        data = load_logs()
        entries = data.get(str(guild_id), [])
    
    if tipo and tipo != "todos":
        entries = [e for e in entries if e.get("tipo") == tipo]
    return list(reversed(entries))

# ──────────────────────────────────────────────────────────────
#  DATOS DE MODERACIÓN
# ──────────────────────────────────────────────────────────────

def load_muted() -> dict:
    """Carga muteados desde Supabase o archivo local"""
    # Supabase no tiene una tabla específica para muted
    # Usamos una sola fila con guild_id = "all"
    if supabase:
        try:
            result = supabase.table("muted").select("*").eq("guild_id", "all").execute()
            if result.data and len(result.data) > 0:
                return result.data[0].get("data", {})
        except Exception as e:
            print(f"❌ Error cargando muteados de Supabase: {e}")
    
    # Fallback a archivo local
    with config_lock:
        if not os.path.exists(MUTED_PATH):
            return {}
        with open(MUTED_PATH, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}

def save_muted(data: dict) -> None:
    """Guarda muteados en Supabase y local"""
    # Guardar en Supabase como JSON
    if supabase:
        try:
            supabase.table("muted").upsert({
                "guild_id": "all",
                "data": data
            }).execute()
        except Exception as e:
            print(f"❌ Error guardando muteados en Supabase: {e}")
    
    # Guardar en archivo local
    with config_lock:
        tmp_path = MUTED_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, MUTED_PATH)


def load_banned_ips() -> dict:
    """Carga IPs baneadas desde Supabase o archivo local"""
    if supabase:
        try:
            result = supabase.table("banned_ips").select("*").eq("guild_id", "all").execute()
            if result.data and len(result.data) > 0:
                return result.data[0].get("data", {})
        except Exception as e:
            print(f"❌ Error cargando banned_ips de Supabase: {e}")
    
    with config_lock:
        if not os.path.exists(BANNED_IPS_PATH):
            return {}
        with open(BANNED_IPS_PATH, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}

def save_banned_ips(data: dict) -> None:
    """Guarda IPs baneadas en Supabase y local"""
    if supabase:
        try:
            supabase.table("banned_ips").upsert({
                "guild_id": "all",
                "data": data
            }).execute()
        except Exception as e:
            print(f"❌ Error guardando banned_ips en Supabase: {e}")
    
    with config_lock:
        tmp_path = BANNED_IPS_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, BANNED_IPS_PATH)


def load_user_ips() -> dict:
    """Carga IPs de usuarios desde Supabase o archivo local"""
    if supabase:
        try:
            result = supabase.table("user_ips").select("*").eq("guild_id", "all").execute()
            if result.data and len(result.data) > 0:
                return result.data[0].get("data", {})
        except Exception as e:
            print(f"❌ Error cargando user_ips de Supabase: {e}")
    
    with config_lock:
        if not os.path.exists(USER_IPS_PATH):
            return {}
        with open(USER_IPS_PATH, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}

def save_user_ips(data: dict) -> None:
    """Guarda IPs de usuarios en Supabase y local"""
    if supabase:
        try:
            supabase.table("user_ips").upsert({
                "guild_id": "all",
                "data": data
            }).execute()
        except Exception as e:
            print(f"❌ Error guardando user_ips en Supabase: {e}")
    
    with config_lock:
        tmp_path = USER_IPS_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, USER_IPS_PATH)


# ──────────────────────────────────────────────────────────────
#  FUNCIONES DE MODERACIÓN
# ──────────────────────────────────────────────────────────────

def get_user_ip(user_id: int) -> str | None:
    data = load_user_ips()
    return data.get(str(user_id))


def set_user_ip(user_id: int, ip: str) -> None:
    data = load_user_ips()
    data[str(user_id)] = ip
    save_user_ips(data)


def is_ip_banned(ip: str) -> bool:
    data = load_banned_ips()
    return ip in data


def ban_ip(ip: str, reason: str = "No especificada", moderator: str = "Sistema") -> None:
    data = load_banned_ips()
    data[ip] = {
        "reason": reason,
        "moderator": moderator,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    save_banned_ips(data)


def unban_ip(ip: str) -> bool:
    data = load_banned_ips()
    if ip in data:
        del data[ip]
        save_banned_ips(data)
        return True
    return False


def is_user_muted(guild_id: int, user_id: int) -> bool:
    data = load_muted()
    key = f"{guild_id}_{user_id}"
    if key not in data:
        return False
    mute_data = data[key]
    if mute_data.get("permanent", False):
        return True
    expiry = mute_data.get("expiry")
    if expiry and datetime.now(timezone.utc) > datetime.fromisoformat(expiry):
        del data[key]
        save_muted(data)
        return False
    return True


def get_mute_reason(guild_id: int, user_id: int) -> str | None:
    data = load_muted()
    key = f"{guild_id}_{user_id}"
    return data.get(key, {}).get("reason")


def mute_user(guild_id: int, user_id: int, duration: str | None, reason: str, moderator: str) -> None:
    data = load_muted()
    key = f"{guild_id}_{user_id}"
    
    mute_data = {
        "reason": reason,
        "moderator": moderator,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    
    if duration:
        unit = duration[-1]
        value = int(duration[:-1])
        
        if unit == "m":
            delta = timedelta(minutes=value)
        elif unit == "h":
            delta = timedelta(hours=value)
        elif unit == "d":
            delta = timedelta(days=value)
        elif unit == "w":
            delta = timedelta(weeks=value)
        elif unit == "M":
            delta = timedelta(days=value * 30)
        else:
            delta = timedelta(minutes=value)
        
        mute_data["expiry"] = (datetime.now(timezone.utc) + delta).isoformat()
        mute_data["permanent"] = False
    else:
        mute_data["permanent"] = True
    
    data[key] = mute_data
    save_muted(data)
    bump_stat(guild_id, "mutes")


def unmute_user(guild_id: int, user_id: int) -> bool:
    data = load_muted()
    key = f"{guild_id}_{user_id}"
    if key in data:
        del data[key]
        save_muted(data)
        return True
    return False


# ──────────────────────────────────────────────────────────────
#  SISTEMA DE WARNS (advertencias persistentes)
# ──────────────────────────────────────────────────────────────

WARNS_PATH = "warns.json"


def load_warns() -> dict:
    """Carga advertencias desde Supabase o archivo local"""
    if supabase:
        try:
            result = supabase.table("warns").select("*").eq("guild_id", "all").execute()
            if result.data and len(result.data) > 0:
                return result.data[0].get("data", {})
        except Exception as e:
            print(f"❌ Error cargando warns de Supabase: {e}")

    with config_lock:
        if not os.path.exists(WARNS_PATH):
            return {}
        with open(WARNS_PATH, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}


def save_warns(data: dict) -> None:
    """Guarda advertencias en Supabase y local"""
    if supabase:
        try:
            supabase.table("warns").upsert({
                "guild_id": "all",
                "data": data
            }).execute()
        except Exception as e:
            print(f"❌ Error guardando warns en Supabase: {e}")

    with config_lock:
        tmp_path = WARNS_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, WARNS_PATH)


def add_warn(guild_id: int, user_id: int, reason: str, moderator: str) -> int:
    """Añade una advertencia y devuelve el total de advertencias del usuario."""
    data = load_warns()
    key = f"{guild_id}_{user_id}"
    entries = data.get(key, [])
    entries.append({
        "reason": reason,
        "moderator": moderator,
        "timestamp": datetime.now(timezone.utc).isoformat()
    })
    data[key] = entries
    save_warns(data)
    return len(entries)


def get_warns(guild_id: int, user_id: int) -> list[dict]:
    data = load_warns()
    key = f"{guild_id}_{user_id}"
    return data.get(key, [])


def clear_warns(guild_id: int, user_id: int) -> int:
    """Elimina todas las advertencias de un usuario, devuelve cuántas se borraron."""
    data = load_warns()
    key = f"{guild_id}_{user_id}"
    count = len(data.get(key, []))
    if key in data:
        del data[key]
        save_warns(data)
    return count


def remove_warn(guild_id: int, user_id: int, index: int) -> dict | None:
    """Elimina una advertencia puntual (1-indexed, como se muestra en /warnings). Devuelve la entrada borrada o None."""
    data = load_warns()
    key = f"{guild_id}_{user_id}"
    entries = data.get(key, [])
    if index < 1 or index > len(entries):
        return None
    removed = entries.pop(index - 1)
    data[key] = entries
    save_warns(data)
    return removed


def parse_duration_to_seconds(duration: str) -> int | None:
    """Convierte '10m', '2h', '1d', '1w' en segundos. Devuelve None si el formato es inválido."""
    duration = duration.strip()
    if not duration or len(duration) < 2:
        return None
    unit = duration[-1]
    try:
        value = int(duration[:-1])
    except ValueError:
        return None
    if value <= 0:
        return None
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    if unit not in multipliers:
        return None
    return value * multipliers[unit]


def format_seconds(seconds: int) -> str:
    """Formatea segundos en un texto legible tipo '1h 20m'."""
    parts = []
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds and not days:
        parts.append(f"{seconds}s")
    return " ".join(parts) if parts else "0s"


# ──────────────────────────────────────────────────────────────
#  CAPTCHA (sistema de verificación)
# ──────────────────────────────────────────────────────────────

def generate_captcha_code(level: int) -> str:
    """Genera el código del captcha según el nivel de dificultad (1=fácil, 3=difícil)."""
    if level >= 3:
        length = 7
        chars = string.ascii_uppercase + string.digits  # incluye caracteres parecidos, más difícil
    elif level == 2:
        length = 6
        chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sin O/0, I/1 para que sea legible
    else:
        length = 4
        chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(random.choices(chars, k=length))


def generate_captcha_image(code: str, level: int) -> io.BytesIO:
    """Genera una imagen PNG tipo captcha con ruido/distorsión según el nivel."""
    scale = 4
    char_w, char_h = 26, 38
    small_w = char_w * len(code) + 24
    small_h = char_h + 24

    img = Image.new("RGB", (small_w, small_h), color=(240, 242, 250))
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()

    # Líneas de ruido de fondo (más líneas = más difícil)
    noise_lines = {1: 3, 2: 7, 3: 12}.get(level, 6)
    for _ in range(noise_lines):
        x1, y1 = random.randint(0, small_w), random.randint(0, small_h)
        x2, y2 = random.randint(0, small_w), random.randint(0, small_h)
        draw.line([(x1, y1), (x2, y2)], fill=tuple(random.randint(160, 205) for _ in range(3)), width=1)

    # Dibujar cada carácter con rotación/posición aleatoria (nivel 2-3)
    x = 12
    for ch in code:
        y = random.randint(4, 16) if level >= 2 else 10
        color = tuple(random.randint(15, 90) for _ in range(3))
        char_img = Image.new("RGBA", (char_w, char_h), (0, 0, 0, 0))
        cdraw = ImageDraw.Draw(char_img)
        cdraw.text((4, 4), ch, font=font, fill=color + (255,))
        if level >= 2:
            angle = random.randint(-15 * level, 15 * level)
            char_img = char_img.rotate(angle, expand=True, resample=Image.BICUBIC)
        img.paste(char_img, (x, y), char_img)
        x += char_w + (4 if level >= 3 else 0)

    # Puntos de ruido (más ruido en niveles altos)
    noise_dots = {1: 25, 2: 90, 3: 180}.get(level, 90)
    for _ in range(noise_dots):
        px, py = random.randint(0, small_w - 1), random.randint(0, small_h - 1)
        draw.point((px, py), fill=tuple(random.randint(150, 210) for _ in range(3)))

    final_w = min(small_w * scale, 500)
    final_h = int(small_h * (final_w / small_w))
    img = img.resize((final_w, final_h), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def generate_math_question() -> tuple[str, str]:
    """Genera una pregunta matemática simple para el captcha nivel 1. Devuelve (pregunta, respuesta)."""
    a, b = random.randint(1, 20), random.randint(1, 20)
    op = random.choice(["+", "-", "×"])
    if op == "+":
        result = a + b
    elif op == "-":
        a, b = max(a, b), min(a, b)  # evitar negativos
        result = a - b
    else:
        a, b = random.randint(1, 9), random.randint(1, 9)
        result = a * b
    return f"¿Cuánto es {a} {op} {b}?", str(result)


async def apply_verification(member: discord.Member, cfg: dict) -> bool:
    """Aplica el rol verificado y remueve el de no verificado. Devuelve True si funcionó."""
    try:
        verified_role = member.guild.get_role(int(cfg["verify_role_id"])) if cfg.get("verify_role_id") else None
        unverified_role = member.guild.get_role(int(cfg["verify_unverified_role_id"])) if cfg.get("verify_unverified_role_id") else None
        if verified_role:
            await member.add_roles(verified_role, reason="Verificación completada")
        if unverified_role and unverified_role in member.roles:
            await member.remove_roles(unverified_role, reason="Verificación completada")
        return True
    except discord.Forbidden:
        return False


# ──────────────────────────────────────────────────────────────
#  COLORES / HELPERS
# ──────────────────────────────────────────────────────────────

COLOR_MAIN = discord.Color.from_rgb(88, 101, 242)
COLOR_OK = discord.Color.from_rgb(87, 242, 135)
COLOR_WARN = discord.Color.from_rgb(237, 66, 69)
COLOR_AMBER = discord.Color.from_rgb(255, 176, 71)
COLOR_PURPLE = discord.Color.from_rgb(168, 85, 247)

BOT_FOOTER_TEXT = "Nexus System"


def build_embed(
    *,
    title: str | None = None,
    description: str | None = None,
    color: discord.Color = COLOR_MAIN,
    fields: list[tuple[str, str, bool]] | None = None,
    footer: str | None = None,
    footer_icon: str | None = None,
    thumbnail: str | None = None,
    image: str | None = None,
    author_name: str | None = None,
    author_icon: str | None = None,
    timestamp: bool = True,
) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.now(timezone.utc) if timestamp else None,
    )
    if author_name:
        embed.set_author(name=author_name, icon_url=author_icon)
    if fields:
        for name, value, inline in fields:
            embed.add_field(name=name, value=value, inline=inline)
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    if image:
        embed.set_image(url=image)
    embed.set_footer(text=footer or BOT_FOOTER_TEXT, icon_url=footer_icon)
    return embed


def format_uptime(seconds: float) -> str:
    seconds = int(seconds)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    if minutes or hours or days:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)


async def get_log_channel(guild: discord.Guild, tipo: str) -> discord.TextChannel | None:
    cfg = get_guild_config(guild.id)
    channel_id = cfg["log_channels"].get(tipo)
    if not channel_id:
        return None
    channel = guild.get_channel(int(channel_id))
    return channel


def check_user_roles(member: discord.Member) -> dict:
    result = {
        "is_owner": False,
        "is_admin_user": False,  # ✅ NUEVO: Admin específico
        "has_insider": False,
        "has_vip": False,
        "has_permission": False,
        "roles_list": [],
        "matched_roles": []
    }
    
    # ✅ Verificar si es el OWNER del bot
    if member.id == Config.OWNER_ID:
        result["is_owner"] = True
        result["has_permission"] = True
        return result
    
    # ✅ Verificar si es ADMIN_USER_ID (nuevo)
    if hasattr(Config, 'ADMIN_USER_ID') and member.id == Config.ADMIN_USER_ID:
        result["is_admin_user"] = True
        result["has_permission"] = True
        return result
    
    # Verificar si es owner del servidor
    if member.guild.owner_id == member.id:
        result["is_owner"] = True
        result["has_permission"] = True
        return result
    
    # Verificar roles
    for role in member.roles:
        result["roles_list"].append(role.name)
        
        if role.id == Config.INSIDER_ROLE_ID:
            result["has_insider"] = True
            result["matched_roles"].append(role.name)
        if role.id == Config.VIP_ROLE_ID:
            result["has_vip"] = True
            result["matched_roles"].append(role.name)
        
        if role.name == Config.INSIDER_ROLE_NAME:
            result["has_insider"] = True
            result["matched_roles"].append(role.name)
        if role.name == Config.VIP_ROLE_NAME:
            result["has_vip"] = True
            result["matched_roles"].append(role.name)
    
    cfg = get_guild_config(member.guild.id)
    allowed_ids = set(cfg.get("roles_enlace", []))
    if Config.INSIDER_ROLE_ID:
        allowed_ids.add(Config.INSIDER_ROLE_ID)
    if Config.NEXUS_PLUS_ROLE_ID:
        allowed_ids.add(Config.NEXUS_PLUS_ROLE_ID)
    member_role_ids = {r.id for r in member.roles}
    
    if allowed_ids & member_role_ids:
        result["has_permission"] = True
    
    if result["has_insider"] or result["has_vip"]:
        result["has_permission"] = True
    
    return result


def has_enlace_role(member: discord.Member, guild_id: int) -> bool:
    result = check_user_roles(member)
    return result["has_permission"]


def fill_placeholders(template: str, member: discord.Member, guild: discord.Guild) -> str:
    return (
        template
        .replace("{mention}", member.mention)
        .replace("{user}", member.mention)
        .replace("{username}", member.display_name)
        .replace("{userid}", str(member.id))
        .replace("{server}", guild.name)
        .replace("{membercount}", str(guild.member_count))
        .replace("{created}", f"<t:{int(member.created_at.timestamp())}:R>")
        .replace("{joindate}", f"<t:{int(member.joined_at.timestamp())}:D>" if member.joined_at else "Desconocido")
        .replace("{avatar}", member.display_avatar.url)
    )


# ──────────────────────────────────────────────────────────────
#  VISTAS
# ──────────────────────────────────────────────────────────────

class AppealView(discord.ui.View):
    """Vista para apelar sanciones (mute, kick, ban)."""
    
    def __init__(self, guild_id: int, user_id: int, action_type: str):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.user_id = user_id
        self.action_type = action_type
        self.owner_id = Config.OWNER_ID
        
        # El botón de apelación principal
        # Nota: Este botón se usa desde DMs, por eso no tiene guild en interaction
        self.appeal_button = discord.ui.Button(
            label="📩 Hablar con el Owner", 
            style=discord.ButtonStyle.primary, 
            emoji="📩"
        )
        self.appeal_button.callback = self.appeal_button_callback
        self.add_item(self.appeal_button)
    
    async def appeal_button_callback(self, interaction: discord.Interaction):
        """Callback cuando el usuario presiona 'Hablar con el Owner'."""
        # Verificar que sea el usuario afectado
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "❌ Este botón solo puede ser usado por el usuario afectado.",
                ephemeral=True
            )
            return
        
        # Obtener el servidor
        guild = bot.get_guild(self.guild_id)
        if not guild:
            await interaction.response.send_message(
                "❌ No se pudo encontrar el servidor. Contacta al soporte directamente.",
                ephemeral=True
            )
            return
        
        # Obtener el owner
        owner = await self._get_owner(guild)
        if not owner:
            await interaction.response.send_message(
                "❌ No se pudo contactar al owner. Intenta más tarde.",
                ephemeral=True
            )
            return
        
        # Crear embed de apelación
        embed = self._create_appeal_embed(interaction.user, guild)
        
        # Crear vista con botón de respuesta para el owner
        view = self._create_owner_response_view(interaction.user.id, owner.id)
        
        try:
            # Enviar al owner
            await owner.send(embed=embed, view=view)
            await interaction.response.send_message(
                "✅ Se ha enviado tu apelación al owner. Recibirás respuesta pronto.",
                ephemeral=True
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ No se pudo contactar al owner. Tiene los DMs desactivados.",
                ephemeral=True
            )
    
    async def _get_owner(self, guild: discord.Guild) -> discord.User | discord.Member | None:
        """Obtiene el owner del bot o del servidor."""
        # Intentar obtener al owner del bot
        owner = guild.get_member(self.owner_id)
        if not owner:
            try:
                owner = await bot.fetch_user(self.owner_id)
            except:
                # Si falla, usar el owner del servidor
                owner = guild.owner
        
        return owner
    
    def _create_appeal_embed(self, user: discord.User, guild: discord.Guild) -> discord.Embed:
        """Crea el embed de apelación."""
        embed = discord.Embed(
            title="📩 Apelación de Sanción",
            description=f"El usuario **{user}** está apelando su sanción.",
            color=COLOR_AMBER,
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(
            name="👤 Usuario", 
            value=f"{user} (ID: {user.id})", 
            inline=False
        )
        embed.add_field(
            name="📋 Tipo de sanción", 
            value=self.action_type.capitalize(), 
            inline=True
        )
        embed.add_field(
            name="🔗 Servidor", 
            value=guild.name, 
            inline=True
        )
        embed.add_field(
            name="📅 Fecha", 
            value=f"<t:{int(datetime.now().timestamp())}:F>", 
            inline=False
        )
        embed.set_footer(text="Responde a este mensaje para contactar al usuario")
        return embed
    
    def _create_owner_response_view(self, user_id: int, owner_id: int) -> discord.ui.View:
        """Crea la vista con el botón de respuesta para el owner."""
        view = discord.ui.View()
        
        # Crear botón de respuesta
        respond_button = discord.ui.Button(
            label="💬 Responder al usuario",
            style=discord.ButtonStyle.success,
            custom_id=f"responder_{user_id}"
        )
        
        # Asignar callback directamente
        async def respond_callback(interaction_owner: discord.Interaction):
            # Verificar que sea el owner
            if interaction_owner.user.id != owner_id:
                await interaction_owner.response.send_message(
                    "Este botón es solo para el owner.", 
                    ephemeral=True
                )
                return
            
            # Crear modal de respuesta
            modal = discord.ui.Modal(title="Responder al usuario")
            modal.add_item(
                discord.ui.TextInput(
                    label="Mensaje de respuesta",
                    style=discord.TextStyle.paragraph,
                    placeholder="Escribe tu respuesta aquí...",
                    required=True,
                    max_length=2000
                )
            )
            
            # Definir submit del modal
            async def on_submit(interaction_modal: discord.Interaction):
                respuesta = interaction_modal.children[0].value
                
                try:
                    # Obtener el usuario que apeló
                    user = await bot.fetch_user(user_id)
                    
                    # Crear embed de respuesta
                    embed_respuesta = discord.Embed(
                        title="📨 Respuesta del Staff",
                        description=respuesta,
                        color=COLOR_OK,
                        timestamp=datetime.now(timezone.utc)
                    )
                    embed_respuesta.set_footer(
                        text=f"Respondido por {interaction_modal.user}"
                    )
                    
                    # Enviar respuesta al usuario
                    await user.send(embed=embed_respuesta)
                    
                    # Deshabilitar el botón
                    for child in interaction_modal.message.view.children:
                        child.disabled = True
                    await interaction_modal.message.edit(view=interaction_modal.message.view)
                    
                    await interaction_modal.response.send_message(
                        "✅ Respuesta enviada al usuario.", 
                        ephemeral=True
                    )
                    
                except discord.Forbidden:
                    await interaction_modal.response.send_message(
                        "❌ No se pudo enviar la respuesta. El usuario podría tener DMs desactivados.",
                        ephemeral=True
                    )
                except Exception as e:
                    await interaction_modal.response.send_message(
                        f"❌ Error al enviar: {str(e)}",
                        ephemeral=True
                    )
            
            modal.on_submit = on_submit
            await interaction_owner.response.send_modal(modal)
        
        # Asignar el callback al botón
        respond_button.callback = respond_callback
        view.add_item(respond_button)
        
        return view
    
    async def on_timeout(self) -> None:
        """Cuando la vista expira, deshabilitar todos los botones."""
        for child in self.children:
            child.disabled = True

class TerminosView(discord.ui.View):
    def __init__(self, autor_id: int):
        super().__init__(timeout=180)
        self.autor_id = autor_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.autor_id:
            await interaction.response.send_message(
                "Este mensaje de términos no es para ti. Usa `/legales` para ver el tuyo.",
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True

    @discord.ui.button(label="Sí, acepto", style=discord.ButtonStyle.success, emoji="✅")
    async def aceptar(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)

        await interaction.channel.send(
            f"✅ {interaction.user.mention} **aceptaste términos y condiciones.** "
            f"Cualquier infracción será tomada legalmente."
        )

        bump_stat(interaction.guild_id, "terms_accepted")
        record_log(interaction.guild_id, "aceptaciones", f"{interaction.user} aceptó los términos.", str(interaction.user))

        cfg = get_guild_config(interaction.guild_id)
        link = cfg.get("current_link") or Config.DOWNLOAD_LINK

        log_channel = await get_log_channel(interaction.guild, "aceptaciones")
        if log_channel is not None:
            log_embed = build_embed(
                title="✅ Términos aceptados",
                description=f"{interaction.user.mention} aceptó los **Términos y Condiciones**.",
                color=COLOR_OK,
                thumbnail=interaction.user.display_avatar.url,
                footer=f"ID del usuario: {interaction.user.id}",
            )
            await log_channel.send(embed=log_embed)

        embed = build_embed(
            title="📥 Instalador de Nexus Pro (Beta)",
            description=(
                "Este es el instalador oficial de **Nexus Pro**.\n\n"
                "⚠️ Ten en cuenta que **este enlace cambia con cada nueva versión**, "
                "así que no lo compartas ni lo guardes como definitivo.\n\n"
                "🔒 Solo los usuarios con rol **Insiders** o **VIP** pueden volver a "
                "consultar el enlace vigente en cualquier momento usando `/enlace`."
            ),
            color=COLOR_OK,
            fields=[("🔗 Enlace de descarga", link, False)],
            footer="Nexus Pro · Beta",
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="No, no acepto", style=discord.ButtonStyle.danger, emoji="❌")
    async def rechazar(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(
            "❌ Has rechazado los términos y condiciones. No podrás acceder a Nexus Pro.",
            ephemeral=True,
        )

        bump_stat(interaction.guild_id, "terms_rejected")
        record_log(interaction.guild_id, "aceptaciones", f"{interaction.user} rechazó los términos.", str(interaction.user))

        log_channel = await get_log_channel(interaction.guild, "aceptaciones")
        if log_channel is not None:
            log_embed = build_embed(
                title="❌ Términos rechazados",
                description=f"{interaction.user.mention} rechazó los **Términos y Condiciones**.",
                color=COLOR_WARN,
                thumbnail=interaction.user.display_avatar.url,
                footer=f"ID del usuario: {interaction.user.id}",
            )
            await log_channel.send(embed=log_embed)


class Paginator(discord.ui.View):
    def __init__(self, embeds: list[discord.Embed], autor_id: int, timeout: float = 120.0):
        super().__init__(timeout=timeout)
        self.embeds = embeds
        self.autor_id = autor_id
        self.index = 0
        self._update_buttons()

    def _update_buttons(self) -> None:
        self.first_page.disabled = self.index == 0
        self.prev_page.disabled = self.index == 0
        self.next_page.disabled = self.index >= len(self.embeds) - 1
        self.last_page.disabled = self.index >= len(self.embeds) - 1
        self.page_indicator.label = f"{self.index + 1}/{len(self.embeds)}"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.autor_id:
            await interaction.response.send_message("Solo quien ejecutó el comando puede pasar de página.", ephemeral=True)
            return False
        return True

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True

    @discord.ui.button(label="⏮", style=discord.ButtonStyle.secondary)
    async def first_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = 0
        self._update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.index], view=self)

    @discord.ui.button(label="◀", style=discord.ButtonStyle.primary)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = max(0, self.index - 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.index], view=self)

    @discord.ui.button(label="1/1", style=discord.ButtonStyle.gray, disabled=True)
    async def page_indicator(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass

    @discord.ui.button(label="▶", style=discord.ButtonStyle.primary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = min(len(self.embeds) - 1, self.index + 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.index], view=self)

    @discord.ui.button(label="⏭", style=discord.ButtonStyle.secondary)
    async def last_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = len(self.embeds) - 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.index], view=self)


def build_log_pages(entries: list[dict], per_page: int = 8) -> list[discord.Embed]:
    if not entries:
        return [build_embed(
            title="🗂️ Historial de logs",
            description="No hay eventos registrados todavía.",
            color=COLOR_PURPLE,
        )]

    ICONS = {
        "editados": "✏️",
        "borrados": "🗑️",
        "aceptaciones": "📜",
        "bienvenidas": "👋",
        "enlace": "🔗",
        "moderacion": "🛡️",
        "error": "⚠️",
    }

    pages = []
    chunks = [entries[i:i + per_page] for i in range(0, len(entries), per_page)]
    for chunk in chunks:
        lines = []
        for e in chunk:
            ts = e.get("timestamp", "")
            try:
                dt = datetime.fromisoformat(ts)
                ts_fmt = f"<t:{int(dt.timestamp())}:R>"
            except Exception:
                ts_fmt = "hace un momento"
            icon = ICONS.get(e.get("tipo"), "•")
            autor = f" — *{e['autor']}*" if e.get("autor") else ""
            lines.append(f"{icon} {ts_fmt}{autor}\n> {e.get('descripcion', '')}")
        pages.append(build_embed(
            title="🗂️ Historial de logs",
            description="\n\n".join(lines),
            color=COLOR_PURPLE,
        ))
    return pages


# ──────────────────────────────────────────────────────────────
#  SISTEMA DE AYUDA
# ──────────────────────────────────────────────────────────────

HELP_CATEGORIES = {
    "general": {
        "label": "🌐 General",
        "description": "Comandos disponibles para todos los usuarios.",
        "commands": [
            ("/ping", "Muestra la latencia del bot."),
            ("/legales-free", "Muestra los Términos de Nexus Core (versión gratuita)."),
            ("/legales", "Muestra los Términos y Condiciones de Nexus Pro."),
            ("/enlace", "Muestra el enlace vigente (requiere rol Insiders/VIP)."),
            ("/server-info", "Muestra información general del servidor."),
            ("/buscar", "Busca miembros por nombre."),
            ("/avatar", "Muestra el avatar de un usuario (con botones para descargarlo)."),
            ("/user-info", "Muestra información de un usuario."),
            ("/roleinfo", "Muestra información detallada de un rol."),
            ("/botinfo", "Muestra el estado técnico del bot."),
            ("/ayuda-error", "Te ayuda a diagnosticar un mensaje de error."),
        ],
    },
    "admin": {
        "label": "🛡️ Administración",
        "description": "Comandos que requieren permisos de Administrador.",
        "commands": [
            ("/actualizar-enlace", "Actualiza el enlace del instalador y avisa a Insiders/VIP."),
            ("/config-logs", "Configura los canales de log del servidor."),
            ("/config-rol-enlace", "Agrega o quita roles autorizados para /enlace."),
            ("/config-welcome", "Configura el sistema de bienvenidas."),
            ("/config-despedida", "Configura el sistema de despedidas."),
            ("/test-welcome", "Previsualiza el mensaje de bienvenida."),
            ("/test-despedida", "Previsualiza el mensaje de despedida."),
            ("/say", "Hace que el bot envíe un mensaje."),
            ("/ver-config", "Muestra la configuración actual del bot."),
            ("/panel-autoroles", "Publica un panel con botones para que la gente se ponga roles solita."),
            ("/panel-autoroles-quitar", "Elimina un panel de autoroles guardado."),
            ("/purge-usuario", "Elimina los últimos mensajes de un usuario en el canal."),
        ],
    },
    "stats": {
        "label": "📊 Estadísticas",
        "description": "Analíticas y registro histórico de eventos.",
        "commands": [
            ("/stats", "Muestra estadísticas y analíticas del servidor."),
            ("/logs", "Muestra el historial de eventos paginado."),
        ],
    },
    "moderacion": {
        "label": "🛡️ Moderación",
        "description": "Comandos de moderación avanzada.",
        "commands": [
            ("/mute", "Mutea a un usuario con duración personalizada."),
            ("/unmute", "Desmutea a un usuario."),
            ("/kick", "Expulsa a un usuario con DM y botón de apelación."),
            ("/ban", "Banea a un usuario con duración y opción de banear IP."),
            ("/unban", "Desbanea a un usuario."),
            ("/ban-ip", "Banea una IP (simulado)."),
            ("/unban-ip", "Desbanea una IP."),
            ("/multicuenta", "Detecta y maneja posibles multicuentas."),
            ("/muted-list", "Muestra la lista de usuarios muteados."),
        ],
    },
    "musica": {
        "label": "🎵 Música",
        "description": "Reproduce música en canales de voz.",
        "commands": [
            ("/play", "Reproduce una canción por nombre o URL (YouTube/Spotify/SoundCloud)."),
            ("/skip", "Salta a la siguiente canción de la cola."),
            ("/pause", "Pausa la reproducción actual."),
            ("/resume", "Reanuda la reproducción pausada."),
            ("/stop", "Detiene la música, vacía la cola y desconecta al bot."),
            ("/queue", "Muestra la cola de reproducción."),
            ("/nowplaying", "Muestra la canción que está sonando."),
            ("/volume", "Ajusta el volumen de reproducción (0-100)."),
            ("/loop", "Activa/desactiva el modo de repetición."),
            ("/shuffle", "Mezcla aleatoriamente la cola actual."),
            ("/remove", "Elimina una canción específica de la cola."),
            ("/disconnect", "Desconecta al bot del canal de voz."),
        ],
    },
}


class HelpSelect(discord.ui.Select):
    def __init__(self, autor_id: int):
        self.autor_id = autor_id
        options = [
            discord.SelectOption(label=data["label"], description=data["description"], value=key)
            for key, data in HELP_CATEGORIES.items()
        ]
        super().__init__(placeholder="Elige una categoría de comandos…", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.autor_id:
            await interaction.response.send_message("Este menú de ayuda no es para ti. Usa `/ayuda`.", ephemeral=True)
            return
        key = self.values[0]
        data = HELP_CATEGORIES[key]
        fields = [(name, desc, False) for name, desc in data["commands"]]
        embed = build_embed(
            title=f"📖 Ayuda · {data['label']}",
            description=data["description"],
            color=COLOR_MAIN,
            fields=fields,
            footer="Nexus System · Usa el menú para ver otra categoría",
        )
        await interaction.response.edit_message(embed=embed, view=self.view)


class HelpView(discord.ui.View):
    def __init__(self, autor_id: int):
        super().__init__(timeout=120)
        self.add_item(HelpSelect(autor_id))

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True


COMMON_ERROR_HINTS = [
    (["missing permissions", "faltan permisos", "403", "forbidden"],
     "🔒 Permisos insuficientes",
     "El bot o el usuario no tienen los permisos necesarios."),
    (["missing access", "unknown channel", "canal desconocido", "404"],
     "📭 Canal o recurso no encontrado",
     "El canal, rol o mensaje al que se hace referencia ya no existe."),
    (["intents", "privileged"],
     "🧩 Intents privilegiados no activados",
     "Activa `Server Members Intent` y `Message Content Intent` en el Developer Portal."),
    (["invalid token", "401", "improper token", "token"],
     "🔑 Token inválido",
     "El token del bot es incorrecto. Regenera el token en el Developer Portal."),
    (["rate limit", "429", "cloudflare"],
     "⏳ Límite de peticiones (rate limit)",
     "El bot está enviando demasiadas peticiones."),
]


# ──────────────────────────────────────────────────────────────
#  SISTEMA DE MÚSICA
# ──────────────────────────────────────────────────────────────

# Silencia los mensajes de "reporta este bug" de yt-dlp en la consola.
yt_dlp.utils.bug_reports_message = lambda *args, **kwargs: ""

URL_REGEX = re.compile(r"^https?://", re.IGNORECASE)

COOKIES_FILE = os.getenv("YTDLP_COOKIES_FILE", "cookies.txt")
if not os.path.isfile(COOKIES_FILE):
    COOKIES_FILE = None

YTDL_FORMAT_OPTIONS = {
    "format": "bestaudio[ext=webm]/bestaudio/best",
    "noplaylist": True,
    "nocheckcertificate": True,
    "ignoreerrors": False,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "extract_flat": False,
    "geo_bypass": True, 
    "cookiefile": COOKIES_FILE,
    # ✅ FIX: YouTube endureció la verificación "confirmá que no sos un bot" en 2025.
    # El cliente "tv" suele evitarla sin necesitar cookies; si igual falla, hace falta
    # subir un cookies.txt (ver YTDLP_COOKIES_FILE más abajo).
    "extractor_args": {
        "youtube": {
            "player_client": ["tv", "android", "ios", "web"],
        }
    },
}

if COOKIES_FILE:
    print(f"✅ Usando cookies de YouTube desde: {COOKIES_FILE}")
else:
    print("⚠️  No se encontró cookies.txt para YouTube. Si /play falla con 'Sign in to confirm you're not a bot', subí un cookies.txt (ver YTDLP_COOKIES_FILE) — es la única solución 100% confiable ante ese error.")

# reconnect_* ayuda a que la transmisión sobreviva a cortes de red breves.
FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

_ytdl = yt_dlp.YoutubeDL(YTDL_FORMAT_OPTIONS)

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None
if not FFMPEG_AVAILABLE:
    print("⚠️  FFmpeg no está instalado o no está en el PATH. La música NO va a poder reproducirse hasta que instales FFmpeg en el servidor/host.")

try:
    import davey  # noqa: F401
    DAVEY_AVAILABLE = True
except ImportError:
    DAVEY_AVAILABLE = False
    print("⚠️  Falta la librería 'davey' (requerida por Discord para el nuevo cifrado de voz DAVE). Instalá 'davey' en requirements.txt o la música va a fallar con 'davey library needed in order to use voice'.")


# En la función ytdl_extract, puedes añadir limpieza de URL
async def ytdl_extract(query: str) -> tuple[dict | None, str | None]:
    """Busca o resuelve una canción (nombre o URL) usando yt-dlp sin bloquear el event loop.

    Devuelve (datos, error). Si hay un error, datos es None y error trae el motivo
    en texto plano para poder mostrárselo al usuario en Discord.
    """
    loop = asyncio.get_event_loop()
    
    # Limpiar la URL de parámetros de lista
    if URL_REGEX.match(query):
        # Remover parámetros de lista
        import urllib.parse
        parsed = urllib.parse.urlparse(query)
        query_params = urllib.parse.parse_qs(parsed.query)
        # Eliminar el parámetro 'list' si existe
        if 'list' in query_params:
            del query_params['list']
        # Reconstruir la URL sin el parámetro list
        new_query = urllib.parse.urlunparse(
            (parsed.scheme, parsed.netloc, parsed.path, parsed.params, 
             urllib.parse.urlencode(query_params, doseq=True), parsed.fragment)
        )
        if new_query:
            query = new_query
    
    search_term = query if URL_REGEX.match(query) else f"ytsearch1:{query}"

    partial = functools.partial(_ytdl.extract_info, search_term, download=False)
    try:
        data = await loop.run_in_executor(None, partial)
    except Exception as e:
        print(f"Error de yt-dlp al buscar '{query}': {e}")
        return None, str(e)

    if data is None:
        return None, "yt-dlp no devolvió resultados."

    # Si es una lista de reproducción, tomar el primer elemento
    if "entries" in data:
        entries = [e for e in data["entries"] if e]
        if not entries:
            return None, "No se encontraron resultados válidos."
        data = entries[0]

    return data, None


def format_duration(seconds) -> str:
    if not seconds:
        return "🔴 En vivo / Desconocida"
    seconds = int(seconds)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02}:{seconds:02}"
    return f"{minutes}:{seconds:02}"


def create_progress_bar(current: float, total: float, length: int = 15) -> str:
    if not total:
        return "🔴 `EN VIVO`"
    filled = int((current / total) * length)
    filled = max(0, min(length, filled))
    return "▬" * filled + "🔘" + "▬" * (length - filled)


def format_ytdlp_error(error: str | None) -> str:
    """Traduce errores comunes de yt-dlp a un mensaje entendible en español."""
    if not error:
        return "Sin detalles"
    if "Sign in to confirm" in error or "not a bot" in error:
        return (
            "YouTube le está pidiendo al bot que confirme que 'no es un bot'. "
            "Esto pasa cada vez más seguido y la única solución confiable es subir un archivo "
            "`cookies.txt` (exportado de una cuenta de YouTube logueada) al servidor y configurar "
            "la variable de entorno `YTDLP_COOKIES_FILE` con su ruta. Sin eso, YouTube seguirá bloqueando la descarga."
        )
    return error[:500]


class Song:
    """Representa una canción resuelta por yt-dlp, lista para encolar."""

    def __init__(self, data: dict, requester: discord.abc.User):
        self.title: str = data.get("title", "Desconocido")
        self.webpage_url: str = data.get("webpage_url") or data.get("url")
        self.duration = data.get("duration")
        self.thumbnail = data.get("thumbnail")
        self.uploader: str = data.get("uploader", "Desconocido")
        self.requester = requester


class GuildMusicState:
    """Estado del reproductor de música para un servidor específico."""

    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        self.queue: list[Song] = []
        self.current: Song | None = None
        self.voice_client: discord.VoiceClient | None = None
        self.text_channel: discord.abc.Messageable | None = None
        self.loop_mode: str = "off"  # off | song | queue
        self.volume: float = 0.5
        self.play_started_at: float | None = None
        self.force_skip_loop: bool = False
        self.lock = asyncio.Lock()
        self.is_paused: bool = False
        self.is_playing: bool = False

    def is_playing(self) -> bool:
        return bool(self.voice_client and (self.voice_client.is_playing() or self.voice_client.is_paused()))


music_states: dict[int, GuildMusicState] = {}


def get_music_state(guild_id: int) -> GuildMusicState:
    if guild_id not in music_states:
        music_states[guild_id] = GuildMusicState(guild_id)
    return music_states[guild_id]


async def resolve_and_queue(query: str, guild_id: int, requester: discord.abc.User) -> tuple[Song | None, str | None]:
    data, error = await ytdl_extract(query)
    if data is None:
        return None, error
    song = Song(data, requester)
    get_music_state(guild_id).queue.append(song)
    return song, None


async def start_playback(guild_id: int) -> None:
    state = get_music_state(guild_id)
    async with state.lock:
        if state.is_playing():
            return
        await _play_next_locked(guild_id)


async def _advance(guild_id: int) -> None:
    state = get_music_state(guild_id)
    async with state.lock:
        await _play_next_locked(guild_id)


async def _play_next_locked(guild_id: int) -> None:
    """Reproduce la siguiente canción. Debe llamarse con el lock del estado ya adquirido."""
    state = get_music_state(guild_id)

    if state.loop_mode == "song" and state.current and not state.force_skip_loop:
        next_song = state.current
    elif state.queue:
        next_song = state.queue.pop(0)
        if state.loop_mode == "queue" and state.current:
            state.queue.append(state.current)
    else:
        state.current = None
        state.play_started_at = None
        state.force_skip_loop = False
        state.is_playing = False  # ✅ ACTUALIZAR
        state.is_paused = False   # ✅ ACTUALIZAR
        return

    state.force_skip_loop = False
    state.current = next_song

    if not state.voice_client or not state.voice_client.is_connected():
        return

    data, error = await ytdl_extract(next_song.webpage_url)
    if data is None:
        if state.text_channel:
            try:
                await state.text_channel.send(embed=build_embed(
                    title="⚠️ No se pudo reproducir una canción",
                    description=(
                        f"Se saltó **{next_song.title}** por un error al obtener el audio.\n"
                        f"```{format_ytdlp_error(error)}```"
                    ),
                    color=COLOR_WARN,
                ))
            except Exception:
                pass
        await _play_next_locked(guild_id)
        return

    stream_url = data.get("url")
    try:
        source = discord.FFmpegPCMAudio(stream_url, **FFMPEG_OPTIONS)
        source = discord.PCMVolumeTransformer(source, volume=state.volume)
    except Exception as e:
        print(f"Error creando la fuente de audio: {e}")
        await _play_next_locked(guild_id)
        return

    def _after_playing(error: Exception | None) -> None:
        if error:
            print(f"Error de reproducción: {error}")
        fut = asyncio.run_coroutine_threadsafe(_advance(guild_id), bot.loop)
        try:
            fut.result()
        except Exception as e:
            print(f"Error avanzando la cola de música: {e}")

    state.voice_client.play(source, after=_after_playing)
    state.play_started_at = time.time()
    state.is_playing = True  # ✅ ACTUALIZAR
    state.is_paused = False  # ✅ ACTUALIZAR

    if state.text_channel:
        embed = build_embed(
            title="🎶 Reproduciendo ahora",
            description=f"[{next_song.title}]({next_song.webpage_url})",
            color=COLOR_OK,
            thumbnail=next_song.thumbnail,
            fields=[
                ("⏱️ Duración", format_duration(next_song.duration), True),
                ("🙋 Pedido por", getattr(next_song.requester, "mention", str(next_song.requester)), True),
                ("📋 En cola", str(len(state.queue)), True),
            ],
            footer="Nexus Music",
        )
        try:
            await state.text_channel.send(embed=embed)
        except Exception:
            pass


async def ensure_voice(interaction: discord.Interaction) -> discord.VoiceClient | None:
    """Verifica que el usuario esté en un canal de voz y conecta/mueve al bot a ese canal."""
    if not isinstance(interaction.user, discord.Member) or interaction.user.voice is None:
        embed = build_embed(
            title="🔇 No estás en un canal de voz",
            description="Debes unirte a un canal de voz para usar comandos de música.",
            color=COLOR_WARN,
        )
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
        return None

    channel = interaction.user.voice.channel
    state = get_music_state(interaction.guild_id)

    if interaction.guild.voice_client is None:
        try:
            state.voice_client = await channel.connect()
        except Exception as e:
            embed = build_embed(
                title="❌ No pude conectarme al canal de voz",
                description=f"Error: {e}",
                color=COLOR_WARN,
            )
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
            return None
    elif interaction.guild.voice_client.channel != channel:
        await interaction.guild.voice_client.move_to(channel)
        state.voice_client = interaction.guild.voice_client
    else:
        state.voice_client = interaction.guild.voice_client

    state.text_channel = interaction.channel
    return state.voice_client


def build_queue_pages(state: GuildMusicState, per_page: int = 10) -> list[discord.Embed]:
    if not state.current and not state.queue:
        return [build_embed(
            title="📋 Cola de reproducción",
            description="No hay nada en la cola. Usa `/play` para añadir una canción.",
            color=COLOR_AMBER,
        )]

    header = ""
    if state.current:
        header = (
            f"🎶 **Sonando ahora:** [{state.current.title}]({state.current.webpage_url}) "
            f"({format_duration(state.current.duration)})\n\n"
        )

    if not state.queue:
        return [build_embed(
            title="📋 Cola de reproducción",
            description=header + "No hay más canciones en la cola.",
            color=COLOR_MAIN,
        )]

    chunks = [state.queue[i:i + per_page] for i in range(0, len(state.queue), per_page)]
    pages = []
    for chunk_index, chunk in enumerate(chunks):
        lines = []
        for i, song in enumerate(chunk):
            pos = chunk_index * per_page + i + 1
            requester = getattr(song.requester, "mention", str(song.requester))
            lines.append(f"`{pos}.` [{song.title}]({song.webpage_url}) · {format_duration(song.duration)} · {requester}")
        pages.append(build_embed(
            title="📋 Cola de reproducción",
            description=header + "\n".join(lines),
            color=COLOR_MAIN,
            footer=f"{len(state.queue)} canción(es) en cola",
        ))
    return pages


# ──────────────────────────────────────────────────────────────
#  EVENTOS
# ──────────────────────────────────────────────────────────────

# ──────────────────────────────────────────────────────────────
#  EVENTO ON_READY CORREGIDO (REEMPLAZA EL ACTUAL)
# ──────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    """Evento cuando el bot está listo."""
    try:
        if GUILD_ID:
            guild_obj = discord.Object(id=int(GUILD_ID))
            bot.tree.copy_global_to(guild=guild_obj)
            synced = await bot.tree.sync(guild=guild_obj)
            print(f"✅ Sincronizados {len(synced)} comandos slash en el servidor {GUILD_ID} (instantáneo).")
        else:
            synced = await bot.tree.sync()
            print(f"✅ Sincronizados {len(synced)} comandos slash globalmente (puede tardar hasta 1h).")
    except Exception as e:
        print(f"❌ Error sincronizando comandos: {e}")
    
    # 🔥 FUERZA LA SINCRONIZACIÓN COMPLETA
    try:
        await bot.tree.sync()
        print("✅ Comandos sincronizados forzadamente")
    except Exception as e:
        print(f"❌ Error en sincronización forzada: {e}")

    # Registrar vistas persistentes (botones que sobreviven a un reinicio)
    try:
        bot.add_view(VerifyPanelView())
        print("✅ Vista persistente de verificación registrada")
    except Exception as e:
        print(f"❌ Error registrando vistas persistentes: {e}")

    try:
        total_paneles = register_autorole_panels()
        print(f"✅ {total_paneles} panel(es) de autoroles registrados")
    except Exception as e:
        print(f"❌ Error registrando paneles de autoroles: {e}")

    # Restaurar canales bloqueados
    data = load_blocked_channels()
    for guild_id_str, channels_data in data.items():
        guild_id = int(guild_id_str)
        guild = bot.get_guild(guild_id)
        if not guild:
            continue
        
        for channel_id_str in channels_data.keys():
            channel_id = int(channel_id_str)
            channel = guild.get_channel(channel_id)
            if channel and isinstance(channel, discord.TextChannel):
                if is_channel_blocked(guild_id, channel_id):
                    everyone = guild.default_role
                    try:
                        await channel.set_permissions(everyone, send_messages=False)
                        print(f"🔒 Canal {channel.name} restaurado al estado bloqueado.")
                    except Exception as e:
                        print(f"⚠️ No se pudo restaurar el bloqueo en {channel.name}: {e}")

    
    # Cambiar presencia del bot
    try:
        await bot.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=f"Nexus Pro · /ayuda | {len(bot.guilds)} servidores"
            )
        )
    except Exception as e:
        print(f"❌ Error cambiando presencia: {e}")
    
    # ✅ INICIAR DESCONEXIÓN POR INACTIVIDAD (MÚSICA)
    try:
        bot.loop.create_task(disconnect_if_empty())
        print("✅ Sistema de desconexión por inactividad iniciado")
    except Exception as e:
        print(f"❌ Error iniciando desconexión por inactividad: {e}")
    
    print(f"🚀 Conectado como {bot.user} (ID: {bot.user.id})")
    print(f"👑 Owner ID configurado: {Config.OWNER_ID}")
    print(f"🔑 Insider Role ID: {Config.INSIDER_ROLE_ID}")
    print(f"🔑 VIP Role ID: {Config.VIP_ROLE_ID}")
    print(f"📊 Total de comandos: {len(bot.tree.get_commands())}")
    print("✅ Bot listo y funcionando correctamente")


# ──────────────────────────────────────────────────────────────
#  DESCONEXIÓN POR INACTIVIDAD (MÚSICA)
# ──────────────────────────────────────────────────────────────

async def disconnect_if_empty():
    """Desconecta el bot si está solo en el canal de voz por más de 5 minutos."""
    while True:
        await asyncio.sleep(60)  # Revisar cada minuto
        for guild_id, state in list(music_states.items()):
            if state.voice_client and state.voice_client.is_connected():
                # Si no está reproduciendo y no hay cola
                if not state.is_playing() and len(state.queue) == 0:
                    # Esperar 5 minutos antes de desconectar
                    await asyncio.sleep(300)
                    # Volver a verificar después de la espera
                    if not state.is_playing() and len(state.queue) == 0:
                        try:
                            await state.voice_client.disconnect()
                            state.voice_client = None
                            print(f"🔇 Desconectado por inactividad en guild {guild_id}")
                            if guild_id in music_states:
                                del music_states[guild_id]
                        except Exception as e:
                            print(f"❌ Error desconectando por inactividad: {e}")

# ──────────────────────────────────────────────────────────────
#  COMANDOS DE MÚSICA COMPLETOS
# ──────────────────────────────────────────────────────────────

@bot.tree.command(name="play", description="🎵 Reproduce una canción en el canal de voz.")
@app_commands.describe(query="Nombre de la canción o URL (YouTube, Spotify, SoundCloud)")
@app_commands.checks.cooldown(1, 3.0)
async def play(interaction: discord.Interaction, query: str):
    """Reproduce una canción en el canal de voz."""
    track_command(interaction.guild_id, "play")
    
    # ✅ RESPONDER INMEDIATAMENTE para evitar timeout
    await interaction.response.defer(ephemeral=True, thinking=True)

    if not FFMPEG_AVAILABLE:
        embed = build_embed(
            title="❌ FFmpeg no está instalado",
            description="El servidor donde corre el bot no tiene `ffmpeg` instalado. Sin FFmpeg la música no puede reproducirse. Avisale al owner del hosting para que lo instale.",
            color=COLOR_WARN,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    if not DAVEY_AVAILABLE:
        embed = build_embed(
            title="❌ Falta la librería 'davey'",
            description="Discord ahora exige el protocolo de cifrado de voz DAVE, que requiere la librería `davey`. Agregá `davey>=0.1.4` a `requirements.txt`, reinstalá dependencias y reiniciá el bot.",
            color=COLOR_WARN,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    
    # Verificar que el usuario está en un canal de voz
    if not interaction.user.voice:
        embed = build_embed(
            title="🔇 No estás en un canal de voz",
            description="Únete a un canal de voz primero.",
            color=COLOR_WARN,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    
    # Conectar al canal de voz (AHORA con defer ya hecho, no hay timeout)
    voice_client = await ensure_voice(interaction)
    if not voice_client:
        return
    
    state = get_music_state(interaction.guild_id)
    state.text_channel = interaction.channel
    
    # Buscar la canción (esto puede tardar)
    song, error = await resolve_and_queue(query, interaction.guild_id, interaction.user)
    if not song:
        embed = build_embed(
            title="❌ Canción no encontrada",
            description=(
                f"No se encontró: `{query}`\n\n"
                f"💡 **Sugerencias:**\n• Usa el nombre de la canción directamente\n• Asegúrate de que la URL sea válida\n• El video podría estar restringido por región/edad\n\n"
                f"```{format_ytdlp_error(error)}```"
            ),
            color=COLOR_WARN,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    
    # Registrar en logs
    record_log(
        interaction.guild_id,
        "musica",
        f"{interaction.user} añadió a la cola: {song.title}",
        str(interaction.user)
    )
    
    # Responder con el embed
    embed = build_embed(
        title="✅ Canción añadida a la cola",
        color=COLOR_OK,
        thumbnail=song.thumbnail,
        fields=[
            ("📝 Canción", f"[{song.title}]({song.webpage_url})", False),
            ("👤 Subido por", song.uploader, True),
            ("⏱️ Duración", format_duration(song.duration), True),
            ("📊 Posición en cola", f"#{len(state.queue)}", True),
        ],
        footer=f"Pedido por {interaction.user.display_name}",
    )
    await interaction.followup.send(embed=embed)
    
    # Si no está reproduciendo, empezar
    if not state.is_playing():
        await start_playback(interaction.guild_id)


@bot.tree.command(name="skip", description="⏭️ Salta la canción actual.")
@app_commands.checks.cooldown(1, 3.0)
async def skip(interaction: discord.Interaction):
    """Salta la canción actual."""
    track_command(interaction.guild_id, "skip")
    
    # ✅ Defer inmediato
    await interaction.response.defer(ephemeral=True, thinking=True)
    
    state = get_music_state(interaction.guild_id)
    
    if not state.is_playing() or not state.current:
        embed = build_embed(
            title="❌ No hay canción reproduciéndose",
            color=COLOR_WARN,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    
    song_title = state.current.title
    state.force_skip_loop = True
    if state.voice_client:
        state.voice_client.stop()
    
    record_log(
        interaction.guild_id,
        "musica",
        f"{interaction.user} saltó la canción: {song_title}",
        str(interaction.user)
    )
    
    embed = build_embed(
        title="⏭️ Canción saltada",
        description=f"Saltando: **{song_title}**",
        color=COLOR_AMBER,
    )
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="pause", description="⏸️ Pausa la canción actual.")
@app_commands.checks.cooldown(1, 3.0)
async def pause(interaction: discord.Interaction):
    """Pausa la canción actual."""
    track_command(interaction.guild_id, "pause")
    
    # ✅ DEFER INMEDIATO
    await interaction.response.defer(ephemeral=True, thinking=True)
    
    state = get_music_state(interaction.guild_id)
    
    if not state.is_playing() or not state.voice_client:
        embed = build_embed(
            title="❌ No hay canción reproduciéndose",
            color=COLOR_WARN,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    
    if state.is_paused:
        embed = build_embed(
            title="⏸️ Ya está pausado",
            color=COLOR_AMBER,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    
    state.voice_client.pause()
    state.is_paused = True
    
    embed = build_embed(
        title="⏸️ Canción pausada",
        description=f"Pausado: **{state.current.title}**",
        color=COLOR_AMBER,
    )
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="resume", description="▶️ Reanuda la canción pausada.")
@app_commands.checks.cooldown(1, 3.0)
async def resume(interaction: discord.Interaction):
    """Reanuda la canción pausada."""
    track_command(interaction.guild_id, "resume")
    
    # ✅ DEFER INMEDIATO
    await interaction.response.defer(ephemeral=True, thinking=True)
    
    state = get_music_state(interaction.guild_id)
    
    if not state.is_playing() or not state.voice_client:
        embed = build_embed(
            title="❌ No hay canción reproduciéndose",
            color=COLOR_WARN,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    
    if not state.is_paused:
        embed = build_embed(
            title="▶️ La canción ya se está reproduciendo",
            color=COLOR_AMBER,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    
    state.voice_client.resume()
    state.is_paused = False
    
    embed = build_embed(
        title="▶️ Canción reanudada",
        description=f"Reanudado: **{state.current.title}**",
        color=COLOR_OK,
    )
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="stop", description="⏹️ Detiene la música y limpia la cola.")
@app_commands.checks.cooldown(1, 3.0)
async def stop(interaction: discord.Interaction):
    """Detiene la música y limpia la cola."""
    track_command(interaction.guild_id, "stop")
    
    # ✅ DEFER INMEDIATO
    await interaction.response.defer(ephemeral=True, thinking=True)
    
    state = get_music_state(interaction.guild_id)
    
    if not state.voice_client:
        embed = build_embed(
            title="❌ No hay música reproduciéndose",
            color=COLOR_WARN,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    
    # Detener y limpiar
    if state.voice_client.is_playing():
        state.voice_client.stop()
    state.queue.clear()
    state.current = None
    state.is_playing = False
    state.is_paused = False  # ✅ RESETEAR PAUSA
    state.force_skip_loop = False
    
    # Desconectar
    try:
        await state.voice_client.disconnect()
    except:
        pass
    state.voice_client = None
    
    # Limpiar estado
    if interaction.guild_id in music_states:
        del music_states[interaction.guild_id]
    
    embed = build_embed(
        title="⏹️ Música detenida",
        description="La cola ha sido limpiada y el bot se ha desconectado.",
        color=COLOR_WARN,
    )
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="queue", description="📋 Muestra la cola de canciones.")
@app_commands.checks.cooldown(1, 5.0)
async def queue_cmd(interaction: discord.Interaction):
    """Muestra la cola de canciones."""
    track_command(interaction.guild_id, "queue")
    
    # ✅ DEFER INMEDIATO (por si hay muchos elementos)
    await interaction.response.defer(ephemeral=True, thinking=True)
    
    state = get_music_state(interaction.guild_id)
    pages = build_queue_pages(state)
    
    if len(pages) > 1:
        view = Paginator(pages, autor_id=interaction.user.id)
        await interaction.followup.send(embed=pages[0], view=view, ephemeral=True)
    else:
        await interaction.followup.send(embed=pages[0], ephemeral=True)


@bot.tree.command(name="nowplaying", description="🎵 Muestra la canción que se está reproduciendo.")
@app_commands.checks.cooldown(1, 3.0)
async def nowplaying(interaction: discord.Interaction):
    """Muestra la canción actual."""
    track_command(interaction.guild_id, "nowplaying")
    
    # ✅ DEFER INMEDIATO
    await interaction.response.defer(ephemeral=True, thinking=True)
    
    state = get_music_state(interaction.guild_id)
    
    if not state.is_playing() or not state.current:
        embed = build_embed(
            title="🎵 No hay canción reproduciéndose",
            color=COLOR_AMBER,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    
    song = state.current
    
    # Calcular progreso
    progress = "🔴 En vivo"
    if state.play_started_at and song.duration:
        elapsed = time.time() - state.play_started_at
        if elapsed < song.duration:
            progress = create_progress_bar(elapsed, song.duration)
    
    embed = build_embed(
        title="🎵 Reproduciendo ahora",
        color=COLOR_OK,
        thumbnail=song.thumbnail,
        fields=[
            ("📝 Canción", f"[{song.title}]({song.webpage_url})", False),
            ("⏱️ Progreso", progress, False),
            ("👤 Subido por", song.uploader, True),
            ("⏱️ Duración", format_duration(song.duration), True),
            ("🙋 Pedido por", getattr(song.requester, "mention", str(song.requester)), True),
        ],
        footer=f"Volumen: {int(state.volume * 100)}% | {len(state.queue)} canciones en cola",
    )
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="volume", description="🔊 Ajusta el volumen de la música.")
@app_commands.describe(volumen="Volumen (0-100)")
@app_commands.checks.cooldown(1, 3.0)
async def volume(interaction: discord.Interaction, volumen: int):
    """Ajusta el volumen de la música."""
    track_command(interaction.guild_id, "volume")
    
    # ✅ DEFER INMEDIATO
    await interaction.response.defer(ephemeral=True, thinking=True)
    
    if volumen < 0 or volumen > 100:
        embed = build_embed(
            title="❌ Volumen inválido",
            description="El volumen debe estar entre 0 y 100.",
            color=COLOR_WARN,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    
    state = get_music_state(interaction.guild_id)
    
    if not state.voice_client:
        embed = build_embed(
            title="❌ No hay música reproduciéndose",
            color=COLOR_WARN,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    
    state.volume = volumen / 100
    if state.voice_client.source:
        state.voice_client.source.volume = state.volume
    
    embed = build_embed(
        title="🔊 Volumen ajustado",
        description=f"Volumen: **{volumen}%**",
        color=COLOR_OK,
    )
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="loop", description="🔄 Activa el modo de repetición.")
@app_commands.describe(modo="Modo de repetición")
@app_commands.choices(modo=[
    app_commands.Choice(name="Desactivado", value="off"),
    app_commands.Choice(name="Repetir canción", value="song"),
    app_commands.Choice(name="Repetir cola", value="queue"),
])
@app_commands.checks.cooldown(1, 3.0)
async def loop(interaction: discord.Interaction, modo: app_commands.Choice[str]):
    """Activa el modo de repetición."""
    track_command(interaction.guild_id, "loop")
    
    # ✅ DEFER INMEDIATO
    await interaction.response.defer(ephemeral=True, thinking=True)
    
    state = get_music_state(interaction.guild_id)
    state.loop_mode = modo.value
    
    textos = {
        "off": "🔄 Loop desactivado",
        "song": "🔁 Repitiendo canción actual",
        "queue": "🔁 Repitiendo toda la cola",
    }
    
    embed = build_embed(
        title=textos.get(modo.value, "🔄 Modo loop"),
        color=COLOR_OK,
    )
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="shuffle", description="🔀 Mezcla aleatoriamente la cola.")
@app_commands.checks.cooldown(1, 5.0)
async def shuffle(interaction: discord.Interaction):
    """Mezcla aleatoriamente la cola."""
    track_command(interaction.guild_id, "shuffle")
    
    # ✅ DEFER INMEDIATO
    await interaction.response.defer(ephemeral=True, thinking=True)
    
    state = get_music_state(interaction.guild_id)
    
    if len(state.queue) < 2:
        embed = build_embed(
            title="🔀 No hay suficientes canciones para mezclar",
            description="Necesitas al menos 2 canciones en la cola.",
            color=COLOR_AMBER,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    
    random.shuffle(state.queue)
    
    embed = build_embed(
        title="🔀 Cola mezclada",
        description=f"Se mezclaron {len(state.queue)} canciones aleatoriamente.",
        color=COLOR_OK,
    )
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="remove", description="🗑️ Elimina una canción de la cola.")
@app_commands.describe(posicion="Número de posición en la cola")
@app_commands.checks.cooldown(1, 3.0)
async def remove(interaction: discord.Interaction, posicion: int):
    """Elimina una canción de la cola."""
    track_command(interaction.guild_id, "remove")
    
    # ✅ DEFER INMEDIATO
    await interaction.response.defer(ephemeral=True, thinking=True)
    
    state = get_music_state(interaction.guild_id)
    
    if posicion < 1 or posicion > len(state.queue):
        embed = build_embed(
            title="❌ Posición inválida",
            description=f"La cola tiene {len(state.queue)} canciones. Usa un número entre 1 y {len(state.queue)}.",
            color=COLOR_WARN,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    
    removed = state.queue.pop(posicion - 1)
    
    embed = build_embed(
        title="🗑️ Canción eliminada",
        description=f"Se eliminó: **{removed.title}**",
        color=COLOR_OK,
    )
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="disconnect", description="🔌 Desconecta al bot del canal de voz.")
@app_commands.checks.cooldown(1, 5.0)
async def disconnect(interaction: discord.Interaction):
    """Desconecta al bot del canal de voz."""
    track_command(interaction.guild_id, "disconnect")
    
    # ✅ DEFER INMEDIATO
    await interaction.response.defer(ephemeral=True, thinking=True)
    
    state = get_music_state(interaction.guild_id)
    
    if not state.voice_client:
        embed = build_embed(
            title="❌ El bot no está en un canal de voz",
            color=COLOR_WARN,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    
    # Detener y limpiar
    if state.voice_client.is_playing():
        state.voice_client.stop()
    state.queue.clear()
    state.current = None
    state.is_playing = False
    state.is_paused = False  # ✅ RESETEAR PAUSA
    
    try:
        await state.voice_client.disconnect()
    except:
        pass
    state.voice_client = None
    
    # Limpiar estado
    if interaction.guild_id in music_states:
        del music_states[interaction.guild_id]
    
    embed = build_embed(
        title="🔌 Desconectado",
        description="El bot ha sido desconectado del canal de voz.",
        color=COLOR_OK,
    )
    await interaction.followup.send(embed=embed)


@bot.event
async def on_member_join(member: discord.Member):
    cfg = get_guild_config(member.guild.id)
    bump_stat(member.guild.id, "joins")
    record_log(member.guild.id, "bienvenidas", f"{member} se unió al servidor.", str(member))

    # Autorole
    autorole_id = cfg.get("autorole_id")
    if autorole_id:
        role = member.guild.get_role(int(autorole_id))
        if role:
            try:
                await member.add_roles(role, reason="Autorole al unirse")
            except:
                pass

    # Verificación: asigna el rol de "no verificados" automáticamente
    if cfg.get("verify_enabled") and cfg.get("verify_unverified_role_id"):
        unverified_role = member.guild.get_role(int(cfg["verify_unverified_role_id"]))
        if unverified_role:
            try:
                await member.add_roles(unverified_role, reason="Pendiente de verificación")
            except:
                pass

    if not cfg.get("welcome_enabled"):
        return
    channel_id = cfg.get("welcome_channel")
    if not channel_id:
        return
    channel = member.guild.get_channel(int(channel_id))
    if channel is None:
        return

    mensaje = fill_placeholders(cfg.get("welcome_message", ""), member, member.guild)
    embed = build_embed(
        title=f"🎉 ¡Nuevo miembro en {member.guild.name}!",
        description=mensaje,
        color=COLOR_AMBER,
        thumbnail=member.display_avatar.url,
        image=cfg.get("welcome_banner"),
        footer=f"Miembro #{member.guild.member_count}",
    )
    await channel.send(embed=embed)


@bot.event
async def on_member_remove(member: discord.Member):
    cfg = get_guild_config(member.guild.id)
    bump_stat(member.guild.id, "leaves")
    record_log(member.guild.id, "bienvenidas", f"{member} abandonó el servidor.", str(member))

    if not cfg.get("farewell_enabled"):
        return
    channel_id = cfg.get("farewell_channel")
    if not channel_id:
        return
    channel = member.guild.get_channel(int(channel_id))
    if channel is None:
        return

    mensaje = fill_placeholders(cfg.get("farewell_message", ""), member, member.guild)
    embed = build_embed(
        title=f"👋 Alguien se fue de {member.guild.name}",
        description=mensaje,
        color=discord.Color.dark_grey(),
        thumbnail=member.display_avatar.url,
        image=cfg.get("farewell_banner"),
    )
    await channel.send(embed=embed)


@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if before.author.bot:
        return
    if before.content == after.content:
        return

    bump_stat(before.guild.id, "messages_edited")
    record_log(before.guild.id, "editados", f"Mensaje editado en #{before.channel}", str(before.author))

    channel = await get_log_channel(before.guild, "editados")
    if channel is None:
        return

    embed = build_embed(
        title="✏️ Mensaje editado",
        color=discord.Color.orange(),
        fields=[
            ("Autor", before.author.mention, True),
            ("Canal", before.channel.mention, True),
            ("Antes", (before.content or "*(vacío / solo adjuntos)*")[:1024], False),
            ("Después", (after.content or "*(vacío / solo adjuntos)*")[:1024], False),
        ],
        thumbnail=before.author.display_avatar.url,
        footer=f"ID del usuario: {before.author.id}",
    )
    await channel.send(embed=embed)


@bot.event
async def on_message_delete(message: discord.Message):
    if message.author.bot:
        return

    bump_stat(message.guild.id, "messages_deleted")
    record_log(message.guild.id, "borrados", f"Mensaje eliminado en #{message.channel}", str(message.author))

    channel = await get_log_channel(message.guild, "borrados")
    if channel is None:
        return

    fields = [
        ("Autor", message.author.mention, True),
        ("Canal", message.channel.mention, True),
        ("Contenido", (message.content or "*(vacío / solo adjuntos)*")[:1024], False),
    ]
    if message.attachments:
        fields.append(("Adjuntos", "\n".join(a.url for a in message.attachments)[:1024], False))

    embed = build_embed(
        title="🗑️ Mensaje eliminado",
        color=COLOR_WARN,
        fields=fields,
        thumbnail=message.author.display_avatar.url,
        footer=f"ID del usuario: {message.author.id}",
    )
    await channel.send(embed=embed)


# ──────────────────────────────────────────────────────────────
#  COMANDOS PRINCIPALES
# ──────────────────────────────────────────────────────────────

@bot.tree.command(name="legales", description="[Privado] Muestra los Términos y Condiciones de Nexus Pro (Beta).")
@app_commands.checks.cooldown(1, 10.0)
async def legales(interaction: discord.Interaction):
    track_command(interaction.guild_id, "legales")
    
    # ✅ VERIFICAR ROLES
    if not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("Este comando solo funciona en el servidor.", ephemeral=True)
        return

    role_check = check_user_roles(interaction.user)
    
    # ✅ PERMITIR ACCESO A: Owner, Admin, Insiders, VIP
    if not role_check["has_permission"]:
        embed = build_embed(
            title="🔒 Acceso restringido",
            description=(
                "Este comando es exclusivo para usuarios autorizados.\n\n"
                "**Requisitos:**\n"
                f"• **Owner** del bot\n"
                f"• **Administrador** del bot\n"
                f"• Rol **{Config.INSIDER_ROLE_NAME}**\n"
                f"• Rol **{Config.VIP_ROLE_NAME}**\n"
                "• Ser **owner** del servidor"
            ),
            color=COLOR_WARN,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    # Si tiene permisos, mostrar términos
    embed = build_embed(
        title="📜 Términos y Condiciones de Uso – Nexus Pro (Beta)",
        description=TERMINOS_EMBED_DESCRIPTION,
        color=COLOR_MAIN,
        footer="Nexus Pro · Debes aceptar para continuar",
    )
    view = TerminosView(autor_id=interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

@bot.tree.command(name="legales-free", description="📜 Muestra los Términos y Condiciones de Nexus Core (Versión Gratuita).")
@app_commands.checks.cooldown(1, 10.0)
async def legales_free(interaction: discord.Interaction):
    track_command(interaction.guild_id, "legales-free")
    
    embed = build_embed(
        title="📜 Términos y Condiciones – Nexus Core (Versión Gratuita)",
        description=(
            "**1. Uso gratuito**\n"
            "Nexus Core es la versión gratuita de Nexus Pro y está disponible "
            "para todos los usuarios sin costo alguno.\n\n"
            
            "**2. Limitaciones**\n"
            "• Funcionalidades básicas de navegación\n"
            "• Sin soporte técnico prioritario\n"
            "• Actualizaciones periódicas\n"
            "• Sin funciones premium (Pro)\n\n"
            
            "**3. Uso permitido**\n"
            "Puedes usar Nexus Core libremente para:\n"
            "• Navegación básica\n"
            "• Gestión de archivos simple\n"
            "• Automatización básica\n\n"
            
            "**4. Prohibiciones**\n"
            "• Modificar o descompilar el software\n"
            "• Vender o redistribuir\n"
            "• Usar para actividades ilegales\n"
            "• Intentar vulnerar la seguridad\n\n"
            
            "**5. Exención de responsabilidad**\n"
            "Nexus Core se proporciona 'tal cual', sin garantías de ningún tipo. "
            "El equipo de desarrollo no se hace responsable por daños o pérdidas "
            "derivadas del uso del software.\n\n"
            
            "**6. Actualizaciones**\n"
            "Al usar Nexus Core, aceptas recibir actualizaciones automáticas "
            "para mejorar la seguridad y funcionalidad.\n\n"
            
            "**7. Privacidad**\n"
            "Nexus Core no recopila datos personales sin tu consentimiento. "
            "Los datos de uso anónimos pueden ser recopilados para mejorar el software.\n\n"
            
            "─────────────────────────────\n"
            "📌 **Al usar Nexus Core, aceptas estos términos y condiciones.**\n"
            "📥 Descarga: `/nexus-free-public`"
        ),
        color=discord.Color.from_rgb(0, 200, 255),
        footer="Nexus Core · Versión Gratuita · Licencia MIT",
        thumbnail="https://cdn.discordapp.com/emojis/123456789.png",  # Logo de Nexus Core
    )
    
    # Botón para descargar
    view = discord.ui.View()
    view.add_item(
        discord.ui.Button(
            label="📥 Descargar Nexus Core",
            style=discord.ButtonStyle.success,
            emoji="📥",
            custom_id="descargar_nexus_core"
        )
    )
    
    async def descargar_core(interaction: discord.Interaction):
        await interaction.response.send_message(
            "📥 Usa `/nexus-free-public` para obtener el enlace de descarga de Nexus Core.",
            ephemeral=True
        )
    
    for item in view.children:
        if hasattr(item, 'custom_id') and item.custom_id == "descargar_nexus_core":
            item.callback = descargar_core
    
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

@bot.tree.command(name="enlace", description="Muestra el enlace vigente del instalador de Pro.")
@app_commands.checks.cooldown(1, 10.0)
async def enlace(interaction: discord.Interaction):
    track_command(interaction.guild_id, "enlace")

    if not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("Este comando solo funciona en el servidor.", ephemeral=True)
        return

    role_check = check_user_roles(interaction.user)
    
    if not role_check["has_permission"]:
        embed = build_embed(
            title="🔒 Acceso restringido",
            description=(
                "No tienes permiso para usar este comando.\n\n"
                "**Requisitos:**\n"
                f"• Rol **{Config.INSIDER_ROLE_NAME}**\n"
                f"• Rol **{Config.VIP_ROLE_NAME}**\n"
                "• Ser **owner** del servidor\n"
                "• Ser **owner** del bot"
            ),
            color=COLOR_WARN,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    cfg = get_guild_config(interaction.guild_id)
    link = cfg.get("current_link") or Config.DOWNLOAD_LINK
    
    if not link:
        embed = build_embed(
            title="📭 Sin enlace configurado",
            description="Aún no hay un enlace configurado. Contacta a un administrador.",
            color=COLOR_WARN,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    embed = build_embed(
        title="📥 Instalador de Nexus Pro (Beta)",
        description=(
            "⚠️ Este enlace cambia con cada nueva versión, no lo compartas ni lo "
            "guardes como definitivo."
        ),
        color=COLOR_OK,
        fields=[("🔗 Enlace de descarga", link, False)],
        footer="Nexus Pro · Beta",
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="verificar-rol", description="[Admin] Verifica los roles y permisos de un usuario.")
@app_commands.checks.has_permissions(administrator=True)
async def verificar_rol(interaction: discord.Interaction, usuario: discord.Member | None = None):
    usuario = usuario or interaction.user
    
    result = check_user_roles(usuario)
    
    roles_lista = ", ".join([f"`{r.name}`" for r in usuario.roles if r.name != "@everyone"])
    
    embed = discord.Embed(
        title="🔍 Verificación de roles",
        color=discord.Color.blue(),
        timestamp=datetime.now(timezone.utc)
    )
    
    embed.set_thumbnail(url=usuario.display_avatar.url)
    embed.add_field(name="👤 Usuario", value=usuario.mention, inline=True)
    embed.add_field(name="🆔 ID", value=f"`{usuario.id}`", inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True)
    
    embed.add_field(
        name="👑 Owner del bot", 
        value="✅ Sí" if result["is_owner"] else "❌ No", 
        inline=True
    )
    embed.add_field(
        name="👑 Owner del servidor", 
        value="✅ Sí" if usuario.id == interaction.guild.owner_id else "❌ No", 
        inline=True
    )
    embed.add_field(
        name="\u200b", 
        value="\u200b", 
        inline=True
    )
    
    # ✅ NUEVO: Mostrar Admin User
    embed.add_field(
        name="👑 Admin del bot", 
        value="✅ Sí" if result.get("is_admin_user", False) else "❌ No", 
        inline=True
    )
    
    embed.add_field(
        name=f"🔑 {Config.INSIDER_ROLE_NAME}", 
        value="✅ Sí" if result["has_insider"] else "❌ No", 
        inline=True
    )
    embed.add_field(
        name=f"🔑 {Config.VIP_ROLE_NAME}", 
        value="✅ Sí" if result["has_vip"] else "❌ No", 
        inline=True
    )
    embed.add_field(
        name="\u200b", 
        value="\u200b", 
        inline=True
    )
    
    embed.add_field(
        name="✅ Permiso para /enlace", 
        value="✅ Sí" if result["has_permission"] else "❌ No", 
        inline=True
    )
    
    embed.add_field(
        name=f"📋 Roles ({len(usuario.roles) - 1})",
        value=roles_lista or "*(sin roles)*",
        inline=False
    )
    
    if result["matched_roles"]:
        embed.add_field(
            name="🎯 Roles coincidentes",
            value=", ".join([f"`{r}`" for r in result["matched_roles"]]),
            inline=False
        )
    
    embed.set_footer(text="Nexus System · Verificación de roles")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ──────────────────────────────────────────────────────────────
#  COMANDOS DE MODERACIÓN
# ──────────────────────────────────────────────────────────────

@bot.tree.command(name="mute", description="[Mod] Mutea a un usuario con duración personalizada.")
@app_commands.describe(
    usuario="Usuario a mutear",
    duracion="Duración: 5m, 2h, 1d, 1w, 1M (o dejar vacío para permanente)",
    razon="Razón del mute"
)
@app_commands.checks.has_permissions(moderate_members=True)
@app_commands.checks.cooldown(1, 5.0)
async def mute(
    interaction: discord.Interaction,
    usuario: discord.Member,
    duracion: str | None = None,
    razon: str = "No especificada"
):
    track_command(interaction.guild_id, "mute")
    
    if usuario.id == interaction.user.id:
        embed = build_embed(
            title="❌ No te puedes mutear a ti mismo",
            color=COLOR_WARN
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    if not interaction.guild.me.guild_permissions.moderate_members:
        embed = build_embed(
            title="❌ El bot no tiene permisos para moderar miembros",
            description="Necesita el permiso `Moderar Miembros`.",
            color=COLOR_WARN
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    if usuario.top_role >= interaction.guild.me.top_role:
        embed = build_embed(
            title="❌ No puedo mutear a este usuario",
            description="Su rol es igual o superior al mío.",
            color=COLOR_WARN
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    if is_user_muted(interaction.guild_id, usuario.id):
        embed = build_embed(
            title="ℹ️ El usuario ya está muteado",
            description=f"Razón actual: {get_mute_reason(interaction.guild_id, usuario.id)}",
            color=COLOR_AMBER
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    duration_text = "Permanente"
    if duracion:
        try:
            unit = duracion[-1]
            value = int(duracion[:-1])
            if unit not in ['m', 'h', 'd', 'w', 'M'] or value <= 0:
                raise ValueError
        except:
            embed = build_embed(
                title="❌ Formato de duración inválido",
                description="Usa: `5m`, `2h`, `1d`, `1w`, `1M`",
                color=COLOR_WARN
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        unit_names = {
            'm': 'minuto(s)',
            'h': 'hora(s)',
            'd': 'día(s)',
            'w': 'semana(s)',
            'M': 'mes(es)'
        }
        duration_text = f"{value} {unit_names[unit]}"
    
    if duracion:
        unit = duracion[-1]
        value = int(duracion[:-1])
        
        if unit == 'm':
            seconds = value * 60
        elif unit == 'h':
            seconds = value * 3600
        elif unit == 'd':
            seconds = value * 86400
        elif unit == 'w':
            seconds = value * 604800
        elif unit == 'M':
            seconds = value * 2592000
        else:
            seconds = value * 60
        
        if seconds > 2419200:
            seconds = 2419200
            duration_text = "28 días (máximo permitido)"
        
        await usuario.timeout(discord.utils.utcnow() + timedelta(seconds=seconds), reason=razon)
    else:
        await usuario.timeout(discord.utils.utcnow() + timedelta(days=28), reason=f"Permanente: {razon}")
    
    mute_user(interaction.guild_id, usuario.id, duracion, razon, str(interaction.user))
    
    record_log(
        interaction.guild_id,
        "moderacion",
        f"{interaction.user} muteó a {usuario} por {duration_text}. Razón: {razon}",
        str(interaction.user)
    )
    
    try:
        dm_embed = discord.Embed(
            title="🔇 Has sido muteado",
            description=f"Has sido muteado en **{interaction.guild.name}**.",
            color=COLOR_WARN,
            timestamp=datetime.now(timezone.utc)
        )
        dm_embed.add_field(name="📋 Razón", value=razon, inline=False)
        dm_embed.add_field(name="⏱️ Duración", value=duration_text, inline=True)
        dm_embed.add_field(name="👤 Moderador", value=str(interaction.user), inline=True)
        dm_embed.add_field(
            name="📝 ¿Consideras que es injusto?",
            value="Puedes apelar usando el botón de abajo.",
            inline=False
        )
        dm_embed.set_footer(text="Nexus Moderation System")
        
        view = AppealView(interaction.guild_id, usuario.id, "mute")
        await usuario.send(embed=dm_embed, view=view)
    except:
        pass
    
    embed = build_embed(
        title="🔇 Usuario muteado",
        description=f"**{usuario}** ha sido muteado.",
        color=COLOR_OK,
        fields=[
            ("📋 Razón", razon, True),
            ("⏱️ Duración", duration_text, True),
            ("👤 Moderador", interaction.user.mention, True)
        ]
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="unmute", description="[Mod] Desmutea a un usuario.")
@app_commands.describe(usuario="Usuario a desmutear")
@app_commands.checks.has_permissions(moderate_members=True)
async def unmute(interaction: discord.Interaction, usuario: discord.Member):
    track_command(interaction.guild_id, "unmute")
    
    if usuario.timed_out_until is None and not is_user_muted(interaction.guild_id, usuario.id):
        embed = build_embed(
            title="ℹ️ El usuario no está muteado",
            color=COLOR_AMBER
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    try:
        await usuario.timeout(None)
    except:
        pass
    
    unmute_user(interaction.guild_id, usuario.id)
    
    record_log(
        interaction.guild_id,
        "moderacion",
        f"{interaction.user} desmuteó a {usuario}",
        str(interaction.user)
    )
    
    try:
        dm_embed = discord.Embed(
            title="🔊 Has sido desmuteado",
            description=f"Has sido desmuteado en **{interaction.guild.name}**.",
            color=COLOR_OK,
            timestamp=datetime.now(timezone.utc)
        )
        dm_embed.add_field(name="👤 Moderador", value=str(interaction.user), inline=True)
        await usuario.send(embed=dm_embed)
    except:
        pass
    
    embed = build_embed(
        title="🔊 Usuario desmuteado",
        description=f"**{usuario}** ha sido desmuteado.",
        color=COLOR_OK
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="kick", description="[Mod] Expulsa a un usuario con DM y botón de apelación.")
@app_commands.describe(
    usuario="Usuario a expulsar",
    razon="Razón de la expulsión"
)
@app_commands.checks.has_permissions(kick_members=True)
@app_commands.checks.cooldown(1, 5.0)
async def kick(interaction: discord.Interaction, usuario: discord.Member, razon: str = "No especificada"):
    track_command(interaction.guild_id, "kick")
    
    if usuario.id == interaction.user.id:
        embed = build_embed(
            title="❌ No te puedes expulsar a ti mismo",
            color=COLOR_WARN
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    if usuario.top_role >= interaction.guild.me.top_role:
        embed = build_embed(
            title="❌ No puedo expulsar a este usuario",
            description="Su rol es igual o superior al mío.",
            color=COLOR_WARN
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    try:
        dm_embed = discord.Embed(
            title="👋 Has sido expulsado",
            description=f"Has sido expulsado de **{interaction.guild.name}**.",
            color=COLOR_WARN,
            timestamp=datetime.now(timezone.utc)
        )
        dm_embed.add_field(name="📋 Razón", value=razon, inline=False)
        dm_embed.add_field(name="👤 Moderador", value=str(interaction.user), inline=True)
        dm_embed.add_field(
            name="📝 ¿Consideras que es injusto?",
            value="Puedes apelar usando el botón de abajo.",
            inline=False
        )
        dm_embed.set_footer(text="Nexus Moderation System")
        
        view = AppealView(interaction.guild_id, usuario.id, "kick")
        await usuario.send(embed=dm_embed, view=view)
    except:
        pass
    
    bump_stat(interaction.guild_id, "kicks")
    
    record_log(
        interaction.guild_id,
        "moderacion",
        f"{interaction.user} expulsó a {usuario}. Razón: {razon}",
        str(interaction.user)
    )
    
    await usuario.kick(reason=razon)
    
    embed = build_embed(
        title="👋 Usuario expulsado",
        description=f"**{usuario}** ha sido expulsado.",
        color=COLOR_WARN,
        fields=[
            ("📋 Razón", razon, False),
            ("👤 Moderador", interaction.user.mention, True)
        ]
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="ban", description="[Mod] Banea a un usuario con duración personalizada.")
@app_commands.describe(
    usuario="Usuario a banear",
    duracion="Duración: 5m, 2h, 1d, 1w, 1M, 1y (o dejar vacío para permanente)",
    razon="Razón del baneo",
    ban_ip_choice="¿Banear también la IP?"
)
@app_commands.choices(
    ban_ip_choice=[
        app_commands.Choice(name="Sí, banear IP", value="si"),
        app_commands.Choice(name="No, solo usuario", value="no"),
    ]
)
@app_commands.checks.has_permissions(ban_members=True)
@app_commands.checks.cooldown(1, 5.0)
async def ban(
    interaction: discord.Interaction,
    usuario: discord.Member,
    duracion: str | None = None,
    razon: str = "No especificada",
    ban_ip_choice: app_commands.Choice[str] | None = None
):
    # FIX: el parámetro ya no se llama "ban_ip" (chocaba con la función global
    # ban_ip(), lo que provocaba "TypeError: 'Choice' object is not callable"
    # al elegir "Sí, banear IP"). Ahora se llama "ban_ip_choice".
    track_command(interaction.guild_id, "ban")
    
    if usuario.id == interaction.user.id:
        embed = build_embed(
            title="❌ No te puedes banear a ti mismo",
            color=COLOR_WARN
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    if usuario.top_role >= interaction.guild.me.top_role:
        embed = build_embed(
            title="❌ No puedo banear a este usuario",
            description="Su rol es igual o superior al mío.",
            color=COLOR_WARN
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    duration_text = "Permanente"
    if duracion:
        try:
            unit = duracion[-1]
            value = int(duracion[:-1])
            if unit not in ['m', 'h', 'd', 'w', 'M', 'y'] or value <= 0:
                raise ValueError
        except:
            embed = build_embed(
                title="❌ Formato de duración inválido",
                description="Usa: `5m`, `2h`, `1d`, `1w`, `1M`, `1y`",
                color=COLOR_WARN
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        unit_names = {
            'm': 'minuto(s)',
            'h': 'hora(s)',
            'd': 'día(s)',
            'w': 'semana(s)',
            'M': 'mes(es)',
            'y': 'año(s)'
        }
        duration_text = f"{value} {unit_names[unit]}"
    
    try:
        dm_embed = discord.Embed(
            title="🔨 Has sido baneado",
            description=f"Has sido baneado de **{interaction.guild.name}**.",
            color=COLOR_WARN,
            timestamp=datetime.now(timezone.utc)
        )
        dm_embed.add_field(name="📋 Razón", value=razon, inline=False)
        dm_embed.add_field(name="⏱️ Duración", value=duration_text, inline=True)
        dm_embed.add_field(name="👤 Moderador", value=str(interaction.user), inline=True)
        if ban_ip_choice and ban_ip_choice.value == "si":
            dm_embed.add_field(name="🌐 IP baneada", value="Sí", inline=True)
        dm_embed.add_field(
            name="📝 ¿Consideras que es injusto?",
            value="Puedes apelar usando el botón de abajo.",
            inline=False
        )
        dm_embed.set_footer(text="Nexus Moderation System")
        
        view = AppealView(interaction.guild_id, usuario.id, "ban")
        await usuario.send(embed=dm_embed, view=view)
    except:
        pass
    
    ip_banned = False
    if ban_ip_choice and ban_ip_choice.value == "si":
        ip = get_user_ip(usuario.id) or f"192.168.1.{usuario.id % 255}"
        ban_ip(ip, reason=razon, moderator=str(interaction.user))
        ip_banned = True
        set_user_ip(usuario.id, ip)
    
    bump_stat(interaction.guild_id, "bans")
    
    record_log(
        interaction.guild_id,
        "moderacion",
        f"{interaction.user} baneó a {usuario} por {duration_text}. Razón: {razon}{' (IP baneada)' if ip_banned else ''}",
        str(interaction.user)
    )
    
    await interaction.guild.ban(usuario, reason=f"{razon} | Moderador: {interaction.user}")
    
    embed = build_embed(
        title="🔨 Usuario baneado",
        description=f"**{usuario}** ha sido baneado.",
        color=COLOR_WARN,
        fields=[
            ("📋 Razón", razon, False),
            ("⏱️ Duración", duration_text, True),
            ("🌐 IP baneada", "Sí" if ip_banned else "No", True),
            ("👤 Moderador", interaction.user.mention, True)
        ]
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="unban", description="[Mod] Desbanea a un usuario.")
@app_commands.describe(
    usuario_id="ID del usuario a desbanear",
    desbanear_ip="¿Desbanear también la IP?"
)
@app_commands.choices(
    desbanear_ip=[
        app_commands.Choice(name="Sí, desbanear IP", value="si"),
        app_commands.Choice(name="No, solo usuario", value="no"),
    ]
)
@app_commands.checks.has_permissions(ban_members=True)
async def unban(
    interaction: discord.Interaction,
    usuario_id: str,
    desbanear_ip: app_commands.Choice[str] | None = None
):
    track_command(interaction.guild_id, "unban")
    
    try:
        user_id = int(usuario_id)
    except:
        embed = build_embed(
            title="❌ ID inválido",
            description="Debes proporcionar un ID de usuario válido.",
            color=COLOR_WARN
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    try:
        user = await bot.fetch_user(user_id)
        await interaction.guild.unban(user)
        
        ip_unbanned = False
        if desbanear_ip and desbanear_ip.value == "si":
            ip = get_user_ip(user_id)
            if ip:
                ip_unbanned = unban_ip(ip)
        
        record_log(
            interaction.guild_id,
            "moderacion",
            f"{interaction.user} desbaneó a {user} (ID: {user_id}){' (IP desbaneada)' if ip_unbanned else ''}",
            str(interaction.user)
        )
        
        embed = build_embed(
            title="🔓 Usuario desbaneado",
            description=f"**{user}** ha sido desbaneado.",
            color=COLOR_OK,
            fields=[
                ("🌐 IP desbaneada", "Sí" if ip_unbanned else "No", True),
                ("👤 Moderador", interaction.user.mention, True)
            ]
        )
        await interaction.response.send_message(embed=embed)
        
    except discord.NotFound:
        embed = build_embed(
            title="❌ Usuario no encontrado",
            description=f"No se encontró un usuario baneado con ID `{usuario_id}`.",
            color=COLOR_WARN
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        embed = build_embed(
            title="❌ Error al desbanear",
            description=f"No se pudo desbanear al usuario: {e}",
            color=COLOR_WARN
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="ban-ip", description="[Mod] Banea una IP (simulado).")
@app_commands.describe(
    ip="Dirección IP a banear",
    razon="Razón del baneo de IP"
)
@app_commands.checks.has_permissions(ban_members=True)
async def ban_ip_cmd(interaction: discord.Interaction, ip: str, razon: str = "No especificada"):
    track_command(interaction.guild_id, "ban-ip")
    
    if is_ip_banned(ip):
        embed = build_embed(
            title="ℹ️ La IP ya está baneada",
            color=COLOR_AMBER
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    ban_ip(ip, reason=razon, moderator=str(interaction.user))
    
    record_log(
        interaction.guild_id,
        "moderacion",
        f"{interaction.user} baneó la IP {ip}. Razón: {razon}",
        str(interaction.user)
    )
    
    embed = build_embed(
        title="🌐 IP baneada",
        description=f"La IP `{ip}` ha sido baneada.",
        color=COLOR_WARN,
        fields=[
            ("📋 Razón", razon, False),
            ("👤 Moderador", interaction.user.mention, True)
        ]
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="unban-ip", description="[Mod] Desbanea una IP.")
@app_commands.describe(ip="Dirección IP a desbanear")
@app_commands.checks.has_permissions(ban_members=True)
async def unban_ip_cmd(interaction: discord.Interaction, ip: str):
    track_command(interaction.guild_id, "unban-ip")
    
    if unban_ip(ip):
        record_log(
            interaction.guild_id,
            "moderacion",
            f"{interaction.user} desbaneó la IP {ip}",
            str(interaction.user)
        )
        
        embed = build_embed(
            title="🌐 IP desbaneada",
            description=f"La IP `{ip}` ha sido desbaneada.",
            color=COLOR_OK
        )
        await interaction.response.send_message(embed=embed)
    else:
        embed = build_embed(
            title="❌ IP no encontrada",
            description=f"No se encontró la IP `{ip}` en la lista de baneadas.",
            color=COLOR_WARN
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="multicuenta", description="[Admin] Detecta y maneja posibles multicuentas.")
@app_commands.describe(
    usuario="Usuario a verificar (opcional)",
    ip="IP específica a verificar (opcional)"
)
@app_commands.checks.has_permissions(administrator=True)
async def multicuenta(
    interaction: discord.Interaction,
    usuario: discord.Member | None = None,
    ip: str | None = None
):
    track_command(interaction.guild_id, "multicuenta")
    
    user_ips_data = load_user_ips()
    banned_ips_data = load_banned_ips()
    
    if usuario:
        user_ip = get_user_ip(usuario.id)
        if not user_ip:
            embed = build_embed(
                title="🔍 Usuario sin IP registrada",
                description=f"No hay información de IP para **{usuario}**.",
                color=COLOR_AMBER
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        same_ip_users = []
        for uid, uip in user_ips_data.items():
            if uip == user_ip and int(uid) != usuario.id:
                try:
                    u = await bot.fetch_user(int(uid))
                    same_ip_users.append(u)
                except:
                    pass
        
        in_server = []
        for u in same_ip_users:
            if interaction.guild.get_member(u.id):
                in_server.append(u)
        
        embed = discord.Embed(
            title="🔍 Análisis de Multicuenta",
            color=COLOR_MAIN,
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_thumbnail(url=usuario.display_avatar.url)
        embed.add_field(name="👤 Usuario", value=usuario.mention, inline=True)
        embed.add_field(name="🆔 ID", value=f"`{usuario.id}`", inline=True)
        embed.add_field(name="🌐 IP", value=f"`{user_ip}`", inline=True)
        embed.add_field(
            name="🚫 IP baneada",
            value="✅ Sí" if is_ip_banned(user_ip) else "❌ No",
            inline=True
        )
        embed.add_field(
            name="📊 Usuarios con misma IP",
            value=str(len(same_ip_users)),
            inline=True
        )
        embed.add_field(
            name="🟢 En el servidor",
            value=", ".join([u.mention for u in in_server]) if in_server else "Ninguno",
            inline=False
        )
        embed.add_field(
            name="📋 Lista completa",
            value="\n".join([f"• {u} (ID: {u.id}){' ✅ en servidor' if u.id in [m.id for m in in_server] else ''}" for u in same_ip_users]) or "Ninguno",
            inline=False
        )
        embed.set_footer(text="Nexus Anti-Multicuenta")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
        
    elif ip:
        if ip not in user_ips_data.values():
            embed = build_embed(
                title="🔍 IP sin usuarios asociados",
                description=f"No hay usuarios registrados con la IP `{ip}`.",
                color=COLOR_AMBER
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        users_with_ip = []
        for uid, uip in user_ips_data.items():
            if uip == ip:
                try:
                    u = await bot.fetch_user(int(uid))
                    users_with_ip.append(u)
                except:
                    pass
        
        in_server = []
        for u in users_with_ip:
            if interaction.guild.get_member(u.id):
                in_server.append(u)
        
        embed = discord.Embed(
            title="🔍 Análisis de IP",
            color=COLOR_MAIN,
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="🌐 IP", value=f"`{ip}`", inline=True)
        embed.add_field(
            name="🚫 IP baneada",
            value="✅ Sí" if is_ip_banned(ip) else "❌ No",
            inline=True
        )
        embed.add_field(
            name="📊 Usuarios con esta IP",
            value=str(len(users_with_ip)),
            inline=True
        )
        embed.add_field(
            name="🟢 En el servidor",
            value=", ".join([u.mention for u in in_server]) if in_server else "Ninguno",
            inline=False
        )
        embed.add_field(
            name="📋 Lista completa",
            value="\n".join([f"• {u} (ID: {u.id}){' ✅ en servidor' if u.id in [m.id for m in in_server] else ''}" for u in users_with_ip]) or "Ninguno",
            inline=False
        )
        embed.set_footer(text="Nexus Anti-Multicuenta")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
        
    else:
        total_users = len(user_ips_data)
        unique_ips = len(set(user_ips_data.values()))
        banned_ips = len(banned_ips_data)
        
        ip_counts = {}
        for uid, uip in user_ips_data.items():
            ip_counts[uip] = ip_counts.get(uip, 0) + 1
        
        shared_ips = {ip: count for ip, count in ip_counts.items() if count > 1}
        
        embed = discord.Embed(
            title="📊 Resumen Anti-Multicuenta",
            color=COLOR_PURPLE,
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="👥 Usuarios registrados", value=str(total_users), inline=True)
        embed.add_field(name="🌐 IPs únicas", value=str(unique_ips), inline=True)
        embed.add_field(name="🚫 IPs baneadas", value=str(banned_ips), inline=True)
        embed.add_field(
            name="🔄 IPs compartidas",
            value=f"{len(shared_ips)} IPs con múltiples usuarios",
            inline=True
        )
        
        if shared_ips:
            shared_list = []
            for ip, count in list(shared_ips.items())[:5]:
                users = [uid for uid, uip in user_ips_data.items() if uip == ip]
                user_mentions = []
                for uid in users[:3]:
                    try:
                        u = await bot.fetch_user(int(uid))
                        user_mentions.append(u.mention)
                    except:
                        user_mentions.append(f"ID: {uid}")
                shared_list.append(f"`{ip}` → {count} usuarios: {', '.join(user_mentions)}{'...' if len(users) > 3 else ''}")
            
            embed.add_field(
                name="📋 IPs más compartidas",
                value="\n".join(shared_list),
                inline=False
            )
        
        embed.set_footer(text="Nexus Anti-Multicuenta")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="muted-list", description="[Mod] Muestra la lista de usuarios muteados.")
@app_commands.checks.has_permissions(moderate_members=True)
async def muted_list(interaction: discord.Interaction):
    track_command(interaction.guild_id, "muted-list")
    
    data = load_muted()
    muted_users = []
    
    for key, info in data.items():
        guild_id, user_id = key.split("_")
        if int(guild_id) != interaction.guild_id:
            continue
        
        try:
            user = await bot.fetch_user(int(user_id))
            muted_users.append((user, info))
        except:
            muted_users.append((f"ID: {user_id}", info))
    
    if not muted_users:
        embed = build_embed(
            title="🔇 Lista de muteados",
            description="No hay usuarios muteados en este servidor.",
            color=COLOR_AMBER
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    pages = []
    chunk_size = 10
    for i in range(0, len(muted_users), chunk_size):
        chunk = muted_users[i:i+chunk_size]
        
        lines = []
        for user, info in chunk:
            user_str = user.mention if hasattr(user, 'mention') else str(user)
            permanent = info.get("permanent", False)
            expiry = info.get("expiry")
            
            if permanent:
                time_str = "🔒 Permanente"
            elif expiry:
                try:
                    dt = datetime.fromisoformat(expiry)
                    time_str = f"⏳ <t:{int(dt.timestamp())}:R>"
                except:
                    time_str = "⏳ Desconocido"
            else:
                time_str = "🔒 Permanente"
            
            lines.append(
                f"**{user_str}**\n"
                f"└ 📋 {info.get('reason', 'No especificada')} | {time_str} | 👤 {info.get('moderator', 'Desconocido')}"
            )
        
        embed = build_embed(
            title=f"🔇 Lista de muteados (página {i//chunk_size + 1})",
            description="\n\n".join(lines),
            color=COLOR_MAIN,
            footer=f"Total: {len(muted_users)} usuarios"
        )
        pages.append(embed)
    
    if len(pages) > 1:
        view = Paginator(pages, autor_id=interaction.user.id)
        await interaction.response.send_message(embed=pages[0], view=view, ephemeral=True)
    else:
        await interaction.response.send_message(embed=pages[0], ephemeral=True)


# ──────────────────────────────────────────────────────────────
#  COMANDOS DE ADMINISTRACIÓN Y UTILIDAD (RESTO DEL CÓDIGO)
# ──────────────────────────────────────────────────────────────

# ──────────────────────────────────────────────────────────────
#  ENLACE / CONFIG-LOGS / CONFIG-ROL-ENLACE
# ──────────────────────────────────────────────────────────────

@bot.tree.command(name="actualizar-enlace", description="[Admin] Actualiza el enlace del instalador y avisa a Insiders/VIP.")
@app_commands.describe(
    nuevo_enlace="Nuevo enlace de descarga",
    avisar="¿Avisar a los roles autorizados?"
)
@app_commands.choices(avisar=[
    app_commands.Choice(name="Sí, avisar", value="si"),
    app_commands.Choice(name="No avisar", value="no"),
])
@app_commands.checks.has_permissions(administrator=True)
async def actualizar_enlace(
    interaction: discord.Interaction,
    nuevo_enlace: str,
    avisar: app_commands.Choice[str] | None = None
):
    await interaction.response.defer(ephemeral=True)

    track_command(interaction.guild_id, "actualizar-enlace")
    cfg = get_guild_config(interaction.guild_id)
    cfg["current_link"] = nuevo_enlace
    update_guild_config(interaction.guild_id, cfg)

    record_log(
        interaction.guild_id,
        "enlace",
        f"{interaction.user} actualizó el enlace de descarga.",
        str(interaction.user)
    )

    log_channel = await get_log_channel(interaction.guild, "enlace")
    if log_channel is not None:
        log_embed = build_embed(
            title="🔗 Enlace actualizado",
            description=f"{interaction.user.mention} actualizó el enlace de descarga.",
            color=COLOR_OK,
            fields=[("🔗 Nuevo enlace", nuevo_enlace, False)],
        )
        await log_channel.send(embed=log_embed)

    avisados = 0
    if avisar is None or avisar.value == "si":
        allowed_ids = set(cfg.get("roles_enlace", []))
        if Config.INSIDER_ROLE_ID:
            allowed_ids.add(Config.INSIDER_ROLE_ID)
        if Config.NEXUS_PLUS_ROLE_ID:
            allowed_ids.add(Config.NEXUS_PLUS_ROLE_ID)
            
        notificados = set()
        for role_id in allowed_ids:
            role = interaction.guild.get_role(int(role_id))
            if role is None:
                continue
            for member in role.members:
                if member.id in notificados or member.bot:
                    continue
                notificados.add(member.id)
                try:
                    dm_embed = build_embed(
                        title="🚀 Nueva Actualización Disponible",
                        description=( 
                            f"✨ ¡Hola! Se ha lanzado una nueva versión/enlace de descarga para **Nexus** en **{interaction.guild.name}**.\n"
                            f"🥀 Gracias por ser parte de Insider o Nexus+."
                        ),
                        color=Config.COLOR_SUCCESS,
                        fields=[("🔗 Enlace de descarga", nuevo_enlace, False)]
                    )
                    await member.send(embed=dm_embed)
                    avisados += 1
                except:
                    pass

    embed = build_embed(
        title="✅ Enlace actualizado",
        description=f"El enlace de descarga ha sido actualizado correctamente.",
        color=COLOR_OK,
        fields=[
            ("🔗 Nuevo enlace", nuevo_enlace, False),
            ("📨 Usuarios avisados", str(avisados), True),
        ],
    )
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="config-logs", description="[Admin] Configura los canales de log del servidor.")
@app_commands.describe(
    tipo="Tipo de evento a configurar",
    canal="Canal donde se enviarán los logs (déjalo vacío para desactivar)"
)
@app_commands.choices(tipo=[
    app_commands.Choice(name="Enlace", value="enlace"),
    app_commands.Choice(name="Mensajes editados", value="editados"),
    app_commands.Choice(name="Mensajes borrados", value="borrados"),
    app_commands.Choice(name="Aceptaciones de términos", value="aceptaciones"),
    app_commands.Choice(name="Bienvenidas/Despedidas", value="bienvenidas"),
    app_commands.Choice(name="Moderación", value="moderacion"),
])
@app_commands.checks.has_permissions(administrator=True)
async def config_logs(
    interaction: discord.Interaction,
    tipo: app_commands.Choice[str],
    canal: discord.TextChannel | None = None
):
    track_command(interaction.guild_id, "config-logs")
    cfg = get_guild_config(interaction.guild_id)
    cfg["log_channels"][tipo.value] = canal.id if canal else None
    update_guild_config(interaction.guild_id, cfg)

    embed = build_embed(
        title="✅ Canal de logs actualizado",
        description=(
            f"Los logs de **{tipo.name}** ahora se enviarán a {canal.mention}."
            if canal else
            f"Los logs de **{tipo.name}** han sido desactivados."
        ),
        color=COLOR_OK,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="config-rol-enlace", description="[Admin] Agrega o quita roles autorizados para /enlace.")
@app_commands.describe(
    accion="Agregar o quitar el rol",
    rol="Rol a agregar/quitar"
)
@app_commands.choices(accion=[
    app_commands.Choice(name="Agregar", value="agregar"),
    app_commands.Choice(name="Quitar", value="quitar"),
])
@app_commands.checks.has_permissions(administrator=True)
async def config_rol_enlace(
    interaction: discord.Interaction,
    accion: app_commands.Choice[str],
    rol: discord.Role
):
    track_command(interaction.guild_id, "config-rol-enlace")
    cfg = get_guild_config(interaction.guild_id)
    roles = set(cfg.get("roles_enlace", []))

    if accion.value == "agregar":
        if rol.id in roles:
            embed = build_embed(
                title="ℹ️ Este rol ya está autorizado",
                color=COLOR_AMBER,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        roles.add(rol.id)
        titulo = "✅ Rol agregado"
        descripcion = f"El rol {rol.mention} ahora puede usar `/enlace`."
    else:
        if rol.id not in roles:
            embed = build_embed(
                title="ℹ️ Este rol no estaba autorizado",
                color=COLOR_AMBER,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        roles.discard(rol.id)
        titulo = "✅ Rol quitado"
        descripcion = f"El rol {rol.mention} ya no puede usar `/enlace`."

    cfg["roles_enlace"] = list(roles)
    update_guild_config(interaction.guild_id, cfg)

    embed = build_embed(title=titulo, description=descripcion, color=COLOR_OK)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ──────────────────────────────────────────────────────────────
#  CONFIG-WELCOME / CONFIG-DESPEDIDA / TEST-WELCOME / TEST-DESPEDIDA
#  (FIX: comandos que faltaban — sin ellos, /test-welcome y
#  /test-despedida fallaban con "Ocurrió un error" porque el bot
#  no tenía ningún handler registrado con esos nombres, y tampoco
#  había forma de activar welcome_enabled/farewell_enabled)
# ──────────────────────────────────────────────────────────────

@bot.tree.command(name="config-welcome", description="[Admin] Configura el sistema de bienvenidas.")
@app_commands.describe(
    activado="¿Activar el sistema de bienvenidas?",
    canal="Canal donde se enviarán las bienvenidas",
    mensaje="Mensaje de bienvenida (usa {mention}, {server}, {membercount}, etc.)",
    banner="URL de una imagen para el banner (opcional)"
)
@app_commands.choices(activado=[
    app_commands.Choice(name="Sí, activar", value="si"),
    app_commands.Choice(name="No, desactivar", value="no"),
])
@app_commands.checks.has_permissions(administrator=True)
async def config_welcome(
    interaction: discord.Interaction,
    activado: app_commands.Choice[str] | None = None,
    canal: discord.TextChannel | None = None,
    mensaje: str | None = None,
    banner: str | None = None
):
    track_command(interaction.guild_id, "config-welcome")
    cfg = get_guild_config(interaction.guild_id)

    if activado is not None:
        cfg["welcome_enabled"] = activado.value == "si"
    if canal is not None:
        cfg["welcome_channel"] = canal.id
    if mensaje is not None:
        cfg["welcome_message"] = mensaje
    if banner is not None:
        cfg["welcome_banner"] = banner

    update_guild_config(interaction.guild_id, cfg)

    embed = build_embed(
        title="✅ Configuración de bienvenidas actualizada",
        color=COLOR_OK,
        fields=[
            ("Estado", "✅ Activado" if cfg.get("welcome_enabled") else "❌ Desactivado", True),
            ("Canal", f"<#{cfg['welcome_channel']}>" if cfg.get("welcome_channel") else "No configurado", True),
            ("Mensaje", cfg.get("welcome_message", ""), False),
        ],
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="config-despedida", description="[Admin] Configura el sistema de despedidas.")
@app_commands.describe(
    activado="¿Activar el sistema de despedidas?",
    canal="Canal donde se enviarán las despedidas",
    mensaje="Mensaje de despedida (usa {username}, {server}, {membercount}, etc.)",
    banner="URL de una imagen para el banner (opcional)"
)
@app_commands.choices(activado=[
    app_commands.Choice(name="Sí, activar", value="si"),
    app_commands.Choice(name="No, desactivar", value="no"),
])
@app_commands.checks.has_permissions(administrator=True)
async def config_despedida(
    interaction: discord.Interaction,
    activado: app_commands.Choice[str] | None = None,
    canal: discord.TextChannel | None = None,
    mensaje: str | None = None,
    banner: str | None = None
):
    track_command(interaction.guild_id, "config-despedida")
    cfg = get_guild_config(interaction.guild_id)

    if activado is not None:
        cfg["farewell_enabled"] = activado.value == "si"
    if canal is not None:
        cfg["farewell_channel"] = canal.id
    if mensaje is not None:
        cfg["farewell_message"] = mensaje
    if banner is not None:
        cfg["farewell_banner"] = banner

    update_guild_config(interaction.guild_id, cfg)

    embed = build_embed(
        title="✅ Configuración de despedidas actualizada",
        color=COLOR_OK,
        fields=[
            ("Estado", "✅ Activado" if cfg.get("farewell_enabled") else "❌ Desactivado", True),
            ("Canal", f"<#{cfg['farewell_channel']}>" if cfg.get("farewell_channel") else "No configurado", True),
            ("Mensaje", cfg.get("farewell_message", ""), False),
        ],
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="test-welcome", description="[Admin] Previsualiza el mensaje de bienvenida.")
@app_commands.checks.has_permissions(administrator=True)
async def test_welcome(interaction: discord.Interaction):
    track_command(interaction.guild_id, "test-welcome")
    cfg = get_guild_config(interaction.guild_id)
    member = interaction.user  # Member en contexto de servidor

    mensaje = fill_placeholders(cfg.get("welcome_message", ""), member, interaction.guild)
    embed = build_embed(
        title=f"🎉 ¡Nuevo miembro en {interaction.guild.name}! (vista previa)",
        description=mensaje,
        color=COLOR_AMBER,
        thumbnail=member.display_avatar.url,
        image=cfg.get("welcome_banner"),
        footer=f"Miembro #{interaction.guild.member_count} · Vista previa",
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="test-despedida", description="[Admin] Previsualiza el mensaje de despedida.")
@app_commands.checks.has_permissions(administrator=True)
async def test_despedida(interaction: discord.Interaction):
    track_command(interaction.guild_id, "test-despedida")
    cfg = get_guild_config(interaction.guild_id)
    member = interaction.user

    mensaje = fill_placeholders(cfg.get("farewell_message", ""), member, interaction.guild)
    embed = build_embed(
        title=f"👋 Alguien se fue de {interaction.guild.name} (vista previa)",
        description=mensaje,
        color=discord.Color.dark_grey(),
        thumbnail=member.display_avatar.url,
        image=cfg.get("farewell_banner"),
        footer="Vista previa",
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="ver-config", description="[Admin] Muestra la configuración actual del bot.")
@app_commands.checks.has_permissions(administrator=True)
async def ver_config(interaction: discord.Interaction):
    track_command(interaction.guild_id, "ver-config")
    cfg = get_guild_config(interaction.guild_id)
    guild = interaction.guild

    def canal_str(cid):
        if not cid:
            return "No configurado"
        ch = guild.get_channel(int(cid))
        return ch.mention if ch else f"`{cid}` (no encontrado)"

    log_lines = [f"• {tipo.capitalize()}: {canal_str(cid)}" for tipo, cid in cfg["log_channels"].items()]

    roles_lines = []
    for rid in cfg.get("roles_enlace", []):
        role = guild.get_role(int(rid))
        roles_lines.append(role.mention if role else f"`{rid}` (no encontrado)")

    embed = build_embed(
        title="⚙️ Configuración actual",
        color=COLOR_MAIN,
        fields=[
            ("🔗 Enlace actual", cfg.get("current_link") or "No configurado", False),
            ("📋 Canales de log", "\n".join(log_lines), False),
            ("🔑 Roles autorizados (/enlace)", ", ".join(roles_lines) if roles_lines else "Ninguno", False),
            (
                "👋 Bienvenidas",
                f"Estado: {'✅ Activado' if cfg.get('welcome_enabled') else '❌ Desactivado'}\n"
                f"Canal: {canal_str(cfg.get('welcome_channel'))}",
                True,
            ),
            (
                "👋 Despedidas",
                f"Estado: {'✅ Activado' if cfg.get('farewell_enabled') else '❌ Desactivado'}\n"
                f"Canal: {canal_str(cfg.get('farewell_channel'))}",
                True,
            ),
        ],
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ──────────────────────────────────────────────────────────────
#  UTILIDAD GENERAL
# ──────────────────────────────────────────────────────────────

@bot.tree.command(name="ping", description="Muestra la latencia del bot.")
async def ping(interaction: discord.Interaction):
    track_command(interaction.guild_id, "ping")
    latencia = round(bot.latency * 1000)
    uptime = format_uptime(time.time() - BOT_START_TIME)

    embed = build_embed(
        title="🏓 Pong!",
        color=COLOR_OK,
        fields=[
            ("📶 Latencia", f"{latencia}ms", True),
            ("⏱️ Uptime", uptime, True),
        ],
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="say", description="[Admin] Hace que el bot envíe un mensaje.")
@app_commands.describe(
    mensaje="Mensaje a enviar",
    canal="Canal donde enviar el mensaje (opcional, por defecto el actual)"
)
@app_commands.checks.has_permissions(administrator=True)
async def say(interaction: discord.Interaction, mensaje: str, canal: discord.TextChannel | None = None):
    track_command(interaction.guild_id, "say")
    destino = canal or interaction.channel
    mensaje = mensaje.replace("\\n", "\n")

    try:
        await destino.send(mensaje)
        embed = build_embed(
            title="✅ Mensaje enviado",
            description=f"Mensaje enviado en {destino.mention}.",
            color=COLOR_OK,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
    except discord.Forbidden:
        embed = build_embed(
            title="❌ Sin permisos",
            description=f"No tengo permisos para enviar mensajes en {destino.mention}.",
            color=COLOR_WARN,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="server-info", description="Muestra información general del servidor.")
async def server_info(interaction: discord.Interaction):
    track_command(interaction.guild_id, "server-info")
    guild = interaction.guild

    online = sum(1 for m in guild.members if m.status != discord.Status.offline)
    bots = sum(1 for m in guild.members if m.bot)
    humanos = guild.member_count - bots

    embed = discord.Embed(
        title=f"📊 Información de {guild.name}",
        color=COLOR_MAIN,
        timestamp=datetime.now(timezone.utc),
    )
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)

    embed.add_field(name="👑 Dueño", value=f"<@{guild.owner_id}>", inline=True)
    embed.add_field(name="🆔 ID", value=f"`{guild.id}`", inline=True)
    embed.add_field(name="📅 Creado", value=f"<t:{int(guild.created_at.timestamp())}:R>", inline=True)
    embed.add_field(name="👥 Miembros", value=str(guild.member_count), inline=True)
    embed.add_field(name="🧑 Humanos", value=str(humanos), inline=True)
    embed.add_field(name="🤖 Bots", value=str(bots), inline=True)
    embed.add_field(name="🟢 En línea (aprox.)", value=str(online), inline=True)
    embed.add_field(name="💬 Canales de texto", value=str(len(guild.text_channels)), inline=True)
    embed.add_field(name="🔊 Canales de voz", value=str(len(guild.voice_channels)), inline=True)
    embed.add_field(name="🎭 Roles", value=str(len(guild.roles)), inline=True)
    embed.add_field(name="😀 Emojis", value=str(len(guild.emojis)), inline=True)
    embed.add_field(name="🚀 Nivel de boost", value=f"Nivel {guild.premium_tier} ({guild.premium_subscription_count} boosts)", inline=True)

    embed.set_footer(text="Nexus System")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="buscar", description="Busca miembros por nombre.")
@app_commands.describe(nombre="Nombre o parte del nombre a buscar")
async def buscar(interaction: discord.Interaction, nombre: str):
    track_command(interaction.guild_id, "buscar")
    nombre_lower = nombre.lower()

    resultados = [
        m for m in interaction.guild.members
        if nombre_lower in m.display_name.lower() or nombre_lower in str(m).lower()
    ][:25]

    if not resultados:
        embed = build_embed(
            title="🔍 Sin resultados",
            description=f"No se encontraron miembros que coincidan con `{nombre}`.",
            color=COLOR_AMBER,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    lines = [f"• {m.mention} — `{m}` (ID: {m.id})" for m in resultados]
    embed = build_embed(
        title=f"🔍 Resultados para '{nombre}'",
        description="\n".join(lines),
        color=COLOR_MAIN,
        footer=f"{len(resultados)} resultado(s) mostrados (máx. 25)",
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


class AvatarDownloadView(discord.ui.View):
    """Vista con botones para descargar el avatar en distintos formatos."""

    def __init__(self, avatar_asset: discord.Asset, server_avatar_asset: discord.Asset | None = None):
        super().__init__(timeout=None)
        big = avatar_asset.with_size(1024)

        try:
            png_url = big.with_format("png").url
        except ValueError:
            png_url = big.url
        try:
            jpg_url = big.with_format("jpg").url
        except ValueError:
            jpg_url = big.url

        self.add_item(discord.ui.Button(label="Descargar PNG", style=discord.ButtonStyle.link, url=png_url, emoji="🖼️"))
        self.add_item(discord.ui.Button(label="Descargar JPG", style=discord.ButtonStyle.link, url=jpg_url, emoji="📷"))

        if server_avatar_asset is not None:
            self.add_item(discord.ui.Button(label="Avatar del servidor", style=discord.ButtonStyle.link, url=server_avatar_asset.with_size(1024).url, emoji="🏠"))


@bot.tree.command(name="avatar", description="Muestra el avatar de un usuario.")
@app_commands.describe(usuario="Usuario del que ver el avatar (opcional)")
async def avatar(interaction: discord.Interaction, usuario: discord.Member | None = None):
    track_command(interaction.guild_id, "avatar")
    usuario = usuario or interaction.user

    global_avatar = usuario.avatar or usuario.default_avatar
    server_avatar = usuario.guild_avatar if isinstance(usuario, discord.Member) else None

    embed = discord.Embed(
        title=f"🖼️ Avatar de {usuario.display_name}",
        color=usuario.color if getattr(usuario, "color", None) and usuario.color.value else COLOR_MAIN,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_author(name=str(usuario), icon_url=usuario.display_avatar.url)
    embed.set_image(url=(server_avatar or global_avatar).with_size(1024).url)
    embed.set_footer(text=BOT_FOOTER_TEXT, icon_url=interaction.guild.icon.url if interaction.guild and interaction.guild.icon else None)

    view = AvatarDownloadView(global_avatar, server_avatar)
    await interaction.response.send_message(embed=embed, view=view)



@bot.tree.command(name="user-info", description="Muestra información de un usuario.")
@app_commands.describe(usuario="Usuario a consultar (opcional)")
async def user_info(interaction: discord.Interaction, usuario: discord.Member | None = None):
    track_command(interaction.guild_id, "user-info")
    usuario = usuario or interaction.user

    roles = [r.mention for r in usuario.roles if r.name != "@everyone"]

    embed = discord.Embed(
        title=f"👤 Información de {usuario}",
        color=usuario.color if usuario.color.value else COLOR_MAIN,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_thumbnail(url=usuario.display_avatar.url)
    embed.add_field(name="🆔 ID", value=f"`{usuario.id}`", inline=True)
    embed.add_field(name="📛 Apodo", value=usuario.nick or "Ninguno", inline=True)
    embed.add_field(name="🤖 Bot", value="Sí" if usuario.bot else "No", inline=True)
    embed.add_field(name="📅 Cuenta creada", value=f"<t:{int(usuario.created_at.timestamp())}:R>", inline=True)
    embed.add_field(
        name="📥 Se unió al servidor",
        value=f"<t:{int(usuario.joined_at.timestamp())}:R>" if usuario.joined_at else "Desconocido",
        inline=True,
    )
    embed.add_field(name="🔇 Muteado", value="Sí" if is_user_muted(interaction.guild_id, usuario.id) else "No", inline=True)
    embed.add_field(name=f"🎭 Roles ({len(roles)})", value=", ".join(roles) if roles else "Ninguno", inline=False)

    embed.set_footer(text="Nexus System")
    await interaction.response.send_message(embed=embed)

# ──────────────────────────────────────────────────────────────
#  COMANDO: /precio-nexus-pro  (versión privada)
# ──────────────────────────────────────────────────────────────

@bot.tree.command(name="precio-nexus-pro", description="Muestra los precios de Nexus Pro en USD, COP, BRL y ARS.")
@app_commands.checks.cooldown(1, 10.0)
async def precio_nexus_pro(interaction: discord.Interaction):
    track_command(interaction.guild_id, "precio-nexus-pro")
    
    # Tasas de cambio aproximadas (actualizar periódicamente)
    USD_TO_COP = 4000
    USD_TO_BRL = 5.50
    USD_TO_ARS = 950
    
    # Definir precios en USD
    precios = {
        "5 días": 3.00,
        "1 semana": 5.00,
        "1 mes": 7.00,
        "3 meses": 11.00,
        "6 meses": 17.00,
        "12 meses": 25.00,
        "De por vida": 50.00,
    }
    
    # Construir la tabla de precios
    lines = []
    for plan, usd in precios.items():
        cop = usd * USD_TO_COP
        brl = usd * USD_TO_BRL
        ars = usd * USD_TO_ARS
        lines.append(
            f"**{plan}**\n"
            f"└ 🇺🇸 ${usd:.2f} USD · 🇨🇴 ${cop:,.0f} COP · 🇧🇷 R${brl:.2f} BRL · 🇦🇷 ${ars:,.0f} ARS"
        )
    
    tabla_precios = "\n\n".join(lines)
    
    embed = build_embed(
        title="💰 Nexus Pro · Precios y Planes",
        description=(
            "**Nexus Pro** es la versión premium de Nexus con todas las funciones avanzadas.\n\n"
            "🔹 **Beneficios incluidos:**\n"
            "• Navegación ultra rápida y segura\n"
            "• Protección contra rastreo avanzada\n"
            "• Actualizaciones prioritarias\n"
            "• Soporte técnico 24/7\n"
            "• Funciones exclusivas Pro\n\n"
            "📌 **Métodos de pago:**\n"
            "• Tarjeta de crédito/débito\n"
            "• PayPal\n"
            "• Criptomonedas (BTC, ETH, USDT)\n"
            "• Transferencia bancaria\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n"
            "**📋 TABLA DE PRECIOS**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━"
        ),
        color=discord.Color.gold(),
        fields=[
            ("💎 Planes disponibles", tabla_precios, False),
        ],
        footer="Nexus Pro · Precios sujetos a cambios sin previo aviso",
        thumbnail="https://i.imgur.com/ejemplo.png",  # Cambiar por logo de Nexus Pro
    )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ──────────────────────────────────────────────────────────────
#  COMANDO: /precio-nexus-pro-public  (versión pública)
# ──────────────────────────────────────────────────────────────

@bot.tree.command(name="precio-nexus-pro-public", description="[Público] Muestra los precios de Nexus Pro en el canal.")
@app_commands.checks.cooldown(1, 30.0)
async def precio_nexus_pro_public(interaction: discord.Interaction):
    track_command(interaction.guild_id, "precio-nexus-pro-public")
    
    USD_TO_COP = 4000
    USD_TO_BRL = 5.50
    USD_TO_ARS = 950
    
    precios = {
        "5 días": 3.00,
        "1 semana": 5.00,
        "1 mes": 7.00,
        "3 meses": 11.00,
        "6 meses": 17.00,
        "12 meses": 25.00,
        "De por vida": 50.00,
    }
    
    lines = []
    for plan, usd in precios.items():
        cop = usd * USD_TO_COP
        brl = usd * USD_TO_BRL
        ars = usd * USD_TO_ARS
        lines.append(
            f"**{plan}**\n"
            f"└ 🇺🇸 ${usd:.2f} USD · 🇨🇴 ${cop:,.0f} COP · 🇧🇷 R${brl:.2f} BRL · 🇦🇷 ${ars:,.0f} ARS"
        )
    
    tabla_precios = "\n\n".join(lines)
    
    embed = build_embed(
        title="💰 Nexus Pro · Precios y Planes",
        description=(
            "**Nexus Pro** está disponible en varios planes para adaptarse a tus necesidades.\n\n"
            "🔹 **Beneficios:** Navegación ultra rápida, protección avanzada, "
            "soporte 24/7 y funciones exclusivas.\n\n"
            "📌 **Aceptamos:** Tarjeta, PayPal, Cripto y Transferencia bancaria.\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n"
            "**📋 TABLA DE PRECIOS**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━"
        ),
        color=discord.Color.gold(),
        fields=[
            ("💎 Planes disponibles", tabla_precios, False),
            ("🔄 Conversión", "Tasas de cambio aproximadas:\n🇺🇸 1 USD = 🇨🇴 4,000 COP = 🇧🇷 R$5.50 = 🇦🇷 $950 ARS", False),
        ],
        footer="Nexus Pro · Precios sujetos a cambios",
    )
    
    await interaction.response.send_message(embed=embed)


# ──────────────────────────────────────────────────────────────
#  COMANDO: /nexus-free  (versión privada)
# ──────────────────────────────────────────────────────────────

@bot.tree.command(name="nexus-free", description="Muestra el enlace de descarga de Nexus Core (versión Free).")
@app_commands.checks.cooldown(1, 10.0)
async def nexus_free(interaction: discord.Interaction):
    track_command(interaction.guild_id, "nexus-free")
    
    LINK_NEXUS_FREE = "https://github.com/santhiagocaro05-debug/NEXUS-INSTALLER/releases/download/installer/Nexus-core-Setup-1.0.0-V3.exe"
    
    record_log(
        interaction.guild_id, 
        "enlace", 
        f"{interaction.user} solicitó el enlace de Nexus Core Free.", 
        str(interaction.user)
    )
    
    embed = build_embed(
        title="📥 Nexus Core · Versión Free",
        description=(
            "**Nexus Core** es la versión gratuita de Nexus Pro.\n\n"
            "⚡ **Características:**\n"
            "• Navegación Fluida\n"
            "• Funciones basicas\n"
            "• Actualizaciones\n\n"
            "📌 **Nota:** Esta versión es totalmente gratuita y no requiere clave de activación."
        ),
        color=discord.Color.from_rgb(0, 200, 255),
        fields=[
            ("🔗 Enlace de descarga", LINK_NEXUS_FREE, False),
            ("📦 Versión", "1.0.0 V3", True),
            ("💾 Tamaño", "~1.5 GB", True),
            ("🔄 Estado", "✅ Estable", True),
        ],
        footer="Nexus Core · Versión Gratuita",
    )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ──────────────────────────────────────────────────────────────
#  COMANDO: /nexus-free-public  (versión pública)
# ──────────────────────────────────────────────────────────────

@bot.tree.command(name="nexus-free-public", description="[Público] Muestra el enlace de Nexus Core Free en el canal.")
@app_commands.checks.cooldown(1, 30.0)
async def nexus_free_public(interaction: discord.Interaction):
    track_command(interaction.guild_id, "nexus-free-public")
    
    LINK_NEXUS_FREE = "https://github.com/santhiagocaro05-debug/NEXUS-INSTALLER/releases/download/installer/Nexus-core-Setup-1.0.0-V3.exe"
    
    record_log(
        interaction.guild_id, 
        "enlace", 
        f"{interaction.user} solicitó el enlace de Nexus Core Free (público).", 
        str(interaction.user)
    )
    
    embed = build_embed(
        title="📥 Nexus Core · Versión Free",
        description=(
            "¡Nexus Core Free ya está disponible para todos!\n\n"
            "Descarga la versión gratuita de Nexus Core y empieza a Manejar tu PC "
            "con privacidad y velocidad y automatizacion.\n\n"
            f"🔗 **Enlace:** {LINK_NEXUS_FREE}"
        ),
        color=discord.Color.from_rgb(0, 200, 255),
        footer="Nexus Core · Gratuito para siempre",
    )
    
    await interaction.response.send_message(embed=embed)


# ──────────────────────────────────────────────────────────────
#  COMANDO: /pagina-web  (página oficial)
# ──────────────────────────────────────────────────────────────

@bot.tree.command(name="pagina-web", description="🌐 Visita la página web oficial de Nexus Pro.")
@app_commands.checks.cooldown(1, 10.0)
async def pagina_web(interaction: discord.Interaction):
    track_command(interaction.guild_id, "pagina-web")
    
    URL_WEB = "https://proyect-nexus.vercel.app"
    
    record_log(
        interaction.guild_id, 
        "enlace", 
        f"{interaction.user} visitó la página web de Nexus Pro.", 
        str(interaction.user)
    )
    
    embed = build_embed(
        title="🌐 Nexus Pro · Página Web Oficial",
        description=(
            "**Descubre todo sobre Nexus Pro en nuestra web oficial.**\n\n"
            "🔹 **Características principales:**\n"
            "• Navegación ultra rápida y segura\n"
            "• Protección avanzada contra rastreo\n"
            "• Interfaz intuitiva y personalizable\n"
            "• Actualizaciones constantes\n"
            "• Soporte técnico 24/7\n\n"
            f"🌐 **Visita nuestra web:** {URL_WEB}"
        ),
        color=discord.Color.from_rgb(88, 101, 242),
        timestamp=datetime.now(timezone.utc)
    )
    
    embed.set_footer(text="Nexus Pro · Haz clic en el botón para visitar la web")
    
    view = discord.ui.View()
    view.add_item(
        discord.ui.Button(
            label="🌐 Visitar Web Oficial",
            url=URL_WEB,
            style=discord.ButtonStyle.link,
            emoji="🌐"
        )
    )
    view.add_item(
        discord.ui.Button(
            label="📥 Descargar Nexus Pro",
            style=discord.ButtonStyle.success,
            emoji="📥",
            custom_id="descargar_nexus"
        )
    )
    
    async def descargar_button(interaction: discord.Interaction):
        if has_enlace_role(interaction.user, interaction.guild_id):
            await interaction.response.send_message(
                "🔗 Usa `/enlace` para obtener el enlace de descarga de Nexus Pro.",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"🔒 Necesitas el rol **{Config.INSIDER_ROLE_NAME}** o **{Config.VIP_ROLE_NAME}** para acceder al enlace de descarga.",
                ephemeral=True
            )
    
    for item in view.children:
        if hasattr(item, 'custom_id') and item.custom_id == "descargar_nexus":
            item.callback = descargar_button
    
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# ──────────────────────────────────────────────────────────────
#  SISTEMA DE ENCUESTAS Y VOTACIONES PROFESIONAL
# ──────────────────────────────────────────────────────────────

import asyncio
from datetime import datetime, timedelta

# ──────────────────────────────────────────────────────────────
#  PERSISTENCIA DE ENCUESTAS
# ──────────────────────────────────────────────────────────────

POLLS_PATH = "polls.json"

def load_polls() -> dict:
    """Carga las encuestas guardadas."""
    if not os.path.exists(POLLS_PATH):
        return {}
    with open(POLLS_PATH, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def save_polls(data: dict) -> None:
    """Guarda las encuestas."""
    with open(POLLS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def create_poll(
    guild_id: int,
    author_id: int,
    question: str,
    options: list[str],
    duration: int = 60,
    anonymous: bool = False,
    multiple_choice: bool = False,
    max_votes: int = 1
) -> dict:
    """Crea una nueva encuesta."""
    poll_id = str(int(datetime.now().timestamp() * 1000))
    
    poll_data = {
        "id": poll_id,
        "author_id": author_id,
        "question": question,
        "options": options,
        "duration": duration,
        "anonymous": anonymous,
        "multiple_choice": multiple_choice,
        "max_votes": max_votes,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=duration)).isoformat(),
        "votes": {str(i): [] for i in range(len(options))},
        "voters": [],
        "total_votes": 0,
        "finished": False
    }
    
    data = load_polls()
    key = str(guild_id)
    if key not in data:
        data[key] = {}
    data[key][poll_id] = poll_data
    save_polls(data)
    
    return poll_data

def get_poll(guild_id: int, poll_id: str) -> dict | None:
    """Obtiene una encuesta por ID."""
    data = load_polls()
    key = str(guild_id)
    if key not in data:
        return None
    return data[key].get(poll_id)

def update_poll(guild_id: int, poll_id: str, poll_data: dict) -> None:
    """Actualiza una encuesta."""
    data = load_polls()
    key = str(guild_id)
    if key in data:
        data[key][poll_id] = poll_data
        save_polls(data)

def delete_poll(guild_id: int, poll_id: str) -> bool:
    """Elimina una encuesta."""
    data = load_polls()
    key = str(guild_id)
    if key in data and poll_id in data[key]:
        del data[key][poll_id]
        save_polls(data)
        return True
    return False

def get_guild_polls(guild_id: int) -> list:
    """Obtiene todas las encuestas de un servidor."""
    data = load_polls()
    key = str(guild_id)
    if key not in data:
        return []
    return list(data[key].values())

def has_voted(poll_data: dict, user_id: int) -> bool:
    """Verifica si un usuario ya votó."""
    return str(user_id) in poll_data.get("voters", [])

def add_vote(poll_data: dict, user_id: int, option_index: int) -> bool:
    """Añade un voto a una opción."""
    if has_voted(poll_data, user_id) and not poll_data.get("multiple_choice", False):
        return False
    
    option_key = str(option_index)
    if option_key not in poll_data["votes"]:
        return False
    
    # Si es opción múltiple, verificar límite
    if poll_data.get("multiple_choice", False):
        user_votes = sum(1 for votes in poll_data["votes"].values() if str(user_id) in votes)
        if user_votes >= poll_data.get("max_votes", 1):
            return False
    
    # Añadir voto
    poll_data["votes"][option_key].append(str(user_id))
    if not poll_data.get("multiple_choice", False):
        poll_data["voters"].append(str(user_id))
    poll_data["total_votes"] += 1
    
    return True

def get_results(poll_data: dict) -> list[tuple[str, int, float]]:
    """Obtiene los resultados de una encuesta."""
    total = poll_data["total_votes"]
    results = []
    
    for i, option in enumerate(poll_data["options"]):
        votes = len(poll_data["votes"][str(i)])
        percentage = (votes / total * 100) if total > 0 else 0
        results.append((option, votes, percentage))
    
    return results

def get_voter_list(poll_data: dict, option_index: int) -> list[str]:
    """Obtiene la lista de votantes de una opción."""
    return poll_data["votes"].get(str(option_index), [])

# ──────────────────────────────────────────────────────────────
#  VISTAS DE ENCUESTAS
# ──────────────────────────────────────────────────────────────

class PollView(discord.ui.View):
    """Vista interactiva para encuestas."""
    
    def __init__(self, poll_data: dict, guild_id: int, poll_message: discord.Message):
        super().__init__(timeout=poll_data.get("duration", 60))
        self.poll_data = poll_data
        self.guild_id = guild_id
        self.poll_message = poll_message
        self._add_buttons()
    
    def _add_buttons(self):
        """Añade botones para cada opción."""
        for i, option in enumerate(self.poll_data["options"]):
            emoji = self._get_emoji(i)
            button = discord.ui.Button(
                label=option[:80],  # Limitar longitud
                style=discord.ButtonStyle.secondary,
                custom_id=f"poll_{self.poll_data['id']}_{i}",
                emoji=emoji
            )
            button.callback = self.create_callback(i)
            self.add_item(button)
    
    def _get_emoji(self, index: int) -> str:
        """Obtiene un emoji para cada opción."""
        emojis = ["🇦", "🇧", "🇨", "🇩", "🇪", "🇫", "🇬", "🇭", "🇮", "🇯", "🇰", "🇱", "🇲", "🇳", "🇴", "🇵", "🇶", "🇷", "🇸", "🇹"]
        return emojis[index % len(emojis)]
    
    def create_callback(self, option_index: int):
        """Crea el callback para un botón."""
        async def callback(interaction: discord.Interaction):
            # Verificar que no sea el creador (opcional)
            # if interaction.user.id == self.poll_data["author_id"]:
            #     await interaction.response.send_message("❌ El creador de la encuesta no puede votar.", ephemeral=True)
            #     return
            
            # Verificar si la encuesta ya terminó
            if self.poll_data.get("finished", False):
                await interaction.response.send_message("❌ Esta encuesta ya ha finalizado.", ephemeral=True)
                return
            
            # Verificar si ya expiró
            expires = self.poll_data.get("expires_at")
            if expires and datetime.now(timezone.utc) > datetime.fromisoformat(expires):
                await self.finish_poll()
                await interaction.response.send_message("❌ Esta encuesta ya ha expirado.", ephemeral=True)
                return
            
            # Verificar si ya votó
            if not self.poll_data.get("multiple_choice", False) and has_voted(self.poll_data, interaction.user.id):
                await interaction.response.send_message("❌ Ya has votado en esta encuesta.", ephemeral=True)
                return
            
            # Verificar límite de votos en opción múltiple
            if self.poll_data.get("multiple_choice", False):
                user_votes = sum(1 for votes in self.poll_data["votes"].values() if str(interaction.user.id) in votes)
                if user_votes >= self.poll_data.get("max_votes", 1):
                    await interaction.response.send_message(f"❌ Ya has alcanzado el límite de {self.poll_data['max_votes']} votos.", ephemeral=True)
                    return
            
            # Registrar voto
            if add_vote(self.poll_data, interaction.user.id, option_index):
                update_poll(self.guild_id, self.poll_data["id"], self.poll_data)
                
                # Actualizar embed
                embed = self.build_embed()
                await interaction.response.edit_message(embed=embed, view=self)
                
                # Mensaje de confirmación
                if self.poll_data.get("anonymous", False):
                    await interaction.response.send_message("✅ Tu voto ha sido registrado.", ephemeral=True)
                else:
                    await interaction.response.send_message(f"✅ Has votado por: **{self.poll_data['options'][option_index]}**", ephemeral=True)
            else:
                await interaction.response.send_message("❌ No se pudo registrar tu voto.", ephemeral=True)
        
        return callback
    
    def build_embed(self) -> discord.Embed:
        """Construye el embed de la encuesta."""
        results = get_results(self.poll_data)
        total = self.poll_data["total_votes"]
        
        # Título y descripción
        finished = self.poll_data.get("finished", False)
        title = f"🔒 {self.poll_data['question']} (Finalizada)" if finished else f"📊 {self.poll_data['question']}"
        description = f"**Total de votos:** {total}\n\n"
        
        # Barras de progreso
        for i, (option, votes, percentage) in enumerate(results):
            bar = self._create_progress_bar(percentage)
            emoji = self._get_emoji(i)
            
            if self.poll_data.get("anonymous", False):
                description += f"{emoji} **{option}**\n"
                description += f"└ {bar} `{percentage:.1f}%` ({votes} votos)\n\n"
            else:
                voters = get_voter_list(self.poll_data, i)
                voter_mentions = [f"<@{v_id}>" for v_id in voters[:10]]  # Mostrar máximo 10

                voters_text = ", ".join(voter_mentions) if voter_mentions else "*Sin votos aún*"
                if len(voters) > 10:
                    voters_text += f" y {len(voters) - 10} más..."

                description += f"{emoji} **{option}**\n"
                description += f"└ {bar} `{percentage:.1f}%` ({votes} votos)\n"
                if votes > 0:
                    description += f"└ 👤 {voters_text}\n\n"
                else:
                    description += "\n"
        
        # Información adicional
        info = []
        if self.poll_data.get("multiple_choice", False):
            info.append(f"🔀 Opción múltiple (máx. {self.poll_data.get('max_votes', 1)})")
        if self.poll_data.get("anonymous", False):
            info.append("👤 Votos anónimos")
        
        if info:
            description += "━━━━━━━━━━━━━━━━━━━━━━━\n"
            description += " • ".join(info)
        
        # Crear embed
        expires = self.poll_data.get("expires_at")
        color = COLOR_MAIN if not self.poll_data.get("finished", False) else COLOR_WARN
        
        embed = discord.Embed(
            title=title,
            description=description,
            color=color,
            timestamp=datetime.now(timezone.utc) if not self.poll_data.get("finished", False) else None
        )
        
        # Footer
        footer_text = f"ID: {self.poll_data['id']}"
        if expires and not self.poll_data.get("finished", False):
            try:
                dt = datetime.fromisoformat(expires)
                footer_text += f" · Finaliza: <t:{int(dt.timestamp())}:R>"
            except:
                pass
        
        embed.set_footer(text=footer_text)

        return embed
    
    def _create_progress_bar(self, percentage: float, length: int = 12) -> str:
        """Crea una barra de progreso visual."""
        filled = int(percentage / 100 * length)
        empty = length - filled
        return "█" * filled + "░" * empty
    
    async def finish_poll(self):
        """Finaliza la encuesta."""
        self.poll_data["finished"] = True
        update_poll(self.guild_id, self.poll_data["id"], self.poll_data)
        
        # Deshabilitar botones
        for child in self.children:
            child.disabled = True
        
        # Actualizar embed
        embed = self.build_embed()
        await self.poll_message.edit(embed=embed, view=self)
    
    async def on_timeout(self):
        """Cuando la encuesta expira."""
        await self.finish_poll()

# ──────────────────────────────────────────────────────────────
#  COMANDOS DE ENCUESTAS
# ──────────────────────────────────────────────────────────────

@bot.tree.command(name="encuesta", description="📊 Crea una encuesta profesional.")
@app_commands.describe(
    pregunta="La pregunta de la encuesta",
    opcion1="Primera opción",
    opcion2="Segunda opción",
    opcion3="Tercera opción (opcional)",
    opcion4="Cuarta opción (opcional)",
    opcion5="Quinta opción (opcional)",
    duracion="Duración en segundos (mínimo 30, máximo 86400 = 24h)",
    anonimo="¿Votos anónimos?",
    multiple="¿Permitir múltiples votos?",
    max_votos="Máximo de votos por usuario (si multiple = Sí)"
)
@app_commands.choices(
    anonimo=[
        app_commands.Choice(name="Sí, anónimo", value="si"),
        app_commands.Choice(name="No, público", value="no"),
    ],
    multiple=[
        app_commands.Choice(name="Sí, múltiple", value="si"),
        app_commands.Choice(name="No, único voto", value="no"),
    ]
)
@app_commands.checks.has_permissions(manage_messages=True)
@app_commands.checks.cooldown(1, 10.0)
async def encuesta(
    interaction: discord.Interaction,
    pregunta: str,
    opcion1: str,
    opcion2: str,
    opcion3: str | None = None,
    opcion4: str | None = None,
    opcion5: str | None = None,
    duracion: int = 300,
    anonimo: app_commands.Choice[str] | None = None,
    multiple: app_commands.Choice[str] | None = None,
    max_votos: int = 3
):
    track_command(interaction.guild_id, "encuesta")
    
    # Validar duración
    if duracion < 30:
        embed = build_embed(
            title="⏰ Duración inválida",
            description="La duración mínima es de 30 segundos.",
            color=COLOR_WARN,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    if duracion > 86400:
        embed = build_embed(
            title="⏰ Duración inválida",
            description="La duración máxima es de 24 horas (86400 segundos).",
            color=COLOR_WARN,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    # Recolectar opciones
    options = [opcion1, opcion2]
    if opcion3:
        options.append(opcion3)
    if opcion4:
        options.append(opcion4)
    if opcion5:
        options.append(opcion5)
    
    if len(options) < 2:
        embed = build_embed(
            title="❌ Opciones insuficientes",
            description="Debes proporcionar al menos 2 opciones.",
            color=COLOR_WARN,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    # Configurar opciones
    is_anonymous = anonimo and anonimo.value == "si"
    is_multiple = multiple and multiple.value == "si"
    
    if is_multiple and max_votos < 2:
        max_votos = 2
    if is_multiple and max_votos > len(options):
        max_votos = len(options)
    
    # Crear encuesta
    poll_data = create_poll(
        guild_id=interaction.guild_id,
        author_id=interaction.user.id,
        question=pregunta,
        options=options,
        duration=duracion,
        anonymous=is_anonymous,
        multiple_choice=is_multiple,
        max_votes=max_votos if is_multiple else 1
    )
    
    # Crear vista
    view = PollView(poll_data, interaction.guild_id, None)
    
    # Construir embed inicial
    embed = view.build_embed()
    embed.set_author(
        name=f"📊 Encuesta creada por {interaction.user.display_name}",
        icon_url=interaction.user.display_avatar.url
    )
    
    # Enviar mensaje
    await interaction.response.send_message(embed=embed, view=view)
    message = await interaction.original_response()
    view.poll_message = message
    
    # Registrar en logs
    record_log(
        interaction.guild_id,
        "moderacion",
        f"{interaction.user} creó una encuesta: {pregunta[:50]}...",
        str(interaction.user)
    )

@bot.tree.command(name="cerrar-encuesta", description="🔒 Finaliza una encuesta anticipadamente.")
@app_commands.describe(
    poll_id="ID de la encuesta (se muestra en el footer)"
)
@app_commands.checks.has_permissions(manage_messages=True)
async def cerrar_encuesta(interaction: discord.Interaction, poll_id: str):
    track_command(interaction.guild_id, "cerrar-encuesta")
    
    poll_data = get_poll(interaction.guild_id, poll_id)
    if not poll_data:
        embed = build_embed(
            title="❌ Encuesta no encontrada",
            description="No se encontró una encuesta con ese ID.",
            color=COLOR_WARN,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    # Verificar permisos (creador o admin)
    if poll_data["author_id"] != interaction.user.id and not interaction.user.guild_permissions.administrator:
        embed = build_embed(
            title="🔒 Sin permisos",
            description="Solo el creador de la encuesta o un administrador pueden cerrarla.",
            color=COLOR_WARN,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    if poll_data.get("finished", False):
        embed = build_embed(
            title="ℹ️ Encuesta ya finalizada",
            color=COLOR_AMBER,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    # Finalizar encuesta
    poll_data["finished"] = True
    update_poll(interaction.guild_id, poll_id, poll_data)
    
    embed = build_embed(
        title="🔒 Encuesta finalizada",
        description="La encuesta ha sido finalizada anticipadamente.",
        color=COLOR_OK,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="resultados-encuesta", description="📊 Muestra los resultados de una encuesta.")
@app_commands.describe(
    poll_id="ID de la encuesta (se muestra en el footer)"
)
async def resultados_encuesta(interaction: discord.Interaction, poll_id: str):
    track_command(interaction.guild_id, "resultados-encuesta")
    
    poll_data = get_poll(interaction.guild_id, poll_id)
    if not poll_data:
        embed = build_embed(
            title="❌ Encuesta no encontrada",
            description="No se encontró una encuesta con ese ID.",
            color=COLOR_WARN,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    # Construir resultados
    results = get_results(poll_data)
    total = poll_data["total_votes"]
    
    embed = discord.Embed(
        title=f"📊 Resultados: {poll_data['question']}",
        color=COLOR_PURPLE,
        timestamp=datetime.now(timezone.utc)
    )
    
    description = f"**Total de votos:** {total}\n\n"
    
    # Ordenar por votos (descendente)
    sorted_results = sorted(results, key=lambda x: x[1], reverse=True)
    
    for i, (option, votes, percentage) in enumerate(sorted_results):
        bar = "█" * int(percentage / 100 * 15) + "░" * (15 - int(percentage / 100 * 15))
        medal = ["🥇", "🥈", "🥉"][i] if i < 3 else f"{i+1}."
        description += f"{medal} **{option}**\n"
        description += f"└ {bar} `{percentage:.1f}%` ({votes} votos)\n\n"
    
    embed.description = description
    embed.set_footer(
        text=f"ID: {poll_id} · {'Finalizada' if poll_data.get('finished') else 'Activa'}"
    )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="listar-encuestas", description="📋 Muestra todas las encuestas activas del servidor.")
@app_commands.checks.has_permissions(manage_messages=True)
async def listar_encuestas(interaction: discord.Interaction):
    track_command(interaction.guild_id, "listar-encuestas")
    
    polls = get_guild_polls(interaction.guild_id)
    active_polls = [p for p in polls if not p.get("finished", False)]
    
    if not active_polls:
        embed = build_embed(
            title="📋 Encuestas activas",
            description="No hay encuestas activas en este servidor.",
            color=COLOR_AMBER,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    embed = discord.Embed(
        title=f"📋 Encuestas activas ({len(active_polls)})",
        color=COLOR_MAIN,
        timestamp=datetime.now(timezone.utc)
    )
    
    for poll in active_polls[:10]:  # Máximo 10
        total = poll.get("total_votes", 0)
        expires = poll.get("expires_at")
        
        time_str = "⏳"
        if expires:
            try:
                dt = datetime.fromisoformat(expires)
                time_str = f"<t:{int(dt.timestamp())}:R>"
            except:
                time_str = "Desconocido"
        
        options_str = " | ".join([f"`{i+1}. {opt[:20]}{'...' if len(opt) > 20 else ''}`" for i, opt in enumerate(poll["options"])])
        
        embed.add_field(
            name=f"📊 {poll['question'][:50]}{'...' if len(poll['question']) > 50 else ''}",
            value=(
                f"🆔 `{poll['id']}`\n"
                f"📝 Opciones: {options_str}\n"
                f"📊 Votos: {total} | ⏱️ {time_str}"
            ),
            inline=False
        )
    
    embed.set_footer(text="Usa /resultados-encuesta <ID> para ver detalles")
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ──────────────────────────────────────────────────────────────
#  LOGS Y ESTADÍSTICAS
# ──────────────────────────────────────────────────────────────

@bot.tree.command(name="logs", description="[Admin] Muestra el historial de eventos paginado.")
@app_commands.describe(tipo="Tipo de evento a filtrar")
@app_commands.choices(tipo=[
    app_commands.Choice(name="Todos", value="todos"),
    app_commands.Choice(name="Enlace", value="enlace"),
    app_commands.Choice(name="Editados", value="editados"),
    app_commands.Choice(name="Borrados", value="borrados"),
    app_commands.Choice(name="Aceptaciones", value="aceptaciones"),
    app_commands.Choice(name="Bienvenidas", value="bienvenidas"),
    app_commands.Choice(name="Moderación", value="moderacion"),
    app_commands.Choice(name="Errores", value="error"),
])
@app_commands.checks.has_permissions(administrator=True)
async def logs_cmd(interaction: discord.Interaction, tipo: app_commands.Choice[str] | None = None):
    track_command(interaction.guild_id, "logs")
    entries = get_logs(interaction.guild_id, tipo.value if tipo else None)
    pages = build_log_pages(entries)

    if len(pages) > 1:
        view = Paginator(pages, autor_id=interaction.user.id)
        await interaction.response.send_message(embed=pages[0], view=view, ephemeral=True)
    else:
        await interaction.response.send_message(embed=pages[0], ephemeral=True)


@bot.tree.command(name="stats", description="Muestra estadísticas y analíticas del servidor.")
@app_commands.checks.has_permissions(administrator=True)
async def stats_cmd(interaction: discord.Interaction):
    track_command(interaction.guild_id, "stats")
    data = get_guild_stats(interaction.guild_id)

    comandos = data.get("commands_used", {})
    top_comandos = Counter(comandos).most_common(5)
    top_str = "\n".join([f"`/{c}` — {n} usos" for c, n in top_comandos]) or "Sin datos aún"

    uptime = format_uptime(time.time() - BOT_START_TIME)

    embed = build_embed(
        title=f"📊 Estadísticas de {interaction.guild.name}",
        color=COLOR_PURPLE,
        fields=[
            ("✅ Términos aceptados", str(data.get("terms_accepted", 0)), True),
            ("❌ Términos rechazados", str(data.get("terms_rejected", 0)), True),
            ("👥 Ingresos", str(data.get("joins", 0)), True),
            ("👋 Salidas", str(data.get("leaves", 0)), True),
            ("✏️ Mensajes editados", str(data.get("messages_edited", 0)), True),
            ("🗑️ Mensajes borrados", str(data.get("messages_deleted", 0)), True),
            ("🔇 Mutes", str(data.get("mutes", 0)), True),
            ("👢 Kicks", str(data.get("kicks", 0)), True),
            ("🔨 Bans", str(data.get("bans", 0)), True),
            ("⚠️ Errores registrados", str(data.get("errors_logged", 0)), True),
            ("⏱️ Uptime del bot", uptime, True),
            ("🏆 Comandos más usados", top_str, False),
        ],
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ──────────────────────────────────────────────────────────────
#  AYUDA
# ──────────────────────────────────────────────────────────────

@bot.tree.command(name="ayuda", description="Muestra el menú de ayuda con todos los comandos.")
async def ayuda(interaction: discord.Interaction):
    track_command(interaction.guild_id, "ayuda")
    embed = build_embed(
        title="📖 Centro de ayuda · Nexus System",
        description=(
            "Selecciona una categoría en el menú de abajo para ver los comandos disponibles.\n\n"
            "🌐 **General** — comandos para todos\n"
            "🛡️ **Administración** — requieren permisos de admin\n"
            "📊 **Estadísticas** — analíticas del servidor\n"
            "🛡️ **Moderación** — comandos de moderación avanzada"
        ),
        color=COLOR_MAIN,
        footer="Nexus System · Usa el menú para explorar",
    )
    view = HelpView(autor_id=interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


@bot.tree.command(name="ayuda-error", description="Te ayuda a diagnosticar un mensaje de error.")
@app_commands.describe(mensaje_error="Pega aquí el mensaje de error que recibiste")
async def ayuda_error(interaction: discord.Interaction, mensaje_error: str):
    track_command(interaction.guild_id, "ayuda-error")
    texto = mensaje_error.lower()

    coincidencias = []
    for keywords, titulo, solucion in COMMON_ERROR_HINTS:
        if any(kw in texto for kw in keywords):
            coincidencias.append((titulo, solucion))

    if not coincidencias:
        embed = build_embed(
            title="🤔 No se reconoció el error",
            description=(
                "No pude identificar automáticamente este error.\n\n"
                "**Sugerencias generales:**\n"
                "• Revisa que el bot tenga los permisos e intents necesarios.\n"
                "• Verifica que el token y las IDs en tu `.env`/`config.py` sean correctas.\n"
                "• Revisa la consola/logs para más contexto.\n\n"
                f"Mensaje analizado:\n```{mensaje_error[:500]}```"
            ),
            color=COLOR_AMBER,
        )
    else:
        fields = [(titulo, solucion, False) for titulo, solucion in coincidencias]
        embed = build_embed(
            title="🩺 Diagnóstico de error",
            description=f"Se detectaron {len(coincidencias)} posible(s) causa(s):",
            color=COLOR_MAIN,
            fields=fields,
        )

    await interaction.response.send_message(embed=embed, ephemeral=True)

TERMINOS_EMBED_DESCRIPTION = (
    "⚠️ **ESTE DOCUMENTO ES CONFIDENCIAL Y EXCLUSIVO PARA MIEMBROS AUTORIZADOS** ⚠️\n\n"
    "**1. Uso exclusivo de la versión Beta**\n"
    "Nexus Pro se encuentra actualmente en fase **Beta** y **no ha sido "
    "lanzado oficialmente**. Su lanzamiento oficial está previsto para el "
    "**30 de agosto**. Queda estrictamente prohibido distribuir, compartir o "
    "facilitar la aplicación a terceros sin autorización expresa del equipo "
    "de desarrollo de Nexus. El incumplimiento de esta norma podrá dar lugar "
    "a la suspensión del acceso y, cuando corresponda, a las acciones "
    "legales que procedan.\n\n"
    
    "**2. Protección de la integridad de la aplicación**\n"
    "Está totalmente prohibido intentar modificar, descompilar, realizar "
    "ingeniería inversa, crackear, alterar o vulnerar cualquier sistema de "
    "seguridad de Nexus Pro. Asimismo, queda prohibido compartir, "
    "vender o transferir claves de activación (Keys). Cualquier intento de "
    "hacerlo podrá ocasionar el bloqueo permanente del **Hardware ID "
    "(HWID)** y la suspensión de las cuentas involucradas, incluyendo tanto "
    "al usuario que comparte la clave como al que intenta utilizarla.\n\n"
    
    "**3. Incumplimiento de las normas**\n"
    "El incumplimiento de cualquiera de las reglas anteriores podrá dar "
    "lugar a la cancelación inmediata del acceso a Nexus Pro, al "
    "bloqueo permanente del dispositivo y de la cuenta, y, cuando exista "
    "fundamento legal suficiente, al ejercicio de las acciones legales "
    "correspondientes para proteger los derechos del desarrollador.\n\n"
    
    "**4. Protección de la propiedad intelectual**\n"
    "Todo el software, código fuente, diseño, logotipos, interfaz, recursos "
    "gráficos y demás elementos que conforman Nexus Pro son propiedad "
    "de sus respectivos titulares y están protegidos por las leyes de "
    "propiedad intelectual. Queda prohibida su copia, reproducción, "
    "modificación o distribución sin autorización previa y por escrito.\n\n"
    
    "**5. Aceptación de los términos**\n"
    "Al acceder o utilizar Nexus Pro Beta, el usuario declara haber "
    "leído, comprendido y aceptado íntegramente estos términos y "
    "condiciones.\n\n"
    
    "─────────────────────────────\n"
    "🔒 **¿Aceptas estos términos y condiciones?**\n"
    "Usa los botones de abajo para responder."
)

# ──────────────────────────────────────────────────────────────
#  MANEJO DE ERRORES
# ──────────────────────────────────────────────────────────────

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if interaction.guild_id:
        bump_stat(interaction.guild_id, "errors_logged")
        record_log(interaction.guild_id, "error", f"{type(error).__name__}: {error}", str(interaction.user))

    if isinstance(error, app_commands.MissingPermissions):
        embed = build_embed(
            title="🔒 Permisos insuficientes",
            description="No tienes permisos suficientes para usar este comando.",
            color=COLOR_WARN,
        )
    elif isinstance(error, app_commands.CommandOnCooldown):
        embed = build_embed(
            title="⏳ Espera un momento",
            description=f"Vuelve a intentarlo en **{error.retry_after:.1f}s**.",
            color=COLOR_AMBER,
        )
    elif isinstance(error, app_commands.BotMissingPermissions):
        embed = build_embed(
            title="🔒 Al bot le faltan permisos",
            description=f"El bot necesita: `{', '.join(error.missing_permissions)}`.",
            color=COLOR_WARN,
        )
    else:
        print(f"Error en comando: {error}")
        embed = build_embed(
            title="❌ Ocurrió un error",
            description="Usa `/ayuda-error` y pega el mensaje de error para obtener un diagnóstico.",
            color=COLOR_WARN,
        )

    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
    except discord.NotFound:
        # La interacción ya expiró (pasaron más de 15 min, o más de 3s sin defer). No hay nada más que hacer.
        pass
    except discord.HTTPException as e:
        print(f"No se pudo notificar el error al usuario: {e}")


# ──────────────────────────────────────────────────────────────
#  SERVIDOR WEB
# ──────────────────────────────────────────────────────────────





app = Flask(__name__)

@app.route("/")
def home():
    return "Nexus bot está vivo."

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

def keep_alive():
    t = threading.Thread(target=run_web)
    t.daemon = True
    t.start()

# ──────────────────────────────────────────────────────────────
#  PANEL DE ANUNCIOS (/announce)
# ──────────────────────────────────────────────────────────────

class AnnounceModal(discord.ui.Modal):
    """Modal para crear un anuncio profesional."""
    
    def __init__(self, channel: discord.TextChannel):
        super().__init__(title="📢 Crear Anuncio")
        self.channel = channel
        
        self.titulo = discord.ui.TextInput(
            label="Título del anuncio",
            placeholder="Ej: ¡Nueva actualización de Nexus Pro!",
            required=True,
            max_length=100
        )
        self.add_item(self.titulo)
        
        self.mensaje = discord.ui.TextInput(
            label="Contenido del anuncio",
            style=discord.TextStyle.paragraph,
            placeholder="Escribe el contenido del anuncio aquí...",
            required=True,
            max_length=2000
        )
        self.add_item(self.mensaje)
        
        self.color = discord.ui.TextInput(
            label="Color (opcional)",
            placeholder="azul, rojo, verde, dorado, morado, o #RRGGBB",
            required=False,
            max_length=20
        )
        self.add_item(self.color)

        self.imagen = discord.ui.TextInput(
            label="Imagen (URL, opcional)",
            placeholder="https://...",
            required=False,
            max_length=300
        )
        self.add_item(self.imagen)

        self.mencion = discord.ui.TextInput(
            label="Mención (opcional)",
            placeholder="@everyone, @here, o vacío",
            required=False,
            max_length=20
        )
        self.add_item(self.mencion)

    async def on_submit(self, interaction: discord.Interaction):
        # Configurar color
        color_map = {
            "azul": 0x5865F2,
            "rojo": 0xED4245,
            "verde": 0x57F287,
            "dorado": 0xFEE75C,
            "morado": 0x9B59B6,
            "naranja": 0xF57C00,
            "rosa": 0xEB459E,
        }
        
        color_input = self.color.value.lower() if self.color.value else "azul"
        color = color_map.get(color_input, 0x5865F2)
        
        # Intentar parsear hex
        if color_input.startswith("#"):
            try:
                color = int(color_input[1:], 16)
            except:
                color = 0x5865F2
        
        # Crear embed profesional
        embed = discord.Embed(
            title=f"📢 {self.titulo.value}",
            description=self.mensaje.value,
            color=color,
            timestamp=datetime.now(timezone.utc)
        )
        
        # Configurar footer con info del servidor
        embed.set_footer(
            text=f"{interaction.guild.name} · {datetime.now().strftime('%d/%m/%Y %H:%M')}",
            icon_url=interaction.guild.icon.url if interaction.guild.icon else None
        )
        
        # Agregar thumbnail del servidor
        if interaction.guild.icon:
            embed.set_thumbnail(url=interaction.guild.icon.url)

        # Imagen grande opcional
        if self.imagen.value and self.imagen.value.strip().startswith("http"):
            embed.set_image(url=self.imagen.value.strip())

        # Agregar campo de "publicado por"
        embed.add_field(
            name="📌 Publicado por",
            value=interaction.user.mention,
            inline=True
        )
        embed.add_field(
            name="🔗 Servidor",
            value=interaction.guild.name,
            inline=True
        )
        
        # Añadir separador visual
        embed.add_field(name="\u200b", value="━━━━━━━━━━━━━━━━━━━━━━━", inline=False)

        # Mención opcional (@everyone / @here)
        mencion_raw = (self.mencion.value or "").strip().lower()
        content = None
        allowed_mentions = discord.AllowedMentions.none()
        if mencion_raw in ("@everyone", "everyone"):
            content = "@everyone"
            allowed_mentions = discord.AllowedMentions(everyone=True)
        elif mencion_raw in ("@here", "here"):
            content = "@here"
            allowed_mentions = discord.AllowedMentions(everyone=True)

        # Enviar el anuncio
        await self.channel.send(content=content, embed=embed, allowed_mentions=allowed_mentions)
        
        # Confirmar al usuario
        embed_confirm = build_embed(
            title="✅ Anuncio enviado",
            description=f"Anuncio enviado correctamente en {self.channel.mention}",
            color=COLOR_OK,
            fields=[
                ("📝 Título", self.titulo.value[:100], False),
                ("📊 Canal", self.channel.mention, True),
            ]
        )
        await interaction.response.send_message(embed=embed_confirm, ephemeral=True)
        
        # Registrar en logs
        record_log(
            interaction.guild_id,
            "moderacion",
            f"{interaction.user} envió un anuncio en {self.channel.name}: {self.titulo.value[:50]}",
            str(interaction.user)
        )


class AnnounceChannelSelect(discord.ui.Select):
    """Selector de canal para el anuncio."""
    
    def __init__(self, autor_id: int):
        self.autor_id = autor_id
        options = []
        
        # Obtener canales de texto del servidor (limitado a 25)
        # Esto se llenará en el callback
        super().__init__(
            placeholder="Selecciona un canal para el anuncio...",
            min_values=1,
            max_values=1
        )
    
    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.autor_id:
            await interaction.response.send_message("❌ Este menú no es para ti.", ephemeral=True)
            return
        
        channel_id = int(self.values[0])
        channel = interaction.guild.get_channel(channel_id)
        
        if not channel:
            await interaction.response.send_message("❌ Canal no encontrado.", ephemeral=True)
            return
        
        # Abrir modal para el anuncio
        modal = AnnounceModal(channel)
        await interaction.response.send_modal(modal)


class AnnounceView(discord.ui.View):
    """Vista del panel de anuncios."""
    
    def __init__(self, autor_id: int, channels: list[discord.TextChannel]):
        super().__init__(timeout=120)
        self.autor_id = autor_id
        
        # Crear opciones del selector
        options = []
        for ch in channels[:25]:  # Límite de 25 opciones
            options.append(
                discord.SelectOption(
                    label=ch.name[:100],
                    value=str(ch.id),
                    description=f"#{ch.name}",
                    emoji="📢"
                )
            )
        
        if options:
            select = discord.ui.Select(
                placeholder="Selecciona un canal...",
                options=options,
                min_values=1,
                max_values=1
            )
            
            async def select_callback(interaction: discord.Interaction):
                if interaction.user.id != self.autor_id:
                    await interaction.response.send_message("❌ Este menú no es para ti.", ephemeral=True)
                    return
                
                channel_id = int(select.values[0])
                channel = interaction.guild.get_channel(channel_id)
                
                if not channel:
                    await interaction.response.send_message("❌ Canal no encontrado.", ephemeral=True)
                    return
                
                modal = AnnounceModal(channel)
                await interaction.response.send_modal(modal)
            
            select.callback = select_callback
            self.add_item(select)
    
    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


@bot.tree.command(name="announce", description="📢 Envía un anuncio profesional a un canal.")
@app_commands.checks.has_permissions(administrator=True)
async def announce(interaction: discord.Interaction):
    """Panel para enviar anuncios con diseño de noticia."""
    track_command(interaction.guild_id, "announce")
    
    # Obtener canales de texto
    channels = [ch for ch in interaction.guild.text_channels if ch.permissions_for(interaction.guild.me).send_messages]
    
    if not channels:
        embed = build_embed(
            title="❌ Sin canales disponibles",
            description="No tengo permisos para enviar mensajes en ningún canal.",
            color=COLOR_WARN
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    embed = build_embed(
        title="📢 Panel de Anuncios",
        description=(
            "**Selecciona un canal** en el menú desplegable y luego completa el formulario.\n\n"
            "📝 **Consejos:**\n"
            "• El título debe ser llamativo\n"
            "• Usa saltos de línea para organizar el contenido\n"
            "• Puedes usar colores: azul, rojo, verde, dorado, morado, naranja, rosa\n"
            f"• Se mostrará automáticamente con diseño de noticia"
        ),
        color=COLOR_MAIN,
        footer=f"{len(channels)} canales disponibles"
    )
    
    view = AnnounceView(interaction.user.id, channels)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# ──────────────────────────────────────────────────────────────
#  PANEL DE ADMINISTRACIÓN (/paneladmin)
# ──────────────────────────────────────────────────────────────

class AdminPanelView(discord.ui.View):
    """Panel interactivo de administración con botones."""
    
    def __init__(self, autor_id: int, guild_id: int):
        super().__init__(timeout=300)
        self.autor_id = autor_id
        self.guild_id = guild_id
    
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.autor_id:
            await interaction.response.send_message("❌ Este panel no es para ti.", ephemeral=True)
            return False
        return True
    
    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
    
    @discord.ui.button(label="🔨 Banear usuario", style=discord.ButtonStyle.danger, emoji="🔨")
    async def ban_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = AdminBanModal(self.guild_id, interaction.user.id)
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="👢 Kickear usuario", style=discord.ButtonStyle.danger, emoji="👢")
    async def kick_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = AdminKickModal(self.guild_id, interaction.user.id)
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="🔇 Mutear usuario", style=discord.ButtonStyle.grey, emoji="🔇")
    async def mute_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = AdminMuteModal(self.guild_id, interaction.user.id)
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="🔊 Desmutear", style=discord.ButtonStyle.success, emoji="🔊")
    async def unmute_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = AdminUnmuteModal(self.guild_id, interaction.user.id)
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="📋 Lista de muteados", style=discord.ButtonStyle.secondary, emoji="📋")
    async def muted_list_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        data = load_muted()
        muted_users = []
        
        for key, info in data.items():
            g_id, user_id = key.split("_")
            if int(g_id) != self.guild_id:
                continue
            
            try:
                user = await bot.fetch_user(int(user_id))
                muted_users.append((user, info))
            except:
                muted_users.append((f"ID: {user_id}", info))
        
        if not muted_users:
            embed = build_embed(
                title="🔇 Lista de muteados",
                description="No hay usuarios muteados en este servidor.",
                color=COLOR_AMBER
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        
        lines = []
        for user, info in muted_users[:15]:
            user_str = user.mention if hasattr(user, 'mention') else str(user)
            permanent = info.get("permanent", False)
            expiry = info.get("expiry")
            
            if permanent:
                time_str = "🔒 Permanente"
            elif expiry:
                try:
                    dt = datetime.fromisoformat(expiry)
                    time_str = f"⏳ <t:{int(dt.timestamp())}:R>"
                except:
                    time_str = "⏳ Desconocido"
            else:
                time_str = "🔒 Permanente"
            
            lines.append(
                f"**{user_str}**\n"
                f"└ 📋 {info.get('reason', 'No especificada')} | {time_str}"
            )
        
        embed = build_embed(
            title="🔇 Usuarios muteados",
            description="\n\n".join(lines[:15]),
            color=COLOR_MAIN,
            footer=f"Total: {len(muted_users)} usuarios"
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
    
    @discord.ui.button(label="🔄 Recargar panel", style=discord.ButtonStyle.primary, emoji="🔄")
    async def reload_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = build_embed(
            title="🛡️ Panel de Administración",
            description=(
                "**Selecciona una acción:**\n\n"
                "🔨 **Banear** — Banea a un usuario (con duración)\n"
                "👢 **Kickear** — Expulsa a un usuario\n"
                "🔇 **Mutear** — Mutea a un usuario (con duración)\n"
                "🔊 **Desmutear** — Desmutea a un usuario\n"
                "📋 **Lista de muteados** — Ver usuarios muteados\n\n"
                "⚠️ Todas las acciones se registran en los logs."
            ),
            color=COLOR_MAIN,
            footer="Nexus Admin Panel"
        )
        await interaction.response.edit_message(embed=embed, view=self)


# ──────────────────────────────────────────────────────────────
#  MODALES PARA ADMIN PANEL
# ──────────────────────────────────────────────────────────────

class AdminBanModal(discord.ui.Modal):
    def __init__(self, guild_id: int, autor_id: int):
        super().__init__(title="🔨 Banear usuario")
        self.guild_id = guild_id
        self.autor_id = autor_id
        
        self.usuario_id = discord.ui.TextInput(
            label="ID del usuario",
            placeholder="Ej: 123456789012345678",
            required=True,
            max_length=20
        )
        self.add_item(self.usuario_id)
        
        self.razon = discord.ui.TextInput(
            label="Razón del baneo",
            placeholder="Ej: Spam, comportamiento inapropiado...",
            required=False,
            max_length=200
        )
        self.add_item(self.razon)
        
        self.duracion = discord.ui.TextInput(
            label="Duración (opcional)",
            placeholder="5m, 2h, 1d, 1w, 1M, 1y (dejar vacío = permanente)",
            required=False,
            max_length=10
        )
        self.add_item(self.duracion)
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            user_id = int(self.usuario_id.value)
            user = await bot.fetch_user(user_id)
        except:
            embed = build_embed(
                title="❌ ID inválido",
                description="No se pudo encontrar un usuario con ese ID.",
                color=COLOR_WARN
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        guild = bot.get_guild(self.guild_id)
        member = guild.get_member(user_id)
        
        if member and member.top_role >= guild.me.top_role:
            embed = build_embed(
                title="❌ No puedo banear a este usuario",
                description="Su rol es igual o superior al mío.",
                color=COLOR_WARN
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        # Procesar duración
        duration_text = "Permanente"
        if self.duracion.value:
            try:
                unit = self.duracion.value[-1]
                value = int(self.duracion.value[:-1])
                if unit not in ['m', 'h', 'd', 'w', 'M', 'y'] or value <= 0:
                    raise ValueError
            except:
                embed = build_embed(
                    title="❌ Formato de duración inválido",
                    description="Usa: `5m`, `2h`, `1d`, `1w`, `1M`, `1y`",
                    color=COLOR_WARN
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
            
            unit_names = {'m': 'minuto(s)', 'h': 'hora(s)', 'd': 'día(s)', 'w': 'semana(s)', 'M': 'mes(es)', 'y': 'año(s)'}
            duration_text = f"{value} {unit_names[unit]}"
        
        razon = self.razon.value or "No especificada"
        
        # Intentar enviar DM
        try:
            dm_embed = discord.Embed(
                title="🔨 Has sido baneado",
                description=f"Has sido baneado de **{guild.name}**.",
                color=COLOR_WARN,
                timestamp=datetime.now(timezone.utc)
            )
            dm_embed.add_field(name="📋 Razón", value=razon, inline=False)
            dm_embed.add_field(name="⏱️ Duración", value=duration_text, inline=True)
            dm_embed.add_field(name="👤 Moderador", value=str(interaction.user), inline=True)
            dm_embed.set_footer(text="Nexus Moderation System")
            
            view = AppealView(self.guild_id, user_id, "ban")
            await user.send(embed=dm_embed, view=view)
        except:
            pass
        
        # Banear
        await guild.ban(user, reason=f"{razon} | Moderador: {interaction.user}")
        
        # Estadísticas
        bump_stat(self.guild_id, "bans")
        
        record_log(
            self.guild_id,
            "moderacion",
            f"{interaction.user} baneó a {user} (ID: {user_id}). Razón: {razon} | Duración: {duration_text}",
            str(interaction.user)
        )
        
        embed = build_embed(
            title="✅ Usuario baneado",
            description=f"**{user}** ha sido baneado correctamente.",
            color=COLOR_OK,
            fields=[
                ("📋 Razón", razon, True),
                ("⏱️ Duración", duration_text, True),
                ("👤 Moderador", interaction.user.mention, True)
            ]
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class AdminKickModal(discord.ui.Modal):
    def __init__(self, guild_id: int, autor_id: int):
        super().__init__(title="👢 Kickear usuario")
        self.guild_id = guild_id
        self.autor_id = autor_id
        
        self.usuario_id = discord.ui.TextInput(
            label="ID del usuario",
            placeholder="Ej: 123456789012345678",
            required=True,
            max_length=20
        )
        self.add_item(self.usuario_id)
        
        self.razon = discord.ui.TextInput(
            label="Razón del kick",
            placeholder="Ej: Comportamiento inapropiado...",
            required=False,
            max_length=200
        )
        self.add_item(self.razon)
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            user_id = int(self.usuario_id.value)
            user = await bot.fetch_user(user_id)
        except:
            embed = build_embed(
                title="❌ ID inválido",
                description="No se pudo encontrar un usuario con ese ID.",
                color=COLOR_WARN
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        guild = bot.get_guild(self.guild_id)
        member = guild.get_member(user_id)
        
        if not member:
            embed = build_embed(
                title="❌ Usuario no encontrado",
                description="El usuario no está en el servidor.",
                color=COLOR_WARN
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        if member.top_role >= guild.me.top_role:
            embed = build_embed(
                title="❌ No puedo kickear a este usuario",
                description="Su rol es igual o superior al mío.",
                color=COLOR_WARN
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        razon = self.razon.value or "No especificada"
        
        # DM
        try:
            dm_embed = discord.Embed(
                title="👋 Has sido expulsado",
                description=f"Has sido expulsado de **{guild.name}**.",
                color=COLOR_WARN,
                timestamp=datetime.now(timezone.utc)
            )
            dm_embed.add_field(name="📋 Razón", value=razon, inline=False)
            dm_embed.add_field(name="👤 Moderador", value=str(interaction.user), inline=True)
            dm_embed.set_footer(text="Nexus Moderation System")
            
            view = AppealView(self.guild_id, user_id, "kick")
            await member.send(embed=dm_embed, view=view)
        except:
            pass
        
        await member.kick(reason=razon)
        
        bump_stat(self.guild_id, "kicks")
        
        record_log(
            self.guild_id,
            "moderacion",
            f"{interaction.user} kickeó a {user}. Razón: {razon}",
            str(interaction.user)
        )
        
        embed = build_embed(
            title="✅ Usuario kickeado",
            description=f"**{user}** ha sido kickeado correctamente.",
            color=COLOR_OK,
            fields=[
                ("📋 Razón", razon, True),
                ("👤 Moderador", interaction.user.mention, True)
            ]
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class AdminMuteModal(discord.ui.Modal):
    def __init__(self, guild_id: int, autor_id: int):
        super().__init__(title="🔇 Mutear usuario")
        self.guild_id = guild_id
        self.autor_id = autor_id
        
        self.usuario_id = discord.ui.TextInput(
            label="ID del usuario",
            placeholder="Ej: 123456789012345678",
            required=True,
            max_length=20
        )
        self.add_item(self.usuario_id)
        
        self.razon = discord.ui.TextInput(
            label="Razón del mute",
            placeholder="Ej: Spam, comportamiento inapropiado...",
            required=False,
            max_length=200
        )
        self.add_item(self.razon)
        
        self.duracion = discord.ui.TextInput(
            label="Duración (opcional)",
            placeholder="5m, 2h, 1d, 1w, 1M (dejar vacío = permanente)",
            required=False,
            max_length=10
        )
        self.add_item(self.duracion)
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            user_id = int(self.usuario_id.value)
            user = await bot.fetch_user(user_id)
        except:
            embed = build_embed(
                title="❌ ID inválido",
                description="No se pudo encontrar un usuario con ese ID.",
                color=COLOR_WARN
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        guild = bot.get_guild(self.guild_id)
        member = guild.get_member(user_id)
        
        if not member:
            embed = build_embed(
                title="❌ Usuario no encontrado",
                description="El usuario no está en el servidor.",
                color=COLOR_WARN
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        if member.top_role >= guild.me.top_role:
            embed = build_embed(
                title="❌ No puedo mutear a este usuario",
                description="Su rol es igual o superior al mío.",
                color=COLOR_WARN
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        razon = self.razon.value or "No especificada"
        
        # Procesar duración
        duration_text = "Permanente"
        seconds = 28 * 86400  # Máximo 28 días
        if self.duracion.value:
            try:
                unit = self.duracion.value[-1]
                value = int(self.duracion.value[:-1])
                if unit not in ['m', 'h', 'd', 'w', 'M'] or value <= 0:
                    raise ValueError
                
                if unit == 'm':
                    seconds = value * 60
                elif unit == 'h':
                    seconds = value * 3600
                elif unit == 'd':
                    seconds = value * 86400
                elif unit == 'w':
                    seconds = value * 604800
                elif unit == 'M':
                    seconds = value * 2592000
                
                if seconds > 2419200:
                    seconds = 2419200
                    duration_text = "28 días (máximo)"
                else:
                    unit_names = {'m': 'minuto(s)', 'h': 'hora(s)', 'd': 'día(s)', 'w': 'semana(s)', 'M': 'mes(es)'}
                    duration_text = f"{value} {unit_names[unit]}"
            except:
                embed = build_embed(
                    title="❌ Formato de duración inválido",
                    description="Usa: `5m`, `2h`, `1d`, `1w`, `1M`",
                    color=COLOR_WARN
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
        
        await member.timeout(discord.utils.utcnow() + timedelta(seconds=seconds), reason=razon)
        
        mute_user(self.guild_id, user_id, self.duracion.value, razon, str(interaction.user))
        
        # DM
        try:
            dm_embed = discord.Embed(
                title="🔇 Has sido muteado",
                description=f"Has sido muteado en **{guild.name}**.",
                color=COLOR_WARN,
                timestamp=datetime.now(timezone.utc)
            )
            dm_embed.add_field(name="📋 Razón", value=razon, inline=False)
            dm_embed.add_field(name="⏱️ Duración", value=duration_text, inline=True)
            dm_embed.add_field(name="👤 Moderador", value=str(interaction.user), inline=True)
            dm_embed.set_footer(text="Nexus Moderation System")
            
            view = AppealView(self.guild_id, user_id, "mute")
            await member.send(embed=dm_embed, view=view)
        except:
            pass
        
        record_log(
            self.guild_id,
            "moderacion",
            f"{interaction.user} muteó a {user}. Razón: {razon} | Duración: {duration_text}",
            str(interaction.user)
        )
        
        embed = build_embed(
            title="✅ Usuario muteado",
            description=f"**{user}** ha sido muteado correctamente.",
            color=COLOR_OK,
            fields=[
                ("📋 Razón", razon, True),
                ("⏱️ Duración", duration_text, True),
                ("👤 Moderador", interaction.user.mention, True)
            ]
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class AdminUnmuteModal(discord.ui.Modal):
    def __init__(self, guild_id: int, autor_id: int):
        super().__init__(title="🔊 Desmutear usuario")
        self.guild_id = guild_id
        self.autor_id = autor_id
        
        self.usuario_id = discord.ui.TextInput(
            label="ID del usuario",
            placeholder="Ej: 123456789012345678",
            required=True,
            max_length=20
        )
        self.add_item(self.usuario_id)
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            user_id = int(self.usuario_id.value)
            user = await bot.fetch_user(user_id)
        except:
            embed = build_embed(
                title="❌ ID inválido",
                description="No se pudo encontrar un usuario con ese ID.",
                color=COLOR_WARN
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        guild = bot.get_guild(self.guild_id)
        member = guild.get_member(user_id)
        
        if member:
            await member.timeout(None)
        
        unmute_user(self.guild_id, user_id)
        
        # DM
        try:
            dm_embed = discord.Embed(
                title="🔊 Has sido desmuteado",
                description=f"Has sido desmuteado en **{guild.name}**.",
                color=COLOR_OK,
                timestamp=datetime.now(timezone.utc)
            )
            dm_embed.add_field(name="👤 Moderador", value=str(interaction.user), inline=True)
            await user.send(embed=dm_embed)
        except:
            pass
        
        record_log(
            self.guild_id,
            "moderacion",
            f"{interaction.user} desmuteó a {user}",
            str(interaction.user)
        )
        
        embed = build_embed(
            title="✅ Usuario desmuteado",
            description=f"**{user}** ha sido desmuteado correctamente.",
            color=COLOR_OK
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="paneladmin", description="🛡️ Abre el panel de administración con botones.")
@app_commands.checks.has_permissions(administrator=True)
async def paneladmin(interaction: discord.Interaction):
    """Panel interactivo con botones para moderación."""
    track_command(interaction.guild_id, "paneladmin")
    
    embed = build_embed(
        title="🛡️ Panel de Administración",
        description=(
            "**Selecciona una acción:**\n\n"
            "🔨 **Banear** — Banea a un usuario (con duración)\n"
            "👢 **Kickear** — Expulsa a un usuario\n"
            "🔇 **Mutear** — Mutea a un usuario (con duración)\n"
            "🔊 **Desmutear** — Desmutea a un usuario\n"
            "📋 **Lista de muteados** — Ver usuarios muteados\n\n"
            "⚠️ Todas las acciones se registran en los logs."
        ),
        color=COLOR_MAIN,
        footer="Nexus Admin Panel"
    )
    
    view = AdminPanelView(interaction.user.id, interaction.guild_id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# ──────────────────────────────────────────────────────────────
#  BLOQUEO DE CANALES (/block-server)
# ──────────────────────────────────────────────────────────────

# Persistencia de canales bloqueados
BLOCKED_CHANNELS_PATH = "blocked_channels.json"
def load_blocked_channels() -> dict:
    """Carga canales bloqueados desde Supabase o archivo local"""
    if supabase:
        try:
            result = supabase.table("blocked_channels").select("*").eq("guild_id", "all").execute()
            if result.data and len(result.data) > 0:
                return result.data[0].get("data", {})
        except Exception as e:
            print(f"❌ Error cargando blocked_channels de Supabase: {e}")
    
    if not os.path.exists(BLOCKED_CHANNELS_PATH):
        return {}
    with open(BLOCKED_CHANNELS_PATH, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def save_blocked_channels(data: dict) -> None:
    """Guarda canales bloqueados en Supabase y local"""
    if supabase:
        try:
            supabase.table("blocked_channels").upsert({
                "guild_id": "all",
                "data": data
            }).execute()
        except Exception as e:
            print(f"❌ Error guardando blocked_channels en Supabase: {e}")
    
    with open(BLOCKED_CHANNELS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def is_channel_blocked(guild_id: int, channel_id: int) -> bool:
    """Verifica si un canal está bloqueado."""
    data = load_blocked_channels()
    key = str(guild_id)
    if key not in data:
        return False
    return str(channel_id) in data.get(key, {})

def get_blocked_channels(guild_id: int) -> list:
    """Obtiene los canales bloqueados de un servidor."""
    data = load_blocked_channels()
    key = str(guild_id)
    return list(data.get(key, {}).keys())


class BlockChannelView(discord.ui.View):
    """Panel para bloquear/desbloquear canales."""
    
    def __init__(self, autor_id: int, guild_id: int):
        super().__init__(timeout=120)
        self.autor_id = autor_id
        self.guild_id = guild_id
        self._refresh_options()
    
    def _refresh_options(self):
        """Actualiza las opciones del selector."""
        # Eliminar select existente
        for child in self.children[:]:
            if isinstance(child, discord.ui.Select):
                self.remove_item(child)
        
        # Obtener canales de texto
        guild = bot.get_guild(self.guild_id)
        if not guild:
            return
        
        channels = [ch for ch in guild.text_channels]
        
        # Crear opciones
        options = []
        for ch in channels[:25]:
            blocked = is_channel_blocked(self.guild_id, ch.id)
            label = f"{'🔒' if blocked else '🔓'} #{ch.name}"
            options.append(
                discord.SelectOption(
                    label=label[:100],
                    value=str(ch.id),
                    description="🔒 Bloqueado" if blocked else "🔓 Disponible",
                    emoji="🔒" if blocked else "🔓"
                )
            )
        
        if options:
            select = discord.ui.Select(
                placeholder="Selecciona un canal para bloquear/desbloquear...",
                options=options,
                min_values=1,
                max_values=1
            )
            
            async def select_callback(interaction: discord.Interaction):
                if interaction.user.id != self.autor_id:
                    await interaction.response.send_message("❌ Este panel no es para ti.", ephemeral=True)
                    return
                
                channel_id = int(select.values[0])
                guild = interaction.guild
                channel = guild.get_channel(channel_id)
                
                if not channel:
                    await interaction.response.send_message("❌ Canal no encontrado.", ephemeral=True)
                    return
                
                # Verificar si está bloqueado
                blocked = is_channel_blocked(self.guild_id, channel_id)
                
                if blocked:
                    # Desbloquear canal
                    data = load_blocked_channels()
                    key = str(self.guild_id)
                    if key in data and str(channel_id) in data[key]:
                        del data[key][str(channel_id)]
                        save_blocked_channels(data)
                    
                    # Restaurar permisos (guardar estado original)
                    # Aquí restauramos los permisos guardados
                    await self._restore_channel_permissions(channel)
                    
                    await interaction.response.send_message(
                        f"🔓 Canal {channel.mention} desbloqueado correctamente.",
                        ephemeral=True
                    )
                    
                    record_log(
                        self.guild_id,
                        "moderacion",
                        f"{interaction.user} desbloqueó el canal {channel.name}",
                        str(interaction.user)
                    )
                else:
                    # Bloquear canal
                    data = load_blocked_channels()
                    key = str(self.guild_id)
                    if key not in data:
                        data[key] = {}
                    
                    # Guardar estado actual de permisos para poder restaurarlos después
                    overwrites = {}
                    for target, overwrite in channel.overwrites.items():
                        if isinstance(target, discord.Role):
                            overwrites[f"role_{target.id}"] = {
                                "send_messages": overwrite.send_messages,
                                "add_reactions": overwrite.add_reactions,
                                "create_public_threads": overwrite.create_public_threads,
                                "create_private_threads": overwrite.create_private_threads,
                                "send_messages_in_threads": overwrite.send_messages_in_threads,
                                "send_tts_messages": overwrite.send_tts_messages,
                            }
                        elif isinstance(target, discord.Member):
                            overwrites[f"member_{target.id}"] = {
                                "send_messages": overwrite.send_messages,
                                "add_reactions": overwrite.add_reactions,
                                "create_public_threads": overwrite.create_public_threads,
                                "create_private_threads": overwrite.create_private_threads,
                                "send_messages_in_threads": overwrite.send_messages_in_threads,
                                "send_tts_messages": overwrite.send_tts_messages,
                            }
                    
                    data[key][str(channel_id)] = {
                        "original_overwrites": overwrites,
                        "blocked_by": interaction.user.id,
                        "blocked_at": datetime.now(timezone.utc).isoformat()
                    }
                    save_blocked_channels(data)
                    
                    # Aplicar bloqueo
                    await self._apply_block(channel)
                    
                    await interaction.response.send_message(
                        f"🔒 Canal {channel.mention} bloqueado correctamente.",
                        ephemeral=True
                    )
                    
                    record_log(
                        self.guild_id,
                        "moderacion",
                        f"{interaction.user} bloqueó el canal {channel.name}",
                        str(interaction.user)
                    )
                
                # Recargar el panel
                self._refresh_options()
                embed = self.build_embed()
                await interaction.message.edit(embed=embed, view=self)
            
            select.callback = select_callback
            self.add_item(select)
    
    async def _apply_block(self, channel: discord.TextChannel):
        """Aplica el bloqueo a un canal."""
        guild = channel.guid
        
        # Obtener el rol @everyone
        everyone = guild.default_role
        
        # Guardar permisos actuales para restauración
        # ya se guardan en el JSON
        
        # Aplicar bloqueo
        await channel.set_permissions(
            everyone,
            send_messages=False,
            add_reactions=False,
            create_public_threads=False,
            create_private_threads=False,
            send_messages_in_threads=False,
            send_tts_messages=False
        )
        
        # También bloquear para otros roles si es necesario
        # (opcional: bloquear para todos excepto admins)
        for role in guild.roles:
            if role.permissions.administrator:
                continue
            if role.id != everyone.id:
                await channel.set_permissions(
                    role,
                    send_messages=False,
                    add_reactions=False,
                    create_public_threads=False,
                    create_private_threads=False,
                    send_messages_in_threads=False,
                    send_tts_messages=False
                )
    
    async def _restore_channel_permissions(self, channel: discord.TextChannel):
        """Restaura los permisos originales de un canal."""
        data = load_blocked_channels()
        key = str(self.guild_id)
        
        if key not in data or str(channel.id) not in data[key]:
            return
        
        saved_data = data[key][str(channel.id)]
        original_overwrites = saved_data.get("original_overwrites", {})
        
        # Restaurar permisos para @everyone primero
        everyone = channel.guild.default_role
        
        # Limpiar permisos actuales del canal
        for target, overwrite in list(channel.overwrites.items()):
            try:
                await channel.set_permissions(target, overwrite=None)
            except:
                pass
        
        # Restaurar overwrites guardados
        for key_ow, perms in original_overwrites.items():
            if key_ow.startswith("role_"):
                role_id = int(key_ow.split("_")[1])
                role = channel.guild.get_role(role_id)
                if role:
                    await channel.set_permissions(role, **perms)
            elif key_ow.startswith("member_"):
                member_id = int(key_ow.split("_")[1])
                member = channel.guild.get_member(member_id)
                if member:
                    await channel.set_permissions(member, **perms)
    
    def build_embed(self) -> discord.Embed:
        """Construye el embed del panel."""
        guild = bot.get_guild(self.guild_id)
        blocked = get_blocked_channels(self.guild_id)
        
        desc = (
            "**Selecciona un canal** en el menú desplegable para bloquearlo o desbloquearlo.\n\n"
            "🔒 **Bloqueado** — Nadie puede escribir, reaccionar o crear hilos\n"
            "🔓 **Disponible** — El canal está abierto normalmente\n\n"
            f"📊 Canales bloqueados: **{len(blocked)}**"
        )
        
        embed = build_embed(
            title="🔒 Panel de Bloqueo de Canales",
            description=desc,
            color=COLOR_MAIN,
            footer="Nexus · Los bloqueos se guardan automáticamente"
        )
        
        if blocked:
            # Mostrar canales bloqueados
            channels_list = []
            for ch_id in blocked[:10]:
                ch = guild.get_channel(int(ch_id))
                if ch:
                    channels_list.append(f"• {ch.mention}")
            if channels_list:
                embed.add_field(
                    name="🔒 Canales bloqueados",
                    value="\n".join(channels_list) + (f"\n... y {len(blocked) - 10} más" if len(blocked) > 10 else ""),
                    inline=False
                )
        else:
            embed.add_field(
                name="🔓 Estado",
                value="No hay canales bloqueados en este servidor.",
                inline=False
            )
        
        return embed
    
    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


@bot.tree.command(name="block-server", description="🔒 Panel para bloquear/desbloquear canales.")
@app_commands.checks.has_permissions(administrator=True)
async def block_server(interaction: discord.Interaction):
    """Panel para bloquear canales (nadie puede escribir)."""
    track_command(interaction.guild_id, "block-server")
    
    view = BlockChannelView(interaction.user.id, interaction.guild_id)
    embed = view.build_embed()
    
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# ──────────────────────────────────────────────────────────────
#  EVENTO PARA BLOQUEAR MENSAJES EN CANALES BLOQUEADOS
# ──────────────────────────────────────────────────────────────

@bot.event
async def on_message(message: discord.Message):
    # ------------------
    # TRAP CHANNEL LOGIC
    # ------------------
    if not message.author.bot and message.guild:
        guild_cfg = get_guild_config(message.guild.id)
        trap_channel_id = guild_cfg.get("trap_channel_id")
        
        if trap_channel_id and message.channel.id == trap_channel_id:
            # Exclude Owner and Admins
            if message.author.id != Config.OWNER_ID and not message.author.guild_permissions.administrator:
                try:
                    await message.delete()
                    await message.author.ban(reason="Cuenta comprometida / Mensaje en Canal Trampa 🥀")
                    
                    # Notify Log Channel
                    log_channel_id = Config.LOG_CHANNEL_ID
                    if log_channel_id:
                        log_ch = message.guild.get_channel(log_channel_id)
                        if log_ch:
                            embed_trap = build_embed(
                                title="🚨 ALERTA: CANAL TRAMPA",
                                description=f"El usuario **{message.author}** ({message.author.id}) ha sido **BANEADO** por interactuar en el canal trampa {message.channel.mention}.",
                                color=Config.COLOR_ERROR
                            )
                            await log_ch.send(embed=embed_trap)
                except discord.Forbidden:
                    print("No tengo permisos para banear en el canal trampa.")
                return # Stop processing

    """Bloquea mensajes en canales bloqueados."""
    if message.author.bot:
        return
    
    if message.guild:
        if is_channel_blocked(message.guild.id, message.channel.id):
            # Eliminar el mensaje
            try:
                await message.delete()
                # Opcional: enviar un mensaje efímero al usuario
                # (no se puede enviar mensaje efímero en DM, así que mejor no)
            except:
                pass
            return

        guild_cfg = get_guild_config(message.guild.id)

        # ------------------
        # ANTILINK
        # ------------------
        if guild_cfg.get("antilink_enabled") and not message.author.guild_permissions.manage_messages:
            if re.search(r"(https?://|discord\.gg/|www\.)\S+", message.content, re.IGNORECASE):
                try:
                    await message.delete()
                    warning = await message.channel.send(
                        f"🔗 {message.author.mention}, no se permiten links en este servidor.",
                        delete_after=6
                    )
                except:
                    pass
                record_log(message.guild.id, "moderacion", f"Antilink: mensaje de {message.author} eliminado (contenía un link)", "Sistema")
                return

        # ------------------
        # ANTISPAM (5 mensajes en 5 segundos = timeout corto)
        # ------------------
        if guild_cfg.get("antispam_enabled") and not message.author.guild_permissions.manage_messages:
            now_ts = time.time()
            history = spam_tracker.setdefault(message.author.id, [])
            history.append(now_ts)
            spam_tracker[message.author.id] = [t for t in history if now_ts - t <= 5]

            if len(spam_tracker[message.author.id]) >= 5:
                spam_tracker[message.author.id] = []
                try:
                    await message.author.timeout(timedelta(minutes=5), reason="Antispam: envío de mensajes en ráfaga")
                    await message.channel.send(
                        embed=build_embed(
                            title="🚫 Antispam activado",
                            description=f"{message.author.mention} fue silenciado 5 minutos por mandar mensajes muy rápido.",
                            color=COLOR_WARN
                        )
                    )
                    record_log(message.guild.id, "moderacion", f"Antispam: {message.author} silenciado 5m por spam", "Sistema")
                except:
                    pass

        # ------------------
        # AFK
        # ------------------
        if message.author.id in afk_users:
            del afk_users[message.author.id]
            try:
                await message.channel.send(f"👋 {message.author.mention}, te quité el estado AFK.", delete_after=5)
            except:
                pass

        if message.mentions:
            for mentioned in message.mentions:
                if mentioned.id in afk_users:
                    info = afk_users[mentioned.id]
                    try:
                        await message.channel.send(
                            f"💤 **{mentioned.display_name}** está AFK: {info['reason']}",
                            delete_after=8
                        )
                    except:
                        pass
    
    # Procesar comandos normalmente
    await bot.process_commands(message)


                        
# ==========================================================
# NUEVOS COMANDOS: Moderación Avanzada y Canal Trampa
# ==========================================================

@bot.tree.command(name="warn", description="🥀 Advierte a un usuario.")
@app_commands.describe(usuario="El usuario a advertir", razon="La razón de la advertencia")
@app_commands.checks.has_permissions(kick_members=True)
async def warn_user(interaction: discord.Interaction, usuario: discord.Member, razon: str = "Sin razón especificada"):
    if usuario.id == Config.OWNER_ID:
        return await interaction.response.send_message(embed=build_embed(title="Error", description="No puedo advertir al Owner.", color=Config.COLOR_ERROR), ephemeral=True)

    total = add_warn(interaction.guild_id, usuario.id, razon, str(interaction.user))

    # DM al usuario
    try:
        await usuario.send(embed=build_embed(
            title="⚠️ Has sido advertido",
            description=f"Has recibido una advertencia en **{interaction.guild.name}**.",
            color=Config.COLOR_WARNING,
            fields=[
                ("📋 Razón", razon, False),
                ("🔢 Total de advertencias", str(total), True),
                ("👤 Moderador", str(interaction.user), True),
            ]
        ))
    except:
        pass

    embed = build_embed(
        title="Usuario Advertido",
        description=f"Se ha advertido a {usuario.mention}.",
        color=Config.COLOR_SUCCESS,
        fields=[
            ("Razón", razon, False),
            ("Total de advertencias", str(total), True),
        ]
    )
    await interaction.response.send_message(embed=embed)
    record_log(interaction.guild_id, "Warn", f"{usuario} fue advertido por: {razon} (total: {total})", str(interaction.user))


@bot.tree.command(name="warnings", description="📋 Muestra las advertencias de un usuario.")
@app_commands.describe(usuario="El usuario a consultar")
@app_commands.checks.has_permissions(kick_members=True)
async def warnings_cmd(interaction: discord.Interaction, usuario: discord.Member):
    entries = get_warns(interaction.guild_id, usuario.id)

    if not entries:
        embed = build_embed(
            title="✅ Sin advertencias",
            description=f"{usuario.mention} no tiene advertencias registradas.",
            color=Config.COLOR_SUCCESS
        )
        return await interaction.response.send_message(embed=embed, ephemeral=True)

    lines = []
    for i, w in enumerate(entries[-15:], start=1):
        ts = w.get("timestamp", "")
        try:
            dt = datetime.fromisoformat(ts)
            fecha = f"<t:{int(dt.timestamp())}:R>"
        except:
            fecha = "?"
        lines.append(f"**#{i}** · {w.get('reason', 'Sin razón')}\n└ 👤 {w.get('moderator', '?')} · {fecha}")

    embed = build_embed(
        title=f"⚠️ Advertencias de {usuario.display_name}",
        description="\n\n".join(lines),
        color=Config.COLOR_WARNING,
        thumbnail=usuario.display_avatar.url,
        footer=f"Total: {len(entries)} advertencia(s)"
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="clearwarns", description="🧹 Elimina todas las advertencias de un usuario.")
@app_commands.describe(usuario="El usuario a limpiar")
@app_commands.checks.has_permissions(administrator=True)
async def clearwarns_cmd(interaction: discord.Interaction, usuario: discord.Member):
    count = clear_warns(interaction.guild_id, usuario.id)
    embed = build_embed(
        title="🧹 Advertencias eliminadas",
        description=f"Se eliminaron **{count}** advertencia(s) de {usuario.mention}.",
        color=Config.COLOR_SUCCESS
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)
    record_log(interaction.guild_id, "ClearWarns", f"Se limpiaron {count} advertencias de {usuario}", str(interaction.user))


@bot.tree.command(name="slowmode", description="🐌 Configura el modo lento del canal actual.")
@app_commands.describe(segundos="Segundos entre mensajes (0 para desactivar, máx. 21600)")
@app_commands.checks.has_permissions(manage_channels=True)
async def slowmode_cmd(interaction: discord.Interaction, segundos: app_commands.Range[int, 0, 21600]):
    await interaction.channel.edit(slowmode_delay=segundos)
    if segundos == 0:
        desc = f"Se desactivó el modo lento en {interaction.channel.mention}."
    else:
        desc = f"Se configuró el modo lento a **{segundos}s** en {interaction.channel.mention}."
    embed = build_embed(title="🐌 Modo lento actualizado", description=desc, color=Config.COLOR_SUCCESS)
    await interaction.response.send_message(embed=embed)
    record_log(interaction.guild_id, "Slowmode", f"Modo lento de #{interaction.channel.name} a {segundos}s", str(interaction.user))


@bot.tree.command(name="nick", description="✏️ Cambia el apodo de un usuario.")
@app_commands.describe(usuario="El usuario a modificar", apodo="Nuevo apodo (vacío para quitarlo)")
@app_commands.checks.has_permissions(manage_nicknames=True)
async def nick_cmd(interaction: discord.Interaction, usuario: discord.Member, apodo: str | None = None):
    try:
        await usuario.edit(nick=apodo)
    except discord.Forbidden:
        embed = build_embed(title="❌ Error", description="No tengo permisos para cambiar el apodo de ese usuario.", color=Config.COLOR_ERROR)
        return await interaction.response.send_message(embed=embed, ephemeral=True)

    desc = f"Se restableció el apodo de {usuario.mention}." if not apodo else f"El apodo de {usuario.mention} ahora es **{apodo}**."
    embed = build_embed(title="✏️ Apodo actualizado", description=desc, color=Config.COLOR_SUCCESS)
    await interaction.response.send_message(embed=embed, ephemeral=True)
    record_log(interaction.guild_id, "Nick", f"Apodo de {usuario} cambiado a: {apodo or '(restablecido)'}", str(interaction.user))


@bot.tree.command(name="softban", description="🔨 Banea y desbanea al instante para borrar mensajes recientes.")
@app_commands.describe(usuario="El usuario a softbanear", razon="Razón del softban", dias="Días de mensajes a borrar (1-7)")
@app_commands.checks.has_permissions(ban_members=True)
async def softban_cmd(interaction: discord.Interaction, usuario: discord.Member, razon: str = "No especificada", dias: app_commands.Range[int, 1, 7] = 1):
    if usuario.id == Config.OWNER_ID:
        return await interaction.response.send_message(embed=build_embed(title="Error", description="No puedo softbanear al Owner.", color=Config.COLOR_ERROR), ephemeral=True)

    try:
        await usuario.send(embed=build_embed(
            title="🔨 Has sido expulsado (softban)",
            description=f"Tus mensajes recientes fueron eliminados en **{interaction.guild.name}** y puedes volver a unirte.",
            color=Config.COLOR_WARNING,
            fields=[("📋 Razón", razon, False)]
        ))
    except:
        pass

    try:
        await interaction.guild.ban(usuario, reason=f"Softban por {interaction.user}: {razon}", delete_message_days=dias)
        await interaction.guild.unban(usuario, reason="Softban - desbaneo automático")
    except discord.Forbidden:
        embed = build_embed(title="❌ Error", description="No tengo permisos suficientes para softbanear a este usuario.", color=Config.COLOR_ERROR)
        return await interaction.response.send_message(embed=embed, ephemeral=True)

    embed = build_embed(
        title="🔨 Softban aplicado",
        description=f"Se limpiaron los mensajes recientes de {usuario.mention} y fue desbaneado automáticamente.",
        color=Config.COLOR_SUCCESS,
        fields=[("Razón", razon, False), ("Días de mensajes borrados", str(dias), True)]
    )
    await interaction.response.send_message(embed=embed)
    record_log(interaction.guild_id, "Softban", f"{usuario} softbaneado por: {razon}", str(interaction.user))


@bot.tree.command(name="timeout", description="🔇 Silencia a un usuario con el timeout nativo de Discord.")
@app_commands.describe(usuario="Usuario a silenciar", duracion="Duración: 10m, 2h, 1d, 1w (máx. 28d)", razon="Razón del timeout")
@app_commands.checks.has_permissions(moderate_members=True)
async def timeout_cmd(interaction: discord.Interaction, usuario: discord.Member, duracion: str, razon: str = "No especificada"):
    if usuario.id == Config.OWNER_ID:
        return await interaction.response.send_message(embed=build_embed(title="Error", description="No puedo silenciar al Owner.", color=Config.COLOR_ERROR), ephemeral=True)

    seconds = parse_duration_to_seconds(duracion)
    if seconds is None:
        embed = build_embed(title="❌ Duración inválida", description="Usá un formato como `10m`, `2h`, `1d` o `1w`.", color=Config.COLOR_ERROR)
        return await interaction.response.send_message(embed=embed, ephemeral=True)
    if seconds > 28 * 86400:
        embed = build_embed(title="❌ Duración inválida", description="El timeout nativo de Discord tiene un máximo de 28 días.", color=Config.COLOR_ERROR)
        return await interaction.response.send_message(embed=embed, ephemeral=True)

    try:
        await usuario.timeout(timedelta(seconds=seconds), reason=f"{razon} — por {interaction.user}")
    except discord.Forbidden:
        embed = build_embed(title="❌ Error", description="No tengo permisos suficientes para silenciar a este usuario.", color=Config.COLOR_ERROR)
        return await interaction.response.send_message(embed=embed, ephemeral=True)

    try:
        await usuario.send(embed=build_embed(
            title="🔇 Has sido silenciado",
            description=f"Fuiste silenciado en **{interaction.guild.name}** por **{format_seconds(seconds)}**.",
            color=Config.COLOR_WARNING,
            fields=[("📋 Razón", razon, False)]
        ))
    except:
        pass

    embed = build_embed(
        title="🔇 Timeout aplicado",
        description=f"{usuario.mention} fue silenciado por **{format_seconds(seconds)}**.",
        color=Config.COLOR_SUCCESS,
        fields=[("Razón", razon, False)]
    )
    await interaction.response.send_message(embed=embed)
    record_log(interaction.guild_id, "Timeout", f"{usuario} silenciado {format_seconds(seconds)} por: {razon}", str(interaction.user))


@bot.tree.command(name="untimeout", description="🔊 Quita el timeout nativo a un usuario.")
@app_commands.describe(usuario="Usuario a des-silenciar")
@app_commands.checks.has_permissions(moderate_members=True)
async def untimeout_cmd(interaction: discord.Interaction, usuario: discord.Member):
    try:
        await usuario.timeout(None, reason=f"Timeout removido por {interaction.user}")
    except discord.Forbidden:
        embed = build_embed(title="❌ Error", description="No tengo permisos suficientes.", color=Config.COLOR_ERROR)
        return await interaction.response.send_message(embed=embed, ephemeral=True)

    embed = build_embed(title="🔊 Timeout removido", description=f"Se le quitó el timeout a {usuario.mention}.", color=Config.COLOR_SUCCESS)
    await interaction.response.send_message(embed=embed)
    record_log(interaction.guild_id, "Untimeout", f"Timeout removido a {usuario}", str(interaction.user))


@bot.tree.command(name="unwarn", description="🗑️ Elimina una advertencia puntual de un usuario.")
@app_commands.describe(usuario="Usuario", numero="Número de la advertencia (ver con /warnings)")
@app_commands.checks.has_permissions(kick_members=True)
async def unwarn_cmd(interaction: discord.Interaction, usuario: discord.Member, numero: app_commands.Range[int, 1, 999]):
    removed = remove_warn(interaction.guild_id, usuario.id, numero)
    if removed is None:
        embed = build_embed(title="❌ No encontrada", description=f"No existe la advertencia #{numero} para {usuario.mention}. Revisá con `/warnings`.", color=Config.COLOR_ERROR)
        return await interaction.response.send_message(embed=embed, ephemeral=True)

    embed = build_embed(
        title="🗑️ Advertencia eliminada",
        description=f"Se eliminó la advertencia #{numero} de {usuario.mention}.",
        color=Config.COLOR_SUCCESS,
        fields=[("Razón original", removed.get("reason", "?"), False)]
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)
    record_log(interaction.guild_id, "Unwarn", f"Se eliminó la advertencia #{numero} de {usuario}", str(interaction.user))


@bot.tree.command(name="role", description="🎭 Agrega o quita un rol a un usuario.")
@app_commands.describe(usuario="Usuario a modificar", rol="Rol a agregar o quitar", accion="Agregar o quitar")
@app_commands.choices(accion=[
    app_commands.Choice(name="Agregar", value="add"),
    app_commands.Choice(name="Quitar", value="remove"),
])
@app_commands.checks.has_permissions(manage_roles=True)
async def role_cmd(interaction: discord.Interaction, usuario: discord.Member, rol: discord.Role, accion: app_commands.Choice[str]):
    if rol >= interaction.guild.me.top_role:
        embed = build_embed(title="❌ Error", description=f"No puedo gestionar el rol {rol.mention} porque está por encima de mi rol más alto.", color=Config.COLOR_ERROR)
        return await interaction.response.send_message(embed=embed, ephemeral=True)

    try:
        if accion.value == "add":
            await usuario.add_roles(rol, reason=f"Agregado por {interaction.user}")
            desc = f"Se agregó el rol {rol.mention} a {usuario.mention}."
        else:
            await usuario.remove_roles(rol, reason=f"Removido por {interaction.user}")
            desc = f"Se quitó el rol {rol.mention} a {usuario.mention}."
    except discord.Forbidden:
        embed = build_embed(title="❌ Error", description="No tengo permisos suficientes para gestionar ese rol.", color=Config.COLOR_ERROR)
        return await interaction.response.send_message(embed=embed, ephemeral=True)

    embed = build_embed(title="🎭 Rol actualizado", description=desc, color=Config.COLOR_SUCCESS)
    await interaction.response.send_message(embed=embed, ephemeral=True)
    record_log(interaction.guild_id, "Role", desc, str(interaction.user))


@bot.tree.command(name="modstats", description="📈 Ranking de acciones de moderación por moderador.")
@app_commands.checks.has_permissions(kick_members=True)
async def modstats_cmd(interaction: discord.Interaction):
    entries = get_logs(interaction.guild_id)
    conteo = Counter()
    for e in entries:
        autor = e.get("autor")
        if autor:
            conteo[autor] += 1

    if not conteo:
        embed = build_embed(title="📈 Sin datos", description="Todavía no hay acciones registradas en los logs.", color=COLOR_WARN)
        return await interaction.response.send_message(embed=embed, ephemeral=True)

    top = conteo.most_common(10)
    medallas = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (autor, cantidad) in enumerate(top):
        medalla = medallas[i] if i < 3 else f"`#{i+1}`"
        lines.append(f"{medalla} **{autor}** — {cantidad} acción(es)")

    embed = build_embed(
        title="📈 Ranking de moderación",
        description="\n".join(lines),
        color=COLOR_PURPLE,
        footer=f"Basado en los últimos {len(entries)} registros de log"
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="antilink", description="🔗 Activa o desactiva el borrado automático de links.")
@app_commands.describe(estado="Activar o desactivar")
@app_commands.choices(estado=[
    app_commands.Choice(name="Activar", value="on"),
    app_commands.Choice(name="Desactivar", value="off"),
])
@app_commands.checks.has_permissions(administrator=True)
async def antilink_cmd(interaction: discord.Interaction, estado: app_commands.Choice[str]):
    cfg = get_guild_config(interaction.guild_id)
    cfg["antilink_enabled"] = (estado.value == "on")
    update_guild_config(interaction.guild_id, cfg)

    desc = "Ahora se eliminarán automáticamente los mensajes con links de usuarios sin permisos de gestión de mensajes." if cfg["antilink_enabled"] else "El borrado automático de links fue desactivado."
    embed = build_embed(title="🔗 Antilink actualizado", description=desc, color=Config.COLOR_SUCCESS)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="antispam", description="🚫 Activa o desactiva la protección antispam.")
@app_commands.describe(estado="Activar o desactivar")
@app_commands.choices(estado=[
    app_commands.Choice(name="Activar", value="on"),
    app_commands.Choice(name="Desactivar", value="off"),
])
@app_commands.checks.has_permissions(administrator=True)
async def antispam_cmd(interaction: discord.Interaction, estado: app_commands.Choice[str]):
    cfg = get_guild_config(interaction.guild_id)
    cfg["antispam_enabled"] = (estado.value == "on")
    update_guild_config(interaction.guild_id, cfg)

    desc = "Ahora se detectará y silenciará automáticamente a quien mande mensajes repetidos muy rápido (5 en 5s)." if cfg["antispam_enabled"] else "La protección antispam fue desactivada."
    embed = build_embed(title="🚫 Antispam actualizado", description=desc, color=Config.COLOR_SUCCESS)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ──────────────────────────────────────────────────────────────
#  DMS DESDE EL BOT (/dm y /dm-masivo)
# ──────────────────────────────────────────────────────────────

class DMModal(discord.ui.Modal):
    """Modal para redactar un DM profesional a un usuario o a un rol."""

    def __init__(self, target_label: str, on_send):
        super().__init__(title="✉️ Redactar mensaje directo")
        self.target_label = target_label
        self.on_send = on_send

        self.titulo = discord.ui.TextInput(
            label="Título",
            placeholder="Ej: Aviso importante de Nexus",
            required=True,
            max_length=100
        )
        self.add_item(self.titulo)

        self.mensaje = discord.ui.TextInput(
            label="Mensaje",
            style=discord.TextStyle.paragraph,
            placeholder="Escribe el contenido del mensaje aquí...",
            required=True,
            max_length=2000
        )
        self.add_item(self.mensaje)

        self.color = discord.ui.TextInput(
            label="Color (opcional)",
            placeholder="azul, rojo, verde, dorado, morado, o #RRGGBB",
            required=False,
            max_length=20
        )
        self.add_item(self.color)

    async def on_submit(self, interaction: discord.Interaction):
        color_map = {
            "azul": 0x5865F2, "rojo": 0xED4245, "verde": 0x57F287,
            "dorado": 0xFEE75C, "morado": 0x9B59B6, "naranja": 0xF57C00, "rosa": 0xEB459E,
        }
        color_input = (self.color.value or "").lower()
        color = color_map.get(color_input, 0x5865F2)
        if color_input.startswith("#"):
            try:
                color = int(color_input[1:], 16)
            except:
                color = 0x5865F2

        embed = discord.Embed(
            title=f"✉️ {self.titulo.value}",
            description=self.mensaje.value,
            color=color,
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(
            text=f"Mensaje de {interaction.guild.name} · Enviado por {interaction.user.display_name}",
            icon_url=interaction.guild.icon.url if interaction.guild.icon else None
        )
        if interaction.guild.icon:
            embed.set_thumbnail(url=interaction.guild.icon.url)

        await self.on_send(interaction, embed)


@bot.tree.command(name="dm", description="✉️ [Mod] Envía un mensaje directo profesional a un usuario.")
@app_commands.describe(usuario="Usuario al que enviarle el DM")
@app_commands.checks.has_permissions(moderate_members=True)
async def dm_cmd(interaction: discord.Interaction, usuario: discord.Member):
    track_command(interaction.guild_id, "dm")

    if usuario.bot:
        embed = build_embed(title="❌ Error", description="No puedes enviarle un DM a un bot.", color=COLOR_WARN)
        return await interaction.response.send_message(embed=embed, ephemeral=True)

    async def send_single(modal_interaction: discord.Interaction, embed: discord.Embed):
        try:
            await usuario.send(embed=embed)
            confirm = build_embed(
                title="✅ Mensaje enviado",
                description=f"Se envió el DM correctamente a {usuario.mention}.",
                color=COLOR_OK
            )
            record_log(
                interaction.guild_id, "moderacion",
                f"{interaction.user} envió un DM a {usuario}: {embed.title}",
                str(interaction.user)
            )
        except discord.Forbidden:
            confirm = build_embed(
                title="⚠️ No se pudo entregar",
                description=f"{usuario.mention} tiene los DMs cerrados o bloqueó al bot.",
                color=COLOR_WARN
            )
        await modal_interaction.response.send_message(embed=confirm, ephemeral=True)

    modal = DMModal(usuario.display_name, send_single)
    await interaction.response.send_modal(modal)


@bot.tree.command(name="dm-masivo", description="📨 [Admin] Envía un DM profesional a todos los usuarios de un rol.")
@app_commands.describe(rol="Rol al que enviarle el mensaje")
@app_commands.checks.has_permissions(administrator=True)
async def dm_masivo_cmd(interaction: discord.Interaction, rol: discord.Role):
    track_command(interaction.guild_id, "dm-masivo")

    miembros = [m for m in rol.members if not m.bot]
    if not miembros:
        embed = build_embed(title="❌ Rol vacío", description=f"El rol {rol.mention} no tiene miembros a los que enviar mensajes.", color=COLOR_WARN)
        return await interaction.response.send_message(embed=embed, ephemeral=True)

    async def send_bulk(modal_interaction: discord.Interaction, embed: discord.Embed):
        await modal_interaction.response.send_message(
            embed=build_embed(
                title="📨 Enviando mensajes...",
                description=f"Enviando a **{len(miembros)}** miembro(s) con el rol {rol.mention}. Esto puede tardar un poco.",
                color=COLOR_MAIN
            ),
            ephemeral=True
        )

        enviados, fallidos = 0, 0
        for miembro in miembros:
            try:
                await miembro.send(embed=embed)
                enviados += 1
            except:
                fallidos += 1
            await asyncio.sleep(1)  # evitar rate limits de DMs

        resumen = build_embed(
            title="✅ Envío masivo completado",
            description=f"Resultado del envío a {rol.mention}.",
            color=COLOR_OK,
            fields=[
                ("📬 Enviados", str(enviados), True),
                ("🚫 Fallidos (DMs cerrados)", str(fallidos), True),
            ]
        )
        await modal_interaction.followup.send(embed=resumen, ephemeral=True)
        record_log(
            interaction.guild_id, "moderacion",
            f"{interaction.user} envió un DM masivo al rol {rol.name}: {embed.title} ({enviados} enviados, {fallidos} fallidos)",
            str(interaction.user)
        )

    modal = DMModal(rol.name, send_bulk)
    await interaction.response.send_modal(modal)

@bot.tree.command(name="purge", description="✨ Elimina mensajes en masa (Clear).")
@app_commands.describe(cantidad="Cantidad de mensajes a eliminar (1-100)")
@app_commands.checks.has_permissions(manage_messages=True)
async def purge_messages(interaction: discord.Interaction, cantidad: app_commands.Range[int, 1, 100]):
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=cantidad)
    embed = build_embed(title="Chat Limpiado", description=f"Se eliminaron **{len(deleted)}** mensajes.", color=Config.COLOR_SUCCESS)
    await interaction.followup.send(embed=embed)
    record_log(interaction.guild_id, "Purge", f"Borrados {len(deleted)} mensajes en #{interaction.channel.name}", str(interaction.user))

@bot.tree.command(name="lock", description="🔒 Bloquea el canal actual para usuarios normales.")
@app_commands.checks.has_permissions(manage_channels=True)
async def lock_channel(interaction: discord.Interaction):
    await interaction.channel.set_permissions(interaction.guild.default_role, send_messages=False)
    embed = build_embed(title="Canal Bloqueado", description="Este canal ha sido bloqueado por moderación. 🥀", color=Config.COLOR_ERROR)
    await interaction.response.send_message(embed=embed)
    record_log(interaction.guild_id, "Lock", f"Canal #{interaction.channel.name} bloqueado", str(interaction.user))

@bot.tree.command(name="unlock", description="🔓 Desbloquea el canal actual.")
@app_commands.checks.has_permissions(manage_channels=True)
async def unlock_channel(interaction: discord.Interaction):
    await interaction.channel.set_permissions(interaction.guild.default_role, send_messages=True)
    embed = build_embed(title="Canal Desbloqueado", description="Este canal ha sido desbloqueado. 🌸", color=Config.COLOR_SUCCESS)
    await interaction.response.send_message(embed=embed)
    record_log(interaction.guild_id, "Unlock", f"Canal #{interaction.channel.name} desbloqueado", str(interaction.user))

@bot.tree.command(name="canal-trampa", description="🥀 Configura un canal trampa (Honeypot) para banear intrusos.")
@app_commands.describe(canal="El canal que funcionará como trampa")
@app_commands.checks.has_permissions(administrator=True)
async def set_trap_channel(interaction: discord.Interaction, canal: discord.TextChannel):
    guild_cfg = get_guild_config(interaction.guild_id)
    guild_cfg["trap_channel_id"] = canal.id
    update_guild_config(interaction.guild_id, guild_cfg)
    embed = build_embed(title="Canal Trampa Configurado", description=f"El canal {canal.mention} ahora es una trampa. Cualquier usuario que escriba ahí será baneado. 🥀", color=Config.COLOR_SUCCESS)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ──────────────────────────────────────────────────────────────
#  COMUNIDAD: /recordatorio, /afk, /sugerencia
# ──────────────────────────────────────────────────────────────

@bot.tree.command(name="recordatorio", description="⏰ El bot te recuerda algo después de un tiempo.")
@app_commands.describe(tiempo="Ej: 10m, 2h, 1d", texto="Qué querés que te recuerde")
async def recordatorio_cmd(interaction: discord.Interaction, tiempo: str, texto: str):
    seconds = parse_duration_to_seconds(tiempo)
    if seconds is None or seconds > 7 * 86400:
        embed = build_embed(title="❌ Tiempo inválido", description="Usá un formato como `10m`, `2h`, `1d` (máximo 7 días).", color=COLOR_WARN)
        return await interaction.response.send_message(embed=embed, ephemeral=True)

    embed = build_embed(
        title="⏰ Recordatorio guardado",
        description=f"Te voy a avisar en **{format_seconds(seconds)}**.",
        color=COLOR_OK,
        fields=[("📝 Texto", texto[:200], False)]
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

    async def wait_and_remind():
        await asyncio.sleep(seconds)
        recordatorio_embed = build_embed(
            title="⏰ ¡Recordatorio!",
            description=texto,
            color=COLOR_AMBER,
            footer="Pediste que te avisara esto"
        )
        try:
            await interaction.user.send(embed=recordatorio_embed)
        except:
            try:
                await interaction.channel.send(content=interaction.user.mention, embed=recordatorio_embed)
            except:
                pass

    asyncio.create_task(wait_and_remind())


@bot.tree.command(name="afk", description="💤 Marcate como AFK. El bot avisa si te mencionan.")
@app_commands.describe(razon="Motivo (opcional)")
async def afk_cmd(interaction: discord.Interaction, razon: str = "Sin especificar"):
    afk_users[interaction.user.id] = {"reason": razon, "since": datetime.now(timezone.utc)}
    embed = build_embed(
        title="💤 Modo AFK activado",
        description=f"Te marqué como AFK. Apenas escribas de nuevo se te quita automáticamente.",
        color=COLOR_MAIN,
        fields=[("Razón", razon, False)]
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="sugerencia", description="💡 Manda una sugerencia al buzón del servidor.")
@app_commands.describe(texto="Tu sugerencia")
async def sugerencia_cmd(interaction: discord.Interaction, texto: str):
    cfg = get_guild_config(interaction.guild_id)
    channel_id = cfg.get("suggestions_channel")
    channel = interaction.guild.get_channel(int(channel_id)) if channel_id else interaction.channel

    if not channel:
        embed = build_embed(title="❌ Error", description="No se encontró el canal de sugerencias configurado.", color=COLOR_WARN)
        return await interaction.response.send_message(embed=embed, ephemeral=True)

    embed = build_embed(
        title="💡 Nueva sugerencia",
        description=texto,
        color=COLOR_PURPLE,
        author_name=str(interaction.user),
        author_icon=interaction.user.display_avatar.url,
        footer=f"Sugerido por {interaction.user.id}"
    )
    msg = await channel.send(embed=embed)
    try:
        await msg.add_reaction("👍")
        await msg.add_reaction("👎")
    except:
        pass

    confirm = build_embed(title="✅ Sugerencia enviada", description=f"Tu sugerencia se publicó en {channel.mention}.", color=COLOR_OK)
    await interaction.response.send_message(embed=confirm, ephemeral=True)
    record_log(interaction.guild_id, "moderacion", f"{interaction.user} envió una sugerencia: {texto[:60]}", str(interaction.user))


@bot.tree.command(name="config-sugerencias", description="[Admin] Configura el canal donde caen las sugerencias.")
@app_commands.describe(canal="Canal para las sugerencias")
@app_commands.checks.has_permissions(administrator=True)
async def config_sugerencias_cmd(interaction: discord.Interaction, canal: discord.TextChannel):
    cfg = get_guild_config(interaction.guild_id)
    cfg["suggestions_channel"] = canal.id
    update_guild_config(interaction.guild_id, cfg)
    embed = build_embed(title="💡 Canal configurado", description=f"Las sugerencias ahora se publican en {canal.mention}.", color=COLOR_OK)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ──────────────────────────────────────────────────────────────
#  AUTOROLE
# ──────────────────────────────────────────────────────────────

@bot.tree.command(name="autorole", description="[Admin] Configura el rol automático al unirse alguien.")
@app_commands.describe(rol="Rol a asignar automáticamente (dejar vacío para desactivar)")
@app_commands.checks.has_permissions(administrator=True)
async def autorole_cmd(interaction: discord.Interaction, rol: discord.Role | None = None):
    cfg = get_guild_config(interaction.guild_id)
    cfg["autorole_id"] = rol.id if rol else None
    update_guild_config(interaction.guild_id, cfg)

    if rol:
        embed = build_embed(title="🎭 Autorole configurado", description=f"Cada nuevo miembro recibirá el rol {rol.mention} automáticamente.", color=COLOR_OK)
    else:
        embed = build_embed(title="🎭 Autorole desactivado", description="Ya no se asignará ningún rol automático.", color=COLOR_WARN)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ──────────────────────────────────────────────────────────────
#  PANEL DE AUTOROLES (roles que la gente se pone solita con botones)
# ──────────────────────────────────────────────────────────────

AUTOROLE_BUTTON_STYLES = [
    discord.ButtonStyle.primary,
    discord.ButtonStyle.success,
    discord.ButtonStyle.secondary,
    discord.ButtonStyle.danger,
]


class AutoRolePanelView(discord.ui.View):
    """Vista persistente: un botón por rol. Al presionarlo, el usuario se lo pone o se lo quita.

    Es completamente autodescriptiva (el custom_id trae el ID del rol), así que
    sobrevive a un reinicio del bot sin necesitar guardar el message_id.
    """

    def __init__(self, roles_data: list[dict]):
        super().__init__(timeout=None)
        for i, entry in enumerate(roles_data):
            role_id = entry["role_id"]
            label = entry.get("label") or "Rol"
            emoji = entry.get("emoji") or None
            style = AUTOROLE_BUTTON_STYLES[i % len(AUTOROLE_BUTTON_STYLES)]
            button = discord.ui.Button(
                label=label[:80],
                emoji=emoji,
                style=style,
                custom_id=f"autorolepanel:{role_id}",
            )
            button.callback = self._make_callback(role_id, label)
            self.add_item(button)

    def _make_callback(self, role_id: int, label: str):
        async def callback(interaction: discord.Interaction):
            guild = interaction.guild
            member = interaction.user
            role = guild.get_role(role_id) if guild else None

            if role is None:
                embed = build_embed(
                    title="⚠️ Este rol ya no existe",
                    description="Es probable que un administrador lo haya borrado. Avisale a un admin.",
                    color=COLOR_WARN,
                )
                return await interaction.response.send_message(embed=embed, ephemeral=True)

            if guild.me.top_role <= role and guild.owner_id != guild.me.id:
                embed = build_embed(
                    title="⚠️ No puedo asignar ese rol",
                    description=f"El rol {role.mention} está por encima o al mismo nivel que mi rol más alto. Un admin debe subir mi rol en `Configuración del servidor → Roles`.",
                    color=COLOR_WARN,
                )
                return await interaction.response.send_message(embed=embed, ephemeral=True)

            try:
                if role in member.roles:
                    await member.remove_roles(role, reason="Autorole panel (auto-quitado)")
                    embed = build_embed(
                        title="➖ Rol quitado",
                        description=f"Te quité el rol {role.mention}.",
                        color=COLOR_AMBER,
                    )
                else:
                    await member.add_roles(role, reason="Autorole panel (auto-asignado)")
                    embed = build_embed(
                        title="➕ Rol asignado",
                        description=f"¡Listo! Ahora tenés el rol {role.mention}.",
                        color=COLOR_OK,
                    )
                await interaction.response.send_message(embed=embed, ephemeral=True)
            except discord.Forbidden:
                embed = build_embed(
                    title="❌ Sin permisos",
                    description="No tengo permisos suficientes para gestionar ese rol.",
                    color=COLOR_WARN,
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)

        return callback


def register_autorole_panels() -> int:
    """Reconstruye y registra en el bot todas las vistas de paneles de autoroles guardadas,
    para que los botones sigan funcionando después de un reinicio. Devuelve cuántos registró."""
    count = 0
    data = load_config()
    for guild_id_str, guild_cfg in data.items():
        panels = guild_cfg.get("autorole_panels") or []
        for panel in panels:
            roles_data = panel.get("roles") or []
            if not roles_data:
                continue
            try:
                bot.add_view(AutoRolePanelView(roles_data))
                count += 1
            except Exception as e:
                print(f"⚠️ No se pudo registrar un panel de autoroles del servidor {guild_id_str}: {e}")
    return count


@bot.tree.command(name="panel-autoroles", description="[Admin] Publica un panel con botones para que la gente se ponga roles solita.")
@app_commands.describe(
    canal="Canal donde publicar el panel (por defecto el canal actual)",
    titulo="Título del panel",
    descripcion="Texto descriptivo del panel (opcional)",
    rol1="Rol #1", emoji1="Emoji para el rol #1 (opcional)",
    rol2="Rol #2 (opcional)", emoji2="Emoji para el rol #2 (opcional)",
    rol3="Rol #3 (opcional)", emoji3="Emoji para el rol #3 (opcional)",
    rol4="Rol #4 (opcional)", emoji4="Emoji para el rol #4 (opcional)",
    rol5="Rol #5 (opcional)", emoji5="Emoji para el rol #5 (opcional)",
)
@app_commands.checks.has_permissions(administrator=True)
async def panel_autoroles_cmd(
    interaction: discord.Interaction,
    rol1: discord.Role,
    titulo: str = "🎭 Elegí tus roles",
    descripcion: str | None = None,
    canal: discord.TextChannel | None = None,
    emoji1: str | None = None,
    rol2: discord.Role | None = None, emoji2: str | None = None,
    rol3: discord.Role | None = None, emoji3: str | None = None,
    rol4: discord.Role | None = None, emoji4: str | None = None,
    rol5: discord.Role | None = None, emoji5: str | None = None,
):
    track_command(interaction.guild_id, "panel-autoroles")
    await interaction.response.defer(ephemeral=True, thinking=True)
    target = canal or interaction.channel

    pares = [(rol1, emoji1), (rol2, emoji2), (rol3, emoji3), (rol4, emoji4), (rol5, emoji5)]
    roles_data = []
    lineas_desc = []
    for rol, emoji in pares:
        if rol is None:
            continue
        if rol >= interaction.guild.me.top_role and interaction.guild.owner_id != interaction.guild.me.id:
            embed = build_embed(
                title="⚠️ No puedo usar ese rol",
                description=f"El rol {rol.mention} está por encima o al mismo nivel que mi rol más alto. Subí mi rol en la lista de roles del servidor e intentá de nuevo.",
                color=COLOR_WARN,
            )
            return await interaction.followup.send(embed=embed, ephemeral=True)
        roles_data.append({"role_id": rol.id, "label": rol.name, "emoji": emoji})
        lineas_desc.append(f"{emoji + ' ' if emoji else '🔹 '}{rol.mention}")

    if not roles_data:
        embed = build_embed(title="❌ Faltan roles", description="Tenés que indicar al menos un rol (`rol1`).", color=COLOR_WARN)
        return await interaction.followup.send(embed=embed, ephemeral=True)

    embed = discord.Embed(
        title=titulo,
        description=(descripcion + "\n\n" if descripcion else "") + "Hacé clic en un botón para ponerte o quitarte ese rol.\n\n" + "\n".join(lineas_desc),
        color=COLOR_PURPLE,
        timestamp=datetime.now(timezone.utc),
    )
    if interaction.guild.icon:
        embed.set_thumbnail(url=interaction.guild.icon.url)
    embed.set_footer(text=BOT_FOOTER_TEXT)

    view = AutoRolePanelView(roles_data)

    try:
        message = await target.send(embed=embed, view=view)
    except discord.Forbidden:
        embed = build_embed(title="❌ Sin permisos", description=f"No tengo permisos para enviar mensajes en {target.mention}.", color=COLOR_WARN)
        return await interaction.followup.send(embed=embed, ephemeral=True)

    cfg = get_guild_config(interaction.guild_id)
    cfg.setdefault("autorole_panels", []).append({
        "message_id": message.id,
        "channel_id": target.id,
        "roles": roles_data,
    })
    update_guild_config(interaction.guild_id, cfg)

    confirm = build_embed(
        title="✅ Panel publicado",
        description=f"El panel de autoroles se publicó en {target.mention} con {len(roles_data)} rol(es).",
        color=COLOR_OK,
    )
    await interaction.followup.send(embed=confirm, ephemeral=True)


@bot.tree.command(name="panel-autoroles-quitar", description="[Admin] Elimina un panel de autoroles guardado (no borra el mensaje).")
@app_commands.describe(message_id="ID del mensaje del panel (click derecho → Copiar ID)")
@app_commands.checks.has_permissions(administrator=True)
async def panel_autoroles_quitar_cmd(interaction: discord.Interaction, message_id: str):
    track_command(interaction.guild_id, "panel-autoroles-quitar")
    cfg = get_guild_config(interaction.guild_id)
    panels = cfg.get("autorole_panels") or []
    try:
        target_id = int(message_id)
    except ValueError:
        embed = build_embed(title="❌ ID inválido", description="El `message_id` debe ser un número.", color=COLOR_WARN)
        return await interaction.response.send_message(embed=embed, ephemeral=True)

    nuevos = [p for p in panels if p.get("message_id") != target_id]
    if len(nuevos) == len(panels):
        embed = build_embed(title="❌ No encontrado", description="No hay ningún panel guardado con ese `message_id`.", color=COLOR_WARN)
        return await interaction.response.send_message(embed=embed, ephemeral=True)

    cfg["autorole_panels"] = nuevos
    update_guild_config(interaction.guild_id, cfg)
    embed = build_embed(title="🗑️ Panel eliminado", description="Se eliminó de la configuración. Los botones dejarán de funcionar en el mensaje original después del próximo reinicio del bot (o podés borrar el mensaje manualmente).", color=COLOR_OK)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ──────────────────────────────────────────────────────────────
#  GIVEAWAYS (/giveaway)
# ──────────────────────────────────────────────────────────────

class GiveawayView(discord.ui.View):
    """Vista con botón para participar en un sorteo."""

    def __init__(self, prize: str, winners_count: int, host_id: int):
        super().__init__(timeout=None)
        self.prize = prize
        self.winners_count = winners_count
        self.host_id = host_id
        self.participants: set[int] = set()

    @discord.ui.button(label="🎉 Participar (0)", style=discord.ButtonStyle.success, custom_id="giveaway_join")
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id in self.participants:
            self.participants.discard(interaction.user.id)
            await interaction.response.send_message("❌ Saliste del sorteo.", ephemeral=True)
        else:
            self.participants.add(interaction.user.id)
            await interaction.response.send_message("✅ ¡Ya estás participando!", ephemeral=True)

        button.label = f"🎉 Participar ({len(self.participants)})"
        try:
            await interaction.message.edit(view=self)
        except:
            pass

    async def finish(self, message: discord.Message):
        for child in self.children:
            child.disabled = True

        if not self.participants:
            embed = build_embed(
                title="🎁 Sorteo finalizado",
                description=f"**{self.prize}**\n\nNadie participó, no hay ganador. 😔",
                color=COLOR_WARN
            )
            await message.edit(embed=embed, view=self)
            return

        winners = random.sample(list(self.participants), min(self.winners_count, len(self.participants)))
        mentions = ", ".join(f"<@{w}>" for w in winners)

        embed = build_embed(
            title="🎁 Sorteo finalizado",
            description=f"**{self.prize}**\n\n🏆 Ganador(es): {mentions}",
            color=COLOR_OK,
            footer=f"{len(self.participants)} participante(s) en total"
        )
        await message.edit(embed=embed, view=self)
        try:
            await message.reply(f"🎉 ¡Felicidades {mentions}! Ganaste **{self.prize}**.")
        except:
            pass


@bot.tree.command(name="giveaway", description="🎁 Inicia un sorteo en el canal actual.")
@app_commands.describe(premio="Qué se sortea", duracion="Duración: 10m, 1h, 1d", ganadores="Cantidad de ganadores")
@app_commands.checks.has_permissions(manage_guild=True)
async def giveaway_cmd(interaction: discord.Interaction, premio: str, duracion: str, ganadores: app_commands.Range[int, 1, 20] = 1):
    seconds = parse_duration_to_seconds(duracion)
    if seconds is None or seconds > 30 * 86400:
        embed = build_embed(title="❌ Duración inválida", description="Usá un formato como `10m`, `1h`, `1d` (máx. 30 días).", color=COLOR_WARN)
        return await interaction.response.send_message(embed=embed, ephemeral=True)

    view = GiveawayView(premio, ganadores, interaction.user.id)
    ends_at = datetime.now(timezone.utc) + timedelta(seconds=seconds)

    embed = build_embed(
        title="🎁 ¡Nuevo sorteo!",
        description=f"**{premio}**\n\nHacé clic en 🎉 **Participar** para entrar.",
        color=COLOR_PURPLE,
        fields=[
            ("🏆 Ganadores", str(ganadores), True),
            ("⏰ Finaliza", f"<t:{int(ends_at.timestamp())}:R>", True),
        ],
        footer=f"Organizado por {interaction.user.display_name}"
    )
    await interaction.response.send_message(embed=embed, view=view)
    message = await interaction.original_response()

    record_log(interaction.guild_id, "moderacion", f"{interaction.user} inició un sorteo: {premio}", str(interaction.user))

    async def wait_and_finish():
        await asyncio.sleep(seconds)
        try:
            await view.finish(message)
        except:
            pass

    asyncio.create_task(wait_and_finish())


# ──────────────────────────────────────────────────────────────
#  SISTEMA DE VERIFICACIÓN CON CAPTCHA (3 niveles)
# ──────────────────────────────────────────────────────────────

class MathCaptchaModal(discord.ui.Modal):
    """Captcha nivel 1: pregunta matemática simple."""

    def __init__(self, question: str, answer: str):
        super().__init__(title="✅ Verificación — Nivel 1")
        self.expected_answer = answer

        self.respuesta = discord.ui.TextInput(
            label=question,
            placeholder="Escribí el resultado...",
            required=True,
            max_length=10
        )
        self.add_item(self.respuesta)

    async def on_submit(self, interaction: discord.Interaction):
        if self.respuesta.value.strip() != self.expected_answer:
            embed = build_embed(
                title="❌ Respuesta incorrecta",
                description="Volvé a hacer clic en **Verificarme** para intentar de nuevo.",
                color=COLOR_WARN
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        cfg = get_guild_config(interaction.guild_id)
        ok = await apply_verification(interaction.user, cfg)
        if ok:
            embed = build_embed(title="✅ ¡Verificación completa!", description=f"Bienvenido/a a **{interaction.guild.name}**. Ya tenés acceso al servidor.", color=COLOR_OK)
            record_log(interaction.guild_id, "moderacion", f"{interaction.user} se verificó (nivel 1)", str(interaction.user))
        else:
            embed = build_embed(title="⚠️ Verificado, pero...", description="No pude asignarte el rol automáticamente. Avisale a un admin.", color=COLOR_WARN)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class TextCaptchaModal(discord.ui.Modal):
    """Captcha nivel 2/3: código de imagen a transcribir."""

    def __init__(self):
        super().__init__(title="✅ Verificación — Ingresá el código")

        self.codigo = discord.ui.TextInput(
            label="Código de la imagen",
            placeholder="Escribí el código exactamente como lo ves...",
            required=True,
            max_length=10
        )
        self.add_item(self.codigo)

    async def on_submit(self, interaction: discord.Interaction):
        pending = pending_captchas.get(interaction.user.id)

        if not pending or pending["guild_id"] != interaction.guild_id:
            embed = build_embed(title="❌ Captcha expirado", description="Volvé a hacer clic en **Verificarme** para generar uno nuevo.", color=COLOR_WARN)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        if datetime.now(timezone.utc) > pending["expires"]:
            del pending_captchas[interaction.user.id]
            embed = build_embed(title="⏰ Captcha expirado", description="Se venció el tiempo. Hacé clic en **Verificarme** para generar uno nuevo.", color=COLOR_WARN)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        if self.codigo.value.strip().upper() != pending["code"]:
            pending["attempts"] += 1
            max_attempts = 3
            if pending["attempts"] >= max_attempts:
                del pending_captchas[interaction.user.id]
                embed = build_embed(
                    title="❌ Demasiados intentos fallidos",
                    description="Superaste el máximo de intentos. Hacé clic en **Verificarme** de nuevo para generar un código nuevo.",
                    color=COLOR_WARN
                )
            else:
                restantes = max_attempts - pending["attempts"]
                embed = build_embed(
                    title="❌ Código incorrecto",
                    description=f"Te quedan **{restantes}** intento(s). Volvé a hacer clic en **Ingresar código**.",
                    color=COLOR_WARN
                )
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        # Correcto
        del pending_captchas[interaction.user.id]
        cfg = get_guild_config(interaction.guild_id)
        ok = await apply_verification(interaction.user, cfg)
        if ok:
            embed = build_embed(title="✅ ¡Verificación completa!", description=f"Bienvenido/a a **{interaction.guild.name}**. Ya tenés acceso al servidor.", color=COLOR_OK)
            record_log(interaction.guild_id, "moderacion", f"{interaction.user} se verificó (nivel {cfg.get('verify_level', 2)})", str(interaction.user))
        else:
            embed = build_embed(title="⚠️ Verificado, pero...", description="No pude asignarte el rol automáticamente. Avisale a un admin.", color=COLOR_WARN)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class CaptchaAnswerView(discord.ui.View):
    """Botón que abre el modal para escribir el código del captcha de imagen."""

    def __init__(self):
        super().__init__(timeout=180)

    @discord.ui.button(label="✍️ Ingresar código", style=discord.ButtonStyle.primary)
    async def enter_code(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TextCaptchaModal())


class VerifyPanelView(discord.ui.View):
    """Vista persistente del panel de verificación (botón fijo en el mensaje)."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="✅ Verificarme", style=discord.ButtonStyle.success, custom_id="nexus_verify_button")
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        cfg = get_guild_config(interaction.guild_id)

        if not cfg.get("verify_enabled"):
            embed = build_embed(title="❌ Verificación desactivada", description="La verificación no está activa en este servidor.", color=COLOR_WARN)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        verified_role = interaction.guild.get_role(int(cfg["verify_role_id"])) if cfg.get("verify_role_id") else None
        if verified_role and verified_role in interaction.user.roles:
            embed = build_embed(title="✅ Ya estás verificado", description="No necesitás hacer nada más.", color=COLOR_OK)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        level = int(cfg.get("verify_level", 1))

        if level <= 1:
            question, answer = generate_math_question()
            await interaction.response.send_modal(MathCaptchaModal(question, answer))
            return

        code = generate_captcha_code(level)
        pending_captchas[interaction.user.id] = {
            "code": code,
            "guild_id": interaction.guild_id,
            "level": level,
            "attempts": 0,
            "expires": datetime.now(timezone.utc) + timedelta(minutes=3),
        }

        buf = generate_captcha_image(code, level)
        file = discord.File(buf, filename="captcha.png")
        embed = build_embed(
            title=f"🔐 Verificación — Nivel {level}",
            description="Escribí el código que ves en la imagen. Tenés 3 intentos y 3 minutos.",
            color=COLOR_MAIN,
            image="attachment://captcha.png"
        )
        await interaction.response.send_message(embed=embed, file=file, view=CaptchaAnswerView(), ephemeral=True)


@bot.tree.command(name="config-verificacion", description="[Admin] Configura el sistema de verificación con captcha.")
@app_commands.describe(
    rol_verificado="Rol que se da al verificarse (ej: Members)",
    rol_no_verificado="Rol que tienen los nuevos hasta verificarse (ej: no verificados)",
    nivel="Dificultad del captcha: 1 fácil, 2 medio, 3 difícil",
    canal="Canal donde se va a publicar el panel (opcional)"
)
@app_commands.choices(nivel=[
    app_commands.Choice(name="Nivel 1 — Matemática simple", value=1),
    app_commands.Choice(name="Nivel 2 — Imagen con código", value=2),
    app_commands.Choice(name="Nivel 3 — Imagen difícil", value=3),
])
@app_commands.checks.has_permissions(administrator=True)
async def config_verificacion_cmd(
    interaction: discord.Interaction,
    rol_verificado: discord.Role,
    rol_no_verificado: discord.Role,
    nivel: app_commands.Choice[int],
    canal: discord.TextChannel | None = None
):
    cfg = get_guild_config(interaction.guild_id)
    cfg["verify_enabled"] = True
    cfg["verify_role_id"] = rol_verificado.id
    cfg["verify_unverified_role_id"] = rol_no_verificado.id
    cfg["verify_level"] = nivel.value
    if canal:
        cfg["verify_channel_id"] = canal.id
    update_guild_config(interaction.guild_id, cfg)

    embed = build_embed(
        title="🔐 Verificación configurada",
        description="El sistema de verificación quedó activo. Usá `/panel-verificacion` para publicar el botón.",
        color=COLOR_OK,
        fields=[
            ("✅ Rol verificado", rol_verificado.mention, True),
            ("🚫 Rol sin verificar", rol_no_verificado.mention, True),
            ("🎯 Nivel", nivel.name, True),
        ]
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="panel-verificacion", description="[Admin] Publica el panel con el botón de verificación.")
@app_commands.describe(canal="Canal donde publicar el panel (por defecto el canal actual)")
@app_commands.checks.has_permissions(administrator=True)
async def panel_verificacion_cmd(interaction: discord.Interaction, canal: discord.TextChannel | None = None):
    cfg = get_guild_config(interaction.guild_id)
    if not cfg.get("verify_enabled"):
        embed = build_embed(title="❌ Falta configurar", description="Primero corré `/config-verificacion` para configurar los roles y el nivel.", color=COLOR_WARN)
        return await interaction.response.send_message(embed=embed, ephemeral=True)

    target = canal or interaction.channel
    nivel = cfg.get("verify_level", 1)
    nivel_texto = {1: "Fácil (matemática)", 2: "Medio (imagen)", 3: "Difícil (imagen)"}.get(nivel, "Fácil")

    embed = build_embed(
        title="🔐 Verificación requerida",
        description=(
            f"Para acceder al resto de **{interaction.guild.name}**, hacé clic en **✅ Verificarme** "
            f"y completá el captcha.\n\n🎯 Nivel actual: **{nivel_texto}**"
        ),
        color=COLOR_MAIN,
        thumbnail=interaction.guild.icon.url if interaction.guild.icon else None
    )
    await target.send(embed=embed, view=VerifyPanelView())

    confirm = build_embed(title="✅ Panel publicado", description=f"El panel de verificación se publicó en {target.mention}.", color=COLOR_OK)
    await interaction.response.send_message(embed=confirm, ephemeral=True)


# ──────────────────────────────────────────────────────────────
#  MÁS UTILIDADES PARA ADMINS
# ──────────────────────────────────────────────────────────────

@bot.tree.command(name="roleinfo", description="🎭 Muestra información detallada de un rol.")
@app_commands.describe(rol="El rol a consultar")
async def roleinfo_cmd(interaction: discord.Interaction, rol: discord.Role):
    track_command(interaction.guild_id, "roleinfo")

    permisos_clave = []
    perms = rol.permissions
    for nombre, activo in [
        ("Administrador", perms.administrator),
        ("Gestionar servidor", perms.manage_guild),
        ("Gestionar roles", perms.manage_roles),
        ("Gestionar canales", perms.manage_channels),
        ("Banear miembros", perms.ban_members),
        ("Expulsar miembros", perms.kick_members),
        ("Gestionar mensajes", perms.manage_messages),
        ("Mencionar @everyone", perms.mention_everyone),
    ]:
        if activo:
            permisos_clave.append(f"✅ {nombre}")

    embed = discord.Embed(
        title=f"🎭 Información del rol · {rol.name}",
        color=rol.color if rol.color.value else COLOR_MAIN,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="🆔 ID", value=f"`{rol.id}`", inline=True)
    embed.add_field(name="👥 Miembros", value=str(len(rol.members)), inline=True)
    embed.add_field(name="📊 Posición", value=str(rol.position), inline=True)
    embed.add_field(name="🎨 Color", value=str(rol.color), inline=True)
    embed.add_field(name="📌 Mencionable", value="Sí" if rol.mentionable else "No", inline=True)
    embed.add_field(name="🔝 Se muestra aparte", value="Sí" if rol.hoist else "No", inline=True)
    embed.add_field(name="📅 Creado", value=f"<t:{int(rol.created_at.timestamp())}:R>", inline=True)
    embed.add_field(name="🤖 Gestionado por integración", value="Sí" if rol.managed else "No", inline=True)
    embed.add_field(
        name="🔑 Permisos clave",
        value="\n".join(permisos_clave) if permisos_clave else "Ninguno relevante",
        inline=False,
    )
    embed.set_footer(text=BOT_FOOTER_TEXT)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="botinfo", description="🤖 Muestra el estado técnico del bot.")
async def botinfo_cmd(interaction: discord.Interaction):
    track_command(interaction.guild_id, "botinfo")

    uptime = format_uptime(time.time() - BOT_START_TIME)
    latencia = round(bot.latency * 1000)
    total_miembros = sum(g.member_count or 0 for g in bot.guilds)

    embed = discord.Embed(
        title="🤖 Estado de Nexus",
        color=COLOR_MAIN,
        timestamp=datetime.now(timezone.utc),
    )
    if bot.user:
        embed.set_thumbnail(url=bot.user.display_avatar.url)
    embed.add_field(name="📶 Latencia", value=f"{latencia}ms", inline=True)
    embed.add_field(name="⏱️ Uptime", value=uptime, inline=True)
    embed.add_field(name="🌐 Servidores", value=str(len(bot.guilds)), inline=True)
    embed.add_field(name="👥 Miembros totales", value=str(total_miembros), inline=True)
    embed.add_field(name="⚡ Comandos slash", value=str(len(bot.tree.get_commands())), inline=True)
    embed.add_field(name="🎵 FFmpeg", value="✅ Disponible" if FFMPEG_AVAILABLE else "❌ No encontrado", inline=True)
    embed.add_field(name="🔐 Davey (voz E2EE)", value="✅ Disponible" if DAVEY_AVAILABLE else "❌ No encontrado", inline=True)
    embed.add_field(name="🗄️ Base de datos", value="✅ Supabase" if supabase else "📁 Archivos locales", inline=True)
    embed.add_field(name="🎶 Reproduciendo música en", value=f"{sum(1 for s in music_states.values() if s.is_playing())} servidor(es)", inline=True)
    embed.set_footer(text=BOT_FOOTER_TEXT)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="purge-usuario", description="[Mod] Elimina los últimos mensajes de un usuario específico en este canal.")
@app_commands.describe(
    usuario="El usuario cuyos mensajes se van a borrar",
    cantidad="Cuántos mensajes recientes revisar como máximo (1-200, por defecto 100)",
)
@app_commands.checks.has_permissions(manage_messages=True)
async def purge_usuario_cmd(interaction: discord.Interaction, usuario: discord.Member, cantidad: app_commands.Range[int, 1, 200] = 100):
    track_command(interaction.guild_id, "purge-usuario")
    await interaction.response.defer(ephemeral=True, thinking=True)

    def check(m: discord.Message) -> bool:
        return m.author.id == usuario.id

    try:
        borrados = await interaction.channel.purge(limit=cantidad, check=check)
    except discord.Forbidden:
        embed = build_embed(title="❌ Sin permisos", description="No tengo permisos para borrar mensajes en este canal.", color=COLOR_WARN)
        return await interaction.followup.send(embed=embed, ephemeral=True)

    embed = build_embed(
        title="🧹 Mensajes eliminados",
        description=f"Se eliminaron **{len(borrados)}** mensaje(s) de {usuario.mention} (de los últimos {cantidad} revisados).",
        color=COLOR_OK,
    )
    await interaction.followup.send(embed=embed, ephemeral=True)


app = Flask(__name__)

@app.route("/")
def home():
    return "Nexus bot está vivo."

@app.route("/ping")
def ping_route():
    return "pong"

@app.route("/health")
def health_route():
    return {
        "status": "online",
        "uptime": format_uptime(time.time() - BOT_START_TIME),
        "guilds": len(bot.guilds),
        "bot": bot.user.name if bot.user else "Desconocido"
    }

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

def keep_alive():
    t = threading.Thread(target=run_web)
    t.daemon = True
    t.start()
    print("🔥 Servidor web iniciado")

# ──────────────────────────────────────────────────────────────
#  AUTO KEEP-ALIVE (PING A SÍ MISMO)
# ──────────────────────────────────────────────────────────────

import requests
import time

def auto_keep_alive():
    """Hace ping a sí mismo cada 5 minutos para evitar que Render duerma"""
    url = "https://bot-nexus-1-nego.onrender.com/ping"
    while True:
        try:
            response = requests.get(url, timeout=10)
            print(f"✅ Keep-alive ping: {response.status_code}")
        except Exception as e:
            print(f"⚠️ Error en keep-alive: {e}")
        time.sleep(300)  # 5 minutos

def start_keep_alive():
    """Inicia el keep-alive en un hilo separado"""
    thread = threading.Thread(target=auto_keep_alive, daemon=True)
    thread.start()
    print("🔥 Keep-alive iniciado (ping cada 5 minutos)")

# ──────────────────────────────────────────────────────────────
#  MAIN
# ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("Falta la variable de entorno DISCORD_TOKEN.")
    
    # ✅ Iniciar keep-alive (ping a sí mismo)
    start_keep_alive()
    
    # ✅ Iniciar servidor web (Flask)
    keep_alive()
    
    # ✅ Iniciar bot de Discord
    bot.run(TOKEN)
