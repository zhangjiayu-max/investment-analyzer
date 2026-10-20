# 持仓稳健决策三批升级实施计划

**目标：** 修正专项审查中的账务、补仓和风险缺陷，接通统一的投前约束与投后风险展示，建立公平的策略比较和可审计复盘，验证后提交并推送远程分支。

**架构：** 复用 SQLite 交易账本及统一持仓快照；计算与读取不触发真实成交。共享决策服务负责数据质量、可执行现金、目标用途、集中度、压力损失和建议冲突的最终约束。页面只展示同一快照的事实及最终获准金额，风险生命周期独立于通知已读。

**技术栈：** Python / FastAPI / sqlite3 / Vue 3 / pytest / Vitest。

**执行方式：** 主代理协调集成，按文件边界委派账务、补仓、预警专项。所有测试使用临时数据库或 mock 数据源，禁用外部 TCP；不迁移/重算生产账目、不调用真实交易及收费模型。使用当前 checkout 的新功能分支，无需新建 worktree。用户已授权连续完成三批并推送，无需批间再次确认。

## 第一批：事实和控制纠错

### 1. 交易与现金账务

涉及 `backend/db/portfolio.py`、`backend/models/portfolio.py`、持仓路由及 `PortfolioManagement.vue`。

- [x] 先建立临时库回归：内部现金买入扣款，外部入金显式记录，卖出按账户形成应收并到账，重复确认不重复记账。
- [x] 资金来源、现金流水、应收/冻结、确认/撤销采用原子变动；兼容历史未指定资金来源的交易，明确其口径，禁止静默重复扣历史现金。
- [x] 编辑份额/成本生成可审计调整，后续交易回放不覆盖对账值。
- [x] 净值获取和交易确认分离；缺价保留未知；网络刷新提交时重新读取份额，拒绝旧价覆盖。
- [x] 统一家庭基金暴露与收益命名，修复导入和待确认失败反馈。

核心断言：
```python
assert after['total_assets'] == before['total_assets'] - fees
assert after['cash_balance'] == before['cash_balance'] - purchase_amount
assert after['valuation_complete'] is False  # 缺净值
```

### 2. 补仓信号、硬上限、配置和统计

涉及 `smart_add_planner.py`、`smart_add_metrics.py`、`position_sizing.py`、补仓路由和 `SmartAddPlan.vue`。

- [x] 先复现下一档提前触发、过期数据、上限被替换、余量非单调、配置丢字段、网格定位和成熟样本数错误。
- [x] 分开条件计划与可执行金额；每个引擎共享冷却、执行档位、数据质量门槛及最终交易规则。
- [x] 最终允许增量取所有硬限制较小值；0余量不得回退宽松限制；预算后重新取整和校验起购额。
- [x] 配置结构化验证及无损往返；无可靠锚点/执行状态的网格降级为观察计划。
- [x] 每期限成熟样本独立返回；样本不足、估值指标不一致时不得放大金额。
- [x] 修正模拟器入口、多个金额展示、未知或过期数据和退出条件的可见性。

核心断言：
```python
assert untriggered_plan['executable_amount'] == 0
assert holding_value + approved_amount <= total_assets * hard_limit / 100
assert restored_tiers == original_tiers
assert stats['sample_count_12m'] == actual_mature_samples
```

### 3. 预警检测与活动风险生命周期

涉及 `alert_scanner.py`、细粒度扫描、`AlertCenter.vue`、独立 `db/alert_lifecycle.py` 及集成适配。

- [x] 先建立真实字段、嵌套扫描协议、同时多风险、danger排序、估值过期和风险已读后恶化的回归。
- [x] 各风险独立计算；净值历史正确解包；扫描统一新增/更新/失败/跳过/覆盖返回。
- [x] 移除硬编码100%胜率与危险下跌直接高信心补仓；缺数据不生成可执行买入。
- [x] 风险首次/最后发现、持续/恢复/复发与已读分开；活动风险按严重性优先且不被清理。
- [x] 巡检、读取和刷新失败明确呈现，不产生“未发现风险”的伪成功。

核心断言：
```python
assert {'concentration_high', 'loss_warning', 'daily_drop'} <= detected_types
assert active_alert['is_read'] == 0  # 已读风险明显恶化后重新提醒
assert resolved_alert['risk_status'] == 'resolved'
```

### 4. 高级风险数值基础

涉及 `portfolio_intelligence.py`、`portfolio_risk.py`、`stress_test.py` 和组合风险展示。

- [x] 历史list协议、共同日期对齐、缺失覆盖、百分比单位和最大回撤恢复期回归先失败。
- [x] 修复历史取数；未知风险不显示0；标明当前配置历史模拟与实际收益差异。
- [x] 同指数聚合风险、共同交易日相关性、统一债券子类压力映射和未知分类假设。

验证命令：`python3 -m pytest -q backend/tests/test_portfolio_risk_governance.py backend/tests/test_portfolio_intelligence.py`；前端执行相应组件/格式化 Vitest。

