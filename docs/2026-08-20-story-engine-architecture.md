# 剧情引擎架构重设计提案（2026-08-20）

状态：提案，待评审。替代范围：生成/上下文/作者层架构；不替代事件溯源内核。

## 0. 结论

当前引擎的病根一句话：**它是一个"戏剧记账系统"挂着一个"失忆的即兴写手"——有记账、无记忆、无计划。**

- **记账**有了：事实真值、知识隔离、关系数值、义务账本、完成度契约、因果链——这套确定性内核是项目真正的资产，保留。
- **记忆**没有：模型唯一能看到玩家读过的原文的渠道是 1000 token 尾窗（约最后一段），此前全部正文退化为每场一行摘要。封皮黑→深蓝、目击地点三段三说、整套桥段三遍重演，都是"未建模为 fact 的叙事细节没有正典存储"的必然结果，不是模型不行。
- **计划**没有：pack 里没有一场戏、没有一个节拍、没有一句作者正文。Director/统一代理每一段都在从零即兴结构。同时状态机里设计了弧光/信念/承诺/立场挑战共 9 类事件，生产运行时**从未发出**（全仓验证）——"戏剧账本"是一个没接线的精巧机关。

外部调研（Dramatron 分层生成、Re3/DOC 大纲控制、Drama Llama/Dramamancer storylet、Ink 织入收束、story-skills/Novel-OS 的 story-bible 文件模式、Generative Agents 记忆流、Open-Theatre 场景链+自动试玩）指向同一个共识结构：**作者写节拍骨架，确定性引擎导航，LLM 只做场景级演出，正典账本管记忆。**

本提案 = 保留内核 + 三个新子系统 + 剧本包 v3：

| 子系统 | 解决什么 | 对应失败类（.scratch/script-quality 实测） |
|---|---|---|
| A. 叙事正典账本 Canon Ledger | 叙事记忆：实体细节、承诺伏笔、母题、场景档案 | ④整段复读 ⑤细节自相矛盾 |
| B. 节拍骨架 Beat Map + 戏剧导航 | 叙事计划：作者的场景级表达力、结构防重、决策密度 | ①选项密集无进展 作者无法写戏/结局 |
| C. 场景级演出环 Scene Loop | 生成单位与失败经济：小单元、块级修复、砍串行调用 | ②场景跳变 ③语气断裂 ③延迟/fail-closed |

---

## 1. 诊断：五个结构性缺陷（代码实证）

### 1.1 模型看不到自己在写的故事

- 逐字窗口 1000 token（`segment_context.py:40`）+ 60 块环（`state/models.py:190`）≈ 只覆盖最后一段。commit 5b96111 加窗后失败率 -89%，证明"看得见尾巴"价值巨大；但窗口是权宜，中期细节照样漂移。
- 场景摘要由生成模型自己写、每场一行 ≤200 字符、无质量校验（`contracts.py:59-61`）。
- **未建模为 fact 的自由细节（封皮颜色、目击地点、角色姿态）没有任何 canonical 存储**——窗口滑出即进入无人管辖的自由漂移区。
- 判官（semantic judge）的上下文**不含逐字窗口**（`semantic_judge.py:104-156` 只有 digest）："整段复读"对判官结构性不可见。

### 1.2 重复没有结构性防线

- 防复读全靠指令"never repeat"只约束窗口内；窗口外的结构只有一行摘要，模型看不出"这套结构已演过三遍"。
- `eval/detectors.py` 里的 `shared_phrases`/`_maximal_common_substrings` 判重**只存在于离线评测，运行时零接线**（验证：runtime 无导入）。
- 节拍/beat 无一等概念，引擎不知道"这个戏剧情境已经发生过"（Ink 的 visit count 对应物不存在）。

### 1.3 作者表达力错位：能写谜题，不能写戏

