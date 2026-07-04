import pytest

from spykt_anthropic_client import RetentionGateError, require_pseudonymized

VALID = {"pseudonymized": True, "scrub_report_hash": "abc123"}


def test_fable_blocked_without_attestation():
    with pytest.raises(RetentionGateError):
        require_pseudonymized("claude-fable-5", None)


def test_fable_blocked_with_stripped_attestation():
    """G1 gate case: attestation stripped → client raises (CLAUDE.md Phase 1 gate)."""
    with pytest.raises(RetentionGateError):
        require_pseudonymized("claude-fable-5", {"pseudonymized": True})  # no scrub_report_hash
    with pytest.raises(RetentionGateError):
        require_pseudonymized("claude-fable-5", {"pseudonymized": "true", "scrub_report_hash": "x"})


def test_fable_allowed_with_valid_attestation():
    require_pseudonymized("claude-fable-5", VALID)


def test_non_fable_models_pass_without_attestation():
    for model in ("claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"):
        require_pseudonymized(model, None)
