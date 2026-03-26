import asyncio
import heapq
import itertools
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, Generic, TypeVar


JobId = TypeVar("JobId")


@dataclass(slots=True, frozen=True)
class JobResult:
    next_run_at: datetime | None
    # None means: do not reschedule


@dataclass(slots=True, frozen=True, order=True)
class _HeapEntry(Generic[JobId]):
    run_at_monotonic: float
    token: int
    job_id: JobId


class Scheduler(Generic[JobId]):
    def __init__(
        self,
        runner: Callable[[JobId], Awaitable[JobResult]],
    ) -> None:
        self._runner = runner
        self._heap: list[_HeapEntry[JobId]] = []
        self._scheduled: dict[JobId, tuple[float, int]] = {}
        self._token_counter = itertools.count()
        self._wakeup = asyncio.Event()
        self._running_tasks: set[asyncio.Task[None]] = set()
        self._closed = False

    def schedule_at(self, job_id: JobId, run_at: datetime) -> None:
        run_at = _ensure_utc(run_at)
        delay = max(0.0, (run_at - _utc_now()).total_seconds())
        run_at_monotonic = time.monotonic() + delay
        token = next(self._token_counter)

        self._scheduled[job_id] = (run_at_monotonic, token)
        heapq.heappush(self._heap, _HeapEntry(run_at_monotonic, token, job_id))
        self._wakeup.set()

    def unschedule(self, job_id: JobId) -> None:
        self._scheduled.pop(job_id, None)
        self._wakeup.set()

    def is_scheduled(self, job_id: JobId) -> bool:
        return job_id in self._scheduled

    def get_scheduled_job_ids(self) -> set[JobId]:
        return set(self._scheduled.keys())

    async def run_forever(self) -> None:
        while not self._closed:
            if not self._heap:
                self._wakeup.clear()
                await self._wakeup.wait()
                continue

            next_entry = self._heap[0]
            now_monotonic = time.monotonic()
            sleep_for = next_entry.run_at_monotonic - now_monotonic

            if sleep_for > 0:
                self._wakeup.clear()
                try:
                    await asyncio.wait_for(self._wakeup.wait(), timeout=sleep_for)
                    continue
                except asyncio.TimeoutError:
                    pass

            entry = heapq.heappop(self._heap)

            current = self._scheduled.get(entry.job_id)
            if current != (entry.run_at_monotonic, entry.token):
                continue

            del self._scheduled[entry.job_id]

            task = asyncio.create_task(self._run_one(entry.job_id))
            self._running_tasks.add(task)
            task.add_done_callback(self._running_tasks.discard)

    async def _run_one(self, job_id: JobId) -> None:
        try:
            result = await self._runner(job_id)
        except Exception as exc:
            print(f"Scheduler runner crashed for job_id={job_id}: {exc}")
            return

        if result.next_run_at is not None:
            self.schedule_at(job_id, result.next_run_at)

    async def close(self) -> None:
        self._closed = True
        self._wakeup.set()

        if self._running_tasks:
            await asyncio.gather(*self._running_tasks, return_exceptions=True)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("Datetime must be timezone-aware")
    return dt.astimezone(timezone.utc)
