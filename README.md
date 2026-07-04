# spykt-school

Spykt Autonomous Prep Institution — build repo. Specs in `specs/`, build protocol in `CLAUDE.md`.

## Layout

```
apps/web/                  Next.js 14 (student/parent/coach/admin surfaces)
services/api/              FastAPI gateway (Railway svc: api)
services/orchestrator/     A0 Orchestrator worker (event bus consumer, state machine)
services/workers/          Specialist pool + Fable cron jobs
packages/contracts/        Specialist JSON I/O contracts (Phase 1)
packages/anthropic-client/ Shared Anthropic client + mandatory middlewares
evals/                     Eval suites (pinned at G3) + CI gate runner
infra/supabase/            Migrations, RLS smoke tests, local DB compose
infra/railway/             Service provisioning
infra/clerk/               Auth roles + webhook setup
```

## Development

Requires: [uv](https://docs.astral.sh/uv/), Node 20+, Docker (for the RLS test).

```
uv sync --all-packages     # python workspace
make test                  # python tests + web typecheck/build
make rls-test              # spins up pgvector Postgres, runs the RLS smoke suite
make eval-gate             # eval gate (vacuous pre-G3)
```

## Build protocol

Autonomous build per `CLAUDE.md` — phase plan, human gates (G1–G4), and reporting in
`PROGRESS.md` / `DECISIONS_NEEDED.md` / `EVAL_CHANGES.md`.
