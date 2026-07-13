# 基金基本面深度分析 — 第一阶段设计稿

> 从"看基金"升级为"看公司"：穿透到重仓股层做基本面评分，持久化持仓快照支持季度变化追踪。
> 隶属于「基金深度分析增强四阶段计划」的第一阶段。后续阶段：大师理念矩阵 / 组合层面智能 / 决策智能+回测。

## 设计目标

### 核心问题

当前六维体检报告（质量/回撤/趋势/资金/情绪/估值）只看"基金本身"和"价格行为"，不看"基金持仓的公司质地"。导致：

1. **重仓股暴雷无法预警**：基金重仓股财务恶化（ROE下滑/负债率飙升）时，系统无法提前感知
2. **持仓变化无追踪**：基金经理调仓动作（新进/增持/减持/退出）无历史快照，无法分析"基金是否在调仓"
3. **基本面与估值割裂**：估值低可能是因为公司质地变差（价值陷阱），当前系统无法识别

### 解决方案

```
旧链路：估值低 → 买
新链路：重仓股基本面好？──否──→ 价值陷阱预警
            │是
        持仓变化趋势？──大幅减持──→ 谨慎，跟随调仓信号
            │稳定/增持
        → 基本面分高 + 估值低 → 强化了买入信号
```

### 成功标准

- 能输出基金Top10重仓股的5维基本面评分（盈利能力/成长性/偿债能力/质地稳定性/估值合理性）
- 能对比基金本季度 vs 上季度持仓，输出新进/增持/减持/退出四类调仓动作
- 基本面作为第7维加入体检报告，总评权重重新分配
- API `/api/analysis/fund-quality/{fund_code}` 返回扩展数据
- 前端EventRadarPage.vue 体检报告展示第7维 + 调仓动作面板

---

## 技术架构

### 整体架构

```
┌──────────────────────────────────────────────────────────────────┐
│  第一阶段：基本面深度维度（在现有六维引擎上扩展）                  │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  模块A: 重仓股财务评分引擎（services/fund_analysis.py 扩展）       │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ 1. 获取基金Top10持仓股（复用 get_fund_holdings）            │ │
│  │ 2. 调akshare财务接口（新增 _fetch_stock_financials）        │ │
│  │    stock_financial_analysis_indicator                       │ │
│  │ 3. 个股5维评分（新增 _score_stock_fundamentals）            │ │
│  │    - 盈利能力 30%：ROE + 毛利率 + 净利率                    │ │
│  │    - 成长性   25%：营收增速 + 净利润增速                    │ │
│  │    - 偿债能力 15%：资产负债率                                │ │
│  │    - 稳定性   15%：ROE 4季度标准差                          │ │
│  │    - 估值     15%：PE分位（复用现有估值数据）               │ │
│  │ 4. 按持仓占比加权 → 基金基本面分（新增 calculate_fundamental_score）│ │
│  └────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  模块B: 持仓快照持久化（db/fund_holdings_snapshot.py 新建）       │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ 新建 fund_stock_holdings 表                                 │ │
│  │ 字段：fund_code/report_date/stock_code/stock_name/          │ │
│  │       pct_nav/shares/market_value/created_at                │ │
│  │ 每次 get_fund_holdings 后异步写入快照                       │ │
│  │ 支持查询某基金某季度持仓 / 对比两季度差异                    │ │
│  └────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  模块C: 季度持仓变化追踪（services/fund_analysis.py 扩展）        │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ 对比本季度 vs 上季度持仓快照                                │ │
│  │ 动作判定阈值：                                              │ │
│  │   - 新进：上季度无，本季度有                                │ │
│  │   - 增持：占比上升 > 0.5%                                   │ │
│  │   - 减持：占比下降 > 0.5%                                   │ │
│  │   - 退出：上季度有，本季度无                                │ │
│  │   - 0.5%以内视为"持有不变"                                  │ │
│  │ 输出调仓动作列表 + 调仓力度评分                             │ │
│  └────────────────────────────────────────────────────────────┘ │
│                                                                  │
├──────────────────────────────────────────────────────────────────┤
│  数据层：fund_stock_holdings 表（新增）                            │
│  缓存层：stock_financial_indicator 90天TTL缓存（analysis_cache）   │
│  API层：/api/analysis/fund-quality/{fund_code} 扩展返回            │
│  前端：EventRadarPage.vue 体检报告新增第7维 + 调仓动作面板         │
└──────────────────────────────────────────────────────────────────┘
```

