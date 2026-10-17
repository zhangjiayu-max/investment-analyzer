import { shallowMount, mount, flushPromises } from '@vue/test-utils'
import { h, KeepAlive } from 'vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
vi.hoisted(() => { vi.stubGlobal('localStorage', { getItem: vi.fn(), setItem: vi.fn(), removeItem: vi.fn() }) })
vi.mock('../api', async (original) => {
  const actual = await original()
  return Object.fromEntries(Object.entries(actual).map(([key, value]) => [key, typeof value === 'function' ? vi.fn(async () => ({ data: {} })) : value]))
})
import * as api from '../api'
import MarketIntelligence from '../components/market/MarketIntelligence.vue'
import ChatView from '../components/ChatView.vue'
import ValuationHistory from '../components/valuation/ValuationHistory.vue'
import Portfolio from '../components/portfolio/PortfolioManagement.vue'
import Dashboard from '../components/Dashboard.vue'
import EventRadar from '../components/market/EventRadarPage.vue'
import AlertCenter from '../components/task/AlertCenter.vue'
const wrappers = []
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r }); return { promise, resolve } }
const mountPage = component => { const w = shallowMount(component); wrappers.push(w); return w }
beforeEach(() => {
  vi.clearAllMocks()
  api.autoConfirmPortfolioTransactions.mockResolvedValue({ data: {} })
  api.listAsyncTasks.mockResolvedValue({ data: { tasks: [] } })
  api.listAgents.mockResolvedValue({ data: { agents: [] } })
  api.listValuationIndexes.mockResolvedValue({ data: { indexes: [] } })
  api.getPortfolioSummary.mockResolvedValue({ data: { holdings: [] } })
  vi.spyOn(api.dailyAdviceAPI, 'getToday').mockResolvedValue({ data: {} })
  api.listConversations.mockResolvedValue({ data: { conversations: [] } })
})
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); vi.useRealTimers() })
it('keeps selected conversation B when A responds last', async () => {
  const wrapper = mountPage(ChatView); await flushPromises(); const state = wrapper.vm.$.setupState
  const a = deferred(); api.getMessages.mockImplementation(id => id === 1 ? a.promise : Promise.resolve({ data: { messages: [{ role: 'user', content: 'B' }] } }))
  state.selectedConv = { id: 1 }; const first = state.loadMessages(1)
  state.selectedConv = { id: 2 }; await state.loadMessages(2)
  a.resolve({ data: { messages: [{ role: 'user', content: 'A' }] } }); await first
  expect(state.messages[0].content).toBe('B')
})
it('keeps selected index and metric when an older history returns late', async () => {
  const wrapper = mountPage(ValuationHistory); await flushPromises(); const state = wrapper.vm.$.setupState
  const a = deferred(); api.getValuationHistory.mockImplementation(code => code === 'A' ? a.promise : Promise.resolve({ data: { history: [{ id: 'B' }], latest: { id: 'B' } } }))
  state.selectedCode = 'A'; state.selectedMetric = 'PE'; const first = state.loadHistory()
  state.selectedCode = 'B'; await state.loadHistory()
  a.resolve({ data: { history: [{ id: 'A' }], latest: { id: 'A' } } }); await first
  expect(state.latest.id).toBe('B')
})
it('starts holdings immediately despite slow auto-confirm and keeps current account', async () => {
  const confirmation = deferred(); api.autoConfirmPortfolioTransactions.mockReturnValue(confirmation.promise)
  const wrapper = mountPage(Portfolio)
  expect(api.getPortfolioSummary).toHaveBeenCalledTimes(1)
  confirmation.resolve({ data: {} }); await flushPromises(); const state = wrapper.vm.$.setupState
  const a = deferred(); api.getPortfolioSummary.mockImplementation(params => params?.account === 'A' ? a.promise : Promise.resolve({ data: { holdings: [{ id: 'B' }] } }))
  state.accountFilter = 'A'; const first = state.loadData()
  state.accountFilter = 'B'; await state.loadData()
  a.resolve({ data: { holdings: [{ id: 'A' }] } }); await first
  expect(state.holdings[0].id).toBe('B')
})
it.each([[Dashboard, 'getDashboard'], [Portfolio, 'getPortfolioSummary'], [AlertCenter, 'listAlerts']])('loads %s core once on KeepAlive initial entry', async (component, method) => {
  api.listAsyncTasks.mockReturnValue(new Promise(() => {}))
  const wrapper = mount({ render: () => h(KeepAlive, null, () => h(component)) }, { shallow: true, global: { stubs: { KeepAlive: false, [component.__name]: false } } }); wrappers.push(wrapper)
  await flushPromises()
  expect(api[method]).toHaveBeenCalledTimes(1)
})
it('reconciles a visible disconnected stream using only execution-state and replay with backoff', async () => {
  vi.useFakeTimers()
  const wrapper = mountPage(ChatView); await flushPromises(); const state = wrapper.vm.$.setupState
  state.selectedConv = { id: 9, title: '断线任务' }
  api.getMessages.mockResolvedValue({ data: { messages: [{ role: 'assistant', content: '最终结果', metadata: { execution_status: 'completed' } }] } })
  api.getConversationExecutionState.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({ data: { item: { channel_id: 'ch9', channel_status: 'running' } } }).mockResolvedValue({ data: { item: { channel_id: 'ch9', channel_status: 'completed' } } })
  let receive
  api.sendMessageStream.mockImplementation((_id, _text, callback) => { receive = callback; return { abort: vi.fn() } })
  api.replayConversationStream.mockImplementation((_id, _channel, _seq, callback) => { queueMicrotask(() => callback({ type: 'disconnect', data: {} })); return { abort: vi.fn() } })
  state.sendMessageAndTrack(9, 'question'); receive({ type: 'disconnect', data: {} })
  await vi.advanceTimersByTimeAsync(30000); await flushPromises()
  expect(api.replayConversationStream).toHaveBeenCalledTimes(1)
  expect(api.sendMessageStream).toHaveBeenCalledTimes(1)
  expect(api.resumeConversationStream).not.toHaveBeenCalled()
  expect(state.messages[0].content).toBe('最终结果')
  expect(state.getStreamState(9)?.sending).not.toBe(true)
})

