import { defineComponent, h, KeepAlive, ref } from 'vue'
import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
vi.mock('../api', () => ({
  getExpenseCashflow: vi.fn(async () => ({ data: { data_available: true, recorded_expense: 777, monthly_expense: 3500, monthly_income: 10000 } })),
  getFinanceDashboard: vi.fn(async () => ({ data: { cash_flow: { data_available: true, recorded_expense: 777, monthly_expense: 3500, monthly_income: 10000 } } })),
  getFinanceTrend: vi.fn(async () => ({ data: {} })),
  listGoalBuckets: vi.fn(async () => ({ data: { items: [], summary: {} } })), listDecisions: vi.fn(async () => ({ data: { items: [] } })),
  createGoalBucket: vi.fn(), updateGoalBucket: vi.fn(), deleteGoalBucket: vi.fn(), syncGoalBuckets: vi.fn(), getDecisionPrecheck: vi.fn(),
  getHealthV2Dashboard: vi.fn(async () => ({ data: { asset_overview: { net_worth: null, total_assets: 12000, liabilities_status: 'unknown' } } })),
  getHealthV2Profile: vi.fn(async () => ({ data: {} })), getHealthV2History: vi.fn(async () => ({ data: { scores: [] } })), getHealthV2TargetPotsDefaults: vi.fn(async () => ({ data: {} })),
  recalculateHealthV2: vi.fn(), updateHealthV2Profile: vi.fn(), updateHealthV2ActionStatus: vi.fn(),
}))
vi.mock('../components/charts/LineChart.vue', () => ({ default: { template: '<div />' } }))
vi.mock('../components/family/ExpenseManager.vue', () => ({ default: { template: '<div />' } }))
import * as api from '../api'
import FamilyFinanceDashboard from '../components/family/FamilyFinanceDashboard.vue'
import GoalBucketsPage from '../components/decision/GoalBucketsPage.vue'
import HealthDashboardV2 from '../components/health/HealthDashboardV2.vue'
import HouseholdCashflowCard from '../components/family/HouseholdCashflowCard.vue'
describe('HouseholdCashflowCard', () => {
  it('shows recorded expense and incomplete budget forecast as distinct amounts', () => {
    const w = mount(HouseholdCashflowCard, { props: { cashflow: { data_available: true, expense_complete: false, expense_source: 'budget_and_records', recorded_expense: 200, budget_expense: 3000, pending_bills: 500, future_provision: 100, monthly_expense: 3600, monthly_income: 10000, monthly_surplus: 6400, investable_surplus: 6000, emergency_months: null } } })
    expect(w.text()).toContain('已记实际')
    expect(w.text()).toContain('¥200')
    expect(w.text()).toContain('¥3,600')
    expect(w.text()).toContain('尚未记全')
    expect(w.text()).toContain('预算兜底')
    expect(w.text()).toContain('保障评估')
    expect(w.text()).toContain('待确认')
    expect(w.text()).toContain('本月还贷')
    expect(w.text()).toContain('还贷占收入')
    expect(w.text()).toContain('月供不代表贷款余额')
  })
  it('keeps known amounts visible when income and budget are insufficient', () => {
    const w = mount(HouseholdCashflowCard, { props: { cashflow: { data_available: false, recorded_expense: 200, pending_bills: 500, future_provision: 100, monthly_income: 0, budget_expense: 0, investable_surplus: 0, monthly_surplus: 0, monthly_expense: 0, emergency_target: 0 } } })
    expect(w.text()).toContain('¥200')
    expect(w.text()).toContain('¥500')
    expect(w.text()).toContain('可投资额度暂不计算')
    const investable = w.findAll('.flow-grid > div').find(d => d.text().includes('可投资结余'))
    expect(investable.text()).toContain('待确认')
    const income = w.findAll('.flow-grid > div').find(d => d.text().startsWith('月收入'))
    expect(income.text()).toContain('待确认')
    const surplus = w.findAll('.flow-grid > div').find(d => d.text().startsWith('预测月结余'))
    expect(surplus.text()).toContain('待确认')
  })
  it('renders unavailable data without claiming zero surplus', () => {
    const w = mount(HouseholdCashflowCard, { props: { cashflow: null } })
    expect(w.text()).toContain('现金流暂不可用')
    expect(w.text()).not.toContain('¥0')
  })
})

