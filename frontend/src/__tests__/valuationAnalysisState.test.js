import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { shallowMount, flushPromises } from '@vue/test-utils'
vi.mock('../composables/useTheme', () => ({ isDark: { value: false } }))
vi.mock('../api', async original => {
  const api = await original()
  return Object.fromEntries(Object.entries(api).map(([k, v]) => [k, typeof v === 'function' ? vi.fn(async () => ({ data: {} })) : v]))
})
import * as api from '../api'
import ValuationHistory from '../components/valuation/ValuationHistory.vue'
let wrapper
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r }); return { promise, resolve } }
async function page() { wrapper = shallowMount(ValuationHistory); await flushPromises(); return wrapper.vm.$.setupState }
beforeEach(() => {
  vi.clearAllMocks()
  api.listValuationIndexes.mockResolvedValue({ data: { indexes: [] } })
  api.listAsyncTasks.mockResolvedValue({ data: { tasks: [] } })
  api.listAnalysisHistory.mockResolvedValue({ data: { history: [] } })
})
afterEach(() => { wrapper?.unmount(); vi.useRealTimers() })
it('sends the selected metric and keeps concurrent metric tasks independent', async () => {
  vi.useFakeTimers()
  const state = await page()
  api.runAnalysis.mockResolvedValueOnce({ data: { id: 1, task_id: 11 } }).mockResolvedValueOnce({ data: { id: 2, task_id: 12 } })
  api.getAsyncTaskStatus.mockImplementation(id => Promise.resolve({ data: { status: 'done', result: { result: String(id) } } }))
  state.selectedCode = 'A'; state.selectedMetric = '市盈率'; await state.handleRunAnalysis()
  state.selectedMetric = '市净率'; await state.handleRunAnalysis()
  expect(api.runAnalysis).toHaveBeenNthCalledWith(2, 'A', 'A', null, '市净率')
  expect(state.runningCount).toBe(2)
  await vi.advanceTimersByTimeAsync(3000)
  expect(state.runningCount).toBe(0)
  expect(state.analysisResult.result).toBe('12')
  state.selectedMetric = '市盈率'
  expect(state.analysisResult.result).toBe('11')
})
it('restores every running task using its persisted index and metric', async () => {
  vi.useFakeTimers()
  api.listAsyncTasks.mockResolvedValue({ data: { tasks: [
    { id: 11, source_id: 1, result: { index_code: 'A', metric_type: '市盈率' } },
    { id: 12, source_id: 2, result: { index_code: 'B', metric_type: '市净率' } },
  ] } })
  api.getAsyncTaskStatus.mockResolvedValue({ data: { status: 'running' } })
  const state = await page()
  expect(state.runningCount).toBe(2)
  await vi.advanceTimersByTimeAsync(3000)
  expect(api.getAsyncTaskStatus).toHaveBeenCalledWith(11)
  expect(api.getAsyncTaskStatus).toHaveBeenCalledWith(12)
})
it('discards an analysis history response from a previous index selection', async () => {
  const state = await page(); const old = deferred()
  api.listAnalysisHistory.mockImplementation(code => code === 'A' ? old.promise : Promise.resolve({ data: { history: [{ id: 2, metric_type: '市净率', result: 'B' }] } }))
  state.selectedCode = 'A'; state.selectedMetric = '市净率'; const first = state.loadAnalysisHistory()
  state.selectedCode = 'B'; await state.loadAnalysisHistory()
  old.resolve({ data: { history: [{ id: 1, metric_type: '市净率', result: 'A' }] } }); await first
  expect(state.analysisHistory[0].result).toBe('B')
  expect(state.analysisResult.result).toBe('B')
})
it('does not restore a legacy history report as the current metric report', async () => {
  const state = await page(); state.selectedCode = 'A'; state.selectedMetric = '市净率'
  api.listAnalysisHistory.mockResolvedValue({ data: { history: [{ id: 1, result: 'legacy' }, { id: 2, metric_type: '市盈率', result: 'PE' }] } })
  await state.loadAnalysisHistory()
  expect(state.analysisResult).toBe(null)
})
it('stops pending task callbacks when unmounted', async () => {
  vi.useFakeTimers(); const state = await page(); const pending = deferred()
  api.runAnalysis.mockResolvedValue({ data: { id: 1, task_id: 11 } })
  api.getAsyncTaskStatus.mockReturnValue(pending.promise)
  state.selectedCode = 'A'; state.selectedMetric = '市盈率'; await state.handleRunAnalysis()
  await vi.advanceTimersByTimeAsync(3000); wrapper.unmount()
  pending.resolve({ data: { status: 'done', result: { result: 'late' } } }); await flushPromises()
  expect(state.analysisResult.result).not.toBe('late')
})
it('merges top N enrichment without truncating the full rule candidates', async () => {
  const state = await page()
  state.superValueData = { snapshot_id: 's1', opportunities: [{ index_code: 'A', score: 70 }, { index_code: 'B', score: 65 }] }
  api.getSuperValueDeep.mockResolvedValue({ data: { snapshot_id: 's1', opportunities: [{ index_code: 'A', score: 999, forecast_signal: 'signal' }], deep_analysis: [{ index_code: 'A', action: '观望' }] } })
  await state.loadSuperValueDeep()
  expect(state.superValueData.opportunities).toHaveLength(2)
  expect(state.superValueData.opportunities[0]).toMatchObject({ score: 70, forecast_signal: 'signal' })
})
it('replaces old candidates with an authoritative empty new snapshot', async () => {
  const state = await page(); state.superValueData = { snapshot_id: 'old', opportunities: [{ index_code: 'A' }] }
  api.getSuperValueDeep.mockResolvedValue({ data: { snapshot_id: 'new', opportunities: [], empty_reason: 'all_stale' } })
  await state.loadSuperValueDeep()
  expect(state.superValueData.opportunities).toEqual([])
})
it('manual refresh fetches a new snapshot and invalidates old explanations', async () => {
  const state = await page(); state.superValueData = { opportunities: [{ index_code: 'old' }] }; state.superValueDeepData = { deep_analysis: [{ index_code: 'old' }] }; state.strategyData = { strategies: [{ index_code: 'old' }] }
  api.getSuperValue.mockResolvedValue({ data: { opportunities: [] } })
  await state.loadSuperValue()
  expect(api.getSuperValue).toHaveBeenCalledOnce()
  expect(state.superValueData.opportunities).toEqual([])
  expect(state.superValueDeepData).toBe(null)
  expect(state.strategyData).toBe(null)
})
it('shows request failures and a retry action instead of a blank scan', async () => {
  const state = await page(); state.outerTab = 'super-value'
  api.getSuperValue.mockRejectedValue(new Error('offline'))
  await state.loadSuperValue(); await flushPromises()
  expect(wrapper.text()).toContain('offline')
  expect(wrapper.text()).toContain('刷新扫描')
})
it('maps whole strategy actions to a single style class', async () => {
  const state = await page()
  expect(state.strategyActionClass('分批建仓')).toBe('sv-action-buy')
  expect(state.strategyActionClass('观望')).toBe('sv-action-hold')
})
it('ignores a deep response invalidated by a newer manual scan', async () => {
  const state = await page(); const pending = deferred()
  state.superValueData = { snapshot_id: 'old', opportunities: [{ index_code: 'old' }] }
  api.getSuperValueDeep.mockReturnValue(pending.promise)
  const deep = state.loadSuperValueDeep()
  api.getSuperValue.mockResolvedValue({ data: { snapshot_id: 'new', opportunities: [] } }); await state.loadSuperValue()
  pending.resolve({ data: { snapshot_id: 'old', opportunities: [{ index_code: 'old' }], deep_analysis: [{ index_code: 'old' }] } }); await deep
  expect(state.superValueData.snapshot_id).toBe('new')
  expect(state.superValueDeepData).toBe(null)
})
it('labels disabled interpretation and all-stale scan results explicitly', async () => {
  const state = await page()
  api.getSuperValue.mockResolvedValue({ data: { snapshot_id: 's', opportunities: [], empty_reason: 'all_stale' } })
  state.outerTab = 'super-value'; await flushPromises()
  api.getSuperValueDeep.mockResolvedValue({ data: { snapshot_id: 's', opportunities: [], enabled: false, status: 'disabled' } })
  await state.loadSuperValueDeep(); await flushPromises()
  expect(wrapper.text()).toContain('均已过期')
  expect(wrapper.text()).toContain('深度解读未启用')
})
it('does not display an old history detail after a selection switch', async () => {
  const state = await page(); const pending = deferred()
  state.selectedCode = 'A'; state.selectedMetric = '市盈率'
  api.getAnalysisHistoryDetail.mockReturnValue(pending.promise)
  const detail = state.viewHistoryItem({ id: 1 })
  state.selectedCode = 'B'
  pending.resolve({ data: { id: 1, result: 'A' } }); await detail
  expect(state.viewingHistory).toBe(null)
})
it('serializes the fourth metric argument without changing the agent argument', async () => {
  const actual = await vi.importActual('../api')
  const { default: http } = await import('../api/http')
  const previous = http.defaults.adapter
  let sent
  http.defaults.adapter = async config => { sent = JSON.parse(config.data); return { data: { code: 0, message: 'ok', data: {} }, status: 200, statusText: 'OK', headers: {}, config } }
  try {
    await actual.runAnalysis('A', '指数', 9, '股息率')
    expect(sent).toEqual({ index_code: 'A', index_name: '指数', agent_id: 9, metric_type: '股息率' })
  } finally { http.defaults.adapter = previous }
})
it('does not roll back a newer strategy snapshot when an earlier deep request finishes late', async () => {
  const state = await page(); const pending = deferred()
  state.superValueData = { snapshot_id: 's1', opportunities: [{ index_code: 'A' }] }
  api.getSuperValueDeep.mockReturnValue(pending.promise)
  const deep = state.loadSuperValueDeep()
  api.getEnhancedStrategy.mockResolvedValue({ data: { snapshot_id: 's2', opportunities: [{ index_code: 'B' }], strategies: [{ index_code: 'B' }] } })
  await state.loadStrategy()
  pending.resolve({ data: { snapshot_id: 's1', opportunities: [{ index_code: 'A' }], deep_analysis: [{ index_code: 'A' }] } }); await deep
  expect(state.superValueData.snapshot_id).toBe('s2')
  expect(state.strategyData.strategies[0].index_code).toBe('B')
  expect(state.superValueDeepData).toBe(null)
})
it('clears candidates on an explicit no-candidates response even without a new snapshot id', async () => {
  const state = await page()
  state.superValueData = { snapshot_id: 's', opportunities: [{ index_code: 'A' }] }
  api.getSuperValueDeep.mockResolvedValue({ data: { snapshot_id: 's', opportunities: [], status: 'no_candidates', empty_reason: 'all_stale' } })
  await state.loadSuperValueDeep()
  expect(state.superValueData.opportunities).toEqual([])
})
