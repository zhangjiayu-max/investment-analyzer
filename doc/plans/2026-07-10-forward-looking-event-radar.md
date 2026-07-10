# 前瞻性事件雷达 — 设计稿

> 日期：2026-07-10
> 状态：待审阅
> 关联：增强 `services/alert_scanner.py` + 新增 `services/event_radar.py`

---

## 1. 背景与目标

### 1.1 问题陈述

当前系统的主动提醒（`alert_scanner.py` 的 3 个扫描函数）全部围绕**持仓维度**：
- 建议到达 T+N 验证窗口 → 验证结果 alert
- 持仓相关指数估值突破阈值 → 估值 alert
- 持仓集中度/亏损超阈值 → 风险 alert

**缺失的能力**：对"尚未发生但即将发生"的市场事件没有前瞻性感知。例如：
- 火箭回收发射——发射日期早有公开消息，但系统事后才从行情异动察觉
- 半导体政策会议、行业峰会、财报披露窗口——公告日之前 1-2 周已是公开信息
- 结果：用户只能在事件落地、行情异动后才收到提示，错过提前布局窗口

### 1.2 目标

构建**前瞻性事件雷达**（Forward-looking Event Radar）：从每日新闻中结构化提取"未来 1-2 周将发生的事件"，按板块/主题归并，匹配用户持仓与候选建仓基金，按 3 级优先级推送提醒。

**一句话目标**：让用户在事件落地前 1-2 周收到"🔍 某事件预计 7 月 18 日发生，可能影响你持有的半导体基金，或可作为建仓机会"。

### 1.3 需求边界（3 轮澄清结论）

| 维度 | 决策 | 来源 |
|------|------|------|
| 标的 | 基金（非股票，有滞后性） | 用户反馈第 2 轮 |
| 扫描频率 | 每晚 20:00 一次（非盘中高频） | 用户反馈第 2 轮 |
| 视野 | 前瞻 1-2 周（非事后通知） | 用户反馈第 2 轮 |
| 推送范围 | 即使无持仓关联也推送（建仓机会） | 用户反馈第 3 轮 |
| 推送分级 | 3 级：持仓影响 / 建仓机会 / 市场关注 | 用户反馈第 3 轮 |

---

## 2. 总体架构

```
┌─────────────────────────────────────────────────────────────┐
│                  每晚 20:00 调度（独立任务）                  │
│        _auto_event_radar_scan()  ← app.py 注册               │
└────────────────────────┬────────────────────────────────────┘
                         ↓
┌────────────────────────────────────────────────────────────┐
│  services/event_radar.py  — scan_forward_events()           │
│                                                             │
│  ┌──────────┐   ┌────────────┐   ┌──────────────────────┐  │
│  │ 1. 新闻  │→ │ 2. LLM 提取 │→ │ 3. 事件去重/状态更新  │  │
│  │   采集   │   │  未来事件  │   │   (market_events 表)  │  │
│  └──────────┘   └────────────┘   └──────────────────────┘  │
│                                            ↓                │
│  ┌──────────────────────────┐   ┌──────────────────────┐   │
│  │ 5. 生成 alert（3 级分级） │← │ 4. 板块→指数→基金匹配 │   │
│  │   写入 alerts 表          │   │  （持仓 + 候选建仓）  │   │
│  └──────────────────────────┘   └──────────────────────┘   │
└────────────────────────────────────────────────────────────┘
                         ↓
┌────────────────────────────────────────────────────────────┐
│  前端 AlertBell 铃铛组件                                      │
│  - 新增 "event_radar" alert 类型图标 🛰️                       │
│  - 卡片显示：事件标题 / 预期日期 / 推送级别 / 关联板块 / 操作建议│
│  - 点击跳转：持仓影响→持仓详情；建仓机会→候选基金列表           │
└────────────────────────────────────────────────────────────┘
```

**关键设计**：
- **独立调度任务**，不复用 `_auto_periodic_scan`（30 分钟间隔太频繁），单独 `_auto_event_radar_scan` 每天 20:00 触发
- **独立开关** `alerts.event_radar_enabled`（默认 false，LLM 相关开关硬约束）
- **复用 alerts 表**，通过 `alert_type='event_radar'` 区分，前端按类型渲染
- **复用 sector_keywords 字典**，与 hotspots.py 共享板块词库

