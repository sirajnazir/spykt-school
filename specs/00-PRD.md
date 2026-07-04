# PRD — Spykt Autonomous Prep Institution (v2.0 DEFINITIVE)

**Status:** Handoff-ready after adversarial critique pass (see 04-REFINEMENT_LOG.md)
**Owner:** Siraj Nazir (Founder/CEO, Ivylevel)
**Audience:** Claude Code (Fable 5) autonomous build agent + human reviewers
**Supersedes:** Living Context Architecture PRDs v1–v15, Master Spec v2.1 (this document does not replace those as knowledge sources; it is the build-scoped extraction)

---

## 1. Vision & Thesis

Democratize the college-prep infrastructure that elite private schools provide, by converting a proven 93-week human coaching methodology (Jenny Duan → Huda: 9 acceptances / 27 applications, 1150→1530 SAT, 6+ national awards) into an **AI-first, continuously running prep institution** with humans in the loop at the moments that matter.

**Founding formula:** `Agent Success = IQ × EQ × CQ × Data`
- **IQ** — reasoning quality (model capability; now Fable 5 at the deep layer)
- **EQ** — coaching empathy and timing (Jenny EQ Signals v1.2 corpus)
- **CQ (Context Quotient — the moat)** — longitudinal, compounding knowledge of this specific student
- **Data** — evidence of execution, captured continuously, not recalled at essay time

**What "autonomous, continuous" means here (and what it does not):**
- The system runs a weekly cycle for every student without a human initiating it: plan → execute → capture evidence → score → adapt.
- Agents work between sessions: scouting opportunities, scoring progress, drafting next-week plans, monitoring funding.
- It does **not** mean the AI makes high-stakes decisions alone. Autonomy is bounded by the HITL Autonomy Ladder (§6). A human is always reachable, and certain event classes route to humans unconditionally.

## 2. Outcomes Framework (Leading & Lagging)

The product is managed against a metric tree. Every feature must trace to a node.

### Lagging outcomes (the point, but unmovable week-to-week)
| Metric | Definition | Target (cohort) |
|---|---|---|
| Admission outcomes | Acceptances at reach/target/safety vs. Genome-predicted baseline | Beat sigmoid-model baseline by ≥15pp |
| National-level recognition | Awards/publications/competitive placements per student | ≥1 by end of junior year |
| Test delta | SAT/ACT improvement vs. entry diagnostic | +200 SAT median |
| Scholarship & funding $ | Dollars secured via SPIKE-discovered opportunities | ≥ program cost 2x median |
| Spike depth | Depth-of-commitment score in declared spike domain (rubric §2.3) | Level 4+ ("recognized outside school") by application season |

### Leading indicators (what the system actually drives weekly)
| Metric | Definition | Why it predicts |
|---|---|---|
| Weekly Execution Rate (WER) | % of committed weekly tasks completed with evidence | Direct proxy for the Jenny cadence; in the 93-week corpus, sustained WER ≥70% preceded every major outcome |
| Evidence Capture Rate | Artifacts logged per completed task | No evidence = no essay material = no CQ growth |
| CQ Growth Index | Net new structured intelligence per student per month (facts, signals, narrative threads) | The moat compounds or it doesn't |
| Opportunity Conversion | Applied ÷ surfaced (Scout) and won ÷ applied | Measures both matching quality and student follow-through |
| Narrative Coherence Score | Fable-scored alignment between activities and declared spike thesis | Detects "checklist drift" 12+ months before essays |
| Momentum Streak | Consecutive weeks with WER ≥ 50% | Attrition predictor; streak breaks trigger coach escalation |
| Session Depth Signal | EQ-schema signals per Zuzu session (openness, ownership language, initiative) | Early-warning for disengagement and for wellbeing escalation |

### Anti-metrics (explicitly not optimized)
- Raw time-in-app / message volume (engagement theater; a student grinding chat is a failure signal, not success)
- Number of activities (breadth is the enemy of spike depth)

### 2.3 Spike Depth Rubric (scored by GenomeScorer, verified by coach quarterly)
L1 participation → L2 sustained contribution → L3 leadership/creation → L4 external recognition → L5 field-level impact. Progression targets are per-archetype (student archetypes from the coaching intelligence corpus).

## 3. Users & Roles

