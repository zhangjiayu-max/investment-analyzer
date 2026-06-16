/**
 * Shared helper functions for Dashboard and its sub-components.
 */

export const assessmentColors = {
  extreme: { bg: '#dc2626', label: '极度低估' },
  undervalued: { bg: '#f59e0b', label: '低估' },
  slightly_low: { bg: '#10b981', label: '偏低' },
  fair: { bg: '#6b7280', label: '合理' },
  slightly_high: { bg: '#f59e0b', label: '偏高' },
  overvalued: { bg: '#ef4444', label: '高估' },
  extreme_high: { bg: '#dc2626', label: '极度高估' },
}

export function getPercentileColor(p) {
  if (p <= 10) return '#dc2626'
  if (p <= 25) return '#f59e0b'
  if (p <= 40) return '#10b981'
  if (p <= 60) return '#6b7280'
  if (p <= 80) return '#f59e0b'
  return '#ef4444'
}

export function formatMoney(v) {
  if (v == null) return '—'
  if (Math.abs(v) >= 1e8) return (v / 1e8).toFixed(2) + '亿'
  if (Math.abs(v) >= 1e4) return (v / 1e4).toFixed(2) + '万'
  return v.toFixed(2)
}

export function formatPct(v) {
  if (v == null) return '—'
  return (v * 100).toFixed(1) + '%'
}

export const concentrationColor = { low: '#10b981', moderate: '#f59e0b', high: '#ef4444' }
export const concentrationIcon = { low: '✅', moderate: '⚡', high: '⚠️' }

export function _cmpTemp(prev, current) {
  if (prev == null || current == null) return '--'
  const diff = current - prev
  const arrow = diff > 0 ? '↑' : diff < 0 ? '↓' : '→'
  return `${prev}° → ${current}° ${arrow}${Math.abs(diff).toFixed(0)}°`
}

export function formatBriefingTime(ts) {
  if (!ts) return ''
  const d = new Date(ts.replace(' ', 'T'))
  if (isNaN(d)) return ts
  return `${d.getMonth() + 1}月${d.getDate()}日 ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

export function renderBriefing(text) {
  if (!text) return ''
  const html = text
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/^\s*[-*]\s+(.*)/gm, '<li>$1</li>')
    .replace(/(<li>.*<\/li>\n?)+/gs, m => `<ul>${m}</ul>`)
    .replace(/\n/g, '<br>')
  // DOMPurify must be called at the component level since it's a dependency
  return html
}

export function getBondTempLabel(temp) {
  if (temp == null) return ''
  if (temp <= 30) return '低温（适合买入）'
  if (temp <= 50) return '偏低'
  if (temp <= 70) return '偏高'
  return '高温（谨慎买入）'
}
