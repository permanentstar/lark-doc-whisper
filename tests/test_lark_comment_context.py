from __future__ import annotations

from types import SimpleNamespace

import pytest

from lark_doc_whisper.lark.comments import (
    CommentContext,
    get_comment_context,
    get_comment_thread_history,
    get_reply_text,
)


_SUPPORTED_FEISHU_LINK_CASES = [
    ("https://bytedance.sg.larkoffice.com/docx/dx1", "产品文档"),
    ("https://bytedance.sg.larkoffice.com/wiki/wk1", "知识库节点"),
    ("https://bytedance.sg.larkoffice.com/sheets/sh1", "电子表格"),
    ("https://bytedance.sg.larkoffice.com/base/ba1", "多维表格"),
    ("https://bytedance.sg.larkoffice.com/bitable/bi1", "多维表格视图"),
    ("https://bytedance.sg.larkoffice.com/slides/sl1", "幻灯片"),
    ("https://bytedance.sg.larkoffice.com/file/fl1", "云盘文件"),
    ("https://bytedance.sg.larkoffice.com/board/bd1", "飞书画板"),
    ("https://bytedance.sg.larkoffice.com/docs/lg1", "旧版飞书文档"),
    ("https://bytedance.sg.larkoffice.com/mindnotes/mn1", "思维笔记"),
]


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


