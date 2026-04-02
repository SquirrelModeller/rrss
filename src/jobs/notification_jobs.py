"""
Notification job dispatcher.

Polls the notificationJob table for pending rows and sends them via all
configured sinks.  Marks delivered on success, increments attempts +
schedules retry on transient failure, drops permanently on hard failure.

A job is considered delivered if at least one sink succeeds.  If any sink
returns RETRY the job is retried.  Only if every sink fails permanently is
the job dropped.
"""

import asyncio
from datetime import datetime, timezone

from database import database
from sinks.base import Sink
from models import SendResult, SendStatus, NotificationMessage


MAX_ATTEMPTS = 20
POLL_INTERVAL_SECONDS = 10


async def run_notification_dispatcher(sinks: list[Sink]) -> None:
    print("[notifications] dispatcher started")
    while True:
        try:
            await _dispatch_pending(sinks)
        except Exception as exc:
            print(f"[notifications] dispatcher error: {exc}")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def _dispatch_pending(sinks: list[Sink]) -> None:
    jobs = database.get_pending_notification_jobs()

    if not jobs:
        return

    print(f"[notifications] dispatching {len(jobs)} pending job(s)")

    for job in jobs:
        item = database.get_feed_item_by_id(job["feed_item_id"])
        if item is None:
            database.delete_notification_job(job["id"])
            continue

        feed = database.get_feed_by_id(item.feed_id)

        msg = NotificationMessage(
            title=item.title or "(no title)",
            body=item.description or "",
            url=item.link,
            source_name=feed.title if feed else None,
        )

        results: list[SendResult] = await asyncio.gather(
            *[sink.send(msg) for sink in sinks]
        )

        result = _aggregate_results(results)
        now = datetime.now(timezone.utc)

        if result.status == SendStatus.SUCCESS:
            database.mark_notification_delivered(job["id"], now)
            print(f"[notifications] delivered job {job['id']} ({item.title})")

        elif result.status == SendStatus.RETRY:
            new_attempts = job["attempts"] + 1
            if new_attempts >= MAX_ATTEMPTS:
                print(
                    f"[notifications] job {job['id']} exceeded max attempts, dropping"
                )
                database.delete_notification_job(job["id"])
            else:
                database.mark_notification_attempted(job["id"], new_attempts, now)
                print(
                    f"[notifications] job {job['id']} failed (attempt {new_attempts}): {result.error}"
                )

        else:
            print(f"[notifications] job {job['id']} failed permanently: {result.error}")
            database.delete_notification_job(job["id"])


def _aggregate_results(results: list[SendResult]) -> SendResult:
    """
    Merge results from multiple sinks into a single SendResult.

    Any SUCCESS -> SUCCESS (notification is out).
    No SUCCESS but any RETRY -> RETRY (try again later).
    All FAILURE -> FAILURE (drop the job).
    """
    if not results:
        return SendResult(status=SendStatus.FAILURE, error="No sinks to send to")

    statuses = {r.status for r in results}

    if SendStatus.SUCCESS in statuses:
        return SendResult(status=SendStatus.SUCCESS)

    if SendStatus.RETRY in statuses:
        errors = [r.error for r in results if r.error]
        return SendResult(status=SendStatus.RETRY, error="; ".join(errors))

    errors = [r.error for r in results if r.error]
    return SendResult(status=SendStatus.FAILURE, error="; ".join(errors))
