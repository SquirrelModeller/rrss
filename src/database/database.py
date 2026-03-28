import sqlite3
from datetime import datetime, timedelta, timezone
from typing import List

import item_derivation
from models import Feed, FeedItem, ParsedFeed, ParsedFeedEntry
from .database_config import DatabaseConfig
import os

# NOTE: Claude 4.6 wrote documentation

DB_PATH = "rrss_data.db"

SINK_ENV_MAP: dict[str, str] = {
    "matrix": "RRSS_ADMIN_MATRIX",
    "fluxer": "RRSS_ADMIN_FLUXER",
}


def generate_database() -> None:
    with _connect() as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        cursor = conn.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS feed(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                feed_url TEXT NOT NULL UNIQUE,
                title TEXT,
                etag TEXT,
                last_modified TEXT,
                last_checked_at TEXT NOT NULL,
                last_success_at TEXT NOT NULL,
                failure_count INTEGER NOT NULL,
                next_check_at TEXT NOT NULL,
                poll_interval_seconds INTEGER NOT NULL,
                disabled INTEGER NOT NULL
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS feedItem(
                id TEXT PRIMARY KEY,
                feed_id INTEGER NOT NULL,
                source_id_raw TEXT,
                link TEXT,
                title TEXT,
                description TEXT,
                published_at TEXT,
                content_hash TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                notified_at TEXT,
                FOREIGN KEY (feed_id) REFERENCES feed(id)
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS notificationJob(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                feed_item_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                attempts INTEGER NOT NULL,
                last_attempt_at TEXT,
                delivered_at TEXT,
                FOREIGN KEY (feed_item_id) REFERENCES feedItem(id)
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS admin(
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                name     TEXT NOT NULL UNIQUE,
                added_at TEXT NOT NULL,
                added_by TEXT NOT NULL
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS adminIdentity(
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER NOT NULL,
                sink     TEXT NOT NULL,
                handle   TEXT NOT NULL,
                UNIQUE(sink, handle),
                FOREIGN KEY (admin_id) REFERENCES admin(id)
            )
            """
        )

        _bootstrap_admin(cursor)


def get_feeds() -> list[Feed]:
    with _connect() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM feed")
        rows = cursor.fetchall()

    return [_feed_from_row(row) for row in rows]


def get_all_active_feeds() -> list[Feed]:
    with _connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM feed
            WHERE disabled = 0
            """
        )
        rows = cursor.fetchall()

    return [_feed_from_row(row) for row in rows]


def get_due_feeds(now: datetime) -> list[Feed]:
    now = _ensure_utc(now)

    with _connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM feed
            WHERE disabled = 0
              AND next_check_at <= ?
            """,
            (now.isoformat(),),
        )
        rows = cursor.fetchall()

    return [_feed_from_row(row) for row in rows]


def get_feed_by_id(feed_id: int) -> Feed | None:
    with _connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM feed
            WHERE id = ?
            """,
            (feed_id,),
        )
        row = cursor.fetchone()

    if row is None:
        return None

    return _feed_from_row(row)


def get_feed_from_url(url: str) -> Feed | None:
    with _connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM feed
            WHERE feed_url = ?
            """,
            (url,),
        )
        row = cursor.fetchone()

    if row is None:
        return None

    return _feed_from_row(row)


def insert_feed(feed: ParsedFeed) -> None:
    now = _utc_now()

    last_seen_at = _ensure_utc(feed.last_seen_at)
    next_check_at = last_seen_at + timedelta(seconds=60)

    with _connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO feed (
                feed_url,
                title,
                etag,
                last_modified,
                last_checked_at,
                last_success_at,
                failure_count,
                next_check_at,
                poll_interval_seconds,
                disabled
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(feed_url) DO UPDATE SET
                title = excluded.title,
                etag = excluded.etag,
                last_checked_at = excluded.last_checked_at
            """,
            (
                feed.feed_link,
                feed.title,
                feed.etag,
                None,
                now.isoformat(),
                now.isoformat(),
                0,
                next_check_at.isoformat(),
                60,
                0,
            ),
        )
        conn.commit()


def insert_feed_entries(
    feed_entries: list[ParsedFeedEntry], feed: Feed, notify_new: bool
) -> None:
    now = _utc_now().isoformat()

    with _connect() as conn:
        cursor = conn.cursor()

        for feed_entry in feed_entries:
            item_id = item_derivation.derivation_feed_item(feed_entry)

            cursor.execute(
                """
                INSERT INTO feedItem(
                    id,
                    feed_id,
                    source_id_raw,
                    link,
                    title,
                    description,
                    published_at,
                    content_hash,
                    first_seen_at,
                    last_seen_at,
                    notified_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                (
                    item_id,
                    feed.id,
                    feed_entry.source_id_raw,
                    feed_entry.link,
                    feed_entry.title,
                    feed_entry.description,
                    (
                        _ensure_utc(feed_entry.published_at).isoformat()
                        if feed_entry.published_at is not None
                        else None
                    ),
                    None,
                    now,
                    now,
                    None,
                ),
            )

            inserted = cursor.rowcount == 1

            if inserted:
                if notify_new:
                    cursor.execute(
                        """
                        INSERT INTO notificationJob(
                            feed_item_id,
                            created_at,
                            attempts,
                            last_attempt_at,
                            delivered_at
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            item_id,
                            now,
                            0,
                            None,
                            None,
                        ),
                    )
            else:
                cursor.execute(
                    """
                    UPDATE feedItem
                    SET last_seen_at = ?
                    WHERE id = ?
                    """,
                    (now, item_id),
                )

        conn.commit()


def insert_feed_item(feed_entries: List[ParsedFeedEntry], feed: Feed) -> None:
    """
    Insert all feed entries disregarding notifications.
    If ID conflicts, it overrides last_seen_at.
    """
    with _connect() as conn:
        cursor = conn.cursor()

        for feed_entry in feed_entries:
            last_seen_at = _ensure_utc(feed_entry.last_seen_at).isoformat()
            published_at = (
                _ensure_utc(feed_entry.published_at).isoformat()
                if feed_entry.published_at is not None
                else None
            )

            cursor.execute(
                """
                INSERT INTO feedItem(
                    id,
                    feed_id,
                    source_id_raw,
                    link,
                    title,
                    description,
                    published_at,
                    content_hash,
                    first_seen_at,
                    last_seen_at,
                    notified_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    item_derivation.derivation_feed_item(feed_entry),
                    feed.id,
                    feed_entry.source_id_raw,
                    feed_entry.link,
                    feed_entry.title,
                    feed_entry.description,
                    published_at,
                    None,
                    last_seen_at,
                    last_seen_at,
                    None,
                ),
            )

        conn.commit()


def get_feed_items_for_feed(feed_id: int) -> list[FeedItem]:
    with _connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM feedItem
            WHERE feed_id = ?
            ORDER BY first_seen_at DESC
            """,
            (feed_id,),
        )
        rows = cursor.fetchall()

    return [_feed_item_from_row(row) for row in rows]


def mark_feed_fetch_success(
    feed_id: int,
    checked_at: datetime,
    success_at: datetime,
    next_check_at: datetime,
    etag: str | None = None,
    last_modified: str | None = None,
) -> None:
    checked_at = _ensure_utc(checked_at)
    success_at = _ensure_utc(success_at)
    next_check_at = _ensure_utc(next_check_at)

    with _connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE feed
            SET
                last_checked_at = ?,
                last_success_at = ?,
                failure_count = 0,
                next_check_at = ?,
                etag = COALESCE(?, etag),
                last_modified = COALESCE(?, last_modified)
            WHERE id = ?
            """,
            (
                checked_at.isoformat(),
                success_at.isoformat(),
                next_check_at.isoformat(),
                etag,
                last_modified,
                feed_id,
            ),
        )
        conn.commit()


def mark_feed_fetch_failure(
    feed_id: int,
    checked_at: datetime,
    next_check_at: datetime,
) -> None:
    checked_at = _ensure_utc(checked_at)
    next_check_at = _ensure_utc(next_check_at)

    with _connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE feed
            SET
                last_checked_at = ?,
                failure_count = failure_count + 1,
                next_check_at = ?
            WHERE id = ?
            """,
            (
                checked_at.isoformat(),
                next_check_at.isoformat(),
                feed_id,
            ),
        )
        conn.commit()


