"""G1 gate tests for the four client middlewares (01-TECH_SPEC §4.1, CLAUDE.md Phase 1).

All model traffic is stubbed — no real Anthropic API calls, no API key required.
"""

import logging
from types import SimpleNamespace

import pytest

from spykt_anthropic_client import (
    BudgetExceededError,
    RetentionGateError,
    SpyktAnthropicClient,
    load_model_config,
)
from spykt_anthropic_client.client import estimate_input_tokens

VALID_ATTESTATION = {"pseudonymized": True, "scrub_report_hash": "a" * 64}
MESSAGES = [{"role": "user", "content": "scored transcript extract " * 10}]


def stub_response(stop_reason="end_turn", tokens_in=120, tokens_out=40, classifier=None):
    response = SimpleNamespace(
        stop_reason=stop_reason,
        content=[],
        usage=SimpleNamespace(input_tokens=tokens_in, output_tokens=tokens_out),
    )
    if classifier is not None:
        # 01 §4.1.1: refusals arrive with a classifier identifier on the response.
        response.refusal_classifier = classifier
    return response


class StubSDK:
    """Simple recorder standing in for anthropic.Anthropic — records every request."""

    def __init__(self, responses=None):
        self.requests: list[dict] = []
        self._responses = list(responses or [])
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        return self._responses.pop(0) if self._responses else stub_response()


class RecordingAudit:
    def __init__(self):
        self.rows: list[dict] = []

    def write(
        self,
        *,
        agent,
        model,
        prompt_version,
        action,
        autonomy_level=None,
        human_approver=None,
        student_id=None,
    ):
        self.rows.append(
            {
                "agent": agent,
                "model": model,
                "prompt_version": prompt_version,
                "action": action,
                "autonomy_level": autonomy_level,
                "human_approver": human_approver,
                "student_id": student_id,
            }
        )

    def rows_for(self, action):
        return [r for r in self.rows if r["action"] == action]


class StubSpendStore:
    def __init__(self, spend=None):
        self.spend = spend or {}

    def month_spend(self, student_id, model_alias):
        return self.spend.get((student_id, model_alias), 0.0)


class RecordingEscalation:
    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, *, escalation_class, reason):
        self.calls.append({"escalation_class": escalation_class, "reason": reason})


def make_client(sdk=None, spend=None, audit=None, escalation=None):
    return SpyktAnthropicClient(
        config=load_model_config(),
        audit_writer=audit if audit is not None else RecordingAudit(),
        spend_store=StubSpendStore(spend),
        escalation_hook=escalation,
        sdk_client=sdk if sdk is not None else StubSDK(),
    )


def call(client, job="genome_scorer", attestation=VALID_ATTESTATION, **overrides):
    kwargs = dict(
        student_id="stu_001",
        prompt_version="genome_scorer_v1",
        autonomy_level="L1",
        max_tokens=4000,
        attestation=attestation,
    )
    kwargs.update(overrides)
    return client.call(job, MESSAGES, **kwargs)


# --- 01 §4.1.1 refusal handling ------------------------------------------------------


def test_refusal_retries_on_opus_with_flag_escalation_and_two_audit_rows(caplog):
    sdk = StubSDK(
        responses=[
            stub_response(stop_reason="refusal", classifier="cls_minor_safety_v2"),
            stub_response(),
        ]
    )
    audit = RecordingAudit()
    escalation = RecordingEscalation()
    client = make_client(sdk=sdk, audit=audit, escalation=escalation)

    with caplog.at_level(logging.WARNING, logger="spykt_anthropic_client.client"):
        result = call(client)

    # Fallback retry lands on claude-opus-4-8 (models.yaml fallbacks: fable -> opus).
    assert [req["model"] for req in sdk.requests] == ["claude-fable-5", "claude-opus-4-8"]
    assert result.model_used == "claude-opus-4-8"
    assert "model_fallback" in result.flags

    # Class-5 escalation fired (PRD §6.2.5) — reason carries the classifier id (01 §4.1.1).
    assert len(escalation.calls) == 1
    assert escalation.calls[0]["escalation_class"] == 5
    assert "cls_minor_safety_v2" in escalation.calls[0]["reason"]

    # TWO model_call audit rows — one per request issued — plus the fable_refusal event
    # whose action carries the classifier id (01 §4.1.1: "with classifier id").
    model_calls = audit.rows_for("model_call")
    assert len(model_calls) == 2
    assert [r["model"] for r in model_calls] == ["claude-fable-5", "claude-opus-4-8"]
    refusal_rows = [r for r in audit.rows if r["action"].startswith("fable_refusal")]
    assert len(refusal_rows) == 1
    assert refusal_rows[0]["action"] == "fable_refusal:cls_minor_safety_v2"

    # The refusal log line carries the classifier id too.
    assert any("cls_minor_safety_v2" in record.getMessage() for record in caplog.records)


