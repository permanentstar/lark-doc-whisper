from __future__ import annotations

from types import SimpleNamespace

from lark_doc_whisper.orchestrator.comment_context import CommentContextProvider


class _Resp:
    def __init__(self, ok: bool, data=None):
        self.data = data
        self._ok = ok

    def success(self) -> bool:
        return self._ok


def _text_block(block_id: str, parent_id: str, text: str) -> SimpleNamespace:
    return SimpleNamespace(
        block_id=block_id,
        parent_id=parent_id,
        children=[],
        heading1=None,
        heading2=None,
        heading3=None,
        heading4=None,
        heading5=None,
        heading6=None,
        heading7=None,
        heading8=None,
        heading9=None,
        text=SimpleNamespace(
            elements=[SimpleNamespace(text_run=SimpleNamespace(content=text))],
        ),
        bullet=None,
        ordered=None,
        code=None,
        quote=None,
        todo=None,
        callout=None,
        table=None,
        board=None,
        grid=None,
        grid_column=None,
        page=None,
    )


def _heading_block(block_id: str, parent_id: str, level: int, text: str) -> SimpleNamespace:
    kwargs = {
        "heading1": None,
        "heading2": None,
        "heading3": None,
        "heading4": None,
        "heading5": None,
        "heading6": None,
        "heading7": None,
        "heading8": None,
        "heading9": None,
    }
    kwargs[f"heading{level}"] = SimpleNamespace(
        elements=[SimpleNamespace(text_run=SimpleNamespace(content=text))],
    )
    return SimpleNamespace(
        block_id=block_id,
        parent_id=parent_id,
        children=[],
        text=None,
        bullet=None,
        ordered=None,
        code=None,
        quote=None,
        todo=None,
        callout=None,
        table=None,
        board=None,
        grid=None,
        grid_column=None,
        page=None,
        **kwargs,
    )


class _DocumentBlockApi:
    def __init__(self, blocks: list[SimpleNamespace]):
        self.blocks = blocks
        self.list_requests = []

    def list(self, req):
        self.list_requests.append(req)
        return _Resp(True, SimpleNamespace(items=self.blocks, has_more=False, page_token=""))


class _Client:
    def __init__(self, blocks: list[SimpleNamespace], raw_text: str):
        self.docx = SimpleNamespace(
            v1=SimpleNamespace(
                document_block=_DocumentBlockApi(blocks),
                document=SimpleNamespace(
                    raw_content=lambda req: _Resp(
                        True,
                        SimpleNamespace(content=raw_text),
                    )
                ),
            )
        )


class _ReplyApi:
    def __init__(self, replies: list[SimpleNamespace]):
        self.replies = replies

    def list(self, req):
        return _Resp(True, SimpleNamespace(items=self.replies))


class _ThreadClient:
    def __init__(self, replies: list[SimpleNamespace]):
        self.drive = SimpleNamespace(
            v1=SimpleNamespace(
                file_comment_reply=_ReplyApi(replies),
            )
        )


def _reply(reply_id: str, text: str) -> SimpleNamespace:
    return SimpleNamespace(
        reply_id=reply_id,
        content=SimpleNamespace(
            elements=[
                SimpleNamespace(type="text_run", text_run=SimpleNamespace(text=text)),
            ],
        ),
    )


def test_provider_uses_requested_nearby_window_and_clamps_to_limits():
    doc_id = "doc_1"
    blocks = [
        SimpleNamespace(
            block_id=doc_id,
            parent_id="",
            children=["h1", "p1", "p2", "h2", "p3"],
            heading1=None,
            heading2=None,
            heading3=None,
            heading4=None,
            heading5=None,
            heading6=None,
            heading7=None,
            heading8=None,
            heading9=None,
            text=None,
            bullet=None,
            ordered=None,
            code=None,
            quote=None,
            todo=None,
            callout=None,
            table=None,
            board=None,
            grid=None,
            grid_column=None,
            page=None,
        ),
        _heading_block("h1", doc_id, 2, "10. 配置建议"),
        _text_block("p1", doc_id, "模型只提上下文类别"),
        _text_block("p2", doc_id, "program gate 控窗口和轮次"),
        _heading_block("h2", doc_id, 2, "11. 最终落点"),
        _text_block("p3", doc_id, "回复不再只靠 quote 猜"),
    ]
    client = _Client(blocks, raw_text="整篇文档原文")

    provider = CommentContextProvider(
        client=client,
        file_token=doc_id,
        file_type="docx",
        quote="模型只提上下文类别",
        anchor_block_id="p1",
        default_nearby_before=0,
        default_nearby_after=1,
        max_nearby_before=1,
        max_nearby_after=2,
        max_context_chars=2000,
    )

    requested = provider.resolve("nearby", before_blocks=1, after_blocks=2)

    assert "10. 配置建议" in requested
    assert "模型只提上下文类别" in requested
    assert "program gate 控窗口和轮次" in requested
    assert "11. 最终落点" in requested

    provider = CommentContextProvider(
        client=client,
        file_token=doc_id,
        file_type="docx",
        quote="模型只提上下文类别",
        anchor_block_id="p1",
        default_nearby_before=0,
        default_nearby_after=1,
        max_nearby_before=0,
        max_nearby_after=1,
        max_context_chars=2000,
    )

    clamped = provider.resolve("nearby", before_blocks=99, after_blocks=99)

    assert "10. 配置建议" not in clamped
    assert "模型只提上下文类别" in clamped
    assert "program gate 控窗口和轮次" in clamped
    assert "11. 最终落点" not in clamped


