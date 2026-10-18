import { expect, it, vi } from 'vitest'
import { listAnalysisAgentVersions, rollbackAnalysisAgentPrompt, getAgentRegressionResult } from '../api'
import http from '../api/http'

vi.mock('../api/http', () => ({ default: {
  get: vi.fn(), post: vi.fn(),
  interceptors: { response: { use: vi.fn() }, request: { use: vi.fn() } },
} }))

it('uses the registered analysis version and agent regression routes', () => {
  listAnalysisAgentVersions(8)
  expect(http.get).toHaveBeenLastCalledWith('/analysis-agents/8/versions')
  rollbackAnalysisAgentPrompt(8, 11)
  expect(http.post).toHaveBeenLastCalledWith('/analysis-agents/8/rollback/11')
  getAgentRegressionResult(8, 'analysis')
  expect(http.get).toHaveBeenLastCalledWith('/agents/8/regression', { params: { agent_type: 'analysis' } })
})
