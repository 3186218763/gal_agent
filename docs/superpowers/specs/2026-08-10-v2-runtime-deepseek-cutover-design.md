# Galgame AI V2 Runtime 与 DeepSeek 接入设计

- **日期**：2026-08-10
- **状态**：已确认
- **目标**：删除旧 V1 运行时，以 V2 为唯一产品主线，并通过 OpenAI Agents SDK 接入 OpenCode Go 的 DeepSeek V4 Flash Responses API
- **依赖设计**：`2026-08-10-constrained-dynamic-galgame-design.md`
- **取代范围**：旧 V1 运行时、旧 V1 前后端协议，以及依赖设计中的模型部署与迁移章节

---

## 1. 已确认决策

1. V2 是唯一运行时和状态权威，不保留 V1 兼容层。
2. 旧 V1 后端立即删除；接受 V2 Runtime 完成前项目暂时不可玩。
3. React/Vite 工程与视觉样式保留，但删除 V1 会话、WebSocket、消息类型和游戏逻辑。
4. V2 第一阶段仍是纯文本、有限选项、不可回溯、多结局动态 Galgame。
5. 运行时只使用 OpenCode Go 的 `deepseek-v4-flash` Responses API，所有 Agent 角色共享同一模型配置。
6. OpenAI Agents SDK 负责 Agent 调用、运行编排和模型抽象；V2 Kernel 保留状态、规则和结局的最终决定权。
7. 必须提供默认离线测试和显式启用的真实 OpenCode Go 网络测试。

用户曾在对话中暴露的密钥不得写入仓库、测试快照、日志或设计文档。实施和 live 测试只能使用吊销旧密钥后新建的密钥。

---

## 2. 方案选择

采用 OpenAI Agents SDK 内置的 Responses API 路径：

```text
AsyncOpenAI(base_url, api_key)
        |
        v
OpenAIResponsesModel(model="deepseek-v4-flash")
        |
        +---- Planner Agent
        +---- Writer Agent
```

不自行实现 HTTP 请求协议，也不引入 LiteLLM。V2 直接使用 Agents SDK 的 Responses 模型实现，不经过 `OpenAIChatCompletionsModel`，也不在应用内进行 Chat Completions 协议转换。

OpenCode Go 目标配置：

```text
base_url = https://opencode.ai/zen/go/v1
model    = deepseek-v4-flash
endpoint = /responses
```

该配置把 OpenCode Go 视为 Responses-compatible Provider。是否由 Go 网关在内部转译为上游协议属于 Provider 实现细节，不进入 V2 Runtime。

---

## 3. V1 清理边界

### 3.1 后端删除

删除只服务旧运行时的模块：

```text
backend/src/agents/
backend/src/content/
backend/src/core/
backend/src/domain/
backend/src/kernel/
backend/src/models/
backend/src/rules/
backend/src/models.py
backend/scripts/chapter_01/
```

删除对应 V1 测试。`backend/src/main.py` 不保留兼容行为，由 V2 API 入口重写。

保留：

```text
backend/src/story/
backend/script_packs/
backend/tests/test_story_*.py
backend/tests/test_script_pack_*.py
backend/tests/test_condition_dsl.py
backend/tests/test_cafe_mystery_pack.py
backend/tests/story_factories.py
```

依赖和 README 随代码同步清理。删除内容仍可从 Git 历史恢复，但主分支不再包含可执行 V1 路径。

### 3.2 前端保留外壳

保留 React、Vite、TypeScript、全局样式和应用根组件。删除：

- V1 `api.ts` 会话客户端
- V1 WebSocket hook
- V1 `Game` 组件及消息类型
- `chapter_01`、`option_index` 和旧消息协议相关逻辑

V2 API 未接入前，前端只呈现静态应用外壳和明确的不可用状态，不模拟游戏成功或继续调用旧端点。

---

## 4. V2 Runtime 架构

新增 `backend/src/story/runtime/`，各模块保持单一职责：

| 模块 | 职责 |
|---|---|
| `config.py` | 加载并校验 OpenCode Go、模型、Responses、超时和重试配置 |
| `model.py` | 创建共享 `AsyncOpenAI` 和 `OpenAIResponsesModel` |
| `contracts.py` | Planner、Writer、选择和运行时命令的 Pydantic 契约 |
| `context.py` | 从编译包与 SessionState 组装最小可信上下文 |
| `planner.py` | 提出下一事件、候选行动和玩家行动结果 |
| `validator.py` | 检查事实、知识、行动、效果边界和结局约束 |
| `simulator.py` | 在状态副本上模拟候选事件，不写正式状态 |
| `writer.py` | 将已批准计划写成小说场景、选项文案和动态终章 |
| `service.py` | 编排一次 V2 回合并通过 EventStore 原子提交 |
| `fallbacks.py` | 提供不依赖模型的安全场景与标准行动回退 |

