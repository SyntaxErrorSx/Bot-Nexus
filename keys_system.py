import os
import random
import string
import smtplib
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import discord
from discord import app_commands
import requests

# ──────────────────────────────────────────────────────────────
#  CONFIG / CREDENCIALES (todas desde variables de entorno)
# ──────────────────────────────────────────────────────────────

SUPABASE_URL = os.getenv("SUPABASE_URL", "").replace("/rest/v1", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

EMAIL_SENDER = os.getenv("EMAIL_SENDER", "")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))

NEXUS_DOWNLOAD_URL = os.getenv("NEXUS_DOWNLOAD_URL", "")
NEXUS_BROWSER_URL = os.getenv("NEXUS_BROWSER_URL", "")

COOLDOWN_HOURS = 24
KEYS_TABLE = "licenses"
COOLDOWN_TABLE = "key_cooldowns"


def _licenses_configured() -> bool:
    return bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY)


def _service_headers() -> dict:
    return {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


# ──────────────────────────────────────────────────────────────
#  GENERACIÓN DE LA KEY + INSERCIÓN EN SUPABASE
# ──────────────────────────────────────────────────────────────

def _generate_key_code() -> str:
    def seg():
        return "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
    return f"NEXUS-{seg()}-{seg()}-{seg()}"


def create_license(email: str, duration_days: int) -> str:
    """Crea la licencia en Supabase (vía service role key) y devuelve la key generada."""
    if not _licenses_configured():
        raise RuntimeError(
            "Faltan SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY en las variables de entorno."
        )

    new_key = _generate_key_code()
    payload = {
        "key": new_key,
        "email": email.strip().lower(),
        "registered_hwid": None,
        "reset_count": 0,
        "max_resets": 5,
        "is_active": True,
        "duration_days": duration_days,
    }
    r = requests.post(f"{SUPABASE_URL}/rest/v1/{KEYS_TABLE}", headers=_service_headers(), json=payload, timeout=15)
    r.raise_for_status()
    return new_key


# ──────────────────────────────────────────────────────────────
#  ENVÍO DE CORREO
# ──────────────────────────────────────────────────────────────

def send_key_email(email: str, client_name: str, key: str, duration_days: int) -> bool:
    """Envía la key por correo. Si no hay SMTP configurado, no rompe: solo devuelve False."""
    if not EMAIL_SENDER or not EMAIL_PASSWORD:
        print("[keys_system] ⚠️ SMTP no configurado, no se envió el correo (solo Discord).")
        return False

    saludo = f"Hola {client_name}," if client_name else "Hola,"
    subject = "🎀 Tu clave de acceso para NEXUS STUDIOS está lista"

    body_text = f"""{saludo}

Tu clave de acceso exclusiva para activar el aplicativo es:
{key}

📅 Duración de tu licencia: {duration_days} día(s)

{f"📥 Descarga: {NEXUS_DOWNLOAD_URL}" if NEXUS_DOWNLOAD_URL else ""}
{f"🌐 Nexus Browser: {NEXUS_BROWSER_URL}" if NEXUS_BROWSER_URL else ""}

Conservá esta licencia, será necesaria para futuras reinstalaciones.

— El equipo de NEXUS STUDIOS
"""

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = EMAIL_SENDER
        msg["To"] = email
        msg.attach(MIMEText(body_text, "plain", "utf-8"))

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, email, msg.as_string())
        return True
    except Exception as e:
        print(f"[keys_system] ❌ Error enviando correo: {e}")
        return False


# ──────────────────────────────────────────────────────────────
#  COOLDOWN (persistido en Supabase para sobrevivir reinicios)
# ──────────────────────────────────────────────────────────────

def _check_and_set_cooldown(supabase_get, supabase_set, guild_id: int, user_id: int) -> timedelta | None:
    """
    Devuelve None si el usuario puede pedir una key ahora (y ya registra el pedido).
    Devuelve el tiempo restante (timedelta) si todavía está en cooldown.
    """
    data = supabase_get(guild_id, COOLDOWN_TABLE) or {}
    key = str(user_id)
    now = datetime.now(timezone.utc)

    last_str = data.get(key)
    if last_str:
        try:
            last = datetime.fromisoformat(last_str)
            elapsed = now - last
            if elapsed < timedelta(hours=COOLDOWN_HOURS):
                return timedelta(hours=COOLDOWN_HOURS) - elapsed
        except ValueError:
            pass

    data[key] = now.isoformat()
    supabase_set(guild_id, COOLDOWN_TABLE, data)
    return None


