"""Event bus tests (01-TECH_SPEC §2: at-least-once + idempotency; §10 reliability).

All Redis behavior runs against fakeredis; no network, no wall-clock assertions.
"""

import json
from collections.abc import Callable

import fakeredis
import pytest

from spykt_eventbus import (
    ERROR_MODE_SKIP,
    ESCALATION_QUEUE,
    FABLE_JOB_QUEUE,
    SYSTEM_STREAM,
    EventBus,
    InMemoryDedupeStore,
    dead_letter_stream,
    new_ulid,
    student_stream,
)

GROUP = "workers"
CONSUMER = "worker-1"


@pytest.fixture()
def redis_client():
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture()
def bus(redis_client):
    return EventBus(redis_client)


def collect_handler(seen: list) -> Callable[[str, dict[str, str]], None]:
    def handler(stream: str, fields: dict[str, str]) -> None:
        seen.append((stream, fields))

    return handler


def test_publish_consume_round_trip(bus):
    payload = {"plan_id": "p1", "week": 3}
    event_id = bus.publish("plan.approved", payload, student_id=None)

    seen: list = []
    handled = bus.consume([SYSTEM_STREAM], GROUP, CONSUMER, collect_handler(seen), InMemoryDedupeStore())

    assert handled == 1
    [(stream, fields)] = seen
    assert stream == SYSTEM_STREAM
    assert fields["event_id"] == event_id
    assert fields["type"] == "plan.approved"
    assert json.loads(fields["payload"]) == payload
    assert fields["student_id"] == ""
    assert fields["published_at"]  # server-side timestamp present; value not asserted


def test_per_student_stream_naming_and_ordering(bus, redis_client):
    ids = [bus.publish("task.completed", {"n": n}, student_id="stu-42") for n in range(5)]
    bus.publish("other.event", {}, student_id="stu-99")

    stream = student_stream("stu-42")
    assert stream == "events:student.stu-42"
    assert redis_client.xlen(stream) == 5
    assert redis_client.xlen("events:student.stu-99") == 1
    assert not redis_client.exists(SYSTEM_STREAM)

    seen: list = []
    bus.consume([stream], GROUP, CONSUMER, collect_handler(seen), InMemoryDedupeStore(), count=50)
    assert [f["event_id"] for _, f in seen] == ids  # delivery order == publish order
    assert [json.loads(f["payload"])["n"] for _, f in seen] == [0, 1, 2, 3, 4]
    assert all(f["student_id"] == "stu-42" for _, f in seen)


def test_duplicate_delivery_runs_handler_once(bus, redis_client):
    event_id = bus.publish("cq.fact_added", {"fact": "x"}, student_id="stu-1")
    stream = student_stream("stu-1")
    # Simulate at-least-once duplication: same logical event lands as a second entry.
    original = redis_client.xrange(stream)[0][1]
    redis_client.xadd(stream, original)
    assert redis_client.xlen(stream) == 2

    seen: list = []
    dedupe = InMemoryDedupeStore()
    handled = bus.consume([stream], GROUP, CONSUMER, collect_handler(seen), dedupe, count=10)

    assert handled == 1
    assert len(seen) == 1
    assert seen[0][1]["event_id"] == event_id
    # Duplicate was acked (skipped, not left pending).
    assert redis_client.xpending(stream, GROUP)["pending"] == 0


def test_handler_exception_leaves_entry_pending_and_redelivers(bus, redis_client):
    event_id = bus.publish("sentinel.signal", {"class": 1}, student_id=None)
    dedupe = InMemoryDedupeStore()

    def failing(stream: str, fields: dict[str, str]) -> None:
        raise RuntimeError("boom")

    handled = bus.consume([SYSTEM_STREAM], GROUP, CONSUMER, failing, dedupe)
    assert handled == 0
    assert redis_client.xpending(SYSTEM_STREAM, GROUP)["pending"] == 1  # NOT acked
    assert not dedupe.seen(event_id)  # NOT marked processed

    seen: list = []
    handled = bus.consume([SYSTEM_STREAM], GROUP, CONSUMER, collect_handler(seen), dedupe)
    assert handled == 1
    assert [f["event_id"] for _, f in seen] == [event_id]  # redelivered on next poll
    assert redis_client.xpending(SYSTEM_STREAM, GROUP)["pending"] == 0
    assert dedupe.seen(event_id)


