# 2026-07-18 雷达与关注计划增强 - 第一批设计稿

## 背景

用户反馈：雷达和关注计划功能还有增强空间。经多维度分析，识别出 14 个增强点，按 ROI 排序分三批实施。

本设计稿为**第一批**（高 ROI，1-2 天工作量），包含 3 个增强点：

1. **关注计划退出机制** — 只管买入时机，缺止盈/止损信号
2. **关注计划异常波动预警** — 信号灯只看估值百分位，不看短期波动
3. **雷达事件影响量化** — 事件只有方向，无幅度预估

## 设计原则

- **纯增量**：所有改动均通过独立开关控制，默认关闭，不影响现有逻辑
- **字段加列**：用 `_ensure_column` 给老库升级，无需重建表
- **巡检复用**：异常波动预警复用现有巡检流程 `patrol_watchlist`
- **LLM 开关**：事件影响量化新增 LLM 调用，必须有独立开关
- **前端兼容**：新增字段在 watchlist / market_events 表上，前端读不到时不影响渲染

---

## 增强点 1：关注计划退出机制

### 1.1 字段增量（watchlist 表）

```sql
-- 新增 6 个字段到 watchlist 表
ALTER TABLE watchlist ADD COLUMN target_profit_pct REAL;       -- 目标止盈百分比（如 30 = 30%）
ALTER TABLE watchlist ADD COLUMN stop_loss_pct REAL;           -- 止损百分比（如 10 = 10%）
ALTER TABLE watchlist ADD COLUMN entry_price REAL;             -- 实际买入价（用户手动填或上车时自动写）
ALTER TABLE watchlist ADD COLUMN entry_date TEXT;              -- 买入日期
ALTER TABLE watchlist ADD COLUMN exit_signal TEXT;             -- 退出信号：profit_target/stop_loss/none
ALTER TABLE watchlist ADD COLUMN exit_signal_reason TEXT;      -- 退出信号原因（"已涨 35% 超过止盈 30%"）
```

### 1.2 后端逻辑

**新增函数** `db/watchlist.py`：
- `update_entry_info(item_id, entry_price, entry_date, target_profit_pct, stop_loss_pct)` — 用户上车后更新买入信息
- `get_watchlist_with_exit_status(item_id)` — 计算当前盈亏百分比和退出信号

**巡检逻辑增强** `routers/portfolio/watchlist.py:patrol_watchlist_api`：
```python
# 现有巡检循环内新增退出信号计算
if item.get("entry_price") and item.get("entry_price", 0) > 0 and current_nav:
    pnl_pct = (current_nav - entry_price) / entry_price * 100
    exit_signal = "none"
    exit_reason = ""
    if target_profit_pct and pnl_pct >= target_profit_pct:
        exit_signal = "profit_target"
        exit_reason = f"已涨 {pnl_pct:.1f}%，达到止盈目标 {target_profit_pct}%"
    elif stop_loss_pct and pnl_pct <= -stop_loss_pct:
        exit_signal = "stop_loss"
        exit_reason = f"已跌 {abs(pnl_pct):.1f}%，触发止损 -{stop_loss_pct}%"
    # 写回 watchlist 表
    update_watchlist_item(item_id, exit_signal=exit_signal, exit_signal_reason=exit_reason)
```

**新增 API**：
- `POST /api/watchlist/{item_id}/entry` — 标记上车，写入买入价/日期/止盈/止损
- `GET /api/watchlist/{item_id}/exit-status` — 查询退出信号状态

### 1.3 前端增强（EventRadarPage.vue）

- 关注卡片新增 6 档信号灯（从 4 档扩到 6 档）：
  - 原：green / yellow / red / gray（估值信号）
  - 新增：profit_target（蓝色"止盈"）/ stop_loss（红色"止损"）
- 退出信号优先级高于估值信号，触发时卡片边框变色 + 徽章动画
- 卡片底部新增"上车设置"按钮，弹出表单填写买入价/日期/止盈/止损
- 巡检面板新增"退出信号"统计区，显示当前触发止盈/止损的基金数

### 1.4 配置开关

```python
# orchestration_config 表
watchlist.exit_signal_enabled = false  # 默认关闭（项目硬约束）
watchlist.default_target_profit_pct = 30  # 默认止盈 30%
watchlist.default_stop_loss_pct = 10      # 默认止损 10%
```

---

## 增强点 2：关注计划异常波动预警

### 2.1 字段增量（watchlist 表）

