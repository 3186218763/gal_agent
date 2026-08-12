# GalAgent V2 代码审计报告

- 日期：2026-08-11
- 范围：`backend/`（Python FastAPI + SQLite 事件溯源）、`frontend/`（React/Vite/TS 浏览器玩家）、README 声明核对
- 方法：5 个并行调研子域 + 逐文件人工复核；全部发现用可运行的验证测试证实（测试位于 `/tmp/opencode/audit/`，未修改任何生产代码）
- 基线：后端 `122 passed, 1 skipped`；前端 `26 passed + 2 skipped`；ruff 对 `src tests` 通过

---

## 一、高严重度

### H1. 409 无进展重试死锁（前端）+ 服务端不释放演进（后端）——组合缺陷
- **前端**：`frontend/src/App.tsx:95-105, 110-138, 190-199` 的 `reloadProjection` 在 revision 未变化时保留 `pendingRef` 并报错；"重试"按钮复用**同一 idempotency_key + 同一 expected_revision**。服务器反复 409，场景永不前进，重试按钮是死按钮。
- **后端根因 1**（验证测试 `test_decision_required_409_revision_unchanged`）：`service.py:91-92` 在决策挂起时抛 `DecisionRequired`，`api.py:113-119` 将其映射为 409 `command_conflict` 且**不产生任何事件、revision 不变**——客户端拿到旧 revision 后任何重试组合都会继续 409。
- **后端根因 2**（`test_stale_revision_new_key_repeated_409`）：`service.py:143-145` 在**所有**异常路径（含 409 类）调用 `release_command` 删除 receipt；带旧 revision 重试时服务端每次都重新 claim → 重新失败，永久 409 + receipt 写放大。
- **后端根因 3**（`test_stale_revision_completed_key_replays_old_result`）：已完成 key + 旧 revision 重试会**静默 200 重放旧场景**，客户端无感知会话已前进。
- 结论：错误码语义混乱（`DecisionRequired`/`RuntimeRevisionConflict`/`RevisionConflict`/`PackMismatch`/`RuntimeSessionEnded` 全部合并为一个 409 `command_conflict`），客户端无法区分"可恢复"与"不可恢复"，配合固定 key 重试形成死锁。

### H2. README 声称的失败恢复语义在代码中不存在（文档与实现严重脱节）
三处 README:64 的声称经验证**均无实现**（`tests/audit/testquality/test_readme_claims.py`）：
- "revision conflicts discard the stale result **and recompute once**" → 代码无任何 recompute，planner 不被重调（`test_revision_conflict_does_not_recompute_once`）。
- "**all-fail → standard-action fallback**" → 所有提案被拒 → `RuntimeGenerationUnavailable` → 503，会话不变，不存在 standard-action 兜底（`test_no_standard_action_fallback_exists`；src 中 "standard action" 只出现在编译期规则检查 `compiler.py:322`）。
- "invalid JSON gets one contract-repair retry **then a deterministic fallback**" → repair 后再失败直接 503，无确定性兜底（`test_no_deterministic_fallback_after_repair_retry`）。

### H3. 根目录测试脚本污染测试管线（`backend/test_diag.py`）
- 模块级 `asyncio.run(main())` 在 pytest **收集阶段**执行：无 env 时抛 `ConfigurationError` 使裸 `uv run pytest` 收集 ERROR；env 齐全时在收集期发起**真实模型调用并写 `data/diag.db`**（烧钱）。
- `test_api_flow.py` 收集 0 测试；二者均未 git 跟踪、ruff 共 7 处错误（F401/I001/BLE001）——README "Ruff clean" 只在 `src tests` 范围内成立。

### H4. 会话创建非幂等且无 idempotency_key（`test_session_creation_is_not_idempotent`）
- `POST /api/v2/sessions`（`api.py:139-150`）只有 `pack_id`+`session_seed`；同参数重试产生第二个会话。与 README:212 "Every mutation requires an idempotency_key" 矛盾，也与前端双击（L1）形成浪费。

---

## 二、中严重度

### M1. 幂等/lease 机制缺陷（`storage/event_store.py`，验证测试 5 项）
- **同 key 在途重试 → 500**：`claim_command`（:263）在 120s lease 内对同 key 抛 `CommandInProgress`，`api.py` 无 handler → 裸 500；双线程并发同 key 实测 `[200, 500]`，事件只追加 1 条。幂等语义本应让"在途重试"安全。
- **同 key 不同请求体 → 500**：`CommandRequestMismatch`（:255）无 handler；同 key 改 revision 或用过的 key 跨端点 → 500（应为 409）。
- **lease 偷走后旧 worker 仍可提交**：`commit_command`（:308-400）不校验 `status=='in_progress'` 也不校验 lease 所有权；lease 过期被重试方抢占后，旧 worker 仍能提交成功（`test_stale_claimant_can_commit_after_lease_stolen`）→ 双 worker 双 LLM 生成、结果归属取决于竞速。120s lease 对长生成（45s×重试×2）并非不可达。
- **release 删除对方 receipt**：`release_command`（:282-306）不校验所有权；A 的 lease 被 B 偷走、A 先失败 release 后，B 的 commit 抛未映射 `StoryStoreError` → 500 且不可重放。
- **无显式 busy_timeout**：依赖 Python 默认 5s，锁等待超时即 `database is locked` → 500。

