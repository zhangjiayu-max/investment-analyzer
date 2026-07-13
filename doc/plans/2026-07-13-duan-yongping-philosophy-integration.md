# 段永平投资哲学集成方案——5 项增强设计稿

> 基于段永平（大道）思维操作系统，将"商业模式优先""本分""能力圈""平常心""农夫思维"六大心智模型
> 融入 investment-analyzer 多 Agent 系统，补上当前系统缺失的"投资哲学层"。

---

## 一、背景与动机

### 1.1 当前系统能力盘点

investment-analyzer 已具备：

| 已有能力 | 实现方式 |
|---------|---------|
| 多 Agent 编排 | Orchestrator + Specialist Agents |
| 估值分析 | PE/PB 分位点 + 历史对比 |
| RAG 知识库 | FTS5 + ChromaDB 混合检索 |
| 持仓管理 | 多账户、盈亏追踪 |
| KYC 用户画像 | 风险偏好、投资经验 |
| 决策记录 | decision_records 表 |
| 行为教练 | 简单的风险提示 |
| 评测系统 | LLM-as-Judge |

### 1.2 缺失的能力层

当前系统偏"量化分析"和"流程管理"，缺少**投资哲学层**——即"这件事本身对不对"的判断。

具体缺失：

- ❌ 没有"能力圈"检查——用户不懂的领域也敢建议
- ❌ 没有"商业模式"评分——只看估值不看生意好坏
- ❌ 没有"本分"过滤器——价值观层面的决策把关
- ❌ 没有"农夫 vs 猎人"行为检测——频繁操作的代价没量化
- ❌ 没有"长期持有"追踪——用户不知道自己"折腾"亏了多少

### 1.3 设计目标

在现有系统之上，新增 5 个模块，形成"量化分析 + 投资哲学"双层架构：

```
┌─────────────────────────────────────────┐
│         投资哲学层（新增）                │
│  能力圈守卫 · 商业模式评分 · 本分检查     │
│  行为偏差检测 · 农夫型持仓追踪           │
├─────────────────────────────────────────┤
│         量化分析层（已有）                │
│  估值分析 · 仓位管理 · RAG 知识库        │
│  KYC · 决策记录 · 评测系统              │
└─────────────────────────────────────────┘
```

---

## 二、5 项增强详细设计

### 2.1 P0：能力圈守卫 Agent（Circle of Competence Guard）

**段永平语录**：「不懂不投不是谦虚，是纪律。」

#### 功能描述

在用户请求分析某只股票/行业时，自动检查用户是否"懂"这个领域。

#### 触发时机

- 用户首次询问某个行业/个股
- 用户要求生成买入/卖出决策草案
- 用户持仓发生重大变化（新增不熟悉的标的）

#### 实现方案

```python
# 新增 Agent: circle_of_competence_agent
class CircleOfCompetenceAgent:
    """
    能力圈检查 Agent
    """
    def check(self, user_id, stock_code, industry):
        checks = {
            "持仓经验": self._has_position_history(user_id, industry),
            "行业认知": self._assess_industry_knowledge(user_id, stock_code),
            "周期理解": self._assess_cycle_awareness(user_id, industry),
            "竞争格局": self._assess_competitive_landscape(user_id, stock_code),
        }
        
        confidence = sum(checks.values()) / len(checks)
        
        if confidence < 0.5:
            return {
                "action": "downgrade",
                "level": "观察",
                "message": f"你对 {industry} 的了解程度较低。建议先学习再考虑行动。",
                "learning_materials": self._suggest_learning(industry),
            }
        elif confidence < 0.7:
            return {
                "action": "caution",
                "level": "小仓位尝试",
                "message": f"你对 {industry} 有一定了解，但建议控制仓位在 5% 以内。",
            }
        else:
            return {
                "action": "proceed",
                "level": "正常分析",
                "message": f"你对 {industry} 比较熟悉，可以正常评估。",
            }
    
    def _assess_industry_knowledge(self, user_id, stock_code):
        """评估用户对该行业的了解程度"""
        # 1. 用户历史对话中是否讨论过该行业？
        # 2. 用户持仓中是否有同行业股票？
        # 3. 用户能否回答行业基本问题？（可选：主动提问）
        # 4. 知识库中是否有该用户对该行业的学习记录？
        pass
    
    def _suggest_learning(self, industry):
        """推荐学习材料"""
        # 从知识库检索行业基础知识
        # 推荐相关阅读材料
        pass
```

