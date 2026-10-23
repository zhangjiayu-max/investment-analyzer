# 交叉审阅预算不足诊断报告（conv 208）

日期：2026-10-22
对象：conversation 208 / assistant message 618 / trace `4954b9ca-55f`
问题：三个专家的交叉审阅全部返回「交叉审阅预算不足，未完成复核。」

---

## 一、结论先行

交叉审阅一次 LLM 调用都没有发出去，不是模型失败，也不是网络超时，而是**请求前的本地预算预检直接拒绝**。

拒绝点：`backend/agent/core/multi_agent.py:471`

```
affordable = remaining - prompt_estimate
if affordable <= 0:
    raise _SpecialistBudgetExhausted(...)
```

关键数字：

| 项 | 数值 | 来源 |
|---|---|---|
| review 预算池 | 40,000 | `budget_usage.cost_budget.review`（medium 档） |
| reflection 实际消耗 | 28,916 | `budget_usage.by_phase.reflection`（占池 72.3%） |
| 分配前剩余 | 11,084 | 40,000 − 28,916 |
| 3 个专家均分 | 3,694/人 | `11,084 // 3` |
| 单人请求实际输入估算 | 4,960 | 按 `estimate_request_tokens` 公式复现 |
| affordable | −1,266 | 3,694 − 4,960 → **触发异常** |
| cross_review 实际用量 | 0 | `by_phase.cross_review` |

同时 `budget_usage.tokens_used = 135,947 / max_total = 290,000`，**全局只用了 46.9%**，但交叉审阅这一格饿死了。

---

## 二、四个架构问题

### A1 预算按"人头均分"，不按"单人可行性"分配

```python
# pipeline.py:475
review_cost_limit = _review_tokens_remaining(state) // max(1, len(_original_specialists))
```

均分只保证三份加起来不超池，不保证每份装得下 prompt。交叉审阅的 prompt 结构是
`system + 自己的分析(4000 字) + 另外 2 家的分析(各 6000 字)`，单人输入约 4,960 tokens，
是固定成本的**平方级增长**（N 个专家 → 每家读 N−1 份）。

人均额度 `池 / N` 随 N 线性下降，而单次成本随 N 线性上升，二者相向而行。
N=3 时 3,694 < 4,960 就崩了。N 越大崩得越早。

### A2 review 池被 reflection 提前吃掉 72%

`_review_tokens_remaining()` 把 `reflection / cross_review / debate` 三类阶段合并计费：

```python
# pipeline.py:2238
used = sum(state.tokens_used_by_phase.get(phase, 0)
           for phase in ('reflection', 'cross_review', 'debate'))
remaining = max(0, budget.get('review', 1500) - used)
```

执行顺序是 reflection 先、cross_review 后。reflection 花了 28,916，交叉审阅只剩下 11,084 的残羹。
池子没超，但**先到先得，后到饿死**。

### A3 预算档位与任务实际复杂度不一致

`budget_usage.cost_budget` 的取值精确等于 `termination.py` 的 **medium** 档：

```
execution 180000 / review 40000 / synthesis 50000 / max_total 290000 / max_specialists 4
```

而 `metadata.complexity = "complex"`，complex 档应为：

```
execution 240000 / review 60000 / synthesis 70000 / max_total 400000 / max_specialists 6
```

原因是复杂度判定有两套：

- 用户可见的 `complexity` 来自 clarify 阶段（route_result），写进 metadata；
- 预算用的 `complexity` 来自 `_effective_complexity()`，它只合并 `initial / plan.complexity / (专家数≥2→medium)`，
  **不包含 `need_cross_review` / `needs_arbitration`，且专家数 3 也只加 medium**。

于是"要做交叉审阅的 complex 任务"拿到了"不做审阅的 medium 预算"。

### A4 全部失败，状态却报 completed —— 最严重的一条

