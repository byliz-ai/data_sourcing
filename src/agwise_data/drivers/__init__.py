"""Driver registry: each catalog entry names the driver that fetches it."""

from __future__ import annotations

from typing import Dict, Type

from ..config import Config

_REGISTRY: Dict[str, Type] = {}


def register(name: str):
    """Class decorator registering a Driver under ``name``."""

    def deco(cls):
        _REGISTRY[name] = cls
        return cls

    return deco


def get_driver(entry: dict, config: Config):
    name = entry.get("driver")
    if name not in _REGISTRY:
        raise KeyError(
            f"No driver '{name}' for source '{entry.get('id')}'. "
            f"Registered drivers: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[name](entry, config)


# Import concrete drivers so they self-register.
from . import agera5, chirps  # noqa: E402,F401