## 第二批：统一稳健决策

### 5. 统一快照、政策与投前检查

新增聚焦的 `backend/services/portfolio/decision_guardrails.py`，复用 `get_portfolio_snapshot`、家庭现金流与资金桶。

- [x] 测试同快照家庭聚合、数据缺失、当前到账资金/未来流入、备用金/必要支出、单基金/权益敞口、投后压力损失。
- [x] 输出版本化事实、覆盖率、可用/保护/应收/冻结现金、约束来源、允许/等待/暂停和具体原因。
- [x] 为不补、小额和原计划生成同口径前后比较；未来结余只进入计划预算，不能作为当日执行额度。
- [x] 持仓、补仓、风险提醒和候选转化使用同一服务；硬限制失败或冲突时不产生可执行加仓候选。
- [x] 各入口读取共享快照一次，保留as_of与政策版本，不以默认分数掩盖未知。

核心断言：
```python
assert approved_amount <= cash_available_now
assert future_income_only['executable_amount'] == 0
assert stale_data['decision_status'] == 'wait_for_data'
assert policy_breach['decision_status'] == 'blocked'
```

### 6. 三页风险与行动展示

- [x] 新增共用风险概览组件：家庭暴露、已知/未知、可用资金、假设损失、资金用途、活动风险。
- [x] 补仓主行动仅一个最终金额，展示不补/少补/计划方案、投后集中度/现金/压力损失、失效和复查条件。
- [x] 风险卡保留全部原因、数据日期和解除条件，区分采纳、成交和恢复；重要变动使用ConfirmDialog。
- [x] 请求异常保留可诊断状态；纯查看不创建交易或反复落档。

验证命令：针对统一服务、三个API契约和共享UI的 pytest/Vitest；随后跑三模块关联回归。

## 第三批：公平比较、收益与复盘

### 7. 等资金情景比较和收益口径

- [x] 相同初始财富与外部现金流下，少补/不补可胜出；上涨/下跌/横盘/预算不足和费用场景先写失败断言。
- [x] 模拟计入剩余现金、已投入、总财富、损失、追加资金峰值及回撤；情景模拟明确假设，不用“最优”绝对措辞。
- [x] 分开持仓浮盈亏、已实现、分红、费用与累计投资收益；资金加权收益对不足信息明确不可算。
- [x] 用已确认成交计算真实结果，与建议跟踪/当前配置历史模拟分开。

核心断言：
```python
assert all(s['initial_wealth'] == shared_wealth for s in scenarios)
assert s['final_wealth'] == s['cash_remaining'] + s['final_value']
assert no_add['final_wealth'] > aggressive_add['final_wealth']  # 持续下跌样例
```

### 8. 有界、自融资的建议验证和可审计复盘

- [x] 显式保存建议快照、价格可知日期、策略/政策版本和有效期；GET预览不自动写成交/候选。
- [x] 影子组合按共同现金、自融资、不重复执行、费用与可成交时点逐步验证；缺价保持待验证，不取未来或当前价代替。
- [x] 展示分期限成熟样本、未恢复/持续亏损、最大水下时间；弱证据不作为仓位放大依据。
- [x] 建立实际执行关联、风险拦截结果和固定期限复查，评价总财富/最大损失/现金缺口，不以通知数及补仓次数作为成功指标。

## 集成、复核和远程交付

- [x] 三批分别运行目标回归，检查新失败与既有基线差异；共用服务与三页相互依赖，最终以一个完整集成提交交付。
- [x] 独立代码审查资金幂等、缺数据fail-closed、投后硬限制、风险生命周期、模拟公平性；修正重要问题后复核。
- [x] 前端完整Vitest、构建到临时目录；后端关联回归与接口契约检查；AST解析及`git diff --check`。
- [x] 更新专项结果文档，明确已实现、验证范围和外部行情/真实交易验证边界。
- 交付步骤：提交本任务文件，推送`origin codex/portfolio-risk-governance`，读取远程SHA确认一致；执行结果见交付回复。

实际执行如发现现有数据协议不同，优先按真实调用方修订集成细节并补回归；不缩减已授权的三批目标，不自动批量改写历史账目。

## 实施边界

- 没有可核验的历史档位/锚点执行状态时，金字塔和网格仅生成观察计划；不自动猜测或初始化历史执行次数。
- 家庭风险政策首次需要用户显式确认，默认值不授权新增投资。
- 压力损失按固定资产类别冲击估算，债券信用/久期、个股交叉持仓、汇率及赎回限制未穿透时明确列出未评估。
- 建议跟踪使用固定期初虚拟现金；净值日期不同不能合成为同日财富。固定期限真实成交复查遇其他交易/分红则转待对账，实际现金缺口证据不足时返回未知。
- 三批相互依赖的代码最终作为一个集成提交验证与推送，避免远程分支中间版本缺少统一服务。
