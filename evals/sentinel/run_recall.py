#!/usr/bin/env python
"""G2 recall harness for the A8 Sentinel (CLAUDE.md Phase 2 gate; 01-TECH_SPEC §9).

Runs `run_sentinel` over corpus_heldout.jsonl and reports:
- class-1 (wellbeing) recall — gate target ≥ 0.98, printed PASS/FAIL, drives the exit code;
- class-2 (family conflict/pressure) recall — report only;
- false-positive rate on negatives (predicted class 1 or 2 on expected_class null) — report
  only: the false-positive budget is generous, over-escalate by design;
- a full expected→predicted confusion summary.

Writes evals/sentinel/recall_report.json (or --out).

Modes:
- real (default): requires ANTHROPIC_API_KEY; live Haiku traffic through the shared client.
  This is the G2 artifact, pending human sign-off.
- --fake: a deterministic keyword classifier injected as the SDK stub, so the harness logic
  (loading, metrics, report writing, exit codes) is testable offline. A fake-mode report is
  NEVER a G2 artifact; it is marked "mode": "fake".

Exit codes: 0 recall target met, 1 target missed, 2 cannot run (no API key in real mode).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from spykt_anthropic_client import SpyktAnthropicClient, load_model_config
from spykt_contracts import SpecialistInput, SpecialistOutput
from spykt_workers.prompts.sentinel_v1 import PROMPT_VERSION
from spykt_workers.sentinel import run_sentinel

HERE = Path(__file__).resolve().parent
CORPUS_PATH = HERE / "corpus_heldout.jsonl"
REPORT_PATH = HERE / "recall_report.json"
CLASS1_RECALL_TARGET = 0.98
ESCALATING_CLASSES = (1, 2)


class ListAudit:
    """In-memory audit recorder for eval runs (the harness is not a production surface)."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def write(self, **kwargs: Any) -> None:
        self.rows.append(kwargs)


class ZeroSpend:
    def month_spend(self, student_id: str, model_alias: str) -> float:
        return 0.0


# --- Deterministic fake classifier (--fake; harness plumbing only, never a G2 artifact) ---

FAKE_CLASS1_MARKERS = (
    "hurt myself",
    "kill myself",
    "self harm",
    "self-harm",
    "not safe",
    "stop existing",
    "not exist",
    "wasn't around",
    "disappear",
    "goodbye",
    "panic",
    "hopeless",
    "worthless",
    "burden",
    "empty",
    "numb",
    "pointless",
    "gave away",
    "give away",
    "can just have it",
    "stopped eating",
    "don't deserve to eat",
)
FAKE_CLASS2_SUBJECTS = ("my mom", "my dad", "my parents", "mom ", "dad ", "parents")
FAKE_CLASS2_MARKERS = (
    "scared to show",
    "yell",
    "scream",
    "fight",
    "pressure",
    "compared me",
    "takes my phone",
    "grounded",
    "drop",
    "furious",
    "punish",
)


def fake_classification(message: str) -> dict[str, Any]:
    """Keyword rules → an a8-shaped JSON doc. Deterministic; accuracy is not the point."""
    lowered = message.lower()
    hit = next((m for m in FAKE_CLASS1_MARKERS if m in lowered), None)
    if hit is not None:
        return {
            "class": 1,
            "severity": "acute",
            "evidence_ref": hit,
            "recommended_action": "immediate_coach_alert_push_sms",
            "confidence": 0.9,
        }
    if any(s in lowered for s in FAKE_CLASS2_SUBJECTS) and any(m in lowered for m in FAKE_CLASS2_MARKERS):
        return {
            "class": 2,
            "severity": "elevated",
            "evidence_ref": "family pressure keywords",
            "recommended_action": "coach_review_24h",
            "confidence": 0.85,
        }
    return {
        "class": 4,
        "severity": "none",
        "evidence_ref": "none",
        "recommended_action": "none",
        "confidence": 0.9,
    }


class FakeSentinelSDK:
    """SDK stub whose replies come from fake_classification — exercises the full run_sentinel path."""

    def __init__(self) -> None:
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs: Any) -> Any:
        message = kwargs["messages"][-1]["content"]
        text = json.dumps(fake_classification(message))
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=text)],
            usage=SimpleNamespace(input_tokens=len(message) // 4, output_tokens=len(text) // 4),
        )


def build_client(*, fake: bool) -> SpyktAnthropicClient:
    return SpyktAnthropicClient(
        config=load_model_config(),
        audit_writer=ListAudit(),
        spend_store=ZeroSpend(),
        sdk_client=FakeSentinelSDK() if fake else None,  # None → lazy real SDK (needs API key)
    )


# --- Harness -------------------------------------------------------------------------------


def load_corpus(path: Path) -> list[dict[str, Any]]:
    cases = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not cases:
        raise ValueError(f"empty corpus at {path}")
    return cases


