# 家庭固定账单与现金流联动实施计划

**目标**：先修复月份复制、同额固定项漏复制和部分记账高估结余，再支持周期账单、支付确认、月度预留及三页联动。

**范围**：沿用现有 Vue 页面和 SQLite 数据层；账单模板与实际支出分开。车贷支持结束月份，保险支持月/季/年缴。提醒在页面中展示到期、逾期状态，不新增外部通知或自动扣款。贷款余额及保单保额没有录入数据时标为未知，不虚构为零或已保障。

## 数据合同

- 模板：`id/name/category/amount/frequency/start_month/end_month/due_day/active/note`；frequency 为 `monthly/quarterly/yearly`；起始月份锚定季度及年度缴费。
- 账单按模板和月份投影，列表提供 `template_id/month/name/category/amount/due_date/status/expense_id`，status 为 pending/paid；确认支付在事务中幂等生成一条固定实际支出。
- API：`GET/POST /api/expense/templates`，`PUT/DELETE /api/expense/templates/{id}`，`GET /api/expense/bills?month=YYYY-MM`，`POST /api/expense/templates/{id}/pay` body `{month}`。删除为停用，保留已付历史。
- 周期汇总：`pending_total/paid_total/monthly_provision/future_provision/debt_payment/overdue_count/upcoming_count`。`monthly_provision` 展示周期项目按月折算，`future_provision` 仅包含本月不缴款的周期项目，避免实际支付与预留重复扣算。
- 统一现金流保留既有字段并增加 `recorded_expense/pending_bills/future_provision/budget_expense/expense_complete/investable_surplus/emergency_target/emergency_gap/emergency_months/bucket_reserve`。
- 有未结账月份时，预计月支出为 `max(画像月预算, 已支付实际 + 待付账单 + 非缴款月周期预留)`；用户明确月度记录完成后采用实际加待付及预留。不再用不完整实际记录下调画像预算。
- 可投资月结余在预计结余上扣除应急桶缺口（上限为正结余），三页显示同一结果；既有现金存量不重复扣减已支付支出。

## 步骤与验收

- [x] 先补回归测试并观察失败：上海时区月份/跨年、同额保单复制及幂等、部分录入预算不下降、负结余不能回退全局正额度。
- [x] 修复旧支出链路，增加月份结账标记，保留画像预算；明确未知值及收入来源。
- [x] 新增周期模板及支付事务测试：月底日期截断、跨年季度、结束/停用、年缴预留、同额不同模板、重复支付及历史保留、无效金额/日期。
- [x] 完成周期数据层与路由；通过现有 init_db 初始化入口建表（仓库实际入口为 backend/db/__init__.py）。
- [x] 在原支出抽屉增加账单模板、当月待付及确认支付，编辑和重要操作使用 ConfirmDialog，保留实际支出录入。
- [x] 总览显示实际/待付/预留/预计结余；资金桶显示必要预留与可投资上限；诊断增加现金流、还贷和应急金事实并限制现金增配建议。
- [ ] 执行后端相关回归、前端交互回归与构建；使用临时数据库验证旧库升级及完整支付流程；独立审查需求和代码质量。

## 验证命令

```sh
python3 -m pytest backend/tests/test_db_expense.py backend/tests/test_recurring_expenses.py backend/tests/test_family_cashflow.py -q
cd frontend
npm test -- src/__tests__/expenseManager.test.js src/__tests__/recurringExpenses.test.js src/__tests__/familyCashflow.test.js
npm run build
```

当前工作区有其他任务的未提交修改，保留这些修改，在现有 checkout 上仅更新本任务相关文件，不自动提交混合变更。
