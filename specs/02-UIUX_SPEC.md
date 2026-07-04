# UI/UX SPEC — Spykt Autonomous Prep Institution (v2.0 DEFINITIVE)

**Audience:** Claude Code (Fable 5) build agent; frontend built per `/mnt/skills/public/frontend-design` conventions when in the build environment.
**Platform:** Next.js 14, mobile-first responsive (student surface is a phone product; coach/parent surfaces desktop-friendly).

---

## 0. Design Principles (every screen is tested against these)

1. **Agency, not surveillance.** The student is the protagonist; agents are staff. Every AI action shows a "Why" affordance (one tap → rationale from audit trail). Nothing about the student is shown to parents that the student can't see is being shown (transparency indicator on shared views).
2. **Depth over dashboard.** One spike, one weekly plan, ≤5 tasks. The UI physically resists breadth (no "add 10 activities" affordances).
3. **Calm autonomy.** Background agents produce *artifacts* (a drafted plan, a found opportunity), never anxiety (no "Zuzu is thinking about you" ambient noise). Notifications budget: ≤2 push/day student, ≤1/week parent digest, escalations only for coach.
4. **Human moments are sacred.** Coach sessions and escalations get first-class, warm UI; nothing about HITL should feel like a compliance checkbox.
5. **No dark patterns.** Streaks pause (protected weeks) without shame states; no red badges for missed reflection; language is Jenny-toned (from EQ corpus), never guilt-toned.
6. **Age-appropriate voice.** Zuzu register: warm, direct, coach-like; zero therapy-speak, zero corporate-speak.

## 1. Information Architecture

```
Student (mobile-first)          Parent (web/mobile)         Coach (desktop console)
├─ Today                        ├─ Family Home              ├─ Escalation Queue   ★ default tab
├─ Week (plan)                  ├─ Outcomes (leading+lag)   ├─ Approvals (L2/L3 diffs)
├─ Zuzu (chat)                  ├─ Funding (SPIKE)          ├─ Roster (students, Genome sparklines)
├─ Spike (thesis+evidence)      ├─ Approvals (L3)           ├─ Student Detail (Genome, plan, CQ, sessions)
├─ Opportunities (feed)         └─ Trust Center             ├─ Session Room (monthly live, auto-agenda)
└─ Me (streaks, settings)                                   └─ Quarterly Review workspace
Admin: cohort health, coach load, model spend, prompt/eval registry (thin; tables + charts)
```

## 2. Student Surface

### 2.1 Today (home)
- **Header:** week N of cycle, streak chip (pauses gray during protected weeks — never breaks red).
- **Now card:** single next task (title, spike-alignment tag, effort, "Why this" link). Primary CTA: `Start` → focus mode with timer + evidence capture on completion.
- **Evidence nudge:** if task done w/o evidence: gentle bottom sheet "Capture proof (photo/link/file/note) — future-you writing essays will thank you."
- **Zuzu entry:** persistent FAB; badge only when Zuzu owes the student something (never to summon them).
- States: empty (Sunday pre-consent → "Your week is drafted — review it"), all-done (celebration, next-session countdown), protected week (rest state art).

### 2.2 Week (plan consent — the L1 moment)
- Drafted plan arrives as a **proposal**: tasks listed with rationale lines ("because your Genome shows Aptitude/Research jumped after the lab reachout").
- Actions per task: accept / edit / swap (swap opens 2 Planner alternates) / discuss with Zuzu.
- `Commit my week` → consent artifact recorded; header shows human chain when L2 ("Reviewed by Coach Maya ✓").
- Diff view when a mid-week amendment is proposed (changed items highlighted; nothing silently mutates).

### 2.3 Zuzu (chat)
- Streaming chat; session types: Monday planning (15m guided arc), Saturday reflection (20m Jenny arc: wins → friction → learning → next), micro-coaching (freeform).
- Structured moments render as cards inline (commitment card, evidence request, opportunity card) — chat is the OS, cards are the apps.
- **Boundary behaviors (build-verified):** wellbeing signal → Zuzu shifts to supportive holding pattern + "I've looped in Coach Maya, she'll reach out today" (only after Sentinel fires; never fake it); requests to do the work for them → scaffold, not ghost-write, with visible integrity note.
- "Talk to a human" is always in the overflow menu, one tap, no friction, no guilt copy.

### 2.4 Spike
- Thesis statement (current version + history), depth level (L1–L5 rubric visual), evidence timeline (Curator-filed artifacts, filterable), Narrative coherence shown as coach-mediated guidance text, not a raw score (PRD OD-3).

### 2.5 Opportunities
- Feed of Scout/SPIKE cards: deadline, effort, "Why you" (Genome-matched reasoning, 2 lines), match confidence as words not percentages ("strong fit").
- Actions: Save / Start application task (adds to next plan draft) / Not for me (captured as CQ signal with optional why).
- L3-gated items (fees) show the parent-approval state inline ("Waiting on parent ✓ sent Tuesday").

