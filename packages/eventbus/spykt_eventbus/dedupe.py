"""Idempotency stores for at-least-once delivery (01-TECH_SPEC §2, §10).

The bus delivers each event at least once; handlers stay idempotent by checking
a DedupeStore keyed on the event's ULID `event_id` before doing work. The
in-memory implementation below is for tests and single-process workers; the
durable implementation backed by the Postgres `events` table (01-TECH_SPEC §3,
control plane) arrives with the integration wiring.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class DedupeStore(Protocol):
    """Records which event_ids a consumer group has fully processed."""

    def seen(self, event_id: str) -> bool:
        """Return True if `event_id` was already processed successfully."""
        ...

    def mark(self, event_id: str) -> None:
        """Record `event_id` as processed. Called only after the handler succeeds."""
        ...


class InMemoryDedupeStore:
    """Process-local DedupeStore.

    Suitable for tests and single-process workers only: state is lost on
    restart, so cross-restart idempotency requires the Postgres `events`
    table implementation (ships with integration wiring).
    """

    def __init__(self) -> None:
        self._processed: set[str] = set()

    def seen(self, event_id: str) -> bool:
        return event_id in self._processed

    def mark(self, event_id: str) -> None:
        self._processed.add(event_id)
