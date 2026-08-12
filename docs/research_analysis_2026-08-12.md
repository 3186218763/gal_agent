# GalAgent 后端运行调研报告

- 日期：2026-08-12
- 目标：以 demo 体验为标杆，让真实后端端到端跑起来
- 方法：深度阅读全部后端 runtime 模块 + 调研 6 个开源/学术 AI 叙事项目 + 对照审计报告

---

## 一、调研对象

| 项目 | 类型 | 核心思路 | 与 GalAgent 的关联 |
|------|------|----------|-------------------|
| **Archon** (SEP) | AI D&D DM | 三层记忆（事件日志→向量库→知识图谱）+ 主动检索 | 长会话记忆、角色关系建模 |
| **Intra** (Ian Bicking) | LLM 文字冒险 | "地面真相"状态 + 事件日志按角色视角过滤 + 引导式思考 | 角色知识隔离、prompt 分层 |
| **SoloQuest DM** (Austin Amento) | AI D&D 5e DM | 四层 prompt 架构（规则契约→按需注入→状态序列化→强制输出结构） | 结构化输出、一致性保证 |
| **Infinite Novel** (0penAGI) | 开源 AI 视觉小说 | Gemma3 1B + SD 1.5 + 量子/分形记忆 + 线程叙事 | 小模型驱动、实时流式体验 |
| **Schema-Governed LLM Pipeline** (论文) | 学术 | 将叙事生成重构为结构化知识管理系统 | Guard 的学术理论基础 |
| **The Drama Machine** (论文) | 学术 | 多 LLM agent 模拟角色发展 | 多角色独立 agent 模式 |

---

## 二、架构对比

### 2.1 GalAgent 当前架构

```
玩家选择
  ↓
TurnOrchestrator.execute_turn()
  ├── 1. claim_command (幂等/并发控制)
  ├── 2. resolve choice (Planner)
  ├── 3. compute_pacing_envelope (确定性)
  ├── 4. Director.plan_segment() ──→ LLM 调用 #1：结构化计划（无散文）
  ├── 5. validate_segment_plan (确定性)
  ├── 6. Writer.write_segment() ──→ LLM 调用 #2：渲染计划为对话块
  ├── 7. validate_segment_draft (确定性)
  ├── 8. Guard.check_segment() ──→ LLM 调用 #3（仅语义层）：知识泄漏检测
  ├── 9. simulate_segment (确定性事件)
  ├── 10. CompletionJudge (如果结局) ──→ LLM 调用 #4
  ├── 11. atomic commit
  └── 12. SSE stream (segment_started → blocks → segment_ready)
```

### 2.2 跨项目对比

| 维度 | GalAgent | Archon | Intra | SoloQuest |
|------|----------|--------|-------|-----------|
| **状态管理** | 事件溯源 SQLite | 三层（NoSQL+向量+图谱） | 浏览器事件日志 | 每轮序列化引擎状态 |
| **记忆/检索** | 全量上下文塞入 prompt | 主动三层检索（入scene前预取） | 按NPC过滤事件日志 | 每轮注入完整状态 |
| **LLM 调用/轮** | 2-4 次（Director+Writer+Guard+Judge） | 1-2 次（DM+Memory Agent） | 3-5 次（解析→解决→生成） | 1 次（四段输出） |
| **知识隔离** | 按角色 fact visibility（编译期） | 知识图谱边/节点属性 | 事件日志masking | N/A（单一DM） |
| **一致性保证** | Guard（确定性+语义双层） | ground truth在引擎状态 | ground truth在代码 | parser tags→引擎 |
| **流式输出** | SSE（block by block） | 未提及 | 列为未来方向 | 不适用 |
| **失败兜底** | 无（503 报错） | N/A | N/A | N/A |
| **模型** | deepseek-v4-flash | GPT-4o + 多模型组合 | 用户自选（OpenRouter） | GPT-4o |

### 2.3 关键洞察

**GalAgent 做对了什么：**