#### 数据需求

| 数据 | 来源 | 说明 |
|------|------|------|
| 用户行业持仓历史 | `portfolios` 表 | 判断用户是否买过同行业股票 |
| 用户对话历史 | `conversations` 表 | 提取用户对该行业的讨论 |
| 行业分类映射 | 外部数据 / 配置文件 | stock_code → industry 映射 |
| 行业学习材料 | RAG 知识库 | 基础知识 + 周期规律 |

#### 前后端改动

- **后端**：新增 `circle_of_competence_agent.py`
- **前端**：决策草案生成前弹出能力圈评估卡片
- **数据**：无需新表，复用现有对话和持仓数据

---

### 2.2 P0：本分检查清单（Benfen Checklist）

**段永平语录**：「做对的事，然后把事情做对。顺序不能反。」

#### 功能描述

在每次生成决策草案后、保存决策记录前，弹出"本分检查清单"，强制用户回答 6 个问题。

#### 检查清单

```python
BENFEN_CHECKLIST = [
    {
        "id": "long_term",
        "question": "这个决策是基于长期判断还是短期情绪？",
        "hint": "如果你在 10 年后回头看，这个决定是对的吗？",
        "pass_condition": "长期判断",
    },
    {
        "id": "fomo",
        "question": "你是不是因为别人赚钱了才想买？（FOMO）",
        "hint": "投资不需要抓住每一个机会，只需要抓住你看得懂的那一个。",
        "pass_condition": "否",
    },
    {
        "id": "panic",
        "question": "你是不是因为跌了害怕才想卖？（恐慌）",
        "hint": "买股票就是买公司，跌了不等于公司变坏了。",
        "pass_condition": "否",
    },
    {
        "id": "borrow",
        "question": "你用的是不是借来的钱？",
        "hint": "千万别借钱，因为没有人知道市场疯起来有多疯狂。",
        "pass_condition": "否",
    },
    {
        "id": "understand",
        "question": "这个生意你真的懂吗？能说清楚它怎么赚钱吗？",
        "hint": "如果说不清楚，你买的是什么？",
        "pass_condition": "能",
    },
    {
        "id": "price_vs_value",
        "question": "你买的是价格还是价值？",
        "hint": "价格是你付出的，价值是你得到的。",
        "pass_condition": "价值",
    },
]
```

#### 流程

```
用户请求 → Agent 分析 → 生成决策草案
                           ↓
                    弹出"本分检查清单"
                           ↓
                   ┌─ 全部通过 → 保存决策记录
                   │
                   └─ 有未通过项 → 降级为"观察/学习"
                                   记录未通过原因
```

#### 数据需求

| 数据 | 来源 | 说明 |
|------|------|------|
| 检查结果 | 新增字段 | `decision_records` 表新增 `benfen_check_result` JSON 字段 |
| 降级原因 | 同上 | 记录哪些项未通过 |

#### 前后端改动

- **后端**：在 `decision_records` 保存前插入检查逻辑
- **前端**：新增"本分检查"弹窗组件
- **数据**：`decision_records` 表新增 `benfen_check_result` 字段

---

### 2.3 P1：商业模式评分 Agent（Business Model Scorer）

**段永平语录**：「商业模式就是公司赚钱的模式。投资的第一件事不是看价格，是看这个公司怎么赚钱。」

#### 功能描述

在估值分析之前，先对公司的"生意质量"进行评分。商业模式不好的公司，再便宜也不推荐。

#### 评分维度

