"""
Matrix sink configuration.

Required env vars (all must be set or none - partial config is an error):
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
from dataclasses import dataclass

_REQUIRED = (
    "MATRIX_HOMESERVER",
    "MATRIX_USER_ID",
    "MATRIX_PASSWORD",
    "MATRIX_ROOM_IDS",
)


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
    def try_from_env(cls) -> "MatrixConfig | None":
        present = [v for v in _REQUIRED if os.environ.get(v)]
        missing = [v for v in _REQUIRED if not os.environ.get(v)]

        if not present:
            return None

        if missing:
            raise EnvironmentError(
                f"Matrix sink is partially configured. Missing: {', '.join(missing)}"
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

    def build(self) -> "MatrixSink":
        from sinks.matrix.matrix import MatrixSink

        return MatrixSink(
            homeserver=self.homeserver,
            user_id=self.user_id,
            password=self.password,
            room_ids=self.room_ids,
            store_path=self.store_path,
            cred_file=self.cred_file,
            device_name=self.device_name,
            ignore_unverified_devices=self.ignore_unverified_devices,
        )
