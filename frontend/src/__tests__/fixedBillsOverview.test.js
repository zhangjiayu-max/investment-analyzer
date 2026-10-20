import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import FixedBillsOverview from '../components/family/FixedBillsOverview.vue'

describe('FixedBillsOverview', () => {
  it('renders monthly obligations, category totals, and paid snapshot differences', () => {
    const w = mount(FixedBillsOverview, { props: {
      month: '2026-02', cashflow: {
        monthly_provision: 7000, recurring_due_total: 6742.14, recurring_paid: 6742.14,
        recurring_pending: 0, recurring_count: 5,
        recurring_bills: [
          { template_id: 1, month: '2026-02', name: '车贷', category: '贷款', amount: 6364.87, due_date: '2026-02-10', status: 'paid', current_template_amount: 6400 },
          { template_id: 2, month: '2026-02', name: '保险', category: '保险', amount: 377.27, due_date: '2026-02-15', status: 'paid', current_template_amount: 400 },
        ], recurring_by_category: [{ category: '贷款', paid: 6364.87, pending: 0, total: 6364.87 }],
      },
    } })
    expect(w.text()).toContain('月均固定负担')
    expect(w.text()).toContain('¥7,000.00')
    expect(w.text()).toContain('本月应付')
    expect(w.text()).toContain('¥6,742.14')
    expect(w.text()).toContain('车贷')
    expect(w.text()).toContain('实际 ¥6,364.87')
    expect(w.text()).toContain('计划 ¥6,400.00')
    expect(w.text()).toContain('已付')
  })

  it('distinguishes no active bills from a month with no due bills', () => {
    const noActive = mount(FixedBillsOverview, { props: { month: '2026-02', cashflow: { recurring_count: 0, recurring_bills: [] } } })
    expect(noActive.text()).toContain('暂无启用的固定账单')
    const outsideDue = mount(FixedBillsOverview, { props: { month: '2026-02', cashflow: { recurring_count: 2, recurring_bills: [] } } })
    expect(outsideDue.text()).toContain('本月没有应付账单')
  })
})
