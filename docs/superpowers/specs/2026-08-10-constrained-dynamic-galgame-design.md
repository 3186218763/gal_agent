# Galgame AI V2 - 受约束动态视觉小说设计规格

- **日期**：2026-08-10
- **状态**：设计已批准，待实施计划
- **目标版本**：纯文本叙事内核 V1
- **取代**：`2026-08-10-dynamic-gal-agent-design.md` 中以 `SettingPack + Director` 为核心的运行时设计

---

## 1. 产品定义

### 1.1 一句话定义

作者只提供统一**剧本包**：世界背景、固定主角、角色、事实空间、欲望冲突、行动边界和多结局契约；运行时由受约束的叙事内核动态规划事件，Agent 将已批准事件实现为小说场景和有限选项，玩家不可自由输入、不可回溯，只能通过关键选择改变后续世界状态并走向不同结局。

### 1.2 第一阶段产品

第一阶段交付一部可完整游玩的纯文本动态 Galgame，而不是通用创作平台或研究演示。

- 一次完整通关 1-3 小时
- 20-60 个场景
- 10-25 次关键选择
- 至少 3 个正常结局和 1 个兜底结局
- 同一剧本包重复游玩时路径明显不同
- 已观察事实不可改写，玩家不可读档回溯
- 主要体验是连续阅读，只在真正改变关系、风险或目标时出现 2-4 个选项

### 1.3 内容自由度

剧本包不包含固定场景、对白、剧情节点顺序或分支树。它只定义合法故事空间。运行时故事必须动态产生，但动态性受世界规则、事实、角色知识、行动能力和结局契约约束。

### 1.4 第一阶段非目标

- 立绘、背景、BGM 和语音
- 实时图片生成
- 玩家自由文本输入
- 每个 NPC 一个常驻自治 Agent
- 多章节跨作品世界
- 多人游戏
- 向量数据库
- 剧本包可视化编辑器
- 模型微调
- 允许玩家回退或重写历史

视觉资源字段会在剧本包和事件协议中预留，但不进入本阶段验收范围。

---

## 2. 成功标准

一个剧本包达到以下条件才算验证成功：

1. 可稳定完成 1-3 小时的纯文本游玩。
2. 至少 3 个正常结局在自动跑局和人工试玩中实际可达。
3. 重复游玩的事件路径、角色关系或秘密揭示过程存在实质差异。
4. 已提交事实矛盾率为 0。
5. 未授权角色知识泄露率为 0。
6. 所有会话在最大场景数内进入正常或兜底收束。
7. 每个玩家选项都具有不同的可观察意图或结果，不出现完全同构的假选择。
8. 人工试玩中不出现严重 OOC、剧情死循环或结局不回应本局经历。

---

## 3. 设计原则

### 3.1 Kernel 是裁判，Agent 是提案者和写作者

- Agent 可以提出事件、行动结果和文本草稿。
- Agent 不能直接修改游戏状态。
- Kernel 验证提案、模拟后果、提交事件并判断结局。
- 所有不可逆变化都必须先通过确定性规则。

### 3.2 世界真相与角色陈述分离

- 世界事实、角色知识、角色信念和角色说出口的话是四种不同数据。
- 角色可以误判或撒谎，但不能无理由知道未获知的事实。
- 对白中的陈述不自动成为世界事实。

### 3.3 未揭示时可分化，提交后不可改写

潜在真相可以在尚未产生因果影响时保持未定。首次生成不可逆证据、明确揭示或对角色行为产生决定性影响时，相关真相必须原子提交。提交后的事实不可修改。

### 3.4 选择表达玩家意图，不预写裁决

选项描述玩家要做什么，不直接携带任意的 `trust +5` 等最终后果。Action Resolver 根据事实、关系和角色意图解析结果，Validator 再限制结果范围。

### 3.5 结局是语义契约，不是固定段落