def test_provider_resolves_section_from_anchor():
    doc_id = "doc_1"
    blocks = [
        SimpleNamespace(
            block_id=doc_id,
            parent_id="",
            children=["h1", "p1", "p2", "h2", "p3"],
            heading1=None,
            heading2=None,
            heading3=None,
            heading4=None,
            heading5=None,
            heading6=None,
            heading7=None,
            heading8=None,
            heading9=None,
            text=None,
            bullet=None,
            ordered=None,
            code=None,
            quote=None,
            todo=None,
            callout=None,
            table=None,
            board=None,
            grid=None,
            grid_column=None,
            page=None,
        ),
        _heading_block("h1", doc_id, 2, "10. 配置建议"),
        _text_block("p1", doc_id, "模型只提上下文类别"),
        _text_block("p2", doc_id, "program gate 控窗口和轮次"),
        _heading_block("h2", doc_id, 2, "11. 最终落点"),
        _text_block("p3", doc_id, "回复不再只靠 quote 猜"),
    ]
    client = _Client(blocks, raw_text="整篇文档原文")

    provider = CommentContextProvider(
        client=client,
        file_token=doc_id,
        file_type="docx",
        quote="模型只提上下文类别",
        anchor_block_id="p1",
        max_context_chars=2000,
    )

    section = provider.resolve("section")

    assert "10. 配置建议" in section
    assert "program gate 控窗口和轮次" in section
    assert "11. 最终落点" not in section


def test_provider_returns_empty_nearby_without_anchor():
    client = _Client([], raw_text="整篇文档原文")

    provider = CommentContextProvider(
        client=client,
        file_token="doc_1",
        file_type="docx",
        quote="模型只提上下文类别",
        anchor_block_id="",
    )

    assert provider.resolve("nearby") == ""


def test_provider_rejects_duplicate_context_without_spending_round():
    doc_id = "doc_1"
    blocks = [
        SimpleNamespace(
            block_id=doc_id,
            parent_id="",
            children=["h1", "p1", "p2"],
            heading1=None,
            heading2=None,
            heading3=None,
            heading4=None,
            heading5=None,
            heading6=None,
            heading7=None,
            heading8=None,
            heading9=None,
            text=None,
            bullet=None,
            ordered=None,
            code=None,
            quote=None,
            todo=None,
            callout=None,
            table=None,
            board=None,
            grid=None,
            grid_column=None,
            page=None,
        ),
        _heading_block("h1", doc_id, 2, "背景"),
        _text_block("p1", doc_id, "锚点内容"),
        _text_block("p2", doc_id, "后续内容"),
    ]
    provider = CommentContextProvider(
        client=_Client(blocks, raw_text="整篇文档原文"),
        file_token=doc_id,
        file_type="docx",
        quote="锚点内容",
        anchor_block_id="p1",
        max_fetch_rounds=2,
    )

    first = provider.resolve("nearby", before_blocks=0, after_blocks=0)
    duplicate = provider.resolve("nearby", before_blocks=0, after_blocks=0)
    duplicate_reason = provider.last_unavailable_reason
    section = provider.resolve("section")
    summary = provider.resolve("document_summary")
    summary_reason = provider.last_unavailable_reason

    assert first == "锚点内容"
    assert duplicate == ""
    assert duplicate_reason == "no_new_info"
    assert "锚点内容" in section
    assert "后续内容" in section
    assert summary == ""
    assert summary_reason == "max_fetch_rounds"


def test_provider_enforces_requested_and_total_char_budgets():
    doc_id = "doc_1"
    blocks = [
        SimpleNamespace(
            block_id=doc_id,
            parent_id="",
            children=["p1", "p2"],
            heading1=None,
            heading2=None,
            heading3=None,
            heading4=None,
            heading5=None,
            heading6=None,
            heading7=None,
            heading8=None,
            heading9=None,
            text=None,
            bullet=None,
            ordered=None,
            code=None,
            quote=None,
            todo=None,
            callout=None,
            table=None,
            board=None,
            grid=None,
            grid_column=None,
            page=None,
        ),
        _text_block("p1", doc_id, "abcdef"),
        _text_block("p2", doc_id, "ghijkl"),
    ]
    provider = CommentContextProvider(
        client=_Client(blocks, raw_text="full document that should not fit"),
        file_token=doc_id,
        file_type="docx",
        quote="abcdef",
        anchor_block_id="p1",
        max_nearby_before=0,
        max_nearby_after=1,
        max_context_chars=20,
        max_context_chars_total=4,
    )

    nearby = provider.resolve("nearby", before_blocks=0, after_blocks=1, max_chars=4)
    summary = provider.resolve("document_summary")

    assert nearby == "abcd"
    assert summary == ""
    assert provider.last_unavailable_reason == "max_context_chars_total"


def test_provider_resolves_comment_thread_history_with_clamped_limit():
    provider = CommentContextProvider(
        client=_ThreadClient([
            _reply("r1", "第一轮问题"),
            _reply("r2", "第一轮回答"),
            _reply("r3", "第二轮问题"),
            _reply("r4", "第二轮回答"),
            _reply("r5", "当前追问"),
        ]),
        file_token="doc_1",
        file_type="docx",
        quote="接口契约原文",
        anchor_block_id="blk_anchor",
        comment_id="cmt_1",
        current_reply_id="r5",
        default_thread_history_replies=2,
        max_thread_history_replies=3,
        default_thread_history_chars=3000,
        max_thread_history_chars=8000,
    )

    history = provider.resolve("comment_thread_history", limit=99, max_chars=8000)

    assert "第一轮问题" not in history
    assert "第一轮回答" in history
    assert "第二轮问题" in history
    assert "第二轮回答" in history
    assert "当前追问" not in history
