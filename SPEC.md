# RRSS Feed Polling & Notification Specification

## 1. Purpose
RRSS monitors RSS and Atom feeds, stores feed and entry state in a persistent registry, and delivers notifications for newly discovered entries to one or more notification sinks, such as Matrix rooms.

## 2. Execution Modes

RRSS supports the following modes of operation:

### 2.1 Daemon Mode
In daemon mode, RRSS continuously performs three responsibilities:
1. Poll subscribed feeds according to their scheduled next-check times.
2. Reconcile the active feed set with the in-memory scheduler.
3. Dispatch pending notification jobs to configured sinks.

### 2.2 Add-Feed / Bootstrap Mode
In add-feed mode, RRSS:
1. Validates and fetches the supplied feed URL.
2. Extracts and stores feed metadata.
3. Fetches the current entries once.
4. Inserts those entries into persistent storage as already seen.
5. Does not enqueue notifications for those initial entries.

This mode is used to subscribe to a feed without back-notifying historical content.

## 3. Data Definitions
* **Feed**: A subscribed RSS or Atom endpoint together with its polling state.
* **FeedItem**: A deduplicated record representing one entry from a feed.
* **NotificationJob**: A outbox record representing one pending or attempted notification for one feed item.
* **Registry**: The persistent store used to keep feed state, feed items, and notification jobs.
* **Active Feed**: A feed whose `disabled` flag is false.

## 4. Persistence Requirements

### 4.1 Storage Backend
The registry MUST use SQLite.

### 4.2 General Requirements
The registry MUST provide:
* transactional writes
* foreign key enforcement
* durable state across process restarts
* deterministic deduplication behavior

### 4.3 Database Path

The database path is implementation-defined but SHOULD be configurable through environment or configuration settings.

A default state-directory-based path MAY be used.

## 5. Data Model


### 5.1 Feed
| Field                 | Type            | Description                          |
| --------------------- | --------------- | ------------------------------------ |
| id                    | integer         | Primary key                          |
| feed_url              | string UNIQUE   | Canonical subscribed feed URL        |
| title                 | string nullable | Feed title                           |
| etag                  | string nullable | Last observed HTTP ETag              |
| last_modified         | string nullable | Last observed HTTP Last-Modified     |
| last_checked_at       | timestamp       | Time of most recent poll attempt     |
| last_success_at       | timestamp       | Time of most recent successful fetch |
| failure_count         | integer         | Consecutive failed poll attempts     |
| next_check_at         | timestamp       | Scheduled next poll time             |
| poll_interval_seconds | integer         | Base poll interval                   |
| disabled              | boolean         | Whether the feed is disabled         |

### 5.2 FeedItem

| Field         | Type               | Description                                              |
| ------------- | ------------------ | -------------------------------------------------------- |
| id            | string             | Primary key; deterministic item identifier               |
| feed_id       | integer            | Foreign key to Feed                                      |
| source_id_raw | string nullable    | Raw GUID / Atom ID from source                           |
| link          | string nullable    | Entry URL                                                |
| title         | string nullable    | Entry title                                              |
| description   | string nullable    | Entry summary / description / content excerpt            |
| published_at  | timestamp nullable | Entry publication time                                   |
| content_hash  | string nullable    | Reserved for content hashing; currently optional         |
| first_seen_at | timestamp          | Time RRSS first observed this entry                      |
| last_seen_at  | timestamp          | Time RRSS most recently observed this entry              |
| notified_at   | timestamp nullable | Time a successful notification was recorded for the item |

### 5.3 NotificationJob
| Field           | Type               | Description                              |
| --------------- | ------------------ | ---------------------------------------- |
| id              | integer            | Primary key                              |
| feed_item_id    | string             | Foreign key to FeedItem                  |
| created_at      | timestamp          | Time the notification job was created    |
| attempts        | integer            | Number of delivery attempts recorded     |
| last_attempt_at | timestamp nullable | Time of the most recent delivery attempt |
| delivered_at    | timestamp nullable | Time delivery was marked successful      |

## 6. Time Semantics

### 6.1 UTC Normalization
All timestamps stored in the registry MUST be normalized to UTC.

### 6.2 Timestamp Format
Implementations MAY use any timestamp representation that preserves UTC semantics and can be compared reliably, such as ISO 8601 or Unix time.

### 6.3 Time Source
All scheduling, observation, and delivery timestamps MUST be derived from the system clock at the time the relevant event occurs.


## 7. Supported Feed Formats
RRSS MUST support:
* RSS
* Atom

