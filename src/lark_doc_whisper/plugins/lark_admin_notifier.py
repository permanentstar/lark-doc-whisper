"""Fire-and-forget notifier plugin.

Deliberately off in the OSS default. Every failure event is delivered
asynchronously so the main comment reply loop is never blocked by
outbound IO. To avoid alert storms we suppress duplicate ``(stage,
error_type)`` failures inside a fixed window — suppressed events are
still marked notified so the store does not accumulate a backlog.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Mapping, Sequence

from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

from ..state.failure_events import FailureEvent, default_store as _default_store
from .base import PluginBuildCtx

logger = logging.getLogger(__name__)

SUPPRESSION_WINDOW_SEC: float = 300.0


class LarkAdminNotifierPlugin:
    name = "admin_notifier"

    def __init__(
        self,
        *,
        client,
        recipients: Sequence[Mapping[str, str]],
        failure_store=None,
        suppression_window_sec: float = SUPPRESSION_WINDOW_SEC,
    ) -> None:
        self._client = client
        self._recipients: tuple[dict[str, str], ...] = tuple(
            {"receive_id_type": r["receive_id_type"], "receive_id": r["receive_id"]}
            for r in recipients
        )
        self._failure_store = failure_store if failure_store is not None else _default_store
        self._pending: set[asyncio.Task] = set()
        self._suppression_window_sec = suppression_window_sec
        # (stage, error_type) -> last-send monotonic timestamp
        self._last_sent_by_key: dict[tuple[str, str], float] = {}

    @property
    def pending_tasks(self) -> set[asyncio.Task]:
        return self._pending

    def on_mention_event(self, header, meta) -> None:
        return None

    def on_failure(self, event: FailureEvent) -> None:
        if not self._recipients:
            return
        if self._should_suppress(event):
            # Mark notified so the failure store doesn't grow unbounded, and
            # log at info so operators can see the burst was throttled.
            logger.info(
                "admin_notifier suppressed duplicate failure stage=%s error_type=%s event_id=%s",
                event.stage, event.error_type, event.event_id,
            )
            try:
                self._failure_store.mark_notified(event.event_id)
            except Exception:
                logger.warning(
                    "admin_notifier failed to mark_notified (suppressed) event_id=%s",
                    event.event_id, exc_info=True,
                )
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No event loop (e.g. sync test path) — deliver inline as a fallback.
            self._deliver_sync(event)
            return
        task = loop.create_task(self._deliver(event))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    def _should_suppress(self, event: FailureEvent) -> bool:
        key = (event.stage, event.error_type)
        now = time.monotonic()
        self._evict_expired(now)
        last = self._last_sent_by_key.get(key)
        if last is not None and (now - last) < self._suppression_window_sec:
            return True
        self._last_sent_by_key[key] = now
        return False

    def _evict_expired(self, now: float) -> None:
        """Drop keys older than the window so the map never grows unbounded."""
        window = self._suppression_window_sec
        expired = [k for k, ts in self._last_sent_by_key.items() if (now - ts) >= window]
        for k in expired:
            self._last_sent_by_key.pop(k, None)

    async def _deliver(self, event: FailureEvent) -> None:
        try:
            all_ok = await asyncio.to_thread(self._send_all, event)
            if all_ok:
                await asyncio.to_thread(self._mark_notified, event.event_id)
        except Exception:
            logger.warning(
                "admin_notifier async delivery failed event_id=%s",
                event.event_id, exc_info=True,
            )

    def _deliver_sync(self, event: FailureEvent) -> None:
        try:
            if self._send_all(event):
                self._mark_notified(event.event_id)
        except Exception:
            logger.warning(
                "admin_notifier sync delivery failed event_id=%s",
                event.event_id, exc_info=True,
            )

    def _send_all(self, event: FailureEvent) -> bool:
        content = json.dumps(_build_card(event), ensure_ascii=False)
        all_ok = True
        for recipient in self._recipients:
            if not self._send_one(recipient, content):
                all_ok = False
        return all_ok

    def _mark_notified(self, event_id: str) -> None:
        try:
            self._failure_store.mark_notified(event_id)
        except Exception:
            logger.warning(
                "admin_notifier failed to mark_notified event_id=%s",
                event_id, exc_info=True,
            )

    def _send_one(self, recipient: Mapping[str, str], content: str) -> bool:
        receive_id = recipient["receive_id"]
        receive_id_type = recipient["receive_id_type"]
        body = (
            CreateMessageRequestBody.builder()
            .receive_id(receive_id)
            .msg_type("interactive")
            .content(content)
            .build()
        )
        request = (
            CreateMessageRequest.builder()
            .receive_id_type(receive_id_type)
            .request_body(body)
            .build()
        )
        try:
            response = self._client.request(request)
        except Exception:
            logger.warning(
                "admin_notifier request raised for recipient=%s", receive_id, exc_info=True,
            )
            return False
        if not response.success():
            logger.warning(
                "admin_notifier delivery failed recipient=%s code=%s msg=%s",
                receive_id,
                getattr(response, "code", ""),
                getattr(response, "msg", ""),
            )
            return False
        return True


def _template_for(stage: str) -> str:
    return "red" if stage in {"backend_chat", "post_reply"} else "orange"


def _build_card(event: FailureEvent) -> dict[str, Any]:
    fallback = "sent" if event.fallback_reply_succeeded else "MISSED"
    fields = [
        _field("Stage", event.stage),
        _field("Error", f"{event.error_type}"),
        _field("File token", event.file_token or "-"),
        _field("Comment", event.comment_id or "-"),
        _field("Reply", event.reply_id or "-"),
        _field("User", event.user_id or "-"),
        _field("Fallback reply", fallback),
    ]
    detail = (event.error_message or "").strip()
    elements: list[dict[str, Any]] = [
        {"tag": "div", "fields": fields},
    ]
    if detail:
        elements.append({"tag": "hr"})
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"**Detail**\n```\n{detail[:1500]}\n```"},
        })
    elements.append({"tag": "note", "elements": [
        {"tag": "plain_text", "content": f"event_id: {event.event_id}"},
    ]})
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": _template_for(event.stage),
            "title": {
                "tag": "plain_text",
                "content": f"[whisper] {event.stage} failure",
            },
        },
        "elements": elements,
    }


def _field(label: str, value: str) -> dict[str, Any]:
    return {
        "is_short": True,
        "text": {"tag": "lark_md", "content": f"**{label}**\n{value}"},
    }


def build_admin_notifier_plugin(
    ctx: PluginBuildCtx, options: Mapping[str, Any]
) -> LarkAdminNotifierPlugin:
    recipients = options.get("recipients") or ()
    if not recipients:
        raise RuntimeError("admin_notifier plugin requires non-empty recipients")
    if ctx.api_client is None:
        raise RuntimeError("admin_notifier plugin requires api_client in build ctx")
    normalized = []
    for entry in recipients:
        rid = str(entry.get("receive_id", "")).strip()
        rtype = str(entry.get("receive_id_type", "")).strip()
        if not rid or not rtype:
            raise RuntimeError(
                f"admin_notifier recipient missing receive_id/receive_id_type: {entry!r}"
            )
        normalized.append({"receive_id": rid, "receive_id_type": rtype})
    return LarkAdminNotifierPlugin(
        client=ctx.api_client,
        recipients=normalized,
        failure_store=ctx.failure_store,
        suppression_window_sec=float(options.get("suppression_window_sec", SUPPRESSION_WINDOW_SEC)),
    )