def update_feed_schedule(
    feed_id: int,
    next_check_at: datetime,
    poll_interval_seconds: int | None = None,
) -> None:
    next_check_at = _ensure_utc(next_check_at)

    with _connect() as conn:
        cursor = conn.cursor()

        if poll_interval_seconds is None:
            cursor.execute(
                """
                UPDATE feed
                SET next_check_at = ?
                WHERE id = ?
                """,
                (next_check_at.isoformat(), feed_id),
            )
        else:
            cursor.execute(
                """
                UPDATE feed
                SET
                    next_check_at = ?,
                    poll_interval_seconds = ?
                WHERE id = ?
                """,
                (next_check_at.isoformat(), poll_interval_seconds, feed_id),
            )

        conn.commit()


def set_feed_disabled(feed_id: int, disabled: bool) -> None:
    with _connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE feed
            SET disabled = ?
            WHERE id = ?
            """,
            (int(disabled), feed_id),
        )
        conn.commit()


def delete_feed(feed_id: int) -> None:
    with _connect() as conn:
        cursor = conn.cursor()

        cursor.execute(
            """
            DELETE FROM notificationJob
            WHERE feed_item_id IN (
                SELECT id FROM feedItem WHERE feed_id = ?
            )
            """,
            (feed_id,),
        )

        cursor.execute(
            """
            DELETE FROM feedItem
            WHERE feed_id = ?
            """,
            (feed_id,),
        )

        cursor.execute(
            """
            DELETE FROM feed
            WHERE id = ?
            """,
            (feed_id,),
        )

        conn.commit()


def is_admin(sink: str, handle: str) -> bool:
    """Return True if the given sink handle belongs to a known admin."""
    with _connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT 1 FROM adminIdentity
            WHERE sink = ? AND handle = ?
            """,
            (sink, handle),
        )
        return cursor.fetchone() is not None


