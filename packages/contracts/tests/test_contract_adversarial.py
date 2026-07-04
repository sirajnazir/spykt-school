"""G1 gate: adversarial documents must get the SAME verdict from BOTH layers —
the pydantic model and the JSON Schema. Every assertion here first checks the
two layers agree, then checks the shared verdict; a document that one layer
accepts and the other rejects is exactly the kind of contract drift these
tests exist to catch, and fails immediately regardless of expected verdict."""

import copy
from typing import Any

import jsonschema
import pydantic
import pytest

from spykt_contracts import validate
from spykt_contracts.models import (
    GenomeResult,
    PlannerResult,
    SentinelResult,
    SpecialistOutput,
    VerifierResult,
    ZuzuFrame,
)


def valid_planner_doc(n_tasks: int) -> dict[str, Any]:
    task = {
        "title": "Draft robotics paper outline",
        "spike_alignment": "core robotics spike",
        "effort_hrs": 2.5,
        "evidence_required": "outline doc",
        "rationale": "Moves the publication milestone forward",
    }
    return {
        "week_plan": {"tasks": [copy.deepcopy(task) for _ in range(n_tasks)]},
        "roadmap_diff": [],
        "autonomy_level_required": "L1",
        "agenda_seed_for_coach_session": "Discuss publication venue",
    }


def valid_genome_doc() -> dict[str, Any]:
    return {
        "rings": {
            "intellectual": {
                "depth": {
                    "score": 7.2,
                    "confidence": 0.8,
                    "evidence_refs": ["ev-123"],
                    "delta_vs_last": 0.4,
                    "chetty_modifiers_applied": ["first_gen"],
                }
            }
        },
        "sigmoid_admit_probs": {"T1": 0.12, "T2": 0.4},
        "flags": [],
    }


def valid_sentinel_doc(escalation_class: int) -> dict[str, Any]:
    return {
        "class": escalation_class,
        "severity": "high",
        "evidence_ref": "transcript-456#L12",
        "recommended_action": "Immediate coach alert (push + SMS)",
    }


def valid_output_doc(confidence: float) -> dict[str, Any]:
    return {
        "job_id": "job-1",
        "status": "ok",
        "confidence": confidence,
        "result": {},
        "escalate": None,
        "audit": {"model": "claude-fable-5", "prompt_version": "v1", "tokens_in": 10, "tokens_out": 5},
    }


def layer_verdicts(
    schema_name: str, model_cls: type[pydantic.BaseModel], doc: dict[str, Any]
) -> tuple[bool, bool]:
    """(schema_ok, pydantic_ok) for the same wire document."""
    try:
        validate(schema_name, doc)
        schema_ok = True
    except jsonschema.ValidationError:
        schema_ok = False
    try:
        model_cls.model_validate(doc)
        pydantic_ok = True
    except pydantic.ValidationError:
        pydantic_ok = False
    return schema_ok, pydantic_ok


def assert_layer_verdict(
    expected_ok: bool, schema_name: str, model_cls: type[pydantic.BaseModel], doc: dict[str, Any]
):
    schema_ok, pydantic_ok = layer_verdicts(schema_name, model_cls, doc)
    assert schema_ok == pydantic_ok, (
        f"CONTRACT DRIFT on {schema_name}: schema_ok={schema_ok}, pydantic_ok={pydantic_ok} "
        f"for doc {doc!r} — the two validation layers disagree on the same wire document"
    )
    assert schema_ok is expected_ok


def assert_rejected_by_both(schema_name: str, model_cls: type[pydantic.BaseModel], doc: dict[str, Any]):
    assert_layer_verdict(False, schema_name, model_cls, doc)


def assert_accepted_by_both(schema_name: str, model_cls: type[pydantic.BaseModel], doc: dict[str, Any]):
    assert_layer_verdict(True, schema_name, model_cls, doc)


def test_valid_baseline_docs_pass_both_layers():
    """Positive control: the adversarial docs below are minimal mutations of these valid ones."""
    assert_accepted_by_both("a3_planner", PlannerResult, valid_planner_doc(5))
    assert_accepted_by_both("a2_genome", GenomeResult, valid_genome_doc())
    assert_accepted_by_both("a8_sentinel", SentinelResult, valid_sentinel_doc(1))
    assert_accepted_by_both("specialist_output", SpecialistOutput, valid_output_doc(0.9))


def test_six_tasks_in_a_plan_rejected():
    """Max 5 tasks/week — Jenny cadence: depth over breadth (01-TECH_SPEC §5 A3)."""
    assert_rejected_by_both("a3_planner", PlannerResult, valid_planner_doc(6))


def test_task_without_rationale_rejected():
    doc = valid_planner_doc(3)
    del doc["week_plan"]["tasks"][0]["rationale"]
    assert_rejected_by_both("a3_planner", PlannerResult, doc)


def test_genome_score_with_empty_evidence_refs_rejected():
    """A score without evidence is invalid — the Verifier rejects it (01-TECH_SPEC §5 A2)."""
    doc = valid_genome_doc()
    doc["rings"]["intellectual"]["depth"]["evidence_refs"] = []
    assert_rejected_by_both("a2_genome", GenomeResult, doc)


