#!/usr/bin/env bash
# Provision/link the Supabase project and push migrations.
# Requires: supabase CLI, SUPABASE_PROJECT_REF + SUPABASE_DB_PASSWORD env (or interactive login).
set -euo pipefail
cd "$(dirname "$0")"

: "${SUPABASE_PROJECT_REF:?Set SUPABASE_PROJECT_REF (from the Supabase dashboard)}"

supabase link --project-ref "$SUPABASE_PROJECT_REF"
supabase db push --include-all

echo "Supabase migrations pushed."
echo "Manual follow-ups (documented, one-time):"
echo "  1. Enable Clerk third-party auth (Dashboard → Auth → Third-party) so request.jwt.claims carries Clerk claims."
echo "  2. Create the 'evidence' storage bucket with per-student-prefix policies (Phase 3 work, GAP-07)."