```python
BUSINESS_MODEL_DIMENSIONS = {
    "asset_intensity": {
        "name": "资产轻重",
        "levels": ["轻资产", "中资产", "重资产"],
        "description": "重资产企业周期性强，需要持续资本投入",
        "weight": 0.20,
    },
    "differentiation": {
        "name": "差异化能力",
        "levels": ["强", "弱", "无"],
        "description": "产品有没有定价权？客户为什么选你而不是别人？",
        "weight": 0.25,
    },
    "switching_cost": {
        "name": "客户转换成本",
        "levels": ["高", "中", "低"],
        "description": "用户离开了还能不能轻易回来？",
        "weight": 0.20,
    },
    "cyclicality": {
        "name": "周期性强弱",
        "levels": ["弱周期", "中周期", "强周期"],
        "description": "利润是否随经济周期剧烈波动？",
        "weight": 0.15,
    },
    "visibility_10y": {
        "name": "10年可见性",
        "levels": ["清晰", "模糊", "看不清"],
        "description": "10年后这个公司还在不在？还在不在赚钱？",
        "weight": 0.10,
    },
    "management_trust": {
        "name": "管理层可信度",
        "levels": ["高", "中", "低"],
        "description": "管理层是否本分？是否说真话？是否对股东负责？",
        "weight": 0.10,
    },
}
```

#### 评分逻辑

```python
def score_business_model(stock_code):
    scores = {}
    for dim_id, dim in BUSINESS_MODEL_DIMENSIONS.items():
        # 1. 从知识库检索该公司的相关信息
        # 2. LLM 评估每个维度
        # 3. 人工可覆写
        scores[dim_id] = llm_evaluate(stock_code, dim)
    
    total = sum(scores[d] * BUSINESS_MODEL_DIMENSIONS[d]["weight"] for d in scores)
    
    # 分类
    if total >= 0.7:
        category = "好生意"
        action = "可以深入研究估值"
    elif total >= 0.4:
        category = "一般生意"
        action = "估值合理才可以考虑"
    else:
        category = "坏生意"
        action = "不建议投资，不管多便宜"
    
    return {
        "score": total,
        "category": category,
        "action": action,
        "dimensions": scores,
    }
```

#### 与估值分析的关系

```
投资分析新流程：

商业模式评分（先做）
  ├─ 好生意 → 进入估值分析
  ├─ 一般生意 → 进入估值分析，但标注"谨慎"
  └─ 坏生意 → 直接终止，不进入估值分析
  
估值分析（后做）
  ├─ 好生意 + 好价格 → 考虑买入
  ├─ 好生意 + 坏价格 → 等待
  ├─ 坏生意 + 好价格 → 不买
  └─ 坏生意 + 坏价格 → 坚决不碰
```

#### 数据需求

| 数据 | 来源 | 说明 |
|------|------|------|
| 行业分类 | 外部数据 | 判断资产轻重、周期性强弱 |
| 公司护城河分析 | RAG 知识库 + LLM | 差异化、转换成本 |
| 管理层评价 | 新闻 + 公开信息 | 本分程度、可信度 |
| 10 年可见性 | LLM 推断 | 基于行业趋势和技术演进 |

#### 前后端改动

- **后端**：新增 `business_model_scorer_agent.py`
- **前端**：估值分析页面新增"商业模式评分"卡片
- **数据**：`analysis_results` 表新增 `business_model_score` 字段

---

### 2.4 P1：行为偏差检测 Agent（Behavioral Bias Detector）

**段永平语录**：「投资不是打猎，是种地。你急也没用，季节到了自然会收获。」

#### 功能描述

量化检测用户的"猎人行为"，在用户做出非理性操作时发出警告。

#### 检测规则

