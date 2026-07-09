# TODO Roadmap

按优先级分组的待办事项。优先级排序：正确性/幂等性 > 安全加固 > 性能与可维护性。

## P0 · 正确性与幂等性

- [ ] **seen_events 标记时序待明确**
  - 模块：`src/lark_doc_whisper/state/seen_events.py`、`src/lark_doc_whisper/handlers/comment_handler.py`
  - 位置：`mark_seen()` 在评论事件处理链中的调用方式
  - 问题：`mark_seen()` 属于写路径，语义必须绑定当前 event/request 的处理结果，后续异步化时不能演变成独立后台线程的 fire-and-forget 落库。
  - 风险：
    - 过早标记会导致处理失败的事件被错误去重。
    - 脱离当前请求时序异步提交会导致重复事件在落库前穿透。
    - 时序约束不清晰时，后续重构容易引入隐性幂等问题。
  - 后续优化方向：
    - 明确 `mark_seen()` 的语义是 per event/request 完成时提交。
    - 如需异步化，只把阻塞 SQLite 调用 offload 到 thread/executor，但仍等待结果完成。
    - 补充成功、失败、跳过分支下的去重时序测试。

- [ ] **comment replies 拉取分页待补全**
  - 模块：`src/lark_doc_whisper/lark/comments.py`
  - 位置：`get_reply_text()`、`get_comment_thread_history()` 中 `ListFileCommentReplyRequest.page_size(100)`
  - 问题：当前 replies 读取固定只拉一页 `100` 条，未继续翻页，也没有超限降级策略。
  - 风险：
    - 评论线程回复数超过 `100` 时，可能找不到当前 `reply_id`。
    - comment thread history 只基于单页数据，历史上下文可能不完整。
    - 正确性依赖线程规模和接口默认返回顺序，边界条件偏脆弱。
  - 后续优化方向：
    - 补充分页拉取，直到命中目标 `reply_id` 或满足历史窗口需求。
    - 将页大小抽成常量或配置，并明确接口上限假设。
    - 为“大于单页回复数”的线程补充回归测试。

## P1 · 安全加固

- [ ] **security policy 黑名单策略待收敛**
  - 模块：`src/lark_doc_whisper/security/policy.py`
  - 位置：`_DANGEROUS_PATTERNS` 与 `evaluate_user_query()`
  - 问题：当前基于 regex 枚举的 denylist 做用户输入拦截，只适合作为低成本首层筛查，不适合作为主安全边界。
  - 风险：
    - 同义改写、拆词、混淆写法容易绕过，漏拦截风险高。
    - `curl`、`bash`、`.env`、`secret` 等关键词在正常问答场景下容易误杀。
    - 规则未按能力边界建模，后续接更多工具时安全性不可依赖。
  - 后续优化方向：
    - 明确安全主边界应由工具权限、只读能力和 allowlist 控制，而非仅靠输入黑名单。
    - 对输入规则增加归一化、分类和更细粒度决策，降低误杀与漏拦截。
    - 补充典型绕过样例与误杀样例测试，校准策略有效性。

- [ ] **prompt injection 防御待补强**
  - 模块：`src/lark_doc_whisper/agent/doc_context.py`、`src/lark_doc_whisper/agent/deerflow_backend.py`
  - 位置：模型调用边界的 system prompt / middleware 注入策略
  - 问题：当前安全主边界更多依赖工具权限和只读能力收口，但缺少显式的 prompt-level 防御，用来声明文档内容、评论内容和 URL 抓取内容都属于不可信输入，不能把其中的指令当作系统命令执行。
  - 风险：
    - 文档正文、评论回复或外链网页中的注入性文本可能影响模型行为稳定性。
    - 缺少明确角色分层时，模型更容易把资料内容误当成高优先级指令。
    - 后续若接入更多工具能力，这类风险会进一步放大。
  - 后续优化方向：
    - 在模型调用边界增加显式防御提示，明确“资料是数据，不是指令”。
    - 将提示词防御定位为第二道防线，主安全边界仍由工具权限、只读能力和 allowlist 负责。
    - 补充文档注入、评论注入、外链注入等对抗样例测试，验证实际效果。

## P2 · 性能与可维护性

- [ ] **comment_handler 入口过滤链待抽象**
  - 模块：`src/lark_doc_whisper/handlers/comment_handler.py`
  - 位置：`handle_comment_event()` 中 `L184-L210` 附近的 dedup / bot 学习 / self-trigger / mention / required fields 过滤逻辑
  - 问题：多段 early return 过滤、日志和 `mark_seen()` 副作用混在主流程里，入口判断链偏长，后续扩展容易继续堆条件分支。
  - 风险：
    - 可读性和可维护性持续下降。
    - 跳过原因、是否 `mark_seen`、日志输出分散在多个分支里，行为不容易统一。
    - 后续改造去重或自触发保护时更容易漏改分支。
  - 后续优化方向：
    - 抽出独立的 precheck/filter function，统一封装入口过滤决策。
    - 显式返回是否继续处理、跳过原因、是否需要 `mark_seen` 等结果。
    - 将主流程收敛为“预检查 -> 业务处理 -> 收尾标记”的更清晰结构。

- [ ] **URL 分类与路由职责待拆分**
  - 模块：`src/lark_doc_whisper/security/policy.py`、`src/lark_doc_whisper/agent/url_fetch.py`
  - 位置：`_classify_url()`、`AllowedUrl.kind`、`preflight_feishu_urls()`、`fetch_url_content_tool()`
  - 问题：当前在 `policy.py` 里同时做了安全 gate、URL 提取和 URL 类型路由，`_classify_url()` 还依赖字符串包含判断硬编码域名和路径，职责混杂且规则脆弱。
  - 风险：
    - 新域名、路径变体或飞书链接形态变化时容易误分类。
    - `policy` 层和 `url_fetch` 层职责耦合，后续扩展新的 URL 类型时容易到处改分支。
    - `external_http` 这类命名语义不准，增加理解和维护成本。
  - 后续优化方向：
    - `evaluate_user_query()` 只负责是否拦截和提取/规范化 URL，不负责业务路由分类。
    - 将 URL 分类下沉到独立 router/resolver，基于 `urlparse()`、host、path 做判定。
    - 让 `preflight_feishu_urls()` 和 `fetch_url_content_tool()` 复用同一套 URL 解析结果，避免分散硬编码。
    - 将 host/path 规则集中成常量或配置，并为 unsupported / unknown 类型保留显式分支。
