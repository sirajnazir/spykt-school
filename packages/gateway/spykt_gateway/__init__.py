"""Pseudonymization Gateway v1 (01-TECH_SPEC §7, PRD §7.1 — compliance-critical).

Raw student-identifiable content never reaches claude-fable-5: all Fable-bound
payloads pass through `PseudonymizationGateway.scrub()` / `scrub_payload()`,
whose attestation is what the client-side retention gate requires.
"""

from spykt_gateway.gateway import (
    NER_LABELS,
    PARENT_FIELD_STRIPPED,
    TOKEN_RE,
    GatewayScrubError,
    PseudonymizationGateway,
    ScrubResult,
    derive_token,
)
from spykt_gateway.store import (
    InMemoryPseudonymStore,
    PseudonymRecord,
    PseudonymStore,
    TokenCollisionError,
)

__all__ = [
    "NER_LABELS",
    "PARENT_FIELD_STRIPPED",
    "TOKEN_RE",
    "GatewayScrubError",
    "InMemoryPseudonymStore",
    "PseudonymRecord",
    "PseudonymStore",
    "PseudonymizationGateway",
    "ScrubResult",
    "TokenCollisionError",
    "derive_token",
]