```python
BEHAVIORAL_ALERTS = [
    {
        "id": "buy_sell_3d",
        "trigger": "买完 3 天内就想卖",
        "message": "你是在打猎，不是在种地。刚种下的庄稼，三天就想收割？",
        "severity": "high",
    },
    {
        "id": "full_position",
        "trigger": "想全仓买一只股票",
        "message": "5% 仓位和全仓，功力差 20 年。看懂了才敢重仓，不是赌。",
        "severity": "high",
    },
    {
        "id": "frequent_sell_intent",
        "trigger": "7 天内超过 3 次卖出意图",
        "message": "你最近想卖了太多次。你是不是在盯着 K 线看？",
        "severity": "medium",
    },
    {
        "id": "panic_sell",
        "trigger": "持仓跌幅超 5% 就想割肉",
        "message": "跌了 5% 不是公司坏了，是市场在波动。你买的是公司还是股票？",
        "severity": "high",
    },
    {
        "id": "chase_hot",
        "trigger": "连续 3 次询问同一个热门行业",
        "message": "你是不是因为别人赚钱了才想买？用找行业状元的办法很容易投到模式不好的公司。",
        "severity": "medium",
    },
    {
        "id": "borrow_money",
        "trigger": "提及借钱、杠杆、融资买入",
        "message": "千万别借钱。没有人知道市场疯起来有多疯狂。",
        "severity": "critical",
    },
]
```

#### 行为报告

```python
def generate_behavior_report(user_id, period="30d"):
    return {
        "trade_count": 15,                # 本月交易次数
        "avg_hold_days": 8.3,             # 平均持有天数
        "chase_count": 4,                 # 追涨次数
        "panic_count": 3,                 # 恐慌卖出次数
        "fomo_count": 6,                  # FOMO 询问次数
        "alerts_triggered": 8,            # 触发警告次数
        "comparison": {
            "actual_return": -2.3,        # 实际收益率
            "buy_and_hold_return": +5.1,  # 如果一直持有的收益率
            "difference": -7.4,           # 折腾亏了多少
        },
        "duan_quote": "投资是农夫的活，不是猎人的活。",
    }
```

#### 数据需求

| 数据 | 来源 | 说明 |
|------|------|------|
| 交易记录 | `transactions` 表 | 交易频率、持有天数 |
| 对话记录 | `conversations` 表 | 意图提取、情绪分析 |
| 持仓变动 | `portfolios` 表 | 仓位变化 |
| 基准收益 | 外部数据 | 买入持有 vs 实际操作对比 |

#### 前后端改动

- **后端**：新增 `behavioral_bias_detector_agent.py`
- **前端**：新增"行为报告"页面 / 卡片
- **数据**：新增 `behavior_logs` 表

---

### 2.5 P2：农夫型持仓追踪（Farmer-Style Holding Tracker）

**段永平语录**：「不想持有十年，就不要持有十分钟。」

#### 功能描述

追踪长期持有 vs 频繁操作的收益差异，用数据证明"少折腾"的价值。

#### 核心功能

**1. 持有天数排行榜**

```
你的持仓：
🥇 沪深300 ETF — 持有 847 天 — 收益 +32.5%
🥈 茅台 — 持有 520 天 — 收益 +18.7%
🥉 长鑫科技 — 持有 3 天 — 收益 -2.1%

结论：持有时间越长，收益越稳定。
```

**2. "如果一直持有"模拟**

```
2025 年 3 月你买入某股票，6 月卖出。

实际收益：+8%
如果一直持有到现在：+45%
你"卖飞"了 37% 的收益。

过去 12 个月，你累计"卖飞"了：
  - 3 次
  - 合计少赚 52,800 元
```

**3. 定期投资哲学报告**

```
📊 2026 年 Q2 投资行为报告

操作次数：47 次
平均持有天数：8.3 天

如果什么都不做，你的收益会高 8.3%。

最大收益来源：长期持仓（+35%）
最大亏损来源：短线操作（-12%）

段永平说：投资是农夫的活，不是猎人的活。
你的行为更像猎人。建议减少操作频率。
```

#### 数据需求

| 数据 | 来源 | 说明 |
|------|------|------|
| 完整交易历史 | `transactions` 表 | 所有买卖记录 |
| 历史行情数据 | 外部数据 | 用于计算"如果一直持有" |
| 持仓变动日志 | `portfolio_changes` 表 | 每次持仓变化的时间和原因 |

#### 前后端改动

- **后端**：新增 `holding_tracker.py`
- **前端**：新增"持有分析"页面
- **数据**：新增 `holding_analysis_cache` 表

---

## 三、系统架构调整

### 3.1 Agent 体系扩展