def test_default_block_mode_preserves_order_under_failure(bus, redis_client):
    """error_mode="block" (default): a failed entry halts its stream so per-student
    ordering (01-TECH_SPEC §2) holds — event N+1 is never processed before failed event N."""
    bad = bus.publish("evt", {"which": "bad"}, student_id="stu-ord")
    good = bus.publish("evt", {"which": "good"}, student_id="stu-ord")
    stream = student_stream("stu-ord")
    dedupe = InMemoryDedupeStore()
    attempts: list = []

    def failing_on_bad(s: str, fields: dict[str, str]) -> None:
        attempts.append(fields["event_id"])
        if fields["event_id"] == bad:
            raise ValueError("bad one")

    handled = bus.consume([stream], GROUP, CONSUMER, failing_on_bad, dedupe)
    assert handled == 0
    assert attempts == [bad]  # `good` was NOT processed ahead of the failed earlier event
    assert redis_client.xpending(stream, GROUP)["pending"] == 2  # both retained for retry

    seen: list = []
    handled = bus.consume([stream], GROUP, CONSUMER, collect_handler(seen), dedupe)
    assert handled == 2
    assert [f["event_id"] for _, f in seen] == [bad, good]  # publish order preserved on retry
    assert redis_client.xpending(stream, GROUP)["pending"] == 0


def test_failure_in_one_stream_does_not_block_other_streams(bus, redis_client):
    bad = bus.publish("evt", {"which": "bad"}, student_id="stu-a")
    good = bus.publish("evt", {"which": "good"}, student_id="stu-b")
    seen: list = []

    def handler(stream: str, fields: dict[str, str]) -> None:
        if fields["event_id"] == bad:
            raise ValueError("bad one")
        seen.append(fields["event_id"])

    streams = [student_stream("stu-a"), student_stream("stu-b")]
    handled = bus.consume(streams, GROUP, CONSUMER, handler, InMemoryDedupeStore())
    assert handled == 1
    assert seen == [good]  # ordering hold is per stream, not global
    assert redis_client.xpending(student_stream("stu-a"), GROUP)["pending"] == 1


def test_skip_mode_does_not_block_other_entries(redis_client):
    """error_mode="skip" (explicit opt-in, order-insensitive consumers only): a failed
    entry is skipped and retried next poll while later entries proceed."""
    bus = EventBus(redis_client, error_mode=ERROR_MODE_SKIP)
    bad = bus.publish("evt", {"which": "bad"})
    good = bus.publish("evt", {"which": "good"})
    seen: list = []

    def handler(stream: str, fields: dict[str, str]) -> None:
        if fields["event_id"] == bad:
            raise ValueError("bad one")
        seen.append(fields["event_id"])

    handled = bus.consume([SYSTEM_STREAM], GROUP, CONSUMER, handler, InMemoryDedupeStore())
    assert handled == 1
    assert seen == [good]
    assert redis_client.xpending(SYSTEM_STREAM, GROUP)["pending"] == 1


def test_queue_helpers_land_on_right_streams(bus, redis_client):
    esc_id = bus.push_escalation({"class": 2, "student": "stu-7"})
    job_id = bus.push_fable_job({"agent": "A2", "kind": "genome_scoring"})

    [(_, esc_fields)] = redis_client.xrange(ESCALATION_QUEUE)
    assert esc_fields["event_id"] == esc_id
    assert json.loads(esc_fields["payload"]) == {"class": 2, "student": "stu-7"}
    assert esc_fields["published_at"]

    [(_, job_fields)] = redis_client.xrange(FABLE_JOB_QUEUE)
    assert job_fields["event_id"] == job_id
    assert json.loads(job_fields["payload"]) == {"agent": "A2", "kind": "genome_scoring"}

    assert redis_client.xlen(ESCALATION_QUEUE) == 1
    assert redis_client.xlen(FABLE_JOB_QUEUE) == 1


def test_queues_consumable_with_consumer_groups(bus):
    bus.push_fable_job({"agent": "A3"})
    seen: list = []
    handled = bus.consume([FABLE_JOB_QUEUE], "fable-workers", "w1", collect_handler(seen), InMemoryDedupeStore())
    assert handled == 1
    assert json.loads(seen[0][1]["payload"]) == {"agent": "A3"}


def test_ulids_lexicographically_monotonic_in_sequence():
    ids = [new_ulid() for _ in range(200)]
    assert ids == sorted(ids)
    assert len(set(ids)) == 200


def test_published_event_ids_monotonic(bus):
    ids = [bus.publish("evt", {"i": i}, student_id="stu-m") for i in range(50)]
    assert ids == sorted(ids)
    assert len(set(ids)) == 50


