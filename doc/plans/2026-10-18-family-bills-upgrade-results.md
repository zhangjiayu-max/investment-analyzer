# 家庭固定账单与现金流升级结果

日期：2026-10-18

## 使用入口

家庭财务 → 财务总览 → 记录支出 → 周期账单。

- 车贷、房贷可设置每月金额、到期日、起始月份及结束月份。
- 保险等固定项目支持月缴、季缴、年缴；页面展示待支付、当日到期、已过到期日及已支付状态。
- 确认已支付后生成实际支出，重复确认只记一笔；编辑或停用模板保留已支付快照。
- 家庭收支预算设置维护全月总预算，包含固定账单；本月录完后可明确确认，也可撤销确认。

## 修复与联动

- 修复上月计算、跨年月份、同额固定项目漏复制与往返复制重复问题。周期支付记录不进入手动固定项复制。
- 零散记账不再覆盖全月预算。未记全时取全月预算与「实际 + 待支付 + 非缴款月预留」中的较大值。
- 月度收入、预计支出、应急桶缺口统一限制可投资结余；零结余、负结余和资料缺失不再回退到正的全局投资配置。
- 总览、资金桶、全账户诊断共享现金流；切回缓存页面会刷新，旧请求不能覆盖新数据。诊断刷新失败时隐藏旧投资行动。
- 支付与支出在同一写事务生成；现金流通过同一读快照汇总，防止并发确认造成漏算。
- 负债本金与保障资料缺失时显示未知；月供不推断贷款余额，保费不推断保额。

## 验证

- 后端相关回归：110 项通过（14 条现有依赖弃用提示）。
- 前端支出、周期账单、现金流和 API 路径回归：49 项通过。
- Vite 生产构建通过，产物位于 `/tmp/investment-household-frontend-build`；保留现有图表包体积提示。
- `git diff --check HEAD` 通过。
- 浏览器检查了本地总览、支出抽屉及窄屏账单表单；未写入虚构的用户账单。
- 旧库迁移、模板支付完整流程、幂等支付、失败回滚、历史保留及并发读取均用临时数据库验证。

验证命令：

```sh
python3 -m pytest backend/tests/test_family_cashflow.py backend/tests/test_db_expense.py backend/tests/test_recurring_expenses.py backend/tests/test_audit_contracts.py tests/test_goal_buckets.py backend/tests/test_smart_add_p0_governance.py -q
cd frontend
npm test -- src/__tests__/expenseManager.test.js src/__tests__/recurringExpenses.test.js src/__tests__/familyCashflow.test.js src/__tests__/api-urls.test.js
npm run build -- --outDir /tmp/investment-household-frontend-build
```

## 当前边界

账单提醒在页面内展示；“确认已支付”仅记账，不发起扣款或转账，也不改写投资账户现金流水。贷款摊还及保单保障评估仍需独立资料支持。

本次修改已在本地源码及开发页面验证，生产静态目录未发布更新。保留工作区内其他任务修改，不提交混合变更。
