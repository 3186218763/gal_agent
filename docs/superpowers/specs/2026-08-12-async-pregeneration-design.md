# 异步预生成管线设计

> 日期：2026-08-12
> 状态：待审核
> 目标：消除 Galgame 游玩过程中的生成等待，实现玩家与后端的异步连续推进

## 一、问题陈述

### 当前瓶颈

每次玩家选择后，TurnOrchestrator 串行执行 2 次 LLM 调用：

1. **Planner.resolve_action**（选择解析）— ~5-15s
2. **UnifiedSegmentAgent.generate**（段生成）— ~10-20s

总计 15-35 秒，期间玩家看到 "AI 正在生成剧情…" 加载画面。这违反了 PROJECT_GOAL.md 的核心体验要求：

> *"普通场景之间不能出现加载和网络停顿"*
> *"前端从缓冲队列连续播放"*
> *"后端生成完成不等于玩家播放完成"*

### 当前代码已有的基础

| 已有 | 说明 |
|------|------|
| Unified Segment Agent | Director + Writer 已合并为单次 LLM 调用 |
| 确定性 Guard | 纯确定性检查，不消耗 LLM |
| 确定性 Fallback | `fallback.py` 生成最小有效段，保证不卡死 |
| SSE block 流式协议 | `segment_started → block → segment_ready` |
| 前端 SegmentPlayer | 完整的缓冲 → 播放 → 排空 → 选择状态机 |
| 确定性初始状态 | `initial_session_state` 完全由 pack 决定，不依赖 seed |

## 二、设计约束

| 约束 | 来源 |
|------|------|
| 玩家选择后等待 ≤ 15 秒 | 用户确认 |
| 等待期间要有进度感（文字逐步出现） | 用户确认 |
| API 成本不敏感，可预生成 2-3 个选择 | 用户确认 |
| 开场文本 5 分钟以上，与剧本包绑定 | 用户确认 |
| 同一剧本的所有 session 共享冻结开场 | 用户确认 |
| 选项不要太密集，段要长 | 用户确认 |
| 剧本导入有初始化过程，结果持久化 | 用户确认 |
| 流式播放不能绕过 Planner 和状态约束 | PROJECT_GOAL.md |

## 三、架构总览：双层缓存

```
┌──────────────────────────────────────────────────────────────────┐
│  Pack Cache（冻结层）                                              │
│  init-pack 时生成一次，所有 session 共享                             │
│                                                                  │
│  data/pack_cache/<pack_hash>/                                    │
│  ├── opening.json          开场长段（5min+）+ 第一个决策点的选择      │
│  └── pregen/                                                      │
│      ├── <choice_id>.json  第一个决策每个选择对应的下一段             │
│  │                                                                │
│  特性：pack 变更时自动失效（hash 不匹配则重新初始化）                  │
└──────────────────────────────────────────────────────────────────┘
                               ↓
                  玩家做出第一个选择后进入动态层
                               ↓
┌──────────────────────────────────────────────────────────────────┐
│  Session Cache（动态层）                                           │
│  运行时后台预生成，仅当前 session 有效                                │
│                                                                  │
│  内存: Dict[(session_id, choice_id), PreGeneratedSegment]         │
│                                                                  │
│  每次提交决策段后，后台并行预生成所有选择                              │
│  玩家选择时查缓存 → 命中则跳过所有 LLM 调用                           │
└──────────────────────────────────────────────────────────────────┘
```

### 玩家体验时间线

```
T=0       点击"开始游戏"
T=0       开场段从 Pack Cache 加载 → SSE 瞬间推送（零 LLM 调用）
T=0       前端打字机播放开场（5 分钟+）
          ── 后端无事可做：开场及其预生成都在 Pack Cache 里 ──

T=5min    玩家读完开场，选择第一个选项
T=5min    Pack Cache 命中 → 瞬间推送（零 LLM 调用）
T=5min    前端打字机播放第一段（~2-3 分钟）
          ── 后端立即开始 Session Cache 预生成 ──

T=5min30  预生成完成，存入 Session Cache
T=8min    玩家读完，选择 → Session Cache 命中 → 瞬间推送
          ── 后端立即开始下一轮预生成 ──

          → 从此玩家永远不会感到等待
```

