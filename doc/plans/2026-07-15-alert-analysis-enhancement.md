# 预警/分析/对话 "论据增强" 设计稿

> 日期：2026-07-15
> 问题：一周前预警"博时恒乐A占比过高，建议减仓"，用户觉得"只说占比高，没说服力"，没减仓，现在跌了
> 根因：预警/分析只给结论（占比高/亏损大），不给论据（重仓哪些股在跌、哪些债在爆、利率怎么变）

## 一、现状诊断

### 1.1 预警扫描（alert_scanner.py:257）

当前 `scan_portfolio_risk()` 只做两件事：

```python
# ① 集中度检测：weight > 30%
content = f"{fund_name} 当前占组合 {weight:.1f}%，超过阈值，建议关注分散化风险。"

# ② 亏损检测：loss > 15%
content = f"{fund_name} 当前亏损 {abs(profit_rate):.1f}%，超过阈值，建议关注是否止损。"
```

**问题**：content 只有 1 句话，没有调用 `get_fund_holdings()` 获取底层持仓数据。用户看到"占比高"不知道具体风险在哪里。

### 1.2 深度分析 prompt（analysis.py:351）

`DEFAULT_FUND_DEEP_DIVE_PROMPT` 对混合型基金的指导：

```
3. **基金质量** — 重仓股分析、行业分布、基金经理能力
4. **综合建议** — 基于收益+基金质量+组合配置
```

**问题**：prompt 引导 LLM 分析"基金质量"，但没有明确要求做"亏损归因"——拆解亏损来自哪些股票、哪些债券、哪些市场因素。

### 1.3 对话系统（router.py:151）

```python
# 债券占比高 → 追加 allocation_advisor
```

**问题**：路由只追加了配置专家，但没有把底层持仓数据（重仓股、债券类型）传给专家分析。

### 1.4 已有但未充分利用的数据

`get_fund_holdings()` 已返回：
- 股票重仓Top10（名称、代码、占净值比、持仓市值）
- 债券持仓Top10（名称、代码、占净值比、债券类型）
- 资产配置（股票/债券/现金/其他占比）
- 行业配置
- 债券类型汇总（国债/企业债/金融债/可转债）

**这些数据已存在于系统中，但预警和对话没有用到。**

## 二、方案设计

### 核心理念

**"给结论 + 给论据"**：预警不再只说"占比高"，而是追加"因为该基金重仓了XX股（跌X%）、持有YY可转债（风险高）、近期利率上行Zbp"。

改动范围：不改 API 接口，不增新功能，**只增强现有预警/分析/对话的论据质量**。

### 2.1 预警扫描增强（alert_scanner.py）

集中度预警和亏损预警的 content 从 1 句话扩展为包含基金底层持仓的关键风险信息。

**伪代码**：

```python
def scan_portfolio_risk():
    for h in holdings:
        # 原逻辑：集中度/亏损检测
        if weight > threshold or loss > threshold:
            # 新：获取基金底层持仓
            fund_data = get_fund_holdings(fund_code)
            
            # 构建增强版 content
            parts = [f"{fund_name} 当前占组合 {weight:.1f}%，超过阈值"]
            
            # 股票风险
            if fund_data.get("top_stocks"):
                stocks = fund_data["top_stocks"][:3]
                parts.append(f"重仓股：{', '.join(s['stock_name']+f'({s['pct_nav']}%)' for s in stocks)}")
            
            # 债券风险（可转债占比高 → 风险提示）
            if fund_data.get("bond_type_summary"):
                cb = fund_data["bond_type_summary"].get("可转债", 0)
                if cb > 3:
                    parts.append(f"可转债占比 {cb}%，股市波动时风险较高")
            
            # 资产配置
            if fund_data.get("asset_allocation"):
                stock_pct = next((a['pct'] for a in fund_data['asset_allocation'] if '股票' in a['type']), None)
                if stock_pct:
                    parts.append(f"股票仓位 {stock_pct}，混合型基金波动大于纯债")
            
            content = "；".join(parts) + "。建议关注分散化风险。"
```

**效果对比**：

| 当前 | 优化后 |
|------|--------|
| "博时恒乐A 占比 45%，超过 30% 阈值，建议关注分散化风险。" | "博时恒乐A 占比 45%，超过 30% 阈值；重仓股：宁德时代(8.5%)、贵州茅台(6.2%)；可转债占比 12.3%，股市波动时风险较高；股票仓位 35%，混合型基金波动大于纯债。建议关注分散化风险。" |

### 2.2 深度分析 prompt 增强（analysis.py）

在 `DEFAULT_FUND_DEEP_DIVE_PROMPT` 的混合型基金分析框架中增加"亏损归因"维度：

```markdown
## 混合型基金（无跟踪指数）
1. **角色定位** — 资产配置特点、在组合中的功能
2. **持有收益** — 持有时间、年化收益
3. **亏损归因** — 【新增】拆分亏损来源：
   - 权益端：哪些重仓股跌了，拖累多少
   - 固收端：利率变动影响、信用利差变化
   - 费用端：管理费、申购费对收益的影响
4. **基金质量** — 重仓股分析、行业分布、基金经理能力
5. **综合建议** — 基于收益+基金质量+组合配置
```

### 2.3 对话系统路由增强（router.py）

对话中检测到"债券占比高"时，不仅追加 allocation_advisor，还自动获取该基金的底层持仓数据并放入上下文，让专家能分析具体风险。

### 2.4 全景诊断 prompt 增强（可选）

全景诊断中对"占比过高"的基金，追加展示其底层持仓要点，让用户看到"占比高"背后的具体风险。

## 三、改动文件清单

| 文件 | 改动 |
|------|------|
| `backend/services/advisor/alert_scanner.py` | `scan_portfolio_risk()` 调用 `get_fund_holdings()` 增强 content |
| `backend/db/analysis.py` | `DEFAULT_FUND_DEEP_DIVE_PROMPT` 增加"亏损归因"维度 |
| `backend/agent/core/router.py` | 债券占比高检测时追加基金持仓数据到上下文 |

## 四、风险

1. **alert_scanner 性能**：`get_fund_holdings()` 调用 akshare，可能慢。建议：加超时保护（5s），失败则退回原逻辑。
2. **避免过度告警**：已有 `_is_alert_recently_created` 去重（24h），不受影响。
3. **向后兼容**：只改 content 内容，不改 alert 结构，现有前端无需适配。