def test_refusal_without_classifier_id_still_audited_and_escalated():
    """A refusal missing its classifier id (defensive) keeps the plain action string."""
    sdk = StubSDK(responses=[stub_response(stop_reason="refusal"), stub_response()])
    audit = RecordingAudit()
    escalation = RecordingEscalation()
    client = make_client(sdk=sdk, audit=audit, escalation=escalation)

    result = call(client)

    assert result.model_used == "claude-opus-4-8"
    assert len(audit.rows_for("fable_refusal")) == 1
    assert len(escalation.calls) == 1
    assert "<none>" in escalation.calls[0]["reason"]


def test_second_refusal_on_fallback_reaudited_and_reescalated_without_third_request():
    """PRD §6.2.5 'beyond fallback': re-audit + re-escalate, exactly two requests total."""
    sdk = StubSDK(
        responses=[
            stub_response(stop_reason="refusal", classifier="cls_a"),
            stub_response(stop_reason="refusal", classifier="cls_b"),
        ]
    )
    audit = RecordingAudit()
    escalation = RecordingEscalation()
    client = make_client(sdk=sdk, audit=audit, escalation=escalation)

    result = call(client)

    # No retry loop: exactly two requests (fable, then opus) — never a third.
    assert [req["model"] for req in sdk.requests] == ["claude-fable-5", "claude-opus-4-8"]
    assert result.model_used == "claude-opus-4-8"
    assert "model_fallback" in result.flags
    assert result.response.stop_reason == "refusal"  # surfaced, not swallowed

    # Both refusals audited: fable_refusal on the Fable row, model_refusal on the opus row
    # (the fable_refusal action name is scoped to Fable models — 01 §4.1 / PRD §6.2.5).
    assert audit.rows[1]["action"] == "fable_refusal:cls_a"
    assert audit.rows[1]["model"] == "claude-fable-5"
    assert audit.rows[3]["action"] == "model_refusal:cls_b"
    assert audit.rows[3]["model"] == "claude-opus-4-8"

    # Both escalated class-5.
    assert [c["escalation_class"] for c in escalation.calls] == [5, 5]


def test_non_fable_refusal_not_labeled_fable_refusal():
    """A Sonnet refusal is audited as model_refusal, never fable_refusal (01 §4.1 scope)."""
    sdk = StubSDK(responses=[stub_response(stop_reason="refusal", classifier="cls_x")])
    audit = RecordingAudit()
    escalation = RecordingEscalation()
    client = make_client(sdk=sdk, audit=audit, escalation=escalation)

    result = call(client, job="zuzu_live", attestation=None)

    # Sonnet has no fallback configured: one request, refusal surfaced.
    assert [req["model"] for req in sdk.requests] == ["claude-sonnet-4-6"]
    assert result.response.stop_reason == "refusal"
    assert not any(r["action"].startswith("fable_refusal") for r in audit.rows)
    refusal_rows = [r for r in audit.rows if r["action"].startswith("model_refusal")]
    assert len(refusal_rows) == 1
    assert refusal_rows[0]["action"] == "model_refusal:cls_x"
    # Still escalated — over-escalating is the safe direction.
    assert len(escalation.calls) == 1


def test_unwired_escalation_hook_logs_loudly_on_refusal(caplog):
    """PRD §6.2: escalation is unconditional — an unwired hook must not drop it silently."""
    sdk = StubSDK(responses=[stub_response(stop_reason="refusal", classifier="cls_y")])
    audit = RecordingAudit()
    client = make_client(sdk=sdk, audit=audit, escalation=None)

    with caplog.at_level(logging.ERROR, logger="spykt_anthropic_client.client"):
        call(client, job="zuzu_live", attestation=None)

    # Refusal is still audited, and the missing delivery hook is an ERROR, not silence.
    assert any(r["action"].startswith("model_refusal") for r in audit.rows)
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1
    message = errors[0].getMessage()
    assert "class-5" in message
    assert "cls_y" in message


def test_fallback_retry_still_passes_retention_gate_bookkeeping():
    """The opus retry is issued even with attestation present; gate re-checked per model."""
    sdk = StubSDK(responses=[stub_response(stop_reason="refusal"), stub_response()])
    client = make_client(sdk=sdk)
    result = call(client)
    assert result.model_used == "claude-opus-4-8"
    assert len(sdk.requests) == 2


# --- 01 §4.1.3 budget guard ----------------------------------------------------------


def test_over_budget_fable_job_degrades_to_opus_without_attestation():
    # Student already at the $28 fable ceiling (models.yaml budgets) -> any estimate tips it.
    sdk = StubSDK()
    audit = RecordingAudit()
    client = make_client(sdk=sdk, audit=audit, spend={("stu_001", "fable"): 28.0})

    # Opus needs no attestation — the degraded call must pass the retention gate bare.
    result = call(client, attestation=None)

    assert result.model_used == "claude-opus-4-8"
    assert "degraded_budget" in result.flags
    assert [req["model"] for req in sdk.requests] == ["claude-opus-4-8"]
    assert len(audit.rows_for("model_call")) == 1


