# 家庭预算中枢升级实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with verification checkpoints.

**Goal:** 让家庭财务从“投资资产展示”升级为可直接回答本月可花、未来三个月现金安全和可投资结余的预算中枢，并修复月收入入口与全账户诊断的无效感。

**Architecture:** 后端在 `db.expense.get_cashflow()` 统一计算家庭现金流和三个月预测，财务总览与健康诊断只消费这份事实数据；用户画像继续保存收入和预算，周期账单继续保存固定计划。前端将预算编辑提升到财务总览现金流主卡，健康诊断按家庭现金安全、数据缺口、投资风险排序输出行动。

**Tech Stack:** Python 3.11、FastAPI、SQLite、pytest、Vue 3 `<script setup>`、Vitest、Vite。

---

### Task 1: 固化现金流事实字段与三个月预测

**Files:**
- Modify: `backend/db/expense.py:360-440`
- Modify: `backend/services/finance/household_finance.py`
- Test: `backend/tests/test_family_cashflow.py`
- Test: `backend/tests/test_db_expense.py`

- [ ] **Step 1: Write failing tests for unknown income and safe-to-spend fields**

```python
def test_cashflow_unknown_income_exposes_actionable_state(tmp_db):
    update_user_profile(monthly_income=None, monthly_expense=None, monthly_surplus=None)
    cf = get_cashflow(month="2026-10")
    assert cf["income_known"] is False
    assert cf["safe_to_spend"] is None
    assert cf["investable_surplus"] is None
    assert cf["data_gaps"] == ["monthly_income"]

def test_cashflow_builds_three_month_forecast_from_recurring_obligations(tmp_db):
    update_user_profile(monthly_income=10000, monthly_expense=8000)
    create_recurring_expense(name="车贷", category="车贷", amount=3000,
                             frequency="monthly", start_date="2026-10-01")
    forecast = get_cashflow(month="2026-10")["forecast_3m"]
    assert [item["month"] for item in forecast] == ["2026-10", "2026-11", "2026-12"]
    assert forecast[0]["fixed_obligation"] == 3000
    assert forecast[0]["cash_balance"] is not None
```

- [ ] **Step 2: Run the focused tests and confirm they fail for missing fields**

Run: `pytest -q backend/tests/test_family_cashflow.py::test_cashflow_unknown_income_exposes_actionable_state backend/tests/test_family_cashflow.py::test_cashflow_builds_three_month_forecast_from_recurring_obligations`

Expected: FAIL with missing `safe_to_spend`/`forecast_3m` keys.

- [ ] **Step 3: Implement one canonical cashflow calculation**

In `get_cashflow`, preserve existing values but add:

```python
income_known = profile.get("monthly_income") is not None and float(profile.get("monthly_income") or 0) > 0
data_gaps = [] if income_known else ["monthly_income"]
if not (budget > 0 or complete):
    data_gaps.append("monthly_expense_or_month_complete")
safe_to_spend = round(income - fixed_obligation - variable_recorded - provision, 2) if income_known else None
investable = round(max(0.0, safe_to_spend - reserve), 2) if income_known and not data_gaps else None
```

Add a small month iterator that calls the existing recurring summary for the selected month and the next two months, carries forward the account cash balance, and returns `month`, `income`, `fixed_obligation`, `known_expense`, `cash_outflow`, `cash_balance`, and `status`. Do not mutate the database while reading.

- [ ] **Step 4: Make household diagnosis consume canonical fields**

Change `build_household_diagnosis` to emit `actions` with `priority`, `reason`, `evidence`, `cta`, and `navigate_to`. Put income gaps, pending fixed bills, runway gaps, incomplete month, and unknown liabilities before investment actions. Preserve `warnings` for existing callers.

- [ ] **Step 5: Run focused backend tests and refactor only after green**

Run: `pytest -q backend/tests/test_family_cashflow.py backend/tests/test_db_expense.py`

Expected: all focused tests pass, with only existing deprecation warnings permitted.

- [ ] **Step 6: Commit the backend cashflow slice**

```bash
git add backend/db/expense.py backend/services/finance/household_finance.py backend/tests/test_family_cashflow.py backend/tests/test_db_expense.py
git commit -m "feat: add household cash safety forecast"
```

### Task 2: Add a first-class budget editor to the financial overview

