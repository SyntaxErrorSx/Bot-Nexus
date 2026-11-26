# Nexus System · Studio & Music Edition

Actualización del proyecto original. Conserva sus módulos, archivos de configuración y sistemas de persistencia. No contiene el `.env` original ni archivos de sesión o caché de Python.

## Inicio

1. Instala las dependencias: `python -m pip install -r requirements.txt`.
2. Configura `DISCORD_TOKEN` y las demás variables de tu instalación en Render o en un `.env` local basado en `.env.example`. No publiques credenciales. Si el token original fue compartido, regénéralo.
3. En Discord Developer Portal, habilita Server Members Intent y Message Content Intent. Para comandos, autoriza los scopes `bot` y `applications.commands`. Concede los permisos necesarios para cada módulo; no se requiere administrador para las funciones de música.
4. Inicia con `python bot.py`. En Render usa el puerto `PORT` que proporciona la plataforma. `/ping` y `/health` están disponibles; `NEXUS_PUBLIC_URL` permite configurar el destino del ping (o se usa `RENDER_EXTERNAL_URL`). El keep-alive no garantiza que un plan gratuito no suspenda el servicio.
5. FFmpeg del sistema es preferido; si falta, se intenta el binario de `imageio-ffmpeg`. Verifica también Deno, PyNaCl y davey para voz. YouTube puede bloquear solicitudes de centros de datos; no se garantiza un tiempo de carga ni acceso a todo video. No se implementa Spotify como proveedor.

## Comandos nuevos y cambios

- `/embed [canal]`: editor privado con contenido Markdown, autor, pie, color, imagen o GIF HTTPS, miniatura, campos, fecha y exportación JSON. Publicar requiere gestionar mensajes y permisos en el canal. El editor dura 15 minutos; no guarda presets en el servidor.
- `/nexus invitar`: enlace OAuth del bot.
- `/nexus comandos buscar`: buscador de comandos.
- `/nexus musica-estado`: estado de la conexión y reproductor.
- `/nexus cola-limpiar`: elimina la cola pendiente (mismo canal de voz).
- `/nexus botinfo` y `/nexus ayuda-error`: reemplazan los nombres anteriores `/botinfo` y `/ayuda-error` para respetar el límite de 100 comandos raíz de Discord.
- `/play`, `/panel-play`, `/nowplaying`, `/queue` y los controles originales se mantienen. El panel muestra portada, progreso, duración, artista, estado, cola y controles, con actualización manual mediante el botón Actualizar.

## Diseño

Los GIF de `assets/` se adjuntan automáticamente en las pantallas seleccionadas (bienvenida al propietario, ayuda, invitación, administración y reproducción). Se pueden sustituir por GIF HTTPS con `NEXUS_BANNER_MUSIC_URL`, `NEXUS_BANNER_HELP_URL` y `NEXUS_BANNER_WELCOME_URL`. Se necesita el permiso Attach Files para los adjuntos. Discord controla la reproducción de GIF y de emojis animados; los emojis existentes en `emojis.py` solo se usan cuando están disponibles, con alternativa Unicode. El modo de movimiento reducido de Discord puede desactivar su animación. No hay animaciones CSS ni transiciones de embeds nativas de Discord.

## Bienvenida al propietario

Al añadir el bot a un servidor (`on_guild_join`), se intenta enviar un único mensaje directo al dueño del servidor con el banner de bienvenida y los pasos iniciales. Si sus DMs están cerrados se registra el fallo y no se insiste. Para deshabilitarlo: `NEXUS_OWNER_WELCOME=0`.

## Comprobaciones

Se verificó sintaxis de todos los Python, recuento de comandos raíz y validez de los GIF. No se pudo ejecutar una sesión real de Discord, instalar sus dependencias en el entorno de revisión ni medir el tiempo real de YouTube. Los módulos que ya estaban deshabilitados (como moderación extra) permanecen deshabilitados. Prueba el bot en un servidor de test antes de desplegar a producción.
