import asyncio
import argparse
import sys

from database import database
import general_logic
from jobs.feed_jobs import FeedJobRunner
from scheduler import Scheduler
from jobs.notification_jobs import run_notification_dispatcher
from sinks.registry import load_sinks


async def reconcile_feeds(
    scheduler: Scheduler[int], interval_seconds: int = 30
) -> None:
    while True:
        try:
            active_feeds = database.get_all_active_feeds()
            active_feed_ids = {feed.id for feed in active_feeds}

            for scheduled_feed_id in scheduler.get_scheduled_job_ids():
                if scheduled_feed_id not in active_feed_ids:
                    scheduler.unschedule(scheduled_feed_id)

            for feed in active_feeds:
                if not scheduler.is_scheduled(feed.id):
                    scheduler.schedule_at(feed.id, feed.next_check_at)

        except Exception as exc:
            print(f"reconcile_feeds failed: {exc}")

        await asyncio.sleep(interval_seconds)


async def main(url: str | None = None) -> None:
    database.generate_database()

    if url:
        await general_logic.add_new_website(url)
        return

    sinks = load_sinks()

    runner = FeedJobRunner()
    await runner.start()

    scheduler = Scheduler[int](runner=runner.run)

    for feed in database.get_all_active_feeds():
        scheduler.schedule_at(feed.id, feed.next_check_at)

    listener_tasks = [asyncio.create_task(sink.start_listener()) for sink in sinks]

    scheduler_task = asyncio.create_task(scheduler.run_forever())
    reconcile_task = asyncio.create_task(
        reconcile_feeds(scheduler, interval_seconds=15)
    )
    notification_task = asyncio.create_task(run_notification_dispatcher(sinks))

    try:
        await asyncio.gather(
            scheduler_task, reconcile_task, notification_task, *listener_tasks
        )
    finally:
        reconcile_task.cancel()
        scheduler_task.cancel()
        notification_task.cancel()
        for t in listener_tasks:
            t.cancel()

        await runner.close()
        await scheduler.close()
        for sink in sinks:
            await sink.close()


def parse_args():
    parser = argparse.ArgumentParser(prog="rrss")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser(
        "verify",
        help="Interactively verify the bot's Matrix device",
    )

    run_parser = subparsers.add_parser(
        "run",
        help="Run the bot",
    )
    run_parser.add_argument(
        "url",
        nargs="?",
        help="Optional RSS feed URL to add",
    )

    argv = sys.argv[1:]
    known_commands = {"run", "verify"}

    if argv and argv[0] not in known_commands:
        argv = ["run", *argv]

    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()

    if args.command == "verify":
        from sinks.matrix.verify import run_verification

        asyncio.run(run_verification())
    else:
        asyncio.run(main(getattr(args, "url", None)))
