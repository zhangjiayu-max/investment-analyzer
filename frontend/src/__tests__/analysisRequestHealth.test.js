import { afterEach, describe, expect, it, vi } from 'vitest'

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))
vi.mock('../api/http.js', () => ({ default: { get, post } }))
vi.mock('../api/interceptors.js', () => ({}))

afterEach(() => {
  vi.useRealTimers()
  vi.clearAllMocks()
})

describe('analysis request ownership and deadlines', () => {
  it('lets the server resolve the default index Agent', async () => {
    const { runAnalysis } = await import('../api/index.js')
    await runAnalysis('000300', '沪深300')
    expect(post).toHaveBeenCalledWith('/analysis/run', { index_code: '000300', index_name: '沪深300' })
    await runAnalysis('000905', '中证500', 42)
    expect(post).toHaveBeenLastCalledWith('/analysis/run', { index_code: '000905', index_name: '中证500', agent_id: 42 })
  })

  it('sends scenario keys, numeric parameters and custom text', async () => {
    const { buildWhatIfRequest } = await import('../utils/whatIfScenarios.js')
    const { runWhatIf } = await import('../api/index.js')
    const request = buildWhatIfRequest('rate_hike')
    expect(request).toEqual({ scenario: 'rate_hike', parameter: 200 })
    await runWhatIf(request)
    expect(post).toHaveBeenCalledWith('/portfolio/analysis/what-if', request)
    expect(buildWhatIfRequest('market_drop')).toEqual({ scenario: 'market_drop', parameter: 20 })
    expect(buildWhatIfRequest('sector_rotation').scenario).toBe('custom')
    expect(buildWhatIfRequest('custom', '行业需求下降')).toEqual({ scenario: 'custom', custom_scenario: '行业需求下降' })
    expect(() => buildWhatIfRequest('custom', '  ')).toThrow()
  })

  it('stops polling stale records after its deadline', async () => {
    vi.useFakeTimers()
    get.mockResolvedValue({ data: { status: 'running' } })
    const { pollAnalysisStatus } = await import('../api/index.js')
    const progress = vi.fn()
    pollAnalysisStatus(123, progress, 100, 300)
    await vi.advanceTimersByTimeAsync(500)
    expect(progress).toHaveBeenLastCalledWith(expect.objectContaining({ status: 'error', error: expect.stringContaining('超时') }))
    const calls = get.mock.calls.length
    await vi.advanceTimersByTimeAsync(1000)
    expect(get).toHaveBeenCalledTimes(calls)
  })

  it('rejects missing record IDs without requesting an undefined URL', async () => {
    const { pollAnalysisStatus } = await import('../api/index.js')
    const progress = vi.fn()
    pollAnalysisStatus(undefined, progress)
    expect(get).not.toHaveBeenCalled()
    expect(progress).toHaveBeenCalledWith(expect.objectContaining({ status: 'error' }))
  })
})
