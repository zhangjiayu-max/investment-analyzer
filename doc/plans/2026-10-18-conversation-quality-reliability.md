# 对话质量与恢复可靠性修复计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 修复对话 202 的 SSE、截断回答、恢复状态、持仓快照、交易统计、反思审查和结构化动作问题，确保失败或不完整结果不会被标记为成功。

**Architecture:** 先在 LLM 完成层建立有界续写与截断状态，再让 pipeline、恢复器和 SSE 共用统一的最终化状态机。持仓与交易数据由只读快照提供给各阶段；专家结果使用可恢复的 JSON 结构，反思与仲裁失败时保守降级并保留证据。

**Tech Stack:** Python 3.11+、FastAPI、SQLite、pytest、Vue 3。

---

### Task 1: SSE 与文本完成保护

- [ ] 为截断、空内容和重复截断补回归测试。
- [ ] 把同步/异步文本续写逻辑统一到 LLM 服务层，保留 reasoning_content。
- [ ] pipeline 合成识别 finish_reason=length，有限续写失败时返回降级状态并记录原因。

### Task 2: 恢复与最终化状态机

- [ ] 测试恢复不能覆盖 cancelled、必须写 answer/done/recovered 事件且通道状态一致。
- [ ] 添加统一、幂等的原子最终化函数，恢复和正常流都调用。
- [ ] 处理外层 SSE 断开与后台 worker 生命周期分离。

### Task 3: 统一投资组合快照

- [ ] 测试家庭现金、投资资产/总资产分母、跨账户基金聚合和债券指数分类。
- [ ] 添加带 as_of、freshness、counts、fund aggregation 的只读快照。
- [ ] 让 portfolio context/fact layer/约束使用同一快照并读取 KYC 配置。

### Task 4: 交易、记忆和指标真实性

- [ ] 测试交易先过滤状态/日期/系统记录再 limit，并分离 confirmed 与 pending。
- [ ] 修复近期记忆的用户、时间和来源标记。
- [ ] 指标只接受价格/NAV/收益序列，缺少序列时返回 unknown。

### Task 5: 反思、结构化动作和工具记录

- [ ] 测试反思 JSON 截断/非法时保守失败，置信度受证据和新鲜度约束。
- [ ] 测试动作解析不会把“禁止加仓”当 BUY，按目标代码和结构化字段仲裁冲突。
- [ ] 统一 agent_runs.tool_calls 的 JSON 序列化，历史坏数据标记 evidence_missing。

### Task 6: 复杂度与交叉审查

- [ ] 测试复杂度和强制动作会触发交叉审查，跳过必须有原因。
- [ ] 恢复只执行缺失阶段，持久化 review_required/performed/skipped_reason。
- [ ] 为恢复后的质量评测保留审查记录。

### Task 7: 验证

- [ ] 运行新增回归测试、相关 backend 测试、编译检查和前端已有测试。
- [ ] 检查工作树差异，保留用户已有决策模块修改。
