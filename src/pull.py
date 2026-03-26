from models import ParsedFeed, ParsedFeedEntry, Feed
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import NamedTuple, Optional
import xml.etree.ElementTree as ET

import aiohttp


class FeedInfo(NamedTuple):
    kind: str
    version: Optional[str]


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _find_child_text(elem: ET.Element, name: str) -> str | None:
    for child in elem:
        if _strip_ns(child.tag) == name:
            return child.text.strip() if child.text else None
    return None


def _find_child(elem: ET.Element, name: str) -> ET.Element | None:
    for child in elem:
        if _strip_ns(child.tag) == name:
            return child
    return None


def detect_feed_type(root: ET.Element) -> FeedInfo:
    tag = _strip_ns(root.tag).lower()

    if tag == "rss":
        return FeedInfo("rss", root.attrib.get("version"))

    if tag == "feed":
        return FeedInfo("atom", root.attrib.get("version", "1.0"))

    return FeedInfo("unknown", None)


def _parse_feed_metadata_rss(root: ET.Element, url: str) -> ParsedFeed:
    channel = _find_child(root, "channel")
    if channel is None:
        raise ValueError(f"Could not find RSS channel in {url}")

    return ParsedFeed(
        feed_link=url,
        website_url=_find_child_text(channel, "link"),
        title=_find_child_text(channel, "title"),
        description=_find_child_text(channel, "description"),
        last_seen_at=datetime.now(timezone.utc),
    )


def _parse_feed_metadata_atom(root: ET.Element, url: str) -> ParsedFeed:
    website_url = None

    for child in root:
        if _strip_ns(child.tag) != "link":
            continue

        rel = child.attrib.get("rel", "alternate")
        href = child.attrib.get("href")

        if rel == "alternate" and href:
            website_url = href
            break

        if website_url is None and href:
            website_url = href

    subtitle = _find_child_text(root, "subtitle")

    return ParsedFeed(
        feed_link=url,
        website_url=website_url,
        title=_find_child_text(root, "title"),
        description=subtitle,
        last_seen_at=datetime.now(timezone.utc),
    )


def _parse_entry_rss(item: ET.Element) -> ParsedFeedEntry:
    parsed = ParsedFeedEntry(datetime.now(timezone.utc))
    parsed.title = _find_child_text(item, "title")
    parsed.link = _find_child_text(item, "link")
    parsed.source_id_raw = _find_child_text(item, "guid") or _find_child_text(
        item, "id"
    )
    parsed.description = _find_child_text(item, "description")

    published_at = _find_child_text(item, "pubDate")
    if published_at:
        dt = parsedate_to_datetime(published_at)
        parsed.published_at = dt.astimezone(timezone.utc)

    return parsed


def _parse_entry_atom(entry: ET.Element) -> ParsedFeedEntry:
    parsed = ParsedFeedEntry(datetime.now(timezone.utc))
    parsed.title = _find_child_text(entry, "title")
    parsed.source_id_raw = _find_child_text(entry, "id")
    parsed.description = _find_child_text(entry, "summary") or _find_child_text(
        entry, "content"
    )

    link = None
    for child in entry:
        if _strip_ns(child.tag) != "link":
            continue

        rel = child.attrib.get("rel", "alternate")
        href = child.attrib.get("href")

        if rel == "alternate" and href:
            link = href
            break

        if link is None and href:
            link = href

    parsed.link = link

    published_at = _find_child_text(entry, "published") or _find_child_text(
        entry, "updated"
    )
    if published_at:
        dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        parsed.published_at = dt.astimezone(timezone.utc)

    return parsed


async def get_feed(session: aiohttp.ClientSession, url: str) -> ParsedFeed:
    async with session.get(url) as res:
        res.raise_for_status()
        content = await res.read()

    root = ET.fromstring(content)
    feed_info = detect_feed_type(root)

    if feed_info.kind == "rss":
        return _parse_feed_metadata_rss(root, url)

    if feed_info.kind == "atom":
        return _parse_feed_metadata_atom(root, url)

    raise ValueError(f"Unsupported feed type {root.tag!r} from {url}")


async def fetch(session: aiohttp.ClientSession, feed: Feed) -> list[ParsedFeedEntry]:
    async with session.get(feed.feed_url) as res:
        res.raise_for_status()
        content = await res.read()

    root = ET.fromstring(content)
    feed_info = detect_feed_type(root)

    if feed_info.kind == "rss":
        return [_parse_entry_rss(item) for item in root.findall(".//item")]

    if feed_info.kind == "atom":
        parsed_entries: list[ParsedFeedEntry] = []
        for child in root:
            if _strip_ns(child.tag) == "entry":
                parsed_entries.append(_parse_entry_atom(child))
        return parsed_entries

    raise ValueError(f"Unsupported feed type {root.tag!r} from {feed.feed_url}")