只创建两个主要 SDK Agent：

- **Planner**：根据上下文提出结构化事件，生成受限行动候选，并在玩家选择后提出结果。
- **Writer**：将已批准语义写为旁白、对白、选项显示文本或结局文本。

角色差异来自剧本包中的性格、知识、信念、关系和声音档案，不创建每个 NPC 的常驻 Agent。

两个 Agent 共享同一个具体 `OpenAIResponsesModel` 配置。统一配置不意味着合并职责，也不允许 Agent 互相直接调用。

---

## 5. 配置与密钥

首选部署变量：

```dotenv
GAL_LLM_PROVIDER=opencode_go
OPENCODE_GO_API_KEY=<rotated-secret>
OPENCODE_GO_BASE_URL=https://opencode.ai/zen/go/v1
GAL_LLM_MODEL=deepseek-v4-flash
GAL_LLM_API=responses
GAL_LLM_TIMEOUT_SECONDS=45
GAL_LLM_MAX_RETRIES=1
```

为兼容用户现有启动习惯，可将 `OPENAI_API_KEY` 作为 `OPENCODE_GO_API_KEY` 的后备别名，但必须遵循以下规则：

1. 两者都存在且值不同时启动失败，禁止静默选取。
2. 日志只显示变量名、模型和主机，不显示密钥或完整授权头。
3. `.env` 保持 Git ignore；仓库只提供无密钥的 `.env.example`。
4. 默认禁用 Agents SDK 的 OpenAI tracing，避免 OpenCode Go 密钥被误用于 OpenAI trace 上传。
5. 只有将来显式配置独立 OpenAI trace key 时才能启用官方 trace exporter。

模型、Provider 和密钥是部署配置，不进入剧本包，不写入事件日志和存档。

---

## 6. 回合数据流

### 6.1 生成场景

```text
load compiled pack + replay session
        |
        v
ContextAssembler
        |
        v
Planner -> EventProposal[]
        |
        v
Validator -> Simulator -> accepted plan
        |
        v
Writer -> SceneDraft
        |
        v
Scene Validator
        |
        v
typed events + atomic append(expected_revision)
        |
        v
V2 API response
```

LLM 调用期间不持有 SQLite 写事务。提交时使用 EventStore 的 `expected_revision`；若发现并发冲突，放弃生成结果并从最新状态重新计算，最多重试一次。

### 6.2 玩家选择

1. 服务端只接受当前 `pending_decision` 中的不可伪造 choice ID。
2. choice ID 映射到受剧本包允许的标准行动或扩展行动。
3. Planner 可以提出行动结果，但不能直接写 relationship、fact、goal 或 ending。
4. Validator 将效果限制在 action capability、precondition 和 effect bounds 内。
5. Simulator 检查事实一致性、角色知识和至少一个结局仍可达。
6. 通过后提交 `PlayerActionSelected`、`ActionResolved` 及派生 typed events。
7. Ending Evaluator 以提交后的权威状态判断是否进入收束。

### 6.3 Responses 结构化输出

Planner 与 Writer 通过 Agents SDK 的 `output_type` 使用 Pydantic 契约，让 SDK 在 Responses 请求中声明结构化输出：

1. `OpenAIResponsesModel` 固定使用 `deepseek-v4-flash`。
2. Planner 和 Writer 分别声明自己的严格 Pydantic `output_type`。
3. Runner 返回后再次执行领域级 Pydantic 校验和 Validator 检查。
4. 结构化输出解析失败时，向同一 Agent 发起一次携带错误摘要的修复请求。
5. 再次失败则使用确定性 fallback，不把非法结果写入状态。

live capability test 必须验证 OpenCode Go 的 DeepSeek V4 Flash 能通过 Responses API 完成 SDK `output_type`。若该能力在 Provider 侧回归，V2 应明确报兼容性错误；本设计不静默切换到 Chat Completions。

---

## 7. 错误处理

| 故障 | 行为 |
|---|---|
| 配置缺失或 base URL 非法 | V2 Runtime API 启动失败并指出缺失变量，不回退到 Stub；离线 validate CLI 不受影响 |
| OpenCode Go 401/403 | 该回合失败，返回可识别配置错误，绝不记录密钥 |
| 429 或临时 5xx | SDK/应用总计最多一次受控重试，遵守服务端 retry hint |
| 超时或断网 | 不提交任何世界事件；会话保持原 revision |
| 非法 JSON | 修复一次，然后确定性 fallback |
| Planner 提案越界 | 丢弃候选；全部失败时生成标准行动 fallback |
| Writer 文本失败 | 保留已批准语义，用确定性短文本呈现，不伪造新事实 |
| revision conflict | 丢弃过期结果，从最新状态重算一次 |
| fallback 结局到达 | Writer 可润色终章，结局 ID 与必备义务由 Kernel 固定 |

