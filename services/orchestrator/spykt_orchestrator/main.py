"""Orchestrator worker — Phase 0 hello-world skeleton.

Phase 1 replaces the heartbeat loop with the Redis Streams consumer
(events:student.{id}, events:system) + idempotent dispatch. The weekly-cycle
state machine (PLAN_DRAFT → … → CLOSED) lands in Phase 3.
"""

import asyncio
import logging
import signal

logger = logging.getLogger("spykt.orchestrator")

HEARTBEAT_SECONDS = 30


async def heartbeat_loop(stop: asyncio.Event) -> int:
    """Logs a heartbeat until stopped. Returns tick count (used by tests)."""
    ticks = 0
    while not stop.is_set():
        ticks += 1
        logger.info("orchestrator heartbeat tick=%d", ticks)
        try:
            await asyncio.wait_for(stop.wait(), timeout=HEARTBEAT_SECONDS)
        except TimeoutError:
            pass
    return ticks


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    stop = asyncio.Event()
    loop = asyncio.new_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    loop.run_until_complete(heartbeat_loop(stop))


if __name__ == "__main__":
    main()
