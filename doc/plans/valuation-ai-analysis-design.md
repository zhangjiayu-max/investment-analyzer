# 估值数据页 — AI 市场分析功能设计稿

## 一、问题现状

### 1.1 当前选择指数无明显标识
- 估值数据页选择了指数后，用户无法一眼看到当前选的是哪个指数
- 需要在页面顶部或显著位置展示当前指数名称和代码

### 1.2 缺少 AI 市场分析入口
- 用户需要手动去查新闻、看行情，再回到估值页做判断
- 需要一个"一键 AI 分析"按钮，自动生成基于最新市场信息的投资分析报告

### 1.3 分析结果无法沉淀
- 每次分析都是临时的，无法回看历史分析
- 需要将 AI 分析结果持久化，支持历史查看

---

## 二、功能设计

### 2.1 当前指数标识

**位置**：估值数据页面顶部，Tab 栏上方

**样式**：
```
┌─────────────────────────────────────────────────┐
│  📊 沪深300 (000300.SH)          [AI 市场分析]  │
│  PE: 12.5  百分位: 35%  状态: 合理偏低           │
├─────────────────────────────────────────────────┤
│  [估值历史] [AI 分析历史]                         │
└─────────────────────────────────────────────────┘
```

**数据来源**：从估值历史数据中取最新一条的估值指标

---

### 2.2 AI 市场分析

#### 2.2.1 入口
- 估值页顶部"AI 市场分析"按钮
- 点击后弹出确认对话框（显示将消耗 Token 提示）
- 确认后触发分析

#### 2.2.2 Agent 配置（数据库表）

新增 `analysis_agents` 表：

```sql
CREATE TABLE IF NOT EXISTS analysis_agents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,           -- agent 名称，如"市场日报分析师"
    description TEXT,             -- 描述
    system_prompt TEXT NOT NULL,  -- 系统提示词（可在线编辑）
    is_active INTEGER DEFAULT 1, -- 是否启用
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

默认插入一条：

```sql
INSERT INTO analysis_agents (name, description, system_prompt) VALUES (
    '市场日报分析师',
    '基于最新财经新闻生成 A 股市场快报，服务于基金配置决策',
    '你扮演一位专业的基金投资经理，为我提供一份今日的A股市场快报，重点服务于基金配置决策。报告需包含：

* **今日市况速览**：用一两句话总结市场整体情绪和主要特征。
* **板块掘金与排雷**：
  - **机会所在（热门板块）**：分析强势板块。请使用"政策/事件驱动 + 资金动向 + 估值安全边际"的框架进行分析。
  - **风险提示（回调板块）**：分析弱势板块。请说明回调原因，并判断是短期技术性调整还是基本面发生变化。
* **基金策略池**：根据上述分析，构建一个简单的基金组合建议，例如：
  - **进攻端**：推荐与强势板块对应的ETF或主动型基金。
  - **防御/均衡端**：推荐能覆盖"低估值+高股息"资产的基金，或选股能力较强的均衡型基金。
  - 请简要说明每只基金入围的理由及其与当前市场逻辑的契合点。

请确保分析有数据支撑，结论清晰明了。'
);
```

#### 2.2.3 分析流程

```
用户点击 [AI 市场分析]
    ↓
后端查询最新财经新闻（web_search 工具）
    ↓
拼装 Prompt：Agent 系统提示词 + 新闻上下文 + 当前指数估值数据
    ↓
调用 LLM 生成分析报告
    ↓
保存到 analysis_history 表
    ↓
返回前端展示（Markdown 渲染）
```

#### 2.2.4 新增 API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/analysis/run` | 触发 AI 分析（传入 index_code） |
| GET | `/api/analysis/history` | 获取分析历史列表 |
| GET | `/api/analysis/history/{id}` | 获取单条分析详情 |
| DELETE | `/api/analysis/history/{id}` | 删除分析记录 |
| GET | `/api/analysis-agents` | 获取 Agent 配置列表 |
| PUT | `/api/analysis-agents/{id}` | 更新 Agent 配置（提示词） |

---

### 2.3 分析历史

#### 2.3.1 数据表