const pageWrappers = []
beforeEach(() => vi.clearAllMocks())
afterEach(() => pageWrappers.splice(0).forEach(w => w.unmount()))
describe('shared household cashflow pages', () => {
  it.each([FamilyFinanceDashboard, GoalBucketsPage, HealthDashboardV2])('shows the same actual amount in $.__name', async component => {
    const w = mount(component, { global: { stubs: { Teleport: true, Transition: false } } }); pageWrappers.push(w)
    await flushPromises()
    expect(w.findComponent(HouseholdCashflowCard).exists()).toBe(true)
    expect(w.text()).toContain('¥777')
  })
  it('health shows live cashflow failure independently of cached investment score and unknown liabilities', async () => {
    api.getExpenseCashflow.mockRejectedValueOnce(new Error('offline'))
    const w = mount(HealthDashboardV2, { global: { stubs: { Teleport: true, Transition: false } } }); pageWrappers.push(w)
    await flushPromises()
    expect(w.text()).toContain('现金流暂不可用')
    expect(w.text()).toContain('已登记资产')
    expect(w.text()).toContain('负债余额未登记，净资产待确认')
    expect(w.text()).toContain('家庭收支诊断')
    expect(w.text()).toContain('投资健康分')
    expect(w.text()).toContain('¥12,000')
  })
})

describe('household page freshness', () => {
  it('refreshes household emergency target after saving emergency months', async () => {
    api.getExpenseCashflow.mockResolvedValueOnce({ data: { data_available: true, emergency_target: 6000 } }).mockResolvedValueOnce({ data: { data_available: true, emergency_target: 12000 } })
    api.updateHealthV2Profile.mockResolvedValueOnce({ data: { emergency_months: 12 } })
    const w = mount(HealthDashboardV2, { global: { stubs: { Teleport: true, Transition: false } } }); pageWrappers.push(w)
    await flushPromises()
    await w.findAll('button').find(b => b.text().includes('投资画像')).trigger('click'); await flushPromises()
    const group = w.findAll('.profile-drawer .form-group').find(g => g.text().includes('备用金月数'))
    await group.get('input').setValue('12')
    await w.findAll('.drawer-footer button').find(b => b.text() === '保存').trigger('click'); await flushPromises()
    expect(api.getExpenseCashflow).toHaveBeenCalledTimes(2)
    expect(w.findComponent(HouseholdCashflowCard).text()).toContain('¥12,000')
  })
  it.each([
    ['finance', FamilyFinanceDashboard, 'getFinanceDashboard'],
    ['buckets', GoalBucketsPage, 'listGoalBuckets'],
    ['health', HealthDashboardV2, 'getHealthV2Dashboard'],
  ])('refreshes canonical data when cached %s page is activated again', async (_, component, method) => {
    const visible = ref(true)
    const placeholder = defineComponent({ render: () => h('p', 'other page') })
    const host = defineComponent({ setup: () => () => h(KeepAlive, null, { default: () => visible.value ? h(component) : h(placeholder) }) })
    const w = mount(host, { global: { stubs: { Teleport: true, Transition: true } } }); pageWrappers.push(w)
    await flushPromises()
    visible.value = false; await flushPromises()
    const before = api[method].mock.calls.length
    visible.value = true; await flushPromises()
    expect(api[method].mock.calls.length).toBeGreaterThan(before)
  })
  it('shows unknown debt principal rather than claiming no liabilities', async () => {
    api.getFinanceDashboard.mockResolvedValueOnce({ data: { debt: { debt_summary: '无负债', liabilities_status: 'unknown' } } })
    const w = mount(FamilyFinanceDashboard, { global: { stubs: { Teleport: true, Transition: false } } }); pageWrappers.push(w)
    await flushPromises()
    expect(w.text()).toContain('负债余额未录入')
    expect(w.text()).not.toContain('无负债')
  })
})

describe('failed activation refresh', () => {
  it('hides stale investment actions while refreshing and leaves unavailable state on failure', async () => {
    api.getHealthV2Dashboard.mockResolvedValueOnce({ data: { actions: [{ action_id: 'old', title: '旧投资行动', category: 'cash_deploy' }] } })
    let rejectRefresh
    api.getHealthV2Dashboard.mockReturnValueOnce(new Promise((_, reject) => { rejectRefresh = reject }))
    const visible = ref(true)
    const placeholder = defineComponent({ render: () => h('p', 'other page') })
    const host = defineComponent({ setup: () => () => h(KeepAlive, null, { default: () => visible.value ? h(HealthDashboardV2) : h(placeholder) }) })
    const w = mount(host, { global: { stubs: { Teleport: true, Transition: true } } }); pageWrappers.push(w)
    await flushPromises(); expect(w.text()).toContain('旧投资行动')
    visible.value = false; await flushPromises(); visible.value = true; await flushPromises()
    expect(w.text()).not.toContain('旧投资行动')
    rejectRefresh(new Error('offline')); await flushPromises()
    expect(w.text()).toContain('诊断暂不可用')
    expect(w.text()).not.toContain('旧投资行动')
  })
})
