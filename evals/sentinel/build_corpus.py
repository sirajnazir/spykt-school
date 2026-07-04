"""Assemble the Sentinel wellbeing corpus from raw synthetic case files.

Merges ``evals/sentinel/raw/*.jsonl``, validates every record, rejects
exact/near duplicates, and writes a deterministic ~70/30 stratified split:

- ``corpus_train.jsonl``   — prompt-development pool.
- ``corpus_heldout.jsonl`` — recall measurement ONLY. The Sentinel prompt
  must never quote or embed held-out cases (CLAUDE.md Phase 2, G2 gate).

Determinism: no randomness anywhere. Within each ``(mode, severity)``
stratum, records are sorted lexicographically by ``id`` and every record
whose rank satisfies ``rank % 10 < 3`` goes to the held-out set (30%);
the rest go to train. Re-running on the same raw files always produces
byte-identical outputs, as required for reproducible builds.

Usage:
    python evals/sentinel/build_corpus.py
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

SENTINEL_DIR = Path(__file__).resolve().parent
RAW_DIR = SENTINEL_DIR / "raw"
TRAIN_PATH = SENTINEL_DIR / "corpus_train.jsonl"
HELDOUT_PATH = SENTINEL_DIR / "corpus_heldout.jsonl"

REQUIRED_FIELDS = ("id", "text", "expected_class", "mode", "severity", "notes")
VALID_CLASSES = (1, 2, None)

# Every 10 records per stratum (rank-ordered by id), ranks 0-2 are held out.
HELDOUT_RANKS_PER_TEN = 3


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


def validate(records: list[dict[str, Any]]) -> None:
    errors: list[str] = []
    seen_ids: set[str] = set()
    for record in records:
        source = record["_source"]
        missing = [f for f in REQUIRED_FIELDS if f not in record]
        if missing:
            errors.append(f"{source}: missing fields {missing}")
            continue
        if not isinstance(record["id"], str) or not record["id"].strip():
            errors.append(f"{source}: id must be a non-empty string")
        elif record["id"] in seen_ids:
            errors.append(f"{source}: duplicate id {record['id']!r}")
        else:
            seen_ids.add(record["id"])
        if not isinstance(record["text"], str) or not record["text"].strip():
            errors.append(f"{source}: text must be non-empty")
        if record["expected_class"] not in VALID_CLASSES:
            errors.append(f"{source}: expected_class {record['expected_class']!r} not in {{1, 2, null}}")
        if not isinstance(record["mode"], str) or not record["mode"].strip():
            errors.append(f"{source}: mode must be a non-empty string")
    if errors:
        for error in errors:
            print(f"VALIDATION ERROR: {error}", file=sys.stderr)
        raise SystemExit(f"{len(errors)} validation error(s); corpus not written")


def drop_duplicates(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Keep the first occurrence of each normalized text; drop later duplicates."""
    kept: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    dropped = 0
    for record in records:
        key = normalize_text(record["text"])
        if key in seen:
            dropped += 1
            print(
                f"DUPLICATE dropped: {record['id']} ({record['_source']}) duplicates {seen[key]}",
                file=sys.stderr,
            )
            continue
        seen[key] = record["id"]
        kept.append(record)
    return kept, dropped


def stratified_split(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    strata: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in records:
        key = (record["mode"], record["severity"] or "none")
        strata.setdefault(key, []).append(record)
    train: list[dict[str, Any]] = []
    heldout: list[dict[str, Any]] = []
    for key in sorted(strata):
        members = sorted(strata[key], key=lambda r: r["id"])
        for rank, record in enumerate(members):
            if rank % 10 < HELDOUT_RANKS_PER_TEN:
                heldout.append(record)
            else:
                train.append(record)
    train.sort(key=lambda r: r["id"])
    heldout.sort(key=lambda r: r["id"])
    return train, heldout


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    lines = []
    for record in records:
        clean = {k: v for k, v in record.items() if not k.startswith("_")}
        lines.append(json.dumps(clean, ensure_ascii=False, sort_keys=True))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def summarize(label: str, records: list[dict[str, Any]]) -> None:
    counts = Counter((r["mode"], r["severity"] or "none", r["expected_class"]) for r in records)
    print(f"{label}: {len(records)} cases")
    for (mode, severity, cls), count in sorted(counts.items(), key=lambda item: (item[0][0], item[0][1])):
        print(f"  mode={mode} severity={severity} class={cls}: {count}")


def main() -> None:
    records = load_raw_records()
    validate(records)
    records, dropped = drop_duplicates(records)
    print(f"loaded {len(records) + dropped} records; dropped {dropped} duplicate(s)")
    train, heldout = stratified_split(records)
    write_jsonl(TRAIN_PATH, train)
    write_jsonl(HELDOUT_PATH, heldout)
    summarize("train", train)
    summarize("heldout", heldout)
    print(f"wrote {TRAIN_PATH.name} ({len(train)}) and {HELDOUT_PATH.name} ({len(heldout)})")


if __name__ == "__main__":
    main()
