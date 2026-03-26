import asyncio
import json
import os
from typing import Optional

from nio import (
    AsyncClient,
    AsyncClientConfig,
    LoginResponse,
    RoomSendError,
)

from sinks.base import Notifier
from models import NotificationMessage, SendResult, SendStatus


class MatrixNotifier(Notifier):
    """
    Sends notifications to one or more Matrix rooms as formatted messages.

    One AsyncClient / one login - messages are sent out to every room_id
    in the list.  The overall SendResult reflects the worst outcome across
    all rooms: SUCCESS only if every room succeeded, RETRY if any room had
    a transient failure, FAILURE if all rooms failed permanently.

    Supports E2E encryption if the nio[e2e] extras are installed and
    a store_path is provided.  Falls back gracefully to unencrypted if
    encryption is unavailable.
    """

    def __init__(
        self,
        homeserver: str,
        user_id: str,
        password: str,
        room_ids: list[str],
        *,
        store_path: str = "./matrix_store",
        cred_file: str = "./matrix_credentials.json",
        device_name: str = "rrss-bot",
        ignore_unverified_devices: bool = True,
    ) -> None:
        if not room_ids:
            raise ValueError("MatrixNotifier requires at least one room_id")

        self._homeserver = homeserver
        self._user_id = user_id
        self._password = password
        self._room_ids = room_ids
        self._store_path = store_path
        self._cred_file = cred_file
        self._device_name = device_name
        self._ignore_unverified_devices = ignore_unverified_devices

        self._client: Optional[AsyncClient] = None
        self._ready = False

    async def send(self, message: NotificationMessage) -> SendResult:
        if not self._ready:
            ok = await self._connect()
            if not ok:
                return SendResult(
                    status=SendStatus.RETRY,
                    error="Matrix login failed; will retry next time.",
                )

        body_plain, body_html = _format_message(message)
        content = {
            "msgtype": "m.text",
            "body": body_plain,
            "format": "org.matrix.custom.html",
            "formatted_body": body_html,
            "m.mentions": {"room": True},
        }

        results: list[SendResult] = await asyncio.gather(
            *[self._send_to_room(room_id, content) for room_id in self._room_ids]
        )

        return _aggregate_results(results)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
            self._ready = False

    async def _send_to_room(self, room_id: str, content: dict) -> SendResult:
        try:
            resp = await self._client.room_send(
                room_id=room_id,
                message_type="m.room.message",
                content=content,
                ignore_unverified_devices=self._ignore_unverified_devices,
            )

            if isinstance(resp, RoomSendError):
                return SendResult(
                    status=SendStatus.RETRY,
                    error=f"[{room_id}] room_send error: {resp.message}",
                )

            return SendResult(status=SendStatus.SUCCESS, message_id=resp.event_id)

        except Exception as exc:
            return SendResult(status=SendStatus.RETRY, error=f"[{room_id}] {exc}")

    async def _connect(self) -> bool:
        os.makedirs(self._store_path, exist_ok=True)

        encryption_available = _encryption_available()

        config = AsyncClientConfig(
            encryption_enabled=encryption_available,
            store_sync_tokens=True,
        )

        if os.path.exists(self._cred_file):
            try:
                with open(self._cred_file) as f:
                    creds = json.load(f)

                self._client = AsyncClient(
                    creds["homeserver"],
                    creds["user_id"],
                    device_id=creds["device_id"],
                    store_path=self._store_path if encryption_available else None,
                    config=config,
                )
                self._client.restore_login(
                    user_id=creds["user_id"],
                    device_id=creds["device_id"],
                    access_token=creds["access_token"],
                )

                await self._client.sync(timeout=5000, full_state=True)

                if encryption_available and self._client.should_upload_keys:
                    await self._client.keys_upload()

                self._ready = True
                print("Connected to matrix!")
                return True

            except Exception as exc:
                print(f"[MatrixNotifier] Could not restore credentials: {exc}")

        self._client = AsyncClient(
            self._homeserver,
            self._user_id,
            store_path=self._store_path if encryption_available else None,
            config=config,
        )

        resp = await self._client.login(self._password, device_name=self._device_name)
        if not isinstance(resp, LoginResponse):
            print(f"[MatrixNotifier] Login failed: {resp}")
            return False

        try:
            with open(self._cred_file, "w") as f:
                json.dump(
                    {
                        "homeserver": self._homeserver,
                        "user_id": resp.user_id,
                        "device_id": resp.device_id,
                        "access_token": resp.access_token,
                    },
                    f,
                )
        except Exception as exc:
            print(f"[MatrixNotifier] Could not save credentials: {exc}")

        await self._client.sync(timeout=5000, full_state=True)

        if encryption_available and self._client.should_upload_keys:
            await self._client.keys_upload()

        self._ready = True
        return True


def _encryption_available() -> bool:
    try:
        import olm  # noqa: F401

        return True
    except ImportError:
        return False


def _aggregate_results(results: list[SendResult]) -> SendResult:
    """
    Merge per-room results into a single SendResult.

    Priority: SUCCESS < RETRY < FAILURE
    The caller (notification job system) only retries on RETRY, so we
    want to surface any transient failure rather than silently swallow it.
    """
    if not results:
        return SendResult(status=SendStatus.FAILURE, error="No rooms to send to")

    statuses = {r.status for r in results}

    if SendStatus.FAILURE in statuses:
        errors = [r.error for r in results if r.error]
        return SendResult(status=SendStatus.FAILURE, error="; ".join(errors))

    if SendStatus.RETRY in statuses:
        errors = [r.error for r in results if r.error]
        return SendResult(status=SendStatus.RETRY, error="; ".join(errors))

    return SendResult(status=SendStatus.SUCCESS, message_id=results[0].message_id)


def _format_message(msg: NotificationMessage) -> tuple[str, str]:
    from html import escape

    title = msg.title.strip()
    source_name = msg.source_name.strip() if msg.source_name else None
    body = msg.body.strip() if msg.body else None
    url = msg.url.strip() if msg.url else None

    if body and len(body) > 300:
        body = body[:297] + "..."

    plain_parts = [title]
    if source_name:
        plain_parts.append(f"via {source_name}")
    if body:
        plain_parts.extend(["", body])
    if url:
        plain_parts.extend(["", url])
    plain = "\n".join(plain_parts)

    e_title = escape(title)
    e_source = escape(source_name) if source_name else None
    e_body = escape(body) if body else None
    e_url = escape(url) if url else None

    title_line = f'<a href="{e_url}">{e_title}</a>' if e_url else e_title

    html_parts = [f"<h3>{title_line}</h3>"]
    if e_source:
        html_parts.append(f"<p><em>via {e_source}</em></p>")
    if e_body:
        html_parts.append(f"<p>{e_body}</p>")
    if e_url:
        html_parts.append(f'<p><a href="{e_url}">Open article</a></p>')

    return plain, "".join(html_parts)

    return plain, html
