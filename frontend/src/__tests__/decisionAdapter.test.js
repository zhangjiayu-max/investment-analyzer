import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

vi.mock('../api', async (original) => {
  const api = await original()
  const mocked = Object.fromEntries(Object.entries(api).map(([key, value]) => [key, typeof value === 'function' ? vi.fn(async () => ({ data: {} })) : value]))
  mocked.decisionAPI = Object.fromEntries(Object.keys(api.decisionAPI).map(key => [key, vi.fn(async () => null)]))
  return mocked
})

import * as api from '../api'
import DecisionCenter from '../components/decision/DecisionCenter.vue'
import DecisionRecordsPage from '../components/decision/DecisionRecordsPage.vue'

const wrappers = []

beforeEach(() => {
  vi.stubGlobal('localStorage', { getItem: vi.fn(), setItem: vi.fn(), removeItem: vi.fn() })
})

afterEach(() => {
  wrappers.splice(0).forEach(w => w.unmount())
  vi.clearAllMocks()
  vi.unstubAllGlobals()
})

it('shows archive link for a linked quant decision', async () => {
  api.decisionAPI.getLedger.mockResolvedValue([{ id: 7, fund_name: '测试基金', verdict: 'watch', decision_record_id: 21 }])
  api.decisionAPI.getAccuracy.mockResolvedValue({ overall: { reviewed: 0, hits: 0, hit_rate_pct: 0 } })
  const wrapper = mount(DecisionCenter, { global: { stubs: { Icon: true, ConfirmDialog: true, Teleport: true } } })
  wrappers.push(wrapper)
  await flushPromises()
  expect(wrapper.text()).toContain('已关联档案')
})

it('creates an archive from an unlinked quant decision', async () => {
  api.decisionAPI.getLedger.mockResolvedValue([{ id: 8, fund_name: '待关联基金', verdict: 'watch', decision_record_id: null }])
  api.decisionAPI.getAccuracy.mockResolvedValue({ overall: { reviewed: 0, hits: 0, hit_rate_pct: 0 } })
  api.decisionAPI.createArchive.mockResolvedValue({ decision_record_id: 31 })
  const wrapper = mount(DecisionCenter, { global: { stubs: { Icon: true, ConfirmDialog: true, Teleport: true } } })
  wrappers.push(wrapper)
  await flushPromises()
  const button = wrapper.findAll('button').find(item => item.text().includes('创建档案'))
  expect(button).toBeTruthy()
  await button.trigger('click')
  await flushPromises()
  expect(api.decisionAPI.createArchive).toHaveBeenCalledWith(8)
})

it('renders quant ledger details inside a linked archive', async () => {
  api.listDecisions.mockResolvedValue({ data: { items: [{ id: 4, target_name: '测试基金', summary: '观察', status: 'proposed', investment_decision_id: 12 }] } })
  api.getDecisionStats.mockResolvedValue({ data: { by_status: {}, total: 1 } })
  api.getDecisionPrecheck.mockResolvedValue({ data: {} })
  api.getExecutionStatus.mockResolvedValue({ data: { matches: [] } })
  api.listRecommendationCandidates.mockResolvedValue({ data: { items: [] } })
  api.getDecision.mockResolvedValue({ data: {
    id: 4, target_name: '测试基金', summary: '观察', status: 'proposed', investment_decision_id: 12,
    investment_decision: { confidence: 0.8, suggested_amount: 500, verdict: 'watch' },
  } })
  const wrapper = mount(DecisionRecordsPage, { global: { stubs: { Icon: true, ConfirmDialog: true, Teleport: true } } })
  wrappers.push(wrapper)
  await flushPromises()
  const timeline = wrapper.findAll('button').find(item => item.text().includes('时间线'))
  await timeline.trigger('click')
  await flushPromises()
  const card = wrapper.find('.decision-card')
  await card.trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('量化账本')
})