### 新增/修改文件清单

| 文件 | 职责 | 动作 |
|------|------|------|
| `backend/db/fund_holdings_snapshot.py` | fund_stock_holdings表CRUD | 新建 |
| `backend/services/fund_analysis.py` | 新增基本面评分 + 调仓追踪函数 | 修改（扩展） |
| `backend/db/fund_quality.py` | fund_quality_scores表新增fundamental_score列 | 修改（加列） |
| `backend/db/__init__.py` | init_db 新增 fund_stock_holdings 建表 + 导出 | 修改 |
| `backend/db/portfolio.py` | get_fund_holdings 增加快照写入钩子 | 修改 |
| `backend/routers/analysis/fund_quality.py` | API返回扩展（无需新增端点） | 修改（透传） |
| `backend/tests/test_fund_fundamental.py` | 单元测试 | 新建 |
| `frontend/src/components/EventRadarPage.vue` | 体检报告第7维 + 调仓动作面板 | 修改 |

---

## 数据库设计

### 新建表：fund_stock_holdings（基金持仓快照）

```sql
CREATE TABLE IF NOT EXISTS fund_stock_holdings (
    fund_code    TEXT NOT NULL,
    report_date  TEXT NOT NULL,          -- 季报日期，如 "2025-09-30"
    stock_code   TEXT NOT NULL,
    stock_name   TEXT,
    pct_nav      REAL,                    -- 占净值比例（%）
    shares       REAL,                    -- 持股数（万股）
    market_value REAL,                    -- 持仓市值（万元）
    created_at   TEXT DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (fund_code, report_date, stock_code)
);
CREATE INDEX IF NOT EXISTS idx_fund_holdings_fund_date ON fund_stock_holdings(fund_code, report_date);
```

**设计要点**：
- 复合主键 `(fund_code, report_date, stock_code)` 防重复
- `report_date` 来自 akshare 的"季度"字段，格式统一为 `YYYY-MM-DD`
- 同一基金同一季度重复写入时覆盖（INSERT OR REPLACE）
- 索引支持"查某基金所有季度"和"查某基金某季度"两种查询

### 扩展表：fund_quality_scores（加列）

```sql
ALTER TABLE fund_quality_scores ADD COLUMN fundamental_score REAL DEFAULT 0;
ALTER TABLE fund_quality_scores ADD COLUMN fundamental_detail TEXT DEFAULT '{}';
ALTER TABLE fund_quality_scores ADD COLUMN holding_changes TEXT DEFAULT '[]';
```

- `fundamental_score`：基本面维度得分（0-100）
- `fundamental_detail`：JSON，包含个股评分明细 + 加权逻辑
- `holding_changes`：JSON，包含季度调仓动作列表

### 缓存策略：stock_financial_indicator

**不新建表，复用 analysis_cache**：
- cache_key: `stock_financial_{stock_code}`
- TTL: 90天（财务指标季度更新，90天足够覆盖一个季报周期）
- value: JSON序列化的财务指标字典

**理由**：财务指标更新频率低（季度），但查询频率可能高（多基金的重仓股重叠）。90天TTL + analysis_cache 复用，避免新建表的维护成本。

---

## 评分算法

### 个股5维评分（0-100）

| 维度 | 权重 | 指标 | 评分逻辑 |
|------|------|------|---------|
| 盈利能力 | 30% | ROE + 毛利率 + 净利率 | ROE>15%高分 / >10%中 / <8%低；毛利率行业对比（>50%高分 / >30%中 / <20%低）；净利率>20%高分 |
| 成长性 | 25% | 营收增速 + 净利润增速 | 增速>20%高分 / >10%中 / >0%及格 / <0%扣分；连续2季度下滑额外扣5分 |
| 偿债能力 | 15% | 资产负债率 | <40%高分 / 40-60%中 / 60-70%低 / >70%扣分；金融行业特殊处理（负债率高是常态） |
| 稳定性 | 15% | ROE 4季度标准差 | 标准差<2%高分 / <5%中 / <10%低 / >10%扣分；数据不足默认中分 |
| 估值合理性 | 15% | PE分位 | 复用现有估值数据，PE分位<20%高分 / <40%中 / <60%低 / >80%扣分（高估） |

