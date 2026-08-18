# 剧本系统四问题:共识与证据(2026-08-15)

> 临时文档:grill 共识 + story-v2.db 挖掘证据 + 外部调研结论(待回填)。
> 决定:**暂不写 ADR**(作者要求,待 SillyTavern/LingChat 调研回填后再定固化形式)。
> 本文件是后续 `/to-spec` 的输入。

## 一、四个问题 → 根因 → 已定决策 → 修改点

| # | 问题 | 根因(代码实锤) | 决策 | 修改点 |
|---|------|----------------|------|--------|
| 1 | 选项太密集 | `SegmentPlan.terminal` 只有 decision/ending,每段**必须**以 2-4 选项收尾(`segment_contracts.py:54`、`unified_segment.py:191-194`);`target_block_range`/`quiet_scene_allowance` 只进 prompt,validator 对块数只查非文(`validator.py:337`) | **主**:prompt 加"戏剧理由"指令——无新剧情进展/未消化上一选择前不得出选项;**兜底**:validator 强制块数下界。`terminal="continue"` 不做(留作将来合同演进,单独 ADR) | `unified_segment.py`、`validator.py`、`pacing.py` |
| 2 | 选后衔接差 | 已承诺的 Choice Meaning 从不进写手 prompt(只进 planner `planner.py:49-53` 和 judge `semantic_judge.py:122-124`;orchestrator 手里有但只喂 judge `turn_orchestrator.py:566`);后果 obligations/stances 无键进 context | `pending_choice` 全量注入写手 context | `unified_segment.py`、`turn_orchestrator.py` |
| 3 | 选卡延迟 | 每回合 3 次串行调用(`turn_orchestrator.py:501-507,909-915,612-622`)+ `timeout=0` 无上限 + 否决重生成翻倍;写手/judge 共用单 client(`api.py:87-107`,现 gemini-3.7-flash) | 目标 **<90s**;timeout 恢复 **180s** 有界;模型已换 | `.env`、`config.py`;实测靠 #4 落盘的阶段耗时 |
| 4 | 前后文控制差 | 写手**零历史感知**:`recent_scene_summaries` 空壳占位(`segment_context.py:117-127`),全仓无窗口/截断/摘要机制;prompt 恒定 4-8k tokens 与进度无关;CONTEXT.md 的 Attributed Assertion 代码中不存在 | **三层上下文、零额外调用**:写手自产每场一行摘要(schema 加字段,随 Committed Segment 提交复用)+ 确定性事件 digest(从 event store 免费算)+ 最近 ~15 块原文尾部窗口(保文风与衔接点) | `segment_context.py`、`segment_contracts.py` |

## 二、证据(story-v2.db,2 会话 / 69 blocks / 9 处失败)

五类失败全部实锤:

- **① 选项后果未兑现 ×2**(承接率 1/3):选"我和你一起找找看吧"→ 下段艾丽丝退回初始求助姿态"我真的不是故意弄丢的";选"转向店长美奈试探"→ 美奈公式化应答,鲍勃把玩家当路人重置。
- **② 场景跳变 ×1**:上段结尾"天色渐暗,快到六点打烊"→ 下段开头"午后的阳光洒在桌面"(时间倒流无过渡)。
- **③ 语气断裂 ×2**:引号格式漂移——无引号 → 全角引号 →「」→ 退回无引号,三段两变(写手看不到自己上一段的排版)。
- **④ 整段复读 ×2**:S2 第三段把"艾丽丝描述丢失+鲍勃警告+美奈打烊提醒"整套结构第三次重演;S1 美奈重复广播打烊时间、鲍勃两次"推了推眼镜"同姿态出场。
- **⑤ 细节自相矛盾 ×2**:笔记本封皮黑→深蓝硬皮;最后目击地点三段三说(手边→背包侧袋→提包里)。

### 密度精化(修正最初判断)

实测 **21–27 blocks/次选择**,落在 `target_block_range` (8,25) 区间内。"太密集"的真实来源不是字数不够,而是**选项出现时没有新的戏剧进展**:S2 第三段整体复读前文,结尾照常出选项——玩家感受是"又选了一次,剧情没动"。故问题 1 的修法重心在 prompt 戏剧理由指令,块数下界只是兜底。

### 种子语料(评测集)

- S2 seq18→28:一条连续样本同时命中①②④⑤ → 回归测试锚点
- S1 seq6→18 封皮变色 → ⑤ 最小复现对

### 数据卫生备忘

