# AI 对话建议割裂根因分析与修复方案

**日期**: 2026-07-06
**版本**: v1.0
**问题**: AI 回答不结合持仓盈亏，只看估值推荐加减仓，不参考历史操作

---

## 一、根因排查（5 个问题）

### 根因 1：跨对话结论复用是关闭的 🔴

```python
# backend/db/config.py 第155行
('agent.reuse_recent_conclusions', 'false',
 '是否跨对话复用 24h 内同标的结论（默认关闭，会改变专家 prompt）')
```

**影响**：用户今天问"XX基金怎么样"，AI 给出建议。明天用户再问"XX基金呢"，AI **不知道昨天说过什么**。每次都是第一次见面。

**这是最核心的问题。** 用户说"也不看之前是否加仓过"正是这个原因——AI 没有"记忆"。

### 根因 2：orchestrator prompt 没有强制要求结合 P&L

当前 orchestrator prompt（2025 行）有"持仓亏损处理原则"，但说的是"不要简单建议割肉止损"，**没有明确要求"必须结合每只持仓的盈亏率给出建议"**。

**影响**：valuation_expert 看到"估值低"就推荐加仓，没有检查"这只基金用户已经亏了 15%，继续加仓是否合理"。

### 根因 3：valuation_expert 的 prompt 偏向估值分析

valuation_expert 的职责描述可能是"分析估值水位"，没有要求"结合持仓盈亏综合分析"。每个 specialist 只在自己的领域内说话，缺少**跨领域的综合分析指令**。

### 根因 4：orchestrator 的"桥接指令"不够强

当前 `_inject_analysis_context()` 注入的结论是"供参考但不强制采纳"。这个措辞太弱了，变成了"看也行不看也行"。

### 根因 5：缺少"决策执行记录"的注入

analysis_conclusions 记录的是"分析结论"（估值高/低），不是"操作记录"（用户上次加仓了、赎回了）。AI 需要知道用户的**实际操作历史**，不只是分析结论。

---

## 二、修复方案

### 修复 1：打开跨对话结论复用（1 行配置）

```python
# 在 db/config.py 或系统管理中
agent.reuse_recent_conclusions = true
agent.reuse_conclusions_hours = 48  # 放宽到 48 小时
```

**效果**：AI 在同标的二次提问时，会看到 48 小时内该标的的分析结论，避免重复分析。

### 修复 2：orchestrator prompt 增强（~10 行 prompt 修改）

在 orchestrator system prompt 的"回答原则"中增加：

```python
## 强制规则（必须遵守）
### 1. 必须结合持仓盈亏做建议
- 在给出任何关于基金的建议前，先检查"用户当前持仓"和"盈亏率"
- 估值低但用户已亏损 20% → 建议需谨慎，考虑用户心理承受力
- 估值高但用户盈利 15% → 可以考虑止盈，但要结合配置比例
- 禁止仅凭估值高低给出加减仓建议

### 2. 必须参考历史操作记录
- 检查该基金近 30 天是否有买入/卖出操作
- 用户已加仓 3 次 → 建议持有观察，不宜继续加仓
- 用户刚卖出 → 建议等待，不要马上劝再买
```

### 修复 3：`_inject_analysis_context()` 增强（~30 行）

```python
def _inject_analysis_context(query: str) -> tuple:
    """
    增强版：
    1. 注入 48h 内的分析结论（同源）
    2. 注入该标的近 30 天的交易记录
    3. 注入该标的的持仓盈亏数据
    """
```

具体修改：除了已有的 `get_latest_analysis_conclusions()`，增加：

```python
# 注入近 30 天交易记录
def _get_recent_trades(fund_code: str, days: int = 30) -> str:
    """查该基金近 30 天的买卖记录。"""
    trades = get_transactions_for_fund(fund_code, days_back=days)
    if not trades:
        return ""
    
    lines = ["## 该基金近期操作记录"]
    for t in trades:
        lines.append(f"- {t['date']} {t['action']} {t['amount']}")
    
    # 总结
    buy_count = sum(1 for t in trades if t['action'] in ('buy', 'dca'))
    if buy_count >= 3:
        lines.append(f"⚠️ 近30天已加仓 {buy_count} 次，建议暂缓加仓，先观察效果")
    
    return "\n".join(lines)
```

### 修复 4：valuation_expert prompt 增强（~5 行）

在 valuation_expert 的 system prompt 末尾追加：

```python
## 增强指令
5. 你的估值分析必须和实际持仓盈亏结合：
   - 如果用户持有该基金且已亏损，请给出加仓/持有/止损的建议
   - 如果用户未持有该基金，请给出是否适合建仓的建议
   - 如果用户刚卖出，请说明"刚卖出，建议等回调企稳后再考虑"
   - 你的最终输出必须包含"对当前持仓的影响"一段
```

### 修复 5：增加"决策执行追踪"（~80 行新增）

```python
# 新增：decision_tracking.py

def track_recent_decisions(fund_code: str, days=30) -> dict:
    """
    追踪指定基金近 N 天的决策执行情况。
    
    返回：
    {
        "suggestions_by_ai": [
            "2026-07-01 AI建议加仓",
            "2026-06-28 AI建议持有",
        ],
        "user_actions": [
            "2026-07-02 用户加仓2000元",
            "2026-06-29 用户赎回了",
        ],
        "summary": "AI 7天前建议加仓，用户2天后执行了加仓",
    }
    """
```

这个数据注入到 prebuilt_context，让 AI 看到"我说过什么"和"用户做了什么"的对应关系。

---

## 三、改动量汇总

| 修复 | 位置 | 行数 | 优先级 |
|------|------|------|--------|
| 打开结论复用 | 配置中心（1 行） | 1 | P0 |
| orchestra prompt 增强 | `orchestrator.py` | ~10 | P0 |
| 注入交易记录 | `orchestrator.py` | ~30 | P0 |
| valuation prompt 增强 | `multi_agent.py` 或 DB | ~5 | P1 |
| 决策执行追踪 | 新增 `decision_tracking.py` | ~80 | P1 |
| 总计 | | **~126 行** | |

### 先做 P0 的 3 个（~40 行）

1. 打开 `agent.reuse_recent_conclusions = true`（1 行配置）
2. orchestra prompt 增加"结合 P&L + 参考历史操作"（~10 行）
3. 注入交易记录到 prebuilt_context（~30 行）

**这三个改完，AI 的回答应该从"估值低→加仓"变成"XX基金您已持有且盈利15%，估值虽低但建议分批止盈"。**