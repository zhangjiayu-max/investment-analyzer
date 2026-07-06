# 投资分析器 Bad Case 分析与优化设计稿

> 基于 2026-07-06 用户反馈的 3 条 bad case（对话 ID 87、88、89），定位根因并提出可落地的优化方案。

---

## 一、Bad Case 摘要

### Case 1：对话87（ID 87）
- **用户反馈**：加仓金额依据不明、白酒已超持有限制仍建议加仓、信息不够好
- **系统数据**：白酒当前持仓 ¥49,177（占比 7.23%），单标的默认上限 15%。用户认为"已超限制"可能指自设定投规则中的限制，而非系统配置的 15% 上限
- **根因**：专家 prompt 中没有注入用户的实际持仓占比和系统配置的仓位上限，导致 LLM 无法判断"是否超限"

### Case 2：对话85（ID 88）
- **用户反馈**：
  1. 择时分析师返回异常（"分析过程遇到问题，请重试。"）
  2. 减仓建议太猛（一次减两个基金共 ¥130,000）
  3. 加仓比例不符合 4% 定投法
  4. 未推荐未持有的新基金
  5. 未站在用户角度
- **根因**：
  1. 择时分析师用 mimo-v2.5-pro，返回大量 tool_call 标记，清理后仅 12 字触发 fallback
  2. 专家 prompt 缺少"单次减仓上限"约束，LLM 自行决定减仓幅度
  3. 专家 prompt 中没有注入 4% 定投法规则（base_dca_amount=500, dca_drop_step_pct=4, max_dca_steps=3）
  4. 专家不知道关注列表中有哪些未持有基金
  5. 最终综合 prompt `_build_final_synthesis_prompt` 只有一句话，缺乏用户视角约束

### Case 3：对话89（ID 89）
- **用户反馈**：
  1. 估值数据来源均为 6 月份（白酒 PE 数据来源 6 月 9 日）
  2. 减仓建议太狠（一次减仓太多）
- **根因**：
  1. **中证白酒市盈率数据停在 2026-06-09**，但市净率数据到 7 月 5 日。PE 数据源（螺丝钉/手动录入）已 27 天未更新，PB 数据源（自动抓取）正常
  2. `get_best_valuation` 的降级策略会使用过期数据（max_days=365），但没有在输出中强调数据过期警告
  3. 同 Case 2，减仓无上限约束

---

## 二、根因分类

| # | 根因 | 影响范围 | 严重度 | 修复难度 |
|---|------|----------|--------|----------|
| R1 | **专家 prompt 缺少持仓上下文**（占比、成本、盈亏、仓位上限） | 所有专家 | 高 | 中 |
| R2 | **专家 prompt 缺少定投规则注入**（4% 定投法参数） | 估值分析师、资产配置师 | 高 | 低 |
| R3 | **减仓无幅度上限约束** | 风险管理师、资产配置师 | 高 | 低 |
| R4 | **PE 数据源断裂**（白酒 PE 停在 6 月 9 日） | 估值分析师 | 中 | 中 |
| R5 | **过期数据无显著警告** | 所有专家 + 最终综合 | 中 | 低 |
| R6 | **综合 prompt 太简单**，缺用户视角和未持有基金推荐 | 最终综合阶段 | 中 | 低 |
| R7 | **mimo-v2.5-pro 模型质量问题**（tool_call 标记污染） | 择时分析师 | 中 | 低（切模型） |

---

## 三、优化方案

### 3.1 R1：注入持仓上下文（核心修复）

**问题**：专家分析时看不到用户的实际持仓，无法判断"白酒已超限"。

**方案**：在 `orchestrate_stream` 中构建 `portfolio_context` 字符串，注入到每个专家的 system_prompt 末尾。