def test_under_budget_fable_job_stays_on_fable():
    sdk = StubSDK()
    client = make_client(sdk=sdk, spend={("stu_001", "fable"): 0.0})
    result = call(client)
    assert result.model_used == "claude-fable-5"
    assert "degraded_budget" not in result.flags


def test_over_ceiling_with_no_fallback_raises_never_silently_skips():
    """Sonnet ($9 ceiling, no fallback in models.yaml) over budget: raise, zero requests."""
    sdk = StubSDK()
    audit = RecordingAudit()
    client = make_client(sdk=sdk, audit=audit, spend={("stu_001", "sonnet"): 9.0})

    with pytest.raises(BudgetExceededError):
        call(client, job="zuzu_live", attestation=None)

    assert sdk.requests == []  # blocked pre-flight — nothing was sent
    assert audit.rows == []


def test_budget_degrade_triggers_at_partial_spend_boundary():
    """§4.1.3 estimate math: degrade fires when spent + (est_in + max_tokens) x pricing
    crosses the ceiling — not only at exactly-at-ceiling spend."""
    cfg = load_model_config()
    pricing = cfg.pricing["fable"]
    max_tokens = 4000
    est_in = estimate_input_tokens(MESSAGES, None)
    estimate = est_in / 1e6 * pricing["input"] + max_tokens / 1e6 * pricing["output"]
    ceiling = cfg.budgets["fable"]
    assert 0 < estimate < ceiling  # the estimate itself, not the spend, must tip it

    # Spend just under the ceiling but within `estimate` of it -> degrades to opus.
    sdk = StubSDK()
    client = make_client(sdk=sdk, spend={("stu_001", "fable"): ceiling - estimate / 2})
    result = call(client, max_tokens=max_tokens)
    assert result.model_used == "claude-opus-4-8"
    assert "degraded_budget" in result.flags

    # Spend leaving just enough headroom for the estimate -> stays on fable.
    sdk = StubSDK()
    client = make_client(sdk=sdk, spend={("stu_001", "fable"): ceiling - estimate * 2})
    result = call(client, max_tokens=max_tokens)
    assert result.model_used == "claude-fable-5"
    assert "degraded_budget" not in result.flags


# --- 01 §4.1.4 retention gate --------------------------------------------------------


def test_stripped_attestation_raises_before_any_request():
    sdk = StubSDK()
    audit = RecordingAudit()
    client = make_client(sdk=sdk, audit=audit)

    with pytest.raises(RetentionGateError):
        call(client, attestation=None)

    assert sdk.requests == []  # zero requests issued
    assert audit.rows == []  # nothing to audit — nothing was sent


def test_invalid_attestation_raises_before_any_request():
    sdk = StubSDK()
    client = make_client(sdk=sdk)
    with pytest.raises(RetentionGateError):
        call(client, attestation={"pseudonymized": False, "scrub_report_hash": "x"})
    assert sdk.requests == []


# --- 01 §4.1.2 thinking config + happy path ------------------------------------------


def test_happy_path_fable_call_sets_thinking_config_and_audits_once():
    sdk = StubSDK()
    audit = RecordingAudit()
    client = make_client(sdk=sdk, audit=audit)

    result = call(client)

    assert result.model_used == "claude-fable-5"
    assert result.flags == []
    assert result.tokens_in == 120
    assert result.tokens_out == 40

    assert len(sdk.requests) == 1
    request = sdk.requests[0]
    assert request["model"] == "claude-fable-5"
    assert request["thinking"] == {"type": "adaptive", "display": "summarized"}

    rows = audit.rows_for("model_call")
    assert len(rows) == 1
    assert rows[0]["model"] == "claude-fable-5"
    assert rows[0]["action"] == "model_call"
    assert rows[0]["student_id"] == "stu_001"
    assert rows[0]["prompt_version"] == "genome_scorer_v1"
    assert rows[0]["autonomy_level"] == "L1"


def test_agent_name_overrides_job_in_audit_row():
    audit = RecordingAudit()
    client = make_client(audit=audit)
    call(client, agent_name="A2-GenomeScorer")
    assert audit.rows_for("model_call")[0]["agent"] == "A2-GenomeScorer"


# --- CLAUDE.md Phase 1 gate: audit rows on every model call --------------------------


def test_audit_row_on_every_call_for_sonnet_and_haiku_jobs():
    for job, expected_model in [
        ("zuzu_live", "claude-sonnet-4-6"),
        ("sentinel", "claude-haiku-4-5-20251001"),
    ]:
        sdk = StubSDK()
        audit = RecordingAudit()
        client = make_client(sdk=sdk, audit=audit)

        result = call(client, job=job, attestation=None)  # non-Fable: no attestation needed

        assert result.model_used == expected_model
        rows = audit.rows_for("model_call")
        assert len(rows) == 1
        assert rows[0]["model"] == expected_model
        # Non-Fable models never get the Fable thinking config.
        assert "thinking" not in sdk.requests[0]
