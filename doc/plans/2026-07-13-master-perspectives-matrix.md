# 大师理念矩阵设计稿（第二阶段）

> **阶段目标**：从单一"段永平视角"升级为6位投资大师的多视角决策矩阵，让用户看到不同理念下对同一基金的差异化判断，避免单一视角的盲区。

## 1. 设计背景

### 现状（第一阶段已完成）
- 7维体检报告（质量/回撤/趋势/资金/情绪/估值/基本面）
- 段永平视角（4维字符串拼接，纯规则）
- 决策矩阵（8因子 → 5个action，纯规则）

### 痛点
- 段永平视角过于浅层（仅字符串拼接，无深度推理）
- 决策矩阵单一视角，无法表达不同投资理念的差异化判断
- 用户看不到"巴菲特会怎么看这只基金" "林奇会怎么操作"

### 第二阶段目标
- 实现6位大师视角评分（巴菲特/林奇/博格/马克斯/达利欧/段永平）
- 每位大师基于7维数据的不同子集做规则映射
- 大师间横向对比，识别共识与冲突
- LLM深度分析（可选，受开关控制，默认关闭）

## 2. 6位大师核心理念映射

### 2.1 巴菲特（Warren Buffett）
**核心理念**：护城河 + ROE持续性 + 安全边际 + 长期持有

| 理念要素 | 数据来源 | 评分逻辑 |
|---------|---------|---------|
| 护城河 | 基本面.盈利能力（ROE+毛利率） | profitability≥80 → 有护城河 |
| ROE持续性 | 基本面.稳定性（4季度标准差） | stability≥70 → ROE稳定 |
| 安全边际 | 估值水位 | valuation=low → 有安全边际 |
| 长期持有 | 质量.经理稳定性 | quality≥70 → 适合长期持有 |

**决策倾向**：
- 有护城河+ROE稳定+有安全边际 → strong_buy
- 有护城河+ROE稳定+估值中位 → hold
- 无护城河 → wait（巴菲特不买无护城河的公司）
- 估值高 → reduce（即使好公司也不追高）

### 2.2 林奇（Peter Lynch）
**核心理念**：PEG + 六类公司分类 + 身边事物 + 快速增长

| 理念要素 | 数据来源 | 评分逻辑 |
|---------|---------|---------|
| PEG | PE / 净利润增速 | PEG<1 → 低估 |
| 快速增长 | 基本面.成长性 | growth≥70 → 快速增长类 |
| 资产配置 | 基本面.偿债能力 | solvency≥70 → 资产富裕类 |
| 估值合理性 | 估值水位 | valuation=low/mid → 合理 |

**决策倾向**：
- PEG<1 + 快速增长 → strong_buy
- PEG<1.5 + 成长性好 → dca
- PEG>2 → wait（林奇不买贵的高增长）
- 零增长 + 估值低 → hold（缓慢增长类）

### 2.3 博格（John Bogle）
**核心理念**：成本最小化 + 指数化 + 均值回归 + 长期持有

| 理念要素 | 数据来源 | 评分逻辑 |
|---------|---------|---------|
| 成本优势 | 质量.费率竞争力 | fee_score≥80 → 低成本 |
| 指数化程度 | 质量.跟踪误差 | tracking_error小 → 指数化好 |
| 均值回归 | 趋势.均线排列 + 回撤 | 回撤高位+趋势企稳 → 均值回归机会 |
| 估值合理性 | 估值水位 | valuation=high → 警惕均值回归 |

**决策倾向**：
- 低成本+指数化好+估值低 → strong_buy
- 低成本+估值中位 → dca
- 估值高 → reduce（均值回归风险）
- 高费率 → wait（博格反对高成本）

### 2.4 马克斯（Howard Marks）
**核心理念**：周期位置 + 逆向投资 + 第二层次思维 + 风险控制

