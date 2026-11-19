"""Provider-independent interface for OPTIONAL external language models (Component C).

Nothing in the trading core imports this module. Callers must handle ``AIUnavailable``;
the default provider is ``NullProvider`` which is always unavailable.
"""

from __future__ import annotations

from typing import Protocol

from apex.core.config import Settings


class AIUnavailable(RuntimeError):
    pass


class LanguageModel(Protocol):
    name: str

    def available(self) -> bool: ...

    def complete(self, prompt: str, *, max_tokens: int = 1024) -> str: ...


class NullProvider:
    name = "none"

    def available(self) -> bool:
        return False

    def complete(self, prompt: str, *, max_tokens: int = 1024) -> str:
        raise AIUnavailable("external AI is disabled (APEX_AI_PROVIDER=none)")


_FACTORIES: dict[str, type] = {"none": NullProvider}


def register_provider(name: str, factory: type) -> None:
    _FACTORIES[name] = factory


def get_provider(settings: Settings) -> LanguageModel:
    factory = _FACTORIES.get(settings.ai_provider)
    if factory is None:
        raise AIUnavailable(f"unknown AI provider {settings.ai_provider!r}")
    return factory() if factory is NullProvider else factory(settings)
