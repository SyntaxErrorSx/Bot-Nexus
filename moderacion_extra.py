import asyncio
import json
import os
import re
import time
from datetime import datetime, timezone, timedelta

import discord
from discord import app_commands

from emojis import EMOJIS

# ──────────────────────────────────────────────────────────────
#  PATRONES ANTI-SCAM (links / textos típicos de estafas de cripto,
#  nitro falso, "free steam gift", etc.)
# ──────────────────────────────────────────────────────────────

SCAM_DOMAIN_PATTERNS = [
    r"discord-?nitro\w*\.(com|net|org|gift|xyz|info|ru)",
    r"discordgift\w*\.(com|net|org|gift|xyz)",
    r"steamcommunlty\.com",  # typosquat de steamcommunity
    r"steam-?community\w*\.(ru|xyz|top|info)",
    r"dlscord\.\w+",  # 'l' en vez de 'i'
    r"telegram-?airdr[o0]p\w*\.\w+",
    r"free-?nitro\w*\.\w+",
    r"claim-?nitro\w*\.\w+",
    r"binance-?(gift|airdr[o0]p|bonus)\w*\.\w+",
    r"metamask-?(support|help|verify)\w*\.\w+",
]
SCAM_DOMAIN_RE = re.compile("|".join(SCAM_DOMAIN_PATTERNS), re.IGNORECASE)

SCAM_TEXT_HINTS = [
    "airdrop gratis", "free airdrop", "regalo de nitro", "nitro gratis",
    "duplica tus", "duplicación de cripto", "double your btc", "double your crypto",
    "conecta tu wallet", "connect your wallet para reclamar", "verifica tu wallet",
]


def message_has_scam_pattern(content: str) -> bool:
    lowered = content.lower()
    if SCAM_DOMAIN_RE.search(lowered):
        return True
    return any(hint in lowered for hint in SCAM_TEXT_HINTS)


# ──────────────────────────────────────────────────────────────
#  PERSISTENCIA LOCAL (fallback si no hay Supabase)
# ──────────────────────────────────────────────────────────────

JAIL_PATH = "jail.json"
GLOBAL_LIST_PATH = "global_lists.json"
_jail_lock = asyncio.Lock()


