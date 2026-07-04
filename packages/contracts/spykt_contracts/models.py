"""Pydantic v2 mirrors of the specialist JSON Schemas (01-TECH_SPEC §5).

Every model here mirrors one schema in spykt_contracts/schemas/ field-for-field,
including requiredness: a field the schema requires has no default here, so the
two layers give the same verdict on the same wire document. The schemas are the
wire contract; these models are the typed in-process view. Round-trip and
layer-agreement tests at the G1 gate keep the two in lockstep.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

AutonomyLevel = Literal["L0", "L1", "L2", "L3"]
SpecialistStatus = Literal["ok", "low_confidence", "refused", "error"]

NonEmptyStr = Annotated[str, Field(min_length=1)]
UnitFloat = Annotated[float, Field(ge=0.0, le=1.0)]


class ContractModel(BaseModel):
    """Base for all contract models: unknown fields are rejected, matching additionalProperties:false.

    Inbound parsing accepts wire keys only (aliases; never Python field names),
    so a document is pydantic-valid iff it is schema-valid — the two validation
    layers must never disagree on the same wire document.
    """

    model_config = ConfigDict(extra="forbid")

    def to_contract_dict(self) -> dict[str, Any]:
        """Dump to the exact wire shape the JSON Schemas validate (aliases, JSON-native types)."""
        return self.model_dump(mode="json", by_alias=True)


# ---------------------------------------------------------------------------
# Envelopes (every specialist)
# ---------------------------------------------------------------------------


class SpecialistInput(ContractModel):
    """Mirror of specialist_input.schema.json."""

    job_id: NonEmptyStr
    student_pseudonym: NonEmptyStr
    task: NonEmptyStr
    context_refs: list[NonEmptyStr] = Field(default_factory=list)
    budget_tokens: int = Field(ge=1)
    autonomy_ceiling: AutonomyLevel


class EscalationDirective(ContractModel):
    """Escalation request inside a SpecialistOutput; classes per PRD §6.2.

    The wire key is 'class' (a Python keyword); construct in Python via
    `EscalationDirective.model_validate({"class": ..., "reason": ...})`.
    The Python field name is not accepted on parse — only the wire key is.
    """

    escalation_class: int = Field(alias="class", ge=1, le=5)
    reason: NonEmptyStr


class AuditStamp(ContractModel):
    """Provenance stamp: which model/prompt produced the output and at what token cost."""

    model: NonEmptyStr
    prompt_version: NonEmptyStr
    tokens_in: int = Field(ge=0)
    tokens_out: int = Field(ge=0)


class SpecialistOutput(ContractModel):
    """Mirror of specialist_output.schema.json.

    `escalate` is required with no default: a specialist must affirmatively
    state `null` ("I considered escalation and there is none"). A payload that
    omits the key is malformed, never silently treated as no-escalation —
    escalation routing (PRD §6.2) keys off this field.
    """

    job_id: NonEmptyStr
    status: SpecialistStatus
    confidence: UnitFloat
    result: dict[str, Any]
    escalate: EscalationDirective | None
    audit: AuditStamp


# ---------------------------------------------------------------------------
# A2 GenomeScorer
# ---------------------------------------------------------------------------


class SubfactorScore(ContractModel):
    """One scored Genome cell. evidence_refs min_length=1: a score without evidence is invalid."""

    score: float
    confidence: UnitFloat
    evidence_refs: list[NonEmptyStr] = Field(min_length=1)
    delta_vs_last: float | None = None
    chetty_modifiers_applied: list[NonEmptyStr] = Field(default_factory=list)


class GenomeResult(ContractModel):
    """Mirror of results/a2_genome.schema.json: ring → subfactor → SubfactorScore.

    Shape deviation from 01-TECH_SPEC §5 (flagged for G1 schema review): the
    spec sketch shows ring names at the top level of the result; here the ring
    map nests under an explicit 'rings' key, because dynamic top-level keys
    cannot coexist with additionalProperties:false on the envelope.

    `flags` is required (matching the schema): a scorer must affirmatively
    state an empty list rather than omit the key.
    """

    rings: dict[str, dict[str, SubfactorScore]]
    sigmoid_admit_probs: dict[str, UnitFloat]
    flags: list[NonEmptyStr]


# ---------------------------------------------------------------------------
# A3 Pathway Planner
# ---------------------------------------------------------------------------


class PlanTask(ContractModel):
    """One weekly task; rationale is mandatory (every task must explain itself)."""

    title: NonEmptyStr
    spike_alignment: str
    effort_hrs: float = Field(ge=0.0)
    evidence_required: str
    rationale: NonEmptyStr


class WeekPlan(ContractModel):
    """At most 5 tasks per week — Jenny cadence: depth over breadth.

    No minimum: a zero-task week is contract-valid (e.g. a protected week,
    01-TECH_SPEC §6, suppresses tasking while keeping evidence capture open).
    """

    tasks: list[PlanTask] = Field(max_length=5)


class PlannerResult(ContractModel):
    """Mirror of results/a3_planner.schema.json.

    The ≥60% spike-alignment rule is semantic, not schema-expressible: see
    spykt_contracts.semantic.validate_planner_result.
    """

    week_plan: WeekPlan
    roadmap_diff: list[NonEmptyStr] = Field(default_factory=list)
    autonomy_level_required: AutonomyLevel
    agenda_seed_for_coach_session: str


# ---------------------------------------------------------------------------
# A1 Zuzu trailing frame
# ---------------------------------------------------------------------------


class WellbeingFlagDetail(ContractModel):
    """Structured form of a Zuzu wellbeing flag."""

    flagged: bool
    reason: NonEmptyStr


class ZuzuFrame(ContractModel):
    """Mirror of results/a1_zuzu_frame.schema.json (trailing structured frame, parsed post-stream).

    eq_signals/commitments_made/followups are required (matching the schema):
    Zuzu must affirmatively emit empty lists, not omit the keys.
    """

    eq_signals: list[NonEmptyStr]
    commitments_made: list[NonEmptyStr]
    wellbeing_flag: bool | WellbeingFlagDetail | None = None
    followups: list[NonEmptyStr]


# ---------------------------------------------------------------------------
# A8 Escalation Sentinel
# ---------------------------------------------------------------------------


class SentinelResult(ContractModel):
    """Mirror of results/a8_sentinel.schema.json; classes per PRD §6.2.

    The wire key is 'class' (a Python keyword); construct in Python via
    `SentinelResult.model_validate({"class": ..., ...})`. The Python field
    name is not accepted on parse — only the wire key is.
    """

    escalation_class: int = Field(alias="class", ge=1, le=5)
    severity: NonEmptyStr
    evidence_ref: NonEmptyStr
    recommended_action: NonEmptyStr


# ---------------------------------------------------------------------------
# A9 Verifier
# ---------------------------------------------------------------------------


class VerifierFailure(ContractModel):
    """One failed eval case; expected/got are JSON-serialized strings for stable diffing."""

    case: NonEmptyStr
    expected: str
    got: str
    diagnosis: str


class VerifierResult(ContractModel):
    """Mirror of results/a9_verifier.schema.json.

    `failures` is required (matching the schema): a clean run must
    affirmatively report an empty list, not omit the key.
    """

    suite: NonEmptyStr
    cases_run: int = Field(ge=0)
    pass_rate: UnitFloat
    failures: list[VerifierFailure]