不允许用旧 V1、Stub Agent 或任意状态修改作为生产回退。确定性 fallback 只保证会话可继续或安全失败。

---

## 8. V2 API 边界

重写 `backend/src/main.py`，只暴露 V2 会话协议：

```text
POST /api/v2/sessions
GET  /api/v2/sessions/{session_id}
POST /api/v2/sessions/{session_id}/advance
POST /api/v2/sessions/{session_id}/choices/{choice_id}
```

首版优先使用普通 HTTP 命令接口，使 revision、幂等和测试更清晰。文本生成完成后一次返回完整场景；流式输出和 V2 WebSocket 在协议稳定后增加，不纳入本次接入验收。

每个响应至少包含：

- `session_id`
- `revision`
- `scene_id`
- 已批准的旁白/对白块
- 0 或 2-4 个带稳定 ID 的选项
- 结束时的 ending ID、title 和动态终章

客户端不得提交任意后果，只提交 choice ID 和它所基于的 expected revision。

---

## 9. 测试策略

### 9.1 默认离线测试

默认 `pytest` 不联网，使用录制的契约响应或测试 Model Double，覆盖：

- 配置优先级、冲突和密钥脱敏
- 模型固定走 Responses，不走 Chat Completions
- Planner/Writer 共用同一模型配置
- JSON 解析、Pydantic 校验、修复重试和 fallback
- 非法事实、知识泄露、越界效果被拒绝
- 玩家不能提交不存在或过期的 choice ID
- 事件提交、revision conflict、快照恢复和 replay 一致
- 正常结局与 max-scenes fallback 均能收束
- V1 模块、V1 API 路径和旧前端协议已不存在

### 9.2 真实 OpenCode Go capability test

提供显式启用的 live 测试，默认跳过：

```bash
RUN_LIVE_ZEN_TEST=1 \
OPENCODE_GO_API_KEY=<rotated-secret> \
uv run pytest -m live tests/live/test_opencode_go_v2_runtime.py -v
```

该测试使用临时 SQLite 数据库和 `cafe_mystery` 剧本包，验证：

1. OpenCode Go 接受 `deepseek-v4-flash` `/responses` 请求。
2. Agents SDK 的 `OpenAIResponsesModel` 能通过 Runner 正常完成调用。
3. Planner 的 Pydantic `output_type` 能产生可解析并通过规则层的提案。
4. Writer 返回非空中文场景。
5. Runtime 产生 2-4 个合法、不同的有限选项。
6. 选择其中一个后能进入下一 revision，事件 replay 与在线状态一致。
7. 请求、异常和 pytest 输出中不出现密钥。

live 测试只验证最小正常回合，控制费用与不确定性。另提供人工 CLI 跑局命令，允许使用真实模型从创建会话一直运行到正常或 fallback 结局；它不作为默认 CI 测试。

### 9.3 验收命令

实施完成后至少验证：

```bash
uv run pytest tests/ -q
uv run ruff check src/story tests
uv run python -m src.story.cli validate script_packs/cafe_mystery
```

配置新密钥后，再运行 live capability test 和一次人工 V2 正常跑局。

---

## 10. 实施阶段划分

本设计拆为五个顺序阶段，但属于同一个 V2 Runtime 计划：

1. 删除 V1 后端和 V1 测试，清理依赖与文档。
2. 保留 React 外壳并删除 V1 网络、协议和游戏逻辑。
3. 实现 V2 Runtime 的契约、规则编排和确定性 Model Double。
4. 接入 Agents SDK、OpenCode Go DeepSeek Responses 和 V2 HTTP API。
5. 完成离线测试、live capability test、CLI 跑局和验收。

每个阶段必须保持 Git 可审查；删除 V1 与新增 V2 Runtime 分开提交，避免迁移差异不可读。

---

## 11. 完成标准

满足以下条件才视为本阶段完成：

1. 主分支中没有可执行 V1 后端、V1 API 或 V1 前端协议。
2. V2 是唯一 SessionState、EventStore、规则和 API 权威。
3. 所有模型角色通过 Agents SDK 的 `OpenAIResponsesModel` 使用同一 DeepSeek 配置。
4. Agent 不能绕过 Validator、Simulator 和 typed event reducer 修改状态。
5. 无密钥环境下所有离线测试通过；服务启动会明确拒绝真实模型配置缺失。
6. 使用轮换后的 OpenCode Go 密钥，live 测试通过 Responses API 完成一个创建场景和玩家选择的正常回合。
7. `cafe_mystery` 能通过人工 CLI 持续运行到正常或 fallback 结局。
8. 前端工程仍可构建，但在 V2 客户端接入前不会调用旧接口或伪装成可玩状态。
