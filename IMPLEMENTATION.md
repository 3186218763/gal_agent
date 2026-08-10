# Galgame AI - 实现总结

## 已完成的核心模块

### 1. Agent 系统（使用 OpenAI Agents SDK）

#### Director Agent (`backend/src/agents/director.py`)
- 使用 `Agent[GameContext]` 创建游戏导演
- 包含 `should_trigger_option_tool` 工具用于判断选项触发时机
- 实现动态选项生成：根据游戏状态、角色关系、剧情标记生成 2-4 个选项
- 返回 `OptionCandidate` 对象列表，包含预测后果

#### Character Agent (`backend/src/agents/character.py`)
- 每个角色使用独立的 `Agent[CharacterContext]`
- 维护对话历史以保持连续性
- 根据信任度和好感度调整对话语气
- 支持记忆系统（最近 5 条重要事件）

#### Character Factory
- 统一管理所有角色 agents
- 为每个角色维护独立的上下文（`CharacterContext`）

### 2. 游戏循环 (`backend/src/core/game_loop.py`)

- 异步游戏主循环，通过 WebSocket 推送事件
- Beat 执行：区分 narration 和 dialogue 类型
- 选项触发：使用 Director Agent 判断时机
- 选项生成：Director Agent 生成后发送给前端
- 玩家选择处理：应用后果到游戏状态
- 结局检查：基于条件表达式评估

### 3. 状态管理 (`backend/src/core/state_manager.py`)

- 游戏状态持久化到 JSON 文件
- 支持会话创建、加载、保存、删除
- 提供便捷方法更新 flags 和角色关系
- 自动保存到 `data/` 目录

### 4. 剧本解析 (`backend/src/core/script_parser.py`)

- 解析 YAML 元数据（角色、结局条件）
- 解析 Markdown 剧情（按 Beat 分割）
- 自动识别：Mood、选项标记、flag 设置、角色交互
- 支持章节列表功能

### 5. FastAPI 后端 (`backend/src/main.py`)

**REST API:**
- `POST /api/sessions` - 创建游戏会话
- `GET /api/sessions/{session_id}` - 获取会话信息
- `DELETE /api/sessions/{session_id}` - 删除会话
- `GET /api/sessions` - 列出所有会话

**WebSocket:**
- `WS /ws/game/{session_id}` - 实时游戏连接

### 6. 前端界面（React + TypeScript）

- 完整的游戏 UI 组件
- WebSocket 实时通信
- 自动滚动消息显示
- 选项按钮交互
- 结局显示

## 技术栈

### 后端
- **OpenAI Agents SDK** (`openai-agents==0.1.3`)
- FastAPI + Uvicorn
- WebSocket
- Pydantic (数据验证)
- PyYAML (剧本解析)

### 前端
- React 18
- TypeScript
- Vite
- WebSocket API

## 项目结构

```
gal_agent/
├── backend/
│   ├── src/
│   │   ├── agents/
│   │   │   ├── __init__.py
│   │   │   ├── director.py       # Director Agent (OpenAI Agents SDK)
│   │   │   └── character.py      # Character Agent (OpenAI Agents SDK)
│   │   ├── core/
│   │   │   ├── __init__.py
│   │   │   ├── game_loop.py      # 游戏主循环
│   │   │   ├── script_parser.py  # 剧本解析器
│   │   │   └── state_manager.py  # 状态管理器
│   │   ├── models.py              # 数据模型
│   │   └── main.py                # FastAPI 服务器
│   ├── scripts/
│   │   └── chapter_01/
│   │       ├── metadata.yaml      # 章节元数据
│   │       └── plot.md            # 剧情内容
│   ├── data/                      # 游戏存档（自动创建）
│   ├── requirements.txt
│   ├── .env.example
│   └── start.sh                   # 启动脚本
│
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── Game.tsx           # 主游戏组件
│   │   │   └── Game.css
│   │   ├── hooks/
│   │   │   └── useWebSocketGame.ts
│   │   ├── types.ts
│   │   ├── api.ts
│   │   ├── App.tsx
│   │   └── main.tsx
│   └── package.json
│
└── README.md
```

## 启动指南

### 1. 配置环境

```bash
# 后端
cd backend
cp .env.example .env
# 编辑 .env，填入你的 OPENAI_API_KEY
```

### 2. 启动后端

```bash
cd backend
./start.sh
```

或者手动启动：

```bash
cd backend
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
python -m src.main
```

后端将在 `http://localhost:8000` 启动

### 3. 启动前端

```bash
cd frontend
npm install
npm run dev
```

前端将在 `http://localhost:5173` 启动

### 4. 开始游戏

1. 打开浏览器访问 `http://localhost:5173`
2. 点击"开始游戏"
3. 阅读剧情，在提示时做出选择
4. 根据你的选择，最终会触发不同的结局

