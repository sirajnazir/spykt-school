# TECH SPEC — Spykt Autonomous Prep Institution (v2.0 DEFINITIVE)

**Audience:** Claude Code (Fable 5) autonomous build agent. Assume zero conversation context beyond this spec set.
**Companion docs:** 00-PRD.md (what/why), 02-UIUX_SPEC.md (surfaces), 03-BUILD_HANDOFF.md (how to build), 04-REFINEMENT_LOG.md (why decisions look this way).

---

## 1. Locked Stack (do not substitute)

| Concern | Choice | Notes |
|---|---|---|
| Hosting | Railway | API + workers as separate services |
| DB | Supabase Postgres + pgvector + RLS | RLS is the security boundary; every table has policies |
| Auth | Clerk | Roles: student, parent, coach, admin; org = family unit |
| LLM | Anthropic API direct (`claude-fable-5`, `claude-opus-4-8`, `claude-sonnet-4-6`, `claude-haiku-4-5-20251001`) | **No agent framework.** Specialists = system prompt + tools + contract, orchestrated with Python `asyncio`. (Standing Agno-alignment rule applies only if Agno is ever reintroduced; it is not part of this build.) |
| Payments | Stripe | Subscriptions + SPIKE L3 fee flows |
| Email | Resend | Digests, approvals |
| Queue/cache | Upstash Redis | Streams for event bus; rate-limit tokens; job locks |
| Push | OneSignal | Nudges, escalation alerts (coach: push + SMS via Twilio — **new dependency, approved**) |
| CI/CD | GitHub Actions | Includes eval-gate job (§9) |
| Observability | Sentry + Helicone + PostHog | Helicone = model spend enforcement |
| Language | Python 3.12 (agents/API, FastAPI) + TypeScript (Next.js 14 app) | Matches existing codebase patterns |

## 2. System Topology

```
[Next.js app (student/parent/coach/admin)]
        │ HTTPS (Clerk JWT)
[FastAPI gateway  ── Railway svc: api]
        │ publishes events
[Upstash Redis Streams  ── event bus]
        │ consumed by
[Orchestrator worker ── Railway svc: orchestrator]
   ├─ dispatches → [Specialist pool (asyncio tasks in worker svc)]
   │                 A1 Zuzu · A4 Scout · A5 SPIKE · A6 Curator · A8 Sentinel
   ├─ schedules  → [Cron svc: weekly Fable jobs] A2 Genome · A3 Planner · A7 Narrative · A9 Verifier
   └─ writes     → [Supabase] state, CQ store, audit log
[Pseudonymization Gateway ── in-process library, mandatory on Anthropic client path]
```

**Event bus streams:** `events:student.{id}` (per-student ordering), `events:system`, `queue:coach_escalations`, `queue:fable_jobs`. Consumer groups per worker type; at-least-once delivery with idempotency keys (`event_id` ULID, dedupe table).

## 3. Data Model (Supabase — core tables)

