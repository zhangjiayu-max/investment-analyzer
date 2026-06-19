// API URL 正确性测试
// 防止 /conversation/ vs /conversations/ 这类路径不匹配 bug

import { describe, it, expect, vi, beforeEach } from 'vitest'

// Mock axios
const mockGet = vi.fn(() => Promise.resolve({ data: {} }))
const mockPost = vi.fn(() => Promise.resolve({ data: {} }))
const mockPut = vi.fn(() => Promise.resolve({ data: {} }))
const mockDelete = vi.fn(() => Promise.resolve({ data: {} }))

vi.mock('axios', () => ({
  default: {
    create: () => ({
      get: mockGet,
      post: mockPost,
      put: mockPut,
      delete: mockDelete,
      defaults: { baseURL: '/api' },
      interceptors: {
        request: { use: vi.fn() },
        response: { use: vi.fn() },
      },
    }),
  },
}))

describe('API URL 路径正确性', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('getHotTopics 调用 /api/dashboard/hot-topics', async () => {
    const { getHotTopics } = await import('../api/index.js')
    await getHotTopics()
    expect(mockGet).toHaveBeenCalledWith('/dashboard/hot-topics')
  })

  it('getHotspotsRelate 调用 /api/dashboard/hotspots-relate (POST)', async () => {
    const { getHotspotsRelate } = await import('../api/index.js')
    await getHotspotsRelate()
    expect(mockPost).toHaveBeenCalledWith('/dashboard/hotspots-relate')
  })

  it('getBondMarketTemperature 调用 /api/bond/market-temperature', async () => {
    const { getBondMarketTemperature } = await import('../api/index.js')
    await getBondMarketTemperature()
    expect(mockGet).toHaveBeenCalledWith('/bond/market-temperature')
  })

  it('scanPortfolioAlerts 调用 /api/portfolio/alerts/scan (POST)', async () => {
    const { scanPortfolioAlerts } = await import('../api/index.js')
    await scanPortfolioAlerts()
    expect(mockPost).toHaveBeenCalledWith('/portfolio/alerts/scan')
  })

  it('listAlerts 调用 /api/portfolio/alerts', async () => {
    const { listAlerts } = await import('../api/index.js')
    await listAlerts(true, 10)
    expect(mockGet).toHaveBeenCalledWith('/portfolio/alerts', { params: { unread_only: true, limit: 10 } })
  })

  it('runPortfolioAiAnalysis 调用 /api/portfolio/analysis/ai', async () => {
    const { runPortfolioAiAnalysis } = await import('../api/index.js')
    await runPortfolioAiAnalysis('test question')
    expect(mockPost).toHaveBeenCalledWith('/portfolio/analysis/ai', { question: 'test question' }, { timeout: 300000 })
  })

  it('getDashboard 调用 /api/dashboard', async () => {
    const { getDashboard } = await import('../api/index.js')
    await getDashboard()
    expect(mockGet).toHaveBeenCalledWith('/dashboard')
  })
})

describe('stream URL 路径正确性', () => {
  it('sendMessageStream 使用 /conversations/ (复数) 路径', () => {
    // 读取源码验证 URL
    const fs = require('fs')
    const path = require('path')
    const apiPath = path.resolve(__dirname, '../api/index.js')
    const content = fs.readFileSync(apiPath, 'utf-8')

    // 确保 stream URL 使用复数
    expect(content).toContain('/conversations/${convId}/messages/stream')
    // 确保没有使用单数
    expect(content).not.toContain('/conversation/${convId}/messages/stream')
  })
})
