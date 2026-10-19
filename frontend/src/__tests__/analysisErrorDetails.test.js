import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { shallowMount, flushPromises } from '@vue/test-utils'
import AnalysisLogPage from '../components/analysis/AnalysisLogPage.vue'
import { getAnalysisLogDetail } from '../api'

vi.mock('../api', () => ({
  listAnalysisLogs: vi.fn(async () => ({ data: { logs: [], total: 0, stats: {} } })),
  getAnalysisLogDetail: vi.fn(), evaluateAnalysisLog: vi.fn(),
  listAgentRuns: vi.fn(), getAgentRunDetail: vi.fn(),
  listAnalysisAgents: vi.fn(async () => ({ data: { agents: [] } })),
}))
vi.mock('../composables/useAsyncTask', () => ({ useAsyncTask: () => ({
  taskState: { value: 'idle' }, start: vi.fn(), restoreFromServer: async () => false,
}) }))
let wrapper
beforeEach(() => { vi.clearAllMocks() })
afterEach(() => { wrapper?.unmount() })
function render() { wrapper = shallowMount(AnalysisLogPage, { global: { stubs: { teleport: true } } }); return wrapper }

it('shows failure cause, request context and retry evidence in analysis details', async () => {
  getAnalysisLogDetail.mockResolvedValue({ data: {
    log: { id: 128, status: 'error', trace_id: 'log-test', error_msg: 'Connection error.',
      error_details: { summary: '连接被拒绝', category: 'connection_refused', stage: 'llm_request',
        model: 'mimo-v2.6-pro', endpoint: 'https://model.test/v1', attempt_count: 2,
        causes: [{ type: 'ConnectError', message: '[Errno 61] Connection refused' }],
        attempts: [{ attempt: 1, elapsed_ms: 80000, causes: [{ type: 'ReadError', message: 'connection reset' }] }] } },
    source_result: '',
  } })
  const w = render(); await flushPromises()
  await w.vm.$.setupState.viewDetail(128); await flushPromises()
  expect(w.text()).toContain('连接被拒绝')
  expect(w.text()).toContain('[Errno 61] Connection refused')
  expect(w.text()).toContain('mimo-v2.6-pro')
  expect(w.text()).toContain('https://model.test/v1')
  expect(w.text()).toContain('connection reset')
  expect(w.text()).toContain('未生成分析结果')
})

it('explains absent diagnostics for historical failures', async () => {
  getAnalysisLogDetail.mockResolvedValue({ data: { log: { id: 128, status: 'error', error_msg: 'Connection error.', error_details: {} },
    diagnostic_note: '此历史记录未保存底层异常', source_result: '' } })
  const w = render(); await flushPromises()
  await w.vm.$.setupState.viewDetail(128); await flushPromises()
  expect(w.text()).toContain('此历史记录未保存底层异常')
})

it('renders detail loading failures instead of leaving a blank drawer', async () => {
  getAnalysisLogDetail.mockRejectedValue(new Error('记录读取失败'))
  const w = render(); await flushPromises()
  await w.vm.$.setupState.viewDetail(128); await flushPromises()
  expect(w.text()).toContain('记录读取失败')
})
