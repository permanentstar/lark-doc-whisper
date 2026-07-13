"""Plugin package.

See :mod:`lark_doc_whisper.plugins.base` for the plugin contract.
"""
from .base import (
    CommentPlugin,
    CommentPluginRegistry,
    PluginBuildCtx,
    PluginSpec,
    build_registry,
    default_factories,
)

__all__ = [
    "CommentPlugin",
    "CommentPluginRegistry",
    "PluginBuildCtx",
    "PluginSpec",
    "build_registry",
    "default_factories",
]
