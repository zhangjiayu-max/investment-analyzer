# AGENTS.md — 项目开发规范

投资分析助手：基于多 Agent 协作的投资分析系统，支持指数估值分析、持仓管理、文章解读、知识库检索。

**技术栈**：Python 3.11+ / FastAPI / SQLite / ChromaDB / Vue 3 (Composition API + `<script setup>`) / Vite / MIMO 大模型 / akshare

## 项目结构

```
investment-analyzer/
├── backend/
│   ├── app.py              # FastAPI 入口
│   ├── config.py           # 环境变量配置
│   ├── docs/               # 非代码文档（bond_knowledge.md, skill_document.md）
│   ├── agent/              # 多 Agent 协作（按功能分子目录）
│   │   ├── core/           #   编排核心（orchestrator, pipeline, multi_agent, router）
│   │   ├── memory/         #   记忆系统（memory, feedback_learner, kyc_learner）
│   │   ├── eval/           #   评估系统（conversation_evaluator, eval_scorer）
│   │   ├── safety/         #   安全防幻觉（hallucination_guard, prompt_defense）
│   │   ├── infra/          #   基础设施（cache, blackboard, tool_broadcast）
│   │   ├── query/          #   查询处理（query_rewriter, multi_hop_rag）
│   │   ├── kyc/            #   KYC 画像（kyc, wealth_advisor）
│   │   └── state/          #   状态管理（pipeline_state, ab_testing）
│   ├── routers/            # API 路由（按功能分子目录）
│   │   ├── conversation/   #   对话系统
│   │   ├── portfolio/      #   持仓管理
│   │   ├── market/         #   市场数据
│   │   ├── dashboard/      #   看板
│   │   ├── knowledge/      #   知识库
│   │   ├── admin/          #   系统管理
│   │   ├── decision/       #   决策系统
│   │   ├── task/           #   任务管理
│   │   └── analysis/       #   持仓分析
│   ├── services/           # 业务服务层（按功能分子目录）
│   │   ├── llm/            #   LLM 服务
│   │   ├── rag/            #   RAG 检索
│   │   ├── fund/           #   基金分析
│   │   ├── portfolio/      #   组合管理
│   │   ├── valuation/      #   估值分析
│   │   ├── advisor/        #   投资建议
│   │   ├── market/         #   市场数据
│   │   ├── strategy/       #   策略引擎
│   │   └── ...             #   content/conversation/finance/quality/index
│   ├── tools/              # Agent 工具定义 + 执行器
│   ├── mcp/                # MCP 协议客户端（盈米/东方财富/天天基金）
│   ├── db/                 # SQLite 数据层（_conn.py 连接管理 + 按领域分模块）
│   ├── infra/              # 基础设施（熔断器/成本追踪/影子模式）
│   ├── models/             # Pydantic 请求模型
│   ├── api/                # 统一响应协议 + 异常处理
│   ├── scripts/            # 运维脚本
│   └── data/               # SQLite + 图片
├── frontend/
│   └── src/
│       ├── api/index.js    # 所有 API 调用（axios）
│       ├── views/Home.vue  # activePage + v-if 路由切换
│       ├── components/     # 按功能划分的页面组件
│       │   ├── layout/     #   布局组件（Sidebar, ConfirmDialog）
│       │   ├── dashboard/  #   仪表盘卡片
│       │   ├── chat/       #   对话组件
│       │   ├── charts/     #   图表组件
│       │   ├── finance/    #   金融可视化
│       │   ├── ui/         #   通用 UI
│       │   ├── mobile/     #   移动端
│       │   ├── valuation/  #   估值分析
│       │   ├── portfolio/  #   持仓管理
│       │   ├── decision/   #   决策系统
│       │   ├── agent/      #   Agent 管理
│       │   ├── knowledge/  #   知识库
│       │   ├── task/       #   任务与监控
│       │   ├── quality/    #   质量评测
│       │   ├── family/     #   家庭财务
│       │   ├── market/     #   市场分析
│       │   └── analysis/   #   分析结果
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
