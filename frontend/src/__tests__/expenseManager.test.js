import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
vi.mock('../api', () => ({
  getExpenses: vi.fn(async () => ({ data: { items: [] } })),
  getExpenseSummary: vi.fn(async () => ({ data: { total: 0, fixed_total: 0, variable_total: 0 } })),
  getExpenseTrend: vi.fn(async () => ({ data: { items: [] } })),
  getExpenseCategories: vi.fn(async () => ({ data: { fixed: ['房贷', '保险'], variable: ['餐饮'] } })),
  getExpenseCashflow: vi.fn(async () => ({ data: { expense_complete: false, data_available: true } })),
  getProfile: vi.fn(async () => ({ data: { monthly_income: 10000, monthly_expense: 3000 } })),
  updateProfile: vi.fn(async () => ({ data: {} })),
  createExpense: vi.fn(async () => ({ data: {} })), updateExpense: vi.fn(async () => ({ data: {} })),
  deleteExpense: vi.fn(async () => ({ data: {} })), copyFixedExpenses: vi.fn(async () => ({ data: { copied: 1 } })),
  listRecurringTemplates: vi.fn(async () => ({ data: { items: [] } })), getRecurringBills: vi.fn(async () => ({ data: { items: [] } })),
  createRecurringTemplate: vi.fn(), updateRecurringTemplate: vi.fn(), deleteRecurringTemplate: vi.fn(), payRecurringBill: vi.fn(),
  setExpenseMonthComplete: vi.fn(async () => ({ data: {} })),
}))
vi.mock('../components/charts/LineChart.vue', () => ({ default: { template: '<div />' } }))
import * as api from '../api'
import ExpenseManager from '../components/family/ExpenseManager.vue'
const wrappers = []
function render(actualBills = false) {
  const w = mount(ExpenseManager, { props: { open: true }, global: { stubs: { Teleport: true, Transition: true, RecurringBills: !actualBills, HouseholdCashflowCard: true } } })
  wrappers.push(w); return w
}
const button = (w, text) => w.findAll('button').find(b => b.text().includes(text))
beforeEach(() => vi.clearAllMocks())
afterEach(() => { wrappers.forEach(w => w.unmount()); wrappers.length = 0; vi.useRealTimers() })
describe('ExpenseManager', () => {
  it('uses Shanghai current month at the UTC month boundary', async () => {
    vi.useFakeTimers(); vi.setSystemTime(new Date('2026-01-31T17:00:00Z'))
    const w = render(); await flushPromises()
    expect(w.get('input[type="month"]').element.value).toBe('2026-02')
  })
  it('removes previous-month edit controls while the next month request is pending', async () => {
    api.getExpenses.mockResolvedValueOnce({ data: { items: [{ id: 9, month: '2026-01', category: '保险', amount: 100, is_fixed: true }] } })
    const w = render(); await flushPromises()
    let resolveNext
    api.getExpenses.mockReturnValueOnce(new Promise(resolve => { resolveNext = resolve }))
    await w.get('input[type="month"]').setValue('2026-02')
    expect(w.find('button[title="编辑"]').exists()).toBe(false)
    resolveNext({ data: { items: [] } }); await flushPromises()
  })
  it('keeps the record original month when submitting an edit', async () => {
    api.getExpenses.mockResolvedValueOnce({ data: { items: [{ id: 9, month: '2026-01', category: '保险', amount: 100, is_fixed: true }] } })
    const w = render(); await flushPromises(); await w.get('button[title="编辑"]').trigger('click')
    await w.get('form.expense-form').trigger('submit'); await flushPromises()
    expect(api.updateExpense).toHaveBeenCalledWith(9, expect.objectContaining({ month: '2026-01' }))
  })
  it('preserves insurance category when editing fixed expense', async () => {
    api.getExpenses.mockResolvedValueOnce({ data: { items: [{ id: 3, category: '保险', amount: 200, is_fixed: true }] } })
    const w = render(); await flushPromises(); await w.get('button[title="编辑"]').trigger('click'); await flushPromises()
    expect(w.get('[aria-label="支出分类"]').element.value).toBe('保险')
    await w.get('form.expense-form').trigger('submit'); await flushPromises()
    expect(api.updateExpense).toHaveBeenCalledWith(3, expect.objectContaining({ category: '保险' }))
  })
  it('confirms copying paid manual records and captures January target month', async () => {
    const w = render(); await flushPromises(); await w.get('input[type="month"]').setValue('2026-01'); await flushPromises()
    await button(w, '复制上月固定项').trigger('click')
    expect(api.copyFixedExpenses).not.toHaveBeenCalled()
    await w.get('input[type="month"]').setValue('2026-03')
    await button(w, '确认复制').trigger('click'); await flushPromises()
    expect(api.copyFixedExpenses).toHaveBeenCalledWith('2025-12', '2026-01')
  })
  it('ignores old responses after month changes and drawer closes then reopens', async () => {
    let resolveOld
    api.getExpenses.mockReturnValueOnce(new Promise(resolve => { resolveOld = resolve }))
    const w = render(); await w.get('input[type="month"]').setValue('2026-02'); await flushPromises()
    await w.setProps({ open: false }); await w.setProps({ open: true }); await flushPromises()
    resolveOld({ data: { items: [{ id: 99, category: '过期响应', amount: 99 }] } }); await flushPromises()
    expect(w.text()).not.toContain('过期响应')
  })
  it('ignores a stale budget response after closing and reopening the drawer', async () => {
    let resolveOld
    api.getProfile.mockReturnValueOnce(new Promise(resolve => { resolveOld = resolve }))
    const w = render(); await flushPromises()
    await w.setProps({ open: false }); await w.setProps({ open: true }); await flushPromises()
    expect(w.get('[aria-label="月收入"]').element.value).toBe('10000')
    resolveOld({ data: { monthly_income: 1, monthly_expense: 1 } }); await flushPromises()
    expect(w.get('[aria-label="月收入"]').element.value).toBe('10000')
  })
  it('shows unavailable record data rather than empty-month totals after a failed fetch', async () => {
    api.getExpenses.mockRejectedValueOnce(new Error('offline'))
    const w = render(); await flushPromises()
    expect(w.text()).toContain('支出记录暂不可用')
    expect(w.find('.expense-summary').exists()).toBe(false)
  })
  it('confirms month completion against its captured month', async () => {
    const w = render(); await flushPromises(); await w.get('input[type="month"]').setValue('2026-01'); await flushPromises()
    await button(w, '确认本月已记全').trigger('click')
    expect(api.setExpenseMonthComplete).not.toHaveBeenCalled()
    await w.get('input[type="month"]').setValue('2026-02'); await flushPromises()
    await w.get('.dialog-actions .btn-primary').trigger('click'); await flushPromises()
    expect(api.setExpenseMonthComplete).toHaveBeenCalledWith('2026-01', true)
  })
  it('saves the direct profile income and budget fields intentionally', async () => {
    const w = render(); await flushPromises()
    await w.get('[aria-label="月收入"]').setValue('12000')
    await w.get('[aria-label="月支出预算"]').setValue('4000')
    await w.get('form.budget-form').trigger('submit'); await flushPromises()
    expect(api.updateProfile).toHaveBeenCalledWith({ monthly_income: 12000, monthly_expense: 4000 })
  })
  it('reloads bill state immediately after deleting a linked payment record', async () => {
    api.getExpenses.mockResolvedValueOnce({ data: { items: [{ id: 8, category: '保险', amount: 200, recurring_template_id: 2 }] } })
    const w = render(true); await flushPromises()
    expect(api.getRecurringBills).toHaveBeenCalledTimes(1)
    await w.get('button[title="删除"]').trigger('click')
    await w.get('.dialog-actions .btn-primary').trigger('click'); await flushPromises()
    expect(api.getRecurringBills.mock.calls.length).toBeGreaterThan(1)
  })
  it('prevents editing linked paid records and explains deletion returns bill to pending', async () => {
    api.getExpenses.mockResolvedValueOnce({ data: { items: [{ id: 8, category: '保险', amount: 200, is_fixed: true, recurring_template_id: 2 }] } })
    const w = render(); await flushPromises()
    expect(w.get('button[title="编辑"]').attributes('disabled')).toBeDefined()
    await w.get('button[title="删除"]').trigger('click')
    expect(w.text()).toContain('待支付')
  })
})
