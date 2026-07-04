#!/usr/bin/env bash
# Provision the four Railway services (01-TECH_SPEC §2): api, orchestrator, workers, web.
# Requires: railway CLI authenticated (`railway login`) and a project (`railway init`).
# Service-level config lives in each service's railway.json (build/start commands).
set -euo pipefail

for svc in api orchestrator workers web; do
  railway add --service "$svc" || echo "service $svc may already exist — continuing"
done

echo
echo "Services created. Set these variables per service (Railway dashboard or 'railway variables'):"
echo "  all python svcs : SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, UPSTASH_REDIS_URL, ANTHROPIC_API_KEY,"
echo "                    HELICONE_API_KEY, SENTRY_DSN"
echo "  api             : CLERK_SECRET_KEY, CLERK_WEBHOOK_SIGNING_SECRET, RESEND_API_KEY"
echo "  workers         : ONESIGNAL_APP_ID/KEY, TWILIO_* (coach SMS), STRIPE_SECRET_KEY (Phase 4)"
echo "  web             : NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY, CLERK_SECRET_KEY, NEXT_PUBLIC_API_URL"
echo
echo "Then deploy: railway up --service <svc> from the matching directory, or connect the GitHub repo."
