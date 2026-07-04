# PROGRESS

> Updated every session per CLAUDE.md §6. Newest session first.

## Session 2026-07-04 (later) — Phase 2 Safety Spine complete → G2 human review window open

**Phase:** 2 — Safety Spine. Built via a 22-agent workflow (G1 approved by Siraj beforehand):
8 corpus generators → 2 assemblers → 5 builders → 5 adversarial verifiers → 2 fixers, plus inline fixes.

### Units completed (spec refs)
- **Synthetic wellbeing corpus** (CLAUDE.md Phase 2): 340 cases — 240 class-1 (80 explicit / 80 oblique /
  80 masked), 20 class-2 family-conflict, 80 negatives incl. 55 idiom hard-negatives; deterministic
  228/112 train/held-out split; PII-free by construction; `evals/sentinel/`.
- **A8 Sentinel** (PRD §6.2, 01 §5): Haiku classifier through the Phase 1 client; over-escalates by design;
  classes 1–3 emit the escalate directive (class-3 fail-open was a verifier catch — fixed); malformed model
  output → error + class-4 (fails toward humans); held-out-leak guard test; versioned prompt `sentinel-v1`.
  Recall harness ready (`run_recall.py`, live run = D-005.1).
- **Gateway red-team** (01 §7): 240 adversarial cases / 532 seeded PII values; hardened gateway from 92.1%
  baseline to **100% recall** with **zero PII in captured Fable-route request bodies**; gate test in CI;
  no cases dropped or edited (prime directive 2 held).
- **Escalation + notify** (PRD §6.2, GAP-08, 02-UIUX §0.3): class-1 fires push+SMS synchronously to assigned
  coach + on-call with per-recipient failure isolation (verifier catch — an audit outage can no longer
  suppress alerts); 15-min unack → admin phone tree (idempotent); budgets fail closed, coach channel exempt;
  Postgres + in-memory stores; everything audited.
- **Autonomy enforcement** (PRD §6.1, 01 §6): server-side `authorize()` from autonomy.yaml; fail-closed on
  unknown actions/levels/ids; L3 dual sign-off requires two DISTINCT humans (inline hardening); 22
  bypass-attempt tests + Postgres integration tests.
- **Zuzu holding pattern** (PRD §6.2.1, 02-UIUX §2.3, GAP-08): constructible only from a fired escalation;
  coach-looped-in message has no other code path; static crisis copy flagged PENDING_G2_HUMAN_APPROVAL;
  coaching-pressure suppression for context assembly.
- **Cross-unit integration**: class-1 end-to-end (message → Sentinel → escalation → push+SMS to coach+on-call)
  asserted < 5s locally with the model stubbed (staging re-run = D-005.2).

### Gate status (→G2)
- Sentinel recall ≥0.98: harness + held-out ready; **live run needs ANTHROPIC_API_KEY (D-005.1)**.
- Class-1 e2e ≤5s: local analogue ✅; staging pending provisioning (D-005.2).
- L2/L3 blocked without approval rows: ✅ (bypass-attempt battery, incl. forged roles, cross-subject rows,
  rejected rows, single-human dual-role).
- Gateway red-team ≥99.5%: ✅ **100%**; zero PII in Fable bodies: ✅.
- Human sign-off required: corpus + crisis copy review (D-005.4), Helicone config (D-005.3).

### Suite
331 passed + 20 skipped (no DB); 94 DB-gated green. Ruff clean.

### Model spend (build-time)
- $0 Anthropic API. ~1.29M subagent tokens for the Phase 2 workflow.

## Session 2026-07-04 — Phase 1 complete → G1 human review window open

**Phase:** 1 — Contracts, Gateway, Client. Built via a 14-agent workflow: 5 parallel build agents
(disjoint packages) → 5 adversarial spec-compliance verifiers → 4 fix agents (audit needed none).

### Units completed (spec refs)
- `packages/contracts` (01 §5): 7 JSON Schemas (SpecialistInput/Output + A1/A2/A3/A8/A9 results),
  pydantic v2 mirrors with layer-agreement guarantees, ≥60% spike-alignment semantic validator,
  hypothesis round-trip property tests + dual-layer adversarial rejection tests.
- `packages/anthropic-client` (01 §4.1): all four middlewares in pipeline order — budget guard
  (pricing/budgets from models.yaml), retention gate on the final model, Fable thinking config
  (summarized; blocks never parsed), refusal→Opus fallback with classifier-id capture, class-5
  escalation hook, audit row on every request incl. retries.
