"""
Notification job dispatcher.

Polls the notificationJob table for pending rows and sends them via the
notifier.  Marks delivered on success, increments attempts + schedules
retry on transient failure, drops permanently on hard failure.
"""

import asyncio
from datetime import datetime, timedelta, timezone

from database import database
from sinks.base import Notifier
from models import SendStatus


MAX_ATTEMPTS = 20
POLL_INTERVAL_SECONDS = 10


async def run_notification_dispatcher(notifier: Notifier) -> None:
    print("[notifications] dispatcher started")
    while True:
        try:
            await _dispatch_pending(notifier)
        except Exception as exc:
            print(f"[notifications] dispatcher error: {exc}")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def _dispatch_pending(notifier: Notifier) -> None:
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

        from models import NotificationMessage

        msg = NotificationMessage(
            title=item.title or "(no title)",
            body=item.description or "",
            url=item.link,
            source_name=feed.title if feed else None,
        )

        result = await notifier.send(msg)
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
