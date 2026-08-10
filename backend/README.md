# Galgame AI Backend

基于 OpenAI Agent SDK 的动态 Galgame 后端服务。

## 功能特性

- 🤖 AI 驱动的角色对话（每个角色独立的 Agent）
- 📖 动态剧情生成和选项系统
- 🎯 智能选项触发算法
- 💾 游戏状态持久化
- 🎭 多结局系统
- 🔌 WebSocket 实时通信

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env` 并填入你的 OpenAI API Key：

```bash
cp .env.example .env
```

编辑 `.env`：
```
OPENAI_API_KEY=sk-your-actual-key-here
```

### 3. 运行服务器

```bash
python -m src.main
```

服务器将在 `http://localhost:8000` 启动。

### 4. API 文档

启动后访问：
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

## 项目结构

```
backend/
├── src/
│   ├── agents/          # AI Agents
│   │   ├── director.py      # 导演 Agent（生成选项）
│   │   └── character.py     # 角色 Agent（扮演 NPC）
│   ├── core/            # 核心逻辑
│   │   ├── game_loop.py         # 游戏主循环
│   │   ├── script_parser.py     # 剧本解析器
│   │   ├── state_manager.py     # 状态管理
│   │   ├── option_trigger.py    # 选项触发算法
│   │   ├── option_generator.py  # 选项验证
│   │   └── ending_evaluator.py  # 结局评估
│   ├── models.py        # 数据模型
│   └── main.py          # FastAPI 服务器
├── scripts/             # 游戏剧本
│   └── chapter_01/
│       ├── metadata.yaml    # 章节元数据
│       └── plot.md          # 剧情内容
├── data/                # 游戏存档（自动创建）
└── requirements.txt
```

## API 端点

### REST API

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/` | 健康检查 |
| POST | `/api/sessions` | 创建新游戏会话 |
| GET | `/api/sessions/{session_id}` | 获取会话信息 |
| DELETE | `/api/sessions/{session_id}` | 删除会话 |
| GET | `/api/sessions` | 列出所有会话 |

### WebSocket

| 端点 | 说明 |
|------|------|
| `/ws/game/{session_id}` | 游戏实时连接 |

## WebSocket 消息格式

### 服务器 → 客户端

**游戏开始**
```json
{
  "type": "game_start",
  "chapter": "邂逅",
  "session_id": "uuid"
}
```

**叙事内容**
```json
{
  "type": "narration",
  "content": "午后的咖啡馆...",
  "mood": "平静"
}
```

**角色对话**
```json
{
  "type": "dialogue",
  "character": "alice",
  "content": "我知道你在找什么。",
  "mood": "紧张"
}
```

**选项**
```json
{
  "type": "options",
  "options": [
    {
      "id": "opt_0",
      "text": "询问她更多细节",
      "preview": "艾丽丝会更信任你"
    }
  ]
}
```

**状态更新**
```json
{
  "type": "state_update",
  "changes": {
    "flags": {"met_alice": true},
    "relationships": {
      "alice": {"trust": 55, "romance": 0}
    }
  }
}
```

**结局**
```json
{
  "type": "ending",
  "ending_id": "alice_trust_ending",
  "title": "信任的开始",
  "content": "你选择相信艾丽丝...",
  "ending_type": "victory"
}
```

### 客户端 → 服务器

**玩家选择**
```json
{
  "type": "player_choice",
  "option_index": 0
}
```

## 剧本格式

### metadata.yaml

```yaml
chapter_id: "chapter_01"
title: "章节标题"

characters:
  - id: "alice"
    name: "艾丽丝"
    personality: "性格描述..."
    initial_trust: 50
    initial_romance: 0

endings:
  - id: "good_ending"
    condition: "alice_trust >= 70 && met_alice"
    type: "victory"
    priority: 100
    title: "结局标题"
    content: "结局内容"
```

### plot.md

```markdown
# 章节标题

## Beat 1: 开场
**Mood**: 平静

剧情内容...

Set flag: `game_started = true`

[OPTION POINT - 可选择点标记]
```

## 开发

### 添加新章节

1. 在 `scripts/` 下创建新目录，如 `chapter_02/`
2. 创建 `metadata.yaml` 和 `plot.md`
3. 定义角色、结局和剧情内容

### 调试

启动时添加 `--reload` 实现热重载：

```bash
uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
```

## V2 Story Foundation

The V2 domain can validate a script pack and initialize an event-sourced session without an API key:

```bash
uv run python -m src.story.cli validate script_packs/cafe_mystery
uv run python -m src.story.cli init-session script_packs/cafe_mystery --database data/story.db --session-id local_demo --seed 17
uv run python -m src.story.cli inspect-session local_demo --database data/story.db
```

The V1 FastAPI and WebSocket entry point remains unchanged during this foundation milestone.

## License

MIT
