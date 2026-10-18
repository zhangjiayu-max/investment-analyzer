import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
vi.mock('../api', () => ({
  listRecurringTemplates: vi.fn(async () => ({ data: { items: [] } })),
  getRecurringBills: vi.fn(async () => ({ data: { items: [{ template_id: 1, name: '房贷', category: '房贷', amount: 2000, due_date: '2026-01-15', status: 'pending' }], summary: {} } })),
  createRecurringTemplate: vi.fn(async () => ({ data: {} })), updateRecurringTemplate: vi.fn(async () => ({ data: {} })),
  deleteRecurringTemplate: vi.fn(async () => ({ data: {} })), payRecurringBill: vi.fn(async () => ({ data: {} })),
}))
import * as api from '../api'
import RecurringBills from '../components/family/RecurringBills.vue'
const wrappers = []
const button = (w, text) => w.findAll('button').find(b => b.text().includes(text))
function render() { const w = mount(RecurringBills, { props: { month: '2026-01', categories: ['房贷', '保险'] }, global: { stubs: { Teleport: true, Transition: false } } }); wrappers.push(w); return w }
beforeEach(() => vi.clearAllMocks())
afterEach(() => { wrappers.forEach(w => w.unmount()); wrappers.length = 0; vi.useRealTimers() })
describe('RecurringBills', () => {
  it('shows due and overdue reminders using the Shanghai calendar date', async () => {
    vi.useFakeTimers(); vi.setSystemTime(new Date('2026-01-14T17:00:00Z'))
    const w = render(); await flushPromises()
    expect(w.text()).toContain('今日到期（待支付）')
    w.unmount(); wrappers.pop()
    vi.setSystemTime(new Date('2026-01-15T17:00:00Z'))
    const overdue = render(); await flushPromises()
    expect(overdue.text()).toContain('已过到期日（待确认）')
    await button(overdue, '登记已支付').trigger('click')
    expect(overdue.text()).toContain('不会发起转账或扣款')
    expect(button(overdue, '确认已支付并记账')).toBeDefined()
  })
  it('confirms payment for the captured month and blocks duplicate clicks', async () => {
    const w = render(); await flushPromises(); await button(w, '登记已支付').trigger('click')
    expect(api.payRecurringBill).not.toHaveBeenCalled()
    await w.setProps({ month: '2026-02' }); await flushPromises()
    let resolvePay; api.payRecurringBill.mockImplementationOnce(() => new Promise(resolve => { resolvePay = resolve }))
    await button(w, '支付并记账').trigger('click'); await button(w, '执行中').trigger('click')
    expect(api.payRecurringBill).toHaveBeenCalledExactlyOnceWith(1, '2026-01')
    resolvePay({ data: {} }); await flushPromises()
  })
  it.each(['monthly', 'quarterly', 'yearly'])('saves %s recurring template with blank end month as null', async frequency => {
    const w = render(); await flushPromises()
    await w.get('[aria-label="账单名称"]').setValue('家庭保险')
    await w.get('[aria-label="账单金额"]').setValue('1200')
    await w.get('[aria-label="账单周期"]').setValue(frequency)
    await w.get('form').trigger('submit'); await flushPromises()
    expect(api.createRecurringTemplate).toHaveBeenCalledWith(expect.objectContaining({ name: '家庭保险', frequency, amount: 1200, start_month: '2026-01', end_month: null }))
  })
  it('edits templates without sending server-managed fields to strict API', async () => {
    api.listRecurringTemplates.mockResolvedValueOnce({ data: { items: [{ id: 3, name: '保险', active: true, category: '保险', amount: 1200, frequency: 'yearly', start_month: '2026-01', end_month: null, due_day: 10, created_at: 'server-date' }] } })
    const w = render(); await flushPromises(); await button(w, '编辑模板').trigger('click')
    await w.get('form').trigger('submit'); await flushPromises()
    const payload = api.updateRecurringTemplate.mock.calls[0][1]
    expect(payload).not.toHaveProperty('id')
    expect(payload).not.toHaveProperty('created_at')
    expect(payload).not.toHaveProperty('active')
    expect(payload.end_month).toBe(null)
  })
  it('saving template content after a confirmed stop cannot silently reactivate it', async () => {
    api.listRecurringTemplates.mockResolvedValueOnce({ data: { items: [{ id: 3, name: '保险', active: true, category: '保险', amount: 1200, frequency: 'yearly', start_month: '2026-01', end_month: null, due_day: 10 }] } })
    const w = render(); await flushPromises(); await button(w, '编辑模板').trigger('click')
    await button(w, '停用').trigger('click'); await button(w, '确认停用').trigger('click'); await flushPromises()
    expect(api.deleteRecurringTemplate).toHaveBeenCalledWith(3)
    await w.get('form').trigger('submit'); await flushPromises()
    expect(api.updateRecurringTemplate.mock.calls[0][1]).not.toHaveProperty('active')
  })
  it('confirms stopping and reactivating templates', async () => {
    api.listRecurringTemplates.mockResolvedValue({ data: { items: [{ id: 3, name: '保险', active: false, category: '保险', amount: 1200, frequency: 'yearly', start_month: '2026-01', end_month: null, due_day: 10 }] } })
    const w = render(); await flushPromises(); await button(w, '恢复').trigger('click')
    expect(api.updateRecurringTemplate).not.toHaveBeenCalled()
    await button(w, '确认恢复').trigger('click'); await flushPromises()
    expect(api.updateRecurringTemplate).toHaveBeenCalledWith(3, { active: true })
  })
})
