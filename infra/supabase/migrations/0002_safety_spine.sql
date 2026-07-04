-- 0002_safety_spine.sql — Phase 2: consent artifacts, on-call rotation, escalation ack
-- (PRD §6.1 autonomy ladder, §6.2 escalation classes; GAP-08 on-call default; CLAUDE.md Phase 2)

-- Consent artifacts. Autonomy enforcement (server-side, Orchestrator) blocks L1-L3 actions
-- until the required approval rows exist here. UI is advisory (01-TECH_SPEC §6).
create table approvals (
  id               uuid primary key default gen_random_uuid(),
  student_id       uuid not null references students(id) on delete cascade,
  subject_type     text not null check (subject_type in
                     ('weekly_plan','task_swap','session_scheduling',
                      'quarter_roadmap_change','spike_thesis_pivot','test_prep_strategy_change',
                      'fee_bearing_application','program_enrollment','external_submission','data_sharing')),
  subject_id       uuid not null,
  level            text not null check (level in ('L1','L2','L3')),
  approver_role    text not null check (approver_role in ('student','coach','parent')),
  approver_clerk_id text not null,
  decision         text not null check (decision in ('approved','rejected')),
  note             text,
  created_at       timestamptz not null default now(),
  decided_at       timestamptz not null default now()
);
create index approvals_subject_idx on approvals (subject_type, subject_id);
create index approvals_student_idx on approvals (student_id);

-- On-call rotation (GAP-08 default / PRD OD-5): class-1 alerts fan out to the assigned
-- coach + active on-call; unacknowledged 15 min → admin phone tree (worker-enforced).
create table oncall (
  id         uuid primary key default gen_random_uuid(),
  coach_id   uuid not null references coaches(id) on delete cascade,
  priority   int not null default 1,
  active     boolean not null default true,
  created_at timestamptz not null default now()
);

alter table escalations
  add column acknowledged_at timestamptz,
  add column acknowledged_by uuid references coaches(id);

-- RLS
alter table approvals enable row level security;
alter table oncall enable row level security;

grant select on approvals to authenticated;
-- oncall: service plane only (no authenticated grants/policies) — staffing data.
grant select, insert, update, delete on approvals, oncall to service_role;

-- Approvals are visible to everyone in the loop: the student, assigned coach, and family parent
-- (consent artifacts render as stamp components on plans/approvals — 02-UIUX §5).
create policy approvals_select on approvals for select to authenticated
  using (app.is_self_student(student_id) or app.is_assigned_coach(student_id) or app.is_family_parent(student_id));
