"""Session / thread id construction and parsing.

Format: ``doc__<file_token>__user__<user_open_id>``

Per-(doc, user) granularity: same user in same doc share context, different
users in same doc are isolated, same user in different docs is isolated too.

Only ``[A-Za-z0-9_-]`` are allowed in the final string — deerflow's
``_validate_thread_id`` rejects anything else (colons/slashes/etc).
"""
from __future__ import annotations

_PREFIX_DOC = "doc__"
_SEP_USER = "__user__"


def build(file_token: str, user_open_id: str) -> str:
    if not file_token or "__" in file_token:
        raise ValueError(f"invalid file_token: {file_token!r}")
    if not user_open_id or "__" in user_open_id:
        raise ValueError(f"invalid user_open_id: {user_open_id!r}")
    return f"{_PREFIX_DOC}{file_token}{_SEP_USER}{user_open_id}"


def parse(thread_id: str) -> tuple[str, str]:
    """Return (file_token, user_open_id). Raise ValueError on bad input."""
    if not thread_id.startswith(_PREFIX_DOC):
        raise ValueError(f"thread_id missing doc prefix: {thread_id!r}")
    body = thread_id[len(_PREFIX_DOC):]
    sep = body.find(_SEP_USER)
    if sep < 0:
        raise ValueError(f"thread_id missing user segment: {thread_id!r}")
    file_token = body[:sep]
    user_open_id = body[sep + len(_SEP_USER):]
    if not file_token or not user_open_id:
        raise ValueError(f"thread_id has empty segment: {thread_id!r}")
    return file_token, user_open_id
