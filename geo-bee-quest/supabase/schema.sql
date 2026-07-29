-- GeoBee Quest — Supabase schema.
-- Run this once in your Supabase project: SQL Editor → New query → paste → Run.
-- Then paste your Project URL + anon key into js/config.js.

create table if not exists public.progress (
  user_id    uuid        not null default auth.uid(),
  profile    text        not null default 'default',
  state      jsonb       not null,
  updated_at timestamptz not null default now(),
  primary key (user_id, profile)
);

alter table public.progress enable row level security;

-- Each signed-in family account can only see and write its own rows.
drop policy if exists "own rows select" on public.progress;
create policy "own rows select" on public.progress
  for select using (auth.uid() = user_id);

drop policy if exists "own rows insert" on public.progress;
create policy "own rows insert" on public.progress
  for insert with check (auth.uid() = user_id);

drop policy if exists "own rows update" on public.progress;
create policy "own rows update" on public.progress
  for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
