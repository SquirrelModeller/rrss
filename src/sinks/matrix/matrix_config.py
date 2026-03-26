"""
Matrix notifier configuration.

Required env vars:
    MATRIX_HOMESERVER    e.g. https://matrix.org
    MATRIX_USER_ID       e.g. @rrss-bot:matrix.org
    MATRIX_PASSWORD      bot account password
    MATRIX_ROOM_IDS      space- or comma-separated list of room IDs
                         e.g. "!abc:matrix.org,!xyz:matrix.org"

Optional:
    RRSS_STATE_DIR       override the state directory (default: $HOME/.local/state/rrss)
    MATRIX_DEVICE_NAME   device label shown in Matrix  (default: rrss-bot)
"""

import os
from dataclasses import dataclass, field


def _state_dir() -> str:
    if explicit := os.environ.get("RRSS_STATE_DIR"):
        return explicit
    xdg = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state"
    )
    return os.path.join(xdg, "rrss")


def _parse_room_ids(raw: str) -> list[str]:
    """Accept comma- or space-separated room IDs, strip whitespace, drop empties."""
    import re

    ids = [r.strip() for r in re.split(r"[,\s]+", raw) if r.strip()]
    if not ids:
        raise ValueError("MATRIX_ROOM_IDS is set but contains no valid room IDs")
    return ids


@dataclass
class MatrixConfig:
    homeserver: str
    user_id: str
    password: str
    room_ids: list[str]
    store_path: str
    cred_file: str
    device_name: str
    ignore_unverified_devices: bool = True

    @classmethod
    def from_env(cls) -> "MatrixConfig":
        missing = [
            v
            for v in (
                "MATRIX_HOMESERVER",
                "MATRIX_USER_ID",
                "MATRIX_PASSWORD",
                "MATRIX_ROOM_IDS",
            )
            if not os.environ.get(v)
        ]
        if missing:
            raise EnvironmentError(
                f"Missing required environment variables for Matrix: {', '.join(missing)}"
            )

        state = _state_dir()
        os.makedirs(state, exist_ok=True)

        return cls(
            homeserver=os.environ["MATRIX_HOMESERVER"],
            user_id=os.environ["MATRIX_USER_ID"],
            password=os.environ["MATRIX_PASSWORD"],
            room_ids=_parse_room_ids(os.environ["MATRIX_ROOM_IDS"]),
            store_path=os.path.join(state, "matrix_store"),
            cred_file=os.path.join(state, "matrix_credentials.json"),
            device_name=os.environ.get("MATRIX_DEVICE_NAME", "rrss-bot"),
        )
