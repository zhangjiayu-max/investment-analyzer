# 多 Agent 系统优化 — 降本增效不降质量

## Context

当前多 Agent 系统存在三个核心问题：
1. **协调成本爆炸** — 一次 complex 查询最多 48 次 LLM 调用，全部串行
2. **一致性问题** — Phase A 专家完全隔离，交叉审阅昂贵且无条件触发
3. **过度专业化** — 专家看不到全局，orchestrator 无法验证事实

用户约束：**不能因为成本压缩导致质量变差**。

优化策略：只做"不影响质量的效率提升"——并行化、去重、智能跳过冗余步骤、预算兜底。

---

## P0: 专家并行执行（最大延迟收益）

**现状**：`for tc in msg.tool_calls` 逐个串行等待，3 个专家 = 3 倍等待时间。
**目标**：3 个专家并行执行，延迟从 24s 降到 8s。

### 文件：`backend/agent/orchestrator.py`

**`orchestrate()` (非流式，~line 744)**：
- 用 `concurrent.futures.ThreadPoolExecutor` 替代串行 `for` 循环
- 提交所有 specialist 到线程池，`as_completed()` 收集结果
- 按原始顺序排列结果后 append 到 `llm_messages`（OpenAI API 要求 tool response 顺序匹配 tool_calls）

**`orchestrate_stream()` (流式，~line 1052)**：
- 先 yield 所有 `specialist_start` 事件
- 用 `ThreadPoolExecutor` + `queue.Queue` 模式并行执行
- 每个专家完成时 yield `specialist_done` 事件（用户看到"专家逐个完成"的体验）
- 全部完成后按顺序 append tool response

**线程安全**：
- `_call_llm()` 使用的 OpenAI client 是线程安全的
- `execute_tool()` 每次调用 `_get_conn()` 创建独立连接，线程安全
- `threading.Event` 本身线程安全
- `_record_token_usage()` 独立连接，线程安全

**关键细节**：tool response 消息必须按原始 tool_calls 顺序 append，用 `ordered_results[idx]` 追踪。

---

## P1: 请求级上下文缓存（消除重复 DB 查询）

**现状**：每个 specialist 独立调用 `build_portfolio_context()` 查询 DB，3 个专家 = 3 次相同查询。
**目标**：orchestrator 构建一次，传给所有 specialist。

### 文件：`backend/agent/orchestrator.py`

在 `orchestrate()` 和 `orchestrate_stream()` 开头，构建 `prebuilt_context`：
```python
prebuilt_context = ""
portfolio_ctx = build_portfolio_context()
valuation_ctx = build_valuation_summary()
# 组合成完整上下文字符串
```

修改 `_execute_specialist()` 签名，增加 `prebuilt_context` 参数，传递给 `run_specialist()`。

### 文件：`backend/agent/multi_agent.py`

修改 `run_specialist()` 和 `run_specialist_with_context()`：
- 增加 `prebuilt_context` 参数
- 当 `prebuilt_context` 非空时，跳过内部的 `build_portfolio_context()` 调用
- 同理跳过 `build_valuation_summary()`

---

## P2: 智能交叉审阅（质量保持的成本削减）

**现状**：所有 complex + ≥2 专家的查询都触发交叉审阅，即使专家意见完全一致。
**目标**：只有当专家意见分歧时才触发交叉审阅。

### 文件：`backend/agent/orchestrator.py`

新增 `_detect_specialist_disagreement(specialist_results)` 函数：
- 关键词匹配提取每个专家的情感方向（看多/看空/中性）
- 如果所有专家方向一致 → 返回 False（跳过交叉审阅）
- 如果存在看多+看空分歧 → 返回 True（触发交叉审阅）
- 纯字符串匹配，无 LLM 调用，零延迟

修改交叉审阅触发条件：
```python
# 原来：
if complexity == "complex" and len(specialist_results) >= 2:

# 改为：
if complexity == "complex" and len(specialist_results) >= 2 and _detect_specialist_disagreement(specialist_results):
```

跳过时 yield 状态消息："各专家意见一致，跳过交叉审阅，直接综合..."

**质量保证**：保守策略——只要有方向性分歧就触发，只在完全一致时跳过。

---

## P3: Session 级 Token 预算（安全网）

**现状**：`token_usage` 表记录了所有调用，但没有任何代码读取它来限制。
**目标**：每日 token 上限 + 优雅降级。

### 文件：`backend/db.py`

新增 `get_today_token_total()` 函数：
```sql
SELECT COALESCE(SUM(total_tokens), 0) FROM token_usage
WHERE date(created_at) = date('now', 'localtime')
```

### 文件：`backend/config.py`

新增配置项：
```python
DAILY_TOKEN_LIMIT = int(os.getenv("DAILY_TOKEN_LIMIT", "500000"))
TOKEN_WARN_THRESHOLD = float(os.getenv("TOKEN_WARN_THRESHOLD", "0.8"))
```

### 文件：`backend/agent/orchestrator.py`

新增 `check_token_budget()` 函数，在 `orchestrate()` 和 `orchestrate_stream()` 开头调用：
- `mode=normal`（<80%）：正常执行
- `mode=conservative`（80-100%）：减少 MAX_TURNS 到 3，强制跳过交叉审阅
- `mode=exceeded`（>100%）：返回友好提示，不执行分析

---

## P4: 降低专家 MAX_TURNS

**现状**：`run_specialist()` 中 `MAX_TURNS = 4`，大多数专家 1-2 轮就完成。
**目标**：改为 `MAX_TURNS = 3`，保留 fallback 摘要兜底。

### 文件：`backend/agent/multi_agent.py`

`run_specialist()` line 228：`MAX_TURNS = 4` → `MAX_TURNS = 3`

fallback 摘要机制（line 317-332）仍然生效，即使 3 轮都用于工具调用，最后一次机会仍能产出分析。

---

## 实施顺序

1. **P1（上下文缓存）** — 最低风险，纯重构
2. **P4（降低 MAX_TURNS）** — 一行改动
3. **P0（并行执行）** — 最高收益，先做非流式再做流式
4. **P2（智能交叉审阅）** — 依赖 P0 稳定
5. **P3（Token 预算）** — 独立于其他改动

## 预期效果

| 维度 | 优化前 | 优化后 |
|------|--------|--------|
| 3专家延迟 | ~42s（串行） | ~8-14s（并行） |
| Complex LLM 调用 | ~22 次 | ~10-16 次 |
| 重复 DB 查询 | 4-6 次 | 1 次 |
| 交叉审阅 | 无条件触发 | 仅分歧时触发 |
| Token 安全 | 无限制 | 每日 50 万上限 |

## 关键文件

| 文件 | 改动 |
|------|------|
| `backend/agent/orchestrator.py` | P0 并行执行 + P2 智能审阅 + P3 预算集成 |
| `backend/agent/multi_agent.py` | P1 接受预构建上下文 + P4 降低 MAX_TURNS |
| `backend/db.py` | P3 新增 `get_today_token_total()` |
| `backend/config.py` | P3 新增预算配置 |

## 验证方案

1. 发送"白酒能买吗"→ 验证 3 个专家并行执行（观察 SSE 事件）
2. 对比优化前后延迟（应减少 60%+）
3. 验证专家意见一致时跳过交叉审阅
4. 设置低 token 限额验证降级行为
5. 验证取消功能在线程池中正常工作
