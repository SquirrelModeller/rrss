"""
This was mainly a testing file. Quick and dity.
Keeping it here for reference (for now)
"""

import asyncio
from nio import AsyncClient, MatrixRoom, RoomMessageText

HOMESERVER = "https://matrix.org"
USER_ID = "@rrss-bot:matrix.org"
PASSWORD = ""


class SimpleBot:
    def __init__(self):
        self.client = AsyncClient(HOMESERVER, USER_ID)

    async def on_message(self, room: MatrixRoom, event: RoomMessageText):
        if event.sender == USER_ID:
            return

        print(f"[{room.display_name}] {event.sender}: {event.body}")

        await self.client.room_send(
            room_id=room.room_id,
            message_type="m.room.message",
            content={
                "msgtype": "m.text",
                "body": f"Echo: {event.body}",
            },
        )

    async def run(self):
        self.client.add_event_callback(self.on_message, RoomMessageText)

        resp = await self.client.login(PASSWORD)
        if hasattr(resp, "access_token"):
            print("Logged in")
        else:
            print(f"Login failed: {resp}")
            return

        await self.client.sync_forever(timeout=30000)


if __name__ == "__main__":
    asyncio.run(SimpleBot().run())
