import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { shallowMount, flushPromises } from '@vue/test-utils'
import SmartAddPlan from '../components/finance/SmartAddPlan.vue'
const api = vi.hoisted(() => ({ getPlan: vi.fn(), getIndexExposure: vi.fn(), simulate: vi.fn(), updateConfig: vi.fn(), savePlan: vi.fn() }))
vi.mock('../api', () => ({ smartAddAPI: api }))
let wrapper
beforeEach(() => {
  vi.clearAllMocks()
  api.getPlan.mockResolvedValue({ enabled: true, plans: [], config: { tiers: [{ loss_pct: -10, release_pct: 15, add_ratio: 5 }] } })
  api.getIndexExposure.mockResolvedValue({ exposure: {} })
  api.simulate.mockResolvedValue({ strategies: [], scenario: {} })
})
afterEach(() => wrapper?.unmount())
function render() { wrapper = shallowMount(SmartAddPlan); return wrapper.vm.$.setupState }
it('preserves add_ratio during a configuration round trip', async () => {
  const state = render(); await flushPromises()
  expect(state.cfgForm.pyramid_tiers).toBe('10:15:5')
})
it('runs the comparison through the imported API', async () => {
  const state = render(); await flushPromises(); state.simStratCode = 'F'
  await state.runSimulate()
  expect(api.simulate).toHaveBeenCalledWith(expect.objectContaining({ fund_code: 'F' }))
  expect(state.simStratError).toBe('')
})
it('makes stale evidence and the one final amount visible', async () => {
  api.getPlan.mockResolvedValue({ enabled: true, summary: {}, plans: [{ fund_code: 'F', fund_name: '测试基金',
    has_signal: true, final_suggested_amount: 0, triggered_signals: [], safety: { can_add: false, data_warning: '估值已过期，等待补齐' } }] })
  render(); await flushPromises()
  expect(wrapper.text()).toContain('估值已过期，等待补齐')
  expect(wrapper.text()).toContain('当前可执行')
  expect(wrapper.text()).toContain('暂不执行')
})
it('does not save a tracking plan when the preview loads', async () => {
  render(); await flushPromises()
  expect(api.savePlan).not.toHaveBeenCalled()
  expect(wrapper.findComponent({ name: 'ConfirmDialog' }).exists()).toBe(true)
})
