"""NexusMusic releases, per-guild announcement subscriptions, and official websites."""
import asyncio
import json
import logging
import os
import time
from pathlib import Path
from datetime import datetime
import aiohttp
import discord
from discord import app_commands
from nexus_release_core import manifest, releases, RELEASES_URL, MANIFEST_URL, REPO_URL, WEBS, manifest_release, manifest_action, mark_manifest
from nexus_ui import banner, animated, set_style_context
from nexus_supabase import SUPABASE_URL
from urllib.parse import urlsplit

log=logging.getLogger(__name__)
NO_MENTIONS=discord.AllowedMentions.none()
TABLE='nexusmusic_updates'
LOCAL=Path('nexusmusic_updates.json')
RED=0xD92138

def storage_error(exc):
    import re
    text=str(exc)
    match=re.search(r'Supabase HTTP (\d{3})(?: \[([A-Za-z0-9_-]+)\])?',text)
    if match:
        status,code=match.groups()
        if status=='404' or code in ('PGRST205','42P01'):
            return 'Supabase: falta la tabla nexusmusic_updates. Ejecuta SUPABASE-NEXUSMUSIC-UPDATES.sql en el proyecto del bot.'
        if status in ('401','403'):
            return f'Supabase HTTP {status}: revisa la clave sb_secret_ y los permisos de la tabla.'
        return f'Supabase HTTP {status}'+(f' [{code}]' if code else '')+'. Revisa el esquema y los permisos de nexusmusic_updates.'
    if text.startswith('Supabase:'): return text[:350]
    return 'Error de almacenamiento ('+type(exc).__name__+'). Revisa los logs del bot.'


def feed_error(exc):
    import re
    m=re.fullmatch(r'GitHub HTTP (\d{3})',str(exc))
    if m: return f'GitHub HTTP {m.group(1)}: no pude consultar el feed oficial.'
    if isinstance(exc,ValueError): return 'El feed oficial devolvió JSON inválido o inesperado.'
    return 'No pude consultar GitHub ('+type(exc).__name__+').'


class Feed:
    def __init__(self):
        self.cache={}; self.lock=asyncio.Lock()
    async def get(self,url,params=None,ttl=60):
        key=(url,tuple(sorted((params or {}).items())))
        async with self.lock:
            old=self.cache.get(key)
            if old and time.monotonic()-old[0]<ttl: return old[1]
            headers={'User-Agent':'NexusMusic-Discord/3','Accept':'application/vnd.github+json' if url==RELEASES_URL else 'application/json'}
            token=os.getenv('NEXUS_GITHUB_TOKEN','').strip()
            if token and url==RELEASES_URL: headers['Authorization']='Bearer '+token
            if old and old[2]: headers['If-None-Match']=old[2]
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
                async with s.get(url,params=params,headers=headers,allow_redirects=False) as r:
                    if r.status==304 and old:
                        self.cache[key]=(time.monotonic(),old[1],old[2]); return old[1]
                    if r.status!=200: raise RuntimeError(f'GitHub HTTP {r.status}')
                    data=await r.content.read(4_000_001)
                    # read() can return fewer bytes than requested; consume until EOF, bounded.
                    while not r.content.at_eof() and len(data)<=4_000_000:
                        chunk=await r.content.read(4_000_001-len(data))
                        if not chunk: break
                        data+=chunk
                    if len(data)>4_000_000: raise RuntimeError('Respuesta demasiado grande')
                    result=json.loads(data)
                    self.cache[key]=(time.monotonic(),result,r.headers.get('ETag'))
                    return result
    async def current(self): return manifest(await self.get(MANIFEST_URL))
    async def history(self):
        out=[]
        for page in range(1,11):
            data=await self.get(RELEASES_URL,{'per_page':100,'page':page},ttl=120)
            out.extend(releases(data))
            if len(data)<100: break
        return sorted({r['key']:r for r in out}.values(),key=lambda r:(r['published'],r['key']),reverse=True)
    async def latest(self):
        # Published stable release with the APK; never announce draft or prerelease.
        for page in range(1,11):
            raw=await self.get(RELEASES_URL,{'per_page':100,'page':page},ttl=120)
            found=[r for r in releases(raw) if not r['pre']]
            if found: return found[0]
            if len(raw)<100: break
        return None

