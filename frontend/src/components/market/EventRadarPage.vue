<script setup>
/**
 * EventRadarPage — 机会雷达整合页
 *
 * 三个 Tab 形成「发现机会→跟踪观察→上车信号→落地验证」闭环：
 * - 事件雷达：未来 1-2 周即将发生的市场事件，候选基金可一键加入关注
 * - 关注机会：关注列表基金卡片，展示目标价/估值分位/上车信号状态
 * - 落地验证：已验证事件 + 板块准确率统计
 */
import { ref, computed, onMounted, watch, nextTick } from 'vue'
import {
  listMarketEvents, triggerEventRadarScan, triggerEventRadarVerify, getEventRadarAccuracy,
  listWatchlist, addToWatchlist, removeWatchlistItem, refreshWatchlistNavs,
  triggerWatchlistScan, patrolWatchlist, updateWatchlistItem, markWatchlistBought, analyzeArticleTrends,
  getBuyScore, getFundQuality, getWatchlistSignalStats,
  getPortfolioHealthReport, getPortfolioRiskMetrics,
  getMasterDecisionHistory, getMasterAccuracyStats, triggerMasterVerification,
  updateWatchlistEntry, getWatchlistExitStatus, analyzeEventImpact,
  getEventImpactAmount,
} from '../../api'
import Icon from '../ui/Icon.vue'
import { useToast } from '../../composables/useToast'
import { renderMarkdown } from '../../composables/useMarkdown'

const emit = defineEmits(['navigate'])

const activeTab = ref('events') // events / watchlist / verification
const events = ref([])
const watchlist = ref([])
const loading = ref(false)
const scanning = ref(false)
const verifying = ref(false)
const refreshingNavs = ref(false)
const scanningWatchlist = ref(false)
const patrolling = ref(false)
const accuracy = ref(null)
const lastScanTime = ref(null)
const editingId = ref(null)
const editForm = ref({ target_price: '', target_percentile: '' })
const activeFilter = ref('all') // all / holding_impact / watchlist_impact / opportunity / market_watch
const activeStatus = ref('active') // active / all / materialized / expired

// 文章趋势分析相关状态
const showArticleDialog = ref(false)
const articleUrl = ref('')
const analyzingArticle = ref(false)

// 买入评分：{ [itemId]: { score, rating, dimensions, calculated_at } }
const buyScores = ref({})
const refreshingScores = ref(false)
// P0-3（2026-07-21）信号回测命中率统计
const signalStats = ref({ total: 0, reviewed: 0, pending: 0, hit: 0, miss: 0, hit_rate: null })

// 基金六维体检报告：{ [fund_code]: { total_score, rating, report, decision_matrix, ... } }
const fundReports = ref({})
// 单基金体检报告加载中标记：{ [fund_code]: bool }
const loadingReports = ref({})
// 体检报告详情弹窗当前展示的报告对象
const reportDetailItem = ref(null)

// ── 组合智能 ──
// 组合7维体检+大师组合版（getPortfolioHealthReport）
const portfolioData = ref(null)
// 组合风险度量（getPortfolioRiskMetrics）
const portfolioRisk = ref(null)
const loadingPortfolio = ref(false)

const FILTERS = [
  { key: 'all', label: '全部', icon: 'satellite' },
  { key: 'holding_impact', label: '持仓影响', icon: 'alert-triangle' },
  { key: 'watchlist_impact', label: '关注机会', icon: 'bookmark' },
  { key: 'opportunity', label: '建仓机会', icon: 'trending-up' },
  { key: 'market_watch', label: '市场关注', icon: 'info' },
]

const STATUS_TABS = [
  { key: 'active', label: '进行中' },
  { key: 'materialized', label: '已落地' },
  { key: 'expired', label: '已过期' },
  { key: 'all', label: '全部' },
]

const filteredEvents = computed(() => {
  let list = events.value
  if (activeFilter.value !== 'all') {
    list = list.filter(e => e.relevance_to_user === activeFilter.value)
  }
  if (activeStatus.value === 'active') {
    list = list.filter(e => e.status === 'upcoming' || e.status === 'imminent')
  } else if (activeStatus.value !== 'all') {
    list = list.filter(e => e.status === activeStatus.value)
  }
  // 按预期日期升序
  return [...list].sort((a, b) => {
    const da = a.expected_date || ''
    const db = b.expected_date || ''
    return da.localeCompare(db)
  })
})

const verifiedEvents = computed(() => {
  return events.value
    .filter(e => parseVerification(e.verification_result))
    .sort((a, b) => (b.expected_date || '').localeCompare(a.expected_date || ''))
})

/** 被回溯校准过的事件（原始置信度 ≠ 校准后） */
const calibratedEvents = computed(() => {
  return events.value
    .filter(e => e.original_confidence != null && Math.abs((e.original_confidence || 0) - (e.confidence || 0)) > 0.001)
    .sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''))
    .slice(0, 10)
})

/** 板块准确率颜色（绿→红渐变） */
function sectorAccuracyColor(acc) {
  if (acc >= 0.8) return 'rgba(22, 163, 74, 0.18)'
  if (acc >= 0.6) return 'rgba(34, 197, 94, 0.12)'
  if (acc >= 0.4) return 'rgba(245, 158, 11, 0.15)'
  return 'rgba(220, 38, 38, 0.12)'
}

const stats = computed(() => {
  const total = events.value.length
  const holding = events.value.filter(e => e.relevance_to_user === 'holding_impact').length
  const watchlistHit = events.value.filter(e => e.relevance_to_user === 'watchlist_impact').length
  const opportunity = events.value.filter(e => e.relevance_to_user === 'opportunity').length
  const watch = events.value.filter(e => e.relevance_to_user === 'market_watch').length
  return { total, holding, watchlistHit, opportunity, watch }
})

const watchlistStats = computed(() => {
  const total = watchlist.value.length
  // 使用 effectiveSignal：已触发退出信号(profit_target/stop_loss)的项不计入估值信号统计
  const canBuy = watchlist.value.filter(w => effectiveSignal(w) === 'green').length
  const approaching = watchlist.value.filter(w => effectiveSignal(w) === 'yellow').length
  const waiting = watchlist.value.filter(w => effectiveSignal(w) === 'red').length
  const noData = watchlist.value.filter(w => effectiveSignal(w) === 'gray').length
  // Batch1 增强点 1：退出信号统计
  const profitTargets = watchlist.value.filter(w => effectiveSignal(w) === 'profit_target').length
  const stopLosses = watchlist.value.filter(w => effectiveSignal(w) === 'stop_loss').length
  // 近7日新增
  const recent7d = watchlist.value.filter(w => {
    if (!w.created_at) return false
    const created = new Date(w.created_at)
    const now = new Date()
    return (now - created) / (1000 * 60 * 60 * 24) <= 7
  }).length
  // P0-3（2026-07-21）信号回测命中率
  const hitRate = signalStats.value.hit_rate
  const hitReviewed = signalStats.value.reviewed || 0
  const hitCount = signalStats.value.hit || 0
  return { total, canBuy, approaching, waiting, noData, recent7d, profitTargets, stopLosses, hitRate, hitReviewed, hitCount }
})

// 今日操作建议：优先可上车基金名称
const firstCanBuyFund = computed(() => {
  const green = sortedWatchlist.value.filter(w => effectiveSignal(w) === 'green')
  return green.length > 0 ? green[0].fund_name : ''
})
const firstCanBuyInfo = computed(() => {
  const green = sortedWatchlist.value.filter(w => effectiveSignal(w) === 'green')
  if (green.length === 0) return null
  const f = green[0]
  return {
    percentile: f.current_percentile != null ? Number(f.current_percentile).toFixed(0) + '%' : '未知',
    distance: f.distance_to_buy != null ? (parseFloat(f.distance_to_buy) >= 0 ? '+' : '') + Number(f.distance_to_buy).toFixed(2) + '%' : '未知',
  }
})

// 关注列表排序
const watchlistSort = ref('default') // default | percentile | created | updated
const watchlistSortOptions = [
  { key: 'default', label: '默认' },
  { key: 'percentile', label: '估值低→高' },
  { key: 'created', label: '最近加入' },
  { key: 'updated', label: '最近更新' },
]
// 关注列表筛选：all | canBuy | approaching | waiting | noData | recent7d
const watchlistFilter = ref('all')
const watchlistFilterOptions = [
  { key: 'all', label: '全部' },
  { key: 'canBuy', label: '可上车' },
  { key: 'approaching', label: '接近上车' },
  { key: 'waiting', label: '等待中' },
  { key: 'noData', label: '数据不足' },
  { key: 'recent7d', label: '近7日新增' },
]
// 基金类型筛选（从 watchlist 数据动态提取）
const watchlistCategoryFilter = ref('all')
const watchlistCategoryOptions = computed(() => {
  const set = new Set()
  watchlist.value.forEach(w => {
    if (w.fund_category) set.add(w.fund_category)
  })
  return Array.from(set).sort()
})
const sortedWatchlist = computed(() => {
  let list = [...watchlist.value]
  // 应用基金类型筛选
  if (watchlistCategoryFilter.value !== 'all') {
    list = list.filter(w => w.fund_category === watchlistCategoryFilter.value)
  }
  // 应用筛选（使用 effectiveSignal：已触发退出信号的项不归入估值信号档位）
  if (watchlistFilter.value === 'canBuy') {
    list = list.filter(w => effectiveSignal(w) === 'green')
  } else if (watchlistFilter.value === 'approaching') {
    list = list.filter(w => effectiveSignal(w) === 'yellow')
  } else if (watchlistFilter.value === 'waiting') {
    list = list.filter(w => effectiveSignal(w) === 'red')
  } else if (watchlistFilter.value === 'noData') {
    list = list.filter(w => effectiveSignal(w) === 'gray')
  } else if (watchlistFilter.value === 'recent7d') {
    list = list.filter(w => {
      if (!w.created_at) return false
      const created = new Date(w.created_at)
      const now = new Date()
      return (now - created) / (1000 * 60 * 60 * 24) <= 7
    })
  }
  if (watchlistSort.value === 'percentile') {
    // 估值分位升序，null/undefined 排最后
    list.sort((a, b) => {
      const pa = (a.current_percentile === null || a.current_percentile === undefined) ? Infinity : parseFloat(a.current_percentile)
      const pb = (b.current_percentile === null || b.current_percentile === undefined) ? Infinity : parseFloat(b.current_percentile)
      return pa - pb
    })
  } else if (watchlistSort.value === 'created') {
    list.sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0))
  } else if (watchlistSort.value === 'updated') {
    list.sort((a, b) => new Date(b.nav_updated_at || b.updated_at || 0) - new Date(a.nav_updated_at || a.updated_at || 0))
  }
  return list
})

/** 切换筛选条件（点击同一筛选再次点击取消） */
function toggleWatchlistFilter(key) {
  watchlistFilter.value = watchlistFilter.value === key ? 'all' : key
}

/**
 * 渲染 notes 文本：将"来自事件「title」"中的 title 部分渲染为可点击链接，
 * 点击切换到事件雷达 Tab。
 * 用 v-html 注入，需先转义其它字符防止 XSS。
 */
