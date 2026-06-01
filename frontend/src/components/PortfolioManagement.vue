<script setup>
import { ref, computed, onMounted, onActivated, watch } from 'vue'

const maxIndustryPct = computed(() => {
  if (!detailData.value?.industry_allocation?.length) return 100
  return Math.max(...detailData.value.industry_allocation.map(i => i.pct_nav), 1)
})
import {
  getPortfolioSummary, createPortfolio, updatePortfolio, deletePortfolio,
  listPortfolioTransactions, createPortfolioTransaction,
  confirmTransaction, settleTransaction, deletePortfolioTransaction,
  refreshPortfolioPrice,
  lookupFundInfo, getFundHoldings,
  getCashBalance, adjustCashBalance,
  getFundNavHistory,
  getPortfolioDiversification, getHoldingPerformance, getTransactionSummary,
  listAlerts, getUnreadAlertCount, markAlertRead, deleteAlert, generateAlert, scanPortfolioAlerts,
  addTransactionTag, removeTransactionTag, getTransactionTags, clearAllPortfolio, chat,
  runPortfolioAiAnalysis, listPortfolioAiAnalysisRecords,
  getPortfolioAiAnalysisRecord, deletePortfolioAiAnalysisRecord,
  runDiversificationAiSummary, getAiSummaryTodayStatus, getPortfolioPenetration,
  runPanoramaAnalysis, runDeepDiveAnalysis, runTradeReview, runWhatIfAnalysis,
  listPanoramaRecords, listDeepDiveRecords, listTradeReviewRecords, listWhatIfRecords,
  submitAnalysisFeedback,
  getRebalanceConfig, updateRebalanceConfig, getRebalanceConfigHistory, rollbackRebalanceConfig,
} from '../api'
import ConfirmDialog from './ConfirmDialog.vue'
import PieChart from './charts/PieChart.vue'
import LineChart from './charts/LineChart.vue'
import Skeleton from './ui/Skeleton.vue'
import EmptyState from './ui/EmptyState.vue'
import AnalysisCard from './ui/AnalysisCard.vue'
import { renderMarkdown } from '../composables/useMarkdown'

// ── 持仓占比计算 ──
const holdingWeights = computed(() => {
  const total = holdings.value.reduce((s, h) => s + ((h.current_value || 0)), 0)
  if (total <= 0) return []
  return [...holdings.value]
    .map(h => ({
      ...h,
      weight: ((h.current_value || 0) / total * 100),
      weightLabel: ((h.current_value || 0) / total * 100).toFixed(1) + '%',
    }))
    .sort((a, b) => b.weight - a.weight)
})

// ECharts 饼图数据格式
const pieChartData = computed(() => {
  if (!holdingWeights.value.length) return []
  return holdingWeights.value
    .filter(h => h.weight >= 0.5)
    .map(h => ({
      name: h.fund_name || h.fund_code,
      value: h.current_value || 0,
    }))
})

// ── 折叠状态 ──
const showIndexDist = ref(false)
const showHoldingWeight = ref(true)

// ── 今日盈亏（排除已清仓持仓） ──
const todayTotalProfit = computed(() => {
  let total = 0
  for (const h of holdings.value) {
    if ((h.shares || 0) <= 0) continue
    if (h.today_profit != null) total += h.today_profit
  }
  return total
})
const todayProfitLabel = computed(() => {
  const today = new Date().toISOString().slice(0, 10)
  const yesterday = new Date(Date.now() - 86400000).toISOString().slice(0, 10)
  let latest = ''
  for (const h of holdings.value) {
    if (h.price_updated_at && h.price_updated_at > latest) latest = h.price_updated_at
  }
  if (!latest || latest >= today) return { text: '今日盈亏', date: '' }
  if (latest >= yesterday) return { text: '昨日盈亏', date: latest }
  return { text: '盈亏', date: latest }
})