- pack.yaml 389 行里没有一场戏、没有一个节拍、没有一句正文、没有一个结局。作者实际是"约束与素材作者"。
- 幕结构唯一抓手是 min/max/reserved 三个数字；phase 阈值（0.20/0.45/0.70）硬编码在 `endings.py:58-76`，作者不可调。
- 结局完全生成（ADR 0001 有意为之），作者不能保证任何结局可达、不能写结局的形。
- 状态机承诺的弧光/心智表达是死状态：`ArcPressureAdvanced`、`CharacterDramaticStateChanged`、`BeliefChanged`、`PromiseOpened/Changed`、`ConsequenceScheduled`、`StanceChallenged`、`DramaticQuestionSet` 共 9 类事件在 simulator/turn_orchestrator 中**零构造点**。后果之一：`stance_defended` 完成度算子在真实对局中**永远无法满足**（需要 `StanceChallenged` 夹在 established 与 reinforced 之间）——作者侧隐形陷阱。

### 1.4 管线信息丢失

- `ActionResolution.outcome`（success/partial/resisted/backfire）进事件流但**不进任何 prompt**——写手知道玩家选了什么，不知道世界如何回应。
- `player_choice` 只存活一段（`segment_context.py:158`）；之后承诺在 digest 里只剩 kind 字符串，intent 原文丢失——choice_reversal 的直接根源。
- `state.drama.stances` 不进任何上下文；guard 的"语义层"要求 fact_id 字面出现在正文，而写作规则又禁止 ID 出现——检查与规则互斥，天然空转。

### 1.5 失败经济学：大单元 × 整段重摇 × 3 次串行调用

- 每选择回合 = planner → unified → judge 三次串行模型调用，单次 1-2.5 分钟，合计 3-7 分钟。
- 一次 regen 预算 1、整段丢弃重来（无块级修复）；预算耗尽 fail-closed 且 `fallback.py` 是死代码——玩家面对"生成失败请重试"，整回合重跑。
- 每段强制以 2-4 选项收尾（`segment_contracts.py:54` terminal 只有 decision/ending）——共识文档已认定这是反模式，当时搁置。

---

## 2. 保留的地基（不推翻，以及为什么）

| 保留 | 理由 |
|---|---|
| 事件溯源 + reducer + 不变量 | Committed History 唯一真相源、精确回放、幂等命令。这是全项目 447 个测试覆盖最多、最成熟的部分，也是与"纯 LLM 跑团"产品的本质差异。 |
| 知识隔离（世界真值≠角色陈述，facts/known_by/beliefs） | 防"角色未卜先知"的硬保证，galgame 多线攻略的刚需。 |
| Choice Meaning 先提交、后生成 | 玩家输入不可丢，因果链起点（ADR 0003/0013/0011），保留。 |
| 完成度契约 CompletionJudge（证据+引用） | "结局是否算讲完"可审计，保留并增强（ending seeds 提供作者锚点）。 |
| validator 确定性校验、条件 DSL | 保留；条件 DSL 直接复用为 beat 的前置条件语言。 |
| SSE 单命令流 /turns、前端播放器 | 传输与播放层不动，scene 级流式反而让前端更早收到首批块。 |

**推翻/重建**：unified 单次大调用、planner 独立串行层、director 从零即兴、三层混合上下文的"摘要账本"部分、每段强制决策、纯生成结局（ADR 0001 部分推翻）、guard 装饰性语义层。

---

## 3. 新架构总览