If a fetched XML document is neither RSS nor Atom, the fetch MUST be treated as a failure.

## 8. Feed Parsing Behavior

### 8.1 RSS Parsing
For RSS feeds, RRSS MUST read entries from item elements and SHOULD extract, when present:
* `title`
* `link`
* `guid` or `id`
* `description`
* `pubDate`

If `pubDate` is present and parseable, it MUST be converted to UTC.

### 8.2 Atom Parsing

For Atom feeds, RRSS MUST read entries from top-level entry elements and SHOULD extract, when present:
* `id`
* `title`
* `summary` or `content`
* alternate link, or otherwise the first available link
* `published` or `updated`

If `published` or `updated` is present and parseable, it MUST be converted to UTC.

### 8.3 Feed Metadata Parsin

Feed-level metadata SHOULD include, when available:
* feed URL
* website URL
* title
* description or subtitle

## 9. Item Identifier Derivation

### 9.1 Overview
Each feed entry MUST be mapped to one deterministic item identifier used as the primary deduplication key.

### 9.2 Derivation Order
The item identifier MUST be derived using the following precedence:

1. Raw source identifier (source_id_raw)
2. Link
3. No identifier available

9.3 Identifier Construction

If `source_id_raw` exists:
```
id = "guid:" + source_id_raw
```

Else if `link` exists
```
id = "link:" + normalize(link)
```

Else:
* RRSS MUST treat the entry as non-identifiable.
* The entry MUST NOT be inserted unless the implementation defines and documents an additional fallback rule.

### 9.4 Link Normalization
At minimum, the current behavior is equivalent to:

* trim leading and trailing whitespace
* remove trailing /
* lowercase the resulting link string.

Implementations that aim for compatibility MUST preserve the same normalization behavior for identifier derivation unless the specification is intentionally revised.

## 10. Feed Subscription / Bootstrap Behavior

When a new feed is added:
1. RRSS MUST fetch and validate the feed.
2. RRSS MUST insert or update the feed record.
3. RRSS MUST fetch the current entries.
4. RRSS MUST insert all currently visible entries into FeedItem.
5. RRSS MUST NOT create notification jobs for those initial entries.
6.If an item already exists during bootstrap, RRSS MAY update `last_seen_at`.

## 11. Feed Ingestion and Deduplication

For each parsed entry obtained during ordinary polling:

1. Derive the deterministic item identifier.
2. Attempt to insert a new FeedItem using that identifier as primary key.
3. If insertion succeeds:
   * set `first_seen_at`
   * set `last_seen_at`
   * enqueue a notification job
4. If insertion does not succeed because the item already exists:
   * update `last_seen_at`
   * do not create another notification job

This ensures at-most-once enqueue behavior per deduplicated feed item.



## 12. Polling Behavior

### 12.1 Poll Request
For a scheduled poll of a feed, RRSS MUST attempt to fetch the feed URL.

### 12.2
A poll is considered successful if the feed is fetched and parsed successfully.

On success RRSS MUST:
1. ingest returned entries
2. set `last_checked_at` to the poll time
3. set `last_success_at` to the poll time
4. reset `failure_count` to 0
5. set `next_check_at = now + poll_interval_seconds`.

### 12.3 Failed Poll

A poll is considered failed if fetching or parsing fails for any reason, including:
* network errors
* timeouts
* invalid XML
* unsupported feed type.

On failure RRSS MUST:

1. set `last_checked_at` to the poll time,
2. increment `failure_count` by 1,
3. compute and store a retry time,
4. set `next_check_at` to that retry time.

## 13 Notification Sink Result Semantics

A notifier MUST return one of the following outcomes

### 13.1 SUCCESS
The notification was delivered successfully.

On SUCCESS, RRSS MUST:
* set delivered_at
* increment attempts
* set last_attempt_at

RRSS SHOULD also set `FeedItem.notified_at` to the successful delivery time.

### 13.2 RETRY
The delivery failed transiently and may succeed later.
On RETRY, RRSS MUST:
* increment attempts
* set last_attempt_at


### 13.3 FAILURE
If the new attempt count reaches the configured maximum, RRSS MAY delete the job instead of retrying further.


## 14 System Guarentees

An implementation conforming to this specification MUST guarantee:
1. A feed entry with an identifiable source maps to one deterministic item identifier.
2. The same deduplicated item is not enqueued for notification more than once.
3. Repeated polling does not create duplicate notifications for already known items.
4. Registry state survives process restarts.
5. Disabled or removed feeds are not continuously rescheduled.
6. Bootstrap ingestion does not emit notifications for historical entries.
