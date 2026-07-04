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

## D-003 — Contract schema shapes that deviate from the spec's abbreviated sketch (review at G1)
**Class:** design (G1 explicitly reviews schemas)
**Deviations, each deliberate:**
1. A2 Genome: the spec sketch shows ring names at the top level (`{ring→subfactor→{...}}`); the schema
   nests the ring map under a `rings` key because dynamic top-level keys don't compose with
   `additionalProperties:false` validation. Wire shape: `{rings, sigmoid_admit_probs, flags}`.
2. A8 Sentinel / escalate envelope: `class` is a Python keyword, so pydantic models use
   `escalation_class` with wire alias `class`; the wire contract is unchanged (`class`), and property
   tests pin the wire key.
3. Retention-gate spec tension (also documented in `retention.py`): PRD §7.1 says unverifiable
   pseudonymization "degrades to Opus"; the CLAUDE.md G1 gate says "attestation stripped → client raises".
   Implemented the safe direction: the client always raises; the degrade-to-Opus-with-logged-event flow
   belongs to the Orchestrator (Phase 3), which may catch and re-route.
4. Refusal classifier id is recorded as a colon suffix on the audit action (`fable_refusal:<id>`) because
   `audit_log` has no detail column. Alternative: add a `detail jsonb` column in a Phase 2 migration.
**Needs:** G1 sign-off (approve or direct changes) — none of these block Phase 2 prep work.
**RESOLVED 2026-07-04:** G1 approved by Siraj as implemented.

## D-004 — Sentinel class-4/5 envelope semantics (review at G2 with prompt pinning)
**Class:** design
**Ambiguity:** PRD §6.2 classes 4 (low confidence) and 5 (model refusal) have delivery mechanisms outside
the Sentinel result: class-4 routes via `status=low_confidence` in the envelope, class-5 is raised by the
client refusal middleware. The Sentinel prompt taxonomy also uses class 4 as the "no escalation" bucket
(the a8 schema requires class 1–5, so negatives need a class).
**Default implemented:** escalate directive fires for classes 1–3 (unconditional-to-human per PRD §6.2 —
class 3 was a verifier catch); class-4 rides the envelope status; class-5 originates in middleware, not
classification. Any prompt change at G2 requires a PROMPT_VERSION bump + recall re-run.
**Needs:** G2 confirmation when the Sentinel prompt is pinned.

## D-005 — G2 gate items blocked on environment/credentials (human action)
**Class:** operational
1. **Live Sentinel recall run** (target ≥0.98 on 112 held-out cases, 79 class-1 — at most 1 miss allowed):
   harness ready (`evals/sentinel/run_recall.py`), needs `ANTHROPIC_API_KEY` (~$1–2 of Haiku).
2. **Class-1 ≤5s in staging:** local end-to-end analogue passes (tests/integration/test_class1_end_to_end.py,
   <1s with model stubbed); the staging measurement needs the Railway env (D-002).
3. **Helicone request-body logging OFF for Fable routes** (01 §7 residual-risk mitigation): dashboard/config
   act at provisioning time; code never logs Fable bodies.
4. **Human review of the synthetic wellbeing corpus (340 cases) and the Zuzu crisis copy**
   (`holding_copy.py` is flagged `PENDING_G2_HUMAN_APPROVAL`; includes 988 + Crisis Text Line).
5. **Coach SMS phone source:** coaches/oncall tables carry no phone column by design; production must join
   coach ids to phones from Clerk profile metadata or deploy config (documented on `CoachContact`).
