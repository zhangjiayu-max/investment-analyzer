# 2026-07-19 雷达与关注计划增强 - 第二批设计稿

## 背景

Batch1 已完成（退出机制 / 异常波动预警 / 事件影响量化，commit `2f65976`）。

Batch2 聚焦"实战派"方向：3 个纯计算、无 LLM 成本、立即见效的增强点：

1. **关注计划自动剔除已上车** — 用户买入基金后，watchlist 仍停留 watching 状态，需手动 mark-as-bought，极易遗漏
2. **事件影响金额估算** — `expected_impact_pct` 是抽象百分比，用户难感知实际冲击金额
3. **事件置信度时间衰减** — 临近 expected_date 的事件仍维持原始置信度，过期未落地的事件不降权会误导决策

## 设计原则

- **纯增量**：所有改动均通过独立开关控制，默认关闭，不影响现有逻辑
- **字段加列**：用 `_ensure_column` 给老库升级，无需重建表
- **零 LLM 成本**：3 个增强点全部为纯计算或 SQL 聚合，不引入任何 LLM 调用
- **前端兼容**：新增字段在 watchlist / market_events 表上，前端读不到时不影响渲染
- **复用现有基础设施**：置信度衰减复用 `_calibrate_confidence`/`_calibrate_direction` 调度时机；金额估算复用 `analyze_event_impact` 缓存机制；自动剔除复用 portfolio 交易写入钩子

---

## 增强点 1：关注计划自动剔除已上车

### 1.1 问题与目标

当前流程：
- 用户在 watchlist 中加入"000001"基金（status=watching）
- 巡检触发 green 信号 → 用户在持仓中买入该基金
- watchlist 中 status 仍为 watching，导致下次巡检继续生成 green 信号 + 告警
- 用户需手动调用 `POST /api/watchlist/{item_id}/mark-bought` 才能停止告警

目标：portfolio 交易写入时，自动同步 watchlist 状态，让关注列表与持仓状态自动对齐。

### 1.2 实现方案

**钩子位置**：`db/portfolio.py` 中的交易写入函数（add_holding / update_holding 等）。

**新增函数** `db/watchlist.py`：
```python
def auto_mark_bought_on_trade(fund_code: str, entry_price: float, entry_date: str) -> int:
    """当 portfolio 中买入某基金时，自动把 watchlist 中同 fund_code 的 watching 项标为 bought。
    
    Returns:
        受影响的行数（0 表示无匹配项）
    """
    conn = _get_conn()
    try:
        cursor = conn.execute("""
            UPDATE watchlist 
            SET status = 'bought',
                entry_price = ?,
                entry_date = ?,
                exit_signal = 'none',
                exit_signal_reason = '',
                updated_at = datetime('now','localtime')
            WHERE fund_code = ? AND status = 'watching'
        """, (entry_price, entry_date, fund_code))
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()
```

**钩子调用点**：在 portfolio 交易写入的 API 层调用（不在 db 层直接调用，避免 db 层耦合）：
- `routers/portfolio/portfolio.py` 中的 add_holding / update_holding / batch_import 等 API
- 调用前检查开关 `watchlist.auto_mark_bought_enabled`

```python
# 示例：add_holding 完成后调用
if get_config_bool("watchlist.auto_mark_bought_enabled", False):
    try:
        affected = auto_mark_bought_on_trade(fund_code, entry_price, entry_date)
        if affected > 0:
            logger.info(f"[watchlist] 自动标记 {fund_code} 为 bought，影响 {affected} 行")
    except Exception as e:
        logger.warning(f"[watchlist] 自动标记买入失败: {e}")
```

### 1.3 前端增强

- 关注列表卡片新增"自动同步"标签（status=bought 且 entry_date 在 7 天内时显示）
- 标签 tooltip 说明："该基金已自动同步持仓买入信息"
- 当用户手动设置 entry_price 后，标签消失（说明用户已确认上车信息）

### 1.4 配置开关

```python
# system_config 表
watchlist.auto_mark_bought_enabled = false  # 默认关闭
```

---

## 增强点 2：事件影响金额估算

### 2.1 问题与目标

当前 `expected_impact_pct` 是抽象百分比（如 -3.5%），用户难感知实际冲击。

