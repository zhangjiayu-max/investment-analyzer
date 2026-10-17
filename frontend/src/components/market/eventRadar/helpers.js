/**
 * EventRadarPage 纯函数 + 常量抽离
 *
 * 从 8638 行的 EventRadarPage.vue 抽出的无状态工具：label 映射常量、
 * 颜色/文案格式化函数。这些函数只依赖入参或本文件内的常量，
 * 不依赖任何 ref/computed/toast/API，可独立单测。
 *
 * 抽离目的（2026-10-15 功能精简）：降低主文件体积、锁定纯逻辑行为。
 */

// ── 常量：筛选/状态选项 ──

export const FILTERS = [
  { key: 'all', label: '全部', icon: 'satellite' },
  { key: 'holding_impact', label: '持仓影响', icon: 'alert-triangle' },
  { key: 'watchlist_impact', label: '关注机会', icon: 'bookmark' },
  { key: 'opportunity', label: '建仓机会', icon: 'trending-up' },
  { key: 'market_watch', label: '市场关注', icon: 'info' },
]

export const STATUS_TABS = [
  { key: 'active', label: '进行中' },
  { key: 'materialized', label: '已落地' },
  { key: 'expired', label: '已过期' },
  { key: 'all', label: '全部' },
]

// ── 常量：label 映射表 ──

/** 买入评分维度中文名 */
export const BUY_DIMENSION_LABELS = {
  valuation: '估值维度',
  price: '净值距目标',
  correlation: '相关性',
  concentration: '集中度',
}

/** 七维体检报告维度中文名 */
export const DIMENSION_LABELS = {
  quality: '基金质量',
  drawdown: '回撤恢复',
  trend: '趋势均线',
  capital: '资金流向',
  sentiment: '情绪温度',
  valuation: '估值水位',
  fundamental: '基本面',
}

/** 季度调仓动作标签映射 */
export const HOLDING_ACTION_LABELS = {
  new: { label: '新进', class: 'hc-action-new' },
  increase: { label: '增持', class: 'hc-action-increase' },
  decrease: { label: '减持', class: 'hc-action-decrease' },
  exit: { label: '退出', class: 'hc-action-exit' },
}

/** 操作建议映射 */
export const ACTION_LABELS = {
  strong_buy: { label: '强烈加仓', class: 'action-strong-buy' },
  dca: { label: '定投加仓', class: 'action-dca' },
  hold: { label: '持有', class: 'action-hold' },
  reduce: { label: '减仓', class: 'action-reduce' },
  wait: { label: '等待', class: 'action-wait' },
}

/** 体检报告子项中文名（用于详情弹窗展示各维度细分指标） */
export const SUBDIM_LABELS = {
  // 基金质量
  manager_stability: '经理稳定性',
  tracking_error: '跟踪误差',
  scale_trend: '规模趋势',
  fee_competitiveness: '费率竞争',
  peer_ranking: '同类排名',
  // 回撤恢复
  current_drawdown: '当前回撤',
  drawdown_percentile: '回撤分位',
  recovery_ability: '恢复能力',
  drawdown_speed: '回撤速度',
  bottoming_signal: '底部信号',
  // 趋势均线
  ma_arrangement: '均线排列',
  trend_strength: '趋势强度',
  ma_deviation: '均线偏离',
  turning_signal: '转折信号',
  relative_strength: '相对强弱',
  // 资金流向
  margin_trend: '融资趋势',
  margin_percentile: '融资分位',
  etf_share_change: 'ETF份额',
  institutional_flow: '机构动向',
  sector_flow: '板块资金',
  // 情绪温度
  turnover_percentile: '换手率分位',
  advance_decline: '涨跌家数',
  volatility_percentile: '波动率',
  news_sentiment: '新闻情绪',
  fear_greed: '恐贪指数',
}

/** 基本面重仓股5维评分中文名 */
export const STOCK_DIM_LABELS = {
  profitability: '盈利能力',
  growth: '成长性',
  solvency: '偿债能力',
  stability: '稳定性',
  valuation: '估值',
}