### 2.6 Me
- Streaks + momentum history (calm line, not gamified fire), settings: notification budget, protected week request (student-initiated, coach-confirmed), data controls (view "what the system knows about me" → CQ facts browser with dispute button — disputes become coach annotations).

## 3. Parent Surface

**Parent surfaces render exclusively from the allowlisted event set (PRD §6.2.1). Escalation classes 1–3 never appear in any automated parent view — that contact is coach-made, human, and logged.**

### 3.1 Family Home — this week's plan (titles only), WER trend, next human session, one Zuzu-free summary line written by Planner ("This week is about depth in the research spike").
### 3.2 Outcomes — leading indicators charted (WER, evidence, opportunity conversion, momentum) with plain-language explainers; lagging outcomes section (tests, awards, funding won); explicitly no live Genome scores (quarterly coach-mediated report instead — prevents week-to-week score anxiety).
### 3.3 Funding (SPIKE) — discovered funding cards, provider labels always visible (regression: never mislabel catalog vs. agentic provenance), total secured tracker, L3 approval inbox.
### 3.4 Approvals — L3 dual-sign-off flows: money, external submissions, data sharing. Each shows: what, why (agent rationale), coach's sign-off state, cost, deadline. Approve/decline/ask-coach.
### 3.5 Trust Center — the sales-differentiating screen: live autonomy ladder explainer, full activity log for their student (L0 actions included), data map ("what goes to which model, what's pseudonymized, retention per store"), export & delete controls, consent toggles.

## 4. Coach Console

### 4.1 Escalation Queue (default) — sorted class-1 first; class-1 rows are visually distinct and also fired to push+SMS; each card: student, class, severity, evidence snippet, SLA timer, actions (resolve w/ note → CQ annotation, schedule session, contact parent per protocol, freeze agents).
### 4.2 Approvals — L2 plan/roadmap diffs rendered as side-by-side (current vs proposed, changed nodes highlighted, Planner rationale inline, Verifier score chip). Approve / edit-then-approve / reject-with-note (note becomes Planner context next cycle). 48h SLA timers.
### 4.3 Roster — table: student, WER sparkline, streak, last session, open escalations, Genome trend arrow; sort by "needs attention" composite.
### 4.4 Student Detail — tabs: Genome (rings visualization, subfactor drill-down with evidence refs and Fable rationale summaries), Plan history, CQ browser (facts + annotations; coach annotations authored here **outrank model inference** — labeled as such), Session transcripts (coach-only), Audit trail.
### 4.5 Session Room — monthly live session: auto-drafted agenda (editable), shared screen of spike timeline, in-session note → CQ annotation pipeline, post-session summary drafted by Zuzu for coach approval before filing.
### 4.6 Quarterly Review workspace — side-by-side: Fable Genome scoring vs. coach judgment per subfactor; countersign or dispute (disputes auto-file as Verifier training cases).

## 5. Cross-cutting Components
- **"Why" drawer** — universal: any AI artifact → agent, model, prompt version, rationale summary, human approver if any. (Audit log → human-readable rendering.)
- **Consent artifacts** — visually consistent stamp component (who, when, level) on plans, approvals, submissions.
- **Degraded-quality chip** — when a job ran on fallback (Opus/budget/refusal), affected artifacts show a subtle "generated in reduced mode" marker to coach/admin (not student).
- **Notification system** — respects per-role budgets (§0.3); all notifications deep-link.

## 6. Accessibility & Quality Bars
WCAG 2.1 AA; full keyboard nav on coach console; reduced-motion respect; dyslexia-friendly type option (student setting); dark mode student surface; p75 route TTI < 2.5s mobile.

## 7. Key Flows (build-testable acceptance flows — mirrored in 03-BUILD_HANDOFF)
F1 Weekly consent: draft → student review → edit one task → commit → plan ACTIVE. (Variant F1b: three rejections → Zuzu negotiation session → unresolved routes to coach as planning deadlock.)
F2 L2 approval: Planner proposes roadmap change → coach diff → edit → approve → student notified with change explanation.
F3 Class-1 escalation: wellbeing phrase in chat → Zuzu holding pattern + Sentinel fires ≤5s → coach push+SMS → queue top; student sees human-loop message; no plan pressure until resolved.
F4 L3 funding: SPIKE surfaces fee-bearing program → coach sign-off → parent approval card → both signed → task created.
F5 Evidence: task complete → capture sheet → Curator tags → appears on Spike timeline ≤30s.
F6 "Why": student taps Why on a task → drawer shows rationale + Genome basis in <1s.
F7 Dispute: student disputes a CQ fact → coach annotation flow → next Genome pass reflects it.
