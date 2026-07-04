# DECISIONS_NEEDED

> Per CLAUDE.md prime directive 4: ambiguities get a recommended default, implemented behind config,
> and logged here. Compliance-class items stall loudly instead. Append-only per item; mark resolved inline.

---

## D-001 — RLS identity bridge: Clerk JWT → Postgres policies (review at G1)
**Class:** security design (not compliance-blocking; G1 reviews it before real data)
**Ambiguity:** Stack locks Clerk for auth and Supabase RLS as the security boundary (01 §1), but does not
specify how Clerk identities reach Postgres policy evaluation.
**Default implemented:** Supabase third-party-auth pattern — policies call `app.clerk_id()`, which reads
`request.jwt.claims ->> 'sub'` (the Clerk user id). Role checks read the `role` claim. Works identically
under Supabase (PostgREST sets the GUC from the verified JWT) and in CI (test harness sets the GUC
directly under a non-privileged role). See `infra/supabase/migrations/0001_core.sql`.
**Config surface:** claim names centralized in the `app.clerk_id()` / `app.role()` SQL helpers.
**Needs:** G1 sign-off on the approach alongside schema review.

## D-002 — Live provisioning requires credentials (human action)
**Class:** operational (not blocking the build loop)
**Ambiguity:** Phase 0 calls for "Railway + Supabase provisioning scripts" and a "hello-world deploy";
no Railway/Supabase/Clerk credentials exist in this environment.
**Default implemented:** provisioning is scripted (`infra/railway/provision.sh`, `infra/supabase/provision.sh`,
`infra/clerk/README.md`) and the CI deploy job is a guarded stub that no-ops with a clear message until
`RAILWAY_TOKEN` is set as a GitHub secret. CI "green on hello-world" is satisfied by build+test+RLS-smoke jobs.
**Needs:** Siraj to run the provisioning scripts (or provide tokens) when ready to stand up live envs.