### 唯一可能等待的场景

玩家阅读速度极快（5 秒/block），在预生成完成前就做出选择：

1. **预生成任务在跑** → 前端显示心跳进度，等任务完成（通常再等几秒）
2. **预生成失败** → 走正常生成 + 确定性 fallback（保证不卡死）

## 四、组件 1：Pack 开场初始化

### CLI 命令

```bash
uv run python -m src.story.cli init-pack script_packs/cafe_mystery
```

### 初始化流程

```
1. 编译 pack（已有逻辑）
2. 构建初始 session state（确定性，不依赖 seed）
3. 生成开场段
   ├── 使用专门的 opening prompt（更长、更多场景）
   ├── 目标：8-15 个场景，30-50 个 block（~5-10 分钟阅读量）
   ├── terminal="decision"，最后场景带 2-4 个选择
   └── 完整走 validate → guard → simulate 管线
4. 为开场段的每个选择预生成下一段
   ├── 模拟选择解析 → 假设状态 → 生成段 → 验证
   └── 并行执行（2-4 个并行 LLM 调用）
5. 持久化到 data/pack_cache/<pack_hash>/
```

### 持久化格式

```
data/pack_cache/<pack_hash>/
├── opening.json
│   ├── segment_plan       # SegmentPlan（含场景结构、选择）
│   ├── segment_draft      # SegmentDraft（含所有 block 文本）
│   ├── events             # 开场段产生的所有 StoryEvent
│   └── pacing             # 生成时使用的 PacingEnvelope
└── pregen/
    └── <choice_id>.json
        ├── choice_id      # 对应的选择 ID
        ├── pre_events     # 选择解析产生的事件
        ├── segment_plan   # 预生成的段计划
        ├── segment_draft  # 预生成的段草稿
        ├── seg_events     # 段模拟产生的事件
        └── pacing
```

### 失效策略

- Pack Cache 目录以 `pack_hash` 为键
- Pack YAML 变更 → hash 变化 → 旧缓存自动忽略
- 提供 `init-pack --force` 强制重新生成

### 开场 Prompt 设计

现有 unified agent prompt 需要一个 opening 变体：

```
关键区别：
- 目标场景数：8-15（vs 普通段 1-5）
- 必须以决策结尾（terminal="decision"）
- 铺陈世界、角色关系、初始矛盾
- 不要急于推进剧情，让玩家沉浸在开场氛围中
- 严格遵守知识隔离和所有现有写作规则
```

### 开场初始化是幂等的

- 检测 `data/pack_cache/<pack_hash>/` 是否存在且完整
- 已存在且 hash 匹配 → 跳过，输出 "Pack already initialized"
- 这满足用户要求："下次就不用初始化了"

## 五、组件 2：Session 级预生成

### PreGenerationManager