- receipts 的 `choices` 字段恒为空(选项只在 `decision_presented` 事件)→ 剧本导出需联表
- scene ID 命名漂移(`scene_*` → `sc_cafe_tension`)
- 两个会话各有一个 decision 呈现后未选即终局

## 三、横切决策

- **优先级**:质量 > 延迟 > 成本(Engine Work 阶段为验收付钱)
- **评测集**:建;种子 = 现有 2 会话;改 prompt 前后跑对比
- **诊断落盘**:每 turn 阶段耗时 + judge blocking findings 进 event store(作者/开发侧,不碰玩家 API;原计划 ADR 0012 修订,暂缓)
- **完整剧本保存**:每段提交增量追加 `data/playthroughs/<session_id>.md` + CLI 按需导出;DB 仍是唯一真相源,文件是派生物
- **先预防后检测**:judge 本期**不加**新检查项,等预防侧上线 + 评测一轮,只对顽固类别补

## 四、验证指标

1. 选择承接率(现状 **1/3**)
2. 复读率(整段结构重演,现状 S2 第三段)
3. 每次选择前有新剧情进展(评测集判定)
4. 选项回合端到端 <90s(阶段耗时数据佐证)

(块数密度降为兜底指标)

## 五、非目标

`terminal="continue"` 合同演进、judge 连续性检查项、Attributed Assertion 实现(词条保留,记后续方向)、任何前端改动。

## 六、留给 spec 的细节

块数下界数值(评测集数据调)、原文窗口大小(~15 块起调)、planner 是否共享新历史 context(倾向是,按延迟预算定)、摘要字段 schema 位置。

## 七、外部调研(待回填)

对象:SillyTavern(酒馆)、LingChat(SlimeBoyOwO/LingChat)。
要回答:① 他们怎么做剧本/剧情组织;② 怎么让 LLM 稳定产出好剧本;③ 选项与正文怎么关联;④ 整体连贯靠什么机制;⑤ 架构(每次交互几次调用、流式、状态存哪)。
每个机制标注:对应我们四个问题中的哪个、可借鉴/不适用及原因。

- [x] **LingChat**(2026-08-15,克隆于 /tmp/script-research/LingChat)

  **定位差异(重要)**:它是 Tauri 桌面 AI 陪伴助手,"剧本模式"=作者预写的章节 YAML 事件序列,LLM 只在节点上即兴填台词。结构先行、LLM 即兴——与我们的"LLM 生成结构+文本"互补,不可整体照搬,但机制层面多处可借鉴。

  - **L1 选项是稀缺节点**(支持问题1决策):选项由作者/结构显式放置,自带 `condition`+`lock_hint` 条件禁用语义(`script_engine/events/choice_event.rs:59-82`)——选项是状态检查点而非进度推进器。反证我们"每段必出选项"是反模式。条件语义记远期候选。
  - **L2 ai_judged 章节收尾**:章节结束三态 linear/branching/ai_judged;LLM 判官只从命名章节中选一个+兜底取第一项(`chapter_end_event.rs:71-123,144-218`)。与我们 Semantic Judge 位置相近,容错模式(只回标识符+确定性兜底)可参考。
  - **L3 记忆双层**(支持问题4决策):SQLite 台词表唯一真相源,每轮全量重建记忆 O(n) 换"永不漂移"(`docs/function_call/memory.md:131`);MemoryBank 四段摘要(短期承接/编年史/用户画像/承诺)在后台 `tokio::spawn` 四路并行压缩,全部成功才推指针,失败冷却 60s(`persistent_memory_system.rs:302-335`)。指针式裁剪 `recent_window=30` + 该区间不可见则直接移指针防无限触发(`:243-250`)。
  - **L4 promises 契约段**(直接治①类"答应的事被悬置"):MemoryBank 有专门的"待办契约"段,携带"新增约定/状态核销"指令(`persistent_memory_system.rs:64-73`)。**spec 候选:我们的 digest 加"未兑现承诺清单",选择产生的承诺在剧情兑现后显式核销**——正面治时间倒流与悬置。
  - **L5 延迟哲学**(后备方案,不动本期):一切非必要调用挪出关键路径——摘要后台化、四路并行、选角单次非流式。"不支持流式工具的 Provider 宁可跳过工具闭环也不做非流式预检——避免可感知的延迟抖动"(`docs/function_call/architecture.md` §10.4)。**若 <90s 目标不达:judge 事后化/planner 与写手并发是后备选项。注意与 ADR 0007 fail-closed 语义冲突,属架构级决策,届时单独过。**
  - **L6 情绪=逐句标签**:system prompt 强制每句`【情绪】`开头,本地 ONNX 分类器校验 19 个合法标签,历史情绪原样回填不重算(`message_system/processor.rs:115-178`)。额外收获:Performance Cue 的合法性校验可参考"生成后本地校验+历史不重算"模式。
  - **L7 它与我们②同病**:玩家选择的 intent/stance 不进任何写手 prompt,靠 vars→条件分支的纯状态桥硬关联(`chapter_end_event.rs:77-104`);且自由对话的 Plot 提示有"回复后应清除"的 TODO 未清(`free_dialogue_event.rs:210`)。反证问题2决策正确;**教训:pending_choice 注入要有生命周期——只对紧邻的下一段可见,防止陈旧指令泄漏**(与我们 8/14 实锤的 choice_reversal 同源风险)。

