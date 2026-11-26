"""Nexus visual system and interactive embed studio."""
from pathlib import Path
import os
import re
import json
import io
import contextvars
import discord
from discord import app_commands

ASSETS = Path(__file__).resolve().parent / 'assets'
BRAND = 'NEXUSMUSIC  •  Nexus Studios'
STYLE = contextvars.ContextVar('nexusmusic_style', default=(None, None))
CHANNEL_SCOPE = contextvars.ContextVar('nexusmusic_channel', default=None)

def set_style_context(bot, guild):
    STYLE.set((bot, guild))

class NexusTree(app_commands.CommandTree):
    async def interaction_check(self, interaction):
        set_style_context(interaction.client, interaction.guild)
        CHANNEL_SCOPE.set(interaction.channel)
        data=interaction.data or {}
        name=data.get('name','')
        opts=data.get('options') or []
        while opts and opts[0].get('type') in (1,2):
            name+=' '+opts[0].get('name',''); opts=opts[0].get('options') or []
        COMMAND_SCOPE.set(name)
        return True


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

EMOJI_NAMES={
    'checkmark':('nexus_success','checkmark','check','success','verify'),
    'olhos70':('nexus_music','music','equalizer','olhos70'),
    'alert':('nexus_alert','warning','alert'), 'staff':('nexus_staff','staff','shield','ban'),
    'gift':('nexus_gift','gift','giveaway'), 'web':('nexus_web','web','globe'),
    'update':('nexus_update','update','download'), 'star':('nexus_star','star','sparkles'),
    'loading':('nexus_loading','loading','spinner'), 'welcome':('nexus_welcome','welcome'),
}

def animated(bot, name, fallback, guild=None):
    from emojis import EMOJIS
    ctx_bot, ctx_guild=STYLE.get(); bot=bot or ctx_bot; guild=guild or ctx_guild
    if not bot: return fallback
    names=EMOJI_NAMES.get(name,(name,)); configured=os.getenv('NEXUS_EMOJI_'+name.upper(),'').strip()
    match=re.fullmatch(r'<a?:\w+:(\d+)>',configured or EMOJIS.get(name,'')); preferred=match.group(1) if match else configured
    items=[e for e in (getattr(guild,'emojis',()) or ()) if getattr(e,'available',True) and (not hasattr(e,'is_usable') or e.is_usable())]+list(getattr(bot,'nexus_app_emojis',()) or ())
    valid=[e for e in items if getattr(e,'available',True) and (str(e.id)==preferred or e.name.casefold() in names or (preferred and e.name.casefold()==preferred.casefold()))]
    valid.sort(key=lambda e:(not e.animated,str(e.id)!=preferred))
    return str(valid[0]) if valid else fallback

PALETTE={0x7468ff:0xD92138,0x5865f2:0xD92138,0x7270f5:0xD92138,0x87cefa:0x20C997,0xb0c4de:0xD92138,0xa855f7:0xBD3158}
SYMBOLS={'✅':'checkmark','🎵':'olhos70','🎶':'olhos70','⚠️':'alert','🛡️':'staff','🎁':'gift','🌐':'web','⬇️':'update','✨':'star','⏳':'loading','🎉':'welcome','✦':'star'}
def style_embed(embed, bot=None, guild=None):
    if not isinstance(embed,discord.Embed): return embed
    bot=bot or STYLE.get()[0]
    if embed.colour and embed.colour.value in PALETTE: embed.colour=PALETTE[embed.colour.value]
    if not embed.footer.text or embed.footer.text.startswith('NEXUS SYSTEM'):
        embed.set_footer(text=BRAND)
    if not embed.author.name and bot and bot.user and len(embed)<5900:
        embed.set_author(name='NEXUSMUSIC / STUDIO',icon_url=bot.user.display_avatar.url)
    # Only replace leading markers. Avoid changing code, links, or user-authored message bodies.
    def label(value):
        if not value: return value
        for symbol,key in SYMBOLS.items():
            if value.startswith(symbol): return animated(bot,key,symbol,guild)+value[len(symbol):]
        return value
    if len(embed)>5800: return embed
    if embed.title: embed.title=label(embed.title)[:256]
    for idx,f in enumerate(embed.fields):
        if f.name and not f.name.startswith('<'):
            embed.set_field_at(idx,name=label(f.name)[:256],value=f.value,inline=f.inline)
    return embed

COMMAND_ART={
    **dict.fromkeys(('play','nowplaying','panel-play','queue'),'music'),
    **dict.fromkeys(('ayuda','nexus comandos'),'help'),
    **dict.fromkeys(('ban','kick','mute','timeout','warn','softban','modstats','logs','paneladmin','purge'),'staff'),
    **dict.fromkeys(('antilink','antispam','block-server','panel-verificacion','canal-trampa'),'security'),
    **dict.fromkeys(('giveaway','encuesta','announce','sugerencia','panel-autoroles'),'community'),
    **dict.fromkeys(('test-welcome','test-despedida','test-dm-bienvenida'),'welcome'),
    **dict.fromkeys(('panel-keys','generar-key','ver-keys','actualizar-enlace','legales','legales-free'),'studio'),
}
ART_TITLES=(('security',('antispam','antilink','enlace bloqueado','canal trampa','verificación','bloqueado')),('staff',('baneado','expulsado','advertencia','moderación','silenciado','timeout','administración')),('community',('sorteo','encuesta','anuncio','sugerencia')),('welcome',('bienvenido','bienvenida','despedida')),('music',('nexus music','cola de reproducción','panel de música')),('help',('menú de ayuda','centro de comandos')))
COMMAND_SCOPE=contextvars.ContextVar('nexusmusic_command',default='')