def test_genome_score_missing_evidence_refs_rejected():
    doc = valid_genome_doc()
    del doc["rings"]["intellectual"]["depth"]["evidence_refs"]
    assert_rejected_by_both("a2_genome", GenomeResult, doc)


@pytest.mark.parametrize("escalation_class", [0, 6])
def test_sentinel_class_out_of_range_rejected(escalation_class: int):
    """Escalation classes are 1–5, per PRD §6.2."""
    assert_rejected_by_both("a8_sentinel", SentinelResult, valid_sentinel_doc(escalation_class))


@pytest.mark.parametrize("confidence", [1.5, -0.1])
def test_output_confidence_out_of_range_rejected(confidence: float):
    assert_rejected_by_both("specialist_output", SpecialistOutput, valid_output_doc(confidence))


def test_output_escalate_class_out_of_range_rejected():
    doc = valid_output_doc(0.5)
    doc["escalate"] = {"class": 6, "reason": "too spicy"}
    assert_rejected_by_both("specialist_output", SpecialistOutput, doc)


def test_unknown_field_rejected():
    """additionalProperties: false in the schema must mirror extra='forbid' in the models."""
    doc = valid_output_doc(0.5)
    doc["smuggled"] = "field"
    assert_rejected_by_both("specialist_output", SpecialistOutput, doc)


# ---------------------------------------------------------------------------
# Asymmetric drift cases: wire documents that one layer historically accepted
# while the other rejected. assert_layer_verdict fails on ANY disagreement, so
# a regression in either direction is caught, not just dual rejection.
# ---------------------------------------------------------------------------


def valid_zuzu_doc() -> dict[str, Any]:
    return {
        "eq_signals": ["frustration easing"],
        "commitments_made": ["submit draft by Friday"],
        "wellbeing_flag": None,
        "followups": ["ask about the demo"],
    }


def valid_verifier_doc() -> dict[str, Any]:
    return {
        "suite": "planner-quality",
        "cases_run": 12,
        "pass_rate": 1.0,
        "failures": [],
    }


def test_output_missing_escalate_rejected():
    """'escalate' is required: the specialist must affirmatively state null.

    Omitting the key must be malformed in BOTH layers — never silently treated
    as 'no escalation', because escalation routing (PRD §6.2) keys off it.
    """
    doc = valid_output_doc(0.9)
    del doc["escalate"]
    assert_rejected_by_both("specialist_output", SpecialistOutput, doc)


def test_zuzu_empty_frame_rejected():
    """eq_signals/commitments_made/followups are required; {} is malformed in both layers."""
    assert_rejected_by_both("a1_zuzu_frame", ZuzuFrame, {})


@pytest.mark.parametrize("field", ["eq_signals", "commitments_made", "followups"])
def test_zuzu_missing_required_list_rejected(field: str):
    doc = valid_zuzu_doc()
    del doc[field]
    assert_rejected_by_both("a1_zuzu_frame", ZuzuFrame, doc)


def test_zuzu_omitted_wellbeing_flag_accepted():
    """wellbeing_flag is the one genuinely optional ZuzuFrame field in both layers."""
    doc = valid_zuzu_doc()
    del doc["wellbeing_flag"]
    assert_accepted_by_both("a1_zuzu_frame", ZuzuFrame, doc)


def test_genome_missing_flags_rejected():
    doc = valid_genome_doc()
    del doc["flags"]
    assert_rejected_by_both("a2_genome", GenomeResult, doc)


def test_verifier_missing_failures_rejected():
    doc = valid_verifier_doc()
    del doc["failures"]
    assert_rejected_by_both("a9_verifier", VerifierResult, doc)


def test_verifier_baseline_accepted():
    assert_accepted_by_both("a9_verifier", VerifierResult, valid_verifier_doc())


def test_sentinel_python_field_name_rejected():
    """The wire key is 'class'; the Python field name 'escalation_class' is not a wire key."""
    doc = valid_sentinel_doc(1)
    doc["escalation_class"] = doc.pop("class")
    assert_rejected_by_both("a8_sentinel", SentinelResult, doc)


def test_output_escalate_python_field_name_rejected():
    doc = valid_output_doc(0.5)
    doc["escalate"] = {"escalation_class": 4, "reason": "confidence below threshold"}
    assert_rejected_by_both("specialist_output", SpecialistOutput, doc)


def test_output_with_escalation_accepted():
    doc = valid_output_doc(0.3)
    doc["escalate"] = {"class": 4, "reason": "confidence below threshold"}
    assert_accepted_by_both("specialist_output", SpecialistOutput, doc)


def test_zero_task_plan_accepted():
    """Zero-task week plans are contract-valid (protected weeks, 01-TECH_SPEC §6)."""
    assert_accepted_by_both("a3_planner", PlannerResult, valid_planner_doc(0))


def test_planner_omitted_roadmap_diff_accepted():
    """roadmap_diff is optional (defaulted) in both layers."""
    doc = valid_planner_doc(2)
    del doc["roadmap_diff"]
    assert_accepted_by_both("a3_planner", PlannerResult, doc)