```python
# agent/orchestrator.py 新增函数
def _build_portfolio_context(user_id: str = "default") -> str:
    """构建持仓上下文，注入专家 prompt。"""
    from db.valuations import get_portfolio_holdings
    holdings = get_portfolio_holdings(user_id)
    if not holdings:
        return ""
    
    total_value = sum(h["current_value"] for h in holdings if h["current_value"] > 0)
    lines = ["## 📊 你的当前持仓（专家分析时必须参考）", ""]
    lines.append(f"总资产: ¥{total_value:,.0f}")
    lines.append(f"用户画像: 平衡型 / 长期 / 高亏损承受")
    lines.append(f"系统配置: 单标的上限 15% / 加仓估值百分位上限 35% / 减仓估值百分位下限 80%")
    lines.append(f"定投规则: 基础 ¥500 / 4% 跌幅加一档 / 最多 3 档")
    lines.append("")
    lines.append("| 基金 | 类别 | 市值 | 占比 | 成本 | 盈亏 | 盈亏率 |")
    lines.append("|------|------|------|------|------|------|--------|")
    for h in sorted(holdings, key=lambda x: x["current_value"], reverse=True):
        if h["current_value"] <= 0:
            continue
        pct = h["current_value"] / total_value * 100
        lines.append(
            f"| {h['fund_name']} | {h.get('fund_category','')} | "
            f"¥{h['current_value']:,.0f} | {pct:.1f}% | "
            f"¥{h['total_cost']:,.0f} | ¥{h['profit_loss']:,.0f} | "
            f"{h['profit_rate']*100:.1f}% |"
        )
    lines.append("")
    lines.append("⚠️ 加仓前必须检查目标基金当前占比是否已达 15% 上限。如已超限，禁止建议加仓。")
    return "\n".join(lines)
```

**注入位置**：`orchestrate_stream` 中，构建每个专家的 messages 时，在 system_prompt 后追加。

---

### 3.2 R2：注入 4% 定投法规则

**问题**：专家不知道系统配置的定投参数，无法给出符合规则的加仓建议。

**方案**：从 `system_config` 读取定投参数，注入到估值分析师和资产配置师的 prompt 中。

```python
def _build_dca_rules() -> str:
    """构建定投规则上下文。"""
    from db import get_config, get_config_int, get_config_float
    base = get_config_int("daily_advice.base_dca_amount", 500)
    step_pct = get_config_float("daily_advice.dca_drop_step_pct", 4)
    max_steps = get_config_int("daily_advice.max_dca_steps", 3)
    add_max_pct = get_config_float("daily_advice.add_valuation_max_percentile", 35)
    reduce_min_pct = get_config_float("daily_advice.reduce_valuation_min_percentile", 80)
    cooldown_days = get_config_int("daily_advice.recent_buy_cooldown_days", 10)
    cooldown_max = get_config_int("daily_advice.recent_buy_max_count", 2)
    
    return f"""## 📐 定投与加减仓规则（必须遵守）

### 4% 定投法
- 基础定投金额: ¥{base}
- 每下跌 {step_pct}% 加一档，每档加 ¥{base}
- 最多加 {max_steps} 档（即最大单次加仓 ¥{base * max_steps}）

### 加仓条件
- 估值百分位必须 ≤ {add_max_pct}% 才建议加仓
- 加仓前检查冷静期：{cooldown_days} 天内最多买入 {cooldown_max} 次
- 单标的占比达 15% 禁止加仓

### 减仓条件
- 估值百分位 ≥ {reduce_min_pct}% 才建议减仓
- 单次减仓幅度不超过该基金持仓的 20%（防止一次性减仓太狠）
- 单次建议总减仓金额不超过总资产的 10%

### 禁止行为
- ❌ 一次性减仓超过 ¥50,000
- ❌ 单条建议同时减仓 2 个以上基金
- ❌ 加仓金额超出 4% 定投法计算结果
- ❌ 对已超 15% 仓位的基金建议加仓"""
```

---

### 3.3 R3：减仓幅度上限约束

**问题**：LLM 自行决定减仓幅度，一次减 ¥130,000 太猛。

**方案**：在 `_build_dca_rules()` 中已包含减仓约束。同时，在最终综合阶段增加 validator 检查：

