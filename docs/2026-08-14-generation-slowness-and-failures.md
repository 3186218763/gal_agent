# 排查记录:开局后等待过久、选项回合"生成失败"(2026-08-14)

## 现象

- 开局明明已"初始化成功"(预热缓存已建),新会话开局仍要等约 30–60 秒。
- 选择选项后等待数分钟,前端提示「AI 模型生成失败,请重试」(`generation_unavailable`)。
- 后端日志只有一行 `turn stream failed: planner failed to resolve the consequence`,无堆栈、无细节,无法定位。

## 排查过程

1. **Provider 探测**:直接调 OpenCode Zen Responses 接口——最小请求 4.3s、
   1400 token 长输出 17s、strict json_schema 结构化输出 2.6s。Provider 正常,吞吐约 80 token/s。
2. **超时递增实验**:`GAL_LLM_TIMEOUT_SECONDS` 45s → 120s → 300s → 不限时。
   完整段落生成(unified agent 一次性产出 plan+draft)实际需要 2–5 分钟,旧 45s/120s 超时必然 `APITimeoutError`。
3. **补日志**:`api.py` 原来只记 `warning("%s", exc)` 不带堆栈,补 `exc_info=exc` 后复现拿到完整异常链。
4. **复现结果**:planner 与段落生成都成功,倒在了最后的语义校验(semantic judge);用户那次则倒在 planner 阶段。

## 根因(三层叠加)

1. **开局缓存没有语义预审**。预热(`ensure_opening_cache`)只跑确定性校验 + guard,
   不跑 semantic judge;运行时每个新会话的开局都重新现场调一次 judge(30–60s 模型调用)。
2. **每个选项回合 = 三次串行模型调用**(planner → unified 段落生成 → semantic judge),
   该 provider 单次调用 1–2.5 分钟,合计 3–7 分钟。选项后果不走缓存(设计上刻意不消费 choice pregen)。
3. **写手模型质量不足 + fail-closed 放大**。deepseek-v4-flash 两次生成都被 judge 抓到
   真实违规,fail-closed 设计正确地拒绝提交,玩家看到"生成失败":
   - 第 1 次 `choice_reversal`:玩家已承诺"答应艾丽丝一起找笔记本",段落结尾却让艾丽丝
     再次征集同伴、把同一承诺重新列为选项——无视并撤回了已承诺的选择。
   - 第 2 次 `boundary_violation`:鲍勃直接透露"笔记本在艾丽丝包里",而
     `facts.notebook_holder` 在世界状态中尚未揭示,属于知识泄漏。
   此外旧代码丢弃否决理由(judge findings / guard violations),日志无法诊断。

实测阶段耗时:planning 108s → generating 123s → validating 77s → regenerating 135s → validating ~80s。

## 修复(已合入)

| # | 修复 | 位置 |
|---|------|------|
| 1 | 不限时:`GAL_LLM_TIMEOUT_SECONDS=0`(或 `none`)→ `AsyncOpenAI(timeout=None)`;正数仍可设上限(≤3600) | `config.py` |
| 2 | 回合进度流:`progress` SSE 事件(阶段 `planning/generating/validating/committing` + `elapsed_ms`),模型调用期间每 15s 一次 `heartbeat`;`execute_turn` 改为 worker+queue 以便长调用期间实时吐事件 | `turn_orchestrator.py` |
| 3 | 前端进度条:阶段步骤条(✓/当前)+ 流动进度条 + mm:ss 计时,替换静态"正在生成…" | `Playback.tsx` / `Playback.css` / `stream.ts` |
| 4 | 失败可诊断:`api.py` 失败日志带完整堆栈;orchestrator 记录 guard violations / judge blocking findings 明细 | `api.py` / `turn_orchestrator.py` |
| 5 | 开局缓存构建时跑一次 judge,通过则写 `judge_preapproved=True`,运行时跳过 judge;**未打标的缓存(如离线 `init-pack` 产物)运行时照常校验**,离线工具无法绕过验收门 | `cli.py` / `pack_cache.py` / `turn_orchestrator.py` |
| 6 | 否决后自动重生成一次:把 blocking 理由回传给写手(`fix_these_rejection_reasons`),进度条显示 `regenerating`;比玩家手动重试便宜(不重复 planner);二次否决才 fail-closed,期间零提交 | `turn_orchestrator.py` / `unified_segment.py` |

## 真机验证

- 开局(旧格式缓存,运行时校验):progress 流 + 心跳正常,judge 26–60s,成功,选项正常呈现。
- 选项回合(恢复 pending consequence):`planning → generating → validating → regenerating → validating`,
  重试链路按设计运转;两次否决理由均已记录(见上),均属写手真实违规,裁判判定正确。

## 结论与后续选项

链路已全部按设计工作。剩余瓶颈是 **deepseek-v4-flash 的生成质量**(违反世界真相规则的概率偏高)
与 **该 provider 的单调用延迟**(1–2.5 分钟)。可选方向:

1. 换更强的生成模型(改 `.env` 的 `GAL_LLM_MODEL`;Zen 平台可用模型列表待查)。
2. 写手与裁判分层:生成用 flash 级、judge 用更强模型(或反过来),平衡延迟与质量。
3. 增加重生成次数 / 对 planner 也做理由回传重试(当前仅段落生成有重试)。
4. 评估 milestone 计划中的 trace store,用数据统计否决率随模型/回合的分布。

## 环境备注

- WSL2 下 shell 有 `http_proxy`,curl 访问 `127.0.0.1` 会被代理拦成 502,本地调试需 `--noproxy '*'`。
- `backend/debug_live_pipeline.py` 已过时(`PacingEnvelope` 缺 `target_block_range`),待修或删。