---

## 3. 事件数据模型

### 3.1 market_events 表（新增）

位置：`db/core.py` 的 `init_db()` 中新增 CREATE TABLE。

```sql
CREATE TABLE IF NOT EXISTS market_events (
    event_id TEXT PRIMARY KEY,           -- 事件唯一 ID（hash(title+expected_date)）
    title TEXT NOT NULL,                  -- 事件标题（LLM 提取，≤50 字）
    summary TEXT,                         -- 事件摘要（≤200 字）
    event_type TEXT NOT NULL,             -- 事件分类（见 §4）
    status TEXT NOT NULL DEFAULT 'upcoming', -- 状态（见 §5）
    direction TEXT,                       -- 影响方向：positive/negative/neutral
    confidence REAL DEFAULT 0.5,          -- LLM 置信度 0-1
    
    expected_date TEXT,                   -- 预期发生日期（YYYY-MM-DD）
    detected_date TEXT NOT NULL,          -- 首次检测到日期
    materialized_date TEXT,               -- 实际落地日期（status=materialized 时填）
    expired_date TEXT,                    -- 过期日期（status=expired 时填）
    
    affected_sectors TEXT,                -- JSON 数组：["半导体","军工"]
    affected_themes TEXT,                 -- JSON 数组：["国产替代","火箭回收"]
    
    relevance_to_user TEXT NOT NULL DEFAULT 'market_watch',
                                          -- 推送分级：holding_impact/opportunity/market_watch
    matched_holdings TEXT,                -- JSON 数组：[{"fund_code":"005918","fund_name":"...","match_reason":"跟踪半导体指数"}]
    candidate_funds TEXT,                 -- JSON 数组：[{"fund_code":"161031","fund_name":"...","match_reason":"跟踪航天指数"}]
    
    sources TEXT,                         -- JSON 数组：[{"url":"...","title":"...","publish_date":"..."}]
    timeline TEXT,                        -- JSON 数组：[{"date":"...","event":"首次检测"},{"date":"...","event":"状态更新为 imminent"}]
    
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_market_events_status ON market_events(status);
CREATE INDEX IF NOT EXISTS idx_market_events_expected ON market_events(expected_date);
CREATE INDEX IF NOT EXISTS idx_market_events_relevance ON market_events(relevance_to_user);
```

### 3.2 事件字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `event_id` | TEXT PK | `sha1(title + expected_date)[:16]` 保证幂等 |
| `event_type` | TEXT | 见 §4 事件分类树 |
| `status` | TEXT | upcoming / imminent / materialized / expired |
| `direction` | TEXT | positive=利好 / negative=利空 / neutral=中性 |
| `confidence` | REAL | LLM 自评 0-1，低于 0.4 不推送 |
| `expected_date` | TEXT | LLM 从新闻提取（如"7 月 18 日"→"2026-07-18"） |
| `relevance_to_user` | TEXT | 3 级推送分级（见 §6） |
| `matched_holdings` | JSON | 命中用户当前持仓的基金列表 |
| `candidate_funds` | JSON | 无持仓关联但可作为建仓候选的基金列表 |
| `sources` | JSON | 事件来源新闻列表（≥1 条） |
| `timeline` | JSON | 事件状态变更轨迹 |

---

## 4. 事件分类树

```
event_type
├── policy        政策类（法规/会议/规划）
│   ├── monetary       货币政策（降息/LPR/MLF）
│   ├── fiscal         财政政策（减税/专项债）
│   ├── industry       产业政策（新能源补贴/半导体扶持）
│   └── regulatory     监管政策（IPO/再融资/行业整顿）
├── industry      产业类（事件/峰会/投产）
│   ├── summit         行业峰会/展会（半导体大会/新能源车展）
│   ├── product        产品发布（苹果发布会/火箭发射）
│   ├── capacity       产能投产/投产爬坡
│   └── breakthrough   技术突破（回收成功/制程突破）
├── earnings      财报类（披露窗口）
│   ├── quarterly      季报披露（一季报/年报）
│   ├── guidance       业绩预告/快报
│   └── dividend       分红/回购
├── capital       资本类（解禁/增发/IPO）
│   ├── unlock         限售解禁
│   ├── placement      定增/配股
│   └── ipo            大型 IPO 上市
├── macro         宏观类（数据/海外）
│   ├── data           经济数据公布（CPI/PMI/GDP）
│   ├── overseas       海外事件（美联储议息/非农）
│   └── commodity      大宗商品（OPEC+会议/原油）
└── theme         主题类（概念/季节性）
    ├── seasonal       季节性（双 11/春运/暑期档）
    ├── concept        概念炒作（元宇宙/AIGC）
    └── black_swan     黑天鹅预警（疫情/地缘冲突）
```