剧本包定义结局何时可用、必须兑现什么、禁止声称什么。Agent 根据本局事件动态写终章，但不能改变结局语义。

### 3.6 摘要不是事实来源

LLM 摘要、向量检索结果和模型会话历史都只是上下文缓存。Fact Ledger 与 Event Log 才是权威数据。

---

## 4. 总体架构

```text
Script Pack Sources
        |
        v
Script Pack Compiler ----> CompiledScriptPack
                                |
                                v
                         Story Kernel
                     +-------------------+
                     | World Snapshot    |
                     | Fact Ledger       |
                     | Character Runtime |
                     | Narrative Threads |
                     | Event Log         |
                     +-------------------+
                                |
                 Context Assembler / Memory Retrieval
                                |
                                v
                         Planner Agent
                                |
                         EventProposal[]
                                |
                 Validator + Consequence Simulator
                                |
                         Drama Manager
                                |
                         accepted plan
                                |
                         Scene Writer Agent
                                |
                    Scene Validator + staged commit
                                |
                  narration / dialogue / decision
                                |
                         player action
                                |
                        Action Resolver
                                |
                         atomic commit
                                |
                       Ending Evaluator
```

### 4.1 模块边界

| 模块 | 职责 | 不负责 |
|------|------|--------|
| Script Pack Compiler | 解析、标准化、引用检查、约束检查、生成包哈希 | 运行时剧情生成 |
| Story Kernel | 会话状态、事务、事件提交、并发控制、调用顺序 | 写小说文本 |
| Context Assembler | 精确召回事实、角色认知、线程和相关历史 | 决定下一事件 |
| Planner Agent | 提出结构化候选事件或行动结果 | 直接写状态、直接决定结局 |
| Event Validator | 前置条件、知识边界、事实一致性、合法引用 | 文学质量判断 |
| Consequence Simulator | 在状态副本上模拟候选后果与结局可达性 | 提交正式状态 |
| Drama Manager | 按推进价值、节奏、伏笔和新鲜度选择事件 | 生成长对白 |
| Scene Writer Agent | 将已批准事件实现为旁白、对白和选项文本 | 改变事件语义 |
| Scene Validator | 检查泄密、越权陈述、引用、长度和结构 | 重新规划故事 |
| Action Resolver | 将玩家意图解析成受约束的实际结果 | 接受自由文本行动 |
| Ending Evaluator | 维护可达结局集合，触发收束计划 | 临时生成新结局类型 |
| Event Store | append-only 历史、快照、恢复、幂等 | 业务规则 |

### 4.2 Agent 数量

第一版只需要两个主要生成角色：

1. **Planner**：以不同模式规划下一事件、解析行动结果、规划终章义务。
2. **Writer**：将已批准计划写成小说场景。

不默认创建每角色一个 Agent。角色差异通过结构化角色档案、知识边界、当前意图和说话风格传给 Writer。只有在试玩证明单 Writer 无法保持角色区分时，才引入 Character Agent。

---

## 5. 统一剧本包

### 5.1 语义模型优先于文件布局

作者可以使用单个 `pack.yaml`，也可以拆分目录：

```text
script_packs/<pack_id>/
├── pack.yaml
├── characters.yaml
├── facts.yaml
├── goals.yaml
├── endings.yaml
└── assets/
```

编译器将所有来源标准化为同一个不可变 `CompiledScriptPack`。存档记录编译包哈希，运行中不能悄悄切换内容版本。

### 5.2 顶层结构

```yaml
schema_version: "1.0"

identity:
  id: cafe_mystery
  title: 咖啡馆疑云
  language: zh-CN
  genres: [romance, mystery]
  expected_minutes: 120

experience:
  viewpoint: first_person
  prose_style: 轻小说
  tone: 温柔、悬疑
  choice_density: key_moments
  min_scenes: 20
  max_scenes: 60
  forbidden_content: []

protagonist: {}
world: {}
characters: []
facts: {}
goals: []
interaction_rules: {}
endings: []
assets: {}
```