```sql
-- identity & roles via Clerk webhooks → mirrored here
students(id, clerk_id, family_id, grade, archetype, spike_thesis_id, protected_week bool, created_at)
families(id, plan, consent_flags jsonb)        -- coppa/ferpa consent state
coaches(id, clerk_id, load int)
coach_assignments(coach_id, student_id)

-- CQ store (the moat)
cq_facts(id, student_id, kind, content jsonb, source_event_id, confidence, superseded_by, created_at)
  -- kind: identity|aptitude|passion|impact|eq_signal|narrative_thread|coach_annotation
  -- coach_annotation outranks model inference at scoring time (PRD §6.3)
cq_embeddings(fact_id, embedding vector(1536))
transcripts(id, student_id, session_id, role, content, ts)          -- raw, RLS-locked
evidence(id, student_id, task_id, type, uri, curator_tags jsonb, captured_at)

-- planning & execution
plans(id, student_id, week_start, status, autonomy_level, approved_by, plan jsonb, verifier_score)
tasks(id, plan_id, title, spike_alignment, due, status, evidence_required bool)
genome_scores(id, student_id, ring, subfactor, score, confidence, rationale_ref, model, prompt_version, scored_at)
genome_reviews(id, student_id, quarter, coach_id, verdict, deltas jsonb)   -- quarterly countersign
opportunities(id, source, title, deadline, match jsonb, status)            -- Scout + SPIKE
narrative(id, student_id, thesis, coherence_score, drift_flags jsonb, version)

-- control plane
events(id ulid, student_id, type, payload jsonb, processed_at)             -- idempotency/dedupe
escalations(id, student_id, class, severity, payload, assigned_coach, sla_due, resolved_at)
audit_log(id, agent, model, prompt_version, action, autonomy_level, human_approver, student_id, ts) -- append-only
model_spend(student_id, month, model, usd)                                  -- Helicone sync
pseudonym_map(student_id, pseudonym, salt)                                   -- RLS: service-role only
prompt_versions(agent, version, sha, deployed_at)
eval_runs(id, suite, agent, pass_rate, threshold, git_sha, ran_at)
```
RLS policy classes: student sees self; parent sees family (minus raw transcripts — digest views only); coach sees assigned; admin sees aggregates, raw access requires `audit_reason` function that writes to audit_log.

**Evidence file storage (GAP-07):** Supabase Storage bucket `evidence/` with per-student prefixes, storage policies mirroring table RLS, signed URLs (short TTL) for reads, 25MB/object cap, EXIF stripped from images on upload, included in the deletion cascade.

## 4. Model Routing Matrix

| Job | Model | Context strategy | Latency SLO | Fallback chain |
|---|---|---|---|---|
| Zuzu live turn | Sonnet 4.6 | CQ retrieval (top-k facts + session buffer), ≤30k in | p95 ≤ 3.5s first token | Haiku (degrade note) |
| Nudge/ack/classify | Haiku 4.5 | ≤2k | p95 ≤ 1s | none |
| GenomeScorer weekly (light) | **Fable 5** | trailing **2 weeks raw** transcripts/evidence + consolidated cq_facts, 150–250k tokens | ≤15 min batch | Opus 4.8 (flag `degraded_scoring`) |
| GenomeScorer monthly (deep) | **Fable 5** | trailing **12 weeks raw** + full cq_facts, 500–700k tokens (the 1M-context job) | ≤30 min batch | Opus 4.8 (flag `degraded_scoring`) |
| Pathway Planner weekly | **Fable 5** | Genome deltas + roadmap + opportunity feed, ~100–200k | ≤10 min batch | Opus 4.8 |
| Narrative monthly | **Fable 5** | full CQ narrative threads + activity log | ≤15 min | Opus 4.8 |
| Verifier suites | **Fable 5** | self-built harness (see §9) | nightly batch | Opus 4.8 |
| Scout matching | Sonnet 4.6 + web_search tool | per-opportunity | batch | Haiku triage only |
| SPIKE agentic | Sonnet 4.6 | existing service contract | <20s target (Phase-2 track) | catalog path (must be **labeled** — regression test for the silent-fallback bug is mandatory) |

### 4.1 Fable-specific client middleware (required, shared library `anthropic_client.py`)
1. **Refusal handling:** Fable can return HTTP 200 with `stop_reason: "refusal"` and a classifier identifier. Middleware: catch → log `fable_refusal` event with classifier id → retry on `claude-opus-4-8` (client-side fallback; adopt the server-side `fallbacks` parameter when out of beta) → tag output `model_fallback=true`. Refusals on student-content jobs also raise a Sentinel class-5 escalation (PRD §6.2) because a refusal on prep content is anomalous and worth a human glance.
2. **Thinking output:** raw chain of thought is never returned on Fable 5; set `thinking.display: "summarized"` for A2/A3/A7 (summaries stored as `rationale_ref` for coach inspection — **never parse thinking blocks programmatically**; all machine-read output goes through the JSON contract in the response body). Pass thinking blocks back unchanged in multi-turn Fable jobs.
3. **Budget guard:** pre-flight token estimate × price vs. `model_spend` remaining; over budget → degrade to Opus 4.8 + `degraded_budget` flag (PRD §10).
4. **Retention gate:** the client refuses to send any payload to `claude-fable-5` unless it carries a `pseudonymized=true` attestation from the Gateway (§7). This is enforced in the client, not by caller discipline.

