# 现金流闭环与导航收束 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 将月度实际支出接入家庭财务、投资额度和 AI 建议，并收束日常导航。

**Architecture:** `db.expense` 是现金流事实源；家庭总览、仓位计算和对话上下文通过 `get_cashflow()` 获取当月值。支出管理嵌入家庭财务页，导航只改变可发现性。

**Tech Stack:** Python 3.11, FastAPI, SQLite, Vue 3, Axios, pytest, Vitest.

---

## File Structure

- `backend/tests/test_db_expense.py`: 支出事实与回退逻辑。
- `backend/routers/dashboard/finance_dashboard.py`: 财务看板现金流来源。
- `backend/services/advisor/position_sizing.py`: 实际结余限制月度流入。
- `backend/services/conversation/conversation_context.py`: AI 数据来源提示。
- `frontend/src/api/index.js`: 支出 API 封装。
- `frontend/src/components/family/ExpenseManager.vue`: 支出录入和月度分析。
- `frontend/src/components/family/FamilyFinanceDashboard.vue`: 支出入口和来源提示。
- `frontend/src/navigation.js`: 高低频入口分层。

### Task 1: Stabilize Cashflow Facts

**Files:** Create `backend/tests/test_db_expense.py`; modify `backend/db/expense.py` and `backend/routers/dashboard/finance_dashboard.py`.

- [ ] Write the failing current-month precedence test.

```python
def test_cashflow_prefers_current_month_records(test_user_id):
    update_user_profile(test_user_id, monthly_income=10000, monthly_expense=2500)
    create_expense(current_month(), "餐饮", 1800, user_id=test_user_id)
    cashflow = get_cashflow(user_id=test_user_id)
    assert cashflow["monthly_expense"] == 1800
    assert cashflow["expense_source"] == "records"
    assert cashflow["monthly_surplus"] == 8200
```

- [ ] Run `cd backend && pytest tests/test_db_expense.py::test_cashflow_prefers_current_month_records -v`; it must fail until data month and application month are aligned.
- [ ] Implement deterministic month injection: `def get_cashflow(user_id="default", month: str | None = None) -> dict`, choosing `_validate_month(month)` when supplied and `_current_month()` otherwise.
- [ ] In the finance dashboard import `get_cashflow`; return its `expense_source`, `fixed_total`, and `debt_payment`; use its values instead of reading profile income/expense directly.
- [ ] Add fallback/validation coverage.

```python
def test_cashflow_falls_back_to_profile_without_records(test_user_id):
    update_user_profile(test_user_id, monthly_income=10000, monthly_expense=2500)
    assert get_cashflow(user_id=test_user_id)["expense_source"] == "profile_static"

def test_expense_rejects_zero_amount(test_user_id):
    with pytest.raises(ValueError, match="金额必须为正数"):
        create_expense(current_month(), "餐饮", 0, user_id=test_user_id)
```

- [ ] Run `cd backend && pytest tests/test_db_expense.py -v`; expect PASS.
- [ ] Commit: `git add backend/db/expense.py backend/routers/dashboard/finance_dashboard.py backend/tests/test_db_expense.py && git commit -m "feat: use recorded expenses in finance dashboard"`.

### Task 2: Connect Investment and Conversation Consumers

**Files:** Modify `backend/services/advisor/position_sizing.py` and `backend/services/conversation/conversation_context.py`; extend `backend/tests/test_dca_suggestion.py` and `backend/tests/test_conversation_context.py`.

- [ ] Write and run a failing goal-aware cash constraint test.

```python
def test_goal_aware_cash_constraint_uses_live_cashflow(monkeypatch):
    monkeypatch.setattr("db.expense.get_cashflow", lambda user_id: {
        "monthly_surplus": 1200, "expense_source": "records"
    })
    result = calc_cash_constraint("default", {}, [], 100000)
    assert result["monthly_inflow"] == 1200
    assert result["monthly_inflow_source"] == "expense_records"
```

- [ ] Run `cd backend && pytest tests/test_dca_suggestion.py -k live_cashflow -v`; expect FAIL.
- [ ] In the existing `goal_aware` branch, call `get_cashflow(user_id)`. Use positive `monthly_surplus`; label its source `expense_records` or `profile_static`; preserve global configuration fallback for zero or negative values.
- [ ] Add `test_goal_aware_cash_constraint_falls_back_for_negative_surplus`, asserting global configured inflow remains the source.
- [ ] In conversation context append `当月支出来源` and `当月可结余` from `get_cashflow()`; retain current profile fields for compatibility.
- [ ] Run `cd backend && pytest tests/test_dca_suggestion.py tests/test_conversation_context.py -v`; expect PASS.
- [ ] Commit: `git add backend/services/advisor/position_sizing.py backend/services/conversation/conversation_context.py backend/tests/test_dca_suggestion.py backend/tests/test_conversation_context.py && git commit -m "feat: use live cashflow in investment advice"`.

