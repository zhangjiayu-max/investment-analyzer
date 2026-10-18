import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { shallowMount, flushPromises } from '@vue/test-utils'
import AdminAgentsPage from '../components/agent/AdminAgentsPage.vue'
import TokenUsagePage from '../components/quality/TokenUsagePage.vue'
import SystemConfigPage from '../components/quality/SystemConfigPage.vue'
import * as api from '../api'

vi.mock('../api', () => Object.fromEntries([
  'listAgents', 'getAgent', 'updateAgent', 'deleteAgent', 'listAnalysisAgents', 'updateAnalysisAgent',
  'generateAgentPrompt', 'listAgentVersions', 'rollbackAgentPrompt', 'listAnalysisAgentVersions',
  'rollbackAnalysisAgentPrompt', 'getAgentRegressionResult', 'getTokenUsage', 'getTokenUsageRecent',
  'getTokenUsageSummary', 'getTokenUsageByCaller', 'getTokenUsageDaily', 'getPerformanceStats',
  'getPerformanceByAgent', 'getRunningAgents', 'clearTokenUsage', 'getTokenUsageCost',
  'getTokenUsageByModel', 'getTokenUsageHourly', 'getTokenUsageTrace', 'getTokenUsageBudget',
  'getSystemConfigs', 'updateSystemConfig', 'resetSystemConfigs',
].map(name => [name, vi.fn(async () => ({ data: { items: [], agents: [], records: [], configs: [], today: {} } }))])))

const wrappers = []
function mount(component) {
  const wrapper = shallowMount(component)
  wrappers.push(wrapper)
  return wrapper
}
beforeEach(() => {
  vi.clearAllMocks()
  api.getTokenUsageCost.mockResolvedValue({ data: { total_cost: 30, today_cost: .3, week_cost: 7, month_cost: 18 } })
  api.getAgentRegressionResult.mockResolvedValue({ data: { status: 'none' } })
})
afterEach(() => wrappers.splice(0).forEach(w => w.unmount()))

describe('Agent publication state', () => {
  it('loads the newly selected agent history and never offers the previous agent version', async () => {
    api.listAgentVersions.mockImplementation(async id => ({ data: { versions: [{ id, version: 1, system_prompt: `agent-${id}` }] } }))
    const w = mount(AdminAgentsPage)
    await flushPromises()
    w.vm.selectAgent({ id: 1 })
    w.vm.toggleVersions()
    await flushPromises()
    w.vm.selectAgent({ id: 2 })
    if (!w.vm.showVersions) w.vm.toggleVersions()
    await flushPromises()
    expect(api.listAgentVersions).toHaveBeenLastCalledWith(2)
    expect(w.vm.versions[0].system_prompt).toBe('agent-2')
  })

  it('ignores a stale regression request after another agent was selected', async () => {
    let finish
    api.getAgentRegressionResult.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    const w = mount(AdminAgentsPage)
    await flushPromises()
    w.vm.selectAgent({ id: 1 })
    w.vm.selectAgent({ id: 2 })
    await flushPromises()
    finish({ data: { status: 'completed', agent: 1 } })
    await flushPromises()
    expect(w.vm.regressionResult).toEqual({ status: 'none' })
  })
})

describe('Token window and cost semantics', () => {
  it('shows server calendar costs before details load and keeps them when paging details', async () => {
    const w = mount(TokenUsagePage)
    await flushPromises()
    expect(w.vm.todayCost).toBe(.3)
    expect(w.vm.weekCost).toBe(7)
    expect(w.vm.monthCost).toBe(18)
    w.vm.records = [{ model: 'mimo', prompt_tokens: 10000000, created_at: '2020-01-01' }]
    expect(w.vm.todayCost).toBe(.3)
  })

  it('uses the selected calendar window on the first details and performance visits', async () => {
    const w = mount(TokenUsagePage)
    await flushPromises()
    w.vm.globalDays = 30
    w.vm.loadAllData()
    w.vm.switchTab('details')
    w.vm.switchTab('perf')
    await flushPromises()
    expect(api.getTokenUsageRecent.mock.calls.at(-1)[2]).toBe(30)
    expect(api.getPerformanceByAgent).toHaveBeenLastCalledWith(30)
  })

  it('loads trace aggregation and reports a failed query explicitly', async () => {
    api.getTokenUsageTrace.mockRejectedValueOnce(new Error('trace unavailable'))
    const w = mount(TokenUsagePage)
    await flushPromises()
    w.vm.traceInput = 'test'
    await w.vm.searchTrace()
    expect(w.text()).toContain('trace unavailable')
  })
})

it('asks to confirm configuration changes and keeps a visible error on failure', async () => {
  api.getSystemConfigs.mockResolvedValue({ data: { configs: [{ key: 'llm.timeout_default', value: '120', category: 'llm', description: '超时' }] } })
  api.updateSystemConfig.mockRejectedValueOnce(new Error('save failed'))
  const w = mount(SystemConfigPage)
  await flushPromises()
  w.vm.startEdit('llm.timeout_default', '120')
  w.vm.editingValue = '9'
  await w.vm.saveEdit('llm.timeout_default')
  expect(api.updateSystemConfig).not.toHaveBeenCalled()
  expect(w.vm.confirm.visible).toBe(true)
  await w.vm.confirm.onConfirm()
  expect(w.text()).toContain('save failed')
})