```sql
ALTER TABLE watchlist ADD COLUMN daily_change_pct REAL;       -- 近1日涨跌幅
ALTER TABLE watchlist ADD COLUMN weekly_change_pct REAL;      -- 近5日涨跌幅
ALTER TABLE watchlist ADD COLUMN volatility_alert TEXT;      -- 波动预警级别：severe/warning/none
ALTER TABLE watchlist ADD COLUMN volatility_alert_reason TEXT;-- 预警原因
ALTER TABLE watchlist ADD COLUMN volatility_updated_at TEXT;  -- 预警刷新时间
```

### 2.2 后端逻辑

**新增工具函数** `routers/portfolio/watchlist.py`：
```python
def _calculate_volatility_alert(fund_code: str, current_nav: float) -> tuple[str, str, float, float]:
    """计算异常波动预警。
    
    Returns:
        (alert_level, reason, daily_pct, weekly_pct)
        alert_level: severe (日跌≥3% 或 周跌≥6%) / warning (日跌≥1.5% 或 周跌≥3%) / none
    """
    # 拉取近 7 日净值历史
    from db.nav_history import get_nav_history
    history = get_nav_history(fund_code, days=7)
    if not history or len(history) < 2:
        return "none", "", None, None
    
    daily_pct = (current_nav - history[-1]["nav"]) / history[-1]["nav"] * 100
    weekly_pct = (current_nav - history[0]["nav"]) / history[0]["nav"] * 100 if len(history) >= 6 else None
    
    if daily_pct <= -3.0 or (weekly_pct is not None and weekly_pct <= -6.0):
        return "severe", f"近1日跌{abs(daily_pct):.2f}%" + (f"，近5日跌{abs(weekly_pct):.2f}%" if weekly_pct else ""), daily_pct, weekly_pct
    elif daily_pct <= -1.5 or (weekly_pct is not None and weekly_pct <= -3.0):
        return "warning", f"近1日跌{abs(daily_pct):.2f}%" + (f"，近5日跌{abs(weekly_pct):.2f}%" if weekly_pct else ""), daily_pct, weekly_pct
    return "none", "", daily_pct, weekly_pct
```

**巡检逻辑增强**：在 `patrol_watchlist_api` 循环内调用该函数，结果写回 watchlist 表。

### 2.3 前端增强

- 关注卡片新增"波动预警"徽章：
  - severe：红色脉冲动画 + "异常波动"标签
  - warning：黄色边框 + "关注波动"标签
  - none：不显示
- 卡片展开详情新增"近期波动"区块，展示 daily/weekly 涨跌幅 + mini 折线图（ECharts）
- 巡检面板新增"波动预警"统计区，显示 severe/warning 数量

### 2.4 配置开关

```python
watchlist.volatility_alert_enabled = false  # 默认关闭
watchlist.volatility_severe_daily_threshold = -3.0  # 日跌 3% 触发 severe
watchlist.volatility_severe_weekly_threshold = -6.0
watchlist.volatility_warning_daily_threshold = -1.5
watchlist.volatility_warning_weekly_threshold = -3.0
```

---

## 增强点 3：雷达事件影响量化

### 3.1 字段增量（market_events 表）

```sql
ALTER TABLE market_events ADD COLUMN expected_impact_pct REAL;  -- 预估影响幅度（如 3.5 = +3.5%）
ALTER TABLE market_events ADD COLUMN impact_direction TEXT;     -- 影响方向（up/down/flat）
ALTER TABLE market_events ADD COLUMN impact_duration TEXT;     -- 影响持续期（short_term/medium_term/long_term）
ALTER TABLE market_events ADD COLUMN impact_analysis TEXT;     -- LLM 影响分析全文（缓存）
ALTER TABLE market_events ADD COLUMN impact_analyzed_at TEXT;  -- 分析时间戳
```

### 3.2 后端逻辑

**LLM 提取阶段增强** `services/market/event_radar.py:_extract_events_from_news`：

在 prompt 中新增字段要求：
```
- expected_impact_pct: 预估影响幅度（正负数，如 3.5 表示预估涨 3.5%，-2.0 表示预估跌 2.0%）
- impact_direction: 影响方向（up/down/flat）
- impact_duration: 影响持续期（short_term=1-3天 / medium_term=1-2周 / long_term=超过2周）
```

LLM 返回的 JSON 中包含这些字段，写入 market_events 表。

**新增 API**：
- `POST /api/market-events/{event_id}/analyze-impact` — 触发 LLM 深度解读事件影响，结合用户持仓生成个性化影响分析
  - 开关：`alerts.event_impact_analysis_enabled = false`（默认关闭）
  - LLM 调用：传入事件标题/摘要/affected_sectors + 用户持仓 → 输出个性化影响分析（含具体持仓影响金额估算）
  - 结果缓存到 `impact_analysis` 字段，7 天内不重复调用

### 3.3 前端增强（EventRadarPage.vue）

