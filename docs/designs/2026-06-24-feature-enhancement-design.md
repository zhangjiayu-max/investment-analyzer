# 投资分析器功能增强设计稿

> 日期：2026-06-24
> 目标：让所有AI分析功能从"看完就忘"变成"可执行的行动"，真正增强理财能力

---

## 一、问题诊断

当前 20 个分析模块，核心问题：

| 问题 | 影响 | 涉及模块 |
|------|------|---------|
| 分析结果只是一段文本，看完就关 | 分析不产生实际价值 | 所有模块 |
| 推荐没有事后验证 | 不知道分析准不准 | 热点分析、债券推荐 |
| 数据展示≠分析 | 用户不知道怎么用 | 恐贪/FED/股债配比 |
| 死功能占位 | 浪费认知成本 | 情景推演、对比分析 |

---

## 二、P0：分析结果 → 可执行行动

### 2.1 统一行动框架

**核心思路**：每个分析结果末尾，自动提取可执行建议，生成"下一步行动"卡片。

**行动类型**：
```
ACTION_WATCH     → 加入关注列表
ACTION_BUY       → 创建买入决策
ACTION_SELL      → 创建卖出决策
ACTION_REDUCE    → 创建减仓决策
ACTION_REPLACE   → 创建替换建议（费率优化）
ACTION_REBALANCE → 创建再平衡决策
ACTION_REVIEW    → 设置定时复盘
```

**实现方案**：

#### 后端：统一行动提取器 `backend/analysis/action_extractor.py`

```python
class ActionExtractor:
    """从分析文本中提取可执行行动。"""

    def extract_actions(self, analysis_type: str, result: dict, holdings: list) -> list[dict]:
        """
        输入：分析类型 + 分析结果 + 当前持仓
        输出：行动建议列表
        [
            {
                "action_type": "buy",
                "target_name": "中证红利",
                "target_code": "09051",
                "reason": "估值百分位12%，极度低估",
                "priority": "high",
                "estimated_amount": 5000,  # 建议金额
                "source": "hotspots_analysis",
                "source_id": 123
            }
        ]
        """
```

**行动提取逻辑（按分析类型）**：

| 分析类型 | 提取规则 |
|---------|---------|
| 热点分析 | direction=up → ACTION_WATCH/BUY, direction=down → ACTION_REDUCE |
| 健康分 | 维度得分<60 → 对应改进行动 |
| 费率分析 | 费率>同类均值1.5倍 → ACTION_REPLACE |
| 相关性分析 | 相关性>0.8 → ACTION_REBALANCE（加入低相关资产） |
| 全景诊断 | 提取"建议"段落中的具体操作 |
| 滚动收益 | 胜率<50% → ACTION_REVIEW |
| 四笔钱 | 活钱>30% → ACTION_REBALANCE |

#### 前端：行动卡片组件 `ActionCard.vue`

```vue
<template>
  <div class="action-card" :class="action.priority">
    <div class="action-header">
      <span class="action-icon">{{ icon }}</span>
      <span class="action-type">{{ label }}</span>
      <span class="action-priority">{{ priorityLabel }}</span>
    </div>
    <div class="action-body">
      <p class="action-target">{{ action.target_name }}</p>
      <p class="action-reason">{{ action.reason }}</p>
      <p v-if="action.estimated_amount" class="action-amount">
        建议金额：¥{{ action.estimated_amount.toLocaleString() }}
      </p>
    </div>
    <div class="action-buttons">
      <button @click="createDecision">创建决策</button>
      <button @click="addWatchlist">加入关注</button>
      <button @click="dismiss">忽略</button>
    </div>
  </div>
</template>
```

### 2.2 健康分 — 具体改进建议

**当前状态**：只显示5维度分数和总分
**目标**：每个低分维度给出具体改进建议

**改进建议模板**：

| 维度 | 低分原因 | 改进建议 |
|------|---------|---------|
| 质量分 | 持有太多亏损基金 | "你的基金中X只亏损超过20%，建议止损或定投摊薄" |
| 分散度 | 持仓集中 | "你的持仓中X%集中在消费行业，建议加入科技/医药/债券" |
| 估值分 | 买入高估基金 | "XX基金估值百分位85%，处于高估区间，建议减仓或止盈" |
| 行为分 | 频繁交易 | "你近30天交易X次，频繁交易会增加费率损耗" |
| 风险分 | 单只基金占比过高 | "XX基金占总仓位X%，超过30%警戒线，建议分散" |

**实现**：在 `health_score.py` 的 `calc_health_score()` 返回结果中增加 `improvements` 字段。

### 2.3 费率分析 — 替换建议

**当前状态**：只显示费率拖累金额
**目标**：给出同类低费率替代基金

**实现方案**：
1. 在费率分析结果中，对高费率基金，从估值数据中找同指数的低费率基金
2. 计算替换后的费率节省金额
3. 生成 ACTION_REPLACE 行动

**数据来源**：
- 基金费率：从 `fund_info` 或 akshare 获取
- 替代基金：从 `index_valuations` 中找同指数基金，按费率排序

### 2.4 相关性分析 — 配置建议

**当前状态**：显示相关性矩阵
**目标**：识别高相关资产对，建议低相关替代

**实现方案**：
1. 找出相关性>0.8 的基金对
2. 对每对，建议一个低相关替代（从不同资产类别中选）
3. 生成 ACTION_REBALANCE 行动

**低相关资产推荐逻辑**：
- 股票+债券 → 低相关
- 消费+科技 → 中等相关
- A股+港股 → 中等相关
- 同行业基金 → 高相关

