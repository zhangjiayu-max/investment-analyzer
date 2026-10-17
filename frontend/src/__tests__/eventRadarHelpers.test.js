import { describe, expect, it } from 'vitest'
import {
  FILTERS, STATUS_TABS,
  scoreColorClass, scoreRatingLabel, ratingColorClass, ratingLabel,
  effectiveSignal, signalMeta, confidenceClass, hasMultidimSignal,
  multidimBadgeClass, multidimLabel, opportunityLabel, opportunityValue,
  opportunityIsLow, analysisMethodLabel,
  isTrendEvent, timeFrameLabel, impactDirectionLabel, impactDurationLabel,
  impactPctText, hasCachedImpactAnalysis, formatAmount, groupBeneficiariesByTier,
  isAutoSynced, parseVerification, verificationLabel, verificationIcon,
  relevanceLabel, statusLabel, parseJsonArray, formatDate, directionLabel,
  formatShortDate, renderNotes, formatMetricValue, formatMetrics,
  masterActionColor, masterRatingColor, masterAccentColor, masterIcon,
  winRateColor, verifyResultMeta, sectorAccuracyColor,
} from '../components/market/eventRadar/helpers.js'

describe('EventRadar helpers — 常量', () => {
  it('FILTERS 和 STATUS_TABS 结构完整', () => {
    expect(FILTERS.map(f => f.key)).toEqual(['all', 'holding_impact', 'watchlist_impact', 'opportunity', 'market_watch'])
    expect(STATUS_TABS.map(s => s.key)).toEqual(['active', 'materialized', 'expired', 'all'])
  })
})

describe('EventRadar helpers — 评分颜色/文案', () => {
  it('scoreColorClass 分档正确', () => {
    expect(scoreColorClass(90)).toBe('score-excellent')
    expect(scoreColorClass(70)).toBe('score-good')
    expect(scoreColorClass(50)).toBe('score-normal')
    expect(scoreColorClass(30)).toBe('score-cautious')
    expect(scoreColorClass(null)).toBe('')
  })

  it('scoreRatingLabel 分档正确', () => {
    expect(scoreRatingLabel(85)).toBe('优秀')
    expect(scoreRatingLabel(65)).toBe('良好')
    expect(scoreRatingLabel(45)).toBe('一般')
    expect(scoreRatingLabel(20)).toBe('谨慎')
    expect(scoreRatingLabel(null)).toBe('—')
  })

  it('ratingLabel 映射及 fallback', () => {
    expect(ratingLabel('excellent')).toBe('优秀')
    expect(ratingLabel('poor')).toBe('较差')
    expect(ratingLabel('unknown')).toBe('unknown')
    expect(ratingLabel(undefined)).toBe('—')
  })

  it('sectorAccuracyColor 分档', () => {
    expect(sectorAccuracyColor(0.9)).toContain('22, 163, 74')
    expect(sectorAccuracyColor(0.3)).toContain('220, 38, 38')
  })
})

describe('EventRadar helpers — 信号/机会值', () => {
  it('effectiveSignal 退出信号优先', () => {
    expect(effectiveSignal({ exit_signal: 'stop_loss', signal_status: 'green' })).toBe('stop_loss')
    expect(effectiveSignal({ exit_signal: 'profit_target', signal_status: 'green' })).toBe('profit_target')
    expect(effectiveSignal({ signal_status: 'green' })).toBe('green')
    expect(effectiveSignal({})).toBe('gray')
  })

  it('signalMeta 返回正确元信息', () => {
    expect(signalMeta({ signal_status: 'green' }).cls).toBe('sig-hit')
    expect(signalMeta({ exit_signal: 'stop_loss' }).label).toBe('止损')
  })

  it('confidenceClass 分档', () => {
    expect(confidenceClass(80)).toBe('conf-high')
    expect(confidenceClass(60)).toBe('conf-mid')
    expect(confidenceClass(10)).toBe('conf-low')
    expect(confidenceClass(null)).toBe('')
  })

  it('hasMultidimSignal 检测非 neutral 信号', () => {
    expect(hasMultidimSignal({ tech_signal: 'bull' })).toBe(true)
    expect(hasMultidimSignal({ tech_signal: 'neutral', capital_signal: 'neutral', sentiment_signal: 'neutral' })).toBe(false)
  })

  it('multidimBadgeClass 维度映射', () => {
    expect(multidimBadgeClass('tech', 'bull')).toBe('md-bull')
    expect(multidimBadgeClass('sentiment', 'fear')).toBe('md-bull') // 逆向
    expect(multidimBadgeClass('sentiment', 'greed')).toBe('md-bear')
  })

  it('opportunityLabel 按可用字段回退', () => {
    expect(opportunityLabel({ current_percentile: 10 })).toBe('估值分位')
    expect(opportunityLabel({ drawdown_percentile: 90 })).toBe('回撤分位')
    expect(opportunityLabel({})).toBe('机会值')
  })

  it('opportunityIsLow 判断机会区', () => {
    expect(opportunityIsLow({ current_percentile: 15 })).toBe(true)
    expect(opportunityIsLow({ drawdown_percentile: 90 })).toBe(true)
    expect(opportunityIsLow({ current_percentile: 50 })).toBe(false)
  })
})