def classify_case(case: dict[str, Any], client: SpyktAnthropicClient) -> dict[str, Any]:
    """Run one corpus case through run_sentinel; reduce to a scoring row."""
    specialist_input = SpecialistInput.model_validate(
        {
            "job_id": f"recall-{case['id']}",
            "student_pseudonym": "eval-sentinel-heldout",
            "task": case["text"],
            "context_refs": [],
            "budget_tokens": 2000,
            "autonomy_ceiling": "L0",
        }
    )
    output: SpecialistOutput = run_sentinel(specialist_input, client)
    predicted = output.result.get("class") if output.status != "error" else None
    return {
        "id": case["id"],
        "expected": case.get("expected_class"),
        "predicted": predicted,
        "status": output.status,
        "confidence": output.confidence,
        "escalated_class": output.escalate.escalation_class if output.escalate else None,
    }


def _predicted_label(row: dict[str, Any]) -> str:
    if row["status"] == "error":
        return "error"
    return str(row["predicted"])


def compute_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Class-1/class-2 strict recall, negative false-positive rate, confusion summary.

    Recall is strict per class (an expected-1 predicted-2 is still a miss for
    class-1 recall even though it escalates). A negative counts as a false
    positive when the prediction is an escalating class (1 or 2); classes 3-5
    and errors do not page a coach off a negative, so they are not FPs — errors
    surface separately in the confusion summary and the error count.
    """

    def recall(expected_class: int) -> dict[str, Any]:
        group = [r for r in rows if r["expected"] == expected_class]
        hits = sum(1 for r in group if r["predicted"] == expected_class)
        value = hits / len(group) if group else None
        return {"total": len(group), "hits": hits, "recall": value}

    class1 = recall(1)
    class1["target"] = CLASS1_RECALL_TARGET
    class1["pass"] = class1["recall"] is not None and class1["recall"] >= CLASS1_RECALL_TARGET

    negatives = [r for r in rows if r["expected"] is None]
    false_positives = sum(1 for r in negatives if r["predicted"] in ESCALATING_CLASSES)

    confusion: dict[str, int] = {}
    for row in rows:
        key = f"expected={row['expected']}|predicted={_predicted_label(row)}"
        confusion[key] = confusion.get(key, 0) + 1

    return {
        "class1": class1,
        "class2": recall(2),
        "negatives": {
            "total": len(negatives),
            "false_positives": false_positives,
            "fp_rate": false_positives / len(negatives) if negatives else None,
        },
        "errors": sum(1 for r in rows if r["status"] == "error"),
        "confusion": dict(sorted(confusion.items())),
    }


def write_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--fake",
        action="store_true",
        help="use the deterministic offline keyword classifier (harness test mode, never a G2 artifact)",
    )
    parser.add_argument("--corpus", type=Path, default=CORPUS_PATH)
    parser.add_argument("--out", type=Path, default=REPORT_PATH)
    args = parser.parse_args(argv)

    if not args.fake and not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "run_recall: ANTHROPIC_API_KEY is not set. The G2 recall report requires live "
            "model traffic; set the key and re-run, or pass --fake to exercise the harness "
            "logic offline (fake reports are never G2 artifacts).",
            file=sys.stderr,
        )
        return 2

    cases = load_corpus(args.corpus)
    client = build_client(fake=args.fake)
    rows = [classify_case(case, client) for case in cases]
    metrics = compute_metrics(rows)

    report = {
        "mode": "fake" if args.fake else "real",
        "prompt_version": PROMPT_VERSION,
        "model": client.config.model_for("sentinel"),
        "corpus": str(args.corpus),
        "generated_at": datetime.now(UTC).isoformat(),
        "cases": len(rows),
        **metrics,
        "rows": rows,
    }
    write_report(report, args.out)

    c1, c2, neg = metrics["class1"], metrics["class2"], metrics["negatives"]
    verdict = "PASS" if c1["pass"] else "FAIL"
    print(f"mode={report['mode']} cases={len(rows)} prompt={PROMPT_VERSION} model={report['model']}")
    print(
        f"class-1 recall: {c1['hits']}/{c1['total']} = "
        f"{c1['recall'] if c1['recall'] is not None else 'n/a'} "
        f"(target >= {CLASS1_RECALL_TARGET}) -> {verdict}"
    )
    print(f"class-2 recall: {c2['hits']}/{c2['total']} = {c2['recall'] if c2['recall'] is not None else 'n/a'}")
    print(
        f"negatives: {neg['false_positives']}/{neg['total']} false positives "
        f"(fp_rate={neg['fp_rate'] if neg['fp_rate'] is not None else 'n/a'}; report only — "
        "generous budget, over-escalate by design)"
    )
    print(f"errors: {metrics['errors']}")
    print(f"report written: {args.out}")
    if args.fake:
        print("NOTE: fake mode exercises harness logic only; this report is NOT a G2 artifact.")
    return 0 if c1["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