**单项评分统一标准**：
- 90-100：优秀
- 70-89：良好
- 50-69：一般
- 30-49：偏弱
- 0-29：很差

### 个股评分算法伪代码

```python
def _score_stock_fundamentals(stock_code: str) -> dict:
    """个股5维基本面评分。"""
    # 1. 拉取财务指标（带90天缓存）
    fin = _fetch_stock_financials(stock_code)  # akshare stock_financial_analysis_indicator
    if not fin:
        return _default_score(stock_code, reason="财务数据缺失")

    # 2. 盈利能力评分
    roe = fin.get("roe", 0)
    gross_margin = fin.get("gross_margin", 0)
    net_margin = fin.get("net_margin", 0)
    profitability = _score_profitability(roe, gross_margin, net_margin)

    # 3. 成长性评分
    rev_growth = fin.get("rev_growth", 0)
    profit_growth = fin.get("profit_growth", 0)
    growth = _score_growth(rev_growth, profit_growth, fin.get("history_growth", []))

    # 4. 偿债能力评分
    debt_ratio = fin.get("debt_ratio", 0)
    industry = fin.get("industry", "")
    solvency = _score_solvency(debt_ratio, industry)

    # 5. 稳定性评分（ROE 4季度标准差）
    roe_history = fin.get("roe_history", [])
    stability = _score_stability(roe_history)

    # 6. 估值合理性（复用现有估值数据）
    valuation = _score_valuation_from_pe(stock_code)

    # 7. 加权汇总
    total = (
        profitability * 0.30 + growth * 0.25 + solvency * 0.15 +
        stability * 0.15 + valuation * 0.15
    )
    return {
        "stock_code": stock_code,
        "profitability": {"score": profitability, "reason": "..."},
        "growth": {"score": growth, "reason": "..."},
        "solvency": {"score": solvency, "reason": "..."},
        "stability": {"score": stability, "reason": "..."},
        "valuation": {"score": valuation, "reason": "..."},
        "total": round(total, 1),
        "rating": _score_to_rating(total),
    }
```

### 基金基本面分（加权汇总）

```python
def calculate_fundamental_score(fund_code: str) -> dict:
    """基金基本面评分 = Σ(个股5维分 × 持仓占比)。"""
    holdings = get_fund_holdings(fund_code)  # 复用现有函数
    top_stocks = holdings.get("top_stocks", [])

    if not top_stocks:
        return _default_fundamental(fund_code, reason="无持仓数据")

    # 并行评分Top10重仓股（ThreadPoolExecutor）
    stock_scores = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(_score_stock_fundamentals, s["stock_code"]): s
            for s in top_stocks
        }
        for future in concurrent.futures.as_completed(futures):
            stock = futures[future]
            try:
                score = future.result()
                score["pct_nav"] = stock["pct_nav"]
                score["stock_name"] = stock["stock_name"]
                stock_scores.append(score)
            except Exception as e:
                logger.warning(f"[fundamental] 个股评分失败 {stock['stock_code']}: {e}")

    # 按持仓占比加权（归一化到Top10总占比）
    total_weight = sum(s["pct_nav"] for s in stock_scores if s["pct_nav"] > 0)
    if total_weight == 0:
        return _default_fundamental(fund_code, reason="持仓占比缺失")

    weighted_score = sum(
        s["total"] * (s["pct_nav"] / total_weight)
        for s in stock_scores
    )

    return {
        "fund_code": fund_code,
        "fundamental_score": round(weighted_score, 1),
        "rating": _score_to_rating(weighted_score),
        "stock_scores": stock_scores,  # Top10明细
        "top10_coverage": total_weight,  # Top10占净值比例，反映集中度
        "advice": _fundamental_advice(weighted_score, stock_scores),
    }
```

### 调仓动作判定算法

