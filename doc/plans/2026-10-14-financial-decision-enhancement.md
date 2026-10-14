# 理财决策导向的系统重构 + 支出记录功能设计稿

日期：2026-10-14
状态：待评审

## 背景与目标

当前系统有 143 张 DB 表、60+ 路由、30 个前端页面、11 个专家 Agent，但**功能重心偏移**：近一半页面是研发自监控（Token 用量/评测/Shadow Mode/RAG 调试），对"日常理财决策"零价值。同时，理财决策最关键的**支出侧数据完全空白**——只有 `user_profiles.monthly_expense` 一个静态数字，无贷款、保险、生活费等分类流水，导致现金流分析、备用金测算、四笔钱配置全部基于拍脑袋数字。

**本次目标**：
1. 新增**每月支出记录**功能（分类+金额+固定项），回填消费给现有现金流/健康度/资金桶模块
2. 裁剪/折叠低价值功能，把系统收敛到"理财决策"主线
3. 让 Agent（wealth_advisor 等）能消费支出数据，给出真正落地的现金流建议

---

## 一、功能全景评估

### 1.1 保留（理财决策主线，13 项）

| 模块 | 页面/路由 | 价值 |
|------|----------|------|
| 每日看板 | `dashboard` | 决策入口 |
| AI 对话 | `chat` + 11 专家 | 核心交互 |
| 持仓管理 | `portfolio` + 交易 + 预警 | 资产侧底座 |
| 决策档案 | `decisions` + `decision-center` | 决策闭环 |
| 估值数据 | `valuation` | 买卖锚点 |
| 风险提示 | `alert-center` | 下行保护 |
| 家庭财务总览 | `family-finance` + `/api/finance-dashboard` | **支出功能落点** |
| 资金桶 | `goal-buckets` | 目标管理 |
| 全账户诊断 | `health-v2`（四笔钱） | 配置体检 |
| 行为诊断 | `behavior` | 认知纠偏 |
| 收益归因 | `attribution` | 复盘 |
| 每日建议 | `daily-advice` | 主动推送 |
| **🆕 支出记录** | 本次新增 | **现金流底座** |

### 1.2 裁剪/折叠（低价值，建议下线或收进"系统"折叠菜单）

| 候选项 | 处理建议 | 理由 |
|--------|---------|------|
| Shadow Mode / 评测集 / Bad Case / 质量仪表盘 | 合并为 1 个「系统」入口，默认隐藏 | 研发自监控，与理财决策无关 |
| RAG 分析 / RagTestPage | 下线前端 | 检索日志调试工具 |
| Token 用量 / 功能分析 / 数据健康 | 移出主导航 | `feature_usage` 表为空，自证无人用 |
| 健康分 v1 | 下线，保留 health-v2 | v2 已覆盖 v1 |
| 策略沙盒 vs 策略回测 | 二合一 | 功能重叠 |
| 估值图片 gallery / 文章管理 / 作者文章 | 折叠到「知识库」子菜单 | 采集管道遗留，数据已结构化入库 |
| analysis 组长尾 API（dip_4pct / compare_diff / trade_pattern 等） | 无页面消费的下线 | 降维护面 |

> 裁剪**只动前端导航与路由注册**，DB 表和代码保留，避免误伤。确认稳定 1 个月后再做代码级清理。

### 1.3 Agent 分工梳理（无需下线，仅标注消费关系）

- **决策直接相关**：wealth_advisor（主动关怀）、behavioral_advisor、allocation_advisor、risk_assessor、KYC 三件套
- **行情专用**：market_analyst / quant / convertible_bond / industry / macro / article
- **支出数据消费者**：wealth_advisor（现金流建议）、allocation_advisor（可投资金额计算）、KYC 画像（自动回写 monthly_expense）

---

## 二、新增功能：每月支出记录

### 2.1 数据模型（`expense_records` 表）

```sql
CREATE TABLE IF NOT EXISTS expense_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL DEFAULT 'default',
    month TEXT NOT NULL,                    -- 'YYYY-MM'，如 '2026-10'
    category TEXT NOT NULL,                 -- 一级分类（见 2.2）
    sub_category TEXT,                      -- 二级分类（自由文本，可空）
    amount REAL NOT NULL,                   -- 金额（正数）
    is_fixed INTEGER NOT NULL DEFAULT 0,    -- 1=固定支出（每月自动复制）
    note TEXT,                              -- 备注
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, month, category, sub_category, note)
);
CREATE INDEX IF NOT EXISTS idx_expense_user_month ON expense_records(user_id, month);
```

聚合视图逻辑（代码层实现，不建物化视图）：
- 月度总支出 = SUM(amount) WHERE month=?
- 固定支出合计 = SUM(amount) WHERE is_fixed=1
- **自动回填** `user_profiles.monthly_expense`（取最近 3 个月均值，平滑波动）

### 2.2 分类体系

