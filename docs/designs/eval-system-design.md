# 投资分析器 — Agent 质量闭环评测系统设计稿

## 1. 目标

建立一套**自动化、可量化、可持续**的 Agent 输出质量评测体系，实现：

- 每次修改提示词，能量化"变好了还是变差了"
- 每日自动评测，质量下降自动报警
- 用户反馈能归因到具体问题，形成修复闭环
- Prompt 版本化管理，支持 A/B 测试

## 2. 系统架构

```
┌──────────────────────────────────────────────────────┐
│                    质量闭环系统                        │
│                                                      │
│  ┌─────────┐    ┌──────────┐    ┌──────────────┐     │
│  │ 测试集  │───→│ LLM Judge│───→│ 评分存储 DB  │     │
│  │管理     │    │ 打分引擎 │    │              │     │
│  └─────────┘    └──────────┘    └──────┬───────┘     │
│                                        │             │
│       ┌────────────────────────────────┼──────┐      │
│       │                                │      │      │
│  ┌────▼────┐   ┌──────────┐   ┌───────▼───┐  │      │
│  │Prompt   │   │ A/B 测试 │   │ 质量趋势  │  │      │
│  │版本管理 │   │ 框架     │   │ Dashboard │  │      │
│  └─────────┘   └──────────┘   └───────────┘  │      │
│                                               │      │
│  ┌──────────────────────────────────────────┐ │      │
│  │           用户反馈闭环                    │ │      │
│  │  反馈 → 归因 → 修复 → 回归验证           │ │      │
│  └──────────────────────────────────────────┘ │      │
└──────────────────────────────────────────────────────┘
```

## 3. 数据模型

### 3.1 评测用例表 `eval_cases`

```sql
CREATE TABLE eval_cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_type TEXT NOT NULL,           -- panorama/deepdive/fee/correlation/trade-review
    case_name TEXT NOT NULL,           -- 用例名称，如"高集中度持仓诊断"
    portfolio_context TEXT NOT NULL,   -- JSON: 持仓数据快照
    expected_behavior TEXT,            -- 期望行为描述（给 judge 参考）
    tags TEXT DEFAULT '',              -- 标签，如 "high-concentration,bond-heavy"
    is_active INTEGER DEFAULT 1,      -- 是否启用
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);
```

### 3.2 评测结果表 `eval_results`

```sql
CREATE TABLE eval_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL,
    prompt_version TEXT NOT NULL,      -- 提示词版本，如 "panorama_v3"
    model TEXT NOT NULL,               -- 使用的模型
    agent_output TEXT NOT NULL,        -- Agent 实际输出
    -- 评分维度（1-5分）
    score_data_accuracy INTEGER,       -- 数据准确性
    score_actionability INTEGER,       -- 建议可操作性
    score_risk_warning INTEGER,        -- 风险提示充分度
    score_hallucination INTEGER,       -- 幻觉程度（1=严重幻觉，5=无幻觉）
    score_format INTEGER,              -- 格式规范性
    score_total INTEGER,               -- 总分
    judge_comments TEXT,               -- 评审意见
    judge_model TEXT,                  -- 评审模型
    run_mode TEXT DEFAULT 'manual',    -- manual/daily/auto
    created_at TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (case_id) REFERENCES eval_cases(id)
);
```

### 3.3 提示词版本表 `prompt_versions`

```sql
CREATE TABLE prompt_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_type TEXT NOT NULL,          -- panorama/deepdive/fee/correlation 等
    version TEXT NOT NULL,             -- v1/v2/v3...
    prompt_content TEXT NOT NULL,      -- 提示词全文
    changelog TEXT DEFAULT '',         -- 变更说明
    is_active INTEGER DEFAULT 0,      -- 是否为当前使用版本
    avg_score REAL DEFAULT 0,         -- 该版本平均分
    eval_count INTEGER DEFAULT 0,     -- 评测次数
    created_at TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(agent_type, version)
);
```

### 3.4 质量日报表 `eval_daily_reports`

```sql
CREATE TABLE eval_daily_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date TEXT NOT NULL UNIQUE,  -- 日期 YYYY-MM-DD
    total_cases INTEGER,               -- 评测用例数
    avg_score REAL,                    -- 平均总分
    scores_by_type TEXT,               -- JSON: 各类型分数
    score_trend TEXT,                   -- up/down/stable vs 前一天
    alerts TEXT,                        -- JSON: 触发的告警
    recommendations TEXT,              -- 改进建议
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
```

## 4. 核心模块

### 4.1 LLM-as-Judge 引擎

**评审 Prompt 模板（通用）：**