1. **事件溯源 + 乐观并发** — 审计报告验证了原子性正确（4线程同 revision 恰好1成功3冲突）
2. **按角色知识隔离** — `build_segment_writer_context()` 给每个角色只发它们自己的 facts，Guard 检测知识泄漏，这在调研的所有项目中是最严格的
3. **Director/Writer 分离** — Director 只做结构计划不写散文，Writer 负责渲染，这避免了 LLM 在"边想剧情边写对话"时的质量下降
4. **确定性 pacing 包络** — `compute_pacing_envelope()` 用数学公式控制节奏（opening→exploration→escalation→crisis→resolution），不依赖 LLM 判断"该不该收尾了"
5. **latent_questions 机制** — `notebook_holder` 这样的延迟提交事实设计非常独特，允许叙事在不确定状态下自然演化，直到证据足够时才"锁定"真相

**GalAgent 的核心问题：**

| 编号 | 问题 | 严重度 | 来源 |
|------|------|--------|------|
| **P0-1** | 每轮 2-4 次 LLM 调用，flash 模型难以稳定通过 Guard | 高 | 架构分析 |
| **P0-2** | 409 死锁（H1）— 前端无法从冲突中恢复 | 高 | 审计报告 |
| **P0-3** | 无确定性兜底（H2）— 所有提案被拒直接 503 | 高 | 审计报告 |
| **P1-1** | 前端未连接后端 — demo 和真实后端完全脱节 | 高 | 代码检查 |
| **P1-2** | 无记忆/检索层 — 长会话 prompt 会爆炸 | 中 | Archon 对比 |
| **P1-3** | lease/幂等机制缺陷（M1）— 并发竞态 | 中 | 审计报告 |
| **P2-1** | 无应用日志（L14）— 运行态问题不可观测 | 低 | 审计报告 |

---

## 三、核心建议

### Phase 0：打通端到端（最小可运行）

**目标：demo 前端 → 真实后端 → 玩到第一段对话**

#### 0.1 创建一个精简的测试 pack

现有 `cafe_mystery` pack 设计完善但过于复杂（min_scenes=20, max_scenes=60），首次测试应创建一个 min_scenes=4, max_scenes=8 的精简 pack，减少变量。

#### 0.2 前端对接 `/turns` SSE 端点

后端 SSE 事件格式与 demo 的 `DemoBlock` 几乎一一对应：

```
SSE block event                    Demo DemoBlock
─────────────────────────────────────────────────
{ kind: "narration"|"dialogue",  ← { kind: "narration"|"dialogue",
  text: "...",                       text: "...",
  character_id: "alice" }            characterId: "alice" }
```

对接路径：
```
1. POST /api/v2/sessions  →  获取 session_id + revision
2. POST /api/v2/sessions/{id}/turns (SSE)
   ├── event: segment_started  →  进入 "play" 阶段
   ├── event: block            →  逐块打字机播放
   ├── event: segment_ready
   │   ├── terminal: "decision"  →  显示 choices
   │   ├── terminal: "ending"    →  显示结局画面
   │   └── terminal: "continue"  →  自动发下一轮 /turns
   └── event: retry_after | error  →  错误处理
3. 用户选择 → POST /turns (带 choice_id) → 回到步骤 2
```

**建议：新建 `LiveApp.tsx` 而非修改 `DemoApp.tsx`**，保留 demo 作为纯前端展示。

#### 0.3 修复 409 死锁（H1 核心路径）

最小修复：前端收到 `revision_conflict` 时自动 `GET /api/v2/sessions/{id}` 刷新 revision，然后用新 revision + 新 idempotency_key 重试。不再复用同一 key。

#### 0.4 环境验证

```bash
export GAL_LLM_PROVIDER=opencode_go
export OPENCODE_GO_API_KEY=<key>
cd backend && uv run uvicorn src.story.main:app --port 8000
```