/** 大师理念矩阵 — key_metrics 字段中文映射 */
export const MASTER_METRIC_LABELS = {
  has_moat: '护城河',
  roe_consistent: 'ROE持续性',
  margin_of_safety: '安全边际',
  profitability_avg: '盈利能力',
  stability_avg: '稳定性',
  peg_estimate: 'PEG',
  company_type: '公司类型',
  is_fast_grower: '快速增长',
  is_low_cost: '低成本',
  is_indexed: '指数化',
  cycle_position: '周期位置',
  is_fearful: '逆向信号',
  is_well_diversified: '分散化',
  top10_coverage: 'Top10集中度',
  business: '生意属性',
  company: '公司质地',
  price: '价格评估',
}

/** 大师左侧色条颜色（6位大师不同标识色） */
export const MASTER_ACCENT_COLORS = {
  buffett: '#1565c0',
  lynch: '#2e7d32',
  bogle: '#ef6c00',
  marks: '#6a1b9a',
  dalio: '#00838f',
  duanyongping: '#c62828',
}

/** 大师 icon 映射（回测API不返回master_icon，前端补全） */
export const MASTER_ICONS = {
  buffett: '🏰',
  lynch: '📈',
  bogle: '💰',
  marks: '🔄',
  dalio: '⚖️',
  duanyongping: '🎯',
}

/** 信号状态映射：6 档 = 4 档估值信号 + 2 档退出信号 */
export const SIGNAL_STATUS_META = {
  green: { label: '可上车', cls: 'sig-hit' },
  yellow: { label: '接近上车', cls: 'sig-warn' },
  red: { label: '等待中', cls: 'sig-bad' },
  gray: { label: '数据不足', cls: 'sig-neutral' },
  profit_target: { label: '止盈', cls: 'sig-profit' },
  stop_loss: { label: '止损', cls: 'sig-stop' },
}

/** 受益标的分级中文 + 颜色 */
export const TIER_META = {
  strong_buy: { label: '强推荐买入', color: '#dc2626', icon: 'star' },
  watch: { label: '关注', color: '#2563eb', icon: 'bookmark' },
  add_position: { label: '建议补仓', color: '#16a34a', icon: 'plus' },
  observe_only: { label: '仅观察', color: '#6b7280', icon: 'eye' },
}

/** 估值状态中文 + 颜色 */
export const VALUATION_META = {
  undervalued: { label: '低估', color: '#16a34a' },
  fair: { label: '合理', color: '#2563eb' },
  overvalued: { label: '偏高', color: '#f59e0b' },
  expensive: { label: '高估', color: '#dc2626' },
  unknown: { label: '无数据', color: '#6b7280' },
}

/** LI-8 领先指标类型/等级标签 */
export const LEADING_TYPE_LABELS = {
  policy_draft: '政策草案', capex_announcement: '资本开支', insider_trading: '产业资本',
  customs_data: '海关数据', pmi_subitem: 'PMI分项',
}
export const LEADING_LEVEL_LABELS = { strong: '强领先', medium: '中领先', weak: '弱领先' }
export const LEADING_LEVEL_COLORS = { strong: '#ef4444', medium: '#f59e0b', weak: '#9ca3af' }

// ── 纯函数：颜色/文案格式化 ──

/** 板块准确率颜色（绿→红渐变） */
export function sectorAccuracyColor(acc) {
  if (acc >= 0.8) return 'rgba(22, 163, 74, 0.18)'
  if (acc >= 0.6) return 'rgba(34, 197, 94, 0.12)'
  if (acc >= 0.4) return 'rgba(245, 158, 11, 0.15)'
  return 'rgba(220, 38, 38, 0.12)'
}

/** 买入评分颜色类：>=80绿 / >=60蓝 / >=40橙 / <40红 */
export function scoreColorClass(score) {
  if (score == null) return ''
  if (score >= 80) return 'score-excellent'
  if (score >= 60) return 'score-good'
  if (score >= 40) return 'score-normal'
  return 'score-cautious'
}

/** 买入评级文案 */
export function scoreRatingLabel(score) {
  if (score == null) return '—'
  if (score >= 80) return '优秀'
  if (score >= 60) return '良好'
  if (score >= 40) return '一般'
  return '谨慎'
}

/** 评级 → CSS 类映射（excellent绿 / good蓝 / fair黄 / poor红） */
export function ratingColorClass(rating) {
  return {
    excellent: 'rating-excellent',
    good: 'rating-good',
    fair: 'rating-fair',
    poor: 'rating-poor',
  }[rating] || ''
}

/** 评级中文文案 */
export function ratingLabel(rating) {
  return { excellent: '优秀', good: '良好', fair: '一般', poor: '较差' }[rating] || rating || '—'
}