目标：结合用户持仓，自动计算"预计影响金额 = expected_impact_pct × holding_weight × portfolio_total_value"，让事件影响从抽象百分比转化为可感知金额。

### 2.2 实现方案

**新增函数** `services/market/event_radar.py`：
```python
def estimate_event_impact_amount(event: dict, holdings: list, portfolio_total: float) -> dict:
    """估算事件对用户持仓的金额影响。
    
    Args:
        event: market_events 行（含 expected_impact_pct, affected_sectors 等）
        holdings: 用户持仓列表
        portfolio_total: 持仓总市值
    
    Returns:
        {
            "total_impact_amount": float,    # 总影响金额（正=利好，负=利空）
            "affected_holdings": [           # 受影响的持仓列表
                {
                    "fund_code": str,
                    "fund_name": str,
                    "weight": float,         # 持仓占比
                    "holding_value": float,   # 持仓市值
                    "impact_pct": float,      # 事件预估影响幅度
                    "impact_amount": float,   # 影响金额 = impact_pct × holding_value
                }
            ],
            "estimated_at": str,
        }
    """
    impact_pct = event.get("expected_impact_pct")
    if impact_pct is None or not holdings or portfolio_total <= 0:
        return {"total_impact_amount": 0.0, "affected_holdings": [], "estimated_at": ""}
    
    affected_sectors = json.loads(event.get("affected_sectors") or "[]")
    affected_themes = json.loads(event.get("affected_themes") or "[]")
    
    affected_holdings = []
    for h in holdings:
        # 判断持仓是否受此事件影响（复用 _determine_relevance 逻辑的简化版）
        h_sectors = _extract_holding_sectors(h)  # 从 index_name/index_code 提取板块
        h_themes = _extract_holding_themes(h)
        if not (set(affected_sectors) & set(h_sectors) or set(affected_themes) & set(h_themes)):
            continue
        
        holding_value = portfolio_total * (h.get("weight", 0) / 100.0)
        impact_amount = impact_pct / 100.0 * holding_value
        affected_holdings.append({
            "fund_code": h.get("fund_code"),
            "fund_name": h.get("fund_name"),
            "weight": h.get("weight", 0),
            "holding_value": round(holding_value, 2),
            "impact_pct": impact_pct,
            "impact_amount": round(impact_amount, 2),
        })
    
    total = sum(a["impact_amount"] for a in affected_holdings)
    return {
        "total_impact_amount": round(total, 2),
        "affected_holdings": sorted(affected_holdings, key=lambda x: abs(x["impact_amount"]), reverse=True),
        "estimated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
```

**新增 API** `routers/market/event_radar.py`：
- `GET /api/alerts/event-radar/{event_id}/impact-amount` — 实时计算事件对用户持仓的金额影响
  - 开关：`alerts.event_impact_amount_enabled`（默认 false）
  - 不缓存（每次调用实时计算，因为持仓会变化）
  - 若事件无 `expected_impact_pct` 字段（影响量化开关未开启时），返回 `{"total_impact_amount": 0, "reason": "事件未启用影响量化"}`

### 2.3 前端增强

- 事件卡片"影响预估"区块新增"金额影响"标签：
  - 显示总影响金额（正数绿色 ▲，负数红色 ▼）
  - 标签格式："预计影响持仓 ¥-3,250（3 只基金）"
  - 点击展开受影响持仓列表（fund_name / weight / impact_amount）
- 事件列表头部统计区新增"今日累计影响金额"指标（汇总所有 imminent/materialized 事件的总金额影响）

### 2.4 配置开关

```python
# system_config 表
alerts.event_impact_amount_enabled = false  # 默认关闭
```

---

## 增强点 3：事件置信度时间衰减

### 3.1 问题与目标

当前置信度只按板块准确率校准，无时间维度：
- 距 expected_date 越近，事件不确定性应越低（置信度应稳定或上升）
- expected_date 已过但未落地（status 仍为 imminent/upcoming），置信度应每日衰减
- 过期未验证的事件（status=expired 但 verification_result 为空）应快速降权

目标：在 `_calibrate_confidence` 基础上叠加时间衰减因子，让陈旧未落地的事件自然降权。

### 3.2 实现方案

