# Galgame AI Frontend

React + TypeScript + Vite 前端界面。

## 功能

- 🎮 实时游戏界面
- 💬 WebSocket 实时通信
- 🎨 优雅的深色主题
- 📱 响应式设计

## 安装

```bash
npm install
```

## 开发

```bash
npm run dev
```

前端将在 `http://localhost:5173` 启动。

确保后端服务器在 `http://localhost:8000` 运行。

## 构建

```bash
npm run build
```

构建产物在 `dist/` 目录。

## 项目结构

```
frontend/
├── src/
│   ├── components/
│   │   ├── Game.tsx        # 游戏主界面
│   │   └── Game.css        # 游戏样式
│   ├── App.tsx             # 应用入口
│   ├── App.css             # 应用样式
│   ├── main.tsx            # React 入口
│   ├── index.css           # 全局样式
│   ├── types.ts            # TypeScript 类型定义
│   └── api.ts              # API 客户端
├── index.html
├── vite.config.ts
└── package.json
```

## WebSocket 消息类型

### 接收的消息

- `game_start` - 游戏开始
- `narration` - 叙事内容
- `dialogue` - 角色对话
- `options` - 选项列表
- `state_update` - 状态更新
- `ending` - 结局

### 发送的消息

- `player_choice` - 玩家选择
