# Deployment & Ops SOP (bare metal / VM + systemd)

English | [简体中文](deploy_sop.zh-CN.md)

> This is the **single authoritative deployment & ops manual** for the lark-doc-whisper gateway.
> Target environment: a single bare-metal host or VM, with the process managed by systemd.
> Distilled from the early hardening / zombie-connection remediation work; phrased as current practice.

---

## 0. Architecture in one line

Lark doc comment `@bot` → WS gateway receives event → bounded queue → worker orchestration → DeerFlow (OpenAI-compatible model) generates → reply posted back.
The process is a **single long-lived connection** (flock-protected); all state lives in SQLite / files under `runtime/`, cleaned up periodically by the background thread `StateCleanupService`.

---

## 1. Prerequisites

| Dependency | Requirement | Notes |
|---|---|---|
| Python | >= 3.12 | hard deerflow constraint |
| uv | latest | dependency management & running, see https://docs.astral.sh/uv/ |
| deerflow harness | git dependency (no local checkout) | `[tool.uv.sources]` in `pyproject.toml` pulls from `github.com/bytedance/deer-flow`, tracking `main`; the build machine needs GitHub access |
| OS | Linux (prod) / macOS (dev) | relies on `fcntl.flock` and `add_signal_handler`; Unix-like only |

> ⚠️ `deerflow-harness` is an unpublished uv-workspace package pulled straight from git (tracking main). `uv.lock` is committed to pin a reproducible build; no local deer-flow source is needed — only GitHub access at build time.

---

## 2. Bot onboarding (one-time, before first deploy)

Before deploying the gateway, first create a bot application on the Lark Open Platform to obtain credentials, and add the bot to the target documents. This is a one-time step.

### 2.1 Create a custom enterprise app (get App ID / App Secret)

