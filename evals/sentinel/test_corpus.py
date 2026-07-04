"""Invariant checks for the assembled Sentinel wellbeing corpus."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from build_corpus import (
    HELDOUT_PATH,
    RAW_DIR,
    REQUIRED_FIELDS,
    TRAIN_PATH,
    VALID_CLASSES,
    normalize_text,
)


def _load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture(scope="module")
def train() -> list[dict]:
    return _load(TRAIN_PATH)


@pytest.fixture(scope="module")
def heldout() -> list[dict]:
    return _load(HELDOUT_PATH)


def test_outputs_exist(train: list[dict], heldout: list[dict]) -> None:
    assert train and heldout


def test_required_fields_and_classes(train: list[dict], heldout: list[dict]) -> None:
    for record in train + heldout:
        for field in REQUIRED_FIELDS:
            assert field in record, f"{record.get('id')}: missing {field}"
        assert record["text"].strip(), f"{record['id']}: empty text"
        assert record["expected_class"] in VALID_CLASSES, record["id"]


def test_ids_unique_and_splits_disjoint(train: list[dict], heldout: list[dict]) -> None:
    train_ids = [r["id"] for r in train]
    heldout_ids = [r["id"] for r in heldout]
    assert len(set(train_ids)) == len(train_ids)
    assert len(set(heldout_ids)) == len(heldout_ids)
    assert not set(train_ids) & set(heldout_ids)


def test_no_duplicate_texts(train: list[dict], heldout: list[dict]) -> None:
    keys = [normalize_text(r["text"]) for r in train + heldout]
    assert len(set(keys)) == len(keys)


def test_split_covers_all_nonduplicate_raw_records(train: list[dict], heldout: list[dict]) -> None:
    raw: list[dict] = []
    for path in sorted(RAW_DIR.glob("*.jsonl")):
        raw.extend(_load(path))
    seen: set[str] = set()
    expected_ids: set[str] = set()
    for record in raw:
        key = normalize_text(record["text"])
        if key in seen:
            continue
        seen.add(key)
        expected_ids.add(record["id"])
    assert {r["id"] for r in train} | {r["id"] for r in heldout} == expected_ids


def test_stratified_ratio_roughly_70_30(train: list[dict], heldout: list[dict]) -> None:
    def strata(records: list[dict]) -> Counter:
        return Counter((r["mode"], r["severity"] or "none") for r in records)

    train_strata, heldout_strata = strata(train), strata(heldout)
    for key in set(train_strata) | set(heldout_strata):
        total = train_strata[key] + heldout_strata[key]
        ratio = heldout_strata[key] / total
        assert 0.2 <= ratio <= 0.4, f"stratum {key}: heldout ratio {ratio:.2f} outside [0.2, 0.4]"


def test_split_is_deterministic(train: list[dict], heldout: list[dict]) -> None:
    """Re-deriving the split from raw files reproduces the committed files exactly."""
    import build_corpus

    records = build_corpus.load_raw_records()
    build_corpus.validate(records)
    records, _ = build_corpus.drop_duplicates(records)
    expected_train, expected_heldout = build_corpus.stratified_split(records)
    strip = lambda rs: [{k: v for k, v in r.items() if not k.startswith("_")} for r in rs]  # noqa: E731
    assert strip(expected_train) == train
    assert strip(expected_heldout) == heldout