/** 大师 action → 颜色 */
export function masterActionColor(action) {
  return {
    strong_buy: '#4caf50',
    dca: '#2196f3',
    hold: '#9e9e9e',
    reduce: '#ff9800',
    wait: '#f44336',
  }[action] || '#9e9e9e'
}

/** 大师 action → 中文文案（分布条 tooltip 用） */
export function masterActionLabel(action) {
  return ACTION_LABELS[action]?.label || action || '—'
}

/** 大师 rating → 评分颜色 */
export function masterRatingColor(rating) {
  return {
    excellent: '#4caf50',
    good: '#2196f3',
    fair: '#ff9800',
    poor: '#f44336',
  }[rating] || '#9e9e9e'
}

/** 大师 key → 左侧色条颜色 */
export function masterAccentColor(masterKey) {
  return MASTER_ACCENT_COLORS[masterKey] || '#6b7280'
}

/** 大师 key → icon */
export function masterIcon(masterKey) {
  return MASTER_ICONS[masterKey] || '🎯'
}

/** 格式化单个指标值：布尔→是/否，数字→去除多余小数位 */
export function formatMetricValue(v) {
  if (v === true) return '是'
  if (v === false) return '否'
  if (typeof v === 'number') {
    return Number.isInteger(v) ? String(v) : v.toFixed(1).replace(/\.0$/, '')
  }
  return String(v)
}

/** 格式化 key_metrics 为可读键值对数组 */
export function formatMetrics(metrics) {
  if (!metrics) return []
  return Object.entries(metrics).map(([k, v]) => ({
    label: MASTER_METRIC_LABELS[k] || k,
    value: formatMetricValue(v),
  }))
}

/** 共识强度 → CSS 类（高度共识绿/多数共识蓝/温和共识橙/意见分歧红） */
export function consensusClass(consensus) {
  const label = consensus?.agreement_label || ''
  if (label.includes('高度')) return 'mm-consensus-high'
  if (label.includes('多数')) return 'mm-consensus-major'
  if (label.includes('温和')) return 'mm-consensus-mild'
  return 'mm-consensus-split'
}

/** 夏普比率颜色：>1绿 / 0-1蓝 / <0红 */
export function sharpeColorClass(v) {
  if (v == null) return ''
  if (v > 1) return 'risk-good'
  if (v >= 0) return 'risk-normal'
  return 'risk-bad'
}

/** 波动率颜色：<15%绿 / 15-25%黄 / >25%红 */
export function volatilityColorClass(v) {
  if (v == null) return ''
  if (v < 15) return 'risk-good'
  if (v <= 25) return 'risk-warn'
  return 'risk-bad'
}

/** 胜率颜色：≥60%绿 / 40-60%黄 / <40%红 / 无数据灰 */
export function winRateColor(rate) {
  if (rate == null || isNaN(rate)) return '#9e9e9e'
  if (rate >= 60) return '#4caf50'
  if (rate >= 40) return '#ff9800'
  return '#f44336'
}

/** 验证结果 → {label, icon, color}（correct绿/wrong红/flat灰/待验证橙） */
export function verifyResultMeta(result) {
  return {
    correct: { label: '正确', icon: '✓', color: '#4caf50' },
    wrong: { label: '偏差', icon: '✗', color: '#f44336' },
    flat: { label: '平淡', icon: '—', color: '#9e9e9e' },
  }[result] || { label: '待验证', icon: '⏳', color: '#ff9800' }
}

// ── 纯函数：信号/机会值 ──

/** 计算有效信号：退出信号优先于估值信号 */
export function effectiveSignal(item) {
  const exit = item.exit_signal
  if (exit === 'profit_target' || exit === 'stop_loss') return exit
  return item.signal_status || 'gray'
}

/** 取关注基金信号状态元信息（默认 gray，退出信号优先级高） */
export function signalMeta(item) {
  return SIGNAL_STATUS_META[effectiveSignal(item)] || SIGNAL_STATUS_META.gray
}

/** 信号置信度颜色分级 */
export function confidenceClass(score) {
  if (score == null) return ''
  if (score >= 75) return 'conf-high'
  if (score >= 50) return 'conf-mid'
  return 'conf-low'
}

