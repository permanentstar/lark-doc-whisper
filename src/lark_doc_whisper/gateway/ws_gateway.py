"""WebSocket gateway. Establishes a long-lived connection to Feishu and
dispatches drive.notice.comment_add_v1 events to the comment handler.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import sys
import threading
from datetime import datetime

import lark_oapi as lark
from lark_oapi.api.drive.v1 import P2DriveNoticeCommentAddV1

from ..agent.deerflow_backend import DeerFlowBackend
from ..config import DEERFLOW_CONFIG_PATH, load_app_config, load_env
from .singleton import SingleInstanceLock
from .oauth_callback import OAuthCallbackApp, OAuthCallbackService
from ..handlers.comment_handler import HandlerContext, handle_comment_event
from ..lark.client import get_client, resolve_bot_open_id
from ..lark.oauth import LarkOAuthClient
from ..plugins.base import PluginBuildCtx, build_registry
from ..state.cleanup import StateCleanupService
from ..state.failure_events import default_store as failure_event_store
from ..state.paths import LOGS_DIR, ensure_dirs
from ..state.user_doc_tokens import InMemoryUserDocTokenStore

logger = logging.getLogger("lark_doc_whisper.gateway")


def _setup_logging() -> None:
    ensure_dirs()
    log_path = LOGS_DIR / "gateway.log"
    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_path),
    ]
    fmt = "%(asctime)s [%(levelname)s] %(name)s — %(message)s"
    for h in handlers:
        h.setFormatter(logging.Formatter(fmt))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in handlers:
        root.addHandler(h)


def _start_worker_loop() -> asyncio.AbstractEventLoop:
    """Spin up a dedicated event loop in a background thread.

    lark-oapi's WS client owns its own asyncio loop and invokes event
    handlers synchronously from within that loop. Calling ``asyncio.run``
    from a handler blows up ("cannot be called from a running event loop").
    We therefore ship async work to a separate loop over here.
    """
    loop = asyncio.new_event_loop()

    def _run() -> None:
        asyncio.set_event_loop(loop)
        loop.run_forever()

    t = threading.Thread(target=_run, name="whisper-worker-loop", daemon=True)
    t.start()
    return loop


async def _enqueue_event(queue: asyncio.Queue, event) -> bool:
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        logger.warning("event queue full; dropping incoming event")
        return False
    return True


async def _event_worker(queue: asyncio.Queue, ctx: HandlerContext, worker_id: int) -> None:
    logger.info("event worker %s started", worker_id)
    while True:
        event = await queue.get()
        try:
            if event is None:
                return
            await handle_comment_event(event, ctx)
        except Exception:
            logger.exception("event worker %s failed to handle event", worker_id)
        finally:
            queue.task_done()


def main() -> int:
    _setup_logging()
    logger.info("=== lark-doc-whisper starting at %s ===", datetime.now().isoformat())

    cfg = load_app_config()
    env = load_env(require_llm=True)  # fails fast if LLM_API_KEY missing
    slot = os.environ.get("WHISPER_SLOT", "0")
    force = os.environ.get("WHISPER_FORCE") == "1"
    lock_cm = (
        contextlib.nullcontext()
        if force
        else SingleInstanceLock.for_app(env["LARK_APP_ID"], slot=slot)
    )
    if force:
        logger.warning("WHISPER_FORCE=1: bypassing gateway slot lock (unsafe)")

    with lock_cm:
        return _run_gateway(cfg, env)


def _run_gateway(cfg, env: dict[str, str]) -> int:
    api_client = get_client()
    bot_open_id = resolve_bot_open_id(api_client)
    logger.info("resolved bot open_id for self-trigger guard: %s", bot_open_id)

    logger.info("loading deerflow backend (config=%s) — this may take a few seconds…",
                DEERFLOW_CONFIG_PATH)
    backend = DeerFlowBackend(
        config_path=DEERFLOW_CONFIG_PATH,
        checkpointer_cfg=cfg.deerflow_checkpointer_cfg,
    )
    user_doc_token_store = InMemoryUserDocTokenStore()

    plugin_registry = build_registry(
        cfg.plugins,
        PluginBuildCtx(api_client=api_client, failure_store=failure_event_store),
    )
    if plugin_registry.plugins:
        logger.info(
            "activated plugins: %s",
            ", ".join(getattr(p, "name", "?") for p in plugin_registry.plugins),
        )

    ctx = HandlerContext(
        cfg=cfg,
        api_client=api_client,
        backend=backend,
        bot_open_id=bot_open_id,
        app_id=env.get("LARK_APP_ID", ""),
        authorization_state_secret=env.get("LARK_APP_SECRET", ""),
        user_doc_token_store=user_doc_token_store,
        plugins=plugin_registry,
    )

    oauth_callback_service = _start_oauth_callback_service(
        cfg=cfg,
        env=env,
        token_store=user_doc_token_store,
    )
    worker_loop = _start_worker_loop()
    event_queue: asyncio.Queue = asyncio.Queue(maxsize=cfg.event_queue_size)
    for i in range(max(1, cfg.event_worker_count)):
        asyncio.run_coroutine_threadsafe(_event_worker(event_queue, ctx, i), worker_loop)

    cleanup_service = StateCleanupService(
        interval_sec=cfg.state_cleanup_interval_sec,
        doc_cache_ttl_sec=cfg.doc_cache_ttl_sec,
        event_dedup_ttl_sec=cfg.event_dedup_ttl_sec,
        user_memory_ttl_sec=cfg.user_memory_ttl_sec,
        user_doc_token_store=user_doc_token_store,
    )
    cleanup_service.start()

    def _dispatch(event: P2DriveNoticeCommentAddV1) -> None:
        fut = asyncio.run_coroutine_threadsafe(
            _enqueue_event(event_queue, event), worker_loop,
        )

        def _log_result(f: "asyncio.Future") -> None:
            exc = f.exception()
            if exc is not None:
                logger.exception("event dispatch failed", exc_info=exc)

        fut.add_done_callback(_log_result)

    ws_handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_drive_notice_comment_add_v1(_dispatch)
        .build()
    )
    ws_client = lark.ws.Client(
        env["LARK_APP_ID"],
        env["LARK_APP_SECRET"],
        event_handler=ws_handler,
        log_level=lark.LogLevel.DEBUG,
    )

    async def _shutdown(signum: int) -> None:
        logger.info("received signal %s, closing websocket and exiting", signum)
        try:
            await ws_client._disconnect()
        except Exception:
            logger.warning("graceful websocket disconnect failed", exc_info=True)
        if oauth_callback_service is not None:
            oauth_callback_service.stop()
        cleanup_service.stop()
        for _ in range(max(1, cfg.event_worker_count)):
            asyncio.run_coroutine_threadsafe(event_queue.put(None), worker_loop)
        worker_loop.call_soon_threadsafe(worker_loop.stop)
        asyncio.get_running_loop().stop()

    from lark_oapi.ws import client as ws_module

    def _request_shutdown(signum: int) -> None:
        ws_module.loop.create_task(_shutdown(signum))

    try:
        ws_module.loop.add_signal_handler(signal.SIGINT, _request_shutdown, signal.SIGINT)
        ws_module.loop.add_signal_handler(signal.SIGTERM, _request_shutdown, signal.SIGTERM)
    except NotImplementedError:
        signal.signal(
            signal.SIGINT,
            lambda signum, _frame: ws_module.loop.call_soon_threadsafe(_request_shutdown, signum),
        )
        signal.signal(
            signal.SIGTERM,
            lambda signum, _frame: ws_module.loop.call_soon_threadsafe(_request_shutdown, signum),
        )

    logger.info("gateway listening on drive.notice.comment_add_v1 (bot_open_id=%s)",
                ctx.bot_open_id)
    try:
        ws_client.start()
    except RuntimeError as exc:
        if "Event loop stopped before Future completed" not in str(exc):
            raise
    return 0


def _start_oauth_callback_service(
    *,
    cfg,
    env: dict[str, str],
    token_store: InMemoryUserDocTokenStore,
) -> OAuthCallbackService | None:
    if not cfg.oauth_callback.enabled:
        return None
    auth_cfg = cfg.url_fetch.authorization
    scopes = tuple(
        scope.strip() for scope in auth_cfg.scopes
        if scope.strip() and scope.strip().lower() != "offline_access"
    )
    if not auth_cfg.enabled or not auth_cfg.redirect_uri or not scopes:
        raise RuntimeError("oauth_callback enabled but url_fetch.authorization is not fully configured")
    service = OAuthCallbackService(
        host=cfg.oauth_callback.host,
        port=cfg.oauth_callback.port,
        app=OAuthCallbackApp(
            oauth_client=LarkOAuthClient(
                app_id=env["LARK_APP_ID"],
                app_secret=env["LARK_APP_SECRET"],
                redirect_uri=cfg.url_fetch.authorization.redirect_uri,
                timeout_sec=cfg.url_fetch.timeout_sec,
            ),
            token_store=token_store,
            state_secret=env["LARK_APP_SECRET"],
        ),
    )
    service.start()
    return service


if __name__ == "__main__":
    sys.exit(main())