```python
# pipeline.py:552-557
if (_cr_results and _review_tokens_remaining(state) <= 0):
    state.set_phase_result("cross_review_status", {"status": "budget_exhausted"})
elif len(_cr_results) != len(_original_specialists):
    state.set_phase_result("cross_review_status", {"status": "failed"})
elif _cr_results:
    state.set_phase_result("cross_review_status", {"status": "completed"})
```

每个专家失败时 `_review_single_pipeline` 在 `except` 里返回了一个**占位结果**（`status="budget_exhausted"`），
这些占位结果被 append 进 `_cr_results`，长度等于专家数 → 走第三个分支 → `completed`。

而第一个条件用的是**总池**（40,000 − 28,916 = 11,084 > 0），永远不成立。

后果：
- `metadata.cross_review_status = {"status": "completed"}`，实际 0 次审阅；
- `_validate_final_facts()` 里 `review_status in {failed, budget_exhausted, degraded}` 的降级链路没触发，
  `quality_issues` 里只有 `trade_execution_unverified`，看不出审阅没做；
- 监控、评测、回归全部拿不到这个失败信号 —— **这比预算不足本身更危险**。

---

## 三、修复方案（按 ROI 排序）

| 优先级 | 修复 | 改动量 | 预期效果 |
|---|---|---|---|
| P0 | `_effective_complexity` 纳入交叉审阅/仲裁信号 + 专家数≥3→complex | 1 个函数 | review 池 40k→60k |
| P0 | `_review_tokens_remaining` 分子池，reflection 与 cross_review 各占固定比例 | 1 个函数 + 1 处调用 | reflection 不再挤占审阅 |
| P0 | 分配前可行性预检：人均额度 < 单人估算 → 裁剪 peer 文本，而非直接抛 | 1 处 | 小额预算也能出审阅 |
| P1 | `cross_review_status` 按**有效结果数**判定，占位结果不算数 | 1 处 | 失败可见 |
| P2 | reflection `max_tokens=2400` 与 4 字段 JSON 不匹配（第一次恰好 2400 被截断）→ 对齐 review_output | 1 行 | 省一次重试 ≈ 13.5k tokens |

### 补丁 1：`pipeline.py:43`

```python
def _effective_complexity(initial: str, plan_result: dict | None = None,
                          specialists: list | None = None) -> str:
    """合并预处理、计划和实际专家规模，避免后续阶段沿用过低复杂度。

    交叉审阅/仲裁是复杂度信号：需要互审的任务，其输入成本随专家数平方增长，
    若仍按 medium 分配 review 池，会出现「池子没超但人均装不下 prompt」的死锁。
    """
    rank = {"chat": 0, "simple": 1, "medium": 2, "complex": 3}
    values = [initial or "medium"]
    plan = (plan_result or {}).get("plan") if isinstance(plan_result, dict) else None
    if isinstance(plan, dict):
        values.append(plan.get("complexity", ""))
        if plan.get("need_cross_review") or plan.get("needs_arbitration"):
            values.append("complex")
    if len(specialists or []) >= 2:
        values.append("medium")
    if len(specialists or []) >= 3:
        values.append("complex")
    return max((v for v in values if v in rank), key=lambda v: rank[v], default="medium")
```

### 补丁 2：`pipeline.py:2238`

```python
_REVIEW_PHASES = ('reflection', 'cross_review', 'debate')
# reflection 先执行，必须给它硬上限，否则会把交叉审阅的额度吃光；
# 未用完的额度不回收（回收会让先跑的阶段无上限），但总池仍统一封顶。
_REVIEW_POOL_SHARE = {'reflection': 0.40, 'cross_review': 0.60, 'debate': 0.60}

def _review_tokens_remaining(state, phase=None):
    """审查类阶段的可用额度；phase 指定时按子池计算，避免先跑阶段挤占后跑阶段。"""
    budget = state.token_budget
    total = budget.get('review', 1500)
    if phase is None:
        used = sum(state.tokens_used_by_phase.get(p, 0) for p in _REVIEW_PHASES)
        remaining = max(0, total - used)
    else:
        cap = int(total * _REVIEW_POOL_SHARE.get(phase, 1.0))
        used = state.tokens_used_by_phase.get(phase, 0)
        others = sum(state.tokens_used_by_phase.get(p, 0) for p in _REVIEW_PHASES if p != phase)
        remaining = max(0, min(cap - used, total - others - used))
    if budget.get('max_total'):
        remaining = min(remaining, max(0, budget['max_total'] - budget.get('synthesis', 0) - state.tokens_used))
    return remaining
```

