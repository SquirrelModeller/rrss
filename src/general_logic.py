import aiohttp
from database import database
import pull


async def add_new_website(url: str):
    database.generate_database()

    async with aiohttp.ClientSession() as session:
        parsed_feed = await pull.get_feed(session, url)

        database.insert_feed(parsed_feed)

        feed = database.get_feed_from_url(parsed_feed.feed_link)

        feed_entries = await pull.fetch(session, feed)

        database.insert_feed_entries(feed_entries, feed, False)