模型供应商、API Key 和具体模型不属于剧本包，由部署配置管理。

### 5.3 主角

主角由剧本包定义，不是空白聊天用户。

```yaml
protagonist:
  id: protagonist
  name: 悠真
  personality:
    traits: [谨慎, 善于观察]
    values: [不轻易伤害别人]
    flaws: [容易犹豫]
  background: 刚搬到这座城市的大学生
  capabilities: [调查, 交谈, 跟踪]
  boundaries:
    cannot: [使用暴力逼供, 凭空掌握秘密]
```

生成选项必须符合主角能力与人格边界。人格允许在经历中发展，但不能无事件依据地反转。

### 5.4 世界与角色

世界定义前提、不可违反的规则、地点、派系和开场状态。角色至少定义：

- 公开身份和背景
- traits、values、fears、flaws
- 说话风格与禁用风格
- 长期 drives 与短期能力
- 开局知识、秘密和错误信念
- 与主角和其他角色的初始关系
- 不可突破的行为边界

角色 `drive` 产生行动动力，但不指定剧情节点。

### 5.5 事实类型

```yaml
facts:
  fixed:
    - id: org_exists
      statement: 神秘组织真实存在
      known_by: [alice, bob]

  latent_questions:
    - id: who_took_notebook
      question: 谁拿走了笔记本
      selection: lazy_commit
      candidates:
        - value: bob
          weight: 1
          requirements: []
        - value: cafe_owner
          weight: 1
          requirements: []
      commit_when:
        - first_irreversible_evidence
        - explicit_revelation
      evidence_required: 2

  derived:
    - id: alice_trusts_player
      condition: relationships.alice.trust >= 70
```

- `fixed`：开局即确定。
- `latent_questions`：候选受约束，延迟提交。
- `derived`：从当前状态计算，不单独持久化。

潜在事实候选必须声明互斥关系和适用条件。使用会使其他已提交事实矛盾的候选会被淘汰。

### 5.6 目标与 Narrative Thread 的来源

目标属于角色或体验层：

```yaml
goals:
  - id: alice_find_ally
    owner: alice
    desire: 找到愿意一起调查的人
    urgency: 0.7
    conflicts_with: [alice_hide_mistake]
    success_condition: relationships.alice.trust >= 70
    failure_condition: alice_has_left == true
```

运行时可以从目标、潜在问题、承诺、冲突和玩家选择创建 Narrative Thread。剧本包不需要预写线程推进顺序。

### 5.7 玩家行动目录

引擎提供标准社交行动：`ask`、`observe`、`support`、`challenge`、`withhold`、`disclose`、`follow`、`leave`。剧本包可以启用、禁用或扩展行动，并限制：

- 可用主体和目标类型
- 前置条件
- 允许影响的状态字段
- 单次变化范围
- 可产生的风险标签
- 明确禁止的副作用

所有选项必须映射到一个合法行动。

### 5.8 结局契约

```yaml
endings:
  - id: alice_alliance
    title: 共同追寻
    type: hopeful
    priority: 80
    eligibility:
      all:
        - goals.alice_find_ally == completed
        - relationships.alice.trust >= 70
        - facts.who_took_notebook.committed
    required_outcomes:
      - 玩家与 Alice 决定继续合作
      - 本局核心秘密得到解释
    forbidden_outcomes:
      - 将 Alice 写成幕后主谋
    closing_tone: 温暖，但保留未知风险
```

每个包至少包含 3 个正常结局和 1 个 fallback。fallback 同样必须包含收束义务，不能只是突然中止文本。

条件使用受限、可解析的声明式 DSL。YAML 示例中的条件字符串会被编译为类型化 AST；实现不得使用 Python `eval` 或允许任意代码执行。

### 5.9 编译检查

启动会话前必须通过：

