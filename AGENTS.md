# AGENTS.md — 项目开发规范

投资分析助手：基于多 Agent 协作的投资分析系统，支持指数估值分析、持仓管理、文章解读、知识库检索。

**技术栈**：Python 3.11+ / FastAPI / SQLite / ChromaDB / Vue 3 (Composition API + `<script setup>`) / Vite / MIMO 大模型 / akshare

## 项目结构

```
investment-analyzer/
├── backend/
│   ├── app.py              # FastAPI 入口，所有 API 路由（146 条）
│   ├── config.py           # 环境变量配置
│   ├── llm_service.py      # LLM 调用封装（重试、流式、token 记录）
│   ├── rag.py              # RAG 检索（FTS5 + ChromaDB）
│   ├── tools/              # Agent 工具定义 + 执行器（__init__.py）
│   ├── agent/              # 多 Agent 协作
│   │   ├── multi_agent.py  #   专家 Agent 定义 + run_specialist()
│   │   └── orchestrator.py #   主控编排 + clarification
│   ├── mcp/                # MCP 协议客户端
│   │   ├── yingmi_client.py#   盈米且慢 MCP（Streamable HTTP）
│   │   └── trading_calendar.py
│   ├── db/                 # SQLite 数据层
│   │   ├── core.py         #   连接管理 _get_conn()、init_db()
│   │   ├── portfolio.py    #   持仓/交易/现金/预警
│   │   ├── agents.py       #   对话/消息/Agent 运行
│   │   └── ...             #   文章/估值/评测/反馈等模块
│   ├── article_reader.py   # 公众号文章抓取
│   ├── market_data.py      # 行情数据（akshare）
│   ├── valuation.py        # PE/PB 百分位估值分析
│   ├── image_parser.py     # 图片估值解析
│   └── data/               # SQLite + 图片
├── frontend/
│   └── src/
│       ├── api/index.js    # 所有 API 调用（axios）
│       ├── views/Home.vue  # activePage + v-if 路由切换
│       ├── components/     # 按功能划分的页面组件
│       └── style.css       # CSS 变量设计系统
├── doc/plans/              # 设计文档（日期前缀命名）
└── static/                 # 构建产物
```

## 编码规范

### Python 后端
- FastAPI 路由用 `@app.get/post/put/delete`，统一在 `app.py`
- sqlite3 直连（无 ORM），`from db import func1, func2` 显式导入
- CRUD 模式：`create_xxx() → int` / `get_xxx(id) → dict|None` / `list_xxx() → list[dict]` / `update_xxx(id, **fields)` / `delete_xxx(id) → bool`
- 建表在 `db/core.py` 的 `init_db()` 中用 `CREATE TABLE IF NOT EXISTS`
- API 返回 `{"key": value}` 或 `{"ok": True, "id": ...}`，异常抛 `HTTPException`
- 异步任务用 `asyncio.create_task()` 后台执行
- snake_case 函数/变量，UPPER_SNAKE_CASE 常量，中文模块注释

### Vue 前端
- `<script setup>` + Composition API，无 Pinia/Vuex
- 无 vue-router，`activePage` + `v-if` 切换页面
- API 统一在 `api/index.js`，组件内 `import { func } from '../api'`
- 弹窗用 `<Teleport to="body">` + `<Transition name="fade">`
- 所有删除/重要操作必须用 `ConfirmDialog` 二次确认
- 组件名 PascalCase，与文件名一致

### Agent 系统
- 工具定义在 `tools/__init__.py` 的 `TOOLS` 数组 + `execute_tool()` 分发
- 专家 Agent 在 `agent/multi_agent.py` 的 `SPECIALIST_AGENTS` 定义
- 编排逻辑在 `agent/orchestrator.py`
- 所有 prompt 通过 `analysis_agents` 表配置管理（反对硬编码）
- SSE 流式用 generator `yield {"type": ..., ...}` + fetch ReadableStream

## 开发流程

| 任务 | 步骤 |
|------|------|
| 新增表 | `db/core.py` init_db 加 CREATE → 模块内加 CRUD → app.py 加路由 → api/index.js 加函数 → 组件 → Sidebar → Home.vue |
| Agent 工具 | `tools/__init__.py` 加 TOOLS 定义 + execute_tool case → 加入专家 tools 列表 |
| 前端页面 | 创建组件 → api/index.js 加函数 → Sidebar 加导航 → Home.vue 加 v-if |

## 注意事项
- LLM `reasoning_content` 需在后续消息传回（MIMO thinking mode）
- 复杂任务用 `compress_history()` / `compress_rag_context()` 控制 token
- 微信图片通过 `/api/proxy-image` 代理绕过防盗链
- 设计文档存 `doc/plans/` 目录，文件名加 `YYYY-MM-DD-` 前缀
- skill 文档存 `backend/skill_document.md`
