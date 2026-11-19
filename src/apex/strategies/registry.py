"""Strategy plugin registry. Third-party strategies register via the
``apex.strategies`` entry-point group, so they can be added without touching the core."""

from __future__ import annotations

from importlib.metadata import entry_points

from apex.strategies.base import Strategy

_REGISTRY: dict[str, type[Strategy]] = {}


def register(cls: type[Strategy]) -> type[Strategy]:
    key = cls.meta.key
    if key in _REGISTRY and _REGISTRY[key] is not cls:
        raise ValueError(f"duplicate strategy key {key}")
    _REGISTRY[key] = cls
    return cls


def load_plugins() -> None:
    for ep in entry_points(group="apex.strategies"):
        register(ep.load())


def get(key: str) -> type[Strategy]:
    return _REGISTRY[key]


def all_strategies() -> dict[str, type[Strategy]]:
    from apex.ml import strategy as _ml  # noqa: F401  (registers the ML strategy)
    from apex.strategies import (  # noqa: F401  (registers built-ins)
        builtin,
        builtin2,
        builtin3,
        reference,
    )

    return dict(_REGISTRY)
