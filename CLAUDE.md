# BUILD HANDOFF — Autonomous Build Protocol for Claude Code (Fable 5)

**This file is the CLAUDE.md seed for the build repo.** It defines how the agent builds, verifies, and decides "done." The specs (00–02) define *what*; this defines *how the loop runs without a human driving every step* — and where humans gate it anyway.

---

## 0. Prime Directives (ordered; earlier wins)
1. Never weaken a safety/compliance mechanism to make a test pass (Pseudonymization Gateway, autonomy enforcement, Sentinel, audit log immutability).
2. Never mark an acceptance criterion passed by modifying the criterion. Eval suites are L2 artifacts: propose changes in `EVAL_CHANGES.md`, do not edit pinned suites.
3. Prefer boring, observable code over clever code. Every agent path must be traceable in the audit log.
4. When blocked > 2 attempts on an ambiguity, write it to `DECISIONS_NEEDED.md` with a recommended default, implement the default behind a config flag, and continue. Do not stall; do not silently choose on compliance-relevant ambiguities (those stall loudly).
5. Stack is locked (01-TECH_SPEC §1). Introduce no new services/frameworks without a `DECISIONS_NEEDED.md` entry.

## 1. The Autonomous Loop (per work unit)
```
SELECT next unit from phase plan (§3) → read relevant spec sections
→ IMPLEMENT (tests-first for contracts and gates)
→ SELF-VERIFY: unit + contract tests, then run/extend Verifier eval suite for touched agents
→ CRITIQUE PASS: re-read the spec section as a fresh reader; list mismatches; fix
→ GATE CHECK: phase acceptance criteria (§3) — if impacted, run them
→ COMMIT with spec-section references; update PROGRESS.md
→ if gate newly passed → tag phase checkpoint → human review window (see §2)
```

## 2. Human Gates in the Build Itself (HITL applies to construction, not just runtime)
The build is autonomous *between* gates, not through them:
- **G1 (end Phase 1):** contracts + Gateway design review — Siraj approves schemas and pseudonymization approach before any real-corpus data is used.
- **G2 (end Phase 2):** Gateway red-team results + Sentinel recall report — human sign-off required before any live-model traffic includes student-derived corpus content.
- **G3 (end Phase 3):** synthetic-cohort 4-week simulation review — Siraj + one coach walk the coach console against F1–F7 flows.
- **G4 (pre-pilot):** budget burn report, security checklist, deletion-cascade demo.
Between gates: nightly `PROGRESS.md` + eval dashboard is the only required human touch.

## 3. Phase Plan & Acceptance Gates

### Phase 0 — Skeleton (repo, CI, envs)
Monorepo (`apps/web`, `services/api`, `services/orchestrator`, `services/workers`, `packages/contracts`, `packages/anthropic-client`, `evals/`), Railway + Supabase provisioning scripts, Clerk roles, GitHub Actions with eval-gate job stub.
**Gate:** CI green on hello-world deploy; RLS smoke test (cross-student read fails).

### Phase 1 — Contracts, Gateway, Client
JSON Schemas for all SpecialistInput/Output + per-agent results; `anthropic_client.py` with the four middlewares (refusal→Opus fallback, thinking config, budget guard, retention gate); Pseudonymization Gateway v1; audit log writer; event bus + idempotency.
**Gate (→G1):** contract round-trip property tests; retention gate provably blocks unpseudonymized Fable payloads (test: attestation stripped → client raises); refusal middleware verified against mocked `stop_reason:"refusal"`; audit rows on every model call.

### Phase 2 — Safety Spine
Sentinel (classifier + synthetic wellbeing corpus — generate ≥300 cases spanning explicit/oblique/masked distress, reviewed at G2), escalation queue + push/SMS, autonomy enforcement in Orchestrator (server-side), Gateway red-team suite (≥200 adversarial PII cases), Zuzu holding-pattern behavior.
**Gate (→G2):** Sentinel recall ≥0.98 on held-out cases; class-1 end-to-end alert ≤5s in staging; L2/L3 actions blocked without approval rows (attempt-to-bypass tests); Gateway red-team ≥99.5% recall; **zero PII found in captured Fable-route request bodies during the suite.**

### Phase 3 — The Weekly Cycle
Orchestrator state machine; Planner + GenomeScorer Fable jobs (against pseudonymized seeded corpus extracts); Zuzu sessions (planning/reflection arcs); Evidence Curator; Verifier bootstrap (Fable generates eval suites v1 → pinned at gate); student app Today/Week/Zuzu/Spike; coach console queue+approvals; parent digest.
**Gate (→G3):** 4 simulated weeks × 10 synthetic students run with zero manual intervention; flows F1–F7 pass as automated E2E; Genome scoring variance suite passes; every plan task carries rationale + spike alignment; digest emails render.

### Phase 4 — Feeds & Money
Opportunity Scout (allowlist config), SPIKE integration with labeled-fallback regression + `harvestToolResultUrls()` salvage path, L3 dual-approval flows, Stripe wiring, Trust Center, Me/data controls incl. deletion cascade.
**Gate (→G4):** SPIKE provenance labels correct under forced-timeout chaos test; deletion cascade demo (student data verifiably gone incl. embeddings + pseudonym map, provider deletion request logged); Helicone budgets enforce (over-budget Fable job degrades to Opus with flag); security checklist.

### Phase 5 — Pilot hardening
Load (cohort 50 simulated), observability dashboards, runbooks (refusal spike, Fable outage → full-Opus degraded mode, bus backlog), chaos drills (worker kill mid-cycle resumes state machine).
**Gate:** PRD §9 Definition of Done, all clauses.

## 4. Definition of Done (verbatim from PRD §9 + operational additions)
(a) full weekly cycle, 4 simulated weeks, zero manual intervention; (b) all pinned eval suites ≥ thresholds; (c) L2/L3 provably blocked without humans (bypass-attempt tests); (d) Gateway red-team clean; (e) SLOs: live p95 ≤3.5s, Genome pass ≤30min/student, unit cost within PRD §10; plus (f) runbooks exist and chaos drills pass; (g) `DECISIONS_NEEDED.md` empty of compliance-class items.

## 5. What the Agent Must NOT Do
- No real student data (even pseudonymized) before G2 sign-off; Phases 0–2 use synthetic + the pre-approved corpus extract fixtures only.
- No essay-generation capability in Narrative Architect (scaffold structures only) — test exists, keep it green.
- No relaxation of notification budgets or streak-shame states "for engagement."
- No prompt changes without `prompt_versions` bump + eval run.
- No editing 04-REFINEMENT_LOG.md history (append only).
- Do not rewrite SPIKE (GAP-09 default): build a new monorepo; consume the existing `/api/scout/agentic` service over HTTP with its current contract. Rewriting it is a `DECISIONS_NEEDED.md` item, not a unilateral call.

## 6. Reporting
`PROGRESS.md` updated every session: units completed (spec refs), eval dashboard deltas, open `DECISIONS_NEEDED.md` items, spend on model calls (build-time Fable usage also budgeted: flag if projected > $400/phase).
