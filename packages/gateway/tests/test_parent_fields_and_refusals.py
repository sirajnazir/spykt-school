"""Parent-provided field stripping + scrub-or-refuse payload hardening.

01-TECH_SPEC §7 Outbound: "payloads to Fable additionally strip parent-provided
free-text fields unless whitelisted." And the attestation must never vouch for
content the pipeline did not inspect (PRD §7.1, retention gate §4.1.4): dict
keys are scrubbed like values; PII-shaped numerics, non-string keys, and
unsupported leaf types are refused loudly. No real API calls anywhere.
"""

import json

import pytest
from conftest import MAYA
from spykt_anthropic_client.retention import require_pseudonymized

from spykt_gateway import (
    PARENT_FIELD_STRIPPED,
    GatewayScrubError,
    InMemoryPseudonymStore,
    PseudonymizationGateway,
)

# -- parent-provided field stripping (§7 Outbound) ---------------------------


def test_conventional_parent_key_is_stripped(gateway: PseudonymizationGateway):
    payload = {"task": "robotics milestone", "parent_note": "Wei Chen says Maya is anxious."}
    scrubbed, attestation = gateway.scrub_payload("s1", payload, MAYA)
    assert scrubbed["parent_note"] == PARENT_FIELD_STRIPPED
    flat = json.dumps(scrubbed).lower()
    for leak in ("maya", "chen", "wei", "anxious"):
        assert leak not in flat, (leak, scrubbed)
    require_pseudonymized("claude-fable-5", attestation)


def test_declared_parent_field_is_stripped_without_conventional_name(
    gateway: PseudonymizationGateway,
):
    payload = {"observations": "Mom wrote: Maya cried after practice.", "week": 12}
    scrubbed, _ = gateway.scrub_payload("s1", payload, MAYA, parent_fields=["observations"])
    assert scrubbed["observations"] == PARENT_FIELD_STRIPPED
    assert scrubbed["week"] == 12


def test_declared_dotted_path_strips_only_that_path(gateway: PseudonymizationGateway):
    payload = {
        "meta": {"observations": "Mom's note about Maya."},
        "coach": {"observations": "Maya Chen hit the milestone."},
    }
    scrubbed, _ = gateway.scrub_payload("s1", payload, MAYA, parent_fields=["meta.observations"])
    assert scrubbed["meta"]["observations"] == PARENT_FIELD_STRIPPED
    # Non-declared sibling passes through the normal scrub pipeline instead.
    assert scrubbed["coach"]["observations"] != PARENT_FIELD_STRIPPED
    assert "maya" not in scrubbed["coach"]["observations"].lower()


def test_parent_subtree_is_stripped_wholesale(gateway: PseudonymizationGateway):
    payload = {"parent_feedback": {"mood": "Maya seemed off", "contacts": ["503-555-0142"]}}
    scrubbed, _ = gateway.scrub_payload("s1", payload, MAYA)
    assert scrubbed["parent_feedback"] == PARENT_FIELD_STRIPPED


def test_parent_key_inside_list_items_is_stripped(gateway: PseudonymizationGateway):
    payload = {"entries": [{"note": "fine"}, {"note": "fine", "from_parent": "Maya's mom called."}]}
    scrubbed, _ = gateway.scrub_payload("s1", payload, MAYA)
    assert scrubbed["entries"][1]["from_parent"] == PARENT_FIELD_STRIPPED
    assert "maya" not in json.dumps(scrubbed).lower()


def test_parenting_substring_is_not_a_false_positive(gateway: PseudonymizationGateway):
    scrubbed, _ = gateway.scrub_payload("s1", {"parenting_style": "supportive"}, MAYA)
    assert scrubbed["parenting_style"] == "supportive"


def test_whitelisted_parent_field_passes_but_is_still_scrubbed(store: InMemoryPseudonymStore):
    gateway = PseudonymizationGateway(store, parent_field_whitelist=["parent_note"])
    payload = {"parent_note": "Wei Chen thinks Maya Chen is ready."}
    scrubbed, attestation = gateway.scrub_payload("s1", payload, MAYA)
    # Whitelist never bypasses scrubbing — the content is tokenized, not raw.
    assert scrubbed["parent_note"] != PARENT_FIELD_STRIPPED
    flat = json.dumps(scrubbed).lower()
    for leak in ("maya", "chen", "wei"):
        assert leak not in flat, (leak, scrubbed)
    require_pseudonymized("claude-fable-5", attestation)


