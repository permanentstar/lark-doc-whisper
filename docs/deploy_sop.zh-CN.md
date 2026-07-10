# 部署与运维 SOP（裸机 / VM + systemd）

[English](deploy_sop.md) | 简体中文

> 本文是 lark-doc-whisper gateway 的**唯一权威部署运维手册**。
> 目标环境：单台裸机或 VM，用 systemd 托管进程。
> 沉淀自早期加固/僵尸连接治理方案，语态已转为「现行做法」。

---

## 0. 一句话架构

飞书文档评论 `@bot` → WS gateway 收事件 → 有界队列 → worker 编排 → DeerFlow(OpenAI 兼容模型) 生成 → 回帖。
进程是**单实例长连接**（flock 保护），状态全部落在 `runtime/` 下的 SQLite / 文件，由后台线程 `StateCleanupService` 定时清理。

---

## 1. 前置依赖

| 依赖 | 要求 | 说明 |
|---|---|---|
| Python | >= 3.12 | deerflow 硬约束 |
| uv | 最新 | 依赖管理与运行，见 https://docs.astral.sh/uv/ |
| deerflow harness | git 依赖（无需本地 checkout） | `pyproject.toml` 的 `[tool.uv.sources]` 从 `github.com/bytedance/deer-flow` 拉取，跟踪 `main`；构建时机器需能访问 GitHub |
| 系统 | Linux（生产）/ macOS（开发） | 依赖 `fcntl.flock`、`add_signal_handler`，仅支持类 Unix |

> ⚠️ `deerflow-harness` 是未发布的 uv-workspace 包，直接从 git 拉取（跟踪 main）。`uv.lock` 已入库锁定可复现构建，无需本地 deer-flow 源码；仅需构建机能访问 GitHub。

---

## 2. 机器人接入（首次部署前一次性）

部署 gateway 前，需先在飞书开放平台创建机器人应用拿到凭据，并把机器人加入目标文档。此步只做一次。

### 2.1 创建企业自建应用（拿 App ID / App Secret）

1. 打开飞书开放平台应用管理页：<https://open.larkoffice.com/app?lang=zh-CN>。
2. 新建「**企业自建应用**」，按引导为其开启机器人（Bot）能力。
3. 在「凭证与基础信息」页复制 **App ID** 与 **App Secret**——分别对应 §3.2 必填项的 `LARK_APP_ID`（#1）与 `LARK_APP_SECRET`（#2），填入 `~/.env`。
4. gateway 启动时会用上面的 App ID / Secret 调开放平台 `bot/v3/info` 自动解析机器人身份；无需手工配置机器人身份。

> 应用需具备文档评论的读写权限，并开启长连接（WebSocket）事件订阅；具体权限项与订阅事件以开放平台引导为准。

### 2.2 把机器人加入目标文档（至少「可编辑」）

问答只在**已把机器人加为文档应用**的文档里生效：

1. 打开要启用问答的飞书文档，点右上角「··· 更多」。
2. 选择「**添加文档应用**」，搜索并添加上一步创建的机器人。
3. 授予机器人**至少「可编辑」**权限（读评论与回帖都需要）。

添加后，在该文档评论区 `@机器人` 提问即可触发问答：机器人会围绕选中原文取上下文，生成回复并贴回评论线程。

---

## 3. 敏感信息清单与部署前必填项

### 3.1 必需的密钥（三项）

密钥**只从 `.env` 文件加载**，候选路径见 `config.py` 的 `ENV_CANDIDATES`：
`<repo>/configs/.env` → `~/.env`（按顺序取**第一个存在的文件**加载）。
`load_dotenv` 不覆盖进程里已 export 的同名变量，所以已在 shell / systemd `EnvironmentFile` 注入的变量也生效。

> ⚠️ **实操把三项密钥放在 `~/.env`（用户级，可跨项目复用）**；代码同时兼容项目级 `configs/.env`。
> 注意加载逻辑是「命中第一个存在的 .env 就停」：若两者都存在，只读 `configs/.env`——因此二选一放，不要拆开。
> 仓库根目录的 `.env` **不在** `ENV_CANDIDATES` 内，放这里不会被读到。
> **任何情况下都不要把真实密钥提交进 git**（`.env` / `configs/.env` 已在 `.gitignore`）。

| 变量 | 用途 | 缺失后果 |
|---|---|---|
| `LARK_APP_ID` | 飞书应用 ID | gateway 启动即 `RuntimeError` |
| `LARK_APP_SECRET` | 飞书应用密钥 | 同上 |
| `LLM_API_KEY` | 模型端点密钥（deerflow 后端用；OpenAI 兼容，任意厂商） | 同上（`load_env(require_llm=True)`） |

可选但推荐：`GITHUB_MCP_AUTHORIZATION` 存放 GitHub 官方 remote MCP 的授权 header，例如 `Bearer <token>`。缺失时服务仍可正常启动，但 GitHub 仓库链接无法使用 GitHub MCP 工具。