### 4.2 Zuzu context assembly (retrieval spec — GAP-04)
- Embeddings: Voyage `voyage-3-large` (config-pinned in `models.yaml`, swappable), stored in `cq_embeddings` (pgvector).
- Retrieval per turn: pgvector cosine top-24 candidate facts → Haiku 4.5 rerank to 8 → merged with the **pinned set** (spike thesis, current plan + active commitments, coach annotations ≤90 days old — always included, never retrieved-or-lost) → rolling session buffer of last 12 turns.
- Hard cap: total assembled Zuzu context ≤ 30k tokens (enforced, oldest buffer turns dropped first, pinned set never dropped).

### 4.3 Model configuration (GAP-11)
All model strings live in `models.yaml` (single source): `claude-fable-5`, `claude-opus-4-8`, `claude-sonnet-4-6`, `claude-haiku-4-5-20251001`, `voyage-3-large`. Model swaps are a config change + mandatory eval run (§9 CI gate), never a code change.

## 5. Specialist Contracts (JSON I/O — all agents)

Every specialist is `run(context: SpecialistInput) -> SpecialistOutput` with:
```json
SpecialistInput  { "job_id", "student_pseudonym", "task", "context_refs": [...], "budget_tokens", "autonomy_ceiling" }
SpecialistOutput { "job_id", "status": "ok|low_confidence|refused|error",
                   "confidence": 0.0-1.0, "result": {...}, "escalate": null | {class, reason},
                   "audit": {"model","prompt_version","tokens_in","tokens_out"} }
```
`low_confidence` (below per-agent threshold, config) never reaches the student — Orchestrator reroutes to coach queue (PRD §6.2.4).

Per-agent `result` schemas (abbreviated; full JSON Schema files in `/contracts/*.schema.json`, generated in Build Phase 1):
- **A2 GenomeScorer:** `{ring→subfactor→{score, confidence, evidence_refs[], delta_vs_last, chetty_modifiers_applied[]}, sigmoid_admit_probs{school_tier→p}, flags[]}` — deterministic-schema output; scores without ≥1 evidence_ref are invalid (Verifier rejects).
- **A3 Planner:** `{week_plan{tasks[{title, spike_alignment, effort_hrs, evidence_required, rationale}]}, roadmap_diff[], autonomy_level_required, agenda_seed_for_coach_session}` — max 5 tasks/week (Jenny cadence: depth over breadth), ≥60% tasks must reference the spike thesis.
- **A1 Zuzu:** streaming text + trailing structured frame `{eq_signals[], commitments_made[], wellbeing_flag?, followups[]}` parsed post-stream by Curator.
- **A8 Sentinel:** `{class:1-5, severity, evidence_ref, recommended_action}` — classes per PRD §6.2; class-1 (wellbeing) bypasses queue ordering, fires push+SMS synchronously.
- **A9 Verifier:** `{suite, cases_run, pass_rate, failures[{case, expected, got, diagnosis}]}`.

## 6. Orchestrator (the only stateful brain)

State machine per student-week: `PLAN_DRAFT → VERIFY → APPROVAL(L-gated) → ACTIVE → REFLECT → SCORE → DIGEST → CLOSED`, persisted in `plans.status`; transitions are event-driven and idempotent. Responsibilities:
- Autonomy enforcement: reads action class → required level → blocks until consent artifact exists (student accept / coach approval row / dual sign-off). **Enforcement is server-side; UI is advisory.**
- Scheduling: cron emits `weekly.plan_due`, `weekly.score_due`, `narrative.monthly`, per-student timezone.
- Backpressure: Fable job queue max concurrency (config, start 4); Genome passes for a cohort spread across the Sat-night window.
- Protected weeks: `students.protected_week` suppresses nudges/streak penalties, keeps evidence capture open.

