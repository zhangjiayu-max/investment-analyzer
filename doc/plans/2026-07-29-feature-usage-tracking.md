# 功能使用埋点与价值分析系统 设计稿

> 日期：2026-07-29
> 目标：记录哪些功能经常使用、哪些对理财更有帮助，为后续维护和增强提供数据支撑

## 一、背景与现状

### 现状（调研结论）
- **后端执行记录丰富**：agent_runs / analysis_records / decisions / token_usage / llm_feedback 等表已覆盖执行链路
- **前端零埋点**：不知道用户访问了哪些页面、点了哪些功能、停留多久、从哪跳到哪
- **价值维度缺失**：无法回答"估值分析和持仓诊断哪个对用户理财帮助更大"这类问题

### 目标
1. 记录功能使用频率（哪些功能常用、哪些冷门）
2. 衡量功能价值（哪些功能真正推动了理财决策）
3. 可视化展示，方便定期复盘和资源倾斜

## 二、设计方案

### 核心思路：前端轻量埋点 + 后端价值关联 + 独立分析页

```
用户操作 → 前端异步上报(功能名+时长) → feature_usage表
                                              ↓
后端定时关联 → decision_records(决策转化) → 价值评分 → 功能分析看板
```

### 设计原则
- **只记功能名，不记敏感内容**（不记录指数代码、持仓金额、查询文本）
- **异步上报，不阻塞主流程**（navigator.sendBeacon，页面关闭也能发出）
- **复用现有数据**（决策转化率从 decision_records 聚合，不重复采集）
- **默认开启，可关闭**（`tracking.feature_usage_enabled` 开关控制）

## 三、数据库设计

### 新增表：`feature_usage`（功能使用记录）

```sql
CREATE TABLE IF NOT EXISTS feature_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,          -- 浏览器会话ID（localStorage生成，匿名）
    page_key TEXT NOT NULL,            -- 页面标识：valuation / portfolio / decision / chat ...
    feature_key TEXT,                  -- 功能标识：估值查询 / 持仓诊断 / 决策生成（NULL=页面访问）
    action_type TEXT NOT NULL,         -- 动作类型：page_enter / page_leave / feature_click / feature_complete
    duration_ms INTEGER,               -- 停留时长（page_leave时记录）
    referrer_page TEXT,                -- 来源页面（从哪跳过来）
    created_at TEXT DEFAULT (datetime('localtime'))
);

CREATE INDEX IF NOT EXISTS idx_feature_usage_page ON feature_usage(page_key);
CREATE INDEX IF NOT EXISTS idx_feature_usage_feature ON feature_usage(feature_key);
CREATE INDEX IF NOT EXISTS idx_feature_usage_created ON feature_usage(created_at);
```

### 不新建表：价值关联复用现有表

价值评分从以下现有表聚合，不重复采集：
- `decision_records` — 分析→决策→执行→盈利转化链路
- `decision_reviews` — 用户复盘标记（helpful/neutral/unhelpful）
- `llm_feedback` — 用户点赞/点踩
- `agent_analysis_log` — 各分析类型执行频率和评分

## 四、后端 API 设计

### 上报接口（前端调用）

```
POST /api/feature-usage/track
```
- 单条上报，支持批量
- 接收：`{page_key, feature_key, action_type, duration_ms, referrer_page}`
- session_id 从 Cookie/Header 提取，前端不传
- 响应：`{ok: true}`（始终返回成功，埋点失败不影响用户体验）

### 统计接口（看板调用）

```
GET /api/feature-usage/stats?days=30
```
返回：
```json
{
  "total_sessions": 156,
  "total_actions": 1240,
  "page_ranking": [
    {"page_key": "chat", "visits": 89, "avg_duration_ms": 180000, "value_score": 78},
    {"page_key": "valuation", "visits": 67, "avg_duration_ms": 95000, "value_score": 65}
  ],
  "feature_ranking": [
    {"feature_key": "估值查询", "clicks": 45, "page_key": "valuation", "value_score": 72},
    {"feature_key": "持仓诊断", "clicks": 23, "page_key": "portfolio", "value_score": 81}
  ],
  "value_funnel": {
    "analysis_count": 120,
    "decision_candidates": 35,
    "decisions_adopted": 18,
    "decisions_executed": 12,
    "decisions_profitable": 8
  },
  "daily_trend": [{"date": "2026-07-29", "actions": 45, "sessions": 12}]
}
```