function renderNotes(notes) {
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

/** 处理 notes 中事件链接点击：切换到事件雷达 Tab */
function handleNotesClick(e) {
  if (e?.target?.classList?.contains('wl-note-event-link')) {
    activeTab.value = 'events'
  }
}

/** 格式化短日期：MM-DD（超过1年显示 MM-DD-YY） */
function formatShortDate(dateStr) {
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

async function loadEvents() {
  loading.value = true
  try {
    const { data } = await listMarketEvents({ limit: 100 })
    events.value = data?.events || []
    lastScanTime.value = data?.last_scan_time || null
  } catch (e) {
    useToast().showToast('加载事件失败', 'error')
  } finally {
    loading.value = false
  }
}

async function loadWatchlist() {
  try {
    const { data } = await listWatchlist('watching')
    watchlist.value = data?.items || []
    // 只做轻量级巡检刷新估值分位（静默）
    if (watchlist.value.length) autoPatrol()
    // P0-3（2026-07-21）并行加载信号回测命中率统计
    loadSignalStats()
    // 买入评分和体检报告改为按需加载（用户查看时再加载）
  } catch (e) {
    useToast().showToast('加载关注列表失败', 'error')
  }
}

/** P0-3（2026-07-21）加载信号回测命中率统计（静默失败） */
async function loadSignalStats() {
  try {
    const { data } = await getWatchlistSignalStats()
    if (data) signalStats.value = data
  } catch (e) {
    // 静默失败，不打扰用户
  }
}

/** 批量加载所有关注基金的买入评分（静默失败） */
async function loadBuyScores() {
  const items = watchlist.value
  if (!items.length) return
  refreshingScores.value = true
  try {
    const results = await Promise.allSettled(
      items.map(item => getBuyScore(item.id))
    )
    const next = { ...buyScores.value }
    results.forEach((r, idx) => {
      const itemId = items[idx].id
      if (r.status === 'fulfilled' && r.value?.data) {
        next[itemId] = r.value.data
      } else {
        // 失败时清除旧分，避免展示过期数据
        delete next[itemId]
      }
    })
    buyScores.value = next
  } catch { /* 静默失败 */ } finally {
    refreshingScores.value = false
  }
}

/** 批量加载所有关注基金的六维体检报告（静默失败，单个失败不影响其他） */
async function loadFundReports() {
  const items = watchlist.value
  if (!items.length) return
  // 标记所有基金为加载中
  const loadingMap = { ...loadingReports.value }
  items.forEach(item => { loadingMap[item.fund_code] = true })
  loadingReports.value = loadingMap
  try {
    const results = await Promise.allSettled(
      items.map(item => getFundQuality(item.fund_code))
    )
    const next = { ...fundReports.value }
    results.forEach((r, idx) => {
      const fundCode = items[idx].fund_code
      if (r.status === 'fulfilled' && r.value?.data) {
        next[fundCode] = r.value.data
      } else {
        // 失败时清除旧报告，避免展示过期数据
        delete next[fundCode]
      }
    })
    fundReports.value = next
  } catch { /* 静默失败 */ } finally {
    // 清除加载中标记
    const cleared = { ...loadingReports.value }
    items.forEach(item => { delete cleared[item.fund_code] })
    loadingReports.value = cleared
  }
}

/** 刷新单个基金的六维体检报告（强制重新计算） */
async function refreshFundReport(fundCode) {
  if (loadingReports.value[fundCode]) return
  loadingReports.value = { ...loadingReports.value, [fundCode]: true }
  try {
    const { data } = await getFundQuality(fundCode, true)
    fundReports.value = { ...fundReports.value, [fundCode]: data }
    useToast().showToast('体检报告已刷新', 'success')
  } catch (e) {
    const msg = e?.response?.data?.detail || e?.response?.data?.message || '刷新失败'
    useToast().showToast(msg, 'error')
  } finally {
    const cleared = { ...loadingReports.value }
    delete cleared[fundCode]
    loadingReports.value = cleared
  }
}

/** 打开体检报告详情弹窗 */
function openReportDetail(item) {
  const report = fundReports.value[item.fund_code]
  if (!report) return
  // 附上基金名称用于弹窗标题展示
  reportDetailItem.value = { ...report, fund_name: item.fund_name }
}

/** 关闭体检报告详情弹窗 */
function closeReportDetail() {
  reportDetailItem.value = null
}

/** 买入评分颜色类：>=80绿 / >=60蓝 / >=40橙 / <40红 */
function scoreColorClass(score) {
  if (score == null) return ''
  if (score >= 80) return 'score-excellent'
  if (score >= 60) return 'score-good'
  if (score >= 40) return 'score-normal'
  return 'score-cautious'
}

/** 买入评级文案 */
function scoreRatingLabel(score) {
  if (score == null) return '—'
  if (score >= 80) return '优秀'
  if (score >= 60) return '良好'
  if (score >= 40) return '一般'
  return '谨慎'
}

/** 买入评分维度中文名 */
const BUY_DIMENSION_LABELS = {
  valuation: '估值维度',
  price: '净值距目标',
  correlation: '相关性',
  concentration: '集中度',
}

/** 七维体检报告维度中文名 */
const DIMENSION_LABELS = {
  quality: '基金质量',
  drawdown: '回撤恢复',
  trend: '趋势均线',
  capital: '资金流向',
  sentiment: '情绪温度',
  valuation: '估值水位',
  fundamental: '基本面',
}

/** 季度调仓动作标签映射 */
const HOLDING_ACTION_LABELS = {
  new: { label: '新进', class: 'hc-action-new' },
  increase: { label: '增持', class: 'hc-action-increase' },
  decrease: { label: '减持', class: 'hc-action-decrease' },
  exit: { label: '退出', class: 'hc-action-exit' },
}

/** 评级 → CSS 类映射（excellent绿 / good蓝 / fair黄 / poor红） */
function ratingColorClass(rating) {
  return {
    excellent: 'rating-excellent',
    good: 'rating-good',
    fair: 'rating-fair',
    poor: 'rating-poor',
  }[rating] || ''
}

/** 评级中文文案 */
function ratingLabel(rating) {
  return { excellent: '优秀', good: '良好', fair: '一般', poor: '较差' }[rating] || rating || '—'
}

/** 操作建议映射 */
const ACTION_LABELS = {
  strong_buy: { label: '强烈加仓', class: 'action-strong-buy' },
  dca: { label: '定投加仓', class: 'action-dca' },
  hold: { label: '持有', class: 'action-hold' },
  reduce: { label: '减仓', class: 'action-reduce' },
  wait: { label: '等待', class: 'action-wait' },
}

/** 体检报告子项中文名（用于详情弹窗展示各维度细分指标） */
const SUBDIM_LABELS = {
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
const STOCK_DIM_LABELS = {
  profitability: '盈利能力',
  growth: '成长性',
  solvency: '偿债能力',
  stability: '稳定性',
  valuation: '估值',
}

/** 大师理念矩阵 — key_metrics 字段中文映射 */
const MASTER_METRIC_LABELS = {
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
const MASTER_ACCENT_COLORS = {
  buffett: '#1565c0',
  lynch: '#2e7d32',
  bogle: '#ef6c00',
  marks: '#6a1b9a',
  dalio: '#00838f',
  duanyongping: '#c62828',
}

/** 大师 icon 映射（回测API不返回master_icon，前端补全） */
const MASTER_ICONS = {
  buffett: '🏰',
  lynch: '📈',
  bogle: '💰',
  marks: '🔄',
  dalio: '⚖️',
  duanyongping: '🎯',
}

/** 大师 action → 颜色（strong_buy绿/dca蓝/hold灰/reduce橙/wait红） */
function masterActionColor(action) {
  return {
    strong_buy: '#4caf50',
    dca: '#2196f3',
    hold: '#9e9e9e',
    reduce: '#ff9800',
    wait: '#f44336',
  }[action] || '#9e9e9e'
}

/** 大师 action → 中文文案（分布条 tooltip 用） */
function masterActionLabel(action) {
  return ACTION_LABELS[action]?.label || action || '—'
}

/** 大师 rating → 评分颜色（excellent绿/good蓝/fair黄/poor红/null灰） */
function masterRatingColor(rating) {
  return {
    excellent: '#4caf50',
    good: '#2196f3',
    fair: '#ff9800',
    poor: '#f44336',
  }[rating] || '#9e9e9e'
}

/** 大师 key → 左侧色条颜色 */
function masterAccentColor(masterKey) {
  return MASTER_ACCENT_COLORS[masterKey] || '#6b7280'
}

/** 格式化单个指标值：布尔→是/否，数字→去除多余小数位 */
function formatMetricValue(v) {
  if (v === true) return '是'
  if (v === false) return '否'
  if (typeof v === 'number') {
    return Number.isInteger(v) ? String(v) : v.toFixed(1).replace(/\.0$/, '')
  }
  return String(v)
}

/** 格式化 key_metrics 为可读键值对数组 */
function formatMetrics(metrics) {
  if (!metrics) return []
  return Object.entries(metrics).map(([k, v]) => ({
    label: MASTER_METRIC_LABELS[k] || k,
    value: formatMetricValue(v),
  }))
}

/** 共识强度 → CSS 类（高度共识绿/多数共识蓝/温和共识橙/意见分歧红） */
function consensusClass(consensus) {
  const label = consensus?.agreement_label || ''
  if (label.includes('高度')) return 'mm-consensus-high'
  if (label.includes('多数')) return 'mm-consensus-major'
  if (label.includes('温和')) return 'mm-consensus-mild'
  return 'mm-consensus-split'
}

/** 大师卡片展开状态：{ [`${fundCode}:${masterKey}`]: bool } */
const expandedMasters = ref({})
// 关注基金卡片展开状态（记录 fund_code）
const expandedCards = ref({})
// 事件雷达跳转：需要滚动到的基金代码
const scrollToFundCode = ref('')
// 组合智能/大师回测面板折叠状态（默认折叠）
const expandedPortfolio = ref(false)
const expandedMasterBacktest = ref(false)

function toggleCardExpand(fundCode) {
  expandedCards.value = { ...expandedCards.value, [fundCode]: !expandedCards.value[fundCode] }
}

/** 切换大师卡片展开/收起 */
function toggleMaster(fundCode, masterKey) {
  const key = `${fundCode}:${masterKey}`
  expandedMasters.value = { ...expandedMasters.value, [key]: !expandedMasters.value[key] }
}

/** 切换组合智能大师卡片展开/收起 */
function togglePortfolioMaster(masterKey) {
  const key = `portfolio:${masterKey}`
  expandedMasters.value = { ...expandedMasters.value, [key]: !expandedMasters.value[key] }
}

/** 夏普比率颜色：>1绿 / 0-1蓝 / <0红 */
function sharpeColorClass(v) {
  if (v == null) return ''
  if (v > 1) return 'risk-good'
  if (v >= 0) return 'risk-normal'
  return 'risk-bad'
}

/** 波动率颜色：<15%绿 / 15-25%黄 / >25%红 */
function volatilityColorClass(v) {
  if (v == null) return ''
  if (v < 15) return 'risk-good'
  if (v <= 25) return 'risk-warn'
  return 'risk-bad'
}

/** 静默巡检：刷新估值分位，更新 watchlist 数据 */
async function autoPatrol() {
  if (patrolling.value) return
  patrolling.value = true
  try {
    const { data } = await patrolWatchlist()
    // 用巡检结果更新当前 watchlist，保存后端 signal_status 等完整字段
    const patrolMap = {}
    for (const item of data?.all_items || []) {
      patrolMap[item.id] = item
    }
    watchlist.value = watchlist.value.map(w => {
      const p = patrolMap[w.id]
      if (!p) return w
      return {
        ...w,
        current_percentile: p.current_percentile ?? w.current_percentile,
        current_nav: p.current_nav ?? w.current_nav,
        signal_status: p.signal_status ?? w.signal_status ?? 'gray',
        signal_reason: p.signal_reason ?? w.signal_reason ?? '',
        distance_to_target: p.distance_to_target ?? w.distance_to_target ?? null,
        pe: p.pe ?? w.pe ?? null,
        pb: p.pb ?? w.pb ?? null,
        change_pct: p.change_pct ?? w.change_pct ?? null,
        source: p.source ?? w.source ?? '',
        suggested_buy_price: p.suggested_buy_price ?? w.suggested_buy_price ?? null,
        buy_price_source: p.buy_price_source ?? w.buy_price_source ?? '',
        distance_to_buy: p.distance_to_buy ?? w.distance_to_buy ?? null,
        nav_updated_at: p.nav_updated_at ?? w.nav_updated_at ?? '',
      }
    })
  } catch { /* 静默失败 */ } finally {
    patrolling.value = false
  }
}

/** 信号状态映射：6 档 = 4 档估值信号 + 2 档退出信号
 *  - 估值信号：green/yellow/red/gray（来自后端 signal_status）
 *  - 退出信号：profit_target（止盈，蓝）/ stop_loss（止损，红）（来自后端 exit_signal）
 *  - 退出信号优先级高于估值信号，触发时覆盖估值显示
 */
const SIGNAL_STATUS_META = {
  green: { label: '可上车', cls: 'sig-hit' },
  yellow: { label: '接近上车', cls: 'sig-warn' },
  red: { label: '等待中', cls: 'sig-bad' },
  gray: { label: '数据不足', cls: 'sig-neutral' },
  profit_target: { label: '止盈', cls: 'sig-profit' },
  stop_loss: { label: '止损', cls: 'sig-stop' },
}

/** 计算有效信号：退出信号优先于估值信号
 *  - item.exit_signal 为 profit_target / stop_loss 时，覆盖估值信号
 *  - 否则使用 item.signal_status（4 档估值信号）
 */
function effectiveSignal(item) {
  const exit = item.exit_signal
  if (exit === 'profit_target' || exit === 'stop_loss') return exit
  return item.signal_status || 'gray'
}

/** 取关注基金信号状态元信息（默认 gray，退出信号优先级高） */
function signalMeta(item) {
  return SIGNAL_STATUS_META[effectiveSignal(item)] || SIGNAL_STATUS_META.gray
}

/** P0-1（2026-07-21）信号置信度颜色分级 */
function confidenceClass(score) {
  if (score == null) return ''
  if (score >= 75) return 'conf-high'
  if (score >= 50) return 'conf-mid'
  return 'conf-low'
}

/** P0-1（2026-07-21）是否有多维信号（至少一个非 neutral） */
function hasMultidimSignal(item) {
  return (item.tech_signal && item.tech_signal !== 'neutral')
      || (item.capital_signal && item.capital_signal !== 'neutral')
      || (item.sentiment_signal && item.sentiment_signal !== 'neutral')
}

/** P0-1（2026-07-21）多维信号徽章样式 */
function multidimBadgeClass(dim, signal) {
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

/** P0-1（2026-07-21）多维信号中文标签 */
function multidimLabel(dim, signal) {
  const map = {
    tech: { bull: '多头', bear: '看空', neutral: '中性' },
    capital: { inflow: '净流入', outflow: '净流出', neutral: '中性' },
    sentiment: { fear: '恐惧', greed: '贪婪', neutral: '中性' },
  }
  return (map[dim] && map[dim][signal]) || signal
}

/** 机会值分析维度标签（估值分位/回撤分位/净值分位） */
function opportunityLabel(item) {
  if (item.current_percentile != null) return '估值分位'
  if (item.drawdown_percentile != null) return '回撤分位'
  if (item.nav_percentile != null) return '净值分位'
  return '机会值'
}

/** 机会值分析数值（含%），无则 null */
function opportunityValue(item) {
  if (item.current_percentile != null) return Number(item.current_percentile).toFixed(0)
  if (item.drawdown_percentile != null) return Number(item.drawdown_percentile).toFixed(0)
  if (item.nav_percentile != null) return Number(item.nav_percentile).toFixed(0)
  return null
}

/** 机会值是否处于低位/机会区 */
function opportunityIsLow(item) {
  if (item.current_percentile != null) return parseFloat(item.current_percentile) <= 20
  if (item.drawdown_percentile != null) return parseFloat(item.drawdown_percentile) >= 80
  if (item.nav_percentile != null) return parseFloat(item.nav_percentile) <= 20
  return false
}

/** 分析方法描述文案 */
function analysisMethodLabel(item) {
  const m = item.analysis_method
  if (m === 'drawdown') return '基于回撤分位'
  if (m === 'nav_percentile') return '基于净值分位'
  if (m === 'yingmi') return '基于盈米诊断'
  if (m === 'index_valuation') return '基于指数估值'
  return ''
}

async function handleScan() {
  if (scanning.value) return
  scanning.value = true
  try {
    const { data } = await triggerEventRadarScan()
    useToast().showToast(
      `扫描完成：提取 ${data?.extracted || 0} 个事件，新增 ${data?.new || 0} 个`,
      'success'
    )
    await loadEvents()
  } catch (e) {
    useToast().showToast('扫描失败', 'error')
  } finally {
    scanning.value = false
  }
}

async function handleVerify() {
  if (verifying.value) return
  verifying.value = true
  try {
    const { data } = await triggerEventRadarVerify()
    useToast().showToast(
      `验证完成：${data?.verified || 0} 个事件，正确 ${data?.correct || 0}，偏差 ${data?.wrong || 0}`,
      'success'
    )
    await loadEvents()
    await loadAccuracy()
  } catch (e) {
    useToast().showToast('验证失败', 'error')
  } finally {
    verifying.value = false
  }
}

/** 判断事件是否为趋势类型（有 time_frame 字段） */
function isTrendEvent(evt) {
  return !!(evt && evt.time_frame)
}

/** 趋势时间跨度标签 */
function timeFrameLabel(tf) {
  return { short: '短期', medium: '中期', long: '长期' }[tf] || tf || ''
}

// ── Batch1 增强点 3：事件影响量化辅助展示 ──
/** 影响方向 → {text, cls} */
function impactDirectionLabel(dir) {
  return {
    up: { text: '↑ 利好', cls: 'impact-up' },
    down: { text: '↓ 利空', cls: 'impact-down' },
    flat: { text: '→ 中性', cls: 'impact-flat' },
  }[dir] || { text: dir || '', cls: 'impact-flat' }
}

/** 影响持续期 → 中文标签 */
function impactDurationLabel(d) {
  return { short_term: '1-3天', medium_term: '1-2周', long_term: '>2周' }[d] || d || ''
}

/** 影响幅度格式化（带正负号） */
function impactPctText(pct) {
  if (pct == null || pct === '') return ''
  const n = parseFloat(pct)
  if (Number.isNaN(n)) return ''
  return (n >= 0 ? '+' : '') + n.toFixed(2) + '%'
}

/** 判断事件是否已有缓存的影响分析（7 天内） */
function hasCachedImpactAnalysis(evt) {
  if (!evt.impact_analysis || !evt.impact_analyzed_at) return false
  try {
    const dt = new Date(evt.impact_analyzed_at.replace(' ', 'T'))
    const days = (Date.now() - dt.getTime()) / (1000 * 60 * 60 * 24)
    return days < 7
  } catch (_) { return false }
}

// 事件深度解读状态：{ [event_id]: { loading, analysis, analyzed_at, cached, error } }
const eventImpactMap = ref({})

/** 触发事件深度解读（LLM） */
async function handleAnalyzeImpact(evt) {
  if (!evt?.event_id) return
  const eid = evt.event_id
  const cur = eventImpactMap.value[eid] || {}
  if (cur.loading) return
  eventImpactMap.value[eid] = { ...cur, loading: true, error: '' }
  try {
    const { data } = await analyzeEventImpact(eid)
    const result = data?.data || data || {}
    if (result.error) {
      eventImpactMap.value[eid] = { ...cur, loading: false, error: result.error }
      useToast().showToast(result.error, 'warning')
      return
    }
    eventImpactMap.value[eid] = {
      loading: false,
      analysis: result.analysis || '',
      analyzed_at: result.analyzed_at || '',
      cached: !!result.cached,
      error: '',
    }
    // 同步写回 evt 本地状态（影响分析展示用）
    evt.impact_analysis = result.analysis || evt.impact_analysis
    evt.impact_analyzed_at = result.analyzed_at || evt.impact_analyzed_at
    useToast().showToast(
      result.cached ? '已读取缓存的影响分析' : '影响分析生成完成',
      'success'
    )
  } catch (e) {
    const msg = e?.response?.data?.detail || e?.response?.data?.message || '深度解读失败'
    eventImpactMap.value[eid] = { ...cur, loading: false, error: msg }
    useToast().showToast(msg, 'error')
  }
}

/** 切换事件深度解读展开/收起 */
const expandedImpactEvents = ref({})
function toggleImpactExpand(eid) {
  expandedImpactEvents.value[eid] = !expandedImpactEvents.value[eid]
}

// ── Batch2 增强点 2：事件影响金额估算（纯计算） ─────────────────────────
const impactAmountMap = ref({})
const expandedImpactAmountEvents = ref({})

/** 触发事件金额影响估算（纯计算，无 LLM） */
async function handleGetImpactAmount(evt) {
  const eid = evt.event_id
  if (!eid) return
  // 已有数据则直接展开
  if (impactAmountMap.value[eid]?.data) {
    expandedImpactAmountEvents.value[eid] = !expandedImpactAmountEvents.value[eid]
    return
  }
  impactAmountMap.value[eid] = { loading: true }
  try {
    const { data } = await getEventImpactAmount(eid)
    impactAmountMap.value[eid] = { loading: false, data: data?.data || data }
    // 自动展开
    expandedImpactAmountEvents.value[eid] = true
  } catch (e) {
    const msg = e?.response?.data?.detail || e?.response?.data?.message || '金额估算失败'
    impactAmountMap.value[eid] = { loading: false, error: msg }
    useToast().showToast(msg, 'error')
  }
}

/** 切换金额影响展开/收起 */
function toggleImpactAmountExpand(eid) {
  expandedImpactAmountEvents.value[eid] = !expandedImpactAmountEvents.value[eid]
}

/** 格式化金额（千分位） */
function formatAmount(num) {
  if (num == null) return '-'
  const n = Number(num)
  if (isNaN(n)) return '-'
  return Math.abs(n).toLocaleString('zh-CN', { minimumFractionDigits: 0, maximumFractionDigits: 0 })
}

// ── Batch2 增强点 1：关注计划自动同步徽章判定 ──────────────────────────
/** 判断关注基金是否在 7 天内被自动同步为 bought */
function isAutoSynced(item) {
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

/** 打开文章分析弹窗 */
function openArticleDialog() {
  articleUrl.value = ''
  showArticleDialog.value = true
}

/** 关闭文章分析弹窗 */
function closeArticleDialog() {
  if (analyzingArticle.value) return
  showArticleDialog.value = false
  articleUrl.value = ''
}

/** 抓取文章并提取投资趋势 */
async function handleAnalyzeArticle() {
  const url = articleUrl.value.trim()
  if (!url) {
    useToast().showToast('请输入文章 URL', 'error')
    return
  }
  if (!/^https?:\/\//i.test(url)) {
    useToast().showToast('URL 必须以 http:// 或 https:// 开头', 'error')
    return
  }
  if (analyzingArticle.value) return
  analyzingArticle.value = true
  try {
    const { data } = await analyzeArticleTrends(url)
    const total = data?.total || 0
    const newCount = data?.new || 0
    useToast().showToast(
      `分析完成：提取 ${total} 个趋势，新增 ${newCount} 个`,
      'success'
    )
    showArticleDialog.value = false
    articleUrl.value = ''
    await loadEvents()
  } catch (e) {
    const msg = e?.response?.data?.detail || e?.response?.data?.message || '分析失败'
    useToast().showToast(msg, 'error')
  } finally {
    analyzingArticle.value = false
  }
}

async function loadAccuracy() {
  try {
    const { data } = await getEventRadarAccuracy()
    accuracy.value = data
  } catch { /* 静默失败 */ }
}

async function handleAddToWatchlist(fund, evt) {
  try {
    // 把来源事件和候选理由写入 notes，让用户知道"为什么关注这只基金"
    const eventTitle = evt?.title ? `来自事件「${evt.title}」` : '来自事件雷达候选基金'
    const reason = fund.match_reason ? `；${fund.match_reason}` : ''
    const direction = evt?.direction ? `；影响：${directionLabel(evt.direction).text}` : ''
    await addToWatchlist({
      fund_code: fund.fund_code,
      fund_name: fund.fund_name,
      index_code: fund.index_code || '',
      index_name: fund.index_name || '',
      notes: `${eventTitle}${direction}${reason}`,
    })
    useToast().showToast(`已加入关注：${fund.fund_name}`, 'success')
    if (activeTab.value === 'watchlist') await loadWatchlist()
  } catch (e) {
    const msg = e?.response?.data?.detail || e?.response?.data?.message || '加入失败'
    useToast().showToast(msg, 'error')
  }
}

async function handleRemoveWatchlist(item) {
  if (!confirm(`确认从关注列表移除「${item.fund_name}」？`)) return
  try {
    await removeWatchlistItem(item.id)
    useToast().showToast('已移除', 'success')
    await loadWatchlist()
  } catch (e) {
    useToast().showToast('移除失败', 'error')
  }
}

/** 标记关注基金为已买入（支持可选 payload：买入价/止盈/止损） */
async function handleMarkBought(item, payload = null) {
  if (item.status === 'bought' && !payload) return
  try {
    const { data } = await markWatchlistBought(item.id, payload)
    item.status = 'bought'
    if (payload) {
      // 同步本地字段，避免再查一次
      item.entry_price = payload.entry_price
      item.entry_date = payload.entry_date
      item.target_profit_pct = payload.target_profit_pct
      item.stop_loss_pct = payload.stop_loss_pct
    }
    useToast().showToast(
      payload ? `已记录「${item.fund_name}」上车信息` : `已标记「${item.fund_name}」为已买入`,
      'success'
    )
  } catch (e) {
    const msg = e?.response?.data?.detail || e?.response?.data?.message || '标记失败'
    useToast().showToast(msg, 'error')
  }
}

// ── Batch1 增强点 1：上车设置弹窗（entry_price/target_profit_pct/stop_loss_pct） ──
const showEntryDialog = ref(false)
const entryFormItem = ref(null) // 当前编辑的 watchlist item
const entryForm = ref({ entry_price: '', entry_date: '', target_profit_pct: '', stop_loss_pct: '' })
const savingEntry = ref(false)

/** 打开上车设置弹窗 */
function openEntryDialog(item) {
  entryFormItem.value = item
  const today = new Date().toISOString().slice(0, 10)
  entryForm.value = {
    entry_price: item.entry_price || item.current_nav || '',
    entry_date: item.entry_date || today,
    target_profit_pct: item.target_profit_pct ?? '',
    stop_loss_pct: item.stop_loss_pct ?? '',
  }
  showEntryDialog.value = true
}

/** 关闭上车设置弹窗 */
function closeEntryDialog() {
  if (savingEntry.value) return
  showEntryDialog.value = false
  entryFormItem.value = null
}

/** 提交上车设置 */
async function handleEntrySubmit() {
  const item = entryFormItem.value
  if (!item) return
  const ep = parseFloat(entryForm.value.entry_price)
  if (!ep || ep <= 0) {
    useToast().showToast('请输入有效的买入价', 'error')
    return
  }
  const tp = entryForm.value.target_profit_pct !== '' ? parseFloat(entryForm.value.target_profit_pct) : null
  const sl = entryForm.value.stop_loss_pct !== '' ? parseFloat(entryForm.value.stop_loss_pct) : null
  if (tp !== null && tp <= 0) {
    useToast().showToast('止盈百分比必须大于 0', 'error')
    return
  }
  if (sl !== null && sl <= 0) {
    useToast().showToast('止损百分比必须大于 0', 'error')
    return
  }
  if (savingEntry.value) return
  savingEntry.value = true
  try {
    const payload = {
      entry_price: ep,
      entry_date: entryForm.value.entry_date,
    }
    if (tp !== null) payload.target_profit_pct = tp
    if (sl !== null) payload.stop_loss_pct = sl
    // 已买入的走 updateWatchlistEntry，未买入的走 markWatchlistBought 带 payload
    if (item.status === 'bought') {
      await updateWatchlistEntry(item.id, payload)
    } else {
      await handleMarkBought(item, payload)
    }
    // 拉一次退出状态，让前端立即显示盈亏
    try {
      const { data: exitData } = await getWatchlistExitStatus(item.id)
      if (exitData) {
        item.pnl_pct = exitData.pnl_pct
        item.exit_signal = exitData.exit_signal
        item.exit_signal_reason = exitData.exit_signal_reason
      }
    } catch (_) { /* 忽略，主流程已成功 */ }
    showEntryDialog.value = false
    entryFormItem.value = null
  } catch (e) {
    const msg = e?.response?.data?.detail || e?.response?.data?.message || '保存失败'
    useToast().showToast(msg, 'error')
  } finally {
    savingEntry.value = false
  }
}

// ── 添加关注弹窗 ──
const showAddWatchlistDialog = ref(false)
const addWatchlistForm = ref({ fund_code: '', target_price: '' })
const addingWatchlist = ref(false)

/** 打开添加关注弹窗 */
function openAddWatchlistDialog() {
  addWatchlistForm.value = { fund_code: '', target_price: '' }
  showAddWatchlistDialog.value = true
}

/** 关闭添加关注弹窗 */
function closeAddWatchlistDialog() {
  if (addingWatchlist.value) return
  showAddWatchlistDialog.value = false
}

/** 提交添加关注 */
async function handleAddWatchlistSubmit() {
  const code = addWatchlistForm.value.fund_code.trim()
  if (!code) {
    useToast().showToast('请输入基金代码', 'error')
    return
  }
  if (addingWatchlist.value) return
  addingWatchlist.value = true
  try {
    const payload = { fund_code: code }
    const tp = addWatchlistForm.value.target_price
    if (tp !== '' && tp != null) payload.target_price = parseFloat(tp)
    await addToWatchlist(payload)
    useToast().showToast(`已添加关注：${code}`, 'success')
    showAddWatchlistDialog.value = false
    await loadWatchlist()
  } catch (e) {
    const msg = e?.response?.data?.detail || e?.response?.data?.message || '添加失败'
    useToast().showToast(msg, 'error')
  } finally {
    addingWatchlist.value = false
  }
}

/** 打开目标编辑面板 */
function startEditTarget(item) {
  editingId.value = item.id
  editForm.value = {
    target_price: item.target_price || '',
    target_percentile: item.target_percentile || '',
  }
}

/** 保存目标价/分位 */
async function saveTarget(item) {
  try {
    const payload = {}
    if (editForm.value.target_price !== '') payload.target_price = parseFloat(editForm.value.target_price)
    if (editForm.value.target_percentile !== '') payload.target_percentile = parseFloat(editForm.value.target_percentile)
    await updateWatchlistItem(item.id, payload)
    useToast().showToast('目标已更新', 'success')
    editingId.value = null
    await loadWatchlist()
  } catch (e) {
    useToast().showToast('保存失败: ' + (e?.response?.data?.detail || e.message), 'error')
  }
}

async function handleRefreshNavs() {
  if (refreshingNavs.value) return
  refreshingNavs.value = true
  try {
    const { data } = await refreshWatchlistNavs()
    const ok = (data || []).filter(r => !r.error).length
    const fail = (data || []).filter(r => r.error).length
    useToast().showToast(`刷新完成：成功 ${ok}，失败 ${fail}`, ok > 0 ? 'success' : 'warning')
    await loadWatchlist()
  } catch (e) {
    useToast().showToast('刷新净值失败', 'error')
  } finally {
    refreshingNavs.value = false
  }
}

async function handleScanWatchlistSignals() {
  if (scanningWatchlist.value) return
  scanningWatchlist.value = true
  try {
    const { data } = await triggerWatchlistScan()
    useToast().showToast(
      `扫描完成：扫描 ${data?.watchlist_scanned || 0} 只基金，生成 ${data?.alerts_created || 0} 个信号`,
      'success'
    )
    await loadWatchlist()
  } catch (e) {
    useToast().showToast('扫描失败', 'error')
  } finally {
    scanningWatchlist.value = false
  }
}

function parseVerification(str) {
  try { return JSON.parse(str || '') } catch { return null }
}

function verificationLabel(status) {
  return { correct: '验证正确', wrong: '验证偏差', flat: '波动平淡' }[status] || status
}

function verificationIcon(status) {
  return { correct: 'check-circle', wrong: 'alert-triangle', flat: 'info' }[status] || 'info'
}

function relevanceLabel(r) {
  return {
    holding_impact: '持仓影响',
    watchlist_impact: '关注机会',
    opportunity: '建仓机会',
    market_watch: '市场关注',
  }[r] || r
}

function statusLabel(s) {
  return { upcoming: '即将到来', imminent: '临近', materialized: '已落地', expired: '已过期' }[s] || s
}

function parseJsonArray(str) {
  try { return JSON.parse(str || '[]') } catch { return [] }
}

function formatDate(dateStr) {
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

function directionLabel(d) {
  if (d === 'positive') return { text: '利好', class: 'dir-positive' }
  if (d === 'negative') return { text: '利空', class: 'dir-negative' }
  return { text: '中性', class: 'dir-neutral' }
}

/** 加载组合智能数据（7维体检+大师组合版+风险度量） */
async function loadPortfolioIntelligence() {
  loadingPortfolio.value = true
  try {
    const [healthRes, riskRes] = await Promise.allSettled([
      getPortfolioHealthReport(),
      getPortfolioRiskMetrics(),
    ])
    if (healthRes.status === 'fulfilled') portfolioData.value = healthRes.value?.data || null
    if (riskRes.status === 'fulfilled') portfolioRisk.value = riskRes.value?.data || null
  } catch { /* 静默失败 */ } finally {
    loadingPortfolio.value = false
  }
}

// ── 大师决策回测 ──
// 胜率统计 getMasterAccuracyStats
const masterBacktestStats = ref(null)
// 历史决策 getMasterDecisionHistory
const masterHistory = ref([])
const loadingMasterBacktest = ref(false)
const verifyingMasters = ref(false)
// 历史列表大师筛选
const masterHistoryFilter = ref('all')

function masterIcon(masterKey) {
  return MASTER_ICONS[masterKey] || '🎯'
}

/** 胜率颜色：≥60%绿 / 40-60%黄 / <40%红 / 无数据灰 */
function winRateColor(rate) {
  if (rate == null || isNaN(rate)) return '#9e9e9e'
  if (rate >= 60) return '#4caf50'
  if (rate >= 40) return '#ff9800'
  return '#f44336'
}

/** 验证结果 → {label, icon, color}（correct绿/wrong红/flat灰/待验证橙） */
function verifyResultMeta(result) {
  return {
    correct: { label: '正确', icon: '✓', color: '#4caf50' },
    wrong: { label: '偏差', icon: '✗', color: '#f44336' },
    flat: { label: '平淡', icon: '—', color: '#9e9e9e' },
  }[result] || { label: '待验证', icon: '⏳', color: '#ff9800' }
}

/** 历史决策可选大师列表（来自统计 per_master） */
const masterFilterOptions = computed(() => {
  return (masterBacktestStats.value?.per_master || []).map(m => ({ key: m.master_key, name: m.master_name }))
})

/** 按大师筛选后的历史决策 */
const filteredMasterHistory = computed(() => {
  if (masterHistoryFilter.value === 'all') return masterHistory.value
  return masterHistory.value.filter(d => d.master_key === masterHistoryFilter.value)
})

/** 加载大师决策回测数据（胜率统计 + 历史列表） */
async function loadMasterBacktest() {
  loadingMasterBacktest.value = true
  try {
    const [statsRes, historyRes] = await Promise.allSettled([
      getMasterAccuracyStats(90),
      getMasterDecisionHistory({ days: 90, limit: 50 }),
    ])
    if (statsRes.status === 'fulfilled') masterBacktestStats.value = statsRes.value?.data || null
    if (historyRes.status === 'fulfilled') masterHistory.value = historyRes.value?.data?.history || []
  } catch { /* 静默失败 */ } finally {
    loadingMasterBacktest.value = false
  }
}

/** 手动触发T+N验证后重新加载 */
async function verifyMasters() {
  if (verifyingMasters.value) return
  verifyingMasters.value = true
  try {
    const { data } = await triggerMasterVerification()
    useToast().showToast(
      `验证完成：T+7 已验证 ${data?.stats?.verified_7d ?? 0} 条`,
      'success'
    )
    await loadMasterBacktest()
  } catch (e) {
    useToast().showToast('触发验证失败', 'error')
  } finally {
    verifyingMasters.value = false
  }
}

// Tab 切换时按需加载数据
const watchlistDataLoaded = ref(false)
const portfolioDataLoaded = ref(false)

function handleTabChange(tab) {
  activeTab.value = tab
  if (tab === 'watchlist' && !watchlistDataLoaded.value) {
    watchlistDataLoaded.value = true
    // 加载买入评分和体检报告
    if (watchlist.value.length) {
      loadBuyScores()
      loadFundReports()
    }
  }
  if (tab === 'watchlist' && !portfolioDataLoaded.value) {
    portfolioDataLoaded.value = true
    // 加载组合智能和大师回测
    loadPortfolioIntelligence()
    loadMasterBacktest()
  }
}

onMounted(() => {
  loadEvents()
  loadAccuracy()
  
  // 延迟加载非核心数据，提升首屏加载速度
  setTimeout(() => {
    loadWatchlist()
  }, 300)
})

// 事件雷达联动：点击关联关注基金 → 跳转到关注机会 Tab 并滚动到对应卡片
watch(scrollToFundCode, async (code) => {
  if (!code) return
  activeTab.value = 'watchlist'
  expandedCards.value = { ...expandedCards.value, [code]: true }
  await nextTick()
  setTimeout(() => {
    const el = document.querySelector(`.wl-card[data-fund-code="${code}"]`)
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' })
      el.classList.add('wl-card-highlight')
      setTimeout(() => el.classList.remove('wl-card-highlight'), 2000)
    }
  }, 300)
})
</script>

<template>
  <div class="event-radar-page">
    <!-- 顶部标题区 -->
    <div class="page-header">
      <div class="header-left">
        <div class="page-title-row">
          <Icon name="satellite" size="22" class="page-title-icon" />
          <h2 class="page-title">机会雷达</h2>
        </div>
        <p class="page-subtitle">发现机会 → 跟踪观察 → 上车信号 → 落地验证，形成投资闭环</p>
        <p v-if="lastScanTime" class="last-scan-time">
          <Icon name="clock" size="12" />
          上次扫描：{{ lastScanTime }}
        </p>
      </div>
      <div class="header-actions">
        <button v-if="activeTab === 'events'" class="btn btn-secondary verify-btn" @click="handleVerify" :disabled="verifying" title="验证已落地事件的方向预测">
          <Icon :name="verifying ? 'spinner' : 'check-circle'" size="14" :class="{ spinning: verifying }" />
          <span>{{ verifying ? '验证中...' : '落地验证' }}</span>
        </button>
        <button v-if="activeTab === 'events'" class="btn btn-secondary analyze-btn" @click="openArticleDialog" :disabled="analyzingArticle" title="抓取文章并提取投资趋势">
          <Icon :name="analyzingArticle ? 'spinner' : 'file-text'" size="14" :class="{ spinning: analyzingArticle }" />
          <span>{{ analyzingArticle ? '分析中...' : '分析文章' }}</span>
        </button>
        <button v-if="activeTab === 'events'" class="btn btn-primary scan-btn" @click="handleScan" :disabled="scanning">
          <Icon :name="scanning ? 'spinner' : 'scan-search'" size="14" :class="{ spinning: scanning }" />
          <span>{{ scanning ? '扫描中...' : '立即扫描' }}</span>
        </button>
        <button v-if="activeTab === 'watchlist'" class="btn btn-secondary verify-btn" @click="loadBuyScores" :disabled="refreshingScores" title="重新计算买入时机评分">
          <Icon :name="refreshingScores ? 'spinner' : 'gauge'" size="14" :class="{ spinning: refreshingScores }" />
          <span>{{ refreshingScores ? '评分中...' : '刷新评分' }}</span>
        </button>
        <button v-if="activeTab === 'watchlist'" class="btn btn-secondary verify-btn" @click="handleRefreshNavs" :disabled="refreshingNavs">
          <Icon :name="refreshingNavs ? 'spinner' : 'refresh-cw'" size="14" :class="{ spinning: refreshingNavs }" />
          <span>{{ refreshingNavs ? '刷新中...' : '刷新净值' }}</span>
        </button>
        <button v-if="activeTab === 'watchlist'" class="btn btn-primary scan-btn" @click="handleScanWatchlistSignals" :disabled="scanningWatchlist">
          <Icon :name="scanningWatchlist ? 'spinner' : 'zap'" size="14" :class="{ spinning: scanningWatchlist }" />
          <span>{{ scanningWatchlist ? '扫描中...' : '信号扫描' }}</span>
        </button>
      </div>
    </div>

    <!-- 主 Tab 切换 -->
    <div class="main-tabs">
      <button class="main-tab" :class="{ active: activeTab === 'events' }" @click="activeTab = 'events'">
        <Icon name="satellite" size="14" />
        <span>事件雷达</span>
        <span v-if="stats.total" class="tab-badge">{{ stats.total }}</span>
      </button>
      <button class="main-tab" :class="{ active: activeTab === 'watchlist' }" @click="activeTab = 'watchlist'">
        <Icon name="bookmark" size="14" />
        <span>关注机会</span>
        <span v-if="watchlistStats.total" class="tab-badge tab-badge-orange">{{ watchlistStats.total }}</span>
      </button>
      <button class="main-tab" :class="{ active: activeTab === 'verification' }" @click="activeTab = 'verification'">
        <Icon name="check-circle" size="14" />
        <span>落地验证</span>
        <span v-if="accuracy?.overall?.total" class="tab-badge tab-badge-green">{{ accuracy.overall.total }}</span>
      </button>
    </div>

    <!-- ════ Tab 1：事件雷达 ════ -->
    <template v-if="activeTab === 'events'">
      <!-- 统计卡片 -->
      <div class="stats-row">
        <div class="stat-card stat-all" @click="activeFilter = 'all'">
          <div class="stat-value">{{ stats.total }}</div>
          <div class="stat-label">全部事件</div>
        </div>
        <div class="stat-card stat-holding" @click="activeFilter = 'holding_impact'">
          <div class="stat-value">{{ stats.holding }}</div>
          <div class="stat-label">持仓影响</div>
        </div>
        <div class="stat-card stat-watchlist-hit" @click="activeFilter = 'watchlist_impact'">
          <div class="stat-value">{{ stats.watchlistHit }}</div>
          <div class="stat-label">关注机会</div>
        </div>
        <div class="stat-card stat-opportunity" @click="activeFilter = 'opportunity'">
          <div class="stat-value">{{ stats.opportunity }}</div>
          <div class="stat-label">建仓机会</div>
        </div>
        <div class="stat-card stat-watch" @click="activeFilter = 'market_watch'">
          <div class="stat-value">{{ stats.watch }}</div>
          <div class="stat-label">市场关注</div>
        </div>
      </div>

      <!-- 筛选栏 -->
      <div class="filter-bar">
        <div class="filter-tabs">
          <button
            v-for="f in FILTERS"
            :key="f.key"
            class="filter-tab"
            :class="{ active: activeFilter === f.key }"
            @click="activeFilter = f.key"
          >
            <Icon :name="f.icon" size="13" />
            <span>{{ f.label }}</span>
          </button>
        </div>
        <div class="status-tabs">
          <button
            v-for="s in STATUS_TABS"
            :key="s.key"
            class="status-tab"
            :class="{ active: activeStatus === s.key }"
            @click="activeStatus = s.key"
          >
            {{ s.label }}
          </button>
        </div>
      </div>

      <!-- 事件时间线 -->
      <div class="events-timeline">
        <div v-if="loading" class="empty-state">
          <Icon name="spinner" size="24" class="spinning" />
          <span>加载中...</span>
        </div>
        <div v-else-if="filteredEvents.length === 0" class="empty-state">
          <Icon name="check-circle" size="28" class="empty-icon" />
          <span class="empty-text">暂无相关事件</span>
          <span class="empty-hint">点击右上角「立即扫描」从今日新闻中提取前瞻事件</span>
        </div>
        <template v-else>
          <div
            v-for="(evt, idx) in filteredEvents"
            :key="evt.event_id"
            class="event-card"
            :class="[`relevance-${evt.relevance_to_user}`, `status-${evt.status}`, { 'event-card-trend': isTrendEvent(evt) }]"
          >
            <!-- 左侧时间标记 -->
            <div class="event-time-col">
              <div class="event-date-badge">
                <template v-if="isTrendEvent(evt)">趋势</template>
                <template v-else>{{ formatDate(evt.expected_date) }}</template>
              </div>
              <div class="event-date-sub">{{ evt.expected_date || (isTrendEvent(evt) ? timeFrameLabel(evt.time_frame) : '') }}</div>
              <div v-if="idx < filteredEvents.length - 1" class="timeline-line"></div>
            </div>

            <!-- 右侧内容 -->
            <div class="event-content-col">
              <div class="event-header">
                <div class="event-title-row">
                  <h3 class="event-title">{{ evt.title }}</h3>
                  <span
                    v-if="isTrendEvent(evt)"
                    class="event-type-tag event-type-trend"
                    title="中长期行业趋势"
                  >趋势</span>
                  <span v-else class="event-type-tag">{{ {
                    policy: '政策', industry: '行业', earnings: '财报',
                    capital: '资本', macro: '宏观', theme: '主题'
                  }[evt.event_type] || evt.event_type }}</span>
                </div>
                <div class="event-meta-row">
                  <span
                    class="relevance-tag"
                    :class="`tag-${evt.relevance_to_user}`"
                  >
                    {{ relevanceLabel(evt.relevance_to_user) }}
                  </span>
                  <span v-if="isTrendEvent(evt)" class="time-frame-tag" :class="`tf-${evt.time_frame}`">
                    {{ timeFrameLabel(evt.time_frame) }}
                  </span>
                  <span v-else class="status-tag" :class="`st-${evt.status}`">
                    {{ statusLabel(evt.status) }}
                  </span>
                  <span v-if="evt.direction" class="direction-tag" :class="directionLabel(evt.direction).class">
                    {{ directionLabel(evt.direction).text }}
                  </span>
                  <!-- Batch1 增强点 3：影响预估徽章（幅度 + 方向 + 持续期） -->
                  <span
                    v-if="evt.expected_impact_pct != null || evt.impact_direction"
                    class="impact-est-tag"
                    :class="impactDirectionLabel(evt.impact_direction).cls"
                    :title="evt.impact_duration ? `持续期：${impactDurationLabel(evt.impact_duration)}` : ''"
                  >
                    <Icon name="chart" size="11" />
                    预估 {{ impactPctText(evt.expected_impact_pct) }} {{ impactDirectionLabel(evt.impact_direction).text }}
                  </span>
                  <span
                    class="confidence-tag"
                    :class="{
                      'confidence-calibrated': evt.original_confidence != null && Math.abs((evt.original_confidence || 0) - (evt.confidence || 0)) > 0.001,
                      'confidence-decayed': evt.effective_confidence != null && Math.abs((evt.effective_confidence || 0) - (evt.confidence || 0)) > 0.001
                    }"
                    :title="evt.effective_confidence != null && Math.abs((evt.effective_confidence || 0) - (evt.confidence || 0)) > 0.001 ? '该事件过期未验证，已自动降权' : ''"
                  >
                    <template v-if="evt.original_confidence != null && Math.abs((evt.original_confidence || 0) - (evt.confidence || 0)) > 0.001">
                      原始 {{ Math.round((evt.original_confidence || 0) * 100) }}% → 校准 {{ Math.round((evt.confidence || 0) * 100) }}%
                    </template>
                    <template v-else>
                      置信度 {{ Math.round((evt.confidence || 0) * 100) }}%
                    </template>
                    <!-- Batch2 增强点 3：时间衰减后的有效置信度 -->
                    <template v-if="evt.effective_confidence != null && Math.abs((evt.effective_confidence || 0) - (evt.confidence || 0)) > 0.001">
                      ⚠️ 有效 {{ Math.round((evt.effective_confidence || 0) * 100) }}%
                    </template>
                  </span>
                  <!-- 已有缓存的影响分析标记 -->
                  <span
                    v-if="hasCachedImpactAnalysis(evt)"
                    class="impact-cached-tag"
                    :title="`已于 ${formatShortDate(evt.impact_analyzed_at)} 生成影响分析`"
                  >
                    <Icon name="check-circle" size="11" />
                    已分析
                  </span>
                  <!-- 验证结果标签 -->
                  <span
                    v-if="parseVerification(evt.verification_result)"
                    class="verify-tag"
                    :class="`verify-${parseVerification(evt.verification_result).status}`"
                  >
                    <Icon :name="verificationIcon(parseVerification(evt.verification_result).status)" size="11" />
                    {{ verificationLabel(parseVerification(evt.verification_result).status) }}
                    {{ parseVerification(evt.verification_result).change_pct > 0 ? '+' : '' }}{{ parseVerification(evt.verification_result).change_pct }}%
                  </span>
                </div>
              </div>

              <p v-if="evt.summary" class="event-summary">{{ evt.summary }}</p>

              <!-- 趋势证据 -->
              <div v-if="isTrendEvent(evt) && evt.evidence" class="event-section trend-evidence">
                <span class="section-label">证据</span>
                <div class="evidence-text">{{ evt.evidence }}</div>
              </div>

              <!-- 影响板块 -->
              <div v-if="parseJsonArray(evt.affected_sectors).length" class="event-section">
                <span class="section-label">影响板块</span>
                <div class="tag-list">
                  <span v-for="s in parseJsonArray(evt.affected_sectors)" :key="s" class="chip chip-sector">{{ s }}</span>
                </div>
              </div>

              <!-- 影响主题 -->
              <div v-if="parseJsonArray(evt.affected_themes).length" class="event-section">
                <span class="section-label">主题标签</span>
                <div class="tag-list">
                  <span v-for="t in parseJsonArray(evt.affected_themes)" :key="t" class="chip chip-theme">#{{ t }}</span>
                </div>
              </div>

              <!-- 关联持仓/关注 -->
              <div v-if="parseJsonArray(evt.matched_holdings).length" class="event-section">
                <span class="section-label">关联持仓/关注</span>
                <div class="holding-list">
                  <div v-for="h in parseJsonArray(evt.matched_holdings)" :key="h.fund_code" class="holding-item" :class="{ 'holding-item-watchlist': h.match_type === 'watchlist' }">
                    <Icon :name="h.match_type === 'watchlist' ? 'bookmark' : 'briefcase'" size="13" />
                    <span
                      class="holding-name"
                      :class="{ 'holding-name-clickable': h.match_type === 'watchlist' }"
                      @click="h.match_type === 'watchlist' && scrollToFundCode && (scrollToFundCode = h.fund_code)"
                      :title="h.match_type === 'watchlist' ? '点击查看关注详情' : ''"
                    >{{ h.fund_name }}</span>
                    <span class="holding-reason">{{ h.match_reason }}</span>
                  </div>
                </div>
              </div>

              <!-- 候选建仓基金 -->
              <div v-if="parseJsonArray(evt.candidate_funds).length" class="event-section">
                <span class="section-label">候选建仓基金</span>
                <div class="candidate-list">
                  <div v-for="c in parseJsonArray(evt.candidate_funds)" :key="c.fund_code" class="candidate-item">
                    <Icon name="trending-up" size="13" class="candidate-icon" />
                    <div class="candidate-info">
                      <span class="candidate-name">{{ c.fund_name }}</span>
                      <span class="candidate-reason">{{ c.match_reason }}</span>
                    </div>
                    <button class="btn-watch-add" @click="handleAddToWatchlist(c, evt)" title="加入关注列表">
                      <Icon name="bookmark-plus" size="13" />
                      <span>关注</span>
                    </button>
                  </div>
                </div>
              </div>

              <!-- Batch1 增强点 3：事件深度解读（LLM 分析，结合用户持仓） -->
              <div class="event-impact-section">
                <!-- Batch2 增强点 2：金额影响按钮 + 展开列表（纯计算，无 LLM） -->
                <button
                  v-if="evt.expected_impact_pct != null"
                  class="btn-event-amount"
                  :disabled="impactAmountMap[evt.event_id]?.loading"
                  @click="handleGetImpactAmount(evt)"
                >
                  <Icon
                    :name="impactAmountMap[evt.event_id]?.loading ? 'spinner' : 'circle-dollar-sign'"
                    size="12"
                  />
                  <span>{{
                    impactAmountMap[evt.event_id]?.loading ? '计算中...' :
                    impactAmountMap[evt.event_id]?.data ? '查看金额影响' :
                    '估算金额影响'
                  }}</span>
                  <span
                    v-if="impactAmountMap[evt.event_id]?.data && impactAmountMap[evt.event_id].data.total_impact_amount !== 0"
                    class="amount-summary"
                    :class="impactAmountMap[evt.event_id].data.total_impact_amount > 0 ? 'amount-positive' : 'amount-negative'"
                  >
                    ¥{{ formatAmount(impactAmountMap[evt.event_id].data.total_impact_amount) }}
                  </span>
                </button>
                <!-- 金额影响展开/收起按钮 -->
                <button
                  v-if="impactAmountMap[evt.event_id]?.data?.affected_holdings?.length"
                  class="btn-impact-toggle"
                  @click="toggleImpactAmountExpand(evt.event_id)"
                >
                  <Icon :name="expandedImpactAmountEvents[evt.event_id] ? 'chevron-up' : 'chevron-down'" size="11" />
                  {{ expandedImpactAmountEvents[evt.event_id] ? '收起' : '展开' }}
                </button>
                <!-- 金额影响受影响持仓列表 -->
                <div
                  v-if="expandedImpactAmountEvents[evt.event_id] && impactAmountMap[evt.event_id]?.data?.affected_holdings?.length"
                  class="event-amount-content"
                >
                  <div class="amount-row amount-header">
                    <span>基金</span>
                    <span>占比</span>
                    <span>市值</span>
                    <span>影响金额</span>
                  </div>
                  <div
                    v-for="h in impactAmountMap[evt.event_id].data.affected_holdings"
                    :key="h.fund_code"
                    class="amount-row"
                  >
                    <span class="amount-name">{{ h.fund_name || h.fund_code }}</span>
                    <span>{{ h.weight != null ? Number(h.weight).toFixed(1) + '%' : '-' }}</span>
                    <span>¥{{ formatAmount(h.holding_value) }}</span>
                    <span :class="h.impact_amount > 0 ? 'amount-positive' : 'amount-negative'">
                      {{ h.impact_amount > 0 ? '+' : '' }}¥{{ formatAmount(h.impact_amount) }}
                    </span>
                  </div>
                </div>
                <!-- 金额影响原因说明（空数据时） -->
                <div v-if="impactAmountMap[evt.event_id]?.data?.reason" class="event-amount-reason">
                  <Icon name="info" size="11" />
                  {{ impactAmountMap[evt.event_id].data.reason }}
                </div>

                <button
                  class="btn-event-impact"
                  :disabled="eventImpactMap[evt.event_id]?.loading"
                  @click="handleAnalyzeImpact(evt)"
                >
                  <Icon
                    :name="eventImpactMap[evt.event_id]?.loading ? 'spinner' : 'lightbulb'"
                    size="12"
                  />
                  <span>{{
                    eventImpactMap[evt.event_id]?.loading ? '分析中...' :
                    eventImpactMap[evt.event_id]?.analysis || evt.impact_analysis ? '查看影响分析' :
                    '深度解读影响'
                  }}</span>
                </button>
                <span v-if="hasCachedImpactAnalysis(evt) && !eventImpactMap[evt.event_id]?.analysis" class="impact-cached-hint">
                  缓存于 {{ formatShortDate(evt.impact_analyzed_at) }}
                </span>
                <!-- 展开切换按钮（已有分析结果时） -->
                <button
                  v-if="eventImpactMap[evt.event_id]?.analysis || evt.impact_analysis"
                  class="btn-impact-toggle"
                  @click="toggleImpactExpand(evt.event_id)"
                >
                  <Icon :name="expandedImpactEvents[evt.event_id] ? 'chevron-up' : 'chevron-down'" size="11" />
                  {{ expandedImpactEvents[evt.event_id] ? '收起' : '展开' }}
                </button>
                <!-- 错误提示 -->
                <div v-if="eventImpactMap[evt.event_id]?.error" class="event-impact-error">
                  <Icon name="info" size="11" />
                  {{ eventImpactMap[evt.event_id].error }}
                </div>
                <!-- 影响分析结果展示 -->
                <div
                  v-if="expandedImpactEvents[evt.event_id] && (eventImpactMap[evt.event_id]?.analysis || evt.impact_analysis)"
                  class="event-impact-content"
                  v-html="renderMarkdown((eventImpactMap[evt.event_id]?.analysis || evt.impact_analysis))"
                ></div>
              </div>
            </div>
          </div>
        </template>
      </div>
    </template>

    <!-- ════ Tab 2：关注机会 ════ -->
    <template v-if="activeTab === 'watchlist'">
      <!-- ════ 今日操作建议 ════ -->
      <div v-if="watchlist.length" class="daily-advice-banner">
        <div class="da-content">
          <span class="da-icon">💡</span>
          <span class="da-text" v-if="watchlistStats.canBuy > 0">
            <strong>{{ watchlistStats.canBuy }}</strong> 只基金可上车，建议优先关注<strong>「{{ firstCanBuyFund }}」</strong>
            <span v-if="firstCanBuyInfo">（估值分位{{ firstCanBuyInfo.percentile }}%，距上车价{{ firstCanBuyInfo.distance }}）</span>
          </span>
          <span class="da-text" v-else-if="watchlistStats.approaching > 0">
            <strong>{{ watchlistStats.approaching }}</strong> 只接近上车，近期关注
          </span>
          <span class="da-text" v-else>暂无上车信号，继续等待</span>
        </div>
        <div class="da-tags">
          <span class="da-tag da-tag-green" :class="{ active: watchlistFilter === 'canBuy' }" @click="toggleWatchlistFilter('canBuy')">🟢 可上车 {{ watchlistStats.canBuy }}</span>
          <span class="da-tag da-tag-yellow" :class="{ active: watchlistFilter === 'approaching' }" @click="toggleWatchlistFilter('approaching')">🟡 接近 {{ watchlistStats.approaching }}</span>
          <span class="da-tag da-tag-red" :class="{ active: watchlistFilter === 'waiting' }" @click="toggleWatchlistFilter('waiting')">🔴 等待 {{ watchlistStats.waiting }}</span>
          <span v-if="watchlistStats.noData" class="da-tag da-tag-gray" :class="{ active: watchlistFilter === 'noData' }" @click="toggleWatchlistFilter('noData')">⚪ 数据不足 {{ watchlistStats.noData }}</span>
          <!-- Batch1 增强点 1：退出信号统计（止盈/止损） -->
          <span v-if="watchlistStats.profitTargets" class="da-tag da-tag-profit" title="已触发止盈目标的持仓">
            🎯 止盈 {{ watchlistStats.profitTargets }}
          </span>
          <span v-if="watchlistStats.stopLosses" class="da-tag da-tag-stop" title="已触发止损线的持仓">
            ⛔ 止损 {{ watchlistStats.stopLosses }}
          </span>
        </div>
      </div>
      <!-- ════ 组合智能面板 ════ -->
      <div class="collapsible-panel">
        <div class="collapsible-header" @click="expandedPortfolio = !expandedPortfolio">
          <div class="collapsible-header-left">
            <Icon :name="expandedPortfolio ? 'chevron-down' : 'chevron-right'" size="14" />
            <Icon name="activity" size="16" class="pi-header-icon" />
            <span class="collapsible-title">📊 组合智能</span>
            <span v-if="portfolioData?.health_score != null" class="collapsible-badge" :class="scoreColorClass(portfolioData.health_score)">{{ portfolioData.health_score }}分</span>
          </div>
          <button class="btn-pi-refresh" @click.stop="loadPortfolioIntelligence" :disabled="loadingPortfolio">
            <Icon :name="loadingPortfolio ? 'spinner' : 'refresh-cw'" size="12" :class="{ spinning: loadingPortfolio }" />
          </button>
        </div>
        <div v-if="expandedPortfolio" class="collapsible-body">
          <div class="portfolio-intelligence-panel">

        <!-- 加载中 -->
        <div v-if="loadingPortfolio && !portfolioData" class="pi-loading">
          <Icon name="spinner" size="20" class="spinning" />
          <span>组合智能分析中...</span>
        </div>

        <template v-else-if="portfolioData">
          <!-- 数据降级提示 -->
          <div v-if="portfolioData.data_status === 'degraded'" class="pi-degraded">
            <Icon name="alert-triangle" size="12" />
            <span>数据降级：持仓数据不足，部分指标可能不准确</span>
          </div>

          <!-- 2.1 组合风险卡片 -->
          <div v-if="portfolioRisk" class="pi-section">
            <div class="pi-section-title">
              <Icon name="trending-up" size="12" />
              <span>组合风险度量</span>
            </div>
            <div class="pi-risk-grid">
              <div class="pi-risk-card">
                <div class="pi-risk-label">年化波动率</div>
                <div class="pi-risk-value" :class="volatilityColorClass(portfolioRisk.portfolio_volatility)">
                  {{ portfolioRisk.portfolio_volatility != null ? Number(portfolioRisk.portfolio_volatility).toFixed(2) + '%' : '—' }}
                </div>
              </div>
              <div class="pi-risk-card">
                <div class="pi-risk-label">VaR 95% (1日)</div>
                <div class="pi-risk-value risk-bad">
                  {{ portfolioRisk.var_95_daily != null ? Number(portfolioRisk.var_95_daily).toFixed(2) + '%' : '—' }}
                </div>
                <div v-if="portfolioRisk.var_95_amount != null" class="pi-risk-sub">
                  ≈ ¥{{ Number(portfolioRisk.var_95_amount).toLocaleString() }}
                </div>
              </div>
              <div class="pi-risk-card">
                <div class="pi-risk-label">CVaR 95%</div>
                <div class="pi-risk-value risk-bad">
                  {{ portfolioRisk.cvar_95_daily != null ? Number(portfolioRisk.cvar_95_daily).toFixed(2) + '%' : '—' }}
                </div>
                <div v-if="portfolioRisk.cvar_95_amount != null" class="pi-risk-sub">
                  ≈ ¥{{ Number(portfolioRisk.cvar_95_amount).toLocaleString() }}
                </div>
              </div>
              <div class="pi-risk-card">
                <div class="pi-risk-label">最大回撤</div>
                <div class="pi-risk-value risk-bad">
                  {{ portfolioRisk.max_drawdown != null ? Number(portfolioRisk.max_drawdown).toFixed(2) + '%' : '—' }}
                </div>
                <div v-if="portfolioRisk.recovery_days != null" class="pi-risk-sub">
                  恢复 {{ portfolioRisk.recovery_days }} 天
                </div>
              </div>
              <div class="pi-risk-card">
                <div class="pi-risk-label">夏普比率</div>
                <div class="pi-risk-value" :class="sharpeColorClass(portfolioRisk.sharpe_ratio)">
                  {{ portfolioRisk.sharpe_ratio != null ? Number(portfolioRisk.sharpe_ratio).toFixed(2) : '—' }}
                </div>
              </div>
              <div class="pi-risk-card">
                <div class="pi-risk-label">Sortino 比率</div>
                <div class="pi-risk-value" :class="sharpeColorClass(portfolioRisk.sortino_ratio)">
                  {{ portfolioRisk.sortino_ratio != null ? Number(portfolioRisk.sortino_ratio).toFixed(2) : '—' }}
                </div>
              </div>
              <div class="pi-risk-card">
                <div class="pi-risk-label">Effective N</div>
                <div class="pi-risk-value">
                  {{ portfolioRisk.effective_n != null ? Number(portfolioRisk.effective_n).toFixed(2) : '—' }}
                </div>
              </div>
              <div class="pi-risk-card">
                <div class="pi-risk-label">平均相关系数</div>
                <div class="pi-risk-value">
                  {{ portfolioRisk.avg_correlation != null ? Number(portfolioRisk.avg_correlation).toFixed(2) : '—' }}
                </div>
              </div>
            </div>
          </div>

          <!-- 2.2 组合7维体检 -->
          <div v-if="portfolioData.portfolio_total_score != null" class="pi-section">
            <div class="pi-section-title">
              <Icon name="activity" size="12" />
              <span>组合7维体检</span>
            </div>
            <div class="pi-health-summary">
              <span class="pi-health-score" :class="scoreColorClass(portfolioData.portfolio_total_score)">
                {{ portfolioData.portfolio_total_score }}
              </span>
              <span class="pi-health-rating" :class="scoreColorClass(portfolioData.portfolio_total_score)">
                {{ scoreRatingLabel(portfolioData.portfolio_total_score) }}
              </span>
              <span
                v-if="portfolioData.portfolio_decision?.action || portfolioData.portfolio_decision?.action_label"
                class="pi-health-action"
                :class="ACTION_LABELS[portfolioData.portfolio_decision?.action]?.class"
              >
                {{ portfolioData.portfolio_decision?.action_label || ACTION_LABELS[portfolioData.portfolio_decision?.action]?.label || '—' }}
              </span>
            </div>
            <div v-if="portfolioData.portfolio_decision?.reason" class="pi-health-reason">
              {{ portfolioData.portfolio_decision.reason }}
            </div>
            <div v-if="portfolioData.portfolio_report" class="pi-health-dims">
              <div
                v-for="(dim, key) in portfolioData.portfolio_report"
                :key="key"
                class="pi-health-dim"
              >
                <div class="pi-health-dim-head">
                  <span class="pi-health-dim-label">{{ DIMENSION_LABELS[key] || dim.label || key }}</span>
                  <span class="pi-health-dim-score" :class="scoreColorClass(dim.score)">{{ dim.score }}</span>
                </div>
                <div class="pi-health-dim-bar">
                  <div
                    class="pi-health-dim-fill"
                    :class="scoreColorClass(dim.score)"
                    :style="{ width: `${dim.score}%` }"
                  ></div>
                </div>
              </div>
            </div>
          </div>

          <!-- 2.3 大师矩阵组合版 -->
          <div v-if="portfolioData.master_perspectives" class="pi-section">
            <div class="pi-section-title">
              <Icon name="target" size="12" />
              <span>大师矩阵 · 组合版</span>
            </div>
            <template v-if="portfolioData.master_perspectives.consensus">
              <div
                class="mm-consensus"
                :class="consensusClass(portfolioData.master_perspectives.consensus)"
              >
                <span class="mm-consensus-text">
                  {{ portfolioData.master_perspectives.consensus.agreement_label }}
                  · {{ portfolioData.master_perspectives.consensus.agreement_count }}
                  建议{{ portfolioData.master_perspectives.consensus.consensus_action_label }}
                </span>
              </div>
              <div
                v-if="portfolioData.master_perspectives.consensus.action_distribution"
                class="mm-dist"
              >
                <template
                  v-for="(count, action) in portfolioData.master_perspectives.consensus.action_distribution"
                  :key="action"
                >
                  <div
                    v-if="count > 0"
                    class="mm-dist-seg"
                    :style="{ flexGrow: count, background: masterActionColor(action) }"
                    :title="`${masterActionLabel(action)}: ${count}`"
                  ></div>
                </template>
              </div>
            </template>
            <div
              v-if="portfolioData.master_perspectives.masters?.length"
              class="mm-masters"
            >
              <div
                v-for="m in portfolioData.master_perspectives.masters"
                :key="m.master_key"
                class="mm-card"
                :style="{ '--mm-accent': masterAccentColor(m.master_key) }"
                @click="togglePortfolioMaster(m.master_key)"
              >
                <div class="mm-card-head">
                  <span class="mm-master-icon">{{ m.master_icon }}</span>
                  <span class="mm-master-name">{{ m.master_name }}</span>
                  <span
                    v-if="m.score == null"
                    class="mm-score-na"
                  >不适用</span>
                  <span
                    v-else
                    class="mm-score"
                    :style="{ color: masterRatingColor(m.rating) }"
                  >{{ m.score }}</span>
                  <span
                    class="mm-action-tag"
                    :style="{ background: masterActionColor(m.action) + '20', color: masterActionColor(m.action) }"
                  >{{ m.action_label }}</span>
                </div>
                <div v-if="m.reason" class="mm-reason">{{ m.reason }}</div>
                <div
                  v-if="expandedMasters[`portfolio:${m.master_key}`]"
                  class="mm-detail"
                  @click.stop
                >
                  <div v-if="m.core_philosophy" class="mm-detail-row">
                    <span class="mm-detail-label">核心理念</span>
                    <span class="mm-detail-value">{{ m.core_philosophy }}</span>
                  </div>
                  <div v-if="m.view_text" class="mm-detail-row">
                    <span class="mm-detail-label">综合视角</span>
                    <span class="mm-detail-value">{{ m.view_text }}</span>
                  </div>
                  <div
                    v-if="m.key_metrics && Object.keys(m.key_metrics).length"
                    class="mm-metrics"
                  >
                    <div
                      v-for="kv in formatMetrics(m.key_metrics)"
                      :key="kv.label"
                      class="mm-metric-row"
                    >
                      <span class="mm-metric-label">{{ kv.label }}</span>
                      <span class="mm-metric-value">{{ kv.value }}</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- 2.4 持仓明细表 -->
          <div v-if="portfolioData.holding_reports?.length" class="pi-section">
            <div class="pi-section-title">
              <Icon name="briefcase" size="12" />
              <span>持仓明细</span>
            </div>
            <div class="pi-holdings-table-wrap">
              <table class="pi-holdings-table">
                <thead>
                  <tr>
                    <th>基金名称</th>
                    <th>总分</th>
                    <th>评级</th>
                    <th>决策</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="h in portfolioData.holding_reports"
                    :key="h.fund_code"
                  >
                    <td class="pi-ht-name" :title="h.fund_code">{{ h.fund_name || h.fund_code }}</td>
                    <td class="pi-ht-score" :class="scoreColorClass(h.total_score)">{{ h.total_score ?? '—' }}</td>
                    <td>
                      <span class="pi-ht-rating" :class="ratingColorClass(h.rating)">
                        {{ ratingLabel(h.rating) }}
                      </span>
                    </td>
                    <td>
                      <span
                        v-if="h.decision?.action || h.decision?.action_label"
                        class="pi-ht-action"
                        :class="ACTION_LABELS[h.decision?.action]?.class"
                      >
                        {{ h.decision?.action_label || ACTION_LABELS[h.decision?.action]?.label || '—' }}
                      </span>
                      <span v-else class="pi-ht-mute">—</span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </template>
      </div>
        </div>
      </div>

      <!-- ════ 大师决策回测面板 ════ -->
      <div class="collapsible-panel">
        <div class="collapsible-header" @click="expandedMasterBacktest = !expandedMasterBacktest">
          <div class="collapsible-header-left">
            <Icon :name="expandedMasterBacktest ? 'chevron-down' : 'chevron-right'" size="14" />
            <Icon name="target" size="16" class="mb-header-icon" />
            <span class="collapsible-title">🎯 大师决策回测</span>
            <span v-if="masterBacktestStats?.accuracy != null" class="collapsible-badge" :class="masterBacktestStats.accuracy >= 0.6 ? 'score-good' : 'score-warn'">胜率 {{ (masterBacktestStats.accuracy * 100).toFixed(0) }}%</span>
          </div>
          <button class="btn-mb-refresh" @click.stop="verifyMasters" :disabled="verifyingMasters">
            <Icon :name="verifyingMasters ? 'spinner' : 'refresh-cw'" size="12" :class="{ spinning: verifyingMasters }" />
          </button>
        </div>
        <div v-if="expandedMasterBacktest" class="collapsible-body">
          <div class="master-backtest-panel">

        <!-- 加载中 -->
        <div v-if="loadingMasterBacktest && !masterBacktestStats" class="mb-loading">
          <Icon name="spinner" size="20" class="spinning" />
          <span>加载大师回测数据...</span>
        </div>

        <template v-else-if="masterBacktestStats">
          <!-- 总体统计 -->
          <div class="mb-overall">
            <div class="mb-overall-card">
              <div class="mb-overall-value">{{ masterBacktestStats.overall?.total ?? 0 }}</div>
              <div class="mb-overall-label">总决策数</div>
            </div>
            <div class="mb-overall-card">
              <div class="mb-overall-value">{{ masterBacktestStats.overall?.verified ?? 0 }}</div>
              <div class="mb-overall-label">已验证</div>
            </div>
            <div class="mb-overall-card">
              <div
                class="mb-overall-value"
                :style="{ color: winRateColor(masterBacktestStats.overall?.win_rate) }"
              >
                {{ masterBacktestStats.overall?.win_rate != null ? Number(masterBacktestStats.overall.win_rate).toFixed(1) + '%' : '—' }}
              </div>
              <div class="mb-overall-label">综合胜率</div>
            </div>
          </div>

          <!-- 大师胜率排名（T+7） -->
          <div v-if="masterBacktestStats.per_master?.length" class="mb-section">
            <div class="mb-section-title">
              <Icon name="chart" size="12" />
              <span>大师胜率排名（T+7）</span>
            </div>
            <div class="mb-rank-list">
              <div
                v-for="m in masterBacktestStats.per_master"
                :key="m.master_key"
                class="mb-rank-item"
                :class="{ 'mb-rank-best': masterBacktestStats.best_master?.master_key === m.master_key }"
              >
                <div class="mb-rank-master">
                  <span class="mb-rank-icon">{{ masterIcon(m.master_key) }}</span>
                  <span class="mb-rank-name">{{ m.master_name }}</span>
                  <span
                    v-if="masterBacktestStats.best_master?.master_key === m.master_key"
                    class="mb-rank-badge"
                  >最佳</span>
                </div>
                <div class="mb-rank-bar-wrap">
                  <div class="mb-rank-bar">
                    <div
                      class="mb-rank-fill"
                      :style="{
                        width: (m.win_rate_7d || 0) + '%',
                        background: `linear-gradient(90deg, ${winRateColor(m.win_rate_7d)}aa, ${winRateColor(m.win_rate_7d)})`
                      }"
                    ></div>
                  </div>
                  <span class="mb-rank-rate" :style="{ color: winRateColor(m.win_rate_7d) }">
                    {{ m.verified_7d > 0 ? Number(m.win_rate_7d).toFixed(1) + '%' : '—' }}
                  </span>
                  <span class="mb-rank-verified">验证 {{ m.verified_7d }}/{{ m.total }}</span>
                </div>
              </div>
            </div>
          </div>

          <!-- 历史决策列表 -->
          <div class="mb-section">
            <div class="mb-section-title">
              <Icon name="clipboard-list" size="12" />
              <span>历史决策</span>
              <select v-model="masterHistoryFilter" class="mb-filter-select">
                <option value="all">全部大师</option>
                <option
                  v-for="m in masterFilterOptions"
                  :key="m.key"
                  :value="m.key"
                >{{ m.name }}</option>
              </select>
            </div>
            <div v-if="filteredMasterHistory.length === 0" class="mb-empty">
              暂无大师决策记录
            </div>
            <div v-else class="mb-history-list">
              <div
                v-for="d in filteredMasterHistory"
                :key="d.id"
                class="mb-history-item"
              >
                <div class="mb-hist-head">
                  <span class="mb-hist-master">
                    <span class="mb-hist-icon">{{ masterIcon(d.master_key) }}</span>
                    <span class="mb-hist-name">{{ d.master_name }}</span>
                  </span>
                  <span
                    class="mb-hist-action"
                    :style="{ background: masterActionColor(d.action) + '20', color: masterActionColor(d.action) }"
                  >{{ masterActionLabel(d.action) }}</span>
                </div>
                <div class="mb-hist-fund">
                  {{ d.fund_name }}
                  <span class="mb-hist-code">{{ d.fund_code }}</span>
                </div>
                <div v-if="d.score != null" class="mb-hist-score">评分 {{ Number(d.score).toFixed(0) }}</div>
                <div v-if="d.reason" class="mb-hist-reason" :title="d.reason">{{ d.reason }}</div>
                <div class="mb-hist-verify">
                  <span class="mb-verify-label">T+7</span>
                  <span
                    class="mb-verify-tag"
                    :style="{ color: verifyResultMeta(d.verify_7d_result).color, background: verifyResultMeta(d.verify_7d_result).color + '20' }"
                  >
                    {{ verifyResultMeta(d.verify_7d_result).icon }} {{ verifyResultMeta(d.verify_7d_result).label }}
                  </span>
                  <span
                    v-if="d.verify_7d_change_pct != null"
                    class="mb-verify-change"
                    :style="{ color: d.verify_7d_change_pct >= 0 ? '#4caf50' : '#f44336' }"
                  >
                    {{ d.verify_7d_change_pct >= 0 ? '+' : '' }}{{ Number(d.verify_7d_change_pct).toFixed(2) }}%
                  </span>
                  <template v-if="d.verify_30d_result">
                    <span class="mb-verify-sep">·</span>
                    <span class="mb-verify-label">T+30</span>
                    <span
                      class="mb-verify-tag"
                      :style="{ color: verifyResultMeta(d.verify_30d_result).color, background: verifyResultMeta(d.verify_30d_result).color + '20' }"
                    >
                      {{ verifyResultMeta(d.verify_30d_result).icon }} {{ verifyResultMeta(d.verify_30d_result).label }}
                    </span>
                  </template>
                </div>
                <div class="mb-hist-time">{{ d.created_at }}</div>
              </div>
            </div>
          </div>
        </template>
      </div>
        </div>
      </div>

      <!-- 关注列表统计 -->
      <div class="stats-row stats-row-6">
        <div
          class="stat-card stat-all"
          :class="{ 'stat-active': watchlistFilter === 'all' }"
          @click="watchlistFilter = 'all'"
        >
          <div class="stat-value">{{ watchlistStats.total }}</div>
          <div class="stat-label">关注总数</div>
        </div>
        <div
          class="stat-card stat-watchlist-hit"
          :class="{ 'stat-active': watchlistFilter === 'canBuy' }"
          @click="toggleWatchlistFilter('canBuy')"
        >
          <div class="stat-value">{{ watchlistStats.canBuy }}</div>
          <div class="stat-label">可上车</div>
        </div>
        <div
          class="stat-card stat-holding"
          :class="{ 'stat-active': watchlistFilter === 'approaching' }"
          @click="toggleWatchlistFilter('approaching')"
        >
          <div class="stat-value">{{ watchlistStats.approaching }}</div>
          <div class="stat-label">接近上车</div>
        </div>
        <div
          class="stat-card stat-opportunity"
          :class="{ 'stat-active': watchlistFilter === 'waiting' }"
          @click="toggleWatchlistFilter('waiting')"
        >
          <div class="stat-value">{{ watchlistStats.waiting }}</div>
          <div class="stat-label">等待中</div>
        </div>
        <div
          class="stat-card"
          :class="{ 'stat-active': watchlistFilter === 'noData' }"
          @click="toggleWatchlistFilter('noData')"
        >
          <div class="stat-value">{{ watchlistStats.noData }}</div>
          <div class="stat-label">数据不足</div>
        </div>
        <div
          class="stat-card stat-opportunity"
          :class="{ 'stat-active': watchlistFilter === 'recent7d' }"
          @click="toggleWatchlistFilter('recent7d')"
        >
          <div class="stat-value">{{ watchlistStats.recent7d }}</div>
          <div class="stat-label">近7日新增</div>
        </div>
        <!-- P0-3（2026-07-21）信号命中率 chip -->
        <div
          class="stat-card stat-hit-rate"
          :title="`已回测 ${watchlistStats.hitReviewed} 条，命中 ${watchlistStats.hitCount} 条`"
        >
          <div class="stat-value">
            {{ watchlistStats.hitRate != null ? watchlistStats.hitRate + '%' : '--' }}
          </div>
          <div class="stat-label">信号命中率</div>
        </div>
      </div>

      <!-- 排序工具栏 -->
      <div v-if="watchlist.length > 0" class="wl-sort-bar">
        <span class="wl-sort-label">排序：</span>
        <div class="wl-sort-group">
          <button
            v-for="opt in watchlistSortOptions"
            :key="opt.key"
            class="wl-sort-btn"
            :class="{ active: watchlistSort === opt.key }"
            @click="watchlistSort = opt.key"
          >{{ opt.label }}</button>
        </div>
        <div class="wl-category-filter">
          <span class="wl-sort-label">类型：</span>
          <select
            v-model="watchlistCategoryFilter"
            class="wl-category-select"
          >
            <option value="all">全部</option>
            <option v-for="c in watchlistCategoryOptions" :key="c" :value="c">{{ c }}</option>
          </select>
        </div>
        <button class="btn-wl-add" @click="openAddWatchlistDialog">
          <Icon name="plus" size="12" />
          <span>添加基金</span>
        </button>
      </div>

      <!-- 关注基金卡片 -->
      <div class="watchlist-grid" @click="handleNotesClick">
        <div v-if="watchlist.length === 0" class="empty-state">
          <Icon name="bookmark" size="28" class="empty-icon" />
          <span class="empty-text">暂无关注基金</span>
          <span class="empty-hint">在「事件雷达」Tab 的候选基金中点击「关注」按钮加入</span>
        </div>
        <div
          v-for="item in sortedWatchlist"
          :key="item.id"
          class="wl-card"
          :class="[signalMeta(item).cls, { 'wl-card-bought': item.status === 'bought' }]"
          :data-fund-code="item.fund_code"
        >
          <div class="wl-card-header">
            <div class="wl-name-block">
              <Icon name="bookmark" size="14" class="wl-icon" />
              <span class="wl-fund-name">{{ item.fund_name }}</span>
              <span v-if="item.priority >= 1" class="wl-priority-tag" :class="item.priority >= 2 ? 'wl-priority-high' : 'wl-priority-mid'">
                {{ item.priority >= 2 ? '高' : '中' }}
              </span>
              <span v-if="item.created_at" class="wl-joined-date">加入于 {{ formatShortDate(item.created_at) }}</span>
            </div>
            <span class="wl-signal-tag" :class="signalMeta(item).cls" :title="item.signal_reason || ''">
              {{ signalMeta(item).label }}
            </span>
            <!-- Batch1 增强点 1：退出信号徽章（profit_target/stop_loss 优先级高于估值信号） -->
            <span
              v-if="item.exit_signal && item.exit_signal !== 'none'"
              class="wl-exit-badge"
              :class="`exit-${item.exit_signal}`"
              :title="item.exit_signal_reason || ''"
            >
              <Icon :name="item.exit_signal === 'profit_target' ? 'trending-up' : 'trending-down'" size="11" />
              {{ item.exit_signal === 'profit_target' ? '止盈' : '止损' }}
            </span>
            <!-- Batch1 增强点 2：异常波动徽章（severe=红 / warning=黄） -->
            <span
              v-if="item.volatility_alert && item.volatility_alert !== 'none'"
              class="wl-volatility-badge"
              :class="`vol-${item.volatility_alert}`"
              :title="item.volatility_alert_reason || ''"
            >
              <Icon name="alert-triangle" size="11" />
              {{ item.volatility_alert === 'severe' ? '大跌' : '异动' }}
              <template v-if="item.daily_change_pct != null">{{ Number(item.daily_change_pct).toFixed(2) }}%</template>
            </span>
            <!-- Batch2 增强点 1：自动同步徽章（status=bought 且 entry_date 在 7 天内） -->
            <span
              v-if="item.status === 'bought' && isAutoSynced(item)"
              class="wl-auto-sync-badge"
              title="该基金已自动同步持仓买入信息"
            >
              <Icon name="refresh-cw" size="11" />
              自动同步
            </span>
          </div>
          <div class="wl-card-body">
            <!-- 来源事件说明 -->
            <div v-if="item.notes" class="wl-source-row">
              <Icon name="info" size="11" class="wl-source-icon" />
              <span class="wl-source-text" v-html="renderNotes(item.notes)"></span>
            </div>
            <!-- 3列关键指标 -->
            <div class="wl-key-grid">
              <div class="wl-key-item">
                <span class="wl-key-label">{{ opportunityLabel(item) }}</span>
                <span v-if="opportunityValue(item) !== null" class="wl-key-value" :class="{ 'value-hit': opportunityIsLow(item) }">
                  {{ opportunityValue(item) }}%
                  <span v-if="item.drawdown_percentile != null && parseFloat(item.drawdown_percentile) >= 80" class="wl-key-hint">深回撤</span>
                  <span v-else-if="item.drawdown_percentile != null && parseFloat(item.drawdown_percentile) >= 60" class="wl-key-hint">回撤深</span>
                  <span v-else-if="item.current_percentile != null && parseFloat(item.current_percentile) <= 20" class="wl-key-hint">低估</span>
                  <span v-else-if="item.current_percentile != null && parseFloat(item.current_percentile) <= 40" class="wl-key-hint">偏低</span>
                  <span v-else-if="item.nav_percentile != null && parseFloat(item.nav_percentile) <= 20" class="wl-key-hint">低位</span>
                  <span v-else-if="item.nav_percentile != null && parseFloat(item.nav_percentile) <= 40" class="wl-key-hint">偏低</span>
                </span>
                <span v-else class="wl-key-value wl-key-mute">{{ patrolling ? '查询中' : '无数据' }}</span>
              </div>
              <div class="wl-key-item">
                <span class="wl-key-label">距上车</span>
                <span v-if="item.distance_to_buy != null && item.current_nav" class="wl-key-value" :class="parseFloat(item.distance_to_buy) <= 0 ? 'value-hit' : ''">
                  {{ parseFloat(item.distance_to_buy) >= 0 ? '+' : '' }}{{ Number(item.distance_to_buy).toFixed(2) }}%
                  <span v-if="parseFloat(item.distance_to_buy) <= 0" class="wl-key-hint">已到</span>
                  <span v-else-if="parseFloat(item.distance_to_buy) < 5" class="wl-key-hint">接近</span>
                </span>
                <span v-else class="wl-key-value wl-key-mute">--</span>
              </div>
              <div class="wl-key-item">
                <span class="wl-key-label">建议上车价</span>
                <span v-if="item.suggested_buy_price" class="wl-key-value">{{ Number(item.suggested_buy_price).toFixed(4) }}</span>
                <span v-else class="wl-key-value wl-key-mute">数据不足</span>
              </div>
            </div>
            <!-- 1句话理由 -->
            <div v-if="item.signal_reason" class="wl-signal-reason" :class="signalMeta(item).cls">
              {{ item.signal_reason }}
            </div>
            <!-- 展开/收起按钮 -->
            <div class="wl-expand-bar" @click="toggleCardExpand(item.fund_code)">
              <Icon :name="expandedCards[item.fund_code] ? 'chevron-up' : 'chevron-down'" size="12" />
              <span>{{ expandedCards[item.fund_code] ? '收起详情' : '展开详情' }}</span>
              <span class="wl-expand-hint">体检报告 · 大师矩阵 · 买入评分 · 走势</span>
            </div>
            <!-- 折叠详情区 -->
            <div v-if="expandedCards[item.fund_code]" class="wl-card-details">
              <div class="wl-data-row">
              <span class="wl-data-label">当前净值</span>
              <span
                class="wl-data-value"
                :title="item.source ? `数据来源：${item.source}` : ''"
              >
                {{ item.current_nav ? Number(item.current_nav).toFixed(4) : (patrolling ? '查询中...' : '—') }}
                <span
                  v-if="item.change_pct != null && item.current_nav"
                  class="wl-change-tag"
                  :class="parseFloat(item.change_pct) >= 0 ? 'wl-change-up' : 'wl-change-down'"
                >
                  今日 {{ parseFloat(item.change_pct) >= 0 ? '+' : '' }}{{ Number(item.change_pct).toFixed(2) }}%
                </span>
              </span>
            </div>
            <!-- Batch1 增强点 1：买入价 + 当前盈亏 -->
            <div v-if="item.entry_price" class="wl-data-row">
              <span class="wl-data-label">买入价</span>
              <span class="wl-data-value">
                {{ Number(item.entry_price).toFixed(4) }}
                <span v-if="item.entry_date" class="wl-target-set-date">（{{ formatShortDate(item.entry_date) }}入）</span>
                <span v-if="item.pnl_pct != null" class="wl-change-tag" :class="parseFloat(item.pnl_pct) >= 0 ? 'wl-change-up' : 'wl-change-down'">
                  盈亏 {{ parseFloat(item.pnl_pct) >= 0 ? '+' : '' }}{{ Number(item.pnl_pct).toFixed(2) }}%
                </span>
              </span>
            </div>
            <div v-if="item.target_profit_pct || item.stop_loss_pct" class="wl-data-row">
              <span class="wl-data-label">止盈/止损</span>
              <span class="wl-data-value">
                <span v-if="item.target_profit_pct" class="wl-change-tag wl-change-up">+{{ Number(item.target_profit_pct).toFixed(1) }}%</span>
                <span v-if="item.stop_loss_pct" class="wl-change-tag wl-change-down">-{{ Number(item.stop_loss_pct).toFixed(1) }}%</span>
              </span>
            </div>
            <div class="wl-data-row">
              <span class="wl-data-label">{{ opportunityLabel(item) }}</span>
              <span
                v-if="item.current_percentile !== null && item.current_percentile !== undefined"
                class="wl-data-value"
                :class="{ 'value-hit': parseFloat(item.current_percentile) <= 20 }"
              >
                {{ Number(item.current_percentile).toFixed(0) }}%
                <span v-if="parseFloat(item.current_percentile) <= 20" class="wl-pct-hint">低估</span>
                <span v-else-if="parseFloat(item.current_percentile) <= 40" class="wl-pct-hint">偏低</span>
                <span v-else-if="parseFloat(item.current_percentile) >= 80" class="wl-pct-hint wl-pct-high">高估</span>
                <span
                  v-if="item.pe != null || item.pb != null"
                  class="wl-pe-pb"
                >
                  <template v-if="item.pe != null">PE:{{ Number(item.pe).toFixed(1) }}</template>
                  <template v-if="item.pe != null && item.pb != null"> </template>
                  <template v-if="item.pb != null">PB:{{ Number(item.pb).toFixed(1) }}</template>
                </span>
              </span>
              <span v-else-if="item.drawdown_percentile != null" class="wl-data-value" :class="{ 'value-hit': parseFloat(item.drawdown_percentile) >= 80 }">
                {{ Number(item.drawdown_percentile).toFixed(0) }}%
                <span v-if="parseFloat(item.drawdown_percentile) >= 80" class="wl-pct-hint">深回撤</span>
                <span v-else-if="parseFloat(item.drawdown_percentile) >= 60" class="wl-pct-hint">回撤深</span>
                <span v-else class="wl-pct-hint wl-pct-high">回撤不足</span>
                <span class="wl-pe-pb">{{ analysisMethodLabel(item) }}</span>
              </span>
              <span v-else-if="item.nav_percentile != null" class="wl-data-value" :class="{ 'value-hit': parseFloat(item.nav_percentile) <= 20 }">
                {{ Number(item.nav_percentile).toFixed(0) }}%
                <span v-if="parseFloat(item.nav_percentile) <= 20" class="wl-pct-hint">低位</span>
                <span v-else-if="parseFloat(item.nav_percentile) <= 40" class="wl-pct-hint">偏低</span>
                <span v-else class="wl-pct-hint wl-pct-high">偏高</span>
                <span class="wl-pe-pb">{{ analysisMethodLabel(item) }}</span>
              </span>
              <span v-else class="wl-data-value wl-value-mute">
                {{ patrolling ? '查询中...' : '无数据' }}
                <span
                  v-if="item.pe != null || item.pb != null"
                  class="wl-pe-pb"
                >
                  <template v-if="item.pe != null">PE:{{ Number(item.pe).toFixed(1) }}</template>
                  <template v-if="item.pe != null && item.pb != null"> </template>
                  <template v-if="item.pb != null">PB:{{ Number(item.pb).toFixed(1) }}</template>
                </span>
              </span>
            </div>
            <div class="wl-data-row">
              <span class="wl-data-label">目标价</span>
              <span class="wl-data-value" :class="{ 'value-hit': item.target_price && item.current_nav && parseFloat(item.current_nav) <= parseFloat(item.target_price) }">
                {{ item.target_price ? Number(item.target_price).toFixed(4) : '未设' }}
                <span v-if="item.target_price && item.updated_at" class="wl-target-set-date">（{{ formatShortDate(item.updated_at) }}设）</span>
                <span
                  v-if="item.target_price && item.distance_to_target != null"
                  class="wl-distance-tag"
                  :class="parseFloat(item.distance_to_target) <= 0 ? 'wl-distance-hit' : 'wl-distance-wait'"
                >
                  距目标 {{ parseFloat(item.distance_to_target) >= 0 ? '+' : '' }}{{ Number(item.distance_to_target).toFixed(1) }}%
                </span>
              </span>
            </div>
            <!-- 上车时机区块 -->
            <div class="wl-buy-timing">
              <div class="wl-buy-signal" :class="signalMeta(item).cls">
                <span class="wl-buy-dot"></span>
                <span class="wl-buy-label">{{ signalMeta(item).label }}</span>
                <span v-if="item.signal_confidence != null" class="wl-buy-confidence" :class="confidenceClass(item.signal_confidence)">
                  置信度 {{ item.signal_confidence }}
                </span>
                <span v-if="item.signal_reason" class="wl-buy-reason">{{ item.signal_reason }}</span>
              </div>
              <!-- P0-1（2026-07-21）多维信号徽章 -->
              <div v-if="hasMultidimSignal(item)" class="wl-multidim-badges">
                <span v-if="item.tech_signal && item.tech_signal !== 'neutral'"
                      class="wl-md-badge"
                      :class="multidimBadgeClass('tech', item.tech_signal)">
                  技术 {{ multidimLabel('tech', item.tech_signal) }}
                </span>
                <span v-if="item.capital_signal && item.capital_signal !== 'neutral'"
                      class="wl-md-badge"
                      :class="multidimBadgeClass('capital', item.capital_signal)">
                  资金 {{ multidimLabel('capital', item.capital_signal) }}
                </span>
                <span v-if="item.sentiment_signal && item.sentiment_signal !== 'neutral'"
                      class="wl-md-badge"
                      :class="multidimBadgeClass('sentiment', item.sentiment_signal)">
                  情绪 {{ multidimLabel('sentiment', item.sentiment_signal) }}
                </span>
              </div>
              <div v-if="item.suggested_buy_price" class="wl-buy-row">
                <span class="wl-data-label">建议上车价</span>
                <span class="wl-data-value">
                  {{ Number(item.suggested_buy_price).toFixed(4) }}
                  <span v-if="item.buy_price_source" class="wl-buy-source">（{{ item.buy_price_source }}）</span>
                </span>
              </div>
              <div v-if="item.distance_to_buy != null && item.current_nav" class="wl-buy-row">
                <span class="wl-data-label">距上车</span>
                <span
                  class="wl-data-value"
                  :class="parseFloat(item.distance_to_buy) <= 0 ? 'value-hit' : (parseFloat(item.distance_to_buy) < 5 ? 'wl-distance-wait' : '')"
                >
                  {{ parseFloat(item.distance_to_buy) >= 0 ? '+' : '' }}{{ Number(item.distance_to_buy).toFixed(2) }}%
                  <span v-if="parseFloat(item.distance_to_buy) <= 0" class="wl-pct-hint">已到上车价</span>
                  <span v-else-if="parseFloat(item.distance_to_buy) < 5" class="wl-pct-hint">接近</span>
                  <span v-else class="wl-pct-hint wl-pct-high">还需跌</span>
                </span>
              </div>
              <div v-if="!item.suggested_buy_price && (item.signal_status || 'gray') === 'gray'" class="wl-buy-hint">
                <Icon name="info" size="11" class="wl-source-icon" />
                <span>估值数据不足，无法推算上车价。点击"扫描信号"刷新数据。</span>
              </div>
            </div>
            <div v-if="item.target_percentile" class="wl-data-row">
              <span class="wl-data-label">目标分位</span>
              <span class="wl-data-value">{{ item.target_percentile }}%</span>
            </div>
            <div v-if="item.index_name" class="wl-data-row">
              <span class="wl-data-label">跟踪指数</span>
              <span class="wl-data-value wl-index-name">{{ item.index_name }}</span>
            </div>
            <!-- 基金六维体检报告区块 -->
            <div v-if="fundReports[item.fund_code]" class="fund-report-block">
              <div class="report-header">
                <span class="report-title">
                  <Icon name="activity" size="12" class="report-title-icon" />
                  基金体检报告
                </span>
                <div class="report-summary">
                  <span class="report-total-score" :class="scoreColorClass(fundReports[item.fund_code].total_score)">
                    {{ fundReports[item.fund_code].total_score }}
                  </span>
                  <span class="report-rating-tag" :class="scoreColorClass(fundReports[item.fund_code].total_score)">
                    {{ scoreRatingLabel(fundReports[item.fund_code].total_score) }}
                  </span>
                </div>
              </div>
              <div v-if="fundReports[item.fund_code].decision_matrix" class="report-action-row">
                <span class="report-action-tag" :class="ACTION_LABELS[fundReports[item.fund_code].decision_matrix.action]?.class">
                  {{ ACTION_LABELS[fundReports[item.fund_code].decision_matrix.action]?.label || fundReports[item.fund_code].decision_matrix.action_label || '—' }}
                </span>
              </div>
              <div v-if="fundReports[item.fund_code].report" class="report-dims">
                <div
                  v-for="(dim, key) in fundReports[item.fund_code].report"
                  :key="key"
                  class="report-dim"
                >
                  <div class="report-dim-head">
                    <span class="report-dim-label">{{ DIMENSION_LABELS[key] || dim.label || key }}</span>
                    <span class="report-dim-score" :class="scoreColorClass(dim.score)">{{ dim.score }}</span>
                  </div>
                  <div class="report-dim-bar">
                    <div
                      class="report-dim-fill"
                      :class="scoreColorClass(dim.score)"
                      :style="{ width: `${dim.score}%` }"
                    ></div>
                  </div>
                </div>
              </div>
              <div v-if="fundReports[item.fund_code].duan_yongping_view" class="report-view">
                <Icon name="lightbulb" size="11" class="report-view-icon" />
                <span class="report-view-text">{{ fundReports[item.fund_code].duan_yongping_view }}</span>
              </div>
              <!-- 大师理念矩阵 -->
              <div
                v-if="fundReports[item.fund_code].master_perspectives"
                class="master-matrix"
              >
                <div class="mm-header">
                  <span class="mm-title">🎯 大师理念矩阵</span>
                </div>
                <template v-if="fundReports[item.fund_code].master_perspectives.consensus">
                  <div
                    class="mm-consensus"
                    :class="consensusClass(fundReports[item.fund_code].master_perspectives.consensus)"
                  >
                    <span class="mm-consensus-text">
                      {{ fundReports[item.fund_code].master_perspectives.consensus.agreement_label }}
                      · {{ fundReports[item.fund_code].master_perspectives.consensus.agreement_count }}
                      建议{{ fundReports[item.fund_code].master_perspectives.consensus.consensus_action_label }}
                    </span>
                  </div>
                  <div
                    v-if="fundReports[item.fund_code].master_perspectives.consensus.conflicts?.length"
                    class="mm-conflicts"
                  >
                    <Icon name="alert-triangle" size="11" class="mm-conflict-icon" />
                    <span class="mm-conflict-text">
                      {{ fundReports[item.fund_code].master_perspectives.consensus.conflicts.length }}项意见冲突
                    </span>
                  </div>
                  <div
                    v-if="fundReports[item.fund_code].master_perspectives.consensus.action_distribution"
                    class="mm-dist"
                  >
                    <template
                      v-for="(count, action) in fundReports[item.fund_code].master_perspectives.consensus.action_distribution"
                      :key="action"
                    >
                      <div
                        v-if="count > 0"
                        class="mm-dist-seg"
                        :style="{ flexGrow: count, background: masterActionColor(action) }"
                        :title="`${masterActionLabel(action)}: ${count}`"
                      ></div>
                    </template>
                  </div>
                </template>
                <div
                  v-if="fundReports[item.fund_code].master_perspectives.masters?.length"
                  class="mm-masters"
                >
                  <div
                    v-for="m in fundReports[item.fund_code].master_perspectives.masters"
                    :key="m.master_key"
                    class="mm-card"
                    :style="{ '--mm-accent': masterAccentColor(m.master_key) }"
                    @click="toggleMaster(item.fund_code, m.master_key)"
                  >
                    <div class="mm-card-head">
                      <span class="mm-master-icon">{{ m.master_icon }}</span>
                      <span class="mm-master-name">{{ m.master_name }}</span>
                      <span
                        v-if="m.score == null"
                        class="mm-score-na"
                      >不适用</span>
                      <span
                        v-else
                        class="mm-score"
                        :style="{ color: masterRatingColor(m.rating) }"
                      >{{ m.score }}</span>
                      <span
                        class="mm-action-tag"
                        :style="{ background: masterActionColor(m.action) + '20', color: masterActionColor(m.action) }"
                      >{{ m.action_label }}</span>
                    </div>
                    <div v-if="m.reason" class="mm-reason">{{ m.reason }}</div>
                    <div
                      v-if="expandedMasters[`${item.fund_code}:${m.master_key}`]"
                      class="mm-detail"
                      @click.stop
                    >
                      <div v-if="m.core_philosophy" class="mm-detail-row">
                        <span class="mm-detail-label">核心理念</span>
                        <span class="mm-detail-value">{{ m.core_philosophy }}</span>
                      </div>
                      <div v-if="m.view_text" class="mm-detail-row">
                        <span class="mm-detail-label">综合视角</span>
                        <span class="mm-detail-value">{{ m.view_text }}</span>
                      </div>
                      <div
                        v-if="m.key_metrics && Object.keys(m.key_metrics).length"
                        class="mm-metrics"
                      >
                        <div
                          v-for="kv in formatMetrics(m.key_metrics)"
                          :key="kv.label"
                          class="mm-metric-row"
                        >
                          <span class="mm-metric-label">{{ kv.label }}</span>
                          <span class="mm-metric-value">{{ kv.value }}</span>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
              <!-- 季度调仓动作面板 -->
              <div
                v-if="fundReports[item.fund_code].details?.holding_changes"
                class="report-holding-changes"
              >
                <div class="hc-header">
                  <Icon name="refresh-cw" size="11" class="hc-header-icon" />
                  <span class="hc-title">季度调仓动作</span>
                </div>
                <template v-if="fundReports[item.fund_code].details.holding_changes.has_history">
                  <div class="hc-date-row">
                    {{ fundReports[item.fund_code].details.holding_changes.prev_quarter }}
                    <Icon name="arrow-right" size="10" class="hc-date-arrow" />
                    {{ fundReports[item.fund_code].details.holding_changes.current_quarter }}
                  </div>
                  <div
                    v-if="fundReports[item.fund_code].details.holding_changes.summary"
                    class="hc-summary"
                  >
                    {{ fundReports[item.fund_code].details.holding_changes.summary }}
                  </div>
                  <div
                    v-if="fundReports[item.fund_code].details.holding_changes.changes?.length"
                    class="hc-changes"
                  >
                    <div
                      v-for="(ch, idx) in fundReports[item.fund_code].details.holding_changes.changes"
                      :key="idx"
                      class="hc-change-item"
                    >
                      <span class="hc-stock-name" :title="ch.stock_code">{{ ch.stock_name }}</span>
                      <span
                        class="hc-action-tag"
                        :class="HOLDING_ACTION_LABELS[ch.action]?.class"
                      >
                        {{ HOLDING_ACTION_LABELS[ch.action]?.label || ch.action }}
                      </span>
                      <span
                        class="hc-delta"
                        :class="ch.delta_pct >= 0 ? 'hc-delta-up' : 'hc-delta-down'"
                      >
                        {{ ch.delta_pct > 0 ? '+' : '' }}{{ ch.delta_pct }}%
                      </span>
                    </div>
                  </div>
                </template>
                <div v-else class="hc-empty">无历史数据</div>
              </div>
              <div class="report-actions">
                <button
                  class="btn-report-refresh"
                  @click="refreshFundReport(item.fund_code)"
                  :disabled="!!loadingReports[item.fund_code]"
                >
                  <Icon
                    :name="loadingReports[item.fund_code] ? 'spinner' : 'refresh-cw'"
                    size="11"
                    :class="{ spinning: loadingReports[item.fund_code] }"
                  />
                  <span>{{ loadingReports[item.fund_code] ? '刷新中...' : '刷新分析' }}</span>
                </button>
                <button class="btn-report-detail" @click="openReportDetail(item)">
                  <Icon name="file-text" size="11" />
                  <span>查看详情</span>
                </button>
              </div>
            </div>
            <div v-else-if="loadingReports[item.fund_code]" class="fund-report-block fund-report-loading">
              <Icon name="spinner" size="14" class="spinning" />
              <span>体检分析中...</span>
            </div>
            <div v-else class="fund-report-block fund-report-empty">
              <span class="fund-report-empty-text">暂无分析数据</span>
            </div>
            <!-- 买入评分区块 -->
            <div class="wl-score-block">
              <div class="wl-score-header">
                <span class="wl-score-title">
                  <Icon name="zap" size="12" class="wl-score-title-icon" />
                  买入评分
                </span>
                <div v-if="buyScores[item.id]" class="wl-score-summary">
                  <span class="wl-score-value" :class="scoreColorClass(buyScores[item.id].score)">
                    {{ buyScores[item.id].score }}
                  </span>
                  <span class="wl-score-rating" :class="scoreColorClass(buyScores[item.id].score)">
                    {{ scoreRatingLabel(buyScores[item.id].score) }}
                  </span>
                </div>
                <span v-else-if="refreshingScores" class="wl-score-loading">计算中...</span>
                <span v-else class="wl-score-loading">未计算</span>
              </div>
              <div v-if="buyScores[item.id]?.dimensions" class="wl-score-dims">
                <div
                  v-for="(dim, key) in buyScores[item.id].dimensions"
                  :key="key"
                  class="wl-score-dim"
                >
                  <div class="wl-dim-head">
                    <span class="wl-dim-label">{{ BUY_DIMENSION_LABELS[key] || key }}</span>
                    <span class="wl-dim-weight">权重 {{ Math.round(dim.weight * 100) }}%</span>
                  </div>
                  <div class="wl-dim-bar">
                    <div
                      class="wl-dim-fill"
                      :class="scoreColorClass(dim.score)"
                      :style="{ width: `${dim.score}%` }"
                    ></div>
                  </div>
                  <div class="wl-dim-foot">
                    <span class="wl-dim-score" :class="scoreColorClass(dim.score)">{{ dim.score }}</span>
                    <span class="wl-dim-reason">{{ dim.reason }}</span>
                  </div>
                </div>
              </div>
            </div>
            <!-- 编辑目标面板 -->
            <div v-if="editingId === item.id" class="wl-edit-panel">
              <div class="wl-edit-row">
                <label>目标价</label>
                <input v-model="editForm.target_price" type="number" step="0.0001" placeholder="如 1.5000" class="wl-edit-input" />
              </div>
              <div class="wl-edit-row">
                <label>目标分位(%)</label>
                <input v-model="editForm.target_percentile" type="number" step="1" placeholder="如 20" class="wl-edit-input" />
              </div>
              <div class="wl-edit-actions">
                <button class="btn-wl-save" @click="saveTarget(item)">保存</button>
                <button class="btn-wl-cancel" @click="editingId = null">取消</button>
              </div>
            </div>
            </div>
          </div>
          <div class="wl-card-footer">
            <span v-if="item.nav_updated_at" class="wl-updated">更新于 {{ item.nav_updated_at.slice(5, 16) }}</span>
            <span v-else class="wl-updated wl-updated-stale">未刷新</span>
            <div class="wl-footer-actions">
              <button class="btn-wl-edit" @click="startEditTarget(item)" v-if="editingId !== item.id">
                <Icon name="target" size="12" />
                <span>设目标</span>
              </button>
              <!-- Batch1 增强点 1：上车设置（买入价/止盈/止损） -->
              <button class="btn-wl-entry" @click="openEntryDialog(item)">
                <Icon name="pencil" size="12" />
                <span>{{ item.entry_price ? '改上车' : '上车设置' }}</span>
              </button>
              <button
                v-if="item.status !== 'bought'"
                class="btn-wl-bought"
                @click="handleMarkBought(item)"
              >
                <Icon name="target" size="12" />
                <span>标记买入</span>
              </button>
              <button class="btn-wl-remove" @click="handleRemoveWatchlist(item)">
                <Icon name="trash-2" size="12" />
                <span>移除</span>
              </button>
            </div>
          </div>
          <span v-if="item.status === 'bought'" class="wl-bought-badge">已买入</span>
        </div>
      </div>
    </template>

    <!-- ════ Tab 3：落地验证 ════ -->
    <template v-if="activeTab === 'verification'">
      <!-- 准确率统计面板 -->
      <div v-if="accuracy && accuracy.overall?.total > 0" class="accuracy-panel">
        <div class="accuracy-header">
          <Icon name="target" size="14" class="accuracy-icon" />
          <span class="accuracy-title">验证准确率</span>
        </div>
        <div class="accuracy-body">
          <div class="accuracy-overall">
            <div class="accuracy-value">{{ (accuracy.overall.accuracy * 100).toFixed(0) }}%</div>
            <div class="accuracy-label">总体准确率</div>
            <div class="accuracy-detail">
              {{ accuracy.overall.correct }}正确 / {{ accuracy.overall.wrong }}偏差 / {{ accuracy.overall.flat }}平淡
              （共 {{ accuracy.overall.total }} 个已验证）
            </div>
          </div>
          <div v-if="Object.keys(accuracy.by_sector).length" class="accuracy-sectors">
            <div
              v-for="(s, name) in accuracy.by_sector"
              :key="name"
              class="sector-acc-item"
              :style="{ background: sectorAccuracyColor(s.accuracy) }"
            >
              <span class="sector-acc-name">{{ name }}</span>
              <div class="sector-acc-bar">
                <div class="sector-acc-fill" :style="{ width: `${s.accuracy * 100}%` }"></div>
              </div>
              <span class="sector-acc-pct">{{ (s.accuracy * 100).toFixed(0) }}%</span>
              <span class="sector-acc-samples">({{ s.total }})</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 最近验证事件列表 -->
      <div v-if="verifiedEvents.length" class="verify-section">
        <div class="verify-section-header">
          <Icon name="list" size="14" class="verify-section-icon" />
          <span class="verify-section-title">最近验证事件（{{ verifiedEvents.length }}）</span>
        </div>
        <div class="verify-events-list">
          <div v-for="evt in verifiedEvents.slice(0, 10)" :key="evt.event_id" class="verify-event-row">
            <span class="verify-event-date">{{ formatShortDate(evt.expected_date) }}</span>
            <span class="verify-event-title" :title="evt.title">{{ evt.title }}</span>
            <span v-if="evt.direction" class="direction-tag" :class="directionLabel(evt.direction).class">
              {{ directionLabel(evt.direction).text }}
            </span>
            <span
              v-if="parseVerification(evt.verification_result)"
              class="verify-tag"
              :class="`verify-${parseVerification(evt.verification_result).status}`"
            >
              <Icon :name="verificationIcon(parseVerification(evt.verification_result).status)" size="11" />
              {{ verificationLabel(parseVerification(evt.verification_result).status) }}
              {{ parseVerification(evt.verification_result).change_pct > 0 ? '+' : '' }}{{ parseVerification(evt.verification_result).change_pct }}%
            </span>
          </div>
        </div>
      </div>

      <!-- 回溯校准日志 -->
      <div v-if="calibratedEvents.length" class="verify-section">
        <div class="verify-section-header">
          <Icon name="refresh-cw" size="14" class="verify-section-icon" />
          <span class="verify-section-title">回溯校准日志（{{ calibratedEvents.length }}）</span>
        </div>
        <div class="verify-events-list">
          <div v-for="evt in calibratedEvents" :key="evt.event_id" class="verify-event-row">
            <span class="verify-event-date">{{ formatShortDate(evt.updated_at) }}</span>
            <span class="verify-event-title" :title="evt.title">{{ evt.title }}</span>
            <span class="calibrate-arrow">
              {{ Math.round((evt.original_confidence || 0) * 100) }}% → {{ Math.round((evt.confidence || 0) * 100) }}%
            </span>
            <span v-if="evt.original_direction && evt.original_direction !== evt.direction" class="direction-tag dir-neutral">
              {{ directionLabel(evt.original_direction).text }}→中性
            </span>
          </div>
        </div>
      </div>

      <!-- 引导文案：已验证事件在事件雷达中查看 -->
      <div class="verify-guide">
        <Icon name="info" size="14" class="verify-guide-icon" />
        <span class="verify-guide-text">
          已验证事件请在「事件雷达」中查看，筛选「已落地」状态可查看完整事件列表与验证结果
        </span>
        <button class="btn-verify-goto-events" @click="activeTab = 'events'; activeStatus = 'materialized'">
          <Icon name="arrow-right" size="12" />
          <span>去事件雷达</span>
        </button>
      </div>
    </template>

    <!-- 添加关注基金弹窗 -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="showAddWatchlistDialog" class="article-dialog-backdrop" @click.self="closeAddWatchlistDialog">
          <div class="article-dialog-box">
            <div class="article-dialog-header">
              <Icon name="bookmark-plus" size="16" class="article-dialog-icon" />
              <h3 class="article-dialog-title">添加关注基金</h3>
              <button class="article-dialog-close" @click="closeAddWatchlistDialog" :disabled="addingWatchlist" title="关闭">
                <Icon name="x" size="16" />
              </button>
            </div>
            <div class="article-dialog-body">
              <p class="article-dialog-hint">
                输入基金代码添加到关注列表。可选填目标价，系统将自动监测净值到位信号。
              </p>
              <div class="wl-add-form">
                <div class="wl-add-row">
                  <label class="wl-add-label">基金代码 <span class="wl-add-required">*</span></label>
                  <input
                    v-model="addWatchlistForm.fund_code"
                    type="text"
                    placeholder="如 110011 / 005827"
                    class="article-url-input"
                    :disabled="addingWatchlist"
                    @keydown.enter="handleAddWatchlistSubmit"
                    autofocus
                  />
                </div>
                <div class="wl-add-row">
                  <label class="wl-add-label">目标价（选填）</label>
                  <input
                    v-model="addWatchlistForm.target_price"
                    type="number"
                    step="0.0001"
                    placeholder="如 1.5000"
                    class="article-url-input"
                    :disabled="addingWatchlist"
                    @keydown.enter="handleAddWatchlistSubmit"
                  />
                </div>
              </div>
            </div>
            <div class="article-dialog-actions">
              <button class="btn-article-cancel" @click="closeAddWatchlistDialog" :disabled="addingWatchlist">
                取消
              </button>
              <button
                class="btn-article-confirm"
                @click="handleAddWatchlistSubmit"
                :disabled="addingWatchlist || !addWatchlistForm.fund_code.trim()"
              >
                <Icon v-if="addingWatchlist" name="spinner" size="14" class="spinning" />
                <Icon v-else name="plus" size="14" />
                <span>{{ addingWatchlist ? '添加中...' : '添加关注' }}</span>
              </button>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>

    <!-- 文章分析 URL 输入弹窗 -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="showArticleDialog" class="article-dialog-backdrop" @click.self="closeArticleDialog">
          <div class="article-dialog-box">
            <div class="article-dialog-header">
              <Icon name="file-text" size="16" class="article-dialog-icon" />
              <h3 class="article-dialog-title">分析文章提取趋势</h3>
              <button class="article-dialog-close" @click="closeArticleDialog" :disabled="analyzingArticle" title="关闭">
                <Icon name="x" size="16" />
              </button>
            </div>
            <div class="article-dialog-body">
              <p class="article-dialog-hint">
                输入深度分析文章 URL（CSDN/知乎/雪球/博客等），系统将抓取正文并提取中长期投资趋势。
              </p>
              <input
                v-model="articleUrl"
                type="url"
                placeholder="https://blog.csdn.net/..."
                class="article-url-input"
                :disabled="analyzingArticle"
                @keydown.enter="handleAnalyzeArticle"
                autofocus
              />
            </div>
            <div class="article-dialog-actions">
              <button class="btn-article-cancel" @click="closeArticleDialog" :disabled="analyzingArticle">
                取消
              </button>
              <button class="btn-article-confirm" @click="handleAnalyzeArticle" :disabled="analyzingArticle || !articleUrl.trim()">
                <Icon v-if="analyzingArticle" name="spinner" size="14" class="spinning" />
                <Icon v-else name="scan-search" size="14" />
                <span>{{ analyzingArticle ? '分析中...' : '开始分析' }}</span>
              </button>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>

    <!-- Batch1 增强点 1：上车设置弹窗（买入价/止盈/止损） -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="showEntryDialog" class="entry-dialog-backdrop" @click.self="closeEntryDialog">
          <div class="entry-dialog-box">
            <div class="entry-dialog-header">
              <Icon name="pencil" size="16" class="entry-dialog-icon" />
              <h3 class="entry-dialog-title">
                {{ entryFormItem?.status === 'bought' ? '修改上车信息' : '设置上车信息' }}
                <span v-if="entryFormItem?.fund_name" class="entry-dialog-subtitle">— {{ entryFormItem.fund_name }}</span>
              </h3>
              <button class="entry-dialog-close" @click="closeEntryDialog" :disabled="savingEntry" title="关闭">
                <Icon name="x" size="16" />
              </button>
            </div>
            <div class="entry-dialog-body">
              <p class="entry-dialog-hint">
                填写买入价和止盈/止损目标，系统会在巡检时自动计算盈亏并触发退出信号。
                <br />止盈/止损留空则使用系统默认（止盈 30% / 止损 10%）。
              </p>
              <div class="entry-form-grid">
                <div class="entry-form-row">
                  <label class="entry-form-label">买入价 *</label>
                  <input
                    v-model="entryForm.entry_price"
                    type="number"
                    step="0.0001"
                    min="0"
                    placeholder="如 1.2345"
                    class="entry-form-input"
                    :disabled="savingEntry"
                  />
                </div>
                <div class="entry-form-row">
                  <label class="entry-form-label">买入日期</label>
                  <input
                    v-model="entryForm.entry_date"
                    type="date"
                    class="entry-form-input"
                    :disabled="savingEntry"
                  />
                </div>
                <div class="entry-form-row">
                  <label class="entry-form-label">止盈目标 (%)</label>
                  <input
                    v-model="entryForm.target_profit_pct"
                    type="number"
                    step="0.1"
                    min="0"
                    placeholder="如 30（留空使用默认）"
                    class="entry-form-input"
                    :disabled="savingEntry"
                  />
                </div>
                <div class="entry-form-row">
                  <label class="entry-form-label">止损线 (%)</label>
                  <input
                    v-model="entryForm.stop_loss_pct"
                    type="number"
                    step="0.1"
                    min="0"
                    placeholder="如 10（留空使用默认）"
                    class="entry-form-input"
                    :disabled="savingEntry"
                  />
                </div>
              </div>
              <div v-if="entryFormItem?.current_nav" class="entry-form-tip">
                <Icon name="info" size="11" />
                <span>
                  当前净值 {{ Number(entryFormItem.current_nav).toFixed(4) }}，
                  <template v-if="entryForm.entry_price">
                    距买入价
                    <strong :class="parseFloat(entryFormItem.current_nav) >= parseFloat(entryForm.entry_price) ? 'tip-up' : 'tip-down'">
                      {{ ((parseFloat(entryFormItem.current_nav) - parseFloat(entryForm.entry_price)) / parseFloat(entryForm.entry_price) * 100).toFixed(2) }}%
                    </strong>
                  </template>
                </span>
              </div>
            </div>
            <div class="entry-dialog-actions">
              <button class="btn-entry-cancel" @click="closeEntryDialog" :disabled="savingEntry">
                取消
              </button>
              <button class="btn-entry-confirm" @click="handleEntrySubmit" :disabled="savingEntry || !entryForm.entry_price">
                <Icon v-if="savingEntry" name="spinner" size="14" class="spinning" />
                <Icon v-else name="check-circle" size="14" />
                <span>{{ savingEntry ? '保存中...' : '保存' }}</span>
              </button>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>

    <!-- 基金六维体检报告详情弹窗 -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="reportDetailItem" class="report-detail-backdrop" @click.self="closeReportDetail">
          <div class="report-detail-modal">
            <div class="report-detail-header">
              <Icon name="activity" size="16" class="report-detail-header-icon" />
              <h3 class="report-detail-title">
                {{ reportDetailItem.fund_name || reportDetailItem.fund_code }} — 基金体检报告
              </h3>
              <button class="report-detail-close" @click="closeReportDetail" title="关闭">
                <Icon name="x" size="16" />
              </button>
            </div>
            <div class="report-detail-body">
              <!-- 综合评分 + 操作建议 -->
              <div class="report-detail-summary">
                <div class="report-detail-score-row">
                  <span class="report-detail-score-label">综合评分</span>
                  <span class="report-detail-score-value" :class="scoreColorClass(reportDetailItem.total_score)">
                    {{ reportDetailItem.total_score }}/100
                  </span>
                  <span class="report-detail-rating" :class="scoreColorClass(reportDetailItem.total_score)">
                    {{ scoreRatingLabel(reportDetailItem.total_score) }}
                  </span>
                </div>
                <div v-if="reportDetailItem.decision_matrix" class="report-detail-action-row">
                  <span class="report-detail-action-label">操作建议</span>
                  <span class="report-action-tag" :class="ACTION_LABELS[reportDetailItem.decision_matrix.action]?.class">
                    {{ ACTION_LABELS[reportDetailItem.decision_matrix.action]?.label || reportDetailItem.decision_matrix.action_label || '—' }}
                  </span>
                  <span v-if="reportDetailItem.decision_matrix.reason" class="report-detail-action-reason">
                    {{ reportDetailItem.decision_matrix.reason }}
                  </span>
                </div>
              </div>

              <!-- 段永平视角 -->
              <div v-if="reportDetailItem.duan_yongping_view" class="report-detail-view">
                <div class="report-detail-view-title">
                  <Icon name="lightbulb" size="12" />
                  段永平视角
                </div>
                <p class="report-detail-view-text">{{ reportDetailItem.duan_yongping_view }}</p>
              </div>

              <!-- 综合建议 -->
              <div v-if="reportDetailItem.advice" class="report-detail-advice">
                <div class="report-detail-advice-title">综合建议</div>
                <p class="report-detail-advice-text">{{ reportDetailItem.advice }}</p>
              </div>

              <!-- 每个维度的详细子项 -->
              <div v-if="reportDetailItem.report" class="report-detail-dims">
                <div
                  v-for="(dim, key) in reportDetailItem.report"
                  :key="key"
                  class="report-section"
                >
                  <div class="report-section-header">
                    <span class="report-section-title">{{ DIMENSION_LABELS[key] || dim.label || key }}</span>
                    <span class="report-section-score" :class="scoreColorClass(dim.score)">{{ dim.score }}分</span>
                  </div>
                  <div v-if="dim.dimensions" class="report-section-subs">
                    <div
                      v-for="(sub, subKey) in dim.dimensions"
                      :key="subKey"
                      class="report-sub-item"
                    >
                      <div class="report-sub-head">
                        <span class="report-sub-name">{{ SUBDIM_LABELS[subKey] || subKey }}</span>
                        <span class="report-sub-score">{{ sub.score }}/{{ Math.round(sub.weight * 100) }}</span>
                      </div>
                      <div v-if="sub.reason" class="report-sub-reason">{{ sub.reason }}</div>
                    </div>
                  </div>
                  <div v-else class="report-section-empty">
                    <span>维度得分 {{ dim.score }}/100</span>
                  </div>
                </div>
              </div>

              <!-- 基本面详情：Top10重仓股5维评分 -->
              <div v-if="reportDetailItem.details?.fundamental" class="report-detail-fundamental">
                <div class="report-section-header">
                  <span class="report-section-title">
                    <Icon name="activity" size="12" class="report-detail-section-icon" />
                    基本面详情 · Top10重仓股5维评分
                  </span>
                  <span class="report-section-score" :class="ratingColorClass(reportDetailItem.details.fundamental.rating)">
                    {{ reportDetailItem.details.fundamental.fundamental_score }}分
                  </span>
                </div>
                <div v-if="reportDetailItem.details.fundamental.top10_coverage != null" class="fundamental-coverage">
                  Top10集中度：<strong>{{ reportDetailItem.details.fundamental.top10_coverage }}%</strong>
                </div>
                <div v-if="reportDetailItem.details.fundamental.stock_scores?.length" class="fundamental-table-wrap">
                  <table class="fundamental-table">
                    <thead>
                      <tr>
                        <th>股票名</th>
                        <th>持仓占比</th>
                        <th>盈利能力</th>
                        <th>成长性</th>
                        <th>偿债能力</th>
                        <th>稳定性</th>
                        <th>估值</th>
                        <th>总分</th>
                        <th>评级</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr
                        v-for="(stock, idx) in reportDetailItem.details.fundamental.stock_scores"
                        :key="idx"
                      >
                        <td class="ft-stock-name" :title="stock.stock_code">{{ stock.stock_name }}</td>
                        <td class="ft-pct">{{ stock.pct_nav }}%</td>
                        <td
                          class="ft-score"
                          :class="scoreColorClass(stock.profitability?.score)"
                          :title="stock.profitability?.reason"
                        >{{ stock.profitability?.score ?? '—' }}</td>
                        <td
                          class="ft-score"
                          :class="scoreColorClass(stock.growth?.score)"
                          :title="stock.growth?.reason"
                        >{{ stock.growth?.score ?? '—' }}</td>
                        <td
                          class="ft-score"
                          :class="scoreColorClass(stock.solvency?.score)"
                          :title="stock.solvency?.reason"
                        >{{ stock.solvency?.score ?? '—' }}</td>
                        <td
                          class="ft-score"
                          :class="scoreColorClass(stock.stability?.score)"
                          :title="stock.stability?.reason"
                        >{{ stock.stability?.score ?? '—' }}</td>
                        <td
                          class="ft-score"
                          :class="scoreColorClass(stock.valuation?.score)"
                          :title="stock.valuation?.reason"
                        >{{ stock.valuation?.score ?? '—' }}</td>
                        <td class="ft-total" :class="scoreColorClass(stock.total)">{{ stock.total }}</td>
                        <td>
                          <span class="ft-rating-tag" :class="ratingColorClass(stock.rating)">
                            {{ ratingLabel(stock.rating) }}
                          </span>
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
                <div v-if="reportDetailItem.details.fundamental.advice" class="fundamental-advice">
                  {{ reportDetailItem.details.fundamental.advice }}
                </div>
              </div>

              <!-- 调仓动作详情 -->
              <div v-if="reportDetailItem.details?.holding_changes" class="report-detail-holding-changes">
                <div class="report-section-header">
                  <span class="report-section-title">
                    <Icon name="refresh-cw" size="12" class="report-detail-section-icon" />
                    季度调仓动作详情
                  </span>
                </div>
                <template v-if="reportDetailItem.details.holding_changes.has_history">
                  <div class="hc-date-row">
                    {{ reportDetailItem.details.holding_changes.prev_quarter }}
                    <Icon name="arrow-right" size="10" class="hc-date-arrow" />
                    {{ reportDetailItem.details.holding_changes.current_quarter }}
                  </div>
                  <div v-if="reportDetailItem.details.holding_changes.summary" class="hc-summary">
                    {{ reportDetailItem.details.holding_changes.summary }}
                  </div>
                  <div
                    v-if="reportDetailItem.details.holding_changes.changes?.length"
                    class="hc-changes hc-changes-detail"
                  >
                    <div
                      v-for="(ch, idx) in reportDetailItem.details.holding_changes.changes"
                      :key="idx"
                      class="hc-change-item hc-change-item-detail"
                    >
                      <span class="hc-stock-name hc-stock-name-detail" :title="ch.stock_code">
                        {{ ch.stock_name }}
                        <span class="hc-stock-code">{{ ch.stock_code }}</span>
                      </span>
                      <span
                        class="hc-action-tag"
                        :class="HOLDING_ACTION_LABELS[ch.action]?.class"
                      >
                        {{ HOLDING_ACTION_LABELS[ch.action]?.label || ch.action }}
                      </span>
                      <span
                        class="hc-delta"
                        :class="ch.delta_pct >= 0 ? 'hc-delta-up' : 'hc-delta-down'"
                      >
                        {{ ch.delta_pct > 0 ? '+' : '' }}{{ ch.delta_pct }}%
                      </span>
                    </div>
                  </div>
                  <div v-else class="hc-empty">本季度无调仓动作</div>
                </template>
                <div v-else class="hc-empty">无历史数据</div>
              </div>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<style scoped>
.event-radar-page {
  padding: 1.25rem 1.5rem;
  max-width: 960px;
  margin: 0 auto;
}

/* 顶部标题 */
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 1.25rem;
}
.header-left { flex: 1; min-width: 0; }
.page-title-row {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  margin-bottom: 0.3rem;
}
.page-title-icon { color: var(--color-primary); }
.page-title {
  font-size: 1.3rem;
  font-weight: 700;
  color: var(--color-text-primary);
  margin: 0;
}
.page-subtitle {
  font-size: 0.82rem;
  color: var(--color-text-tertiary);
  margin: 0;
}
.last-scan-time {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  margin: 4px 0 0;
  display: flex;
  align-items: center;
  gap: 4px;
}
.header-actions { flex-shrink: 0; }

.scan-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.5rem 1rem;
  border-radius: 6px;
  font-size: 0.82rem;
  font-weight: 500;
  border: none;
  cursor: pointer;
  transition: all 0.15s;
  background: var(--color-primary);
  color: white;
}
.scan-btn:hover:not(:disabled) { opacity: 0.9; transform: translateY(-1px); }
.scan-btn:disabled { opacity: 0.6; cursor: not-allowed; }

/* 分析文章按钮 */
.analyze-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.5rem 0.9rem;
  border-radius: 6px;
  font-size: 0.82rem;
  font-weight: 500;
  border: 1px solid var(--color-border);
  cursor: pointer;
  transition: all 0.15s;
  background: var(--color-bg-card);
  color: var(--color-text-secondary);
  margin-right: 0.5rem;
}
.analyze-btn:hover:not(:disabled) {
  border-color: #7c3aed;
  color: #7c3aed;
}
.analyze-btn:disabled { opacity: 0.6; cursor: not-allowed; }

/* 统计卡片 */
.stats-row {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 0.75rem;
  margin-bottom: 1rem;
}
.stats-row-3 {
  grid-template-columns: repeat(3, 1fr);
}
.stats-row-4 {
  grid-template-columns: repeat(4, 1fr);
}
.stats-row-6 {
  grid-template-columns: repeat(6, 1fr);
}
.stat-card {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border-light);
  border-radius: 8px;
  padding: 0.9rem 1rem;
  cursor: pointer;
  transition: all 0.15s;
  border-left: 3px solid var(--color-border);
}
.stat-card:hover {
  border-color: var(--color-border);
  background: var(--color-bg-secondary);
}
.stat-card.stat-active {
  border-color: var(--color-border);
  background: var(--color-bg-secondary);
  box-shadow: inset 0 0 0 1px var(--color-border);
}
.stat-card.stat-active .stat-value {
  font-weight: 800;
}
.stat-all { border-left-color: var(--color-text-tertiary); }
.stat-holding { border-left-color: #dc2626; }
.stat-watchlist-hit { border-left-color: #ea580c; }
.stat-opportunity { border-left-color: #d97706; }
/* P0-3（2026-07-21）信号命中率 chip：青色边框区分 */
.stat-hit-rate { border-left-color: #0891b2; cursor: default; }
.stat-hit-rate .stat-value { color: #0891b2; }
.stat-watch { border-left-color: #2563eb; }
.stat-value {
  font-size: 1.5rem;
  font-weight: 700;
  color: var(--color-text-primary);
  line-height: 1.2;
  font-family: var(--font-jet);
}
.stat-label {
  font-size: 0.78rem;
  color: var(--color-text-secondary);
  margin-top: 0.2rem;
}

/* 筛选栏 */
.filter-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 1rem;
  gap: 1rem;
}
.filter-tabs {
  display: flex;
  gap: 0.3rem;
  background: var(--color-bg-secondary);
  border-radius: 6px;
  padding: 3px;
}
.filter-tab {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.4rem 0.75rem;
  border: none;
  background: transparent;
  border-radius: 5px;
  font-size: 0.78rem;
  color: var(--color-text-secondary);
  cursor: pointer;
  transition: all 0.15s;
}
.filter-tab:hover { color: var(--color-text-primary); }
.filter-tab.active {
  background: var(--color-bg-card);
  color: var(--color-text-primary);
  font-weight: 500;
  box-shadow: 0 1px 2px rgba(0,0,0,0.06);
}
.status-tabs {
  display: flex;
  gap: 0.2rem;
}
.status-tab {
  padding: 0.35rem 0.7rem;
  border: none;
  background: transparent;
  border-radius: 4px;
  font-size: 0.75rem;
  color: var(--color-text-tertiary);
  cursor: pointer;
  transition: all 0.15s;
}
.status-tab:hover { color: var(--color-text-secondary); }
.status-tab.active {
  color: var(--color-text-primary);
  background: var(--color-bg-secondary);
  font-weight: 500;
}

/* 时间线 */
.events-timeline {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 3rem 1rem;
  gap: 0.6rem;
  color: var(--color-text-tertiary);
  font-size: 0.85rem;
}
.empty-icon { opacity: 0.5; }
.empty-text { font-size: 0.9rem; color: var(--color-text-secondary); }
.empty-hint { font-size: 0.78rem; }

/* 事件卡片 */
.event-card {
  display: flex;
  gap: 1rem;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border-light);
  border-radius: 8px;
  padding: 1rem 1.1rem;
  transition: all 0.15s;
}
.event-card:hover {
  border-color: var(--color-border);
  box-shadow: 0 2px 8px rgba(0,0,0,0.04);
}
.event-card.relevance-holding_impact { border-left: 3px solid #dc2626; }
.event-card.relevance-watchlist_impact { border-left: 3px solid #ea580c; }
.event-card.relevance-opportunity { border-left: 3px solid #d97706; }
.event-card.relevance-market_watch { border-left: 3px solid #2563eb; }

/* 左侧时间列 */
.event-time-col {
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 70px;
  flex-shrink: 0;
  position: relative;
}
.event-date-badge {
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--color-text-primary);
  background: var(--color-bg-secondary);
  padding: 0.25rem 0.5rem;
  border-radius: 4px;
  white-space: nowrap;
  font-family: var(--font-jet);
}
.event-date-sub {
  font-size: 0.68rem;
  color: var(--color-text-tertiary);
  margin-top: 0.25rem;
  font-family: var(--font-jet);
}
.timeline-line {
  width: 1px;
  flex: 1;
  background: var(--color-border-light);
  margin-top: 0.5rem;
  min-height: 20px;
}

/* 右侧内容 */
.event-content-col {
  flex: 1;
  min-width: 0;
}
.event-header { margin-bottom: 0.5rem; }
.event-title-row {
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
  margin-bottom: 0.4rem;
}
.event-title {
  font-size: 0.95rem;
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0;
  line-height: 1.4;
  flex: 1;
}
.event-type-tag {
  flex-shrink: 0;
  font-size: 0.68rem;
  padding: 0.15rem 0.5rem;
  border-radius: 3px;
  background: var(--color-bg-tertiary);
  color: var(--color-text-secondary);
  font-weight: 500;
}
.event-meta-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
}
.relevance-tag, .status-tag, .direction-tag, .confidence-tag {
  font-size: 0.68rem;
  padding: 0.18rem 0.5rem;
  border-radius: 3px;
  font-weight: 500;
}
.relevance-tag.tag-holding_impact { background: rgba(220,38,38,0.1); color: #dc2626; }
.relevance-tag.tag-watchlist_impact { background: rgba(234,88,12,0.1); color: #ea580c; }
.relevance-tag.tag-opportunity { background: rgba(217,119,6,0.1); color: #d97706; }
.relevance-tag.tag-market_watch { background: rgba(37,99,235,0.1); color: #2563eb; }

.status-tag.st-upcoming { background: var(--color-bg-tertiary); color: var(--color-text-secondary); }
.status-tag.st-imminent { background: rgba(220,38,38,0.1); color: #dc2626; }
.status-tag.st-materialized { background: rgba(22,163,74,0.1); color: #16a34a; }
.status-tag.st-expired { background: var(--color-bg-tertiary); color: var(--color-text-tertiary); }

.direction-tag.dir-positive { background: rgba(22,163,74,0.1); color: #16a34a; }
.direction-tag.dir-negative { background: rgba(220,38,38,0.1); color: #dc2626; }
.direction-tag.dir-neutral { background: var(--color-bg-tertiary); color: var(--color-text-secondary); }

.confidence-tag {
  background: var(--color-bg-tertiary);
  color: var(--color-text-tertiary);
  font-family: var(--font-jet);
}

.confidence-tag.confidence-calibrated {
  background: rgba(245, 158, 11, 0.12);
  color: #d97706;
}

/* Batch2 增强点 3：置信度时间衰减样式 */
.confidence-tag.confidence-decayed {
  background: rgba(239, 68, 68, 0.12);
  color: #dc2626;
  border: 1px dashed rgba(239, 68, 68, 0.4);
}

/* Batch2 增强点 1：关注计划自动同步徽章 */
.wl-auto-sync-badge {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 0.18rem 0.45rem;
  border-radius: 3px;
  font-size: 0.66rem;
  font-weight: 500;
  background: rgba(16, 185, 129, 0.12);
  color: #059669;
  border: 1px solid rgba(16, 185, 129, 0.3);
}

/* ── 趋势事件样式 ── */
.event-card.event-card-trend {
  border-left: 3px solid #7c3aed;
}
.event-card.event-card-trend .event-date-badge {
  background: rgba(124,58,237,0.1);
  color: #7c3aed;
}
.event-type-tag.event-type-trend {
  background: rgba(124,58,237,0.1);
  color: #7c3aed;
}
.time-frame-tag {
  font-size: 0.68rem;
  padding: 0.18rem 0.5rem;
  border-radius: 3px;
  font-weight: 500;
}
.time-frame-tag.tf-short { background: rgba(234,88,12,0.1); color: #ea580c; }
.time-frame-tag.tf-medium { background: rgba(217,119,6,0.1); color: #d97706; }
.time-frame-tag.tf-long { background: rgba(124,58,237,0.1); color: #7c3aed; }

/* 趋势证据区域 */
.trend-evidence {
  background: rgba(124,58,237,0.04);
  border-left: 2px solid rgba(124,58,237,0.3);
  border-radius: 4px;
  padding: 0.5rem 0.6rem;
}
.trend-evidence .section-label {
  color: #7c3aed;
}
.evidence-text {
  font-size: 0.78rem;
  color: var(--color-text-secondary);
  line-height: 1.6;
  margin-top: 0.2rem;
}

.event-summary {
  font-size: 0.82rem;
  color: var(--color-text-secondary);
  line-height: 1.6;
  margin: 0 0 0.6rem 0;
}

.event-section {
  margin-top: 0.5rem;
}
.section-label {
  font-size: 0.72rem;
  color: var(--color-text-tertiary);
  font-weight: 500;
  margin-right: 0.5rem;
  display: inline-block;
}
.tag-list {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 0.3rem;
  vertical-align: middle;
}
.chip {
  font-size: 0.7rem;
  padding: 0.15rem 0.5rem;
  border-radius: 3px;
  font-weight: 500;
}
.chip-sector {
  background: rgba(59,130,246,0.08);
  color: #2563eb;
}
.chip-theme {
  background: rgba(168,85,247,0.08);
  color: #7c3aed;
}

/* 持仓列表 */
.holding-list {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  margin-top: 0.25rem;
}
.holding-item {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.78rem;
  color: var(--color-text-secondary);
  padding: 0.25rem 0.5rem;
  background: var(--color-bg-secondary);
  border-radius: 4px;
}
.holding-item .holding-name {
  color: var(--color-text-primary);
  font-weight: 500;
}
.holding-item .holding-reason {
  color: var(--color-text-tertiary);
  font-size: 0.72rem;
}

/* 候选基金 */
.candidate-list {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  margin-top: 0.25rem;
}
.candidate-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.4rem 0.6rem;
  background: var(--color-bg-secondary);
  border-radius: 4px;
}
.candidate-icon { color: #d97706; flex-shrink: 0; }
.candidate-info {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.candidate-name {
  font-size: 0.8rem;
  font-weight: 500;
  color: var(--color-text-primary);
}
.candidate-reason {
  font-size: 0.7rem;
  color: var(--color-text-tertiary);
}

/* 响应式 */
@media (max-width: 768px) {
  .event-radar-page { padding: 0.5rem; }
  .stats-row { grid-template-columns: repeat(2, 1fr); gap: 0.35rem; }
  .event-time-col { width: 48px; }
  .event-date-badge { font-size: 0.68rem; padding: 0.15rem 0.35rem; }
  .event-date-sub { font-size: 0.62rem; }
  .filter-bar { flex-direction: column; align-items: stretch; gap: 0.4rem; }

  /* 顶部标题区 —— 纵向布局，避免标题与按钮挤压 */
  .page-header { flex-direction: column; align-items: stretch; gap: 0.4rem; }
  .header-left { flex: none; }
  .page-title-row { flex-wrap: wrap; }
  .page-title { font-size: 1.05rem; }
  .page-subtitle { font-size: 0.72rem; display: none; }
  .header-actions { flex-shrink: 1; display: flex; flex-wrap: wrap; gap: 0.3rem; }
  .header-actions .btn,
  .header-actions .scan-btn,
  .header-actions .analyze-btn,
  .header-actions .verify-btn { margin-right: 0; padding: 0.35rem 0.6rem; font-size: 0.72rem; }

  /* 主Tab —— 允许换行，避免3个标签挤压 */
  .main-tabs { flex-wrap: wrap; gap: 0.15rem; }
  .main-tab { padding: 0.35rem 0.5rem; font-size: 0.74rem; }

  /* 筛选区 —— 允许换行，控制最大宽度 */
  .filter-tabs { flex-wrap: wrap; gap: 0.15rem; padding: 2px; }
  .filter-tab { padding: 0.25rem 0.4rem; font-size: 0.68rem; white-space: nowrap; }
  .status-tabs { flex-wrap: wrap; gap: 0.1rem; }
  .status-tab { padding: 0.25rem 0.4rem; font-size: 0.66rem; white-space: nowrap; }

  /* 统计卡片 */
  .stat-card { padding: 0.5rem 0.55rem; }
  .stat-value { font-size: 1rem; }
  .stat-label { font-size: 0.64rem; }

  /* 事件卡片移动端适配 —— 避免标签/文字挤压堆叠 */
  .event-card { padding: 0.65rem 0.7rem; gap: 0.5rem; flex-wrap: wrap; }
  .event-content-col { flex: 1; min-width: 0; }
  .event-title { font-size: 0.84rem; line-height: 1.35; word-break: break-word; }
  .event-title-row { gap: 0.3rem; margin-bottom: 0.3rem; flex-wrap: wrap; }
  .event-type-tag { font-size: 0.62rem; padding: 0.1rem 0.35rem; }
  .event-meta-row { gap: 0.25rem; }
  .relevance-tag,
  .status-tag,
  .direction-tag,
  .confidence-tag,
  .time-frame-tag { font-size: 0.62rem; padding: 0.12rem 0.35rem; }
  .event-summary { font-size: 0.76rem; line-height: 1.5; margin-bottom: 0.4rem; word-break: break-word; }
  .section-label { font-size: 0.64rem; }
  .chip { font-size: 0.64rem; padding: 0.1rem 0.35rem; }
  
  /* 验证标签在移动端更小 */
  .verify-tag { font-size: 0.62rem; padding: 0.12rem 0.4rem; }
}

/* ── 验证按钮 ── */
.verify-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.5rem 0.9rem;
  border-radius: 6px;
  font-size: 0.82rem;
  font-weight: 500;
  border: 1px solid var(--color-border);
  cursor: pointer;
  transition: all 0.15s;
  background: var(--color-bg-card);
  color: var(--color-text-secondary);
  margin-right: 0.5rem;
}
.verify-btn:hover:not(:disabled) {
  border-color: var(--color-primary);
  color: var(--color-primary);
}
.verify-btn:disabled { opacity: 0.6; cursor: not-allowed; }

/* ── 准确率统计面板 ── */
.accuracy-panel {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border-light);
  border-radius: 8px;
  padding: 1rem 1.1rem;
  margin-bottom: 1rem;
}

/* ── Tab3 引导文案 ── */
.verify-guide {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.9rem 1.1rem;
  background: var(--color-bg-secondary);
  border: 1px dashed var(--color-border);
  border-radius: 8px;
  color: var(--color-text-secondary);
  flex-wrap: wrap;
}

/* ── 落地验证：事件列表 + 校准日志 ── */
.verify-section {
  margin-top: 1rem;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  overflow: hidden;
}
.verify-section-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.7rem 1rem;
  border-bottom: 1px solid var(--color-border);
  background: var(--color-bg-secondary);
}
.verify-section-icon { color: var(--color-text-tertiary); flex-shrink: 0; }
.verify-section-title { font-size: 0.85rem; font-weight: 600; color: var(--color-text-primary); }
.verify-events-list { display: flex; flex-direction: column; }
.verify-event-row {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.55rem 1rem;
  border-bottom: 1px solid var(--color-border-light);
  font-size: 0.78rem;
}
.verify-event-row:last-child { border-bottom: none; }
.verify-event-date {
  color: var(--color-text-tertiary);
  font-family: var(--font-jet);
  font-size: 0.72rem;
  flex-shrink: 0;
  width: 70px;
}
.verify-event-title {
  flex: 1;
  color: var(--color-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.calibrate-arrow {
  color: #d97706;
  font-family: var(--font-jet);
  font-size: 0.72rem;
  flex-shrink: 0;
}
.verify-guide-icon { color: var(--color-text-tertiary); flex-shrink: 0; }
.verify-guide-text {
  font-size: 0.8rem;
  flex: 1;
  min-width: 200px;
  line-height: 1.5;
}
.btn-verify-goto-events {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.35rem 0.75rem;
  border: 1px solid var(--color-border);
  background: var(--color-bg-card);
  color: var(--color-text-primary);
  border-radius: 5px;
  font-size: 0.75rem;
  cursor: pointer;
  transition: all 0.15s;
}
.btn-verify-goto-events:hover {
  border-color: var(--color-primary);
  color: var(--color-primary);
}
.accuracy-header {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  margin-bottom: 0.75rem;
}
.accuracy-icon { color: var(--color-primary); }
.accuracy-title {
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--color-text-primary);
}
.accuracy-body {
  display: flex;
  gap: 1.5rem;
  align-items: flex-start;
}
.accuracy-overall {
  flex-shrink: 0;
  min-width: 140px;
}
.accuracy-value {
  font-size: 1.6rem;
  font-weight: 700;
  color: var(--color-primary);
  font-family: var(--font-jet);
  line-height: 1.2;
}
.accuracy-label {
  font-size: 0.75rem;
  color: var(--color-text-secondary);
  margin-top: 0.15rem;
}
.accuracy-detail {
  font-size: 0.7rem;
  color: var(--color-text-tertiary);
  margin-top: 0.3rem;
}
.accuracy-sectors {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  min-width: 0;
}
.sector-acc-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.75rem;
}
.sector-acc-name {
  width: 60px;
  flex-shrink: 0;
  color: var(--color-text-secondary);
}
.sector-acc-bar {
  flex: 1;
  height: 6px;
  background: var(--color-bg-tertiary);
  border-radius: 3px;
  overflow: hidden;
}
.sector-acc-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--color-primary), var(--color-gold));
  border-radius: 3px;
  transition: width 0.3s;
}
.sector-acc-pct {
  width: 36px;
  text-align: right;
  font-weight: 600;
  color: var(--color-text-primary);
  font-family: var(--font-jet);
}
.sector-acc-samples {
  font-size: 0.68rem;
  color: var(--color-text-tertiary);
  width: 30px;
}

/* ── 验证结果标签 ── */
.verify-tag {
  display: inline-flex;
  align-items: center;
  gap: 0.2rem;
  font-size: 0.68rem;
  padding: 0.18rem 0.5rem;
  border-radius: 3px;
  font-weight: 500;
}
.verify-tag.verify-correct {
  background: rgba(22,163,74,0.1);
  color: #16a34a;
}
.verify-tag.verify-wrong {
  background: rgba(220,38,38,0.1);
  color: #dc2626;
}
.verify-tag.verify-flat {
  background: var(--color-bg-tertiary);
  color: var(--color-text-secondary);
}

/* ── 主 Tab 切换 ── */
.main-tabs {
  display: flex;
  gap: 0.25rem;
  border-bottom: 1px solid var(--color-border-light);
  margin-bottom: 1rem;
  padding-bottom: 0;
}
.main-tab {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.55rem 0.9rem;
  border: none;
  background: transparent;
  color: var(--color-text-secondary);
  font-size: 0.85rem;
  font-weight: 500;
  cursor: pointer;
  border-bottom: 2px solid transparent;
  transition: all 0.15s;
  margin-bottom: -1px;
}
.main-tab:hover { color: var(--color-text-primary); }
.main-tab.active {
  color: var(--color-primary);
  border-bottom-color: var(--color-primary);
}
.tab-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: 9px;
  background: var(--color-bg-tertiary);
  color: var(--color-text-secondary);
  font-size: 0.68rem;
  font-weight: 600;
  font-family: var(--font-jet);
}
.tab-badge-orange { background: rgba(234,88,12,0.12); color: #ea580c; }
.tab-badge-green { background: rgba(22,163,74,0.12); color: #16a34a; }

/* ── 候选基金关注按钮 ── */
.candidate-item {
  position: relative;
}
.btn-watch-add {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  padding: 0.25rem 0.55rem;
  border: 1px solid var(--color-border);
  background: var(--color-bg-card);
  color: var(--color-text-secondary);
  border-radius: 4px;
  font-size: 0.72rem;
  cursor: pointer;
  transition: all 0.15s;
  flex-shrink: 0;
  margin-left: auto;
}
.btn-watch-add:hover {
  border-color: #ea580c;
  color: #ea580c;
  background: rgba(234,88,12,0.04);
}

/* ── Batch1 增强点 3：事件影响预估徽章 + 深度解读 ── */
.impact-est-tag {
  display: inline-flex;
  align-items: center;
  gap: 0.2rem;
  font-size: 0.68rem;
  padding: 0.15rem 0.45rem;
  border-radius: 3px;
  font-weight: 500;
  white-space: nowrap;
}
.impact-est-tag.impact-up {
  background: rgba(22,163,74,0.1);
  color: #16a34a;
}
.impact-est-tag.impact-down {
  background: rgba(220,38,38,0.1);
  color: #dc2626;
}
.impact-est-tag.impact-flat {
  background: var(--color-bg-tertiary);
  color: var(--color-text-secondary);
}
.impact-cached-tag {
  display: inline-flex;
  align-items: center;
  gap: 0.2rem;
  font-size: 0.66rem;
  padding: 0.12rem 0.4rem;
  border-radius: 3px;
  background: rgba(22,163,74,0.08);
  color: #16a34a;
  white-space: nowrap;
}
.event-impact-section {
  margin-top: 0.6rem;
  padding-top: 0.5rem;
  border-top: 1px dashed var(--color-border);
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
}
.btn-event-impact {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.32rem 0.7rem;
  background: linear-gradient(135deg, rgba(37,99,235,0.08), rgba(139,92,246,0.08));
  color: #4f46e5;
  border: 1px solid rgba(79,70,229,0.25);
  border-radius: 4px;
  font-size: 0.74rem;
  cursor: pointer;
  transition: all 0.18s;
  font-weight: 500;
}
.btn-event-impact:hover:not(:disabled) {
  background: linear-gradient(135deg, rgba(37,99,235,0.15), rgba(139,92,246,0.15));
  border-color: rgba(79,70,229,0.5);
}
.btn-event-impact:disabled {
  cursor: wait;
  opacity: 0.7;
}
.btn-impact-toggle {
  display: inline-flex;
  align-items: center;
  gap: 0.2rem;
  padding: 0.25rem 0.5rem;
  background: transparent;
  color: var(--color-text-secondary);
  border: 1px solid var(--color-border);
  border-radius: 3px;
  font-size: 0.7rem;
  cursor: pointer;
  transition: all 0.15s;
}
.btn-impact-toggle:hover {
  background: var(--color-bg-tertiary);
  color: var(--color-text-primary);
}
.impact-cached-hint {
  font-size: 0.66rem;
  color: var(--color-text-tertiary);
}
.event-impact-error {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  font-size: 0.7rem;
  color: #dc2626;
  background: rgba(220,38,38,0.08);
  padding: 0.2rem 0.5rem;
  border-radius: 3px;
}

/* ── Batch2 增强点 2：事件金额影响（纯计算） ── */
.btn-event-amount {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.32rem 0.7rem;
  background: linear-gradient(135deg, rgba(16,185,129,0.08), rgba(20,184,166,0.08));
  color: #059669;
  border: 1px solid rgba(16,185,129,0.3);
  border-radius: 4px;
  font-size: 0.74rem;
  cursor: pointer;
  transition: all 0.18s;
  font-weight: 500;
}
.btn-event-amount:hover:not(:disabled) {
  background: linear-gradient(135deg, rgba(16,185,129,0.15), rgba(20,184,166,0.15));
  border-color: rgba(16,185,129,0.5);
}
.btn-event-amount:disabled {
  cursor: wait;
  opacity: 0.7;
}
.amount-summary {
  font-weight: 700;
  padding-left: 0.3rem;
  border-left: 1px solid currentColor;
  margin-left: 0.2rem;
}
.amount-positive {
  color: #16a34a;
}
.amount-negative {
  color: #dc2626;
}
.event-amount-content {
  width: 100%;
  margin-top: 0.5rem;
  padding: 0.5rem 0.65rem;
  background: var(--color-bg-tertiary);
  border-radius: 4px;
  border-left: 3px solid #059669;
  font-size: 0.78rem;
}
.amount-row {
  display: grid;
  grid-template-columns: 2fr 0.8fr 1fr 1.2fr;
  gap: 0.4rem;
  padding: 0.22rem 0;
  border-bottom: 1px dashed var(--color-border);
  align-items: center;
}
.amount-row:last-child {
  border-bottom: none;
}
.amount-row.amount-header {
  font-size: 0.66rem;
  color: var(--color-text-tertiary);
  font-weight: 600;
  border-bottom: 1px solid var(--color-border);
  padding-bottom: 0.3rem;
}
.amount-name {
  color: var(--color-text-primary);
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.event-amount-reason {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  font-size: 0.7rem;
  color: var(--color-text-tertiary);
  background: var(--color-bg-tertiary);
  padding: 0.2rem 0.5rem;
  border-radius: 3px;
}
.event-impact-content {
  width: 100%;
  margin-top: 0.5rem;
  padding: 0.7rem 0.85rem;
  background: var(--color-bg-tertiary);
  border-radius: 4px;
  font-size: 0.82rem;
  line-height: 1.6;
  color: var(--color-text-primary);
  border-left: 3px solid #4f46e5;
}
.event-impact-content :deep(h3) {
  font-size: 0.85rem;
  margin: 0.4rem 0 0.2rem;
  color: #4f46e5;
}
.event-impact-content :deep(h3:first-child) {
  margin-top: 0;
}
.event-impact-content :deep(ul),
.event-impact-content :deep(ol) {
  padding-left: 1.2rem;
  margin: 0.3rem 0;
}
.event-impact-content :deep(li) {
  margin: 0.15rem 0;
}
.event-impact-content :deep(strong) {
  color: var(--color-text-primary);
}

/* ── Batch1 增强点 1：上车设置弹窗 ── */
.entry-dialog-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}
.entry-dialog-box {
  width: 92%;
  max-width: 480px;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  box-shadow: 0 8px 32px rgba(0,0,0,0.2);
  display: flex;
  flex-direction: column;
  max-height: 90vh;
}
.entry-dialog-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.85rem 1rem;
  border-bottom: 1px solid var(--color-border);
}
.entry-dialog-icon {
  color: #2563eb;
}
.entry-dialog-title {
  font-size: 0.95rem;
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0;
  flex: 1;
  display: flex;
  align-items: center;
  gap: 0.35rem;
}
.entry-dialog-subtitle {
  font-size: 0.85rem;
  color: var(--color-text-secondary);
  font-weight: 400;
}
.entry-dialog-close {
  background: transparent;
  border: none;
  color: var(--color-text-tertiary);
  cursor: pointer;
  padding: 0.2rem;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 3px;
}
.entry-dialog-close:hover:not(:disabled) {
  background: var(--color-bg-tertiary);
  color: var(--color-text-primary);
}
.entry-dialog-close:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}
.entry-dialog-body {
  padding: 0.85rem 1rem;
  overflow-y: auto;
}
.entry-dialog-hint {
  font-size: 0.78rem;
  color: var(--color-text-secondary);
  line-height: 1.6;
  margin: 0 0 0.7rem;
}
.entry-form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.65rem 0.7rem;
}
.entry-form-row {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}
.entry-form-label {
  font-size: 0.74rem;
  color: var(--color-text-secondary);
  font-weight: 500;
}
.entry-form-input {
  padding: 0.4rem 0.55rem;
  border: 1px solid var(--color-border);
  border-radius: 4px;
  font-size: 0.85rem;
  background: var(--color-bg-input, var(--color-bg-card));
  color: var(--color-text-primary);
  outline: none;
  transition: border-color 0.15s;
}
.entry-form-input:focus {
  border-color: #2563eb;
}
.entry-form-input:disabled {
  background: var(--color-bg-tertiary);
  cursor: not-allowed;
}
.entry-form-tip {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  margin-top: 0.65rem;
  font-size: 0.74rem;
  color: var(--color-text-secondary);
}
.entry-form-tip .tip-up { color: #dc2626; font-weight: 600; }
.entry-form-tip .tip-down { color: #16a34a; font-weight: 600; }
.entry-dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
  padding: 0.75rem 1rem;
  border-top: 1px solid var(--color-border);
}
.btn-entry-cancel,
.btn-entry-confirm {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.4rem 0.85rem;
  border-radius: 4px;
  font-size: 0.82rem;
  cursor: pointer;
  transition: all 0.15s;
  border: 1px solid;
}
.btn-entry-cancel {
  background: var(--color-bg-card);
  color: var(--color-text-secondary);
  border-color: var(--color-border);
}
.btn-entry-cancel:hover:not(:disabled) {
  background: var(--color-bg-tertiary);
}
.btn-entry-confirm {
  background: #2563eb;
  color: #fff;
  border-color: #2563eb;
  font-weight: 500;
}
.btn-entry-confirm:hover:not(:disabled) {
  background: #1d4ed8;
  border-color: #1d4ed8;
}
.btn-entry-confirm:disabled,
.btn-entry-cancel:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}
@media (max-width: 540px) {
  .entry-form-grid { grid-template-columns: 1fr; }
  .entry-dialog-box { width: 96%; }
}

/* ── 关注列表卡片 ── */
.watchlist-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 0.75rem;
}
.wl-card {
  position: relative;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border-light);
  border-radius: 8px;
  padding: 0.9rem 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  transition: all 0.15s;
  border-left: 3px solid var(--color-border);
}
.wl-card:hover {
  border-color: var(--color-border);
  box-shadow: 0 2px 8px rgba(0,0,0,0.04);
}
.wl-card.sig-hit { border-left-color: #16a34a; }
.wl-card.sig-warn { border-left-color: #ea580c; }
.wl-card.sig-bad { border-left-color: #dc2626; }
.wl-card.sig-waiting { border-left-color: var(--color-text-tertiary); }
.wl-card.sig-neutral { border-left-color: var(--color-border); }

.wl-card-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.5rem;
}
.wl-name-block {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  min-width: 0;
  flex: 1;
  flex-wrap: wrap;
}
.wl-icon { color: #ea580c; flex-shrink: 0; }
.wl-fund-name {
  font-size: 0.88rem;
  font-weight: 600;
  color: var(--color-text-primary);
  line-height: 1.3;
  word-break: break-all;
}
.wl-joined-date {
  font-size: 0.68rem;
  color: var(--color-text-tertiary);
  font-weight: 400;
  white-space: nowrap;
}
/* 优先级标签 */
.wl-priority-tag {
  font-size: 0.62rem;
  padding: 0.08rem 0.32rem;
  border-radius: 3px;
  font-weight: 600;
  white-space: nowrap;
  line-height: 1.4;
}
.wl-priority-high {
  background: rgba(220,38,38,0.1);
  color: #dc2626;
}
.wl-priority-mid {
  background: rgba(234,88,12,0.1);
  color: #ea580c;
}
/* 已买入角标 */
.wl-bought-badge {
  position: absolute;
  top: 0;
  right: 0;
  background: #16a34a;
  color: #fff;
  font-size: 0.62rem;
  font-weight: 600;
  padding: 0.12rem 0.5rem;
  border-radius: 0 8px 0 6px;
}
.wl-card.wl-card-bought {
  opacity: 0.78;
  background: var(--color-bg-secondary);
}
.wl-target-set-date {
  font-size: 0.68rem;
  color: var(--color-text-tertiary);
  font-weight: 400;
}
/* 排序工具栏 */
.wl-sort-bar {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.75rem;
  flex-wrap: wrap;
}
.wl-sort-label {
  font-size: 0.75rem;
  color: var(--color-text-tertiary);
  white-space: nowrap;
}
.wl-sort-group {
  display: flex;
  gap: 0.25rem;
  flex-wrap: wrap;
}
.wl-sort-btn {
  font-size: 0.72rem;
  padding: 0.25rem 0.6rem;
  border: 1px solid var(--color-border);
  background: var(--color-bg-card);
  color: var(--color-text-secondary);
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.15s;
  white-space: nowrap;
}
.wl-sort-btn:hover {
  border-color: var(--color-text-tertiary);
  color: var(--color-text-primary);
}
.wl-sort-btn.active {
  background: #ea580c;
  border-color: #ea580c;
  color: #fff;
}
/* 类型筛选下拉 */
.wl-category-filter {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
}
.wl-category-select {
  font-size: 0.72rem;
  padding: 0.25rem 0.45rem;
  border: 1px solid var(--color-border);
  background: var(--color-bg-card);
  color: var(--color-text-secondary);
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.15s;
  outline: none;
}
.wl-category-select:hover {
  border-color: var(--color-text-tertiary);
}
.wl-category-select:focus {
  border-color: #ea580c;
}
/* 添加关注按钮 */
.btn-wl-add {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.72rem;
  padding: 0.3rem 0.6rem;
  border: 1px solid #16a34a;
  background: rgba(22,163,74,0.06);
  color: #16a34a;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.15s;
  white-space: nowrap;
  margin-left: auto;
}
.btn-wl-add:hover:not(:disabled) {
  background: #16a34a;
  color: #fff;
}
.btn-wl-add:disabled { opacity: 0.6; cursor: not-allowed; }
/* 卡片操作区"已买入"按钮 */
.btn-wl-bought {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  font-size: 0.7rem;
  padding: 0.22rem 0.5rem;
  border: 1px solid #16a34a;
  background: transparent;
  color: #16a34a;
  border-radius: 3px;
  cursor: pointer;
  transition: all 0.15s;
}
.btn-wl-bought:hover:not(:disabled) {
  background: rgba(22,163,74,0.08);
}
.btn-wl-bought.btn-wl-bought-done {
  border-color: var(--color-border);
  background: var(--color-bg-tertiary);
  color: var(--color-text-tertiary);
  cursor: default;
}
.wl-signal-tag {
  flex-shrink: 0;
  font-size: 0.68rem;
  padding: 0.18rem 0.5rem;
  border-radius: 3px;
  font-weight: 500;
  white-space: nowrap;
}
.wl-signal-tag.sig-hit {
  background: rgba(22,163,74,0.1);
  color: #16a34a;
}
.wl-signal-tag.sig-warn {
  background: rgba(234,88,12,0.1);
  color: #ea580c;
}
.wl-signal-tag.sig-bad {
  background: rgba(220,38,38,0.1);
  color: #dc2626;
}
.wl-signal-tag.sig-waiting {
  background: var(--color-bg-tertiary);
  color: var(--color-text-secondary);
}
.wl-signal-tag.sig-neutral {
  background: var(--color-bg-tertiary);
  color: var(--color-text-tertiary);
}
/* Batch1 增强点 1：止盈/止损信号灯（6 档扩展） */
.wl-signal-tag.sig-profit {
  background: rgba(37,99,235,0.12);
  color: #2563eb;
  font-weight: 600;
}
.wl-signal-tag.sig-stop {
  background: rgba(220,38,38,0.15);
  color: #dc2626;
  font-weight: 600;
}
.wl-card.sig-profit {
  border-left-color: #2563eb;
  box-shadow: 0 0 0 1px rgba(37,99,235,0.15);
}
.wl-card.sig-stop {
  border-left-color: #dc2626;
  box-shadow: 0 0 0 1px rgba(220,38,38,0.2);
}
/* 退出信号徽章 */
.wl-exit-badge {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  gap: 0.2rem;
  font-size: 0.68rem;
  padding: 0.18rem 0.5rem;
  border-radius: 3px;
  font-weight: 600;
  white-space: nowrap;
  animation: pulse-badge 1.6s ease-in-out infinite;
}
.wl-exit-badge.exit-profit_target {
  background: rgba(37,99,235,0.15);
  color: #2563eb;
  border: 1px solid rgba(37,99,235,0.3);
}
.wl-exit-badge.exit-stop_loss {
  background: rgba(220,38,38,0.18);
  color: #dc2626;
  border: 1px solid rgba(220,38,38,0.4);
}
@keyframes pulse-badge {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.7; }
}
/* 异常波动徽章 */
.wl-volatility-badge {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  gap: 0.2rem;
  font-size: 0.68rem;
  padding: 0.18rem 0.5rem;
  border-radius: 3px;
  font-weight: 500;
  white-space: nowrap;
}
.wl-volatility-badge.vol-severe {
  background: rgba(220,38,38,0.12);
  color: #dc2626;
  border: 1px solid rgba(220,38,38,0.25);
}
.wl-volatility-badge.vol-warning {
  background: rgba(234,88,12,0.1);
  color: #ea580c;
  border: 1px solid rgba(234,88,12,0.2);
}
/* 上车设置按钮 */
.btn-wl-entry {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  padding: 0.25rem 0.55rem;
  background: rgba(37,99,235,0.08);
  color: #2563eb;
  border: 1px solid rgba(37,99,235,0.2);
  border-radius: 4px;
  font-size: 0.7rem;
  cursor: pointer;
  transition: all 0.15s;
}
.btn-wl-entry:hover {
  background: rgba(37,99,235,0.15);
  border-color: rgba(37,99,235,0.4);
}

.wl-card-body {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}
.wl-data-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  font-size: 0.78rem;
}
.wl-data-label {
  color: var(--color-text-tertiary);
  flex-shrink: 0;
}
.wl-data-value {
  color: var(--color-text-primary);
  font-weight: 500;
  text-align: right;
  font-family: var(--font-jet);
}
.wl-data-value.value-hit {
  color: #16a34a;
  font-weight: 600;
}
.wl-index-name {
  font-family: inherit;
  font-size: 0.75rem;
  color: var(--color-text-secondary);
}
.wl-notes {
  font-family: inherit;
  font-size: 0.72rem;
  color: var(--color-text-tertiary);
  font-style: italic;
}

/* 来源事件说明 */
.wl-source-row {
  display: flex;
  align-items: flex-start;
  gap: 0.35rem;
  padding: 0.4rem 0.55rem;
  background: rgba(59,130,246,0.05);
  border-radius: 4px;
  border-left: 2px solid rgba(59,130,246,0.3);
}
.wl-source-icon { color: #2563eb; flex-shrink: 0; margin-top: 1px; }
.wl-source-text {
  font-size: 0.72rem;
  color: var(--color-text-secondary);
  line-height: 1.5;
}
/* notes 中的事件标题链接 */
.wl-source-text :deep(.wl-note-event-link) {
  color: #2563eb;
  text-decoration: underline;
  cursor: pointer;
  font-weight: 500;
}
.wl-source-text :deep(.wl-note-event-link:hover) {
  color: #1d4ed8;
}

/* 估值分位提示 */
.wl-pct-hint {
  font-size: 0.65rem;
  padding: 0.1rem 0.35rem;
  border-radius: 2px;
  margin-left: 0.3rem;
  background: rgba(22,163,74,0.1);
  color: #16a34a;
  font-weight: 600;
}
.wl-pct-high {
  background: rgba(220,38,38,0.1);
  color: #dc2626;
}
/* PE/PB 标签 */
.wl-pe-pb {
  font-size: 0.65rem;
  padding: 0.1rem 0.35rem;
  border-radius: 2px;
  margin-left: 0.3rem;
  background: rgba(37,99,235,0.08);
  color: #2563eb;
  font-weight: 500;
  white-space: nowrap;
}
/* 今日涨跌幅 */
.wl-change-tag {
  font-size: 0.65rem;
  padding: 0.05rem 0.3rem;
  border-radius: 2px;
  margin-left: 0.3rem;
  font-weight: 600;
  white-space: nowrap;
}
.wl-change-up {
  background: rgba(220,38,38,0.1);
  color: #dc2626;
}
.wl-change-down {
  background: rgba(22,163,74,0.1);
  color: #16a34a;
}
/* 距目标涨幅 */
.wl-distance-tag {
  font-size: 0.65rem;
  padding: 0.05rem 0.3rem;
  border-radius: 2px;
  margin-left: 0.3rem;
  font-weight: 600;
  white-space: nowrap;
}
.wl-distance-hit {
  background: rgba(22,163,74,0.1);
  color: #16a34a;
}
.wl-distance-wait {
  background: rgba(234,88,12,0.1);
  color: #ea580c;
}
.wl-value-mute {
  color: var(--color-text-tertiary);
  font-style: italic;
}

/* ── 上车时机区块 ── */
.wl-buy-timing {
  margin: 0.4rem 0;
  padding: 0.6rem;
  background: var(--color-bg-secondary);
  border-radius: 6px;
  border: 1px solid var(--color-border-light);
}
.wl-buy-signal {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  font-size: 0.8rem;
  font-weight: 700;
  margin-bottom: 0.4rem;
  padding-bottom: 0.35rem;
  border-bottom: 1px dashed var(--color-border-light);
}
.wl-buy-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.wl-buy-label {
  white-space: nowrap;
}
.wl-buy-reason {
  font-size: 0.65rem;
  font-weight: 400;
  color: var(--color-text-tertiary);
  margin-left: auto;
  text-align: right;
}
/* P0-1（2026-07-21）信号置信度小标签 */
.wl-buy-confidence {
  font-size: 0.62rem;
  font-weight: 600;
  padding: 0.05rem 0.3rem;
  border-radius: 4px;
  margin-left: 0.3rem;
  white-space: nowrap;
}
.wl-buy-confidence.conf-high { background: #dcfce7; color: #16a34a; }
.wl-buy-confidence.conf-mid { background: #fef3c7; color: #d97706; }
.wl-buy-confidence.conf-low { background: #fee2e2; color: #dc2626; }

/* P0-1（2026-07-21）多维信号徽章 */
.wl-multidim-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem;
  margin-top: 0.25rem;
}
.wl-md-badge {
  font-size: 0.6rem;
  font-weight: 500;
  padding: 0.05rem 0.35rem;
  border-radius: 8px;
  white-space: nowrap;
}
.wl-md-badge.md-bull { background: #dcfce7; color: #16a34a; }
.wl-md-badge.md-bear { background: #fee2e2; color: #dc2626; }
.wl-md-badge.md-neutral { background: #f3f4f6; color: var(--color-text-tertiary); }
.wl-buy-timing .sig-hit .wl-buy-dot { background: #16a34a; }
.wl-buy-timing .sig-hit .wl-buy-label { color: #16a34a; }
.wl-buy-timing .sig-warn .wl-buy-dot { background: #ea580c; }
.wl-buy-timing .sig-warn .wl-buy-label { color: #ea580c; }
.wl-buy-timing .sig-bad .wl-buy-dot { background: #dc2626; }
.wl-buy-timing .sig-bad .wl-buy-label { color: #dc2626; }
.wl-buy-timing .sig-neutral .wl-buy-dot { background: var(--color-text-tertiary); }
.wl-buy-timing .sig-neutral .wl-buy-label { color: var(--color-text-tertiary); }
.wl-buy-row {
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
  font-size: 0.75rem;
  margin-top: 0.25rem;
}
.wl-buy-source {
  font-size: 0.65rem;
  color: var(--color-text-tertiary);
  font-weight: 400;
}
.wl-pct-hint {
  font-size: 0.62rem;
  color: var(--color-text-tertiary);
  font-weight: 400;
  margin-left: 0.2rem;
}
.wl-pct-hint.wl-pct-high {
  color: #dc2626;
}
.wl-buy-hint {
  font-size: 0.7rem;
  color: var(--color-text-tertiary);
  display: flex;
  align-items: center;
  gap: 0.3rem;
  margin-top: 0.2rem;
}

/* ── 买入评分区块 ── */
.wl-score-block {
  margin-top: 0.3rem;
  padding: 0.6rem;
  background: var(--color-bg-secondary);
  border-radius: 6px;
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
}
.wl-score-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
}
.wl-score-title {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--color-text-secondary);
}
.wl-score-title-icon { color: #ea580c; }
.wl-score-summary {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
}
.wl-score-value {
  font-size: 1.15rem;
  font-weight: 700;
  font-family: var(--font-jet);
  line-height: 1;
}
.wl-score-rating {
  font-size: 0.7rem;
  padding: 0.12rem 0.4rem;
  border-radius: 3px;
  font-weight: 600;
}
.wl-score-loading {
  font-size: 0.72rem;
  color: var(--color-text-tertiary);
  font-style: italic;
}

/* 评分色阶：>=80绿 / >=60蓝 / >=40橙 / <40红 */
.score-excellent { color: #16a34a; }
.score-excellent.wl-score-rating { background: rgba(22,163,74,0.12); color: #16a34a; }
.score-excellent.wl-dim-fill { background: #16a34a; }

.score-good { color: #2563eb; }
.score-good.wl-score-rating { background: rgba(37,99,235,0.12); color: #2563eb; }
.score-good.wl-dim-fill { background: #2563eb; }

.score-normal { color: #d97706; }
.score-normal.wl-score-rating { background: rgba(217,119,6,0.12); color: #d97706; }
.score-normal.wl-dim-fill { background: #d97706; }

.score-cautious { color: #dc2626; }
.score-cautious.wl-score-rating { background: rgba(220,38,38,0.12); color: #dc2626; }
.score-cautious.wl-dim-fill { background: #dc2626; }

/* 维度细分 */
.wl-score-dims {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}
.wl-score-dim {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}
.wl-dim-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 0.7rem;
}
.wl-dim-label {
  color: var(--color-text-secondary);
  font-weight: 500;
}
.wl-dim-weight {
  color: var(--color-text-muted);
  font-size: 0.66rem;
  font-family: var(--font-jet);
}
.wl-dim-bar {
  height: 5px;
  background: var(--color-bg-tertiary);
  border-radius: 3px;
  overflow: hidden;
}
.wl-dim-fill {
  height: 100%;
  border-radius: 3px;
  transition: width 0.3s ease;
}
.wl-dim-foot {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.68rem;
}
.wl-dim-score {
  font-weight: 700;
  font-family: var(--font-jet);
  min-width: 22px;
}
.wl-dim-reason {
  color: var(--color-text-tertiary);
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 编辑目标面板 */
.wl-edit-panel {
  margin-top: 0.4rem;
  padding: 0.6rem;
  background: var(--color-bg-secondary);
  border-radius: 6px;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.wl-edit-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.75rem;
}
.wl-edit-row label {
  width: 75px;
  color: var(--color-text-tertiary);
  flex-shrink: 0;
}
.wl-edit-input {
  flex: 1;
  padding: 0.3rem 0.5rem;
  border: 1px solid var(--color-border);
  border-radius: 4px;
  background: var(--color-bg-card);
  color: var(--color-text-primary);
  font-size: 0.78rem;
  font-family: var(--font-jet);
}
.wl-edit-input:focus {
  outline: none;
  border-color: var(--color-primary);
}
.wl-edit-actions {
  display: flex;
  gap: 0.4rem;
  justify-content: flex-end;
}
.btn-wl-save {
  padding: 0.25rem 0.7rem;
  border: none;
  background: var(--color-primary);
  color: white;
  border-radius: 4px;
  font-size: 0.72rem;
  cursor: pointer;
}
.btn-wl-save:hover { opacity: 0.9; }
.btn-wl-cancel {
  padding: 0.25rem 0.7rem;
  border: 1px solid var(--color-border);
  background: var(--color-bg-card);
  color: var(--color-text-secondary);
  border-radius: 4px;
  font-size: 0.72rem;
  cursor: pointer;
}

/* 底部操作区 */
.wl-footer-actions {
  display: flex;
  gap: 0.3rem;
}
.btn-wl-edit {
  display: inline-flex;
  align-items: center;
  gap: 0.2rem;
  padding: 0.2rem 0.5rem;
  border: none;
  background: transparent;
  color: var(--color-text-tertiary);
  font-size: 0.7rem;
  cursor: pointer;
  border-radius: 3px;
  transition: all 0.15s;
}
.btn-wl-edit:hover {
  color: var(--color-primary);
  background: rgba(59,130,246,0.06);
}

.wl-card-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-top: 0.4rem;
  border-top: 1px dashed var(--color-border-light);
}
.wl-updated {
  font-size: 0.7rem;
  color: var(--color-text-tertiary);
  font-family: var(--font-jet);
}
.wl-updated-stale { color: #d97706; }
.btn-wl-remove {
  display: inline-flex;
  align-items: center;
  gap: 0.2rem;
  padding: 0.2rem 0.5rem;
  border: none;
  background: transparent;
  color: var(--color-text-tertiary);
  font-size: 0.7rem;
  cursor: pointer;
  border-radius: 3px;
  transition: all 0.15s;
}
.btn-wl-remove:hover {
  color: #dc2626;
  background: rgba(220,38,38,0.06);
}

/* 关联持仓中的关注列表项样式 */
.holding-item.holding-item-watchlist {
  background: rgba(234,88,12,0.04);
}
.holding-item.holding-item-watchlist .holding-name {
  color: #ea580c;
}

/* 响应式补充 */
@media (max-width: 768px) {
  .stats-row { grid-template-columns: repeat(2, 1fr); }
  .stats-row-3 { grid-template-columns: repeat(3, 1fr); }
  .stats-row-4 { grid-template-columns: repeat(2, 1fr); }
  .stats-row-6 { grid-template-columns: repeat(3, 1fr); }
  .watchlist-grid { grid-template-columns: 1fr; }
  .main-tab { padding: 0.45rem 0.6rem; font-size: 0.78rem; }
  .stat-card { padding: 0.6rem 0.5rem; }
  .stat-value { font-size: 1.1rem; }
  .stat-label { font-size: 0.68rem; }
}

/* ── 文章分析 URL 输入弹窗 ── */
.article-dialog-backdrop {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.4);
  backdrop-filter: blur(4px);
}
.article-dialog-box {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: 10px;
  box-shadow: 0 10px 40px rgba(0, 0, 0, 0.15);
  width: 100%;
  max-width: 480px;
  margin: 0 1rem;
  overflow: hidden;
}
.article-dialog-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 1rem 1.25rem;
  border-bottom: 1px solid var(--color-border-light);
}
.article-dialog-icon {
  color: #7c3aed;
  flex-shrink: 0;
}
.article-dialog-title {
  flex: 1;
  font-size: 0.95rem;
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0;
}
.article-dialog-close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border: none;
  background: transparent;
  color: var(--color-text-tertiary);
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.15s;
}
.article-dialog-close:hover:not(:disabled) {
  background: var(--color-bg-secondary);
  color: var(--color-text-primary);
}
.article-dialog-close:disabled { opacity: 0.5; cursor: not-allowed; }
.article-dialog-body {
  padding: 1rem 1.25rem;
}
.article-dialog-hint {
  font-size: 0.78rem;
  color: var(--color-text-tertiary);
  line-height: 1.6;
  margin: 0 0 0.75rem 0;
}
.article-url-input {
  width: 100%;
  padding: 0.6rem 0.75rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-bg-secondary);
  color: var(--color-text-primary);
  font-size: 0.85rem;
  font-family: var(--font-jet);
  transition: border-color 0.15s;
  box-sizing: border-box;
}
.article-url-input:focus {
  outline: none;
  border-color: #7c3aed;
}
.article-url-input:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
/* 添加关注基金表单 */
.wl-add-form {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.wl-add-row {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}
.wl-add-label {
  font-size: 0.75rem;
  color: var(--color-text-secondary);
  font-weight: 500;
}
.wl-add-required {
  color: #dc2626;
}
.article-dialog-actions {
  display: flex;
  gap: 0.5rem;
  padding: 0 1.25rem 1.25rem;
  justify-content: flex-end;
}
.btn-article-cancel {
  padding: 0.5rem 1rem;
  border: 1px solid var(--color-border);
  background: var(--color-bg-card);
  color: var(--color-text-secondary);
  border-radius: 6px;
  font-size: 0.82rem;
  cursor: pointer;
  transition: all 0.15s;
}
.btn-article-cancel:hover:not(:disabled) {
  border-color: var(--color-text-secondary);
}
.btn-article-cancel:disabled { opacity: 0.6; cursor: not-allowed; }
.btn-article-confirm {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.5rem 1rem;
  border: none;
  background: #7c3aed;
  color: white;
  border-radius: 6px;
  font-size: 0.82rem;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s;
}
.btn-article-confirm:hover:not(:disabled) {
  opacity: 0.9;
  transform: translateY(-1px);
}
.btn-article-confirm:disabled { opacity: 0.6; cursor: not-allowed; }

/* ── 基金六维体检报告区块 ── */
.fund-report-block {
  margin-top: 0.3rem;
  padding: 0.6rem;
  background: linear-gradient(135deg, rgba(124,58,237,0.04), rgba(37,99,235,0.04));
  border: 1px solid rgba(124,58,237,0.15);
  border-radius: 6px;
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
}
.fund-report-loading {
  flex-direction: row;
  align-items: center;
  justify-content: center;
  gap: 0.4rem;
  font-size: 0.72rem;
  color: var(--color-text-tertiary);
  font-style: italic;
}
.fund-report-empty {
  align-items: center;
  justify-content: center;
  background: var(--color-bg-secondary);
  border-color: var(--color-border-light);
}
.fund-report-empty-text {
  font-size: 0.72rem;
  color: var(--color-text-muted);
  font-style: italic;
}

.report-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
}
.report-title {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.75rem;
  font-weight: 600;
  color: #7c3aed;
}
.report-title-icon { color: #7c3aed; }
.report-summary {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
}
.report-total-score {
  font-size: 1.2rem;
  font-weight: 700;
  font-family: var(--font-jet);
  line-height: 1;
}
.report-rating-tag {
  font-size: 0.68rem;
  padding: 0.12rem 0.4rem;
  border-radius: 3px;
  font-weight: 600;
}
/* 复用 scoreColorClass 色阶：>=80绿 / >=60蓝 / >=40橙 / <40红 */
.score-excellent.report-rating-tag { background: rgba(22,163,74,0.12); }
.score-good.report-rating-tag { background: rgba(37,99,235,0.12); }
.score-normal.report-rating-tag { background: rgba(217,119,6,0.12); }
.score-cautious.report-rating-tag { background: rgba(220,38,38,0.12); }

/* 操作建议行 */
.report-action-row {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}
.report-action-tag {
  font-size: 0.72rem;
  padding: 0.2rem 0.55rem;
  border-radius: 3px;
  font-weight: 600;
}
.action-strong-buy {
  background: rgba(220,38,38,0.12);
  color: #dc2626;
}
.action-dca {
  background: rgba(234,88,12,0.12);
  color: #ea580c;
}
.action-hold {
  background: rgba(37,99,235,0.12);
  color: #2563eb;
}
.action-reduce {
  background: var(--color-bg-tertiary);
  color: var(--color-text-secondary);
}
.action-wait {
  background: rgba(217,119,6,0.12);
  color: #d97706;
}

/* 六维进度条 */
.report-dims {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}
.report-dim {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}
.report-dim-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 0.7rem;
}
.report-dim-label {
  color: var(--color-text-secondary);
  font-weight: 500;
}
.report-dim-score {
  font-weight: 700;
  font-family: var(--font-jet);
  min-width: 22px;
  text-align: right;
}
.report-dim-bar {
  height: 5px;
  background: var(--color-bg-tertiary);
  border-radius: 3px;
  overflow: hidden;
}
.report-dim-fill {
  height: 100%;
  border-radius: 3px;
  transition: width 0.3s ease;
}
/* 进度条颜色复用 scoreColorClass 色阶 */
.score-excellent.report-dim-fill { background: #16a34a; }
.score-good.report-dim-fill { background: #2563eb; }
.score-normal.report-dim-fill { background: #d97706; }
.score-cautious.report-dim-fill { background: #dc2626; }

/* 段永平视角 */
.report-view {
  display: flex;
  align-items: flex-start;
  gap: 0.35rem;
  padding: 0.4rem 0.5rem;
  background: rgba(124,58,237,0.04);
  border-left: 2px solid rgba(124,58,237,0.3);
  border-radius: 4px;
}
.report-view-icon { color: #7c3aed; flex-shrink: 0; margin-top: 1px; }
.report-view-text {
  font-size: 0.72rem;
  color: var(--color-text-secondary);
  line-height: 1.5;
}

/* 报告操作按钮区 */
.report-actions {
  display: flex;
  gap: 0.4rem;
  margin-top: 0.1rem;
}
.btn-report-refresh,
.btn-report-detail {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  padding: 0.25rem 0.55rem;
  border: 1px solid var(--color-border);
  background: var(--color-bg-card);
  color: var(--color-text-secondary);
  border-radius: 4px;
  font-size: 0.7rem;
  cursor: pointer;
  transition: all 0.15s;
  flex: 1;
  justify-content: center;
}
.btn-report-refresh:hover:not(:disabled) {
  border-color: #7c3aed;
  color: #7c3aed;
  background: rgba(124,58,237,0.04);
}
.btn-report-refresh:disabled { opacity: 0.6; cursor: not-allowed; }
.btn-report-detail:hover {
  border-color: #2563eb;
  color: #2563eb;
  background: rgba(37,99,235,0.04);
}

/* ── 体检报告详情弹窗 ── */
.report-detail-backdrop {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.45);
  backdrop-filter: blur(4px);
}
.report-detail-modal {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: 10px;
  box-shadow: 0 10px 40px rgba(0, 0, 0, 0.18);
  width: 100%;
  max-width: 560px;
  margin: 0 1rem;
  max-height: 80vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.report-detail-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 1rem 1.25rem;
  border-bottom: 1px solid var(--color-border-light);
  flex-shrink: 0;
}
.report-detail-header-icon { color: #7c3aed; flex-shrink: 0; }
.report-detail-title {
  flex: 1;
  font-size: 0.95rem;
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0;
  line-height: 1.4;
}
.report-detail-close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border: none;
  background: transparent;
  color: var(--color-text-tertiary);
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.15s;
  flex-shrink: 0;
}
.report-detail-close:hover {
  background: var(--color-bg-secondary);
  color: var(--color-text-primary);
}
.report-detail-body {
  padding: 1rem 1.25rem;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 0.9rem;
}

/* 综合评分 + 操作建议 */
.report-detail-summary {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  padding-bottom: 0.75rem;
  border-bottom: 1px dashed var(--color-border-light);
}
.report-detail-score-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.report-detail-score-label {
  font-size: 0.78rem;
  color: var(--color-text-tertiary);
}
.report-detail-score-value {
  font-size: 1.15rem;
  font-weight: 700;
  font-family: var(--font-jet);
}
.report-detail-rating {
  font-size: 0.72rem;
  padding: 0.15rem 0.45rem;
  border-radius: 3px;
  font-weight: 600;
}
.score-excellent.report-detail-rating { background: rgba(22,163,74,0.12); }
.score-good.report-detail-rating { background: rgba(37,99,235,0.12); }
.score-normal.report-detail-rating { background: rgba(217,119,6,0.12); }
.score-cautious.report-detail-rating { background: rgba(220,38,38,0.12); }

.report-detail-action-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
}
.report-detail-action-label {
  font-size: 0.78rem;
  color: var(--color-text-tertiary);
}
.report-detail-action-reason {
  font-size: 0.72rem;
  color: var(--color-text-secondary);
  flex: 1;
  min-width: 0;
}

/* 段永平视角 */
.report-detail-view {
  padding: 0.6rem 0.7rem;
  background: rgba(124,58,237,0.04);
  border-left: 3px solid rgba(124,58,237,0.3);
  border-radius: 4px;
}
.report-detail-view-title {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.78rem;
  font-weight: 600;
  color: #7c3aed;
  margin-bottom: 0.3rem;
}
.report-detail-view-text {
  font-size: 0.78rem;
  color: var(--color-text-secondary);
  line-height: 1.6;
  margin: 0;
}

/* 综合建议 */
.report-detail-advice {
  padding: 0.6rem 0.7rem;
  background: var(--color-bg-secondary);
  border-radius: 4px;
}
.report-detail-advice-title {
  font-size: 0.78rem;
  font-weight: 600;
  color: var(--color-text-secondary);
  margin-bottom: 0.3rem;
}
.report-detail-advice-text {
  font-size: 0.78rem;
  color: var(--color-text-secondary);
  line-height: 1.6;
  margin: 0;
}

/* 各维度分区 */
.report-detail-dims {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.report-section {
  padding: 0.6rem 0.7rem;
  background: var(--color-bg-secondary);
  border-radius: 6px;
}
.report-section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  padding-bottom: 0.4rem;
  margin-bottom: 0.4rem;
  border-bottom: 1px solid var(--color-border-light);
}
.report-section-title {
  font-size: 0.82rem;
  font-weight: 600;
  color: var(--color-text-primary);
}
.report-section-score {
  font-size: 0.78rem;
  font-weight: 700;
  font-family: var(--font-jet);
}
.report-section-subs {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.report-sub-item {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}
.report-sub-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
}
.report-sub-name {
  font-size: 0.75rem;
  color: var(--color-text-secondary);
  font-weight: 500;
}
.report-sub-score {
  font-size: 0.72rem;
  color: var(--color-text-tertiary);
  font-family: var(--font-jet);
  flex-shrink: 0;
}
.report-sub-reason {
  font-size: 0.7rem;
  color: var(--color-text-tertiary);
  line-height: 1.5;
}
.report-section-empty {
  font-size: 0.72rem;
  color: var(--color-text-muted);
  font-style: italic;
}

/* ── 季度调仓动作面板（卡片内） ── */
.report-holding-changes {
  padding: 0.45rem 0.5rem;
  background: rgba(59,130,246,0.04);
  border-left: 2px solid rgba(59,130,246,0.3);
  border-radius: 4px;
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}
.hc-header {
  display: flex;
  align-items: center;
  gap: 0.3rem;
}
.hc-header-icon { color: #2563eb; flex-shrink: 0; }
.hc-title {
  font-size: 0.72rem;
  font-weight: 600;
  color: #2563eb;
}
.hc-date-row {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.7rem;
  color: var(--color-text-secondary);
  font-family: var(--font-jet);
}
.hc-date-arrow { color: var(--color-text-tertiary); }
.hc-summary {
  font-size: 0.7rem;
  color: var(--color-text-secondary);
  line-height: 1.5;
}
.hc-changes {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}
.hc-change-item {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.7rem;
  padding: 0.15rem 0;
}
.hc-stock-name {
  flex: 1;
  min-width: 0;
  color: var(--color-text-primary);
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.hc-stock-name-detail {
  display: inline-flex;
  align-items: baseline;
  gap: 0.3rem;
}
.hc-stock-code {
  font-size: 0.65rem;
  color: var(--color-text-muted);
  font-family: var(--font-jet);
}
.hc-action-tag {
  font-size: 0.65rem;
  padding: 0.1rem 0.35rem;
  border-radius: 3px;
  font-weight: 600;
  white-space: nowrap;
  flex-shrink: 0;
}
.hc-action-new { background: rgba(76,175,80,0.12); color: #4caf50; }
.hc-action-increase { background: rgba(33,150,243,0.12); color: #2196f3; }
.hc-action-decrease { background: rgba(255,152,0,0.12); color: #ff9800; }
.hc-action-exit { background: rgba(244,67,54,0.12); color: #f44336; }
.hc-delta {
  font-size: 0.68rem;
  font-family: var(--font-jet);
  font-weight: 600;
  flex-shrink: 0;
  min-width: 40px;
  text-align: right;
}
.hc-delta-up { color: #16a34a; }
.hc-delta-down { color: #dc2626; }
.hc-empty {
  font-size: 0.7rem;
  color: var(--color-text-muted);
  font-style: italic;
  text-align: center;
  padding: 0.2rem 0;
}

/* ── 评级颜色映射（excellent绿 / good蓝 / fair黄 / poor红） ── */
.rating-excellent { color: #4caf50; }
.rating-good { color: #2196f3; }
.rating-fair { color: #ff9800; }
.rating-poor { color: #f44336; }
/* 评级标签背景 */
.ft-rating-tag.rating-excellent { background: rgba(76,175,80,0.12); color: #4caf50; }
.ft-rating-tag.rating-good { background: rgba(33,150,243,0.12); color: #2196f3; }
.ft-rating-tag.rating-fair { background: rgba(255,152,0,0.12); color: #ff9800; }
.ft-rating-tag.rating-poor { background: rgba(244,67,54,0.12); color: #f44336; }

/* ── 弹窗内基本面详情区块 ── */
.report-detail-fundamental,
.report-detail-holding-changes {
  padding: 0.6rem 0.7rem;
  background: var(--color-bg-secondary);
  border-radius: 6px;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.report-detail-section-icon {
  color: #7c3aed;
  flex-shrink: 0;
  margin-right: 0.15rem;
  vertical-align: -1px;
}
.fundamental-coverage {
  font-size: 0.72rem;
  color: var(--color-text-secondary);
}
.fundamental-coverage strong {
  color: var(--color-text-primary);
  font-family: var(--font-jet);
}
.fundamental-table-wrap {
  overflow-x: auto;
  border-radius: 4px;
  border: 1px solid var(--color-border-light);
}
.fundamental-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.7rem;
  white-space: nowrap;
}
.fundamental-table thead {
  background: var(--color-bg-tertiary);
}
.fundamental-table th {
  padding: 0.35rem 0.4rem;
  text-align: center;
  font-weight: 600;
  color: var(--color-text-secondary);
  font-size: 0.68rem;
  border-bottom: 1px solid var(--color-border-light);
}
.fundamental-table th:first-child { text-align: left; }
.fundamental-table td {
  padding: 0.35rem 0.4rem;
  text-align: center;
  color: var(--color-text-secondary);
  border-bottom: 1px solid var(--color-border-light);
}
.fundamental-table tbody tr:last-child td { border-bottom: none; }
.fundamental-table tbody tr:hover { background: var(--color-bg-tertiary); }
.ft-stock-name {
  text-align: left !important;
  color: var(--color-text-primary);
  font-weight: 500;
  max-width: 90px;
  overflow: hidden;
  text-overflow: ellipsis;
}
.ft-pct {
  font-family: var(--font-jet);
  color: var(--color-text-secondary);
}
.ft-score {
  font-family: var(--font-jet);
  font-weight: 600;
  cursor: help;
}
.ft-total {
  font-family: var(--font-jet);
  font-weight: 700;
}
.ft-rating-tag {
  display: inline-block;
  font-size: 0.65rem;
  padding: 0.1rem 0.35rem;
  border-radius: 3px;
  font-weight: 600;
}
.fundamental-advice {
  font-size: 0.72rem;
  color: var(--color-text-secondary);
  line-height: 1.5;
  padding: 0.35rem 0.5rem;
  background: var(--color-bg-card);
  border-radius: 4px;
  border-left: 2px solid rgba(124,58,237,0.3);
}

/* ── 弹窗内调仓动作详情 ── */
.hc-changes-detail {
  gap: 0.3rem;
}
.hc-change-item-detail {
  padding: 0.3rem 0.4rem;
  background: var(--color-bg-card);
  border-radius: 4px;
  font-size: 0.75rem;
}

/* fade 过渡（弹窗） */
.fade-enter-active, .fade-leave-active {
  transition: opacity 0.2s ease;
}
.fade-enter-from, .fade-leave-to {
  opacity: 0;
}

/* ── 大师理念矩阵 ── */
.master-matrix {
  padding: 0.45rem 0.5rem;
  background: rgba(124, 58, 237, 0.04);
  border-left: 2px solid rgba(124, 58, 237, 0.3);
  border-radius: 4px;
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}
.mm-header {
  display: flex;
  align-items: center;
  gap: 0.3rem;
}
.mm-title {
  font-size: 0.72rem;
  font-weight: 600;
  color: var(--color-text-primary);
}
.mm-consensus {
  display: flex;
  align-items: center;
  padding: 0.35rem 0.5rem;
  border-radius: 4px;
  font-size: 0.72rem;
  font-weight: 600;
}
.mm-consensus-text { letter-spacing: 0.01em; }
.mm-consensus-high { background: rgba(76, 175, 80, 0.12); color: #4caf50; }
.mm-consensus-major { background: rgba(33, 150, 243, 0.12); color: #2196f3; }
.mm-consensus-mild { background: rgba(255, 152, 0, 0.12); color: #ff9800; }
.mm-consensus-split { background: rgba(244, 67, 54, 0.12); color: #f44336; }
.mm-conflicts {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.3rem 0.5rem;
  background: rgba(255, 152, 0, 0.1);
  border-radius: 4px;
  font-size: 0.68rem;
  color: #ff9800;
}
.mm-conflict-icon { flex-shrink: 0; }
.mm-conflict-text { line-height: 1.4; }
.mm-dist {
  display: flex;
  gap: 2px;
  height: 6px;
  border-radius: 3px;
  overflow: hidden;
}
.mm-dist-seg {
  flex-basis: 0;
  min-width: 2px;
  border-radius: 2px;
  transition: filter 0.15s;
}
.mm-dist-seg:hover { filter: brightness(1.15); }
.mm-masters {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}
.mm-card {
  border-left: 3px solid var(--mm-accent, #6b7280);
  padding: 0.3rem 0.45rem;
  background: var(--color-bg-card);
  border-radius: 0 4px 4px 0;
  cursor: pointer;
  transition: background 0.15s;
}
.mm-card:hover { background: var(--color-bg-tertiary); }
.mm-card-head {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  flex-wrap: wrap;
}
.mm-master-icon { font-size: 0.85rem; line-height: 1; flex-shrink: 0; }
.mm-master-name {
  font-size: 0.72rem;
  font-weight: 600;
  color: var(--color-text-primary);
  flex-shrink: 0;
}
.mm-score {
  font-size: 0.7rem;
  font-weight: 700;
  font-family: var(--font-jet);
  margin-left: auto;
}
.mm-score-na {
  font-size: 0.62rem;
  font-weight: 600;
  color: var(--color-text-tertiary);
  background: var(--color-bg-tertiary);
  padding: 0.1rem 0.35rem;
  border-radius: 3px;
  margin-left: auto;
}
.mm-action-tag {
  font-size: 0.62rem;
  font-weight: 600;
  padding: 0.1rem 0.4rem;
  border-radius: 3px;
  white-space: nowrap;
  flex-shrink: 0;
}
.mm-reason {
  font-size: 0.68rem;
  color: var(--color-text-secondary);
  line-height: 1.5;
  margin-top: 0.2rem;
}
.mm-detail {
  margin-top: 0.3rem;
  padding-top: 0.3rem;
  border-top: 1px dashed var(--color-border-light);
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}
.mm-detail-row {
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
}
.mm-detail-label {
  font-size: 0.62rem;
  color: var(--color-text-tertiary);
  font-weight: 600;
}
.mm-detail-value {
  font-size: 0.68rem;
  color: var(--color-text-secondary);
  line-height: 1.5;
}
.mm-metrics {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem 0.5rem;
  margin-top: 0.1rem;
}
.mm-metric-row {
  display: inline-flex;
  align-items: baseline;
  gap: 0.25rem;
  font-size: 0.65rem;
}
.mm-metric-label { color: var(--color-text-tertiary); }
.mm-metric-value {
  color: var(--color-text-primary);
  font-weight: 600;
  font-family: var(--font-jet);
}

/* ── 组合智能面板 ── */
.portfolio-intelligence-panel {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border-light);
  border-radius: 8px;
  padding: 1rem 1.1rem;
  margin-bottom: 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.pi-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
}
.pi-header-left {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}
.pi-header-icon { color: #7c3aed; flex-shrink: 0; }
.pi-title {
  font-size: 0.9rem;
  font-weight: 700;
  color: var(--color-text-primary);
  margin: 0;
}
.btn-pi-refresh {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.3rem 0.65rem;
  border: 1px solid var(--color-border);
  background: var(--color-bg-secondary);
  color: var(--color-text-secondary);
  border-radius: 4px;
  font-size: 0.72rem;
  cursor: pointer;
  transition: all 0.15s;
}
.btn-pi-refresh:hover:not(:disabled) {
  border-color: #7c3aed;
  color: #7c3aed;
}
.btn-pi-refresh:disabled { opacity: 0.6; cursor: not-allowed; }

.pi-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  padding: 2rem 1rem;
  color: var(--color-text-tertiary);
  font-size: 0.82rem;
  font-style: italic;
}
.pi-degraded {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  padding: 0.45rem 0.6rem;
  background: rgba(255, 152, 0, 0.08);
  border-left: 2px solid rgba(255, 152, 0, 0.4);
  border-radius: 4px;
  font-size: 0.72rem;
  color: #ff9800;
}

/* 面板内分区 */
.pi-section {
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
}
.pi-section-title {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--color-text-secondary);
  padding-bottom: 0.2rem;
  border-bottom: 1px dashed var(--color-border-light);
}

/* 风险卡片网格（4列） */
.pi-risk-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 0.5rem;
}
.pi-risk-card {
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border-light);
  border-radius: 6px;
  padding: 0.55rem 0.65rem;
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}
.pi-risk-label {
  font-size: 0.68rem;
  color: var(--color-text-tertiary);
  font-weight: 500;
}
.pi-risk-value {
  font-size: 1.05rem;
  font-weight: 700;
  font-family: var(--font-jet);
  line-height: 1.2;
}
.pi-risk-sub {
  font-size: 0.66rem;
  color: var(--color-text-tertiary);
  font-family: var(--font-jet);
}

/* 风险数值色阶 */
.risk-good { color: #16a34a; }
.risk-normal { color: #2563eb; }
.risk-warn { color: #d97706; }
.risk-bad { color: #dc2626; }

/* 组合7维体检 */
.pi-health-summary {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
}
.pi-health-score {
  font-size: 1.5rem;
  font-weight: 700;
  font-family: var(--font-jet);
  line-height: 1;
}
.pi-health-rating {
  font-size: 0.72rem;
  padding: 0.15rem 0.45rem;
  border-radius: 3px;
  font-weight: 600;
}
.score-excellent.pi-health-rating { background: rgba(22,163,74,0.12); }
.score-good.pi-health-rating { background: rgba(37,99,235,0.12); }
.score-normal.pi-health-rating { background: rgba(217,119,6,0.12); }
.score-cautious.pi-health-rating { background: rgba(220,38,38,0.12); }
.pi-health-action {
  font-size: 0.72rem;
  padding: 0.2rem 0.55rem;
  border-radius: 3px;
  font-weight: 600;
}
.pi-health-reason {
  font-size: 0.72rem;
  color: var(--color-text-secondary);
  line-height: 1.5;
  padding: 0.35rem 0.5rem;
  background: var(--color-bg-secondary);
  border-radius: 4px;
}
.pi-health-dims {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}
.pi-health-dim {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}
.pi-health-dim-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 0.7rem;
}
.pi-health-dim-label {
  color: var(--color-text-secondary);
  font-weight: 500;
}
.pi-health-dim-score {
  font-weight: 700;
  font-family: var(--font-jet);
  min-width: 22px;
  text-align: right;
}
.pi-health-dim-bar {
  height: 5px;
  background: var(--color-bg-tertiary);
  border-radius: 3px;
  overflow: hidden;
}
.pi-health-dim-fill {
  height: 100%;
  border-radius: 3px;
  transition: width 0.3s ease;
}
.score-excellent.pi-health-dim-fill { background: #16a34a; }
.score-good.pi-health-dim-fill { background: #2563eb; }
.score-normal.pi-health-dim-fill { background: #d97706; }
.score-cautious.pi-health-dim-fill { background: #dc2626; }

/* 持仓明细表 */
.pi-holdings-table-wrap {
  overflow-x: auto;
  border-radius: 4px;
  border: 1px solid var(--color-border-light);
}
.pi-holdings-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.72rem;
  white-space: nowrap;
}
.pi-holdings-table thead {
  background: var(--color-bg-tertiary);
}
.pi-holdings-table th {
  padding: 0.4rem 0.5rem;
  text-align: left;
  font-weight: 600;
  color: var(--color-text-secondary);
  font-size: 0.68rem;
  border-bottom: 1px solid var(--color-border-light);
}
.pi-holdings-table th:nth-child(n+2) { text-align: center; }
.pi-holdings-table td {
  padding: 0.4rem 0.5rem;
  color: var(--color-text-secondary);
  border-bottom: 1px solid var(--color-border-light);
}
.pi-holdings-table td:nth-child(n+2) { text-align: center; }
.pi-holdings-table tbody tr:last-child td { border-bottom: none; }
.pi-holdings-table tbody tr:hover { background: var(--color-bg-tertiary); }
.pi-ht-name {
  color: var(--color-text-primary);
  font-weight: 500;
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
}
.pi-ht-score {
  font-family: var(--font-jet);
  font-weight: 700;
}
.pi-ht-rating {
  display: inline-block;
  font-size: 0.65rem;
  padding: 0.1rem 0.35rem;
  border-radius: 3px;
  font-weight: 600;
}
.pi-ht-rating.rating-excellent { background: rgba(76,175,80,0.12); color: #4caf50; }
.pi-ht-rating.rating-good { background: rgba(33,150,243,0.12); color: #2196f3; }
.pi-ht-rating.rating-fair { background: rgba(255,152,0,0.12); color: #ff9800; }
.pi-ht-rating.rating-poor { background: rgba(244,67,54,0.12); color: #f44336; }
.pi-ht-action {
  display: inline-block;
  font-size: 0.65rem;
  padding: 0.1rem 0.35rem;
  border-radius: 3px;
  font-weight: 600;
}
.pi-ht-mute {
  color: var(--color-text-muted);
  font-style: italic;
}

/* 响应式：风险卡片在小屏降为2列 */
@media (max-width: 768px) {
  .portfolio-intelligence-panel { padding: 0.75rem 0.8rem; gap: 0.6rem; }
  .pi-title { font-size: 0.85rem; }
  .pi-risk-grid { grid-template-columns: repeat(2, 1fr); }
  .pi-risk-card { padding: 0.45rem 0.55rem; }
  .pi-risk-value { font-size: 0.95rem; }
  .pi-risk-label { font-size: 0.64rem; }
  .pi-risk-sub { font-size: 0.62rem; }
  .pi-health-score { font-size: 1.25rem; }
  .pi-health-reason { font-size: 0.68rem; line-height: 1.45; padding: 0.3rem 0.45rem; }
  .pi-health-dim-head { font-size: 0.66rem; }
  .pi-holdings-table { font-size: 0.68rem; }
  .pi-holdings-table th,
  .pi-holdings-table td { padding: 0.3rem 0.4rem; }
  .pi-ht-name { max-width: 120px; }
}

/* ── 大师决策回测面板 ── */
.master-backtest-panel {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border-light);
  border-radius: 8px;
  padding: 1rem 1.1rem;
  margin-bottom: 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.mb-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
}
.mb-header-left {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}
.mb-header-icon { color: #7c3aed; flex-shrink: 0; }
.mb-title {
  font-size: 0.9rem;
  font-weight: 700;
  color: var(--color-text-primary);
  margin: 0;
}
.btn-mb-refresh {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.3rem 0.65rem;
  border: 1px solid var(--color-border);
  background: var(--color-bg-secondary);
  color: var(--color-text-secondary);
  border-radius: 4px;
  font-size: 0.72rem;
  cursor: pointer;
  transition: all 0.15s;
}
.btn-mb-refresh:hover:not(:disabled) {
  border-color: #7c3aed;
  color: #7c3aed;
}
.btn-mb-refresh:disabled { opacity: 0.6; cursor: not-allowed; }

.mb-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  padding: 2rem 1rem;
  color: var(--color-text-tertiary);
  font-size: 0.82rem;
  font-style: italic;
}

/* 总体统计 */
.mb-overall {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 0.5rem;
}
.mb-overall-card {
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border-light);
  border-radius: 6px;
  padding: 0.55rem 0.65rem;
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  text-align: center;
}
.mb-overall-value {
  font-size: 1.15rem;
  font-weight: 700;
  font-family: var(--font-jet);
  line-height: 1.2;
  color: var(--color-text-primary);
}
.mb-overall-label {
  font-size: 0.68rem;
  color: var(--color-text-tertiary);
  font-weight: 500;
}

/* 面板内分区 */
.mb-section {
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
}
.mb-section-title {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--color-text-secondary);
  padding-bottom: 0.2rem;
  border-bottom: 1px dashed var(--color-border-light);
}

/* 胜率排名 */
.mb-rank-list {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.mb-rank-item {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.4rem 0.5rem;
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border-light);
  border-radius: 6px;
  transition: border-color 0.15s;
}
.mb-rank-best {
  border-color: rgba(76, 175, 80, 0.4);
  background: rgba(76, 175, 80, 0.04);
}
.mb-rank-master {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  width: 5.5rem;
  flex-shrink: 0;
}
.mb-rank-icon { font-size: 0.9rem; line-height: 1; flex-shrink: 0; }
.mb-rank-name {
  font-size: 0.74rem;
  font-weight: 600;
  color: var(--color-text-primary);
}
.mb-rank-badge {
  font-size: 0.6rem;
  font-weight: 600;
  color: #4caf50;
  background: rgba(76, 175, 80, 0.15);
  padding: 0.05rem 0.3rem;
  border-radius: 3px;
}
.mb-rank-bar-wrap {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  flex: 1;
  min-width: 0;
}
.mb-rank-bar {
  flex: 1;
  height: 8px;
  background: var(--color-bg-tertiary);
  border-radius: 4px;
  overflow: hidden;
}
.mb-rank-fill {
  height: 100%;
  border-radius: 4px;
  transition: width 0.3s ease;
}
.mb-rank-rate {
  font-size: 0.72rem;
  font-weight: 700;
  font-family: var(--font-jet);
  min-width: 3rem;
  text-align: right;
}
.mb-rank-verified {
  font-size: 0.66rem;
  color: var(--color-text-tertiary);
  font-family: var(--font-jet);
  flex-shrink: 0;
}

/* 历史决策列表 */
.mb-filter-select {
  margin-left: auto;
  padding: 0.2rem 0.4rem;
  border: 1px solid var(--color-border);
  background: var(--color-bg-secondary);
  color: var(--color-text-primary);
  border-radius: 4px;
  font-size: 0.7rem;
  cursor: pointer;
}
.mb-empty {
  padding: 1.2rem 1rem;
  text-align: center;
  color: var(--color-text-tertiary);
  font-size: 0.78rem;
  font-style: italic;
}
.mb-history-list {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.mb-history-item {
  padding: 0.5rem 0.6rem;
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border-light);
  border-radius: 6px;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}
.mb-hist-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.4rem;
}
.mb-hist-master {
  display: flex;
  align-items: center;
  gap: 0.3rem;
}
.mb-hist-icon { font-size: 0.85rem; line-height: 1; }
.mb-hist-name {
  font-size: 0.74rem;
  font-weight: 600;
  color: var(--color-text-primary);
}
.mb-hist-action {
  font-size: 0.66rem;
  font-weight: 600;
  padding: 0.1rem 0.4rem;
  border-radius: 3px;
}
.mb-hist-fund {
  font-size: 0.74rem;
  color: var(--color-text-primary);
  font-weight: 500;
}
.mb-hist-code {
  font-size: 0.66rem;
  color: var(--color-text-tertiary);
  font-family: var(--font-jet);
}
.mb-hist-score {
  font-size: 0.68rem;
  color: var(--color-text-secondary);
  font-family: var(--font-jet);
}
.mb-hist-reason {
  font-size: 0.7rem;
  color: var(--color-text-secondary);
  line-height: 1.4;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mb-hist-verify {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  flex-wrap: wrap;
  font-size: 0.68rem;
}
.mb-verify-label {
  font-size: 0.62rem;
  font-weight: 600;
  color: var(--color-text-tertiary);
}
.mb-verify-tag {
  display: inline-flex;
  align-items: center;
  gap: 0.15rem;
  padding: 0.05rem 0.35rem;
  border-radius: 3px;
  font-weight: 600;
}
.mb-verify-change {
  font-family: var(--font-jet);
  font-weight: 600;
}
.mb-verify-sep {
  color: var(--color-text-muted);
}
.mb-hist-time {
  font-size: 0.62rem;
  color: var(--color-text-tertiary);
  font-family: var(--font-jet);
}

/* 响应式：小屏大师回测面板适配 */
@media (max-width: 768px) {
  .master-backtest-panel { padding: 0.75rem 0.8rem; gap: 0.6rem; }
  .mb-title { font-size: 0.85rem; }
  .mb-overall { grid-template-columns: repeat(3, 1fr); gap: 0.35rem; }
  .mb-overall-card { padding: 0.4rem 0.35rem; }
  .mb-overall-value { font-size: 0.95rem; }
  .mb-overall-label { font-size: 0.62rem; }
  .mb-rank-item { gap: 0.4rem; padding: 0.35rem 0.4rem; }
  .mb-rank-master { width: 4.2rem; gap: 0.25rem; }
  .mb-rank-name { font-size: 0.7rem; }
  .mb-rank-rate { font-size: 0.68rem; min-width: 2.5rem; }
  .mb-rank-verified { font-size: 0.6rem; }
  .mb-history-item { padding: 0.4rem 0.45rem; gap: 0.2rem; }
  .mb-hist-name { font-size: 0.7rem; }
  .mb-hist-fund { font-size: 0.7rem; }
  .mb-hist-reason { font-size: 0.66rem; line-height: 1.4; white-space: normal; word-break: break-word; }
  .mb-hist-verify { font-size: 0.64rem; }
  .mb-filter-select { font-size: 0.66rem; padding: 0.15rem 0.3rem; }
}
/* 事件雷达联动：关注基金名称可点击 */
.holding-name-clickable {
  color: #ea580c;
  cursor: pointer;
  text-decoration: underline;
  text-decoration-style: dotted;
  text-underline-offset: 2px;
}
.holding-name-clickable:hover {
  color: #dc2626;
}

/* 卡片高亮动画（事件雷达跳转） */
.wl-card-highlight {
  animation: wl-card-pulse 0.6s ease-in-out 2;
}
@keyframes wl-card-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(234, 88, 12, 0.4); }
  50% { box-shadow: 0 0 0 6px rgba(234, 88, 12, 0.1); }
}

/* 可折叠面板（组合智能/大师回测） */
.collapsible-panel {
  border: 1px solid var(--color-border-light);
  border-radius: 8px;
  background: var(--color-bg-card);
  margin-bottom: 0.75rem;
  overflow: hidden;
}
.collapsible-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.6rem 0.9rem;
  cursor: pointer;
  transition: background 0.15s;
  user-select: none;
}
.collapsible-header:hover {
  background: var(--color-bg-secondary);
}
.collapsible-header-left {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}
.collapsible-title {
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--color-text-primary);
}
.collapsible-badge {
  font-size: 0.7rem;
  padding: 0.1rem 0.4rem;
  border-radius: 3px;
  font-weight: 600;
}
.collapsible-badge.score-good {
  background: rgba(22, 163, 74, 0.1);
  color: #16a34a;
}
.collapsible-badge.score-warn {
  background: rgba(234, 88, 12, 0.1);
  color: #ea580c;
}
.collapsible-body {
  border-top: 1px solid var(--color-border-light);
}
.collapsible-body .portfolio-intelligence-panel {
  border: none;
  border-radius: 0;
  margin: 0;
}
.collapsible-body .master-backtest-panel {
  border: none;
  border-radius: 0;
  margin: 0;
}

/* 今日操作建议横幅 */
.daily-advice-banner {
  background: linear-gradient(135deg, #e8f5e9, #c8e6c9);
  border: 1px solid #a5d6a7;
  border-radius: 8px;
  padding: 0.75rem 1rem;
  margin-bottom: 0.75rem;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  flex-wrap: wrap;
}
.da-content {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.82rem;
  color: #2e7d32;
  flex: 1;
  min-width: 0;
}
.da-icon {
  font-size: 1.1rem;
  flex-shrink: 0;
}
.da-text {
  line-height: 1.4;
}
.da-text strong {
  color: #1b5e20;
}
.da-tags {
  display: flex;
  gap: 0.35rem;
  flex-wrap: wrap;
}
.da-tag {
  font-size: 0.68rem;
  padding: 0.2rem 0.5rem;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.15s;
  white-space: nowrap;
  border: 1px solid transparent;
  font-weight: 500;
}
.da-tag-green { background: rgba(22, 163, 74, 0.12); color: #2e7d32; }
.da-tag-yellow { background: rgba(234, 88, 12, 0.1); color: #e65100; }
.da-tag-red { background: rgba(220, 38, 38, 0.08); color: #b71c1c; }
.da-tag-gray { background: rgba(0, 0, 0, 0.06); color: #666; }
/* Batch1 增强点 1：退出信号统计标签 */
.da-tag-profit { background: rgba(37, 99, 235, 0.1); color: #1d4ed8; }
.da-tag-stop { background: rgba(220, 38, 38, 0.12); color: #b91c1c; font-weight: 600; }
.da-tag:hover { filter: brightness(0.9); }
.da-tag.active { border-color: currentColor; }

/* 3列关键指标网格 */
.wl-key-grid {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 0.5rem;
  padding: 0.4rem 0;
}
.wl-key-item {
  text-align: center;
  padding: 0.4rem 0.3rem;
  background: var(--color-bg-secondary);
  border-radius: 6px;
}
.wl-key-label {
  display: block;
  font-size: 0.65rem;
  color: var(--color-text-tertiary);
  margin-bottom: 0.15rem;
}
.wl-key-value {
  font-size: 0.85rem;
  font-weight: 700;
  color: var(--color-text-primary);
  font-family: var(--font-jet);
}
.wl-key-value.value-hit {
  color: #16a34a;
}
.wl-key-value.wl-key-mute {
  color: var(--color-text-tertiary);
  font-weight: 400;
}
.wl-key-hint {
  display: block;
  font-size: 0.6rem;
  font-weight: 500;
  color: #16a34a;
  margin-top: 0.1rem;
}
.wl-key-hint.wl-key-high {
  color: #dc2626;
}

/* 信号理由1句话 */
.wl-signal-reason {
  font-size: 0.72rem;
  color: var(--color-text-secondary);
  padding: 0.35rem 0.5rem;
  background: var(--color-bg-secondary);
  border-radius: 4px;
  line-height: 1.4;
  margin-top: 0.2rem;
}
.wl-signal-reason.sig-hit {
  border-left: 2px solid #16a34a;
}
.wl-signal-reason.sig-warn {
  border-left: 2px solid #ea580c;
}
.wl-signal-reason.sig-bad {
  border-left: 2px solid #dc2626;
}

/* 展开/收起按钮 */
.wl-expand-bar {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  padding: 0.35rem 0;
  font-size: 0.7rem;
  color: var(--color-text-tertiary);
  cursor: pointer;
  user-select: none;
  transition: color 0.15s;
}
.wl-expand-bar:hover {
  color: var(--color-text-primary);
}
.wl-expand-hint {
  font-size: 0.65rem;
  color: var(--color-text-tertiary);
  margin-left: auto;
}

/* 折叠详情区 */
.wl-card-details {
  border-top: 1px solid var(--color-border-light);
  padding-top: 0.5rem;
  margin-top: 0.3rem;
}
</style>
