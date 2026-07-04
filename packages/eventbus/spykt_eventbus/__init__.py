"""Event bus + idempotency (01-TECH_SPEC §2, §10).

Redis Streams with consumer groups: at-least-once delivery, ULID event ids,
per-student ordering streams, and DedupeStore-based idempotent handling.
"""

from spykt_eventbus.bus import (
    DEAD_LETTER_PREFIX,
    ERROR_MODE_BLOCK,
    ERROR_MODE_SKIP,
    ESCALATION_QUEUE,
    FABLE_JOB_QUEUE,
    STUDENT_STREAM_PREFIX,
    SYSTEM_STREAM,
    EventBus,
    dead_letter_stream,
    new_ulid,
    student_stream,
)
from spykt_eventbus.dedupe import DedupeStore, InMemoryDedupeStore

__all__ = [
    "DEAD_LETTER_PREFIX",
    "ERROR_MODE_BLOCK",
    "ERROR_MODE_SKIP",
    "ESCALATION_QUEUE",
    "FABLE_JOB_QUEUE",
    "STUDENT_STREAM_PREFIX",
    "SYSTEM_STREAM",
    "DedupeStore",
    "EventBus",
    "InMemoryDedupeStore",
    "dead_letter_stream",
    "new_ulid",
    "student_stream",
]