### 价值评分算法

每个功能的价值评分（0-100）由以下因子加权计算：

| 因子 | 权重 | 数据来源 | 说明 |
|------|------|---------|------|
| 决策转化率 | 40% | decision_records | 该功能产生的决策→执行→盈利比例 |
| 用户复盘评分 | 25% | decision_reviews | helpful=+1 / neutral=0 / unhelpful=-1 |
| 用户反馈 | 15% | llm_feedback | 点赞率 |
| 使用深度 | 10% | feature_usage | 平均停留时长 + 完成率 |
| 使用频率 | 10% | feature_usage | 30天内点击次数 |

> 注意：不是所有功能都能关联到决策（如"估值查询"可能不直接产生决策）。未关联决策的功能，价值评分=使用深度50%+使用频率30%+用户反馈20%。

## 五、前端设计

### 埋点机制

新建 `frontend/src/composables/useTracking.js`：

```javascript
// 核心API
trackPageEnter(pageKey)           // 页面进入时调用
trackPageLeave(pageKey)            // 页面离开时计算时长
trackFeatureClick(featureKey)      // 功能点击
trackFeatureComplete(featureKey)   // 功能完成（如分析完成）
```

**实现要点**：
1. `navigator.sendBeacon` 上报，页面关闭也能发出
2. 30秒批量合并上报，减少请求
3. session_id 存 localStorage，匿名标识
4. 开关控制：`tracking.feature_usage_enabled` 默认 true

### 集成点（最小侵入）

在 Home.vue 的页面切换处统一埋点，**无需修改每个组件**：

```vue
<!-- Home.vue -->
<script setup>
watch(activePage, (newPage, oldPage) => {
  // 旧页面离开
  if (oldPage) trackPageLeave(oldPage)
  // 新页面进入
  trackPageEnter(newPage)
})
</script>
```

关键功能点击埋点（5-8个核心功能，不铺满所有按钮）：
- 估值页：查询估值、AI分析
- 持仓页：持仓诊断、补仓建议
- 决策页：生成决策、执行决策
- 对话页：发送消息

### 新增页面：功能分析

`frontend/src/components/analysis/FeatureUsagePage.vue`：

**页面结构**：
1. **概览卡片**：总会话数、总操作数、最常用功能、最高价值功能
2. **功能使用排行**：横向条形图，按使用频率排序，点击查看详情
3. **价值评分排行**：按价值评分排序，标注各因子贡献
4. **决策转化漏斗**：分析→决策→执行→盈利的漏斗图
5. **每日趋势**：折线图，展示30天使用量和会话数趋势

Sidebar 新增导航项"功能分析"，放在"分析记录"下方。

## 六、实施计划

### Phase 1：埋点基础设施（核心）
- [ ] `db/feature_usage.py` — 建表 + CRUD
- [ ] `routers/admin/feature_usage.py` — track + stats API
- [ ] `app.py` 注册路由 + `tracking.feature_usage_enabled` 开关
- [ ] `frontend/src/composables/useTracking.js` — 埋点composable
- [ ] `Home.vue` 集成页面切换埋点

### Phase 2：分析看板
- [ ] `FeatureUsagePage.vue` — 功能分析页面
- [ ] `api/index.js` — 新增统计查询函数
- [ ] `Sidebar.vue` 新增导航项
- [ ] `Home.vue` 注册页面

### Phase 3：价值关联（增强）
- [ ] 价值评分算法实现（后端聚合）
- [ ] 决策转化漏斗数据接口
- [ ] 看板展示价值评分和漏斗图

## 七、注意事项

1. **性能**：埋点用 sendBeacon 异步上报，30秒批量合并，不阻塞主流程
2. **隐私**：只记功能名和时长，不记录查询内容、指数代码、持仓金额
3. **开关**：`tracking.feature_usage_enabled` 默认 true，可在配置页关闭
4. **清理**：90天前的数据自动清理（定时任务，每日03:00）
5. **不做的事**：
   - 不做用户路径回放（太重，且隐私风险高）
   - 不做实时埋点看板（数据延迟1天可接受）
   - 不做A/B测试（当前用户量不支持）
