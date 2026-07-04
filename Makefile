# Spykt monorepo — common entry points. CI runs the same targets.

DB_URL := postgresql://postgres:postgres@localhost:55432/spykt_test

.PHONY: test test-python test-web rls-test db-up db-down eval-gate ci

test: test-python test-web

test-python:
	uv run pytest -q

test-web:
	cd apps/web && npm run typecheck && npm run build

db-up:
	docker compose -f infra/supabase/docker-compose.yml up -d --wait

db-down:
	docker compose -f infra/supabase/docker-compose.yml down -v

rls-test: db-up
	DATABASE_URL=$(DB_URL) uv run pytest infra/supabase/tests -q

eval-gate:
	uv run python evals/run_gate.py

ci: test rls-test eval-gate