- `packages/gateway` (01 §7): Pseudonymization Gateway v1 — deterministic salted tokens, spaCy NER +
  regex layers, parent-field stripping with whitelist, attestation with scrub-report hash, restore path.
- `packages/eventbus` (01 §2): Redis Streams bus, ULID ids, consumer groups, idempotent handling with
  DedupeStore, XAUTOCLAIM dead-consumer recovery, per-student ordered error mode, dead-lettering.
- `packages/audit` (01 §3/§10): append-only writers (in-memory + Postgres), DB-gated integration test
  proving UPDATE/DELETE rejected.
- `tests/integration`: cross-package G1 story — Gateway scrub → attestation admits Fable call with no
  raw PII in the request body → audit row; missing/forged attestation blocked before any request.

### Gate status (→G1) — all four criteria green
- Contract round-trip property tests: ✅ (hypothesis, all 7 models)
- Retention gate provably blocks unpseudonymized Fable payloads (attestation stripped → raises): ✅
- Refusal middleware verified against mocked `stop_reason:"refusal"`: ✅ (fallback + flags + 2 audit rows)
- Audit rows on every model call: ✅ (unit + integration)
- Suite: 180 passed, 10 skipped locally; +21 DB-gated (RLS + audit) against pgvector Postgres.

### Awaiting human (G1)
Siraj approves schemas + pseudonymization approach before any real-corpus data is used.
Review items: DECISIONS_NEEDED D-001 (RLS identity bridge), D-003 (schema deviations, retention-gate
tension resolution). Note: Phase 2 red-team hardening will further exercise the Gateway; v1 NER is
honest-but-probabilistic per the spec's residual-risk note.

### Eval dashboard
- No pinned suites yet (pre-G3). Eval-gate: vacuous pass.

### Model spend (build-time)
- $0 Anthropic API (all tests run against stubs/mocks). Build-agent token usage this session:
  ~1.0M subagent tokens via the Phase 1 workflow.

## Session 2026-07-03/04 — Phase 0 complete (gate passed locally; CI pending first run)

**Phase:** 0 — Skeleton (repo, CI, envs)

### Units completed
- Monorepo per CLAUDE.md §3: `apps/web`, `services/{api,orchestrator,workers}`,
  `packages/{contracts,anthropic-client}`, `evals/`, `infra/` — uv workspace (py3.12) + Next.js 14.
- `models.yaml` single-source model pinning (01 §4.3 / GAP-11) + loader with routing tests,
  including "live turns never touch Fable" (00 §4).
- Retention-gate enforcement seam (`require_pseudonymized`) with the G1 test case
  (attestation stripped → client raises) already green (01 §4.1.4). Full middleware = Phase 1.
- Supabase migration `0001_core.sql`: all 21 core tables (01 §3), RLS enabled everywhere,
  policies per role class, `pseudonym_map` service-role only, `audit_log` append-only
  (UPDATE/DELETE revoked from service_role too).
- **Phase 0 gate — RLS smoke suite: 6/6 passed** against pgvector Postgres
  (cross-student transcript/audit reads fail; anonymous sees nothing).
- FastAPI hello-world (`/healthz`) + Clerk webhook stub; orchestrator/workers heartbeat shells.
- GitHub Actions: python (pytest+ruff), web (typecheck+build), rls-smoke (pg service container),
  eval-gate (vacuous-pass stub that fails loudly if a suite is pinned without a runner),
  guarded Railway deploy job.
- Provisioning scripts: `infra/railway/provision.sh`, `infra/supabase/provision.sh`,
  `infra/clerk/README.md`; per-service `railway.json`.

### Gate status
- CI green on hello-world: local equivalents all pass; first GitHub Actions run pending this push.
  Live Railway deploy blocked on credentials (D-002).
- RLS smoke (cross-student read fails): **passed** locally and wired into CI.

### Eval dashboard
- No pinned suites yet (pre-G3 by design, GAP-12). Eval-gate job: vacuous pass.

### Open DECISIONS_NEEDED items
- D-001 RLS identity bridge (Clerk→Postgres claims) — default implemented, G1 review.
- D-002 Live provisioning credentials — human action when ready.

### Model spend (build-time)
- $0 — Phase 0 is scaffolding only; no Anthropic API calls.

### Next unit (Phase 1 — Contracts, Gateway, Client)
JSON Schemas for SpecialistInput/Output + per-agent results with round-trip property tests;
then the four client middlewares; then Pseudonymization Gateway v1 + audit writer + event bus.
