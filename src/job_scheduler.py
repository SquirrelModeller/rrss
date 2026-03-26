import asyncio
import heapq
import time
import random
from dataclasses import dataclass, field
from typing import Awaitable, Callable


@dataclass(order=True)
class ScheduledJob:
    run_at: float
    name: str = field(compare=False)
    coro_factory: Callable[[], Awaitable[float]] = field(compare=False)


class JobScheduler:
    def __init__(self):
        self._heap: list[ScheduledJob] = []
        self._wakeup = asyncio.Event()
        self._running_tasks: set[asyncio.Task] = set()

    def schedule_in(
        self, delay: float, name: str, coro_factory: Callable[[], Awaitable[float]]
    ):
        run_at = time.monotonic() + delay
        heapq.heappush(self._heap, ScheduledJob(run_at, name, coro_factory))
        self._wakeup.set()

    async def run_forever(self):
        while True:
            if not self._heap:
                self._wakeup.clear()
                await self._wakeup.wait()
                continue

            next_job = self._heap[0]
            now = time.monotonic()
            sleep_for = next_job.run_at - now

            if sleep_for > 0:
                self._wakeup.clear()
                try:
                    await asyncio.wait_for(self._wakeup.wait(), timeout=sleep_for)
                    continue
                except asyncio.TimeoutError:
                    pass

            job = heapq.heappop(self._heap)
            task = asyncio.create_task(self._run_job(job))
            self._running_tasks.add(task)
            task.add_done_callback(self._running_tasks.discard)

    async def _run_job(self, job: ScheduledJob):
        try:
            next_delay = await job.coro_factory()
        except Exception as e:
            print(f"[{job.name}] failed: {e}")
            next_delay = 60

        print(f"[{job.name}] scheduling next run in {next_delay:.1f}s")
        self.schedule_in(next_delay, job.name, job.coro_factory)