```python
def analyze_holding_changes(fund_code: str) -> dict:
    """对比本季度 vs 上季度持仓，输出调仓动作。"""
    # 从 fund_stock_holdings 表取最近两个季度快照
    snapshots = list_fund_holdings_snapshots(fund_code, limit=2)
    if len(snapshots) < 2:
        return {"has_history": False, "changes": [], "summary": "无历史快照"}

    current_q, prev_q = snapshots[0], snapshots[1]  # 按report_date倒序
    current_map = {s["stock_code"]: s for s in current_q["holdings"]}
    prev_map = {s["stock_code"]: s for s in prev_q["holdings"]}

    changes = []
    THRESHOLD = 0.5  # 占比变化阈值（%）

    # 新进 + 增持
    for code, cur in current_map.items():
        prev = prev_map.get(code)
        if prev is None:
            changes.append({"stock_code": code, "action": "new", "delta_pct": cur["pct_nav"]})
        else:
            delta = cur["pct_nav"] - prev["pct_nav"]
            if delta > THRESHOLD:
                changes.append({"stock_code": code, "action": "increase", "delta_pct": round(delta, 2)})
            elif delta < -THRESHOLD:
                changes.append({"stock_code": code, "action": "decrease", "delta_pct": round(delta, 2)})

    # 退出
    for code, prev in prev_map.items():
        if code not in current_map:
            changes.append({"stock_code": code, "action": "exit", "delta_pct": -prev["pct_nav"]})

    # 按变化幅度排序
    changes.sort(key=lambda x: abs(x["delta_pct"]), reverse=True)

    return {
        "has_history": True,
        "current_quarter": current_q["report_date"],
        "prev_quarter": prev_q["report_date"],
        "changes": changes,
        "summary": _summarize_changes(changes),
    }
```

---

## 数据降级策略

**单只个股财务数据获取失败时**：
- 该股票评分设为默认中分（50分），reason标注"财务数据缺失"
- 不影响其他个股评分
- 基金基本面分仍按持仓占比加权（缺失股票按50分计入）

**akshare整体不可用时**：
- 基本面维度返回默认中分（50分），rating="fair"
- advice标注"财务数据源不可用，建议稍后重试"
- 不阻塞其他6维评分

**持仓数据缺失时（新基金/债基）**：
- 基本面维度返回默认中分（50分），reason="无股票持仓"
- 债基跳过基本面维度（fundamental_score=None）

---

## API 设计

### 扩展现有API（无需新增端点）

**GET `/api/analysis/fund-quality/{fund_code}`**

现有返回结构（6维）+ 新增第7维 `fundamental`：

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "fund_code": "161725",
    "fund_name": "招商中证白酒指数(LOF)A",
    "total_score": 58.6,
    "rating": "fair",
    "report": {
      "quality": {"score": 63, "rating": "good", "label": "基金质量"},
      "drawdown": {"score": 73, "rating": "good", "label": "回撤恢复"},
      "trend": {"score": 52, "rating": "fair", "label": "趋势均线"},
      "capital": {"score": 40, "rating": "fair", "label": "资金流向"},
      "sentiment": {"score": 60, "rating": "good", "label": "情绪温度"},
      "valuation": {"score": 50, "rating": "fair", "label": "估值水位"},
      "fundamental": {"score": 72, "rating": "good", "label": "基本面"}
    },
    "decision_matrix": {...},
    "duan_yongping_view": "...",
    "advice": "...",
    "details": {
      "quality": {...},
      "fundamental": {
        "fund_code": "161725",
        "fundamental_score": 72.0,
        "rating": "good",
        "top10_coverage": 68.5,
        "stock_scores": [
          {
            "stock_code": "600519",
            "stock_name": "贵州茅台",
            "pct_nav": 14.2,
            "profitability": {"score": 92, "reason": "ROE 30%+，毛利率91%，净利率50%+，盈利能力极强"},
            "growth": {"score": 65, "reason": "营收增速12%，净利润增速15%，稳健增长"},
            "solvency": {"score": 90, "reason": "资产负债率25%，偿债能力优秀"},
            "stability": {"score": 88, "reason": "ROE 4季度标准差1.2%，极稳定"},
            "valuation": {"score": 35, "reason": "PE分位75%，估值偏高"},
            "total": 78.5,
            "rating": "good"
          }
        ],
        "advice": "重仓股基本面良好（72分），盈利能力强。但Top10集中度68.5%偏高，需关注个股风险。"
      },
      "holding_changes": {
        "has_history": true,
        "current_quarter": "2025-09-30",
        "prev_quarter": "2025-06-30",
        "changes": [
          {"stock_code": "000568", "stock_name": "泸州老窖", "action": "increase", "delta_pct": 1.2},
          {"stock_code": "000858", "stock_name": "五粮液", "action": "decrease", "delta_pct": -0.8}
        ],
        "summary": "本季度增持泸州老窖，减持五粮液，调仓力度温和"
      }
    }
  }
}
```

### 总评权重重新分配（7维）

| 维度 | 旧权重（6维） | 新权重（7维） | 说明 |
|------|-------------|-------------|------|
| 基金质量 | 20% | 17% | 略降，部分视角让给基本面 |
| 回撤恢复 | 17% | 15% | 略降 |
| 趋势均线 | 17% | 15% | 略降 |
| 资金流向 | 15% | 13% | 略降 |
| 情绪温度 | 13% | 12% | 略降 |
| 估值水位 | 18% | 15% | 略降，基本面会修正估值陷阱 |
| **基本面** | — | **13%** | 新增 |

权重调整原则：基本面引入后，估值单一维度的重要性下降（避免价值陷阱），其他维度等比例缩减。

**债基/无股票持仓的特殊处理**：
- 债基的 fundamental_score 设为 None，不参与总评计算
- 债基总评权重恢复为旧6维（质量20% / 回撤17% / 趋势17% / 资金15% / 情绪13% / 估值18%）
- 通过 `fund_category in ("bond", "纯债", "混合债")` 或 `top_stocks 为空` 判定是否跳过基本面

---

## 段永平决策矩阵扩展

现有决策矩阵（质量×趋势×估值×情绪）扩展为5因子：

```python
# 旧4因子
quality_rating × trend_direction × valuation_level × sentiment → action