def _load_json(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return {}


def _save_json(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def setup_moderacion_extra(
    *,
    bot,
    Config,
    build_embed,
    colors: dict,
    footer_text: str,
    supabase_get,
    supabase_set,
    get_guild_config,
    update_guild_config,
    record_log,
):
    COLOR_MAIN = colors.get("MAIN")
    COLOR_OK = colors.get("OK")
    COLOR_WARN = colors.get("WARN")
    COLOR_AMBER = colors.get("AMBER", colors.get("WARN"))

    def is_owner(interaction: discord.Interaction) -> bool:
        return interaction.user.id == Config.OWNER_ID

    # ────────────────────────────────────────────────────────
    #  GLOBAL WHITELIST / BLACKLIST (cruza todos los servers)
    # ────────────────────────────────────────────────────────

    def _global_get(kind: str) -> dict:
        """kind: 'global_blacklist' o 'global_whitelist'. Usa Supabase (guild_id='all')
        con fallback a un JSON local si Supabase no está configurado."""
        data = supabase_get(0, kind)  # guild_id 0 = tabla global compartida
        if data is not None:
            return data
        return _load_json(GLOBAL_LIST_PATH).get(kind, {})

    def _global_set(kind: str, data: dict) -> None:
        if not supabase_set(0, kind, data):
            all_data = _load_json(GLOBAL_LIST_PATH)
            all_data[kind] = data
            _save_json(GLOBAL_LIST_PATH, all_data)

    def is_globally_blacklisted(user_id: int) -> str | None:
        data = _global_get("global_blacklist")
        entry = data.get(str(user_id))
        return entry.get("razon") if entry else None

    def is_globally_whitelisted(user_id: int) -> bool:
        data = _global_get("global_whitelist")
        return str(user_id) in data

    # ────────────────────────────────────────────────────────
    #  WHITELIST LOCAL (solo exime del automod de ESTE server,
    #  a diferencia de la global que aplica en todos)
    # ────────────────────────────────────────────────────────

    def is_locally_whitelisted(guild_id: int, user_id: int) -> bool:
        cfg = get_guild_config(guild_id)
        return str(user_id) in cfg.get("automod_whitelist", [])

    # ────────────────────────────────────────────────────────
    #  KICKER DE BOTS NO VERIFICADOS + CHEQUEO ANTI-RAID AL ENTRAR
    # ────────────────────────────────────────────────────────

    @bot.tree.command(name="config-bot-kicker", description="[Admin] Activa/desactiva la expulsión automática de bots no autorizados.")
    @app_commands.describe(estado="Activar o desactivar")
    @app_commands.choices(estado=[
        app_commands.Choice(name="activar", value="activar"),
        app_commands.Choice(name="desactivar", value="desactivar"),
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def config_bot_kicker_cmd(interaction: discord.Interaction, estado: app_commands.Choice[str]):
        cfg = get_guild_config(interaction.guild_id)
        cfg["bot_kicker_enabled"] = (estado.value == "activar")
        update_guild_config(interaction.guild_id, cfg)
        desc = (
            "Ahora se expulsará automáticamente a cualquier bot que se una y no esté en la whitelist de bots del server."
            if cfg["bot_kicker_enabled"] else
            "El kicker de bots fue desactivado."
        )
        embed = build_embed(title=f"{EMOJIS['checkmark']} Bot-kicker actualizado", description=desc, color=COLOR_OK)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.tree.command(name="bot-whitelist", description="[Admin] Administra qué bots pueden unirse sin ser expulsados.")
    @app_commands.describe(accion="agregar / quitar / lista", bot_id="ID del bot")
    @app_commands.choices(accion=[
        app_commands.Choice(name="agregar", value="agregar"),
        app_commands.Choice(name="quitar", value="quitar"),
        app_commands.Choice(name="lista", value="lista"),
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def bot_whitelist_cmd(interaction: discord.Interaction, accion: app_commands.Choice[str], bot_id: str | None = None):
        cfg = get_guild_config(interaction.guild_id)
        whitelist = cfg.setdefault("bot_whitelist", [])

        if accion.value == "lista":
            desc = "\n".join(f"`{b}`" for b in whitelist) if whitelist else "Vacía."
            embed = build_embed(title=f"📋 Bots en whitelist ({len(whitelist)})", description=desc, color=COLOR_MAIN)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        if not bot_id or not bot_id.isdigit():
            embed = build_embed(title="❌ Falta el ID del bot", color=COLOR_WARN)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        if accion.value == "agregar":
            if bot_id not in whitelist:
                whitelist.append(bot_id)
            desc = f"`{bot_id}` puede unirse sin ser expulsado."
        else:
            if bot_id in whitelist:
                whitelist.remove(bot_id)
            desc = f"`{bot_id}` fue quitado de la whitelist."

        update_guild_config(interaction.guild_id, cfg)
        embed = build_embed(title=f"{EMOJIS['checkmark']} Whitelist de bots actualizada", description=desc, color=COLOR_OK)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def on_member_join_antiraid(member: discord.Member):
        guild = member.guild
        cfg = get_guild_config(guild.id)

        # 1) Blacklist global (aplica a cualquier miembro, sea bot o no)
        if not is_globally_whitelisted(member.id):
            razon = is_globally_blacklisted(member.id)
            if razon:
                try:
                    await member.ban(reason=f"Blacklist global: {razon}")
                    record_log(guild.id, "moderacion", f"{EMOJIS['olhos70']} {member} detectado y baneado automáticamente (blacklist global: {razon})", "Sistema")
                except discord.Forbidden:
                    pass
                return

        # 2) Kicker de bots no verificados/no autorizados
        if member.bot and cfg.get("bot_kicker_enabled"):
            whitelist = set(cfg.get("bot_whitelist", []))
            if str(member.id) not in whitelist:
                try:
                    await member.kick(reason="Bot no autorizado (no está en la whitelist del server)")
                    record_log(guild.id, "moderacion", f"{EMOJIS['terminal']} Bot {member} expulsado automáticamente (no está en whitelist)", "Sistema")
                except discord.Forbidden:
                    pass

    bot.add_listener(on_member_join_antiraid, "on_member_join")

    # ────────────────────────────────────────────────────────
    #  BLACKLIST DE PALABRAS + ANTI-SCAM + ANTI-@EVERYONE
    # ────────────────────────────────────────────────────────

    @bot.tree.command(name="blacklist-palabra", description="[Admin] Administra palabras prohibidas (se borran automáticamente).")
    @app_commands.describe(accion="agregar / quitar / lista", palabra="Palabra o frase a filtrar")
    @app_commands.choices(accion=[
        app_commands.Choice(name="agregar", value="agregar"),
        app_commands.Choice(name="quitar", value="quitar"),
        app_commands.Choice(name="lista", value="lista"),
    ])
    @app_commands.checks.has_permissions(manage_messages=True)
    async def blacklist_palabra_cmd(interaction: discord.Interaction, accion: app_commands.Choice[str], palabra: str | None = None):
        cfg = get_guild_config(interaction.guild_id)
        palabras = cfg.setdefault("blacklist_words", [])

        if accion.value == "lista":
            desc = ", ".join(f"`{p}`" for p in palabras) if palabras else "Vacía."
            embed = build_embed(title=f"📋 Palabras bloqueadas ({len(palabras)})", description=desc, color=COLOR_MAIN)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        if not palabra:
            embed = build_embed(title="❌ Falta la palabra", color=COLOR_WARN)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        palabra_norm = palabra.strip().lower()
        if accion.value == "agregar":
            if palabra_norm not in palabras:
                palabras.append(palabra_norm)
            desc = f"`{palabra_norm}` agregada a la blacklist."
        else:
            if palabra_norm in palabras:
                palabras.remove(palabra_norm)
            desc = f"`{palabra_norm}` quitada de la blacklist."

        update_guild_config(interaction.guild_id, cfg)
        embed = build_embed(title=f"{EMOJIS['checkmark']} Blacklist actualizada", description=desc, color=COLOR_OK)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.tree.command(name="config-antiscam", description="[Admin] Activa/desactiva el filtro anti-estafas (links falsos de nitro/cripto).")
    @app_commands.describe(estado="Activar o desactivar")
    @app_commands.choices(estado=[
        app_commands.Choice(name="activar", value="activar"),
        app_commands.Choice(name="desactivar", value="desactivar"),
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def config_antiscam_cmd(interaction: discord.Interaction, estado: app_commands.Choice[str]):
        cfg = get_guild_config(interaction.guild_id)
        cfg["antiscam_enabled"] = (estado.value == "activar")
        update_guild_config(interaction.guild_id, cfg)
        desc = "Se borrarán mensajes con links/textos típicos de estafas (nitro falso, airdrops, etc.) y se muteará al autor." if cfg["antiscam_enabled"] else "Anti-scam desactivado."
        embed = build_embed(title=f"{EMOJIS['checkmark']} Anti-scam actualizado", description=desc, color=COLOR_OK)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.tree.command(name="config-antieveryone", description="[Admin] Activa/desactiva el bloqueo de @everyone/@here abusivo.")
    @app_commands.describe(estado="Activar o desactivar")
    @app_commands.choices(estado=[
        app_commands.Choice(name="activar", value="activar"),
        app_commands.Choice(name="desactivar", value="desactivar"),
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def config_antieveryone_cmd(interaction: discord.Interaction, estado: app_commands.Choice[str]):
        cfg = get_guild_config(interaction.guild_id)
        cfg["antieveryone_enabled"] = (estado.value == "activar")
        update_guild_config(interaction.guild_id, cfg)
        desc = "Se borrará cualquier @everyone/@here de usuarios sin permiso de gestión del server." if cfg["antieveryone_enabled"] else "Anti-@everyone desactivado."
        embed = build_embed(title=f"{EMOJIS['checkmark']} Anti-@everyone actualizado", description=desc, color=COLOR_OK)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def on_message_moderacion_extra(message: discord.Message):
        if message.author.bot or not message.guild:
            return
        if message.author.guild_permissions.administrator:
            return
        if is_globally_whitelisted(message.author.id):
            return
        if is_locally_whitelisted(message.guild.id, message.author.id):
            return

        cfg = get_guild_config(message.guild.id)
        content_lower = message.content.lower()

        # Blacklist de palabras
        for palabra in cfg.get("blacklist_words", []):
            if palabra in content_lower:
                try:
                    await message.delete()
                except discord.NotFound:
                    pass
                try:
                    await message.channel.send(
                        f"{EMOJIS['ban']} {message.author.mention}, ese mensaje contenía una palabra prohibida.",
                        delete_after=6,
                    )
                except discord.Forbidden:
                    pass
                record_log(message.guild.id, "moderacion", f"🚫 Mensaje de {message.author} borrado (palabra prohibida: `{palabra}`)", "Sistema")
                return

        # Anti-scam
        if cfg.get("antiscam_enabled") and message_has_scam_pattern(message.content):
            try:
                await message.delete()
            except discord.NotFound:
                pass
            try:
                if message.guild.me.guild_permissions.moderate_members and message.author.top_role < message.guild.me.top_role:
                    await message.author.timeout(discord.utils.utcnow() + timedelta(hours=1), reason="Mensaje de estafa detectado")
            except discord.Forbidden:
                pass
            try:
                await message.channel.send(
                    f"{EMOJIS['ban']} Se borró un mensaje de {message.author.mention} por parecer una estafa (nitro/cripto falso).",
                    delete_after=8,
                )
            except discord.Forbidden:
                pass
            record_log(message.guild.id, "moderacion", f"🚨 Mensaje de estafa de {message.author} borrado y muteado 1h", "Sistema")
            return

        # Anti-@everyone / @here
        if cfg.get("antieveryone_enabled") and message.mention_everyone and not message.author.guild_permissions.mention_everyone:
            try:
                await message.delete()
            except discord.NotFound:
                pass
            try:
                await message.channel.send(
                    f"{EMOJIS['ban']} {message.author.mention}, no tenés permiso para mencionar a todo el server.",
                    delete_after=6,
                )
            except discord.Forbidden:
                pass
            record_log(message.guild.id, "moderacion", f"📢 {message.author} intentó usar @everyone/@here sin permiso (mensaje borrado)", "Sistema")
            return

    bot.add_listener(on_message_moderacion_extra, "on_message")

    # ────────────────────────────────────────────────────────
    #  JAIL (rol de castigo temporal, separado del timeout nativo)
    # ────────────────────────────────────────────────────────

    def _jail_get(guild_id: int) -> dict:
        data = supabase_get(guild_id, "jail") or _load_json(JAIL_PATH).get(str(guild_id), {})
        return data

    def _jail_set(guild_id: int, data: dict) -> None:
        if not supabase_set(guild_id, "jail", data):
            all_data = _load_json(JAIL_PATH)
            all_data[str(guild_id)] = data
            _save_json(JAIL_PATH, all_data)

    @bot.tree.command(name="config-jail-rol", description="[Admin] Define qué rol se usa para /jail.")
    @app_commands.describe(rol="Rol de castigo (configurá sus permisos de canal en Discord)")
    @app_commands.checks.has_permissions(administrator=True)
    async def config_jail_rol_cmd(interaction: discord.Interaction, rol: discord.Role):
        cfg = get_guild_config(interaction.guild_id)
        cfg["jail_role_id"] = rol.id
        update_guild_config(interaction.guild_id, cfg)
        embed = build_embed(
            title=f"{EMOJIS['checkmark']} Rol de jail configurado",
            description=f"Se usará {rol.mention}. Acordate de restringir sus permisos en los canales del server.",
            color=COLOR_OK,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.tree.command(name="jail", description="[Mod] Manda a un usuario a la celda (rol de castigo temporal).")
    @app_commands.describe(usuario="Usuario a enjaular", duracion="Duración: 10m, 1h, 1d (vacío = permanente)", razon="Razón")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def jail_cmd(interaction: discord.Interaction, usuario: discord.Member, duracion: str | None = None, razon: str = "No especificada"):
        cfg = get_guild_config(interaction.guild_id)
        jail_role_id = cfg.get("jail_role_id")
        if not jail_role_id:
            embed = build_embed(title="❌ No hay rol de jail configurado", description="Usá `/config-jail-rol` primero.", color=COLOR_WARN)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        jail_role = interaction.guild.get_role(int(jail_role_id))
        if not jail_role:
            embed = build_embed(title="❌ El rol de jail configurado ya no existe", color=COLOR_WARN)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        expires_at = None
        duration_text = "Permanente"
        if duracion:
            try:
                unit = duracion[-1]
                value = int(duracion[:-1])
                if unit not in ("m", "h", "d") or value <= 0:
                    raise ValueError
            except ValueError:
                embed = build_embed(title="❌ Formato de duración inválido", description="Usá: `10m`, `1h`, `1d`", color=COLOR_WARN)
                return await interaction.response.send_message(embed=embed, ephemeral=True)
            seconds = {"m": 60, "h": 3600, "d": 86400}[unit] * value
            expires_at = time.time() + seconds
            duration_text = duracion

        try:
            await usuario.add_roles(jail_role, reason=razon)
        except discord.Forbidden:
            embed = build_embed(title="❌ No tengo permisos para asignar ese rol", color=COLOR_WARN)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        async with _jail_lock:
            data = _jail_get(interaction.guild_id)
            data[str(usuario.id)] = {"expires_at": expires_at, "razon": razon, "por": str(interaction.user)}
            _jail_set(interaction.guild_id, data)

        record_log(interaction.guild_id, "moderacion", f"🔒 {interaction.user} enjauló a {usuario} ({duration_text}). Razón: {razon}", str(interaction.user))

        embed = build_embed(
            title="🔒 Usuario enjaulado",
            description=f"**{usuario}** fue enjaulado.",
            color=COLOR_OK,
            fields=[("📋 Razón", razon, True), ("⏱️ Duración", duration_text, True)],
        )
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="unjail", description="[Mod] Saca a un usuario de la celda.")
    @app_commands.describe(usuario="Usuario a liberar")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def unjail_cmd(interaction: discord.Interaction, usuario: discord.Member):
        cfg = get_guild_config(interaction.guild_id)
        jail_role_id = cfg.get("jail_role_id")
        jail_role = interaction.guild.get_role(int(jail_role_id)) if jail_role_id else None

        if jail_role and jail_role in usuario.roles:
            try:
                await usuario.remove_roles(jail_role, reason="Liberado")
            except discord.Forbidden:
                pass

        async with _jail_lock:
            data = _jail_get(interaction.guild_id)
            data.pop(str(usuario.id), None)
            _jail_set(interaction.guild_id, data)

        record_log(interaction.guild_id, "moderacion", f"🔓 {interaction.user} liberó a {usuario}", str(interaction.user))
        embed = build_embed(title="🔓 Usuario liberado", description=f"**{usuario}** fue sacado de la celda.", color=COLOR_OK)
        await interaction.response.send_message(embed=embed)

    async def _jail_expiry_loop():
        await bot.wait_until_ready()
        while not bot.is_closed():
            for guild in bot.guilds:
                cfg = get_guild_config(guild.id)
                jail_role_id = cfg.get("jail_role_id")
                if not jail_role_id:
                    continue
                jail_role = guild.get_role(int(jail_role_id))
                if not jail_role:
                    continue

                async with _jail_lock:
                    data = _jail_get(guild.id)
                    now = time.time()
                    expired = [uid for uid, info in data.items() if info.get("expires_at") and info["expires_at"] <= now]
                    for uid in expired:
                        member = guild.get_member(int(uid))
                        if member and jail_role in member.roles:
                            try:
                                await member.remove_roles(jail_role, reason="Fin de la sentencia de jail")
                            except discord.Forbidden:
                                pass
                        data.pop(uid, None)
                    if expired:
                        _jail_set(guild.id, data)
            await asyncio.sleep(60)

    bot.loop.create_task(_jail_expiry_loop())

    # ────────────────────────────────────────────────────────
    #  SISTEMA DE TICKETS
    # ────────────────────────────────────────────────────────

    class CloseTicketView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)

        @discord.ui.button(label="Cerrar ticket", style=discord.ButtonStyle.danger, custom_id="nexus_ticket_close", emoji="🔒")
        async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            cfg = get_guild_config(interaction.guild_id)
            staff_role_id = cfg.get("ticket_staff_role_id")
            is_staff = staff_role_id and any(r.id == int(staff_role_id) for r in interaction.user.roles)
            channel_topic = interaction.channel.topic or ""
            is_owner_of_ticket = channel_topic.startswith(f"ticket:{interaction.user.id}")

            if not (is_staff or is_owner_of_ticket or interaction.user.guild_permissions.administrator):
                embed = build_embed(title="❌ No podés cerrar este ticket", color=COLOR_WARN)
                return await interaction.response.send_message(embed=embed, ephemeral=True)

            embed = build_embed(title="🔒 Cerrando ticket...", description="Este canal se eliminará en unos segundos.", color=COLOR_AMBER)
            await interaction.response.send_message(embed=embed)

            log_channel_id = cfg.get("log_channels", {}).get("moderacion")
            if log_channel_id:
                log_channel = interaction.guild.get_channel(int(log_channel_id))
                if log_channel:
                    try:
                        await log_channel.send(
                            embed=build_embed(
                                title="🎫 Ticket cerrado",
                                description=f"Canal: `{interaction.channel.name}`\nCerrado por: {interaction.user.mention}",
                                color=COLOR_MAIN,
                            )
                        )
                    except discord.Forbidden:
                        pass

            await asyncio.sleep(5)
            try:
                await interaction.channel.delete(reason=f"Ticket cerrado por {interaction.user}")
            except discord.Forbidden:
                pass

    class TicketPanelView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)

        @discord.ui.button(label="Abrir ticket", style=discord.ButtonStyle.success, custom_id="nexus_ticket_open", emoji="🎫")
        async def open_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            cfg = get_guild_config(interaction.guild_id)
            category_id = cfg.get("ticket_category_id")
            staff_role_id = cfg.get("ticket_staff_role_id")

            if not category_id:
                embed = build_embed(title="❌ Los tickets no están configurados", description="Un admin debe usar `/ticket-config` primero.", color=COLOR_WARN)
                return await interaction.response.send_message(embed=embed, ephemeral=True)

            existing = discord.utils.get(interaction.guild.text_channels, topic=f"ticket:{interaction.user.id}")
            if existing:
                embed = build_embed(title="ℹ️ Ya tenés un ticket abierto", description=existing.mention, color=COLOR_AMBER)
                return await interaction.response.send_message(embed=embed, ephemeral=True)

            category = interaction.guild.get_channel(int(category_id))
            overwrites = {
                interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
                interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
                interaction.guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            }
            staff_role = interaction.guild.get_role(int(staff_role_id)) if staff_role_id else None
            if staff_role:
                overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

            safe_name = re.sub(r"[^a-z0-9-]", "", interaction.user.name.lower().replace(" ", "-"))[:20] or "usuario"
            channel = await interaction.guild.create_text_channel(
                name=f"ticket-{safe_name}",
                category=category if isinstance(category, discord.CategoryChannel) else None,
                overwrites=overwrites,
                topic=f"ticket:{interaction.user.id}",
                reason=f"Ticket abierto por {interaction.user}",
            )

            embed = build_embed(
                title="🎫 Ticket abierto",
                description=f"{interaction.user.mention}, contanos en qué te podemos ayudar. Un miembro del staff va a responder pronto.",
                color=COLOR_MAIN,
                footer=footer_text,
            )
            await channel.send(content=staff_role.mention if staff_role else None, embed=embed, view=CloseTicketView())

            confirm = build_embed(title="✅ Ticket creado", description=channel.mention, color=COLOR_OK)
            await interaction.response.send_message(embed=confirm, ephemeral=True)

    @bot.tree.command(name="ticket-config", description="[Admin] Configura la categoría y el rol de staff para los tickets.")
    @app_commands.describe(categoria="Categoría donde se crean los tickets", rol_staff="Rol que puede ver y atender los tickets")
    @app_commands.checks.has_permissions(administrator=True)
    async def ticket_config_cmd(interaction: discord.Interaction, categoria: discord.CategoryChannel, rol_staff: discord.Role):
        cfg = get_guild_config(interaction.guild_id)
        cfg["ticket_category_id"] = categoria.id
        cfg["ticket_staff_role_id"] = rol_staff.id
        update_guild_config(interaction.guild_id, cfg)
        embed = build_embed(
            title=f"{EMOJIS['checkmark']} Tickets configurados",
            description=f"Categoría: {categoria.mention if hasattr(categoria, 'mention') else categoria.name}\nStaff: {rol_staff.mention}",
            color=COLOR_OK,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.tree.command(name="ticket-panel", description="[Admin] Publica el panel para abrir tickets.")
    @app_commands.describe(canal="Canal donde publicar (por defecto el actual)")
    @app_commands.checks.has_permissions(administrator=True)
    async def ticket_panel_cmd(interaction: discord.Interaction, canal: discord.TextChannel | None = None):
        target = canal or interaction.channel
        embed = build_embed(
            title=f"{EMOJIS['discorddevelopers']} Soporte — NEXUS",
            description="¿Necesitás ayuda? Hacé clic en el botón para abrir un ticket privado con el staff.",
            color=COLOR_MAIN,
            footer=footer_text,
        )
        await target.send(embed=embed, view=TicketPanelView())
        confirm = build_embed(title="✅ Panel publicado", description=f"Se publicó en {target.mention}.", color=COLOR_OK)
        await interaction.response.send_message(embed=confirm, ephemeral=True)

    # Registrar las views persistentes para que los botones sigan
    # funcionando después de un reinicio del bot.
    bot.add_view(TicketPanelView())
    bot.add_view(CloseTicketView())
