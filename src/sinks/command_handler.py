from __future__ import annotations

import re

from database import database
import general_logic

MXID_RE = re.compile(r"^@[^:@\s]+:[^:@\s]+\.[^:@\s]+$")

HELP_TEXT = """\
  !help                                  show this message
  !status                                feed count and next poll time
  !add-feed <url>                        subscribe to an RSS feed
  !remove-feed <url>                     unsubscribe from a feed
  !list-feeds                            list all active feeds
  !add-admin <name> <sink> <handle>      grant admin (e.g. !add-admin alice matrix @alice:example.com)
  !remove-admin <sink> <handle>          revoke an admin identity
  !list-admins                           list all trusted admins and their identities\
"""


async def dispatch(sink: str, sender: str, body: str) -> str:
    """
    Main entry point for all sinks.

    sink   - the name of the sink the command arrived on (e.g. "matrix", "fluxer")
    sender - the sink-specific handle of the message author (e.g. MXID, email)
    body   - raw message text

    Returns a response string, or "" to send nothing (non-command messages
    from unknown users are silently dropped so the bot doesn't spam).
    """
    body = body.strip()

    if not body.startswith("!"):
        return ""

    if not database.is_admin(sink, sender):
        return "Sorry, you are not authorised to control this bot."

    admin = database.get_admin_by_identity(sink, sender)
    issuer_name = admin["name"] if admin else "unknown"

    parts = body.split(maxsplit=1)
    command = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if command == "!help":
        return HELP_TEXT

    if command == "!status":
        return _cmd_status()

    if command == "!add-feed":
        return await _cmd_add_feed(arg)

    if command == "!remove-feed":
        return _cmd_remove_feed(arg)

    if command == "!list-feeds":
        return _cmd_list_feeds()

    if command == "!add-admin":
        return _cmd_add_admin(arg, issuer_name)

    if command == "!remove-admin":
        return _cmd_remove_admin(arg, sink, sender)

    if command == "!list-admins":
        return _cmd_list_admins()

    return f"Unknown command: {command}\nType !help for a list of commands."


def _cmd_status() -> str:
    feeds = database.get_all_active_feeds()
    if not feeds:
        return "RRSS is running. No active feeds."

    next_due = min(f.next_check_at for f in feeds)
    last_ok = max(f.last_success_at for f in feeds)

    return "\n".join(
        [
            "RRSS is running.",
            f"Active feeds: {len(feeds)}",
            f"Next poll:    {next_due.strftime('%Y-%m-%d %H:%M:%S UTC')}",
            f"Last success: {last_ok.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        ]
    )


async def _cmd_add_feed(url: str) -> str:
    if not url:
        return "Usage: !add-feed <url>"

    if not url.startswith(("http://", "https://")):
        return f"'{url}' doesn't look like a valid URL."

    existing = database.get_feed_from_url(url)
    if existing is not None:
        status = "disabled" if existing.disabled else "active"
        return f"Feed already exists ({status}): {url}"

    try:
        await general_logic.add_new_website(url)
        return f"Feed added: {url}"
    except Exception as exc:
        return f"Failed to add feed: {exc}"


def _cmd_remove_feed(url: str) -> str:
    if not url:
        return "Usage: !remove-feed <url>"

    feed = database.get_feed_from_url(url)
    if feed is None:
        return f"No feed found for: {url}"

    database.delete_feed(feed.id)
    return f"Feed removed: {url}"


def _cmd_list_feeds() -> str:
    feeds = database.get_all_active_feeds()
    if not feeds:
        return "No active feeds."

    lines = ["Active feeds:"]
    for f in feeds:
        label = f.title or "(no title)"
        lines.append(f"  [{f.id}] {label}\n       {f.feed_url}")

    return "\n".join(lines)


def _cmd_add_admin(arg: str, issuer_name: str) -> str:
    """
    Syntax: !add-admin <name> <sink> <handle>

    Examples:
        !add-admin alice matrix @alice:example.com
        !add-admin alice fluxer alice@fluxer.social
    """
    parts = arg.split(maxsplit=2)
    if len(parts) != 3:
        return "Usage: !add-admin <name> <sink> <handle>"

    name, sink, handle = parts
    sink = sink.lower()

    known_sinks = list(database.SINK_ENV_MAP.keys())
    if sink not in known_sinks:
        return f"Unknown sink '{sink}'. Known sinks: {', '.join(known_sinks)}"

    if sink == "matrix" and not MXID_RE.match(handle):
        return f"'{handle}' is not a valid Matrix ID. Expected format: @user:domain"

    ok, reason = database.add_admin(name, sink, handle, added_by_name=issuer_name)
    if not ok:
        return reason

    return f"Admin added: {name} ({sink}: {handle})"


def _cmd_remove_admin(arg: str, sink: str, sender: str) -> str:
    """
    Syntax: !remove-admin <sink> <handle>

    Examples:
        !remove-admin matrix @alice:example.com
        !remove-admin fluxer alice@fluxer.social
    """
    parts = arg.split(maxsplit=1)
    if len(parts) != 2:
        return "Usage: !remove-admin <sink> <handle>"

    target_sink, handle = parts
    target_sink = target_sink.lower()

    if target_sink == sink and handle == sender:
        return "You cannot remove your own identity."

    ok, message = database.remove_admin_identity(target_sink, handle)
    return message


def _cmd_list_admins() -> str:
    admins = database.list_admins()
    if not admins:
        return "No admins configured."

    lines = ["Trusted admins:"]
    for a in admins:
        lines.append(f"  {a['name']}  (added {a['added_at'][:10]} by {a['added_by']})")
        for identity in a["identities"]:
            lines.append(f"    {identity['sink']}: {identity['handle']}")

    return "\n".join(lines)
