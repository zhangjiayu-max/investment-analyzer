# Agent Harness 工程化升级方案

> 日期：2026-05-31 | 状态：待审批 | 参考：CMU/Yale/JHU《Agent Harness Engineering: A Survey》ETCLOVG 七层框架

## 一、目标

基于 Agent Harness Engineering 综述的 ETCLOVG 七层框架，对投资分析系统进行工程化升级：

```
当前状态（Harness 零散）          目标状态（Harness 体系化）
┌─────────────────────┐          ┌─────────────────────┐
│  模型 + 工具 + RAG   │          │  完整 Harness 体系   │
│  + 反馈 + 评测       │    →     │  ETCLOVG 七层全覆盖  │
│  （部分层有，部分缺失）│          │  trace-native 评估   │
└─────────────────────┘          └─────────────────────┘
```

核心价值：**同一个模型，换一套执行外壳，表现可以完全不一样**。

## 二、ETCLOVG 七层现状盘点

| 层级 | 能力 | 状态 | 位置 | 本次改进 |
|------|------|------|------|---------|
| **Execution** | 执行环境 | ⚠️ 无超时保护 | `execute_tool()` | ✅ 工具级超时 |
| **Tooling** | 工具接口 | ⚠️ 无审计日志 | `tools/__init__.py` | ✅ 工具调用审计 |
| **Context** | 上下文管理 | ✅ 较完善 | `rag.py` / `orchestrator.py` | ⚠️ 动态上下文策略（后续） |
| **Lifecycle** | 生命周期编排 | ✅ 多Agent协作 | `orchestrator.py` | ✅ 编排配置化 |
| **Observability** | 可观测性 | ⚠️ 分散日志 | 多表 | ✅ trace_id 全链路追踪 |
| **Verification** | 验证评估 | ⚠️ 无自动验证 | `eval.py` | ✅ 失败归因 + 质量指标 |
| **Governance** | 治理安全 | ⚠️ 无输出审核 | - | ✅ 输出审核层 |

## 三、功能设计

### 3.1 Trace 全链路追踪（Observability 层）

**问题**：当前 `agent_runs`、`rag_logs`、`token_usage` 各自独立，无法追溯一次对话的完整执行链路。

**设计**：引入 `trace_id` 关联一次对话的所有事件。

```
用户输入 → trace_id 生成
  ├── clarification (agent_runs, trace_id)
  ├── RAG 检索 (rag_logs, trace_id)
  ├── orchestrator 调用 (agent_runs, trace_id)
  ├── specialist_1 (agent_runs, trace_id)
  ├── specialist_2 (agent_runs, trace_id)
  ├── cross_review (agent_runs, trace_id)
  └── 最终回答 (messages, trace_id)
```

**数据库变更**（`db/core.py` `init_db()`）：

```sql
-- agent_runs 表增加 trace_id
ALTER TABLE agent_runs ADD COLUMN trace_id TEXT DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_agent_runs_trace ON agent_runs(trace_id);

-- rag_logs 表增加 trace_id
ALTER TABLE rag_logs ADD COLUMN trace_id TEXT DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_rag_logs_trace ON rag_logs(trace_id);

-- token_usage 表增加 trace_id
ALTER TABLE token_usage ADD COLUMN trace_id TEXT DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_token_usage_trace ON token_usage(trace_id);

-- 新增 trace 表（记录整条链路的元数据）
CREATE TABLE IF NOT EXISTS execution_traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id TEXT UNIQUE NOT NULL,
    conversation_id INTEGER,
    query TEXT,
    complexity TEXT,
    status TEXT DEFAULT 'running',  -- running / completed / failed / cancelled
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP,
    total_ms INTEGER,
    phase_timings TEXT,  -- JSON
    quality_metrics TEXT,  -- JSON: rag_coverage, tool_success_rate, specialist_consensus
    error_category TEXT,  -- model_error / tool_error / rag_miss / timeout / none
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**后端变更**：

1. `backend/routers/conversations.py` — `event_stream()` 开始时生成 `trace_id`：
   ```python
   import uuid
   trace_id = str(uuid.uuid4())[:12]
   # 所有 create_agent_run / log_rag_search 调用都传入 trace_id
   ```

2. `backend/db/agents.py` — `create_agent_run()` 增加 `trace_id` 参数

3. `backend/rag.py` — `log_rag_search()` 增加 `trace_id` 参数

4. `backend/routers/conversations.py` — `done` 事件中写入 `execution_traces` 表

**前端变更**：

- `ChatView.vue` 执行过程面板中显示 `trace_id`（可点击复制）
- 新增 API：`GET /api/conversation/{convId}/trace/{trace_id}` — 返回完整执行链路

---

### 3.2 工具调用超时保护（Execution 层）

**问题**：`execute_tool()` 没有单次超时保护，一个工具卡住会阻塞整个 Agent。

**设计**：给每个工具调用加独立超时。

**文件**：`backend/tools/__init__.py`

```python
import signal
from contextlib import contextmanager

