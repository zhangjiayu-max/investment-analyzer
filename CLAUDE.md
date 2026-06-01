# CLAUDE.md — 项目开发规范

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

### API 路径规范（强制）

**路径格式**：`/api/{模块}/{资源}[/{动作}]`

**命名规则**：
- 统一使用 kebab-case（短横线分隔），如 `/api/valuation/market-temperature`
- 资源使用名词，动作使用动词，如 `/api/agent/create`、`/api/valuation/parse`
- 动态参数放在路径末尾，如 `/api/agent/{agent_id}`
- 静态路由必须在动态路由之前定义，避免路由冲突

**模块划分**：

| 模块 | 路径前缀 | 路由文件 | 说明 |
|------|----------|----------|------|
| 估值 | `/api/valuation` | `routers/valuation.py` | 指数估值数据管理 |
| 持仓 | `/api/portfolio` | `routers/portfolio_new.py` | 投资组合管理 |
| Agent | `/api/agent` | `routers/agent.py` | Agent 系统管理 |
| 对话 | `/api/conversation` | `routers/conversation.py` | 对话和聊天 |
| 任务 | `/api/task` | `routers/task.py` | 任务管理 |
| 文章 | `/api/article` | `routers/article.py` | 文章管理 |
| Dashboard | `/api/dashboard` | `routers/dashboard.py` | 仪表盘和报告 |
| 评测 | `/api/eval` | `routers/eval.py` | 评测系统 |
| Token | `/api/token` | `routers/token_usage.py` | Token 用量统计 |
| 配置 | `/api/config` | `routers/config.py` | 系统配置 |
| 债券 | `/api/bond` | `routers/bond.py` | 债券市场分析 |

**新增 API 必须遵循**：
1. 在对应的路由文件中定义，不要在 `app.py` 中直接定义
2. 路径必须以 `/api/{模块}/` 开头
3. 使用规范的 HTTP 方法（GET 查询、POST 创建/操作、PUT 更新、DELETE 删除）
4. 返回值统一格式：`{"ok": true, "data": ...}` 或 `{"error": "message"}`

### Vue 前端
- `<script setup>` + Composition API，无 Pinia/Vuex
- 无 vue-router，`activePage` + `v-if` 切换页面
- API 统一在 `api/index.js`，组件内 `import { func } from '../api'`
- 弹窗用 `<Teleport to="body">` + `<Transition name="fade">`
- **所有操作类按钮必须用 `ConfirmDialog` 二次确认**（包括：删除、AI 分析触发、重新生成、提交等），仅纯 UI 切换（展开/收起、刷新数据展示）不需要确认
- **所有 LLM 生成内容必须有点赞/点踩反馈按钮**（用于 bad case 收集和系统进化），包括：AI 分析结果、每日简报、热点推荐、对话回复等
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
| 前端测试 | `cd frontend && npm test` — 关键交互必须有测试覆盖 |

**前端测试规范（强制）：**
- 修改 API 路径后必须更新 `api-urls.test.js`
- 修改对话/流式相关逻辑后必须更新 `chat-state.test.js`
- 修改 composable 后必须更新 `composables.test.js`
- 新增关键交互（状态管理、数据流）必须写对应测试
- 每次代码变更后运行 `cd frontend && npm test` 确认通过

## 代码变更后重启规范

**每次代码改动后必须执行以下操作，确保用户看到最新效果：**

| 改动类型 | 重启操作 |
|---------|---------|
| 后端 Python 文件 | 后端有 `--reload` 自动重载，无需手动重启；但修改 `config.py` 环境变量需重启进程 |
| 前端 Vue/JS 文件 | 必须手动构建：`cd frontend && npm run build:deploy`（会自动清理旧文件再复制） |
| 前后端都改了 | 先构建前端，后端自动重载 |

