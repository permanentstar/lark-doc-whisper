# AGENTS.md

本文件是 agent 进入 `lark-doc-whisper` 仓库后的入口指南。先读这里，再按需跳转到 README、架构文档和部署 SOP。

## 项目定位

`lark-doc-whisper` 是一个飞书文档评论区 AI 问答网关：用户在文档评论里 @ 机器人提问，服务通过飞书长连接事件接收评论，读取评论锚定的文档上下文，调用 OpenAI 兼容模型生成答案，并把回复贴回原评论线程。

## 必读顺序

1. `README.md`
   - 面向使用者的产品说明、快速开始和目录概览。
2. `docs/lark_doc_whisper_architecture.md`
   - 分层架构、事件链路、上下文预算、安全边界、并发与状态模型。
3. `docs/deploy_sop.md`
   - 裸机 / VM + systemd 部署、升级、回归门禁、运维规则。
4. `docs/todo.md`
   - 已知后续优化点；不要把其中内容当作已实现能力。

中文文档镜像：

- `docs/lark_doc_whisper_architecture.zh-CN.md`
- `docs/deploy_sop.zh-CN.md`

## 常用命令

```bash
uv sync
uv run --extra dev pytest
uv run python -m compileall -q src
uv run python -m lark_doc_whisper
```

发布前至少运行：

```bash
uv run --extra dev pytest
uv run python -m compileall -q src
```

## 配置规则

- 密钥只从 `.env` 文件加载，候选顺序为 `configs/.env` → `~/.env`。不要使用仓库根目录 `.env`。
- 必需密钥：`LARK_APP_ID`、`LARK_APP_SECRET`、`LLM_API_KEY`。
- 模型配置在 `configs/deerflow.yaml`，其中 `model` 和 `base_url` 必须在部署时填真实值。
- 机器人身份由服务启动时通过飞书开放平台自动解析，不要新增持久化配置字段保存它。
- 程序运行时不得回写任何持久化配置文件。

## 硬约束

- 不提交密钥、token、真实个人信息、内网 IP 或本机绝对路径。
- 不把真实 `.env`、`configs/.env`、运行态数据库、日志或缓存文件提交进 git。
- 不引入运行时配置回写逻辑；配置错误应 fail-fast。
- 不把模型安全边界交给 prompt；安全策略必须由程序侧 gate、工具白名单和 URL policy 收口。
- 不用 `kill -9` 停服务；按 SOP 使用 `systemctl stop` / `restart`，避免飞书长连接残留。
- 不擅自删除或重置用户未提交改动。

## 开发边界

- 核心链路入口在 `src/lark_doc_whisper/gateway/ws_gateway.py` 和 `src/lark_doc_whisper/handlers/comment_handler.py`。
- Lark API 访问集中在 `src/lark_doc_whisper/lark/`。
- DeerFlow 封装在 `src/lark_doc_whisper/agent/deerflow_backend.py`。
- 安全策略在 `src/lark_doc_whisper/security/policy.py`。
- 状态存储在 `src/lark_doc_whisper/state/`，运行数据落在 gitignored 的 `runtime/`。

修改代码前：

1. 先确认变更属于哪个边界。
2. 先补或更新测试，再改实现。
3. 不做与当前任务无关的重构。
4. 完成后跑回归门禁。

## 部署提醒

生产目标是单台裸机 / VM + systemd。部署、升级和排障必须以 `docs/deploy_sop.md` 为准。

远端部署基本顺序：

```bash
git pull
uv sync
uv run --extra dev pytest
uv run python -m compileall -q src
sudo systemctl restart lark-doc-whisper
journalctl -u lark-doc-whisper -n 50 --no-pager
```

如果回归门禁失败，不要发布。