```text
┌─────────────────────────── 作者层（pack v3）───────────────────────────┐
│ story bible: 世界/角色/事实/谜题（现有）                                 │
│ + Beat Map: 幕-节拍骨架（前置条件DSL、必含内容、效果、收束点、场景速写）      │
│ + Ending Seeds: 作者写的结局之形（框架/基调/必含节拍）                     │
│ + 节奏画像: 每幕的决策密度与块数目标（替代硬编码）                          │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   v
玩家选择 → [提交 Choice Meaning（不变）]
                                   |
                 ┌─── B. DramaManager（戏剧导航，确定性为主）───┐
                 │ 1. 条件求值: beat 图中哪些节拍已解锁/到期/必须    │
                 │ 2. 用 Choice Meaning 作前置输入选路（visit-once）│
                 │ 3. 产出本次 Segment 的 SceneBrief 序列           │
                 │    （场景目标/在场者/必含内容/衔接锚/禁区）        │
                 └───────────────────────┬────────────────────────┘
                                         v  （每个场景一次模型调用）
                 ┌─── C. Scene Performance Loop（场景级演出）─────┐
                 │ ScenePerformer: SceneBrief                     │
                 │   + CanonLedger 检索切片（相关实体卡/开放承诺/   │
                 │     母题黑名单/场景档案）                        │
                 │   + 逐字尾窗（上一场景结尾）                     │
                 │ → prose blocks + 结构化增量提案                  │
                 │ → 确定性校验（schema/引用/查重/账本一致性）        │
                 │ → 块级修复环（预算2） → SemanticJudge（带尾窗）   │
                 └───────────────────────┬────────────────────────┘
                                         v
                 ┌─── A. Canon Ledger（叙事正典账本，新事件类型）──┐
                 │ 实体登记簿 / 承诺伏笔账 / 母题与桥段登记 /        │
                 │ 场景档案（摘要+关键台词）/ 死事件接线             │
                 └───────────────────────┬────────────────────────┘
                                         v
              内核（不变）: simulator → reducer → EventStore
              → CompletionJudge（ending seed 锚定）→ 原子提交 → SSE
```

三个子系统各答一个问题：**A 回答"已经写过什么"，B 回答"接下来该讲什么"，C 回答"这一场怎么写"。** 当前架构三个问题都没有一等答案，全压给一次 LLM 调用。

---

## 4. 子系统 A：叙事正典账本（Canon Ledger）

### 4.1 定位

把"实际写过什么"变成事件溯源的一等公民。账本条目是**新的 StoryEvent 类型**，与现有事件同批提交、同样不可变、同样进 reducer。生成时不再"塞历史"，而是**按场景检索切片**（MemGPT 调页 + Generative Agents 检索的务实版：确定性检索优先，向量检索留作增强）。

### 4.2 账本内容（新事件类型）

| 事件 | 内容 | 修什么 |
|---|---|---|
| `EntityEstablished` / `EntityAttributeSet` | 具名实体（物品/地点/角色外观/称谓）：属性=值+出处块ID。例：`notebook.cover = 黑色硬皮 @S2-block14` | ⑤ 封皮黑→深蓝 |
| `NarrativePromiseOpened` / `NarrativePromisePaid` | 散文层承诺与伏笔（契诃夫之枪）：承诺句摘录 + 应回收期限（beat/幕） | ① 后果蒸发、伏笔悬空 |
| `MotifUsed` | 已用桥段/姿态/意象登记：`bob.push_glasses @S1`、`mina.closing_call @S1,S2` | ④ 同姿态重演 |
| `SceneArchived` | 场景档案：摘要（升级为 3-5 行，含关键台词摘录）+ 结构签名（本场景做了什么戏剧动作） | ④ 整段复读、② 衔接 |
| 接线死事件 | beat effects 确定性发出 `ArcPressureAdvanced`/`PromiseChanged`/`StanceChallenged` 等；`stance_defended` 算子自此可达 | 1.3 死状态机 |

### 4.3 维护方式

- **生成即登记**：ScenePerformer 的输出 schema 增加 `ledger_updates` 字段（实体属性、新承诺、母题），与 blocks 同一次调用产出——不增加调用次数。validator 校验：引用的实体要么已登记、要么声明为 new；对已登记属性给新值 = **连续性冲突**，直接拒并给出旧值（与 issue 12 的"可操作拒绝"同模式）。
- **冲突即拦截**：`notebook.cover` 已是黑色，草稿写深蓝 → 确定性拒绝（属性值在两端都是显式结构化数据，可字符串比对；不依赖语义理解）。
- 场景档案的摘要质量：由 judge 侧新增 informational 检查（摘要是否含关键转折），不阻断。