**分类用途**：前端按 `event_type` 一级分类做色彩区分（政策红/产业蓝/财报绿/资本橙/宏观紫/主题灰），不影响业务逻辑。

---

## 5. 事件状态流转

```
                  ┌──────────────────────────────────────┐
                  │                                      ↓
   detected  →  upcoming  ──(T-3日内)──→  imminent  ──(到达 expected_date)──→  materialized
                    │                                          │
                    └────────────(超 expected_date + 7 天)──────→  expired
```

| 状态 | 触发条件 | 行为 |
|------|----------|------|
| `upcoming` | 首次检测到，距 expected_date > 3 天 | 推送 1 次提醒 |
| `imminent` | 距 expected_date ≤ 3 天 | 升级为紧急提醒，铃铛变红 |
| `materialized` | 当前日期 ≥ expected_date | 扫描后续行情验证，关联建议验证 |
| `expired` | 当前日期 > expected_date + 7 天 | 归档不再扫描，时间线收尾 |

**状态更新逻辑**（每次扫描执行）：
1. 查询所有 `status IN ('upcoming','imminent')` 的事件
2. 计算距 `expected_date` 天数：
   - `today >= expected_date` → 更新为 `materialized`，写 `materialized_date`
   - `today > expected_date + 7` → 更新为 `expired`，写 `expired_date`
   - `today <= expected_date - 3 且 status='imminent'` → 不变（避免抖动）
   - `today > expected_date - 3 且 status='upcoming'` → 升级为 `imminent`
3. 每次 status 变更追加 `timeline` 记录

---

## 6. 3 级推送分级

### 6.1 分级定义

| 级别 | `relevance_to_user` | 图标 | 触发条件 | 前端表现 |
|------|---------------------|------|----------|----------|
| 持仓影响 | `holding_impact` | 🔴 | 事件命中用户当前持仓基金跟踪的指数 | 高优先级，铃铛红点+声音 |
| 建仓机会 | `opportunity` | 🟡 | 事件未命中持仓，但板块有候选跟踪基金 | 中优先级，铃铛黄点 |
| 市场关注 | `market_watch` | 🔵 | 事件影响宏观/政策面，无明确板块对应 | 低优先级，铃铛蓝点 |

### 6.2 分级判定逻辑

```python
def _determine_relevance(event, user_holdings, sector_to_index, index_to_funds):
    """
    判定推送分级：
    1. 持仓影响：事件的 affected_sectors 命中持仓基金跟踪指数对应的板块
    2. 建仓机会：未命中持仓，但 affected_sectors 有候选跟踪基金
    3. 市场关注：影响宏观/政策面，或无板块对应
    """
    matched_holdings = []
    candidate_funds = []
    
    for sector in event.get("affected_sectors", []):
        index_codes = sector_to_index.get(sector, [])
        for idx_code in index_codes:
            # 1. 检查持仓基金是否跟踪该指数
            for h in user_holdings:
                if h.get("index_code") == idx_code:
                    matched_holdings.append({
                        "fund_code": h["fund_code"],
                        "fund_name": h["fund_name"],
                        "match_reason": f"跟踪 {sector} 相关指数 {idx_code}"
                    })
            
            # 2. 收集候选建仓基金（非持仓）
            if not matched_holdings:
                candidate_funds = index_to_funds.get(idx_code, [])
    
    if matched_holdings:
        return "holding_impact", matched_holdings, []
    elif candidate_funds:
        return "opportunity", [], candidate_funds[:5]  # 最多 5 只
    else:
        return "market_watch", [], []
```

### 6.3 与用户反馈的对应

- **"就算我没有持仓也可以推送"** → `opportunity` 级别：即使 `matched_holdings=[]`，只要板块有候选跟踪基金，仍生成 alert
- **"我们可以建仓购买"** → alert 卡片附"查看候选基金"按钮，跳转到候选列表
- **"基金有滞后性，每天晚上扫描一次即可"** → 调度仅每晚 20:00 一次，不做盘中高频

