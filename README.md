# Galgame AI - 动态视觉小说

基于 OpenAI Agent SDK 的 AI 驱动 Galgame 引擎。

## 特性

- 🤖 **AI 角色系统**：每个角色由独立的 Agent 扮演，拥有独特的性格和记忆
- 📖 **动态剧情**：根据玩家选择实时生成剧情内容
- 🎯 **智能选项触发**：AI 导演判断何时给予玩家选择机会
- 💾 **状态持久化**：游戏进度和角色关系自动保存
- 🎭 **多结局系统**：基于玩家行为触发不同结局
- 🌐 **前后端分离**：Python 后端 + React 前端

## 架构

```
┌─────────────────────────────────────────────────────────┐
│                      前端 (React)                        │
│                   WebSocket 连接                         │
└─────────────────────┬───────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────┐
│                 后端 (FastAPI)                           │
│  ┌─────────────────────────────────────────────────┐   │
│  │           游戏主循环 (Game Loop)                 │   │
│  │                                                   │   │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐      │   │
│  │  │ Director │  │Character │  │Character │      │   │
│  │  │  Agent   │  │  Agent   │  │  Agent   │      │   │
│  │  │ (导演)   │  │ (艾丽丝) │  │  (鲍勃)  │      │   │
│  │  └──────────┘  └──────────┘  └──────────┘      │   │
│  │                                                   │   │
│  │  ┌─────────────────────────────────────────┐   │   │
│  │  │      选项触发算法 & 验证系统             │   │   │
│  │  └─────────────────────────────────────────┘   │   │
│  └─────────────────────────────────────────────────┘   │
│                                                          │
│  ┌──────────────┐  ┌────────────┐  ┌────────────┐     │
│  │ 状态管理器    │  │ 剧本解析器  │  │结局评估器   │     │
│  └──────────────┘  └────────────┘  └────────────┘     │
└──────────────────────────────────────────────────────────┘
```

## 快速开始

### 前置要求

- Python 3.9+
- Node.js 18+
- OpenAI API Key

### 1. 克隆项目

```bash
cd gal_agent
```

### 2. 启动后端

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env 填入你的 OPENAI_API_KEY
python -m src.main
```

后端将在 `http://localhost:8000` 启动。

### 3. 启动前端

```bash
cd frontend
npm install
npm run dev
```

前端将在 `http://localhost:5173` 启动。

### 4. 开始游戏

在浏览器中打开 `http://localhost:5173`，点击"开始游戏"。

## 项目结构

```
gal_agent/
├── backend/                 # Python 后端
│   ├── src/
│   │   ├── agents/         # AI Agents
│   │   ├── core/           # 核心逻辑
│   │   ├── models.py       # 数据模型
│   │   └── main.py         # FastAPI 服务器
│   ├── scripts/            # 游戏剧本
│   │   └── chapter_01/
│   │       ├── metadata.yaml
│   │       └── plot.md
│   ├── data/               # 游戏存档
│   └── requirements.txt
│
├── frontend/               # React 前端
│   ├── src/
│   │   ├── components/
│   │   ├── App.tsx
│   │   ├── api.ts
│   │   └── types.ts
│   └── package.json
│
└── README.md
```

## 核心概念

### Agent 系统

- **Director Agent**：游戏导演，负责剧情推进、选项生成和结局判断
- **Character Agents**：角色代理，每个 NPC 由独立的 Agent 扮演

### 选项触发算法

导演 Agent 基于以下因素决定何时给玩家选项：

- 对话轮次（避免过长的被动阅读）
- 情感张力（关键时刻优先触发）
- 剧本标记点（预设的选择点）
- 冷却时间（避免选项过密）

### 状态系统

游戏追踪以下状态：

- **角色关系**：信任度、好感度
- **故事标记**：触发的事件和选择
- **紧张度**：当前剧情张力

### 结局系统

结局基于状态条件触发：

```yaml
endings:
  - id: "alice_true_ending"
    condition: "alice_trust >= 70 && met_alice"
    type: "victory"
    priority: 100
```

## 剧本编写

### 创建新章节

1. 在 `backend/scripts/` 下创建新目录，如 `chapter_02/`
2. 创建 `metadata.yaml` 定义角色和结局
3. 创建 `plot.md` 编写剧情内容

### metadata.yaml 示例

```yaml
chapter_id: "chapter_01"
title: "邂逅"

characters:
  - id: "alice"
    name: "艾丽丝"
    personality: "聪明、神秘、谨慎"
    initial_trust: 50
    initial_romance: 0

endings:
  - id: "trust_ending"
    condition: "alice_trust >= 70"
    type: "victory"
    priority: 100
    title: "信任的开始"
    content: "你获得了艾丽丝的信任..."
```

### plot.md 示例

```markdown
# 邂逅

## Beat 1: 咖啡馆相遇
**Mood**: 平静

午后的阳光透过咖啡馆的玻璃窗洒在桌面上...

[OPTION POINT - low]

## Beat 2: 对话开始
**Mood**: 好奇

艾丽丝注意到了你的存在。

Set flag: `met_alice = true`
```

## API 文档

完整 API 文档：`http://localhost:8000/docs`

### 主要端点

- `POST /api/sessions` - 创建游戏会话
- `GET /api/sessions/{session_id}` - 获取会话信息
- `WS /ws/game/{session_id}` - 游戏 WebSocket 连接

## 开发

### 添加新功能

1. **后端**：在 `backend/src/` 中添加逻辑
2. **前端**：在 `frontend/src/components/` 中添加组件

### 调试

- 后端日志：终端输出
- 前端日志：浏览器开发者工具控制台
- WebSocket 消息：在控制台查看

## 贡献

欢迎提交 Issue 和 Pull Request！

## License

MIT

## 致谢

- OpenAI Agent SDK
- FastAPI
- React + Vite
