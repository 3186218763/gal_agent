# 动态 Galgame Agent 引擎 — 设计规格

**日期**：2026-08-10  
**状态**：已定稿（待实现）  
**范围**：V1 文字动态 Gal 引擎

---

## 1. 概述

### 1.1 一句话定义

作者只写**标准设定包**（世界 / 角色 / 多目标 / 多结局）；运行时 **Game Kernel** 调度 **Director / Character / Choice / Memory** Agents；**规则模块**控制隐式起承转合、张力与选项密度；玩家**不可自由输入、不可回溯**，只能从**已验证选项**前进，由可计算状态条件触发多结局。

### 1.2 背景

- 使用 **OpenAI Agents SDK** 驱动角色扮演与动态场面。
- 体验目标接近传统 Gal：大部分时间阅读，关键时刻选择分支。
- 与传统 Gal 的差异：不定死分镜顺序；选项与对白由 Agent 生成，但受状态与规则约束。
- 仓库已有 Beat 剧本驱动骨架（`plot.md` + Director/Character）；本规格将其演进为**状态/目标驱动**。

### 1.3 产品约束（锁定）

| 项 | 决策 |
|----|------|
| 形态 | V1 纯文字；阅读 + 分支选择 |
| 操作 | 禁止自由输入；仅 Agent 提供的选项 |
| 时间线 | 只能向前，无读档回溯 |
| 剧情自由度 | 不定死分镜顺序 |
| 作者输入 | 标准设定包（非线性 plot） |
| 完结 | 多主线目标（引力）+ 多状态阈值结局（结算） |
| 选项密度 | 动态调节（平时稀、冲突密） |
| 技术栈 | OpenAI Agents SDK、FastAPI、WebSocket、React、Pydantic、YAML |

---

## 2. 架构

### 2.1 拓扑

```
Game Kernel (确定性 orchestrator)
├── World State              # 当前快照
├── Event Database           # 只追加历史
├── Rule Modules
│   ├── Phase & Tension
│   ├── Option Trigger
│   ├── Goal / Ending Evaluator
│   └── Option Validator
└── Agents (OpenAI Agents SDK)
    ├── Director Agent       # 场景意图、目标引力、张力建议
    ├── Character Agents ×N  # NPC 对白与反应
    ├── Choice Agent         # 选项 + 结构化预期后果
    └── Memory Agent         # 检索 / 摘要相关历史
```

### 2.2 硬规则

1. **Kernel 是代码，不是 Agent**：主循环、写状态、阻塞等待玩家均由 Kernel 完成。
2. **Agent 不直写 World State**：只返回结构化结果，Kernel 校验后提交。
3. **Agents 不互相直连**：一律 `Kernel → Agent → 结果 → Kernel`。
4. **结局判定与选项触发以规则为准**：Agent 可建议，不可单独拍板结算；硬冷却内不可弹选项。

### 2.3 数据层

#### World State（快照）

至少包含：

- `session_id`, `pack_id`, `steps`
- `phase`: `setup | rising | climax | falling`
- `tension`: 1–10
- `flags`: `dict[str, bool | int | str]`
- `relationships`: `dict[char_id, {trust, romance}]`
- `goal_progress`: `dict[goal_id, GoalRuntime]`
- `turns_since_last_option`
- `summary`（可选，Memory 压缩结果）
- `pending_options`（等待玩家时非空）

#### Event Database（只追加）

每条事件至少：

- `id`, `step`, `type`（`narration | dialogue | player_choice | system`）
- `payload`（文案、角色、后果、tags 等）
- `phase`, `tension`（写入时快照）

用途：不可回溯证据链、Memory 原料、场景去重指纹。

### 2.4 Agent 职责边界

| Agent | 负责 | 不负责 |
|-------|------|--------|
| Director | 选推进方向（贴哪个 goal）、场景骨架、mood、出场角色、`tension_delta` 建议、`wants_option` | 长对白、最终改状态、判结局 |
| Character | 按性格/关系/记忆生成对白与选后反应 | 全局目标推进、生成玩家选项列表 |
| Choice | 2–4 选项文案 + 结构化预期后果 + 软 preview | 直接写 World State |
| Memory | 从 Event DB 召回/摘要本回合相关记忆 | 编造与历史矛盾的事实 |

### 2.5 回合时序

```
1. Memory 召回
2. Director 产出场景意图（目标引力 + phase）
3. Character 生成对白 → 推送阅读内容
4. 更新 tension / phase / steps；Event append
5. Option Trigger 计分
   - 否 → 下一阅读拍
   - 是 → Choice → Validator → 推送选项 → 阻塞等待
6. 玩家选择 → Kernel 应用后果 → Event append
7. Goal Tracker 更新 → Ending Evaluator
8. 未结束则回到 1
```

---

## 3. 设定包（作者输入）

### 3.1 标准设定包字段

一个可玩单元（一局/一章）一份 YAML（可拆多文件），**不写分镜顺序**。

