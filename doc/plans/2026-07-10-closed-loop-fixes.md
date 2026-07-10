# 系统闭环修复设计稿

> **排查日期**：2026-07-10
> **目标**：修复系统排查发现的 3 处严重断点（P0）+ 2 处中危问题（P1），恢复完整闭环

---

## P0-断点1：risk_assessor / allocation_advisor 未配置为 specialist

### 问题

`_init_wealth_specialists`（[db/agents.py:331-446](file:///Users/xiaoyuer/projects/investment-analyzer/backend/db/agents.py)）仅定义 `macro_strategist` 和 `fund_analyst` 两个 specialist。

risk_assessor / allocation_advisor / valuation_expert / market_analyst 仅以 preset 形式存在（`is_specialist=0`、`agent_key=NULL`、`tools='[]'`），**没有 query_portfolio 工具**。

**影响链**：
- `load_specialist_agents()` 查询 `WHERE is_specialist = 1`，不返回这 4 个专家
- [router.py:132-149](file:///Users/xiaoyuer/projects/investment-analyzer/backend/agent/router.py) 的 25%/30%/50% 持仓感知追加会把 `risk_assessor`/`allocation_advisor` 加入集合
- `run_specialist("risk_assessor", ...)` 在 `load_specialist_agents()[agent_key]` 处 **KeyError**
- 风险否决机制（project_memory 硬约束）无法触发

### 修复方案

在 `_init_wealth_specialists` 的 `specialists` 列表中新增 4 项定义，复用 preset 已有的 system_prompt，补上 `agent_key`、`tools`、`knowledge_scope`。

**关键约束**（project_memory）：
- risk_assessor 和 allocation_advisor 必须包含 `query_portfolio` 工具
- agent_key 必须与 router.py / multi_agent.py 中引用的一致

### 具体改动

**文件**：`backend/db/agents.py`

在 specialists 列表（line 344）中，`fund_analyst` 之后追加 4 项：

```python
        {
            "agent_key": "valuation_expert",
            "name": "估值分析师",
            "description": "专注指数估值分析，结合历史分位点、趋势变化给出投资建议",
            "icon": "chart",
            "tools": ["search_knowledge", "query_valuation", "yingmi_latest_quotations",
                      "eastmoney_finance_data"],
            "system_prompt": (复用 preset "估值分析师" 的 system_prompt，line 12-56),
            "knowledge_scope": '{"rag_types": ["valuation", "analysis", "book"], "kyc_dimensions": ["risk_tolerance", "loss_tolerance"]}',
        },
        {
            "agent_key": "market_analyst",
            "name": "市场分析师",
            "description": "分析市场情绪、资金流向、新闻资讯，提供市场动态视角",
            "icon": "research",
            "tools": ["search_knowledge", "yingmi_hot_topics", "yingmi_search_news",
                      "yingmi_latest_quotations", "eastmoney_finance_data", "query_institutional_flow"],
            "system_prompt": "## 人设\n你是市场分析师，专注市场情绪、资金流向、新闻资讯分析...",
            "knowledge_scope": '{"rag_types": ["article", "analysis"], "kyc_dimensions": ["investment_horizon"]}',
        },
        {
            "agent_key": "risk_assessor",
            "name": "风险管理师",
            "description": "专注风险评估与控制，提供回撤分析、波动率评估、止损建议",
            "icon": "shield",
            "tools": ["search_knowledge", "query_portfolio", "query_valuation",
                      "yingmi_latest_quotations", "eastmoney_finance_data"],
            "system_prompt": (复用 preset "风险管理师" 的 system_prompt，line 105-148),
            "knowledge_scope": '{"rag_types": ["valuation", "analysis", "book"], "kyc_dimensions": ["risk_tolerance", "loss_tolerance", "max_single_position_pct"]}',
        },
        {
            "agent_key": "allocation_advisor",
            "name": "资产配置师",
            "description": "专注资产配置策略，提供股债配比、行业轮动、定投策略建议",
            "icon": "pie",
            "tools": ["search_knowledge", "query_portfolio", "query_valuation",
                      "yingmi_latest_quotations", "eastmoney_finance_data"],
            "system_prompt": (复用 preset "资产配置师" 的 system_prompt，line 156-215),
            "knowledge_scope": '{"rag_types": ["valuation", "article", "book"], "kyc_dimensions": ["risk_tolerance", "investment_horizon", "capital_scale", "target_equity_ratio"]}',
        },
```

**幂等机制**：INSERT OR IGNORE 按 name 匹配（preset 已插入同名记录会跳过），UPDATE 按 name 设置 `agent_key`/`tools`/`is_specialist=1`。这样 preset 行会被提升为 specialist，同时保留 preset 标记。

**market_analyst 的 system_prompt**：preset 中没有"市场分析师"，需要新建。基于 macro_strategist 的 prompt 改写，聚焦市场情绪/资金流向/新闻资讯（非宏观周期）。

### 验证方法

```sql
-- 启动后端后执行
SELECT agent_key, name, is_specialist, tools FROM agents WHERE is_specialist = 1;
-- 应返回 6 行：macro_strategist, fund_analyst, valuation_expert, market_analyst, risk_assessor, allocation_advisor
-- risk_assessor 和 allocation_advisor 的 tools 应包含 "query_portfolio"
```

---

## P0-断点2：T+N 验证窗口锚点错误

### 问题

[db/dashboard.py:222-233](file:///Users/xiaoyuer/projects/investment-analyzer/backend/db/dashboard.py) 的 `list_pending_verification_recommendations` 基于 `verify_after_date`（创建时间 + 5×1.4 自然日）查询。

`adopted_at` 字段**未参与验证触发逻辑**，与"T+N 验证"语义不符：
- 未采纳（adopted=0/-1）的建议到达 verify_after_date 后也会被自动验证
- 用户采纳后不重新计算验证窗口

### 修复方案

改为基于 `adopted_at + verify_window_days 天` 作为验证触发锚点，且仅验证已采纳（adopted=1）的建议。

**设计决策**：只验证用户采纳的建议。未采纳的建议对用户价值低，不值得生成验证 alert。

### 具体改动

**文件**：`backend/db/dashboard.py`

替换 `list_pending_verification_recommendations` 函数（line 222-233）：

```python
def list_pending_verification_recommendations(verify_date: str) -> list[dict]:
    """列出到达验证窗口且尚未验证的建议（用于定时任务）。

    验证窗口锚点：用户采纳时间(adopted_at) + verify_window_days 天。
    仅验证已采纳(adopted=1)的建议。
    """
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM recommendations "
        "WHERE status = 'pending' AND baseline_value IS NOT NULL "
        "AND adopted = 1 AND adopted_at IS NOT NULL "
        "AND date(adopted_at, '+' || COALESCE(verify_window_days, 5) || ' days') <= ? "
        "ORDER BY adopted_at ASC",
        (verify_date,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
```

**兼容性**：
- 旧数据如果 `verify_window_days` 为 NULL，用 `COALESCE(..., 5)` 兜底为 5 天
- `adopted_at` 格式为 "YYYY-MM-DD HH:MM:SS"，SQLite `date()` 函数会正确解析
- 未采纳的建议不会被验证，避免生成无用 alert

### 验证方法

```sql
-- 检查是否有已采纳且到达验证窗口的建议
SELECT id, index_name, adopted, adopted_at, verify_window_days, status
FROM recommendations
WHERE status = 'pending' AND adopted = 1 AND adopted_at IS NOT NULL
AND date(adopted_at, '+' || COALESCE(verify_window_days, 5) || ' days') <= date('now');
```

---

## P0-断点3：验证结果通知闭环 + 前端补 alert 分支

### 问题

[alert_scanner.py:96-102](file:///Users/xiaoyuer/projects/investment-analyzer/backend/services/alert_scanner.py) 验证完成后仅写入 `portfolio_alerts` 表，无 SSE 实时推送。

前端 [useStreamingState.js:254-256](file:///Users/xiaoyuer/projects/investment-analyzer/frontend/src/composables/useStreamingState.js) 没有 `case 'alert'` 分支。

### 架构权衡

alert_scanner 是后台定时任务（30 分钟一次），运行时用户可能没有活跃的 SSE 连接。**无法直接在对话 SSE 流中推送 alert 事件**。

当前 AlertBell 60 秒轮询已构成闭环：
```
验证完成 → 写入 portfolio_alerts → AlertBell 60秒轮询 → 铃铛红点 → 用户点击查看
```

对于 T+N 验证（T=5天），60 秒延迟完全可接受。引入 WebSocket/SSE 长连接改动过大，收益低。

### 修复方案（务实方案）

1. **后端**：保持 alert 写入 portfolio_alerts 表的机制不变（已闭环）
2. **前端**：在 useStreamingState.js 补 `case 'alert'` 防御性分支，确保即使后端在对话流中附带推送 alert 也不会被静默丢弃
3. **优化**：AlertBell 轮询间隔在有活跃对话时从 60 秒缩短到 20 秒（提升验证结果触达速度）

### 具体改动

**文件1**：`frontend/src/composables/useStreamingState.js`

在 `case 'title_updated'`（line 247）之后、`default`（line 254）之前插入：

```javascript
    case 'alert':
      // 主动提醒事件（防御性分支：当前 alert 通过 AlertBell 轮询获取，
      // 此分支确保后端在对话流中附带推送 alert 时不会被静默丢弃）
      if (callbacks.onAlert) {
        callbacks.onAlert(data.alert || data, convId)
      }
      break

```

**文件2**：`frontend/src/components/AlertBell.vue`

优化轮询间隔（line 101 附近）：

```javascript
// 有活跃对话时 20 秒轮询，空闲时 60 秒
const POLL_INTERVAL_ACTIVE = 20000
const POLL_INTERVAL_IDLE = 60000

let pollInterval = POLL_INTERVAL_IDLE
const refreshUnread = async () => {
  // ... 原有逻辑
  // 根据是否有活跃对话动态调整间隔
  pollInterval = document.hasFocus() ? POLL_INTERVAL_ACTIVE : POLL_INTERVAL_IDLE
}
setInterval(refreshUnread, POLL_INTERVAL_IDLE)
```

> 简化实现：用 `document.hasFocus()` 判断页面是否活跃，活跃时 20 秒轮询，非活跃时 60 秒。避免跨组件传递对话状态。

### 验证方法

1. 手动触发扫描：`POST /api/portfolio/alerts/scan`
2. 检查 portfolio_alerts 表是否有新记录
3. 前端铃铛应在 20-60 秒内显示红点

---

## P1-问题4：续答路径缺失 reflection/debate 节点

### 问题

[pipeline.py:419-506](file:///Users/xiaoyuer/projects/investment-analyzer/backend/agent/pipeline.py) 的 `run_pipeline_from_checkpoint` 在 Phase 3 完成后直接跳到 Phase 4（line 458-469），未插入 Phase 3.5 反思和 Phase 3.7 辩论节点。

对比主路径 `run_pipeline`（line 247-309）有完整的 reflection + debate 逻辑。

### 修复方案

将主路径的 reflection + debate 逻辑复制到 checkpoint 恢复路径，保持两条路径的分析质量一致。

### 具体改动

**文件**：`backend/agent/pipeline.py`

在 `run_pipeline_from_checkpoint` 的 Phase 3 结束（line 456 `EVENT_PHASE_END`）和 Phase 4 开始（line 458 `transition_to(SYNTHESIS)`）之间，插入以下代码（从主路径 line 247-309 复制并调整）：

```python
        # ── Phase 3.5: 反思（与主路径保持一致）──────────────
        reflection_result = None
        try:
            reflection_enabled = get_config_bool("pipeline.reflection_enabled", True)
        except Exception:
            reflection_enabled = True

        if reflection_enabled and phase3_result.get("specialists"):
            state.transition_to(PipelinePhase.REFLECTION)
            yield {"type": EVENT_PHASE_START, "phase": PipelinePhase.REFLECTION.value}
            try:
                reflection_result = _phase_reflection(
                    state, query, phase3_result, blackboard, trace_id
                )
                state.set_phase_result(PipelinePhase.REFLECTION.value, reflection_result)
                yield {"type": EVENT_REFLECTION_DONE, "result": reflection_result}

                rerun_result = _maybe_rerun_specialist(
                    state, query, phase3_result, reflection_result,
                    blackboard, trace_id,
                )
                if rerun_result:
                    phase3_result["specialists"] = _replace_specialist_analysis(
                        phase3_result["specialists"], rerun_result
                    )
                    state.set_phase_result(PipelinePhase.EXECUTION.value, phase3_result)
                    yield {"type": EVENT_SPECIALIST_DONE,
                           "agent": rerun_result.get("agent", ""),
                           "result": rerun_result,
                           "rerun": True}
            except Exception as refl_err:
                logger.warning(f"[pipeline:{trace_id}] 续答 Reflection 失败，跳过: {refl_err}")
                reflection_result = None
            yield {"type": EVENT_PHASE_END, "phase": PipelinePhase.REFLECTION.value}

        # ── Phase 3.7: 对抗式辩论（冲突时触发）──────────────
        debate_result = None
        try:
            debate_enabled = get_config_bool("pipeline.debate_enabled", True)
        except Exception:
            debate_enabled = True

        if debate_enabled and phase3_result.get("specialists"):
            conflicts = blackboard.find_conflicts() if blackboard else []
            if conflicts:
                logger.info(f"[pipeline:{trace_id}] 续答检测到 {len(conflicts)} 个冲突，触发辩论")
                state.transition_to(PipelinePhase.DEBATE)
                yield {"type": EVENT_PHASE_START, "phase": PipelinePhase.DEBATE.value}
                try:
                    debate_result = _phase_debate(
                        state, query, phase3_result, blackboard, trace_id
                    )
                    state.set_phase_result(PipelinePhase.DEBATE.value, debate_result)
                    yield {"type": EVENT_DEBATE_DONE, "result": debate_result}
                except Exception as debate_err:
                    logger.warning(f"[pipeline:{trace_id}] 续答辩论失败，跳过: {debate_err}")
                    debate_result = None
                yield {"type": EVENT_PHASE_END, "phase": PipelinePhase.DEBATE.value}

```

同时需要将 `phase4_result` 的调用补充 `reflection_result` 和 `debate_result` 参数（line 469-471）：

```python
        phase4_result = _phase_synthesis(
            state, state.refined_query, phase3_result, blackboard, trace_id,
            reflection_result=reflection_result,
            debate_result=debate_result,
        )
```

### 验证方法

触发一次澄清流程（提问模糊问题 → 回答澄清选项），检查日志是否出现 `[pipeline:xxx] 续答 Reflection` 或 `续答检测到 N 个冲突`。

---

## P1-问题5：修复自纠错重跑写黑板 2 个 bug

### 问题

[pipeline.py:1619-1621](file:///Users/xiaoyuer/projects/investment-analyzer/backend/agent/pipeline.py) 的 `_maybe_rerun_specialist` 中：

1. **参数顺序错误**（line 1620）：
   ```python
   entry = extract_entry_from_result(result, agent_key, agent_name)
   ```
   正确签名是 `extract_entry_from_result(agent_key, agent_name, result, ...)`（[blackboard.py:304-310](file:///Users/xiaoyuer/projects/investment-analyzer/backend/agent/blackboard.py)）

2. **方法名错误**（line 1621）：
   ```python
   blackboard.add_entry(entry)
   ```
   `Blackboard` 类没有 `add_entry` 方法，正确方法是 `write(entry)`（[blackboard.py:100](file:///Users/xiaoyuer/projects/investment-analyzer/backend/agent/blackboard.py)）

外层 try/except 静默吞掉异常，重跑结果无法写入黑板，后续专家/综合阶段看不到重跑结论。

### 修复方案

修正参数顺序和方法名。

### 具体改动

**文件**：`backend/agent/pipeline.py`

替换 line 1619-1623：

```python
                try:
                    from agent.blackboard import extract_entry_from_result
                    entry = extract_entry_from_result(agent_key, agent_name, result)
                    blackboard.write(entry)
                except Exception as e:
                    logger.warning(f"[pipeline:{trace_id}] 重跑结果写黑板失败: {e}")
```

**改动点**：
1. `extract_entry_from_result(result, agent_key, agent_name)` → `extract_entry_from_result(agent_key, agent_name, result)`
2. `blackboard.add_entry(entry)` → `blackboard.write(entry)`
3. 日志级别从 `debug` 提升到 `warning`（这个 bug 之前被 debug 级别静默吞掉，提升级别便于发现问题）

### 验证方法

触发一次低置信度的专家分析（如故意问一个数据不充分的问题），检查日志是否出现 `重跑结果写黑板失败`。如果没有此 warning 且重跑成功，说明修复生效。

---

## P2 问题（本次不修复，记录待办）

| 编号 | 问题 | 位置 | 影响 |
|------|------|------|------|
| 6 | 风险否决缺少代码层硬兜底 | pipeline.py:1741-1754 | LLM 可能不遵守 prompt 级降级指令 |
| 7 | RAG 子查询硬截断为 3 个 | pipeline.py:616 | 策略视角子查询被丢弃 |
| 8 | 结论保存 confidence 硬编码 0.7 | pipeline.py:2575 | 数据精度损失 |
| 9 | alerts/scan 路由重复定义 | portfolio.py:325/351 | 死代码 |
| 10 | 黑板重置冗余 | pipeline.py:153/413 | 无害但冗余 |

---

## 执行顺序

1. P0-断点1（specialist 配置）→ 重启后端验证
2. P0-断点2（T+N 验证锚点）→ SQL 验证
3. P0-断点3（前端 alert 分支 + 轮询优化）→ 构建前端
4. P1-问题5（写黑板 bug，改动小）→ 先修
5. P1-问题4（续答路径 reflection/debate，改动大）→ 后修
6. 构建前端 + 重启后端，全量验证
