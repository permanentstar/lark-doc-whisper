"""Program-level policy checks for user comment requests."""
from __future__ import annotations

import re
from dataclasses import dataclass

_URL_RE = re.compile(r"https?://[^\s<>()]+")
_DANGEROUS_PATTERNS = [
    re.compile(r"忽略前面规则"),
    re.compile(r"ignore\s+previous", re.IGNORECASE),
    re.compile(r"/etc/"),
    re.compile(r"\.env\b"),
    re.compile(r"\baccess[_ -]?token\b", re.IGNORECASE),
    re.compile(r"\bsecret\b", re.IGNORECASE),
    re.compile(r"\bapi[_ -]?key\b", re.IGNORECASE),
    re.compile(r"密钥"),
    re.compile(r"读取服务器"),
    re.compile(r"执行命令"),
    re.compile(r"\brm\s+-rf\b", re.IGNORECASE),
    re.compile(r"\bcurl\b", re.IGNORECASE),
    re.compile(r"\bbash\b", re.IGNORECASE),
]


@dataclass(frozen=True)
class AllowedUrl:
    url: str
    kind: str


@dataclass(frozen=True)
class GateDecision:
    blocked: bool
    reply_text: str
    allowed_urls: tuple[AllowedUrl, ...]


def _classify_url(url: str) -> str:
    lowered = url.lower()
    if ".feishu.cn/docx/" in lowered or ".larkoffice.com/docx/" in lowered:
        return "feishu_docx"
    if ".feishu.cn/wiki/" in lowered or ".larkoffice.com/wiki/" in lowered:
        return "feishu_wiki"
    return "external_http"


def extract_allowed_urls(text: str) -> tuple[AllowedUrl, ...]:
    urls: list[AllowedUrl] = []
    for match in _URL_RE.finditer(text):
        url = match.group(0).rstrip(").,]")
        urls.append(AllowedUrl(url=url, kind=_classify_url(url)))
    return tuple(urls)


def evaluate_user_query(user_query: str) -> GateDecision:
    text = user_query.strip()
    for pattern in _DANGEROUS_PATTERNS:
        if pattern.search(text):
            return GateDecision(
                blocked=True,
                reply_text="我只能帮助分析当前文档和受控只读链接内容，不能执行命令、读取服务器信息或进行写操作。",
                allowed_urls=(),
            )

    return GateDecision(blocked=False, reply_text="", allowed_urls=extract_allowed_urls(text))