用 `curl` 验证单轮：
```bash
# 创建会话
curl -X POST http://localhost:8000/api/v2/sessions \
  -H "Content-Type: application/json" \
  -d '{"pack_id": "cafe_mystery", "session_seed": "test1"}'

# 推进一轮（SSE）
curl -N http://localhost:8000/api/v2/sessions/<id>/turns \
  -H "Content-Type: application/json" \
  -d '{"expected_revision": 0, "idempotency_key": "k1"}'
```

---

### Phase 1：简化管线（让 flash 模型能稳定通过）

**核心矛盾：deepseek-v4-flash 是快速模型，但管线要求它连续通过 3 次结构化输出校验。**

#### 1.1 方案 A：合并 Director + Writer（推荐首选）

参考 SoloQuest 的"单次调用，四段输出"模式，将 Director 和 Writer 合并为一次 LLM 调用：

```python
# 当前：2 次调用
plan = await director.plan_segment(context)     # 调用 #1
draft = await writer.write_segment(plan, ctx)   # 调用 #2

# 改为：1 次调用，内部先生成计划再生成散文
result = await unified_writer.generate(context)
# result 包含: segment_plan + scene_drafts（一次调用产出）
```

**收益：**
- 延迟减半（对 galgame 体验至关重要）
- 避免了 Director 计划和 Writer 渲染之间的不一致
- flash 模型一次调用通过率远高于两次连续通过

**代价：**
- 失去 Director/Writer 分离的理论优雅性
- 但对 flash 模型来说，实践 >> 理论

#### 1.2 方案 B：Guard 降级为纯确定性（备选）

当前 Guard 的语义层（Layer 2）需要一次 LLM 调用来检测知识泄漏。如果 Writer 的 prompt 已经足够强地约束了知识隔离（事实清单只包含该角色已知的事实），可以：
- 保留确定性检查（Layer 1）：speaker presence, choice identity, fact visibility, evidence counts
- 跳过语义层（Layer 2）：只在 `debug=True` 时运行

#### 1.3 添加确定性兜底（修复 H2）

**参考 Intra 的"引导式思考"和 SoloQuest 的版本化 prompt：**

当所有 LLM 尝试失败时，不要 503，而是生成一个确定性"过渡场景"：

```python
def deterministic_fallback(state, pack):
    """当 LLM 全部失败时的确定性兜底。"""
    return SegmentDraft(
        blocks=[
            Block(kind="narration",
                  text=f"（时间的流逝中，你陷入了沉思。）"),
        ],
        choices=standard_choices(state),  # observe / wait / change_topic
    )
```

这保证了玩家永远不会卡在 503 错误画面。

---

### Phase 2：记忆系统（长会话一致性）

**参考 Archon 的三层记忆 + 主动检索。**

GalAgent 当前只有 Tier 1（事件日志），长会话（20+ scenes）时 prompt 会包含大量历史信息。

#### 2.1 Tier 1.5：对话摘要层（低成本，高收益）

在每个 segment 提交后，异步生成一个该 segment 的摘要（1-2 句话），存入 `StoryEvent`。Director/Writer 的 context 只包含：
- 最近 2 个 segment 的完整 blocks
- 更早 segment 的摘要
- 当前活跃 facts/goals/threads

这不需要向量库，只需要一个 `summary` 字段。

#### 2.2 Tier 2：语义检索（中期）

当 `scene_count > min_scenes` 时，用向量搜索从历史中提取与当前场景相关的片段：

```python
# 伪代码
relevant_history = vector_store.search(
    query=current_scene_description,
    filter=session_id,
    top_k=5,
)
context.relevant_memories = relevant_history
```

**实现建议：** 用 SQLite + `sqlite-vec` 扩展，不引入额外服务。

#### 2.3 主动检索（长期）

Archon 的核心洞察：**在 LLM 需要之前就预取相关上下文**。

在 Director 生成计划前，根据当前 pacing phase 主动注入：
- convergence 阶段：注入所有未解 latent_questions 的当前状态
- crisis 阶段：注入所有角色的当前 trust/suspicion 值和关键转折事件