**新增函数** `services/market/event_radar.py`：
```python
def _time_decay_factor(event: dict, now: datetime = None) -> float:
    """计算事件置信度的时间衰减因子。
    
    衰减规则：
    - status=upcoming & 距 expected_date > 3 天：factor=1.0（不衰减）
    - status=upcoming & 距 expected_date ≤ 3 天：factor=1.0（临近反而稳定）
    - status=imminent：factor=1.0（即将发生，不衰减）
    - status=materialized：factor=1.0（已发生，验证阶段不衰减）
    - status=expired & verification_result 为空：factor=0.7（30 天内）
    - status=expired & verification_result 为空 & 过期 > 30 天：factor=0.3
    - status=expired & verification_result 非空：factor=1.0（已验证，不衰减）
    
    Returns:
        0.0 ~ 1.0 的衰减因子
    """
    if now is None:
        now = datetime.now()
    
    status = event.get("status", "upcoming")
    verification = event.get("verification_result")
    
    # 已验证的事件不衰减
    if status == "expired" and verification:
        return 1.0
    
    # 未验证的过期事件按过期天数衰减
    if status == "expired" and not verification:
        expired_date = event.get("expired_date")
        if not expired_date:
            return 0.5  # 无过期时间，默认衰减到 0.5
        try:
            exp_dt = datetime.strptime(expired_date[:10], "%Y-%m-%d")
            days_since_expired = (now - exp_dt).days
            if days_since_expired <= 30:
                return 0.7
            else:
                return 0.3
        except Exception:
            return 0.5
    
    # 其他状态不衰减
    return 1.0


def apply_time_decay_to_confidence(event: dict) -> float:
    """对事件置信度应用时间衰减，返回有效置信度。
    
    effective_confidence = original_confidence × time_decay_factor
    """
    original = event.get("confidence", 0.5)
    factor = _time_decay_factor(event)
    return round(original * factor, 3)
```

**集成点**：在 `get_market_event` / `list_market_events` 返回时附加 `effective_confidence` 字段（不修改原 `confidence` 字段，保持数据原始性）。

**前端展示**：事件卡片新增"有效置信度"展示：
- 当 `effective_confidence != confidence` 时，显示两个值："置信度 0.7 → 有效 0.49（时间衰减）"
- 当两者相等时，只显示一个值

**回溯校准集成**：`recalibrate_existing_events` 在校准完成后，对未验证的过期事件应用时间衰减，让前端列表查询时直接看到降权后的有效置信度。

### 3.3 前端增强

- 事件卡片置信度展示区分"原始 / 有效"：
  - 两者相等：显示 "置信度 0.7"
  - 两者不等：显示 "置信度 0.7 ⚠️ 有效 0.49"（黄色警告色）
  - tooltip 说明："该事件过期未验证，已自动降权"
- 事件列表默认按 `effective_confidence` 降序排序（而非 `confidence`）

### 3.4 配置开关

```python
# system_config 表
alerts.event_confidence_time_decay_enabled = false  # 默认关闭
```

---

## 实施步骤

### Step 1：数据库迁移

无需新增字段。Batch2 的 3 个增强点全部基于现有字段计算：
- 自动剔除：复用 watchlist 表的 status / entry_price / entry_date 字段
- 金额估算：复用 market_events 表的 expected_impact_pct 字段 + portfolio 持仓
- 时间衰减：复用 market_events 表的 confidence / status / expired_date / verification_result 字段

### Step 2：后端开关配置

在 `db/config.py:DEFAULT_CONFIGS` 中新增 3 个开关：

```python
# ── Batch2 增强点 1：关注计划自动剔除已上车（2026-07-19，默认关闭） ──
('watchlist.auto_mark_bought_enabled', 'false', '关注计划自动剔除已上车开关：portfolio买入时自动同步watchlist状态', 'watchlist'),

# ── Batch2 增强点 2：事件影响金额估算（2026-07-19，默认关闭） ──
('alerts.event_impact_amount_enabled', 'false', '事件影响金额估算开关：实时计算事件对用户持仓的金额影响', 'alerts'),

# ── Batch2 增强点 3：事件置信度时间衰减（2026-07-19，默认关闭） ──
('alerts.event_confidence_time_decay_enabled', 'false', '事件置信度时间衰减开关：未验证的过期事件自动降权', 'alerts'),
```

### Step 3：后端逻辑