```
你是投资分析质量评审专家。请严格按以下维度给这段分析打分。

评分维度（每项 1-5 分）：
1. 数据准确性 — 引用的数据是否真实可验证，有无编造
2. 建议可操作性 — 用户看完能不能直接行动，是否有具体操作步骤
3. 风险提示 — 是否充分提示风险，有无盲目乐观
4. 幻觉程度 — 是否编造不存在的数据/基金/结论（1=严重幻觉，5=无幻觉）
5. 格式规范 — 结构是否清晰，是否便于阅读

评测用例类型：{case_type}
期望行为：{expected_behavior}

持仓背景：
{portfolio_context}

Agent 输出：
{agent_output}

请严格按 JSON 格式输出：
{
  "score_data_accuracy": 4,
  "score_actionability": 3,
  "score_risk_warning": 5,
  "score_hallucination": 4,
  "score_format": 4,
  "score_total": 20,
  "judge_comments": "具体评审意见...",
  "issues_found": ["问题1", "问题2"],
  "improvement_suggestions": ["建议1", "建议2"]
}
```

### 4.2 评测执行流程

```
输入: eval_case + prompt_version
  ↓
1. 从 prompt_versions 表获取提示词
2. 组装: 提示词 + portfolio_context → 调用目标模型
3. 获取 agent_output
4. 调用 LLM-as-Judge 打分
5. 存入 eval_results
6. 更新 prompt_versions.avg_score
7. 返回评分结果
```

### 4.3 每日自动评测 Pipeline

```
Cron: 每天 03:00
  ↓
1. 从 eval_cases 中随机抽 5 条 is_active=1 的用例
2. 对当前活跃的 prompt_version 执行评测
3. 计算当日平均分
4. 与前日对比:
   - 下降 > 0.5 分 → 触发告警
   - 上升 > 0.5 分 → 记录改进
5. 生成 eval_daily_report
6. 可选: 推送摘要到用户
```

### 4.4 A/B 测试流程

```
输入: agent_type + new_prompt_content
  ↓
1. 创建新 prompt_version (is_active=0)
2. 对 eval_cases 中该类型的所有用例执行:
   - 用当前 active 版本跑一遍
   - 用新版本跑一遍
3. 对比两组评分:
   - 新版胜率 > 60% → 建议上线
   - 新版胜率 < 40% → 建议回滚
   - 中间地带 → 建议继续迭代
4. 输出 A/B 测试报告
```

### 4.5 用户反馈归因

扩展现有 `bad_cases` 表，新增字段：

```sql
ALTER TABLE bad_cases ADD COLUMN root_cause TEXT DEFAULT '';
-- root_cause 枚举: data_error/hallucination/too_generic/format_issue/missing_context/other

ALTER TABLE bad_cases ADD COLUMN prompt_version TEXT DEFAULT '';
-- 记录出问题时使用的 prompt 版本

ALTER TABLE bad_cases ADD COLUMN fix_applied TEXT DEFAULT '';
-- 记录修复措施

ALTER TABLE bad_cases ADD COLUMN verified INTEGER DEFAULT 0;
-- 是否已通过回归验证
```

## 5. API 设计

### 5.1 评测用例管理

```
POST   /api/eval/cases              创建用例
GET    /api/eval/cases              列出用例
PUT    /api/eval/cases/{id}         更新用例
DELETE /api/eval/cases/{id}         删除用例
```

### 5.2 评测执行

```
POST   /api/eval/run                执行单条评测
POST   /api/eval/run-batch          批量评测（A/B 测试）
POST   /api/eval/daily              手动触发每日评测
GET    /api/eval/results            查询评测结果
GET    /api/eval/results/{id}       查询单条详情
```

### 5.3 提示词版本管理

```
POST   /api/eval/prompts            创建新版本
GET    /api/eval/prompts            列出所有版本
PUT    /api/eval/prompts/{id}/activate  激活某版本
GET    /api/eval/prompts/{id}/compare   A/B 对比
```

### 5.4 质量日报

```
GET    /api/eval/daily-reports      查询日报列表
GET    /api/eval/daily-reports/{date}  查询某天日报
GET    /api/eval/trends             质量趋势图数据
```

## 6. 前端设计

### 6.1 质量看板页面

- **质量趋势图**：最近 30 天的评分折线图（按分析类型分色）
- **各维度雷达图**：5 个维度的平均分
- **最新日报**：今日评测摘要 + 告警
- **Top 问题**：最常扣分的维度

### 6.2 评测用例管理

- 用例列表（支持按类型筛选）
- 新建/编辑用例
- 一键执行单条评测

### 6.3 Prompt 版本管理

- 版本列表（按 agent_type 分组）
- A/B 测试入口
- 版本对比视图

## 7. 实施计划

| 阶段 | 内容 | 预计时间 |
|------|------|----------|
| P0 | DB 建表 + LLM Judge 引擎 + 评测执行 API | 2h |
| P1 | 每日评测 Pipeline + 质量日报 API | 1h |
| P2 | Prompt 版本管理 + A/B 测试 | 1.5h |
| P3 | 用户反馈归因扩表 + 闭环流程 | 1h |
| P4 | 前端质量看板 + 用例管理 | 2h |
| P5 | 初始测试集构建（20 条真实用例） | 1h |

**总计约 8.5 小时，分 2-3 天完成。**

## 8. 成功指标

- 每日评测覆盖 5+ 用例，评分趋势可追踪
- Prompt 修改有据可查，每次改动能对比前后分数
- 用户反馈能归因到具体问题类型
- 质量下降 > 0.5 分时自动告警
- 30 天内各分析类型平均分提升 ≥ 1 分