### 4.4 消费方式（Context Assembler v2）

场景简报的上下文 = **检索切片**而非平铺 digest：

1. **实体卡**：本场景 beat 声明的实体 + 尾窗提及的实体 + 最近活跃实体，各带全部已确立属性（封皮颜色这类细节自此永远在场）。
2. **承诺账**：本 beat 相关的未回收承诺（带原文摘录，不再只剩 kind 字符串）；`player_choice` 的 intent 全文进入承诺账，**存活到兑现或显式释放**，不是一段。
3. **母题黑名单**：最近 K 个场景用过的姿态/桥段/结构签名，明确"以下已演过，禁止重演"。
4. **场景档案**：按相关性+时近取 8-12 条（3-5 行版），替代现在的 24 条一行版。
5. **逐字尾窗**：保留，预算提到覆盖"上一场景结尾"（约 600-800 token），因为场景级生成后相邻距离缩短。

总预算有硬上限（token 预算管理器统一分配，消灭现在 characters×2、goals×2 的重复膨胀）。

---

## 5. 子系统 B：节拍骨架（Beat Map）+ 戏剧导航（DramaManager）

### 5.1 定位

作者重新获得**场景级的表达权**，但以 CONTEXT.md 的哲学表达：**定义可能性空间，不枚举分支**。一个 beat（节拍/故事卡）是"什么时候、什么条件下、必须发生什么戏剧事件"的声明，正文仍生成。这是 Drama Llama 的 storylet（内容+前置+效果）与 ADR 0006（用义务而非固定节拍约束戏剧）的合流：义务约束"必须处理什么"，beat 约束"在哪里处理、处理到什么程度"，两者互相锚定。

### 5.2 Beat 数据模型（pack v3 `structure` 段）

```yaml
structure:
  acts:
    - id: common_route
      scene_budget: [8, 14]
      decision_policy: at_beats          # 决策只出现在 decision beat 之后
      beats:
        - id: fox_note_found
          purpose: "触发谜题 paper_fox_sender，建立艾丽丝的求助关系"
          requires: "scenes >= 1"          # 复用现有条件 DSL
          responds_to: [help_alice]        # 可选：由哪些 choice intent 解锁
          position: {min: 1, max: 4}
          once: true                       # visit-once（Ink 访问计数）
          must_include:                    # 作者写的必发生内容（自然语言）
            - "旧书架后夹着一只折纸狐狸，里面裹着手写便签"
          scene_sketch:                    # 建议而非强制
            place: club_room
            present: [alice, protagonist]
            time: after_school
          effects:                          # 确定性落账（接线死事件）
            facts: [stage paper_fox_sender]
            promises: ["艾丽丝相信纸狐狸与失踪的笔记本有关"]
          successors: [fox_exchange, dismiss_fox, keep_secret]   # 收束点提示
        - id: fox_exchange
          kind: decision                    # decision beat：其演出段末尾出选项
          ...
```

要点：

- **决策密度由 beat 类型决定**：`kind: decision` 的 beat 之后才有选项；过场 beat 连续演出多场景不打断——修"每段强制 2-4 选项"反模式，且 choice_density 成为作者可配置项而非引擎常量。
- **once + successors 实现 Ink 式分支收束**：分支后 successors 列出可能的收束 beat，DramaManager 保证殊途同归到关键节点（真结局前置节拍），分支爆炸被结构性地封死。
- **must_include 是作者正文的回归**：关键台词、关键意象作者可直接写，生成器必须包含（judge 新增 blocking 类别 must_include_missing 的对照物）。
- **effects 是死状态机的接线**：`ArcPressureAdvanced`/`PromiseChanged`/`StanceChallenged`/`CharacterDramaticStateChanged` 由 beat 确定性发出，弧光与承诺账本从"设计了没接"变为"作者声明式驱动"。
- phase 阈值、target_block_range、ENDING_BLOCK_FLOOR 全部移入每幕的节奏画像（pacing profile），作者可调。

