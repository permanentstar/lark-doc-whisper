from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from lark_doc_whisper.agent.episode_summarizer import summarize_episode


class _FakeModel:
    def __init__(self, *, content: str | None = None, exc: BaseException | None = None, delay: float = 0.0):
        self._content = content
        self._exc = exc
        self._delay = delay
        self.calls: list[list[dict]] = []

    async def ainvoke(self, messages, **kwargs):
        self.calls.append(messages)
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._exc is not None:
            raise self._exc
        return SimpleNamespace(content=self._content)


def test_summarize_episode_parses_valid_json():
    model = _FakeModel(content='{"summary": "讨论了接口契约的边界问题", "keywords": ["接口契约", "边界"]}')

    summary, keywords = asyncio.run(
        summarize_episode("接口契约有什么问题", "契约需要覆盖边界条件", quote="接口契约原文", model=model)
    )

    assert summary == "讨论了接口契约的边界问题"
    assert keywords == ["接口契约", "边界"]
    # 提示词应携带三段输入
    joined = "".join(m["content"] for m in model.calls[0])
    assert "接口契约有什么问题" in joined
    assert "契约需要覆盖边界条件" in joined
    assert "接口契约原文" in joined


def test_summarize_episode_strips_code_fence():
    model = _FakeModel(content='```json\n{"summary": "s", "keywords": ["k"]}\n```')

    summary, keywords = asyncio.run(summarize_episode("q", "a", model=model))

    assert summary == "s"
    assert keywords == ["k"]


def test_summarize_episode_drops_blank_keywords():
    model = _FakeModel(content='{"summary": "s", "keywords": ["接口", "  ", ""]}')

    _, keywords = asyncio.run(summarize_episode("q", "a", model=model))

    assert keywords == ["接口"]


def test_summarize_episode_raises_on_non_json():
    model = _FakeModel(content="这里没有 JSON 对象")

    with pytest.raises(ValueError):
        asyncio.run(summarize_episode("q", "a", model=model))


def test_summarize_episode_raises_on_missing_summary():
    model = _FakeModel(content='{"keywords": ["k"]}')

    with pytest.raises(ValueError):
        asyncio.run(summarize_episode("q", "a", model=model))


def test_summarize_episode_raises_on_bad_keywords_type():
    model = _FakeModel(content='{"summary": "s", "keywords": "not-a-list"}')

    with pytest.raises(ValueError):
        asyncio.run(summarize_episode("q", "a", model=model))


def test_summarize_episode_raises_on_timeout():
    model = _FakeModel(content='{"summary": "s", "keywords": ["k"]}', delay=0.2)

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(summarize_episode("q", "a", model=model, timeout_sec=0.01))
