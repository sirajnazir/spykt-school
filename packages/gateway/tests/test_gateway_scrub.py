"""Starter adversarial set for Gateway v1 (01-TECH_SPEC §7).

The full 200+ red-team suite is the Phase 2 gate; these seed it honestly.
Every case asserts on the scrubbed text itself — the string Fable would see.
"""

import re

from conftest import MAYA

from spykt_gateway import TOKEN_RE, InMemoryPseudonymStore, PseudonymizationGateway

PSEUDONYM_RE = re.compile(r"Student-[0-9a-f]{8}")


def assert_clean(text: str, *fragments: str) -> None:
    """Assert no PII fragment survives, case-insensitively."""
    lowered = text.lower()
    for fragment in fragments:
        assert fragment.lower() not in lowered, f"PII leak: {fragment!r} in {text!r}"


def test_plain_name_mention(gateway: PseudonymizationGateway):
    result = gateway.scrub("s1", "Maya Chen finished her robotics milestone early.", MAYA)
    assert_clean(result.text, "Maya", "Chen")
    assert PSEUDONYM_RE.search(result.text), result.text
    assert result.report["counts"]["NAME"] >= 1


def test_first_name_alone_is_scrubbed(gateway: PseudonymizationGateway):
    result = gateway.scrub("s1", "Maya seemed anxious about the deadline.", MAYA)
    assert_clean(result.text, "Maya")


def test_name_inside_quoted_essay_text(gateway: PseudonymizationGateway):
    text = 'Her draft opened: "My name is Maya Chen and I attend Lincoln High in Portland."'
    result = gateway.scrub("s1", text, MAYA)
    assert_clean(result.text, "Maya", "Chen", "Lincoln", "Portland")


def test_nickname_from_known_entities(gateway: PseudonymizationGateway):
    result = gateway.scrub("s1", "Her teammates call her May-May during practice.", MAYA)
    assert_clean(result.text, "May-May")
    assert "PII_NAME_" in result.text


def test_family_names_scrubbed(gateway: PseudonymizationGateway):
    result = gateway.scrub("s1", "Wei Chen and Lily Chen attended the showcase.", MAYA)
    assert_clean(result.text, "Wei", "Lily", "Chen")
    assert result.report["counts"]["FAMILY"] == 2


def test_school_full_phrase_and_bare_common_noun_form(gateway: PseudonymizationGateway):
    full = gateway.scrub("s1", "She represents Lincoln High at the state fair.", MAYA)
    assert_clean(full.text, "Lincoln")
    bare = gateway.scrub("s1", "Everyone at Lincoln says the robotics team is strong.", MAYA)
    assert_clean(bare.text, "Lincoln")
    # Longest-match-first: the full phrase is one token, not token+'High'.
    assert "High" not in full.text


def test_possessive_form(gateway: PseudonymizationGateway):
    result = gateway.scrub("s1", "Maya Chen's essay impressed the panel, and Maya's confidence grew.", MAYA)
    assert_clean(result.text, "Maya", "Chen")
    assert re.search(r"Student-[0-9a-f]{8}'s", result.text), result.text
    assert re.search(r"PII_NAME_[0-9a-f]{8}'s", result.text), result.text


def test_email_phone_handle_address_regex_layer(gateway: PseudonymizationGateway):
    text = (
        "Reach me at maya.chen@gmail.com or 503-555-0142, "
        "DM @mayabuilds, or stop by 4620 SW Maple Street."
    )
    result = gateway.scrub("s1", text)  # no known_entities: regex/NER layers must catch these
    assert_clean(result.text, "maya.chen@gmail.com", "gmail", "0142", "@mayabuilds", "4620", "Maple")
    for kind in ("EMAIL", "PHONE", "HANDLE", "ADDRESS"):
        assert result.report["counts"].get(kind, 0) >= 1, (kind, result.report)