GalAgent 的 `build_director_context()` 已经部分实现了这个（它包含了 facts summary 和 goals），但缺少对历史场景的语义检索。

---

### Phase 3：角色深度（参考 Intra + Drama Machine）

#### 3.1 NPC 内心独白

Intra 的"guided thinking"模式：在 Writer 生成对话前，先让 LLM 为每个在场角色生成一段"内心想法"（不展示给玩家），这段想法作为上下文影响该角色的对话风格。

GalAgent 的 `build_segment_writer_context()` 已经包含了角色的 personality/secrets/drives，可以增加一个 `internal_state` 字段：

```yaml
characters:
  - id: alice
    internal_state:
      current_mood: anxious  # 动态更新
      current_goal: find_notebook
      unspoken_thought: "如果鲍勃发现了笔记本的内容..."
```

#### 3.2 角色行动顺序

Intra 发现当多个 NPC 在场时，LLM 倾向于让所有人都发言，导致混乱。建议：
- Director 在 plan 中明确指定每个 scene 的发言角色和顺序
- 已有设计：`SegmentPlan.scenes[].beats[]` 结构支持这一点

---

## 四、行动计划（按优先级）

### 本周（Phase 0）

| 步骤 | 内容 | 预估时间 |
|------|------|---------|
| 0.1 | 设置环境变量，`curl` 验证 `/turns` 单轮 SSE | 30min |
| 0.2 | 创建 `LiveApp.tsx`，对接 SSE | 2-3h |
| 0.3 | 修复前端 409 重试逻辑 | 1h |
| 0.4 | 端到端测试：创建会话→播放→选择→结局 | 1h |

### 下周（Phase 1）

| 步骤 | 内容 | 预估时间 |
|------|------|---------|
| 1.1 | 合并 Director+Writer 为单次调用 | 3-4h |
| 1.2 | Guard 语义层改为可选 | 1h |
| 1.3 | 添加确定性兜底 | 2h |
| 1.4 | 对比测试：合并前 vs 后的延迟和通过率 | 1h |

### 两周后（Phase 2）

| 步骤 | 内容 | 预估时间 |
|------|------|---------|
| 2.1 | segment 摘要层 | 3h |
| 2.2 | 长会话 prompt 压缩 | 2h |
| 2.3 | （可选）向量检索 PoC | 4h |

---

## 五、技术风险

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| deepseek-v4-flash 无法稳定通过结构化输出校验 | 高 | 管线卡死 | Phase 1.1 合并调用 + 1.3 确定性兜底 |
| 长 prompt 导致 flash 模型注意力分散 | 中 | 角色行为不一致 | Phase 2.1 摘要压缩 |
| SSE 连接被代理/防火墙缓冲 | 中 | 前端卡在"生成中" | 已有 `X-Accel-Buffering: no` header |
| opencode.ai 网关延迟/不稳定 | 中 | 用户体验差 | 添加超时 + retry_after 机制 |
| 审计报告中的 M1 lease 竞态触发 | 低（单用户） | 双重提交 | 暂不处理，单用户场景概率低 |

---

## 六、参考资源

- [Archon: Building an AI Dungeon Master With Real Memory](https://sep.com/blog/building-an-ai-dungeon-master-with-real-memory/)
- [Intra: design notes on an LLM-driven text adventure](https://ianbicking.org/blog/2025/07/intra-llm-text-adventure)
- [SoloQuest: Prompt Architecture for a Reliable AI Dungeon Master](https://dev.to/austin_amento_860aebb9f55/prompt-architecture-for-a-reliable-ai-dungeon-master-d99)
- [Infinite Novel (GitHub)](https://github.com/0penAGI/InfiniteNovel)
- [Schema-Governed LLM Pipeline (MDPI)](https://www.mdpi.com/2079-8954/14/2/175)
- [The Drama Machine (arXiv)](https://arxiv.org/html/2408.01725v2)
- [GalAgent V2 审计报告](docs/audit_report_2026-08-11.md)