**Files:**
- Modify: `frontend/src/components/family/FamilyFinanceDashboard.vue`
- Modify: `frontend/src/components/family/HouseholdCashflowCard.vue`
- Modify: `frontend/src/api/index.js`
- Test: `frontend/src/__tests__/familyCashflow.test.js`

- [ ] **Step 1: Write failing component tests for the income entry state**

```js
it('shows a direct income editor when the monthly income is unknown', async () => {
  api.getFinanceDashboard.mockResolvedValue({ data: { cash_flow: { income_known: false, data_gaps: ['monthly_income'] } } })
  api.getProfile.mockResolvedValue({ data: { monthly_income: null, monthly_expense: 8000 } })
  const wrapper = mount(FamilyFinanceDashboard, { global: { stubs: { Icon: true, TermTooltip: true } } })
  await flushPromises()
  expect(wrapper.get('[aria-label="补充月收入"]').exists()).toBe(true)
})

it('saves income and refreshes the dashboard', async () => {
  api.updateProfile.mockResolvedValue({ data: { ok: true } })
  // mount, open editor, type 20000, click save
  expect(api.updateProfile).toHaveBeenCalledWith({ monthly_income: 20000, monthly_expense: 8000 })
  expect(api.getFinanceDashboard).toHaveBeenCalledTimes(2)
})
```

- [ ] **Step 2: Run the tests and verify the direct editor is missing**

Run: `cd frontend && npm test -- --run src/__tests__/familyCashflow.test.js`

Expected: FAIL because the dashboard has no direct income editor or save handler.

- [ ] **Step 3: Add profile API usage and editor state**

Import `getProfile` and `updateProfile` into the dashboard. Add `budgetOpen`, `budget`, `budgetReady`, `budgetSaving`, and `loadBudget`/`saveBudget` functions using the existing `/profile` API. Treat `null` income as unknown; never coerce it to a positive historical default.

- [ ] **Step 4: Make the cashflow card show decision metrics first**

Replace the dense first grid with five primary values in this order: 月收入, 固定必缴, 已发生支出, 剩余可花, 可投资结余. Keep the detailed breakdown below. For null values render “待补充” and a compact action button with `aria-label="补充月收入"` or `aria-label="补充月支出预算"`.

- [ ] **Step 5: Add the three-month forecast block and responsive styles**

Render `cashflow.forecast_3m` as a compact table/list with month, fixed obligation, cash balance, and status. Use `min-width: 0`, `overflow-wrap: anywhere`, and a body-level tooltip portal so explanatory text cannot be clipped by `.card { overflow: hidden }`.

- [ ] **Step 6: Run frontend tests and build**

Run: `cd frontend && npm test -- --run src/__tests__/familyCashflow.test.js src/__tests__/fixedBillsOverview.test.js && npm run build`

Expected: focused tests pass and Vite exits 0.

- [ ] **Step 7: Commit the overview slice**

```bash
git add frontend/src/components/family/FamilyFinanceDashboard.vue frontend/src/components/family/HouseholdCashflowCard.vue frontend/src/api/index.js frontend/src/__tests__/familyCashflow.test.js
git commit -m "feat: make family budget editable from overview"
```

### Task 3: Replace generic health output with household-first actions

**Files:**
- Modify: `backend/services/health/health_v2_service.py`
- Modify: `frontend/src/components/health/HealthDashboardV2.vue`
- Test: `backend/tests/test_health_strategy_governance.py`
- Test: `frontend/src/__tests__/healthStrategyGovernance.test.js`

- [ ] **Step 1: Write failing tests for action ordering and evidence**

```python
def test_household_actions_prioritize_missing_income_over_investment_actions(monkeypatch):
    cf = {"income_known": False, "data_available": False, "data_gaps": ["monthly_income"],
          "monthly_surplus": 0, "pending_bills": 0, "emergency_gap": 0,
          "forecast_3m": [], "expense_complete": False}
    diagnosis = build_household_diagnosis("default", cf)
    assert diagnosis["actions"][0]["category"] == "data_gap"
    assert diagnosis["actions"][0]["navigate_to"] == "family-finance"
```

```js
it('renders the household action list before the investment score', async () => {
  // supply a dashboard with household_diagnosis.actions and health_score
  expect(wrapper.get('[aria-label="家庭财务行动清单"]').isVisible()).toBe(true)
  expect(wrapper.find('.health-score-card').element.compareDocumentPosition(wrapper.get('[aria-label="家庭财务行动清单"]').element) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
})
```