def test_phone_format_variants(gateway: PseudonymizationGateway):
    text = "Call (503) 555-0142 or 503.555.0142 or +1 503-555-0142 or 5035550142."
    result = gateway.scrub("s1", text)
    assert_clean(result.text, "555", "0142")
    assert result.report["counts"]["PHONE"] == 4


def test_ssn_like_pattern(gateway: PseudonymizationGateway):
    result = gateway.scrub("s1", "Her form listed 123-45-6789 by mistake.")
    assert_clean(result.text, "123-45-6789")
    assert result.report["counts"]["SSN"] == 1


def test_ner_catches_unknown_person(gateway: PseudonymizationGateway, store: InMemoryPseudonymStore):
    result = gateway.scrub("s1", "Talked with Jonathan Rodriguez about the robotics project.", MAYA)
    assert_clean(result.text, "Jonathan", "Rodriguez")
    assert "PII_PERSON_" in result.text
    token = TOKEN_RE.search(result.text).group(0)
    assert store.lookup_token("s1", token) == "Jonathan Rodriguez"


def test_ner_catches_unknown_school_and_counselor(gateway: PseudonymizationGateway):
    result = gateway.scrub("s1", "My counselor is Mrs. Alvarez at Roosevelt Middle School.", MAYA)
    assert_clean(result.text, "Alvarez", "Roosevelt")


def test_multi_student_isolation(store: InMemoryPseudonymStore):
    gateway = PseudonymizationGateway(store)
    text = "Maya Chen at Lincoln High won the prize."
    a = gateway.scrub("s1", text, MAYA)
    b = gateway.scrub("s2", text, MAYA)
    assert a.text != b.text
    tokens_a = set(TOKEN_RE.findall(a.text))
    tokens_b = set(TOKEN_RE.findall(b.text))
    assert tokens_a and tokens_a.isdisjoint(tokens_b)


def test_round_trip_restore(gateway: PseudonymizationGateway):
    text = (
        "Maya Chen's counselor at Lincoln High emailed maya.chen@gmail.com and "
        "called 503-555-0142 about the Portland showcase; DM @mayabuilds."
    )
    result = gateway.scrub("s1", text, MAYA)
    assert_clean(result.text, "Maya", "Chen", "Lincoln", "gmail", "0142", "mayabuilds", "Portland")
    assert gateway.restore("s1", result.text) == text


def test_round_trip_restores_ner_tokens_from_store(gateway: PseudonymizationGateway):
    text = "Talked with Jonathan Rodriguez about the robotics project."
    result = gateway.scrub("s1", text, MAYA)
    assert gateway.restore("s1", result.text) == text


def test_restore_canonicalizes_case_variant_surface_forms(gateway: PseudonymizationGateway):
    # Regression note (pre-Phase-2 pin): known-entity matching is case-insensitive
    # but one token maps to one stored original, so shouting restores to the
    # canonical form. Inbound-only cosmetic behavior — no PII exposure.
    result = gateway.scrub("s1", "MAYA CHEN yelled.", MAYA)
    assert_clean(result.text, "Maya", "Chen")
    assert gateway.restore("s1", result.text) == "Maya Chen yelled."


def test_restore_leaves_unknown_tokens_untouched(gateway: PseudonymizationGateway):
    text = "Status for PII_NAME_00000000 pending."
    assert gateway.restore("s1", text) == text


def test_determinism_same_input_twice(gateway: PseudonymizationGateway):
    text = "Maya Chen emailed maya.chen@gmail.com about Lincoln High."
    first = gateway.scrub("s1", text, MAYA)
    second = gateway.scrub("s1", text, MAYA)
    assert first.text == second.text
    assert first.report == second.report
    assert first.attestation == second.attestation


def test_stable_tokens_across_calls(gateway: PseudonymizationGateway):
    a = gateway.scrub("s1", "Lincoln High is proud.", MAYA)
    b = gateway.scrub("s1", "She transferred to Lincoln High last year.", MAYA)
    assert set(TOKEN_RE.findall(a.text)) == set(TOKEN_RE.findall(b.text))