**强制重启后端（杀掉重复进程）**：
```bash
# 使用脚本（推荐）：
./scripts/restart-backend.sh

# 或手动（注意必须 cd 到 backend 目录）：
cd /Users/xiaoyuer/projects/investment-analyzer/backend && pkill -f "uvicorn app:app" 2>/dev/null; sleep 1 && nohup python3 -m uvicorn app:app --host 0.0.0.0 --port 8000 --reload > /tmp/app.log 2>&1 &
```

**⚠️ 易错提醒**：uvicorn 必须在 `backend/` 目录下启动，否则会报 `Could not import module "app"` 错误！
| 前端 Vue/JS 文件 | 必须手动构建：`cd frontend && npm run build:deploy`（会自动清理旧文件再复制） |
| 前后端都改了 | 先构建前端，后端自动重载 |

**强制重启后端（杀掉重复进程）**：
```bash
# 杀掉所有相关进程
pkill -f "uvicorn app:app" 2>/dev/null; sleep 1
# 重新启动
cd /Users/xiaoyuer/projects/investment-analyzer/backend && nohup python3 -m uvicorn app:app --host 0.0.0.0 --port 8000 --reload > /tmp/app.log 2>&1 &
```

**注意**：前端是从 `static/` 目录提供的静态文件，不是 Vite dev server。修改前端代码后必须 `npm run build:deploy`（会清理旧打包文件再复制到 `static/`）才能生效。

## 金融严谨性（最高优先级）

本项目涉及理财投资，任何金融相关的逻辑、指标、阈值、公式**严禁硬编码**：

- **估值阈值**（如 PE/PB 百分位的高/低估分界线）必须存数据库或配置文件，不得写死在代码中
- **计算公式**（如估值百分位、收益率、回撤等）必须抽成独立函数，参数可配置，方便审阅和调整
- **业务规则**（如预警条件、仓位上限、止盈止损比例等）必须通过数据库 `analysis_agents` 表或配置表管理
- **数据来源标识**：所有展示给用户的金融数据必须标注数据来源和更新时间，不得展示无来源的数据
- **风险提示**：涉及投资建议的功能必须附带风险提示，不得给出绝对化的收益承诺
- 任何修改金融逻辑的代码变更，需在代码注释或提交信息中说明变更原因和依据

## 用户记忆机制

当用户在对话中说"记住XX"、"记下XX"、"remember XX"时，必须将相关内容更新到本文件（`CLAUDE.md`）中：

- 归类到合适的章节（编码规范 / 注意事项 / 金融严谨性等），不要随意堆砌
- 如果是临时性任务指令，写入「用户临时指令」章节，任务完成后清理
- 如果是长期偏好或规范，写入对应规范章节
- 保持文件结构清晰，避免冗余

## 用户临时指令

<!-- 用户通过"记住XX"指定的临时任务指令写在此处，完成后删除 -->

## Plan 模式规范

- 使用 plan 模式编写设计稿时，**必须**写入项目 `doc/plans/` 目录下
- 文件名格式：`YYYY-MM-DD-<主题>.md`（如 `2026-05-27-持仓优化.md`）
- 禁止写入 `~/.claude/plans/` 等项目外部目录

## 质量门禁（每次代码变更前检查）
- SQL 必须参数化，禁止拼接用户输入
- LLM 输出必须做 sanitization（防 prompt 注入）
- 金融公式/阈值从配置读取，不硬编码（见「金融严谨性」）
- 改完代码必须跑验证命令，不能空口说"完成"（verification-before-completion）
- **前端状态联动**：改筛选条件/watch 时，检查所有关联数据是否同步刷新（见 `doc/checklists/frontend-state.md`）
- 详细检查清单见 `doc/checklists/code-review.md`

## 注意事项
- LLM `reasoning_content` 需在后续消息传回（MIMO thinking mode）
- 复杂任务用 `compress_history()` / `compress_rag_context()` 控制 token
- 微信图片通过 `/api/proxy-image` 代理绕过防盗链
- 设计文档存 `doc/plans/` 目录，文件名加 `YYYY-MM-DD-` 前缀
- skill 文档存 `backend/skill_document.md`