| Role | Core job | Autonomy relationship |
|---|---|---|
| **Student (14–18, minor)** | Execute weekly plan, grow spike, capture evidence | Primary user of Zuzu; consents to plan changes L1+; can always summon a human |
| **Parent/guardian** | Fund, trust, support without micromanaging | Read-mostly dashboard; consent authority for L2/L3 actions, data-sharing, and SPIKE financial actions; weekly digest |
| **Human coach** | Judgment at leverage points; relationship anchor | Owns escalation queue; approves L2 plans; monthly live session; quarterly Genome review sign-off |
| **Admin (Ivylevel ops)** | Cohort health, coach load balancing, model-spend governance | Ops console; no access to raw session content without audit-logged reason |

**Role boundary (non-negotiable):** Zuzu is a coach for execution and strategy. It is not a therapist, and it does not position itself as the student's friend-of-last-resort. Wellbeing signals route to humans (§6.4).

## 4. Agent Roster (the "institution")

Each agent = a Specialist: a system prompt + tool set + JSON I/O contract on the direct Anthropic API (asyncio orchestration; no framework layer). Contracts in 01-TECH_SPEC §5.

| # | Agent | Model (default) | Cadence | One-line mandate |
|---|---|---|---|---|
| A0 | **Orchestrator** | Sonnet 4.6 | event-driven | Routes events to specialists; enforces autonomy ladder; owns the weekly cycle state machine |
| A1 | **Zuzu Coach** | Sonnet 4.6 (Haiku 4.5 for ack/nudge turns) | live | Conversational coaching; session structure mirrors Jenny session arc; emits EQ signals |
| A2 | **GenomeScorer** | **Fable 5** | weekly deep pass + on-demand | 4 Rings × 23 subfactors, Chetty modifiers, sigmoid admission model — scored against **raw longitudinal transcripts** in 1M context |
| A3 | **Pathway Planner** | **Fable 5** (weekly) / Sonnet (mid-week amendments) | weekly | Produces next-week plan + quarter roadmap diffs from Genome deltas |
| A4 | **Opportunity Scout** | Sonnet 4.6 + web tools | daily batch | Surfaces competitions, programs, publication venues matched to spike + archetype |
| A5 | **SPIKE Money Agent** | Sonnet 4.6 (existing `/api/scout/agentic`) | weekly + on-demand | Education-funding discovery; ships with the timeout/fallback fixes + `harvestToolResultUrls()` salvage path |
| A6 | **Evidence Curator** | Haiku 4.5 | on capture | Tags, structures, and files every artifact into the CQ store; prompts for missing proof |
| A7 | **Narrative Architect** | **Fable 5** | monthly | Maintains the spike thesis; scores coherence; flags checklist drift; (senior year) essay-scaffolding mode |
| A8 | **Escalation Sentinel** | Haiku 4.5 classifier + human queue | every event | Detects wellbeing signals, autonomy-ladder breaches, low-confidence outputs → routes to coach |
| A9 | **Verifier** | **Fable 5** | continuous in build; weekly in prod | Self-built eval harnesses over other agents' outputs (scoring consistency, plan quality, extraction fidelity) |

**Model routing is a cost/latency/depth decision, not a prestige decision.** Fable 5 is reserved for the four jobs where 1M-context long-horizon analysis is the actual requirement (A2, A3-weekly, A7, A9). Live turns never touch Fable (latency + $10/$50 per MTok + retention constraint).

## 5. Core Product Loops

### 5.0 Onboarding Sequence (cold start — GAP-01)
New students have no transcript history, so the weekly cycle is seeded, not assumed:
1. **Discovery Diagnostic intake** — 60–90 min guided Zuzu session (structured arc) + parent intake form + document upload (school transcript, activities list, test scores, optional prior work samples).
2. **Genome Baseline pass** — Fable 5 scores the intake bundle (~80–150k tokens, pseudonymized); all baseline scores carry `provisional` confidence until week 6.
3. **Archetype assignment** + spike-thesis hypothesis (explicitly labeled a hypothesis; revisited at week 6 and quarterly).
4. **Week-1 starter plan** at reduced load (3 tasks) to establish the evidence-capture habit before intensity.
Weekly Genome context then tiers by tenure (TECH §4): weekly light pass (trailing 2 weeks raw + consolidated CQ) and a monthly deep pass (trailing 12 weeks raw — the 1M-context job).