- `db/watchlist.py`：新增 `auto_mark_bought_on_trade()` 函数
- `routers/portfolio/portfolio.py`（或对应交易写入 API）：在 add_holding / update_holding 完成后调用钩子
- `services/market/event_radar.py`：
  - 新增 `estimate_event_impact_amount()` 函数
  - 新增 `_time_decay_factor()` / `apply_time_decay_to_confidence()` 函数
  - `get_market_event` / `list_market_events` 返回时附加 `effective_confidence` 字段（开关控制）
- `routers/market/event_radar.py`：
  - 新增 `GET /api/alerts/event-radar/{event_id}/impact-amount` API

### Step 4：前端

- `api/index.js`：新增 `getEventImpactAmount(eventId)` API 调用函数
- `components/market/EventRadarPage.vue`：
  - 事件卡片"影响预估"区块新增金额标签 + 受影响持仓展开列表
  - 事件卡片置信度展示区分"原始 / 有效"
  - 关注列表卡片新增"自动同步"标签

### Step 5：单元测试

- `tests/test_batch2_enhancements.py`：
  - `TestAutoMarkBought`：portfolio 买入 → watchlist 自动标记 bought / 跨账号不影响 / 重复买入幂等
  - `TestEventImpactAmount`：金额计算 / 持仓为空 / 事件无影响量化字段 / 受影响持仓排序
  - `TestConfidenceTimeDecay`：upcoming 不衰减 / 已验证不衰减 / 未验证过期 30 天内 0.7 / 30 天外 0.3 / effective_confidence 字段附加

### Step 6：验证

- 启用 3 个开关
- 自动剔除：portfolio 买入一只关注中的基金，验证 watchlist status 自动变为 bought
- 金额估算：调用 API，验证返回的金额计算正确
- 时间衰减：构造一个 30 天前过期未验证的事件，验证 effective_confidence = 0.7 × original
- 关闭开关后现有逻辑不受影响

---

## 风险与缓解

| 风险 | 缓解措施 |
|------|---------|
| 自动剔除误判（用户在不同账号买入） | 默认 user_id='default'，跨账号场景通过 user_id 过滤；开关默认关闭，启用前用户需确认 |
| 金额估算依赖持仓准确性 | 持仓为空时返回 0，不影响前端渲染；portfolio_total ≤ 0 时跳过计算 |
| 时间衰减让用户困惑 | 前端同时展示原始/有效置信度，tooltip 说明衰减原因；已验证事件不衰减 |
| 钩子调用失败影响 portfolio 写入 | 钩子用 try/except 包裹，失败只记日志不抛异常 |
| 前端字段缺失容错 | 新字段用 v-if 判空，缺失时降级渲染不报错 |

---

## 验收标准

### 关注计划自动剔除已上车
- [ ] portfolio 买入基金后，watchlist 中同 fund_code 的 watching 项自动变为 bought
- [ ] entry_price / entry_date 自动填充为成交数据
- [ ] 关闭开关后 portfolio 写入不影响 watchlist

### 事件影响金额估算
- [ ] API 返回正确的总影响金额（正=利好，负=利空）
- [ ] 受影响持仓按金额绝对值降序排列
- [ ] 持仓为空或事件无 expected_impact_pct 时返回 0
- [ ] 关闭开关后 API 返回 403/404 或空数据

### 事件置信度时间衰减
- [ ] 未验证的过期事件 effective_confidence < original_confidence
- [ ] 已验证的事件 effective_confidence == original_confidence
- [ ] upcoming / imminent 事件不衰减
- [ ] 前端同时展示原始/有效置信度
- [ ] 关闭开关后 effective_confidence 字段不附加

---

## 与 Batch1 的关系

| Batch1 | Batch2 衔接点 |
|---|---|
| 退出机制 | 自动剔除复用 entry_price / entry_date 字段，自动同步后退出机制可直接生效 |
| 事件影响量化 | 金额估算依赖 expected_impact_pct 字段，Batch1 开关未开启时金额估算自动返回 0 |
| 异常波动预警 | 无直接依赖，独立运行 |

---

## 与现有功能的协同

- **决策候选联动**：金额估算可为决策候选生成提供金额依据（未来扩展点）
- **回溯校准**：时间衰减在回溯校准后应用，不冲突
- **巡检流程**：自动剔除减少巡检无效信号（已上车基金不再生成 green 信号）