## 7. Pseudonymization Gateway (compliance-critical)

In-process library on the Anthropic client path.
- **Outbound:** replace student name/family names/school/city/contacts with stable tokens from `pseudonym_map` (deterministic per student, salted); NER pass (spaCy + custom rules for school names) + regex layer (emails, phones, addresses, handles); payloads to Fable additionally strip parent-provided free-text fields unless whitelisted.
- **Attestation:** output carries `pseudonymized=true` + scrub-report hash; client-side retention gate (§4.1.4) requires it for Fable.
- **Inbound:** re-substitution before persistence/UI.
- **Red-team suite (Build Phase 2 gate):** 200+ adversarial cases (names inside quoted essay text, school names as common nouns, nicknames from transcripts); target ≥99.5% recall on seeded PII, and any leak class found later becomes a permanent regression case.
- **Residual-risk note (honest):** NER is not perfect; hence Fable payload logging is itself restricted (Helicone request-body logging **off** for Fable routes; metadata only).

## 8. External Surfaces (FastAPI)

```
POST /api/events                     # app → bus (typed, zod/pydantic mirrored)
GET  /api/students/{id}/today        # Today view aggregate
GET  /api/students/{id}/plan         # current plan + status
POST /api/plans/{id}/consent         # L1 accept/edit
POST /api/escalations/{id}/resolve   # coach console
POST /api/approvals/{id}             # L2/L3 sign-off (dual-auth for L3)
GET  /api/genome/{student}/latest    # coach + student views (student sees coach-mediated framing)
POST /api/evidence                   # multipart capture
GET  /api/audit/{student}            # inspectability ("show me why")
WS   /ws/zuzu/{student}              # streaming chat
(existing) /api/scout/agentic        # SPIKE — unchanged contract, labeled-fallback regression enforced
```

## 9. Verification & Evals (Fable builds its own harness — this is the point)

- **A9 Verifier bootstrap (Phase 3, post-G2 — GAP-12):** a Fable job that, given each specialist's contract + pre-approved pseudonymized corpus extracts + PRD rubrics, **generates eval suites** (golden cases + property tests + adversarial cases) into `/evals/{agent}/`. Phases 0–2 run on synthetic fixtures only; corpus material enters the build only after the G2 Gateway sign-off. Human (Siraj/coach) reviews and pins suite v1; thereafter Verifier proposes suite extensions monthly, humans approve (evals are L2 artifacts — the model must not silently grade itself against tests it silently rewrote).
- **Suites (minimum):** Genome scoring consistency (same input → score variance ≤ threshold across runs; monotonicity properties — more evidence of X never lowers X), Planner quality rubric (spike alignment %, load ≤ 5 tasks, rationale presence), Zuzu EQ-signal extraction fidelity vs. hand-labeled Jenny corpus samples, Sentinel recall on synthetic wellbeing cases (recall target ≥ 0.98; false-positive budget generous — over-escalate by design), Gateway red-team, SPIKE labeled-fallback regression.
- **CI gate:** GitHub Actions runs suites on every prompt_version or contract change; deploy blocked below thresholds. `eval_runs` table is the record.
- **Prod:** nightly sampled re-verification; drift alerts to Sentry.

## 10. Non-functional Requirements
- Latency: §4 SLOs; SPIKE Phase-2 (<20s) tracked as OD-4, out of MVP scope.
- Cost: PRD §10 budgets enforced pre-flight.
- Reliability: at-least-once bus + idempotent handlers; weekly cycle survives worker restarts (state machine resumes from `plans.status`).
- Security: RLS everywhere; service-role key only in workers; audit log append-only (no UPDATE/DELETE grants); Clerk JWT verified at gateway; secrets in Railway env.
- Data rights: deletion job cascades (transcripts → cq → embeddings → pseudonym_map) + provider deletion request log.

## 11. Explicit Non-Requirements (v1)
No Agno or other framework reintroduction; no fine-tuned model in runtime path; no essay ghost-writing capability (Narrative Architect scaffolds/structures — integrity class-3 escalation covers misuse); no parent access to raw transcripts.
