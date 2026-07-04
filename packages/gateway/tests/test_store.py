import re

import pytest

from spykt_gateway import InMemoryPseudonymStore, TokenCollisionError


def test_get_returns_none_before_create(store: InMemoryPseudonymStore):
    assert store.get("s1") is None


def test_create_returns_random_pseudonym_and_salt(store: InMemoryPseudonymStore):
    record = store.create("s1")
    assert record.student_id == "s1"
    assert re.fullmatch(r"Student-[0-9a-f]{8}", record.pseudonym)
    assert re.fullmatch(r"[0-9a-f]{32}", record.salt)
    assert store.get("s1") == record


def test_create_is_idempotent(store: InMemoryPseudonymStore):
    assert store.create("s1") == store.create("s1")


def test_distinct_students_get_distinct_identities(store: InMemoryPseudonymStore):
    a, b = store.create("s1"), store.create("s2")
    assert a.pseudonym != b.pseudonym
    assert a.salt != b.salt


def test_token_mapping_roundtrip_is_per_student(store: InMemoryPseudonymStore):
    store.save_token_mapping("s1", "PII_NAME_deadbeef", "Maya")
    assert store.lookup_token("s1", "PII_NAME_deadbeef") == "Maya"
    assert store.lookup_token("s2", "PII_NAME_deadbeef") is None
    assert store.lookup_token("s1", "PII_NAME_00000000") is None


def test_token_collision_refuses_instead_of_overwriting(store: InMemoryPseudonymStore):
    # 32-bit truncation means two distinct originals can derive the same token;
    # silent last-write-wins would corrupt restore(), so the store raises loudly.
    store.save_token_mapping("s1", "PII_NAME_deadbeef", "Maya")
    with pytest.raises(TokenCollisionError):
        store.save_token_mapping("s1", "PII_NAME_deadbeef", "Jonathan")
    assert store.lookup_token("s1", "PII_NAME_deadbeef") == "Maya"
    # Same token for a different student is not a collision.
    store.save_token_mapping("s2", "PII_NAME_deadbeef", "Jonathan")
    assert store.lookup_token("s2", "PII_NAME_deadbeef") == "Jonathan"


def test_token_remapping_same_original_is_idempotent(store: InMemoryPseudonymStore):
    store.save_token_mapping("s1", "PII_NAME_deadbeef", "Maya")
    store.save_token_mapping("s1", "PII_NAME_deadbeef", "Maya")
    assert store.lookup_token("s1", "PII_NAME_deadbeef") == "Maya"


def test_case_variant_original_keeps_first_seen_surface_form(store: InMemoryPseudonymStore):
    # derive_token() lowercases, so case variants share one token; the first-seen
    # surface form is the canonical original (restore is canonicalizing, not byte-exact).
    store.save_token_mapping("s1", "PII_NAME_deadbeef", "Maya")
    store.save_token_mapping("s1", "PII_NAME_deadbeef", "MAYA")
    assert store.lookup_token("s1", "PII_NAME_deadbeef") == "Maya"