/** 是否有多维信号（至少一个非 neutral） */
export function hasMultidimSignal(item) {
  return (item.tech_signal && item.tech_signal !== 'neutral')
      || (item.capital_signal && item.capital_signal !== 'neutral')
      || (item.sentiment_signal && item.sentiment_signal !== 'neutral')
}

/** 多维信号徽章样式 */
export function multidimBadgeClass(dim, signal) {
  if (dim === 'tech') {
    if (signal === 'bull') return 'md-bull'
    if (signal === 'bear') return 'md-bear'
    return 'md-neutral'
  }
  if (dim === 'capital') {
    if (signal === 'inflow') return 'md-bull'
    if (signal === 'outflow') return 'md-bear'
    return 'md-neutral'
  }
  if (dim === 'sentiment') {
    if (signal === 'fear') return 'md-bull'  // 逆向布局机会
    if (signal === 'greed') return 'md-bear' // 过热风险
    return 'md-neutral'
  }
  return 'md-neutral'
}

/** 多维信号中文标签 */
export function multidimLabel(dim, signal) {
  const map = {
    tech: { bull: '多头', bear: '看空', neutral: '中性' },
    capital: { inflow: '净流入', outflow: '净流出', neutral: '中性' },
    sentiment: { fear: '恐惧', greed: '贪婪', neutral: '中性' },
  }
  return (map[dim] && map[dim][signal]) || signal
}

/** 机会值分析维度标签（估值分位/回撤分位/净值分位） */
export function opportunityLabel(item) {
  if (item.current_percentile != null) return '估值分位'
  if (item.drawdown_percentile != null) return '回撤分位'
  if (item.nav_percentile != null) return '净值分位'
  return '机会值'
}

/** 机会值分析数值（含%），无则 null */
export function opportunityValue(item) {
  if (item.current_percentile != null) return Number(item.current_percentile).toFixed(0)
  if (item.drawdown_percentile != null) return Number(item.drawdown_percentile).toFixed(0)
  if (item.nav_percentile != null) return Number(item.nav_percentile).toFixed(0)
  return null
}

/** 机会值是否处于低位/机会区 */
export function opportunityIsLow(item) {
  if (item.current_percentile != null) return parseFloat(item.current_percentile) <= 20
  if (item.drawdown_percentile != null) return parseFloat(item.drawdown_percentile) >= 80
  if (item.nav_percentile != null) return parseFloat(item.nav_percentile) <= 20
  return false
}

/** 分析方法描述文案 */
export function analysisMethodLabel(item) {
  const m = item.analysis_method
  if (m === 'drawdown') return '基于回撤分位'
  if (m === 'nav_percentile') return '基于净值分位'
  if (m === 'yingmi') return '基于盈米诊断'
  if (m === 'index_valuation') return '基于指数估值'
  return ''
}

// ── 纯函数：事件/趋势/影响 ──

/** 判断是否为趋势事件（含 time_frame） */
export function isTrendEvent(evt) {
  return !!(evt && evt.time_frame)
}

/** 趋势时间跨度标签 */
export function timeFrameLabel(tf) {
  return { short: '短期', medium: '中期', long: '长期' }[tf] || tf || ''
}

/** 影响方向 → {text, cls} */
export function impactDirectionLabel(dir) {
  return {
    up: { text: '↑ 利好', cls: 'impact-up' },
    down: { text: '↓ 利空', cls: 'impact-down' },
    flat: { text: '→ 中性', cls: 'impact-flat' },
  }[dir] || { text: dir || '', cls: 'impact-flat' }
}

/** 影响持续期 → 中文标签 */
export function impactDurationLabel(d) {
  return { short_term: '1-3天', medium_term: '1-2周', long_term: '>2周' }[d] || d || ''
}

/** 影响幅度格式化（带正负号） */
export function impactPctText(pct) {
  if (pct == null || pct === '') return ''
  const n = parseFloat(pct)
  if (Number.isNaN(n)) return ''
  return (n >= 0 ? '+' : '') + n.toFixed(2) + '%'
}

/** 判断事件是否已有缓存的影响分析（7 天内） */
export function hasCachedImpactAnalysis(evt) {
  if (!evt.impact_analysis || !evt.impact_analyzed_at) return false
  try {
    const dt = new Date(evt.impact_analyzed_at.replace(' ', 'T'))
    const days = (Date.now() - dt.getTime()) / (1000 * 60 * 60 * 24)
    return days < 7
  } catch (_) { return false }
}

