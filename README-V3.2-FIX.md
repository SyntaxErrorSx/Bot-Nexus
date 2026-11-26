# NexusMusic V3.2 — Updates Connection Fix

The APK table already exists in the observed database. No schema migration is required for this update. Keep the existing `.env`, local data and Firebase file in your own deployment; they are intentionally not included in the archive.

## Render Environment

```
SUPABASE_URL=https://uuobxfflmslprwosvpvv.supabase.co
SUPABASE_SECRET_KEY=YOUR_NEW_SB_SECRET_KEY
NEXUS_UPDATES_LOCAL_ONLY=0
```

Use a current secret key for the SAME Supabase project. The REST client sends it in `apikey` only, with normal certificate verification. Do not use a legacy JWT or a publishable key for writes.

Deploy `python bot.py` and let slash commands sync. Run `/config diagnostico-ac` to compare the `config` and `nexusmusic_updates` read paths and the GitHub manifest independently. The command does not write or print any keys. Then run `/config canal-ac` with your selected channel and `avisar_actual=True`. A successful read is not proof of write permission: the latter command confirms the upsert. Transport errors now include DNS, connection, read timeout or TLS, instead of a generic message. Only idempotent upserts and reads of the update table get up to three attempts. Each guild has a separate update lock.

If Supabase is still unreachable, verify the project URL and project status, and inspect the exact diagnostic. No code can force an unavailable remote service to respond.

## Optional temporary local mode

Set `NEXUS_UPDATES_LOCAL_ONLY=1` in Render only if you accept storing update subscriptions in `nexusmusic_updates.json` locally. Redeploy and configure the channel again. GitHub and Discord must still work. Other bot modules retain their current storage behavior. Render's ephemeral filesystem can lose this file on redeploy/restart; a persistent disk is required for durability. Do not run multiple bot instances. Before returning to cloud mode, change the flag to `0`, redeploy and configure again (`avisar_actual=False` avoids deliberately reposting the current release). Local data is not automatically merged into the cloud. Back up any existing data first.

## Tests

35 local tests passed, including mocked DNS, timeout, TLS, 403, transient retry and local-only storage. Live access from Render was not tested.
