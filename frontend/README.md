# Galgame AI Frontend

React + TypeScript + Vite 段式播放器（segment-aware player）。

## 功能

- 🎮 基于 `POST /api/v2/sessions/{id}/turns` SSE 流的段式演出播放
- ⏳ 段内积压缓冲：`segment_ready` 前的内容按 provisional 缓冲，解锁后才播放
- ⌨️ 点击 / Enter 打字机推进，本地队列排空后才展示选项或结局
- 🔄 刷新后从公开投影回放已提交段落；Pending Consequence 仅通过 `/turns` 恢复一次
- 🎨 优雅的深色主题、响应式设计

## 安装

```bash
npm install
```

## 开发

```bash
npm run dev
```

前端将在 `http://localhost:5173` 启动。确保后端服务器在 `http://localhost:8000` 运行（`VITE_API_BASE` 可覆盖 API 地址）。

## 构建 / 测试 / 检查

```bash
npm run build   # tsc strict + vite build
npm test        # vitest run（74 个测试）
npm run lint    # eslint --max-warnings 0
```

## 项目结构

```
frontend/
├── src/
│   ├── segmentPlayer.ts    # 纯状态机：provisional 缓冲、解锁、排空、终局迁移
│   ├── stream.ts           # SSE 消费：streamTurn（segment_started/block/segment_ready/heartbeat/retry_after/error）
│   ├── api.ts              # REST 客户端 + SessionProjection 段字段
│   ├── Playback.tsx        # 段式播放组件（typewriter、buffering overlay、replay）
│   ├── App.tsx             # 屏幕状态机（booting/start/play/choices/ending/error）
│   ├── storage.ts          # localStorage 存档
│   ├── main.tsx            # React 入口（StrictMode）
│   └── *.test.ts(x)        # 状态机 / SSE / 播放 / 屏幕 / 端到端（spec 12.4）测试
├── index.html
├── vite.config.ts
└── package.json
```

## SSE 事件类型（与后端 Plan 2 协议一致）

- `segment_started` — `{segment_id, expected_revision}`
- `block` — `{segment_id, index, kind, text, character_id}`（provisional）
- `segment_ready` — `{segment_id, revision, terminal, choices|null, ending|null, blocks, cleared?}`
- `heartbeat` / `retry_after` / `error` — 保活、租约重试提示、`{code}` 错误
