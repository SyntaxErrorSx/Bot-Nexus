-- Run once in the SAME project used by NexusMusic. Existing data is unchanged.
create table if not exists public.nexusmusic_updates (
  guild_id text primary key,
  data jsonb not null default '{}'::jsonb
);
alter table public.nexusmusic_updates enable row level security;
revoke all on table public.nexusmusic_updates from public, anon, authenticated;
grant select, insert, update on table public.nexusmusic_updates to service_role;
notify pgrst, 'reload schema';