1. Open the Lark Open Platform app management page: <https://open.larkoffice.com/app?lang=en-US>.
2. Create a new **custom enterprise app** and enable its bot (Bot) capability as guided.
3. On the "Credentials & Basic Info" page, copy the **App ID** and **App Secret** — corresponding to `LARK_APP_ID` (#1) and `LARK_APP_SECRET` (#2) in the §3.2 required-items list; put them in `<repo>/configs/.env`.
4. The gateway resolves the bot's own identity at startup via the Open Platform `bot/v3/info` API, using the App ID / Secret above. No manual bot identity config is needed.

> The app needs read/write permission on doc comments and must enable long-connection (WebSocket) event subscription; the exact scopes and subscribed events follow the Open Platform guidance.

### 2.2 Add the bot to target documents (at least "can edit")

Q&A only works in documents where the **bot has been added as a doc application**:

1. Open the Lark document you want to enable Q&A in, and click "··· More" at the top right.
2. Choose "**Add document application**", search for and add the bot you created above.
3. Grant the bot **at least "can edit"** permission (both reading comments and posting replies need it).

Once added, `@bot` in that document's comments triggers Q&A: the bot gathers context around the highlighted text, generates a reply, and posts it back into the comment thread.

---

## 3. Sensitive-info inventory & required pre-deploy items

> ⚠️ `__fill_me__` placeholders in `configs/deerflow.yaml` (`model`, `base_url`) apply to **local dev too, not just production** — if they remain unset, the very first model call raises `openai.APIConnectionError`. Fill them, or point the config at your local model endpoint, before running.

### 3.1 Required secrets (three)

Secrets are **only loaded from `.env` files**; candidate paths are in `config.py`'s `ENV_CANDIDATES`:
`<repo>/configs/.env` → `~/.env` (the **first existing file** in order is loaded).
`load_dotenv` does not overwrite same-named variables already exported in the process, so variables injected via shell / systemd `EnvironmentFile` also take effect.

> ⚠️ **For production, put all three secrets in `<repo>/configs/.env`** so this service does not depend on a shared user-level `~/.env`.
> Note the load logic stops at "the first existing .env": if both exist, only `configs/.env` is read — so pick one, don't split them.
> The repo-root `.env` is **not** in `ENV_CANDIDATES`; placing it there means it won't be read.
> **Never commit real secrets to git under any circumstances** (`.env` / `configs/.env` are already in `.gitignore`).

| Variable | Purpose | Consequence if missing |
|---|---|---|
| `LARK_APP_ID` | Lark app ID | gateway `RuntimeError` at startup |
| `LARK_APP_SECRET` | Lark app secret | same as above |
| `LLM_API_KEY` | model endpoint key (used by the deerflow backend; OpenAI-compatible, any provider) | same as above (`load_env(require_llm=True)`) |

Optional but recommended: `GITHUB_MCP_AUTHORIZATION` stores the authorization header for the official GitHub MCP remote server, for example `Bearer <token>`. If it is missing, normal service startup still succeeds, but GitHub repository links cannot use the GitHub MCP tools.

At startup, the gateway also calls `GET /open-apis/bot/v3/info` with the Lark credentials to resolve the bot open_id used by the self-trigger guard. Invalid credentials, disabled bot capability, or an unavailable Open Platform API fails startup early.

### 3.2 Full pre-deploy required-items checklist (all `__fill_me__` placeholders must be replaced)

All machine-specific placeholders in the repo are `__fill_me__`. **They must each be replaced with real values before deploy; otherwise it errors** (`model`/`base_url` fail on the first model call).

| # | Item | Location | What to fill | Consequence if unset |
|---|---|---|---|---|
| 1 | `LARK_APP_ID` | `configs/.env` | Lark app ID | `RuntimeError` fail-fast at startup |
| 2 | `LARK_APP_SECRET` | same as above | Lark app secret | `RuntimeError` fail-fast at startup |
| 3 | `LLM_API_KEY` | same as above | model endpoint key (OpenAI-compatible, any provider) | `RuntimeError` fail-fast at startup |
| 4 | `model` | `configs/deerflow.yaml` | model / endpoint id (e.g. Ark: `ep-xxxxxxxx`; OpenAI: `gpt-4o`, etc.) | first model call fails |
| 5 | `base_url` | `configs/deerflow.yaml` | the model endpoint's OpenAI-compatible base_url | first model call fails |

> Secrets (#1–#3) are loaded from `.env`, candidate order `configs/.env` → `~/.env` (the first existing file wins; same-named already-exported env vars take priority and are not overwritten). For production, keep all three in `configs/.env`.
> The deerflow dependency is now pulled from git (`pyproject.toml`, tracking main); no local source and no path placeholders to fill.
> `GITHUB_MCP_AUTHORIZATION` is optional and should be stored in the same `.env` file when GitHub repository reading is needed. Keep the token read-only / no-scope for public repository access.

After replacing, you can visually confirm no leftover placeholders:

```bash
grep -rn "__fill_me__" configs/ 2>/dev/null   # should print nothing (comments aside)
```

---

## 4. Installation

```bash
# 1. Install deps (deerflow-harness is pulled from git; uv.lock pins versions)
#    No local deer-flow source needed; the build machine needs GitHub access.
uv sync          # install deps into an isolated venv per the committed uv.lock

# 2. Configure secrets (see §3) — production uses repo-scoped configs/.env
cp .env.example configs/.env
# Then edit configs/.env and fill in the three secrets

# 3. Against the §3.2 checklist, confirm all __fill_me__ have been replaced

# 4. Regression gate (see §8) — must be all green before release
uv run pytest -q
```

---

## 5. Start / stop

### 5.1 Standard commands (systemd-managed)

```bash
sudo systemctl start   lark-doc-whisper     # start
sudo systemctl stop    lark-doc-whisper     # graceful stop (sends SIGTERM)
sudo systemctl restart lark-doc-whisper     # restart (use after upgrades)
sudo systemctl status  lark-doc-whisper     # check status
journalctl -u lark-doc-whisper -f           # follow logs
```

Unit template: [`deploy/lark-doc-whisper.service`](../deploy/lark-doc-whisper.service).

### 5.2 Manual foreground start (for debugging)

```bash
uv run python -m lark_doc_whisper
```

### 5.3 Env vars: slot and force

| Variable | Default | Meaning |
|---|---|---|
| `WHISPER_SLOT` | `0` | Instance slot. Only one process per `app_id + slot`; run multiple instances on one host with different slots (`0/1/2...`) explicitly. |
| `WHISPER_FORCE` | unset | `=1` bypasses the single-instance lock, for emergencies only (e.g. corrupted lock-file permissions). **Causes event sharding, logs a WARN, use with caution.** |

Lock file: `runtime/locks/gateway_<safe_app_id>_slot_<slot>.lock` (contents: `pid` + start time).
Process liveness is judged by the OS `flock`, released on exit; the lock file is not deleted (avoids inode races).

### 5.4 Optional OAuth callback port

When `oauth_callback.enabled=true`, the gateway starts a small HTTP callback
server in the same process. Without a reverse proxy, expose the configured
port directly and register the same URL in the Lark Open Platform redirect URL
settings.

```yaml
oauth_callback:
  enabled: true
  host: 0.0.0.0
  port: 8088

url_fetch:
  authorization:
    enabled: true
    redirect_uri: http://<host-or-ip>:8088/oauth/callback
    scopes:
      - docx:document:readonly
```

The callback only stores short-lived `user_access_token` values in memory.
Restarting the gateway, token expiry, or a read failure requires the user to
authorize again on the next comment. Do not add `offline_access`; refresh
tokens are intentionally out of scope.

---

## 6. Ops iron rules

- **No `kill -9` / `SIGKILL`.** Use only `systemctl stop` / `kill -TERM` / `Ctrl+C` (SIGINT).
  - Why: SIGKILL leaves no time to send the WS close frame, so a **zombie connection** lingers on Lark's server and keeps grabbing events (competing-consumer model), causing only some of a user's comments to reach the current process.
- **A graceful stop does**: stop accepting new events → send WS close frame (`_disconnect`) → stop the worker loop → stop the cleanup thread → release the lock fd.
- **Restart with `systemctl restart`**, don't kill and hand-relaunch.
- **Auto-restart is systemd's job** (`Restart=on-failure`); the gateway does not self-restart internally.

---

## 7. Upgrade / release flow

```bash
# 1. Pull new code
git pull

# 2. Sync deps (uv.lock is committed; deerflow is via git — to upgrade deerflow, see the note below)
uv sync

# 3. Regression gate all green (see §8)
uv run pytest -q

# 4. Graceful restart
sudo systemctl restart lark-doc-whisper

# 5. Smoke test: check the log to confirm it's listening, @ the bot in a comment to confirm the reply
journalctl -u lark-doc-whisper -n 50 --no-pager
```

If the regression gate isn't all green, **do not release**.

> Upgrading deerflow-harness: `uv.lock` pins the git dependency at a specific commit. To pull the latest main, run
> `uv lock --upgrade-package deerflow-harness` to refresh the pinned commit, then `uv sync`, and commit the updated `uv.lock`.

---

## 8. Regression gate

Everything below must pass before a release:

```bash
uv run pytest -q                    # full unit tests, must be all green
uv run python -m compileall -q src  # syntax compile check
```

Current baseline: **104 passed** in full.

---

## 9. Troubleshooting

### 9.1 Log & state locations

| Path | Contents |
|---|---|
| `runtime/logs/gateway.log` | gateway main log (also to stdout, viewable via journalctl) |
| `runtime/state/checkpoints.db` | LangGraph conversation-history checkpointer (SQLite WAL) |
| `runtime/state/seen_events.db` | event idempotency dedup |
| `runtime/state/user_memory.db` | user recent-Q&A episode memory |
| `runtime/state/doc_cache/` | full-document cache |
| `runtime/locks/` | single-instance lock files |

### 9.1.1 Log rotation

The repo ships a `logrotate` template at [`deploy/lark-doc-whisper.logrotate`](../deploy/lark-doc-whisper.logrotate).
Default policy: `daily`, `rotate 7`, `maxsize 50M`, `compress`, `delaycompress`, `copytruncate`.
It covers both `runtime/logs/gateway.log` and `runtime/logs/audit.jsonl`; if the `audit_log` plugin is disabled, `missingok` lets rotation continue quietly.

```bash
sudo cp deploy/lark-doc-whisper.logrotate /etc/logrotate.d/lark-doc-whisper
sudo $EDITOR /etc/logrotate.d/lark-doc-whisper   # replace <REPO_DIR>
sudo logrotate -d /etc/logrotate.d/lark-doc-whisper
sudo logrotate -f /etc/logrotate.d/lark-doc-whisper
ls -lh runtime/logs
```

### 9.2 Single-instance lock conflict

**Symptom**: startup reports `another gateway instance holds ...`.
**Handling**: means a process is already running on the same slot. First confirm with `systemctl status` / `pgrep -f lark_doc_whisper`; only investigate as a leftover once confirmed. Don't rashly use `WHISPER_FORCE=1`.

### 9.3 Zombie connection (drop in event delivery rate)

**Symptom**: the process is alive and the connection looks healthy (ping/pong normal), but a user sending several comments in a row only gets some through. Usually caused by an earlier `kill -9`, or host sleep / network jitter triggering repeated SDK reconnects that leave a zombie connection.
**Recovery flow**:
1. Gracefully stop the gateway (`systemctl stop`).
2. Wait for Lark's server to time out and reclaim the connection, about **4–6 minutes** (ping interval ~120s, needs 2–3 timeouts).
3. Restart the gateway (`systemctl start`).
4. Send a few comments to verify all arrive (`grep -c "receive message" runtime/logs/gateway.log`).

> Lark exposes no API to clear remote zombie connections; you can only wait for the platform timeout — consistent with the official lark-cli approach.

### 9.4 Secret-related startup failure

`RuntimeError: <KEY> not set` → go back to the §3.2 checklist and fill in the missing secret.

---

## 10. State & automatic cleanup

`StateCleanupService` (`state/cleanup.py`) is a daemon thread that starts/stops with the gateway lifecycle and, every `state_cleanup_interval_sec` (default 600s), physically purges expired data from three stores:

| Store | Retention config |
|---|---|
| `doc_cache` | `doc_cache_ttl_sec` (default 300s) |
| `seen_events` | `event_dedup_ttl_sec` (default 86400s) |
| `user_memory` | `user_memory_ttl_sec` (default 2592000s ≈ 30 days) |

- No manual purging needed; to reset, gracefully stop the process first, then delete the corresponding `.db`.
- Back up by copying `runtime/state/*.db` directly (SQLite WAL; copy while stopped or use `.backup`).

---

## 10.1 Optional plugins (off by default)

Two side-effect plugins ship with the gateway. Both are **off in the OSS default**; enable them on the deployment host only when you need audit or alerting. Unknown plugin names fail fast at boot. Example activation:

```yaml
plugins:
  - name: audit_log
  - name: admin_notifier
    options:
      recipients:
        - receive_id_type: user_id
          receive_id: <recipient_id>
```

- `audit_log`: when enabled, records an access-log-style JSONL trail of incoming events under `runtime/logs/`. Envelope metadata only — user query bodies are not persisted.
- `admin_notifier`: when enabled, forwards persisted failure events to the configured recipients. Reuses the gateway's existing credentials — no extra secret to configure.

For plugin option schemas and field names, refer to the source under `src/lark_doc_whisper/plugins/`.

---

## 11. Explicit non-goals

| Item | Reason |
|---|---|
| Docker / containerization | current target is bare metal + systemd |
| Cross-host / TCE multi-instance cluster | SQLite/local-file state is unfit for cross-host sharing; needs external Postgres/Redis |
| Multi-host HA hot standby | the competing-consumer model would inevitably drop events |
| Actively clearing Lark's remote zombie connections | no public platform API; depends on server timeout |
| Gateway internal self-restart | that's systemd's job |
