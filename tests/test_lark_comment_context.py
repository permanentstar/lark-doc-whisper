from __future__ import annotations

from types import SimpleNamespace

from lark_doc_whisper.lark.comments import (
    CommentContext,
    get_comment_context,
    get_comment_thread_history,
    get_reply_text,
)


class _Resp:
    def __init__(self, ok: bool, data=None):
        self.data = data
        self._ok = ok

    def success(self) -> bool:
        return self._ok


class _FileCommentApi:
    def __init__(
        self,
        resp: _Resp,
        *,
        list_resp: _Resp | None = None,
        batch_resp: _Resp | None = None,
    ):
        self.resp = resp
        self.list_resp = list_resp or _Resp(True, SimpleNamespace(items=[]))
        self.batch_resp = batch_resp or _Resp(True, SimpleNamespace(items=[]))
        self.requests = []
        self.list_requests = []
        self.batch_requests = []

    def get(self, req):
        self.requests.append(req)
        return self.resp

    def list(self, req):
        self.list_requests.append(req)
        return self.list_resp

    def batch_query(self, req):
        self.batch_requests.append(req)
        return self.batch_resp


class _FileCommentReplyApi:
    def __init__(self, list_resp: _Resp):
        self.list_resp = list_resp
        self.list_requests = []

    def list(self, req):
        self.list_requests.append(req)
        return self.list_resp


class _Client:
    def __init__(
        self,
        resp: _Resp,
        *,
        list_resp: _Resp | None = None,
        batch_resp: _Resp | None = None,
        reply_list_resp: _Resp | None = None,
    ):
        self.drive = SimpleNamespace(
            v1=SimpleNamespace(
                file_comment=_FileCommentApi(
                    resp,
                    list_resp=list_resp,
                    batch_resp=batch_resp,
                ),
                file_comment_reply=_FileCommentReplyApi(
                    reply_list_resp or _Resp(True, SimpleNamespace(items=[])),
                ),
            )
        )


def _reply(reply_id: str, text: str, *, docs_url: str = "") -> SimpleNamespace:
    elements = [
        SimpleNamespace(type="text_run", text_run=SimpleNamespace(text=text)),
    ]
    if docs_url:
        elements.append(
            SimpleNamespace(type="docs_link", docs_link=SimpleNamespace(url=docs_url))
        )
    return SimpleNamespace(
        reply_id=reply_id,
        content=SimpleNamespace(elements=elements),
    )


def test_get_comment_context_uses_batch_query_for_partial_comment():
    batch_resp = _Resp(
        True,
        SimpleNamespace(
            items=[
                SimpleNamespace(
                    comment_id="123",
                    quote="接口契约原文",
                    is_whole=False,
                    relation=SimpleNamespace(
                        content_deleted=False,
                        relation='{"22-doc_token":{"positionInfo":{"blockID":"blk_anchor"}}}',
                    ),
                ),
            ],
        ),
    )
    client = _Client(
        _Resp(False),
        batch_resp=batch_resp,
    )

    ctx = get_comment_context(client, "doc_token", "docx", "123")

    assert ctx == CommentContext(
        quote="接口契约原文",
        is_whole=False,
        anchor_block_id="blk_anchor",
    )
    assert client.drive.v1.file_comment.requests == []
    assert client.drive.v1.file_comment.list_requests == []
    req = client.drive.v1.file_comment.batch_requests[0]
    assert req.file_token == "doc_token"
    assert req.file_type == "docx"
    assert req.user_id_type == "open_id"
    assert req.request_body.comment_ids == ["123"]


def test_get_comment_context_returns_empty_on_api_failure():
    client = _Client(_Resp(False), batch_resp=_Resp(False))

    assert get_comment_context(client, "doc_token", "docx", "123") == CommentContext()
    assert len(client.drive.v1.file_comment.batch_requests) == 1


def test_get_comment_context_returns_empty_on_empty_comment_id():
    client = _Client(_Resp(True, SimpleNamespace(quote="should not call", is_whole=True)))

    assert get_comment_context(client, "doc_token", "docx", "") == CommentContext()
    assert client.drive.v1.file_comment.requests == []
    assert client.drive.v1.file_comment.batch_requests == []


def test_get_comment_context_returns_empty_when_comment_missing_in_batch_query():
    client = _Client(
        _Resp(False),
        batch_resp=_Resp(True, SimpleNamespace(items=[])),
    )

    ctx = get_comment_context(client, "doc_token", "docx", "123")

    assert ctx == CommentContext()
    assert len(client.drive.v1.file_comment.batch_requests) == 1


def test_get_comment_context_ignores_deleted_relation_anchor():
    batch_resp = _Resp(
        True,
        SimpleNamespace(
            items=[
                SimpleNamespace(
                    comment_id="123",
                    quote="接口契约原文",
                    is_whole=False,
                    relation=SimpleNamespace(
                        content_deleted=True,
                        relation='{"22-doc_token":{"positionInfo":{"blockID":"blk_deleted"}}}',
                    ),
                ),
            ],
        ),
    )
    client = _Client(_Resp(False), batch_resp=batch_resp)

    ctx = get_comment_context(client, "doc_token", "docx", "123")

    assert ctx == CommentContext(quote="接口契约原文", is_whole=False)


def test_get_reply_text_preserves_docs_link_url():
    docs_url = "https://bytedance.sg.larkoffice.com/docx/TnSfdcFZDoSN3wxXhnVlGhVQgDb"
    client = _Client(
        _Resp(False),
        reply_list_resp=_Resp(
            True,
            SimpleNamespace(
                items=[
                    _reply("r1", "可以先看看这个文档 ", docs_url=docs_url),
                ],
            ),
        ),
    )

    text = get_reply_text(client, "doc_token", "docx", "123", "r1")

    assert "可以先看看这个文档" in text
    assert docs_url in text


def test_get_comment_thread_history_excludes_current_reply_and_keeps_recent_window():
    client = _Client(
        _Resp(False),
        reply_list_resp=_Resp(
            True,
            SimpleNamespace(
                items=[
                    _reply("r1", "第一问"),
                    _reply("r2", "第一答"),
                    _reply("r3", "第二问"),
                    _reply("r4", "当前追问"),
                ],
            ),
        ),
    )

    history = get_comment_thread_history(
        client,
        "doc_token",
        "docx",
        "123",
        current_reply_id="r4",
        limit=2,
        max_chars=1000,
    )

    assert "第一问" not in history
    assert "第一答" in history
    assert "第二问" in history
    assert "当前追问" not in history
    assert 'reply_id="r2"' in history
    assert 'reply_id="r3"' in history
    req = client.drive.v1.file_comment_reply.list_requests[0]
    assert req.file_token == "doc_token"
    assert req.file_type == "docx"
    assert req.comment_id == "123"


def test_get_comment_thread_history_truncates_to_char_budget():
    client = _Client(
        _Resp(False),
        reply_list_resp=_Resp(
            True,
            SimpleNamespace(items=[_reply("r1", "A" * 200)]),
        ),
    )

    history = get_comment_thread_history(
        client,
        "doc_token",
        "docx",
        "123",
        current_reply_id="r2",
        limit=8,
        max_chars=40,
    )

    assert len(history) <= 40
    assert "[truncated]" in history