---

## 7. LLM 事件提取

### 7.1 数据源

复用现有新闻获取能力，按优先级：

| 优先级 | 数据源 | 实现位置 | 备注 |
|--------|--------|----------|------|
| 1 | 盈米且慢 MCP | `mcp/yingmi_client.py` | 已有，每日新闻摘要 |
| 2 | 东财妙想 MCP | `mcp/eastmoney_client.py` | 已有，财经新闻检索 |
| 3 | akshare 财经新闻 | `services/market_data.py` | 兜底，免费 |

**采集策略**：
- 每晚 20:00 拉取最近 24 小时的新闻（去重）
- 单次最多 50 条（控制 LLM 成本）
- 跨源去重：按标题相似度（jieba 分词 + Jaccard > 0.7）

### 7.2 LLM 提取 Prompt

```
你是一位资深财经分析师。请从以下新闻列表中提取「即将在未来 1-2 周内发生」的市场事件。

【新闻列表】
{news_json}

【输出要求】
仅输出 JSON 数组，每个事件包含：
- title: 事件标题（≤50 字，主谓宾完整）
- summary: 事件摘要（≤200 字，说明影响）
- event_type: 事件分类（policy/industry/earnings/capital/macro/theme）
- direction: 影响方向（positive/negative/neutral）
- expected_date: 预期发生日期（YYYY-MM-DD 格式，从新闻推断）
- affected_sectors: 受影响板块（数组，从以下选取：半导体/人工智能/新能源/消费/医药/金融/地产/军工/教育/体育/传媒/汽车/基建/科技/农业/环保/有色/化工）
- affected_themes: 受影响主题（数组，自由文本如"国产替代""火箭回收"）
- confidence: 置信度（0-1，1 表示高度确定会发生）

【过滤规则】
1. 只提取"即将发生"的事件，不提取"已经发生"的新闻
2. 跳过模糊时间（如"近期""未来"），必须能推断到具体日期
3. 跳过无市场影响的事件（如纯娱乐八卦）
4. 单条新闻可提取 0-2 个事件，最多输出 15 个事件
5. expected_date 必须在 [今天, 今天+14天] 范围内

【示例】
输入：{"title":"SpaceX 宣布 7 月 18 日进行星舰第六次试飞，首次尝试用机械臂回收超重型助推器"}
输出：
[{
  "title": "SpaceX 星舰第六次试飞首次尝试助推器回收",
  "summary": "7 月 18 日星舰试飞首次尝试用机械臂回收超重型助推器，若成功将验证完全可复用火箭技术，利好商业航天产业链",
  "event_type": "industry",
  "direction": "positive",
  "expected_date": "2026-07-18",
  "affected_sectors": ["军工"],
  "affected_themes": ["火箭回收", "商业航天"],
  "confidence": 0.95
}]

只输出 JSON 数组，不要其他解释。
```

### 7.3 LLM 调用参数

```python
resp = _call_llm(
    caller="event_radar_extractor",
    trace_id=trace_id,
    model=MODEL,
    messages=[{"role": "user", "content": prompt}],
    temperature=0.1,    # 低温度保证结构化
    max_tokens=2000,    # 15 事件 × ~130 token
)
```

**成本管控**：
- 每晚 1 次调用，单次 ~3000 input token + ~2000 output token
- 月成本估算：30 × 5000 token × 单价 ≈ 可控
- 失败降级：LLM 异常时跳过本次扫描，记录日志，不影响其他 alert

---

## 8. 板块→指数→基金匹配

### 8.1 映射链路

```
事件.affected_sectors (如 "军工")
        ↓ sector_to_index 映射表
跟踪指数代码 (如 "399967" 中证军工)
        ↓ fund_metadata.tracking_index 查询
持仓基金 / 候选建仓基金列表
```

### 8.2 sector_to_index 映射表（新增）

位置：`services/event_radar.py` 内常量（参考 hotspots.py 的 sector_keywords 结构）。

