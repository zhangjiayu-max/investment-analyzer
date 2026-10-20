import { mount, shallowMount, flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
vi.hoisted(() => { vi.stubGlobal('localStorage', { getItem: vi.fn(), setItem: vi.fn(), removeItem: vi.fn() }) })
vi.mock('../api', async original => {
  const actual = await original()
  return Object.fromEntries(Object.entries(actual).map(([key, value]) => [key, typeof value === 'function' ? vi.fn(async () => ({ data: {} })) : value]))
})
vi.mock('../components/charts/LineChart.vue', () => ({ default: { template: '<div />' } }))
import * as api from '../api'
import Family from '../components/family/FamilyFinanceDashboard.vue'
import Portfolio from '../components/portfolio/PortfolioManagement.vue'
const wrappers = []
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r }); return { promise, resolve } }
beforeEach(() => {
  vi.clearAllMocks()
  api.getFinanceTrend.mockResolvedValue({ data: {} })
  api.getPortfolioSummary.mockReset().mockResolvedValue({ data: { holdings: [], total_value: 1000, cash_balance: 456 } })
  api.getCashBalance.mockReset().mockResolvedValue({ data: { balance: 1 } })
  api.autoConfirmPortfolioTransactions.mockResolvedValue({ data: {} })
  api.listAsyncTasks.mockResolvedValue({ data: { tasks: [] } })
  vi.spyOn(api.dailyAdviceAPI, 'getToday').mockResolvedValue({ data: {} })
})
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); vi.restoreAllMocks() })
describe('family investment asset composition', () => {
  it('explains the all-account total as holdings plus account cash without calling it full household net worth', async () => {
    api.getFinanceDashboard.mockResolvedValueOnce({ data: { net_worth: { total_assets: 1600.35, holding_value: 1000.10, cash_balance: 600.25, cash_scope: 'household_accounts', cash_accounts: [{ account: '小鱼儿', balance: 400.20 }, { account: '花无缺', balance: 200.05 }] } } })
    const w = mount(Family, { global: { stubs: { ExpenseManager: true, HouseholdCashflowCard: true } } }); wrappers.push(w); await flushPromises()
    const card = w.get('.net-worth-card')
    expect(card.text()).toContain('全部账户')
    expect(card.text()).toContain('持仓市值 ¥1,000.10 + 现金 ¥600.25 = 总资产 ¥1,600.35')
    expect(card.text()).toContain('小鱼儿 ¥400.20')
    expect(card.text()).toContain('花无缺 ¥200.05')
    expect(card.text()).toContain('完整家庭净资产')
    expect(w.find('.term-with-tip').exists()).toBe(false)
  })
  it('clearly labels legacy cash when account balances are absent', async () => {
    api.getFinanceDashboard.mockResolvedValueOnce({ data: { net_worth: { total_assets: 1600, holding_value: 1000, cash_balance: 600, cash_scope: 'legacy_default_cash', cash_accounts: [{ account: 'default', balance: 600 }] } } })
    const w = mount(Family, { global: { stubs: { ExpenseManager: true, HouseholdCashflowCard: true } } }); wrappers.push(w); await flushPromises()
    expect(w.get('.net-worth-card').text()).toContain('旧版家庭现金')
  })
})
describe('canonical holdings cash', () => {
  it('keeps legacy cash intact without reading or creating absent named balances', async () => {
    let namedAccountCreated = false
    api.getPortfolioSummary.mockImplementation(async () => ({ data: { holdings: [], cash_balance: namedAccountCreated ? 0 : 1234, cash_scope: namedAccountCreated ? 'household_accounts' : 'legacy_default_cash', cash_accounts: namedAccountCreated ? [{ account: '小鱼儿', balance: 0 }] : [{ account: 'default', balance: 1234 }] } }))
    api.getCashBalance.mockImplementation(async () => { namedAccountCreated = true; return { data: { balance: 0 } } })
    const w = shallowMount(Portfolio); wrappers.push(w); await flushPromises(); const state = w.vm.$.setupState
    expect(api.getCashBalance).not.toHaveBeenCalled()
    expect(state.totalCash).toBe(1234)
    await state.loadData({ skipAutoConfirm: true }); await flushPromises()
    expect(state.totalCash).toBe(1234)
  })
  it('waits for canonical metadata and only reads existing accounts in the selected scope', async () => {
    const canonical = deferred(); api.getPortfolioSummary.mockReturnValueOnce(canonical.promise)
    const w = shallowMount(Portfolio); wrappers.push(w)
    expect(api.getCashBalance).not.toHaveBeenCalled()
    canonical.resolve({ data: { holdings: [], cash_balance: 50, cash_scope: 'household_accounts', cash_accounts: [{ account: '小鱼儿', balance: 50 }] } }); await flushPromises()
    expect(api.getCashBalance).toHaveBeenCalledExactlyOnceWith('小鱼儿')
    api.getCashBalance.mockClear()
    api.getPortfolioSummary.mockResolvedValue({ data: { holdings: [], cash_balance: 0, cash_scope: 'household_accounts', cash_accounts: [] } })
    w.vm.$.setupState.accountFilter = '花无缺'; await flushPromises()
    expect(api.getCashBalance).not.toHaveBeenCalled()
  })
  it('uses the summary household cash even when auxiliary account reads disagree, including zero', async () => {
    const w = shallowMount(Portfolio); wrappers.push(w); await flushPromises()
    const state = w.vm.$.setupState
    expect(state.totalCash).toBe(456)
    state.summary = { holdings: [], total_value: 1000, cash_balance: 0 }
    expect(state.totalCash).toBe(0)
    state.summary = { holdings: [], total_value: 1000 }
    expect(state.totalCash).toBe(2)
  })
  it('forces a fresh canonical summary after cash adjustment and rejects an older in-flight summary', async () => {
    const w = shallowMount(Portfolio); wrappers.push(w); await flushPromises(); const state = w.vm.$.setupState
    const old = deferred(); const refreshed = deferred()
    api.getPortfolioSummary.mockReturnValueOnce(old.promise).mockReturnValueOnce(refreshed.promise)
    const oldLoad = state.loadData({ skipAutoConfirm: true })
    api.adjustCashBalance.mockResolvedValueOnce({ data: { balance: 700 } })
    state.cashForm = { user_id: '花无缺', amount: 700 }; state.cashMode = 'set'
    const adjustment = state.submitCashAdjust(); await flushPromises()
    expect(api.getPortfolioSummary).toHaveBeenCalledTimes(3)
    expect(state.totalCash).not.toBe(456)
    old.resolve({ data: { holdings: [], cash_balance: 456 } }); await oldLoad
    refreshed.resolve({ data: { holdings: [], cash_balance: 701 } }); await adjustment; await flushPromises()
    expect(state.totalCash).toBe(701)
  })
})
