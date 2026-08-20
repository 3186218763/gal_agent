# HANDOFF — 剧情引擎重构交接（2026-08-20）

> 给下一个接手 agent 的完整交接。当前状态：**P0–P4 已全部落地并验证**（625 passed / 6 skipped / ruff clean），
> P5 与文档收尾待做。本文件与代码同 commit 提交。

## 1. 一句话架构（已实现）

**作者写节拍骨架，确定性引擎导航，LLM 只做场景级演出，正典账本管记忆。**

完整设计见 `docs/2026-08-20-story-engine-architecture.md`（该文档头部仍是"提案，待评审"——
收尾任务之一就是把状态表更新为已落地，见 §5）。事件溯源内核（StoryEvent 39 类 + 纯 reducer +
原子命令提交）未推翻，三个新子系统叠在其上。

## 2. 已落地内容（P0–P4）

| 波次 | 内容 | 主要文件 |
|---|---|---|
| P0 | judge 逐字尾窗、运行时查重门（repetition gate 接线 eval 判重）、guard 装饰层删除 | `runtime/repetition.py`、`semantic_judge.py`、`guard.py` |
| P1 | 正典账本 Canon Ledger：LedgerUpdate 事件/reducer/validator/上下文注入/judge 可见 | `state/events.py`、`test_canon_ledger.py` |
| P3 | Beat Map v2 增量 schema（acts/beats/ending_seeds）、编译期全引用校验、`BeatCompleted` 事件、模拟器死事件接线（commit_latent/promises/stance/arc 全部真正发出）、DramaManager 确定性导航 | `script_pack/models.py`、`compiler.py`、`runtime/drama_manager.py` |
| P2 | 场景演出环：`ScenePerformerPort`（场景=生成/修复单元，段落=提交单元）、逐字接缝锚（seam_tail）、块级修复预算 2 后才整段重生成、authored choice 确定性解析（beat 路径不再调 planner 模型） | `runtime/scene_performer.py`、`turn_orchestrator.py` |
| P4 | yokai 包 v3：`structure.yaml`（4 幕 14 节拍）+ `ending_seeds.yaml`（3 结局种子）经 includes 挂入 pack；编译期 beat 图校验（responds_to 必须前向引用、有 seeds 必须有 ending beat）；`optional` 节拍语义（被门控的条件节拍永不阻塞幕推进）；结局块密度下限写进 scene brief；三线全程回归测试 | `script_packs/yokai_after_school/*`、`tests/test_yokai_beat_walkthrough.py` |

yokai 三条路线的确定性走查（`tests/test_yokai_beat_walkthrough.py`，走完整 orchestrator）：
- 日和线 → `multilingual_campus_guide` → 结局《普通的下一周》
- 千夏线 → `after_school_radio_special` → 结局《心跳的放学后直播》
- 澪线 → `shrine_and_campus_open_day` → 结局《纸签归处》（真结局）

每条线 12 个节拍全部按幕序完成、`club_future`/`paper_fox_sender` 提交并在终幕揭示、零即兴回退段。

## 3. 新 agent 必须知道的引擎语义

（这些是设计意图，不是显而易见的代码行为；改动前先理解）

1. **导航是幕封闭的**：当前幕 = 第一个还有"未完成的非 optional 节拍"的幕。幕内按 `(-priority, beat_index)`
   排序，scene 节拍累积成 continue 场景，第一个合格的 decision/ending 节拍终止段落。
   `plan_next_segment` 返回 None → 即兴回退（安全网，不是主线）。
2. **每个幕必须有 decision 或 ending 节拍**，否则该幕永远产不出计划（编译期不查，是设计约定）。
   条件场景节拍（如按好感度门控的支线）必须标 `optional: true`，否则会永久堵住后续幕。
3. **responds_to 只能引用更早的 (act_index, beat_index)**，编译期强制；同段内已演出的节拍算已回应。
4. **commit_latent 是作者对 latent question 的确定性作答**：value 必须是声明的候选值；提交自带
   1 个证据锚，因此同节拍 reveal 需要 `evidence_required <= 1`，ending 节拍有终幕豁免。
   已 COMMITTED 的事实再次 commit_latent 会被静默跳过（不重复提交）。
5. **ending 节拍触发时选种子**：`_select_seed` 取 requires 达标的最高优先级种子，fallback 永远垫底。
   种子的 requires 在终幕时刻求值（关系值/事实状态）。
