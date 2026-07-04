# PROGRESS

> Updated every session per CLAUDE.md §6. Newest session first.

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