def test_per_call_whitelist_extends_constructor_whitelist(gateway: PseudonymizationGateway):
    payload = {"parent_note": "Maya is thriving."}
    scrubbed, _ = gateway.scrub_payload("s1", payload, MAYA, parent_field_whitelist=["parent_note"])
    assert scrubbed["parent_note"] != PARENT_FIELD_STRIPPED
    assert "maya" not in scrubbed["parent_note"].lower()


def test_whitelist_is_exact_dotted_path_not_bare_key(gateway: PseudonymizationGateway):
    payload = {"meta": {"parent_note": "Maya is thriving."}}
    # Whitelisting the bare key must NOT whitelist the nested path (narrow by design).
    scrubbed, _ = gateway.scrub_payload("s1", payload, MAYA, parent_field_whitelist=["parent_note"])
    assert scrubbed["meta"]["parent_note"] == PARENT_FIELD_STRIPPED
    scrubbed, _ = gateway.scrub_payload(
        "s1", payload, MAYA, parent_field_whitelist=["meta.parent_note"]
    )
    assert scrubbed["meta"]["parent_note"] != PARENT_FIELD_STRIPPED


def test_stripping_changes_the_attestation_hash(gateway: PseudonymizationGateway):
    stripped_run = gateway.scrub_payload("s1", {"parent_note": "hello"}, MAYA)
    whitelisted_run = gateway.scrub_payload(
        "s1", {"parent_note": "hello"}, MAYA, parent_field_whitelist=["parent_note"]
    )
    # Stripped paths are part of the hashed scrub report, so the audit trail differs.
    assert stripped_run[1]["scrub_report_hash"] != whitelisted_run[1]["scrub_report_hash"]


# -- scrub-or-refuse: keys and non-string leaves (PRD §7.1) -------------------


def test_pii_bearing_dict_key_is_scrubbed(
    gateway: PseudonymizationGateway, store: InMemoryPseudonymStore
):
    scrubbed, attestation = gateway.scrub_payload("s1", {"Maya Chen": "weekly note"}, MAYA)
    assert "Maya Chen" not in scrubbed
    pseudonym = store.get("s1").pseudonym
    assert scrubbed == {pseudonym: "weekly note"}
    require_pseudonymized("claude-fable-5", attestation)


def test_reviewer_repro_numeric_phone_is_refused(gateway: PseudonymizationGateway):
    # Exact adversarial repro: PII key + phone-as-int must never attest silently.
    with pytest.raises(GatewayScrubError, match="PHONE"):
        gateway.scrub_payload("s1", {"Maya Chen": "note", "phone": 5035550142}, MAYA)


def test_numeric_phone_as_float_is_refused(gateway: PseudonymizationGateway):
    with pytest.raises(GatewayScrubError, match="PHONE"):
        gateway.scrub_payload("s1", {"contact": 5035550142.0}, MAYA)


def test_safe_scalars_pass_through_unchanged(gateway: PseudonymizationGateway):
    payload = {"week": 12, "score": 3.9, "active": True, "coachref": None, "big": 123456789012}
    scrubbed, attestation = gateway.scrub_payload("s1", payload, MAYA)
    assert scrubbed == payload
    require_pseudonymized("claude-fable-5", attestation)


def test_unsupported_leaf_type_is_refused(gateway: PseudonymizationGateway):
    with pytest.raises(GatewayScrubError, match="unsupported leaf type"):
        gateway.scrub_payload("s1", {"blob": b"maya.chen@gmail.com"}, MAYA)


def test_non_string_dict_key_is_refused(gateway: PseudonymizationGateway):
    with pytest.raises(GatewayScrubError, match="non-string dict key"):
        gateway.scrub_payload("s1", {5035550142: "call me"}, MAYA)


def test_post_scrub_key_collision_is_refused(gateway: PseudonymizationGateway):
    # Case variants of the student's name both scrub to the one stable pseudonym;
    # silently keeping one branch would drop data, so the gateway refuses.
    with pytest.raises(GatewayScrubError, match="collide"):
        gateway.scrub_payload("s1", {"Maya Chen": 1, "MAYA CHEN": 2}, MAYA)
