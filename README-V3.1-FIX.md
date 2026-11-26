# NexusMusic V3.1 — Actualizaciones APK

1. Conserva tu `.env`, tus JSON de datos y el archivo de Firebase fuera del repositorio. No están incluidos.
2. En el mismo proyecto Supabase de NexusMusic (no el de NexusGuard), ejecuta `SUPABASE-NEXUSMUSIC-UPDATES.sql` una vez. No elimina datos.
3. Despliega con `python bot.py`, usando las variables de Render existentes.
4. Ejecuta `/config canal-ac canal:#actualizaciones avisar_actual:True`.

El canal ahora se guarda antes de consultar GitHub. El monitor usa el version.json oficial cada cinco minutos; si GitHub falla, conserva la configuración y reintenta. El historial sigue usando GitHub Releases; si falla, muestra la versión oficial actual y un enlace al historial. Los errores de almacenamiento distinguen tablas inexistentes, permisos y red sin imprimir claves.

Las suscripciones antiguas sin identificador de manifiesto toman la primera versión disponible como referencia para no reenviar un aviso antiguo. Usa avisar_actual:True para publicar la versión actual deliberadamente. Si la primera consulta falla y no solicitaste aviso actual, la primera versión recuperada se toma como referencia. Los reintentos tras un envío exitoso y una escritura fallida se conservan en memoria; si el proceso se reinicia antes de confirmar el guardado, puede haber avisos duplicados.

Pruebas locales: 25 pruebas unitarias de manifiesto y releases; compilación Python. No se probó Discord ni Supabase en vivo. Sin Supabase configurado, el archivo local debe persistir entre reinicios. En Render es preferible Supabase.