def get_admin_by_identity(sink: str, handle: str) -> dict | None:
    """
    Return the admin record for a given sink identity, or None.
    Returned dict keys: id, name, added_at, added_by.
    """
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT a.id, a.name, a.added_at, a.added_by
            FROM admin a
            JOIN adminIdentity ai ON ai.admin_id = a.id
            WHERE ai.sink = ? AND ai.handle = ?
            """,
            (sink, handle),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def add_admin(
    name: str, sink: str, handle: str, added_by_name: str
) -> tuple[bool, str]:
    """
    Add an admin identity.

    - If `name` doesn't exist yet, creates the admin row first.
    - If the (sink, handle) pair already exists anywhere, returns (False, reason).

    Returns (True, "") on success, (False, reason) on failure.
    added_by_name is the name field of the admin issuing the command.
    """
    now = _utc_now().isoformat()

    with _connect() as conn:
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT a.name FROM admin a
            JOIN adminIdentity ai ON ai.admin_id = a.id
            WHERE ai.sink = ? AND ai.handle = ?
            """,
            (sink, handle),
        )
        existing = cursor.fetchone()
        if existing:
            return (
                False,
                f"{handle} is already registered to admin '{existing[0]}' on {sink}.",
            )

        cursor.execute(
            "INSERT OR IGNORE INTO admin(name, added_at, added_by) VALUES (?, ?, ?)",
            (name, now, added_by_name),
        )
        cursor.execute("SELECT id FROM admin WHERE name = ?", (name,))
        row = cursor.fetchone()
        if row is None:
            return False, "Failed to create admin record."
        admin_id = row[0]

        cursor.execute(
            "INSERT INTO adminIdentity(admin_id, sink, handle) VALUES (?, ?, ?)",
            (admin_id, sink, handle),
        )
        conn.commit()

    return True, ""