- 事件卡片新增"影响预估"区块：
  - 大数字展示 `expected_impact_pct`（正数绿色▲，负数红色▼）
  - 标签展示 `impact_duration`（短期/中期/长期）
  - 影响方向用箭头图标
- 事件卡片新增"深度解读"按钮：
  - 点击调用 `analyze-impact` API
  - 弹窗展示 LLM 生成的影响分析全文（含持仓影响估算）
  - 加载中显示 spinner，失败显示"分析失败，请稍后重试"
- 事件列表头部统计区新增"平均影响幅度"指标

### 3.4 配置开关

```python
alerts.event_impact_quantification_enabled = false  # 默认关闭，影响 LLM 提取阶段
alerts.event_impact_analysis_enabled = false         # 默认关闭，影响深度解读按钮
alerts.event_impact_analysis_cache_days = 7          # 深度解读缓存 7 天
```

---

## 实施步骤

### Step 1：数据库迁移
- `db/__init__.py:init_db` 中给 watchlist 表加 11 个新列（退出 6 + 波动 5）
- `db/market_events.py:init_market_events_tables` 中给 market_events 表加 5 个新列

### Step 2：后端开关配置
- 在 `orchestration_config` 表中插入 7 个新开关，全部默认 false

### Step 3：后端逻辑
- `db/watchlist.py` 新增 `update_entry_info()`、`get_watchlist_with_exit_status()` 函数
- `routers/portfolio/watchlist.py`：
  - 新增 `_calculate_volatility_alert()` 工具函数
  - 在 `patrol_watchlist_api` 循环内新增退出信号计算（开关控制）
  - 在 `patrol_watchlist_api` 循环内新增波动预警计算（开关控制）
  - 新增 `POST /api/watchlist/{item_id}/entry` API
  - 新增 `GET /api/watchlist/{item_id}/exit-status` API
- `services/market/event_radar.py`：
  - `_extract_events_from_news` prompt 增加 3 个字段要求
  - 新增 `analyze_event_impact()` 函数（LLM 深度解读）
- `routers/market/__init__.py` 或 `routers/analysis/market_intel.py`：
  - 新增 `POST /api/market-events/{event_id}/analyze-impact` API

### Step 4：前端
- `api/index.js` 新增 3 个 API 调用函数
- `components/market/EventRadarPage.vue`：
  - 关注卡片：信号灯从 4 档扩到 6 档 + 波动徽章 + 上车设置按钮
  - 事件卡片：影响预估区块 + 深度解读按钮
  - 巡检面板：新增退出信号/波动预警统计

### Step 5：单元测试
- `tests/test_watchlist_exit.py`：测试止盈/止损触发逻辑
- `tests/test_volatility_alert.py`：测试波动预警阈值
- `tests/test_event_impact.py`：测试 LLM 影响量化解析

### Step 6：验证
- 启用所有开关，触发一次巡检，确认数据正确写入
- 前端查看卡片显示效果
- 关闭所有开关，确认现有逻辑不受影响

---

## 风险与缓解

| 风险 | 缓解措施 |
|------|---------|
| LLM 影响量化增加成本 | 独立开关 `event_impact_quantification_enabled` 默认关闭，开启时才在提取阶段多输出 3 个字段 |
| 巡检变慢（拉净值历史） | 波动预警计算每基金拉取 7 日净值，控制在 50ms 内；缓存 1 小时 |
| 老库升级失败 | 用 `_ensure_column` 幂等加列，已存在不报错 |
| 前端字段缺失容错 | 新字段用 `v-if` 判空，缺失时降级渲染不报错 |
| 止盈/止损误判 | 必须用户主动设置 entry_price 和 target_profit_pct/stop_loss_pct 才生效，否则跳过 |

---

## 验收标准

### 退出机制
- [ ] 用户上车后可填写买入价/日期/止盈/止损
- [ ] 巡检时正确计算盈亏百分比
- [ ] 触发止盈/止损时前端卡片变色 + 徽章
- [ ] 关闭开关后不影响现有信号灯逻辑

### 异常波动预警
- [ ] 巡检时正确计算日/周涨跌幅
- [ ] 触发 severe/warning 时前端显示徽章
- [ ] 卡片展开能看到 mini 波动折线图
- [ ] 关闭开关后不显示波动徽章

### 事件影响量化
- [ ] LLM 提取阶段输出 expected_impact_pct/impact_direction/impact_duration
- [ ] 前端事件卡片显示影响幅度数字
- [ ] 点击"深度解读"按钮能弹出 LLM 分析结果
- [ ] 7 天内重复点击不重新调用 LLM
- [ ] 关闭开关后 LLM 不输出影响量化字段