def _format_timedelta(td: timedelta) -> str:
    total_minutes = int(td.total_seconds() // 60)
    hours, minutes = divmod(total_minutes, 60)
    if hours > 0:
        return f"{hours}h {minutes}min"
    return f"{minutes}min"


# ──────────────────────────────────────────────────────────────
#  UI: MODAL DE CORREO
# ──────────────────────────────────────────────────────────────

class EmailModal(discord.ui.Modal, title="Solicitar Key — NEXUS STUDIOS"):
    def __init__(self, duration_days: int, build_embed, colors: dict, footer_text: str,
                 supabase_get, supabase_set):
        super().__init__(timeout=180)
        self.duration_days = duration_days
        self.build_embed = build_embed
        self.colors = colors
        self.footer_text = footer_text
        self.supabase_get = supabase_get
        self.supabase_set = supabase_set

    correo = discord.ui.TextInput(
        label="Tu correo electrónico",
        placeholder="ejemplo@gmail.com",
        required=True,
        max_length=120,
    )

    async def on_submit(self, interaction: discord.Interaction):
        email = self.correo.value.strip()
        if "@" not in email or "." not in email:
            embed = self.build_embed(title="❌ Correo inválido", description="Escribí un correo válido e intentá de nuevo.", color=self.colors["WARN"])
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        await interaction.response.defer(ephemeral=True, thinking=True)

        restante = _check_and_set_cooldown(self.supabase_get, self.supabase_set, interaction.guild_id, interaction.user.id)
        if restante is not None:
            embed = self.build_embed(
                title="⏳ Todavía no podés pedir otra key",
                description=f"Ya pediste una key recientemente. Podés volver a pedir en **{_format_timedelta(restante)}**.",
                color=self.colors["WARN"],
                footer=self.footer_text,
            )
            return await interaction.followup.send(embed=embed, ephemeral=True)

        try:
            key = create_license(email, self.duration_days)
        except Exception as e:
            embed = self.build_embed(title="❌ Error generando la key", description=f"No se pudo crear la licencia: `{e}`", color=self.colors["WARN"], footer=self.footer_text)
            return await interaction.followup.send(embed=embed, ephemeral=True)

        emailed = send_key_email(email, interaction.user.display_name, key, self.duration_days)

        embed = self.build_embed(
            title="✅ ¡Tu key está lista!",
            description=(
                f"🔑 **Key:** `{key}`\n"
                f"📅 **Duración:** {self.duration_days} día(s)\n"
                f"📧 **Enviada a:** {email} {'✅' if emailed else '⚠️ (no se pudo enviar por correo, guardá esta key)'}"
            ),
            color=self.colors["OK"],
            footer=self.footer_text,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

        # DM adicional (además del correo)
        try:
            dm_embed = self.build_embed(
                title="🔑 Tu key de NEXUS STUDIOS",
                description=f"`{key}`\n\nDuración: **{self.duration_days} día(s)**",
                color=self.colors["OK"],
                footer=self.footer_text,
            )
            await interaction.user.send(embed=dm_embed)
        except discord.Forbidden:
            pass  # el usuario tiene los DMs cerrados, no pasa nada


# ──────────────────────────────────────────────────────────────
#  UI: SELECT DE DURACIÓN (Nexus+ / VIP → 6 a 10 días)
# ──────────────────────────────────────────────────────────────

class DurationSelect(discord.ui.Select):
    def __init__(self, build_embed, colors: dict, footer_text: str, supabase_get, supabase_set):
        options = [discord.SelectOption(label=f"{d} días", value=str(d)) for d in range(6, 11)]
        super().__init__(placeholder="Elegí la duración de tu key...", options=options, min_values=1, max_values=1)
        self.build_embed = build_embed
        self.colors = colors
        self.footer_text = footer_text
        self.supabase_get = supabase_get
        self.supabase_set = supabase_set

    async def callback(self, interaction: discord.Interaction):
        duration = int(self.values[0])
        await interaction.response.send_modal(
            EmailModal(duration, self.build_embed, self.colors, self.footer_text, self.supabase_get, self.supabase_set)
        )


class DurationSelectView(discord.ui.View):
    def __init__(self, build_embed, colors: dict, footer_text: str, supabase_get, supabase_set):
        super().__init__(timeout=120)
        self.add_item(DurationSelect(build_embed, colors, footer_text, supabase_get, supabase_set))


# ──────────────────────────────────────────────────────────────
#  UI: PANEL PRINCIPAL (persistente)
# ──────────────────────────────────────────────────────────────

def build_keys_panel_view(config_getter, build_embed, colors: dict, footer_text: str, supabase_get, supabase_set):
    """Fábrica que crea la View persistente del panel, ya con las dependencias inyectadas."""

    class KeysPanelView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)

        @discord.ui.button(label="🔑 Solicitar Key", style=discord.ButtonStyle.success, custom_id="nexus_keys_request_button")
        async def request_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            cfg = config_getter()
            member = interaction.user

            nexus_plus_id = cfg.get("NEXUS_PLUS_ROLE_ID")
            vip_id = cfg.get("VIP_ROLE_ID")
            role_ids = {r.id for r in getattr(member, "roles", [])}

            is_top_tier = (nexus_plus_id and nexus_plus_id in role_ids) or (vip_id and vip_id in role_ids)

            if is_top_tier:
                embed = build_embed(
                    title="🔑 Elegí la duración",
                    description="Como Nexus+/VIP podés elegir entre 6 y 10 días.",
                    color=colors["MAIN"],
                    footer=footer_text,
                )
                view = DurationSelectView(build_embed, colors, footer_text, supabase_get, supabase_set)
                return await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

            # Todo el resto (usuarios normales e Insiders): 5 días fijos
            await interaction.response.send_modal(
                EmailModal(5, build_embed, colors, footer_text, supabase_get, supabase_set)
            )

    return KeysPanelView


# ──────────────────────────────────────────────────────────────
#  REGISTRO DE COMANDOS
# ──────────────────────────────────────────────────────────────

def setup_keys_system(bot, Config, build_embed, colors: dict, footer_text: str, supabase_get, supabase_set):
    """
    Llamá esta función UNA VEZ desde bot.py, después de que 'bot', 'Config',
    'build_embed', 'supabase_get' y 'supabase_set' ya estén definidos.

    colors: dict con las keys "MAIN", "OK", "WARN" apuntando a tus discord.Color existentes.

    Devuelve la clase KeysPanelView, para que la registres en on_ready con bot.add_view(...).
    """

    def _config_getter():
        return {
            "NEXUS_PLUS_ROLE_ID": Config.NEXUS_PLUS_ROLE_ID or None,
            "VIP_ROLE_ID": Config.VIP_ROLE_ID or None,
        }

    KeysPanelView = build_keys_panel_view(_config_getter, build_embed, colors, footer_text, supabase_get, supabase_set)

    @bot.tree.command(name="generar-key", description="[Admin] Genera y envía una key manualmente a un cliente.")
    @app_commands.describe(correo="Correo del cliente", nombre="Nombre del cliente", dias="Duración de la key en días")
    @app_commands.checks.has_permissions(administrator=True)
    async def generar_key_cmd(interaction: discord.Interaction, correo: str, nombre: str, dias: app_commands.Range[int, 1, 365] = 30):
        if "@" not in correo or "." not in correo:
            embed = build_embed(title="❌ Correo inválido", description="Escribí un correo válido.", color=colors["WARN"])
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            key = create_license(correo, dias)
        except Exception as e:
            embed = build_embed(title="❌ Error", description=f"No se pudo generar la key: `{e}`", color=colors["WARN"], footer=footer_text)
            return await interaction.followup.send(embed=embed, ephemeral=True)

        emailed = send_key_email(correo, nombre, key, dias)

        embed = build_embed(
            title="✅ Key generada",
            description=(
                f"🔑 **Key:** `{key}`\n"
                f"👤 **Cliente:** {nombre}\n"
                f"📧 **Correo:** {correo} {'✅' if emailed else '⚠️ no se pudo enviar el correo'}\n"
                f"📅 **Duración:** {dias} día(s)"
            ),
            color=colors["OK"],
            footer=footer_text,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @bot.tree.command(name="panel-keys", description="[Admin] Publica el panel de auto-pedido de keys.")
    @app_commands.describe(canal="Canal donde publicar el panel (por defecto el canal actual)")
    @app_commands.checks.has_permissions(administrator=True)
    async def panel_keys_cmd(interaction: discord.Interaction, canal: discord.TextChannel | None = None):
        target = canal or interaction.channel

        embed = build_embed(
            title="🔑 Panel de Keys — NEXUS STUDIOS",
            description=(
                "Hacé clic en el botón para pedir tu key.\n\n"
                "• **Insiders / usuarios:** key de **5 días**.\n"
                "• **Nexus+ / VIP:** elegís entre **6 y 10 días**.\n\n"
                "⏳ Solo se puede pedir **una key cada 24 horas**."
            ),
            color=colors["MAIN"],
            footer=footer_text,
        )
        await target.send(embed=embed, view=KeysPanelView())

        confirm = build_embed(title="✅ Panel publicado", description=f"El panel de keys se publicó en {target.mention}.", color=colors["OK"])
        await interaction.response.send_message(embed=confirm, ephemeral=True)

    return KeysPanelView