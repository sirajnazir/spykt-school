"""Gateway red-team harness (01-TECH_SPEC §7; CLAUDE.md Phase 2 gate G2).

Runs every case in cases.jsonl through the real scrub path
(``PseudonymizationGateway(InMemoryPseudonymStore()).scrub``) and scores
seeded PII per the suite's pinned rule: a seeded value counts DETECTED iff it
does not appear (case-insensitively) as a substring of the scrubbed text.

Then the FABLE-BODY CHECK: every scrubbed text is sent through the real
``SpyktAnthropicClient`` — the only sanctioned Anthropic path — with a
recorder stub standing in for the SDK (no network, no API key) and the
attestation produced by the scrub. Every captured request body is searched
for every seeded value of its case. The G2 gate requires recall ≥ 0.995 AND
zero seeded values in captured Fable-route request bodies; the recall metric
never supersedes the body check.

cases.jsonl is a pinned L2 eval artifact (prime directive 2): this harness
never edits or drops a case because the Gateway misses it. The ONLY drop
allowed is an objectively malformed case — a seeded value that is not a
verbatim substring of its own text can never be scored — and every drop is
logged in the report under ``dropped_cases``.

Run directly (``python harness.py``) to write report.json, or import ``run``
in-process (the gate test in tests/test_redteam_gate.py does this in CI).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from spykt_anthropic_client import SpyktAnthropicClient
from spykt_anthropic_client.config import ModelConfig
from spykt_gateway import InMemoryPseudonymStore, PseudonymizationGateway

HERE = Path(__file__).resolve().parent
CASES_PATH = HERE / "cases.jsonl"
REPORT_PATH = HERE / "report.json"

# CLAUDE.md Phase 2 gate / 01-TECH_SPEC §7: ≥99.5% recall on seeded PII.
RECALL_THRESHOLD = 0.995

FABLE_JOB = "gateway_redteam_probe"

# Minimal config routing the probe job to the Fable model string, so the
# retention gate in the client is exercised exactly as in production
# (models.yaml stays the single source of truth for real routing; this probe
# must hit a claude-fable-* model string regardless of routing edits there).
_FABLE_CONFIG = ModelConfig(
    models={"fable": "claude-fable-5"},
    routing={FABLE_JOB: "fable"},
    fallbacks={},
    embedding_dimensions=1536,
    pricing={},
    budgets={},
)


class _RecorderSDK:
    """Stands in for anthropic.Anthropic — records every request, no network."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[],
            usage=SimpleNamespace(input_tokens=0, output_tokens=0),
        )


class _NullAudit:
    def write(self, **kwargs: Any) -> None:  # AuditWriter protocol
        pass


class _ZeroSpend:
    def month_spend(self, student_id: str, model_alias: str) -> float:  # SpendStore protocol
        return 0.0


def _strings_in(node: Any) -> list[str]:
    """Every string anywhere in a captured request body (keys and values)."""
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        out: list[str] = []
        for key, value in node.items():
            out.extend(_strings_in(key))
            out.extend(_strings_in(value))
        return out
    if isinstance(node, (list, tuple)):
        out = []
        for item in node:
            out.extend(_strings_in(item))
        return out
    return []


