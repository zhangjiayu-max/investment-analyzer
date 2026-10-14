import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

vi.mock('../api', () => ({
  getExpenses: vi.fn(() => Promise.resolve({ data: { items: [] } })),
  getExpenseSummary: vi.fn(() => Promise.resolve({ data: { total: 0, fixed_total: 0, variable_total: 0 } })),
  getExpenseTrend: vi.fn(() => Promise.resolve({ data: { items: [] } })),
  getExpenseCategories: vi.fn(() => Promise.resolve({ data: { fixed: ['房贷'], variable: ['餐饮'] } })),
  createExpense: vi.fn(),
  updateExpense: vi.fn(),
  deleteExpense: vi.fn(),
  copyFixedExpenses: vi.fn(),
}))

import ExpenseManager from '../components/family/ExpenseManager.vue'

describe('ExpenseManager', () => {
  it('renders the monthly expense form when open', () => {
    const wrapper = mount(ExpenseManager, {
      props: { open: true },
      global: { stubs: { Teleport: true, Transition: false, ConfirmDialog: true, LineChart: true } },
    })

    expect(wrapper.text()).toContain('本月支出')
    expect(wrapper.find('input[type="number"]').exists()).toBe(true)
  })

  it('is mounted from the household finance dashboard', () => {
    const testDir = path.dirname(fileURLToPath(import.meta.url))
    const source = fs.readFileSync(path.join(testDir, '../components/family/FamilyFinanceDashboard.vue'), 'utf8')
    expect(source).toContain('ExpenseManager')
    expect(source).toContain('记录支出')
  })
})
