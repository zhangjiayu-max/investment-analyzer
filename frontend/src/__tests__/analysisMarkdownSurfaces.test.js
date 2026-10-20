import { shallowMount, flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

vi.hoisted(() => vi.stubGlobal('localStorage', { getItem: vi.fn(), setItem: vi.fn(), removeItem: vi.fn() }))
vi.mock('../api', async original => {
  const actual = await original()
  return Object.fromEntries(Object.entries(actual).map(([key, value]) => [key, typeof value === 'function' ? vi.fn(async () => ({ data: {} })) : value]))
})
import * as api from '../api'
import MarkdownActions from '../components/ui/MarkdownActions.vue'
import BriefingCard from '../components/dashboard/BriefingCard.vue'
import HotspotsCard from '../components/dashboard/HotspotsCard.vue'
import CashManagementCard from '../components/dashboard/CashManagementCard.vue'
import MarketIntelligence from '../components/market/MarketIntelligence.vue'
import ValuationHistory from '../components/valuation/ValuationHistory.vue'
import AnalysisLogPage from '../components/analysis/AnalysisLogPage.vue'
import AnalysisComparison from '../components/analysis/AnalysisComparison.vue'
import ChatMessage from '../components/chat/ChatMessage.vue'
import Portfolio from '../components/portfolio/PortfolioManagement.vue'
import AnalysisCard from '../components/ui/AnalysisCard.vue'
import PortfolioHealthCard from '../components/dashboard/PortfolioHealthCard.vue'
import RagAnalysis from '../components/agent/RagAnalysis.vue'
import CapabilityCenter from '../components/agent/CapabilityCenter.vue'
import BadCasePage from '../components/quality/BadCasePage.vue'

const wrappers = []
function page(component, props = {}, realCards = false) {
  const w = shallowMount(component, { props, global: { stubs: { MarkdownActions: false, teleport: true, ...(realCards ? { AnalysisCard: false } : {}) } } })
  wrappers.push(w)
  return w
}
beforeEach(() => {
  vi.clearAllMocks()
  api.listAsyncTasks.mockResolvedValue({ data: { tasks: [] } })
  api.listAnalysisAgents.mockResolvedValue({ data: { agents: [] } })
  api.listValuationIndexes.mockResolvedValue({ data: { indexes: [] } })
  api.getPortfolioSummary.mockResolvedValue({ data: { holdings: [] } })
  Object.defineProperty(window, 'isSecureContext', { value: true, configurable: true })
  Object.defineProperty(navigator, 'clipboard', { value: { writeText: vi.fn().mockResolvedValue() }, configurable: true })
})
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); vi.restoreAllMocks() })

it.each([
  [BriefingCard, { dailyReport: { id: 9, result: '## 市场日报\n完整报告', created_at: '2026-10-20' }, feedback: {} }, '完整报告'],
  [HotspotsCard, { hotspotsAnalysis: { summary: '热点摘要', recommendations: [{ index_code: '000300', reason: '完整推荐理由' }], analysis_text: '更长的完整分析' } }, '更长的完整分析'],
  [CashManagementCard, { bondResult: { summary: '债券摘要', policy_analysis: '完整政策分析', recommendations: [{ fund_code: '000001', amount: 1000 }] } }, '完整政策分析'],
])('copies complete Agent output from dashboard surface %s', async (component, props, expected) => {
  const w = page(component, props)
  expect(w.find('.markdown-actions__export').exists()).toBe(true)
  await w.get('.markdown-actions__copy').trigger('click')
  expect(navigator.clipboard.writeText.mock.calls[0][0]).toContain(expected)
})

it('exports market intelligence including every sector, outlook and risk', async () => {
  const w = page(MarketIntelligence); await flushPromises()
  w.vm.$.setupState.data = { summary: '综合研判', forecast_1w: '完整一周预测', risk_warning: '风险正文', sectors: [{ name: '板块一', reason: '完整板块理由' }, { name: '板块二' }] }
  await flushPromises()
  await w.get('.markdown-actions__copy').trigger('click')
  const text = navigator.clipboard.writeText.mock.calls[0][0]
  for (const part of ['综合研判', '完整一周预测', '风险正文', '板块一', '板块二']) expect(text).toContain(part)
})

it('makes current and historical index reports exportable while blocking running placeholders', async () => {
  const w = page(ValuationHistory); await flushPromises(); const state = w.vm.$.setupState
  state.activeTab = 'analysis'
  const key = state.currentAnalysisKey
  state.analysisResultMap = { [key]: { id: 3, result: '## 当前指数报告', status: 'done' } }
  state.viewingHistory = { id: 2, result: '## 旧指数报告', status: 'done' }
  await flushPromises()
  expect(w.findAllComponents(MarkdownActions).map(c => c.props('content'))).toEqual(['## 当前指数报告', '## 旧指数报告'])
  state.analysisResultMap = { [key]: { result: '分析已提交，正在后台生成结果...', status: 'running' } }
  await flushPromises()
  expect(w.findAllComponents(MarkdownActions)).toHaveLength(1)
})