6. **authored choice 零模型解析**：`authored_choice_resolution(pack, pending)` 直接从
   AuthoredChoiceSource 构造 ActionResolution（含 relationship_deltas/outcome），
   pending 的 (option_id, action_id) 对不上任何节拍选项时返回 None 走即兴路径。
7. **结局的 blocks 就是终幕场景的 blocks**（`_perform_beat_scenes` 里 EndingDraft.blocks =
   scene_drafts[-1].blocks），受 `ENDING_BLOCK_FLOOR = 10` 密度下限约束；该下限已写进
   scene brief（`ending_block_floor` 字段），`FakeScenePerformer` 同步垫块。
8. **yokai act3 的提交门限是与 act2 辩论增量的精确耦合**（hiyori ≥45 / chika ≥40 / mio ≥35）：
   重新调阈值会改变三线可达性，walkthrough 测试会钉住它。

## 4. 常用命令

```bash
cd backend
uv run pytest tests/ -q            # 625 passed, 6 skipped（离线，fakes）
uv run ruff check src tests        # clean
# 编译一个剧本包：
uv run python -c "from src.story.script_pack.compiler import compile_script_pack; print(compile_script_pack('script_packs/yokai_after_school').beat_ids)"
```

## 5. 剩余工作（Plan）

### P5：PlayerAgent 自动试玩 + 发布 gate（下一步，优先级最高）

1. **自动试玩 harness**：参考 `tests/test_yokai_beat_walkthrough.py` 的 `_walk` 模式（orchestrator +
   FakeScenePerformer 循环 execute_turn 直到 ending），做成可复用工具：
   - `backend/tools/autoplay.py`（或 tests 里参数化扩展）：给定 pack + 路线偏好/随机种子，
     跑完整局，产出 transcript（已有 `test_playthrough_transcript.py` 的落盘格式）+ 正典断言
     （节拍序、事实提交/揭示、结局标题）。
   - 接真 LLM 的 live 冒烟（`tests/live/` 已有骨架）：至少 1 局真模型全流程，验证
     ScenePerformer 的真实指令遵循（接缝、must_include 落地、结局密度）。
2. **发布 gate**：一个 `make release-check`（或脚本）串起：全剧本包编译 → pytest → ruff →（可选）
   live 冒烟；作为合入 main 的门槛。现在是三条命令手动跑，容易漏。

### P6：文档收尾

1. **设计文档状态表**：`docs/2026-08-20-story-engine-architecture.md` 把"P0–P5"各子系统从提案改为
   已落地，标注对应测试文件（证据链）。
2. **新 ADR**：
   - 取代 ADR 0006（obligations-not-fixed-beats）：节拍骨架现在是一等作者表达层；义务仍存在但
     降级为节拍 effects 的一部分。写清楚为什么反转（见设计文档 §1.3 作者表达力错位）。
   - 新增：Scene Performance Loop（场景=生成单元/段落=提交单元，块级修复经济学）。
3. **README**：新增架构流程图（作者 structure.yaml → DramaManager 导航 → ScenePerformer 演出 →
   事件溯源提交），以及"如何给一个 pack 写节拍/结局种子"的作者指南（参照 yokai 包）。

### 小项

- `.scratch/` 已加入 .gitignore（本次 commit 顺带）。若 `.scratch/script-quality` 的评测数据仍有用，
  考虑挪到 `docs/eval/` 归档；无用则删。
- `ending_seeds.yaml` 的 `must_address` 只校验 id 存在，尚未接入终幕 brief 的必答清单（运行时
  `seed_must_address` 已有函数，检查是否已在 brief 里生效；若未生效，是 P5 顺手项）。

## 6. 建议技能（下个 agent）

- `implement`：P5 的 autoplay harness 与 release gate 直接用它。
- `tdd`：autoplay 断言先写（正典不变量清单），再写 harness。
- `domain-modeling`：写取代 ADR 0006 的新 ADR 时用。
- `code-review`：本 commit 之后跑一次，检查 P0–P4 的 spec 偏差。

## 7. Git 状态说明

本 commit 包含自 `5274404` 之后的全部未提交工作：P0–P4 代码、测试、yokai v3 剧本包、
设计文档（`docs/2026-08-20-story-engine-architecture.md`）与本交接文件。分支 `main`，推送至 origin。
