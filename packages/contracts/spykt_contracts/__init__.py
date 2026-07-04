"""Specialist I/O contracts (01-TECH_SPEC §5).

JSON Schema files (draft 2020-12) ship as package data in
spykt_contracts/schemas/ — the wire contract every specialist honors. This
package ships pydantic v2 mirrors, a schema loader/validator, and the semantic
planner check (≥60% spike alignment), with round-trip and layer-agreement
property tests at the G1 gate.
"""

from spykt_contracts.loader import SCHEMAS_DIR, load_schema, schema_names, validate
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
from spykt_contracts.semantic import SPIKE_ALIGNMENT_THRESHOLD, SpikeAlignmentError, validate_planner_result

__all__ = [
    "SCHEMAS_DIR",
    "SPIKE_ALIGNMENT_THRESHOLD",
    "AuditStamp",
    "ContractModel",
    "EscalationDirective",
    "GenomeResult",
    "PlanTask",
    "PlannerResult",
    "SentinelResult",
    "SpecialistInput",
    "SpecialistOutput",
    "SpikeAlignmentError",
    "SubfactorScore",
    "VerifierFailure",
    "VerifierResult",
    "WeekPlan",
    "WellbeingFlagDetail",
    "ZuzuFrame",
    "load_schema",
    "schema_names",
    "validate",
    "validate_planner_result",
]
