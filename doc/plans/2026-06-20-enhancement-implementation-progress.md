# 投资助手增强实现进度

> 日期：2026-06-20  
> 来源：`2026-06-20-investment-assistant-enhancement-blueprint.md`

## 已完成批次

### 1. 决策闭环 MVP

- 对话中的 AI 回复可保存为决策草案。
- 决策草案包含证据快照、反方观点、适当性检查、行动项和复盘日期。
- 决策前检查支持画像约束：备用金、月结余、目标权益仓位、资金来源。
- 决策复盘会沉淀为 `user_lesson` 知识原子。
- 低质量复盘会生成 eval case，进入评测飞轮。

### 2. 个人财务画像 2.0

- 用户画像新增现金流、备用金、目标权益仓位、单标仓位上限、主要目标、资金用途、流动性需求、负债摘要、行为偏差。
- 画像 API 支持新增字段读写。
- RAG 个性化重排会读取资金用途、目标和行为偏差。

### 3. 目标账户 / 资金桶

- 新增 `goal_buckets` 表和 CRUD。
- 新增 `/api/profile/buckets` 系列 API。
- 新增前端“资金桶”页面。
- 决策预检查会识别 `source_bucket_id`，使用备用金桶买入/加仓会被阻断。

### 4. 组合偏离和再平衡驾驶舱

- 新增 `/api/portfolio/allocation-dashboard`。
- 基于已有 `rebalancer.py` 输出当前配置、目标配置、偏离度、建议路径。
- 新增前端“配置偏离”页面。
- 输出会合并资金桶约束，例如备用金未达标提示。

### 5. 行为教练与反方观点

- 现有 `behavior_coach` 已纳入关键词路由。
- 新增 `counter_argument` 反方观点审查员。
- 追涨、恐慌、重仓、清仓、买入、卖出等高风险场景会自动加入行为教练、反方观点和风险专家。
- 场景化 RAG 增加行为金融、反例、失效条件、风险边界关键词。

### 6. RAG 证据化和个人化重排

- 知识库扩展知识原子字段：`atom_type`、`evidence_level`、`as_of_date`、`valid_until`、`limitations`、`counterpoints`。
- RAG 默认排序会使用关注资产、资金用途、主要目标、行为偏差、知识原子类型、反方观点等信号。
- 个性化加权会输出 `personal_boost` 与 `personal_reasons`，便于后续观测。

### 7. 确定性压力测试

- 新增 `/api/portfolio/stress-test`。
- 支持市场下跌、利率上行、流动性冲击等确定性情景。
- 输出资产类别冲击、预计损失、回撤比例、风险等级、备用金缓冲提示。
- 该能力不依赖 LLM，可用于决策前硬检查。

## 顺手修复

- 修复 `portfolio_holdings` 唯一约束迁移重建时丢失扩展字段的问题。
- 修复 `portfolio_cash` 新表缺少 `last_interest_date` / `today_interest` 的迁移顺序问题。
- 修复 `dashboard.py` 使用 `json.dumps` 但未导入 `json` 的问题。
- 修复 `/api/portfolio/*` 动态路由抢占新静态路由的问题。

## 仍建议后续继续推进

- 家庭财务仪表盘：把净值、现金流、负债、目标进度、配置偏离、压力测试放到统一视图。
- 策略沙盒和回测：对定投、再平衡、估值分位买卖规则做历史模拟。
- 多模型评审：关键决策由多个模型或多套 prompt 交叉验证。
- 压力测试前端入口：当前已提供 API，后续可接入“配置偏离”或“持仓管理”页面。
- RAG 个性化观测页：展示哪些画像信号影响了召回排序。

## 验证命令

```bash
PYTHONPATH=backend:. python3 -m unittest tests.test_stress_test tests.test_rag_personalization tests.test_behavior_counter_agents tests.test_allocation_dashboard tests.test_goal_buckets tests.test_decisions tests.test_knowledge tests.test_orchestrator_scenarios
cd frontend && npm test -- --run
cd frontend && npm run build
```