| 理念要素 | 数据来源 | 评分逻辑 |
|---------|---------|---------|
| 周期位置 | 回撤.回撤分位 | drawdown_percentile≥0.7 → 周期底部 |
| 逆向信号 | 情绪.恐贪指数 | fear_greed≤30 → 别人恐惧时贪婪 |
| 风险评估 | 回撤.最大回撤 | drawdown_depth大 → 风险已释放 |
| 趋势确认 | 趋势.均线排列 | trend企稳 → 周期反转确认 |

**决策倾向**：
- 周期底部+情绪恐惧+趋势企稳 → strong_buy（逆向加仓）
- 周期底部+情绪恐惧 → dca（分批逆向建仓）
- 周期顶部（回撤低位+估值高+情绪贪婪） → reduce
- 趋势不明 → wait（马克斯强调"不要在趋势不明时行动"）

### 2.5 达利欧（Ray Dalio）
**核心理念**：全天候配置 + 风险平价 + 债务周期 + 分散化

| 理念要素 | 数据来源 | 评分逻辑 |
|---------|---------|---------|
| 分散化程度 | 持仓集中度（Top10覆盖率） | coverage<50 → 分散良好 |
| 风险平价 | 资金流向 + 波动率 | capital稳定 → 风险平衡 |
| 债务周期 | 宏观资金流向 | 资金流入 → 周期上行 |
| 配置建议 | 估值+趋势综合 | 单一资产过度集中 → reduce |

**决策倾向**：
- 分散良好+风险平衡 → hold（全天候配置）
- 单一资产集中度>70% → reduce（达利欧反对过度集中）
- 估值低+分散良好 → dca
- 估值高+集中度高 → reduce

### 2.6 段永平（已实现，扩展深度）
**核心理念**：好生意 + 好公司 + 好价格 + 能力圈

**扩展**：在现有4维字符串基础上，新增结构化输出（score/action/key_metrics）

## 3. 大师矩阵聚合算法

### 3.1 共识检测
```python
def _detect_consensus(master_results: list[dict]) -> dict:
    """检测6位大师的共识与冲突。"""
    actions = [m["action"] for m in master_results]
    
    # 统计action分布
    action_counts = Counter(actions)
    majority_action = action_counts.most_common(1)[0]
    agreement = majority_action[1] / len(actions)
    
    # 识别冲突（如同时出现strong_buy和reduce）
    conflicts = []
    if "strong_buy" in actions and "reduce" in actions:
        conflicts.append("大师意见分歧：部分建议加仓，部分建议减仓")
    if "buy" in actions and "wait" in actions:
        conflicts.append("买卖信号冲突")
    
    return {
        "consensus_action": majority_action[0],
        "agreement": round(agreement, 2),
        "agreement_label": _agreement_label(agreement),
        "conflicts": conflicts,
        "action_distribution": dict(action_counts),
    }
```

### 3.2 共识强度标签
- agreement ≥ 0.83（5/6一致） → "高度共识"
- agreement ≥ 0.67（4/6一致） → "多数共识"
- agreement ≥ 0.50（3/6一致） → "温和共识"
- agreement < 0.50 → "意见分歧"

## 4. API扩展

### 4.1 现有API扩展
`GET /api/analysis/fund-quality/{fund_code}` 返回结构新增 `master_perspectives` 字段：

```json
{
  "data": {
    "fund_code": "161725",
    "total_score": 57.0,
    "report": {...},
    "decision_matrix": {...},
    "duan_yongping_view": "...",
    "master_perspectives": {
      "masters": [
        {
          "master_key": "buffett",
          "master_name": "巴菲特",
          "master_icon": "🏰",
          "core_philosophy": "护城河+ROE持续性+安全边际",
          "score": 75,
          "action": "hold",
          "action_label": "持有",
          "reason": "有护城河+ROE稳定，但估值不低，持有等待安全边际",
          "key_metrics": {
            "has_moat": true,
            "roe_consistent": true,
            "margin_of_safety": false
          }
        },
        {...lynch...},
        {...bogle...},
        {...marks...},
        {...dalio...},
        {...duanyongping...}
      ],
      "consensus": {
        "consensus_action": "hold",
        "agreement": 0.67,
        "agreement_label": "多数共识",
        "conflicts": [],
        "action_distribution": {"hold": 4, "dca": 1, "wait": 1}
      }
    }
  }
}
```