TOOL_TIMEOUT_SECONDS = 30  # 默认工具超时

@contextmanager
def tool_timeout(seconds=TOOL_TIMEOUT_SECONDS):
    """工具调用超时上下文管理器。"""
    def timeout_handler(signum, frame):
        raise TimeoutError(f"工具执行超时 ({seconds}s)")
    old_handler = signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)

def execute_tool(tool_name: str, arguments: dict) -> dict:
    """执行工具，带超时保护。"""
    try:
        with tool_timeout():
            # ... 现有分发逻辑 ...
    except TimeoutError as e:
        return {"error": str(e), "error_category": "timeout"}
    except Exception as e:
        return {"error": str(e), "error_category": "tool_error"}
```

**注意**：`signal.SIGALRM` 在主线程外不可用，改用 `threading.Timer` 方案：

```python
import threading

def execute_tool_with_timeout(tool_name: str, arguments: dict, timeout: int = 30) -> dict:
    """在独立线程中执行工具，带超时保护。"""
    result = [None]
    error = [None]

    def _run():
        try:
            result[0] = _execute_tool_impl(tool_name, arguments)
        except Exception as e:
            error[0] = e

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=timeout)

    if t.is_alive():
        return {"error": f"工具 {tool_name} 执行超时 ({timeout}s)", "error_category": "timeout"}
    if error[0]:
        return {"error": str(error[0]), "error_category": "tool_error"}
    return result[0]
```

---

### 3.3 工具调用审计日志（Tooling 层）

**问题**：工具调用没有独立审计日志，无法分析工具质量和失败模式。

**设计**：新增 `tool_audit_logs` 表，记录每次工具调用的输入/输出/耗时。

**数据库变更**：

```sql
CREATE TABLE IF NOT EXISTS tool_audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id TEXT,
    tool_name TEXT NOT NULL,
    arguments TEXT,  -- JSON
    result_preview TEXT,  -- 结果前 500 字符
    success INTEGER DEFAULT 1,  -- 1=成功, 0=失败
    error_category TEXT,  -- none / timeout / tool_error / model_error
    duration_ms INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_tool_audit_trace ON tool_audit_logs(trace_id);
CREATE INDEX IF NOT EXISTS idx_tool_audit_name ON tool_audit_logs(tool_name);
```

**后端变更**：在 `execute_tool()` 中自动记录：

```python
def execute_tool(tool_name: str, arguments: dict, trace_id: str = "") -> dict:
    t0 = time.time()
    result = _execute_tool_impl(tool_name, arguments)
    duration_ms = int((time.time() - t0) * 1000)

    # 审计日志
    log_tool_audit(
        trace_id=trace_id,
        tool_name=tool_name,
        arguments=arguments,
        result_preview=str(result)[:500],
        success=1 if "error" not in result else 0,
        error_category=result.get("error_category", "none"),
        duration_ms=duration_ms,
    )
    return result