- schema 与类型检查
- 全部 id 引用检查
- 潜在事实候选一致性检查
- 开局知识权限检查
- 行动效果边界检查
- 结局条件语法与引用检查
- 每个结局的有界可达性分析
- 至少一个始终可进入的收束路径
- min/max scenes 与结局条件不存在明显矛盾

编译器的可达性分析基于行动目录、条件表达式和抽象状态转换，证明“规则空间中存在路径”，不假设模型一定会生成该路径。真实生成行为由自动跑局继续验证。

编译失败的剧本包不能启动会话。

---

## 6. 运行时状态

### 6.1 Session State

```text
SessionState
├── session_id
├── pack_id / pack_hash
├── revision
├── status
├── session_seed
├── WorldSnapshot
├── FactLedger
├── CharacterRuntime{}
├── NarrativeThread{}
├── pending_scene
├── pending_decision
└── ending_state
```

`revision` 用于并发控制和拒绝过期选择。`session_seed` 使受控随机决策在重试和恢复后保持一致。

### 6.2 World Snapshot

保存当前地点、时间、在场角色、对象状态、关系、目标、阶段、叙事压力和剩余场景预算。

阶段单向推进：

```text
opening -> exploration -> escalation -> crisis -> resolution
```

阶段不绑定固定事件。它只调整候选事件评分、目标张力、选项密度和结局开放条件。

### 6.3 Fact Ledger

事实包含两个正交维度。真相状态为：

```text
possible -> staged -> committed
```

- `possible`：仍可选择多个合法候选。
- `staged`：本事务临时选定，未对玩家可见。
- `committed`：已成为不可变世界事实。

玩家可见性独立变化：

```text
hidden -> evidenced -> revealed
```

- `hidden`：玩家尚无有效信息。
- `evidenced`：玩家获得线索，但还不能确认结论。
- `revealed`：玩家已经获得足够证据或明确揭示。

`fixed` 事实开局即为 committed；公开事实同时为 revealed，私密事实保持 hidden。事实可以 committed 但尚未 revealed，revealed 不会覆盖或替代 committed 状态。

每条记录包含提交事件、证据事件、known_by、believed_by 和 revealed_to_player。

### 6.4 Character Runtime

每个角色独立维护：

- `knowledge`：确实知道的事实
- `beliefs`：相信但未必正确的命题
- `suspicions`：未确认推测
- `intentions`：短期行动意图
- `drives`：长期欲望和恐惧
- `emotional_state`
- `relationships`

Writer 的对白草稿必须附带结构化 `claims`，标注发言是事实陈述、推测、谎言还是情绪表达。Scene Validator 据此检查知识权限。

### 6.5 Narrative Threads

```yaml
id: missing_notebook
type: mystery
status: open
introduced_at: event_12
involved_characters: [alice, bob]
related_facts: [who_took_notebook]
urgency: 0.6
payoff_due_before: resolution
```

线程状态为 `open | advancing | dormant | resolved | abandoned`。Drama Manager 会惩罚长期无推进的高紧迫线程，并在 resolution 前要求处理所有必须兑现的线程。

### 6.6 Event Log

权威历史由 append-only Domain Event 构成：

- `SceneCommitted`
- `SceneAcknowledged`
- `PlayerActionSelected`
- `ActionResolved`
- `FactCommitted`
- `FactRevealed`
- `CharacterLearnedFact`
- `BeliefChanged`
- `RelationshipChanged`
- `GoalAdvanced`
- `ThreadOpened`
- `ThreadAdvanced`
- `ThreadClosed`
- `PhaseAdvanced`
- `EndingEntered`
- `SessionEnded`

World Snapshot 是事件重放得到的缓存。系统定期保存快照，但必须能够从 Event Log 恢复。

---

## 7. 场景规划与提交

### 7.1 EventProposal

Planner 每次返回 3-6 个结构化候选：