## 5. LLM深度分析（可选，默认关闭）

### 5.1 配置开关
- `fund_analysis.master_perspectives_llm_enabled`（默认false）
- 开启时：用LLM为每位大师生成更深入的理由文案
- 关闭时：仅输出规则映射的评分和action

### 5.2 analysis_agents表种子配置
新增6条大师Agent记录（is_active=1）：
- `master_buffett` — 巴菲特视角分析prompt
- `master_lynch` — 林奇视角分析prompt
- `master_bogle` — 博格视角分析prompt
- `master_marks` — 马克斯视角分析prompt
- `master_dalio` — 达利欧视角分析prompt
- `master_duanyongping` — 段永平视角分析prompt

## 6. 前端UI方案

### 6.1 大师矩阵对比面板
位于体检报告区块下方，调仓动作面板上方：

```
┌─────────────────────────────────────────────────────────┐
│  🎯 大师理念矩阵                                          │
├─────────────────────────────────────────────────────────┤
│  共识强度：多数共识（4/6 建议持有）                        │
│  [hold: 4] [dca: 1] [wait: 1]                           │
├─────────────────────────────────────────────────────────┤
│  🏰 巴菲特    75分  持有   有护城河+ROE稳定，但估值不低   │
│  📈 林奇      68分  定投   PEG<1，快速增长类             │
│  💰 博格      60分  持有   低成本指数化，估值中位         │
│  🔄 马克斯    55分  持有   周期中位，趋势不明             │
│  ⚖️ 达利欧    50分  持有   分散良好，全天候配置           │
│  🎯 段永平    55分  持有   尚可的生意+质地一般+价格合理   │
└─────────────────────────────────────────────────────────┘
```

### 6.2 交互
- 点击大师卡片 → 展开详细分析（key_metrics + 核心理念说明）
- 共识冲突时 → 高亮显示冲突警告

## 7. 开发任务分解

### Task 1: 服务层 — master_perspectives.py（6位大师评分函数）
- 创建 `backend/services/master_perspectives.py`
- 实现6位大师的评分函数（规则映射）
- 每位大师基于7维数据的不同子集

### Task 2: 服务层 — 大师矩阵聚合 + 共识检测
- `build_master_perspectives_matrix(fund_code, report, details)`
- 共识检测算法
- 冲突识别

### Task 3: 集成到 fund_analysis.py
- 修改 `calculate_fund_health_report` 调用大师矩阵
- 返回结构新增 `master_perspectives` 字段

### Task 4: analysis_agents表种子配置
- 新增6条大师Agent记录
- LLM prompt配置（受开关控制）

### Task 5: 单元测试
- 6位大师评分逻辑测试
- 共识检测测试
- 价值陷阱场景测试

### Task 6: 前端 — 大师矩阵对比面板
- EventRadarPage.vue 新增大师矩阵面板
- 大师卡片 + 共识强度 + 冲突警告

### Task 7: 集成验证 + 提交推送
- API验证
- 前端构建
- 后端重启
- 提交推送

## 8. 数据降级策略

- 7维数据缺失时：大师评分默认50分（中分），action=hold
- 债基（无基本面数据）：巴菲特/林奇跳过，博格/马克斯/达利欧/段永平正常评分
- 单位大师评分失败：不影响其他大师
- LLM开关关闭：仅输出规则映射结果

## 9. 硬约束遵循

- ✅ LLM调用有开关控制（`fund_analysis.master_perspectives_llm_enabled`，默认false）
- ✅ 新LLM开关默认false
- ✅ LLM调用包含trace_id
- ✅ prompt通过analysis_agents表配置（不硬编码）
- ✅ API响应遵循{code, message, data}标准
- ✅ 前端API调用使用相对路径
- ✅ 后端绑定0.0.0.0，移除--reload
- ✅ 前端构建输出至backend/static
- ✅ 所有改动完成后自动推送远程git仓库
