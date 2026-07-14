# 组合层面智能设计稿（第三阶段）

> **阶段目标**：打通现有组合分析"零件"的割裂状态，把相关性矩阵回流到健康分/调仓/大师矩阵，新增组合层面风险度量和7维聚合，让系统从"单基金分析"升级为"组合智能"。

## 1. 设计背景

### 现状
- 单基金分析完整：7维体检 + 大师矩阵
- 组合层面已有"零件"但割裂：
  - 相关性矩阵（correlation.py）已算出 Effective N 和高相关对，但未回流到健康分/调仓
  - 组合优化器（portfolio_optimizer.py）不参考单基金体检结论
  - 大师矩阵只有单基金版，无组合视角
  - 组合风险度量（波动率/VaR/最大回撤）缺失
  - 7维体检无组合聚合版

### 第三阶段目标（聚焦3个高价值补齐）
1. **组合风险度量引擎**：组合波动率、VaR、CVaR、组合历史最大回撤
2. **7维体检组合聚合版**：把单基金7维按持仓权重聚合为组合7维
3. **大师矩阵组合版**：6位大师基于组合数据做组合视角决策
4. **相关性回流**：Effective N 接入组合健康分

## 2. 模块设计

### 2.1 组合风险度量引擎（services/portfolio_intelligence.py）

```python
def calculate_portfolio_risk_metrics(user_id: str) -> dict:
    """组合风险度量。

    Returns:
    - portfolio_volatility: 组合年化波动率（基于净值协方差矩阵）
    - var_95: 95%置信度VaR（1日）
    - cvar_95: 95%置信度CVaR（条件期望损失）
    - max_drawdown: 组合历史最大回撤
    - max_drawdown_duration: 最大回撤恢复期（天）
    - sharpe_ratio: 组合夏普比率（无风险利率2%）
    - sortino_ratio: 组合Sortino比率
    - effective_n: 有效分散数（来自相关性矩阵）
    - avg_correlation: 平均相关系数
    - risk_contributions: 各基金的风险贡献占比
    """
```

**算法**：
1. 获取持仓所有基金的净值序列（复用 fund_data_service）
2. 日期对齐，计算日收益率
3. 按持仓权重构建组合日收益率序列
4. 组合年化波动率 = std(组合日收益率) × sqrt(252)
5. VaR_95 = percentile(组合日收益率, 5) × 组合总市值
6. CVaR_95 = mean(组合日收益率[组合日收益率 <= VaR_95阈值]) × 组合总市值
7. 组合净值曲线 = cumprod(1 + 组合日收益率)，计算最大回撤
8. 夏普比率 = (年化收益 - 2%) / 年化波动率
9. 风险贡献 = weight_i × (Σ_j weight_j × Cov_ij) / 组合方差

### 2.2 7维体检组合聚合（services/portfolio_intelligence.py）

```python
def calculate_portfolio_health_report(user_id: str) -> dict:
    """组合7维体检报告（按持仓权重聚合）。

    Returns:
    - portfolio_total_score: 组合总分
    - portfolio_rating: 组合评级
    - portfolio_report: 7维聚合分数
    - portfolio_decision: 组合决策矩阵
    - holding_reports: 各基金7维明细
    - risk_metrics: 组合风险度量
    """
```

**算法**：
1. 获取持仓列表（fund_code + weight）
2. 对每只基金调用 `calculate_fund_health_report`
3. 按持仓权重加权聚合7维分数
4. 组合总分 = Σ(基金总分 × weight)
5. 组合决策 = 基于组合7维和风险度量重新判定

### 2.3 大师矩阵组合版（services/master_perspectives.py 扩展）

```python
def build_portfolio_master_matrix(portfolio_report: dict, risk_metrics: dict) -> dict:
    """6位大师基于组合数据做组合视角决策。"""
```

**每位大师的组合视角**：
- **巴菲特**：组合中多少比例的基金"有护城河"？护城河覆盖率
- **林奇**：组合成长性加权，PEG分布
- **博格**：组合整体费率水平 + 指数化程度
- **马克斯**：组合当前周期位置 + 整体情绪
- **达利欧**：组合分散度（Effective N）+ 风险平价偏离度
- **段永平**：组合中"好生意+好公司"的比例

### 2.4 相关性回流健康分

修改 `health_score.py` 的 `calc_diversification_score`：
- 引入 Effective N 作为评分因子（替代部分"持仓数量"权重）
- 引入平均相关系数（高相关扣分）
- 引入高相关对数量（风险集中扣分）

## 3. API设计

### 新增API
```
GET /api/analysis/portfolio-intelligence/risk-metrics
    → 组合风险度量（波动率/VaR/CVaR/最大回撤/夏普/Sortino）

GET /api/analysis/portfolio-intelligence/health-report
    → 组合7维体检报告（聚合版）

GET /api/analysis/portfolio-intelligence/master-matrix
    → 大师矩阵组合版
```

### 路由文件
`backend/routers/analysis/portfolio_intelligence.py`

## 4. 前端UI方案

### 组合智能面板（Dashboard.vue 或新建 PortfolioIntelligence.vue）
- **组合风险卡片**：波动率/VaR/最大回撤/夏普比率
- **组合7维雷达图**：7维聚合分数雷达图
- **大师矩阵组合版**：6位大师对组合的整体建议
- **风险贡献分布**：各基金风险贡献饼图

## 5. 开发任务分解

### Task 1: services/portfolio_intelligence.py — 组合风险度量引擎
- calculate_portfolio_risk_metrics()
- 组合波动率/VaR/CVaR/最大回撤/夏普/Sortino/风险贡献

### Task 2: services/portfolio_intelligence.py — 组合7维聚合
- calculate_portfolio_health_report()
- 按持仓权重聚合7维 + 组合决策矩阵

### Task 3: master_perspectives.py 扩展 — 大师矩阵组合版
- build_portfolio_master_matrix()
- 6位大师组合视角评分

### Task 4: 路由 + 前端
- routers/analysis/portfolio_intelligence.py
- 前端组合智能面板

### Task 5: 单元测试 + 验证 + 提交

## 6. 数据降级策略

- 基金净值不足（<30天）：该基金不参与组合风险计算，风险度量降级
- 单基金7维计算失败：该基金用默认50分，不阻塞聚合
- 相关性矩阵不可用：Effective N 用持仓数量近似
- 持仓为空：返回空报告 + 提示

## 7. 硬约束遵循

- ✅ 不新增LLM调用（纯量化计算）
- ✅ API响应遵循{code, message, data}标准
- ✅ 前端API调用使用相对路径
- ✅ 复用现有 fund_data_service / correlation / fund_analysis 能力
- ✅ 所有改动完成后自动推送远程git仓库
