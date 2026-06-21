# 投资分析器全面升级执行计划

**日期**: 2026-06-21
**基于**: investment-analyzer-system-evaluation_2026-06-21.md

---

## Phase 1: P0 任务（本周完成）

### Task 1: Agent 首响优化
- **目标**: 规则预判替代 LLM 判断复杂度，首响时间减少 2-3 秒
- **文件**: `backend/agent/orchestrator.py`
- **方案**: 用问题长度+关键词做规则预判，只在边界情况调 LLM

### Task 2: 决策执行跟踪
- **目标**: 监听持仓变动，自动匹配待执行决策
- **文件**: `backend/db/decisions.py`, `backend/routers/decisions.py`, `frontend/src/components/DecisionRecordsPage.vue`
- **方案**: 持仓变动时检查是否有匹配的待执行决策，提示用户确认

### Task 3: 数据新鲜度监控
- **目标**: 仪表盘显示每类数据的最后更新时间、健康状态
- **文件**: 新建 `backend/routers/data_health.py`, 新建 `frontend/src/components/DataHealthDashboard.vue`
- **方案**: 检查估值/持仓/新闻/文章的最后更新时间，标红过期数据

### Task 4: 评测基准扩充
- **目标**: 50+ 标准评测用例，覆盖四大类
- **文件**: `backend/scripts/rag_eval_suite.py`, `backend/db/eval.py`
- **方案**: 构建估值分析/持仓诊断/市场解读/策略建议各 15 个用例

---

## Phase 2: P1 任务（2-3周完成）

### Task 5: 专家分析模板化
- **文件**: `backend/agent/multi_agent.py`, `backend/agent/orchestrator.py`

### Task 6: 知识图谱构建
- **文件**: 新建 `backend/db/knowledge_graph.py`, `backend/rag.py`

### Task 7: 移动端响应式适配
- **文件**: `frontend/src/components/Dashboard.vue`, `frontend/src/components/GoalBucketsPage.vue`, `frontend/src/components/DecisionRecordsPage.vue`

### Task 8: 投资组合可视化
- **文件**: `frontend/src/components/PortfolioManagement.vue`, `frontend/src/components/FamilyFinanceDashboard.vue`

### Task 9: 决策时间线视图
- **文件**: `frontend/src/components/DecisionRecordsPage.vue`

### Task 10: 用户知识反馈回流
- **文件**: `backend/db/knowledge.py`, `backend/rag.py`

---

## 执行策略

1. 每个任务用 sub-agent 独立执行
2. 每完成一个任务后验证（前端 build + 后端测试）
3. Phase 1 完成后再启动 Phase 2
