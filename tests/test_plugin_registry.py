from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Mapping

import pytest

from lark_doc_whisper.plugins.base import (
    CommentPluginRegistry,
    PluginBuildCtx,
    PluginSpec,
    build_registry,
)
from lark_doc_whisper.state.failure_events import FailureEvent


def _failure_event(event_id: str = "evt_1") -> FailureEvent:
    return FailureEvent(
        event_id=event_id,
        file_token="doc_token",
        comment_id="c1",
        reply_id="r1",
        user_id="ou_user",
        session_id="sid",
        stage="backend_chat",
        error_type="RuntimeError",
        error_message="boom",
        fallback_reply_text="soft-fail",
        fallback_reply_succeeded=True,
        created_at=1000.0,
    )


class _RecordingPlugin:
    def __init__(self, name: str) -> None:
        self.name = name
        self.mentions: list = []
        self.failures: list = []

    def on_mention_event(self, header, meta) -> None:
        self.mentions.append((header, meta))

    def on_failure(self, event: FailureEvent) -> None:
        self.failures.append(event)


class _ExplodingPlugin:
    name = "explode"

    def on_mention_event(self, header, meta) -> None:
        raise RuntimeError("mention boom")

    def on_failure(self, event: FailureEvent) -> None:
        raise RuntimeError("failure boom")


def test_empty_spec_yields_no_op_registry():
    registry = build_registry((), PluginBuildCtx(api_client=None))

    # Both dispatch methods must be safe to call and produce no side effects.
    registry.dispatch_mention(header=object(), meta=object())
    registry.dispatch_failure(_failure_event())


def test_unknown_plugin_name_fails_fast():
    with pytest.raises(RuntimeError, match="unknown plugin: not_a_thing"):
        build_registry(
            (PluginSpec(name="not_a_thing", options={}),),
            PluginBuildCtx(api_client=None),
        )


def test_registry_dispatches_to_all_plugins_in_order():
    p1 = _RecordingPlugin("a")
    p2 = _RecordingPlugin("b")
    registry = CommentPluginRegistry((p1, p2))

    header = object()
    meta = object()
    registry.dispatch_mention(header=header, meta=meta)
    event = _failure_event()
    registry.dispatch_failure(event)

    assert p1.mentions == [(header, meta)]
    assert p2.mentions == [(header, meta)]
    assert p1.failures == [event]
    assert p2.failures == [event]


def test_registry_isolates_plugin_exceptions(caplog):
    good = _RecordingPlugin("good")
    registry = CommentPluginRegistry((_ExplodingPlugin(), good))

    with caplog.at_level(logging.WARNING, logger="lark_doc_whisper.plugins.base"):
        registry.dispatch_mention(header=object(), meta=object())
        registry.dispatch_failure(_failure_event())

    # Good plugin still gets invoked, exceptions swallowed.
    assert len(good.mentions) == 1
    assert len(good.failures) == 1
    messages = " ".join(rec.getMessage() for rec in caplog.records)
    assert "plugin explode" in messages


def test_build_registry_invokes_factory_with_options_and_ctx():
    captured: dict[str, Any] = {}

    class _Fake:
        name = "fake"

        def __init__(self, options: Mapping[str, Any], ctx: PluginBuildCtx) -> None:
            captured["options"] = options
            captured["ctx"] = ctx

        def on_mention_event(self, header, meta) -> None: ...

        def on_failure(self, event) -> None: ...

    def _factory(ctx: PluginBuildCtx, options: Mapping[str, Any]) -> "_Fake":
        return _Fake(options, ctx)

    build_ctx = PluginBuildCtx(api_client="stub")
    registry = build_registry(
        (PluginSpec(name="fake", options={"k": "v"}),),
        build_ctx,
        factories={"fake": _factory},
    )

    assert captured["options"] == {"k": "v"}
    assert captured["ctx"] is build_ctx
    # Registry is functional.
    registry.dispatch_mention(header=None, meta=None)