async def send_styled(send,*args,**kwargs):
    # A single fresh upload on important sends; preserve custom artwork and existing attachments.
    target=getattr(send,'__self__',None)
    interaction=getattr(target,'_parent',None)
    guild=getattr(target,'guild',None) or getattr(interaction,'guild',None) or STYLE.get()[1]
    channel=getattr(interaction,'channel',None) or (target if hasattr(target,'permissions_for') else None)
    if channel is None and getattr(target,'type',None)==discord.WebhookType.application: channel=CHANNEL_SCOPE.get()
    e=kwargs.get('embed')
    if isinstance(e,discord.Embed):
        e=e.copy(); kwargs['embed']=e
        style_embed(e,guild=guild)
        if not e.image.url and not any(k in kwargs for k in ('file','files','attachments')):
            kind=COMMAND_ART.get(COMMAND_SCOPE.get())
            if not kind:
                title=(e.title or '').casefold(); kind=next((k for k,words in ART_TITLES if any(w in title for w in words)),None)
            if kind and (not channel or not guild or not guild.me or not hasattr(channel,'permissions_for') or channel.permissions_for(guild.me).attach_files):
                kwargs.update(banner(e,kind))
    return await send(*args,**kwargs)

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
    color = discord.ui.TextInput(label='Color HEX', max_length=7, required=False, placeholder='#D92138')
    image = discord.ui.TextInput(label='Banner o imagen (URL HTTPS)', required=False, max_length=1000)
    thumb = discord.ui.TextInput(label='Miniatura (URL HTTPS)', required=False, max_length=1000)
    def __init__(self, studio):
        super().__init__(); self.studio = studio; e = studio.draft
        self.color.default = str(e.colour or discord.Colour.blurple())
        self.image.default = e.image.url or ''; self.thumb.default = e.thumbnail.url or ''
    async def on_submit(self, interaction):
        e = self.studio.draft.copy(); color = str(self.color).strip().lstrip('#')
        if color and not re.fullmatch(r'[0-9a-fA-F]{6}', color):
            return await interaction.response.send_message('Color inválido. Ejemplo: `#D92138`.', ephemeral=True)
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
        self.draft = discord.Embed(title='✦ Nuevo anuncio', description='## Tu mensaje empieza aquí\n> Edita el contenido con los botones.\n\n**Negritas**, *cursivas*, `código` y listas.', colour=0xD92138)
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
        e = build_embed(title='✦ Añade Nexus a tu servidor', description='## Tu comunidad, mejor conectada\n> Música, herramientas y moderación en un solo lugar.\n\nLos módulos de administración pueden requerir permisos adicionales.', color=0xD92138)
        v = discord.ui.View(); v.add_item(discord.ui.Button(label='Añadir a Discord', url=url))
        await interaction.response.send_message(embed=e, view=v, ephemeral=True, **banner(e,'welcome'))

    @nexus_group.command(name='musica-estado', description='Comprueba la conexión y el estado de la sesión musical.')
    async def music_status(interaction: discord.Interaction):
        if not interaction.guild: return await interaction.response.send_message('Usa este comando en un servidor.', ephemeral=True)
        s = get_state(interaction.guild_id); vc = s.voice_client
        e = build_embed(title='✦ Diagnóstico musical', description='> Estado actual de este servidor.', fields=[('Conexión', 'Activa' if vc and vc.is_connected() else 'Desconectada', True),('Cola', str(len(s.queue)),True),('Reproductor','Pausa' if s.is_paused else 'Activo' if s.is_playing() else 'Inactivo',True),('Latencia Gateway', f'{bot.latency*1000:.0f} ms',True)], color=0xD92138)
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
        set_style_context(bot,guild)
        owner = guild.owner
        try:
            if owner is None: owner = await bot.fetch_user(guild.owner_id)
            e = build_embed(title='✦ Bienvenido a NexusMusic', description=f'## Gracias por añadir Nexus\n> Ya formo parte de **{discord.utils.escape_markdown(guild.name)}**.\n\n**Primeros pasos**\n`/ayuda` Explora los módulos\n`/paneladmin` Configura tu comunidad\n`/embed` Crea anuncios personalizados\n`/play` Reproduce música\n`/config canal-ac` Activa avisos APK\n`/nexusmusic version` Descargas oficiales\n`/web` Portales oficiales', fields=[('Servidor', str(guild.id),True),('Miembros',str(guild.member_count or '—'),True)], color=0xD92138, thumbnail=guild.icon.url if guild.icon else None)
            await owner.send(embed=e, **banner(e,'welcome'))
        except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
            print(f'Owner DM no disponible en {guild.id}: {type(exc).__name__}')
