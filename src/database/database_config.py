"""
Database configuration.

Optional:
    RRSS_STATE_DIR       override the state directory
                         (default: $HOME/.local/state/rrss)
"""

import os
from dataclasses import dataclass


def _state_dir() -> str:
    if explicit := os.environ.get("RRSS_STATE_DIR"):
        return explicit
    xdg = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state"
    )
    return os.path.join(xdg, "rrss")


@dataclass(frozen=True)
class DatabaseConfig:
    state_dir: str
    db_path: str

    @classmethod
    def from_env(cls) -> "DatabaseConfig":
        state = _state_dir()
        os.makedirs(state, exist_ok=True)

        return cls(
            state_dir=state,
            db_path=os.path.join(state, "rrss_data.db"),
        )