def test_consume_is_single_poll_no_loop(bus):
    """consume() returns after one poll even when the stream stays empty."""
    handled = bus.consume([SYSTEM_STREAM], GROUP, CONSUMER, lambda s, f: None, InMemoryDedupeStore())
    assert handled == 0


# -- client validation (idempotency must not silently degrade) ---------------


def test_bytes_client_rejected_at_construction():
    """redis-py defaults to decode_responses=False; with a bytes client the event_id
    lookup would miss and dedupe would silently fall back to Redis entry ids."""
    with pytest.raises(ValueError, match="decode_responses=True"):
        EventBus(fakeredis.FakeRedis())  # no decode_responses


def test_invalid_error_mode_rejected():
    with pytest.raises(ValueError, match="error_mode"):
        EventBus(fakeredis.FakeRedis(decode_responses=True), error_mode="explode")


def test_invalid_max_deliveries_rejected():
    with pytest.raises(ValueError, match="max_deliveries"):
        EventBus(fakeredis.FakeRedis(decode_responses=True), max_deliveries=0)


# -- producer-side idempotency (caller-supplied event_id) --------------------


def test_publish_accepts_caller_supplied_event_id(bus, redis_client):
    eid = new_ulid()
    assert bus.publish("evt", {"x": 1}, student_id="stu-1", event_id=eid) == eid
    [(_, fields)] = redis_client.xrange(student_stream("stu-1"))
    assert fields["event_id"] == eid


def test_queue_helpers_accept_caller_supplied_event_id(bus, redis_client):
    esc_eid, job_eid = new_ulid(), new_ulid()
    assert bus.push_escalation({"class": 2}, event_id=esc_eid) == esc_eid
    assert bus.push_fable_job({"agent": "A2"}, event_id=job_eid) == job_eid
    assert redis_client.xrange(ESCALATION_QUEUE)[0][1]["event_id"] == esc_eid
    assert redis_client.xrange(FABLE_JOB_QUEUE)[0][1]["event_id"] == job_eid


def test_producer_retry_with_same_event_id_deduped_on_consume(bus, redis_client):
    """A producer retrying an ambiguous XADD reuses its event_id; the consumer
    collapses the resulting duplicate entries via the DedupeStore."""
    eid = new_ulid()
    bus.publish("evt", {"x": 1}, student_id="stu-r", event_id=eid)
    bus.publish("evt", {"x": 1}, student_id="stu-r", event_id=eid)  # retry after lost response
    stream = student_stream("stu-r")
    assert redis_client.xlen(stream) == 2

    seen: list = []
    handled = bus.consume([stream], GROUP, CONSUMER, collect_handler(seen), InMemoryDedupeStore())
    assert handled == 1
    assert len(seen) == 1
    assert redis_client.xpending(stream, GROUP)["pending"] == 0


# -- dead/renamed consumer recovery (§10: survives worker restarts) ----------


def test_stale_pending_entry_reclaimed_by_new_consumer_name(redis_client):
    """A worker that crashed after delivery but before XACK and restarted under a
    NEW consumer name (container redeploy) must not strand the entry in the old PEL."""
    bus = EventBus(redis_client, claim_idle_ms=0)  # 0 so the test needs no clock control
    event_id = bus.publish("plan.approved", {"week": 1}, student_id=None)
    dedupe = InMemoryDedupeStore()

    def crashing(stream: str, fields: dict[str, str]) -> None:
        raise RuntimeError("worker died before ack")

    assert bus.consume([SYSTEM_STREAM], GROUP, "worker-old", crashing, dedupe) == 0
    assert redis_client.xpending(SYSTEM_STREAM, GROUP)["pending"] == 1  # stranded with worker-old

    seen: list = []
    handled = bus.consume([SYSTEM_STREAM], GROUP, "worker-new", collect_handler(seen), dedupe)
    assert handled == 1
    assert [f["event_id"] for _, f in seen] == [event_id]
    assert redis_client.xpending(SYSTEM_STREAM, GROUP)["pending"] == 0


def test_claim_disabled_leaves_other_consumers_pel_alone(redis_client):
    bus = EventBus(redis_client, claim_idle_ms=None)
    bus.publish("evt", {}, student_id=None)
    dedupe = InMemoryDedupeStore()

    def crashing(stream: str, fields: dict[str, str]) -> None:
        raise RuntimeError("boom")

    bus.consume([SYSTEM_STREAM], GROUP, "worker-old", crashing, dedupe)
    handled = bus.consume([SYSTEM_STREAM], GROUP, "worker-new", lambda s, f: None, dedupe)
    assert handled == 0  # no claim path: only the same consumer name would retry it
    assert redis_client.xpending(SYSTEM_STREAM, GROUP)["pending"] == 1


