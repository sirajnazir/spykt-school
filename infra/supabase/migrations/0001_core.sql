-- 0001_core.sql — core schema + RLS (01-TECH_SPEC §3)
-- RLS is the security boundary: every table has row security enabled. Tables with no
-- policy for a role are invisible to it (deny-by-default). Service workers use the
-- service role (BYPASSRLS in Supabase); end-user access flows through `authenticated`
-- with Clerk JWT claims in request.jwt.claims (see DECISIONS_NEEDED D-001, reviewed at G1).

create extension if not exists pgcrypto;
create extension if not exists vector;

-- Roles exist in Supabase already; create them when running on plain Postgres (CI).
do $$
begin
  if not exists (select from pg_roles where rolname = 'authenticated') then
    create role authenticated nologin;
  end if;
  if not exists (select from pg_roles where rolname = 'service_role') then
    create role service_role nologin bypassrls;
  end if;
end $$;

-- ---------------------------------------------------------------------------
-- app schema: JWT claim helpers + relationship predicates
-- ---------------------------------------------------------------------------
create schema if not exists app;
grant usage on schema app to authenticated, service_role;

create or replace function app.jwt() returns jsonb
language sql stable as $$
  select coalesce(nullif(current_setting('request.jwt.claims', true), '')::jsonb, '{}'::jsonb)
$$;

create or replace function app.clerk_id() returns text
language sql stable as $$ select app.jwt()->>'sub' $$;

create or replace function app.role() returns text
language sql stable as $$ select coalesce(app.jwt()->>'role', 'anonymous') $$;

create or replace function app.family_id() returns uuid
language sql stable as $$ select nullif(app.jwt()->>'family_id', '')::uuid $$;

-- ---------------------------------------------------------------------------
-- identity & roles (mirrored from Clerk webhooks)
-- ---------------------------------------------------------------------------
create table families (
  id            uuid primary key default gen_random_uuid(),
  plan          text,
  consent_flags jsonb not null default '{}',   -- coppa/ferpa consent state
  created_at    timestamptz not null default now()
);

create table students (
  id              uuid primary key default gen_random_uuid(),
  clerk_id        text unique not null,
  family_id       uuid references families(id),
  grade           int check (grade between 8 and 13),
  archetype       text,
  spike_thesis_id uuid,
  protected_week  boolean not null default false,
  created_at      timestamptz not null default now()
);

create table coaches (
  id         uuid primary key default gen_random_uuid(),
  clerk_id   text unique not null,
  load       int not null default 0,
  created_at timestamptz not null default now()
);

create table coach_assignments (
  coach_id   uuid not null references coaches(id) on delete cascade,
  student_id uuid not null references students(id) on delete cascade,
  primary key (coach_id, student_id)
);

-- Relationship predicates. SECURITY DEFINER so membership lookups do not recurse
-- through RLS on the tables they consult.
create or replace function app.is_self_student(sid uuid) returns boolean
language sql stable security definer set search_path = public, app as $$
  select exists (select 1 from students s where s.id = sid and s.clerk_id = app.clerk_id())
$$;

create or replace function app.is_assigned_coach(sid uuid) returns boolean
language sql stable security definer set search_path = public, app as $$
  select exists (
    select 1 from coach_assignments ca
    join coaches c on c.id = ca.coach_id
    where ca.student_id = sid and c.clerk_id = app.clerk_id()
  )
$$;

create or replace function app.is_family_parent(sid uuid) returns boolean
language sql stable security definer set search_path = public, app as $$
  select app.role() = 'parent' and exists (
    select 1 from students s where s.id = sid and s.family_id = app.family_id()
  )
$$;

create or replace function app.my_family_id() returns uuid
language sql stable security definer set search_path = public, app as $$
  select coalesce(
    app.family_id(),
    (select s.family_id from students s where s.clerk_id = app.clerk_id())
  )
$$;

-- ---------------------------------------------------------------------------
-- CQ store (the moat)
-- ---------------------------------------------------------------------------
create table cq_facts (
  id              uuid primary key default gen_random_uuid(),
  student_id      uuid not null references students(id) on delete cascade,
  kind            text not null check (kind in
                    ('identity','aptitude','passion','impact','eq_signal','narrative_thread','coach_annotation')),
  content         jsonb not null,
  source_event_id text,
  confidence      real check (confidence between 0 and 1),
  superseded_by   uuid references cq_facts(id),
  created_at      timestamptz not null default now()
);