describe('EventRadar helpers — 事件/趋势/影响', () => {
  it('isTrendEvent 检测 time_frame', () => {
    expect(isTrendEvent({ time_frame: 'short' })).toBe(true)
    expect(isTrendEvent({})).toBe(false)
    expect(isTrendEvent(null)).toBe(false)
  })

  it('timeFrameLabel 映射', () => {
    expect(timeFrameLabel('short')).toBe('短期')
    expect(timeFrameLabel('unknown')).toBe('unknown')
  })

  it('impactDirectionLabel 映射', () => {
    expect(impactDirectionLabel('up').text).toBe('↑ 利好')
    expect(impactDirectionLabel('down').cls).toBe('impact-down')
  })

  it('impactPctText 格式化', () => {
    expect(impactPctText(1.5)).toBe('+1.50%')
    expect(impactPctText(-2)).toBe('-2.00%')
    expect(impactPctText(null)).toBe('')
    expect(impactPctText('abc')).toBe('')
  })

  it('formatAmount 千分位', () => {
    expect(formatAmount(1234567)).toBe('1,234,567')
    expect(formatAmount(null)).toBe('-')
  })

  it('groupBeneficiariesByTier 按固定顺序分组', () => {
    const items = [
      { recommendation_tier: 'observe_only' },
      { recommendation_tier: 'strong_buy' },
      { recommendation_tier: 'watch' },
    ]
    const groups = groupBeneficiariesByTier(items)
    expect(groups.map(g => g.tier)).toEqual(['strong_buy', 'watch', 'observe_only'])
    expect(groupBeneficiariesByTier([])).toEqual([])
  })
})

describe('EventRadar helpers — 日期/验证/状态', () => {
  it('parseVerification 容错', () => {
    expect(parseVerification('{"a":1}')).toEqual({ a: 1 })
    expect(parseVerification('invalid')).toBe(null)
  })

  it('verificationLabel / verificationIcon', () => {
    expect(verificationLabel('correct')).toBe('验证正确')
    expect(verificationIcon('wrong')).toBe('alert-triangle')
  })

  it('relevanceLabel / statusLabel', () => {
    expect(relevanceLabel('holding_impact')).toBe('持仓影响')
    expect(statusLabel('materialized')).toBe('已落地')
  })

  it('parseJsonArray 容错', () => {
    expect(parseJsonArray('["a"]')).toEqual(['a'])
    expect(parseJsonArray('invalid')).toEqual([])
  })

  it('directionLabel 映射', () => {
    expect(directionLabel('positive').text).toBe('利好')
    expect(directionLabel('negative').class).toBe('dir-negative')
    expect(directionLabel('other').class).toBe('dir-neutral')
  })

  it('formatShortDate 显示 MM-DD', () => {
    expect(formatShortDate('2026-03-05')).toBe('03-05')
    expect(formatShortDate('')).toBe('')
  })

  it('renderNotes 转义并渲染事件链接', () => {
    const out = renderNotes('来自事件「美联储加息」<script>')
    expect(out).toContain('wl-note-event-link')
    expect(out).toContain('美联储加息')
    expect(out).not.toContain('<script>') // 已转义
  })
})

describe('EventRadar helpers — 大师/指标', () => {
  it('formatMetricValue 布尔/数字', () => {
    expect(formatMetricValue(true)).toBe('是')
    expect(formatMetricValue(false)).toBe('否')
    expect(formatMetricValue(3)).toBe('3')
    expect(formatMetricValue(3.5)).toBe('3.5')
  })

  it('formatMetrics 映射中文 key', () => {
    const out = formatMetrics({ has_moat: true, roe_consistent: false })
    expect(out).toEqual([
      { label: '护城河', value: '是' },
      { label: 'ROE持续性', value: '否' },
    ])
  })

  it('masterIcon fallback', () => {
    expect(masterIcon('buffett')).toBe('🏰')
    expect(masterIcon('unknown')).toBe('🎯')
  })

  it('winRateColor 分档', () => {
    expect(winRateColor(70)).toBe('#4caf50')
    expect(winRateColor(50)).toBe('#ff9800')
    expect(winRateColor(30)).toBe('#f44336')
    expect(winRateColor(null)).toBe('#9e9e9e')
  })

  it('verifyResultMeta 待验证兜底', () => {
    expect(verifyResultMeta('correct').label).toBe('正确')
    expect(verifyResultMeta('pending').label).toBe('待验证')
  })
})