配套：`pipeline.py:2252` 的 `_call_review_model` 里
`remaining = _review_tokens_remaining(state)` → `_review_tokens_remaining(state, phase)`

### 补丁 3：`pipeline.py:475-476`

```python
            review_cost_limit = _review_tokens_remaining(state, 'cross_review') // max(1, len(_original_specialists))
            review_output_limit = min(review_cost_limit,
                                      state.token_budget.get("review_output", review_cost_limit))
            # 人均额度装不下一次审阅 prompt 时先缩 peer 文本，再决定是否放弃。
            # 均分只保证总量不超，不保证单份可行；直接抛异常会让整个阶段静默降级。
            _peer_char_limit = 6000
            if review_cost_limit < 6000:
                _peer_char_limit = max(1200, review_cost_limit * 2 // max(1, len(_original_specialists) - 1))
```

并给 `_run_cr_op` 传 `peer_char_limit=_peer_char_limit`（`run_cross_review_opinion` 需加同名参数，默认 6000，内部
`peer_analysis[:peer_char_limit]`）。

### 补丁 4：`pipeline.py:552-557`

```python
            _valid_cr = [r for r in _cr_results
                         if r.get("status") not in {"budget_exhausted", "failed"}]
            if not _cr_results or not _valid_cr:
                state.set_phase_result("cross_review_status",
                                       {"status": "budget_exhausted" if _cr_results else "failed",
                                        "valid": 0, "total": len(_original_specialists)})
            elif len(_valid_cr) < len(_original_specialists):
                state.set_phase_result("cross_review_status",
                                       {"status": "degraded", "valid": len(_valid_cr),
                                        "total": len(_original_specialists)})
            else:
                state.set_phase_result("cross_review_status",
                                       {"status": "completed", "valid": len(_valid_cr),
                                        "total": len(_original_specialists)})
```

### 补丁 5：`pipeline.py:3578`

```python
                temperature=0.2, max_tokens=min(2400, state.token_budget.get('review_output', 2400) or 2400),
```
（本次第一次 reflection 的 `completion_tokens` 恰好等于 2400，即被 `max_tokens` 截断，
重试一次又多花 13,538 tokens。）

---

## 四、验证方式

- 单元：构造 `token_budget.review=40000`、`reflection` 已用 28916 的 state，
  断言 `_review_tokens_remaining(state,'cross_review') > 0` 且 `>= 0.6*40000`。
- 回归：重跑 conv 208 同类问题，断言 `by_phase.cross_review > 0`、
  `cross_review_status.status == "completed"`、`valid == total == 3`。
- 负向：把 review 池压到 3000，断言状态为 `budget_exhausted` 且
  `_validate_final_facts` 产出 `cross_review_budget_exhausted` 质量项。

---

## 五、成本参考

本次单轮总用量 135,947 tokens，其中：
- execution（3 专家 × 2 轮 ReAct）84,162 —— 61.9%
- reflection（2 次，含 1 次截断重试）28,916 —— 21.3%
- synthesis 19,093 —— 14.0%
- auxiliary 3,776 —— 2.8%
- **cross_review 0**

修好 P0 后，交叉审阅预计增加约 3 × (4,960 + 1,500) ≈ 19,400 tokens，
总用量约 155,000，仍远低于 complex 档 400,000 上限。