### 5.3 DramaManager 导航（确定性为主）

每回合（选择提交后）：

1. **求值**：条件 DSL 对当前 state 求值，得到已解锁/到期（position max 逼近）/强制（mandatory 且未做，收敛窗内优先）的 beat 集。
2. **选路**：确定性打分（强制性 > 到期压力 > 与 Choice Meaning 的 responds_to 匹配 > 作者 priority）；分不出时才用一次轻量 LLM 调用做语义 tiebreak（可配置关闭）。
3. **产出 SceneBrief 序列**：本 segment 演出 1-3 个 beat，每个 beat 一份 SceneBrief：

```text
SceneBrief（结构化，确定性组装）
- 戏剧目标：本 beat 的 purpose + 必含内容
- 世界衔接锚：上一场景末块原文 + 当前时间/地点/在场者（从账本取）
- 相关实体卡 + 相关承诺账（检索切片）
- 边界：禁区、不许揭示的事实、母题黑名单
- 收尾指令：本场景是否 decision（出选项）/ continue / ending
```

**planner 层取消**。选择的世界后果由两处承接：beat 的 `responds_to`/effects（结构层）+ ScenePerformer 的结构化增量（数值层，沿用现有 validator 边界）。串行调用从 3 次降到 1-2 次，且 `ActionResolution.outcome` 信息丢失问题消失——outcome 直接编入 SceneBrief 的衔接锚。

### 5.4 Ending Seeds（部分推翻 ADR 0001）

作者写**结局之形**，不写结局正文：

```yaml
endings:
  seeds:
    - id: reconciliation
      frame: "真相揭示后的和解：纸狐狸的寄信人身份揭晓，社团去留的决定权交还给学生"
      tone: 温暖而怅然
      requires: "facts.paper_fox_sender.visibility == 'revealed'"
      must_address: [club_future, hiyori_wish]   # 必须兑现的 Dramatic Obligation
      epilogue_beats: [after_the_festival]
```

Dynamic Ending 语义保留——seed 的**选择**由 Committed History 决定（requires 条件 + CompletionJudge 证据），seed 的**演出**由历史生成（同一 seed 不同 playthrough 正文不同）——但作者重新控制结局的形、基调与必答事项。未满足任何 seed 的 requires 时，fallback seed（必须提供）兜底，替代现在"结局完全由 flash 模型即兴"的失控。

---

## 6. 子系统 C：场景级演出环（Scene Performance Loop）

### 6.1 单位重构

- **场景 = 生成/校验/修复单位**（一次模型调用，1-10 块）；**Segment = 提交/播放单位**（1-3 场景，至下一个 decision beat 或 ending）。
- 小单元直接改善失败经济：契约失败率随输出长度下降；重试丢弃的是一场不是一段；首批块更早流给玩家（选择后首个场景即可开始播放，替代 3-7 分钟白屏）。
- 场景间衔接是**同环连续生成**：第 n+1 场的 SceneBrief 衔接锚就是第 n 场的已过审结尾——时间倒流（"快打烊"→"午后阳光"）失去结构性土壤。Segment 内后场开始生成时可与前场的 judge 并行（提交仍在 segment 边界原子化，ADR 0007 的 fail-closed 语义不破坏）。

### 6.2 ScenePerformer 契约（替代 unified_segment）

一次调用输出：

```text
{
  blocks: [...],                    # 本场景正文
  ledger_updates: {...},            # 实体属性/新承诺/母题（见 4.3）
  fact_commits: [...],              # 沿用现有 FactCommitPlan
  relationship_deltas: [...],       # 沿用现有边界校验
  scene_summary: "...",             # 3-5 行，含关键台词摘录
  choice_set?: {...}                # 仅 decision 场
}
```

### 6.3 校验链与修复环（顺序前移确定性、后置语义）