```python
SECTOR_TO_INDEX = {
    "半导体": ["990001", "H30184"],           # 国证芯片 / 中证全指半导体
    "人工智能": ["930713", "931071"],          # 中证人工智能 / 中证 AI 主题
    "新能源": ["399808", "931151"],            # 中证新能源 / 新能源车
    "消费": ["000932", "399932"],              # 中证消费 / 消费领先
    "医药": ["930791", "000993"],              # 中证医药 / 全指医药
    "金融": ["399949", "930601"],              # 证券公司 / 银行
    "地产": ["931775", "399393"],              # 国证房地产 / 房地产
    "军工": ["399967", "930798"],              # 中证军工 / 中证国防
    "教育": ["930711"],                        # 中证教育
    "体育": ["930711"],                        # 复用教育（无独立指数）
    "传媒": ["930681", "930901"],              # 中证传媒 / 中证文娱
    "汽车": ["930758", "399975"],              # 中证汽车 / 汽车指数
    "基建": ["399388", "930608"],              # 国证基建 / 基建工程
    "科技": ["931087", "930986"],              # 中证科技 / 中证科技龙头
    "农业": ["930687", "000936"],              # 中证农业 / 中证大农业
    "环保": ["930790", "930615"],              # 中证环保 / 中证新材
    "有色": ["930708", "399395"],              # 中证有色 / 国证有色
    "化工": ["930695", "930751"],              # 中证化工 / 中证精细化工
}
```

> 注：代码需在实现阶段对照 `fund_metadata` 表实际数据校准；现有 `services/index_fund_mapper.py` 已有 `_normalize_index_code` 等工具可复用。

### 8.3 候选基金查询逻辑

```python
def _find_candidate_funds(index_code: str, exclude_codes: set) -> list[dict]:
    """查询跟踪该指数的候选建仓基金（排除已持仓）。
    
    复用 fund_metadata.tracking_index 列做精确匹配。
    """
    conn = _get_conn()
    rows = conn.execute("""
        SELECT fund_code, fund_name, fund_type, tracking_index
        FROM fund_metadata
        WHERE tracking_index = ?
          AND fund_code NOT IN (?, ?, ?, ?)  -- 排除持仓（动态占位）
        ORDER BY fund_type, fund_code
        LIMIT 5
    """, (index_code, *exclude_codes)).fetchall()
    conn.close()
    return [dict(r) for r in rows]
```

### 8.4 映射表维护

- 沿用现有约束：**映射表需从持仓表动态生成而非硬编码**（project_memory）
- 启动时调用 `services/index_fund_mapper.backfill_recommendations_target_fund` 模式回填
- 无内置映射的指数通过基金详情接口的 `tracking_index` 字段精确匹配

---

## 9. 调度集成

### 9.1 新增调度任务

位置：`backend/app.py`，仿照 `_auto_periodic_scan` 模式新增 `_auto_event_radar_scan`。

```python
async def _auto_event_radar_scan():
    """前瞻性事件雷达 — 每晚 20:00 扫描一次。
    
    从新闻中提取未来 1-2 周的市场事件，匹配持仓/候选基金，生成 3 级 alert。
    
    开关：alerts.event_radar_enabled（默认 false，LLM 相关开关硬约束）
    调度：每晚 20:00 一次（不走 _auto_periodic_scan 的 30 分钟间隔）
    """
    import time
    try:
        await asyncio.sleep(120)  # 等启动完成（比 _auto_periodic_scan 晚 1 分钟避免抢资源）
        
        while True:
            # 计算距下次 20:00 的等待秒数
            now = datetime.now()
            target = now.replace(hour=20, minute=0, second=0, microsecond=0)
            if now >= target:
                target = target + timedelta(days=1)
            wait_seconds = (target - now).total_seconds()
            await asyncio.sleep(wait_seconds)
            
            if get_config("alerts.event_radar_enabled", "false") != "true":
                continue
                
            try:
                from services.event_radar import scan_forward_events
                result = scan_forward_events()
                logging.info(
                    f"[event-radar] 扫描完成: "
                    f"extracted={result.get('extracted', 0)}, "
                    f"new={result.get('new', 0)}, "
                    f"updated={result.get('updated', 0)}, "
                    f"alerts={result.get('alerts_created', 0)}"
                )
            except Exception as e:
                logging.warning(f"[event-radar] 扫描异常: {e}")
    except Exception as e:
        logging.warning(f"前瞻事件雷达任务异常: {e}")
```

### 9.2 启动注册

位置：`app.py` 的 `startup()` 函数中，紧跟 `_auto_periodic_scan` 注册之后。