```python
class PreGeneratedSegment:
    """一个选择对应的完整预生成结果。"""
    pre_events: list[StoryEvent]      # 选择解析事件
    seg_events: list[StoryEvent]      # 段模拟事件
    plan: SegmentPlan
    draft: SegmentDraft


class PreGenerationManager:
    """后台预生成管理器，维护 session 级缓存。"""

    def __init__(self, ...):
        self._cache: dict[tuple[str, str], PreGeneratedSegment]
        self._tasks: dict[tuple[str, str], asyncio.Task]

    async def pregenerate_choices(
        self,
        session_id: str,
        state: SessionState,           # 当前提交后的状态
        choices: list[PresentedChoice],
        pack: CompiledScriptPack,
    ) -> None:
        """为每个选择启动后台预生成任务。"""
        for choice in choices:
            key = (session_id, choice.id)
            if key in self._cache or key in self._tasks:
                continue
            task = asyncio.create_task(
                self._pregenerate_one(session_id, choice, pack, state)
            )
            self._tasks[key] = task

    async def _pregenerate_one(
        self, session_id, choice, pack, state
    ) -> None:
        """单个选择的完整预生成管线。"""
        try:
            # 1. 解析选择（LLM）
            resolution = await planner.resolve_action(pack, state, choice)
            resolution = validate_action_resolution(
                pack, state, resolution, expected_action_id=choice.action_id
            )

            # 2. 模拟解析事件 → 假设状态
            pre_events = simulate_resolution(state, choice, resolution, idempotency_key)
            hypothetical_state = apply_events(state, pre_envelopes)

            # 3. 计算 pacing（确定性）
            pacing = compute_pacing_envelope(hypothetical_state, pack)

            # 4. 生成段（LLM）
            result = await unified_agent.generate(pack, hypothetical_state, pacing)
            plan = validate_segment_plan(pack, hypothetical_state, result.segment_plan, pacing)
            draft = validate_segment_draft(plan, result.segment_draft)

            # 5. Guard（确定性）
            guard_result = guard.check_segment(pack, hypothetical_state, plan, draft)
            if not guard_result.passed:
                return  # 预生成失败，放弃缓存

            # 6. 模拟段事件（确定性）
            seg_events = simulate_segment(pack, hypothetical_state, plan, draft)

            # 7. 存入缓存
            self._cache[(session_id, choice.id)] = PreGeneratedSegment(
                pre_events, seg_events, plan, draft
            )
        except Exception:
            pass  # 预生成失败 → 运行时走正常生成
        finally:
            self._tasks.pop((session_id, choice.id), None)

    def try_get(
        self, session_id: str, choice_id: str
    ) -> PreGeneratedSegment | None:
        """取出缓存项（取出后删除）。"""
        return self._cache.pop((session_id, choice_id), None)

    async def await_in_progress(
        self, session_id: str, choice_id: str, timeout: float = 15.0
    ) -> PreGeneratedSegment | None:
        """如果有在跑的任务，等它完成。"""
        task = self._tasks.get((session_id, choice_id))
        if task is None:
            return None
        try:
            await asyncio.wait_for(task, timeout=timeout)
        except (asyncio.TimeoutError, Exception):
            return None
        return self._cache.pop((session_id, choice_id), None)

    def cleanup_session(self, session_id: str) -> None:
        """清理一个 session 的所有缓存和任务。"""
        keys_to_remove = [
            k for k in self._cache if k[0] == session_id
        ]
        for k in keys_to_remove:
            self._cache.pop(k, None)
        for k in list(self._tasks):
            if k[0] == session_id:
                task = self._tasks.pop(k)
                task.cancel()
```

### 并发模型

- 每个选择一个 `asyncio.Task`，互不依赖
- 2-4 个并行 LLM 调用，受 OpenCode Go API 并发能力限制
- 如果 API 不支持并发 → 串行预生成，仍在阅读窗口内完成
- 任务异常不传播 — 单个选择预生成失败不影响其他

### 缓存生命周期

```
提交决策段 → pregenerate_choices(所有选择)
                                    ↓
              ┌─────────────────────┼─────────────────────┐
              ↓                     ↓                     ↓
         选择 A 缓存           选择 B 缓存           选择 C 缓存
              ↓                     ↓                     ↓
         玩家选 B ──────────────────┘
              ↓
         取出 B 缓存，提交
         丢弃 A 和 C（cleanup）
              ↓
         B 的段有选择 → pregenerate_choices(新选择)
              ↓
         循环……
```

## 六、组件 3：TurnOrchestrator 集成

### 修改后的 execute_turn 流程

