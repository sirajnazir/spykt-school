# Gateway Red-Team Suite

Adversarial PII-scrubbing suite for the Pseudonymization Gateway
(TECH_SPEC §7 — Build Phase 2 gate; CLAUDE.md G2: Gateway red-team recall
≥ 0.995, and zero PII in captured Fable-route request bodies during the run).

## Provenance

**All cases are synthetic**, generated for this build. No real student data,
no pseudonymized student data, and no data derived from any real conversation
is present anywhere in this suite. Raw source files live in `raw/*.jsonl`;
the assembled suite is `cases.jsonl`, built by `build_suite.py`.

## Record schema

Each JSONL record in `cases.jsonl`:

- `id` — unique string.
- `category` — adversarial category (see counts below).
- `text` — the message sent through the Gateway (the scrub input).
- `known_entities` — what the `pseudonym_map` already knows for this
  student: `name` (string), `school` / `city` (string or null),
  `family_names` / `emails` / `phones` / `handles` (lists of strings),
  optional `nicknames` (list of strings). Entities seeded in `text` but
  absent from `known_entities` must be caught by the NER/regex layer.
- `seeded_pii` — list of `{kind, value}`; every `value` is a **verbatim
  substring** of `text` (enforced at build time — see validation).
- `notes` — why the case is adversarial.

## Counts

Total: **240** cases, **532** seeded PII values (0 dropped at merge).

### Per category

| category | cases |
|---|---|
| contact | 60 |
| formats | 60 |
| names | 60 |
| places | 60 |
| **total** | **240** |

### Seeded PII per kind

| kind | seeded values |
|---|---|
| person | 289 |
| org | 81 |
| email | 38 |
| place | 32 |
| handle | 29 |
| phone | 25 |
| address | 23 |
| id_number | 15 |
| **total** | **532** |

## Scoring rule

Run every case `text` through the Gateway scrub path and check each
`seeded_pii.value` against the scrubbed output:

- A seeded value is **caught** if it is absent (as a verbatim substring)
  from the scrubbed output.
- A seeded value is **leaked** if it survives verbatim.

```
recall = (seeded values absent from scrubbed output) / (total seeded values)
```

**Gate: recall ≥ 0.995** over all 532 seeded values. At current size that
means **at most 2 leaked values** pass the gate. Scoring is per seeded
value, not per case, so multi-entity cases weigh more — deliberately: a
message that leaks three identifiers is a worse failure than one that
leaks one.

In addition to the recall threshold, the G2 gate requires **zero PII found
in captured Fable-route request bodies** during the suite run (CLAUDE.md
Phase 2 gate). The recall metric does not supersede that check.

## Validation at build time (`build_suite.py`)

1. Merge `raw/*.jsonl` (sorted filename order).
2. **Hard errors** (suite not written; fix the raw file): missing fields,
   duplicate ids, malformed `known_entities` or `seeded_pii`.
3. **Dropped and logged**: any case with a `seeded_pii.value` that is not
   a verbatim substring of its `text` — a value that cannot be located in
   the input can never be checked against the output, so the case cannot
   be scored and must not count toward the gate.
4. **Dropped and logged**: duplicate texts (exact/near, after lowercasing,
   whitespace collapse, punctuation strip); first occurrence kept.
5. Output is deterministic: sorted by `id`, sorted JSON keys — re-running
   on the same raw files is byte-identical.

Invariants are re-checked in CI by `test_cases.py`.

## Permanent-regression policy (TECH_SPEC §7)

**Any leak class found later becomes a permanent case.** When a PII leak is
discovered anywhere — prod sampling, coach report, Helicone metadata audit,
a later eval — a synthetic case reproducing that leak *class* (never the
real leaked value or any real student data) must be added to `raw/*.jsonl`
and rebuilt into `cases.jsonl` before the incident is closed. Cases are
never removed to make the gate pass; the suite only grows. Suite changes
are L2 artifacts under the Verifier policy (TECH_SPEC §9): human review
required, and the model must not silently grade itself against tests it
silently rewrote.

## Residual-risk note (honest, from TECH_SPEC §7)

NER is not perfect. Passing this gate does not certify the Gateway
leak-proof; it certifies it against these 240 known-adversarial patterns.
That is exactly why Helicone request-body logging stays **off** for Fable
routes (metadata only) regardless of suite results — the defense in depth
is not conditional on recall numbers.