### M2. 条件拼写错误 → 运行期永久 500（`test_condition_typo_in_normal_ending_compiles_but_crashes_advance`）
- `compiler.py:149-171` 的条件引用校验只覆盖 `facts`/`goals`/`relationships` 三个 root；`session.*`/`world.*`/`threads.*` 的拼写错误编译期不报，运行期 `select_ending` 求值抛 `ConditionEvaluationError`，无 API handler → 每次 advance 都 500。pack 作者一个字段名写错就把整个会话推进打成 500。

### M3. 无鉴权 + 无速率限制 + 启动绑定不一致
- `api.py` 全部端点无任何认证/授权（`Depends`/`Authorization` 均无），GET 可读取**任意**会话与包元数据；POST advance/choose 消耗付费 LLM 配额 → 暴露公网即成本滥用/DoS 面。
- `main.py` 直接运行绑 `0.0.0.0:8000`，而 `start.sh` 绑 `127.0.0.1`——部署意图不一致。

### M4. 前端健壮性缺陷（`frontend/src`，11 项 App 级验证）
- **无 schema 校验渲染崩溃**（`api.ts:98` `as T` 强转，缺 `choices` → TypeError 整树卸载，无错误边界）。
- **脏 localStorage 永不自愈**（`App.tsx:246-260`）：仅 `session_not_found` 才清 id；405/422/500 → 永久 boot 错误循环。
- **无超时/无取消**（`api.ts:93-99`）：fetch 永不 settle → 生成中永久卡死，唯一出路是刷新。
- **开始屏静默失败**（`App.tsx:288-298`）：createSession 失败无任何用户反馈。

### M5. 错误处理与日志卫生
- 未处理异常 → 500 + uvicorn 完整 traceback（含源码路径）进服务器日志；`get_pack` 缺 `PackCompileError` handler → 500（`api.py:156-158`），同一错误在 `POST /sessions` 是 422——错误映射不一致（`test_get_pack_compile_errors_return_500_not_422_404`）。
- `start.sh` 带 `--reload` 进生产启动脚本。

### M6. 供应链漂移
- `frontend/package-lock.json` 存在但**未 git 跟踪**（`git ls-files` 证实）；新检出后 `npm install` 走 `^` 最新解析。pyproject 全为 `>=` 下限。当前锁定版本无已知高危（npm audit 离线 0 漏洞）。

---

## 三、低严重度

| # | 位置 | 问题 | 证据 |
|---|------|------|------|
| L1 | `App.tsx:201-222` | 同 tick 双击"开始" → 双会话 + 跨会话复用同一幂等 key（服务端无害但语义错误） | `AUDIT: same-tick double-click start race` |
| L2 | `App.tsx:103-105` | reload 网络失败被误标为 `command_conflict` | `AUDIT: reload failure mislabeled as conflict` |
| L3 | `App.tsx:57-69` | `network` 错误码无专属文案 | `AUDIT: retry reuses key on NETWORK error` |
| L4 | `api.ts:93-99` | 网络拒绝 → 裸 TypeError；204/空 body → 裸 SyntaxError | api.audit.test.ts |
| L5 | `storage.ts:7-8` | localStorage 访问无 try/catch（隐私模式 → 孤儿会话） | 未单测 |
| L6 | `event_store.py:407-412` | `event_count()` 对不存在会话返回 0，`load_events()` 抛异常——语义不一致 | `test_event_count_missing_session_returns_zero_not_error` |
| L7 | `event_store.py:233-250` | `claim_command` 对不存在会话抛裸 `sqlite3.IntegrityError` | `test_claim_for_missing_session_raises_raw_integrity_error` |
| L8 | `api.py:51-60` | `ScriptPackRegistry.get` 允许 `..` 路径遍历读取 root 外 pack.yaml（`is_relative_to` 只对 include 生效）；POST body `pack_id` 无格式校验 → 目录存在性 oracle | `test_pack_registry_traversal_reads_outside_root` |
| L9 | `config.py:28-35` | base_url 校验缺口：`ftp://localhost` 通过、`https://169.254.169.254/...` 通过（仅环境变量输入，实际可利用性低） | `test_non_https_scheme_on_localhost_passes` |
| L10 | `reducer.py:56-329` | `apply_event` 无 else 分支：未来新增事件类型被静默接受、仅 revision+1 → 事件日志与状态永久分歧 | `test_reducer_silently_accepts_unknown_event_type` |
| L11 | `service.py` | `select_choice` 后无自动推进：choose 后客户端必须再发 advance（多一跳，前端需自行串联） | 设计确认 |
| L12 | `api.py:160-188` | advance/choose 每请求 `load_session` 全量重放两次（handler + `_load_matching`） | 代码审查 |
| L13 | `config.py:63-64` | env 中非法 `GAL_LLM_TIMEOUT_SECONDS`/`MAX_RETRIES` 抛裸 ValueError 而非 `ConfigurationError` | 代码审查 |
| L14 | 全仓 | 零应用日志（无 `logging` 调用）——可观测性差，运行态问题只能靠 500 traceback | security 审计 |
| L15 | 前端 | `localStorage` 存档无版本号/结构校验（与 M4 同源） | 代码审查 |

