-- Execute no SQL Editor do Supabase. Nunca use a service-role no plugin.
create table if not exists editorial_profiles (
  id text primary key, project_type text not null check (project_type in ('videoaula','videocast','videocast_graduacao','videocast_mba')),
  name text not null, created_at timestamptz not null default now()
);
create table if not exists editing_sessions (
  id uuid primary key default gen_random_uuid(), profile_id text not null references editorial_profiles(id), installation_hash text not null,
  configuration_version text, created_at timestamptz not null default now()
);
create table if not exists editor_feedback (
  id uuid primary key default gen_random_uuid(), idempotency_key text not null unique, profile_id text not null references editorial_profiles(id),
  project_type text not null, categories jsonb not null default '[]', metrics jsonb not null default '{}', examples_count integer not null default 0 check (examples_count >= 0),
  content_shared boolean not null default false, created_at timestamptz not null default now()
);
create index if not exists editor_feedback_profile_created_idx on editor_feedback(profile_id, created_at desc);
create table if not exists cut_metrics (
  id uuid primary key default gen_random_uuid(), session_id uuid references editing_sessions(id) on delete cascade, metrics jsonb not null, created_at timestamptz not null default now()
);
create table if not exists learning_patterns (
  id uuid primary key default gen_random_uuid(), profile_id text not null references editorial_profiles(id), category text not null, count integer not null check (count >= 0), evidence jsonb not null default '{}', created_at timestamptz not null default now()
);
create table if not exists learning_jobs (
  id uuid primary key default gen_random_uuid(), profile_id text not null references editorial_profiles(id), type text not null,
  status text not null check (status in ('queued','running','completed','failed')), result jsonb, error text, created_at timestamptz not null default now()
);
create index if not exists learning_jobs_status_created_idx on learning_jobs(status, created_at);
create table if not exists prompt_versions (
  id uuid primary key default gen_random_uuid(), profile_id text not null references editorial_profiles(id), configuration jsonb not null,
  reason text not null, evidence jsonb not null, model text not null, telemetry jsonb not null default '{}', status text not null check (status in ('evaluation_pending','approved','rejected','rolled_back')),
  previous_version_id uuid references prompt_versions(id), created_at timestamptz not null default now()
);
create table if not exists evaluation_results (
  id uuid primary key default gen_random_uuid(), prompt_version_id uuid not null references prompt_versions(id) on delete cascade,
  reexecution_complete boolean not null default false, benchmark_samples integer not null default 0, metrics jsonb not null default '{}', status text not null check (status in ('incomplete','passed','failed')), created_at timestamptz not null default now()
);
create table if not exists deployment_versions (
  id uuid primary key default gen_random_uuid(), profile_id text not null references editorial_profiles(id), prompt_version_id uuid references prompt_versions(id),
  configuration jsonb not null, status text not null check (status in ('approved','rolled_back')), created_at timestamptz not null default now()
);
create index if not exists deployment_versions_profile_created_idx on deployment_versions(profile_id, created_at desc);
alter table editorial_profiles enable row level security; alter table editing_sessions enable row level security; alter table editor_feedback enable row level security;
alter table cut_metrics enable row level security; alter table learning_patterns enable row level security; alter table learning_jobs enable row level security;
alter table prompt_versions enable row level security; alter table evaluation_results enable row level security; alter table deployment_versions enable row level security;
-- Nao crie policy publica. O worker usa service-role; clientes acessam somente sua API autenticada.
insert into editorial_profiles (id, project_type, name) values
 ('videoaula_editorial_v1','videoaula','Videoaula'), ('videocast_editorial_camera_v1','videocast','Videocast'),
 ('videocast_graduacao_editorial_v1','videocast_graduacao','Videocast graduação'), ('videocast_mba_editorial_v1','videocast_mba','Videocast MBA') on conflict do nothing;