```yaml
purpose: 让 Alice 试探玩家是否可信
actors: [alice]
location: cafe
preconditions: [...]
focus_threads: [missing_notebook]
fact_operations: [...]
possible_revelations: [alice_lost_notebook]
player_pressure: 需要在相信、追问、回避之间表态
suggested_actions: [support, ask, withhold]
tension_delta: 0.1
novelty_tags: [trust_test]
```

### 7.2 确定性验证

候选至少检查：

- 角色、地点、事实、目标和线程引用存在
- 前置条件满足
- 角色知识足以支撑计划行为
- 事实操作不与 committed facts 冲突
- 行动在角色能力和世界规则内
- 与近期场景不构成语义或标签重复
- 不会让全部结局不可达
- 当前阶段允许该事件强度

### 7.3 Drama Manager 评分

Drama Manager 对合法候选进行代码评分，而不是再次让 LLM 自由挑选。评分维度包括：

- 玩家最近选择的因果相关性
- 角色 drive 和 goal 推进价值
- Narrative Thread 紧迫度与兑现期限
- 目标张力曲线匹配度
- 事件新鲜度
- 关系变化潜力
- 仍可达结局的数量与多样性
- 所需模型成本和场景复杂度

权重可配置，但第一版使用全局默认值，剧本包只允许有限覆盖。

### 7.4 延迟事实的原子提交

1. Fact Resolver 为候选选择合法潜在事实。
2. 在临时状态中标记为 `staged`。
3. Writer 使用临时状态生成场景和证据。
4. Scene Validator 验证文本、claims、证据和事实一致。
5. 一次事务提交 `FactCommitted`、相关状态事件和 `SceneCommitted`。
6. 任一步失败都丢弃临时状态，不对玩家发送草稿。

### 7.5 SceneDraft

一个场景包含 2-8 个显示单元，并以一种终止类型结束：

```text
SceneDraft
├── display_units[]
│   ├── narration
│   └── dialogue + claims
└── terminal
    ├── continue
    ├── decision
    └── ending
```

若终止类型为 `decision`，已批准的 EventProposal 必须先包含一个结构化 `DecisionContract`。Planner 定义合法行动、目标、主题和风险边界；Writer 只能为这些行动生成文案和 preview，不能新增或改写行动语义。Kernel 从验证后的 contract 构造最终 Decision。

Writer 使用最小权限上下文：只接收本场景允许表达的事实、角色知识、信念、谎言目标和 staged revelation，不接收无关隐藏真相。SceneDraft 的 claims 必须引用该 allowlist；这比把完整 Fact Ledger 交给 Writer 后再尝试拦截泄密更可靠。

服务端只正式提交一个场景。客户端阅读期间可以预生成下一个候选，但预生成结果不能修改状态。

---

## 8. 玩家选择与行动解析

### 8.1 Decision 协议

```yaml
decision_id: decision_42
state_revision: 107
prompt: 她停下来，等着你的回答。
options:
  - id: choice_42_a
    text: 直接问她为什么隐瞒笔记本
    intent: confront
    action:
      type: ask
      target: alice
      topic: missing_notebook
    preview: 她可能被迫表明立场
    risk: tense
```

客户端提交 `decision_id + option_id + state_revision`。不使用数组下标。相同请求幂等，过期 revision 被拒绝。

### 8.2 选项验证

每个选项必须：

- 映射到合法 PlayerAction
- 符合主角能力和人格边界
- 当前前置条件满足
- 与其他选项具有不同意图、风险或保证差异
- 选择后至少保留一个可达结局
- 不泄露数值后果或隐藏事实
- 文案简短，preview 只提供叙事软提示

### 8.3 Action Resolver

玩家选择后：

1. Kernel 校验 decision、option、revision 和幂等键。
2. Resolver 根据 committed facts、角色知识、关系、意图和行动规则构造实际结果。
3. Planner 可以提出 `success | partial | resisted | backfire` 及语义后果。
4. Validator 将数值和事实影响限制在行动目录允许范围内。
5. Consequence Simulator 检查结局可达性与状态不变量。
6. Kernel 提交 `PlayerActionSelected + ActionResolved + state events`。
7. 下一场景表现结果，不能重新裁决已经提交的行动。