## OpenAI Agents SDK 核心用法

### Agent 定义

```python
from agents import Agent
from pydantic import BaseModel

class MyContext(BaseModel):
    data: dict = {}

agent = Agent[MyContext](
    name="AgentName",
    instructions="Agent 的职责和行为规则",
    tools=[tool1, tool2]  # 可选的工具
)
```

### Tool 定义

```python
from agents.decorators import tool
from agents import RunContextWrapper

@tool
async def my_tool(
    context: RunContextWrapper[MyContext],
    param1: str,
    param2: int
) -> dict:
    """工具描述"""
    # 访问上下文
    data = context.data
    
    # 执行逻辑
    result = do_something(param1, param2)
    
    return {"result": result}
```

### 运行 Agent

```python
from agents import Runner

context = MyContext(data={"key": "value"})

result = await Runner.run(
    agent,
    input="用户输入或提示",
    context=context
)

# 获取最终输出
output = result.final_output

# 获取对话历史（用于继续对话）
history = result.to_input_list()
```

## 关键设计模式

### 1. 选项触发算法

Director Agent 使用 `should_trigger_option_tool` 计算分数：
- 剧本标记 (+40)
- 对话轮次累积 (每轮 +5，6 轮后开始)
- 紧张度 (≥7 时 +15)
- 冷却期惩罚 (<3 轮 -30)

阈值：≥50 分触发选项

### 2. 选项生成流程

1. Director Agent 根据当前上下文生成 3-4 个候选选项
2. 每个选项包含：
   - 选项文字（玩家视角）
   - 预测后果（flag 变化、关系变化）
   - 对剧情的影响描述
3. 发送到前端，玩家选择
4. 应用选择后果到游戏状态

### 3. 角色对话生成

Character Agent 考虑：
- 当前情境
- 与玩家的信任度/好感度
- 角色性格设定
- 之前的重要事件记忆

### 4. 结局评估

在章节结束或关键时刻评估：
- 按优先级排序所有结局条件
- 使用 eval 评估条件表达式（变量：flags + 角色关系值）
- 返回第一个满足条件的结局

## 扩展指南

### 添加新章节

1. 在 `backend/scripts/` 下创建新目录（如 `chapter_02/`）
2. 创建 `metadata.yaml`（定义角色和结局）
3. 创建 `plot.md`（编写剧情 beats）
4. 在前端调用 `POST /api/sessions` 时指定 `chapter_id: "chapter_02"`

### 添加新角色

在 `metadata.yaml` 中添加：

```yaml
characters:
  - id: "新角色ID"
    name: "角色名"
    personality: "性格描述"
    initial_trust: 50
    initial_romance: 0
```

Character Factory 会自动创建对应的 Character Agent

### 添加新结局

在 `metadata.yaml` 的 `endings` 中添加：

```yaml
endings:
  - id: "ending_id"
    condition: "alice_trust >= 80 and special_flag"
    type: "victory"  # 或 "branch", "game_over"
    priority: 100
    title: "结局标题"
    content: "结局内容描述"
```

## 下一步优化建议

1. **添加 Narrator Agent**：专门生成场景描述
2. **改进结局条件评估**：使用更安全的表达式解析（而非 eval）
3. **添加存档/读档功能**：保存多个游戏进度点
4. **优化选项质量控制**：添加选项验证逻辑
5. **添加角色立绘和背景图**：提升视觉体验
6. **支持流式输出**：让 AI 生成的对话逐字显示
7. **添加音效和背景音乐**
8. **支持分支章节**：根据结局类型跳转到不同章节

## 常见问题

### Q: 如何调试 Agent 行为？

查看后端控制台输出，Agent 的提示和响应会被打印出来。

### Q: 如何调整选项触发频率？

修改 `backend/src/agents/director.py` 中的 `should_trigger_option_tool` 函数的分数计算逻辑。

### Q: 如何修改角色说话风格？

编辑 `metadata.yaml` 中对应角色的 `personality` 字段，或修改 `CharacterAgent` 的 `instructions`。

### Q: OpenAI API Key 在哪里配置？

在 `backend/.env` 文件中设置 `OPENAI_API_KEY=your_key_here`

## 总结

这是一个基于 OpenAI Agents SDK 的动态 Galgame 引擎：
- ✅ 使用 Agent SDK 实现 Director 和 Character agents
- ✅ 动态选项生成和触发算法
- ✅ 多结局系统
- ✅ 角色关系和剧情标记追踪
- ✅ 前后端 WebSocket 实时通信
- ✅ 剧本解析器（YAML + Markdown）
- ✅ 完整的测试剧本

核心特性：AI 驱动的角色对话、智能的选项触发、基于状态的多结局。