```text
1. schema/引用校验（现有 validator，确定性）
2. 账本一致性（新，确定性：属性冲突、重复母题、承诺超期）
3. 确定性查重（新：接线 eval/detectors 的 shared_phrases，
   草稿块 vs 最近已提交块 + 母题黑名单，命中给出具体重复片段）
4. SemanticJudge（升级：上下文带逐字尾窗 + 实体卡；
   blocking 类别 +detail_contradiction / +must_include_missing / +repetition）
5. 修复环：拒绝原因定位到块 → 定向重写指令
   （"重写第 4 块：封皮已确立为黑色，见 S2-block14"），
   预算 2 次/场景；仍失败 → 丢弃本场景重生成 1 次；再失败 → fail-closed
```

- guard 的装饰性语义启发式（字面 fact_id 匹配）删除，guard 回归纯结构检查——防线诚实化：确定性的归确定性（1-3），语义的归 judge（4），且 judge 拿到的证据链补齐。
- 修复环作用于**块**而非整段，2-5 分钟的整段重摇成为历史。

### 6.4 开局缓存、事件存储、幂等、SSE、前端

全部不动。SceneBrief 序列与逐场景过审草稿作为 turn 诊断落盘（沿用现有诊断机制）。

---

## 7. 剧本包 v3（作者侧总结）

```text
pack.yaml v3
├── identity / experience / world / characters / facts（现有，基本不动）
├── structure: acts → beats（新；幕结构、决策密度、节奏画像）
├── endings.seeds + fallback seed（新；部分推翻 ADR 0001）
├── obligations / completion_requirements / conflict_axes（现有，与 beat effects 互锚）
└── assets（现有占位）
```

作者心智模型从"写世界观宪法 + 谜题机器"升级为"写宪法 + **故事骨架** + 谜题机器"：宪法管边界，骨架管叙事，谜题管真相。作者投入产出比的关键：**must_include 一句话就能锚定一场戏**，不必写整段正文；想写整段也可以（作为 must_include 的长文本）。

迁移：yokai_after_school v2 → v3 由编译器强制（beat 图静态校验：mandatory beat 可达性、successors 收敛性、decision beat 密度在画像内——故事图可达性检查，编译期完成，对应 IF 修复论文的"坏图/死链"校验）。

---

## 8. 质量闭环

1. **防线前移**：连续性问题中一切可确定性判定的（属性冲突、短语重复、母题重演、承诺超期）移入提交前校验，judge 只判真正需要语义理解的。
2. **自动试玩（PlayerAgent）**：策略化模拟玩家（贪心看戏型/速通型/全选项遍历型）自动跑包，接现有 eval harness；beat 覆盖率、结局 seed 可达性、平均决策间隔、每块失败率进报告。发布前 gate。
3. **连续性 CI**：每 pack 版本发布前跑 N 个种子 autoplay，全部通过 detectors + judge 复核才可 publish（对应"把矛盾当编译错误"的 story-skills 模式）。

---

## 9. 失败类别 → 机制映射

| 实测失败类 | 根因 | 新机制 | 层 |
|---|---|---|---|
| ⑤ 细节自相矛盾（封皮变色） | 细节无正典 | 实体登记簿 + 账本一致性拦截 | 确定性 |
| ④ 整段复读（三遍重演/同姿态） | 无结构记忆，judge 看不见原文 | 母题账 + 查重接线 + beat visit-once + judge 带尾窗 | 确定性+结构 |
| ② 场景跳变（时间倒流） | 段间无衔接锚 | SceneBrief 衔接锚 + 场景级连续生成 | 结构 |
| ③ 语气断裂（引号漂移） | 看不到自己排版 | 尾窗（已有）+ 小单元强化 | 已缓解 |
| ① 后果未兑现（承接率 0.33→0.89 后残余） | choice 意图一段后蒸发；planner outcome 丢失 | 承诺账带原文存活到兑现 + outcome 入衔接锚 + beat responds_to | 结构+账本 |
| 选项密集无进展 | 每段强制决策 | decision 由 beat kind 决定 | 作者层 |
| 慢（3-7 分钟）/失败放大 | 3 串行调用 + 整段重摇 | 砍 planner、场景级生成、块级修复、首批块提前播放 | 管线 |
| 结局失控/密度不足 | 结局纯即兴 | ending seeds + CompletionJudge 锚定 + 节奏画像 | 作者层 |
| 死状态机/stance_defended 不可达 | 事件未接线 | beat effects 确定性发事件 | 状态层 |