### 5.1 The Weekly Cycle (primary loop — mirrors the Jenny cadence)
```
SUN 18:00  Pathway Planner (Fable) drafts next-week plan from Genome delta
SUN 18:30  Verifier gates the plan (quality rubric ≥ threshold)
SUN 19:00  Plan → coach queue if L2 diff, else → student for consent (L1)
MON        Student accepts/edits plan with Zuzu (15-min planning session)
MON–SAT    Execution: task nudges (Haiku), evidence capture (A6), micro-coaching (Zuzu)
SAT        Weekly reflection session (Zuzu, 20 min, Jenny session arc)
SAT 22:00  GenomeScorer (Fable) deep pass over the week's raw material
SUN        Digest to parent; streak/WER update; cycle repeats
```
Failure handling: missed reflection → compressed async reflection; 2 consecutive missed weeks → coach escalation (streak break). Plan rejection is bounded (GAP-05): student may request ≤2 redrafts (inline edits are always free); a third rejection routes to a Zuzu negotiation session; still unresolved → "planning deadlock" coach escalation.

### 5.2 Continuous background loops
- **Scout loop (daily):** new opportunities scored vs. Genome; ≥ match threshold → student's Opportunity Feed with "why you" reasoning shown.
- **SPIKE loop (weekly):** funding landscape re-scan; new grants/scholarships → parent + student.
- **CQ compounding loop (on every event):** Evidence Curator writes structured intelligence; monthly Fable consolidation pass dedupes/threads it.

### 5.3 Human session loop
- Monthly live coach session (video), agenda auto-drafted by Pathway Planner from the month's Genome trajectory.
- Quarterly Genome Review: coach reviews and countersigns the Fable scoring; disagreements become Verifier training cases.

## 6. Human-in-the-Loop Design

### 6.1 Autonomy Ladder (per action class)
| Level | Meaning | Examples | Consent path |
|---|---|---|---|
| **L0 — Autonomous** | Agent acts, logs | Nudges, evidence filing, opportunity surfacing, digest emails | None (visible in activity log) |
| **L1 — Student consent** | Agent proposes, student accepts | Weekly plan, task swaps, session scheduling | In-app accept/edit |
| **L2 — Coach approval** | Agent proposes, coach approves diff | Quarter roadmap changes, spike-thesis pivot, test-prep strategy change | Coach queue, 48h SLA |
| **L3 — Parent + coach** | Requires guardian sign-off | Anything with money (SPIKE applications w/ fees, program enrollment), external submissions in student's name, data-sharing beyond platform | Dual sign-off flow |

### 6.2 Escalation classes (Sentinel → human, unconditional)
1. **Wellbeing** — distress signals, self-harm indicators, disordered patterns, acute anxiety spikes in session language → **immediate coach alert (push + SMS), Zuzu shifts to supportive holding pattern (no coaching pressure), never handles it alone.** Coach owns parent contact per protocol.
2. **Family conflict / pressure signals** — coach review within 24h.
3. **Integrity** — plagiarism/fabrication signals in evidence or essays → coach.
4. **Low confidence** — any Fable/Sonnet output below confidence threshold or failing Verifier gate → coach queue instead of student.
5. **Model refusal** — Fable `stop_reason: refusal` events beyond fallback → engineering + coach visibility.

### 6.2.1 Parent-surface allowlist (GAP-06)
All automated parent surfaces (digest, Family Home) generate exclusively from an allowlisted event-type set. Escalation classes 1–3 are excluded from every automated parent surface; parent contact for those is a human (coach) act per protocol, and it is logged. The digest generator carries a test enforcing the allowlist.

### 6.3 Human override is total
Coach can freeze any agent per-student, roll back any plan, and annotate the CQ store; annotations outrank model inferences at next scoring pass.

### 6.4 Why HITL is a feature, not a tax
Positioning: parents buy trust. Every autonomous action is inspectable ("show me why"), every consequential action has a human name on it. This is the sales counter to "you're letting an AI raise my kid."

## 7. Privacy, Safety & Compliance (minors — this section gates the tech spec)