```

---

### 3.4 失败归因机制（Verification 层）

**问题**：当前错误只记录 `error_message`，无法区分是模型错了、工具错了、还是 RAG 检索不到。

**设计**：在 `done` / `error` 事件中增加 `error_category` 和 `quality_metrics`。

**错误分类**：

| 类别 | 含义 | 触发条件 |
|------|------|---------|
| `none` | 正常完成 | 无错误 |
| `model_error` | 模型输出异常 | LLM 返回空/格式错误/幻觉 |
| `tool_error` | 工具执行失败 | 工具返回 error |
| `rag_miss` | RAG 检索无结果 | `rag_result.results` 为空 |
| `timeout` | 执行超时 | 超过全局超时 |
| `cancelled` | 用户取消 | 用户点击停止 |
| `token_budget` | Token 预算超限 | 日用量超限 |

**质量指标**（在 `done` 事件中计算）：

```python
def _calculate_quality_metrics(specialist_results, rag_result, tool_calls):
    """计算本次执行的质量指标。"""
    metrics = {
        # RAG 覆盖率：RAG 检索是否找到相关信息
        "rag_coverage": 1.0 if rag_result.get("results") else 0.0,
        # 工具成功率：工具调用成功比例
        "tool_success_rate": (
            sum(1 for tc in tool_calls if "error" not in tc) / len(tool_calls)
            if tool_calls else 1.0
        ),
        # 专家参与度：完成分析的专家比例
        "specialist_completion": len([
            s for s in specialist_results
            if s.get("analysis") and len(s["analysis"]) > 50
        ]) / max(len(specialist_results), 1),
        # 专家共识度：是否存在分歧（简化版）
        "specialist_consensus": 1.0 if len(specialist_results) <= 1 else (
            0.0 if _detect_specialist_disagreement(specialist_results) else 1.0
        ),
    }
    return metrics
```

**存储**：写入 `execution_traces` 表的 `quality_metrics` 字段（JSON）。

---

### 3.5 输出审核层（Governance 层）

**问题**：LLM 可能给出不合规的投资建议（如"保证收益"、"稳赚不赔"）。

**设计**：在最终输出前增加审核层，检测敏感内容并自动附加风险提示。

**文件**：新增 `backend/output_reviewer.py`

```python
import re

# 绝对化用语（金融合规）
ABSOLUTE_PATTERNS = [
    r"保证[收盈赚]", r"稳赚", r"包赚", r"零风险", r"无风险",
    r"一定[会涨能赚]", r"必定", r"肯定[涨赚]",
    r"100%[收回]", r"翻倍", r"暴涨",
]

# 风险提示模板
RISK_DISCLAIMER = """
---
⚠️ **风险提示**：以上分析仅供参考，不构成投资建议。投资有风险，入市需谨慎。过往业绩不代表未来表现，请根据自身风险承受能力做出决策。
"""

def review_output(content: str, specialist_results: list = None) -> dict:
    """审核 LLM 输出，返回审核结果。

    Returns:
        {"approved": bool, "warnings": [...], "content": str}
    """
    warnings = []

    # 检测绝对化用语
    for pattern in ABSOLUTE_PATTERNS:
        matches = re.findall(pattern, content)
        if matches:
            warnings.append(f"检测到绝对化用语：{'、'.join(matches)}")

    # 检测是否缺少风险提示
    has_disclaimer = any(kw in content for kw in ["风险提示", "风险提醒", "不构成投资建议", "投资有风险"])

    # 自动附加风险提示（如果涉及投资建议且没有）
    is_advice = any(kw in content for kw in ["建议", "推荐", "买入", "卖出", "加仓", "减仓", "配置"])
    if is_advice and not has_disclaimer:
        content = content.rstrip() + RISK_DISCLAIMER

    return {
        "approved": len(warnings) == 0,
        "warnings": warnings,
        "content": content,
    }
```

**集成位置**：`backend/routers/conversations.py`，在发送 `answer` 事件前调用：

```python
from output_reviewer import review_output

# 在 answer 事件前审核
review = review_output(answer, specialist_results)
if review["warnings"]:
    logger.warning(f"输出审核警告: {review['warnings']}")
