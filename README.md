# Galgame AI - 动态视觉小说

基于 **Game Kernel + 设定包（Setting Pack）** 的 AI 驱动 Gal 引擎。作者只写世界/角色/目标/结局，不写线性 `plot.md` 分镜；运行时由 Kernel 调度 Director / Character / Choice / Memory Agents，规则模块控制阶段、张力、选项密度与结局结算。

## 特性

- 🧠 **动态 Kernel**：确定性编排 + Agent 生成场面/对白/选项
- 📦 **设定包驱动**：`setting_pack.yaml`（世界、角色、多目标、多结局），不是 beat 剧本
- 🎯 **多目标 + 多结局**：状态条件触发；`max_steps` 兜底 `fallback` 结局
- 📈 **Phase / Tension**：隐式起承转合与选项密度
- 🔒 **无自由输入、无回溯**：仅已验证选项前进
- 🧪 **Stub 模式默认可玩**：无需 API Key 即可本地跑通
- 🌐 **前后端分离**：FastAPI + WebSocket + React

## 架构

```
Game Kernel (确定性 orchestrator)
├── World State              # 当前快照（phase / tension / flags / goals…）
├── Event Database           # 只追加历史
├── Rule Modules
│   ├── Phase & Tension
│   ├── Option Trigger
│   ├── Goal / Ending Evaluator
│   └── Option Validator
└── Agents (OpenAI Agents SDK 或 Stubs)
    ├── Director   → 场面意图 SceneIntent
    ├── Character  → 对白
    ├── Choice     → 选项 + narrative preview
    └── Memory     → 规则召回（V1）
```

设计规格与实现计划：

- [动态 Gal Agent 设计规格](docs/superpowers/specs/2026-08-10-dynamic-gal-agent-design.md)
- [实现计划（dynamic gal kernel）](docs/superpowers/plans/2026-08-10-dynamic-gal-kernel.md)

## 快速开始

### 前置要求

- Python 3.9+
- Node.js 18+
- （可选）OpenAI API Key — 仅在真实 Agent 模式需要

### 1. 克隆 / 进入项目

```bash
cd gal_agent
```

### 2. 启动后端（默认 Stub，无需 API Key）

```bash
cd backend
pip install -r requirements.txt
# 可选：cp .env.example .env

# 默认 GAL_USE_STUBS=1：使用规则/Stub Agents，不调用 OpenAI
python -m src.main
```

后端：`http://localhost:8000`（API 文档：`/docs`）

#### 真实 Agent 模式

```bash
export GAL_USE_STUBS=0
export OPENAI_API_KEY=sk-...
python -m src.main
```

- `GAL_USE_STUBS=1`（**默认**）：Stub 导演/角色/选项，本地完整可玩到结局
- `GAL_USE_STUBS=0` + `OPENAI_API_KEY`：使用 SDK Agents 生成内容
- 若 `GAL_USE_STUBS=0` 但未设置 API Key，后端仍会回退到 Stub

### 3. 启动前端

```bash
cd frontend
npm install
npm run dev
```

前端：`http://localhost:5173`

### 4. 开始游戏

浏览器打开 `http://localhost:5173`，点击「开始游戏」。默认加载设定包 `chapter_01`。

## 设定包（Setting Pack）

路径约定：

```text
backend/scripts/<pack_id>/setting_pack.yaml
```

示例：[`backend/scripts/chapter_01/setting_pack.yaml`](backend/scripts/chapter_01/setting_pack.yaml)

包内定义：

| 区块 | 含义 |
|------|------|
| `world` | 地点与标签 |
| `characters` | 公开/私密信息、初始关系 |
| `goals` | 多主线目标（可冲突） |
| `endings` | 多结局 + `condition` + `type`（含 `fallback`） |
| `max_steps` | 步数上限，超时强制兜底结局 |
| `opening_seed` | 可选开场旁白 |

> V1 不再以 `plot.md` beats 驱动主循环；旧 beat 剧本可保留作参考，API 已接到 Kernel。

## 项目结构

```
gal_agent/
├── backend/
│   ├── src/
│   │   ├── agents/          # Director / Character / Choice / Memory
│   │   ├── content/         # setting_pack_loader
│   │   ├── domain/          # WorldState, SettingPack, enums
│   │   ├── kernel/          # GameKernel + stubs
│   │   ├── rules/           # phase/tension, validator, endings…
│   │   ├── store/           # world / event 持久化
│   │   └── main.py          # FastAPI + WebSocket
│   ├── scripts/
│   │   └── <pack_id>/
│   │       └── setting_pack.yaml
│   ├── data/                # 会话存档
│   └── tests/
├── frontend/
│   └── src/
│       ├── components/Game.tsx   # 选项含 option.preview
│       ├── api.ts                # pack_id 创建会话
│       └── types.ts
├── docs/superpowers/
│   ├── specs/2026-08-10-dynamic-gal-agent-design.md
│   └── plans/2026-08-10-dynamic-gal-kernel.md
└── README.md
```

## 核心概念

### Kernel 循环

1. Memory 召回 → Director 场面意图  
2. Character 生成对白 / 旁白  
3. 规则更新 tension / phase  
4. Option Trigger 决定是否出选项 → Choice 生成 + Validator  
5. 玩家选择 → 应用 predicted consequences → Ending Evaluator  

### 选项与 Preview

- 玩家只能点选后端下发的选项  
- 每个选项可带 **narrative preview**（软提示，不暴露数值）  
- 前端在选项文案下方渲染 `option.preview`

### 结局

```yaml
endings:
  - id: alice_route
    condition: "goals.ally_alice.completed"
    type: victory
    priority: 100
  - id: timeout_fallback
    condition: "steps >= max_steps"
    type: fallback
    priority: 1
```

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/sessions` | 创建会话；body 优先 `pack_id`（兼容 `chapter_id`） |
| `GET` | `/api/sessions/{id}` | `pack_id`, `steps`, `tension`, `phase` |
| `DELETE` | `/api/sessions/{id}` | 删除会话 |
| `WS` | `/ws/game/{id}` | 游戏消息流 |

WebSocket 消息类型：`game_start`（`chapter` = 包标题）、`narration`、`dialogue`、`options`、`state_update`（可含 `phase`/`tension`）、`ending`（`ending_type` 含 `fallback`）、`error`。

完整 OpenAPI：`http://localhost:8000/docs`

## 开发

### 测试（后端，Stub，无需 API Key）

```bash
cd backend
# 示例
uv run --with pytest --with pytest-asyncio --with pydantic --with pyyaml \
  pytest tests/ -v
```

### 调试

- 后端日志：终端  
- 前端：浏览器控制台（含 WebSocket 消息）  
- 环境变量：`GAL_USE_STUBS`、`OPENAI_API_KEY`、`HOST`、`PORT`

## 贡献

欢迎 Issue 与 PR。

## License

MIT

## 致谢

- OpenAI Agents SDK  
- FastAPI  
- React + Vite  