模型提出的关系或目标变化只是候选，只有 Validator 接受后才成为状态变化。

---

## 9. 节奏与结局

### 9.1 阅读和选择密度

- opening：低压建立人物，选择稀疏。
- exploration：发现矛盾与秘密，约 3-5 场景一次选择。
- escalation：冲突加深，允许短暂缓和，约 2-4 场景一次选择。
- crisis：关键站队与不可逆决策，选择更密。
- resolution：主要回收伏笔，不继续无限扩张新线程。

连续两个关键选择之间至少有一个结果反馈场景。

### 9.2 结局可达性

Ending Evaluator 在每次事务后更新可达结局集合。早期阶段尽量保留多个结局；危机阶段允许玩家选择使部分结局永久不可达，但始终保留至少一个合法收束。

### 9.3 Resolution Plan

结局条件满足后不立即显示固定文本，而是生成收束计划：

```yaml
ending: alice_alliance
required_payoffs:
  - 回应 missing_notebook
  - 说明 Bob 最后的立场
  - 兑现 Alice 对玩家的信任
max_final_scenes: 3
```

Drama Manager 用 1-3 个终章场景完成义务。Writer 最后生成符合 required outcomes、forbidden outcomes 和 closing tone 的动态结局文本。

`max_scenes` 包含终章场景，是不可突破的总上限。系统默认预留最后 3 个场景作为收束预算；到达 `max_scenes - 3` 时仍未进入 resolution，就选择当前最可行的正常结局并规划最短收束路径。若预留预算内无法满足任何正常结局，才进入 fallback contract。

---

## 10. 模型与 OpenAI Agents SDK 边界

### 10.1 SDK 提供的价值

OpenAI Agents SDK 作为默认生成适配器，用于减少以下基础设施工作：

- Agent Runner 与模型调用生命周期
- 结构化输出
- 工具调用循环
- 重试、超时和异常归一化
- tracing 与调试
- 后续可能使用的 session、handoff 和 guardrail

实现前应升级并验证当前 SDK，而不是继续把 `openai-agents==0.1.3` 当成架构约束。

### 10.2 SDK 不拥有领域状态

以下能力始终由项目代码实现：

- Script Pack 和 World State
- Fact Ledger 与 Character Knowledge
- Narrative Threads
- Event Store 与快照
- 选项和后果验证
- Drama Manager 评分
- 结局判断与可达性
- 玩家不可回溯约束

### 10.3 适配器接口

领域层依赖薄接口，例如：

```text
ModelRunner.run_typed(role, input, output_schema, trace_context)
```

OpenAI Agents SDK 是默认实现。测试使用 Fake/Recorded Runner。模型配置属于部署环境，不进入领域模型。

### 10.4 调用预算

正常场景目标为两次主要模型调用：一次 Planner，一次 Writer。关键选择后的 Action Resolver 在需要语义裁决时可增加一次 Planner 调用。修复重试最多各一次。Context Assembler、Validator、Drama Manager、Ending Evaluator 和 Event Store 不调用模型。

---

## 11. 客户端与服务端协议

### 11.1 服务端事件

- `session_started`
- `scene_presented`
- `decision_presented`
- `ending_presented`
- `recoverable_error`
- `fatal_error`

`scene_presented` 携带稳定 `scene_id`、`state_revision` 和显示单元。第一版 UI 按小说阅读方式逐段展示。

### 11.2 客户端命令

- `scene_consumed(scene_id, state_revision)`，只用于 terminal=`continue` 的场景，提交后记录 `SceneAcknowledged`
- `player_choice(decision_id, option_id, state_revision, idempotency_key)`

不存在自由文本消息类型。