```
现有 Agent 体系：
  Orchestrator
  ├── market_research_agent
  ├── valuation_agent
  ├── position_advisor_agent
  ├── risk_manager_agent
  └── behavioral_coach_agent

新增 Agent：
  ├── circle_of_competence_agent    # P0 能力圈守卫
  ├── business_model_scorer_agent   # P1 商业模式评分
  ├── benfen_check_agent            # P0 本分检查
  └── behavioral_bias_detector      # P1 行为偏差检测（增强行为教练）
```

### 3.2 决策流程变更

```
用户请求
    ↓
能力圈检查（新）          ← 不懂 → 降级为"观察/学习"
    ↓ 通过
商业模式评分（新）        ← 坏生意 → 终止分析
    ↓ 通过
估值分析（已有）
    ↓
仓位建议（已有）
    ↓
本分检查（新）            ← 未通过 → 降级
    ↓ 通过
行为偏差检测（已有+增强）  ← 触发告警 → 警告但不阻止
    ↓
保存决策记录
```

### 3.3 数据表变更

| 表 | 变更 | 说明 |
|----|------|------|
| `decision_records` | 新增 `benfen_check_result` JSON | 本分检查结果 |
| `decision_records` | 新增 `circle_confidence` FLOAT | 能力圈置信度 |
| `analysis_results` | 新增 `business_model_score` JSON | 商业模式评分 |
| `behavior_logs` | **新建** | 行为偏差日志 |
| `holding_analysis_cache` | **新建** | 持有分析缓存 |

---

## 四、实施路线图

### Phase 1：最小可行方案（P0，1-2 周）

| 任务 | 工作量 | 产出 |
|------|--------|------|
| 能力圈守卫 Agent 开发 | 3 天 | 后端 Agent + API |
| 本分检查弹窗组件 | 2 天 | 前端组件 + 流程集成 |
| decision_records 表变更 | 0.5 天 | 数据库迁移 |
| 联调测试 | 1 天 | 测试用例 |

### Phase 2：核心增强（P1，2-3 周）

| 任务 | 工作量 | 产出 |
|------|--------|------|
| 商业模式评分 Agent | 5 天 | 评分维度 + LLM 评估 + 前端卡片 |
| 行为偏差检测增强 | 3 天 | 检测规则 + 行为报告 |
| behavior_logs 表 | 0.5 天 | 数据库迁移 |
| 联调测试 | 1 天 | 测试用例 |

### Phase 3：长期价值（P2，2-3 周）

| 任务 | 工作量 | 产出 |
|------|--------|------|
| 农夫型持仓追踪 | 5 天 | 持有分析 + 定期报告 |
| holding_analysis_cache 表 | 0.5 天 | 数据库迁移 |
| "卖飞"统计 | 2 天 | 对比算法 + 前端展示 |
| 联调测试 | 1 天 | 测试用例 |

---

## 五、成功指标

| 指标 | 当前 | 目标 | 衡量方式 |
|------|------|------|---------|
| 用户平均持有天数 | 待统计 | 提升 50% | `transactions` 表 |
| "不懂就买"拦截率 | 0% | > 80% | 能力圈检查触发次数 |
| 本分检查通过率 | - | > 70% | `benfen_check_result` |
| 行为告警触发后操作取消率 | - | > 40% | `behavior_logs` |
| 用户"卖飞"总金额下降 | - | 下降 30% | 持有分析 |

---

## 六、风险与注意事项

1. **能力圈判断的准确性**：初期依赖 LLM 推断，可能存在误判。建议加入人工校准机制。
2. **商业模式评分的客观性**：LLM 评分可能有偏差，需要人工审核和定期校准。
3. **用户抵触心理**：过多的检查和告警可能让用户烦躁。建议初期温和提醒，后续根据用户反馈调整强度。
4. **数据隐私**：行为偏差检测涉及用户操作习惯分析，需要明确告知并获得同意。

---

> 📅 创建日期：2026-07-13
> 📁 关联文档：2026-06-20-investment-assistant-enhancement-blueprint.md
> 🔗 参考技能：duan-yongping-perspective