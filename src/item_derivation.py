from models import ParsedFeedEntry


def derivation_feed_item(feed_item: ParsedFeedEntry) -> str:
    if feed_item.source_id_raw:
        return "guid:" + feed_item.source_id_raw
    elif feed_item.link:
        return feed_item.link.lower().strip().strip("/")