1. **Trust Data Layer commitments hold.** Raw student-identifiable content **never** goes to Fable 5 given its mandatory 30-day retention / no-ZDR status. All Fable calls pass through the **Pseudonymization Gateway** (01-TECH_SPEC §7): stable pseudonym IDs, PII scrubbing (names, school, contacts, addresses) with reversible mapping held only in Supabase under RLS. Sonnet/Haiku live traffic follows existing (less restrictive) policy but uses the same gateway by default. If pseudonymization for a given payload cannot be verified, the Fable call is blocked and the job degrades to Opus 4.8 under standard retention terms — logged as a degraded-quality event.
2. **Consent architecture:** parent consent at onboarding (COPPA-conscious for U13 exclusion — platform is 14+; FERPA-adjacent handling for school records); student assent; per-feature consent toggles. (iMessage-style signal ingestion is deferred to Post-MVP entirely — GAP-10 — and will arrive with its own consent flow.)
3. **Data rights:** full export; deletion cascades through CQ store, embeddings, and provider-side deletion requests; retention schedule documented per store.
4. **Model safety posture:** Zuzu system prompts enforce role boundary (§3); no diagnosis language; escalation-first on wellbeing; no engagement-maximizing dark patterns (streaks pause for illness/vacation without penalty — "protected weeks").
5. **Auditability:** every agent action carries `{agent, model, prompt_version, autonomy_level, human_approver?}` in an append-only audit log.
6. **Probability Presentation Policy (GAP-02):** sigmoid admission-model outputs are internal planning signals calibrated on a small base (~24 students). They surface to coaches with uncertainty bands; to families only as coach-mediated tier guidance ("reach/target/likely") in quarterly reviews; **never** as a numeric chance at a named school, anywhere, including marketing. Recalibration as cohort outcomes accrue is a Verifier-tracked artifact.

## 8. Scope

### 8.1 MVP (Build Phase 1–3, see 03-BUILD_HANDOFF)
Weekly cycle end-to-end for one cohort (10 students): Orchestrator, Zuzu, GenomeScorer (Fable raw-transcript pass), Pathway Planner, Evidence Curator, Sentinel, coach console (escalation queue + plan approval), student app (Today/Plan/Chat/Evidence), parent digest email. SPIKE integrates as-is (existing service, with the Phase-2 latency work tracked separately).

### 8.2 Post-MVP
Opportunity Scout, Narrative Architect essay mode, multi-coach ops console, Builder/Hybrid Explorer verticals, Discovery Diagnostic as self-serve wedge funnel.

### 8.3 Non-goals (v1)
- No application submission automation (students submit; system prepares).
- No fine-tuned Jenny twin in the loop (the GPT-4o-mini twin work is a corpus asset, not a runtime dependency; Fable + EQ-signal prompting replaces it).
- No school-facing product.
- No real-time voice.

## 9. Success Criteria for the Build Itself
The build is "done" (per 03-BUILD_HANDOFF Definition of Done) when: (a) a full weekly cycle runs for a seeded synthetic cohort with zero manual intervention across 4 simulated weeks, (b) all Verifier eval suites pass at thresholds, (c) all L2/L3 actions provably block without human approval, (d) the Pseudonymization Gateway passes its red-team suite (no PII in any recorded Fable payload), and (e) live-turn p95 latency ≤ 3.5s, weekly Fable pass ≤ 30 min/student, unit cost within §10 budget.

## 10. Unit Economics Guardrails (model spend — corrected math per GAP-03)
Per-student monthly Fable workload at $10/$50 per MTok:
| Job | Cadence | Tokens (in/out) | ~Cost |
|---|---|---|---|
| Genome weekly light pass | 4.3×/mo | 150–250k / 6–8k | $8–12 |
| Genome monthly deep pass (12-wk raw, 1M-context job) | 1×/mo | 500–700k / 12–15k | $5.5–7.8 |
| Pathway Planner | 4.3×/mo | 100–150k / 4–6k | $5–8 |
| Narrative Architect | 1×/mo | 250–350k / 8–10k | $3–4 |
| CQ consolidation | 1×/mo | ~200k / 5k | $2–3 |

**Ceilings (Helicone-enforced): Fable ≤ $28, Sonnet ≤ $9, Haiku ≤ $1 → AI COGS ≈ $35–38/student/month.** Orchestrator pre-flight-estimates token cost; over-ceiling Fable jobs degrade to Opus 4.8 with a `degraded_budget` flag. Budgets are config, not code. Pricing must clear COGS with margin — flag to founder if any spec change pushes COGS above $45.

## 11. Open Decisions (tracked, not blocking)
- OD-1: Whether quarterly Genome Review is billable coach time or bundled.
- OD-2: Opportunity Scout web-source allowlist governance (who curates).
- OD-3: Whether parents see Narrative Coherence Score raw or coach-mediated (current: coach-mediated; raw scores misread easily).
- OD-4: SPIKE Phase-2 latency architecture (separate track; target <20s).
- OD-5 (GAP-08): Coach on-call/coverage policy for off-hours class-1 alerts. Spec default: assigned coach + on-call rotation, 15-min unacknowledged → admin phone tree. Staffing model is a founder decision.
