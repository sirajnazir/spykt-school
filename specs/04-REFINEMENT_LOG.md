# REFINEMENT LOG — Adversarial Critique Pass (append-only)

Method: after drafting v1 of 00–03, the full set was re-read against two tests:
(T1) "Can a fresh Claude Code session build this with zero conversation context?"
(T2) "Where does this break a real student, parent, coach, or the unit economics?"
Each gap below was patched into the v2.0 DEFINITIVE files. Gaps that require a human decision are logged, defaulted, and flagged — not silently resolved.

---

## GAP-01 — Cold start: GenomeScorer spec'd against "trailing 12 weeks of transcripts" that a new student doesn't have. (T2, severity: blocker)
**Found in:** 01 §4 routing table; 00 §5.1 assumed a running cycle.
**Resolution:** Added Onboarding Sequence (00-PRD §5.0): Discovery Diagnostic intake (structured student+parent intake, 60–90 min guided Zuzu session, document upload: transcript/activities/scores) → Genome Baseline pass (Fable, intake bundle ~80–150k tokens) → archetype assignment → Week-1 starter plan at reduced task load (3 tasks) → confidence values on all baseline scores marked `provisional` until week 6. Weekly Genome pass context tiers by tenure (GAP-04).

## GAP-02 — Sigmoid admission probabilities shown as numbers to families is a credibility and liability trap: the calibration base is ~24 students. (T2, severity: blocker)
**Found in:** 00 §2 lagging table + A2 contract exposing `sigmoid_admit_probs`.
**Resolution:** PRD §7.6 added — Probability Presentation Policy: sigmoid outputs are **internal planning signals** with uncertainty bands; surfaced to coaches with confidence intervals; surfaced to families only as coach-mediated tier guidance ("reach/target/likely") in quarterly reviews; never as "X% chance at <school>." Marketing/claims must not cite model probabilities. Calibration improves as cohort outcomes accrue; recalibration is a Verifier-tracked artifact.

## GAP-03 — Budget math in PRD §10 didn't survive arithmetic. A weekly 700k-token Fable Genome pass alone ≈ $7 → >$30/mo against an $18 ceiling. (T1+T2, severity: blocker — an autonomous builder would have enforced an impossible budget)
**Resolution:** Tiered context strategy + corrected budget (PRD §10 v2, 01 §4 v2):
- Weekly Genome pass: trailing **2 weeks raw** + consolidated CQ (~150–250k in / ~6–8k out) ≈ $1.8–2.9
- Monthly Deep pass: trailing 12 weeks raw (~500–700k) ≈ $5.5–7.8 (this is the 1M-context showcase job)
- Weekly Planner ~100–150k ≈ $1.2–1.8; Monthly Narrative ~250–350k ≈ $3–4; Monthly CQ consolidation ≈ $2–3
- **New ceilings: Fable ≤ $28, Sonnet ≤ $9, Haiku ≤ $1 → AI COGS ≈ $35–38/student/month.** Fine for the price point, but now it's *true*. Helicone enforcement unchanged.

## GAP-04 — "CQ retrieval top-k" for Zuzu was hand-waving: no embedding model, no ranking, no session buffer size. (T1, severity: major)
**Resolution:** 01 §4.2 added — Retrieval spec: embeddings via Voyage `voyage-3-large` (Anthropic-recommended; 1536-d out of the box — config-pinned, swappable), hybrid retrieval (pgvector cosine top-24 → Haiku rerank to 8 facts) + always-included pinned set (spike thesis, current plan, active commitments, coach annotations ≤ 90 days) + rolling session buffer 12 turns. Total Zuzu context ≤ 30k tokens enforced.

## GAP-05 — Plan-rejection loop unbounded: student can bounce drafts forever; nothing said. (T2, minor)
**Resolution:** 00 §5.1: student may request ≤2 redrafts (edits are free); a 3rd rejection routes to a Zuzu negotiation session; unresolved → coach escalation class-4 style ("planning deadlock"). UI spec F1 updated.

## GAP-06 — Parent digest could leak class-1 (wellbeing) events, undermining the coach-mediated contact protocol. (T2, severity: major)
**Resolution:** 02 §3 + 00 §6.2: digests are generated from an allowlist of event types; escalation classes 1–3 are excluded from all automated parent surfaces — parent contact for those is a human (coach) act per protocol, logged. Digest generator has a test enforcing the allowlist.

## GAP-07 — Evidence file storage unspecified. (T1, minor)
**Resolution:** 01 §3: Supabase Storage buckets (`evidence/`, per-student prefix, RLS-mirrored policies, signed URLs, 25MB cap, image EXIF-stripped on upload).

## GAP-08 — Coach coverage for 2am class-1 alerts: SLA implies 24/7 humans we may not have. (Human decision required)
**Default implemented:** class-1 alert fans out to assigned coach + on-call rotation (config table `oncall`); if unacknowledged in 15 min → admin phone tree. **Logged as OD-5 for Siraj: staffing/on-call policy is a business decision, not a spec decision.** Crisis-resources content in Zuzu holding pattern is static, human-approved copy.

## GAP-09 — Greenfield vs. existing codebase ambiguity: SPIKE already exists in production code. (T1)
**Default implemented (flagged in 03 §5):** new monorepo; SPIKE consumed as an existing HTTP service (`/api/scout/agentic` contract unchanged); no rewrite in MVP. Logged as DECISIONS_NEEDED default.

## GAP-10 — iMessage-signal ingestion appeared as an opt-in toggle with no tech path — scope-creep magnet.
**Resolution:** explicitly moved to Post-MVP (00 §8.2); toggle removed from v1 UI spec.

## GAP-11 — Model-string drift: specs referenced models informally.
**Resolution:** 01 §1 pins exact strings in config (`models.yaml`), single source; deprecation swaps are config + eval-run, not code.

## GAP-12 — Verifier could bootstrap evals from corpus samples containing PII before the Gateway exists (phase-ordering hazard).
**Resolution:** 03 Phase plan reordered — Verifier bootstrap moved into Phase 3 (after G2); Phases 0–2 use synthetic fixtures only; corpus extracts enter only post-G2 and pre-pseudonymized at rest.

## Residual risks accepted (explicitly, not silently)
- NER-based pseudonymization is probabilistic; mitigations: red-team suite, Fable-route body-logging disabled, leak classes become permanent regressions. Zero-risk alternative (no Fable on any student-derived text) sacrifices the core capability; decision: proceed with gateway + monitoring. **Owner sign-off required at G2.**
- 24-student calibration base for the sigmoid model: mitigated by GAP-02 presentation policy; real fix is cohort scale.
- Fable availability risk (June export-control episode as precedent): full-Opus degraded mode is a tested runbook (03 Phase 5), platform continues at reduced depth.