| 字段 | 说明 |
|------|------|
| `pack_id`, `title` | 标识 |
| `premise` | 前提叙述 |
| `world.locations[]`, `world.factions[]` | 世界边界 |
| `characters[]` | id/name/personality/public_info/private_info/initial_relationship |
| `goals[]` | 多主线目标 |
| `endings[]` | 多结局 |
| `opening_seed` | 开场种子（非 Beat 序列） |
| `initial_flags` | 初始 flags |
| `max_steps` | 软上限（默认建议 24） |

### 3.2 Goals（引力）

```yaml
goals:
  - id: ally_alice
    title: "与艾丽丝结盟调查"
    description: "取得信任并同意合作"
    type: pursue          # pursue | avoid | discover
    weight: 1.0
    conflicts_with: [ally_bob]
    success_hint: "alice_trust 高且相关 flag"
```

运行时 `GoalRuntime`：

```text
status: locked | active | completed | failed | abandoned
progress: 0.0–1.0
evidence_event_ids: [...]
```

- 完成 A 时，对 `conflicts_with` 中的目标**降权**（V1 不强制锁死）。
- Director 每回合按 `weight * (1 - progress) * conflict_boost` 排序，服务 Top-1/Top-2。

### 3.3 Endings（结算）

```yaml
endings:
  - id: alice_route
    title: "信任的开始"
    priority: 100
    type: victory          # victory | branch | game_over | fallback
    condition: "goals.ally_alice.completed and alice_trust >= 70"
    content: "..."
```

- 表达式变量：`flags`、`{char}_trust`、`{char}_romance`、`goals.{id}.completed`、`steps`、`phase`
- 按 `priority` 降序，**第一个为真**触发
- 必须配置至少一条 `fallback`（或 `steps >= max_steps` 可命中的结局），防止无限局

### 3.4 Goals 与 Endings 分工

| | Goals | Endings |
|--|-------|---------|
| 作用 | 故事往哪漂 | 何时停、停成什么 |
| 驱动 | Director 场景选择 | Ending Evaluator |
| 数量 | 多条可并行 | 多条竞争 |

---

## 4. 阶段、张力与选项密度

### 4.1 Phase（隐式起承转合）

| Phase | 含义 | 选项密度倾向 |
|-------|------|----------------|
| setup | 建立人物与前提 | 稀 |
| rising | 关系/线索发展 | 中 |
| climax | 对立表面化、站队 | 密 |
| falling | 收束、引向结局 | 中偏稀 |

默认推进（可配置）：

- `setup → rising`: `steps >= 3` 或 任 goal progress ≥ 0.2 或 tension ≥ 5  
- `rising → climax`: `steps >= 10` 或 progress ≥ 0.6 或 tension ≥ 8  
- `climax → falling`: `steps >= 16` 或 重大选择已发生 或 任 goal completed  
- `falling → end`: ending 命中或 `steps >= max_steps`

Director 可给 `phase_hint`，但**最多前进 1 级**。

### 4.2 Tension（1–10）

```
tension' = clamp(1, 10,
  tension
  + director.suggested_delta    # ∈ [-2, +2]
  + event_modifiers
  + phase_bias                  # climax 抬高基线
)
```

事件 tags 示例：`confrontation`, `reveal`, `calm`。

### 4.3 Option Trigger

每阅读拍结束后计分；默认阈值 **50**。

| 信号 | 作用 |
|------|------|
| 硬冷却 | `turns_since_last_option < 2` → 禁止（score 极低） |
| 轮次 | 久未选择 → 加分 |
| tension | 高张力 → 加分 |
| phase | climax 加分，setup 减分 |
| `director.wants_option` | +15 |
| `decision_pressure` | +20（逼问/必须表态） |
| 过长零选项 | 兜底加分，避免纯观影 |

体验目标：阅读:选择约 **2:1～4:1**，climax 明显更密。

---

## 5. 选项生成与验证

### 5.1 Choice 输出协议

```json
{
  "options": [
    {
      "text": "先听艾丽丝说完，再表态",
      "stance": "cautious",
      "player_intent": "gather_info",
      "predicted_consequences": {
        "flag_changes": {"listened_to_alice": true},
        "relationship_deltas": {"alice": {"trust": 5}},
        "goal_effects": [{"goal_id": "ally_alice", "delta_progress": 0.1}],
        "tension_delta": -1,
        "tags": ["listen", "noncommit"]
      },
      "narrative_preview": "气氛稍缓，她愿意多说一点"
    }
  ]
}
```

约束：

- 2–4 个选项；文案短（建议 ≤ 24 字，上限 50）
- 玩家行动/态度视角
- 彼此 stance 或后果可区分
- 至少 1 个非极端
- 后果键名合法、角色存在

### 5.2 Validator

