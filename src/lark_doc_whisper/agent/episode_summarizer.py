"""LLM-based episode summarizer for user memory.

Turns a Q/A round into a structured ``{summary, keywords}`` record via a
one-shot LLM call, reusing deerflow's model factory. The caller is responsible
for falling back to rule-based generation when this raises.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Optional

_SYSTEM_PROMPT = (
    "你是一个用户记忆提炼器。给定一轮用户提问与助手回答，请提炼出一条便于日后检索的记忆。"
    "summary 用一两句中文概括这轮对话的主题与结论；"
    "keywords 抽取 3-8 个能代表该对话的检索关键词（中文词或英文术语，去掉噪声）。"
    "只输出一个 JSON 对象，不要代码块、不要额外说明：\n"
    '{"summary": "...", "keywords": ["...", "..."]}'
)

_MAX_KEYWORDS = 12


def _extract_json_object(raw: str) -> Optional[dict]:
    raw = raw.strip()
    fence_match = re.match(r"^```(?:json)?\s*\n?(.*?)\n?\s*```$", raw, re.DOTALL)
    if fence_match:
        raw = fence_match.group(1).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    start = raw.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(raw)):
        c = raw[i]
        if escape:
            escape = False
            continue
        if c == "\\":
            escape = True
            continue
        if c == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(raw[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _build_messages(user_query: str, answer: str, quote: str) -> list[dict[str, str]]:
    user_parts = [f"用户提问：\n{user_query.strip()}", f"助手回答：\n{answer.strip()}"]
    if quote.strip():
        user_parts.append(f"评论划词原文：\n{quote.strip()}")
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]


def _parse_result(raw: str) -> tuple[str, list[str]]:
    parsed = _extract_json_object(raw)
    if not isinstance(parsed, dict):
        raise ValueError("episode summarizer returned no JSON object")

    summary = parsed.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("episode summarizer returned empty summary")

    keywords_raw = parsed.get("keywords")
    if not isinstance(keywords_raw, list):
        raise ValueError("episode summarizer returned non-list keywords")

    keywords: list[str] = []
    for item in keywords_raw:
        if not isinstance(item, str):
            raise ValueError("episode summarizer returned non-string keyword")
        word = item.strip()
        if word and word not in keywords:
            keywords.append(word)
        if len(keywords) >= _MAX_KEYWORDS:
            break

    return summary.strip(), keywords


async def summarize_episode(
    user_query: str,
    answer: str,
    *,
    quote: str = "",
    model: Any = None,
    timeout_sec: float = 10.0,
) -> tuple[str, list[str]]:
    """Return ``(summary, keywords)`` distilled by an LLM.

    Raises on timeout, network error, invalid JSON, or schema violation so the
    caller can fall back to rule-based generation. ``model`` is injectable for
    tests; when omitted it is built from deerflow's config (first model).
    """
    if model is None:
        from deerflow.models import create_chat_model

        model = create_chat_model(thinking_enabled=False, attach_tracing=False)

    messages = _build_messages(user_query, answer, quote)
    response = await asyncio.wait_for(
        model.ainvoke(messages, config={"run_name": "episode_summarizer"}),
        timeout=timeout_sec,
    )
    raw = str(getattr(response, "content", "") or "")
    return _parse_result(raw)
