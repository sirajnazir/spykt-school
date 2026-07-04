# Sentinel Wellbeing Corpus

Evaluation corpus for the A8 Sentinel wellbeing classifier (CLAUDE.md Phase 2;
PRD §6.2 escalation classes; TECH_SPEC §Verifier suites — recall target ≥ 0.98
on held-out cases, generous false-positive budget by design).

## Provenance

**All cases are synthetic**, generated for this build. No real student data,
no pseudonymized student data, and no data derived from any real conversation
is present anywhere in this corpus (CLAUDE.md guardrail: no real student data
before G2 sign-off). Raw source files live in `raw/*.jsonl`.

## G2 review note

**Human review is required at G2 before this corpus is pinned.** The G2 gate
(CLAUDE.md) requires the Sentinel recall report on the held-out split plus
human sign-off before any live-model traffic includes student-derived corpus
content. Until a human reviewer signs off, treat every file here as draft.

## Content policy (non-graphic)

Cases signal distress through language patterns — minimization, humor-masking,
oblique behavioral cues (e.g. giving away possessions), hopelessness framing —
and **never** contain graphic descriptions of self-harm, methods, plans, or
instructions. Any future addition that violates this policy must be rejected
in review. Hard negatives use common idioms ("this homework is killing me")
to exercise the classifier's false-positive boundary.

## Record schema

Each JSONL record: `id` (unique), `text` (non-empty, the student message),
`expected_class` (`1` = wellbeing, `2` = family conflict/pressure, `null` =
no escalation), `mode`, `severity` (`acute` / `elevated` / `null`), `notes`.

## Counts

Total: **340** cases (0 duplicates dropped at merge).

| mode | severity | expected_class | total | train | heldout |
|---|---|---|---|---|---|
| explicit | acute | 1 | 32 | 21 | 11 |
| explicit | elevated | 1 | 48 | 33 | 15 |
| masked | acute | 1 | 27 | 18 | 9 |
| masked | elevated | 1 | 53 | 35 | 18 |
| oblique | acute | 1 | 52 | 35 | 17 |
| oblique | elevated | 1 | 28 | 19 | 9 |
| family_conflict | — | 2 | 20 | 14 | 6 |
| hard_negative | — | null | 55 | 37 | 18 |
| negative | — | null | 25 | 16 | 9 |
| **total** | | | **340** | **228** | **112** |

## Split rule (deterministic — no randomness)

Built by `build_corpus.py`:

1. Merge `raw/*.jsonl` (sorted filename order); validate unique ids, required
   fields, `expected_class ∈ {1, 2, null}`, non-empty text.
2. Drop exact/near duplicates (normalize: lowercase, collapse whitespace,
   strip punctuation; the later occurrence is dropped and logged).
3. Stratify by `(mode, severity)`. Within each stratum, sort ids
   lexicographically; records at rank `r` with `r % 10 < 3` go to
   `corpus_heldout.jsonl` (~30%), the rest to `corpus_train.jsonl` (~70%).

No random module is used; re-running the script on the same raw files
produces byte-identical outputs (reproducible builds).

## Usage rules

- `corpus_train.jsonl` — prompt-development pool. Sentinel prompt authors may
  read, quote, and iterate against these cases.
- `corpus_heldout.jsonl` — **recall measurement ONLY.** The Sentinel prompt
  must never quote, paraphrase, or embed held-out cases; doing so invalidates
  the G2 recall report.
