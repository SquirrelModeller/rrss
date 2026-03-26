"""
Note: this code was written by Claude 3.5.
"""

import asyncio
import json
import os
import traceback

from nio import (
    AsyncClient,
    AsyncClientConfig,
    KeyVerificationCancel,
    KeyVerificationEvent,
    KeyVerificationKey,
    KeyVerificationMac,
    KeyVerificationStart,
    LocalProtocolError,
    LoginResponse,
    MatrixRoom,
    RoomMessageText,
    ToDeviceError,
    ToDeviceMessage,
    UnknownToDeviceEvent,
)

from sinks.matrix_config import MatrixConfig


class VerificationBot:
    def __init__(self, cfg: MatrixConfig):
        self._cfg = cfg
        self.client = self._make_client()

    def _make_client(self, device_id: str | None = None) -> AsyncClient:
        config = AsyncClientConfig(
            encryption_enabled=True,
            store_sync_tokens=True,
        )
        os.makedirs(self._cfg.store_path, exist_ok=True)
        return AsyncClient(
            self._cfg.homeserver,
            self._cfg.user_id,
            device_id=device_id,
            store_path=self._cfg.store_path,
            config=config,
        )

    async def login_or_restore(self) -> bool:
        if os.path.exists(self._cfg.cred_file):
            with open(self._cfg.cred_file) as f:
                creds = json.load(f)

            self.client = self._make_client(device_id=creds["device_id"])
            self.client.restore_login(
                user_id=creds["user_id"],
                device_id=creds["device_id"],
                access_token=creds["access_token"],
            )
            print(f"Restored login — device ID: {creds['device_id']}")
            return True

        resp = await self.client.login(
            self._cfg.password, device_name=self._cfg.device_name
        )
        if isinstance(resp, LoginResponse):
            with open(self._cfg.cred_file, "w") as f:
                json.dump(
                    {
                        "homeserver": self._cfg.homeserver,
                        "user_id": resp.user_id,
                        "device_id": resp.device_id,
                        "access_token": resp.access_token,
                    },
                    f,
                )
            print(f"Logged in — device ID: {resp.device_id}")
            return True

        print(f"Login failed: {resp}")
        return False

    async def _handle_verification_request_event(self, event: UnknownToDeviceEvent):
        if event.type != "m.key.verification.request":
            return

        content = event.source.get("content", {})
        txid = content.get("transaction_id")
        from_device = content.get("from_device")
        methods = content.get("methods", [])

        print(f"\nVerification request from {event.sender} (device {from_device})")
        print(f"  methods: {methods}  txid: {txid}")

        if not txid or not from_device:
            print("Request missing transaction_id or from_device, ignoring.")
            return

        resp = await self.client.to_device(
            ToDeviceMessage(
                type="m.key.verification.ready",
                recipient=event.sender,
                recipient_device=from_device,
                content={
                    "from_device": self.client.device_id,
                    "methods": ["m.sas.v1"],
                    "transaction_id": txid,
                },
            )
        )
        if isinstance(resp, ToDeviceError):
            print(f"Failed to send ready: {resp}")
            return

        print("Sent ready — waiting for verification start...")

    async def to_device_callback(self, event):
        try:
            if isinstance(event, UnknownToDeviceEvent):
                await self._handle_verification_request_event(event)
                return

            if isinstance(event, KeyVerificationStart):
                print(
                    f"\nVerification start from {event.sender} (txid={event.transaction_id})"
                )

                if "emoji" not in event.short_authentication_string:
                    print("Emoji verification not supported by other device, aborting.")
                    return

                resp = await self.client.accept_key_verification(event.transaction_id)
                if isinstance(resp, ToDeviceError):
                    print(f"accept_key_verification failed: {resp}")
                    return

                sas = self.client.key_verifications[event.transaction_id]
                resp = await self.client.to_device(sas.share_key())
                if isinstance(resp, ToDeviceError):
                    print(f"share_key failed: {resp}")
                    return

                print("Accepted and shared key.")

            elif isinstance(event, KeyVerificationKey):
                print(f"\nReceived key (txid={event.transaction_id})")
                sas = self.client.key_verifications[event.transaction_id]
                emoji = sas.get_emoji()

                print("\nCompare these emoji with Element:")
                for symbol, name in emoji:
                    print(f"  {symbol}  {name}")

                answer = input("\nDo they match? [y/N]: ").strip().lower()

                if answer == "y":
                    resp = await self.client.confirm_short_auth_string(
                        event.transaction_id
                    )
                    if isinstance(resp, ToDeviceError):
                        print(f"confirm_short_auth_string failed: {resp}")
                        return
                    print("Confirmed — waiting for MAC...")
                else:
                    await self.client.cancel_key_verification(
                        event.transaction_id, reject=True
                    )
                    print("Verification rejected.")

            elif isinstance(event, KeyVerificationMac):
                print(f"\nReceived MAC (txid={event.transaction_id})")
                sas = self.client.key_verifications[event.transaction_id]

                try:
                    mac_msg = sas.get_mac()
                except LocalProtocolError as e:
                    print(f"Protocol error: {e}")
                    return

                resp = await self.client.to_device(mac_msg)
                if isinstance(resp, ToDeviceError):
                    print(f"Sending MAC failed: {resp}")
                    return

                for recipient, device in [
                    (event.sender, sas.other_olm_device.device_id),
                    (self.client.user_id, "*"),
                ]:
                    await self.client.to_device(
                        ToDeviceMessage(
                            type="m.key.verification.done",
                            recipient=recipient,
                            recipient_device=device,
                            content={"transaction_id": event.transaction_id},
                        )
                    )

                print("\nVerification complete!")
                print(
                    f"  verified={sas.verified}  verified_devices={sas.verified_devices}"
                )

                for device_id in sas.verified_devices:
                    try:
                        device = self.client.device_store[event.sender][device_id]
                        changed = self.client.verify_device(device)
                        print(f"  Locally trusted {device_id}: changed={changed}")
                    except KeyError:
                        print(f"  Device {device_id} not found in store")

                print("\nVerification done — you can Ctrl+C now.")

            elif isinstance(event, KeyVerificationCancel):
                print(
                    f"\nVerification cancelled by {event.sender}: {event.reason} ({event.code})"
                )

            else:
                print(f"Unhandled to-device event: {type(event).__name__}")

        except Exception:
            print(traceback.format_exc())

    async def run(self):
        ok = await self.login_or_restore()
        if not ok:
            return

        self.client.add_to_device_callback(
            self.to_device_callback,
            (
                KeyVerificationEvent,
                KeyVerificationStart,
                KeyVerificationKey,
                KeyVerificationMac,
                KeyVerificationCancel,
                UnknownToDeviceEvent,
            ),
        )

        if self.client.should_upload_keys:
            await self.client.keys_upload()

        print("\nReady for verification.")
        print(
            "In Element: your profile → Security → Verify another session → select this device."
        )
        print("Waiting...\n")

        await self.client.sync_forever(timeout=30000, full_state=True)


async def run_verification():
    cfg = MatrixConfig.from_env()
    bot = VerificationBot(cfg)
    await bot.run()
