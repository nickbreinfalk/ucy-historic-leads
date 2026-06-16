-- One-time security + schema setup for the historic-leads matcher.
-- Run ONCE with the ADMIN (postgres) credential:
--   psql "$SUPABASE_ADMIN_DB_URL" -f migrations/001_security.sql
-- Captures the hardened state so it's reproducible if the project is ever reset.

-- cron cursor state table
create table if not exists bot_state (channel text primary key, last_ts text);

-- lock the PII tables: deny-all to the API roles + enable RLS
revoke all on table public.leads     from anon, authenticated;
revoke all on table public.bot_state from anon, authenticated;
alter table public.leads     enable row level security;
alter table public.bot_state enable row level security;

-- least-privilege application role — the bot connects as THIS, never as postgres.
-- Set a strong password out-of-band (never commit it):
--   alter role bot_app login password '<secret>';
do $$ begin
  if not exists (select from pg_roles where rolname = 'bot_app') then
    create role bot_app login;
  end if;
end $$;
grant usage  on schema public        to bot_app;
grant select on table public.leads   to bot_app;
grant select, insert, update on table public.bot_state to bot_app;

-- policies so bot_app (no BYPASSRLS) can read/write its two tables, while
-- anon/authenticated stay fully denied (no policy => no rows).
drop policy if exists bot_app_leads_sel on public.leads;
create policy bot_app_leads_sel  on public.leads     for select to bot_app using (true);
drop policy if exists bot_app_state_all on public.bot_state;
create policy bot_app_state_all  on public.bot_state for all    to bot_app using (true) with check (true);