// ── 饼图颜色 ──
const pieColors = ['#c9a84c', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#06b6d4', '#f97316', '#ec4899', '#14b8a6', '#84cc16']

function calcPieSlices(data, total) {
  const slices = []
  let cumulative = 0
  const entries = Object.entries(data).sort((a, b) => b[1] - a[1])
  const cx = 60, cy = 60, r = 50
  entries.forEach(([label, val], i) => {
    const pct = val / total
    const angle = pct * 360
    const startRad = (cumulative - 90) * Math.PI / 180
    const endRad = (cumulative + angle - 90) * Math.PI / 180
    cumulative += angle
    const x1 = cx + r * Math.cos(startRad)
    const y1 = cy + r * Math.sin(startRad)
    const x2 = cx + r * Math.cos(endRad)
    const y2 = cy + r * Math.sin(endRad)
    const largeArc = angle > 180 ? 1 : 0
    const path = angle >= 360
      ? `M ${cx},${cy - r} A ${r},${r} 0 1,1 ${cx - 0.01},${cy - r} Z`
      : `M ${cx},${cy} L ${x1},${y1} A ${r},${r} 0 ${largeArc},1 ${x2},${y2} Z`
    slices.push({ label, value: val, pct: (pct * 100).toFixed(1), path, color: pieColors[i % pieColors.length] })
  })
  return slices
}

// ── 排序 & 筛选 ──
const sortKey = ref('')
const sortOrder = ref(1)
const searchQuery = ref('')
const accountFilter = ref('')  // '' = all
const todayFilter = ref('all')  // 'all' | 'up' | 'down'
const expandedIndexDist = ref(null) // 展开的指数分布详情

const fundsByIndex = computed(() => {
  const map = {}
  for (const h of holdings.value) {
    const idx = h.index_name || '未知'
    if (!map[idx]) map[idx] = []
    map[idx].push(h)
  }
  return map
})

const sortableHoldings = computed(() => {
  let list = [...holdings.value]
  if (accountFilter.value) {
    list = list.filter(h => h.account === accountFilter.value)
  }
  if (todayFilter.value !== 'all') {
    list = list.filter(h => {
      const pct = h.today_change_pct
      if (pct === undefined || pct === null) return false
      return todayFilter.value === 'up' ? pct > 0 : pct < 0
    })
  }
  if (searchQuery.value.trim()) {
    const q = searchQuery.value.trim().toLowerCase()
    list = list.filter(h =>
      (h.fund_name || '').toLowerCase().includes(q) ||
      (h.fund_code || '').toLowerCase().includes(q) ||
      (h.index_name || '').toLowerCase().includes(q)
    )
  }
  if (sortKey.value) {
    list.sort((a, b) => {
      const va = a[sortKey.value] ?? 0
      const vb = b[sortKey.value] ?? 0
      return (va - vb) * sortOrder.value
    })
  }
  return list
})

const activeHoldings = computed(() => sortableHoldings.value.filter(h => (h.shares || 0) > 0))
const closedHoldings = computed(() => sortableHoldings.value.filter(h => !((h.shares || 0) > 0)))
const showClosedHoldings = ref(false)

function toggleSort(key) {
  if (sortKey.value === key) {
    sortOrder.value *= -1
  } else {
    sortKey.value = key
    sortOrder.value = -1
  }
}

function sortIndicator(key) {
  if (sortKey.value !== key) return ''
  return sortOrder.value === -1 ? ' ▼' : ' ▲'
}

// State
const holdings = ref([])
const summary = ref({ holding_count: 0, total_cost: 0, total_value: 0, total_profit: 0, profit_rate: 0 })
const cashBalances = ref({ '小鱼儿': 0, '花无缺': 0 })
const cashInterests = ref({ '小鱼儿': 0, '花无缺': 0 })
const totalCash = computed(() => {
  if (accountFilter.value) {
    return cashBalances.value[accountFilter.value] || 0
  }
  return (cashBalances.value['小鱼儿'] || 0) + (cashBalances.value['花无缺'] || 0)
})
const showCashModal = ref(false)
const cashForm = ref({ amount: 0, user_id: '花无缺' })
const cashMode = ref('add')  // 'add' 存入/支出, 'set' 直接设置

// ECharts 净值曲线数据
const navChartData = computed(() => {
  if (!chartData.value?.nav_history?.length) return { dates: [], series: [] }
  const history = chartData.value.nav_history
  return {
    dates: history.map(d => d.date),
    series: [{
      name: '组合净值',
      data: history.map(d => d.nav),
      color: '#c9a84c',
    }],
  }
})
const loading = ref(false)
const showForm = ref(false)
const editingId = ref(null)
const showTxForm = ref(false)
const txHoldingId = ref(null)
const txFundCode = ref('')
const transactions = ref([])
const showTxHistory = ref(false)

// Fund lookup
const lookupLoading = ref(false)
const lookupResult = ref(null)

// Fund detail panel
const showDetail = ref(false)
const detailLoading = ref(false)
const detailData = ref(null)
const detailFundName = ref('')

// ── 基金 5 年走势图 ──
const chartMode = ref('')  // '' | 'detail' | 'chart5y'
const fundChartData = ref(null)  // 5年净值数据
const fundChartLoading = ref(false)
const chartSearchQuery = ref('')
const chartSearching = ref(false)
const chartSearchError = ref('')

const fundChartStats = computed(() => {
  const data = fundChartData.value
  if (!data?.length) return null
  const first = data[0]
  const last = data[data.length - 1]
  if (!first || !last) return null
  const totalReturn = (last.nav - first.nav) / first.nav
  const years = (data.length - 1) / 365
  const annualReturn = years > 0 ? Math.pow(1 + totalReturn, 1 / years) - 1 : 0
  let maxNav = first.nav, maxDrawdown = 0
  for (const d of data) {
    if (d.nav > maxNav) maxNav = d.nav
    const dd = (d.nav - maxNav) / maxNav
    if (dd < maxDrawdown) maxDrawdown = dd
  }
  return {
    firstDate: first.date,
    lastDate: last.date,
    firstNav: first.nav,
    lastNav: last.nav,
    totalReturn,
    annualReturn,
    maxDrawdown,
    dataPoints: data.length,
  }
})

// ── 5 年走势图计算属性 ──
const chart5yW = 700, chart5yH = 300, chart5yPL = 55, chart5yPR = 20, chart5yPT = 20, chart5yPB = 20

const chart5yRange = computed(() => {
  const data = chart5yDisplayData.value
  if (!data?.length) return { min: -5, max: 5, range: 10 }
  const firstNav = fundChartData.value[0]?.nav
  if (!firstNav) return { min: -5, max: 5, range: 10 }
  let min = Infinity, max = -Infinity
  for (const d of data) {
    const pct = (d.nav - firstNav) / firstNav * 100
    if (pct < min) min = pct
    if (pct > max) max = pct
  }
  const pad = Math.max((max - min) * 0.1, 1)
  return { min: min - pad, max: max + pad, range: max - min + 2 * pad }
})

function chart5yScaleY(pct) {
  const { min, range } = chart5yRange.value
  return chart5yPT + (1 - (pct - min) / range) * (chart5yH - chart5yPT - chart5yPB)
}

function chart5yScaleX(i, total) {
  return chart5yPL + (i / (total - 1 || 1)) * (chart5yW - chart5yPL - chart5yPR)
}

const chart5yGridY = computed(() => {
  const lines = []
  const steps = 5
  const { min, range } = chart5yRange.value
  for (let i = 0; i <= steps; i++) {
    const pct = min + (i / steps) * range
    lines.push(chart5yScaleY(pct))
  }
  return lines
})

const chart5yYLabels = computed(() => {
  const steps = 5
  const { min, range } = chart5yRange.value
  const labels = []
  for (let i = 0; i <= steps; i++) {
    const pct = min + (i / steps) * range
    const y = chart5yScaleY(pct)
    labels.push({ y, label: (pct >= 0 ? '+' : '') + pct.toFixed(1) + '%' })
  }
  return labels
})

const chart5yXLabels = computed(() => {
  const data = chart5yDisplayData.value
  if (!data?.length) return []
  const total = data.length
  const count = 6
  const step = Math.max(1, Math.floor((total - 1) / count))
  const labels = []
  for (let i = 0; i < total; i += step) {
    const d = data[i]
    labels.push({ x: chart5yScaleX(i, total), label: d.date })
  }
  const last = data[total - 1]
  if (labels.length === 0 || labels[labels.length - 1].label !== last.date) {
    labels.push({ x: chart5yScaleX(total - 1, total), label: last.date })
  }
  return labels
})

const chart5yLinePoints = computed(() => {
  const data = chart5yDisplayData.value
  if (!data?.length) return ''
  const firstNav = fundChartData.value[0].nav
  return data.map((d, i) => {
    const pct = (d.nav - firstNav) / firstNav * 100
    return `${chart5yScaleX(i, data.length)},${chart5yScaleY(pct)}`
  }).join(' ')
})

const chart5yZeroY = computed(() => chart5yScaleY(0))

// ── 5 年走势图区域选择（缩放） ──
const chart5yZoomRange = ref(null)  // { startIdx, endIdx } | null
const chart5yBrush = ref(null)      // { active, startX, currentX } | null

const chart5yDisplayData = computed(() => {
  if (!fundChartData.value?.length) return []
  if (!chart5yZoomRange.value) return fundChartData.value
  const { startIdx, endIdx } = chart5yZoomRange.value
  return fundChartData.value.slice(startIdx, endIdx + 1)
})

function chart5yGetDataIdx(vbX) {
  const total = chart5yDisplayData.value.length
  if (!total) return 0
  const plotW = chart5yW - chart5yPL - chart5yPR
  const rawIdx = ((vbX - chart5yPL) / plotW) * (total - 1)
  return Math.max(0, Math.min(total - 1, Math.round(rawIdx)))
}

function chart5yMouseVbX(event, svg) {
  const svgRect = svg.getBoundingClientRect()
  return (event.clientX - svgRect.left) / svgRect.width * chart5yW
}

function onChart5yMouseDown(event) {
  if (!chart5yDisplayData.value?.length) return
  const svg = event.currentTarget
  const vbX = chart5yMouseVbX(event, svg)
  chart5yBrush.value = { active: true, startX: vbX, currentX: vbX }
  chart5yHoverIndex.value = null
}

function onChart5yMouseMove(event) {
  const svg = event.currentTarget
  const vbX = chart5yMouseVbX(event, svg)
  const data = chart5yDisplayData.value
  if (!data?.length) return

  // 正在拖拽选择
  if (chart5yBrush.value?.active) {
    chart5yBrush.value.currentX = vbX
    return
  }

  // 普通 hover
  const total = data.length
  const plotW = chart5yW - chart5yPL - chart5yPR
  const rawIdx = ((vbX - chart5yPL) / plotW) * (total - 1)
  chart5yHoverIndex.value = Math.max(0, Math.min(total - 1, Math.round(rawIdx)))

  const dp = chart5yHoverPoint.value
  if (dp) {
    const svgRect = svg.getBoundingClientRect()
    const xRatio = svgRect.width / chart5yW
    const yRatio = svgRect.height / chart5yH
    chart5yTooltipStyle.value = {
      left: (dp.x * xRatio + 12) + 'px',
      top: (dp.y * yRatio - 10) + 'px',
    }
  }
}

function onChart5yMouseUp(event) {
  if (!chart5yBrush.value?.active) return
  const svg = event.currentTarget
  const vbX = chart5yMouseVbX(event, svg)
  chart5yBrush.value.active = false

  const startX = Math.min(chart5yBrush.value.startX, vbX)
  const endX = Math.max(chart5yBrush.value.startX, vbX)
  const minWidth = (chart5yW - chart5yPL - chart5yPR) * 0.02  // 至少 2% 宽度

  if (endX - startX < minWidth) {
    // 点击而非拖拽，清除缩放
    chart5yBrush.value = null
    return
  }

  const startIdx = chart5yGetDataIdx(startX)
  const endIdx = chart5yGetDataIdx(endX)
  if (startIdx === 0 && endIdx === chart5yDisplayData.value.length - 1) {
    // 选中全部，等同于重置
    chart5yZoomRange.value = null
    chart5yBrush.value = null
    return
  }
  chart5yZoomRange.value = { startIdx, endIdx }
  chart5yBrush.value = null
}

function onChart5yMouseLeave() {
  if (!chart5yBrush.value?.active) {
    chart5yHoverIndex.value = null
    chart5yTooltipStyle.value = {}
  }
}

function resetChart5yZoom() {
  chart5yZoomRange.value = null
  chart5yBrush.value = null
  chart5yHoverIndex.value = null
  chart5yTooltipStyle.value = {}
}

const chart5yBrushRect = computed(() => {
  if (!chart5yBrush.value?.active) return null
  const { startX, currentX } = chart5yBrush.value
  const x = Math.min(startX, currentX)
  const w = Math.abs(currentX - startX)
  // 过小不显示
  if (w < 2) return null
  return { x, y: 20, w, h: 260 }
})

// ── 5 年走势图悬浮交互 ──
const chart5yHoverIndex = ref(null)
const chart5yTooltipStyle = ref({})

const chart5yHoverPoint = computed(() => {
  const data = chart5yDisplayData.value
  if (chart5yHoverIndex.value == null || !data?.length) return null
  const idx = Math.min(chart5yHoverIndex.value, data.length - 1)
  const d = data[idx]
  const firstNav = fundChartData.value[0]?.nav
  if (!firstNav) return null
  const pct = (d.nav - firstNav) / firstNav * 100
  return {
    ...d,
    idx,
    pct,
    x: chart5yScaleX(idx, data.length),
    y: chart5yScaleY(pct),
  }
})

// Add purchase (追加买入) panel — amount-based
const showAddPurchase = ref(false)
const addPurchaseHolding = ref(null)
const addPurchaseForm = ref({
  amount: 0,
  transaction_date: new Date().toISOString().slice(0, 10),
  transaction_time: new Date().toTimeString().slice(0, 5),
  notes: '',
})
const addPurchaseEstShares = computed(() => {
  const price = addPurchaseHolding.value?.current_price
  if (!price || price <= 0) return 0
  return (addPurchaseForm.value.amount / price).toFixed(2)
})

// Sell (卖出) panel — shares-based
const showSell = ref(false)
const sellHolding = ref(null)
const sellForm = ref({
  shares: 0,
  transaction_date: new Date().toISOString().slice(0, 10),
  transaction_time: new Date().toTimeString().slice(0, 5),
  notes: '',
})
const sellEstAmount = computed(() => {
  const price = sellHolding.value?.current_price
  if (!price || price <= 0) return 0
  return (sellForm.value.shares * price).toFixed(2)
})

// Confirm transaction modal
const showConfirmTx = ref(false)
const confirmTxData = ref(null)
const confirmTxPrice = ref(0)

// Pending transactions reminder
const pendingTxs = ref([])

// Alerts
const alerts = ref([])
const unreadAlertCount = ref(0)
const showAlerts = ref(true)

async function clearAllData() {
  confirm.value = {
    visible: true,
    title: '清空持仓数据',
    message: '确定要清空所有持仓、交易记录、预警和标签数据吗？此操作不可撤销！',
    danger: true,
    onConfirm: async () => {
      confirm.value.visible = false
      try {
        await clearAllPortfolio()
        showToast('所有持仓数据已清空')
        loadData()
        alerts.value = []
        unreadAlertCount.value = 0
      } catch (e) {
        showToast('清空失败: ' + e.message, 'error')
      }
    }
  }
}

// Analysis panels (tabbed)
const activeAnalysisTab = ref('holdings')  // 'holdings' | 'diversification' | 'tx' | 'ai'
const diversificationData = ref(null)
const diversificationLoading = ref(false)
const diverShowMcp = ref(false)
const diverAiLoading = ref(false)
const diverAiResult = ref('')
const diverAiRecordId = ref(null)

// ── 费率侵蚀计算器 ──
const feeRate = ref(1.5)
const feeYears = ref(30)
const feePrincipal = ref(100000)
const feeReturn = ref(8)
const showFeeCalc = ref(false)

const feeErosionResult = computed(() => {
  const p = feePrincipal.value
  const r = feeReturn.value / 100
  const f = feeRate.value / 100
  const y = feeYears.value
  const withFee = p * Math.pow(1 + r - f, y)
  const withoutFee = p * Math.pow(1 + r, y)
  const lowerFee = p * Math.pow(1 + r - f + 0.01, y)
  return {
    withFee,
    withoutFee,
    erosion: withoutFee - withFee,
    savingsIfLower: lowerFee - withFee,
    withFeeMultiple: (withFee / p).toFixed(1),
    withoutFeeMultiple: (withoutFee / p).toFixed(1),
  }
})

function feeCurvePoints(withFee, withoutFee, years) {
  const maxVal = withoutFee
  const pts = []
  for (let y = 0; y <= years; y += Math.max(1, Math.floor(years / 60))) {
    const wf = feePrincipal.value * Math.pow(1 + feeReturn.value / 100 - feeRate.value / 100, y)
    const nf = feePrincipal.value * Math.pow(1 + feeReturn.value / 100, y)
    pts.push({ y, wf, nf })
  }
  // Ensure last point
  const lastY = years
  pts.push({
    y: lastY,
    wf: feePrincipal.value * Math.pow(1 + feeReturn.value / 100 - feeRate.value / 100, lastY),
    nf: feePrincipal.value * Math.pow(1 + feeReturn.value / 100, lastY),
  })
  return pts
}

function feeSvgPath(pts, valKey, width, height, maxVal) {
  if (!pts.length) return ''
  return pts.map((p, i) => {
    const x = (i / (pts.length - 1)) * width
    const y = height - (p[valKey] / maxVal) * height
    return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
}

function feeAreaPath(pts, width, height, maxVal) {
  if (!pts.length) return ''
  const line = pts.map((p, i) => {
    const x = (i / (pts.length - 1)) * width
    const y = height - (p.nf / maxVal) * height
    return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
  const bottom = pts.map((p, i) => {
    const x = ((pts.length - 1 - i) / (pts.length - 1)) * width
    const y = height - (p.wf / maxVal) * height
    return `L${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
  return line + ' ' + bottom + ' Z'
}

// ── 持仓穿透 ──
const penetrationData = ref(null)
const penetrationLoading = ref(false)
const showPenetration = ref(false)

async function loadPenetration() {
  penetrationLoading.value = true
  try {
    const { data } = await getPortfolioPenetration()
    penetrationData.value = data
  } catch (e) {
    penetrationData.value = { error: e.message }
  } finally {
    penetrationLoading.value = false
  }
}

function overlapColor(val) {
  if (val >= 0.5) return '#ef4444'
  if (val >= 0.2) return '#f59e0b'
  return '#10b981'
}

function handlePenetration() {
  if (penetrationData.value) {
    confirm.value = {
      visible: true,
      title: '刷新持仓穿透',
      message: '将重新从 akshare 拉取基金持仓数据并计算穿透结果，首次加载可能需要 30-60 秒，是否继续？',
      danger: false,
      onConfirm: () => { confirm.value.visible = false; loadPenetration() }
    }
  } else {
    loadPenetration()
  }
}

const txAnalysisData = ref(null)
const chartFundCode = ref('')
const chartData = ref(null)
const chartLoading = ref(false)
// 基金走势图用——不受账号筛选影响
const allChartFunds = ref([])

// AI analysis
const aiAnalysisInput = ref('')
const aiAnalysisLoading = ref(false)
const aiAnalysisResult = ref('')
const aiRecordId = ref(null)
const aiTokenUsage = ref(0)
const aiMcpSources = ref([])
const aiAnalysisRecords = ref([])
const aiShowHistory = ref(false)

// ── 四模式状态 ──
const aiMode = ref('panorama')  // 'panorama' | 'deepdive' | 'trade-review' | 'what-if'
const modeLoading = ref(false)
const modeResult = ref('')
const modeRecordId = ref(null)
const feedbackGiven = ref(null)  // null | 'helpful' | 'unhelpful'

// 全景诊断
const panoramaRecords = ref([])
const panoramaShowAll = ref(false)

// 单基金深度分析
const deepDiveSelectedHolding = ref('')
const deepDiveRecords = ref([])
const deepDiveShowAll = ref(false)

// 交易复盘
const reviewStartDate = ref('')
const reviewEndDate = ref('')
const tradeReviewRecords = ref([])
const tradeReviewShowAll = ref(false)

// 情景推演
const whatIfScenario = ref('market_drop')
const whatIfParameter = ref(10)
const whatIfRecords = ref([])
const whatIfShowAll = ref(false)
const aiHistoryLoading = ref(false)

// Transaction tags
const editingTxTags = ref(null)  // { txId, tags: [], show: boolean, newTag: '' }

// Toast
const toast = ref({ show: false, message: '', type: 'success' })
function showToast(message, type = 'success') {
  toast.value = { show: true, message, type }
  setTimeout(() => { toast.value.show = false }, 3000)
}

// Confirm dialog
const confirm = ref({ visible: false, title: '', message: '', danger: false, onConfirm: null })

// Form
const form = ref({
  fund_code: '',
  fund_name: '',
  shares: 0,
  total_cost: null,
  current_price: null,
  index_code: '',
  index_name: '',
  buy_date: '',
  notes: '',
  account: '花无缺',
})

// Transaction form
const txForm = ref({
  transaction_type: 'buy',
  amount: 0,
  shares: 0,
  price: 0,
  transaction_date: new Date().toISOString().slice(0, 10),
  notes: '',
})

// Load data
async function loadData() {
  loading.value = true
  try {
    const params = accountFilter.value ? { account: accountFilter.value } : {}
    const { data } = await getPortfolioSummary(params)
    summary.value = data
    holdings.value = data.holdings || []
    // 加载各账户零钱余额
    for (const uid of ['小鱼儿', '花无缺']) {
      try {
        const { data: cashData } = await getCashBalance(uid)
        cashBalances.value[uid] = cashData.balance || 0
        cashInterests.value[uid] = cashData.today_interest || 0
      } catch (_) {}
    }
    // 加载所有待确认交易
    await loadPendingTxs()
    // 加载全部基金列表（用于走势图，不受账号筛选影响）
    await loadAllChartFunds()
  } catch (e) {
    showToast('加载失败: ' + e.message, 'error')
  } finally {
    loading.value = false
  }
}

async function loadAllChartFunds() {
  try {
    const { data } = await getPortfolioSummary()
    allChartFunds.value = (data.holdings || []).map(h => ({
      id: h.id,
      fund_code: h.fund_code,
      fund_name: h.fund_name,
      account: h.account,
      shares: h.shares || 0,
    }))
  } catch (e) { /* ignore */ }
}

async function submitCashAdjust() {
  if (cashMode.value === 'add' && (!cashForm.value.amount || cashForm.value.amount === 0)) {
    showToast('请输入金额', 'warning')
    return
  }
  try {
    const uid = cashForm.value.user_id
    const { data } = await adjustCashBalance(cashForm.value.amount, cashMode.value, uid)
    cashBalances.value[uid] = data.balance
    showCashModal.value = false
    cashForm.value.amount = 0
    showToast('零钱已更新', 'success')
  } catch (e) {
    showToast('操作失败: ' + e.message, 'error')
  }
}

async function loadPendingTxs() {
  const allPending = []
  for (const h of holdings.value) {
    try {
      const { data } = await listPortfolioTransactions(h.id)
      const pending = (data.transactions || []).filter(tx => tx.status === 'pending')
      for (const tx of pending) {
        tx._fund_name = h.fund_name
        tx._fund_code = h.fund_code
      }
      allPending.push(...pending)
    } catch (e) { /* ignore */ }
  }
  pendingTxs.value = allPending
}

// Load alerts
async function loadAlerts() {
  try {
    const { data } = await listAlerts(true, 10)
    alerts.value = data.alerts || []
    const { data: cnt } = await getUnreadAlertCount()
    unreadAlertCount.value = cnt.count
  } catch (e) { /* silently ignore */ }
}

async function handleMarkAlertRead(alertId) {
  try {
    await markAlertRead(alertId)
    alerts.value = alerts.value.filter(a => a.id !== alertId)
    unreadAlertCount.value = Math.max(0, unreadAlertCount.value - 1)
  } catch (e) {
    showToast('操作失败', 'error')
  }
}

async function handleDeleteAlert(alertId) {
  try {
    await deleteAlert(alertId)
    alerts.value = alerts.value.filter(a => a.id !== alertId)
    unreadAlertCount.value = Math.max(0, unreadAlertCount.value - 1)
  } catch (e) {
    showToast('操作失败', 'error')
  }
}

const alertScanning = ref(false)
async function handleScanAlerts() {
  alertScanning.value = true
  try {
    const { data } = await scanPortfolioAlerts()
    if (data.generated > 0) {
      showToast(`巡检完成，发现 ${data.generated} 条新预警`, 'success')
      await loadAlerts()
    } else {
      showToast('巡检完成，未发现新的风险', 'success')
    }
  } catch (e) {
    showToast('巡检失败：' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    alertScanning.value = false
  }
}

// Analysis panels (tabbed)
function switchAnalysisTab(tab) {
  if (activeAnalysisTab.value === tab && tab !== 'holdings') {
    activeAnalysisTab.value = null
    return
  }
  activeAnalysisTab.value = tab
  if (tab === 'diversification') {
    loadDiversification()
  } else if (tab === 'tx') {
    loadTxAnalysis()
  } else if (tab === 'ai') {
    loadAllModeRecords()
  } else if (tab === 'config') {
    loadRebalanceConfig()
  }
}

const aiModes = [
  { key: 'panorama', icon: '🔍', label: '全景诊断' },
  { key: 'deepdive', icon: '🔎', label: '单基金分析' },
  { key: 'trade-review', icon: '📊', label: '交易复盘' },
  { key: 'what-if', icon: '🔮', label: '情景推演' },
]

function switchAiMode(mode) {
  aiMode.value = mode
  modeResult.value = ''
  modeRecordId.value = null
  feedbackGiven.value = null
  aiShowHistory.value = false
}

async function loadAllModeRecords() {
  try {
    const [p, d, t, w] = await Promise.allSettled([
      listPanoramaRecords(10),
      listDeepDiveRecords(10),
      listTradeReviewRecords(10),
      listWhatIfRecords(10),
    ])
    if (p.status === 'fulfilled') panoramaRecords.value = p.value.data.records || []
    if (d.status === 'fulfilled') deepDiveRecords.value = d.value.data.records || []
    if (t.status === 'fulfilled') tradeReviewRecords.value = t.value.data.records || []
    if (w.status === 'fulfilled') whatIfRecords.value = w.value.data.records || []
  } catch (e) { /* ignore */ }
}

async function runPanoramaMode() {
  modeLoading.value = true
  modeResult.value = ''
  modeRecordId.value = null
  aiTokenUsage.value = 0
  try {
    const { data } = await runPanoramaAnalysis()
    modeResult.value = data.result
    modeRecordId.value = data.id
    aiTokenUsage.value = data.token_usage || 0
    loadPanoramaRecords()
  } catch (e) {
    modeResult.value = '分析失败：' + (e.response?.data?.detail || e.message)
  } finally {
    modeLoading.value = false
  }
}

async function loadPanoramaRecords() {
  try {
    const { data } = await listPanoramaRecords(10)
    panoramaRecords.value = data.records || []
  } catch (e) { /* ignore */ }
}

async function runDeepDiveMode() {
  if (!deepDiveSelectedHolding.value) return
  modeLoading.value = true
  modeResult.value = ''
  modeRecordId.value = null
  aiTokenUsage.value = 0
  try {
    const { data } = await runDeepDiveAnalysis(deepDiveSelectedHolding.value)
    modeResult.value = data.result
    modeRecordId.value = data.id
    aiTokenUsage.value = data.token_usage || 0
    loadDeepDiveRecords()
  } catch (e) {
    modeResult.value = '分析失败：' + (e.response?.data?.detail || e.message)
  } finally {
    modeLoading.value = false
  }
}

async function loadDeepDiveRecords() {
  try {
    const { data } = await listDeepDiveRecords(10)
    deepDiveRecords.value = data.records || []
  } catch (e) { /* ignore */ }
}

async function runTradeReviewMode() {
  modeLoading.value = true
  modeResult.value = ''
  modeRecordId.value = null
  aiTokenUsage.value = 0
  try {
    const { data } = await runTradeReview(reviewStartDate.value || null, reviewEndDate.value || null)
    modeResult.value = data.result
    modeRecordId.value = data.id
    aiTokenUsage.value = data.token_usage || 0
    loadTradeReviewRecords()
  } catch (e) {
    modeResult.value = '分析失败：' + (e.response?.data?.detail || e.message)
  } finally {
    modeLoading.value = false
  }
}

async function loadTradeReviewRecords() {
  try {
    const { data } = await listTradeReviewRecords(10)
    tradeReviewRecords.value = data.records || []
  } catch (e) { /* ignore */ }
}

async function runWhatIfMode() {
  modeLoading.value = true
  modeResult.value = ''
  modeRecordId.value = null
  aiTokenUsage.value = 0
  try {
    const param = whatIfScenario.value === 'market_drop' ? whatIfParameter.value : null
    const { data } = await runWhatIfAnalysis(whatIfScenario.value, param)
    modeResult.value = data.result
    modeRecordId.value = data.id
    aiTokenUsage.value = data.token_usage || 0
    loadWhatIfRecords()
  } catch (e) {
    modeResult.value = '分析失败：' + (e.response?.data?.detail || e.message)
  } finally {
    modeLoading.value = false
  }
}

async function loadWhatIfRecords() {
  try {
    const { data } = await listWhatIfRecords(10)
    whatIfRecords.value = data.records || []
  } catch (e) { /* ignore */ }
}

async function viewModeRecord(record) {
  try {
    const resp = await getPortfolioAiAnalysisRecord(record.id)
    modeResult.value = resp.data.result_data || resp.data.result
    modeRecordId.value = record.id
    aiTokenUsage.value = resp.data.token_usage || record.token_usage || 0
  } catch (e) {
    showToast('加载记录失败', 'error')
  }
}

async function loadDiversification() {
  diversificationLoading.value = true
  try {
    const { data } = await getPortfolioDiversification()
    diversificationData.value = data
    diverShowMcp.value = false
  } catch (e) {
    showToast('加载分散度分析失败', 'error')
  } finally {
    diversificationLoading.value = false
  }
}

// ── 调仓策略配置 ──
const rebalanceConfig = ref(null)
const rebalancePresets = ref([])
const rebalanceCurrentStrategy = ref(null)
const rebalanceLoading = ref(false)
const rebalanceEditing = ref(false)
const rebalanceEditData = ref({})

const CATEGORY_LABELS = { equity: '股票型', bond: '债券型', money: '货币型', hybrid: '混合型', index: '指数型', qdii: 'QDII' }

async function loadRebalanceConfig() {
  rebalanceLoading.value = true
  try {
    const { data } = await getRebalanceConfig()
    rebalanceConfig.value = data.config
    rebalancePresets.value = data.presets
    rebalanceCurrentStrategy.value = data.current_strategy
  } catch (e) {
    showToast('加载调仓配置失败', 'error')
  } finally {
    rebalanceLoading.value = false
  }
}

function startEditRebalance() {
  rebalanceEditing.value = true
  const cfg = rebalanceConfig.value
  rebalanceEditData.value = {
    strategy: cfg.strategy,
    base_allocation: { ...cfg.base_allocation },
    valuation_adjustment: { ...cfg.valuation_adjustment },
    valuation_percentiles: { ...cfg.valuation_percentiles },
    drift_thresholds: { ...cfg.drift_thresholds },
    cash_targets: { ...cfg.cash_targets },
    cash_triggers: { ...cfg.cash_triggers },
    drift_ignore: cfg.drift_ignore,
    undervalue_max: cfg.undervalue_max,
    undervalue_amount: { ...cfg.undervalue_amount },
  }
}

function selectPresetStrategy(key) {
  rebalanceEditData.value.strategy = key
  const preset = rebalancePresets.value.find(p => p.key === key)
  if (preset?.base_allocation) {
    rebalanceEditData.value.base_allocation = { ...preset.base_allocation }
  }
}

async function saveRebalanceConfig() {
  try {
    const payload = { ...rebalanceEditData.value }
    const { data } = await updateRebalanceConfig(payload)
    if (data.ok) {
      showToast('配置已保存', 'success')
      rebalanceEditing.value = false
      await loadRebalanceConfig()
    } else {
      showToast(data.message || '保存失败', 'error')
    }
  } catch (e) {
    showToast('保存失败: ' + (e.response?.data?.detail || e.message), 'error')
  }
}

function cancelEditRebalance() {
  rebalanceEditing.value = false
  rebalanceEditData.value = {}
}

// 配置变更历史
const configHistory = ref([])
const configHistoryLoading = ref(false)
const configHistoryExpanded = ref(false)

async function loadConfigHistory() {
  configHistoryLoading.value = true
  try {
    const { data } = await getRebalanceConfigHistory(20)
    configHistory.value = data.records || []
  } catch (e) {
    showToast('加载历史失败', 'error')
  } finally {
    configHistoryLoading.value = false
  }
}

async function handleRollbackConfig(configId) {
  try {
    const { data } = await rollbackRebalanceConfig(configId)
    if (data.ok) {
      showToast('已回滚', 'success')
      await loadRebalanceConfig()
      await loadConfigHistory()
    }
  } catch (e) {
    showToast('回滚失败', 'error')
  }
}

function toggleConfigHistory() {
  configHistoryExpanded.value = !configHistoryExpanded.value
  if (configHistoryExpanded.value && configHistory.value.length === 0) {
    loadConfigHistory()
  }
}

async function loadTxAnalysis() {
  try {
    const { data } = await getTransactionSummary()
    txAnalysisData.value = data
  } catch (e) {
    showToast('加载交易分析失败', 'error')
  }
}

async function submitFeedback(feedback) {
  if (!modeRecordId.value || feedbackGiven.value) return
  let note = ''
  if (feedback === 'unhelpful') {
    note = window.prompt('请简要说明为什么没用？（可选）', '')
    if (note === null) return  // 用户取消
  }
  try {
    await submitAnalysisFeedback(modeRecordId.value, feedback, note)
    feedbackGiven.value = feedback
    showToast(feedback === 'helpful' ? '感谢反馈 🙏' : '已记录，我们会持续改进')
  } catch (e) {
    showToast('提交失败', 'error')
  }
}

async function loadFundChart(fundCode) {
  if (!fundCode) { chartData.value = null; return }
  chartLoading.value = true
  chartFundCode.value = fundCode
  try {
    const { data } = await getFundNavHistory(fundCode, 365)
    chartData.value = data
  } catch (e) {
    showToast('加载图表数据失败', 'error')
    chartData.value = null
  } finally {
    chartLoading.value = false
  }
}

async function runDiverAiSummary() {
  if (!holdings.value.length) return
  diverAiLoading.value = true
  diverAiResult.value = ''
  try {
    const { data } = await runDiversificationAiSummary()
    diverAiResult.value = data.result
    diverAiRecordId.value = data.id
  } catch (e) {
    diverAiResult.value = 'AI 解读生成失败：' + (e.response?.data?.detail || e.message)
  } finally {
    diverAiLoading.value = false
  }
}

// ── 确认弹窗包装 ──
function confirmDiverAi() {
  confirm.value = {
    visible: true,
    title: diverAiResult.value ? '重新生成分散度解读' : 'AI 分散度解读',
    message: '将使用「分散度分析师」分析持仓分散度并生成解读，是否继续？',
    danger: false,
    onConfirm: () => { confirm.value.visible = false; runDiverAiSummary() }
  }
}

function confirmPanorama() {
  confirm.value = {
    visible: true,
    title: '全景诊断',
    message: '将使用「全景诊断分析师」对投资组合进行全面诊断分析，是否继续？',
    danger: false,
    onConfirm: () => { confirm.value.visible = false; runPanoramaMode() }
  }
}

function confirmDeepDive() {
  confirm.value = {
    visible: true,
    title: '基金深度分析',
    message: '将使用「基金深度分析师」对所选基金进行深度分析，是否继续？',
    danger: false,
    onConfirm: () => { confirm.value.visible = false; runDeepDiveMode() }
  }
}

function confirmTradeReview() {
  aiMode.value = 'trade-review'
  confirm.value = {
    visible: true,
    title: '交易复盘',
    message: '将使用「交易复盘分析师」分析交易行为并生成复盘报告，是否继续？',
    danger: false,
    onConfirm: () => { confirm.value.visible = false; runTradeReviewMode() }
  }
}

function confirmWhatIf() {
  confirm.value = {
    visible: true,
    title: '情景推演',
    message: '将使用「情景推演分析师」模拟市场情景并生成推演报告，是否继续？',
    danger: false,
    onConfirm: () => { confirm.value.visible = false; runWhatIfMode() }
  }
}

async function submitAiAnalysis() {
  aiAnalysisLoading.value = true
  aiAnalysisResult.value = ''
  aiRecordId.value = null
  aiTokenUsage.value = 0
  aiMcpSources.value = []
  try {
    const { data } = await runPortfolioAiAnalysis(aiAnalysisInput.value.trim())
    aiAnalysisResult.value = data.result
    aiRecordId.value = data.id
    aiTokenUsage.value = data.token_usage || 0
    aiMcpSources.value = data.mcp_used || []
    // 刷新记录列表
    loadAiAnalysisRecords()
  } catch (e) {
    aiAnalysisResult.value = '分析失败：' + (e.response?.data?.detail || e.message)
  } finally {
    aiAnalysisLoading.value = false
  }
}

async function loadAiAnalysisRecords() {
  try {
    const { data } = await listPortfolioAiAnalysisRecords(5)
    aiAnalysisRecords.value = data.records || []
  } catch (e) { /* ignore */ }
}

async function viewAiAnalysisRecord(record) {
  try {
    const { data } = await getPortfolioAiAnalysisRecord(record.id)
    aiAnalysisResult.value = data.result_data || data.result
    aiRecordId.value = record.id
    aiTokenUsage.value = record.token_usage || 0
    aiShowHistory.value = false
  } catch (e) {
    showToast('加载记录失败', 'error')
  }
}

async function deleteAiAnalysisRecord(recordId) {
  try {
    await deletePortfolioAiAnalysisRecord(recordId)
    aiAnalysisRecords.value = aiAnalysisRecords.value.filter(r => r.id !== recordId)
    if (aiRecordId.value === recordId) {
      aiAnalysisResult.value = ''
      aiRecordId.value = null
    }
    showToast('已删除')
  } catch (e) {
    showToast('删除失败', 'error')
  }
}

function formatAiTime(dateStr) {
  if (!dateStr) return ''
  const d = new Date(dateStr)
  const month = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  const hour = String(d.getHours()).padStart(2, '0')
  const min = String(d.getMinutes()).padStart(2, '0')
  return `${month}-${day} ${hour}:${min}`
}

// Transaction tags
async function openTxTags(txId) {
  try {
    const { data } = await getTransactionTags(txId)
    editingTxTags.value = { txId, tags: data.tags || [], show: true, newTag: '' }
  } catch (e) {
    showToast('加载标签失败', 'error')
  }
}

async function addTagToTx() {
  const et = editingTxTags.value
  if (!et || !et.newTag.trim()) return
  const tag = et.newTag.trim()
  if (et.tags.includes(tag)) {
    showToast('标签已存在', 'warning')
    return
  }
  try {
    await addTransactionTag(et.txId, tag)
    et.tags.push(tag)
    et.newTag = ''
  } catch (e) {
    showToast('添加标签失败', 'error')
  }
}

async function removeTagFromTx(tag) {
  const et = editingTxTags.value
  if (!et) return
  try {
    await removeTransactionTag(et.txId, tag)
    et.tags = et.tags.filter(t => t !== tag)
  } catch (e) {
    showToast('删除标签失败', 'error')
  }
}

const TAG_PRESETS = ['追涨', '抄底', '定投', '止盈', '止损', '调仓', '管住手', '观望', '加仓', '减仓']

onMounted(async () => {
  await loadData()
  loadAlerts()
  // 如果今天还未分析过，自动触发分散度分析；已分析则加载最新结果
  if (holdings.value.length > 0) {
    try {
      const { data } = await getAiSummaryTodayStatus()
      if (!data.analyzed_today) {
        activeAnalysisTab.value = 'diversification'
        loadDiversification()
        await runDiverAiSummary()
      } else if (data.record_id) {
        // 已分析过，加载已有结果
        try {
          const { data: record } = await getPortfolioAiAnalysisRecord(data.record_id)
          diverAiResult.value = record.result_data || ''
          diverAiRecordId.value = record.id
        } catch (e) { /* silent */ }
      }
    } catch (e) { /* silent */ }
  }
})

// KeepAlive 组件激活时重新加载数据（切换页面回来时触发）
onActivated(async () => {
  await loadData()
  loadAlerts()
})

// 账号切换时重新加载数据
watch(accountFilter, () => {
  loadData()
})

// Refresh prices
// Batch refresh state
const refreshProgress = ref(null)
const refreshingRowId = ref(null)
const refreshRowResult = ref(null)  // { id, pct, profit } | null

async function refreshAll() {
  if (holdings.value.length === 0) return
  refreshProgress.value = {
    items: holdings.value.map(h => ({ id: h.id, fund_name: h.fund_name || h.fund_code, status: 'pending' })),
    total: holdings.value.length,
    done: 0,
  }
  let totalTodayProfit = 0
  let upCount = 0, downCount = 0
  for (const item of refreshProgress.value.items) {
    item.status = 'loading'
    try {
      const { data } = await refreshPortfolioPrice(item.id)
      item.status = 'done'
      const nav = data.nav || {}
      const pct = nav.today_change_pct
      const profit = nav.today_profit
      if (pct !== undefined && pct !== null) {
        item.changePct = pct
        if (pct > 0) upCount++
        else if (pct < 0) downCount++
      }
      if (profit !== undefined && profit !== null) {
        totalTodayProfit += profit
      }
    } catch (e) {
      item.status = 'error'
    }
    refreshProgress.value.done++
  }
  await loadData()
  setTimeout(() => { refreshProgress.value = null }, 1500)
  // 显示今日盈亏汇总
  const sign = totalTodayProfit > 0 ? '+' : ''
  showToast(
    `${upCount}涨${downCount}跌 ${sign}¥${totalTodayProfit.toFixed(2)}`,
    totalTodayProfit > 0 ? 'success' : totalTodayProfit < 0 ? 'error' : 'info'
  )
}

async function refreshSingle(h) {
  refreshingRowId.value = h.id
  refreshRowResult.value = null
  try {
    const { data } = await refreshPortfolioPrice(h.id)
    const nav = data.nav || {}
    const pct = nav.today_change_pct
    const profit = nav.today_profit
    refreshRowResult.value = { id: h.id, pct, profit }
    setTimeout(() => { refreshRowResult.value = null; refreshingRowId.value = null }, 2000)
    loadData()
  } catch (e) {
    refreshRowResult.value = { id: h.id, error: true }
    setTimeout(() => { refreshRowResult.value = null; refreshingRowId.value = null }, 2000)
  }
}

function freshnessHint(dateStr) {
  if (!dateStr) return { text: '未更新', cls: 'fresh-stale' }
  const today = new Date().toISOString().slice(0, 10)
  const yesterday = new Date(Date.now() - 86400000).toISOString().slice(0, 10)
  if (dateStr >= today) return { text: '今日', cls: 'fresh-today' }
  if (dateStr >= yesterday) return { text: '昨日', cls: 'fresh-yesterday' }
  return { text: dateStr, cls: 'fresh-stale' }
}

// Fund lookup
async function doLookup() {
  const code = form.value.fund_code.trim()
  if (!code) { showToast('请输入基金代码', 'error'); return }
  lookupLoading.value = true
  lookupResult.value = null
  try {
    const { data } = await lookupFundInfo(code)
    lookupResult.value = data
    form.value.fund_name = data.fund_name || ''
    form.value.index_name = data.tracking_index || ''
    showToast('已自动填充基金信息')
  } catch (e) {
    showToast('未找到该基金信息，请手动填写', 'error')
  } finally {
    lookupLoading.value = false
  }
}

// Fund detail
async function openDetail(h) {
  detailFundName.value = h.fund_name
  chartMode.value = 'detail'
  showDetail.value = true
  detailLoading.value = true
  detailData.value = null
  try {
    const { data } = await getFundHoldings(h.fund_code)
    detailData.value = data
  } catch (e) {
    showToast('获取基金详情失败', 'error')
  } finally {
    detailLoading.value = false
  }
}

async function openFundAnalysis(h) {
  detailFundName.value = h.fund_name
  chartSearchQuery.value = h.fund_code
  chartSearchError.value = ''
  chartMode.value = 'chart5y'
  showDetail.value = true
  fundChartLoading.value = true
  fundChartData.value = null
  try {
    const { data } = await getFundNavHistory(h.fund_code, 1825)  // ~5年
    fundChartData.value = data?.nav_history || null
  } catch (e) {
    fundChartData.value = null
    showToast('获取 5 年净值数据失败', 'error')
  } finally {
    fundChartLoading.value = false
  }
}

async function searchAndLoadChart() {
  const code = chartSearchQuery.value.trim()
  if (!code) return
  chartSearchError.value = ''
  chartSearching.value = true
  fundChartData.value = null
  resetChart5yZoom()
  try {
    // 先查基金名称
    const info = await lookupFundInfo(code)
    detailFundName.value = info.data?.name || code
  } catch {
    detailFundName.value = code
  }
  try {
    const { data } = await getFundNavHistory(code, 1825)
    fundChartData.value = data?.nav_history || null
    if (!fundChartData.value?.length) {
      chartSearchError.value = '未找到该基金的净值数据'
    }
  } catch (e) {
    fundChartData.value = null
    chartSearchError.value = '获取净值数据失败，请检查基金代码'
  } finally {
    chartSearching.value = false
  }
}

function switchChartFund(fundCode, fundName) {
  chartSearchQuery.value = fundCode
  detailFundName.value = fundName
  chartSearchError.value = ''
  fundChartLoading.value = true
  fundChartData.value = null
  resetChart5yZoom()
  getFundNavHistory(fundCode, 1825).then(({ data }) => {
    fundChartData.value = data?.nav_history || null
  }).catch(() => {
    fundChartData.value = null
  }).finally(() => {
    fundChartLoading.value = false
  })
}

// Form operations
function openAddForm() {
  editingId.value = null
  lookupResult.value = null
  form.value = {
    fund_code: '', fund_name: '', shares: 0, total_cost: null, current_price: null,
    index_code: '', index_name: '', buy_date: '', notes: '', account: '花无缺',
  }
  showForm.value = true
}

function openEditForm(h) {
  editingId.value = h.id
  form.value = {
    fund_code: h.fund_code || '',
    fund_name: h.fund_name || '',
    shares: h.shares || 0,
    total_cost: h.total_cost || null,
    current_price: h.current_price || null,
    index_code: h.index_code || '',
    index_name: h.index_name || '',
    buy_date: h.buy_date || '',
    notes: h.notes || '',
    account: h.account || '花无缺',
  }
  showForm.value = true
}

async function submitForm() {
  if (!form.value.fund_code.trim() || !form.value.fund_name.trim()) {
    showToast('基金代码和名称不能为空', 'error')
    return
  }
  if (!form.value.shares || form.value.shares <= 0) {
    showToast('持有份额必须大于 0', 'error')
    return
  }
  if (!form.value.current_price || form.value.current_price <= 0) {
    showToast('请输入当前净值', 'error')
    return
  }
  // 从 total_cost 计算出 cost_price 传给后端
  const payload = {
    fund_code: form.value.fund_code,
    fund_name: form.value.fund_name,
    shares: form.value.shares,
    current_price: form.value.current_price,
    index_code: form.value.index_code || '',
    index_name: form.value.index_name || '',
    buy_date: form.value.buy_date || '',
    notes: form.value.notes || '',
    account: form.value.account,
  }
  if (form.value.total_cost && form.value.total_cost > 0) {
    payload.cost_price = form.value.total_cost / form.value.shares
  } else {
    payload.cost_price = null  // 不填总金额则按当前净值，盈亏显示 0
  }
  try {
    if (editingId.value) {
      await updatePortfolio(editingId.value, payload)
      showToast('更新成功')
    } else {
      await createPortfolio(payload)
      showToast('新增成功')
    }
    showForm.value = false
    loadData()
  } catch (e) {
    showToast('操作失败: ' + (e.response?.data?.detail || e.message), 'error')
  }
}

function handleDelete(h) {
  confirm.value = {
    visible: true,
    title: '删除持仓',
    message: `确定要删除「${h.fund_name}」吗？关联的交易记录也会一并删除。`,
    danger: true,
    onConfirm: async () => {
      try {
        await deletePortfolio(h.id)
        showToast('已删除')
        loadData()
      } catch (e) {
        showToast('删除失败', 'error')
      }
      confirm.value.visible = false
    },
  }
}

// Transaction operations
function openTxForm(h) {
  txHoldingId.value = h.id
  txFundCode.value = h.fund_code
  txForm.value = {
    transaction_type: 'buy',
    amount: 0,
    shares: 0,
    price: h.current_price || 0,
    transaction_date: new Date().toISOString().slice(0, 10),
    notes: '',
  }
  showTxForm.value = true
}

async function submitTx() {
  if (txForm.value.amount <= 0) {
    showToast('交易金额必须大于 0', 'error')
    return
  }
  try {
    await createPortfolioTransaction({
      fund_code: txFundCode.value,
      holding_id: txHoldingId.value,
      ...txForm.value,
    })
    showToast('交易记录已添加')
    showTxForm.value = false
    loadData()
  } catch (e) {
    showToast('添加失败: ' + (e.response?.data?.detail || e.message), 'error')
  }
}

async function viewTransactions(h) {
  txHoldingId.value = h.id
  try {
    const { data } = await listPortfolioTransactions(h.id)
    transactions.value = data.transactions || []
    showTxHistory.value = true
  } catch (e) {
    showToast('加载交易记录失败', 'error')
  }
}

// Add purchase (追加买入) — amount-based, pending status
function openAddPurchase(h) {
  addPurchaseHolding.value = h
  const now = new Date()
  addPurchaseForm.value = {
    amount: 0,
    transaction_date: now.toISOString().slice(0, 10),
    transaction_time: now.toTimeString().slice(0, 5),
    notes: '',
  }
  showAddPurchase.value = true
}

async function submitAddPurchase() {
  const f = addPurchaseForm.value
  if (f.amount <= 0) {
    showToast('买入金额必须大于 0', 'error')
    return
  }
  const h = addPurchaseHolding.value
  try {
    const { data: txResult } = await createPortfolioTransaction({
      fund_code: h.fund_code,
      holding_id: h.id,
      transaction_type: 'buy',
      amount: 0,
      transaction_date: f.transaction_date,
      transaction_time: f.transaction_time,
      notes: f.notes,
      status: 'pending',
      submitted_amount: f.amount,
    })
    const confirmDate = txResult?.expected_confirm_date
    showToast(confirmDate ? `已提交买入 ¥${f.amount}，预计 ${confirmDate} 确认` : `已提交买入 ¥${f.amount}，待 T+1 确认`)
    showAddPurchase.value = false
    loadData()
  } catch (e) {
    showToast('操作失败: ' + (e.response?.data?.detail || e.message), 'error')
  }
}

// Sell (卖出) — shares-based, pending status
function openSell(h) {
  sellHolding.value = h
  const now = new Date()
  sellForm.value = {
    shares: 0,
    transaction_date: now.toISOString().slice(0, 10),
    transaction_time: now.toTimeString().slice(0, 5),
    notes: '',
  }
  showSell.value = true
}

async function submitSell() {
  const f = sellForm.value
  if (f.shares <= 0) {
    showToast('卖出份额必须大于 0', 'error')
    return
  }
  const h = sellHolding.value
  if (f.shares > (h.shares || 0)) {
    showToast(`卖出份额不能超过持有份额 ${h.shares}`, 'error')
    return
  }
  try {
    const { data: txResult } = await createPortfolioTransaction({
      fund_code: h.fund_code,
      holding_id: h.id,
      transaction_type: 'sell',
      amount: 0,
      shares: null,
      transaction_date: f.transaction_date,
      transaction_time: f.transaction_time,
      notes: f.notes,
      status: 'pending',
      submitted_shares: f.shares,
    })
    const confirmDate = txResult?.expected_confirm_date
    showToast(confirmDate ? `已提交卖出 ${f.shares} 份，预计 ${confirmDate} 确认` : `已提交卖出 ${f.shares} 份，待 T+1 确认`)
    showSell.value = false
    loadData()
  } catch (e) {
    showToast('操作失败: ' + (e.response?.data?.detail || e.message), 'error')
  }
}

// Confirm transaction (确认交易)
function openConfirmTx(tx) {
  confirmTxData.value = tx
  confirmTxPrice.value = 0
  showConfirmTx.value = true
}

async function submitConfirmTx() {
  if (confirmTxPrice.value <= 0) {
    showToast('确认净值必须大于 0', 'error')
    return
  }
  try {
    await confirmTransaction(confirmTxData.value.id, {
      confirmed_price: confirmTxPrice.value,
    })
    showToast('交易已确认')
    showConfirmTx.value = false
    loadData()
    // Refresh transaction list
    if (txHoldingId.value) {
      const { data } = await listPortfolioTransactions(txHoldingId.value)
      transactions.value = data.transactions || []
    }
  } catch (e) {
    showToast('确认失败: ' + (e.response?.data?.detail || e.message), 'error')
  }
}

// Settle transaction (标记到账)
function doCancelFromConfirm() {
  showConfirmTx.value = false
  const tx = confirmTxData.value
  if (tx) handleCancelPendingTx(tx)
}

async function handleSettle(tx) {
  try {
    await settleTransaction(tx.id)
    showToast('已标记到账')
    loadData()
    if (txHoldingId.value) {
      const { data } = await listPortfolioTransactions(txHoldingId.value)
      transactions.value = data.transactions || []
    }
  } catch (e) {
    showToast('操作失败: ' + (e.response?.data?.detail || e.message), 'error')
  }
}

async function handleCancelPendingTx(tx) {
  confirm.value = {
    visible: true,
    title: '撤销交易',
    message: `确定要撤销这笔 ${tx.transaction_type === 'buy' ? '买入' : '卖出'} 交易吗？`,
    danger: false,
    onConfirm: async () => {
      try {
        await deletePortfolioTransaction(tx.id)
        showToast('已撤销')
        loadData()
        if (txHoldingId.value) {
          const { data } = await listPortfolioTransactions(txHoldingId.value)
          transactions.value = data.transactions || []
        }
      } catch (e) {
        showToast('操作失败: ' + (e.response?.data?.detail || e.message), 'error')
      }
      confirm.value.visible = false
    },
  }
}

// Helpers
function formatMoney(v) {
  if (v == null) return '--'
  return '¥' + Number(v).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function formatRate(v) {
  if (v == null) return '--'
  return (v * 100).toFixed(2) + '%'
}

function profitClass(v) {
  if (v > 0) return 'profit-positive'
  if (v < 0) return 'profit-negative'
  return ''
}

function txTypeLabel(t) {
  return { buy: '买入', sell: '卖出', dividend: '分红' }[t] || t
}

function txTypeBadge(t) {
  return { buy: 'badge-success', sell: 'badge-danger', dividend: 'badge-info' }[t] || 'badge-neutral'
}

function txStatusLabel(s) {
  return { pending: '待确认', confirmed: '已确认', settled: '已到账' }[s] || '已确认'
}

function txStatusBadge(s) {
  return { pending: 'badge-warning', confirmed: 'badge-success', settled: 'badge-info' }[s] || 'badge-success'
}

function txDisplayAmount(tx) {
  if (tx.status === 'pending') {
    if (tx.transaction_type === 'buy') return '¥' + (tx.submitted_amount || 0).toLocaleString()
    if (tx.transaction_type === 'sell') return (tx.submitted_shares || 0).toLocaleString() + ' 份'
  }
  return formatMoney(tx.amount)
}
</script>

<template>
  <div class="portfolio-page">
    <div class="page-header">
      <div>
        <h2 class="page-title">持仓管理</h2>
        <p class="page-desc">管理基金持仓，AI 对话时可结合持仓数据给出加减仓建议</p>
      </div>
      <div class="header-actions">
        <button class="btn-secondary" @click="refreshAll" :disabled="refreshProgress !== null">
          <svg v-if="refreshProgress" class="icon-spin spinning" width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h5M20 20v-5h-5M4.93 9a8 8 0 0113.14 0M19.07 15a8 8 0 01-13.14 0"/>
          </svg>
          <svg v-else width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h5M20 20v-5h-5M4.93 9a8 8 0 0113.14 0M19.07 15a8 8 0 01-13.14 0"/>
          </svg>
          {{ refreshProgress ? `刷新中 ${refreshProgress.done}/${refreshProgress.total}` : '刷新净值' }}
        </button>
        <button class="btn-primary" @click="openAddForm">
          <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/>
          </svg>
          新增持仓
        </button>
      </div>
    </div>

    <!-- Pending Transactions Reminder -->
    <div v-if="pendingTxs.length > 0" class="pending-banner">
      <div class="pending-banner-header">
        <svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/>
        </svg>
        <strong>{{ pendingTxs.length }} 笔交易待确认</strong>
        <span class="pending-hint">（提交后 T+1 才能确认净值，请在确认后点击「确认」填入实际净值）</span>
      </div>
      <div class="pending-list">
        <div v-for="tx in pendingTxs" :key="tx.id" class="pending-item">
          <span :class="['badge', txTypeBadge(tx.transaction_type)]">{{ txTypeLabel(tx.transaction_type) }}</span>
          <span class="pending-fund">{{ tx._fund_name }}</span>
          <span class="pending-detail">
            {{ tx.transaction_type === 'buy' ? '¥' + (tx.submitted_amount || 0).toLocaleString() : (tx.submitted_shares || 0).toLocaleString() + ' 份' }}
          </span>
          <span class="pending-date">{{ tx.transaction_date }}</span>
          <span v-if="tx.expected_confirm_date" class="pending-confirm-hint">→ {{ tx.expected_confirm_date }} 确认</span>
          <button class="btn-ghost btn-sm btn-primary-text" @click="openConfirmTx(tx)">确认</button>
          <button class="btn-ghost btn-sm btn-danger-text" @click="handleCancelPendingTx(tx)">撤销</button>
        </div>
      </div>
    </div>

    <!-- Alert Panel -->
    <div class="alert-panel">
      <div class="alert-panel-header" @click="showAlerts = !showAlerts" style="cursor: pointer">
        <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>
        </svg>
        <strong>风险预警</strong>
        <span v-if="unreadAlertCount > 0" class="alert-badge has-unread">{{ unreadAlertCount }}</span>
        <span class="alert-toggle-icon" v-html="showAlerts ? '&#9660;' : '&#9654;'"></span>
        <span style="flex:1"></span>
        <button class="btn-ghost btn-sm" :class="{ 'btn-loading': alertScanning }" :disabled="alertScanning" @click.stop="handleScanAlerts" title="持仓巡检">
          <svg :class="['icon-spin', { 'spinning': alertScanning }]" width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
          </svg>
          <span>{{ alertScanning ? '巡检中...' : '巡检' }}</span>
        </button>
        <button class="btn-ghost btn-sm" @click.stop="loadAlerts" title="刷新">刷新</button>
      </div>
      <div v-if="showAlerts" class="alert-list">
        <div v-if="alerts.length === 0" class="alert-empty">
          <span>暂无预警</span>
          <span class="alert-empty-hint">点击「巡检」主动扫描持仓风险</span>
        </div>
        <div v-for="a in alerts" :key="a.id" :class="['alert-item', 'alert-' + a.severity]">
          <div class="alert-icon">
            <svg v-if="a.severity === 'danger'" width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>
            </svg>
            <svg v-else-if="a.severity === 'warning'" width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>
            </svg>
            <svg v-else width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>
            </svg>
          </div>
          <div class="alert-body">
            <div class="alert-title">{{ a.title }}</div>
            <div v-if="a.content" class="alert-content">{{ a.content }}</div>
            <div class="alert-meta">
              <span class="alert-type-badge">{{ a.alert_type }}</span>
              <span v-if="a.source === 'system_scan'" class="alert-source-badge scan">系统巡检</span>
              <span v-else-if="a.source === 'ai_analysis'" class="alert-source-badge ai">AI 对话</span>
              <span v-else class="alert-source-badge">{{ a.source }}</span>
              <span class="alert-time">{{ a.created_at }}</span>
            </div>
          </div>
          <div class="alert-actions">
            <button class="btn-ghost btn-sm" @click="handleMarkAlertRead(a.id)" title="标记已读">✔</button>
            <button class="btn-ghost btn-sm btn-danger-text" @click="handleDeleteAlert(a.id)" title="删除">✕</button>
          </div>
        </div>
      </div>
    </div>

    <!-- Tab Bar -->
    <div class="analysis-tabs" v-if="holdings.length > 0">
      <button :class="['analysis-tab', { active: activeAnalysisTab === 'holdings' }]" @click="switchAnalysisTab('holdings')">
        <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4"/></svg>
        <span>持仓列表</span>
      </button>
      <button :class="['analysis-tab', { active: activeAnalysisTab === 'diversification' }]" @click="switchAnalysisTab('diversification')">
        <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 3.055A9.001 9.001 0 1020.945 13H11V3.055z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20.488 9H15V3.512A9.025 9.025 0 0120.488 9z"/></svg>
        <span class="term-with-tip">分散度分析<span class="term-tip">分析持仓的基金类型分布、指数分布和个股集中度，评估分散化程度</span></span>
      </button>
      <button :class="['analysis-tab', { active: activeAnalysisTab === 'tx' }]" @click="switchAnalysisTab('tx')">
        <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/></svg>
        <span class="term-with-tip">交易行为分析<span class="term-tip">统计买卖频率、净投入和持仓变化，复盘交易行为</span></span>
      </button>
      <button :class="['analysis-tab', { active: activeAnalysisTab === 'ai' }]" @click="switchAnalysisTab('ai')">
        <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/></svg>
        <span class="term-with-tip">AI 分析<span class="term-tip">AI 结合估值数据、新闻热点和盈米 MCP 数据源，综合分析你的持仓情况。可以问：我的持仓有什么风险？当前估值下是否该调仓？某某基金表现如何？</span></span>
      </button>
      <button :class="['analysis-tab', { active: activeAnalysisTab === 'config' }]" @click="switchAnalysisTab('config')">
        <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/></svg>
        <span class="term-with-tip">策略配置<span class="term-tip">管理调仓分析的资产配比策略、估值阈值和偏离度参数</span></span>
      </button>
    </div>

    <!-- Analysis Panel Content -->
    <div v-if="activeAnalysisTab && activeAnalysisTab !== 'holdings'" class="card analysis-panel">
      <!-- Diversification -->
      <template v-if="activeAnalysisTab === 'diversification'">
        <div class="analysis-panel-header">
          <h3>持仓分散度分析</h3>
          <div class="analysis-panel-actions">
            <button class="btn-diver-refresh" @click="loadDiversification" :disabled="diversificationLoading">
              <svg :class="['icon-spin', { 'spinning': diversificationLoading }]" width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h5M20 20v-5h-5M4.93 9a8 8 0 0113.14 0M19.07 15a8 8 0 01-13.14 0"/>
              </svg>
              {{ diversificationLoading ? '分析中...' : '刷新分析' }}
            </button>
            <button class="btn-ghost btn-sm" @click="switchAnalysisTab('diversification')">✕</button>
          </div>
        </div>
        <div v-if="diversificationLoading" class="loading-state"><div class="spinner"></div></div>
        <div v-else-if="diversificationData" class="analysis-panel-body">
          <div class="analysis-stats">
            <div class="analysis-stat">
              <span class="stat-label">持有基金</span>
              <span class="stat-value">{{ diversificationData.holding_count }} 只</span>
            </div>
            <div class="analysis-stat">
              <span class="stat-label">仓位集中度</span>
              <span :class="['stat-value', diversificationData.max_holding_pct > 40 ? 'text-danger' : 'text-success']">
                {{ diversificationData.max_holding_pct }}%
              </span>
            </div>
            <div class="analysis-stat">
              <span class="stat-label">总投资</span>
              <span class="stat-value">{{ formatMoney(diversificationData.total_cost) }}</span>
            </div>
            <div class="analysis-stat">
              <span class="stat-label">总市值</span>
              <span class="stat-value">{{ formatMoney(diversificationData.total_value) }}</span>
            </div>
          </div>
          <div v-if="diversificationData.type_distribution" class="analysis-section">
            <h4>基金类型分布</h4>
            <div class="pie-chart-row">
              <svg width="120" height="120" viewBox="0 0 120 120">
                <template v-for="s in calcPieSlices(diversificationData.type_distribution, diversificationData.total_value)" :key="s.label">
                  <path :d="s.path" :fill="s.color" stroke="#fff" stroke-width="1.5"/>
                </template>
              </svg>
              <div class="pie-legend">
                <div v-for="s in calcPieSlices(diversificationData.type_distribution, diversificationData.total_value)" :key="s.label" class="legend-item">
                  <span class="legend-dot" :style="{background:s.color}"></span>
                  <span class="legend-label">{{ s.label }}</span>
                  <span class="legend-pct">{{ s.pct }}%</span>
                </div>
              </div>
            </div>
            <div class="distribution-bars">
              <div v-for="(val, key) in diversificationData.type_distribution" :key="key" class="dist-bar-row">
                <span class="dist-label">{{ key }}</span>
                <div class="dist-bar-track">
                  <div class="dist-bar-fill" :style="{ width: (val / diversificationData.total_value * 100) + '%', background: pieColors[Object.keys(diversificationData.type_distribution).indexOf(key) % pieColors.length] }"></div>
                </div>
                <span class="dist-value">{{ formatMoney(val) }}</span>
              </div>
            </div>
          </div>
          <div v-if="diversificationData.index_distribution" class="analysis-section collapsible-section">
            <h4 @click="showIndexDist = !showIndexDist" style="cursor:pointer;user-select:none">
              <span class="collapse-icon">{{ showIndexDist ? '▼' : '▶' }}</span>
              指数分布
            </h4>
            <div v-show="showIndexDist" class="collapse-content">
              <div class="pie-chart-row">
                <svg width="120" height="120" viewBox="0 0 120 120">
                  <template v-for="s in calcPieSlices(diversificationData.index_distribution, diversificationData.total_value)" :key="s.label">
                    <path :d="s.path" :fill="s.color" stroke="#fff" stroke-width="1.5" style="cursor:pointer" @click="expandedIndexDist = (expandedIndexDist === s.label ? null : s.label)"/>
                  </template>
                </svg>
                <div class="pie-legend">
                  <div v-for="s in calcPieSlices(diversificationData.index_distribution, diversificationData.total_value)" :key="s.label" class="legend-item">
                    <span class="legend-dot" :style="{background:s.color}"></span>
                    <span class="legend-label" style="cursor:pointer" @click="expandedIndexDist = (expandedIndexDist === s.label ? null : s.label)">{{ s.label }}</span>
                    <span class="legend-pct">{{ s.pct }}%</span>
                  </div>
                </div>
              </div>
              <div class="distribution-bars">
                <div v-for="(val, key) in diversificationData.index_distribution" :key="key" class="dist-bar-row">
                  <span class="dist-label" style="cursor:pointer" @click="expandedIndexDist = (expandedIndexDist === key ? null : key)">{{ key }}</span>
                  <div class="dist-bar-track" style="cursor:pointer" @click="expandedIndexDist = (expandedIndexDist === key ? null : key)">
                    <div class="dist-bar-fill dist-bar-index" :style="{ width: (val / diversificationData.total_value * 100) + '%' }"></div>
                  </div>
                  <span class="dist-value">{{ formatMoney(val) }}</span>
                </div>
              </div>
              <!-- 指数下基金明细 -->
              <div v-if="expandedIndexDist && fundsByIndex[expandedIndexDist]" class="index-detail-box">
                <div class="index-detail-title">{{ expandedIndexDist }} 包含的基金</div>
                <div v-for="f in fundsByIndex[expandedIndexDist]" :key="f.id" class="index-detail-item">
                  <span class="index-detail-name">{{ f.fund_name }}</span>
                  <span class="index-detail-pct">{{ ((f.current_value||0) / diversificationData.total_value * 100).toFixed(1) }}%</span>
                </div>
              </div>
            </div>
          </div>

          <!-- 持仓占比 -->
          <div v-if="holdingWeights.length > 0" class="analysis-section collapsible-section">
            <h4 @click="showHoldingWeight = !showHoldingWeight" style="cursor:pointer;user-select:none">
              <span class="collapse-icon">{{ showHoldingWeight ? '▼' : '▶' }}</span>
              持仓占比
            </h4>
            <div v-show="showHoldingWeight" class="collapse-content">
              <PieChart
                v-if="pieChartData.length"
                :data="pieChartData"
                :inner-radius="50"
                :outer-radius="78"
                legend-position="right"
                height="260px"
              />
              <div v-else class="chart-empty">暂无持仓数据</div>
              <div class="distribution-bars">
                <div v-for="(h, idx) in holdingWeights" :key="h.id" class="dist-bar-row">
                  <span class="dist-label">{{ h.fund_name }}</span>
                  <div class="dist-bar-track">
                    <div class="dist-bar-fill" :style="{ width: h.weight + '%', background: pieColors[idx % pieColors.length] }"></div>
                  </div>
                  <span class="dist-value">{{ h.weightLabel }}</span>
                </div>
              </div>
            </div>
          </div>
          <div class="analysis-hint" v-if="diversificationData.max_holding_pct > 40">
            ⚠️ 单只基金占比超过 40%，仓位较集中，建议适当分散。
          </div>

          <!-- AI 解读 -->
          <div class="analysis-section">
            <div class="diver-ai-header">
              <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/></svg>
              <strong>AI 解读</strong>
              <span v-if="diverAiLoading" class="badge badge-neutral badge-sm">分析中...</span>
              <button v-else-if="!diverAiResult" class="btn-ai-action" @click="confirmDiverAi">
                <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/></svg>
                <span>生成解读</span>
                <span class="ai-agent-tooltip">分散度分析师</span>
              </button>
              <button v-else class="btn-ai-action" @click="confirmDiverAi">
                <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
                <span>重新生成</span>
                <span class="ai-agent-tooltip">分散度分析师</span>
              </button>
            </div>
            <div v-if="diverAiLoading" class="diver-ai-loading">
              <div class="spinner"></div><span>正在分析...</span>
            </div>
            <div v-else-if="diverAiResult" class="diver-ai-result">{{ diverAiResult }}</div>
            <div v-else class="diver-ai-placeholder">点击「生成解读」获取 AI 分散度分析建议</div>
          </div>

          <!-- MCP 分析过程数据 -->
          <div v-if="diversificationData.mcp" class="analysis-section">
            <div class="mcp-toggle" @click="diverShowMcp = !diverShowMcp">
              <span class="mcp-toggle-icon">{{ diverShowMcp ? '▼' : '▶' }}</span>
              <span class="term-with-tip">分析过程数据（MCP）<span class="term-tip">通过盈米 MCP 数据接口获取的专业分析数据，作为持仓分析的参考依据</span></span>
              <span class="mcp-status-badges">
                <template v-for="(v, k) in diversificationData.mcp" :key="k">
                  <span v-if="k !== 'error'"
                    :class="['badge', v?.status === 'ok' ? 'badge-success' : 'badge-danger', 'badge-sm']"
                    :title="{asset_class: '穿透分析持仓基金的资产类别（股票/债券/商品等）分布情况', correlation: '分析基金之间的涨跌相关性，避免同涨同跌的集中风险', top_holding_industry: '持仓最大基金的行业配置分布', market: 'A股主要指数的估值温度和行情解读'}[k] || ''">
                    {{ {asset_class: '资产大类', correlation: '相关性', top_holding_industry: '行业配置', market: '行情'}[k] || k }}
                    {{ v?.status === 'ok' ? '✓' : '✗' }}
                  </span>
                </template>
              </span>
              <span v-if="diversificationData.mcp.error" class="badge badge-danger badge-sm">MCP异常</span>
            </div>
            <div v-if="diverShowMcp" class="mcp-data-list">
              <div v-for="(v, k) in diversificationData.mcp" :key="k" class="mcp-data-item">
                <template v-if="k !== 'error' && v">
                  <div class="mcp-data-header">
                    <span :class="['mcp-status-dot', v.status === 'ok' ? 'dot-ok' : 'dot-err']"></span>
                    <strong :title="{asset_class: '穿透分析持仓基金的资产类别（股票/债券/商品等）分布情况', correlation: '分析基金之间的涨跌相关性，避免同涨同跌的集中风险', top_holding_industry: '持仓最大基金的行业配置分布', market: 'A股主要指数的估值温度和行情解读'}[k] || ''"
                      style="cursor:help;border-bottom:1px dashed var(--color-text-muted)">{{ {asset_class: '资产大类穿透分析', correlation: '基金相关性分析', top_holding_industry: `行业配置 - ${v.fund_name || ''}`, market: '市场行情'}[k] || k }}</strong>
                    <span v-if="v.status === 'error'" class="text-danger" style="font-size:0.75rem">{{ v.message }}</span>
                  </div>
                  <div v-if="v.status === 'ok' && v.data" class="mcp-raw-output">{{ v.data }}</div>
                </template>
              </div>
              <div v-if="diversificationData.mcp.error" class="mcp-data-item">
                <div class="mcp-data-header"><span class="mcp-status-dot dot-err"></span>系统提示：{{ diversificationData.mcp.error }}</div>
              </div>
            </div>
          </div>

          <!-- 持仓穿透 -->
          <div class="analysis-section">
            <h4 @click="showPenetration = !showPenetration" style="cursor:pointer;user-select:none">
              <span class="collapse-icon">{{ showPenetration ? '▼' : '▶' }}</span>
              持仓穿透分析
            </h4>
            <div v-show="showPenetration" class="collapse-content">
              <div style="margin-bottom:0.75rem">
                <button class="btn-diver-refresh" @click="handlePenetration" :disabled="penetrationLoading">
                  <svg :class="['icon-spin', { 'spinning': penetrationLoading }]" width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h5M20 20v-5h-5M4.93 9a8 8 0 0113.14 0M19.07 15a8 8 0 01-13.14 0"/>
                  </svg>
                  {{ penetrationLoading ? '加载中...' : (penetrationData ? '刷新穿透数据' : '加载持仓穿透') }}
                </button>
                <span v-if="penetrationData?.cached_at" style="font-size:0.72rem;color:var(--color-text-muted);margin-left:0.5rem">
                  缓存: {{ penetrationData.cached_at }}
                </span>
              </div>
              <div v-if="penetrationLoading" class="loading-state"><div class="spinner"></div></div>
              <div v-else-if="penetrationData && !penetrationData.error">
                <!-- Top 穿透股票 -->
                <div v-if="penetrationData.top_stocks?.length" style="margin-bottom:1rem">
                  <h5 style="font-size:0.82rem;margin:0 0 0.5rem;color:var(--color-text-secondary)">Top {{ penetrationData.top_stocks.length }} 穿透持仓（加权聚合）</h5>
                  <div v-for="stock in penetrationData.top_stocks" :key="stock.stock_code" class="dist-bar-row" style="margin-bottom:0.5rem">
                    <span class="dist-label" style="min-width:5rem">{{ stock.stock_name }}</span>
                    <div class="dist-bar-track" style="flex:1">
                      <div class="dist-bar-fill" :style="{ width: Math.min(stock.total_weight_pct * 5, 100) + '%', background: stock.total_weight_pct > 5 ? '#ef4444' : stock.total_weight_pct > 2 ? '#f59e0b' : '#10b981' }"></div>
                    </div>
                    <span class="dist-value" style="min-width:3.5rem;text-align:right;font-weight:700">{{ stock.total_weight_pct }}%</span>
                    <div style="width:100%;margin-top:0.15rem;display:flex;flex-wrap:wrap;gap:0.25rem">
                      <span v-for="f in stock.held_in_funds" :key="f.fund_name" style="font-size:0.68rem;padding:1px 5px;background:rgba(99,102,241,0.08);border-radius:4px;color:var(--color-text-muted)">
                        {{ f.fund_name }} {{ f.contribution_pct }}%
                      </span>
                    </div>
                  </div>
                </div>
                <!-- 重叠度热力图 -->
                <div v-if="penetrationData.overlap_matrix?.fund_names?.length > 1">
                  <h5 style="font-size:0.82rem;margin:0 0 0.5rem;color:var(--color-text-secondary)">
                    <span class="term-with-tip">基金重叠度矩阵<span class="term-tip">衡量任意两只基金之间共同持有相同股票的比例。数值越高说明两只基金持仓越相似，分散投资效果越差。例如 50% 表示两只基金有一半重仓股相同，建议选择重叠度低的基金组合以真正分散风险。</span></span>
                  </h5>
                  <div class="overlap-heatmap">
                    <div class="overlap-row overlap-header">
                      <div class="overlap-cell overlap-corner"></div>
                      <div v-for="name in penetrationData.overlap_matrix.fund_names" :key="'h'+name" class="overlap-cell overlap-label">{{ name }}</div>
                    </div>
                    <div v-for="(row, i) in penetrationData.overlap_matrix.matrix" :key="'r'+i" class="overlap-row">
                      <div class="overlap-cell overlap-label">{{ penetrationData.overlap_matrix.fund_names[i] }}</div>
                      <div v-for="(val, j) in row" :key="'c'+j"
                        class="overlap-cell"
                        :style="{ background: overlapColor(val), color: val >= 0.5 ? '#fff' : '#1e293b' }"
                        :title="penetrationData.overlap_matrix.fund_names[i] + ' × ' + penetrationData.overlap_matrix.fund_names[j]">
                        {{ (val * 100).toFixed(0) }}%
                      </div>
                    </div>
                  </div>
                  <div style="display:flex;gap:0.75rem;margin-top:0.4rem;font-size:0.7rem;color:var(--color-text-muted)">
                    <span><span style="display:inline-block;width:10px;height:10px;background:#10b981;border-radius:2px;vertical-align:middle"></span> 0-20%</span>
                    <span><span style="display:inline-block;width:10px;height:10px;background:#f59e0b;border-radius:2px;vertical-align:middle"></span> 20-50%</span>
                    <span><span style="display:inline-block;width:10px;height:10px;background:#ef4444;border-radius:2px;vertical-align:middle"></span> 50%+</span>
                  </div>
                </div>
                <p style="font-size:0.72rem;color:var(--color-text-muted);margin-top:0.5rem">数据来源: akshare 基金季报 · 持仓 {{ penetrationData.fund_count }} 只基金 · 总市值 ¥{{ (penetrationData.total_portfolio_value / 10000).toFixed(1) }}万</p>
              </div>
              <div v-else-if="penetrationData?.error" style="color:#dc2626;font-size:0.85rem">加载失败: {{ penetrationData.error }}</div>
              <div v-else style="color:var(--color-text-muted);font-size:0.85rem">点击上方按钮加载持仓穿透数据</div>
            </div>
          </div>
        </div>
        <div v-else class="analysis-panel-body">
          <EmptyState
            icon="portfolio"
            title="暂无持仓数据"
            description="添加持仓后即可查看分散度分析"
            action-text="添加持仓"
            @action="openAddForm"
          />
        </div>
      </template>

      <!-- Transaction Analysis -->
      <template v-if="activeAnalysisTab === 'tx'">
        <div class="analysis-panel-header">
          <h3>交易行为分析</h3>
          <button class="btn-ghost btn-sm" @click="switchAnalysisTab('tx')">✕</button>
        </div>
        <div v-if="txAnalysisData" class="analysis-panel-body">
          <div class="analysis-stats">
            <div class="analysis-stat">
              <span class="stat-label">总交易次数</span>
              <span class="stat-value">{{ txAnalysisData.total_tx_count }} 笔</span>
            </div>
            <div class="analysis-stat">
              <span class="stat-label">买入</span>
              <span class="stat-value text-success">{{ txAnalysisData.buy_count }} 笔</span>
            </div>
            <div class="analysis-stat">
              <span class="stat-label">卖出</span>
              <span class="stat-value text-warning">{{ txAnalysisData.sell_count }} 笔</span>
            </div>
            <div class="analysis-stat">
              <span class="stat-label">净投入</span>
              <span class="stat-value">{{ formatMoney(txAnalysisData.net_investment) }}</span>
            </div>
          </div>
          <div class="analysis-transaction-detail">
            <div class="detail-row">
              <span>买入总金额</span>
              <span class="text-success">{{ formatMoney(txAnalysisData.buy_total) }}</span>
            </div>
            <div class="detail-row">
              <span>卖出总金额</span>
              <span class="text-warning">{{ formatMoney(txAnalysisData.sell_total) }}</span>
            </div>
          </div>

          <!-- AI 交易复盘 -->
          <div class="tx-ai-review">
            <button
              class="btn-ai-action"
              :class="{ 'btn-loading': modeLoading }"
              :disabled="modeLoading"
              @click="confirmTradeReview()"
            >
              <svg :class="['icon-spin', { 'spinning': modeLoading }]" width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
              </svg>
              <span>{{ modeLoading ? '复盘中...' : 'AI 交易复盘' }}</span>
              <span class="ai-agent-tooltip">交易复盘分析师</span>
            </button>
          </div>
          <div v-if="modeResult && aiMode === 'trade-review'" class="trade-review-result-inline">
            <div class="result-header">
              <span class="result-title">📊 交易复盘结果</span>
              <span class="result-time">{{ new Date().toLocaleString() }}</span>
            </div>
            <div class="result-body" v-html="renderMarkdown(modeResult)"></div>
            <div class="result-feedback">
              <button class="btn-feedback" @click="submitFeedback('helpful')">👍</button>
              <button class="btn-feedback" @click="submitFeedback('unhelpful')">👎</button>
            </div>
          </div>

          <!-- 交易明细 -->
          <div class="analysis-section" style="margin-top:1rem">
            <h4 style="margin:0 0 0.5rem 0;font-size:0.85rem">交易明细（最近 {{ txAnalysisData.recent_transactions?.length || 0 }} 笔）</h4>
            <div class="tx-detail-table-wrap">
              <table class="tx-detail-table" v-if="txAnalysisData.recent_transactions?.length">
                <thead>
                  <tr>
                    <th>基金</th>
                    <th>方向</th>
                    <th>份额</th>
                    <th>价格</th>
                    <th>金额</th>
                    <th>日期</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="tx in txAnalysisData.recent_transactions" :key="tx.id">
                    <td class="tx-fund">
                      <span class="tx-fund-name">{{ tx.fund_name || tx.fund_code }}</span>
                      <code class="tx-fund-code">{{ tx.fund_code }}</code>
                    </td>
                    <td>
                      <span :class="['tx-type-badge', tx.transaction_type === 'buy' ? 'tx-buy' : 'tx-sell']">
                        {{ tx.transaction_type === 'buy' ? '买入' : '卖出' }}
                      </span>
                    </td>
                    <td class="text-right">{{ (tx.shares || 0).toLocaleString() }}</td>
                    <td class="text-right">{{ tx.price ? '¥' + tx.price.toFixed(4) : '--' }}</td>
                    <td class="text-right">{{ tx.amount ? '¥' + tx.amount.toLocaleString() : '--' }}</td>
                    <td class="text-right">{{ tx.transaction_date }}</td>
                  </tr>
                </tbody>
              </table>
              <p v-else class="text-muted" style="font-size:0.85rem">暂无交易记录。</p>
            </div>
          </div>

          <!-- 基金走势图 -->
          <div class="analysis-section" style="margin-top:1rem">
            <h4 style="margin:0 0 0.5rem 0;font-size:0.85rem">基金走势图（选择基金查看买卖点）</h4>
            <div class="chart-fund-selector">
              <select v-model="chartFundCode" class="input-field" @change="loadFundChart(chartFundCode)" style="flex:1;max-width:300px">
                <option value="">选择基金</option>
                <option v-for="h in allChartFunds" :key="h.id" :value="h.fund_code">{{ h.fund_name }} ({{ h.fund_code }})<template v-if="h.account"> [{{ h.account }}]</template></option>
              </select>
              <span v-if="chartLoading" class="text-muted" style="font-size:0.8rem">加载中...</span>
            </div>
            <div v-if="chartData" class="nav-chart-wrap">
              <LineChart
                v-if="navChartData.dates.length"
                :dates="navChartData.dates"
                :series="navChartData.series"
                :y-names="['净值']"
                :area="true"
                :smooth="true"
                height="280px"
              />
              <div v-else class="chart-empty">暂无净值数据</div>
            </div>
            <div v-else-if="chartFundCode && !chartLoading" class="text-muted" style="font-size:0.85rem;padding:0.5rem 0">暂无净值数据。</div>
          </div>
        </div>
        <div v-else class="analysis-panel-body">
          <p class="text-muted">暂无交易记录。</p>
        </div>
      </template>

      <!-- AI Analysis -->
      <template v-if="activeAnalysisTab === 'ai'">
        <div class="analysis-panel-header">
          <h3>AI 持仓分析</h3>
          <div class="analysis-panel-actions">
            <button class="btn-ghost btn-sm" @click="switchAnalysisTab('ai')">✕</button>
          </div>
        </div>
        <div class="analysis-panel-body">
          <!-- Mode Selector Tabs -->
          <div class="ai-mode-tabs">
            <button v-for="mode in aiModes" :key="mode.key"
              :class="['ai-mode-tab', { active: aiMode === mode.key }]"
              @click="switchAiMode(mode.key)">
              <span class="ai-mode-icon">{{ mode.icon }}</span>
              <span class="ai-mode-label">{{ mode.label }}</span>
            </button>
          </div>

          <!-- Mode: Panorama -->
          <div v-if="aiMode === 'panorama'" class="ai-mode-content">
            <div class="ai-mode-desc">从全局视角诊断你的投资组合健康状况，包括集中度风险、估值水位、分散化程度和市场适配度，并给出加减仓建议。</div>
            <button class="btn-ai-action" :class="{ 'btn-loading': modeLoading }" :disabled="modeLoading" @click="confirmPanorama" style="margin:0.5rem 0">
              <svg :class="['icon-spin', { 'spinning': modeLoading }]" width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/>
              </svg>
              <span>{{ modeLoading ? '诊断中...' : '开始全景诊断' }}</span>
              <span class="ai-agent-tooltip">全景诊断分析师</span>
            </button>
            <div v-if="modeResult && aiMode === 'panorama'" class="ai-mode-result">
              <AnalysisCard
                :result="modeResult"
                agent-name="全景诊断分析师"
                :token-usage="aiTokenUsage"
                :created-at="new Date().toISOString()"
                :feedback="feedbackGiven"
                @feedback="(val) => submitFeedback(val)"
              />
            </div>
            <div v-if="panoramaRecords.length > 0" class="ai-mode-history">
              <div class="ai-mode-history-header" @click="panoramaShowAll = !panoramaShowAll">
                <span>📋 历史诊断记录 ({{ panoramaRecords.length }})</span>
                <span class="ai-mode-history-toggle">{{ panoramaShowAll ? '收起' : '展开全部' }}</span>
              </div>
              <div class="ai-mode-history-list">
                <div v-for="r in (panoramaShowAll ? panoramaRecords : panoramaRecords.slice(0,3))" :key="r.id" :class="['ai-history-item', { 'ai-history-current': r.id === modeRecordId }]">
                  <span class="ai-history-time">{{ formatAiTime(r.created_at) }}</span>
                  <span class="ai-history-summary">{{ r.summary }}</span>
                  <button class="btn-ghost btn-sm" @click="viewModeRecord(r)">查看</button>
                </div>
              </div>
            </div>
          </div>

          <!-- Mode: Deep Dive -->
          <div v-if="aiMode === 'deepdive'" class="ai-mode-content">
            <div class="ai-mode-desc">对单只基金进行深度分析，包括角色定位、持有收益质量、买入/卖出操作质量评估，并给出建议。</div>
            <div class="ai-mode-form">
              <select v-model="deepDiveSelectedHolding" class="input-field" style="flex:1">
                <option value="">请选择基金</option>
                <option v-for="h in holdings" :key="h.id" :value="h.id">{{ h.fund_name }} ({{ h.fund_code }})</option>
              </select>
              <button class="btn-ai-action" :class="{ 'btn-loading': modeLoading }" :disabled="modeLoading || !deepDiveSelectedHolding" @click="confirmDeepDive">
                <svg :class="['icon-spin', { 'spinning': modeLoading }]" width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
                </svg>
                <span>{{ modeLoading ? '分析中...' : '深度分析' }}</span>
                <span class="ai-agent-tooltip">基金深度分析师</span>
              </button>
            </div>
            <div v-if="modeResult && aiMode === 'deepdive'" class="ai-mode-result">
              <div class="ai-result-content markdown-body" v-html="renderMarkdown(modeResult)"></div>
              <div v-if="modeRecordId && !feedbackGiven" class="ai-feedback">
                <span class="ai-feedback-label">对结果满意吗？</span>
                <button class="btn-feedback btn-feedback-up" @click="submitFeedback('helpful')" title="有用">👍</button>
                <button class="btn-feedback btn-feedback-down" @click="submitFeedback('unhelpful')" title="没用">👎</button>
              </div>
              <div v-else-if="feedbackGiven" class="ai-feedback ai-feedback-done">
                已反馈 · {{ feedbackGiven === 'helpful' ? '感谢支持' : '我们会改进' }}
              </div>
            </div>
            <div v-if="deepDiveRecords.length > 0" class="ai-mode-history">
              <div class="ai-mode-history-header" @click="deepDiveShowAll = !deepDiveShowAll">
                <span>📋 历史分析记录 ({{ deepDiveRecords.length }})</span>
                <span class="ai-mode-history-toggle">{{ deepDiveShowAll ? '收起' : '展开全部' }}</span>
              </div>
              <div class="ai-mode-history-list">
                <div v-for="r in (deepDiveShowAll ? deepDiveRecords : deepDiveRecords.slice(0,3))" :key="r.id" class="ai-history-item">
                  <span class="ai-history-time">{{ formatAiTime(r.created_at) }}</span>
                  <span class="ai-history-summary">{{ r.summary }}</span>
                  <button class="btn-ghost btn-sm" @click="viewModeRecord(r)">查看</button>
                </div>
              </div>
            </div>
          </div>

          <!-- Mode: Trade Review -->
          <div v-if="aiMode === 'trade-review'" class="ai-mode-content">
            <div class="ai-mode-desc">分析你的交易行为模式，识别操作中的优点和问题，帮助建立更好的投资纪律。</div>
            <div class="ai-mode-form">
              <label class="ai-form-label">开始日期</label>
              <input v-model="reviewStartDate" type="date" class="input-field" />
              <label class="ai-form-label">结束日期</label>
              <input v-model="reviewEndDate" type="date" class="input-field" />
              <button class="btn-ai-action" :class="{ 'btn-loading': modeLoading }" :disabled="modeLoading" @click="confirmTradeReview">
                <svg :class="['icon-spin', { 'spinning': modeLoading }]" width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01"/>
                </svg>
                <span>{{ modeLoading ? '复盘中...' : '开始复盘' }}</span>
                <span class="ai-agent-tooltip">交易复盘分析师</span>
              </button>
            </div>
            <div v-if="modeResult && aiMode === 'trade-review'" class="ai-mode-result">
              <div class="ai-result-content markdown-body" v-html="renderMarkdown(modeResult)"></div>
              <div v-if="modeRecordId && !feedbackGiven" class="ai-feedback">
                <span class="ai-feedback-label">对结果满意吗？</span>
                <button class="btn-feedback btn-feedback-up" @click="submitFeedback('helpful')" title="有用">👍</button>
                <button class="btn-feedback btn-feedback-down" @click="submitFeedback('unhelpful')" title="没用">👎</button>
              </div>
              <div v-else-if="feedbackGiven" class="ai-feedback ai-feedback-done">
                已反馈 · {{ feedbackGiven === 'helpful' ? '感谢支持' : '我们会改进' }}
              </div>
            </div>
            <div v-if="tradeReviewRecords.length > 0" class="ai-mode-history">
              <div class="ai-mode-history-header" @click="tradeReviewShowAll = !tradeReviewShowAll">
                <span>📋 历史复盘记录 ({{ tradeReviewRecords.length }})</span>
                <span class="ai-mode-history-toggle">{{ tradeReviewShowAll ? '收起' : '展开全部' }}</span>
              </div>
              <div class="ai-mode-history-list">
                <div v-for="r in (tradeReviewShowAll ? tradeReviewRecords : tradeReviewRecords.slice(0,3))" :key="r.id" class="ai-history-item">
                  <span class="ai-history-time">{{ formatAiTime(r.created_at) }}</span>
                  <span class="ai-history-summary">{{ r.summary }}</span>
                  <button class="btn-ghost btn-sm" @click="viewModeRecord(r)">查看</button>
                </div>
              </div>
            </div>
          </div>

          <!-- Mode: What-If -->
          <div v-if="aiMode === 'what-if'" class="ai-mode-content">
            <div class="ai-mode-desc">模拟不同市场情景下你的组合变化，了解潜在风险和收益，提前做好准备。</div>
            <div class="ai-mode-form">
              <select v-model="whatIfScenario" class="input-field" style="flex:1">
                <option value="market_drop">市场整体下跌</option>
                <option value="repair_to_median">估值修复到历史中位数</option>
                <option value="repair_to_opportunity">估值修复到机会值</option>
              </select>
              <label v-if="whatIfScenario === 'market_drop'" class="ai-form-label">跌幅 (%)</label>
              <input v-if="whatIfScenario === 'market_drop'" v-model.number="whatIfParameter" type="number" class="input-field" style="width:80px" min="1" max="50" />
              <button class="btn-ai-action" :class="{ 'btn-loading': modeLoading }" :disabled="modeLoading" @click="confirmWhatIf">
                <svg :class="['icon-spin', { 'spinning': modeLoading }]" width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14.828 14.828a4 4 0 01-5.656 0M9 10h.01M15 10h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>
                </svg>
                <span>{{ modeLoading ? '推演中...' : '开始推演' }}</span>
                <span class="ai-agent-tooltip">情景推演分析师</span>
              </button>
            </div>
            <div v-if="modeResult && aiMode === 'what-if'" class="ai-mode-result">
              <div class="ai-result-content markdown-body" v-html="renderMarkdown(modeResult)"></div>
              <div v-if="modeRecordId && !feedbackGiven" class="ai-feedback">
                <span class="ai-feedback-label">对结果满意吗？</span>
                <button class="btn-feedback btn-feedback-up" @click="submitFeedback('helpful')" title="有用">👍</button>
                <button class="btn-feedback btn-feedback-down" @click="submitFeedback('unhelpful')" title="没用">👎</button>
              </div>
              <div v-else-if="feedbackGiven" class="ai-feedback ai-feedback-done">
                已反馈 · {{ feedbackGiven === 'helpful' ? '感谢支持' : '我们会改进' }}
              </div>
            </div>
            <div v-if="whatIfRecords.length > 0" class="ai-mode-history">
              <div class="ai-mode-history-header" @click="whatIfShowAll = !whatIfShowAll">
                <span>📋 历史推演记录 ({{ whatIfRecords.length }})</span>
                <span class="ai-mode-history-toggle">{{ whatIfShowAll ? '收起' : '展开全部' }}</span>
              </div>
              <div class="ai-mode-history-list">
                <div v-for="r in (whatIfShowAll ? whatIfRecords : whatIfRecords.slice(0,3))" :key="r.id" class="ai-history-item">
                  <span class="ai-history-time">{{ formatAiTime(r.created_at) }}</span>
                  <span class="ai-history-summary">{{ r.summary }}</span>
                  <button class="btn-ghost btn-sm" @click="viewModeRecord(r)">查看</button>
                </div>
              </div>
            </div>
          </div>

          <!-- Result loading -->
          <div v-if="modeLoading" class="loading-state" style="margin-top:1rem">
            <div class="spinner"></div>
            <span>正在分析...</span>
          </div>
        </div>
      </template>

      <!-- Strategy Config -->
      <template v-if="activeAnalysisTab === 'config'">
        <div class="analysis-panel-header">
          <h3>调仓策略配置</h3>
          <div class="analysis-panel-actions">
            <template v-if="!rebalanceEditing">
              <button class="btn-primary btn-sm" @click="startEditRebalance">编辑配置</button>
            </template>
            <template v-else>
              <button class="btn-ghost btn-sm" @click="cancelEditRebalance">取消</button>
              <button class="btn-primary btn-sm" @click="saveRebalanceConfig">保存</button>
            </template>
            <button class="btn-ghost btn-sm" @click="switchAnalysisTab('config')">&#x2715;</button>
          </div>
        </div>
        <div v-if="rebalanceLoading" class="loading-state"><div class="spinner"></div></div>
        <div v-else-if="rebalanceConfig" class="analysis-panel-body">

          <!-- Strategy Presets -->
          <div class="config-section">
            <h4 class="config-section-title">配置策略</h4>
            <div v-if="!rebalanceEditing" class="config-current-strategy">
              <div class="strategy-badge">{{ rebalanceCurrentStrategy?.name || rebalanceConfig.strategy }}</div>
              <div class="strategy-desc">{{ rebalanceCurrentStrategy?.description }}</div>
              <div class="strategy-source" v-if="rebalanceCurrentStrategy?.source">来源：{{ rebalanceCurrentStrategy.source }}</div>
            </div>
            <div v-else class="strategy-presets-grid">
              <div v-for="p in rebalancePresets" :key="p.key"
                :class="['strategy-preset-card', { active: rebalanceEditData.strategy === p.key }]"
                @click="selectPresetStrategy(p.key)">
                <div class="preset-name">{{ p.name }}</div>
                <div class="preset-desc">{{ p.description }}</div>
                <div class="preset-alloc" v-if="p.base_allocation">
                  <span v-for="(v, k) in p.base_allocation" :key="k" class="alloc-tag">
                    {{ CATEGORY_LABELS[k] || k }} {{ (v * 100).toFixed(0) }}%
                  </span>
                </div>
                <div class="preset-source">{{ p.source }}</div>
              </div>
            </div>
          </div>

          <!-- Base Allocation -->
          <div class="config-section">
            <h4 class="config-section-title">资产基础配比</h4>
            <div class="config-alloc-grid">
              <div v-for="(label, key) in CATEGORY_LABELS" :key="key" class="config-alloc-item">
                <span class="config-alloc-label">{{ label }}</span>
                <template v-if="!rebalanceEditing">
                  <span class="config-alloc-value">{{ ((rebalanceConfig.base_allocation[key] || 0) * 100).toFixed(0) }}%</span>
                </template>
                <template v-else>
                  <input type="number" class="config-input config-input-sm"
                    v-model.number="rebalanceEditData.base_allocation[key]"
                    min="0" max="1" step="0.01" />
                  <span class="config-input-unit">%</span>
                </template>
              </div>
            </div>
          </div>

          <!-- Valuation Adjustment -->
          <div class="config-section">
            <h4 class="config-section-title">估值调整系数</h4>
            <p class="config-hint">市场估值偏离时，股票/指数/混合类资产配比的调整倍数</p>
            <div class="config-kv-grid">
              <div v-for="(v, k) in (rebalanceEditing ? rebalanceEditData.valuation_adjustment : rebalanceConfig.valuation_adjustment)" :key="k" class="config-kv-item">
                <span class="config-kv-key">{{ k }}</span>
                <template v-if="!rebalanceEditing">
                  <span class="config-kv-val">{{ v }}x</span>
                </template>
                <template v-else>
                  <input type="number" class="config-input config-input-sm"
                    v-model.number="rebalanceEditData.valuation_adjustment[k]"
                    min="0.1" max="3" step="0.1" />
                  <span class="config-input-unit">x</span>
                </template>
              </div>
            </div>
          </div>

          <!-- Valuation Percentiles -->
          <div class="config-section">
            <h4 class="config-section-title">估值百分位分界线</h4>
            <p class="config-hint">PE/PB 历史百分位的区间划分（%）</p>
            <div class="config-kv-grid">
              <div v-for="(v, k) in (rebalanceEditing ? rebalanceEditData.valuation_percentiles : rebalanceConfig.valuation_percentiles)" :key="k" class="config-kv-item">
                <span class="config-kv-key">{{ k }}</span>
                <template v-if="!rebalanceEditing">
                  <span class="config-kv-val">{{ v }}%</span>
                </template>
                <template v-else>
                  <input type="number" class="config-input config-input-sm"
                    v-model.number="rebalanceEditData.valuation_percentiles[k]"
                    min="0" max="100" step="5" />
                  <span class="config-input-unit">%</span>
                </template>
              </div>
            </div>
          </div>

          <!-- Drift Thresholds -->
          <div class="config-section">
            <h4 class="config-section-title">偏离度阈值</h4>
            <p class="config-hint">当前配比与目标配比的偏差超过阈值时触发调仓建议</p>
            <div class="config-kv-grid">
              <div class="config-kv-item">
                <span class="config-kv-key">平衡上限</span>
                <template v-if="!rebalanceEditing">
                  <span class="config-kv-val">{{ (rebalanceConfig.drift_thresholds.balanced * 100).toFixed(0) }}%</span>
                </template>
                <template v-else>
                  <input type="number" class="config-input config-input-sm"
                    v-model.number="rebalanceEditData.drift_thresholds.balanced"
                    min="0.01" max="0.2" step="0.01" />
                  <span class="config-input-unit">%</span>
                </template>
              </div>
              <div class="config-kv-item">
                <span class="config-kv-key">轻微上限</span>
                <template v-if="!rebalanceEditing">
                  <span class="config-kv-val">{{ (rebalanceConfig.drift_thresholds.slight * 100).toFixed(0) }}%</span>
                </template>
                <template v-else>
                  <input type="number" class="config-input config-input-sm"
                    v-model.number="rebalanceEditData.drift_thresholds.slight"
                    min="0.03" max="0.3" step="0.01" />
                  <span class="config-input-unit">%</span>
                </template>
              </div>
            </div>
          </div>

          <!-- Cash Targets -->
          <div class="config-section">
            <h4 class="config-section-title">现金目标比例</h4>
            <p class="config-hint">根据市场估值水平调整的现金持有目标</p>
            <div class="config-kv-grid">
              <div class="config-kv-item">
                <span class="config-kv-key">低估时</span>
                <template v-if="!rebalanceEditing">
                  <span class="config-kv-val">{{ (rebalanceConfig.cash_targets.low * 100).toFixed(0) }}%</span>
                </template>
                <template v-else>
                  <input type="number" class="config-input config-input-sm"
                    v-model.number="rebalanceEditData.cash_targets.low"
                    min="0" max="0.5" step="0.01" />
                  <span class="config-input-unit">%</span>
                </template>
              </div>
              <div class="config-kv-item">
                <span class="config-kv-key">合理时</span>
                <template v-if="!rebalanceEditing">
                  <span class="config-kv-val">{{ (rebalanceConfig.cash_targets.fair * 100).toFixed(0) }}%</span>
                </template>
                <template v-else>
                  <input type="number" class="config-input config-input-sm"
                    v-model.number="rebalanceEditData.cash_targets.fair"
                    min="0" max="0.5" step="0.01" />
                  <span class="config-input-unit">%</span>
                </template>
              </div>
              <div class="config-kv-item">
                <span class="config-kv-key">高估时</span>
                <template v-if="!rebalanceEditing">
                  <span class="config-kv-val">{{ (rebalanceConfig.cash_targets.high * 100).toFixed(0) }}%</span>
                </template>
                <template v-else>
                  <input type="number" class="config-input config-input-sm"
                    v-model.number="rebalanceEditData.cash_targets.high"
                    min="0" max="0.5" step="0.01" />
                  <span class="config-input-unit">%</span>
                </template>
              </div>
            </div>
          </div>

          <!-- Config History -->
          <div class="config-section">
            <h4 class="config-section-title" style="cursor:pointer;display:flex;align-items:center;gap:0.4rem" @click="toggleConfigHistory">
              <span>变更历史</span>
              <svg :class="['arrow-icon', { rotated: configHistoryExpanded }]" width="12" height="12" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/>
              </svg>
            </h4>
            <div v-if="configHistoryExpanded">
              <div v-if="configHistoryLoading" class="loading-state"><div class="spinner"></div></div>
              <div v-else-if="configHistory.length === 0" class="config-hint">暂无变更记录</div>
              <div v-else class="config-history-list">
                <div v-for="h in configHistory" :key="h.id" :class="['config-history-item', { active: h.is_active }]">
                  <div class="history-meta">
                    <span class="history-id">#{{ h.id }}</span>
                    <span class="history-strategy">{{ h.strategy }}</span>
                    <span v-if="h.is_active" class="history-active-badge">当前</span>
                    <span class="history-note" v-if="h.note">{{ h.note }}</span>
                  </div>
                  <div class="history-time">{{ h.created_at }}</div>
                  <button v-if="!h.is_active" class="btn-ghost btn-xs" @click="handleRollbackConfig(h.id)">回滚</button>
                </div>
              </div>
            </div>
          </div>

        </div>
      </template>
    </div>
    <!-- Summary Cards -->
    <div v-if="activeAnalysisTab === 'holdings'" class="summary-cards">
      <div class="summary-card">
        <div class="summary-label">持仓数量</div>
        <div class="summary-value">{{ summary.holding_count }}</div>
      </div>
      <div class="summary-card summary-card-cost">
        <div class="summary-label">总成本</div>
        <div class="summary-value cost-value">{{ formatMoney(summary.total_cost) }}</div>
        <div class="summary-sub">总资产 {{ formatMoney((summary.total_value || 0) + totalCash) }}</div>
      </div>
      <div class="summary-card">
        <div class="summary-label">总市值</div>
        <div class="summary-value value-value">{{ formatMoney(summary.total_value) }}</div>
      </div>
      <div class="summary-card summary-card-today">
        <div class="summary-label">
          {{ todayProfitLabel.text }}
          <span v-if="todayProfitLabel.date" class="today-profit-date">{{ todayProfitLabel.date }}</span>
        </div>
        <div :class="['summary-value', profitClass(todayTotalProfit)]">
          {{ todayTotalProfit >= 0 ? '+' : '' }}{{ formatMoney(todayTotalProfit) }}
        </div>
        <div v-if="todayTotalProfit != 0" class="today-profit-sub">
          累计 {{ summary.total_profit >= 0 ? '+' : '' }}{{ formatMoney(summary.total_profit) }}
        </div>
      </div>
      <div class="summary-card summary-card-pl">
        <div class="summary-label">总盈亏</div>
        <div :class="['summary-value', profitClass(summary.total_profit)]" style="font-size:0.9rem">
          {{ formatMoney(summary.total_profit) }}
        </div>
        <div :class="['summary-sub', profitClass(summary.profit_rate)]">
          收益率 {{ formatRate(summary.profit_rate) }}
        </div>
      </div>
      <div class="summary-card summary-card-cash" @click="showCashModal = true" style="cursor:pointer">
        <div class="summary-label">
          零钱合计
          <span style="font-size:0.65rem;opacity:0.6;margin-left:4px">✎</span>
        </div>
        <div class="summary-value cash-value">{{ formatMoney(totalCash) }}</div>
        <div class="cash-accounts">
          <span class="cash-account-tag">🐟 {{ formatMoney(cashBalances['小鱼儿']) }}</span>
          <span class="cash-account-tag">🌸 {{ formatMoney(cashBalances['花无缺']) }}</span>
        </div>
      </div>
    </div>

    <!-- Holdings Table -->
    <div v-if="activeAnalysisTab === 'holdings'" class="card holdings-card">
      <div v-if="holdings.length > 0" class="table-search-bar">
        <select v-model="accountFilter" class="input-field account-filter-select">
          <option value="">全部账户</option>
          <option value="花无缺">花无缺</option>
          <option value="小鱼儿">小鱼儿</option>
        </select>
        <input v-model="searchQuery" class="input-field" placeholder="搜索基金名称 / 代码 / 指数..." />
        <span style="font-size:0.75rem;color:var(--color-text-muted);white-space:nowrap">
          {{ activeHoldings.length }} / {{ holdings.length }}
        </span>
      </div>
      <div v-if="loading" class="portfolio-loading">
        <Skeleton variant="title" width="30%" />
        <Skeleton variant="card" />
        <Skeleton variant="text" :count="5" />
      </div>

      <div v-else-if="holdings.length === 0">
        <EmptyState
          icon="portfolio"
          title="暂无持仓数据"
          description="添加持仓后即可查看投资组合分析"
          action-text="添加第一笔持仓"
          @action="openAddForm"
        />
      </div>

      <table v-else class="data-table">
        <thead>
          <tr>
            <th>基金名称</th>
            <th>基金代码</th>
            <th>账户</th>
            <th>关联指数</th>
            <th class="text-right">持有份额</th>
            <th class="text-right">成本价</th>
            <th class="text-right th-sortable" @click="toggleSort('total_cost')">总成本{{ sortIndicator('total_cost') }}</th>
            <th class="text-right">当前净值</th>
            <th class="text-right th-sortable" style="position:relative">
              <span @click="toggleSort('today_profit')">今日涨跌 / 收益{{ sortIndicator('today_profit') }}</span>
              <span class="th-filter" @click.stop="todayFilter = todayFilter === 'all' ? 'down' : todayFilter === 'down' ? 'up' : 'all'" :title="todayFilter === 'all' ? '全部' : todayFilter === 'up' ? '仅涨' : '仅跌'">
                <svg width="12" height="12" fill="currentColor" viewBox="0 0 16 16">
                  <path v-if="todayFilter === 'all'" d="M1.5 1.5A.5.5 0 012 1h12a.5.5 0 01.5.5v2a.5.5 0 01-.128.334L10 8.692V13.5a.5.5 0 01-.223.416l-3 2A.5.5 0 016 15.5V8.692L1.628 3.834A.5.5 0 011.5 3.5v-2z"/>
                  <path v-else-if="todayFilter === 'up'" d="M8 15a.5.5 0 01-.5-.5V3.707L4.354 6.854a.5.5 0 11-.708-.708l4-4a.5.5 0 01.708 0l4 4a.5.5 0 01-.708.708L8.5 3.707V14.5A.5.5 0 018 15z" fill="#dc2626"/>
                  <path v-else d="M8 1a.5.5 0 01.5.5v10.793l3.146-3.147a.5.5 0 01.708.708l-4 4a.5.5 0 01-.708 0l-4-4a.5.5 0 01.708-.708L7.5 12.293V1.5A.5.5 0 018 1z" fill="#16a34a"/>
                </svg>
              </span>
            </th>
            <th class="text-right th-sortable" @click="toggleSort('current_value')">当前市值{{ sortIndicator('current_value') }}</th>
            <th class="text-right th-sortable" @click="toggleSort('profit_loss')">盈亏{{ sortIndicator('profit_loss') }}</th>
            <th class="text-right th-sortable" @click="toggleSort('profit_rate')">收益率{{ sortIndicator('profit_rate') }}</th>
            <th>净值更新</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="h in activeHoldings" :key="h.id">
            <td class="fund-name">
              <span class="fund-name-text">{{ h.fund_name }}</span>
              <span v-if="h.fund_category" :class="['badge', 'badge-sm', 'badge-category-' + h.fund_category]">
                {{ {bond: '债券', equity: '股票', hybrid: '混合', index: '指数', money_market: '货币', bond_index: '债指', convertible_bond: '可转债'}[h.fund_category] || h.fund_category }}
              </span>
            </td>
            <td><code>{{ h.fund_code }}</code></td>
            <td><span class="account-badge">{{ h.account || '默认账户' }}</span></td>
            <td>{{ h.index_name || '--' }}</td>
            <td class="text-right">{{ h.shares?.toLocaleString() }}</td>
            <td class="text-right">{{ h.cost_price?.toFixed(4) }}</td>
            <td class="text-right">{{ formatMoney(h.total_cost) }}</td>
            <td class="text-right">{{ h.current_price?.toFixed(4) || '--' }}</td>
            <td :class="['text-right', profitClass(h.today_change_pct)]">
              <template v-if="h.today_change_pct !== undefined && h.today_change_pct !== null">
                <div class="today-change-cell">
                  <span class="today-change-pct">{{ h.today_change_pct > 0 ? '+' : '' }}{{ h.today_change_pct.toFixed(2) }}%</span>
                  <span v-if="h.today_profit !== undefined && h.today_profit !== null" class="today-change-profit">
                    {{ h.today_profit >= 0 ? '+' : '' }}{{ h.today_profit.toFixed(2) }}元
                  </span>
                </div>
              </template>
              <span v-else class="text-muted">--</span>
            </td>
            <td class="text-right">{{ formatMoney(h.current_value) }}</td>
            <td :class="['text-right', profitClass(h.profit_loss)]">{{ formatMoney(h.profit_loss) }}</td>
            <td :class="['text-right', profitClass(h.profit_rate)]">{{ formatRate(h.profit_rate) }}</td>
            <td>
              <span :class="['freshness-tag', freshnessHint(h.price_updated_at).cls]">
                {{ freshnessHint(h.price_updated_at).text }}
              </span>
            </td>
            <td class="actions-cell">
              <button class="btn-ghost btn-sm" @click="openDetail(h)" title="查看详情">详情</button>
              <button class="btn-ghost btn-sm btn-analysis-text" @click="openFundAnalysis(h)" title="基金分析">分析</button>
              <button class="btn-ghost btn-sm" @click="refreshSingle(h)" :title="'刷新净值'" :disabled="refreshingRowId === h.id">
                <span v-if="refreshingRowId === h.id && !refreshRowResult" class="btn-refresh-spinner"></span>
                <span v-else-if="refreshRowResult?.id === h.id && refreshRowResult.error" style="color:#dc2626">失败</span>
                <span v-else-if="refreshRowResult?.id === h.id && refreshRowResult.pct != null" :class="refreshRowResult.pct >= 0 ? 'profit-positive' : 'profit-negative'">
                  {{ refreshRowResult.pct >= 0 ? '+' : '' }}{{ refreshRowResult.pct?.toFixed(2) }}%
                </span>
                <span v-else>刷新</span>
              </button>
              <button class="btn-ghost btn-sm btn-primary-text" @click="openAddPurchase(h)" title="买入">买入</button>
              <button class="btn-ghost btn-sm btn-sell-text" @click="openSell(h)" title="卖出">卖出</button>
              <button class="btn-ghost btn-sm" @click="openTxForm(h)" title="记账（分红等）">记账</button>
              <button class="btn-ghost btn-sm" @click="viewTransactions(h)" title="交易记录">记录</button>
              <button class="btn-ghost btn-sm" @click="openEditForm(h)" title="编辑">编辑</button>
              <button class="btn-ghost btn-sm btn-danger-text" @click="handleDelete(h)" title="删除">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 已清仓 -->
    <div v-if="closedHoldings.length > 0" class="closed-holdings-section">
      <div class="closed-holdings-header" @click="showClosedHoldings = !showClosedHoldings">
        <span>已清仓 ({{ closedHoldings.length }})</span>
        <span class="closed-holdings-toggle">{{ showClosedHoldings ? '收起' : '展开' }}</span>
      </div>
      <table v-if="showClosedHoldings" class="holdings-table closed-holdings-table">
        <thead>
          <tr>
            <th>基金名称</th>
            <th>代码</th>
            <th>账户</th>
            <th>清仓时净值</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="h in closedHoldings" :key="h.id" class="closed-row">
            <td class="fund-name">{{ h.fund_name }}</td>
            <td><code>{{ h.fund_code }}</code></td>
            <td><span class="account-badge">{{ h.account || '默认账户' }}</span></td>
            <td class="text-right">{{ h.current_price?.toFixed(4) || '--' }}</td>
            <td class="actions-cell">
              <button class="btn-ghost btn-sm" @click="viewTransactions(h)" title="交易记录">记录</button>
              <button class="btn-ghost btn-sm btn-danger-text" @click="handleDelete(h)" title="删除记录">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Batch Refresh Progress -->
    <div v-if="refreshProgress" class="refresh-progress-toast">
      <div class="refresh-toast-header">
        <span>刷新净值 {{ refreshProgress.done }}/{{ refreshProgress.total }}</span>
        <span v-if="refreshProgress.done === refreshProgress.total" style="color:#16a34a">✓</span>
      </div>
      <div class="refresh-toast-track">
        <div class="refresh-toast-bar" :style="{width: (refreshProgress.done / refreshProgress.total * 100) + '%'}"></div>
      </div>
    </div>

    <!-- 零钱 Modal -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="showCashModal" class="modal-overlay" @click.self="showCashModal = false">
          <div class="modal-box" style="max-width:400px">
            <h3 class="modal-title">零钱管理</h3>
            <div class="cash-modal-balances">
              <div class="cash-modal-account" v-for="uid in ['小鱼儿', '花无缺']" :key="uid"
                   :class="{ selected: cashForm.user_id === uid }" @click="cashForm.user_id = uid">
                <span class="cash-modal-uid">{{ uid === '小鱼儿' ? '🐟 小鱼儿' : '🌸 花无缺' }}</span>
                <strong>{{ formatMoney(cashBalances[uid]) }}</strong>
              </div>
            </div>
            <form @submit.prevent="submitCashAdjust" class="modal-form">
              <div class="btn-group" style="margin-bottom:0.75rem">
                <button type="button" :class="['btn-sm', cashMode === 'add' ? 'btn-primary' : 'btn-secondary']" @click="cashMode = 'add'">存入/支出</button>
                <button type="button" :class="['btn-sm', cashMode === 'set' ? 'btn-primary' : 'btn-secondary']" @click="cashMode = 'set'">直接设置</button>
              </div>
              <div class="form-group">
                <label>{{ cashMode === 'add' ? '金额（正数存入，负数支出）' : '新余额' }}</label>
                <input v-model.number="cashForm.amount" type="number" step="0.01" class="input-field" :placeholder="cashMode === 'add' ? '正数存入，负数支出' : '输入新的余额数值'" :required="cashMode === 'add'" />
                <span style="font-size:0.75rem;color:var(--color-text-muted)">卖出成交后金额会自动入账，此处可手动调整</span>
              </div>
              <div class="modal-actions">
                <button type="button" class="btn btn-ghost" @click="showCashModal = false">取消</button>
                <button type="submit" class="btn btn-primary">确认</button>
              </div>
            </form>
          </div>
        </div>
      </Transition>
    </Teleport>

    <!-- Add/Edit Modal -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="showForm" class="modal-overlay" @click.self="showForm = false">
          <div class="modal-box">
            <h3 class="modal-title">{{ editingId ? '编辑持仓' : '新增持仓' }}</h3>
            <form @submit.prevent="submitForm" class="modal-form">
              <!-- 基金代码 + 自动查询 -->
              <div class="form-group">
                <label>基金代码 *</label>
                <div class="lookup-row">
                  <input v-model="form.fund_code" class="input-field" placeholder="输入基金代码，如 161725" required :disabled="!!editingId" />
                  <button v-if="!editingId" type="button" class="btn-secondary btn-sm" @click="doLookup" :disabled="lookupLoading">
                    {{ lookupLoading ? '查询中...' : '查询' }}
                  </button>
                </div>
              </div>
              <!-- 查询结果预览 -->
              <div v-if="lookupResult" class="lookup-preview">
                <span class="lookup-tag">{{ lookupResult.fund_type }}</span>
                <span>{{ lookupResult.fund_name }}</span>
                <span v-if="lookupResult.tracking_index" class="lookup-index">跟踪：{{ lookupResult.tracking_index }}</span>
                <span v-if="lookupResult.scale" class="lookup-scale">规模：{{ lookupResult.scale }}</span>
              </div>
              <div class="form-row">
                <div class="form-group">
                  <label>基金名称 *</label>
                  <input v-model="form.fund_name" class="input-field" placeholder="自动填充或手动输入" required />
                </div>
                <div class="form-group">
                  <label>关联指数名称</label>
                  <input v-model="form.index_name" class="input-field" placeholder="自动填充或手动输入" />
                </div>
              </div>
              <div class="form-row">
                <div class="form-group">
                  <label>持有份额 *</label>
                  <input v-model.number="form.shares" type="number" step="0.01" class="input-field" placeholder="如 10000" required />
                </div>
                <div class="form-group">
                  <label>当前净值 *</label>
                  <input v-model.number="form.current_price" type="number" step="0.0001" class="input-field" placeholder="如 1.2345" required />
                </div>
              </div>
              <div class="form-row">
                <div class="form-group">
                  <label>买入总金额（元）</label>
                  <input v-model.number="form.total_cost" type="number" step="0.01" class="input-field" placeholder="如 15000，不填则盈亏显示为 0" />
                  <span v-if="form.total_cost > 0 && form.shares > 0" style="font-size:0.75rem;color:var(--color-text-muted)">
                    成本均价 ≈ {{ (form.total_cost / form.shares).toFixed(4) }} 元/份
                  </span>
                  <span v-else style="font-size:0.7rem;color:var(--color-text-muted)">填写你累计投入的总金额，用于计算盈亏</span>
                </div>
                <div class="form-group">
                  <label>首次买入日期</label>
                  <input v-model="form.buy_date" type="date" class="input-field" />
                </div>
              </div>
              <div class="form-group">
                <label>所属账户</label>
                <select v-model="form.account" class="input-field">
                  <option value="花无缺">花无缺</option>
                  <option value="小鱼儿">小鱼儿</option>
                </select>
              </div>
              <div class="form-group">
                <label>备注</label>
                <input v-model="form.notes" class="input-field" placeholder="可选" />
              </div>
              <div class="modal-actions">
                <button type="button" class="btn-secondary" @click="showForm = false">取消</button>
                <button type="submit" class="btn-primary">{{ editingId ? '保存' : '新增' }}</button>
              </div>
            </form>
          </div>
        </div>
      </Transition>
    </Teleport>

    <!-- Transaction Modal -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="showTxForm" class="modal-overlay" @click.self="showTxForm = false">
          <div class="modal-box">
            <h3 class="modal-title">记账 - {{ txFundCode }}</h3>
            <form @submit.prevent="submitTx" class="modal-form">
              <div class="form-row">
                <div class="form-group">
                  <label>交易类型</label>
                  <select v-model="txForm.transaction_type" class="input-field">
                    <option value="buy">买入</option>
                    <option value="sell">卖出</option>
                    <option value="dividend">分红</option>
                  </select>
                </div>
                <div class="form-group">
                  <label>交易日期</label>
                  <input v-model="txForm.transaction_date" type="date" class="input-field" required />
                </div>
              </div>
              <div class="form-row">
                <div class="form-group">
                  <label>交易金额</label>
                  <input v-model.number="txForm.amount" type="number" step="0.01" class="input-field" required />
                </div>
                <div class="form-group">
                  <label>交易份额</label>
                  <input v-model.number="txForm.shares" type="number" step="0.01" class="input-field" />
                  <span v-if="txForm.shares <= 0 && txForm.amount > 0 && txForm.price > 0" class="field-hint">
                    预估份额: {{ (txForm.amount / txForm.price).toFixed(2) }}
                  </span>
                </div>
              </div>
              <div class="form-row">
                <div class="form-group">
                  <label>交易价格</label>
                  <input v-model.number="txForm.price" type="number" step="0.0001" class="input-field" />
                </div>
                <div class="form-group">
                  <label>备注</label>
                  <input v-model="txForm.notes" class="input-field" placeholder="可选" />
                </div>
              </div>
              <div class="modal-actions">
                <button type="button" class="btn-secondary" @click="showTxForm = false">取消</button>
                <button type="submit" class="btn-primary">确认</button>
              </div>
            </form>
          </div>
        </div>
      </Transition>
    </Teleport>

    <!-- Transaction History Modal -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="showTxHistory" class="modal-overlay" @click.self="showTxHistory = false">
          <div class="modal-box modal-wide">
            <h3 class="modal-title">交易记录</h3>
            <div v-if="transactions.length === 0" class="empty-state" style="padding: 2rem">
              <p>暂无交易记录</p>
            </div>
            <table v-else class="data-table">
              <thead>
                <tr>
                  <th>日期</th>
                  <th>类型</th>
                  <th>状态</th>
                  <th class="text-right">金额</th>
                  <th class="text-right">份额</th>
                  <th class="text-right">净值</th>
                  <th>时间</th>
                  <th>备注</th>
                  <th>标签</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="tx in transactions" :key="tx.id">
                  <td>
                    {{ tx.transaction_date }}
                    <div v-if="tx.expected_confirm_date" class="tx-confirm-hint">预计 {{ tx.expected_confirm_date }} 确认</div>
                  </td>
                  <td><span :class="['badge', txTypeBadge(tx.transaction_type)]">{{ txTypeLabel(tx.transaction_type) }}</span></td>
                  <td><span :class="['badge', txStatusBadge(tx.status || 'confirmed')]">{{ txStatusLabel(tx.status || 'confirmed') }}</span></td>
                  <td class="text-right">{{ txDisplayAmount(tx) }}</td>
                  <td class="text-right">{{ tx.status === 'pending' ? (tx.submitted_shares ? tx.submitted_shares.toLocaleString() + ' (预估)' : '--') : (tx.shares?.toLocaleString() || '--') }}</td>
                  <td class="text-right">{{ tx.price?.toFixed(4) || '--' }}</td>
                  <td>{{ tx.transaction_time || '--' }}</td>
                  <td>{{ tx.notes || '--' }}</td>
                  <td>
                    <div class="tx-tags-cell">
                      <span v-for="tag in (tx.tags || [])" :key="tag" class="badge badge-neutral badge-sm">{{ tag }}</span>
                      <button class="btn-ghost btn-xs" @click="openTxTags(tx.id)" title="编辑标签">🏷️</button>
                    </div>
                  </td>
                  <td>
                    <button v-if="tx.status === 'pending'" class="btn-ghost btn-sm btn-primary-text" @click="openConfirmTx(tx)">确认</button>
                    <button v-if="tx.status === 'pending'" class="btn-ghost btn-sm btn-danger-text" @click="handleCancelPendingTx(tx)">撤销</button>
                    <button v-if="(tx.status || 'confirmed') === 'confirmed' && tx.transaction_type === 'sell'" class="btn-ghost btn-sm btn-info-text" @click="handleSettle(tx)">已到账</button>
                    <span v-if="tx.status === 'settled'" class="text-muted">已完成</span>
                  </td>
                </tr>
              </tbody>
            </table>
            <div class="modal-actions" style="margin-top: 1rem">
              <button class="btn-secondary" @click="showTxHistory = false">关闭</button>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>

    <!-- Add Purchase Modal (追加买入) — amount-based -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="showAddPurchase" class="modal-overlay" @click.self="showAddPurchase = false">
          <div class="modal-box">
            <h3 class="modal-title">买入 — {{ addPurchaseHolding?.fund_name }}</h3>
            <p class="modal-subtitle">{{ addPurchaseHolding?.fund_code }} · 当前持有 {{ addPurchaseHolding?.shares?.toLocaleString() }} 份 · 净值 {{ addPurchaseHolding?.current_price || '--' }}</p>
            <form @submit.prevent="submitAddPurchase" class="modal-form">
              <div class="form-group">
                <label>买入金额（元）*</label>
                <input v-model.number="addPurchaseForm.amount" type="number" step="0.01" class="input-field" placeholder="如 10000" required />
              </div>
              <div v-if="addPurchaseForm.amount > 0 && addPurchaseHolding?.current_price" class="add-purchase-preview">
                <span>预估可买入约 <strong>{{ addPurchaseEstShares }}</strong> 份</span>
                <span class="preview-note">（按当前净值 {{ addPurchaseHolding.current_price }} 估算，实际以 T+1 确认为准）</span>
              </div>
              <div class="form-row">
                <div class="form-group">
                  <label>买入日期</label>
                  <input v-model="addPurchaseForm.transaction_date" type="date" class="input-field" />
                </div>
                <div class="form-group">
                  <label>买入时间</label>
                  <input v-model="addPurchaseForm.transaction_time" type="time" class="input-field" />
                  <span class="preview-note">15:00 前按当日净值，之后按次日</span>
                </div>
              </div>
              <div class="form-group">
                <label>备注</label>
                <input v-model="addPurchaseForm.notes" class="input-field" placeholder="可选" />
              </div>
              <div class="modal-actions">
                <button type="button" class="btn-secondary" @click="showAddPurchase = false">取消</button>
                <button type="submit" class="btn-primary">提交买入</button>
              </div>
            </form>
          </div>
        </div>
      </Transition>
    </Teleport>

    <!-- Sell Modal (卖出) — shares-based -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="showSell" class="modal-overlay" @click.self="showSell = false">
          <div class="modal-box">
            <h3 class="modal-title">卖出 — {{ sellHolding?.fund_name }}</h3>
            <p class="modal-subtitle">{{ sellHolding?.fund_code }} · 当前持有 {{ sellHolding?.shares?.toLocaleString() }} 份 · 净值 {{ sellHolding?.current_price || '--' }}</p>
            <form @submit.prevent="submitSell" class="modal-form">
              <div class="form-group">
                <label>卖出份额 *</label>
                <input v-model.number="sellForm.shares" type="number" step="0.01" class="input-field" placeholder="如 5000" required />
              </div>
              <div v-if="sellForm.shares > 0 && sellHolding?.current_price" class="add-purchase-preview sell-preview">
                <span>预估可赎回约 <strong>¥{{ Number(sellEstAmount).toLocaleString() }}</strong></span>
                <span class="preview-note">（按当前净值 {{ sellHolding.current_price }} 估算，实际以 T+1 确认为准）</span>
              </div>
              <div class="form-row">
                <div class="form-group">
                  <label>卖出日期</label>
                  <input v-model="sellForm.transaction_date" type="date" class="input-field" />
                </div>
                <div class="form-group">
                  <label>卖出时间</label>
                  <input v-model="sellForm.transaction_time" type="time" class="input-field" />
                  <span class="preview-note">15:00 前按当日净值，之后按次日</span>
                </div>
              </div>
              <div class="form-group">
                <label>备注</label>
                <input v-model="sellForm.notes" class="input-field" placeholder="可选" />
              </div>
              <div class="modal-actions">
                <button type="button" class="btn-secondary" @click="showSell = false">取消</button>
                <button type="submit" class="btn-primary">提交卖出</button>
              </div>
            </form>
          </div>
        </div>
      </Transition>
    </Teleport>

    <!-- Confirm Transaction Modal (确认交易) -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="showConfirmTx" class="modal-overlay" @click.self="showConfirmTx = false">
          <div class="modal-box">
            <h3 class="modal-title">确认交易</h3>
            <p class="modal-subtitle">
              {{ txTypeLabel(confirmTxData?.transaction_type) }}
              · {{ confirmTxData?.transaction_type === 'buy' ? '金额' : '份额' }}
              {{ confirmTxData?.transaction_type === 'buy' ? '¥' + (confirmTxData?.submitted_amount || 0).toLocaleString() : (confirmTxData?.submitted_shares || 0).toLocaleString() + ' 份' }}
              <span v-if="confirmTxData?.expected_confirm_date" class="pending-confirm-hint" style="margin-left:0.5rem">
                预计 {{ confirmTxData.expected_confirm_date }} 确认
              </span>
            </p>
            <form @submit.prevent="submitConfirmTx" class="modal-form">
              <div class="form-group">
                <label>T+1 确认净值 *</label>
                <input v-model.number="confirmTxPrice" type="number" step="0.0001" class="input-field" placeholder="输入确认日的实际净值" required />
              </div>
              <div v-if="confirmTxPrice > 0" class="add-purchase-preview">
                <span v-if="confirmTxData?.transaction_type === 'buy'">
                  实际买入约 <strong>{{ ((confirmTxData?.submitted_amount || 0) / confirmTxPrice).toFixed(2) }}</strong> 份
                </span>
                <span v-else>
                  实际赎回约 <strong>¥{{ ((confirmTxData?.submitted_shares || 0) * confirmTxPrice).toFixed(2) }}</strong>
                </span>
              </div>
              <div class="modal-actions">
                <button type="button" class="btn-secondary" @click="showConfirmTx = false">返回</button>
                <button type="button" class="btn-ghost btn-danger-text" @click="doCancelFromConfirm">撤销此交易</button>
                <button type="submit" class="btn-primary">确认</button>
              </div>
            </form>
          </div>
        </div>
      </Transition>
    </Teleport>

    <!-- Fund Detail Modal -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="showDetail" class="modal-overlay" @click.self="showDetail = false; chartMode = ''">
          <div class="modal-box modal-wide">
            <h3 class="modal-title">
              {{ detailFundName }}
              <span style="font-size:0.75rem;font-weight:400;color:var(--color-text-muted);margin-left:0.5rem">
                {{ chartMode === 'chart5y' ? '近 5 年净值走势 · 拖拽选择时间段' : '持仓分析' }}
              </span>
            </h3>

            <!-- 基金搜索 + 快捷切换 -->
            <template v-if="chartMode === 'chart5y'">
              <div class="chart-search-bar">
                <form @submit.prevent="searchAndLoadChart" class="chart-search-form">
                  <input v-model="chartSearchQuery" type="text" class="input-field chart-search-input"
                    placeholder="输入基金代码，如 004853" @keydown.enter.prevent="searchAndLoadChart" />
                  <button type="submit" class="btn-primary btn-sm" :disabled="chartSearching || !chartSearchQuery.trim()">
                    {{ chartSearching ? '查询中...' : '查看' }}
                  </button>
                </form>
                <div v-if="chartSearchError" class="chart-search-error">{{ chartSearchError }}</div>
                <div class="chart-fund-switcher">
                  <span v-for="h in holdings" :key="h.id"
                    :class="['fund-chip', { active: chartSearchQuery === h.fund_code }]"
                    @click="switchChartFund(h.fund_code, h.fund_name)"
                    :title="h.fund_name">
                    {{ h.fund_name?.slice(0, 6) }}
                  </span>
                </div>
              </div>

              <div v-if="fundChartLoading" class="loading-state">
                <div class="spinner"></div>
                <span>加载 5 年净值数据...</span>
              </div>
              <div v-else-if="fundChartData" class="fund-chart-5y">
                <div class="chart-5y-canvas">
                  <svg class="nav-chart" :viewBox="'0 0 700 300'" preserveAspectRatio="xMidYMid meet" @mousedown="onChart5yMouseDown" @mousemove="onChart5yMouseMove" @mouseup="onChart5yMouseUp" @mouseleave="onChart5yMouseLeave">
                    <!-- 网格线 -->
                    <line v-for="(y, i) in chart5yGridY" :key="'g'+i" :x1="55" :y1="y" :x2="680" :y2="y" stroke="rgba(255, 255, 255, 0.1)" stroke-width="0.5"/>
                    <!-- Y 轴标签 -->
                    <text v-for="la in chart5yYLabels" :key="'yl'+la.label" :x="50" :y="la.y + 4" text-anchor="end" fill="#9ca3af" font-size="11" font-family="monospace">{{ la.label }}</text>
                    <!-- X 轴标签 -->
                    <text v-for="la in chart5yXLabels" :key="'xl'+la.label" :x="la.x" :y="296" text-anchor="middle" fill="#9ca3af" font-size="10">{{ la.label }}</text>
                    <!-- 净值线 -->
                    <polyline :points="chart5yLinePoints" fill="none" stroke="#3b82f6" stroke-width="1.5" stroke-linejoin="round"/>
                    <!-- 填充 -->
                    <polyline :points="chart5yLinePoints + ' 680,280 55,280'" fill="url(#chart5yGrad)" opacity="0.12"/>
                    <!-- 参考基准线(100%) -->
                    <line x1="55" y1="chart5yZeroY" x2="680" y2="chart5yZeroY" stroke="#9ca3af" stroke-width="0.8" stroke-dasharray="4,4"/>
                    <defs>
                      <linearGradient id="chart5yGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stop-color="#3b82f6"/>
                        <stop offset="100%" stop-color="#3b82f6" stop-opacity="0"/>
                      </linearGradient>
                    </defs>
                    <!-- 选择遮罩 -->
                    <rect v-if="chart5yBrushRect" :x="chart5yBrushRect.x" :y="chart5yBrushRect.y" :width="chart5yBrushRect.w" :height="chart5yBrushRect.h" fill="#3b82f6" opacity="0.15" stroke="#3b82f6" stroke-width="0.8" stroke-dasharray="3,3"/>
                    <!-- hover 竖线 -->
                    <line v-if="chart5yHoverPoint && !chart5yBrush?.active" :x1="chart5yHoverPoint.x" :y1="20" :x2="chart5yHoverPoint.x" :y2="280" stroke="#9ca3af" stroke-width="0.8" stroke-dasharray="3,3"/>
                    <!-- hover 圆点 -->
                    <circle v-if="chart5yHoverPoint && !chart5yBrush?.active" :cx="chart5yHoverPoint.x" :cy="chart5yHoverPoint.y" r="4" fill="#fff" stroke="#3b82f6" stroke-width="2"/>
                  </svg>
                  <div v-if="chart5yHoverPoint" class="chart-tooltip" :style="chart5yTooltipStyle">
                    <div class="tooltip-date">{{ chart5yHoverPoint.date }}</div>
                    <div class="tooltip-nav">净值: <strong>{{ chart5yHoverPoint.nav?.toFixed(4) }}</strong></div>
                    <div :class="['tooltip-nav', chart5yHoverPoint.pct >= 0 ? 'profit-positive' : 'profit-negative']">
                      {{ (chart5yHoverPoint.pct >= 0 ? '+' : '') + chart5yHoverPoint.pct?.toFixed(2) + '%' }}
                    </div>
                  </div>
                </div>
                <!-- 涨跌幅统计 -->
                <div class="chart-5y-stats" v-if="fundChartStats">
                  <div class="stat-card-stat">
                    <span class="stat-label-stat">区间</span>
                    <span class="stat-value-stat">{{ fundChartStats.firstDate }} ~ {{ fundChartStats.lastDate }}</span>
                  </div>
                  <div class="stat-card-stat">
                    <span class="stat-label-stat">累计涨跌幅</span>
                    <span :class="['stat-value-stat', 'stat-' + (fundChartStats.totalReturn >= 0 ? 'up' : 'down')]">
                      {{ (fundChartStats.totalReturn * 100).toFixed(2) }}%
                    </span>
                  </div>
                  <div class="stat-card-stat">
                    <span class="stat-label-stat">年化收益率</span>
                    <span :class="['stat-value-stat', 'stat-' + (fundChartStats.annualReturn >= 0 ? 'up' : 'down')]">
                      {{ (fundChartStats.annualReturn * 100).toFixed(2) }}%
                    </span>
                  </div>
                  <div class="stat-card-stat">
                    <span class="stat-label-stat">最大回撤</span>
                    <span class="stat-value-stat stat-down">{{ (fundChartStats.maxDrawdown * 100).toFixed(2) }}%</span>
                  </div>
                  <div class="stat-card-stat">
                    <span class="stat-label-stat">起始净值</span>
                    <span class="stat-value-stat">{{ fundChartStats.firstNav?.toFixed(4) }}</span>
                  </div>
                  <div class="stat-card-stat">
                    <span class="stat-label-stat">最新净值</span>
                    <span class="stat-value-stat">{{ fundChartStats.lastNav?.toFixed(4) }}</span>
                  </div>
                </div>
              </div>
              <div v-else class="detail-section" style="padding:2rem;text-align:center;color:var(--color-text-muted)">
                <p>暂无近 5 年净值数据</p>
              </div>
              <div class="modal-actions chart5y-actions" style="margin-top:1rem">
                <button v-if="chart5yZoomRange" class="btn-ghost btn-sm" @click="resetChart5yZoom">恢复全景</button>
                <button class="btn-secondary" @click="showDetail = false">关闭</button>
              </div>
            </template>

            <!-- 持仓分析模式（原有详情） -->
            <template v-if="chartMode === 'detail'">
            <div v-if="detailLoading" class="loading-state">
              <div class="spinner"></div>
              <span>正在查询基金持仓数据...</span>
            </div>
            <div v-else-if="detailData" class="fund-detail">
              <!-- Asset Allocation -->
              <div v-if="detailData.asset_allocation?.length" class="detail-section">
                <h4 class="detail-heading">资产配置</h4>
                <div class="alloc-bars">
                  <div v-for="a in detailData.asset_allocation" :key="a.type" class="alloc-item">
                    <span class="alloc-label">{{ a.type }}</span>
                    <div class="alloc-bar-bg">
                      <div class="alloc-bar" :style="{ width: a.pct + '%' }"></div>
                    </div>
                    <span class="alloc-pct">{{ a.pct }}%</span>
                  </div>
                </div>
              </div>
              <!-- Top Stocks -->
              <div v-if="detailData.top_stocks?.length" class="detail-section">
                <h4 class="detail-heading">重仓股票 Top {{ detailData.top_stocks.length }}</h4>
                <table class="data-table mini-table">
                  <thead>
                    <tr>
                      <th>股票名称</th>
                      <th>代码</th>
                      <th class="text-right">占净值比</th>
                      <th class="text-right">持仓市值(万)</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="s in detailData.top_stocks" :key="s.stock_code">
                      <td class="fund-name">{{ s.stock_name }}</td>
                      <td><code>{{ s.stock_code }}</code></td>
                      <td class="text-right">{{ s.pct_nav }}%</td>
                      <td class="text-right">{{ (s.market_value / 10000).toFixed(0) }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <!-- Bond Holdings -->
              <div v-if="detailData.bond_holdings?.length" class="detail-section">
                <h4 class="detail-heading">债券持仓</h4>
                <div v-if="detailData.bond_type_summary && Object.keys(detailData.bond_type_summary).length" class="bond-summary">
                  <span v-for="(v, k) in detailData.bond_type_summary" :key="k" :class="['bond-tag', `bond-${k}`]">{{ k }} {{ v }}%</span>
                </div>
                <table class="data-table mini-table">
                  <thead>
                    <tr>
                      <th>债券名称</th>
                      <th>类型</th>
                      <th class="text-right">占净值比</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="b in detailData.bond_holdings" :key="b.bond_code">
                      <td>{{ b.bond_name }}</td>
                      <td><span :class="['bond-tag', `bond-${b.bond_type}`]">{{ b.bond_type }}</span></td>
                      <td class="text-right">{{ b.pct_nav }}%</td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <!-- Industry -->
              <div v-if="detailData.industry_allocation?.length" class="detail-section">
                <h4 class="detail-heading">行业配置</h4>
                <div class="industry-bars">
                  <div v-for="i in detailData.industry_allocation" :key="i.industry" class="alloc-item">
                    <span class="alloc-label">{{ i.industry }}</span>
                    <div class="alloc-bar-bg">
                      <div class="alloc-bar" :style="{ width: (i.pct_nav / maxIndustryPct * 100) + '%' }"></div>
                    </div>
                    <span class="alloc-pct">{{ i.pct_nav }}%</span>
                  </div>
                </div>
              </div>
              <!-- Performance Analysis -->
              <div v-if="detailData.performance" class="detail-section">
                <h4 class="detail-heading">投资表现分析</h4>
                <div class="perf-grid">
                  <div class="perf-item">
                    <span class="perf-label">持有份额</span>
                    <span class="perf-value">{{ detailData.performance.shares?.toLocaleString() || '--' }}</span>
                  </div>
                  <div class="perf-item">
                    <span class="perf-label">成本价</span>
                    <span class="perf-value">{{ detailData.performance.cost_price?.toFixed(4) || '--' }}</span>
                  </div>
                  <div class="perf-item">
                    <span class="perf-label">总成本</span>
                    <span class="perf-value">{{ formatMoney(detailData.performance.total_cost) }}</span>
                  </div>
                  <div class="perf-item">
                    <span class="perf-label">当前市值</span>
                    <span class="perf-value">{{ formatMoney(detailData.performance.current_value) }}</span>
                  </div>
                  <div class="perf-item">
                    <span class="perf-label">累计盈亏</span>
                    <span :class="['perf-value', profitClass(detailData.performance.profit_loss)]">{{ formatMoney(detailData.performance.profit_loss) }}</span>
                  </div>
                  <div class="perf-item">
                    <span class="perf-label">收益率</span>
                    <span :class="['perf-value', profitClass(detailData.performance.profit_rate)]">{{ detailData.performance.profit_rate }}%</span>
                  </div>
                  <div class="perf-item">
                    <span class="perf-label">买入次数</span>
                    <span class="perf-value">{{ detailData.performance.buy_count }} 次</span>
                  </div>
                  <div class="perf-item">
                    <span class="perf-label">卖出次数</span>
                    <span class="perf-value">{{ detailData.performance.sell_count }} 次</span>
                  </div>
                </div>
              </div>
            </div>
            <div class="modal-actions" style="margin-top: 1rem">
              <button class="btn-secondary" @click="showDetail = false">关闭</button>
            </div>
          </template>
        </div>
      </div>
    </Transition>
  </Teleport>

    <!-- Confirm Dialog -->
    <ConfirmDialog
      :visible="confirm.visible"
      :title="confirm.title"
      :message="confirm.message"
      :danger="confirm.danger"
      @confirm="confirm.onConfirm"
      @cancel="confirm.visible = false"
    />

    <!-- Tag Editor Modal -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="editingTxTags?.show" class="modal-overlay" @click.self="editingTxTags.show = false">
          <div class="modal-box modal-sm">
            <h3 class="modal-title">编辑交易标签</h3>
            <div class="tag-editor">
              <div class="tag-list">
                <span v-for="tag in editingTxTags.tags" :key="tag" class="badge badge-info tag-item">
                  {{ tag }}
                  <button class="tag-remove" @click="removeTagFromTx(tag)">✕</button>
                </span>
                <span v-if="!editingTxTags.tags.length" class="text-muted">暂无标签</span>
              </div>
              <div class="tag-input-row">
                <input v-model="editingTxTags.newTag" class="input-field" placeholder="输入标签名" @keyup.enter="addTagToTx" />
                <button class="btn-primary btn-sm" @click="addTagToTx">添加</button>
              </div>
              <div class="tag-presets">
                <span v-for="t in TAG_PRESETS" :key="t"
                  :class="['badge', editingTxTags.tags.includes(t) ? 'badge-success' : 'badge-neutral', 'tag-preset']"
                  @click="editingTxTags.newTag = t">
                  {{ t }}
                </span>
              </div>
              <div class="modal-actions">
                <button class="btn-secondary" @click="editingTxTags.show = false">关闭</button>
              </div>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>

    <!-- Toast -->
    <Transition name="fade">
      <div v-if="toast.show" :class="['toast', `toast-${toast.type}`]">
        {{ toast.message }}
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.portfolio-page {
  animation: fadeIn 0.2s ease;
}

@keyframes fadeIn {
  from { opacity: 0; transform: translateY(4px); }
  to { opacity: 1; transform: translateY(0); }
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 1.5rem;
  gap: 1rem;
}

.page-header .btn-primary {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.6rem 1.2rem;
  white-space: nowrap;
}

.header-actions {
  display: flex;
  gap: 0.5rem;
}

.header-actions .btn-secondary {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.6rem 1.2rem;
  white-space: nowrap;
}

.header-actions .btn-primary {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.6rem 1.2rem;
  white-space: nowrap;
}

.icon-spin {
  transition: transform 0.3s;
}
.icon-spin.spinning {
  animation: spin 1s linear infinite;
}
@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

/* Freshness tags */
.freshness-tag {
  display: inline-block;
  padding: 0.15rem 0.5rem;
  border-radius: 9999px;
  font-size: 0.7rem;
  font-weight: 600;
  white-space: nowrap;
}
.fresh-today {
  background: #dcfce7;
  color: #16a34a;
}
.fresh-yesterday {
  background: #fef9c3;
  color: #a16207;
}
.fresh-stale {
  background: #fee2e2;
  color: #dc2626;
}

/* ── 理财彩蛋 ── */
.quote-bar {
  background: linear-gradient(135deg, #1e3a5f 0%, #2d5a87 100%);
  border-radius: var(--radius-md);
  padding: 0.6rem 1rem;
  margin-bottom: 1rem;
  display: flex;
  align-items: center;
  justify-content: space-between;
  cursor: pointer;
  user-select: none;
  min-height: 40px;
}
.quote-text {
  color: #e8eaed;
  font-size: 0.85rem;
  line-height: 1.5;
}
.quote-author {
  color: #93c5fd;
  font-size: 0.75rem;
  margin-left: 0.3rem;
}
.quote-click-hint {
  color: #93c5fd;
  font-size: 0.68rem;
  opacity: 0.6;
  white-space: nowrap;
  margin-left: 1rem;
}
.quote-fade-enter-active,
.quote-fade-leave-active {
  transition: opacity 0.3s ease, transform 0.3s ease;
}
.quote-fade-enter-from {
  opacity: 0;
  transform: translateY(8px);
}
.quote-fade-leave-to {
  opacity: 0;
  transform: translateY(-8px);
}

/* Summary Cards */
.summary-cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 0.75rem;
  margin-bottom: 1.5rem;
}

.summary-card {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 1rem 1.25rem;
  transition: all var(--transition-fast);
  overflow: hidden;
}
.summary-card-today {
  border-color: #3b82f6;
}
.today-profit-date {
  font-size: 0.6rem;
  font-weight: 400;
  color: var(--color-text-muted);
  margin-left: 2px;
}
.today-profit-sub {
  font-size: 0.68rem;
  color: var(--color-text-muted);
  margin-top: 0.1rem;
}
.summary-card-cost .summary-sub {
  font-size: 0.68rem;
  color: var(--color-text-muted);
  margin-top: 0.1rem;
}
.summary-card-pl .summary-sub {
  font-size: 0.72rem;
  margin-top: 0.1rem;
}

/* Pending transactions banner */
.pending-banner {
  background: #fffbeb;
  border: 1px solid #fde68a;
  border-radius: var(--radius-lg);
  padding: 1rem 1.25rem;
  margin-bottom: 1.5rem;
}

.pending-banner-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  color: #92400e;
  font-size: 0.9rem;
  margin-bottom: 0.75rem;
}

/* 已清仓 */
.closed-holdings-section {
  margin-top: 1rem;
  border-top: 1px solid var(--color-border);
  padding-top: 0.75rem;
}

.closed-holdings-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  cursor: pointer;
  font-size: 0.85rem;
  color: var(--color-text-muted);
  padding: 0.5rem 0;
  user-select: none;
}

.closed-holdings-toggle {
  font-size: 0.8rem;
}

.closed-holdings-table {
  margin-top: 0.5rem;
}

.closed-holdings-table th {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  font-weight: 500;
}

.closed-row {
  opacity: 0.6;
}

.closed-row:hover {
  opacity: 1;
}

/* 零钱卡 */
.summary-card-cash {
  border-color: #f59e0b;
  background: linear-gradient(135deg, #fffbeb 0%, #fef3c7 100%);
}

.cash-value {
  color: #d97706 !important;
}

.cash-interest {
  font-size: 0.72rem;
  color: #16a34a;
  margin-top: 0.15rem;
  font-weight: 600;
}

.cash-accounts {
  display: flex;
  gap: 0.35rem;
  margin-top: 0.35rem;
  flex-wrap: wrap;
}

.cash-account-tag {
  font-size: 0.6rem;
  color: #92400e;
  background: rgba(245, 158, 11, 0.12);
  padding: 0.12rem 0.35rem;
  border-radius: 4px;
  font-weight: 500;
  white-space: nowrap;
  line-height: 1.3;
}

.dark .cash-account-tag {
  color: #fbbf24;
  background: rgba(245, 158, 11, 0.15);
}

.cash-modal-balances {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 1rem;
}

.cash-modal-account {
  flex: 1;
  padding: 0.6rem 0.75rem;
  border: 2px solid var(--color-border);
  border-radius: var(--radius-md);
  cursor: pointer;
  text-align: center;
  transition: all 0.2s ease;
}

.cash-modal-account:hover {
  border-color: #f59e0b;
}

.cash-modal-account.selected {
  border-color: #f59e0b;
  background: rgba(245, 158, 11, 0.08);
}

.cash-modal-uid {
  display: block;
  font-size: 0.75rem;
  color: var(--color-text-muted);
  margin-bottom: 0.25rem;
}

.cash-modal-account strong {
  font-size: 1rem;
  color: #d97706;
}

.pending-hint {
  font-size: 0.75rem;
  color: #a16207;
  font-weight: 400;
}

.pending-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.pending-item {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.5rem 0.75rem;
  background: rgba(255, 255, 255, 0.6);
  border-radius: var(--radius-md);
  font-size: 0.82rem;
}

.pending-fund {
  font-weight: 600;
  color: var(--color-text-primary);
}

.pending-detail {
  color: var(--color-text-secondary);
}

.pending-date {
  color: var(--color-text-muted);
  font-size: 0.75rem;
  margin-left: auto;
}

.dark .pending-banner {
  background: #422006;
  border-color: #92400e;
}

.dark .pending-banner-header {
  color: #fbbf24;
}

.dark .pending-hint {
  color: #d97706;
}

.dark .pending-item {
  background: rgba(0, 0, 0, 0.2);
}

.summary-card:hover {
  box-shadow: var(--shadow-md);
}

.summary-label {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  margin-bottom: 0.375rem;
}

.summary-value {
  font-size: 1.25rem;
  font-weight: 700;
  color: var(--color-text-primary);
}

/* Profit colors */
.profit-positive {
  color: #dc2626 !important;
}

.profit-negative {
  color: #16a34a !important;
}

.cost-value {
  color: #6b7280 !important;
}
.value-value {
  color: #2563eb !important;
}

/* Table */
.holdings-card {
  overflow-x: auto;
}

.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.85rem;
}

.data-table th {
  text-align: left;
  padding: 0.75rem 1rem;
  font-weight: 600;
  color: var(--color-text-secondary);
  border-bottom: 1px solid var(--color-border);
  white-space: nowrap;
  font-size: 0.8rem;
}

.data-table td {
  padding: 0.65rem 1rem;
  border-bottom: 1px solid var(--color-border-light, var(--color-border));
  color: var(--color-text-primary);
  white-space: nowrap;
}

.data-table tbody tr:hover {
  background: var(--color-bg-hover);
}

.text-right {
  text-align: right;
}

.fund-name {
  font-weight: 600;
}
.fund-name-text {
  margin-right: 0.4rem;
}
.badge-category-bond { background: #0891b2; color: white; }
.badge-category-bond_index { background: #0891b2; color: white; }
.badge-category-convertible_bond { background: #7c3aed; color: white; }
.badge-category-money_market { background: #059669; color: white; }
.badge-category-hybrid { background: #d97706; color: white; }
.badge-category-index { background: #c9a84c; color: white; }
.badge-category-equity { background: #dc2626; color: white; }

.actions-cell {
  display: flex;
  gap: 0.25rem;
}

.btn-sm {
  padding: 0.3rem 0.6rem;
  font-size: 0.75rem;
}

.btn-danger-text {
  color: var(--color-danger) !important;
}

.btn-danger-text:hover {
  background: var(--color-danger-bg) !important;
}

.btn-primary-text {
  color: var(--color-primary-600) !important;
}

.btn-primary-text:hover {
  background: var(--color-primary-bg) !important;
}

.btn-sell-text {
  color: #d97706 !important;
}

.btn-sell-text:hover {
  background: var(--color-warning-bg) !important;
}

.btn-info-text {
  color: var(--color-primary-600) !important;
}

.btn-analysis-text {
  color: #7c3aed !important;
}

.btn-analysis-text:hover {
  background: rgba(124, 58, 237, 0.1) !important;
}

.btn-info-text:hover {
  background: var(--color-primary-bg) !important;
}

.text-muted {
  color: var(--color-text-muted);
  font-size: 0.78rem;
}

.preview-note {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  display: block;
  margin-top: 0.25rem;
}

.sell-preview {
  background: var(--color-warning-bg, rgba(245, 158, 11, 0.08));
  color: #d97706;
}

/* Empty & Loading */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.75rem;
  padding: 3rem;
  color: var(--color-text-muted);
}

.loading-state {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.75rem;
  padding: 3rem;
  color: var(--color-text-muted);
}

/* Modal */
.modal-overlay {
  position: fixed;
  inset: 0;
  z-index: var(--z-modal, 100);
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.4);
  backdrop-filter: blur(4px);
}

.modal-box {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-lg);
  width: 100%;
  max-width: 520px;
  max-height: 90vh;
  overflow-y: auto;
  margin: 0 1rem;
  padding: 1.5rem;
}

.modal-wide {
  max-width: 700px;
}

.modal-title {
  font-size: 1.1rem;
  font-weight: 700;
  color: var(--color-text-primary);
  margin: 0 0 0.5rem 0;
}

.modal-subtitle {
  font-size: 0.82rem;
  color: var(--color-text-muted);
  margin: 0 0 1rem 0;
}

.add-purchase-preview {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.6rem 0.8rem;
  background: var(--color-primary-bg, rgba(201, 168, 76, 0.08));
  border-radius: var(--radius-md);
  font-size: 0.9rem;
  color: var(--color-primary-600);
}

.modal-form {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.form-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1rem;
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
}

.form-group label {
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--color-text-secondary);
}

.field-hint {
  font-size: 0.75rem;
  color: var(--color-primary);
  margin-top: 0.25rem;
}

.modal-actions {
  display: flex;
  gap: 0.75rem;
  justify-content: flex-end;
  margin-top: 0.5rem;
}

.modal-actions button {
  padding: 0.6rem 1.25rem;
}

select.input-field {
  cursor: pointer;
}

/* Lookup row */
.lookup-row {
  display: flex;
  gap: 0.5rem;
}
.lookup-row .input-field {
  flex: 1;
}
.lookup-row .btn-sm {
  padding: 0.5rem 1rem;
  white-space: nowrap;
}

.lookup-preview {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
  padding: 0.6rem 0.8rem;
  background: var(--color-bg-hover, #f3f4f6);
  border-radius: var(--radius-md);
  font-size: 0.82rem;
  color: var(--color-text-secondary);
}
.lookup-tag {
  background: var(--color-primary, #3b82f6);
  color: white;
  padding: 0.1rem 0.5rem;
  border-radius: 9999px;
  font-size: 0.7rem;
  font-weight: 600;
}
.lookup-index {
  color: var(--color-text-muted);
  font-size: 0.78rem;
}
.lookup-scale {
  color: var(--color-text-muted);
  font-size: 0.78rem;
}

/* Fund detail modal */
.fund-detail {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}
.detail-section {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: 1rem;
}
.detail-heading {
  font-size: 0.9rem;
  font-weight: 700;
  margin: 0 0 0.75rem 0;
  color: var(--color-text-primary);
}

/* Allocation bars */
.alloc-bars, .industry-bars {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.alloc-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.alloc-label {
  width: 100px;
  font-size: 0.8rem;
  color: var(--color-text-secondary);
  text-align: right;
  flex-shrink: 0;
}
.alloc-bar-bg {
  flex: 1;
  height: 14px;
  background: var(--color-border-light, rgba(255, 255, 255, 0.1));
  border-radius: 7px;
  overflow: hidden;
}
.alloc-bar {
  height: 100%;
  background: var(--color-primary, #3b82f6);
  border-radius: 7px;
  transition: width 0.4s ease;
  min-width: 2px;
}
.alloc-pct {
  width: 50px;
  font-size: 0.78rem;
  color: var(--color-text-muted);
  text-align: right;
  flex-shrink: 0;
}

/* Bond tags */
.bond-summary {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 0.75rem;
}
.bond-tag {
  display: inline-block;
  padding: 0.15rem 0.5rem;
  border-radius: 9999px;
  font-size: 0.7rem;
  font-weight: 600;
}
.bond-利率债 {
  background: #dbeafe;
  color: #1d4ed8;
}
.bond-信用债 {
  background: #fef3c7;
  color: #92400e;
}
.bond-可转债 {
  background: #ede9fe;
  color: #6d28d9;
}

.mini-table {
  font-size: 0.8rem;
}
.mini-table th {
  padding: 0.5rem 0.75rem;
  font-size: 0.75rem;
}
.mini-table td {
  padding: 0.45rem 0.75rem;
}

/* Toast */
.toast {
  position: fixed;
  top: 5rem;
  left: 50%;
  transform: translateX(-50%);
  padding: 0.4rem 0.9rem;
  border-radius: 6px;
  font-size: 0.75rem;
  font-weight: 500;
  z-index: 9999;
  box-shadow: 0 2px 8px rgba(0,0,0,0.1);
  white-space: nowrap;
  animation: toast-pop 0.2s ease;
}
@keyframes toast-pop {
  from { opacity: 0; transform: translateX(-50%) translateY(-6px); }
  to { opacity: 1; transform: translateX(-50%) translateY(0); }
}
.btn-refresh-spinner {
  display: inline-block;
  width: 12px;
  height: 12px;
  border: 2px solid var(--color-border);
  border-top-color: #3b82f6;
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
}

.toast-success {
  background: #f0fdf4;
  color: #16a34a;
  border: 1px solid #bbf7d0;
}

.toast-error {
  background: #fef2f2;
  color: #dc2626;
  border: 1px solid #fecaca;
}

.toast-info {
  background: #f9fafb;
  color: #6b7280;
  border: 1px solid rgba(255, 255, 255, 0.1);
}

/* Responsive */
@media (max-width: 768px) {
  .summary-cards {
    grid-template-columns: repeat(2, 1fr);
  }

  .summary-cards .summary-card:last-child {
    grid-column: span 2;
  }

  .form-row {
    grid-template-columns: 1fr;
  }

  .page-header {
    flex-direction: column;
    align-items: flex-start;
  }
}

/* ── Alert Panel ─── */
.alert-panel {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  margin-bottom: 1rem;
  overflow: hidden;
}
.alert-panel-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.6rem 1rem;
  background: var(--color-bg-hover);
  font-size: 0.85rem;
  border-bottom: 1px solid var(--color-border-light);
}
.alert-list {
  display: flex;
  flex-direction: column;
}
.alert-item {
  display: flex;
  align-items: flex-start;
  gap: 0.75rem;
  padding: 0.65rem 1rem;
  border-bottom: 1px solid var(--color-border-light);
  font-size: 0.82rem;
}
.alert-item:last-child {
  border-bottom: none;
}
.alert-danger {
  border-left: 3px solid #dc2626;
}
.alert-warning {
  border-left: 3px solid #d97706;
}
.alert-info {
  border-left: 3px solid #3b82f6;
}
.alert-icon {
  flex-shrink: 0;
  margin-top: 2px;
}
.alert-danger .alert-icon { color: #dc2626; }
.alert-warning .alert-icon { color: #d97706; }
.alert-info .alert-icon { color: #3b82f6; }
.alert-body {
  flex: 1;
  min-width: 0;
}
.alert-title {
  font-weight: 600;
  color: var(--color-text-primary);
}
.alert-content {
  color: var(--color-text-secondary);
  margin-top: 0.2rem;
  font-size: 0.78rem;
}
.alert-meta {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.3rem;
  font-size: 0.7rem;
  color: var(--color-text-muted);
}
.alert-type-badge {
  background: var(--color-bg-hover);
  padding: 0.1rem 0.4rem;
  border-radius: 4px;
}
.alert-source-badge {
  font-size: 0.7rem;
  padding: 0.1rem 0.4rem;
  border-radius: 4px;
  background: var(--color-bg-hover);
  color: var(--color-text-tertiary);
}
.alert-source-badge.scan {
  background: rgba(99,102,241,0.1);
  color: #6366f1;
}
.alert-source-badge.ai {
  background: rgba(201,168,76,0.1);
  color: #c9a84c;
}
.alert-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.25rem;
  padding: 1.5rem;
  color: var(--color-text-tertiary);
  font-size: 0.85rem;
}
.alert-empty-hint {
  font-size: 0.75rem;
  opacity: 0.6;
}
.alert-actions {
  display: flex;
  gap: 0.25rem;
  flex-shrink: 0;
}

.alert-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: 9px;
  font-size: 0.7rem;
  font-weight: 700;
  background: var(--color-bg-hover);
  color: var(--color-text-secondary);
}
.alert-badge.has-unread {
  background: #dc2626;
  color: #fff;
}
.alert-toggle-icon {
  font-size: 0.6rem;
  color: var(--color-text-muted);
}

/* ── Performance Grid ─── */
.perf-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 0.75rem;
}
.perf-item {
  background: var(--color-bg-hover);
  border-radius: var(--radius-sm);
  padding: 0.6rem 0.8rem;
}
.perf-label {
  display: block;
  font-size: 0.75rem;
  color: var(--color-text-muted);
  margin-bottom: 0.2rem;
}
.perf-value {
  display: block;
  font-size: 0.9rem;
  font-weight: 600;
  color: var(--color-text-primary);
}

/* ── Analysis Tabs ─── */
.analysis-tabs {
  display: flex;
  gap: 0;
  margin-bottom: 0.75rem;
  border-bottom: 2px solid var(--color-border-light);
}
.analysis-tab {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.55rem 1rem;
  font-size: 0.85rem;
  font-weight: 500;
  color: var(--color-text-secondary);
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  margin-bottom: -2px;
  cursor: pointer;
  transition: all var(--transition-fast);
}
.analysis-tab:hover {
  color: var(--color-text-primary);
  background: var(--color-bg-hover);
}
.analysis-tab.active {
  color: var(--color-primary-600);
  border-bottom-color: var(--color-primary-600);
  font-weight: 600;
}
.analysis-tab svg {
  flex-shrink: 0;
}

/* ── Analysis Panel ─── */
.analysis-panel {
  margin-bottom: 1rem;
  padding: 1rem;
}
.analysis-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 0.75rem;
}
.analysis-panel-header h3 {
  margin: 0;
  font-size: 0.95rem;
  font-weight: 700;
}
.analysis-panel-body {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}
.analysis-stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: 0.75rem;
}
.analysis-stat {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.25rem;
  padding: 0.75rem;
  background: var(--color-bg-hover);
  border-radius: var(--radius-md);
}
.stat-label {
  font-size: 0.75rem;
  color: var(--color-text-muted);
}
.stat-value {
  font-size: 1.1rem;
  font-weight: 700;
}
.text-success { color: #16a34a; }
.text-danger { color: #dc2626; }
.text-warning { color: #d97706; }
.analysis-section h4 {
  font-size: 0.85rem;
  font-weight: 600;
  margin: 0 0 0.5rem 0;
}
.distribution-bars {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.dist-bar-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.8rem;
}
.dist-label {
  width: 80px;
  text-align: right;
  color: var(--color-text-secondary);
  flex-shrink: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}
.dist-bar-track {
  flex: 1;
  height: 12px;
  background: var(--color-border-light);
  border-radius: 6px;
  overflow: hidden;
}
.dist-bar-fill {
  height: 100%;
  background: var(--color-primary);
  border-radius: 6px;
  min-width: 2px;
  transition: width 0.4s ease;
}
.dist-bar-index {
  background: #10b981;
}
.dist-bar-warn {
  background: #f59e0b;
}
.dist-bar-danger {
  background: #ef4444;
}
.dist-value {
  width: 80px;
  text-align: right;
  color: var(--color-text-muted);
  font-size: 0.78rem;
  flex-shrink: 0;
}
.analysis-hint {
  padding: 0.6rem 0.8rem;
  background: var(--color-warning-bg);
  border-radius: var(--radius-md);
  font-size: 0.82rem;
  color: #d97706;
}
.mcp-raw-output {
  font-size: 0.78rem;
  line-height: 1.5;
  color: var(--color-text-secondary);
  background: var(--color-bg-hover);
  padding: 0.6rem 0.8rem;
  border-radius: var(--radius-sm);
  white-space: pre-wrap;
  max-height: 200px;
  overflow-y: auto;
}

/* MCP toggle & data list */
.mcp-toggle {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  cursor: pointer;
  font-size: 0.82rem;
  color: var(--color-text-secondary);
  user-select: none;
}
.mcp-toggle:hover {
  color: var(--color-text-primary);
}
.mcp-toggle-icon {
  font-size: 0.6rem;
  width: 12px;
}
.mcp-status-badges {
  display: flex;
  gap: 0.3rem;
  margin-left: auto;
}
.mcp-data-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  margin-top: 0.5rem;
}
.mcp-data-item {
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-sm);
  overflow: hidden;
}
.mcp-data-header {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.4rem 0.6rem;
  background: var(--color-bg-hover);
  font-size: 0.78rem;
}
.mcp-status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.dot-ok { background: #16a34a; }
.dot-err { background: #dc2626; }

/* Diversification refresh button */
.btn-diver-refresh {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.4rem 0.8rem;
  font-size: 0.78rem;
  font-weight: 600;
  color: #fff;
  background: #2563eb;
  border: none;
  border-radius: var(--radius-sm);
  cursor: pointer;
  white-space: nowrap;
  transition: background 0.15s;
}
.btn-diver-refresh:hover { background: #1d4ed8; }
.btn-diver-refresh:disabled { opacity: 0.6; cursor: not-allowed; }

/* Diversification AI summary */
.diver-ai-header {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.85rem;
  margin-bottom: 0.5rem;
}
.btn-diver-ai {
  padding: 0.25rem 0.7rem;
  font-size: 0.75rem;
  font-weight: 600;
  color: #7c3aed;
  background: rgba(124, 58, 237, 0.1);
  border: 1px solid rgba(124, 58, 237, 0.25);
  border-radius: var(--radius-sm);
  cursor: pointer;
  margin-left: auto;
  transition: all 0.15s;
}
.btn-diver-ai:hover {
  background: rgba(124, 58, 237, 0.2);
}
.diver-ai-loading {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.82rem;
  color: var(--color-text-muted);
  padding: 0.5rem 0;
}
.diver-ai-result {
  font-size: 0.85rem;
  line-height: 1.7;
  color: var(--color-text-primary);
  white-space: pre-wrap;
  background: var(--color-bg-hover);
  padding: 0.75rem;
  border-radius: var(--radius-md);
}
.diver-ai-placeholder {
  font-size: 0.78rem;
  color: var(--color-text-muted);
  font-style: italic;
}
.analysis-transaction-detail {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  padding: 0.75rem;
  background: var(--color-bg-hover);
  border-radius: var(--radius-md);
  font-size: 0.85rem;
}
.detail-row {
  display: flex;
  justify-content: space-between;
}

/* 交易明细表 */
.tx-detail-table-wrap {
  overflow-x: auto;
  font-size: 0.82rem;
}

.tx-detail-table {
  width: 100%;
  border-collapse: collapse;
}

.tx-detail-table th {
  text-align: right;
  padding: 0.4rem 0.5rem;
  font-weight: 500;
  color: var(--color-text-muted);
  border-bottom: 1px solid var(--color-border);
  font-size: 0.75rem;
  white-space: nowrap;
}

.tx-detail-table th:first-child {
  text-align: left;
}

.tx-detail-table td {
  padding: 0.4rem 0.5rem;
  border-bottom: 1px solid var(--color-border);
  white-space: nowrap;
}

.tx-fund {
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
}

.tx-fund-name {
  font-weight: 500;
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tx-fund-code {
  font-size: 0.7rem;
  color: var(--color-text-muted);
}

.tx-type-badge {
  display: inline-block;
  padding: 0.1rem 0.4rem;
  border-radius: 3px;
  font-size: 0.75rem;
  font-weight: 500;
}

.tx-buy {
  background: #dcfce7;
  color: #166534;
}

.tx-sell {
  background: #fef3c7;
  color: #92400e;
}

/* ── AI Analysis ─── */
.ai-analysis-input {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.ai-input {
  width: 100%;
  resize: vertical;
  font-family: inherit;
  line-height: 1.5;
}
.ai-input-actions {
  display: flex;
  gap: 0.5rem;
  align-items: center;
}
.ai-input-actions .btn-primary,
.ai-input-actions .btn-secondary {
  padding: 0.5rem 1rem;
  white-space: nowrap;
}
.ai-analysis-result {
  padding: 1rem;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  font-size: 0.85rem;
  line-height: 1.7;
  white-space: pre-wrap;
  max-height: 600px;
  overflow-y: auto;
}
.ai-result-content {
  color: var(--color-text-primary);
}
.ai-result-footer {
  display: flex;
  gap: 0.5rem;
  justify-content: flex-end;
  margin-top: 1rem;
  padding-top: 0.75rem;
  border-top: 1px solid var(--color-border-light);
}
.ai-mcp-sources {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  flex-wrap: wrap;
  font-size: 0.75rem;
  color: var(--color-text-muted);
}
.mcp-source-label {
  font-weight: 600;
}
.ai-token-badge {
  display: inline-block;
  padding: 0.1rem 0.4rem;
  border-radius: 4px;
  font-size: 0.65rem;
  font-weight: 600;
  background: var(--color-bg-hover);
  color: var(--color-text-muted);
  vertical-align: middle;
}
.analysis-panel-actions {
  display: flex;
  gap: 0.5rem;
  align-items: center;
}

/* AI history */
.ai-history-list {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  overflow: hidden;
}
.ai-history-title {
  font-size: 0.8rem;
  font-weight: 600;
  margin: 0;
  padding: 0.6rem 0.8rem;
  background: var(--color-bg-hover);
  border-bottom: 1px solid var(--color-border-light);
}
.ai-history-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.5rem 0.8rem;
  border-bottom: 1px solid var(--color-border-light);
  font-size: 0.8rem;
}
.ai-history-item:last-child {
  border-bottom: none;
}
.ai-history-current {
  background: var(--color-primary-bg, rgba(201, 168, 76, 0.06));
}
.ai-history-info {
  display: flex;
  gap: 0.75rem;
  align-items: center;
}
.ai-history-summary {
  font-weight: 500;
  color: var(--color-text-primary);
}
.ai-history-time {
  font-size: 0.7rem;
  color: var(--color-text-muted);
}
.ai-history-actions {
  display: flex;
  gap: 0.25rem;
}

/* ── Transaction Tags ─── */
.tx-tags-cell {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  max-width: 200px;
}
.btn-xs {
  padding: 0.15rem 0.4rem;
  font-size: 0.7rem;
}
.tag-editor {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.tag-list {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
  padding: 0.5rem;
  min-height: 2rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}
.tag-item {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
}
.tag-remove {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 0.7rem;
  color: inherit;
  opacity: 0.7;
  padding: 0;
}
.tag-remove:hover {
  opacity: 1;
}
.tag-input-row {
  display: flex;
  gap: 0.5rem;
}
.tag-input-row .input-field {
  flex: 1;
}
.tag-presets {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
}
.tag-preset {
  cursor: pointer;
  font-size: 0.75rem;
}
.tag-preset:hover {
  opacity: 0.8;
}
.modal-sm {
  max-width: 400px;
}

/* ── 饼图 ── */
.pie-chart-row {
  display: flex;
  align-items: center;
  gap: 1rem;
  margin-bottom: 0.75rem;
}
.pie-legend {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  flex: 1;
}
.legend-item {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.78rem;
}
.legend-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
}
.legend-label {
  color: var(--color-text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.legend-pct {
  margin-left: auto;
  font-weight: 600;
  font-size: 0.78rem;
}

/* ── 折叠面板 ── */
.collapsible-section h4 {
  display: flex;
  align-items: center;
  gap: 0.35rem;
}
.collapse-icon {
  font-size: 0.65rem;
  color: var(--color-text-muted);
  width: 12px;
  flex-shrink: 0;
}
.collapse-content {
  margin-top: 0.5rem;
}

/* ── 指数详情 ── */
.index-detail-box {
  margin-top: 0.5rem;
  padding: 0.6rem 0.75rem;
  background: var(--color-bg-hover);
  border-radius: var(--radius-sm);
  border: 1px solid var(--color-border-light);
}
.index-detail-title {
  font-size: 0.78rem;
  font-weight: 600;
  margin-bottom: 0.35rem;
  color: var(--color-text-secondary);
}
.index-detail-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 0.78rem;
  padding: 0.15rem 0;
}
.index-detail-name {
  color: var(--color-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}
.index-detail-pct {
  color: var(--color-text-muted);
  margin-left: 0.5rem;
}

/* ── 表格排序 & 搜索 ── */
.th-sortable {
  cursor: pointer;
  user-select: none;
}
.th-sortable:hover {
  color: var(--color-primary);
}
.table-search-bar {
  display: flex;
  gap: 0.5rem;
  align-items: center;
  margin-bottom: 0.75rem;
}
.table-search-bar .input-field {
  flex: 1;
  max-width: 280px;
}
.account-filter-select {
  max-width: 120px !important;
  flex: none !important;
}

/* ── 批量刷新进度 ── */
.refresh-progress-toast {
  position: fixed;
  top: 4.5rem;
  right: 1.5rem;
  z-index: 9999;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  padding: 0.4rem 0.7rem;
  box-shadow: 0 2px 12px rgba(0,0,0,0.08);
  min-width: 120px;
  backdrop-filter: blur(8px);
  animation: toast-slide-in 0.2s ease;
}
@keyframes toast-slide-in {
  from { opacity: 0; transform: translateX(12px); }
  to { opacity: 1; transform: translateX(0); }
}
.refresh-toast-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  font-size: 0.72rem;
  color: var(--color-text-secondary);
  font-weight: 500;
}
.refresh-toast-track {
  margin-top: 0.3rem;
  height: 3px;
  background: var(--color-border-light, rgba(255, 255, 255, 0.1));
  border-radius: 2px;
  overflow: hidden;
}
.refresh-toast-bar {
  height: 100%;
  background: linear-gradient(90deg, #c9a84c, #3b82f6);
  border-radius: 2px;
  transition: width 0.3s ease;
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}
.pending-confirm-hint {
  font-size: 0.72rem;
  color: var(--color-primary, #a88a3a);
  margin-left: 0.25rem;
}
.tx-confirm-hint {
  font-size: 0.7rem;
  color: var(--color-text-muted);
  margin-top: 0.15rem;
}
.account-badge {
  display: inline-block;
  padding: 0.1rem 0.5rem;
  border-radius: 999px;
  font-size: 0.72rem;
  background: var(--color-primary-bg, rgba(201, 168, 76, 0.06));
  color: var(--color-primary, #a88a3a);
  white-space: nowrap;
}

/* ── AI 4 模式 UI ─── */
.ai-mode-tabs {
  display: flex;
  gap: 0.25rem;
  margin-bottom: 0.75rem;
  border-bottom: 1px solid var(--color-border-light);
  padding-bottom: 0.5rem;
  overflow-x: auto;
}
.ai-mode-tab {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.4rem 0.65rem;
  border: none;
  border-radius: var(--radius-sm);
  font-size: 0.78rem;
  cursor: pointer;
  background: transparent;
  color: var(--color-text-secondary);
  white-space: nowrap;
  transition: all 0.15s;
}
.ai-mode-tab:hover {
  background: var(--color-bg-hover);
  color: var(--color-text-primary);
}
.ai-mode-tab.active {
  background: var(--color-primary-bg, rgba(201, 168, 76, 0.06));
  color: var(--color-primary, #a88a3a);
  font-weight: 600;
}
.ai-mode-icon {
  font-size: 1rem;
}
.ai-mode-content {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.ai-mode-desc {
  font-size: 0.8rem;
  color: var(--color-text-secondary);
  line-height: 1.5;
}
.ai-mode-form {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  align-items: flex-end;
}
.ai-form-label {
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--color-text-secondary);
  white-space: nowrap;
}
.ai-mode-result {
  padding: 0.75rem;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  font-size: 0.85rem;
  line-height: 1.7;
  white-space: pre-wrap;
  max-height: 500px;
  overflow-y: auto;
}
.ai-mode-history {
  margin-top: 0.5rem;
}
.ai-mode-history-title {
  font-size: 0.78rem;
  color: var(--color-text-muted);
  cursor: pointer;
  user-select: none;
}
.ai-mode-history-title:hover {
  color: var(--color-text-primary);
}
.ai-mode-history-list {
  margin-top: 0.35rem;
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-sm);
  overflow: hidden;
}
.ai-mode-history-list .ai-history-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.35rem 0.6rem;
  border-bottom: 1px solid var(--color-border-light);
  font-size: 0.78rem;
}
.ai-mode-history-list .ai-history-item:last-child {
  border-bottom: none;
}
.ai-mode-history-list .ai-history-summary {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-mode-history-list .ai-history-time {
  font-size: 0.7rem;
  color: var(--color-text-muted);
  white-space: nowrap;
}

/* ── AI 反馈按钮 ─── */
.ai-feedback {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-top: 1rem;
  padding-top: 0.75rem;
  border-top: 1px solid var(--color-border);
  font-size: 0.82rem;
  color: var(--color-text-muted);
}
.ai-feedback-done {
  color: var(--color-text-muted);
  font-style: italic;
}
.ai-feedback-label {
  margin-right: 0.25rem;
}
.btn-feedback {
  background: none;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: 0.25rem 0.6rem;
  cursor: pointer;
  font-size: 0.9rem;
  transition: all 0.15s;
  line-height: 1;
}
.btn-feedback:hover {
  background: var(--color-bg-hover);
  border-color: var(--color-primary);
}
.btn-feedback-up:hover {
  background: #dcfce7;
  border-color: #16a34a;
}
.btn-feedback-down:hover {
  background: #fee2e2;
  border-color: #dc2626;
}

/* ── NAV 图表 ─── */
.chart-fund-selector {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.75rem;
}
.chart-fund-selector select {
  padding: 0.4rem 0.6rem;
  font-size: 0.82rem;
  border-radius: var(--radius-sm);
  border: 1px solid var(--color-border);
  background: var(--color-bg-card);
  color: var(--color-text-primary);
}
.nav-chart-wrap {
  position: relative;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: 0.75rem 0.5rem 0.25rem 0.5rem;
}
.nav-chart {
  width: 100%;
  height: auto;
  display: block;
  overflow: visible;
}
.chart-legend {
  display: flex;
  justify-content: center;
  gap: 1.5rem;
  margin-top: 0.4rem;
  font-size: 0.78rem;
  color: var(--color-text-muted);
}
.chart-legend .legend-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 0.3rem;
  vertical-align: middle;
}
.chart-tooltip {
  position: absolute;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: 0.5rem 0.75rem;
  font-size: 0.78rem;
  line-height: 1.6;
  color: var(--color-text-primary);
  box-shadow: 0 4px 12px rgba(0,0,0,0.12);
  pointer-events: none;
  z-index: 20;
  white-space: nowrap;
  min-width: 100px;
}
.tooltip-date {
  font-size: 0.72rem;
  color: var(--color-text-muted);
  margin-bottom: 0.15rem;
}
.tooltip-nav {
  font-size: 0.85rem;
}
.tooltip-tx-type {
  display: inline-block;
  padding: 0.05rem 0.4rem;
  border-radius: 3px;
  font-size: 0.7rem;
  font-weight: 600;
  background: var(--color-primary-bg);
  color: var(--color-primary);
  margin-bottom: 0.2rem;
}

/* ── 基金搜索栏 ── */
.chart-search-bar {
  margin-bottom: 0.75rem;
}
.chart-search-form {
  display: flex;
  gap: 0.5rem;
  align-items: center;
}
.chart-search-input {
  flex: 1;
  max-width: 240px;
  font-size: 0.85rem;
  padding: 0.4rem 0.6rem;
}
.chart-search-error {
  font-size: 0.75rem;
  color: #ef4444;
  margin-top: 0.3rem;
}
.chart-fund-switcher {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
  margin-top: 0.5rem;
}
.fund-chip {
  display: inline-block;
  padding: 0.2rem 0.55rem;
  font-size: 0.7rem;
  background: var(--color-bg-input);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  cursor: pointer;
  color: var(--color-text-secondary);
  transition: all var(--transition-fast);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100px;
}
.fund-chip:hover {
  border-color: var(--color-primary-400);
  color: var(--color-primary);
}
.fund-chip.active {
  background: var(--color-primary-50, var(--color-primary-bg));
  border-color: var(--color-primary-400);
  color: var(--color-primary);
  font-weight: 500;
}

/* ── 5年走势图 ── */
.fund-chart-5y {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}
.chart-5y-canvas {
  position: relative;
}
.chart-5y-canvas svg {
  width: 100%;
  height: auto;
  max-height: 300px;
  cursor: crosshair;
}
.chart-5y-stats {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 0.75rem;
}
.stat-card-stat {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: 0.6rem 0.8rem;
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}
.stat-label-stat {
  font-size: 0.72rem;
  color: var(--color-text-muted);
}
.stat-value-stat {
  font-size: 1rem;
  font-weight: 700;
  font-family: monospace;
}
.stat-up { color: #dc2626; }
.stat-down { color: #16a34a; }

.chart5y-actions {
  display: flex;
  gap: 0.5rem;
  justify-content: flex-end;
}

/* ── 表头筛选按钮 ── */
.th-filter {
  display: inline-flex;
  align-items: center;
  margin-left: 0.25rem;
  cursor: pointer;
  color: var(--color-text-muted);
  opacity: 0.5;
  transition: all 0.15s;
  vertical-align: middle;
}

.th-filter:hover {
  opacity: 1;
  color: var(--color-primary);
}

/* ── 今日涨跌/收益单元格 ── */
.today-change-cell {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 0.1rem;
}

.today-change-pct {
  font-weight: 600;
  font-size: 0.85rem;
}

.today-change-profit {
  font-size: 0.72rem;
  opacity: 0.8;
}

/* ── AI Action Button (与 Dashboard 一致) ── */
.btn-ai-action {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.45rem 0.85rem;
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--color-primary);
  background: linear-gradient(135deg, var(--color-primary-bg), rgba(201, 168, 76, 0.08));
  border: 1px solid rgba(201, 168, 76, 0.2);
  border-radius: var(--radius-lg);
  cursor: pointer;
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
  white-space: nowrap;
}

.btn-ai-action::before {
  content: '';
  position: absolute;
  top: 0;
  left: -100%;
  width: 100%;
  height: 100%;
  background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.4), transparent);
  transition: left 0.5s ease;
}

.btn-ai-action:hover {
  background: linear-gradient(135deg, rgba(201, 168, 76, 0.15), rgba(201, 168, 76, 0.12));
  border-color: rgba(201, 168, 76, 0.4);
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(201, 168, 76, 0.15);
}

.btn-ai-action:hover::before {
  left: 100%;
}

.btn-ai-action:active {
  transform: translateY(0);
  box-shadow: none;
}

.btn-ai-action:disabled {
  opacity: 0.6;
  cursor: not-allowed;
  transform: none;
  box-shadow: none;
}

.btn-ai-action.btn-loading {
  background: linear-gradient(135deg, rgba(201, 168, 76, 0.08), rgba(201, 168, 76, 0.05));
  border-color: rgba(201, 168, 76, 0.15);
}

.btn-ai-action.btn-loading::after {
  content: '';
  position: absolute;
  bottom: 0;
  left: 0;
  height: 2px;
  width: 100%;
  background: linear-gradient(90deg, transparent, var(--color-primary), transparent);
  animation: loading-bar 1.5s ease-in-out infinite;
}

@keyframes loading-bar {
  0% { transform: translateX(-100%); }
  50% { transform: translateX(0); }
  100% { transform: translateX(100%); }
}

/* ── AI Agent Tooltip ── */
.ai-agent-tooltip {
  position: absolute;
  top: calc(100% + 8px);
  left: 50%;
  transform: translateX(-50%) scale(0.95);
  padding: 0.4rem 0.7rem;
  font-size: 0.7rem;
  font-weight: 600;
  color: white;
  background: linear-gradient(135deg, #1e1b4b, #312e81);
  border-radius: var(--radius-md);
  white-space: nowrap;
  opacity: 0;
  visibility: hidden;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  pointer-events: none;
  z-index: 100;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
}

.ai-agent-tooltip::after {
  content: '';
  position: absolute;
  bottom: 100%;
  left: 50%;
  transform: translateX(-50%);
  border: 5px solid transparent;
  border-bottom-color: #1e1b4b;
}

.btn-ai-action:hover .ai-agent-tooltip {
  opacity: 1;
  visibility: visible;
  transform: translateX(-50%) scale(1);
}

.icon-spin {
  transition: transform 0.3s ease;
}

.icon-spin.spinning {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

/* ── 交易复盘结果（嵌入 tx tab） ── */
.tx-ai-review {
  display: flex;
  justify-content: center;
  padding: 0.75rem 0;
}

.trade-review-result-inline {
  margin-top: 0.75rem;
  padding: 0.75rem;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  font-size: 0.85rem;
  line-height: 1.7;
}

.trade-review-result-inline .result-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 0.5rem;
  padding-bottom: 0.5rem;
  border-bottom: 1px solid var(--color-border-light, var(--color-border));
}

.trade-review-result-inline .result-title {
  font-weight: 600;
  font-size: 0.9rem;
}

.trade-review-result-inline .result-time {
  font-size: 0.72rem;
  color: var(--color-text-muted);
}

.trade-review-result-inline .result-body {
  max-height: 400px;
  overflow-y: auto;
  white-space: pre-wrap;
}

.trade-review-result-inline .result-feedback {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.5rem;
  padding-top: 0.5rem;
  border-top: 1px solid var(--color-border-light, var(--color-border));
}

.btn-feedback {
  background: none;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  padding: 0.25rem 0.5rem;
  cursor: pointer;
  font-size: 1rem;
  transition: all 0.2s;
}

.btn-feedback:hover {
  background: var(--color-bg-hover);
  transform: scale(1.1);
}

/* ── 费率侵蚀计算器 ── */
.fee-calc-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.75rem 1.25rem;
  margin-bottom: 1rem;
}
.fee-slider-group {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}
.fee-slider-group label {
  font-size: 0.82rem;
  font-weight: 600;
  color: var(--color-text-secondary);
  display: flex;
  justify-content: space-between;
}
.fee-val {
  color: var(--color-primary);
  font-weight: 700;
}
.fee-slider {
  -webkit-appearance: none;
  appearance: none;
  width: 100%;
  height: 6px;
  border-radius: 3px;
  background: linear-gradient(90deg, var(--color-primary-bg), var(--color-border));
  outline: none;
  cursor: pointer;
}
.fee-slider::-webkit-slider-thumb {
  -webkit-appearance: none;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: var(--color-primary);
  box-shadow: 0 2px 6px rgba(201, 168, 76, 0.3);
  cursor: pointer;
  transition: transform 0.15s;
}
.fee-slider::-webkit-slider-thumb:hover {
  transform: scale(1.15);
}
.fee-result-cards {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.75rem;
  margin-bottom: 1rem;
}
.fee-result-card {
  padding: 0.85rem 1rem;
  border-radius: 10px;
  text-align: center;
}
.fee-result-erosion {
  background: linear-gradient(135deg, #fef2f2, #fff5f5);
  border: 1px solid #fecaca;
}
.fee-result-savings {
  background: linear-gradient(135deg, #f0fdf4, #f0fff4);
  border: 1px solid #bbf7d0;
}
.fee-result-label {
  font-size: 0.78rem;
  color: var(--color-text-muted);
  margin-bottom: 0.3rem;
}
.fee-result-value {
  font-size: 1.3rem;
  font-weight: 800;
  font-variant-numeric: tabular-nums;
  color: #dc2626;
}
.fee-result-savings .fee-result-value {
  color: #059669;
}
.fee-result-sub {
  font-size: 0.72rem;
  color: var(--color-text-muted);
  margin-top: 0.2rem;
}
.fee-chart-container {
  background: #fafbfc;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  padding: 0.75rem;
  margin-bottom: 0.5rem;
}
.fee-svg {
  width: 100%;
  height: auto;
  display: block;
}
.fee-chart-x-labels {
  display: flex;
  justify-content: space-between;
  font-size: 0.72rem;
  color: var(--color-text-muted);
  padding: 0.25rem 0 0;
}
.fee-disclaimer {
  font-size: 0.72rem;
  color: var(--color-text-muted);
  font-style: italic;
  margin: 0;
  line-height: 1.4;
}

/* ── 重叠度热力图 ── */
.overlap-heatmap {
  display: inline-block;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  overflow: hidden;
  font-size: 0.72rem;
}
.overlap-row {
  display: flex;
}
.overlap-cell {
  min-width: 36px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid rgba(0,0,0,0.06);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.overlap-corner {
  min-width: 60px;
  background: #f8fafc;
}
.overlap-label {
  min-width: 60px;
  padding: 0 6px;
  background: #f8fafc;
  font-weight: 500;
  color: var(--color-text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 0.68rem;
}
.overlap-header .overlap-label {
  writing-mode: vertical-lr;
  text-orientation: mixed;
  height: 60px;
  min-height: 60px;
}

/* ── 调仓策略配置 ── */
.config-section {
  margin-bottom: 1.25rem;
  padding-bottom: 1rem;
  border-bottom: 1px solid var(--color-border);
}
.config-section:last-child { border-bottom: none; margin-bottom: 0; padding-bottom: 0; }
.config-section-title {
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--color-text);
  margin-bottom: 0.4rem;
}
.config-hint {
  font-size: 0.75rem;
  color: var(--color-text-secondary);
  margin-bottom: 0.5rem;
}
.config-current-strategy {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}
.strategy-badge {
  display: inline-block;
  padding: 0.2rem 0.75rem;
  background: var(--color-primary);
  color: #fff;
  border-radius: 999px;
  font-size: 0.8rem;
  font-weight: 600;
  width: fit-content;
}
.strategy-desc {
  font-size: 0.8rem;
  color: var(--color-text-secondary);
}
.strategy-source {
  font-size: 0.72rem;
  color: var(--color-text-tertiary);
  font-style: italic;
}
.strategy-presets-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 0.6rem;
}
.strategy-preset-card {
  padding: 0.6rem 0.75rem;
  border: 2px solid var(--color-border);
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.15s;
}
.strategy-preset-card:hover { border-color: var(--color-primary-light); }
.strategy-preset-card.active { border-color: var(--color-primary); background: var(--color-primary-bg); }
.preset-name { font-size: 0.82rem; font-weight: 600; color: var(--color-text); }
.preset-desc { font-size: 0.72rem; color: var(--color-text-secondary); margin-top: 0.15rem; }
.preset-alloc { display: flex; flex-wrap: wrap; gap: 0.25rem; margin-top: 0.3rem; }
.alloc-tag {
  font-size: 0.68rem;
  padding: 0.1rem 0.35rem;
  background: var(--color-bg-secondary);
  border-radius: 4px;
  color: var(--color-text-secondary);
}
.preset-source { font-size: 0.65rem; color: var(--color-text-tertiary); margin-top: 0.2rem; font-style: italic; }
.config-alloc-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 0.4rem;
}
.config-alloc-item {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.3rem 0.5rem;
  background: var(--color-bg-secondary);
  border-radius: 6px;
}
.config-alloc-label { font-size: 0.78rem; color: var(--color-text-secondary); min-width: 3rem; }
.config-alloc-value { font-size: 0.82rem; font-weight: 600; color: var(--color-text); }
.config-kv-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 0.4rem;
}
.config-kv-item {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.3rem 0.5rem;
  background: var(--color-bg-secondary);
  border-radius: 6px;
}
.config-kv-key { font-size: 0.78rem; color: var(--color-text-secondary); min-width: 4rem; }
.config-kv-val { font-size: 0.82rem; font-weight: 600; color: var(--color-text); }
.config-input {
  width: 60px;
  padding: 0.15rem 0.3rem;
  border: 1px solid var(--color-border);
  border-radius: 4px;
  font-size: 0.78rem;
  background: var(--color-bg);
  color: var(--color-text);
  text-align: right;
}
.config-input:focus { outline: none; border-color: var(--color-primary); }
.config-input-unit { font-size: 0.72rem; color: var(--color-text-secondary); }
.arrow-icon { transition: transform 0.2s; }
.arrow-icon.rotated { transform: rotate(180deg); }
.config-history-list { display: flex; flex-direction: column; gap: 0.35rem; }
.config-history-item {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.4rem 0.6rem;
  background: var(--color-bg-secondary);
  border-radius: 6px;
  border-left: 3px solid transparent;
}
.config-history-item.active { border-left-color: var(--color-primary); background: var(--color-primary-bg); }
.history-meta { display: flex; align-items: center; gap: 0.4rem; flex: 1; min-width: 0; }
.history-id { font-size: 0.72rem; color: var(--color-text-tertiary); font-family: monospace; }
.history-strategy { font-size: 0.78rem; font-weight: 600; color: var(--color-text); }
.history-active-badge {
  font-size: 0.65rem;
  padding: 0.05rem 0.4rem;
  background: var(--color-primary);
  color: #fff;
  border-radius: 999px;
}
.history-note { font-size: 0.72rem; color: var(--color-text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.history-time { font-size: 0.7rem; color: var(--color-text-tertiary); white-space: nowrap; }
.btn-xs { font-size: 0.7rem; padding: 0.15rem 0.4rem; }
</style>