---

## 三、P1：推荐自动验证

### 3.1 推荐价格快照

**当前状态**：推荐记录中有 `baseline_price`，但没有自动触发验证
**目标**：推荐时自动记录价格，7天/30天后自动验证

**实现方案**：

#### 后端：推荐验证器 `backend/analysis/recommendation_verifier.py`

```python
async def verify_recommendations():
    """定时任务：验证历史推荐的准确性。"""
    # 1. 获取所有未验证的推荐（created_at > 7天前）
    unverified = get_unverified_recommendations(days_ago=7)

    for rec in unverified:
        # 2. 获取当前价格
        current_price = get_index_current_price(rec.index_code)
        if not current_price:
            continue

        # 3. 计算收益率
        baseline = rec.baseline_price
        if baseline and baseline > 0:
            return_pct = (current_price - baseline) / baseline

            # 4. 更新验证结果
            verify_recommendation(rec.id, {
                "verified_price": current_price,
                "return_pct": return_pct,
                "verified_at": datetime.now(),
                "is_correct": (rec.direction == "up" and return_pct > 0) or
                             (rec.direction == "down" and return_pct < 0)
            })
```

#### 前端：推荐准确率卡片

在 Dashboard 增加"推荐准确率"卡片：
```
📊 推荐验证
最近30天：12条推荐
准确率：67%（8/12）
平均收益：+3.2%
最佳推荐：中证白酒 +8.5%
最差推荐：恒生科技 -5.2%
```

### 3.2 定时验证任务

在 `scheduled_jobs.py` 中增加每日验证任务：
```python
# 每天下午3:30（收盘后）验证推荐
schedule.every().day.at("15:30").do(verify_recommendations)
```

---

## 四、P2：数据展示 → 真正的分析

### 4.1 恐贪指数 + 持仓建议

**当前状态**：只显示 score=49, label="中性"
**目标**：结合持仓给出具体建议

**增强逻辑**：
```
恐贪指数 = 20（极度恐惧）
→ 历史上买入胜率85%
→ 你的持仓中XX基金估值百分位15%，建议加仓
→ 你的现金比例30%，可以分批买入
→ 建议行动：定投加仓，每次2000元
```

**实现**：在 `health_score.py` 的 fear-greed 端点返回结果中增加 `portfolio_advice` 字段。

### 4.2 FED模型 + 配置建议

**当前状态**：只显示 FED 值和股债推荐
**目标**：结合用户持仓给出调仓建议

**增强逻辑**：
```
FED值 = 2.1%（股票更有吸引力）
→ 建议股债配比：70%股票 + 30%债券
→ 你的当前配比：55%股票 + 45%债券
→ 建议：增加15%股票仓位（约6.6万元）
→ 具体建议：加仓XX基金（估值百分位低）
```

### 4.3 股债配比 + 调仓建议

**当前状态**：只显示当前配比
**目标**：结合风险偏好给出调仓建议

**增强逻辑**：
```
当前股债比：55:45
你的风险偏好：稳健型
建议股债比：50:50
偏差：股票超配5%
建议：减仓XX股票基金5000元，转入XX债券基金
```

---

## 五、P3：清理死功能

### 5.1 情景推演（What-If）

**方案A**（推荐）：在持仓管理的AI模式列表中增加入口按钮
- 在 `aiModes` 数组中增加 `{ key: 'what-if', icon: '🎯', label: '情景推演' }`
- 前端实现 what-if 模式面板（类似 deepdive）
- 用户输入假设条件（如"如果大盘跌20%"），LLM 分析影响

**方案B**（简单）：删除后端 what_if.py，清理死代码

### 5.2 对比分析

**增强方案**：在对比结果中增加 AI 差异分析
- 两次全景诊断的差异点提取
- LLM 分析"为什么分数变了"
- 给出"需要关注的变化"提示

---

## 六、实现顺序

| 阶段 | 任务 | 预计工作量 |
|------|------|-----------|
| P0-1 | 统一行动提取器 + ActionCard 组件 | 2h |
| P0-2 | 健康分改进建议 | 1h |
| P0-3 | 费率分析替换建议 | 1h |
| P0-4 | 相关性分析配置建议 | 1h |
| P1-1 | 推荐价格快照 + 自动验证 | 1.5h |
| P1-2 | 推荐准确率卡片 | 0.5h |
| P2-1 | 恐贪指数持仓建议 | 0.5h |
| P2-2 | FED模型配置建议 | 0.5h |
| P2-3 | 股债配比调仓建议 | 0.5h |
| P3-1 | 情景推演入口或清理 | 0.5h |
| P3-2 | 对比分析增强 | 0.5h |
| **总计** | | **~10h** |

---

## 七、验收标准

### P0 验收
- [ ] 热点分析结果末尾显示行动卡片（加入关注/创建决策）
- [ ] 健康分低于60的维度显示具体改进建议
- [ ] 费率分析对高费率基金显示替换建议
- [ ] 相关性分析对高相关资产对显示配置建议

### P1 验收
- [ ] 推荐创建时自动记录基准价格
- [ ] 每日自动验证7天前的推荐
- [ ] Dashboard 显示推荐准确率卡片

### P2 验收
- [ ] 恐贪指数页面显示持仓相关建议
- [ ] FED模型页面显示配置建议
- [ ] 股债配比页面显示调仓建议

### P3 验收
- [ ] 情景推演有前端入口（或已清理）
- [ ] 对比分析增加 AI 差异分析