**固定支出**（is_fixed=1，每月 1 号自动复制上月）：
- 房贷、车贷、保险（重疾/医疗/寿险/车险/财险）、水电物业、话费宽带、订阅服务、子女教育固定项、赡养费

**浮动支出**（is_fixed=0）：
- 餐饮、交通、购物、娱乐、医疗、教育提升、人情往来、旅游、其他

一级分类用**代码常量**管理（不做 DB 配置表，避免过度设计），前端从 `/api/expense/categories` 读取。

### 2.3 后端 API（新建 `routers/expense.py`，遵循项目 CRUD 惯例）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/expense/categories` | 分类常量 |
| POST | `/api/expense` | 新增记录 |
| GET | `/api/expense?month=2026-10` | 按月查询明细 |
| PUT | `/api/expense/{id}` | 修改 |
| DELETE | `/api/expense/{id}` | 删除（ConfirmDialog 二次确认） |
| GET | `/api/expense/summary?month=` | 月度汇总（总额/固定/浮动/分类占比） |
| GET | `/api/expense/trend?months=6` | 近 N 月趋势 |
| POST | `/api/expense/copy-fixed` | 把上月固定项复制到指定月 |
| GET | `/api/expense/cashflow` | 收支视图（收入 - 支出 = 可投资结余，供 wealth_advisor 消费） |

DB 层：`db/expense.py`，`create/get/list/update/delete/summary/copy_fixed` 标准 CRUD。

**回填钩子**：每次写操作后异步重算 `user_profiles.monthly_expense` = 近 3 个月（含当月）实际记录均值；`monthly_debt_payment` = 房贷+车贷分类合计。这样 finance-dashboard 的 cash_flow、health_v2 的备用金月数、goal_buckets 的可投资金额**零改动自动变准**。

### 2.4 前端（新建 `components/family/ExpenseTracker.vue`）

页面结构（金融终端风格，复用现有 fin-table/mono 数字 token）：
1. **顶部**：月份切换器 + 当月汇总卡（总支出/固定/浮动/结余率，AnimatedNumber）
2. **中部**：录入表单（分类下拉 + 金额 + 固定项开关 + 备注）+「复制上月固定项」按钮
3. **明细表**：分类分组展示，行内编辑/删除（ConfirmDialog）
4. **图表**：分类占比环形图 + 近 6 月趋势柱状图（复用 charts/ 组件）

导航：挂在「家庭财务」组下，与 `family-finance`、`goal-buckets` 并列。

### 2.5 Agent 联动

1. **wealth_advisor**：`generate_proactive_alerts` 增加一条规则——当月支出 > 近 6 月均值 120% 时主动提醒
2. **KYC 画像**：`kyc_learner` 在对话中识别"我每月还房贷 8000"等信号时，高置信直接写入 `expense_records`（is_fixed=1）
3. **现金流注入**：`memory.py build_user_memory_context` 增加 `<cashflow>` 段（月收入/固定支出/浮动支出/可投资结余），所有专家 prompt 可消费
4. **allocation_advisor**：可投资金额 = 收入 - 支出 - 备用金目标，替代现在的静态估算

---

## 三、实施路线图

| 阶段 | 内容 | 验收 |
|------|------|------|
| P1 | `expense_records` 表 + `db/expense.py` + `routers/expense.py` + 回填钩子 | API 冒烟：增删改查 + 回填 user_profiles 生效 |
| P2 | `ExpenseTracker.vue` + 导航挂载 + 图表 | 录入一条房贷记录，finance-dashboard 结余率实时变化 |
| P3 | Agent 联动（wealth_advisor 规则 + KYC 写入 + cashflow 注入） | 对话中问"我这个月能投多少"能引用真实支出 |
| P4 | 导航折叠/裁剪（仅前端层） | 主导航 ≤ 15 项，系统组收进折叠菜单 |
| P5 | 稳定 1 个月后，代码级清理长尾 API | grep 无引用后删除 |

---

## 四、风险与回滚

1. **回填误伤**：`monthly_expense` 被自动覆盖可能与用户 KYC 手填值冲突 → 加 `expense.auto_fill_profile` 开关（system_config），默认开启，可关闭回退手填
2. **固定项复制幂等**：UNIQUE 约束 + copy 时 `INSERT OR IGNORE`，重复点击不产生脏数据
3. **回滚**：P1-P3 各阶段独立 commit；支出功能本身全新增表/路由/组件，回滚 = revert + drop table
4. **裁剪风险**：P4 仅动导航，随时可还原

## 五、验证清单

1. `sqlite3 data/valuations.db ".schema expense_records"` 表结构正确
2. POST 一条「房贷 8000 is_fixed=1」→ GET summary 固定支出=8000
3. 回填后 `user_profiles.monthly_expense` 更新
4. finance-dashboard 页面结余率数字变化
5. 对话"我每月能投多少"→ wealth_advisor 引用支出数据
