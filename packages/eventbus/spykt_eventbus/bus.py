"""Event bus on Redis Streams (01-TECH_SPEC §2; reliability in §10).

Streams:
- ``events:student.{id}`` — per-student ordering
- ``events:system``       — events with no student scope
- ``queue:coach_escalations`` / ``queue:fable_jobs`` — work queues
- ``dlq:{stream}``        — dead letters (poison/malformed entries, preserved for replay)

Delivery is at-least-once via consumer groups (XGROUP/XREADGROUP/XACK);
idempotency comes from ULID ``event_id`` keys checked against a DedupeStore
before the handler runs. Handlers that raise are NOT acked, so the entry stays
pending and is retried.

Reliability mechanics (§10 "at-least-once bus … survives worker restarts"):

- The redis client MUST be constructed with ``decode_responses=True``; the
  constructor enforces this. With a bytes client the ``event_id`` field lookup
  would silently miss and idempotency would degrade to Redis entry ids.
- Entries stranded in another consumer's PEL (worker crashed after delivery,
  restarted under a new consumer name — e.g. a container redeploy) are
  reclaimed with XAUTOCLAIM once idle longer than ``claim_idle_ms``. Stable
  consumer names still give the fastest redelivery; the claim path is the
  safety net, not the primary mechanism.
- ``error_mode`` picks the failure semantics per stream (see ``EventBus``):
  ``"block"`` (default) preserves per-student ordering by halting the stream at
  the failed entry; ``"skip"`` trades ordering for throughput.
- After ``max_deliveries`` failed attempts an entry is moved to
  ``dlq:{stream}`` (fields preserved, plus provenance) and acked, so a poison
  entry cannot block a student's stream or burn commands forever. Entries with
  no ``event_id`` field (foreign producer) are dead-lettered immediately —
  they cannot be deduped, so processing them would break idempotency.
"""

import enum
import json
import logging
import threading
from collections.abc import Callable, Sequence
from typing import Any

import redis
from ulid import ULID

from spykt_eventbus.dedupe import DedupeStore

logger = logging.getLogger(__name__)

SYSTEM_STREAM = "events:system"
STUDENT_STREAM_PREFIX = "events:student."
ESCALATION_QUEUE = "queue:coach_escalations"
FABLE_JOB_QUEUE = "queue:fable_jobs"
DEAD_LETTER_PREFIX = "dlq:"

ERROR_MODE_BLOCK = "block"  # per-student ordering preserved: halt the stream at a failed entry
ERROR_MODE_SKIP = "skip"  # throughput over ordering: continue past a failed entry
_ERROR_MODES = (ERROR_MODE_BLOCK, ERROR_MODE_SKIP)

# Handler receives (stream_name, entry_fields). `entry_fields["payload"]` is the JSON string.
Handler = Callable[[str, dict[str, str]], None]

_ulid_lock = threading.Lock()
_last_ulid_int: int | None = None


class _Outcome(enum.Enum):
    HANDLED = "handled"  # handler ran and succeeded; acked + marked
    SKIPPED = "skipped"  # duplicate or dead-lettered; acked without running the handler
    FAILED = "failed"  # handler raised; left pending for retry


def new_ulid() -> str:
    """Return a ULID string, strictly lexicographically monotonic within this process.

    python-ulid guarantees millisecond ordering but not intra-millisecond
    ordering, so we bump the random component when a fresh ULID would not sort
    after the previous one (same trick as the reference monotonic generators).
    """
    global _last_ulid_int
    with _ulid_lock:
        candidate = int.from_bytes(ULID().bytes, "big")
        if _last_ulid_int is not None and candidate <= _last_ulid_int:
            candidate = _last_ulid_int + 1
        _last_ulid_int = candidate
        return str(ULID.from_bytes(candidate.to_bytes(16, "big")))


def student_stream(student_id: str) -> str:
    """Stream name carrying per-student ordering (01-TECH_SPEC §2)."""
    return f"{STUDENT_STREAM_PREFIX}{student_id}"


def dead_letter_stream(stream: str) -> str:
    """Dead-letter stream paired with `stream` (poison/malformed entries, kept for replay)."""
    return f"{DEAD_LETTER_PREFIX}{stream}"