it('keeps event scan credentials across a short disconnect and ten minutes running', async () => {
  vi.useFakeTimers()
  api.triggerEventRadarScan.mockResolvedValue({ data: { task_id: 'scan9' } })
  api.getEventRadarScanStatus.mockRejectedValueOnce(new Error('offline')).mockResolvedValue({ data: { status: 'running' } })
  const wrapper = mountPage(EventRadar); await flushPromises(); const state = wrapper.vm.$.setupState
  state.handleScan()
  await vi.advanceTimersByTimeAsync(10 * 60 * 1000 + 15000)
  expect(state.scanTaskId).toBe('scan9')
  expect(state.scanning).toBe(true)
  expect(localStorage.removeItem).not.toHaveBeenCalledWith('event-radar-scan-task')
  api.getEventRadarScanStatus.mockResolvedValue({ data: { status: 'done', result: {} } })
  await vi.advanceTimersByTimeAsync(15000)
  expect(state.scanTaskId).toBe(null)
  expect(state.scanning).toBe(false)
})

it('releases an expired event scan after an authoritative 404 so a new scan can start', async () => {
  vi.useFakeTimers()
  api.triggerEventRadarScan.mockResolvedValue({ data: { task_id: 'expired-scan' } })
  api.getEventRadarScanStatus.mockRejectedValue({ response: { status: 404 } })
  const wrapper = mountPage(EventRadar); await flushPromises(); const state = wrapper.vm.$.setupState
  await state.handleScan()
  await vi.advanceTimersByTimeAsync(3000)
  expect(state.scanTaskId).toBe(null)
  expect(state.scanning).toBe(false)
  expect(localStorage.removeItem).toHaveBeenCalledWith('event-radar-scan-task')
  api.triggerEventRadarScan.mockResolvedValue({ data: { task_id: 'new-scan' } })
  await state.handleScan()
  expect(state.scanTaskId).toBe('new-scan')
})

it('preserves an actual Axios 404 through interceptors to release the expired scan', async () => {
  vi.useFakeTimers()
  const { default: http } = await import('../api/http')
  const { AxiosError } = await import('axios')
  const actual = await vi.importActual('../api')
  const previous = http.defaults.adapter
  http.defaults.adapter = async config => {
    const response = { status: 404, statusText: 'Not Found', config, headers: {}, data: { code: 404, message: '任务不存在或已过期', data: null } }
    throw new AxiosError('Request failed', 'ERR_BAD_REQUEST', config, {}, response)
  }
  const warning = vi.spyOn(console, 'error').mockImplementation(() => {})
  try {
    api.triggerEventRadarScan.mockResolvedValue({ data: { task_id: 'expired-real' } })
    api.getEventRadarScanStatus.mockImplementation(actual.getEventRadarScanStatus)
    const wrapper = mountPage(EventRadar); await flushPromises(); const state = wrapper.vm.$.setupState
    await state.handleScan(); await vi.advanceTimersByTimeAsync(3000)
    expect(state.scanTaskId).toBe(null)
    expect(state.scanning).toBe(false)
  } finally { http.defaults.adapter = previous; warning.mockRestore() }
})