answer = review["content"]  # 可能附加了风险提示
```

---

### 3.6 编排配置化（Lifecycle 层）

**问题**：交叉审阅触发条件、仲裁策略等硬编码在 `orchestrator.py`。

**设计**：将编排策略抽成可配置项，通过 `analysis_agents` 表管理。

**新增配置项**（`analysis_agents` 表或新建 `orchestration_config` 表）：

```sql
CREATE TABLE IF NOT EXISTS orchestration_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    description TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 初始配置
INSERT OR REPLACE INTO orchestration_config VALUES
('cross_review_enabled', 'true', '是否启用交叉审阅'),
('cross_review_min_specialists', '2', '触发交叉审阅的最少专家数'),
('cross_review_trigger', 'disagreement', '触发条件: always / disagreement / never'),
('arbitration_enabled', 'true', '是否启用仲裁'),
('arbitration_complexity', 'complex', '仲裁触发的最低复杂度'),
('max_turns', '6', 'orchestrator 最大轮次'),
('max_tool_timeout', '30', '工具调用超时秒数');
```

**后端变更**：`orchestrator.py` 从数据库读取配置：

```python
def get_orchestration_config(key: str, default=None):
    """从数据库读取编排配置。"""
    from db import _get_conn
    conn = _get_conn()
    row = conn.execute("SELECT value FROM orchestration_config WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default
```

---

## 四、实施计划

### Phase 1: 基础设施（trace_id + 工具超时）

| 任务 | 工作量 | 优先级 |
|------|--------|--------|
| `execution_traces` 表 + `trace_id` 生成 | 2h | P0 |
| `agent_runs` / `rag_logs` / `token_usage` 增加 `trace_id` 字段 | 1h | P0 |
| 工具调用超时保护（`execute_tool_with_timeout`） | 2h | P0 |
| `done` 事件写入 `execution_traces` | 1h | P0 |

### Phase 2: 质量可观测（审计 + 归因 + 指标）

| 任务 | 工作量 | 优先级 |
|------|--------|--------|
| `tool_audit_logs` 表 + 审计记录 | 2h | P1 |
| 失败归因（`error_category` 分类） | 1h | P1 |
| 质量指标计算（`quality_metrics`） | 2h | P1 |
| trace 查询 API + 前端展示 | 3h | P1 |

### Phase 3: 治理与配置（审核 + 编排配置化）

| 任务 | 工作量 | 优先级 |
|------|--------|--------|
| 输出审核层（`output_reviewer.py`） | 2h | P2 |
| `orchestration_config` 表 + 配置读取 | 2h | P2 |
| 编排策略从硬编码改为配置驱动 | 3h | P2 |
| 审核结果展示（前端标记） | 1h | P2 |

### Phase 4: 前端可视化

| 任务 | 工作量 | 优先级 |
|------|--------|--------|
| Trace 详情页面（完整执行链路可视化） | 4h | P2 |
| 质量指标仪表盘（rag_coverage / tool_success_rate 等） | 3h | P3 |
| 工具审计日志查询页面 | 2h | P3 |

---

## 五、验收标准

1. **Trace 全链路**：发送一条消息后，能在 `execution_traces` 表中找到完整的执行链路，包括每个阶段的耗时和状态
2. **工具超时**：模拟一个耗时超过 30s 的工具调用，验证超时后返回错误而不是阻塞
3. **失败归因**：在 `error` 事件中能看到 `error_category`（model_error / tool_error / rag_miss / timeout）
4. **质量指标**：`done` 事件包含 `quality_metrics`（rag_coverage, tool_success_rate, specialist_consensus）
5. **输出审核**：发送涉及投资建议的问题，验证回答自动附加风险提示
6. **编排配置**：修改 `orchestration_config` 表中的 `cross_review_enabled` 为 `false`，验证交叉审阅被跳过

---

## 六、风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| `trace_id` 增加数据库写入量 | 存储增长 | `execution_traces` 表定期清理（保留 30 天） |
| 工具超时误杀正常长任务 | 功能异常 | 超时时间可配置，特殊工具可设更长超时 |
| 输出审核过于严格 | 误报率高 | 先以日志记录为主，不阻断输出，逐步收紧 |
| 编排配置化增加复杂度 | 维护成本 | 保留硬编码默认值，配置只覆盖需要调整的项 |
