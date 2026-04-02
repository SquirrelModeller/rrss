"""
Sink registry.

To add a new sink:
  1. Implement its config class with try_from_env() and build().
  2. Add it to _CONFIGS below.

That's it. main.py never needs to change.
"""

from sinks.base import Sink
from sinks.matrix.matrix_config import MatrixConfig

_CONFIGS = [
    MatrixConfig,
    # FluxerConfig,
]


def load_sinks() -> list[Sink]:
    """
    Attempt to load every known sink from environment variables.

    - Sink with no env vars set -> skipped silently (not configured).
    - Sink with some but not all required vars -> EnvironmentError (misconfigured).
    - Returns at least one sink or raises RuntimeError.
    """
    sinks: list[Sink] = []

    for config_cls in _CONFIGS:
        cfg = config_cls.try_from_env()
        if cfg is not None:
            sinks.append(cfg.build())

    if not sinks:
        raise RuntimeError(
            "No sinks configured. Set environment variables for at least one sink."
        )

    return sinks