class Store:
    def __init__(self,client):
        self.local_only = os.getenv('NEXUS_UPDATES_LOCAL_ONLY','0').strip() == '1'
        self.client = None if self.local_only else client
        self.invalid_cloud = not self.local_only and client is None and bool(os.getenv('SUPABASE_URL') or os.getenv('SUPABASE_SECRET_KEY'))
    def check(self):
        if self.invalid_cloud:
            raise RuntimeError('Supabase: configuración incompleta o inválida; revisa SUPABASE_URL y SUPABASE_SECRET_KEY.')
    def read_local(self):
        if not LOCAL.exists(): return {}
        data=json.loads(LOCAL.read_text(encoding='utf-8'))
        if not isinstance(data,dict): raise ValueError('Archivo de avisos inválido')
        return data
    def load(self,gid):
        self.check()
        if self.client:
            # Never silently use stale local data on a cloud error: could resend old alerts.
            return self.client.get_guild_data(TABLE,gid) or {}
        return self.read_local().get(str(gid),{})
    def save(self,gid,data):
        self.check()
        if self.client:
            self.client.set_guild_data(TABLE,gid,data)
        else:
            all_data=self.read_local(); all_data[str(gid)]=data
            tmp=LOCAL.with_suffix('.tmp'); tmp.write_text(json.dumps(all_data,ensure_ascii=False),encoding='utf-8'); tmp.replace(LOCAL)


def date_label(value):
    try: return f'<t:{int(datetime.fromisoformat((value or "").replace("Z","+00:00")).timestamp())}:D>'
    except (ValueError,TypeError): return 'Fecha no disponible'


def link_view(*links):
    view=discord.ui.View(timeout=None)
    for label,url in links: view.add_item(discord.ui.Button(label=label[:80],url=url,emoji='⬇️' if 'Descargar' in label else '🌐'))
    return view