```python
# agent/prompt_defense.py 新增
def validate_rebalance_amount(answer: str, holdings: list) -> dict:
    """检查减仓建议是否超限。"""
    # 解析回答中的减仓金额
    # 如果单基金减仓 > 持仓 20% 或总减仓 > 总资产 10%，返回不通过
    ...
```

---

### 3.4 R4：PE 数据源断裂修复

**问题**：中证白酒 PE 停在 6 月 9 日，已 27 天未更新。

**当前降级策略**：
1. 7 天内详细数据 → 30 天内螺丝钉数据 → 365 天内过期数据
2. 白酒 PE：7 天内无 → 30 天内螺丝钉无 → 使用 6 月 9 日过期数据

**方案**：
1. **短期**：在 `get_best_valuation` 返回中增加 `is_expired` 和 `days_old` 字段（已有），并在专家 prompt 中强调过期警告
2. **中期**：增加 PB 替代逻辑——当 PE 数据超过 14 天但 PB 数据在 3 天内，优先使用 PB 百分位作为估值参考
3. **长期**：修复 PE 数据源（螺丝钉抓取脚本），确保 PE + PB 双指标同步更新

```python
# db/valuations.py 修改 get_best_valuation
def get_best_valuation(index_code: str, metric_type: str = "市盈率") -> dict | None:
    # ... 现有逻辑 ...
    
    # 新增：如果 PE 过期超过 14 天，尝试 PB 替代
    if metric_type == "市盈率" and result and result.get("days_old", 0) > 14:
        pb_data = get_latest_valuation(index_code, "市净率", max_days=3)
        if pb_data and pb_data.get("percentile") is not None:
            result["fallback_pb"] = pb_data
            result["warning"] = f"⚠️ 市盈率数据已过期 {result['days_old']} 天，使用市净率(百分位 {pb_data['percentile']:.1f}%) 作为替代参考"
    
    return result
```

---

### 3.5 R5：过期数据显著警告

**问题**：专家和最终回答中使用了过期数据但未告知用户。

**方案**：在估值数据注入专家 prompt 时，对过期数据加显著标记。

```python
def _format_valuation_for_prompt(val_data: dict) -> str:
    """格式化估值数据为 prompt 文本，过期数据加警告。"""
    days_old = val_data.get("days_old", 0)
    if days_old > 7:
        return (
            f"当前值: {val_data['current_value']} | "
            f"百分位: {val_data['percentile']:.1f}% | "
            f"⚠️ 数据已过期 {days_old} 天（快照日期: {val_data['snapshot_date']}），"
            f"请明确告知用户数据时效性问题"
        )
    return (
        f"当前值: {val_data['current_value']} | "
        f"百分位: {val_data['percentile']:.1f}% | "
        f"快照日期: {val_data['snapshot_date']}"
    )
```

---

### 3.6 R6：综合 prompt 增强用户视角

**问题**：`_build_final_synthesis_prompt` 只有一句话，缺乏用户视角。

**方案**：增强 prompt，加入用户画像、未持有基金推荐、减仓幅度约束。

```python
def _build_final_synthesis_prompt(specialist_results: list, routed_specialists: list) -> str:
    """构建最终综合提示。"""
    # ... 现有逻辑 ...
    
    prompt = """请根据以上各专家的分析结果，给出最终的综合投资建议。

## 综合要求

1. **用户视角**：你是用户的私人投资顾问，建议必须基于用户的实际持仓和盈亏情况。每条建议都要说明"为什么对你的持仓有意义"。

2. **加减仓约束**：
   - 加仓金额必须符合 4% 定投法（基础 ¥500/档，最多 3 档）
   - 单次减仓不超过该基金持仓的 20%
   - 单次建议总减仓金额不超过总资产的 10%
   - 禁止同时减仓 2 个以上基金

3. **数据时效**：如果专家分析中引用的估值数据已过期，必须在建议中明确标注"该数据截至 X月X日"，不得隐瞒。

4. **未持有基金**：如果关注列表中有未持有且估值低估的基金，可以推荐作为新建仓标的。

5. **格式要求**：
   - 结论先行：先给总体判断（加仓/减仓/持有/观望）
   - 具体操作表格：基金 | 操作 | 金额 | 理由
   - 风险提示：说明主要风险点
   - 数据截止日期：列出关键数据的时效性"""
    
    # ... missing_names 约束逻辑 ...
    
    return prompt
```

