"""Cross-package G1 integration: Gateway scrub → attestation → client retention gate → audit rows.

The per-package suites prove each unit in isolation; this proves the compliance story
end-to-end with the real packages wired together (PRD §7.1, 01-TECH_SPEC §4.1.4 + §7):
a Fable job only runs when its payload went through the Gateway, and every model call
lands in the audit trail.
"""

import pytest

from spykt_anthropic_client import RetentionGateError, SpyktAnthropicClient, load_model_config
from spykt_audit import InMemoryAuditWriter
from spykt_gateway import InMemoryPseudonymStore, PseudonymizationGateway


class StubResponse:
    def __init__(self, stop_reason="end_turn"):
        self.stop_reason = stop_reason
        self.content = [{"type": "text", "text": "{}"}]
        self.usage = type("U", (), {"input_tokens": 10, "output_tokens": 5})()


class StubSDK:
    def __init__(self):
        self.requests = []
        self.messages = self

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return StubResponse()


class ZeroSpend:
    def month_spend(self, student_id, model_alias):
        return 0.0


RAW = "Maya Chen from Lincoln High emailed maya.chen@example.com about her research."
KNOWN = {"name": "Maya Chen", "school": "Lincoln High", "emails": ["maya.chen@example.com"]}


@pytest.fixture()
def wired():
    gateway = PseudonymizationGateway(InMemoryPseudonymStore())
    audit = InMemoryAuditWriter()
    sdk = StubSDK()
    client = SpyktAnthropicClient(load_model_config(), audit, ZeroSpend(), sdk_client=sdk)
    return gateway, audit, sdk, client


def call_fable(client, attestation, text):
    return client.call(
        "genome_scorer",
        [{"role": "user", "content": text}],
        student_id="stu-1",
        prompt_version="genome-v0",
        autonomy_level="L0",
        max_tokens=64,
        attestation=attestation,
    )


def test_gateway_attestation_admits_fable_call_and_audits(wired):
    gateway, audit, sdk, client = wired
    scrubbed = gateway.scrub("stu-1", RAW, KNOWN)

    assert "Maya Chen" not in scrubbed.text and "maya.chen@example.com" not in scrubbed.text

    result = call_fable(client, scrubbed.attestation, scrubbed.text)

    assert result.model_used == "claude-fable-5"
    assert len(sdk.requests) == 1
    assert "Maya Chen" not in str(sdk.requests[0])  # no raw PII in the outbound request body
    assert [r["action"] for r in audit.rows] == ["model_call"]  # audit row on every model call


def test_fable_blocked_without_gateway_attestation(wired):
    _, audit, sdk, client = wired
    with pytest.raises(RetentionGateError):
        call_fable(client, None, RAW)
    assert sdk.requests == []  # blocked before any request was issued
    assert audit.rows == []


def test_fable_blocked_with_forged_attestation(wired):
    """An attestation not shaped by the Gateway (missing scrub-report hash) is refused."""
    _, _, sdk, client = wired
    with pytest.raises(RetentionGateError):
        call_fable(client, {"pseudonymized": True}, RAW)
    assert sdk.requests == []


def test_restore_round_trips_for_ui_persistence(wired):
    gateway, _, _, _ = wired
    scrubbed = gateway.scrub("stu-1", RAW, KNOWN)
    assert "Maya Chen" in gateway.restore("stu-1", scrubbed.text)