create table cq_embeddings (
  fact_id   uuid primary key references cq_facts(id) on delete cascade,
  embedding vector(1536) not null
);

create table transcripts (
  id         uuid primary key default gen_random_uuid(),
  student_id uuid not null references students(id) on delete cascade,
  session_id uuid,
  role       text not null check (role in ('student','zuzu','coach','system')),
  content    text not null,
  ts         timestamptz not null default now()
);

create table evidence (
  id           uuid primary key default gen_random_uuid(),
  student_id   uuid not null references students(id) on delete cascade,
  task_id      uuid,
  type         text not null,
  uri          text,
  curator_tags jsonb not null default '{}',
  captured_at  timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- planning & execution
-- ---------------------------------------------------------------------------
create table plans (
  id             uuid primary key default gen_random_uuid(),
  student_id     uuid not null references students(id) on delete cascade,
  week_start     date not null,
  status         text not null default 'PLAN_DRAFT' check (status in
                   ('PLAN_DRAFT','VERIFY','APPROVAL','ACTIVE','REFLECT','SCORE','DIGEST','CLOSED')),
  autonomy_level text not null default 'L1' check (autonomy_level in ('L0','L1','L2','L3')),
  approved_by    text,
  plan           jsonb not null default '{}',
  verifier_score real,
  created_at     timestamptz not null default now()
);

create table tasks (
  id                uuid primary key default gen_random_uuid(),
  plan_id           uuid not null references plans(id) on delete cascade,
  title             text not null,
  spike_alignment   text,
  due               date,
  status            text not null default 'todo' check (status in ('todo','active','done','skipped')),
  evidence_required boolean not null default false
);

create table genome_scores (
  id             uuid primary key default gen_random_uuid(),
  student_id     uuid not null references students(id) on delete cascade,
  ring           text not null,
  subfactor      text not null,
  score          real not null,
  confidence     real check (confidence between 0 and 1),
  rationale_ref  text,
  model          text not null,
  prompt_version text not null,
  scored_at      timestamptz not null default now()
);

create table genome_reviews (
  id         uuid primary key default gen_random_uuid(),
  student_id uuid not null references students(id) on delete cascade,
  quarter    text not null,
  coach_id   uuid references coaches(id),
  verdict    text,
  deltas     jsonb not null default '{}',
  created_at timestamptz not null default now()
);

create table opportunities (
  id       uuid primary key default gen_random_uuid(),
  source   text not null,
  title    text not null,
  deadline date,
  match    jsonb not null default '{}',
  status   text not null default 'surfaced'
);

create table narrative (
  id              uuid primary key default gen_random_uuid(),
  student_id      uuid not null references students(id) on delete cascade,
  thesis          text not null,
  coherence_score real,
  drift_flags     jsonb not null default '{}',
  version         int not null default 1,
  created_at      timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- control plane
-- ---------------------------------------------------------------------------
create table events (
  id           text primary key,               -- ULID; dedupe key for at-least-once bus
  student_id   uuid references students(id) on delete cascade,
  type         text not null,
  payload      jsonb not null default '{}',
  processed_at timestamptz
);

create table escalations (
  id             uuid primary key default gen_random_uuid(),
  student_id     uuid not null references students(id) on delete cascade,
  class          int not null check (class between 1 and 5),
  severity       text,
  payload        jsonb not null default '{}',
  assigned_coach uuid references coaches(id),
  sla_due        timestamptz,
  resolved_at    timestamptz,
  created_at     timestamptz not null default now()
);

create table audit_log (
  id             bigint generated always as identity primary key,
  agent          text not null,
  model          text,
  prompt_version text,
  action         text not null,
  autonomy_level text,
  human_approver text,
  student_id     uuid references students(id),
  ts             timestamptz not null default now()
);

create table model_spend (
  student_id uuid not null references students(id) on delete cascade,
  month      date not null,
  model      text not null,
  usd        numeric(10,4) not null default 0,
  primary key (student_id, month, model)
);

create table pseudonym_map (
  student_id uuid primary key references students(id) on delete cascade,
  pseudonym  text unique not null,
  salt       text not null
);

create table prompt_versions (
  agent       text not null,
  version     text not null,
  sha         text not null,
  deployed_at timestamptz not null default now(),
  primary key (agent, version)
);

create table eval_runs (
  id        uuid primary key default gen_random_uuid(),
  suite     text not null,
  agent     text not null,
  pass_rate real not null,
  threshold real not null,
  git_sha   text not null,
  ran_at    timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Row-level security: enable everywhere, deny by default
-- ---------------------------------------------------------------------------
do $$
declare t text;
begin
  foreach t in array array[
    'families','students','coaches','coach_assignments','cq_facts','cq_embeddings',
    'transcripts','evidence','plans','tasks','genome_scores','genome_reviews',
    'opportunities','narrative','events','escalations','audit_log','model_spend',
    'pseudonym_map','prompt_versions','eval_runs'
  ] loop
    execute format('alter table %I enable row level security', t);
  end loop;
end $$;

-- Grants: authenticated may SELECT only where a policy allows a row. Writes go through
-- the API/workers under service_role in v1 (end-user writes arrive with their features).
grant select on
  families, students, coaches, coach_assignments, cq_facts, cq_embeddings, transcripts,
  evidence, plans, tasks, genome_scores, genome_reviews, opportunities, narrative,
  escalations, audit_log
to authenticated;
-- Deliberately NOT granted to authenticated: events, model_spend, pseudonym_map,
-- prompt_versions, eval_runs (service plane only; pseudonym_map is service-role only per 01 §3).

grant select, insert, update, delete on all tables in schema public to service_role;

-- audit_log is append-only for every role including service_role (01 §10):
revoke update, delete on audit_log from authenticated, service_role;

-- students: self; assigned coach; parent in same family
create policy students_select on students for select to authenticated
  using (clerk_id = app.clerk_id() or app.is_assigned_coach(id) or app.is_family_parent(id));

-- families: members of the family (student via lookup, parent via claim)
create policy families_select on families for select to authenticated
  using (id = app.my_family_id());

-- coaches: a coach sees self; students/parents see coaches assigned to them (kept simple: self only in v0)
create policy coaches_select_self on coaches for select to authenticated
  using (clerk_id = app.clerk_id());

create policy coach_assignments_select on coach_assignments for select to authenticated
  using (app.is_self_student(student_id) or app.is_assigned_coach(student_id) or app.is_family_parent(student_id));

-- CQ store: student self + assigned coach. Parents get digest views only (PRD §3) — no direct policy.
create policy cq_facts_select on cq_facts for select to authenticated
  using (app.is_self_student(student_id) or app.is_assigned_coach(student_id));

create policy cq_embeddings_select on cq_embeddings for select to authenticated
  using (exists (select 1 from cq_facts f where f.id = fact_id
                 and (app.is_self_student(f.student_id) or app.is_assigned_coach(f.student_id))));

-- transcripts: raw, RLS-locked — student self + assigned coach ONLY (parents never; 01 §11)
create policy transcripts_select on transcripts for select to authenticated
  using (app.is_self_student(student_id) or app.is_assigned_coach(student_id));

create policy evidence_select on evidence for select to authenticated
  using (app.is_self_student(student_id) or app.is_assigned_coach(student_id) or app.is_family_parent(student_id));

create policy plans_select on plans for select to authenticated
  using (app.is_self_student(student_id) or app.is_assigned_coach(student_id) or app.is_family_parent(student_id));

create policy tasks_select on tasks for select to authenticated
  using (exists (select 1 from plans p where p.id = plan_id
                 and (app.is_self_student(p.student_id) or app.is_assigned_coach(p.student_id)
                      or app.is_family_parent(p.student_id))));

-- genome: student (coach-mediated framing is a UI concern) + coach. Parents: quarterly coach-mediated only (PRD GAP-02).
create policy genome_scores_select on genome_scores for select to authenticated
  using (app.is_self_student(student_id) or app.is_assigned_coach(student_id));

create policy genome_reviews_select on genome_reviews for select to authenticated
  using (app.is_self_student(student_id) or app.is_assigned_coach(student_id));

-- opportunities: global catalog, no student PII — any authenticated user
create policy opportunities_select on opportunities for select to authenticated
  using (true);

create policy narrative_select on narrative for select to authenticated
  using (app.is_self_student(student_id) or app.is_assigned_coach(student_id));

-- escalations: coach plane only (classes 1–3 must never reach parent surfaces — PRD §6.2.1)
create policy escalations_select on escalations for select to authenticated
  using (app.is_assigned_coach(student_id));

-- audit_log: inspectability ("show me why") — student self, coach, parent (Trust Center activity log)
create policy audit_log_select on audit_log for select to authenticated
  using (app.is_self_student(student_id) or app.is_assigned_coach(student_id) or app.is_family_parent(student_id));

-- events, model_spend, pseudonym_map, prompt_versions, eval_runs: no authenticated policies
-- and no grants → service plane only.
