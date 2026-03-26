from datetime import datetime, timedelta, timezone

from database import database
import pull
from models import Feed
from scheduler import JobResult
import aiohttp


class FeedJobRunner:
    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        if self._session is None:
            self._session = aiohttp.ClientSession()

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def run(self, feed_id: int) -> JobResult:
        if self._session is None:
            raise RuntimeError("FeedJobRunner.start() must be called before run().")

        feed = database.get_feed_by_id(feed_id)

        if feed is None:
            return JobResult(next_run_at=None)

        if feed.disabled:
            return JobResult(next_run_at=None)

        now = _utc_now()
        try:
            entries = await pull.fetch(self._session, feed)
            # Debug for an odd bug I have not been able to reproduce
            # Essentially the failure count spikes for no reason
            # I have no clue why or how.
            # print("RUN:", feed.title, feed.failure_count)

            database.insert_feed_entries(entries, feed, True)

            next_check_at = now + timedelta(seconds=feed.poll_interval_seconds)

            database.mark_feed_fetch_success(
                feed_id=feed.id,
                checked_at=now,
                success_at=now,
                next_check_at=next_check_at,
            )

            return JobResult(next_run_at=next_check_at)

        except Exception:
            next_check_at = self._compute_retry_time(feed, now)

            database.mark_feed_fetch_failure(
                feed_id=feed.id,
                checked_at=now,
                next_check_at=next_check_at,
            )

            return JobResult(next_run_at=next_check_at)

    def _compute_retry_time(self, feed: Feed, now: datetime) -> datetime:
        # Capped exponential-ish backoff, shrimple
        # Even this is pretty agressive, should probably tone it down
        retry_seconds = min(3600, max(60, 60 * (2 ** min(feed.failure_count, 5))))
        return now + timedelta(seconds=retry_seconds)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