terminal=`decision` 的场景不另发 `scene_consumed`；`player_choice` 同时表示该场景已读并提交玩家行动，避免 acknowledgement 造成 choice revision 过期。

### 11.3 断线恢复

场景必须先记录 `SceneCommitted` 并完成领域事务，再发送给客户端。若发送后未收到 `scene_consumed` 就断线，重连时重新发送同一个已提交场景，不重新生成。

每个 session 同一时间只允许一个 Kernel 命令执行，避免并发选择覆盖状态。

---

## 12. 错误处理

| 故障 | 行为 |
|------|------|
| 剧本包编译失败 | 拒绝创建会话，返回具体路径和错误 |
| Planner 输出结构错误 | SDK/适配器重试一次 |
| 没有合法候选事件 | 使用代码生成的安全观察、追问或缓和事件 |
| Writer 泄密或越权 | 丢弃草稿，带拒绝原因重试一次 |
| Writer 二次失败 | 使用已批准事件的模板化场景，不改写语义 |
| 选项全部非法 | 重生成一次；仍失败则使用与当前事件绑定的安全行动集合 |
| 模型超时 | 不提交状态，返回可重试错误 |
| 状态事务失败 | 回滚 staged state，不发送草稿 |
| 重复玩家命令 | 返回第一次提交结果，不重复应用 |
| 过期 choice | 拒绝并返回当前 pending decision |
| 无正常结局可达 | 记录 invariant failure，进入编译包定义的 fallback resolution |
| 服务器崩溃 | 从最近快照和 Event Log 恢复 |

错误兜底只能降低文本丰富度，不能绕过事实和状态规则。

---

## 13. 测试与评估

### 13.1 确定性测试

- 剧本包 schema、引用和编译
- Fact Ledger 生命周期与不可变性
- 角色知识和信念隔离
- Narrative Thread 状态机
- 行动前置条件和效果范围
- 事务原子性、幂等和并发 revision
- Event Log 重放与快照一致性
- 结局条件与有界可达性

### 13.2 Agent 契约测试

使用 Fake/Recorded ModelRunner 验证：

- 非法 EventProposal 被拒绝
- Writer 泄露未揭示事实时不提交
- OOC 或越权 claims 触发修复
- 损坏结构化输出能够恢复
- 双重失败进入安全事件
- 录制响应可以重放完整会话

### 13.3 自动跑局

每个剧本包至少提供以下自动玩家策略：

- `supportive`
- `skeptical`
- `risk_taking`
- `avoidant`
- `random`

第一版验收执行：

- 至少 100 次无模型规则模拟
- 至少 20 次真实模型完整跑局
- 至少 3 个正常结局实际可达
- 所有跑局在最大场景数内结束
- committed fact contradiction 为 0
- unauthorized knowledge leak 为 0
- 完全同构选项为 0

### 13.4 人工试玩

至少 5 次人工完整试玩，按统一量表评估：

- 因果清晰度
- 角色可信度
- 选择影响感
- 节奏
- 秘密揭示公平性
- 伏笔兑现
- 结局对本局经历的回应
- 重玩意愿

LLM Judge 可以作为辅助筛查，但不能代替领域规则或人工质量判断。

### 13.5 可观测性

每个场景 trace 保存：

- pack hash、model、prompt version、session revision
- Context Assembler 选中的事实、角色状态、线程和历史
- Planner 候选
- 候选拒绝原因与 Drama Manager 分数
- Writer 草稿与 Scene Validator 结果
- 最终状态差异
- token、latency、retry 和错误

生产日志避免记录 API Key 和不必要的完整私密提示内容。

---

## 14. 现有代码迁移

### 14.1 保留的方向

- FastAPI 与 React 工程骨架
- Kernel 独占状态写入的原则
- Pydantic 领域模型
- append-only event 的基础思路
- Stub/Fake 模式
- 规则模块测试方式
- WebSocket 实时推送能力

### 14.2 需要替换