gateway 启动时还会用飞书凭据调用 `GET /open-apis/bot/v3/info`，解析自触发保护所需的机器人 open_id。若凭据无效、机器人能力未启用，或开放平台接口不可用，启动阶段会直接 fail-fast。

### 3.2 部署前必填项总清单（所有占位 `__fill_me__` 必须替换）

仓库内机器相关占位均为 `__fill_me__`。**部署前必须逐项替换为真实值；不配就报错**（`model`/`base_url` 会在首次调用模型时失败）。

| # | 项 | 位置 | 填什么 | 不配的后果 |
|---|---|---|---|---|
| 1 | `LARK_APP_ID` | `~/.env`（或 `configs/.env`） | 飞书应用 ID | 启动 `RuntimeError` fail-fast |
| 2 | `LARK_APP_SECRET` | 同上 | 飞书应用密钥 | 启动 `RuntimeError` fail-fast |
| 3 | `LLM_API_KEY` | 同上 | 模型端点密钥（OpenAI 兼容，任意厂商） | 启动 `RuntimeError` fail-fast |
| 4 | `model` | `configs/deerflow.yaml` | 模型 / endpoint id（如 Ark：`ep-xxxxxxxx`；OpenAI：`gpt-4o` 等） | 首次调用模型失败 |
| 5 | `base_url` | `configs/deerflow.yaml` | 模型端点的 OpenAI 兼容 base_url | 首次调用模型失败 |

> 密钥（#1–#3）从 `.env` 加载，候选顺序 `configs/.env` → `~/.env`（取第一个存在的文件；已 export 的同名环境变量优先且不被覆盖）。实操把三项放同一个 `~/.env`。
> deerflow 依赖已改为 git 拉取（`pyproject.toml`，跟踪 main），无需本地源码，也无路径占位需填。
> `GITHUB_MCP_AUTHORIZATION` 是可选项；需要读取 GitHub 仓库链接时，把它放在同一个 `.env` 文件里。public repo 场景建议使用只读 / no-scope token。

替换完成后，可肉眼确认无残留占位：

```bash
grep -rn "__fill_me__" configs/ ~/.env 2>/dev/null   # 应无输出（注释除外）
```

---

## 4. 安装

```bash
# 1. 装依赖（deerflow-harness 走 git 拉取，uv.lock 已入库锁定版本）
#    无需本地 deer-flow 源码；构建机需能访问 GitHub。
uv sync          # 按已入库的 uv.lock 装依赖到隔离 venv

# 2. 配置密钥（见 §3）——实操放 ~/.env（用户级，可跨项目复用）
cp .env.example ~/.env   # 然后编辑填入三个密钥
# 也可用项目级：cp .env.example configs/.env（二选一，勿两者都放）

# 3. 对照 §3.2 必填项清单，确认所有 __fill_me__ 已替换

# 4. 回归 gate（见 §8）——发布前必须全绿
uv run pytest -q
```

---

## 5. 启停

### 5.1 标准命令（systemd 托管）

```bash
sudo systemctl start   lark-doc-whisper     # 启动
sudo systemctl stop    lark-doc-whisper     # 优雅停止（发 SIGTERM）
sudo systemctl restart lark-doc-whisper     # 重启（升级后用）
sudo systemctl status  lark-doc-whisper     # 查看状态
journalctl -u lark-doc-whisper -f           # 跟随日志
```

unit 模板见 [`deploy/lark-doc-whisper.service`](../deploy/lark-doc-whisper.service)。

### 5.2 手动前台启动（调试用）

```bash
uv run python -m lark_doc_whisper
```

### 5.3 环境变量：slot 与 force

| 变量 | 默认 | 含义 |
|---|---|---|
| `WHISPER_SLOT` | `0` | 实例槽位。同 `app_id + slot` 只能有一个进程；同机多实例用不同 slot（`0/1/2...`）显式启动。 |
| `WHISPER_FORCE` | 未设 | `=1` 绕过单实例锁，仅救急（如锁文件权限损坏）。**会导致事件分片，打 WARN，慎用。** |

锁文件：`runtime/locks/gateway_<safe_app_id>_slot_<slot>.lock`（内容为 `pid` + 启动时间）。
进程活性由 OS `flock` 判定，退出即释放；锁文件不删除（避免 inode 竞态）。

### 5.4 可选 OAuth callback 端口

当 `oauth_callback.enabled=true` 时，gateway 会在同一进程内启动一个很小的 HTTP callback server。没有反向代理时，需要直接暴露配置的端口，并在飞书开放平台重定向 URL 设置中登记同一个地址。

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

callback 只把短期 `user_access_token` 放在内存里。gateway 重启、token 过期或读取失败后，用户下次评论会重新收到授权链接。不要添加 `offline_access`；refresh token 明确不在当前范围内。