it('hydrates completed market intelligence after a page refresh', async () => {
  api.listAsyncTasks.mockResolvedValueOnce({ data: { tasks: [] } }).mockResolvedValueOnce({ data: { tasks: [{ id: 101, status: 'done', result: { news: [{ title: '完成新闻' }], sectors: [] } }] } })
  const wrapper = mountPage(MarketIntelligence); await flushPromises()
  expect(wrapper.vm.$.setupState.data.news[0].title).toBe('完成新闻')
  expect(api.triggerMarketIntelligence).not.toHaveBeenCalled()
})
it('shows a restored market intelligence error instead of an empty analysis screen', async () => {
  api.listAsyncTasks.mockResolvedValueOnce({ data: { tasks: [] } }).mockResolvedValueOnce({ data: { tasks: [{ id: 101, status: 'error', error_msg: '模型暂时不可用' }] } })
  const wrapper = mountPage(MarketIntelligence); await flushPromises()
  expect(wrapper.text()).toContain('模型暂时不可用')
})

it('selects the exact conversation carried by a notification after chat mounts', async () => {
  const { useAppNavigation } = await import('../composables/useAppNavigation')
  api.listConversations.mockResolvedValue({ data: { conversations: [{ id: 77, title: '目标对话', message_count: 1 }, { id: 88, title: '其他对话', message_count: 1 }] } })
  api.getMessages.mockImplementation(id => Promise.resolve({ data: { messages: [{ role: 'assistant', content: `对话${id}`, metadata: { execution_status: 'completed' } }] } }))
  useAppNavigation().openConversation(77)
  const wrapper = mountPage(ChatView); await flushPromises()
  expect(wrapper.vm.$.setupState.selectedConv.id).toBe(77)
  expect(wrapper.vm.$.setupState.messages[0].content).toBe('对话77')
  expect(useAppNavigation().pendingConversationId.value).toBe(null)
})
it('keeps explicit continue available but never resends the original question when resume fails', async () => {
  const wrapper = mountPage(ChatView); await flushPromises(); const state = wrapper.vm.$.setupState
  state.selectedConv = { id: 6 }; state.messages = [{ role: 'user', content: '原问题' }]
  let receive
  api.resumeConversationStream.mockImplementation((_id, callback) => { receive = callback; return { abort: vi.fn() } })
  await state.continueAssistantMessage({ execution_status: 'cancelled' })
  expect(api.resumeConversationStream).toHaveBeenCalledTimes(1)
  receive({ type: 'error', data: { code: 'RESUME_FAILED', message: '任务已停止' } }); await flushPromises()
  expect(api.sendMessageStream).not.toHaveBeenCalled()
})
it('refreshes the first read after auto-confirm commits transactions using the actual nested result contract', async () => {
  const confirmation = deferred(); api.autoConfirmPortfolioTransactions.mockReturnValue(confirmation.promise)
  const wrapper = mountPage(Portfolio); await flushPromises()
  expect(api.getPortfolioSummary).toHaveBeenCalledTimes(1)
  confirmation.resolve({ data: { ok: true, result: { confirmed: 1 } } }); await flushPromises()
  expect(api.getPortfolioSummary).toHaveBeenCalledTimes(2)
  expect(api.autoConfirmPortfolioTransactions).toHaveBeenCalledTimes(1)
})
it('clears the previous account summary while a different account is still loading', async () => {
  const wrapper = mountPage(Portfolio); await flushPromises(); const state = wrapper.vm.$.setupState
  state.holdings = [{ id: 'old', account: 'A' }]; state.summary = { total_value: 999 }
  const pending = deferred(); api.getPortfolioSummary.mockReturnValue(pending.promise)
  state.accountFilter = 'B'; const request = state.loadData()
  expect(state.holdings).toEqual([])
  expect(state.summary.total_value).toBe(0)
  pending.resolve({ data: { holdings: [] } }); await request
})
it('clears previous conversation content immediately on a new selection', async () => {
  const wrapper = mountPage(ChatView); await flushPromises(); const state = wrapper.vm.$.setupState
  state.selectedConv = { id: 1 }; state.messages = [{ role: 'assistant', content: 'A' }]
  const pending = deferred(); api.getMessages.mockReturnValue(pending.promise)
  const request = state.selectConversation({ id: 2 })
  expect(state.messages).toEqual([])
  pending.resolve({ data: { messages: [] } }); await request
})