def _reply(
    reply_id: str,
    text: str,
    *,
    docs_url: str = "",
    link_url: str = "",
    link_label: str = "",
    person_name: str = "",
    mention_name: str = "",
    user_id: str = "ou_user",
) -> SimpleNamespace:
    elements = [
        SimpleNamespace(type="text_run", text_run=SimpleNamespace(text=text)),
    ]
    if person_name:
        elements.append(
            SimpleNamespace(type="person", person=SimpleNamespace(name=person_name))
        )
    if mention_name:
        elements.append(
            SimpleNamespace(type="mention_user", mention_user=SimpleNamespace(name=mention_name))
        )
    if docs_url:
        elements.append(
            SimpleNamespace(type="docs_link", docs_link=SimpleNamespace(url=docs_url))
        )
    if link_url:
        elements.append(
            SimpleNamespace(
                type="link",
                link=SimpleNamespace(url=link_url, text=link_label) if link_label else link_url,
            )
        )
    return SimpleNamespace(
        reply_id=reply_id,
        user_id=user_id,
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


def test_get_reply_text_preserves_plain_link_url():
    sheet_url = "https://bytedance.sg.larkoffice.com/sheets/ss_token?sheet=sh_1"
    client = _Client(
        _Resp(False),
        reply_list_resp=_Resp(
            True,
            SimpleNamespace(
                items=[
                    _reply("r1", "请分析这个表格 ", link_url=sheet_url),
                ],
            ),
        ),
    )

    text = get_reply_text(client, "doc_token", "docx", "123", "r1")

    assert "请分析这个表格" in text
    assert sheet_url in text


def test_get_reply_text_preserves_plain_link_label_and_url():
    sheet_url = "https://bytedance.sg.larkoffice.com/sheets/ss_token?sheet=sh_1"
    client = _Client(
        _Resp(False),
        reply_list_resp=_Resp(
            True,
            SimpleNamespace(
                items=[
                    _reply("r1", "请分析这个表格 ", link_url=sheet_url, link_label="Q3容量迁移分析"),
                ],
            ),
        ),
    )

    text = get_reply_text(client, "doc_token", "docx", "123", "r1")

    assert "请分析这个表格" in text
    assert "Q3容量迁移分析" in text
    assert sheet_url in text


@pytest.mark.parametrize(("link_url", "link_label"), _SUPPORTED_FEISHU_LINK_CASES)
def test_get_reply_text_preserves_supported_feishu_link_matrix(link_url: str, link_label: str):
    client = _Client(
        _Resp(False),
        reply_list_resp=_Resp(
            True,
            SimpleNamespace(
                items=[
                    _reply("r1", "请分析这个链接 ", link_url=link_url, link_label=link_label),
                ],
            ),
        ),
    )

    text = get_reply_text(client, "doc_token", "docx", "123", "r1")

    assert "请分析这个链接" in text
    assert link_label in text
    assert link_url in text


def test_get_reply_text_preserves_person_and_mention_names():
    client = _Client(
        _Resp(False),
        reply_list_resp=_Resp(
            True,
            SimpleNamespace(
                items=[
                    _reply("r1", "请继续排查", person_name="抖云", mention_name="苏恒"),
                ],
            ),
        ),
    )

    text = get_reply_text(client, "doc_token", "docx", "123", "r1")

    assert "@抖云" in text
    assert "@苏恒" in text
    assert "请继续排查" in text


def test_get_reply_text_reads_top_level_comment_from_file_comment_when_reply_id_missing():
    sheet_url = "https://bytedance.sg.larkoffice.com/sheets/ss_token?sheet=sh_1"
    client = _Client(
        _Resp(
            True,
            SimpleNamespace(
                reply_list=SimpleNamespace(
                    replies=[
                        _reply("root_reply", "请分析这个表格 ", docs_url=sheet_url, user_id="ou_user"),
                        _reply("old_bot_reply", "旧回复", user_id="ou_bot"),
                    ],
                ),
            ),
        ),
        reply_list_resp=_Resp(
            True,
            SimpleNamespace(items=[_reply("old_bot_reply", "旧回复", user_id="ou_bot")]),
        ),
    )

    text = get_reply_text(
        client,
        "doc_token",
        "docx",
        "123",
        None,
        from_user_open_id="ou_user",
    )

    assert "请分析这个表格" in text
    assert sheet_url in text


def test_get_reply_text_reads_top_level_comment_link_label_and_url_from_file_comment():
    sheet_url = "https://bytedance.sg.larkoffice.com/sheets/ss_token?sheet=sh_1"
    client = _Client(
        _Resp(
            True,
            SimpleNamespace(
                reply_list=SimpleNamespace(
                    replies=[
                        _reply(
                            "root_reply",
                            "请看这个表格 ",
                            link_url=sheet_url,
                            link_label="Q3容量迁移分析",
                            user_id="ou_user",
                        ),
                        _reply("old_bot_reply", "旧回复", user_id="ou_bot"),
                    ],
                ),
            ),
        ),
        reply_list_resp=_Resp(
            True,
            SimpleNamespace(items=[_reply("old_bot_reply", "旧回复", user_id="ou_bot")]),
        ),
    )

    text = get_reply_text(
        client,
        "doc_token",
        "docx",
        "123",
        None,
        from_user_open_id="ou_user",
    )

    assert "请看这个表格" in text
    assert "Q3容量迁移分析" in text
    assert sheet_url in text


@pytest.mark.parametrize(("link_url", "link_label"), _SUPPORTED_FEISHU_LINK_CASES)
def test_get_reply_text_reads_top_level_supported_feishu_link_matrix(link_url: str, link_label: str):
    client = _Client(
        _Resp(
            True,
            SimpleNamespace(
                reply_list=SimpleNamespace(
                    replies=[
                        _reply(
                            "root_reply",
                            "请看这个链接 ",
                            link_url=link_url,
                            link_label=link_label,
                            user_id="ou_user",
                        ),
                        _reply("old_bot_reply", "旧回复", user_id="ou_bot"),
                    ],
                ),
            ),
        ),
        reply_list_resp=_Resp(
            True,
            SimpleNamespace(items=[_reply("old_bot_reply", "旧回复", user_id="ou_bot")]),
        ),
    )

    text = get_reply_text(
        client,
        "doc_token",
        "docx",
        "123",
        None,
        from_user_open_id="ou_user",
    )

    assert "请看这个链接" in text
    assert link_label in text
    assert link_url in text


def test_get_reply_text_falls_back_to_file_comment_when_exact_reply_missing_from_reply_api():
    docs_url = "https://bytedance.sg.larkoffice.com/bitable/app_token?table=tbl_1"
    client = _Client(
        _Resp(
            True,
            SimpleNamespace(
                reply_list=SimpleNamespace(
                    replies=[
                        _reply("r1", "旧问题", user_id="ou_user"),
                        _reply("r2", "看这个多维表 ", docs_url=docs_url, user_id="ou_user"),
                    ],
                ),
            ),
        ),
        reply_list_resp=_Resp(
            True,
            SimpleNamespace(items=[_reply("r1", "旧问题", user_id="ou_user")]),
        ),
    )

    text = get_reply_text(
        client,
        "doc_token",
        "docx",
        "123",
        "r2",
        from_user_open_id="ou_user",
    )

    assert "看这个多维表" in text
    assert docs_url in text


def test_get_reply_text_reads_top_level_comment_plain_link_from_file_comment():
    sheet_url = "https://bytedance.sg.larkoffice.com/sheets/ss_token?sheet=sh_1"
    client = _Client(
        _Resp(
            True,
            SimpleNamespace(
                reply_list=SimpleNamespace(
                    replies=[
                        _reply("root_reply", "请看这个链接 ", link_url=sheet_url, user_id="ou_user"),
                        _reply("old_bot_reply", "旧回复", user_id="ou_bot"),
                    ],
                ),
            ),
        ),
        reply_list_resp=_Resp(
            True,
            SimpleNamespace(items=[_reply("old_bot_reply", "旧回复", user_id="ou_bot")]),
        ),
    )

    text = get_reply_text(
        client,
        "doc_token",
        "docx",
        "123",
        None,
        from_user_open_id="ou_user",
    )

    assert "请看这个链接" in text
    assert sheet_url in text


def test_get_comment_thread_history_preserves_link_label_and_url():
    sheet_url = "https://bytedance.sg.larkoffice.com/sheets/ss_token?sheet=sh_1"
    client = _Client(
        _Resp(False),
        reply_list_resp=_Resp(
            True,
            SimpleNamespace(
                items=[
                    _reply("r1", "第一问"),
                    _reply("r2", "先看这个表格 ", link_url=sheet_url, link_label="Q3容量迁移分析"),
                    _reply("r3", "当前追问"),
                ],
            ),
        ),
    )

    history = get_comment_thread_history(
        client,
        "doc_token",
        "docx",
        "123",
        current_reply_id="r3",
        limit=8,
        max_chars=1000,
    )

    assert "Q3容量迁移分析" in history
    assert sheet_url in history


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
