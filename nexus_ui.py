"""Nexus visual system and interactive embed studio."""
from pathlib import Path
import os
import re
import json
import io
import discord
from discord import app_commands

ASSETS = Path(__file__).resolve().parent / 'assets'
BRAND = 'NEXUS SYSTEM  •  Nexus Studios'

def banner(embed, kind):
    """Return a fresh attachment, or use an optional hosted GIF."""
    url = os.getenv(f'NEXUS_BANNER_{kind.upper()}_URL', '').strip()
    if url.startswith(('https://', 'http://')):
        embed.set_image(url=url)
        return {}
    path = ASSETS / f'{kind}.gif'
    if path.is_file():
        embed.set_image(url=f'attachment://{path.name}')
        return {'file': discord.File(path, filename=path.name)}
    return {}

def animated(bot, name, fallback):
    from emojis import EMOJIS
    raw = EMOJIS.get(name, '')
    match = re.fullmatch(r'<a?:\w+:(\d+)>', raw)
    if match:
        emoji = bot.get_emoji(int(match.group(1)))
        if emoji and emoji.is_usable():
            return str(emoji)
    return fallback

def style_embed(embed, bot=None):
    if not embed.footer.text:
        embed.set_footer(text=BRAND)
    if not embed.author.name and bot and bot.user:
        embed.set_author(name='Nexus System', icon_url=bot.user.display_avatar.url)
    return embed

class StudioText(discord.ui.Modal, title='Nexus Studio · Contenido'):
    heading = discord.ui.TextInput(label='Título', max_length=256, required=False)
    body = discord.ui.TextInput(label='Descripción (Markdown)', style=discord.TextStyle.paragraph, max_length=4000, required=False)
    footer = discord.ui.TextInput(label='Pie de página', max_length=300, required=False)
    author = discord.ui.TextInput(label='Autor', max_length=256, required=False)
    def __init__(self, studio):
        super().__init__(); self.studio = studio
        e = studio.draft
        self.heading.default = e.title or ''; self.body.default = e.description or ''
        self.footer.default = e.footer.text or ''; self.author.default = e.author.name or ''
    async def on_submit(self, interaction):
        e = self.studio.draft.copy()
        e.title = str(self.heading) or None; e.description = str(self.body) or None
        e.set_footer(text=str(self.footer)) if str(self.footer) else e.remove_footer()
        e.set_author(name=str(self.author)) if str(self.author) else e.remove_author()
        await self.studio.update(interaction, e)

class StudioAppearance(discord.ui.Modal, title='Nexus Studio · Apariencia'):
    color = discord.ui.TextInput(label='Color HEX', max_length=7, required=False, placeholder='#7468FF')
    image = discord.ui.TextInput(label='Banner o imagen (URL HTTPS)', required=False, max_length=1000)
    thumb = discord.ui.TextInput(label='Miniatura (URL HTTPS)', required=False, max_length=1000)
    def __init__(self, studio):
        super().__init__(); self.studio = studio; e = studio.draft
        self.color.default = str(e.colour or discord.Colour.blurple())
        self.image.default = e.image.url or ''; self.thumb.default = e.thumbnail.url or ''
    async def on_submit(self, interaction):
        e = self.studio.draft.copy(); color = str(self.color).strip().lstrip('#')
        if color and not re.fullmatch(r'[0-9a-fA-F]{6}', color):
            return await interaction.response.send_message('Color inválido. Ejemplo: `#7468FF`.', ephemeral=True)
        for value in (str(self.image), str(self.thumb)):
            if value and not value.startswith('https://'):
                return await interaction.response.send_message('Las imágenes deben usar una URL HTTPS.', ephemeral=True)
        if color: e.colour = int(color, 16)
        e.set_image(url=str(self.image) or None); e.set_thumbnail(url=str(self.thumb) or None)
        await self.studio.update(interaction, e)

class StudioField(discord.ui.Modal, title='Nexus Studio · Nuevo campo'):
    heading = discord.ui.TextInput(label='Nombre', max_length=256)
    body = discord.ui.TextInput(label='Contenido', style=discord.TextStyle.paragraph, max_length=1024)
    inline = discord.ui.TextInput(label='En línea: sí/no', default='no', max_length=3)
    def __init__(self, studio):
        super().__init__(); self.studio = studio
    async def on_submit(self, interaction):
        e = self.studio.draft.copy()
        if len(e.fields) >= 25:
            return await interaction.response.send_message('Límite de 25 campos.', ephemeral=True)
        e.add_field(name=str(self.heading), value=str(self.body), inline=str(self.inline).lower() in ('si','sí'))
        await self.studio.update(interaction, e)

