"""Assemble the Gateway red-team suite from raw adversarial case files.

Merges ``evals/gateway_redteam/raw/*.jsonl``, validates every record, and
writes ``cases.jsonl`` (TECH_SPEC §7 — Build Phase 2 gate; recall target
≥ 0.995 on seeded PII).

Validation rules:

- Structural problems (missing fields, malformed ``known_entities``,
  duplicate ids, bad ``seeded_pii`` shape) are **hard errors** — the raw
  file must be fixed; the suite is not written.
- A ``seeded_pii.value`` that is not a verbatim substring of its case
  ``text`` causes the case to be **dropped and logged**: a seeded value
  that cannot be located in the input can never be checked against the
  scrubbed output, so the case cannot be scored.
- Duplicate texts (exact/near, after normalization) are dropped and
  logged; the first occurrence is kept.

Determinism: no randomness anywhere. Output is sorted by ``id`` with
sorted JSON keys, so re-running on the same raw files always produces a
byte-identical ``cases.jsonl``.

Usage:
    python evals/gateway_redteam/build_suite.py
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

SUITE_DIR = Path(__file__).resolve().parent
RAW_DIR = SUITE_DIR / "raw"
CASES_PATH = SUITE_DIR / "cases.jsonl"

REQUIRED_FIELDS = ("id", "category", "text", "known_entities", "seeded_pii", "notes")

# known_entities schema: field -> expected shape.
ENTITY_STR_FIELDS = ("name",)                       # non-empty string
ENTITY_OPT_STR_FIELDS = ("school", "city")          # non-empty string or null
ENTITY_LIST_FIELDS = ("family_names", "emails", "phones", "handles")  # list of non-empty strings
ENTITY_OPTIONAL_LIST_FIELDS = ("nicknames",)        # list of non-empty strings, may be absent

SEEDED_PII_KEYS = {"kind", "value"}
SEEDED_PII_KINDS = ("person", "org", "place", "address", "email", "phone", "handle", "id_number")


def normalize_text(text: str) -> str:
    """Duplicate-detection key: lowercase, collapse whitespace, strip punctuation."""
    lowered = re.sub(r"\s+", " ", text.strip().lower())
    return re.sub(r"[^a-z0-9 ]", "", lowered)


def load_raw_records() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    raw_files = sorted(RAW_DIR.glob("*.jsonl"))
    if not raw_files:
        raise SystemExit(f"no raw files found in {RAW_DIR}")
    for path in raw_files:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path.name}:{lineno}: invalid JSON: {exc}") from exc
            record["_source"] = f"{path.name}:{lineno}"
            records.append(record)
    return records


def _is_nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_known_entities(entities: Any, source: str, errors: list[str]) -> None:
    if not isinstance(entities, dict):
        errors.append(f"{source}: known_entities must be an object")
        return
    for field in ENTITY_STR_FIELDS:
        if not _is_nonempty_str(entities.get(field)):
            errors.append(f"{source}: known_entities.{field} must be a non-empty string")
    for field in ENTITY_OPT_STR_FIELDS:
        value = entities.get(field)
        if value is not None and not _is_nonempty_str(value):
            errors.append(f"{source}: known_entities.{field} must be a non-empty string or null")
    for field in ENTITY_LIST_FIELDS + ENTITY_OPTIONAL_LIST_FIELDS:
        if field in ENTITY_OPTIONAL_LIST_FIELDS and field not in entities:
            continue
        value = entities.get(field)
        if not isinstance(value, list) or not all(_is_nonempty_str(item) for item in value):
            errors.append(f"{source}: known_entities.{field} must be a list of non-empty strings")


def _validate_seeded_pii(seeded: Any, source: str, errors: list[str]) -> None:
    if not isinstance(seeded, list) or not seeded:
        errors.append(f"{source}: seeded_pii must be a non-empty list")
        return
    for index, item in enumerate(seeded):
        if not isinstance(item, dict) or set(item.keys()) != SEEDED_PII_KEYS:
            errors.append(f"{source}: seeded_pii[{index}] must have exactly keys {{kind, value}}")
            continue
        if item["kind"] not in SEEDED_PII_KINDS:
            errors.append(f"{source}: seeded_pii[{index}].kind {item['kind']!r} not in {SEEDED_PII_KINDS}")
        if not _is_nonempty_str(item["value"]):
            errors.append(f"{source}: seeded_pii[{index}].value must be a non-empty string")


def validate_structure(records: list[dict[str, Any]]) -> None:
    """Hard errors: fix the raw files. The suite is not written while any exist."""
    errors: list[str] = []
    seen_ids: set[str] = set()
    for record in records:
        source = record["_source"]
        missing = [f for f in REQUIRED_FIELDS if f not in record]
        if missing:
            errors.append(f"{source}: missing fields {missing}")
            continue
        if not _is_nonempty_str(record["id"]):
            errors.append(f"{source}: id must be a non-empty string")
        elif record["id"] in seen_ids:
            errors.append(f"{source}: duplicate id {record['id']!r}")
        else:
            seen_ids.add(record["id"])
        if not _is_nonempty_str(record["category"]):
            errors.append(f"{source}: category must be a non-empty string")
        if not _is_nonempty_str(record["text"]):
            errors.append(f"{source}: text must be non-empty")
            continue
        _validate_known_entities(record["known_entities"], source, errors)
        _validate_seeded_pii(record["seeded_pii"], source, errors)
    if errors:
        for error in errors:
            print(f"VALIDATION ERROR: {error}", file=sys.stderr)
        raise SystemExit(f"{len(errors)} validation error(s); suite not written")


def drop_unscoreable(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Drop any case whose seeded_pii.value is not a verbatim substring of its text.

    Recall is scored by checking each seeded value against the scrubbed
    output; a value that never appears in the input cannot be checked, so
    the case cannot be scored and must not count toward the gate.
    """
    kept: list[dict[str, Any]] = []
    dropped: list[str] = []
    for record in records:
        missing = [s["value"] for s in record["seeded_pii"] if s["value"] not in record["text"]]
        if missing:
            message = (
                f"UNSCOREABLE dropped: {record['id']} ({record['_source']}) — "
                f"seeded value(s) not verbatim in text: {missing!r}"
            )
            print(message, file=sys.stderr)
            dropped.append(message)
            continue
        kept.append(record)
    return kept, dropped


