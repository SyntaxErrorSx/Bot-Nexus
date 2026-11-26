# NexusMusic V3 — Motion & Releases

## Instalación
1. Conserva tu `.env`, archivos JSON de datos y credenciales locales. No los subas al repositorio. El ZIP no incluye secretos ni datos privados.
2. Reemplaza los archivos del bot y la carpeta `assets`. Instala `pip install -r requirements.txt` y ejecuta `python bot.py`.
3. Si usas Supabase, ejecuta `SUPABASE-NEXUSMUSIC-UPDATES.sql` en su SQL Editor. Solo crea una tabla para las suscripciones APK y protege su acceso; no modifica las tablas anteriores. El backend usa la clave nueva `sb_secret_` existente. Sin Supabase se usa `nexusmusic_updates.json`, que debe persistir en disco entre despliegues.
4. Si usas Render, conserva las variables de entorno (no hace falta volver a escribirlas). Deja `NEXUS_BANNER_*_URL` vacías para usar los GIF locales. Los mensajes de Discord antiguos no cambian. Comprueba la reproducción automática de GIF en accesibilidad de Discord.

## Comandos nuevos
- `/config canal-ac canal:#actualizaciones avisar_actual:True`: selecciona el canal y publica la última APK estable. Sin `avisar_actual`, la versión actual se guarda como referencia y se avisa solo cuando haya una nueva. Sin canal se desactiva.
- `/config sticker-ac id:ID_DEL_STICKER`: selecciona un sticker del mismo servidor para que se envíe junto al aviso como mensaje separado. Sin ID se desactiva. Discord no permite stickers dentro de embeds; los emojis de aplicación se administran en el Developer Portal.
- `/nexusmusic version`: historial de publicaciones con NexusMusic.apk, cuatro por página y botones de descarga independientes. Incluye preliminares identificadas; consulta hasta 1.000 releases.
- `/info nexusmusic`: consulta el `version.json` real (versión, SHA-256 publicado y enlace de descarga); no descarga ni verifica la APK.
- `/web`: enlaces oficiales de NexusMusic y NexusForgex.

Para mantener el límite de 100 comandos raíz de Discord se trasladaron cuatro ajustes antiguos: `/config logs`, `/config welcome`, `/config despedida`, `/config dm-bienvenida`. Los demás se conservan. Discord sincroniza al iniciar (global y, si GUILD_ID está configurado, en el servidor de pruebas).

## Actualizaciones automáticas
El monitor comparte una consulta GitHub para todos los servidores suscritos y revisa cada 5 minutos mientras el proceso esté en ejecución. Busca la publicación estable más reciente con `NexusMusic.apk` en el repositorio público `santhiagocaro05-debug/Nexus-Update`; no anuncia borradores ni preliminares. Si se publican varias versiones entre revisiones, anuncia la última. Los avisos se guardan por guild después de enviar; se reintenta persistir si falla Supabase. Existe una pequeña ventana de duplicado si se cae el proceso inmediatamente después del envío y antes de guardar. Ejecuta una sola instancia del bot. Para repos públicos no se requiere token GitHub; `NEXUS_GITHUB_TOKEN` es opcional para aumentar límites de API. El token se envía únicamente a api.github.com. No se descarga ni ejecuta ningún APK automáticamente.

## Visual
Once GIF locales de 840x300, 32 fotogramas de 100ms, bucle infinito: música, ayuda, bienvenida, staff, seguridad, comunidad, studio, actualización, versiones, información y web. Se conservan las imágenes personalizadas de los embeds. El reproductor conserva sus controles y su lógica de audio original. Los banners necesitan permisos de adjuntar archivos; los embeds necesitan Insertar enlaces. Se priorizan emojis animados disponibles del servidor y de la aplicación: `nexus_music`, `nexus_success`, `nexus_update`, `nexus_web`, `nexus_staff`, `nexus_star`, `nexus_alert`, etc. Fallback Unicode si no existen. Los stickers se configuran por servidor para los avisos.

## Validación
`python -m unittest discover -s tests -v`: validación de manifiesto, URLs de APK, releases y reglas de no repetición. La sintaxis y el recuento estático de comandos se comprueban sin conectar a Discord. No se ha ejecutado el bot con credenciales reales ni probado el envío en Discord. Si Render duerme o se detiene, el monitor no revisa hasta volver a ejecutarse.
