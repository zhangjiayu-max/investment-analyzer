# Orchestrator 性能优化方案

> 日期：2026-06-12 | 状态：✅ 已完成

## 问题分析

### 耗时瓶颈

通过分析对话46的执行数据，发现以下瓶颈：

| 阶段 | 耗时 | 问题 |
|------|------|------|
| 需求澄清 | ~5秒 | 正常 |
| 专家并行执行 | ~2分钟 | 正常（并行） |
| **Orchestrator 处理** | **~10分钟** | ⚠️ 严重延迟 |
| 仲裁法官 | ~2分钟 | 正常 |
| **总计** | **~15分钟** | 偏长 |

### 关键发现

**专家完成到仲裁开始之间有 10.2 分钟的延迟！**

原因：
1. 交叉审阅后有额外的 LLM 调用（综合结果）
2. Orchestrator 进行了多次 LLM 调用
3. 没有使用优化器跳过不必要的步骤

## 优化措施

### 1. 创建优化器模块

**文件**: `backend/agent/orchestrator_optimizer.py`

功能：
- `OrchestratorOptimizer.should_skip_cross_review()` - 快速判断是否跳过交叉审阅
- `OrchestratorOptimizer.should_skip_arbitration()` - 快速判断是否跳过仲裁
- `ParallelExecutor.estimate_execution_time()` - 估算执行时间
- `log_performance_metrics()` - 记录性能指标

### 2. 优化交叉审阅逻辑

**优化前**：
```python
# 交叉审阅后还有一次 LLM 调用
if cross_review_results:
    response = _call_llm(...)  # 额外的 LLM 调用！
    answer = response.choices[0].message.content
```

**优化后**：
```python
# 跳过中间的 LLM 调用，直接进入仲裁
if cross_review_results:
    answer = msg.content or ""  # 使用之前的结果
```

**效果**：减少 1-2 分钟延迟

### 3. 优化仲裁决策

**优化前**：
- 所有 medium/complex 任务都可能触发仲裁

**优化后**：
- 使用 `OrchestratorOptimizer.should_skip_arbitration()` 快速判断
- 如果专家意见一致，跳过仲裁

**条件**：
```python
def should_skip_arbitration(specialist_results, complexity):
    # 1. 简单任务跳过
    if complexity in ("simple", "chat"):
        return True
    # 2. 专家数量不足跳过
    if len(specialist_results) < 2:
        return True
    # 3. 专家意见一致跳过
    if not has_disagreement:
        return True
```

### 4. 添加性能监控

**新增表**: `performance_metrics`

```sql
CREATE TABLE IF NOT EXISTS performance_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER,
    message_id INTEGER,
    metrics_json TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
```

**记录的指标**：
- `phases.clarification` - 需求澄清耗时
- `phases.total` - 总耗时
- `complexity` - 任务复杂度
- `specialist_count` - 专家数量

### 5. 更新编排配置

```sql
-- 只在 complex 任务且有明显分歧时才触发交叉审阅
UPDATE orchestration_config SET value = 'complex' WHERE key = 'cross_review_trigger';
```

## 预期效果

| 优化项 | 预期减少 | 说明 |
|--------|----------|------|
| 跳过交叉审阅综合 | 1-2分钟 | 减少一次 LLM 调用 |
| 优化仲裁决策 | 2-5分钟 | 专家一致时跳过仲裁 |
| 快速分歧检测 | 10-30秒 | 纯字符串匹配，零延迟 |
| **总计** | **3-7分钟** | 总耗时降至 8-12 分钟 |

## 测试验证

### 测试用例

1. **简单任务**（simple）：
   - 预期：无交叉审阅，无仲裁
   - 耗时：< 1 分钟

2. **中等任务**（medium）：
   - 预期：可能有交叉审阅，无仲裁（专家一致时）
   - 耗时：2-4 分钟

3. **复杂任务**（complex）：
   - 预期：有交叉审阅，有仲裁（有分歧时）
   - 耗时：5-8 分钟

### 监控指标

```sql
-- 查看性能指标
SELECT * FROM performance_metrics ORDER BY id DESC LIMIT 10;

-- 分析平均耗时
SELECT
    json_extract(metrics_json, '$.complexity') as complexity,
    AVG(json_extract(metrics_json, '$.phases.total')) as avg_total_ms,
    COUNT(*) as run_count
FROM performance_metrics
GROUP BY complexity;
```

## 后续优化方向

1. **使用更快的模型**：
   - 中间步骤使用轻量级模型
   - 只有最终裁决使用强模型

2. **缓存机制**：
   - 缓存专家结果
   - 避免重复的 LLM 调用

3. **并行优化**：
   - 交叉审阅也并行执行
   - 仲裁准备与专家执行并行

4. **预测性优化**：
   - 基于历史数据预测耗时
   - 动态调整超时时间

## 文件变更

| 文件 | 操作 | 说明 |
|------|------|------|
| `backend/agent/orchestrator_optimizer.py` | 新增 | 优化器模块 |
| `backend/agent/orchestrator.py` | 修改 | 集成优化器 |
| `backend/db/eval.py` | 修改 | 添加性能指标表 |