```sql
CREATE TABLE IF NOT EXISTS analysis_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    index_code TEXT,              -- 关联的指数代码
    index_name TEXT,              -- 指数名称
    agent_id INTEGER,             -- 使用的 agent id
    agent_name TEXT,              -- agent 名称快照
    prompt_used TEXT,             -- 实际使用的完整 prompt（含上下文）
    news_context TEXT,            -- 检索到的新闻上下文
    valuation_context TEXT,       -- 当时的估值数据快照
    result TEXT NOT NULL,         -- LLM 生成的分析报告（Markdown）
    token_usage INTEGER,          -- 消耗的 token 数
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

#### 2.3.2 历史列表样式

```
┌─────────────────────────────────────────────────┐
│  AI 分析历史                                      │
├─────────────────────────────────────────────────┤
│  📅 2026-05-24 15:30  沪深300  消耗 1200 tokens   │
│     市场日报分析师                                 │
│     [查看] [删除]                                 │
├─────────────────────────────────────────────────┤
│  📅 2026-05-23 14:00  白酒    消耗 980 tokens     │
│     市场日报分析师                                 │
│     [查看] [删除]                                 │
└─────────────────────────────────────────────────┘
```

---

## 三、前端改动

### 3.1 ValuationHistory.vue

1. **顶部指数标识区**：
   - 显示当前指数名称、代码、最新估值指标
   - "AI 市场分析"按钮

2. **Tab 栏扩展**：
   - 原有：`[估值历史]`
   - 新增：`[AI 分析历史]`

3. **AI 分析结果展示**：
   - 加载中状态（流式显示）
   - Markdown 渲染分析报告
   - 显示 token 消耗、分析时间

4. **历史列表**：
   - 时间 + 指数 + agent 名称 + token 消耗
   - 点击展开查看完整报告

### 3.2 新增组件（可选）

- `AnalysisAgentConfig.vue` — Agent 提示词在线编辑（设置页）

---

## 四、后端改动

### 4.1 新增文件/模块

无需新增文件，在现有模块中扩展：

- `db.py` — 新增 `analysis_agents` 和 `analysis_history` 表及 CRUD 函数
- `app.py` — 新增 6 个 API 端点
- `llm_service.py` — 复用 `_call_llm()` 带重试和 token 记录

### 4.2 分析触发逻辑（app.py）

```python
@app.post("/api/analysis/run")
async def run_analysis(req: AnalysisRunRequest):
    # 1. 获取 agent 配置
    agent = get_analysis_agent(req.agent_id)
    
    # 2. 获取当前指数估值数据
    valuation = get_latest_valuation(req.index_code)
    
    # 3. 检索最新新闻（复用 web_search 工具）
    from tools import execute_tool
    news_json = execute_tool("web_search", {"query": f"A股 今日行情 板块", "max_results": 5})
    
    # 4. 拼装 prompt
    full_prompt = agent["system_prompt"]
    if valuation:
        full_prompt += f"\n\n当前指数估值数据：\n{valuation}"
    full_prompt += f"\n\n最新财经新闻：\n{news_json}"
    
    # 5. 调用 LLM
    result = _call_llm(
        model=MODEL,
        messages=[
            {"role": "system", "content": full_prompt},
            {"role": "user", "content": "请生成今日市场分析报告。"},
        ],
        temperature=0.3,
        max_tokens=4000,
    )
    
    # 6. 保存历史
    history_id = save_analysis_history(
        index_code=req.index_code,
        index_name=req.index_name,
        agent_id=agent["id"],
        agent_name=agent["name"],
        prompt_used=full_prompt,
        news_context=news_json,
        valuation_context=str(valuation),
        result=result.choices[0].message.content,
        token_usage=result.usage.total_tokens if result.usage else 0,
    )
    
    return {"id": history_id, "result": result.choices[0].message.content}
```

---

## 五、实现优先级

| 优先级 | 功能 | 工作量 |
|--------|------|--------|
| P0 | 当前指数标识显示 | 0.5h |
| P0 | analysis_agents + analysis_history 建表 | 0.5h |
| P0 | AI 分析触发 API | 1h |
| P0 | 前端 AI 分析按钮 + 结果展示 | 1.5h |
| P1 | 分析历史列表 + 查看 | 1h |
| P1 | Agent 提示词在线编辑 | 1h |
| P2 | 流式输出分析过程 | 1h |

总预估：6-7 小时

---

## 六、涉及文件

- `backend/db.py` — 建表 + CRUD
- `backend/app.py` — 新增 API 端点
- `backend/llm_service.py` — 复用 `_call_llm()`
- `frontend/src/components/ValuationHistory.vue` — 主要 UI 改动
- `frontend/src/api/index.js` — 新增 API 调用函数