```python
# 前瞻性事件雷达（每晚 20:00，默认关闭，LLM 相关开关硬约束）
if get_config("alerts.event_radar_enabled", "false") == "true":
    asyncio.create_task(_auto_event_radar_scan())
    logging.info("前瞻事件雷达任务已启动（alerts.event_radar_enabled=true）")
else:
    logging.info("前瞻事件雷达已关闭（alerts.event_radar_enabled=false）")
```

### 9.3 手动触发端点

提供手动触发接口便于测试与即时扫描：

```python
@router.post("/api/alerts/event-radar/scan")
def manual_event_radar_scan():
    """手动触发前瞻事件雷达扫描。"""
    from services.event_radar import scan_forward_events
    result = scan_forward_events()
    return {"ok": True, "data": result}
```

---

## 10. 前端展示

### 10.1 AlertBell 铃铛组件扩展

位置：`frontend/src/components/AlertBell.vue`（现有组件）。

**改动点**：
1. `alert_type` 路由新增 `event_radar` 分支
2. 图标使用 🛰️（与持仓风险/估值阈值等区分）
3. 卡片样式按 `relevance_to_user` 分级着色：
   - 🔴 `holding_impact`：红色边框 + "持仓影响"标签
   - 🟡 `opportunity`：黄色边框 + "建仓机会"标签
   - 🔵 `market_watch`：蓝色边框 + "市场关注"标签

### 10.2 事件卡片结构

```
┌─────────────────────────────────────────────────┐
│ 🛰️ SpaceX 星舰第六次试飞首次尝试助推器回收    🔴 │  ← 标题 + 推送级别
│ ─────────────────────────────────────────────── │
│ 📅 2026-07-18（距今 8 天）  🏷️ industry         │  ← 日期 + 分类
│ 📊 受影响板块：军工                              │
│ 📈 方向：利好  置信度：95%                       │
│ ─────────────────────────────────────────────── │
│ 摘要：7 月 18 日星舰试飞首次尝试用机械臂回收    │
│ 超重型助推器，若成功将验证完全可复用火箭技术...  │
│ ─────────────────────────────────────────────── │
│ 💼 关联持仓：                                   │
│   • 国泰航天ETF (501031) — 跟踪军工指数         │
│ ─────────────────────────────────────────────── │
│ [查看事件详情]  [跟踪持仓]  [不再提醒]           │  ← 操作按钮
└─────────────────────────────────────────────────┘
```

### 10.3 建仓机会卡片差异

当 `relevance_to_user = opportunity` 时：
- 头部标签改为 🟡 "建仓机会"
- "关联持仓"区改为 "🎯 候选建仓基金"（展示 `candidate_funds` 前 3 只）
- 操作按钮增加 "查看候选基金" → 跳转到基金详情/对比页

### 10.4 与现有建议卡片的关系

- **建议（recommendations）**：来自 LLM 对话分析，触发动作（加仓/减仓）
- **事件 alert（event_radar）**：来自新闻扫描，提供前瞻信息
- 二者**不混用**，通过 `alert_type` 区分；事件 alert 不进入建议验证 T+N 流程

---

## 11. 配置开关

新增配置项（默认值遵循 LLM 相关开关硬约束）：

| 开关 | 默认 | 说明 |
|------|------|------|
| `alerts.event_radar_enabled` | `false` | 总开关（LLM 相关，默认关） |
| `alerts.event_radar_lookforward_days` | `14` | 前瞻视野天数（1-14） |
| `alerts.event_radar_max_events` | `15` | 单次扫描最多提取事件数 |
| `alerts.event_radar_min_confidence` | `0.4` | 低于此置信度不推送 |
| `alerts.event_radar_scan_time` | `20:00` | 每日扫描时间（HH:MM） |
| `alerts.event_radar_news_sources` | `yingmi,eastmoney,akshare` | 新闻源（逗号分隔） |
| `alerts.event_radar_max_candidate_funds` | `5` | 建仓机会卡片最多展示基金数 |

---

## 12. 与现有系统的关系

### 12.1 复用

