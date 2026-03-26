# rrss

`rrss` is a small RSS and Atom polling service that watches feeds for new entries and delivers notifications to sinks such as Matrix rooms.

It keeps feed state, seen items, and pending notifications in SQLite so that polling and delivery can resume cleanly across restarts. When a feed is polled, newly discovered entries are inserted into the registry and queued for delivery. Entries that have already been seen are not enqueued again, which prevents duplicate notifications for the same deduplicated item.

## How it works

`rrss` has two main modes of operation. In normal daemon mode, it continuously polls active feeds according to their scheduled next-check times, reconciles the in-memory scheduler with the feeds stored in the database, and dispatches pending notification jobs to the configured sink or sinks.

When run with a feed URL, `rrss` performs a bootstrap subscription. It fetches and validates the feed, stores its metadata, fetches the currently visible entries once, and records them as already seen. This initial import does not send notifications, which allows a feed to be added without back-notifying old posts.

Feeds and feed items are stored in SQLite. Notification delivery is handled through a small outbox-style queue, so transient delivery failures can be retried without losing state. Successful deliveries are recorded, and repeated polling of the same feed will not create duplicate notifications for already known items.

## CLI

```bash
python src/main.py run
python src/main.py run <feed_url>
python src/main.py verify
```
* `run` starts the daemon.
* `run <feed_url>` adds a feed in bootstrap mode and exits.
* `verify` runs interactive Matrix device verification.

## Feed support

`rrss` supports both RSS and Atom feeds. If a fetched XML document is neither RSS nor Atom, the fetch is treated as a failure.

## Configuration

The database path and sink configuration are intended to be provided through configuration or environment variables.


## Development

Contributions are welcome. Please follow [Concentional Standards](https://www.conventionalcommits.org/en/v1.0.0/)
 when contributing to the project.