- [x] **SillyTavern**(2026-08-15,克隆于 /tmp/script-research/SillyTavern)

  **定位差异(重要)**:本地优先的角色扮演聊天前端(Node `src/` + 浏览器 `public/`),全部智能在"prompt 拼装与上下文管理"管线;本体**无叙事规划、无选项生成、无一致性校验**——质量兜底全靠用户手动 swipe/重roll。可借鉴的是它的上下文工程,不是它的叙事。

  - **S1 世界书三层防矛盾**(正对⑤类"同一物品两版描述"):`constant` 条目常驻无条件、排序最前(`world-info.js:2178-2181,3213`);**inclusion group 同组互斥只留最高分**(`filterGroupsByScoring :5209-5240`);sticky/cooldown 时序状态机(`:518-531`)。配套:扫描最近 N 条(默认 depth 2)+ 激活内容递归回扫 + 每条概率掷骰 + token 预算(上下文 25%+绝对 cap,逐条累加超即停 `:4624-4631,4919-4935`)。
  - **S2 分层拼装与可调试性**:prompt 层 = system→人设→世界书→示例对话→聊天记录→事后注(post-history instructions,压在聊天之后 `openai.js:1232,1497-1503`);作者注按 depth 插入(`authors-note.js:347-363`);全部层 marker 化、可拖拽排序(`PromptManager.js`)——**prompt 组成可调试、可实验,值得抄**。
  - **S3 裁剪策略**:不是 keep-N,而是**从最新向最旧累加直到 token 预算耗尽**(`script.js:4816-4864`);各层有独立预算配额,防某层吃光上下文。
  - **S4 Summarize/Vector 双扩展**:滚动摘要**条件触发**(距上次 ≥10 条消息或词数阈值,且等主生成结束再跑,`extensions/memory/index.js:566-618`),以旧摘要为底扩展新事实,限词 200,注入 depth 2;Vector Storage 消息分块→嵌入→top-K 检索注入(`extensions/vectors/index.js:819-854`)。**条件触发+滚动增量=非每回合必跑**。
  - **S5 选项机制**:无内建分支树——STscript 约 300 命令(`/if` `/while` `/setvar` `/gen`)可自拼分支(变量+条件+世界书激活);Quick Replies 是静态预定义按钮(可 auto-execute);swipes=每消息候选数组+滑动换版。启示:选项可以部分不由 LLM 生成(与 L1 condition/lock_hint 呼应,远期候选)。
  - **S6 架构**:每条回复 **1 次**主调用(+摘要/表情分类等 quiet 隐藏调用);SSE 流式;jsonl 存储+定期备份;**无 judge、无自动重试**。→ 我们的 Semantic Judge 是 ST 完全没有的差异化能力,不是负债。
  - **S7 对②的启示**:ST 里玩家原话天然就是聊天记录最后一条,模型自然续写——**玩家的选择必须落到 prompt 的文本层**(如"玩家刚选择了 X,已承诺 Y"的意图确认句),不能只塞结构化参数。

### 调研合并结论(2026-08-15)

**对已定决策:零推翻,五项增强**(S/L 编号指上):

1. `pending_choice` 注入**落到文本层**(意图确认句)+ **生命周期=仅紧邻下一段可见**(S7 + L7,防陈旧指令泄漏致 choice_reversal)
2. digest/facts 层借世界书模式:**硬事实常驻(constant)+ 同主题互斥组**(S1,防⑤类两版描述同时在场)
3. **token 逐层预算分配**:各层配额+cap,原文窗口从最新向最旧填充至预算耗尽(S3,防历史层吃光上下文)
4. digest 加**未兑现承诺清单+显式核销**(L4,治①类"答应的事被悬置"与时间倒流)
5. 远期候选(本期不做):向量检索补充 digest(S4)、选项条件语义/部分模板生成(L1+S5)、judge 事后化(L5,与 ADR 0007 冲突需单独决策)