- [ ] **Step 2: Run focused tests to confirm current action output does not satisfy the contract**

Run: `pytest -q backend/tests/test_health_strategy_governance.py -k household_actions` and `cd frontend && npm test -- --run src/__tests__/healthStrategyGovernance.test.js`

Expected: FAIL because diagnosis has warnings only and the health page renders investment sections first.

- [ ] **Step 3: Implement household action generation and cache invalidation**

Use the canonical cashflow signature already present in `get_health_v2_dashboard`; include `monthly_income`, `monthly_expense`, recurring bills, and forecast fields. Generate at most five actions with explicit evidence and CTA navigation. Keep investment actions after household actions and retain existing persistence/deduplication.

- [ ] **Step 4: Reorder the health page and add missing-data copy**

Place the action list immediately after the page header, show the household cashflow card beside it, and move score/four-pots sections below. Show “数据不足” explanations instead of zero scores when the relevant source is missing. Keep the existing recalculate/profile controls.

- [ ] **Step 5: Run health tests**

Run: `pytest -q backend/tests/test_health_strategy_governance.py backend/tests/test_audit_contracts.py -k "health or household"` and `cd frontend && npm test -- --run src/__tests__/healthStrategyGovernance.test.js`

Expected: all selected tests pass.

- [ ] **Step 6: Commit the diagnostic slice**

```bash
git add backend/services/health/health_v2_service.py frontend/src/components/health/HealthDashboardV2.vue backend/tests/test_health_strategy_governance.py frontend/src/__tests__/healthStrategyGovernance.test.js
git commit -m "feat: prioritize household actions in health diagnosis"
```

### Task 4: Integrate forecast evidence with buckets and complete verification

**Files:**
- Modify: `frontend/src/components/family/FixedBillsOverview.vue`
- Modify: `frontend/src/components/family/FamilyFinanceDashboard.vue`
- Modify: `backend/routers/dashboard/finance_dashboard.py`
- Test: `frontend/src/__tests__/fixedBillsOverview.test.js`
- Test: `backend/tests/test_audit_contracts.py`

- [ ] **Step 1: Add failing tests for archived buckets and forecast contract**

```python
def test_finance_dashboard_exposes_household_forecast_without_recomputing_frontend(monkeypatch):
    monkeypatch.setattr(route, "get_cashflow", lambda *args, **kwargs: {"forecast_3m": [], "monthly_income": 0})
    result = asyncio.run(route.get_finance_dashboard(month="2026-10"))
    assert "forecast_3m" in result["cash_flow"]
```

- [ ] **Step 2: Run the test and confirm the route contract is incomplete**

Run: `pytest -q backend/tests/test_audit_contracts.py -k forecast`

Expected: FAIL until the route passes through the canonical forecast fields.

- [ ] **Step 3: Wire forecast and bucket coverage into the response**

Pass through `forecast_3m`, `safe_to_spend`, `cash_runway_months`, and `data_gaps`. In the bucket summary, exclude archived buckets and add an `emergency_coverage_months` value based on necessary monthly outflow.

- [ ] **Step 4: Show actionable fixed-bill evidence**

Update `FixedBillsOverview.vue` to show the next due amount, paid/pending status, and the forecast month affected. Keep loan and insurance totals separate.

- [ ] **Step 5: Run complete relevant verification**

Run:

```bash
pytest -q backend/tests/test_family_cashflow.py backend/tests/test_db_expense.py backend/tests/test_recurring_expenses.py backend/tests/test_audit_contracts.py
cd frontend && npm test -- --run src/__tests__/familyCashflow.test.js src/__tests__/fixedBillsOverview.test.js src/__tests__/healthStrategyGovernance.test.js src/__tests__/familyAssetSummary.test.js
npm run build
```

Expected: selected backend and frontend tests pass; Vite exits 0.

- [ ] **Step 6: Review the diff and commit the integration slice**

```bash
git diff --check
git status --short
git add backend/routers/dashboard/finance_dashboard.py frontend/src/components/family/FixedBillsOverview.vue frontend/src/components/family/FamilyFinanceDashboard.vue backend/tests/test_audit_contracts.py frontend/src/__tests__/fixedBillsOverview.test.js
git commit -m "feat: connect family forecast to dashboard evidence"
```

Do not stage unrelated existing changes in the repository.
