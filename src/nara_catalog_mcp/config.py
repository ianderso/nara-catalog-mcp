"""Configuration from environment variables.

===================  =========================================================
``NARA_API_KEY``     Key issued by the Catalog API team. Required by the
                     Catalog tools; the AAD tools need none.
``NARA_CACHE_DIR``   Directory for the on-disk response cache.
``NARA_TIMEOUT``     HTTP timeout in seconds.
``NARA_MONTHLY_CALL_BUDGET``
                     Calls the key is allowed per month. Default 10000.
===================  =========================================================

A ``.env`` file in the working directory supplies any of these that the
environment does not.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

#: Default monthly quota on a Catalog API key.
DEFAULT_CALL_BUDGET = 10_000

#: The value ``.env.example`` ships with, which is not a key.
PLACEHOLDER_KEY = "your-key-here"


class ConfigError(RuntimeError):
    """Raised when configuration is missing or invalid."""


@dataclass
class Config:
    """Resolved server configuration.

    Attributes
    ----------
    api_key : str
        Catalog API key.
    cache_dir : Path
        Directory holding cached responses.
    timeout : float
        HTTP timeout in seconds.
    monthly_call_budget : int
        Calls the key is allowed per month, used to report remaining headroom.
    """

    api_key: str
    cache_dir: Path = field(default_factory=lambda: Path.home() / ".cache" / "nara-catalog-mcp")
    timeout: float = 60.0
    monthly_call_budget: int = DEFAULT_CALL_BUDGET


def load_config(*, require_key: bool = True) -> Config:
    """Load configuration from the environment.

    A ``.env`` file in the working directory is read if present; real
    environment variables win. Only the working directory is consulted.
    ``load_dotenv()`` with no path searches upward from the *calling
    module's* location instead, which for an installed package is
    ``site-packages``: it would ignore the ``.env`` beside the user and could
    read an unrelated one from a parent such as the home directory.

    Parameters
    ----------
    require_key : bool, optional
        False for a caller that needs only the other settings: the AAD
        tools read a service that takes no key. A missing key is then an
        empty ``api_key`` rather than an error.

    Returns
    -------
    Config
        Fully resolved configuration.

    Raises
    ------
    ConfigError
        If the key is required and ``NARA_API_KEY`` is unset or still the
        ``.env.example`` placeholder, or a numeric setting is not a positive
        number. The server loads its
        configuration on the first tool call, so this surfaces there as a
        ``not_configured`` result rather than as a crash at launch.
    """
    load_dotenv(Path.cwd() / ".env")

    api_key = (os.environ.get("NARA_API_KEY") or "").strip()
    if not require_key and api_key in ("", PLACEHOLDER_KEY):
        api_key = ""
    elif not api_key:
        raise ConfigError(
            "NARA_API_KEY is not set. Request a Catalog API key at "
            "https://www.archives.gov/research/catalog/help/api and put it in "
            "the environment, or in a .env file in the directory the server "
            "starts in. See README 'Configuration'."
        )
    if api_key == PLACEHOLDER_KEY:
        raise ConfigError(
            "NARA_API_KEY is still the placeholder copied from .env.example. "
            "Replace it with the key the Catalog API team issued."
        )

    cfg = Config(api_key=api_key)
    if raw := os.environ.get("NARA_CACHE_DIR"):
        cfg.cache_dir = Path(raw).expanduser()
    if raw := os.environ.get("NARA_TIMEOUT"):
        cfg.timeout = _positive("NARA_TIMEOUT", raw, float)
    if raw := os.environ.get("NARA_MONTHLY_CALL_BUDGET"):
        cfg.monthly_call_budget = _positive("NARA_MONTHLY_CALL_BUDGET", raw, int)
    return cfg


def _positive(name: str, raw: str, kind: type[int] | type[float]) -> int | float:
    """Parse one numeric setting, naming the variable if it is unusable.

    A bare ``float("sixty")`` would surface as "could not convert string to
    float", which does not say which of several settings is wrong.
    """
    try:
        value = kind(raw.strip())
    except ValueError:
        noun = "a whole number" if kind is int else "a number"
        raise ConfigError(f"{name} must be {noun}; got {raw!r}.") from None
    if not math.isfinite(value) or value <= 0:
        raise ConfigError(f"{name} must be greater than zero; got {raw!r}.")
    return value
