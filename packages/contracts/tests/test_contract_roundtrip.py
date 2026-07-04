"""G1 gate: round-trip property tests (CLAUDE.md Phase 1 gate).

For every contract model: generate an instance, dump to the wire dict, assert it
is jsonschema-valid, survive a real JSON encode/decode, re-parse, and assert
equality. This keeps the pydantic mirrors and the JSON Schema files in lockstep.
"""

import json

from hypothesis import given, settings
from hypothesis import strategies as st

from spykt_contracts import validate
from spykt_contracts.models import (
    AuditStamp,
    ContractModel,
    EscalationDirective,
    GenomeResult,
    PlannerResult,
    PlanTask,
    SentinelResult,
    SpecialistInput,
    SpecialistOutput,
    SubfactorScore,
    VerifierFailure,
    VerifierResult,
    WeekPlan,
    WellbeingFlagDetail,
    ZuzuFrame,
)

roundtrip_settings = settings(max_examples=60, deadline=None)

text = st.text(max_size=20)
nonempty_text = st.text(min_size=1, max_size=20)
unit_float = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)
finite_float = st.floats(allow_nan=False, allow_infinity=False)
autonomy_level = st.sampled_from(["L0", "L1", "L2", "L3"])
str_list = st.lists(nonempty_text, max_size=3)

json_scalar = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(10**9), max_value=10**9),
    finite_float,
    text,
)

specialist_inputs = st.builds(
    SpecialistInput,
    job_id=nonempty_text,
    student_pseudonym=nonempty_text,
    task=nonempty_text,
    context_refs=str_list,
    budget_tokens=st.integers(min_value=1, max_value=10**7),
    autonomy_ceiling=autonomy_level,
)

# Alias-keyed models parse wire keys only ('class', a Python keyword), so these
# strategies build the wire dict and go through model_validate like real inputs.
escalation_directives = st.fixed_dictionaries(
    {
        "class": st.integers(min_value=1, max_value=5),
        "reason": nonempty_text,
    }
).map(EscalationDirective.model_validate)

audit_stamps = st.builds(
    AuditStamp,
    model=nonempty_text,
    prompt_version=nonempty_text,
    tokens_in=st.integers(min_value=0, max_value=10**7),
    tokens_out=st.integers(min_value=0, max_value=10**7),
)

specialist_outputs = st.builds(
    SpecialistOutput,
    job_id=nonempty_text,
    status=st.sampled_from(["ok", "low_confidence", "refused", "error"]),
    confidence=unit_float,
    result=st.dictionaries(text, json_scalar, max_size=4),
    escalate=st.none() | escalation_directives,
    audit=audit_stamps,
)

subfactor_scores = st.builds(
    SubfactorScore,
    score=finite_float,
    confidence=unit_float,
    evidence_refs=st.lists(nonempty_text, min_size=1, max_size=3),
    delta_vs_last=st.none() | finite_float,
    chetty_modifiers_applied=str_list,
)

genome_results = st.builds(
    GenomeResult,
    rings=st.dictionaries(text, st.dictionaries(text, subfactor_scores, max_size=3), max_size=3),
    sigmoid_admit_probs=st.dictionaries(text, unit_float, max_size=3),
    flags=str_list,
)

plan_tasks = st.builds(
    PlanTask,
    title=nonempty_text,
    spike_alignment=text,
    effort_hrs=st.floats(min_value=0.0, max_value=100.0, allow_nan=False),
    evidence_required=text,
    rationale=nonempty_text,
)

planner_results = st.builds(
    PlannerResult,
    # min_size=0: zero-task week plans are contract-valid (protected weeks, 01-TECH_SPEC §6).
    week_plan=st.builds(WeekPlan, tasks=st.lists(plan_tasks, min_size=0, max_size=5)),
    roadmap_diff=str_list,
    autonomy_level_required=autonomy_level,
    agenda_seed_for_coach_session=text,
)

zuzu_frames = st.builds(
    ZuzuFrame,
    eq_signals=str_list,
    commitments_made=str_list,
    wellbeing_flag=st.none()
    | st.booleans()
    | st.builds(WellbeingFlagDetail, flagged=st.booleans(), reason=nonempty_text),
    followups=str_list,
)

sentinel_results = st.fixed_dictionaries(
    {
        "class": st.integers(min_value=1, max_value=5),
        "severity": nonempty_text,
        "evidence_ref": nonempty_text,
        "recommended_action": nonempty_text,
    }
).map(SentinelResult.model_validate)

verifier_results = st.builds(
    VerifierResult,
    suite=nonempty_text,
    cases_run=st.integers(min_value=0, max_value=10**4),
    pass_rate=unit_float,
    failures=st.lists(
        st.builds(VerifierFailure, case=nonempty_text, expected=text, got=text, diagnosis=text),
        max_size=3,
    ),
)


def assert_roundtrip(instance: ContractModel, schema_name: str) -> None:
    dumped = instance.to_contract_dict()
    validate(schema_name, dumped)
    # The wire dict must survive real JSON serialization unchanged.
    rehydrated = json.loads(json.dumps(dumped))
    validate(schema_name, rehydrated)
    reparsed = type(instance).model_validate(rehydrated)
    assert reparsed == instance


@roundtrip_settings
@given(specialist_inputs)
def test_specialist_input_roundtrip(instance: SpecialistInput) -> None:
    assert_roundtrip(instance, "specialist_input")


@roundtrip_settings
@given(specialist_outputs)
def test_specialist_output_roundtrip(instance: SpecialistOutput) -> None:
    assert_roundtrip(instance, "specialist_output")


@roundtrip_settings
@given(genome_results)
def test_genome_result_roundtrip(instance: GenomeResult) -> None:
    assert_roundtrip(instance, "a2_genome")


@roundtrip_settings
@given(planner_results)
def test_planner_result_roundtrip(instance: PlannerResult) -> None:
    assert_roundtrip(instance, "a3_planner")


@roundtrip_settings
@given(zuzu_frames)
def test_zuzu_frame_roundtrip(instance: ZuzuFrame) -> None:
    assert_roundtrip(instance, "a1_zuzu_frame")


@roundtrip_settings
@given(sentinel_results)
def test_sentinel_result_roundtrip(instance: SentinelResult) -> None:
    assert_roundtrip(instance, "a8_sentinel")


@roundtrip_settings
@given(verifier_results)
def test_verifier_result_roundtrip(instance: VerifierResult) -> None:
    assert_roundtrip(instance, "a9_verifier")


@roundtrip_settings
@given(sentinel_results)
def test_sentinel_wire_dict_uses_class_key(instance: SentinelResult) -> None:
    """The wire contract says 'class' (a Python keyword); the alias must always win on dump."""
    dumped = instance.to_contract_dict()
    assert "class" in dumped and "escalation_class" not in dumped


@roundtrip_settings
@given(specialist_outputs)
def test_output_escalate_wire_shape(instance: SpecialistOutput) -> None:
    dumped = instance.to_contract_dict()
    assert "escalate" in dumped
    if dumped["escalate"] is not None:
        assert set(dumped["escalate"]) == {"class", "reason"}