```python
async def execute_turn(self, pack, session_id, expected_revision,
                       idempotency_key, choice_id):
    # ── Step 0: Claim（不变）──
    claim = self.store.claim_command(...)
    if claim.replay_json is not None:
        ...  # 幂等重放（不变）

    try:
        state = self.store.load_session(session_id)
        ...  # revision 检查（不变）

        if choice_id is None:
            # ── 开场轮次 ──
            cached_opening = self.pack_cache.load_opening(pack.pack_hash)
            if cached_opening is not None:
                plan, draft, events = cached_opening
                # 跳过所有 LLM 调用，直接进入提交
            else:
                # 正常生成（init-pack 未执行时的兜底）
                plan, draft, events = await self._generate_segment(...)

        else:
            # ── 选择轮次 ──
            # Step 1: 查 Session Cache
            pregen = self.pregen_manager.try_get(session_id, choice_id)

            # Step 2: 查 Pack Cache（冻结预生成）
            if pregen is None:
                pregen = self.pack_cache.load_pregen(
                    pack.pack_hash, choice_id
                )

            # Step 3: 查在跑任务
            if pregen is None:
                pregen = await self.pregen_manager.await_in_progress(
                    session_id, choice_id
                )

            if pregen is not None:
                # ── 缓存命中：跳过所有 LLM 调用 ──
                plan = pregen.plan
                draft = pregen.draft
                pre_events = pregen.pre_events
                seg_events = pregen.seg_events

            else:
                # ── 缓存未命中：正常生成 ──
                # （现有逻辑：resolve → generate → validate → guard）
                ...

        # ── Guard（不变）──
        # ── Stream blocks（不变）──
        # ── Simulate（如果缓存命中则跳过，已有结果）──
        # ── Atomic commit（不变）──

        # ── Step N: 触发下一轮预生成（新增）──
        if plan.terminal == "decision" and self.pregen_manager:
            choices = ...  # 从提交结果中提取
            updated_state = self.store.load_session(session_id)
            asyncio.create_task(
                self.pregen_manager.pregenerate_choices(
                    session_id, updated_state, choices, pack
                )
            )

        # ── Stream segment_ready（不变）──

    except Exception:
        self.store.release_command(...)
        raise
```

### 关键不变量

1. **缓存命中时仍走原子提交** — events 仍然通过 `commit_command` 原子写入，revision 检查不跳过
2. **幂等性不受影响** — command claim/replay 机制完全不变
3. **预生成结果经过完整验证** — validate + guard + simulate 全部执行，只是提前做了

## 七、段长度 / Pacing 调整

### 目标

- 开场段：8-15 场景，30-50 blocks（~5-10 分钟阅读）
- 普通段：3-8 场景，10-25 blocks（~2-5 分钟阅读）
- 选项间隔：至少 10 个 block 的连续演出

### 实现方式

**不改动 pacing 公式**，而是通过 prompt 引导 + segment 结构约束：

1. **Unified Agent prompt 增加**：
   ```
   - 生成足够长的连续 Galgame 演出。
   - 两次选择之间至少 8 个 block 的叙事和对话。
   - 不要急于推进到决策点 — 让玩家沉浸在场景中。
   ```

2. **开场 prompt 单独处理**：
   ```
   这是游戏开场。生成一段长篇开场演出（目标 30+ blocks）。
   铺陈世界观、角色关系、初始氛围。
   不要在此段内做出选择 — 在最后给出第一个决策点。
   ```

3. **可选：PacingEnvelope 增加 `target_block_range`**：
   ```python
   class PacingEnvelope:
       ...
       target_block_range: tuple[int, int]  # (min_blocks, max_blocks)
   ```
   开场时设为 (30, 60)，普通段设为 (8, 25)。

## 八、Pack Cache 实现

```python
class PackCache:
    """剧本包级别的冻结缓存。"""

    def __init__(self, root: Path):
        self.root = root  # data/pack_cache/

    def _pack_dir(self, pack_hash: str) -> Path:
        return self.root / pack_hash

    def has_opening(self, pack_hash: str) -> bool:
        return (self._pack_dir(pack_hash) / "opening.json").exists()

    def load_opening(self, pack_hash: str) -> CachedSegment | None:
        path = self._pack_dir(pack_hash) / "opening.json"
        if not path.exists():
            return None
        return CachedSegment.model_validate_json(path.read_text())

    def load_pregen(
        self, pack_hash: str, choice_id: str
    ) -> PreGeneratedSegment | None:
        path = self._pack_dir(pack_hash) / "pregen" / f"{choice_id}.json"
        if not path.exists():
            return None
        return PreGeneratedSegment.model_validate_json(path.read_text())

    def save_opening(
        self, pack_hash: str, segment: CachedSegment
    ) -> None:
        d = self._pack_dir(pack_hash)
        d.mkdir(parents=True, exist_ok=True)
        (d / "opening.json").write_text(
            segment.model_dump_json(indent=2)
        )

    def save_pregen(
        self, pack_hash: str, choice_id: str, segment: PreGeneratedSegment
    ) -> None:
        d = self._pack_dir(pack_hash) / "pregen"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{choice_id}.json").write_text(
            segment.model_dump_json(indent=2)
        )
```

### API 启动时检查

