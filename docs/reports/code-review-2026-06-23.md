# 全项目审查报告 — 2026-06-23

## 扫描方式
三路并行 sub-agent 扫描：前端、后端、测试覆盖。

## 审查结果

### 后端 Bug（19个）
- **Critical**: 3个（经核实多为误报，实际影响低）
  - `match_pending_decisions` — row_factory 已设置，不崩溃
  - `get_decision_lineage` — track_sources 从未调用，功能空转
  - LLM API Key — 环境变量已正确配置
- **High**: 6个（同步阻塞、连接泄漏、无文件大小限制等）
- **Medium**: 7个（N+1查询、LIKE未转义等）

### 前端 Bug（22个）
- **P0**: 4个（多为旧代码扫描误报，当前代码已修复）
  - SimplePieChart.vue 已不存在
  - API_BASE 已用 `/api` 相对路径
- **P1**: 7个
- **P2**: 11个

### 测试覆盖：从 0% → 35 个用例
- conftest.py: 使用真实 init_db() 初始化临时数据库
- test_db_portfolio.py: 15 个用例
- test_db_watchlist.py: 13 个用例
- test_db_health_score.py: 7 个用例
- 全部通过

### 移动端适配
- HealthScore.vue: 增加 480px/768px 断点
- PortfolioManagement.vue: 已有完整移动端 CSS（table溢出、form单列、tabs滚动等）

## 待后续处理
- 补充 Router 层测试（API 端点）
- 后端 High 级别问题修复（连接泄漏、同步阻塞）
- 前端 P1 问题修复