def remove_admin_identity(sink: str, handle: str) -> tuple[bool, str]:
    """
    Remove a single sink identity.

    If this was the admin's last identity the admin row is also removed
    an admin with no handles is unreachable and shouldn't linger.

    Returns (True, info_message) or (False, error_message).
    """
    with _connect() as conn:
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT ai.id, ai.admin_id, a.name
            FROM adminIdentity ai
            JOIN admin a ON a.id = ai.admin_id
            WHERE ai.sink = ? AND ai.handle = ?
            """,
            (sink, handle),
        )
        row = cursor.fetchone()
        if row is None:
            return False, f"No {sink} identity found for handle: {handle}"

        identity_id, admin_id, admin_name = row

        cursor.execute("DELETE FROM adminIdentity WHERE id = ?", (identity_id,))

        cursor.execute(
            "SELECT COUNT(*) FROM adminIdentity WHERE admin_id = ?", (admin_id,)
        )
        remaining = cursor.fetchone()[0]

        if remaining == 0:
            cursor.execute("DELETE FROM admin WHERE id = ?", (admin_id,))
            conn.commit()
            return (
                True,
                f"Removed {handle} from {sink}. "
                f"Admin '{admin_name}' had no remaining identities and was also removed.",
            )

        conn.commit()
        return (
            True,
            f"Removed {handle} from {sink}. "
            f"Admin '{admin_name}' still has {remaining} other identity/identities.",
        )


def list_admins() -> list[dict]:
    """
    Return all admins with their identities grouped.

    Each dict: { name, added_at, added_by, identities: [ {sink, handle}, ... ] }
    """
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute(
            "SELECT id, name, added_at, added_by FROM admin ORDER BY added_at"
        )
        admins = [dict(row) for row in cursor.fetchall()]

        for admin in admins:
            cursor.execute(
                "SELECT sink, handle FROM adminIdentity WHERE admin_id = ? ORDER BY sink",
                (admin["id"],),
            )
            admin["identities"] = [dict(r) for r in cursor.fetchall()]

        return admins


def _bootstrap_admin(cursor: sqlite3.Cursor) -> None:
    """
    Seed the first admin from environment variables if no admins exist yet.

    Required env var:
        RRSS_ADMIN_NAME: canonical label for the admin (e.g. "squirrel")

    Optional per-sink env vars (set whichever apply):
        RRSS_ADMIN_MATRIX: Matrix MXID  e.g. @squirrel:crispy-caesus.eu
        RRSS_ADMIN_FLUXER: Fluxer handle e.g. squirrel@fluxer.social

    Safe to call on every startup: INSERT OR IGNORE means it only fires once.
    """
    name = os.environ.get("RRSS_ADMIN_NAME", "").strip()
    if not name:
        return

    identities: list[tuple[str, str]] = []
    for sink, env_var in SINK_ENV_MAP.items():
        handle = os.environ.get(env_var, "").strip()
        if handle:
            identities.append((sink, handle))

    if not identities:
        return

    now = _utc_now().isoformat()

    cursor.execute(
        """
        INSERT OR IGNORE INTO admin(name, added_at, added_by)
        VALUES (?, ?, ?)
        """,
        (name, now, "bootstrap"),
    )

    cursor.execute("SELECT id FROM admin WHERE name = ?", (name,))
    row = cursor.fetchone()
    if row is None:
        return
    admin_id = row[0]

    for sink, handle in identities:
        cursor.execute(
            """
            INSERT OR IGNORE INTO adminIdentity(admin_id, sink, handle)
            VALUES (?, ?, ?)
            """,
            (admin_id, sink, handle),
        )


def _feed_from_row(row: tuple) -> Feed:
    return Feed(
        id=row[0],
        feed_url=row[1],
        title=row[2],
        etag=row[3],
        last_modified=row[4],
        last_checked_at=datetime.fromisoformat(row[5]),
        last_success_at=datetime.fromisoformat(row[6]),
        failure_count=row[7],
        next_check_at=datetime.fromisoformat(row[8]),
        poll_interval_seconds=row[9],
        disabled=bool(row[10]),
    )


def _feed_item_from_row(row: tuple) -> FeedItem:
    return FeedItem(
        id=row[0],
        feed_id=row[1],
        item_key=row[0],
        source_id_raw=row[2],
        link=row[3],
        title=row[4],
        description=row[5],
        published_at=datetime.fromisoformat(row[6]) if row[6] else None,
        content_hash=row[7],
        first_seen_at=datetime.fromisoformat(row[8]),
        last_seen_at=datetime.fromisoformat(row[9]),
        notified_at=datetime.fromisoformat(row[10]) if row[10] else None,
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("Expected timezone-aware datetime")
    return dt.astimezone(timezone.utc)


def _db_path() -> str:
    return DatabaseConfig.from_env().db_path


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_pending_notification_jobs() -> list[dict]:
    """Return all undelivered jobs that haven't exceeded MAX_ATTEMPTS."""
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM notificationJob
            WHERE delivered_at IS NULL
            ORDER BY created_at ASC
            """
        )
        return [dict(row) for row in cursor.fetchall()]


def get_feed_item_by_id(item_id: str) -> "FeedItem | None":
    with _connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM feedItem
            WHERE id = ?
            """,
            (item_id,),
        )
        row = cursor.fetchone()

    if row is None:
        return None

    return _feed_item_from_row(row)


def mark_notification_delivered(job_id: int, delivered_at: datetime) -> None:
    delivered_at = _ensure_utc(delivered_at)
    with _connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE notificationJob
            SET delivered_at = ?,
                attempts = attempts + 1,
                last_attempt_at = ?
            WHERE id = ?
            """,
            (delivered_at.isoformat(), delivered_at.isoformat(), job_id),
        )
        conn.commit()


def mark_notification_attempted(
    job_id: int, attempts: int, attempted_at: datetime
) -> None:
    attempted_at = _ensure_utc(attempted_at)
    with _connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE notificationJob
            SET attempts = ?,
                last_attempt_at = ?
            WHERE id = ?
            """,
            (attempts, attempted_at.isoformat(), job_id),
        )
        conn.commit()


def delete_notification_job(job_id: int) -> None:
    with _connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM notificationJob WHERE id = ?",
            (job_id,),
        )
        conn.commit()