it('copies full valuation analysis directly from each history row without opening a detail dialog', async () => {
  const w = page(ValuationHistory); await flushPromises(); const state = w.vm.$.setupState
  state.activeTab = 'analysis'; await flushPromises()
  const fullReport = '## 历史报告\n\n' + '完整分析正文'.repeat(100) + '\n报告末尾'
  state.analysisHistory = [
    { id: 71, result: fullReport, agent_name: '指数深度分析师', index_name: '沪深300', status: 'done' },
    { id: 72, result: '## 无状态的旧报告', index_name: '中证500' },
  ]
  await flushPromises()
  const rows = w.findAll('.history-item')
  expect(rows).toHaveLength(2)
  for (const row of rows) {
    expect(row.find('.markdown-actions__copy').exists()).toBe(true)
    expect(row.find('.markdown-actions__export').exists()).toBe(true)
  }
  await rows[0].get('.markdown-actions__copy').trigger('click')
  expect(navigator.clipboard.writeText).toHaveBeenCalledWith(fullReport)
  expect(api.getAnalysisHistoryDetail).not.toHaveBeenCalled()
  expect(state.viewingHistory).toBeNull()
})

it('does not offer valuation history row exports for running, failed or empty results', async () => {
  const w = page(ValuationHistory); await flushPromises(); const state = w.vm.$.setupState
  state.activeTab = 'analysis'; await flushPromises()
  state.analysisHistory = [
    { id: 81, result: '分析中...', status: 'running' },
    { id: 82, result: '模型失败', status: 'error' },
    { id: 83, result: '', status: 'done' },
  ]
  await flushPromises()
  expect(w.find('.history-list .markdown-actions__export').exists()).toBe(false)
})

it('exports enhanced strategy as both a full report and individual index analysis', async () => {
  const w = page(ValuationHistory); await flushPromises(); const state = w.vm.$.setupState
  state.outerTab = 'super-value'; await flushPromises()
  const strategy = { index_name: '沪深300', index_code: '000300', action_detail: '完整操作依据' }
  state.superValueLoading = false
  state.superValueData = { opportunities: [], total_scanned: 0 }
  state.strategyData = { overall_summary: '完整增强策略摘要', strategies: [strategy] }
  await flushPromises()
  expect(w.findAllComponents(MarkdownActions).map(c => c.props('content'))).toEqual([state.strategyData, strategy])
})

it('copies original analysis record Markdown safely and supports conversation run history', async () => {
  const raw = '## 原始分析\n\n| 基金 | 建议 |\n| --- | --- |\n| 000001 | 持有 |\n<script>alert(1)</script>'
  api.getAnalysisLogDetail.mockResolvedValue({ data: { log: { id: 3, status: 'done', agent_name: '全景诊断分析师' }, source_result: raw } })
  const w = page(AnalysisLogPage); await flushPromises()
  await w.vm.$.setupState.viewDetail(3); await flushPromises()
  expect(w.find('.detail-result table').exists()).toBe(true)
  expect(w.find('.detail-result script').exists()).toBe(false)
  await w.get('.markdown-actions__copy').trigger('click')
  expect(navigator.clipboard.writeText).toHaveBeenCalledWith(raw)
  w.vm.$.setupState.detailVisible = false
  w.vm.$.setupState.runDetailVisible = true
  w.vm.$.setupState.runDetailData = { id: 5, status: 'success', result: '专家完整历史产出' }
  await flushPromises()
  expect(w.getComponent(MarkdownActions).props('content')).toBe('专家完整历史产出')
})

it('provides independent export actions for both comparison reports', async () => {
  const w = page(AnalysisComparison); await flushPromises(); const state = w.vm.$.setupState
  state.recordA = 1; state.recordB = 2
  await flushPromises()
  state.detailA = { id: 1, result_data: '## 报告 A' }
  state.detailB = { id: 2, result_data: { text: '## 报告 B' } }
  await flushPromises()
  expect(w.findAllComponents(MarkdownActions).map(c => c.props('content'))).toEqual(['## 报告 A', { text: '## 报告 B' }])
})

