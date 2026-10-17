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
import DataHealthDashboard from '../components/quality/DataHealthDashboard.vue'
import { useTaskTracker } from '../composables/useTaskTracker'
const wrappers = []
beforeEach(() => { vi.stubGlobal('localStorage', { getItem: vi.fn(), setItem: vi.fn(), removeItem: vi.fn() }) })
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); vi.clearAllMocks(); vi.useRealTimers(); vi.unstubAllGlobals() })
it('shows already unwrapped decision ledger and accuracy', async () => {
  api.decisionAPI.getLedger.mockResolvedValue([{ id: 42, fund_name: '协议基金', verdict: 'watch' }])
  api.decisionAPI.getAccuracy.mockResolvedValue({ overall: { reviewed: 10, hits: 7, hit_rate_pct: 70 } })
  const wrapper = mount(DecisionCenter, { global: { stubs: { Icon: true, ConfirmDialog: true, Teleport: true } } }); wrappers.push(wrapper)
  await flushPromises()
  expect(wrapper.text()).toContain('协议基金'); expect(wrapper.text()).toContain('70.0%')
})
it('waits for decision background task terminal result before reporting completion', async () => {
  vi.useFakeTimers()
  api.decisionAPI.getLedger.mockResolvedValue([])
  api.decisionAPI.run.mockResolvedValue({ task_id: 'decision-1' })
  api.getAsyncTaskStatus.mockResolvedValue({ data: { status: 'done', result: { verdict: 'watch', confidence: 0.8 } } })
  const wrapper = mount(DecisionCenter, { global: { stubs: { Icon: true, ConfirmDialog: true, Teleport: true } } }); wrappers.push(wrapper)
  await flushPromises()
  const state = wrapper.vm.$.setupState
  state.fundCode = '510300'; state.askRunDecision(); await state.onConfirm()
  expect(wrapper.text()).toContain('执行中')
  await vi.advanceTimersByTimeAsync(3000); await flushPromises()
  expect(wrapper.text()).toContain('决策已生成')
  vi.useRealTimers()
})
it('renders all ten health sources from Axios data', async () => {
  const health = Object.fromEntries(Array.from({ length: 10 }, (_, i) => [`source${i}`, { label: `来源${i}`, count: i + 1, stale_days: 0 }]))
  api.getDataHealth.mockResolvedValue({ data: { health } })
  const wrapper = mount(DataHealthDashboard); wrappers.push(wrapper); await flushPromises()
  expect(wrapper.findAll('.dh-card')).toHaveLength(10)
})
it('recovers wrapped running tasks and notifies exactly once on completion', async () => {
  const tracker = useTaskTracker(); tracker.stopPolling(); tracker.pendingTasks.value = []
  tracker.addTask(12, 3, '后台对话')
  api.getRunningConversationTasks.mockResolvedValue({ data: { tasks: [{ conversation_id: 12, message_id: 3 }] } })
  await tracker.fetchRunningTasks()
  expect(tracker.pendingTasks.value.map(t => t.convId)).toEqual([12])
  api.getConversationTaskStatus.mockResolvedValue({ data: { status: 'completed' } })
  await tracker.checkAllTasks(); const completed = tracker.completedTask.value
  expect(completed.convId).toBe(12)
  await tracker.checkAllTasks(); expect(tracker.completedTask.value).toBe(completed)
})
it.each([
  ['backtest', 'runBacktest', { processed: 4, hits: 3, weight_changes: [] }, '回测完成：处理 4'],
  ['exit', 'runExitScan', { scanned: 6, alerts_created: 2 }, '止盈扫描完成：扫描 6'],
])('waits for %s task results instead of reporting zero on submission', async (type, method, result, expected) => {
  vi.useFakeTimers()
  api.decisionAPI.getLedger.mockResolvedValue([])
  api.decisionAPI[method].mockResolvedValue({ task_id: `${type}-task` })
  api.getAsyncTaskStatus.mockResolvedValue({ data: { status: 'done', result } })
  const wrapper = mount(DecisionCenter, { global: { stubs: { Icon: true, ConfirmDialog: true, Teleport: true } } }); wrappers.push(wrapper); await flushPromises()
  const state = wrapper.vm.$.setupState
  state.confirm = { type, loading: false, visible: true }; await state.onConfirm()
  expect(wrapper.text()).toContain('执行中'); expect(wrapper.text()).not.toContain(expected)
  await vi.advanceTimersByTimeAsync(3000); await flushPromises()
  expect(wrapper.text()).toContain(expected)
})
it('preserves pending task credentials on temporary failures and malformed running lists', async () => {
  const tracker = useTaskTracker(); tracker.pendingTasks.value = []; tracker.addTask(21, 5)
  api.getRunningConversationTasks.mockResolvedValue({ data: {} })
  await tracker.fetchRunningTasks()
  expect(tracker.pendingTasks.value[0].convId).toBe(21)
  api.getConversationTaskStatus.mockRejectedValue(new Error('offline'))
  const warning = vi.spyOn(console, 'error').mockImplementation(() => {})
  await tracker.checkAllTasks()
  expect(tracker.pendingTasks.value[0].convId).toBe(21)
  warning.mockRestore()
})

it.each(['cancelled', 'no_message'])('clears authoritative %s tasks without a failure notification', async status => {
  const tracker = useTaskTracker(); tracker.stopPolling(); tracker.pendingTasks.value = []; tracker.clearCompletedTask()
  tracker.addTask(31, 6)
  api.getRunningConversationTasks.mockResolvedValue({ data: { tasks: [] } })
  await tracker.fetchRunningTasks()
  api.getConversationTaskStatus.mockResolvedValue({ data: { status } })
  await tracker.checkAllTasks()
  expect(tracker.pendingTasks.value).toEqual([])
  expect(tracker.completedTask.value).toBe(null)
})