| 当前实现 | V2 |
|----------|----|
| `SettingPack` | `ScriptPackSource -> CompiledScriptPack` |
| 简单 `WorldState` | Session / World / Facts / Characters / Threads 分层状态 |
| 最近 K 条 Memory | Context Assembler 的精确结构化召回 |
| Director 直接生成下一幕 | Planner 候选 + Validator + Drama Manager |
| Character 独立写一句对白 | Writer 实现完整批准场景 |
| Choice 直接预测后果 | PlayerAction + Action Resolver |
| `phase/tension` 固定阈值 | 体验阶段、目标张力与线程义务评分 |
| option index | decision id + option id + revision |
| WebSocket 自动连续推进 | 单场景提交 + consumed/choice 命令 |
| 命中结局立即输出固定文案 | Resolution Plan + 动态终章 |

旧 `core/game_loop.py` 和旧 Beat/plot 路径不参与 V2。迁移期间可以保留文件用于对照，但最终运行入口只能有一套权威 Kernel。

---

## 15. 调研依据

本设计采用“显式状态与约束负责因果，LLM 负责候选和语言实现”的混合路线，依据包括：

- [Narrative Planning: Balancing Plot and Character](https://doi.org/10.1613/jair.2989)：叙事需要同时保证因果推进与角色行动可信度。
- [Search-Based Drama Management in the Interactive Fiction Anchorhead](https://doi.org/10.1609/aiide.v1i1.18723)：Drama Manager 可将全局体验管理建模为候选和未来质量搜索。
- [Generative Agents: Interactive Simulacra of Human Behavior](https://arxiv.org/abs/2304.03442)：观察、记忆、反思和计划有助于角色可信行为，但不替代全局叙事控制。
- [SceneCraft](https://doi.org/10.1609/aiide.v19i1.27504)：使用目标和场景结构约束 LLM 实现互动场景。
- [Drama Llama](https://arxiv.org/abs/2501.09099)：结构化触发与 LLM 生成结合，比纯即兴更容易保持响应性和控制。
- [NarrativeGenie](https://doi.org/10.1609/aiide.v20i1.31868)：事件表示、部分顺序与实时调度可以兼顾动态性和预期叙事弧。
- [OpenAI Agents SDK guide](https://developers.openai.com/api/docs/guides/agents)：SDK 适合承担 Runner、工具循环、会话、追踪和 guardrail 等模型编排基础设施。
- [OpenAI Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs)：模型边界应优先使用结构化输出，而不是从自由文本中手工截取 JSON。

调研不支持让多个 LLM Agent 自由互聊并自行决定世界状态。那种方案涌现性高，但难以保证事实一致、结局可达、成本和可测试性。

---

## 16. 已批准决议

| 议题 | 决议 |
|------|------|
| 产品优先级 | 先完成一部好玩的动态 Galgame，不先做通用平台 |
| 作者输入 | 只有剧本包，不写固定作品、场景树或分支路线 |
| 主角 | 剧本包定义的固定主角 |
| 单局长度 | 1-3 小时 |
| 玩家自由度 | 只能选择 Agent 提供的关键选项，无自由输入 |
| 选择密度 | 连续阅读为主，关键时刻选择 |
| 时间线 | 不可回溯 |
| 动态事实 | 未揭示可分化，首次因果使用时原子提交 |
| 结局 | 多结局语义契约，终章动态生成 |
| 视觉 | 第一版纯文本，协议预留资源槽位 |
| 核心架构 | 约束驱动 Kernel + Planner + Drama Manager + Writer |
| Agent SDK | 默认生成适配器，用于减少模型编排底层工作，不拥有领域状态 |
| 验收 | 一个剧本包、至少 3 个可达结局、重复路径不同、无事实回写和死循环 |

---

*本文件是 V2 实施计划的权威设计输入。用户审阅通过后，下一步仅创建详细实施计划，不直接进入编码。*