it('keeps chat output exportable after feedback and includes specialist results', async () => {
  const w = page(ChatMessage, { index: 0, feedbackGiven: { 0: 'helpful' }, msg: { id: 4, role: 'assistant', content: '## 完整回答', execution_status: 'completed', specialist_results: [{ agent: '指数分析', analysis: '## 完整专家分析', expanded: true }] } })
  const outputs = w.findAllComponents(MarkdownActions).map(c => c.props('content'))
  expect(outputs).toContain('## 完整回答')
  expect(outputs).toContain('## 完整专家分析')
  await w.setProps({ msg: { id: 4, role: 'assistant', content: '生成中', execution_status: 'streaming' } })
  expect(w.findComponent(MarkdownActions).exists()).toBe(false)
})

it.each(['panorama', 'deepdive', 'trade-review', 'fund-analysis', 'correlation'])('makes %s portfolio reports exportable', async mode => {
  const w = page(Portfolio, {}, true); await flushPromises(); const state = w.vm.$.setupState
  state.activeAnalysisTab = 'ai'; state.aiMode = mode; state.modeResult = '## 持仓分析完整正文'; state.modeRecordId = 7
  await flushPromises()
  const report = w.findComponent(AnalysisCard)
  expect(report.exists()).toBe(true)
  expect(report.getComponent(MarkdownActions).props('content')).toBe('## 持仓分析完整正文')
  expect(report.getComponent(MarkdownActions).props('recordId')).toBe(7)
})

it('makes structured scenario output exportable', async () => {
  const w = page(Portfolio); await flushPromises(); const state = w.vm.$.setupState
  state.activeAnalysisTab = 'ai'; state.aiMode = 'what-if'
  state.modeResult = { result: { analysis: '完整情景推演', scenarios: [{ name: '下跌20%', loss: -20000 }] } }
  await flushPromises()
  const actions = w.getComponent(MarkdownActions)
  await actions.get('.markdown-actions__copy').trigger('click')
  expect(navigator.clipboard.writeText.mock.calls[0][0]).toContain('完整情景推演')
  expect(navigator.clipboard.writeText.mock.calls[0][0]).toContain('-20000')
})

it('does not export unfinished or failed portfolio results', async () => {
  const w = page(Portfolio, {}, true); await flushPromises(); const state = w.vm.$.setupState
  state.activeAnalysisTab = 'ai'; state.aiMode = 'panorama'; state.modeResult = '分析中...'; state.modeLoading = true
  await flushPromises()
  expect(w.find('.markdown-actions__export').exists()).toBe(false)
  state.modeLoading = false; state.modeResult = '分析失败：模型超时'
  await flushPromises()
  expect(w.find('.markdown-actions__export').exists()).toBe(false)
})

it('exports structured rebalancing suggestions from portfolio health', async () => {
  const content = { summary: '再平衡摘要', suggestions: [{ fund_name: '完整基金名', reason: '完整调整理由', amount_range: '1000-2000' }] }
  const w = page(PortfolioHealthCard, { portfolioHealth: {}, showRebalance: true, rebalanceResult: content })
  expect(w.getComponent(MarkdownActions).props('content')).toEqual(content)
})

it('exports analysis text shown in RAG retrieval detail', async () => {
  const w = page(RagAnalysis); await flushPromises()
  w.vm.$.setupState.selectedResult = { label: '分析记录', title: '检索分析记录', reference_id: 7, body: '## 检索到的原始片段' }
  await flushPromises()
  expect(w.getComponent(MarkdownActions).props('content')).toBe('## 检索到的原始片段')
})

it.each([true, false])('uses complete debug output instead of its truncated preview (structured=%s)', async structured => {
  const w = page(CapabilityCenter); await flushPromises(); const state = w.vm.$.setupState
  state.activeTab = 'debug'
  const text = '## 完整调试报告\n' + '长正文'.repeat(2000) + '\n报告末尾'
  const full = structured ? { analysis: text } : text
  state.debugResult = { success: true, result: text.slice(0, 5000) + '...(结果已截断)', raw: { parsed_result: structured ? full : null, full_result: structured ? JSON.stringify(full) : full } }
  await flushPromises()
  await w.get('.markdown-actions__copy').trigger('click')
  expect(navigator.clipboard.writeText.mock.calls[0][0]).toContain('报告末尾')
  expect(navigator.clipboard.writeText.mock.calls[0][0]).not.toContain('结果已截断')
})

it('copies the full analysis in bad case details rather than the 2000 character preview', async () => {
  const w = page(BadCasePage); await flushPromises()
  const text = '## 待改进的原始分析\n' + '完整正文'.repeat(1000) + '\n完整报告末尾'
  w.vm.$.setupState.selectedCase = { id: 8, source: 'analysis', output: text }
  await flushPromises()
  await w.get('.markdown-actions__copy').trigger('click')
  expect(navigator.clipboard.writeText).toHaveBeenCalledWith(text)
})
