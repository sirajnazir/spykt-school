"""Retention gate (01-TECH_SPEC §4.1.4 — compliance-critical, prime directive 1).

The client refuses to send any payload to claude-fable-5 unless it carries a
`pseudonymized=true` attestation from the Pseudonymization Gateway. Enforcement
lives here in the client, not in caller discipline. The full Gateway is Phase 1;
this module fixes the enforcement seam and its exception type now so nothing
else can be built around it.
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
