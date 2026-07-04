"""Invariant checks for the assembled Gateway red-team suite (TECH_SPEC §7)."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from build_suite import (
    CASES_PATH,
    REQUIRED_FIELDS,
    SEEDED_PII_KINDS,
    normalize_text,
)

MIN_CASES = 200  # TECH_SPEC §7: 200+ adversarial cases.


def _load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture(scope="module")
def cases() -> list[dict]:
    return _load(CASES_PATH)


def test_suite_meets_minimum_size(cases: list[dict]) -> None:
    assert len(cases) >= MIN_CASES


def test_required_fields_present(cases: list[dict]) -> None:
    for record in cases:
        for field in REQUIRED_FIELDS:
            assert field in record, f"{record.get('id')}: missing {field}"
        assert record["text"].strip(), f"{record['id']}: empty text"


def test_ids_unique(cases: list[dict]) -> None:
    ids = [r["id"] for r in cases]
    assert len(set(ids)) == len(ids)


def test_no_duplicate_texts(cases: list[dict]) -> None:
    keys = Counter(normalize_text(r["text"]) for r in cases)
    duplicates = [key for key, count in keys.items() if count > 1]
    assert not duplicates, f"{len(duplicates)} duplicate text(s)"


def test_every_seeded_value_is_verbatim_in_text(cases: list[dict]) -> None:
    """A seeded value not present in the input can never be scored — must not ship."""
    for record in cases:
        for seeded in record["seeded_pii"]:
            assert seeded["value"] in record["text"], (
                f"{record['id']}: seeded value {seeded['value']!r} not verbatim in text"
            )


def test_seeded_pii_shape(cases: list[dict]) -> None:
    for record in cases:
        assert record["seeded_pii"], f"{record['id']}: no seeded PII"
        for seeded in record["seeded_pii"]:
            assert set(seeded.keys()) == {"kind", "value"}, record["id"]
            assert seeded["kind"] in SEEDED_PII_KINDS, f"{record['id']}: kind {seeded['kind']!r}"
            assert seeded["value"].strip(), record["id"]


def test_known_entities_well_formed(cases: list[dict]) -> None:
    for record in cases:
        entities = record["known_entities"]
        assert isinstance(entities["name"], str) and entities["name"].strip(), record["id"]
        for field in ("school", "city"):
            value = entities[field]
            assert value is None or (isinstance(value, str) and value.strip()), (
                f"{record['id']}: known_entities.{field} = {value!r}"
            )
        for field in ("family_names", "emails", "phones", "handles"):
            values = entities[field]
            assert isinstance(values, list), f"{record['id']}: known_entities.{field}"
            assert all(isinstance(v, str) and v.strip() for v in values), record["id"]
        if "nicknames" in entities:
            assert isinstance(entities["nicknames"], list), record["id"]
            assert all(isinstance(v, str) and v.strip() for v in entities["nicknames"]), record["id"]


def test_output_is_sorted_and_deterministic(cases: list[dict]) -> None:
    ids = [r["id"] for r in cases]
    assert ids == sorted(ids)
    rebuilt = "\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in cases) + "\n"
    assert CASES_PATH.read_text(encoding="utf-8") == rebuilt


def test_readme_counts_match_suite(cases: list[dict]) -> None:
    """README headline counts must not drift from the assembled suite."""
    readme = (CASES_PATH.parent / "README.md").read_text(encoding="utf-8")
    seeded_total = sum(len(r["seeded_pii"]) for r in cases)
    assert f"**{len(cases)}** cases" in readme
    assert f"**{seeded_total}** seeded PII values" in readme
    for category, count in Counter(r["category"] for r in cases).items():
        assert f"| {category} | {count} |" in readme, f"README missing category row for {category}"
