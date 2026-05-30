# 专家 Agent 数据库化整改方案

## 背景

当前多 Agent 编排系统中，5 个专家 Agent 的配置（name、icon、description、tools、system_prompt）硬编码在 `multi_agent.py` 的 `SPECIALIST_AGENTS` 字典中，与 `agents` 数据库表完全独立。导致：
- 通过页面修改 Agent prompt 后，编排器不会使用新版本
- 版本历史无法追踪专家 prompt 的变更
- 新增/删除专家需要改代码

## 目标

所有专家 Agent 的配置从数据库读取，代码中不再硬编码 prompt。通过 Agent 管理页面修改 prompt 后，编排器立即生效。

## 改动范围

### 1. 数据库：`agents` 表新增字段

给 `agents` 表新增 3 个列：
- `agent_key TEXT` — 专家标识符（如 `valuation_expert`），用于编排器路由。非专家 Agent 为 NULL
- `tools TEXT` — 该专家可调用的工具列表，JSON 数组格式
- `is_specialist INTEGER DEFAULT 0` — 是否为编排专家（1=是，0=普通对话 Agent）

迁移后 5 个专家的数据：

| agent_key | 数据库 ID | name |
|-----------|----------|------|
| valuation_expert | 1 | 估值分析师 |
| market_analyst | 2564 | 择时分析师 |
| risk_assessor | 45 | 风险管理师 |
| fund_analyst | 2565 | 基金分析师 |
| allocation_advisor | 46 | 资产配置师 |

### 2. 新增加载函数：`db/agents.py`

```python
def load_specialist_agents() -> dict:
    """从数据库加载所有编排专家，返回 {agent_key: {name, icon, description, tools, system_prompt}}"""
```

带内存缓存（TTL 60 秒），避免每次请求都查库。Agent 更新时清除缓存。

### 3. 改造 `multi_agent.py`

- **删除**硬编码的 `SPECIALIST_AGENTS` 字典（约 230 行）
- 改为 `from db.agents import load_specialist_agents`
- `run_specialist()` / `run_specialist_with_context()` 中：
  - `agent = SPECIALIST_AGENTS[agent_key]` → `agent = load_specialist_agents()[agent_key]`
  - 同时将 tools 解析从 JSON 字符串还原为 list

### 4. 改造 `orchestrator.py`

- **删除**硬编码的 `ORCHESTRATOR_TOOLS`（约 90 行）
- **删除**硬编码的 `_EXPERT_MAP`（约 7 行）
- **删除** `CLARIFICATION_PROMPT` 中的硬编码专家列表
- **删除** `valid_specialists` 硬编码列表
- 新增 `build_orchestrator_tools(specialists)` — 从数据库数据动态生成 tool 定义
- 新增 `build_clarification_prompt(specialists)` — 从数据库数据动态生成路由提示词
- 新增 `build_expert_map(specialists)` — 从数据库数据动态生成 tool→agent 映射

### 5. 改造 `routers/conversations.py`

- **删除** `_get_specialist_name()` 中的硬编码映射
- 改用 `load_specialist_agents()` 查询

### 6. Agent 更新时清除缓存

在 `routers/agents.py` 的 `update_agent_api` 中，更新 agent 后调用清除缓存函数，确保编排器立即使用新 prompt。

## 不改动的部分

- `agents` 表的 CRUD 接口不变
- Agent 管理页面的前端不变（已有编辑功能）
- 版本管理逻辑不变（已有 `save_prompt_version`）
- 5 个专家的 system_prompt 内容不变（已增强）

## 验证方式

1. 启动后端，确认 5 个专家从数据库正常加载
2. 对话中触发多专家编排，确认各专家正常工作
3. 在 Agent 管理页面修改某个专家的 prompt，再次对话确认使用新 prompt
4. 查看版本历史，确认旧版本已保存
