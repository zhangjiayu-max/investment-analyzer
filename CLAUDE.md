# CLAUDE.md — 项目开发规范

## 项目概述

投资分析助手（Investment Analyzer）：基于多 Agent 协作的投资分析系统，支持指数估值分析、
持仓管理、文章解读、知识库检索等功能。

## 技术栈

- **后端**：Python 3.11+ / FastAPI / SQLite / ChromaDB (向量检索)
- **前端**：Vue 3 (Composition API / `<script setup>`) / Vite / axios
- **AI**：MIMO 大模型（OpenAI 兼容接口）/ function calling / 多 Agent 协作
- **数据源**：akshare（中国金融数据）/ 有知有行（债市温度）

## 项目结构

```
investment-analyzer/
├── backend/
│   ├── app.py              # FastAPI 入口，所有 API 路由
│   ├── db.py               # SQLite 数据层，建表 + CRUD
│   ├── tools.py            # Agent 工具定义 + 执行器
│   ├── multi_agent.py      # 专家 Agent 定义 + run_specialist()
│   ├── orchestrator.py     # Orchestrator 主控逻辑
│   ├── llm_service.py      # LLM 调用封装
│   ├── rag.py              # RAG 检索（FTS5 + ChromaDB）
│   ├── image_parser.py     # 图片估值解析
│   └── ...
├── frontend/
│   └── src/
│       ├── api/index.js    # 所有 API 调用（axios）
│       ├── views/Home.vue  # 页面路由（v-if，无 vue-router）
│       ├── components/     # 页面组件
│       └── style.css       # 全局样式 + CSS 变量
├── data/                   # SQLite 数据库 + 图片
└── static/                 # 构建产物
```

## 编码规范

### Python 后端

- **框架**：FastAPI，路由用 `@app.get/post/put/delete` 装饰器
- **数据库**：直接用 sqlite3，不用 ORM。连接通过 `db._get_conn()` 获取，`conn.row_factory = sqlite3.Row`
- **CRUD 模式**：
  - `create_xxx() -> int` — INSERT，返回 lastrowid
  - `get_xxx(id) -> dict | None` — SELECT by id
  - `list_xxx() -> list[dict]` — SELECT 列表
  - `update_xxx(id, **fields)` — 动态 SET，自动更新 updated_at
  - `delete_xxx(id) -> bool` — DELETE，返回是否成功
- **建表**：在 `db.init_db()` 中用 `CREATE TABLE IF NOT EXISTS`
- **API 返回格式**：`{"key": value}` 或 `{"ok": True, "id": ...}`
- **错误处理**：`HTTPException(404, "...")` / `HTTPException(400, "...")`
- **异步任务**：`asyncio.create_task(...)` 后台执行，返回 `{"ok": True, "message": "..."}`
- **命名**：函数和变量用 snake_case，常量用 UPPER_SNAKE_CASE
- **注释**：模块顶部用中文注释说明用途，函数不写 docstring 除非逻辑复杂
- **import 风格**：`from db import func1, func2, ...` 显式导入

### Vue 前端

- **组件风格**：`<script setup>` + Composition API，不用 Options API
- **状态管理**：`ref()` / `computed()`，不用 Vuex/Pinia
- **路由**：无 vue-router，通过 `activePage` 字符串 + `v-if` 切换页面
- **API 调用**：统一在 `api/index.js` 中定义，组件中 `import { func } from '../api'`
- **样式**：`<style scoped>`，优先用全局 CSS 变量和工具类（.card, .btn-primary 等）
- **弹窗**：用 `<Teleport to="body">` + `<Transition name="fade">`
- **确认操作**：用 `ConfirmDialog` 组件，所有删除/重要操作必须有二次确认
- **Toast 提示**：组件内自建 `toast = ref({ show, message, type })` 实现
- **命名**：组件用 PascalCase，文件名与组件名一致

### Agent 系统

- **工具定义**：在 `tools.py` 的 `TOOLS` 数组中添加 OpenAI function calling 格式
- **工具执行**：在 `execute_tool()` 中添加分发 case，实现 `_tool_name()` 函数
- **专家 Agent**：在 `multi_agent.py` 的 `SPECIALIST_AGENTS` 中定义
- **Orchestrator 工具**：在 `orchestrator.py` 的 `ORCHESTRATOR_TOOLS` 中定义
- **需求澄清**：`clarify_requirement()` 用 LLM 分析问题复杂度和专家选择
- **流式输出**：用 generator `yield {"type": "...", ...}` 传递 SSE 事件

## 文档规范

- **设计文档**：保存到 `doc/plans/` 目录，文件名加时间前缀，如 `2026-05-24-xxx-design.md`
- **CLAUDE.md**：项目根目录，记录开发规范和编码约定
- **skill 文档**：保存到 `backend/skill_document.md`

## 开发流程

### 新增数据库表

1. `db.py` — `init_db()` 中加 `CREATE TABLE IF NOT EXISTS`
2. `db.py` — 添加 CRUD 函数
3. `app.py` — 导入 + 添加 API 路由 + Pydantic 请求模型
4. `api/index.js` — 添加前端 API 函数
5. 前端组件 — 创建页面组件
6. `Sidebar.vue` — 添加导航项 + SVG 图标
7. `Home.vue` — 导入组件 + 添加 `v-if` 块

### 新增 Agent 工具

1. `tools.py` — `TOOLS` 数组添加工具定义
2. `tools.py` — `execute_tool()` 添加 case + 实现函数
3. `multi_agent.py` — 将工具名加入相关专家的 `tools` 列表
4. `orchestrator.py` — 更新 `CLARIFICATION_PROMPT` 和关键词路由

### 新增前端页面

1. `frontend/src/components/XxxPage.vue` — 创建组件
2. `frontend/src/api/index.js` — 添加 API 调用函数
3. `frontend/src/components/Sidebar.vue` — `navItems` 添加项 + SVG 图标
4. `frontend/src/views/Home.vue` — import + `v-if="activePage === 'xxx'"` 块

## 注意事项

- **MIMO thinking mode**：LLM 返回的 `reasoning_content` 需要在后续消息中传递回去
- **Token 优化**：复杂任务用 `compress_history()` 和 `compress_rag_context()` 压缩上下文
- **SSE 流式**：前端用 `fetch` + `ReadableStream` 接收，不是 EventSource
- **图片代理**：微信图片需通过 `/api/proxy-image` 代理，绕过防盗链
- **确认弹窗**：所有删除操作必须使用 ConfirmDialog 进行二次确认（记忆规则）
