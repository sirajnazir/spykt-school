"""Specialist worker pool — Phase 0 hello-world skeleton.

Phase 1+ hosts specialists as asyncio tasks consuming the event bus.
Every specialist will be `run(SpecialistInput) -> SpecialistOutput` per
01-TECH_SPEC §5, called only through packages/anthropic-client.
"""

import asyncio
import logging
import signal

logger = logging.getLogger("spykt.workers")

HEARTBEAT_SECONDS = 30


async def heartbeat_loop(stop: asyncio.Event) -> int:
    ticks = 0
    while not stop.is_set():
        ticks += 1
        logger.info("workers heartbeat tick=%d", ticks)
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