def _require_decoded_client(client: redis.Redis) -> None:
    """Reject clients that return bytes: idempotency is keyed on the str `event_id` field.

    redis-py defaults to ``decode_responses=False``; with a bytes client the
    ``fields["event_id"]`` lookup would miss (keys are ``b"event_id"``) and
    dedupe would silently degrade. Fail loudly at construction instead.
    """
    get_kwargs = getattr(client, "get_connection_kwargs", None)
    if get_kwargs is not None:
        decode = get_kwargs().get("decode_responses", False)
    else:  # client without get_connection_kwargs (e.g. cluster): read the pool directly
        pool = getattr(client, "connection_pool", None)
        decode = getattr(pool, "connection_kwargs", {}).get("decode_responses", False)
    if not decode:
        raise ValueError(
            "EventBus requires a redis client constructed with decode_responses=True; "
            "with a bytes client the event_id field lookup misses and idempotency "
            "silently degrades to Redis entry ids (01-TECH_SPEC §2)."
        )


class EventBus:
    """Thin, boring wrapper over Redis Streams. All calls are synchronous.

    Args:
        redis_client: must be constructed with ``decode_responses=True`` (enforced).
        error_mode: what happens to the rest of a stream when a handler raises.
            ``"block"`` (default) stops processing that stream for the poll so a
            later event is never applied before an earlier failed one —
            required for order-sensitive consumers of ``events:student.{id}``
            (orchestrator transitions §6, cq_facts supersession chains §3).
            ``"skip"`` continues to later entries (failed one retries next
            poll); only safe for order-insensitive consumers.
        max_deliveries: an entry whose handler fails while its delivery count
            is at or past this cap is moved to ``dlq:{stream}`` and acked.
            Checked only when the handler actually raises, so entries that were
            merely held by an ordering block (which inflates Redis delivery
            counts without handler attempts) are never dead-lettered. ``None``
            disables dead-lettering (poison entries then retry forever).
        claim_idle_ms: pending entries owned by ANY consumer in the group and
            idle at least this long are reclaimed via XAUTOCLAIM on each poll,
            so a worker that crashed after delivery but before XACK (and
            restarted under a new consumer name) cannot strand an entry.
            ``None`` disables reclaiming — only safe with stable consumer names.
    """

    def __init__(
        self,
        redis_client: redis.Redis,
        *,
        error_mode: str = ERROR_MODE_BLOCK,
        max_deliveries: int | None = 5,
        claim_idle_ms: int | None = 60_000,
    ) -> None:
        _require_decoded_client(redis_client)
        if error_mode not in _ERROR_MODES:
            raise ValueError(f"error_mode must be one of {_ERROR_MODES}, got {error_mode!r}")
        if max_deliveries is not None and max_deliveries < 1:
            raise ValueError("max_deliveries must be >= 1 or None")
        self._redis = redis_client
        self._error_mode = error_mode
        self._max_deliveries = max_deliveries
        self._claim_idle_ms = claim_idle_ms

    # -- publishing ---------------------------------------------------------

    def publish(
        self,
        event_type: str,
        payload: dict[str, Any],
        student_id: str | None = None,
        *,
        event_id: str | None = None,
    ) -> str:
        """Publish an event; returns its ULID event_id.

        Events for a student go to ``events:student.{id}`` so per-student
        ordering holds; unscoped events go to ``events:system``.
        `published_at` is server-side (Redis TIME), so tests never depend on
        local wall-clock.

        Producers that may retry after an ambiguous XADD failure (request
        landed, response lost) should mint `event_id` up front and pass it on
        every attempt: the DedupeStore then collapses the duplicate entries on
        the consumer side. Omitted, a fresh ULID is minted per call.
        """
        stream = student_stream(student_id) if student_id is not None else SYSTEM_STREAM
        event_id = event_id or new_ulid()
        self._redis.xadd(
            stream,
            {
                "event_id": event_id,
                "type": event_type,
                "payload": json.dumps(payload),
                "student_id": student_id or "",
                "published_at": self._server_time(),
            },
        )
        return event_id

    def push_escalation(self, payload: dict[str, Any], *, event_id: str | None = None) -> str:
        """Queue a coach escalation on ``queue:coach_escalations``; returns its event_id.

        Pass `event_id` when retrying an ambiguous push (see `publish`).
        """
        return self._push_queue(ESCALATION_QUEUE, payload, event_id)

    def push_fable_job(self, payload: dict[str, Any], *, event_id: str | None = None) -> str:
        """Queue a Fable job on ``queue:fable_jobs``; returns its event_id.

        Pass `event_id` when retrying an ambiguous push (see `publish`).
        """
        return self._push_queue(FABLE_JOB_QUEUE, payload, event_id)

    def _push_queue(self, queue: str, payload: dict[str, Any], event_id: str | None) -> str:
        event_id = event_id or new_ulid()
        self._redis.xadd(
            queue,
            {
                "event_id": event_id,
                "payload": json.dumps(payload),
                "published_at": self._server_time(),
            },
        )
        return event_id

    def _server_time(self) -> str:
        seconds, microseconds = self._redis.time()
        return f"{seconds}.{microseconds:06d}"

    # -- consuming ----------------------------------------------------------

    def consume(
        self,
        streams: Sequence[str],
        group: str,
        consumer: str,
        handler: Handler,
        dedupe: DedupeStore,
        block_ms: int | None = None,
        count: int = 10,
    ) -> int:
        """Run one delivery poll for `consumer` in `group`; returns entries handled.

        Poll-style by design (no internal loop): production workers call this
        in their own loop with `block_ms` set; tests call it once per step.
        Each poll processes, in order:

        1. this consumer's own pending (delivered-but-unacked) entries,
        2. entries reclaimed from other consumers' PELs once idle longer than
           ``claim_idle_ms`` (dead/renamed consumer recovery),
        3. new deliveries.

        Per entry: if ``dedupe.seen(event_id)`` the handler is skipped and the
        entry acked (duplicate delivery); otherwise the handler runs, then
        ``dedupe.mark(event_id)`` and XACK. A raising handler is logged and the
        entry left un-acked for retry — never acked, never marked — and in
        ``error_mode="block"`` (default) the rest of that stream is left
        pending for this poll so per-student ordering holds. An entry whose
        handler fails for the ``max_deliveries``-th time moves to
        ``dlq:{stream}`` instead of retrying forever.
        """
        self._ensure_groups(streams, group)
        handled = 0
        blocked: set[str] = set()  # streams halted this poll after a handler failure (error_mode=block)

        # 1) Retry this consumer's own PEL.
        response = self._redis.xreadgroup(group, consumer, dict.fromkeys(streams, "0"), count=count)
        for stream, entries in response or []:
            handled += self._process(stream, group, consumer, entries, handler, dedupe, blocked, retry=True)

        # 2) Reclaim entries stranded with dead/renamed consumers.
        if self._claim_idle_ms is not None:
            for stream in streams:
                entries = self._claim_stale(stream, group, consumer, count)
                handled += self._process(
                    stream, group, consumer, entries, handler, dedupe, blocked, retry=True
                )

        # 3) New deliveries.
        response = self._redis.xreadgroup(
            group, consumer, dict.fromkeys(streams, ">"), count=count, block=block_ms
        )
        for stream, entries in response or []:
            handled += self._process(stream, group, consumer, entries, handler, dedupe, blocked, retry=False)
        return handled

    def _claim_stale(self, stream: str, group: str, consumer: str, count: int) -> list[tuple]:
        """XAUTOCLAIM entries idle >= claim_idle_ms into this consumer's PEL."""
        response = self._redis.xautoclaim(
            stream, group, consumer, min_idle_time=self._claim_idle_ms, start_id="0-0", count=count
        )
        entries = response[1] if len(response) > 1 else []
        # Redis 6.2 reports entries deleted from the stream as (id, None); drop them.
        return [(entry_id, fields) for entry_id, fields in entries if fields is not None]

    def _process(
        self,
        stream: str,
        group: str,
        consumer: str,
        entries: Sequence[tuple],
        handler: Handler,
        dedupe: DedupeStore,
        blocked: set[str],
        retry: bool,
    ) -> int:
        """Handle one batch from one stream; returns entries handled successfully."""
        if not entries:
            return 0
        counts: dict[str, int] = {}
        if retry and self._max_deliveries is not None:
            counts = self._delivery_counts(stream, group, consumer, entries)
        handled = 0
        for entry_id, fields in entries:
            if stream in blocked:
                # Ordering hold: everything after a failed entry stays pending for the next poll.
                break
            outcome = self._handle_entry(stream, group, entry_id, fields, handler, dedupe)
            if outcome is _Outcome.HANDLED:
                handled += 1
            elif outcome is _Outcome.FAILED:
                # Dead-letter only at ACTUAL failure time: XPENDING times_delivered is
                # inflated by ordering holds and claims (deliveries without handler
                # attempts), so an entry that was merely held is never dead-lettered —
                # it must run and raise while at/over the cap.
                deliveries = counts.get(entry_id, 1)
                if self._max_deliveries is not None and deliveries >= self._max_deliveries:
                    self._dead_letter(stream, group, entry_id, fields, "max_deliveries_exceeded", deliveries)
                    continue  # entry resolved (to the DLQ); do not hold the stream on it
                if self._error_mode == ERROR_MODE_BLOCK:
                    blocked.add(stream)
        return handled

    def _delivery_counts(
        self, stream: str, group: str, consumer: str, entries: Sequence[tuple]
    ) -> dict[str, int]:
        pending = self._redis.xpending_range(
            stream, group, min=entries[0][0], max=entries[-1][0], count=len(entries), consumername=consumer
        )
        return {info["message_id"]: info["times_delivered"] for info in pending}

    def _handle_entry(
        self,
        stream: str,
        group: str,
        entry_id: str,
        fields: dict[str, str],
        handler: Handler,
        dedupe: DedupeStore,
    ) -> _Outcome:
        event_id = fields.get("event_id")
        if event_id is None:
            # No idempotency key: running the handler would break dedupe guarantees.
            # Dead-letter (preserved for inspection/replay) rather than guess a key.
            self._dead_letter(stream, group, entry_id, fields, "missing_event_id", deliveries=1)
            return _Outcome.SKIPPED
        if dedupe.seen(event_id):
            # Duplicate delivery: idempotency skip, but ack so it leaves the PEL.
            logger.info("eventbus: duplicate event %s on %s skipped", event_id, stream)
            self._redis.xack(stream, group, entry_id)
            return _Outcome.SKIPPED
        try:
            handler(stream, fields)
        except Exception:
            # At-least-once: no ack, entry stays pending and is retried.
            logger.exception("eventbus: handler failed for event %s on %s; will retry", event_id, stream)
            return _Outcome.FAILED
        dedupe.mark(event_id)
        self._redis.xack(stream, group, entry_id)
        return _Outcome.HANDLED

    def _dead_letter(
        self,
        stream: str,
        group: str,
        entry_id: str,
        fields: dict[str, str],
        reason: str,
        deliveries: int,
    ) -> None:
        """Move a poison/malformed entry to ``dlq:{stream}`` and ack the original.

        Fields are preserved verbatim plus provenance, so the Phase 5 "bus
        backlog" runbook can inspect and replay. The event is NOT marked in the
        DedupeStore — it was never processed, and a replay must run for real.
        """
        self._redis.xadd(
            dead_letter_stream(stream),
            {
                **fields,
                "source_stream": stream,
                "source_entry_id": entry_id,
                "reason": reason,
                "deliveries": str(deliveries),
                "dead_lettered_at": self._server_time(),
            },
        )
        self._redis.xack(stream, group, entry_id)
        logger.error(
            "eventbus: dead-lettered entry %s from %s (reason=%s deliveries=%d event_id=%s)",
            entry_id,
            stream,
            reason,
            deliveries,
            fields.get("event_id", "<missing>"),
        )

    def _ensure_groups(self, streams: Sequence[str], group: str) -> None:
        for stream in streams:
            try:
                self._redis.xgroup_create(stream, group, id="0", mkstream=True)
            except redis.ResponseError as exc:
                if "BUSYGROUP" not in str(exc):
                    raise