# -- poison entries → dead-letter stream --------------------------------------


def test_poison_entry_dead_lettered_after_max_deliveries(redis_client):
    bus = EventBus(redis_client, max_deliveries=2)
    event_id = bus.publish("evt", {"poison": True}, student_id="stu-p")
    stream = student_stream("stu-p")
    dedupe = InMemoryDedupeStore()
    attempts = 0

    def always_failing(s: str, fields: dict[str, str]) -> None:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("permanently broken")

    assert bus.consume([stream], GROUP, CONSUMER, always_failing, dedupe) == 0  # attempt 1: retry
    assert bus.consume([stream], GROUP, CONSUMER, always_failing, dedupe) == 0  # attempt 2: → DLQ
    assert attempts == 2
    assert bus.consume([stream], GROUP, CONSUMER, always_failing, dedupe) == 0  # nothing left
    assert attempts == 2  # handler NOT called past max_deliveries failures

    assert redis_client.xpending(stream, GROUP)["pending"] == 0  # acked out of the PEL
    [(_, dlq_fields)] = redis_client.xrange(dead_letter_stream(stream))
    assert dlq_fields["event_id"] == event_id  # original fields preserved for replay
    assert dlq_fields["source_stream"] == stream
    assert dlq_fields["reason"] == "max_deliveries_exceeded"
    assert dlq_fields["deliveries"] == "2"
    assert dlq_fields["source_entry_id"]
    assert dlq_fields["dead_lettered_at"]
    assert not dedupe.seen(event_id)  # never processed → never marked; replay runs for real


def test_dead_letter_unblocks_stream_in_block_mode(redis_client):
    """The ordering hold (error_mode=block) plus the DLQ cap means a poison entry
    delays, but cannot permanently block, a student's stream. The held `good` entry
    accrues Redis delivery counts while blocked but is never dead-lettered — the cap
    applies only to entries whose handler actually fails."""
    bus = EventBus(redis_client, max_deliveries=2)
    bad = bus.publish("evt", {"which": "bad"}, student_id="stu-u")
    good = bus.publish("evt", {"which": "good"}, student_id="stu-u")
    stream = student_stream("stu-u")
    dedupe = InMemoryDedupeStore()
    seen: list = []

    def handler(s: str, fields: dict[str, str]) -> None:
        if fields["event_id"] == bad:
            raise RuntimeError("permanently broken")
        seen.append(fields["event_id"])

    assert bus.consume([stream], GROUP, CONSUMER, handler, dedupe) == 0  # bad fails (1/2), good held
    assert seen == []
    handled = bus.consume([stream], GROUP, CONSUMER, handler, dedupe)  # bad fails (2/2) → DLQ; good runs
    assert handled == 1
    assert seen == [good]
    assert redis_client.xpending(stream, GROUP)["pending"] == 0
    assert redis_client.xlen(dead_letter_stream(stream)) == 1


def test_max_deliveries_none_retries_forever(redis_client):
    bus = EventBus(redis_client, max_deliveries=None)
    bus.publish("evt", {}, student_id=None)
    dedupe = InMemoryDedupeStore()
    attempts = 0

    def always_failing(s: str, fields: dict[str, str]) -> None:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("boom")

    for _ in range(4):
        bus.consume([SYSTEM_STREAM], GROUP, CONSUMER, always_failing, dedupe)
    assert attempts == 4
    assert redis_client.xpending(SYSTEM_STREAM, GROUP)["pending"] == 1
    assert not redis_client.exists(dead_letter_stream(SYSTEM_STREAM))


def test_entry_without_event_id_dead_lettered_not_handled(bus, redis_client):
    """A foreign entry with no event_id cannot be deduped; running the handler would
    break idempotency, so it is dead-lettered immediately instead of guessing a key."""
    redis_client.xadd(SYSTEM_STREAM, {"payload": "{}"})
    seen: list = []
    handled = bus.consume([SYSTEM_STREAM], GROUP, CONSUMER, collect_handler(seen), InMemoryDedupeStore())
    assert handled == 0
    assert seen == []  # handler never ran
    assert redis_client.xpending(SYSTEM_STREAM, GROUP)["pending"] == 0
    [(_, dlq_fields)] = redis_client.xrange(dead_letter_stream(SYSTEM_STREAM))
    assert dlq_fields["reason"] == "missing_event_id"
    assert dlq_fields["payload"] == "{}"