---

## 6. 运维铁律

- **禁止 `kill -9` / `SIGKILL`。** 只用 `systemctl stop` / `kill -TERM` / `Ctrl+C`(SIGINT)。
  - 原因：SIGKILL 让进程来不及发 WS close frame，飞书 server 端残留**僵尸连接**继续抢事件（竞争消费者模型），导致用户评论只有部分到达当前进程。
- **优雅停止会做**：停止收新事件 → 发 WS close frame（`_disconnect`）→ 停 worker loop → 停清理线程 → 释放锁 fd。
- **重启用 `systemctl restart`**，不要 kill 后手动拉起。
- **自动重启是 systemd 的职责**（`Restart=on-failure`），gateway 内部不做自重启。

---

## 7. 升级发布流程

```bash
# 1. 拉取新代码
git pull

# 2. 同步依赖（uv.lock 已入库；deerflow 走 git，如需升级 deerflow 见下方注记）
uv sync

# 3. 回归 gate 全绿（见 §8）
uv run pytest -q

# 4. 优雅重启
sudo systemctl restart lark-doc-whisper

# 5. 冒烟：查日志确认已监听，@ 机器人一条评论确认回帖
journalctl -u lark-doc-whisper -n 50 --no-pager
```

回归 gate 未全绿则**不发布**。

> 升级 deerflow-harness：`uv.lock` 把 git 依赖固定在某个 commit。要拉最新 main，跑
> `uv lock --upgrade-package deerflow-harness` 刷新锁定的 commit，再 `uv sync`，然后把 `uv.lock` 一起提交。

---

## 8. 回归 gate

发布前必须全部通过：

```bash
uv run pytest -q                    # 全量单测，须全绿
uv run python -m compileall -q src  # 语法编译检查
```

当前基线：全量 **104 passed**。

---

## 9. 排障

### 9.1 日志与状态位置

| 路径 | 内容 |
|---|---|
| `runtime/logs/gateway.log` | gateway 主日志（同时输出 stdout，可用 journalctl 看） |
| `runtime/state/checkpoints.db` | LangGraph 对话历史 checkpointer（SQLite WAL） |
| `runtime/state/seen_events.db` | 事件幂等去重 |
| `runtime/state/user_memory.db` | 用户近期问答 episode 记忆 |
| `runtime/state/doc_cache/` | 文档全文缓存 |
| `runtime/locks/` | 单实例锁文件 |

### 9.2 单实例锁冲突

**症状**：启动即报 `another gateway instance holds ...`。
**处理**：说明同 slot 已有进程在跑。先 `systemctl status` / `pgrep -f lark_doc_whisper` 确认；确属残留再排查。不要贸然 `WHISPER_FORCE=1`。

### 9.3 僵尸连接（事件收到率下降）

**症状**：进程活着、连接健康（ping/pong 正常），但用户连发多条评论只收到一部分。多半是之前用过 `kill -9`，或本机休眠/网络抖动触发 SDK 反复重连留下僵尸连接。
**恢复流程**：
1. 优雅停止 gateway（`systemctl stop`）。
2. 等待飞书 server 端连接超时回收，约 **4–6 分钟**（ping 间隔约 120s，需 2–3 次超时）。
3. 重启 gateway（`systemctl start`）。
4. 再发几条评论验证全部到达（`grep -c "receive message" runtime/logs/gateway.log`）。

> 飞书未公开清理远端僵尸连接的 API，只能等平台超时——这与官方 lark-cli 的做法一致。

### 9.4 密钥相关启动失败

`RuntimeError: <KEY> not set` → 回到 §3.2 必填项清单补齐密钥。

---

## 10. 状态与自动清理

`StateCleanupService`（`state/cleanup.py`）是随 gateway 生命周期启停的守护线程，按 `state_cleanup_interval_sec`（默认 600s）周期物理清理三个库的过期数据：

| 库 | 保留期配置 |
|---|---|
| `doc_cache` | `doc_cache_ttl_sec`（默认 300s） |
| `seen_events` | `event_dedup_ttl_sec`（默认 86400s） |
| `user_memory` | `user_memory_ttl_sec`（默认 2592000s ≈ 30 天） |

- 无需手动清库；如需重置，先优雅停进程再删对应 `.db`。
- 备份直接拷 `runtime/state/*.db`（SQLite WAL，建议停机时拷或用 `.backup`）。

---

## 11. 明确不做

| 项 | 原因 |
|---|---|
| Docker / 容器化 | 当前目标是裸机 + systemd |
| 跨机 / TCE 多实例集群 | SQLite/本地文件状态不适合跨机共享，需外置 Postgres/Redis |
| 多机高可用热备 | 竞争消费者模型下必丢事件 |
| 主动清飞书远端僵尸连接 | 平台无公开 API，依赖 server 超时 |
| gateway 内部自重启 | 属 systemd 职责 |
