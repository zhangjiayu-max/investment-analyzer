import { describe, expect, it } from 'vitest'
import { isFreshMarketIntelligence } from '../components/market/marketIntelligenceHelpers'

describe('market intelligence freshness', () => {
  it('only restores a result fetched on the current business date', () => {
    expect(isFreshMarketIntelligence({ fetched_at: '2026-10-18 10:00' }, '2026-10-18')).toBe(true)
    expect(isFreshMarketIntelligence({ fetched_at: '2026-10-17 23:59' }, '2026-10-18')).toBe(false)
    expect(isFreshMarketIntelligence({ summary: 'missing timestamp' }, '2026-10-18')).toBe(false)
  })
})