| 检查 | 规则 |
|------|------|
| 数量 | 2 ≤ n ≤ 4 |
| 非空后果 | 每条至少改 flag / 关系 / goal 之一 |
| 差分 | 后果指纹不得雷同（假选择） |
| 极端平衡 | n≥3 时不能全部为巨幅关系变化 |
| 合法键 | flag/角色/goal id 有效 |
| 去重 | 与近期 player_choice tags 完全同构则丢弃 |

失败：重试 Choice ≤ 2 次 → **兜底模板选项**（继续追问 / 观望 / 转移话题等预置小后果）。

### 5.3 Preview 与落地

- UI 显示选项文案 + 可选 `narrative_preview`（软提示）
- **不显示** `trust +5` 等数值
- 以验证后的结构化后果为准写入状态；Character 只生成选后反应，不改账

---

## 6. 对外接口（与现前端对齐方向）

### 6.1 保留

- `POST /api/sessions` 创建会话（参数改为 `pack_id`）
- `WS /ws/game/{session_id}` 实时推送

### 6.2 服务器 → 客户端事件类型

| type | 用途 |
|------|------|
| `game_start` | 开局 |
| `narration` | 叙述 |
| `dialogue` | 角色对白 |
| `options` | 选项列表（含可选 preview） |
| `state_update` | flags/关系/phase/tension 等变化 |
| `ending` | 结局 |
| `error` | 错误 |

### 6.3 客户端 → 服务器

```json
{ "type": "player_choice", "option_index": 0 }
```

无自由文本消息类型（V1）。

---

## 7. V1 范围

### 7.1 做

- Kernel + 双数据层 + 全套规则模块
- Director / Character / Choice / Memory（Agents SDK）
- 标准设定包加载 + 1 个示例包（可改编现有 chapter_01 素材）
- 文字 UI + WebSocket
- Session JSON 持久化
- 不可回溯、多目标多结局、选项验证

### 7.2 不做

- 立绘 / 音效 / BGM
- 玩家自由输入
- 读档回溯 / 多周目 meta
- 战斗 / 背包数值
- 流式逐字输出（可后加）
- 完整商业编剧工具链

### 7.3 成功标准

1. 无 `plot.md` 分镜可开一局  
2. 全程仅选项推进、不可回退  
3. 选项密度随 tension/phase 可感知变化  
4. 至少 3 个结局可通过不同选择路径到达  
5. Validator 可观测拦截假选择  
6. 角色对白不严重 OOC（人工抽测）

---

## 8. 与现有代码演进

| 现有 | V1 |
|------|-----|
| `game_loop.py` Beat 遍历 | Kernel 回合循环 |
| `script_parser` + `plot.md` | `setting_pack` 加载器；plot 弃用或仅作素材参考 |
| `director.py` 兼选项 | Director 瘦身 + 新 Choice Agent |
| `character.py` | 保留，接入 Memory 上下文 |
| `option_trigger.py` | 扩展 phase/tension 公式 |
| `option_generator.py` | Validator 升级 |
| `ending_evaluator.py` | 支持 `goals.*` 变量 |
| `state_manager.py` | goal_progress / phase / event_log |
| 前端 Game UI | 消息对齐；骨架复用 |

### 建议实现顺序

1. 数据模型：SettingPack / WorldState / Event / Goal / Option  
2. Kernel 空转（假生成器）跑通 读→选→状态→结局  
3. Rules：phase / tension / trigger / ending / validator  
4. Director + Character 接 SDK  
5. Choice + Validator 真生成  
6. Memory  
7. 示例设定包 + 前后端联调  

---

## 9. 风险与对策

| 风险 | 对策 |
|------|------|
| 故事漂、到不了结局 | 目标引力 + max_steps fallback ending |
| 假选择 | 后果指纹 Validator + 重试 + 兜底 |
| 节奏崩 | 硬冷却 + 阶段权重 + 可调参阈值 |
| 角色失忆 / 场景循环 | Event DB + Memory + 场景指纹去重 |
| API 费用 | 少出场少调用；Memory 优先规则召回 |
| 坏 JSON / 超时 | 重试 1 次 + 兜底文案/选项 |
| 状态非法 | Pydantic 校验，拒绝写入 |

---

## 10. 非目标与后续

- V1.5：流式输出、goal 调试面板、向量 Memory  
- V2：多章节连续、立绘图层、可选 SL（若产品改变「不可回溯」）  
- 工具链：设定包编辑器、结局可达性分析  

---

## 11. 决议记录

| 议题 | 决议 |
|------|------|
| 剧本自由度 | 全部不定死，只靠设定包 + 完结条件收敛 |
| 完结形态 | 状态阈值 + 主线目标，且都要「多」 |
| 选项密度 | 动态调节（张力/阶段） |
| 作者输入 | 标准设定包 |
| 拓扑 | 用户方案 + Kernel/规则模块改进版 |
| Preview | 软 narrative 提示，不显示数值 |
| 回溯 | V1 不做 |

---

*本文件为实现前的权威设计规格。实现计划：`docs/superpowers/plans/2026-08-10-dynamic-gal-kernel.md`。*