`default_dependencies()` 初始化时：
1. 扫描 `script_packs/` 下所有 pack
2. 对每个 pack 检查 `data/pack_cache/<hash>/opening.json` 是否存在
3. 记录日志：哪些 pack 已初始化，哪些需要 `init-pack`

不自动初始化 — 初始化是显式步骤（需要 API key，可能需要 1-2 分钟）。

## 九、前端影响

### 最小改动

SSE 协议完全不变。前端不需要知道内容来自缓存还是实时生成。

**唯一可见差异**：缓存命中时，所有 block 几乎瞬间通过 SSE 到达（而非逐个等待 LLM 生成）。前端现有的缓冲 → 播放状态机自然处理这种情况。

### 可选增强

1. **开场段从 projection 加载**：session 创建时开场已提交，前端可以直接从 `GET /sessions/{id}` 的 projection 中读取 blocks，不需要等 SSE。但这不是必须的 — 现有的 `/turns` SSE 路径也能工作。

2. **预生成进度指示**：如果前端想知道后端是否在预生成，可以增加一个 `GET /sessions/{id}/pregen-status` 端点。非必须。

## 十、不改变的部分

| 组件 | 理由 |
|------|------|
| 事件溯源架构（SQLite revisioned append） | 预生成只是提前计算，提交路径不变 |
| 幂等 / command claim 机制 | 缓存命中仍走 claim → commit，不绕过 |
| Guard / Validator / Simulator | 预生成结果经过完整验证管线 |
| Unified Segment Agent 输出合约 | plan + draft 结构不变 |
| SSE 协议 | `segment_started → block → segment_ready` 不变 |
| 前端 SegmentPlayer 状态机 | 缓冲 → 播放 → 排空 → 选择 不变 |
| CompletionJudge | 结局判定不变 |
| 确定性 fallback | 仍作为最后兜底 |

## 十一、测试策略

### 离线测试（不需要 API key）

1. **PackCache 读写**：序列化/反序列化、hash 匹配、缺失文件
2. **PreGenerationManager**：mock LLM 返回，验证缓存命中/未命中/任务取消
3. **TurnOrchestrator 缓存路径**：mock 预生成结果，验证缓存命中时跳过 LLM 调用
4. **缓存清理**：session 结束/选择后，旧缓存被正确清理
5. **并发安全**：多个预生成任务并行，缓存写入无竞态
6. **失效处理**：pack hash 不匹配时不使用旧缓存

### Live 测试（需要 API key）

1. **init-pack 端到端**：真实 LLM 生成开场 + 预生成，验证持久化
2. **session 创建**：加载冻结开场，验证 blocks 和事件正确
3. **缓存命中轮次**：选择预生成的选项，验证零 LLM 调用、正确提交
4. **缓存未命中轮次**：选择未预生成的选项，验证正常生成
5. **端到端流畅度**：完整游玩，记录每轮延迟

## 十二、风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| OpenCode Go API 不支持高并发 | 中 | 预生成串行，可能超出阅读窗口 | 串行预生成仍有窗口余量；fallback 兜底 |
| 开场 LLM 生成质量不理想 | 低 | 开场体验差 | init-pack 可 `--force` 重新生成；prompt 迭代 |
| 预生成段因状态漂移而无效 | 低 | 缓存命中后验证失败 | 单人游戏状态不会漂移；提交时仍走 revision 检查 |
| 预生成消耗过多内存 | 低 | 内存压力 | 每个 session 最多缓存 4 个段；选择后立即清理 |
| Pack YAML 变更但 hash 未更新 | 极低 | 使用过期缓存 | hash 基于文件内容计算，pack 变更必然改 hash |

## 十三、实施优先级

```
Phase 1 — PackCache + 开场初始化
  ├── PackCache 类
  ├── init-pack CLI 命令
  ├── 开场 prompt 变体
  ├── Orchestrator 开场缓存路径
  └── 测试

Phase 2 — PreGenerationManager + Session 缓存
  ├── PreGenerationManager 类
  ├── Orchestrator 缓存查询路径
  ├── 后台预生成触发
  ├── 缓存清理
  └── 测试

Phase 3 — Pacing / Prompt 调优
  ├── 段长度引导
  ├── 开场长度验证
  ├── 选项密度调整
  └── Live 端到端验证
```
