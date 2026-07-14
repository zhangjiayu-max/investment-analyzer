# 决策智能+回测设计稿（第四阶段）

> **阶段目标**：补齐"大师决策历史回测"缺失，让6位大师的建议可被T+N验证和胜率统计，形成"评分→落库→验证→胜率→校准"闭环。

## 1. 设计背景

### 现状
- 大师矩阵（第二阶段）每次实时计算，**不落库**
- 系统已有6套验证/回测机制（recommendations T+N、决策T+7/T+30、策略回测、事件T+3、预警周回测、建议准确率聚合）
- **唯一缺失**：大师决策历史存储+回测

### 第四阶段目标
1. **大师决策历史表**：每次大师评分落库（action≠hold的才记录）
2. **T+N自动回测**：复用 `auto_backtest_decisions` 模式，T+7和T+30验证大师建议
3. **大师胜率统计**：按master_key分组的准确率、平均收益、最佳/最差大师
4. **前端面板**：大师历史决策列表+胜率统计+收益对比

## 2. 模块设计

### 2.1 数据层 — master_decision_history表

```sql
CREATE TABLE master_decision_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    master_key TEXT NOT NULL,          -- buffett/lynch/bogle/marks/dalio/duanyongping
    master_name TEXT NOT NULL,
    fund_code TEXT NOT NULL,
    fund_name TEXT,
    target_type TEXT DEFAULT 'fund',   -- fund/portfolio
    action TEXT NOT NULL,              -- strong_buy/dca/hold/reduce/wait
    score REAL,                        -- 大师评分0-100
    confidence REAL DEFAULT 0.5,
    reason TEXT,
    snapshot_json TEXT,                -- 完整大师评分快照
    baseline_price REAL,               -- 建议时基金净值
    baseline_date TEXT NOT NULL,
    -- T+N验证结果
    verify_7d_result TEXT,             -- pending/correct/wrong/flat
    verify_7d_change_pct REAL,
    verify_7d_verified_at TEXT,
    verify_30d_result TEXT,
    verify_30d_change_pct REAL,
    verify_30d_verified_at TEXT,
    created_at TIMESTAMP DEFAULT (datetime('now','localtime'))
);
CREATE INDEX idx_master_key ON master_decision_history(master_key);
CREATE INDEX idx_fund_code ON master_decision_history(fund_code);
CREATE INDEX idx_created_at ON master_decision_history(created_at);
CREATE INDEX idx_verify_7d ON master_decision_history(verify_7d_result);
```

### 2.2 落库点 — fund_analysis.py

在 `calculate_fund_health_report` 调用 `build_master_perspectives_matrix` 后：
- 遍历 `master_perspectives["masters"]`
- 对 action ∈ (strong_buy, dca, reduce, wait) 的大师写入历史表
- action=hold 不记录（持有建议无需验证）
- 记录 baseline_price（当前基金净值）和 baseline_date

### 2.3 回测引擎 — services/master_decision_backtest.py

```python
def auto_backtest_master_decisions() -> dict:
    """T+7和T+30自动回测大师决策。

    复用 db/decisions.py:auto_backtest_decisions 的 baseline+end+benchmark 模式。
    """
    # T+7：验证创建于[14,7]天前、verify_7d_result=pending的记录
    # T+30：验证创建于[60,30]天前、verify_30d_result=pending的记录
    # 判定逻辑：
    #   strong_buy/dca → 期望涨，涨跌幅≥2%判correct，≤-2%判wrong，中间flat
    #   reduce → 期望跌，涨跌幅≤-2%判correct，≥2%判wrong
    #   wait → 中性，对比沪深300基准
```

### 2.4 胜率统计

```python
def get_master_accuracy_stats(days: int = 90) -> dict:
    """大师胜率统计。

    Returns:
    - per_master: [{master_key, master_name, total, verified, correct, wrong, flat, win_rate, avg_change}]
    - best_master: 胜率最高的大师
    - worst_master: 胜率最低的大师
    - overall: {total, verified, win_rate}
    - by_action: 按action分组的胜率
    """
```

## 3. API设计

```
GET /api/analysis/master-backtest/history?master_key=&fund_code=&days=90
    → 大师决策历史列表

GET /api/analysis/master-backtest/stats?days=90
    → 大师胜率统计

POST /api/analysis/master-backtest/verify
    → 手动触发T+N验证
```

## 4. 前端UI方案

### 大师决策回测面板（EventRadarPage.vue）
- **胜率统计卡片**：6位大师的胜率排名
- **历史决策列表**：最近N条大师决策+验证结果
- **验证结果标签**：correct绿/wrong红/flat灰/pending橙

## 5. 开发任务分解

### Task 1: 数据层 — master_decision_history表 + CRUD
### Task 2: 落库点 — fund_analysis.py集成
### Task 3: 回测引擎 — master_decision_backtest.py
### Task 4: 路由 + 前端API
### Task 5: 单元测试
### Task 6: 前端面板
### Task 7: 集成验证 + 提交

## 6. 硬约束遵循

- ✅ 不新增LLM调用（纯量化回测）
- ✅ 复用现有 auto_backtest_decisions 模式
- ✅ API响应遵循{code, message, data}标准
- ✅ 前端API调用使用相对路径
- ✅ 所有改动完成后自动推送远程git仓库
