"""Phase 2 gate: Gateway red-team recall + zero PII in Fable request bodies.

CLAUDE.md Phase 2 gate (→G2) / 01-TECH_SPEC §7: red-team recall ≥ 0.995 over
the pinned 200+ case suite, and zero seeded PII found in captured Fable-route
request bodies during the run. Runs the harness fully in-process — no network,
no API key, spaCy sm model only — so it executes in CI on every change.

The threshold and the suite are pinned (prime directive 2): if the Gateway
misses a case, the Gateway gets fixed — this file and cases.jsonl do not.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "gateway_redteam_harness", Path(__file__).resolve().parents[1] / "harness.py"
)
harness = importlib.util.module_from_spec(_spec)
sys.modules["gateway_redteam_harness"] = harness
_spec.loader.exec_module(harness)


@pytest.fixture(scope="module")
def report() -> dict:
    result = harness.run()
    # Persist the artifact reviewed at the G2 human gate.
    harness.write_report(result)
    return result


def test_suite_actually_ran(report: dict):
    assert report["cases"] >= 200, "TECH_SPEC §7 requires a 200+ case suite"
    assert report["seeded_values"] > report["cases"]
    assert report["fable_requests_captured"] >= report["cases"]


def test_no_cases_dropped_silently(report: dict):
    # Drops are allowed ONLY for objectively malformed cases (seeded value not
    # verbatim in text) and each must carry a logged reason.
    for drop in report["dropped_cases"]:
        assert drop["id"] and "not verbatim" in drop["reason"], drop


def test_recall_meets_phase2_gate(report: dict):
    assert report["recall"] >= 0.995, (
        f"red-team recall {report['recall']:.4f} below the 0.995 gate; "
        f"leaks: {report['failures']}"
    )


def test_zero_pii_in_fable_request_bodies(report: dict):
    assert report["fable_body_leaks"] == 0, report["fable_body_leak_detail"]