/** 格式化金额（千分位） */
export function formatAmount(num) {
  if (num == null) return '-'
  const n = Number(num)
  if (isNaN(n)) return '-'
  return Math.abs(n).toLocaleString('zh-CN', { minimumFractionDigits: 0, maximumFractionDigits: 0 })
}

/** 按分级分组受益标的 */
export function groupBeneficiariesByTier(beneficiaries) {
  if (!Array.isArray(beneficiaries) || !beneficiaries.length) return []
  const groups = {}
  for (const b of beneficiaries) {
    const tier = b.recommendation_tier || 'observe_only'
    if (!groups[tier]) groups[tier] = []
    groups[tier].push(b)
  }
  // 按 strong_buy → watch → add_position → observe_only 排序
  const order = ['strong_buy', 'watch', 'add_position', 'observe_only']
  return order
    .filter(t => groups[t] && groups[t].length)
    .map(t => ({ tier: t, items: groups[t] }))
}

// ── 纯函数：日期/验证/状态标签 ──

/** 判断关注基金是否在 7 天内被自动同步为 bought */
export function isAutoSynced(item) {
  if (!item.entry_date) return false
  try {
    const d = new Date(item.entry_date)
    if (isNaN(d.getTime())) return false
    const days = (Date.now() - d.getTime()) / 86400000
    return days <= 7
  } catch {
    return false
  }
}

/** 解析验证结果 JSON */
export function parseVerification(str) {
  try { return JSON.parse(str || '') } catch { return null }
}

/** 验证结果标签 */
export function verificationLabel(status) {
  return { correct: '验证正确', wrong: '验证偏差', flat: '波动平淡' }[status] || status
}

/** 验证结果图标 */
export function verificationIcon(status) {
  return { correct: 'check-circle', wrong: 'alert-triangle', flat: 'info' }[status] || 'info'
}

/** 事件相关度标签 */
export function relevanceLabel(r) {
  return {
    holding_impact: '持仓影响',
    watchlist_impact: '关注机会',
    opportunity: '建仓机会',
    market_watch: '市场关注',
  }[r] || r
}

/** 事件状态标签 */
export function statusLabel(s) {
  return { upcoming: '即将到来', imminent: '临近', materialized: '已落地', expired: '已过期' }[s] || s
}

/** 解析 JSON 数组（失败返回空数组） */
export function parseJsonArray(str) {
  try { return JSON.parse(str || '[]') } catch { return [] }
}

/** 事件日期格式化：今天/明天/N天后 */
export function formatDate(dateStr) {
  if (!dateStr) return ''
  const d = new Date(dateStr)
  if (isNaN(d)) return dateStr
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const diff = Math.round((d - today) / (1000 * 60 * 60 * 24))
  if (diff === 0) return '今天'
  if (diff === 1) return '明天'
  if (diff > 1 && diff <= 7) return `${diff} 天后`
  return dateStr
}

/** 事件影响方向 → {text, class} */
export function directionLabel(d) {
  if (d === 'positive') return { text: '利好', class: 'dir-positive' }
  if (d === 'negative') return { text: '利空', class: 'dir-negative' }
  return { text: '中性', class: 'dir-neutral' }
}

/** 格式化短日期：MM-DD（超过1年显示 MM-DD-YY） */
export function formatShortDate(dateStr) {
  if (!dateStr) return ''
  const d = new Date(dateStr)
  if (isNaN(d.getTime())) return ''
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  const now = new Date()
  if (d.getFullYear() !== now.getFullYear()) {
    return `${mm}-${dd}-${String(d.getFullYear()).slice(-2)}`
  }
  return `${mm}-${dd}`
}

/**
 * 渲染 notes 文本：将"来自事件「title」"中的 title 部分渲染为可点击链接，
 * 点击切换到事件雷达 Tab。用 v-html 注入，需先转义其它字符防止 XSS。
 */
export function renderNotes(notes) {
  if (!notes) return ''
  // 转义 HTML 特殊字符
  const escaped = notes
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
  // 将「来自事件「xxx」」中的 xxx 转为链接
  return escaped.replace(
    /来自事件「([^」]+)」/g,
    (match, title) =>
      `来自事件「<a class="wl-note-event-link" data-event-title="${title}" href="javascript:void(0)">${title}</a>」`
  )
}