def setup_updates(bot,build_embed,config_group,client):
    feed=Feed(); store=Store(client); locks={}; pending={}; states={}; channels={}
    def guild_lock(gid): return locks.setdefault(gid,asyncio.Lock())
    saved_label = "en Supabase" if store.client else "en archivo local (puede perderse al reiniciar Render)"
    def styled(i): set_style_context(bot,i.guild)
    def art(e,kind,channel=None):
        if channel is not None and hasattr(channel,'permissions_for') and getattr(channel,'guild',None) and channel.guild.me and not channel.permissions_for(channel.guild.me).attach_files: return {}
        return banner(e,kind)
    def make(title,desc,fields=None): return build_embed(title=title,description=desc,color=RED,fields=fields,footer='NEXUSMUSIC  •  Nexus Studios')
    def release_embed(r):
        return make('⬇️ Nueva actualización de NexusMusic',f'## {r["version"]} · Android\n> Ya está disponible una nueva versión de la aplicación.\n\nDescarga el APK oficial desde GitHub.',[('Versión',f'`{r["version"]}`',True),('Fuente', 'Manifiesto oficial' if not r.get('published') else date_label(r['published']),True),('Tamaño',f'{r["size"]/1048576:.1f} MB' if r.get('size') is not None else 'Consultar descarga',True)])
    async def state(gid):
        if gid not in states: states[gid]=await asyncio.to_thread(store.load,gid)
        return states[gid]
    async def save(gid,d):
        await asyncio.to_thread(store.save,gid,d); states[gid]=d
    async def announce(guild,r,d):
        ch=guild.get_channel(int(d.get('channel') or 0))
        if not isinstance(ch,discord.TextChannel) or not guild.me: return False
        perms=ch.permissions_for(guild.me)
        if not (perms.send_messages and perms.embed_links): return False
        set_style_context(bot,guild)
        e=release_embed(r); v=link_view(('Descargar APK',r['url']),('Ver publicaciones',REPO_URL))
        await ch.send(embed=e,view=v,allowed_mentions=NO_MENTIONS,**art(e,'update',ch))
        # Stickers are a separate ordinary message, only when explicitly selected by staff.
        sticker=discord.utils.get(guild.stickers,id=d.get('sticker')) if d.get('sticker') else None
        if sticker and getattr(sticker,'available',True):
            try: await ch.send(stickers=[sticker],allowed_mentions=NO_MENTIONS)
            except discord.HTTPException: log.info('Sticker no disponible guild=%s',guild.id)
        return True
    @config_group.command(name='canal-ac',description='Configura el canal de avisos de actualizaciones de NexusMusic')
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(canal='Canal de actualizaciones; omítelo para desactivar',avisar_actual='Publicar también la última versión al configurar')
    async def configure(i:discord.Interaction,canal:discord.TextChannel|None=None,avisar_actual:bool=False):
        await i.response.defer(ephemeral=True,thinking=True); styled(i)
        if canal and (not canal.permissions_for(i.user).manage_channels or not i.guild.me or not canal.permissions_for(i.guild.me).send_messages or not canal.permissions_for(i.guild.me).embed_links):
            return await i.followup.send('Necesitas gestionar ese canal y el bot debe poder enviar mensajes e insertar enlaces.',ephemeral=True)
        # Persist channel first; an unavailable GitHub feed must not undo it.
        try:
            async with guild_lock(i.guild_id):
                d=dict(await state(i.guild_id))
                d.update(channel=canal.id if canal else None, announce_current=bool(canal and avisar_actual))
                if canal:
                    # New configuration starts from the current official manifest.
                    d.pop('last_manifest',None); d.pop('last_version',None)
                await save(i.guild_id,d)
                pending.pop(i.guild_id,None)
        except Exception as exc:
            detail=storage_error(exc)
            log.warning('Configurar canal APK guild=%s: %s',i.guild_id,detail)
            return await i.followup.send('No pude confirmar el guardado del canal. '+detail+' Ejecuta `/config diagnostico-ac` para comprobar ambos servicios.',ephemeral=True)
        if not canal:
            return await i.followup.send('Avisos APK desactivados. Configuración guardada'+(' en Supabase.' if store.client else ' localmente.'),ephemeral=True)
        try:
            r=manifest_release(await feed.get(MANIFEST_URL))
        except Exception as exc:
            detail=feed_error(exc)
            log.warning('Canal APK guardado; manifiesto: %s',detail)
            return await i.followup.send('✅ Canal guardado '+saved_label+'. '+detail+' El monitor reintentará automáticamente; '+('publicará la versión actual cuando pueda.' if avisar_actual else 'tomará la primera versión disponible como referencia.'),ephemeral=True)
        try:
            async with guild_lock(i.guild_id):
                d=await state(i.guild_id)
                if d.get('channel')!=canal.id:
                    return await i.followup.send('La configuración cambió mientras consultaba GitHub.',ephemeral=True)
                action=manifest_action(d,r)
                if action=='announce' and not await announce(i.guild,r,d):
                    return await i.followup.send('Canal guardado '+saved_label+'. Aviso pendiente: revisa permisos del bot.',ephemeral=True)
                if action in ('announce','baseline'):
                    new=mark_manifest(d,r)
                    pending[i.guild_id]=new
                    await save(i.guild_id,new); pending.pop(i.guild_id,None)
        except discord.HTTPException as exc:
            return await i.followup.send(f'Canal guardado {saved_label}. Discord rechazó el aviso (HTTP {exc.status}); revisa permisos. Se reintentará.',ephemeral=True)
        except Exception as exc:
            log.warning('Finalizar aviso APK: %s',storage_error(exc))
            return await i.followup.send('Canal guardado '+saved_label+'. La confirmación de versión quedó pendiente: '+storage_error(exc),ephemeral=True)
        e=make('✦ Centro de actualizaciones',f'**Canal:** {canal.mention}\n**Guardado:** {"Supabase" if store.client else "Archivo local (puede perderse al reiniciar Render)"}\n> Se revisa el manifiesto oficial cada 5 minutos mientras el bot esté activo.\n**Versión de referencia:** `{r["version"]}`')
        await i.followup.send(embed=e,ephemeral=True,**art(e,'update',i.channel),allowed_mentions=NO_MENTIONS)
    @config_group.command(name='diagnostico-ac',description='Comprueba la conexión de los avisos APK con Supabase y GitHub')
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def diagnose(i:discord.Interaction):
        await i.response.defer(ephemeral=True,thinking=True); styled(i)
        lines=[]
        host=urlsplit(SUPABASE_URL).hostname or 'sin configurar'
        lines.append(f'**Proyecto:** `{host}`')
        lines.append(f'**Almacenamiento:** {"Local (no persistente en Render sin disco)" if not store.client and not store.invalid_cloud else "Supabase"}')
        async def check_table(table):
            try:
                await asyncio.to_thread(store.client.table(table).select('guild_id').limit(1).execute)
                return f'**{table}:** lectura correcta'
            except Exception as exc:
                return f'**{table}:** {storage_error(exc)}'
        async def check_feed():
            try:
                r=manifest_release(await feed.get(MANIFEST_URL))
                return f'**GitHub:** disponible · `{r["version"]}`'
            except Exception as exc:
                return '**GitHub:** '+feed_error(exc)
        if store.client:
            lines.extend(await asyncio.gather(check_table('config'),check_table(TABLE),check_feed()))
        else:
            if store.invalid_cloud: lines.append('**Supabase:** variables incompletas o inválidas.')
            lines.append(await check_feed())
        e=make('Diagnóstico de actualizaciones','\n'.join(lines)+'\n\n*Prueba de lectura; no modifica registros ni muestra claves.*')
        await i.followup.send(embed=e,ephemeral=True,allowed_mentions=NO_MENTIONS)
    @config_group.command(name='sticker-ac',description='Configura un sticker de este servidor para los avisos APK')
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(id='ID del sticker; omítelo para desactivar')
    async def sticker_config(i:discord.Interaction,id:str|None=None):
        await i.response.defer(ephemeral=True); styled(i)
        sticker=discord.utils.get(i.guild.stickers,id=int(id)) if id and id.isdigit() and len(id)<=22 else None
        if id and (not sticker or not getattr(sticker,'available',True)): return await i.followup.send('Selecciona un ID de sticker disponible de este servidor.',ephemeral=True)
        try:
            async with guild_lock(i.guild_id):
                d=dict(await state(i.guild_id)); d['sticker']=sticker.id if sticker else None; await save(i.guild_id,d)
            await i.followup.send('Sticker guardado para los avisos.' if sticker else 'Sticker desactivado.',ephemeral=True)
        except Exception: await i.followup.send('No pude guardar la configuración. Comprueba Supabase.',ephemeral=True)
    version_group=app_commands.Group(name='nexusmusic',description='Publicaciones de NexusMusic')
    info_group=app_commands.Group(name='info',description='Información del ecosistema Nexus')
    class History(discord.ui.View):
        def __init__(self,items,user):
            super().__init__(timeout=180); self.items=items; self.user=user; self.page=0
        def render(self):
            self.clear_items(); start=self.page*4; items=self.items[start:start+4]
            e=make('✦ Historial de versiones',f'## NexusMusic · Android\n> {len(self.items)} publicaciones con APK encontradas.\nSelecciona el botón de la versión que quieras descargar.')
            for j,r in enumerate(items):
                e.add_field(name=r['version']+(' · Preliminar' if r['pre'] else ''),value=f'{date_label(r["published"])} · {r["size"]/1048576:.1f} MB',inline=False)
                self.add_item(discord.ui.Button(label=f'Descargar {r["version"]}'[:80],url=r['url'],emoji='⬇️',row=j//2))
            for delta,label in [(-1,'Anterior'),(1,'Siguiente')]:
                b=discord.ui.Button(label=label,style=discord.ButtonStyle.secondary,row=2,disabled=not 0<=self.page+delta<(len(self.items)+3)//4)
                async def change(i,delta=delta):
                    if i.user.id!=self.user: return await i.response.send_message('Abre tu propio historial con `/nexusmusic version`.',ephemeral=True)
                    self.page+=delta; e=self.render()
                    if i.message.embeds and i.message.embeds[0].image.url: e.set_image(url=i.message.embeds[0].image.url)
                    await i.response.edit_message(embed=e,view=self)
                b.callback=change; self.add_item(b)
            e.set_footer(text=f'NexusMusic · Página {self.page+1}/{max(1,(len(self.items)+3)//4)}')
            return e
    @version_group.command(name='version',description='Explora las versiones publicadas y descarga su APK')
    async def versions(i:discord.Interaction):
        await i.response.defer(thinking=True); styled(i)
        try: items=await feed.history()
        except Exception as exc:
            try: current=manifest_release(await feed.get(MANIFEST_URL))
            except Exception: return await i.followup.send(feed_error(exc)+' '+REPO_URL,ephemeral=True)
            e=make('✦ NexusMusic · Versión actual',f'**{current["version"]}**\n> No se pudo cargar el historial completo. Esta es la versión del manifiesto oficial.')
            return await i.followup.send(embed=e,view=link_view(('Descargar APK',current['url']),('Ver historial',REPO_URL)),**art(e,'versions',i.channel),allowed_mentions=NO_MENTIONS)
        if not items: return await i.followup.send('No se encontraron publicaciones con NexusMusic.apk.',ephemeral=True)
        v=History(items,i.user.id); e=v.render(); await i.followup.send(embed=e,view=v,**art(e,'versions',i.channel),allowed_mentions=NO_MENTIONS)
    @info_group.command(name='nexusmusic',description='Consulta versión, APK y SHA-256 del manifiesto oficial')
    async def info(i:discord.Interaction):
        await i.response.defer(thinking=True); styled(i)
        try: d=await feed.current()
        except Exception as exc: return await i.followup.send(feed_error(exc),ephemeral=True)
        e=make('✦ NexusMusic · Información oficial',f'## Tu música, sin límites\n> Información obtenida del manifiesto oficial de la aplicación.',[('Versión actual',f'`{d["version"]}`',True),('Plataforma','Android · APK',True),('SHA-256 publicado',f'`{d["sha256"]}`',False)])
        await i.followup.send(embed=e,view=link_view(('Descargar APK',d['apkUrl']),('Ver manifiesto',f'https://github.com/santhiagocaro05-debug/Nexus-Update/blob/main/version.json')),**art(e,'info',i.channel),allowed_mentions=NO_MENTIONS)
    @bot.tree.command(name='web',description='Portales oficiales de NexusMusic y NexusForgex')
    async def web(i:discord.Interaction):
        styled(i); e=make('🌐 Ecosistema Nexus Studios','## Conecta con nuestras plataformas\n> Explora la música y las herramientas oficiales.',[(name,f'[Visitar sitio]({url})',False) for name,url in WEBS]); await i.response.send_message(embed=e,view=link_view(*[(f'Abrir {name}',url) for name,url in WEBS]),**art(e,'web',i.channel),allowed_mentions=NO_MENTIONS)
    bot.tree.add_command(version_group); bot.tree.add_command(info_group)
    async def monitor():
        await bot.wait_until_ready()
        while not bot.is_closed():
            try:
                # One shared GitHub fetch per interval, not one per guild.
                for guild in list(bot.guilds):
                    try:
                        async with guild_lock(guild.id):
                            d=await state(guild.id)
                            if guild.id in pending:
                                await save(guild.id,pending[guild.id]); pending.pop(guild.id,None)
                                d=states[guild.id]
                            if d.get('channel'): channels[guild.id]=True
                            else: channels.pop(guild.id,None)
                    except Exception as exc: log.warning('No se pudo leer suscripción guild=%s: %s',guild.id,storage_error(exc))
                r=manifest_release(await feed.get(MANIFEST_URL)) if channels else None
                if r:
                    for guild in list(bot.guilds):
                        if guild.id not in channels: continue
                        try:
                            async with guild_lock(guild.id):
                                d=await state(guild.id)
                                if guild.id in pending: continue
                                action=manifest_action(d,r)
                                if action=='baseline' or (action=='announce' and await announce(guild,r,d)):
                                    new=mark_manifest(d,r); pending[guild.id]=new
                                    await save(guild.id,new); pending.pop(guild.id,None)
                        except Exception as exc: log.warning('Aviso APK pendiente guild=%s (%s)',guild.id,type(exc).__name__)
            except Exception as exc: log.warning('Monitor APK: %s',feed_error(exc))
            await asyncio.sleep(300)
    @bot.listen('on_ready')
    async def start():
        task=getattr(bot,'nexus_updates_task',None)
        if task is None or task.done(): bot.nexus_updates_task=asyncio.create_task(monitor(),name='nexusmusic-updates')