### Task 3: Add the Embedded Expense Manager

**Files:** Create `frontend/src/components/family/ExpenseManager.vue` and `frontend/src/__tests__/expenseManager.test.js`; modify `frontend/src/api/index.js` and `frontend/src/components/family/FamilyFinanceDashboard.vue`.

- [ ] Write a failing API contract test.

```javascript
it('uses the expense API contract', async () => {
  await getExpenses('2026-10')
  expect(mockedGet).toHaveBeenCalledWith('/expense', { params: { month: '2026-10' } })
  await createExpense({ month: '2026-10', category: '餐饮', amount: 50 })
  expect(mockedPost).toHaveBeenCalledWith('/expense', expect.any(Object))
})
```

- [ ] Add these API functions.

```javascript
export const getExpenses = month => api.get('/expense', { params: { month } })
export const createExpense = payload => api.post('/expense', payload)
export const updateExpense = (id, payload) => api.put(`/expense/${id}`, payload)
export const deleteExpense = id => api.delete(`/expense/${id}`)
export const getExpenseSummary = month => api.get('/expense/summary', { params: { month } })
export const getExpenseTrend = (months = 6) => api.get('/expense/trend', { params: { months } })
export const getExpenseCategories = () => api.get('/expense/categories')
export const copyFixedExpenses = (from_month, to_month) => api.post('/expense/copy-fixed', { from_month, to_month })
```

- [ ] Implement `ExpenseManager` with `v-model:open`, `updated` after every successful write, month selector, fixed/variable selector, category, amount, note, summary, records, fixed-item copy, and six-month `LineChart`. Use `ConfirmDialog` before deletion and `useToast` for errors.
- [ ] Add to the finance page: `<ExpenseManager v-model:open="expenseOpen" @updated="load" />`; add a `记录支出` button. Show `本月已记录` when `cf.expense_source === 'records'`, otherwise `画像估算`.
- [ ] Run `cd frontend && npm test -- expenseManager.test.js && npm run build`; expect PASS.
- [ ] Commit: `git add frontend/src/api/index.js frontend/src/components/family/ExpenseManager.vue frontend/src/components/family/FamilyFinanceDashboard.vue frontend/src/__tests__/expenseManager.test.js && git commit -m "feat: add household expense manager"`.

### Task 4: Consolidate Navigation Without Deletion

**Files:** Modify `frontend/src/navigation.js`; extend `frontend/src/__tests__/navigation.test.js`.

- [ ] Write a failing navigation assertion.

```javascript
it('keeps RAG debug pages out of sidebar navigation', () => {
  expect(flatNavItems.map(item => item.key)).not.toContain('rag')
  expect(flatNavItems.map(item => item.key)).not.toContain('rag-test')
})
```

- [ ] Keep primary groups for 今日行动, 资产与持仓, 家庭现金流, 市场与机会, and 知识与研究. Rename `group-system` to 系统治理 and make it collapsed. Remove `rag`, `rag-test`, and old `health` from navigation only; retain their registry keys.
- [ ] Run `cd frontend && npm test -- navigation.test.js && npm run build`; expect PASS.
- [ ] Commit: `git add frontend/src/navigation.js frontend/src/__tests__/navigation.test.js && git commit -m "refactor: focus navigation on financial decisions"`.

### Task 5: Verify and Set the Deletion Gate

**Files:** Modify `doc/plans/2026-10-15-financial-system-streamlining-design.md`.

- [ ] Run `cd backend && pytest tests/test_db_expense.py tests/test_dca_suggestion.py tests/test_conversation_context.py -v && cd ../frontend && npm test -- navigation.test.js expenseManager.test.js && npm run build`; expect all PASS.
- [ ] Smoke test: set profile income, record fixed and variable expenses, verify `本月已记录`, confirm goal-aware smart add shows `expense_records`, inspect AI context, and open a hidden page by direct key.
- [ ] Record deployment date and 30-day review: deletion requires zero use events, zero frontend references, zero backend consumers, and a passing smoke test.
- [ ] Commit: `git add doc/plans/2026-10-15-financial-system-streamlining-design.md && git commit -m "docs: add feature cleanup observation gate"`.