def load_cases(cases_path: Path = CASES_PATH) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in cases_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def run(cases_path: Path = CASES_PATH) -> dict[str, Any]:
    """Run the full suite; returns the report dict (see REPORT_PATH schema)."""
    cases = load_cases(cases_path)

    dropped: list[dict[str, str]] = []
    scored: list[dict[str, Any]] = []
    for case in cases:
        bad = [s["value"] for s in case["seeded_pii"] if s["value"] not in case["text"]]
        if bad:
            # Objectively malformed: an unlocatable seeded value can never be
            # scored against the output. Logged, never silently ignored.
            dropped.append(
                {
                    "id": case["id"],
                    "reason": "seeded value(s) not verbatim in text: " + ", ".join(repr(v) for v in bad),
                }
            )
            continue
        scored.append(case)

    gateway = PseudonymizationGateway(InMemoryPseudonymStore())
    recorder = _RecorderSDK()
    client = SpyktAnthropicClient(
        config=_FABLE_CONFIG,
        audit_writer=_NullAudit(),
        spend_store=_ZeroSpend(),
        sdk_client=recorder,
    )

    total = 0
    detected = 0
    per_kind: dict[str, dict[str, int]] = {}
    per_category: dict[str, dict[str, int]] = {}
    failures: list[dict[str, str]] = []
    fable_body_leak_detail: list[dict[str, str]] = []

    for case in scored:
        result = gateway.scrub(case["id"], case["text"], case["known_entities"])
        scrubbed_lower = result.text.lower()

        for seeded in case["seeded_pii"]:
            total += 1
            kind_bucket = per_kind.setdefault(seeded["kind"], {"detected": 0, "total": 0})
            cat_bucket = per_category.setdefault(case["category"], {"detected": 0, "total": 0})
            kind_bucket["total"] += 1
            cat_bucket["total"] += 1
            if seeded["value"].lower() in scrubbed_lower:
                failures.append({"id": case["id"], "kind": seeded["kind"], "value": seeded["value"]})
            else:
                detected += 1
                kind_bucket["detected"] += 1
                cat_bucket["detected"] += 1

        # FABLE-BODY CHECK: scrubbed text through the sanctioned client with
        # the attestation from this scrub; then search the captured body.
        before = len(recorder.requests)
        client.call(
            FABLE_JOB,
            [{"role": "user", "content": result.text}],
            student_id=case["id"],
            prompt_version="redteam-harness-v1",
            autonomy_level="L0",
            max_tokens=16,
            attestation=result.attestation,
        )
        for request in recorder.requests[before:]:
            body = "\n".join(_strings_in(request)).lower()
            for seeded in case["seeded_pii"]:
                if seeded["value"].lower() in body:
                    fable_body_leak_detail.append(
                        {"id": case["id"], "kind": seeded["kind"], "value": seeded["value"]}
                    )

    def _with_recall(buckets: dict[str, dict[str, int]]) -> dict[str, dict[str, float]]:
        return {
            key: {
                "detected": b["detected"],
                "total": b["total"],
                "recall": b["detected"] / b["total"] if b["total"] else 0.0,
            }
            for key, b in sorted(buckets.items())
        }

    return {
        "cases": len(scored),
        "seeded_values": total,
        "recall": detected / total if total else 0.0,
        "recall_threshold": RECALL_THRESHOLD,
        "per_kind": _with_recall(per_kind),
        "per_category": _with_recall(per_category),
        "failures": failures,
        "fable_requests_captured": len(recorder.requests),
        "fable_body_leaks": len(fable_body_leak_detail),
        "fable_body_leak_detail": fable_body_leak_detail,
        "dropped_cases": dropped,
    }


def write_report(report: dict[str, Any], report_path: Path = REPORT_PATH) -> None:
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    report = run()
    write_report(report)
    print(
        f"gateway red-team: recall={report['recall']:.4f} "
        f"({report['seeded_values']} seeded values, {report['cases']} cases, "
        f"{len(report['dropped_cases'])} dropped), "
        f"fable_body_leaks={report['fable_body_leaks']} "
        f"over {report['fable_requests_captured']} captured requests"
    )
    for failure in report["failures"]:
        print(f"  LEAK {failure['id']} [{failure['kind']}]: {failure['value']!r}")
    for drop in report["dropped_cases"]:
        print(f"  DROPPED {drop['id']}: {drop['reason']}")
    ok = report["recall"] >= RECALL_THRESHOLD and report["fable_body_leaks"] == 0
    print(f"gate (recall >= {RECALL_THRESHOLD} and zero fable-body leaks): {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
