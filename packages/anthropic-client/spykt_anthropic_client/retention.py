"""Retention gate (01-TECH_SPEC §4.1.4 — compliance-critical, prime directive 1).

The client refuses to send any payload to claude-fable-5 unless it carries a
`pseudonymized=true` attestation from the Pseudonymization Gateway. Enforcement
lives here in the client, not in caller discipline. The full Gateway is Phase 1;
this module fixes the enforcement seam and its exception type now so nothing
else can be built around it.

Spec tension, resolved in the safe direction (recorded per prime directive 4):
PRD §7.1 says an unverifiable-pseudonymization Fable payload "degrades to Opus 4.8
... logged as a degraded-quality event", while the CLAUDE.md Phase-1 gate (G1)
requires "attestation stripped -> client raises". This module implements the G1
behavior: RetentionGateError is raised and never caught-and-continued inside the
client, so a missing attestation can never silently reach any model. The PRD-style
degrade-to-Opus-with-logged-event flow is an ORCHESTRATOR responsibility (Phase 3):
the Orchestrator may catch RetentionGateError, re-route the job to Opus under
standard retention terms, and write the degraded-quality event. Do NOT weaken this
gate to absorb that behavior into the client.
"""

from typing import Any

FABLE_PREFIX = "claude-fable"


class RetentionGateError(RuntimeError):
    """Raised when a Fable-bound payload lacks a Gateway pseudonymization attestation."""


def require_pseudonymized(model: str, attestation: dict[str, Any] | None) -> None:
    """Raise RetentionGateError if `model` is a Fable model and attestation is missing/invalid.

    A valid attestation is produced only by the Gateway: {"pseudonymized": True, "scrub_report_hash": str}.
    """
    if not model.startswith(FABLE_PREFIX):
        return
    if (
        not attestation
        or attestation.get("pseudonymized") is not True
        or not isinstance(attestation.get("scrub_report_hash"), str)
        or not attestation["scrub_report_hash"]
    ):
        raise RetentionGateError(
            f"Blocked payload to {model}: missing/invalid pseudonymization attestation "
            "(01-TECH_SPEC §4.1.4). Route the payload through the Pseudonymization Gateway."
        )
