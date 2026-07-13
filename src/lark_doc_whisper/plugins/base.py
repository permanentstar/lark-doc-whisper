"""Comment handler plugin contract.

Plugins are optional side effects layered on top of ``handle_comment_event``.
They receive two hooks — ``on_mention_event`` after event extraction, and
``on_failure`` after a failure event is persisted — and are activated by
name via ``AppConfig.plugins``. When no plugin is configured, the registry
degrades to a no-op that the handler can call unconditionally.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, Sequence

from ..state.failure_events import FailureEvent

logger = logging.getLogger(__name__)


class CommentPlugin(Protocol):
    """A comment-handler side effect activated by name."""

    name: str

    def on_mention_event(self, header, meta) -> None:  # pragma: no cover - Protocol
        ...

    def on_failure(self, event: FailureEvent) -> None:  # pragma: no cover - Protocol
        ...


@dataclass(frozen=True)
class PluginSpec:
    """One activation entry: which plugin, and its per-plugin options."""

    name: str
    options: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class PluginBuildCtx:
    """Runtime dependencies exposed to plugin factories at gateway boot."""

    api_client: Any = None
    failure_store: Any = None


PluginFactory = Callable[[PluginBuildCtx, Mapping[str, Any]], CommentPlugin]


class CommentPluginRegistry:
    """Dispatches handler hooks to all registered plugins.

    Exceptions from individual plugins are isolated with a warning so a
    misbehaving plugin cannot poison the reply path.
    """

    def __init__(self, plugins: Sequence[CommentPlugin] = ()) -> None:
        self._plugins: tuple[CommentPlugin, ...] = tuple(plugins)

    @property
    def plugins(self) -> tuple[CommentPlugin, ...]:
        return self._plugins

    def dispatch_mention(self, *, header, meta) -> None:
        for plugin in self._plugins:
            try:
                plugin.on_mention_event(header, meta)
            except Exception:
                logger.warning(
                    "plugin %s on_mention_event failed", getattr(plugin, "name", "?"),
                    exc_info=True,
                )

    def dispatch_failure(self, event: FailureEvent) -> None:
        for plugin in self._plugins:
            try:
                plugin.on_failure(event)
            except Exception:
                logger.warning(
                    "plugin %s on_failure failed", getattr(plugin, "name", "?"),
                    exc_info=True,
                )


def build_registry(
    specs: Sequence[PluginSpec],
    build_ctx: PluginBuildCtx,
    *,
    factories: Mapping[str, PluginFactory] | None = None,
) -> CommentPluginRegistry:
    """Instantiate plugins named in ``specs``.

    Unknown names fail fast so misconfiguration surfaces at boot rather than
    silently degrading observability.
    """
    if not specs:
        return CommentPluginRegistry(())
    resolved = factories if factories is not None else default_factories()
    plugins: list[CommentPlugin] = []
    for spec in specs:
        factory = resolved.get(spec.name)
        if factory is None:
            raise RuntimeError(f"unknown plugin: {spec.name}")
        plugins.append(factory(build_ctx, spec.options))
    return CommentPluginRegistry(tuple(plugins))


def default_factories() -> Mapping[str, PluginFactory]:
    """Built-in plugin factory table. Imported lazily to avoid cycles."""
    from .audit_log import build_audit_log_plugin
    from .lark_admin_notifier import build_admin_notifier_plugin

    return {
        "audit_log": build_audit_log_plugin,
        "admin_notifier": build_admin_notifier_plugin,
    }