---

## 四、测试质量缺口（验证测试全部证实"功能正常但无测试守护"）

1. **reducer 4 个事件分支在 tests/ 零引用**（高）：`BeliefChanged`（reducer.py:230）、`ThreadOpened`（:267）、`ThreadAdvanced`（:273）、`ThreadClosed`（:290）——回归将全绿放行。
2. **`GoalAdvanced` 状态效应零断言**（中）：progress clamp、COMPLETED 翻转、evidence 记录未测。
3. **`PhaseAdvanced` 正向单步路径未测**（中）：套件只有"跳两步被拒"。
4. **`FactEvidenced` 状态效应未断言**（中）：visibility 提升、evidence 追加、未提交事实被拒。
5. **live skip 机制**（中）：函数体内 `pytest.skip` 仅检查 `RUN_LIVE_ZEN_TEST`，不检查 key 是否存在；`tests/live/conftest.py` 在 env 残留时会真实打模型——README "skipped = needs a rotated key" 与实现不完全一致。
6. **弱断言**（低）：`test_create_advance_and_choose_v2_session` 只断状态码不断言内容；投影不泄露测试只测初始状态；`test_runtime_agents.py` 的 `SharedFakeModel.get_response` 是死代码（`Runner.run` 被 monkeypatch，永不触发）。
7. 前端现有 26 测试未覆盖：网络错误 key 复用、双击竞态、脏存档非 404、畸形 API 形状、超时、XSS 转义、StrictMode 双 boot、409 无进展循环、reload 失败误导文案。

## 五、验证为正常的重要方面（负面结果）

- **并发原子性**：BEGIN IMMEDIATE 下 check-then-write 无竞态——4 线程同 revision 恰好 1 成功 3 冲突（`test_concurrent_append_revision_check_is_atomic`）。
- **幂等重放正确**：已完成 key 重放返回逐字节相同结果、planner 不重跑、事件不重复（`test_idempotent_replay_does_not_rerun_planner_as_documented`）。
- **失败零提交**：网络失败/超时/契约失败均不修改会话（`test_network_failure_commits_nothing_as_documented`、T6 超时路径）。
- **投影无泄露**：8 轮全流程递归断言 15 个内部字段名（pack_hash/session_seed/事实真值/知识/信念/怀疑/目标）不出现在任何响应（`test_api_full_flow_no_internal_state_in_any_response`）。
- **SQL 注入面不存在**：全部参数化查询；payload 均安全。
- **YAML 安全**：`yaml.safe_load`，`!!python/object/apply` 被拒且不执行；条件 DSL 为 AST 白名单求值器，无 eval。
- **密钥卫生**：`SecretStr`+`repr=False` 不泄露；全 src 无 logging/print 密钥；git 历史扫描无真实密钥；`.env` 未跟踪。
- **引擎兜底**：不可达 fallback 被编译期拒绝（`_has_guaranteed_fallback`，`test_unreachable_fallback_rejected_at_compile_time`）；`max_scenes` 后必有 fallback 可达。
- **结束契约**：writer 改 ending_id → 503 零提交（T5）；ack+ending 单批原子提交（T4）；超时无 repair 重试（T6b）。
- **前端 XSS 安全**：无 `dangerouslySetInnerHTML`，`<img onerror>`/`<script>` 按文本渲染；StrictMode 防双 boot 生效；"从不自绘本地场景"成立。
- **事件溯源**：快照/重放跨边界一致、损坏检测存在（无修复路径，可接受故障模式）。

## 六、建议修复优先级

1. **P0**：H1 死锁（409 语义细分 + 前端重试策略）、H2 文档对齐（README 措辞与代码二选一对齐）。
2. **P1**：H3 根目录脚本处置；M1 幂等/lease（校验 status+所有权、映射 4xx）；M2 条件字段校验；M3 鉴权决策。
3. **P2**：M4 前端健壮性（错误边界/超时/存档自愈）；M5 错误映射统一；M6 锁文件入库；补 reducer 4 分支测试。
4. **P3**：低严重度项逐条清理。

## 七、验证测试索引

| 目录 | 测试数 | 对应章节 |
|------|--------|----------|
| `/tmp/opencode/audit/frontend/` | 18 | H1、M4、L1-L4 |
| `/tmp/opencode/audit/storage_api/` | 21 | H1、M1、M2、M5、L6-L8 |
| `/tmp/opencode/audit/testquality/` | 25 | H2、H3、M5、四、五 |
| `/tmp/opencode/audit/security/` | 48 | M3、M5、M6、L9、五 |
| `/tmp/opencode/audit/runtime_extra/` | 11 | H1 后端根因、H2、五 |

运行方式：`cd backend && uv run pytest /tmp/opencode/audit -q`（前端：`cd frontend && npx vitest run <file>`）。
