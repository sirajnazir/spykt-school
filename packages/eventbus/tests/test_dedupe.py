from spykt_eventbus import DedupeStore, InMemoryDedupeStore


def test_in_memory_store_seen_and_mark():
    store = InMemoryDedupeStore()
    assert not store.seen("01ARZ3NDEKTSV4RRFFQ69G5FAV")
    store.mark("01ARZ3NDEKTSV4RRFFQ69G5FAV")
    assert store.seen("01ARZ3NDEKTSV4RRFFQ69G5FAV")
    assert not store.seen("01ARZ3NDEKTSV4RRFFQ69G5FAW")


def test_mark_is_idempotent():
    store = InMemoryDedupeStore()
    store.mark("abc")
    store.mark("abc")
    assert store.seen("abc")


def test_in_memory_store_satisfies_protocol():
    # DedupeStore is @runtime_checkable, so this isinstance actually checks conformance.
    assert isinstance(InMemoryDedupeStore(), DedupeStore)


def test_non_conforming_object_fails_protocol():
    class NotAStore:
        def seen(self, event_id: str) -> bool:  # mark() missing
            return False

    assert not isinstance(NotAStore(), DedupeStore)
