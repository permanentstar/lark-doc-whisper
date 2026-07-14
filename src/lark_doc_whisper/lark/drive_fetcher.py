"""Fetch read-only metadata for Feishu Drive resources."""
from __future__ import annotations

import logging

import lark_oapi as lark
from lark_oapi.api.drive.v1 import BatchQueryMetaRequest
from lark_oapi.api.drive.v1.model import MetaRequest, RequestDoc

logger = logging.getLogger(__name__)


def fetch_file_metadata_text(client: lark.Client, file_token: str, file_type: str = "file") -> str:
    """Return a compact metadata summary for a Drive file-like resource."""
    request_doc = RequestDoc.builder().doc_token(file_token).doc_type(file_type).build()
    body = MetaRequest.builder().request_docs([request_doc]).with_url(True).build()
    req = BatchQueryMetaRequest.builder().request_body(body).build()

    try:
        resp = client.drive.v1.meta.batch_query(req)
    except Exception:
        logger.warning("drive metadata query failed token=%s type=%s", file_token, file_type, exc_info=True)
        return ""
    if not resp.success() or not getattr(resp, "data", None):
        logger.warning(
            "drive metadata query failed token=%s type=%s code=%s msg=%s",
            file_token, file_type, getattr(resp, "code", "?"), getattr(resp, "msg", ""),
        )
        return ""

    metas = list(getattr(resp.data, "metas", None) or [])
    if not metas:
        return ""
    meta = metas[0]
    lines = [
        "飞书云盘文件元信息：",
        f"- 标题：{getattr(meta, 'title', '') or '(无标题)'}",
        f"- 类型：{getattr(meta, 'doc_type', '') or file_type}",
        f"- token：{getattr(meta, 'doc_token', '') or file_token}",
    ]
    if getattr(meta, "owner_id", None):
        lines.append(f"- 所有者：{meta.owner_id}")
    if getattr(meta, "latest_modify_user", None):
        lines.append(f"- 最近修改人：{meta.latest_modify_user}")
    if getattr(meta, "latest_modify_time", None):
        lines.append(f"- 最近修改时间：{meta.latest_modify_time}")
    if getattr(meta, "sec_label_name", None):
        lines.append(f"- 密级：{meta.sec_label_name}")
    if getattr(meta, "url", None):
        lines.append(f"- URL：{meta.url}")
    lines.append("说明：当前仅读取文件元信息，不下载或解析二进制内容。")
    return "\n".join(lines)
