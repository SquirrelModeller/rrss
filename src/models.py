from typing import List, Optional
from dataclasses import dataclass, field
from typing import Mapping
from datetime import datetime
from enum import Enum, auto
from enum import Enum, auto


@dataclass(slots=True, frozen=True)
class Feed:
    id: int
    feed_url: str
    title: Optional[str]
    etag: Optional[str]
    last_modified: str
    last_checked_at: datetime
    last_success_at: datetime
    failure_count: int
    next_check_at: datetime
    poll_interval_seconds: int
    disabled: bool


@dataclass(slots=True, frozen=True)
class FeedItem:
    id: int
    feed_id: int
    item_key: str
    source_id_raw: Optional[str]
    link: Optional[str]
    title: Optional[str]
    description: Optional[str]
    published_at: Optional[datetime]
    content_hash: Optional[str]
    first_seen_at: datetime
    last_seen_at: datetime
    notified_at: Optional[datetime]


@dataclass(slots=True)
class ParsedFeedEntry:
    last_seen_at: datetime
    title: Optional[str] = None
    link: Optional[str] = None
    source_id_raw: Optional[str] = None
    description: Optional[str] = None
    published_at: Optional[datetime] = None


@dataclass(slots=True)
class ParsedFeed:
    feed_link: str
    last_seen_at: datetime
    website_url: Optional[str]
    title: Optional[str] = None
    description: Optional[str] = None
    etag: Optional[str] = None


class SendStatus(Enum):
    SUCCESS = auto()
    RETRY = auto()
    FAILURE = auto()


@dataclass
class SendResult:
    status: SendStatus
    message_id: Optional[str] = None
    error: Optional[str] = None


@dataclass(slots=True)
class NotificationMessage:
    title: str
    body: str
    url: str | None = None
    source_name: str | None = None
    tags: list[str] = field(default_factory=list)
    metadata: Mapping[str, str] = field(default_factory=dict)
