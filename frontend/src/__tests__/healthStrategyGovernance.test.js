import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
vi.mock('../api', () => ({
  calculateHealthScore: vi.fn(), getTodayHealthScore: vi.fn(), getHealthHistory: vi.fn(),
  getStockBondRatio: vi.fn(), getFearGreedIndex: vi.fn(), getAsyncTaskStatus: vi.fn(), listAsyncTasks: vi.fn(),
  listStrategies: vi.fn(), runStrategyBacktest: vi.fn(), listStrategyResults: vi.fn(),
}))
vi.mock('../composables/useTracking', () => ({ trackFeatureClick: vi.fn(), trackFeatureComplete: vi.fn() }))
import { trackFeatureClick, trackFeatureComplete } from '../composables/useTracking'
import { useTaskStore } from '../composables/useTaskStore'
import * as api from '../api'
import HealthScore from '../components/analysis/HealthScore.vue'
import StrategyBacktest from '../components/decision/StrategyBacktest.vue'
const wrappers = []
const render = c => { const w = mount(c, { global: { stubs: { teleport: true } } }); wrappers.push(w); return w }
beforeEach(() => {
  useTaskStore().clearTask('health_score_calc')
  vi.clearAllMocks(); vi.useFakeTimers(); vi.stubGlobal('localStorage', { getItem: () => null, setItem: vi.fn(), removeItem: vi.fn() })
  api.getTodayHealthScore.mockResolvedValue({ data: { status: 'empty', result: null } })
  api.getHealthHistory.mockResolvedValue({ data: { scores: [] } })
  api.getStockBondRatio.mockResolvedValue({ data: { result: { error: '缺数据' } } })
  api.getFearGreedIndex.mockResolvedValue({ data: { result: { score: null, error: '缺数据' } } })
  api.listAsyncTasks.mockResolvedValue({ data: { tasks: [] } })
  api.listStrategies.mockResolvedValue({ data: { strategies: [] } })
  api.listStrategyResults.mockResolvedValue({ data: { items: [] } })
})
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); vi.useRealTimers() })

describe('health task and data quality', () => {
  it('keeps empty GET results empty and waits for calculation task before showing score', async () => {
    api.calculateHealthScore.mockResolvedValue({ data: { task_id: 918, status: 'running' } })
    api.getAsyncTaskStatus.mockResolvedValue({ data: { status: 'done', result: { total_score: 812, scores: { quality: 180 }, advice: [] } } })
    const w = render(HealthScore); await flushPromises()
    expect(w.find('.score-hero').exists()).toBe(false)
    await w.get('.page-header button').trigger('click'); await flushPromises()
    const dialog = w.findComponent({ name: 'ConfirmDialog' }); if (dialog.exists()) { dialog.vm.$emit('confirm'); await flushPromises() }
    expect(w.find('.score-number').exists()).toBe(false)
    expect(trackFeatureClick).toHaveBeenCalledWith('health_score_calculate', 'health')
    expect(trackFeatureComplete).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(3100); await flushPromises()
    expect(w.get('.score-number').text()).toBe('812')
    expect(trackFeatureComplete).toHaveBeenCalledWith('health_score_calculate', 'health')
  })
  it('explains insufficient data instead of grading it dangerous', async () => {
    api.getTodayHealthScore.mockResolvedValue({ data: { status: 'ok', result: { total_score: null, scores: {}, data_quality: { status: 'insufficient', reasons: ['缺少持仓'] }, advice: [] } } })
    const w = render(HealthScore); await flushPromises()
    expect(w.text()).toContain('缺少持仓')
    expect(w.text()).not.toContain('危险')
  })
})

describe('backtest comparison and save status', () => {
  it('plots unit NAV on a date axis and refreshes history after a saved result', async () => {
    api.runStrategyBacktest.mockResolvedValue({ data: { status: 'ok', save_status: 'saved', target_code: 'x', nav_curve: [{ date: '2025-01-01', value: 10000 }, { date: '2025-01-03', value: 12000 }], unit_nav_curve: [{ date: '2025-01-01', value: 1 }, { date: '2025-01-03', value: 1.2 }], benchmark_nav_curve: [{ date: '2025-01-01', value: 1 }, { date: '2025-01-02', value: 1.1 }, { date: '2025-01-03', value: 1.2 }] } })
    const w = render(StrategyBacktest); await flushPromises()
    await w.get('.param-input').setValue('x'); await w.get('.run-btn').trigger('click'); await flushPromises()
    const main = w.get('path[stroke="#c9a84c"]').attributes('d')
    const bench = w.get('path[stroke="#94a3b8"]').attributes('d')
    expect(main.split(' ')[0]).toBe(bench.split(' ')[0])
    expect(main.split(' ').at(-1)).toBe(bench.split(' ').at(-1))
    expect(api.listStrategyResults).toHaveBeenCalledTimes(2)
  })
  it('shows save failure while retaining calculated results', async () => {
    api.runStrategyBacktest.mockResolvedValue({ data: { status: 'ok', save_status: 'error', save_error: '保存失败', total_return: 0 } })
    const w = render(StrategyBacktest); await flushPromises()
    await w.get('.param-input').setValue('x'); await w.get('.run-btn').trigger('click'); await flushPromises()
    expect(w.text()).toContain('保存失败')
  })
})