---

### 3.7 R7：择时分析师模型切换

**问题**：mimo-v2.5-pro 返回大量 tool_call 标记，清理后内容过短。

**方案**：将择时分析师的模型从 mimo-v2.5-pro 切换为 deepseek-chat（已用于 risk_assessor）。

```sql
-- 临时修复
UPDATE agents SET tools = REPLACE(tools, 'mimo-v2.5-pro', 'deepseek-chat') 
WHERE agent_key = 'market_analyst';
```

或修改 `cost_routing` 配置：
```sql
INSERT OR REPLACE INTO system_config (key, value, description, category) 
VALUES ('cost_routing.market_analyst_model', 'deepseek-chat', '择时分析师模型', 'cost_routing');
```

---

## 四、实施优先级

| 优先级 | 修复项 | 预估工时 | 影响 |
|--------|--------|----------|------|
| P0 | R1 注入持仓上下文 | 2h | 所有专家分析质量提升 |
| P0 | R2 注入定投规则 | 1h | 加仓建议符合规则 |
| P0 | R3 减仓幅度上限 | 1h | 防止一次性减仓太猛 |
| P1 | R6 综合prompt增强 | 1h | 最终回答质量提升 |
| P1 | R5 过期数据警告 | 0.5h | 数据透明度 |
| P1 | R7 择时分析师切模型 | 0.5h | 消除 fallback 问题 |
| P2 | R4 PE数据源修复 | 4h+ | 数据时效性根本解决 |
| P2 | R4 PE/PB替代逻辑 | 1h | 短期缓解数据过期 |

**总计 P0+P1：6 小时可完成核心修复。**

---

## 五、验证方法

1. **回测验证**：用对话 87/88/89 的相同问题重新触发，对比回答质量
2. **规则检查**：验证回答中加仓金额是否符合 4% 定投法，减仓是否超限
3. **数据透明**：验证过期数据是否有显著警告
4. **持仓校验**：验证专家是否引用了正确的持仓占比和盈亏数据

---

## 六、附录

### A. 当前持仓明细（2026-07-06）

| 基金 | 类别 | 市值 | 占比 | 盈亏率 |
|------|------|------|------|--------|
| 博时恒乐债券A | bond | ¥216,200 | 31.8% | +2.5% |
| 博时恒乐债券C | bond | ¥191,715 | 28.2% | +5.2% |
| 永赢稳健增强债券C | bond | ¥50,984 | 7.5% | +2.0% |
| 招商中证白酒A | index | ¥49,177 | 7.2% | -34.4% |
| 宏利消费红利指数C | index | ¥35,246 | 5.2% | -18.7% |
| 其他 | - | ¥136,731 | 20.1% | - |

总资产: ¥680,054

### B. 白酒估值数据现状

| 指标 | 最新日期 | 值 | 百分位 | 状态 |
|------|----------|-----|--------|------|
| 市盈率(PE) | 2026-06-09 | 19.1 | 9.22% | ⚠️ 过期27天 |
| 市净率(PB) | 2026-07-05 | 3.76 | 0.39% | ✅ 正常 |

### C. 关注列表未持有基金

| 基金 | 指数 | 备注 |
|------|------|------|
| 中欧中证机器人指数C | 中证机器人 | 补仓监控 |
| 国泰黄金ETF联接C | 黄金9999 | - |
| 易方达中证红利ETF联接C | 中证红利 | - |
| 人工智能ETF | 人工智能 | PE百分位98.7%，高估 |
| 南方科创板芯片ETF联接C | 科创板芯片 | - |
