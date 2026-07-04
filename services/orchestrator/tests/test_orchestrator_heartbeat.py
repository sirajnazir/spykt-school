import asyncio

from spykt_orchestrator.main import heartbeat_loop


def test_heartbeat_runs_and_stops():
    async def run():
        stop = asyncio.Event()

        async def stopper():
            await asyncio.sleep(0.01)
            stop.set()

        ticks, _ = await asyncio.gather(heartbeat_loop(stop), stopper())
        return ticks

    assert asyncio.run(run()) >= 1