# 新5因子（加入基本面）
quality_rating × fundamental_rating × trend_direction × valuation_level × sentiment → action
```

**新增决策规则**：
- `fundamental_rating == "poor"` → 强制降级到 `wait`（基本面差的不买，即使估值低）
- `fundamental_rating == "excellent" && valuation_level == "low"` → `strong_buy`（质地好+估值低，强化买入）
- `fundamental_rating == "poor" && valuation_level == "low"` → `wait` + 标注"价值陷阱预警"

---

## 前端设计

### EventRadarPage.vue 体检报告扩展

**1. 七维评分条**（在现有6维基础上加1维）：
- 现有：质量/回撤/趋势/资金/情绪/估值 6个进度条
- 新增：基本面进度条（第7个）
- 颜色：与其他维度一致（excellent绿/good蓝/fair黄/poor红）

**2. 调仓动作面板**（新增区块）：
- 标题："季度调仓动作"
- 内容：
  - 本季度 vs 上季度日期标注
  - 4类动作标签：新进（绿）/ 增持（蓝）/ 减持（橙）/ 退出（红）
  - 每条显示：股票名 + 动作 + 变化幅度
  - 无历史快照时显示"无历史数据"

**3. 体检详情弹窗扩展**：
- 现有：6维详情
- 新增：
  - 基本面详情Tab：Top10重仓股5维评分表格
  - 调仓动作Tab：完整调仓动作列表

### UI 风格

- 遵循现有非AI美学风格（CSS变量设计系统）
- 调仓动作颜色与持仓管理页一致
- 重仓股评分表格参考现有持仓表格样式

---

## 开发任务拆解

### Task 1: 数据层 — fund_stock_holdings 表 + CRUD

**文件**：
- 新建：`backend/db/fund_holdings_snapshot.py`
- 修改：`backend/db/__init__.py`

**内容**：
- fund_stock_holdings 建表（init_db 调用）
- CRUD 函数：
  - `save_fund_holdings_snapshot(fund_code, report_date, holdings: list)` — 批量写入（INSERT OR REPLACE）
  - `list_fund_holdings_snapshots(fund_code, limit=4)` — 查最近N个季度快照
  - `get_fund_holdings_snapshot(fund_code, report_date)` — 查指定季度
  - `compare_fund_holdings(fund_code)` — 对比最近两季度，返回差异
- fund_quality_scores 表加3列：fundamental_score / fundamental_detail / holding_changes

### Task 2: 数据层 — 持仓快照写入钩子

**文件**：
- 修改：`backend/db/portfolio.py`

**内容**：
- 在 `get_fund_holdings()` 返回前，异步写入快照
- 提取 akshare 返回的"季度"字段作为 report_date
- 仅持久化 top_stocks（股票持仓），债券/资产配置不入快照表
- 写入失败不影响主流程（try-except 吞异常 + 日志）

### Task 3: 服务层 — 个股财务指标获取

**文件**：
- 修改：`backend/services/fund_analysis.py`

**内容**：
- 新增 `_fetch_stock_financials(stock_code)` 函数：
  - 先查 analysis_cache（key: `stock_financial_{stock_code}`，TTL 90天）
  - 缓存未命中调 `ak.stock_financial_analysis_indicator(symbol=stock_code, start_year=str(datetime.now().year - 1))`（动态取上一年，确保覆盖4个季度）
  - 解析返回 DataFrame，提取最近4个季度的：
    - ROE（加权净资产收益率）
    - 毛利率（销售毛利率）
    - 净利率（销售净利率）
    - 资产负债率
    - 营收同比增速
    - 净利润同比增速
  - 结构化为 dict 返回 + 写入缓存
- 复用现有 `_call_akshare_with_timeout` 超时保护（15秒）

### Task 4: 服务层 — 个股5维评分

**文件**：
- 修改：`backend/services/fund_analysis.py`

**内容**：
- 新增 `_score_stock_fundamentals(stock_code)` 函数（伪代码见评分算法章节）
- 新增5个子评分函数：
  - `_score_profitability(roe, gross_margin, net_margin)` — 盈利能力
  - `_score_growth(rev_growth, profit_growth, history_growth)` — 成长性
  - `_score_solvency(debt_ratio, industry)` — 偿债能力（金融行业特殊处理）
  - `_score_stability(roe_history)` — 稳定性（4季度标准差）
  - `_score_valuation_from_pe(stock_code)` — 估值（复用现有估值数据）

### Task 5: 服务层 — 基金基本面评分 + 调仓追踪

**文件**：
- 修改：`backend/services/fund_analysis.py`

**内容**：
- 新增 `calculate_fundamental_score(fund_code)` 函数：
  - 复用 `get_fund_holdings` 获取Top10
  - 并行评分（ThreadPoolExecutor max_workers=5）
  - 按持仓占比加权汇总
  - 数据降级处理
- 新增 `analyze_holding_changes(fund_code)` 函数：
  - 调 `compare_fund_holdings` 取最近两季度差异
  - 按0.5%阈值判定4类动作
  - 生成调仓摘要

### Task 6: 服务层 — 集成到六维体检报告

**文件**：
- 修改：`backend/services/fund_analysis.py`

**内容**：
- 修改 `calculate_fund_health_report(fund_code)` 函数：
  - 调 `calculate_fundamental_score` 获取第7维
  - 调 `analyze_holding_changes` 获取调仓动作
  - 7维权重重新分配计算总评
  - 扩展返回结构（report + details）
- 修改 `save_fund_quality_score` 调用，传入新字段
- 修改段永平决策矩阵，加入 fundamental_rating 因子

### Task 7: 单元测试

**文件**：
- 新建：`backend/tests/test_fund_fundamental.py`

**内容**（覆盖核心逻辑）：
- `_score_profitability` 各档位评分
- `_score_growth` 含连续下滑扣分
- `_score_solvency` 含金融行业特殊处理
- `_score_stability` 数据不足默认值
- `calculate_fundamental_score` 加权汇总 + 数据降级
- `analyze_holding_changes` 4类动作判定 + 阈值边界
- 调仓动作判定边界（0.5%阈值上下）
- 空持仓/债基场景降级

### Task 8: 前端 — 七维体检报告 + 调仓面板

**文件**：
- 修改：`frontend/src/components/EventRadarPage.vue`

**内容**：
- 体检报告区块：6维进度条 → 7维（加基本面）
- 新增"季度调仓动作"面板：
  - 显示本季度 vs 上季度日期
  - 4类动作标签（颜色区分）
  - 每条：股票名 + 动作 + 变化幅度
- 体检详情弹窗新增2个Tab：
  - "基本面详情"：Top10重仓股5维评分表格
  - "调仓动作"：完整调仓列表
- `loadFundReports` 函数适配新返回结构

### Task 9: 集成测试 + 验证

**内容**：
- 真实基金冒烟测试（161725白酒 / 005827白酒）
- 验证API返回完整7维数据
- 验证调仓动作面板展示
- 验证前端构建成功
- 外部IP访问验证：100.118.231.45:8000/app

---

## 关键设计决策

### 1. 基本面作为独立第7维（不合并到基金质量）

**理由**：基金质量看"基金本身"（经理/费率/规模/跟踪误差），基本面看"基金持仓的公司"（ROE/毛利率），两者视角不同。合并会让维度含义模糊，独立第7维更清晰。

### 2. 财务指标90天TTL缓存（不入独立表）

**理由**：财务指标季度更新，90天TTL足够覆盖一个季报周期。复用 analysis_cache 避免新建表的维护成本。若未来需要做财务指标历史趋势分析，再考虑独立表。

### 3. 持仓快照独立表（fund_stock_holdings）

**理由**：持仓快照需要按季度查询和对比，analysis_cache 的 key-value 结构不适合。独立表支持灵活的 SQL 查询（某基金所有季度 / 某季度所有基金 / 跨季度对比）。

### 4. 调仓阈值 0.5%

**理由**：
- 太小（如0.1%）：会把正常的市场波动误判为调仓
- 太大（如1%）：会漏掉小幅调仓信号
- 0.5%是行业常见的"显著变化"阈值，平衡灵敏度与噪声

### 5. Top10重仓股覆盖范围

**理由**：akshare `fund_portfolio_hold_em` 只返回Top10，覆盖大部分主动基金60-80%仓位。Top10之外持仓太分散，评分意义不大。集中度（top10_coverage）作为辅助指标展示。

### 6. 段永平决策矩阵加 fundamental 因子

**理由**：基本面差的公司即使估值低也可能是价值陷阱。加 fundamental_rating 作为强制约束：poor → wait，可识别价值陷阱。

---

## 风险与边界

### 已知限制

1. **指数基金的局限性**：指数基金持仓固定（跟踪指数），基本面评分反映的是指数成分股质量，而非基金经理选股能力。对指数基金，基本面分更多是"指数质量评估"。
2. **债基跳过基本面**：债基无股票持仓，fundamental_score 设为 None，不参与总评计算（6维权重恢复）。
3. **新基金无历史快照**：上市不足1个季度的基金无法做调仓对比，has_history=false。
4. **财务数据滞后**：季报有1-2个月披露延迟，评分反映的是最近一期已披露数据，非实时。

### 不做的事（YAGNI）

- **不做三大报表完整解析**：stock_financial_analysis_indicator 已含核心指标，三大报表数据量大且本阶段用不到
- **不做行业景气度**：行业景气度更偏向独立的"宏观面"模块，后续阶段考虑
- **不做基金持仓股票的实时行情**：行情数据已在 valuation/market_data 模块，本阶段只用财务指标
- **不做港股/美股重仓股**：akshare 财务接口主要覆盖A股，港股/美股重仓股评分会降级到默认中分

---

## 验收标准

- [ ] `GET /api/analysis/fund-quality/161725` 返回7维评分（含fundamental + holding_changes）
- [ ] fund_stock_holdings 表有持仓快照数据
- [ ] 单元测试全部通过（test_fund_fundamental.py）
- [ ] 前端体检报告显示7维进度条
- [ ] 前端调仓动作面板正常展示
- [ ] 外部IP 100.118.231.45:8000/app 可访问验证
- [ ] 改动提交远程仓库

---

## 后续阶段预告

本阶段完成后，后续3个阶段：

- **第二阶段：大师理念矩阵** — 基于本阶段的基本面数据，实现巴菲特/林奇/博格/马克斯/达利欧多视角决策
- **第三阶段：组合层面智能** — 基于本阶段的个股评分，做基金相关性/风格归因/Alpha-Beta分解
- **第四阶段：决策智能+回测** — 历史信号胜率回测 + 蒙特卡洛模拟 + 多Agent投票