| 现有能力 | 复用方式 |
|----------|----------|
| `_auto_periodic_scan` 调度模式 | 仿照实现 `_auto_event_radar_scan`，独立间隔 |
| `db.portfolio.create_alert` | 写入 alerts 表，`alert_type='event_radar'` |
| `services/index_fund_mapper` | 板块→指数→基金映射工具 |
| `fund_metadata.tracking_index` 列 | 精确查询跟踪基金 |
| `sector_keywords` 字典（hotspots.py） | 板块关键词词库 |
| `services/llm_service._call_llm` | LLM 调用 + 重试 + trace_id |
| `alert_news_service.py` MCP 新闻获取 | 新闻数据源 |
| 前端 AlertBell | alert_type 路由扩展 |

### 12.2 不改动

- 不改 `_auto_periodic_scan` 内部逻辑（保持持仓维度扫描独立）
- 不改 recommendations 表（事件 alert 不进入 T+N 验证流）
- 不改现有 alert_scanner.py 3 个扫描函数

### 12.3 新增

| 新增 | 位置 |
|------|------|
| `market_events` 表 | `db/core.py` init_db + `db/market_events.py` CRUD |
| `services/event_radar.py` | 扫描主逻辑（提取/去重/状态更新/分级/生成 alert） |
| `_auto_event_radar_scan` | `app.py` 调度任务 |
| `/api/alerts/event-radar/scan` | 手动触发端点 |
| `/api/alerts/event-radar/events` | 事件列表查询端点 |
| `/api/alerts/event-radar/events/{id}` | 事件详情端点 |
| 前端事件卡片渲染分支 | `AlertBell.vue` |

---

## 13. 实施分阶段

### P0（最小可用，1 个迭代）
- `market_events` 表 + CRUD
- `services/event_radar.py`：新闻采集 + LLM 提取 + 去重 + 写表
- `scan_forward_events()` 主流程
- `_auto_event_radar_scan` 调度注册
- 基础 alert 生成（仅 `holding_impact` + `market_watch` 两级）
- 前端 AlertBell 渲染 `event_radar` 类型

### P1（完整版，1 个迭代）
- `opportunity` 建仓机会分级 + 候选基金匹配
- 事件状态流转（upcoming→imminent→materialized→expired）
- 前端 3 级卡片样式
- `/api/alerts/event-radar/events` 列表/详情端点
- 手动触发端点

### P2（增强，可选）
- 事件落地后行情验证（materialized 时扫描相关指数涨跌幅）
- 与建议系统联动（materialized 事件触发专家分析）
- 事件统计报表（命中率/准确率）
- 多源新闻融合去重优化

---

## 14. 风险与限制

| 风险 | 缓解 |
|------|------|
| LLM 提取事件不准 | confidence < 0.4 不推送；人工触发端点便于校验 |
| 新闻源不稳定 | 3 级降级（盈米→东财→akshare），任一可用即可 |
| 板块→指数映射不准 | 启动时回填校准；无映射的指数通过基金详情接口精确匹配 |
| 推送过多干扰用户 | 单事件只推送 1 次（状态变更时再推），前瞻 14 天限制 |
| expected_date 推断错误 | LLM prompt 强制 [今天, 今天+14] 范围校验 |
| 成本失控 | 每晚 1 次调度，单次 ~5000 token；总开关默认 false |

---

## 15. 验收标准

1. 开启 `alerts.event_radar_enabled=true` 后，每晚 20:00 自动扫描
2. 扫描结果写入 `market_events` 表，可在 `/api/alerts/event-radar/events` 查询
3. 命中持仓的事件生成 🔴 级 alert，铃铛显示红点
4. 无持仓关联但有候选基金的事件生成 🟡 级 alert
5. 宏观/政策类事件生成 🔵 级 alert
6. 事件状态自动流转：upcoming→imminent→materialized→expired
7. 前端铃铛点击事件卡片，可查看详情与关联持仓/候选基金
8. 手动触发端点 `/api/alerts/event-radar/scan` 可即时执行扫描
9. LLM 调用失败时降级跳过，不影响其他 alert 功能
10. 关闭总开关后不产生任何事件 alert

---

## 16. 后续演进方向（非本期）

- 事件订阅：用户可按板块/主题订阅感兴趣的事件类型
- 事件回顾：materialized 事件 7 天后生成回顾报告（预期 vs 实际）
- 跨事件关联：多个相关事件聚合为"主题"（如"商业航天主题"含火箭回收+卫星发射+星座组网）
- 个性化置信度：根据用户历史采纳记录校准事件置信度权重