def drop_duplicate_texts(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Keep the first occurrence of each normalized text; drop later duplicates."""
    kept: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    dropped: list[str] = []
    for record in records:
        key = normalize_text(record["text"])
        if key in seen:
            message = f"DUPLICATE dropped: {record['id']} ({record['_source']}) duplicates {seen[key]}"
            print(message, file=sys.stderr)
            dropped.append(message)
            continue
        seen[key] = record["id"]
        kept.append(record)
    return kept, dropped


def write_cases(records: list[dict[str, Any]]) -> None:
    lines = []
    for record in sorted(records, key=lambda r: r["id"]):
        clean = {k: v for k, v in record.items() if not k.startswith("_")}
        lines.append(json.dumps(clean, ensure_ascii=False, sort_keys=True))
    CASES_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def summarize(records: list[dict[str, Any]]) -> None:
    per_category = Counter(r["category"] for r in records)
    per_kind = Counter(s["kind"] for r in records for s in r["seeded_pii"])
    seeded_total = sum(per_kind.values())
    print(f"cases: {len(records)}  seeded_values: {seeded_total}")
    for category, count in sorted(per_category.items()):
        print(f"  category={category}: {count}")
    for kind, count in sorted(per_kind.items()):
        print(f"  seeded kind={kind}: {count}")


def main() -> None:
    records = load_raw_records()
    total_raw = len(records)
    validate_structure(records)
    records, unscoreable = drop_unscoreable(records)
    records, duplicates = drop_duplicate_texts(records)
    print(
        f"loaded {total_raw} raw records; dropped {len(unscoreable)} unscoreable, "
        f"{len(duplicates)} duplicate(s)"
    )
    write_cases(records)
    summarize(records)
    print(f"wrote {CASES_PATH.name} ({len(records)})")


if __name__ == "__main__":
    main()