## 10. 与 ADR 对账

| ADR | 处置 |
|---|---|
| 0001 结局语义开放 | **部分推翻**：ending seeds 锚形，演出与选择仍动态。理由：实测结局密度/兑付不足（issue 10/11 连续修补即为证据），纯涌现结局在 flash 级模型上不可控 |
| 0002 版本钉死 / 0003 选择先提交 / 0004 不可逆 / 0011 因果轨迹 / 0013/0014 命令流 | 保留，原样 |
| 0005 有界真相涌现 | 保留；beat 的 facts effects 只 stage/commit 候选，不预设答案 |
| 0006 义务而非节拍 | **修订**：义务与节拍互锚——义务定义"必须处理"，beat 定义"何处处理"；纯义务在实测中不足以给 flash 模型提供结构 |
| 0007 独立判官 fail-closed | 保留语义；判官证据链补齐（尾窗+实体卡）；segment 内逐场判与后场生成并行不违反提交原子性 |
| 0008 完整可回放段 / 0009 Studio / 0010 延迟上界 / 0012 诊断最小化 / 0015 开局缓存 | 保留；0010 的 10s 目标在"首批块提前"下更接近 |

## 11. 迁移路径（增量，内核测试始终绿）

| 阶段 | 内容 | 交付判据 |
|---|---|---|
| P0 快赢（~天级） | judge 上下文补尾窗；查重器接线运行时；删除 guard 装饰层 | 现有 eval 基线重跑：④⑤ 类失败率下降 |
| P1 Canon Ledger | 新事件类型 + ScenePerformer schema 加 ledger_updates + Context Assembler v2 检索切片 | 封皮类矛盾在 harness 中为零 |
| P2 Scene Loop | 生成单位切场景、块级修复环、planner 并入导航、SSE 首批块提前 | 选择→首批块 < 90s；契约失败率下降 |
| P3 Beat Map + DramaManager | pack v3 structure 段、确定性导航、decision by beat、死事件接线、节奏画像 | stance_defended 可达；决策间隔可配置 |
| P4 Ending Seeds + pack v3 编译校验 | seed 选择/演出/fallback；beat 图可达性静态检查 | yokai v3 包全 seed 可达 |
| P5 质量闭环 | PlayerAgent autoplay + 发布 gate + 连续性 CI | 发布流程文档化 |

P0-P1 不改 pack 格式；P3 起包格式升 v3，v2 包由编译器拒绝（与 v1→v2 同节奏）。

## 12. 开放问题

1. **beat 密度光谱**：稀疏骨架（每幕 3-5 beat，自由度大）vs 密集骨架（接近传统剧本）。建议先稀疏（yokai 用 ~15 beat 验证），把密度留给作者按作品调。
2. **检索增强**：实体卡/场景档案的检索先用确定性规则（beat 声明 + 尾窗提及 + 时近）；embedding 检索何时引入（长包、多线）待 P1 数据。
3. **候选+选优**（Re3 reranker 模式）：每场景生成 2 候选由 judge 选优，质量换延迟，做成 pack 级开关，默认关。
4. **judge 并行化的 ADR 0007 冲突**：本提案用"提交仍在 segment 边界"回避；若 P2 后延迟仍不达标，再议判官事后化（作者已标注为架构级决策）。
5. **自由文本输入**：storylet 架构天然兼容自由文本（Drama Llama 模式），但当前产品决策是仅选项；本提案不改变，仅指出架构上留了门。