class StudioView(discord.ui.View):
    def __init__(self, user_id, channel):
        super().__init__(timeout=900); self.user_id = user_id; self.channel = channel; self.published = False
        self.draft = discord.Embed(title='✦ Nuevo anuncio', description='## Tu mensaje empieza aquí\n> Edita el contenido con los botones.\n\n**Negritas**, *cursivas*, `código` y listas.', colour=0x7468FF)
        self.draft.set_footer(text=BRAND)
    async def interaction_check(self, interaction):
        if interaction.user.id != self.user_id or not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message('Este editor requiere permiso de gestionar mensajes.', ephemeral=True); return False
        return True
    async def update(self, interaction, e):
        if len(e) > 6000 or not (e.title or e.description or e.fields or e.image.url or e.author.name):
            return await interaction.response.send_message('El embed está vacío o supera 6000 caracteres.', ephemeral=True)
        self.draft = e
        await interaction.response.edit_message(embed=e, view=self)
    @discord.ui.button(label='Contenido', emoji='✏️', style=discord.ButtonStyle.primary, row=0)
    async def content(self, interaction, button):
        await interaction.response.send_modal(StudioText(self))
    @discord.ui.button(label='Apariencia', emoji='🎨', style=discord.ButtonStyle.primary, row=0)
    async def appearance(self, interaction, button):
        await interaction.response.send_modal(StudioAppearance(self))
    @discord.ui.button(label='Añadir campo', emoji='➕', row=0)
    async def add(self, interaction, button):
        await interaction.response.send_modal(StudioField(self))
    @discord.ui.button(label='Quitar último', emoji='➖', row=1)
    async def remove(self, interaction, button):
        e = self.draft.copy()
        if e.fields: e.remove_field(len(e.fields)-1)
        await self.update(interaction, e)
    @discord.ui.button(label='Fecha', emoji='🕒', row=1)
    async def timestamp(self, interaction, button):
        e = self.draft.copy(); e.timestamp = None if e.timestamp else discord.utils.utcnow()
        await self.update(interaction, e)
    @discord.ui.button(label='Exportar JSON', emoji='💾', row=1)
    async def export(self, interaction, button):
        raw = json.dumps(self.draft.to_dict(), ensure_ascii=False, indent=2).encode('utf-8')
        await interaction.response.send_message('Guarda esta copia de tu diseño.', file=discord.File(io.BytesIO(raw), filename='nexus-embed.json'), ephemeral=True)
    @discord.ui.button(label='Publicar', emoji='🚀', style=discord.ButtonStyle.success, row=2)
    async def publish(self, interaction, button):
        if self.published:
            return await interaction.response.send_message('Ya publicado.', ephemeral=True)
        permissions = self.channel.permissions_for(interaction.user)
        if not permissions.send_messages or not permissions.manage_messages:
            return await interaction.response.send_message('No tienes permiso en el canal de destino.', ephemeral=True)
        self.published = True
        await interaction.response.defer()
        try:
            msg = await self.channel.send(embed=self.draft, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            self.published = False
            return await interaction.followup.send('No se pudo publicar. Revisa los permisos del bot.', ephemeral=True)
        await interaction.edit_original_response(content=f'✅ Publicado: {msg.jump_url}', embed=self.draft, view=None)
        self.stop()
    @discord.ui.button(label='Cancelar', style=discord.ButtonStyle.danger, row=2)
    async def cancel(self, interaction, button):
        await interaction.response.edit_message(content='Editor cerrado.', embed=None, view=None); self.stop()


def setup_ui(bot, build_embed, get_state, nexus_group):
    @bot.tree.command(name='embed', description='Diseña y publica un embed con vista previa y botones.')
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_messages=True)
    async def embed_studio(interaction: discord.Interaction, canal: discord.TextChannel | None = None):
        channel = canal or interaction.channel
        if not isinstance(channel, discord.TextChannel) or not channel.permissions_for(interaction.user).manage_messages:
            return await interaction.response.send_message('Elige un canal de texto que puedas gestionar.', ephemeral=True)
        view = StudioView(interaction.user.id, channel)
        await interaction.response.send_message(content=f'**Nexus Studio** · Vista previa privada · destino {channel.mention}', embed=view.draft, view=view, ephemeral=True)

    @nexus_group.command(name='invitar', description='Obtén el enlace oficial para añadir Nexus a otro servidor.')
    async def invite(interaction: discord.Interaction):
        url = discord.utils.oauth_url(bot.user.id, permissions=discord.Permissions(send_messages=True, embed_links=True, attach_files=True, read_message_history=True, connect=True, speak=True), scopes=('bot','applications.commands'))
        e = build_embed(title='✦ Añade Nexus a tu servidor', description='## Tu comunidad, mejor conectada\n> Música, herramientas y moderación en un solo lugar.\n\nLos módulos de administración pueden requerir permisos adicionales.', color=0x7468FF)
        v = discord.ui.View(); v.add_item(discord.ui.Button(label='Añadir a Discord', url=url))
        await interaction.response.send_message(embed=e, view=v, ephemeral=True, **banner(e,'welcome'))

    @nexus_group.command(name='musica-estado', description='Comprueba la conexión y el estado de la sesión musical.')
    async def music_status(interaction: discord.Interaction):
        if not interaction.guild: return await interaction.response.send_message('Usa este comando en un servidor.', ephemeral=True)
        s = get_state(interaction.guild_id); vc = s.voice_client
        e = build_embed(title='✦ Diagnóstico musical', description='> Estado actual de este servidor.', fields=[('Conexión', 'Activa' if vc and vc.is_connected() else 'Desconectada', True),('Cola', str(len(s.queue)),True),('Reproductor','Pausa' if s.is_paused else 'Activo' if s.is_playing() else 'Inactivo',True),('Latencia Gateway', f'{bot.latency*1000:.0f} ms',True)], color=0x7468FF)
        await interaction.response.send_message(embed=e, ephemeral=True)

    @nexus_group.command(name='cola-limpiar', description='Elimina las canciones pendientes sin detener la actual.')
    async def clear_queue(interaction: discord.Interaction):
        if not interaction.guild: return await interaction.response.send_message('Usa este comando en un servidor.', ephemeral=True)
        s = get_state(interaction.guild_id); voice = interaction.user.voice
        if not voice or not s.voice_client or voice.channel != s.voice_client.channel:
            return await interaction.response.send_message('Únete al mismo canal de voz del bot.', ephemeral=True)
        count = len(s.queue); s.queue.clear()
        await interaction.response.send_message(embed=build_embed(title='✦ Cola limpia', description=f'Se quitaron **{count}** canciones pendientes. La actual continúa.'), ephemeral=True)

    @nexus_group.command(name='comandos', description='Busca comandos disponibles por nombre o descripción.')
    async def command_search(interaction: discord.Interaction, buscar: str):
        q = buscar.casefold().strip()
        matches = [c for c in bot.tree.walk_commands() if isinstance(c, app_commands.Command) and q in (c.qualified_name+' '+c.description).casefold()]
        lines = [f'**`/{c.qualified_name}`** — {c.description}' for c in matches[:15]]
        await interaction.response.send_message(embed=build_embed(title='✦ Explorador de comandos', description='\n'.join(lines)[:4000] or 'No se encontraron coincidencias.', footer=f'{len(matches)} coincidencias · máximo 15 visibles'), ephemeral=True)

    @bot.listen('on_guild_join')
    async def owner_welcome(guild):
        if os.getenv('NEXUS_OWNER_WELCOME', '1') == '0': return
        owner = guild.owner
        try:
            if owner is None: owner = await bot.fetch_user(guild.owner_id)
            e = build_embed(title='✦ Bienvenido a Nexus System', description=f'## Gracias por añadir Nexus\n> Ya formo parte de **{discord.utils.escape_markdown(guild.name)}**.\n\n**Primeros pasos**\n`/ayuda` Explora los módulos\n`/paneladmin` Configura tu comunidad\n`/embed` Crea anuncios personalizados\n`/play` Reproduce música', fields=[('Servidor', str(guild.id),True),('Miembros',str(guild.member_count or '—'),True)], color=0x7468FF, thumbnail=guild.icon.url if guild.icon else None)
            await owner.send(embed=e, **banner(e,'welcome'))
        except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
            print(f'Owner DM no disponible en {guild.id}: {type(exc).__name__}')
