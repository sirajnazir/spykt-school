"""Attestation contract + payload scrubbing tests.

The attestation produced here must satisfy `require_pseudonymized()` in
spykt-anthropic-client — that is the retention gate that blocks unscrubbed
Fable payloads (01-TECH_SPEC §4.1.4). No real API calls anywhere.
"""

import hashlib
import json
import re

from conftest import MAYA
from spykt_anthropic_client.retention import require_pseudonymized

from spykt_gateway import PseudonymizationGateway


def test_attestation_shape_satisfies_retention_gate(gateway: PseudonymizationGateway):
    result = gateway.scrub("s1", "Maya Chen loves robotics.", MAYA)
    assert result.attestation["pseudonymized"] is True
    assert re.fullmatch(r"[0-9a-f]{64}", result.attestation["scrub_report_hash"])
    # Must not raise for the Fable model string:
    require_pseudonymized("claude-fable-5", result.attestation)


def test_hash_is_sha256_of_canonical_json_report(gateway: PseudonymizationGateway):
    result = gateway.scrub("s1", "Maya Chen loves robotics.", MAYA)
    canonical = json.dumps(result.report, sort_keys=True, separators=(",", ":"))
    assert result.attestation["scrub_report_hash"] == hashlib.sha256(canonical.encode()).hexdigest()


def test_attestation_hash_changes_when_report_changes(gateway: PseudonymizationGateway):
    a = gateway.scrub("s1", "Maya Chen loves robotics.", MAYA)
    b = gateway.scrub("s1", "Maya Chen and Wei Chen love robotics.", MAYA)
    assert a.report != b.report
    assert a.attestation["scrub_report_hash"] != b.attestation["scrub_report_hash"]


def test_report_counts_match_replacements(gateway: PseudonymizationGateway):
    result = gateway.scrub("s1", "Maya Chen emailed maya.chen@gmail.com from Lincoln High.", MAYA)
    replacements = result.report["replacements"]
    assert len(replacements) == sum(result.report["counts"].values())
    for entry in replacements:
        assert set(entry) == {"kind", "token"}


def test_scrub_payload_recurses_and_attests(gateway: PseudonymizationGateway):
    payload = {
        "student": "Maya Chen",
        "week": 12,
        "artifacts": [
            {"title": "Essay draft", "body": "I am Maya Chen and I attend Lincoln High."},
            {"contact": "maya.chen@gmail.com", "tags": ["robotics", None]},
        ],
        "meta": {"nested": {"note": "Call 503-555-0142 about Maya's plan."}},
    }
    scrubbed, attestation = gateway.scrub_payload("s1", payload, MAYA)
    flat = json.dumps(scrubbed).lower()
    for leak in ("maya", "chen", "lincoln", "gmail", "0142"):
        assert leak not in flat, (leak, scrubbed)
    # Structure and non-string values preserved:
    assert scrubbed["week"] == 12
    assert scrubbed["artifacts"][1]["tags"] == ["robotics", None]
    # Original payload untouched:
    assert payload["student"] == "Maya Chen"
    require_pseudonymized("claude-fable-5", attestation)


def test_scrub_payload_empty_strings_still_attest(gateway: PseudonymizationGateway):
    scrubbed, attestation = gateway.scrub_payload("s1", {"note": ""}, MAYA)
    assert scrubbed == {"note": ""}
    require_pseudonymized("claude-fable-5", attestation)
