<script setup>
import { ref, watch, onMounted, onUnmounted, computed } from 'vue'
import * as echarts from 'echarts'
import { listValuationIndexes, getValuationHistory, getIndexInfo, runAnalysis, listAnalysisHistory, getAnalysisHistoryDetail, deleteAnalysisHistory, refreshValuationPrices, listDDValuations, getDDValuation, getMarketTemperature, getSuperValue, getEnhancedStrategy } from '../api'
import { renderMarkdown } from '../composables/useMarkdown'
import { isDark } from '../composables/useTheme'
import { useToast } from '../composables/useToast'
import ConfirmDialog from './ConfirmDialog.vue'
import AppToast from './AppToast.vue'
import EmptyState from './ui/EmptyState.vue'

const { showToast } = useToast()

const confirm = ref({ visible: false, title: '', message: '', danger: false, onConfirm: null })
const indexes = ref([])
const selectedCode = ref('')   // 当前选中的 index_code
const selectedMetric = ref('') // 当前选中的 metric_type
const history = ref([])
const latest = ref(null)
const loading = ref(false)
const trendChartRef = ref(null)
let trendChart = null

// ── 外层 Tab ──────────────────────────────────────
const outerTab = ref('index') // 'index' | 'dd-image'

// ── AI 分析相关 ──────────────────────────────────────
const activeTab = ref('valuation') // 'valuation' | 'analysis' | 'dd'

// ── 市场温度相关 ──────────────────────────────────────
const marketTemperature = ref(null)

// ── 螺丝钉估值相关 ──────────────────────────────────────
const ddRecords = ref([])
const ddLoading = ref(false)
const ddDetailLoading = ref(false)
const ddSelectedRecordId = ref(null)
const ddSelectedRecord = ref(null)
const ddIndexList = ref([])
const ddSearchQuery = ref('')
const ddSortKey = ref('')
const ddSortAsc = ref(true)
// 分析状态：按指数 code 独立存储，支持并发分析多个指数
const analysisLoadingMap = ref({})   // { [indexCode]: boolean }
const analysisResultMap = ref({})    // { [indexCode]: {id, result, agent_name, token_usage, created_at} }
const analysisHistory = ref([])

// 超性价比
const superValueLoading = ref(false)
const superValueData = ref(null)
const strategyLoading = ref(false)
const strategyData = ref(null)
const breakdownLabels = {
  valuation: '估值水位',
  consecutive: '连续下跌',
  drop_7d: '近期跌幅',
  zscore: 'Z-score',
  acceleration: '趋势',
}

// 当前选中指数的 loading/result（computed 代理）
const analysisLoading = computed(() => !!analysisLoadingMap.value[selectedCode.value])
const analysisResult = computed(() => analysisResultMap.value[selectedCode.value] || null)
const runningCount = computed(() => Object.values(analysisLoadingMap.value).filter(Boolean).length)
const refreshingPrices = ref(false)
const historyLoading = ref(false)
const viewingHistory = ref(null) // 正在查看的历史详情
const showConfirmRun = ref(false)

// 搜索相关
const searchQuery = ref('')
const dropdownOpen = ref(false)

// 指数信息 popover
const indexInfoText = ref('')
const indexInfoLoading = ref(false)
const showIndexInfo = ref(false)
const indexInfoCache = {}

// 金融术语解释
const metricGlossary = {
  '市盈率': '市盈率（PE）= 股价 ÷ 每股收益。表示投资者为每1元收益愿意支付的价格。PE越低通常表示越"便宜"，但不同行业的合理PE差异很大。',
  '市净率': '市净率（PB）= 股价 ÷ 每股净资产。表示股价相对于公司净资产的倍数。PB<1称为"破净"，常用于银行、地产等重资产行业估值。',
  '百分位': '百分位表示当前估值在历史数据中所处的位置。例如30%意味着历史上有30%的时间估值比现在低，70%的时间比现在高。通常<30%为低估，>70%为高估。',
  'Z分数': 'Z分数（Z-score）= (当前值 - 平均值) ÷ 标准差。衡量当前估值偏离均值的程度。Z>2表示显著偏高，Z<-2表示显著偏低，绝对值越大越异常。',
  '分位点': '分位点与百分位含义相同，表示当前估值在历史序列中的相对位置。',
  '机会值': '机会值通常是历史估值的较低分位（如20%分位），代表一个相对"便宜"的买入参考价位。',
  '危险值': '危险值通常是历史估值的较高分位（如80%分位），代表估值偏高的警示价位。',
  '中位数': '中位数是历史估值数据的中间值，有一半时间估值高于它，一半时间低于它，是衡量"正常"水平的参考。',
}

// 当前指数拥有的指标类型列表
const availableMetrics = computed(() => {
  if (!selectedCode.value) return []
  return indexes.value
    .filter(idx => idx.index_code === selectedCode.value)
    .map(idx => idx.metric_type || '未知')
})

// 搜索过滤后的下拉选项（去重：每个 index_code 只显示一次）
const filteredIndexes = computed(() => {
  const seen = new Set()
  const unique = []
  for (const idx of indexes.value) {
    if (!seen.has(idx.index_code)) {
      seen.add(idx.index_code)
      unique.push(idx)
    }
  }
  if (!searchQuery.value) return unique
  const q = searchQuery.value.toLowerCase()
  return unique.filter(idx =>
    (idx.index_name || '').toLowerCase().includes(q) ||
    idx.index_code.includes(q)
  )
})

const selectedIndexInfo = computed(() => {
  if (!selectedCode.value) return null
  return indexes.value.find(idx =>
    idx.index_code === selectedCode.value && (idx.metric_type || '未知') === selectedMetric.value
  ) || null
})

// DD image tab computed
const ddFilteredList = computed(() => {
  let list = ddIndexList.value
  if (ddSearchQuery.value) {
    const q = ddSearchQuery.value.toLowerCase()
    list = list.filter(item =>
      (item.index_name || '').toLowerCase().includes(q) ||
      (item.index_code || '').toLowerCase().includes(q)
    )
  }
  if (ddSortKey.value) {
    const key = ddSortKey.value
    const asc = ddSortAsc.value ? 1 : -1
    list = [...list].sort((a, b) => {
      const va = a[key] ?? ''
      const vb = b[key] ?? ''
      if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * asc
      return String(va).localeCompare(String(vb)) * asc
    })
  }
  return list
})

async function loadIndexes() {
  try {
    const { data } = await listValuationIndexes()
    indexes.value = data.indexes || []
    if (indexes.value.length > 0 && !selectedCode.value) {
      selectedCode.value = indexes.value[0].index_code
    }
    // 自动选中第一个指标类型
    if (selectedCode.value && !selectedMetric.value) {
      const metrics = indexes.value
        .filter(idx => idx.index_code === selectedCode.value)
        .map(idx => idx.metric_type || '未知')
      if (metrics.length > 0) selectedMetric.value = metrics[0]
    }
  } catch (e) {
    console.error('Failed to load indexes:', e)
  }
}

async function loadMarketTemperature() {
  try {
    const { data } = await getMarketTemperature()
    marketTemperature.value = data
  } catch (e) {
    console.error('Failed to load market temperature:', e)
  }
}

function onSearchFocus() {
  dropdownOpen.value = true
  // 如果有已选指数，清空搜索框方便输入
  if (selectedCode.value) {
    searchQuery.value = ''
  }
}

function selectIndex(code) {
  selectedCode.value = code
  // 自动选中该指数的第一个指标类型
  const metrics = indexes.value
    .filter(idx => idx.index_code === code)
    .map(idx => idx.metric_type || '未知')
  selectedMetric.value = metrics.length > 0 ? metrics[0] : ''
  dropdownOpen.value = false
  searchQuery.value = ''
  // 失焦搜索框
  document.querySelector('.select-search-input')?.blur()
  // 立即加载该指数的估值历史和分析历史
  if (selectedMetric.value) loadHistory()
  if (activeTab.value === 'analysis') loadAnalysisHistory()
}

function selectMetric(metric) {
  selectedMetric.value = metric
}

async function loadIndexInfo() {
  if (!selectedCode.value) return
  const code = selectedCode.value
  if (indexInfoCache[code]) {
    indexInfoText.value = indexInfoCache[code]
    return
  }
  // 清空旧数据，避免切换指数时残留上一个的信息
  indexInfoText.value = ''
  indexInfoLoading.value = true
  try {
    const { data } = await getIndexInfo(code, selectedIndexName.value)
    // 仅当 code 没变时才写入（防止快速切换时竞态覆盖）
    if (selectedCode.value === code) {
      const info = data.info || ''
      indexInfoCache[code] = info
      indexInfoText.value = info || '暂无该指数的介绍信息'
    }
  } catch {
    if (selectedCode.value === code) {
      indexInfoText.value = '获取指数信息失败'
    }
  } finally {
    if (selectedCode.value === code) {
      indexInfoLoading.value = false
    }
  }
}

function onIndexNameEnter() {
  showIndexInfo.value = true
  if (!indexInfoCache[selectedCode.value]) loadIndexInfo()
}

function onIndexNameLeave() {
  showIndexInfo.value = false
}

// 表格内 tooltip（用 fixed 定位，不受 overflow 裁剪）
const tableTip = ref({ show: false, text: '', x: 0, y: 0 })

function onTableTipEnter(e, text) {
  const rect = e.currentTarget.getBoundingClientRect()
  tableTip.value = {
    show: true,
    text,
    x: rect.left + rect.width / 2,
    y: rect.bottom + 8,
  }
}

function onTableTipLeave() {
  tableTip.value.show = false
}

const selectedIndexName = computed(() => {
  if (!selectedCode.value) return ''
  const idx = indexes.value.find(i => i.index_code === selectedCode.value)
  return idx?.index_name || selectedCode.value
})

async function handleRunAnalysis() {
  if (!selectedCode.value) return
  const code = selectedCode.value
  const name = selectedIndexName.value
  showConfirmRun.value = false
  // 设置当前指数为 loading
  analysisLoadingMap.value = { ...analysisLoadingMap.value, [code]: true }
  // 清空当前指数的旧结果
  const newResultMap = { ...analysisResultMap.value }
  delete newResultMap[code]
  analysisResultMap.value = newResultMap
  try {
    const { data } = await runAnalysis(code, name)
    // 写入该指数的结果（即使用户已切换到其他指数，结果也会保留）
    analysisResultMap.value = {
      ...analysisResultMap.value,
      [code]: {
        id: data.id,
        result: data.result,
        agent_name: '指数深度分析师',
        token_usage: data.token_usage || 0,
        created_at: new Date().toISOString(),
      }
    }
    // 刷新历史列表
    loadAnalysisHistory()
  } catch (e) {
    console.error('Analysis failed:', e)
    analysisResultMap.value = {
      ...analysisResultMap.value,
      [code]: { result: '分析失败：' + (e.response?.data?.detail || e.message), error: true }
    }
  } finally {
    analysisLoadingMap.value = { ...analysisLoadingMap.value, [code]: false }
  }
}

async function handleRefreshPrices() {
  refreshingPrices.value = true
  try {
    const { data } = await refreshValuationPrices()
    showToast(`行情价格已刷新，更新了 ${data.updated} 只指数`, 'success')
    if (selectedCode.value) loadHistory()
  } catch (e) {
    showToast('价格刷新失败：' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    refreshingPrices.value = false
  }
}

async function loadAnalysisHistory() {
  if (!selectedCode.value) return
  historyLoading.value = true
  try {
    const { data } = await listAnalysisHistory(selectedCode.value)
    analysisHistory.value = data.history || data.items || []
  } catch (e) {
    console.error('Failed to load analysis history:', e)
  } finally {
    historyLoading.value = false
  }
}

async function viewHistoryItem(item) {
  try {
    const { data } = await getAnalysisHistoryDetail(item.id)
    viewingHistory.value = data
  } catch (e) {
    console.error('Failed to load detail:', e)
  }
}

function closeHistoryDetail() {
  viewingHistory.value = null
}

async function handleDeleteHistory(id) {
  confirm.value = {
    visible: true,
    title: '删除确认',
    message: '确定删除这条分析记录？',
    danger: true,
    onConfirm: async () => {
      confirm.value.visible = false
      try {
        await deleteAnalysisHistory(id)
        analysisHistory.value = analysisHistory.value.filter(h => h.id !== id)
        if (viewingHistory.value?.id === id) viewingHistory.value = null
      } catch (e) {
        console.error('Failed to delete:', e)
      }
    }
  }
}

function formatAnalysisTime(ts) {
  if (!ts) return ''
  const d = new Date(ts.replace(' ', 'T'))
  const M = d.getMonth() + 1
  const D = d.getDate()
  const h = String(d.getHours()).padStart(2, '0')
  const m = String(d.getMinutes()).padStart(2, '0')
  return `${M}/${D} ${h}:${m}`
}

async function loadDDRecords() {
  ddLoading.value = true
  try {
    const { data } = await listDDValuations()
    ddRecords.value = data.records || []
    // 自动选中最新记录
    if (ddRecords.value.length && !ddSelectedRecordId.value) {
      ddSelectedRecordId.value = ddRecords.value[0].id
      await loadDDIndexList(ddRecords.value[0].id)
    }
  } catch (e) {
    console.error('Failed to load DD valuations:', e)
  } finally {
    ddLoading.value = false
  }
}

async function loadSuperValue() {
  if (superValueData.value) return  // 已加载过，不重复请求
  superValueLoading.value = true
  try {
    const { data } = await getSuperValue()
    superValueData.value = data
  } catch (e) {
    console.error('Failed to load super value:', e)
  } finally {
    superValueLoading.value = false
  }
}

async function loadStrategy() {
  if (strategyData.value) return
  strategyLoading.value = true
  try {
    const { data } = await getEnhancedStrategy()
    strategyData.value = data
  } catch (e) {
    console.error('Failed to load enhanced strategy:', e)
  } finally {
    strategyLoading.value = false
  }
}

async function loadDDIndexList(recordId) {
  ddDetailLoading.value = true
  try {
    const { data } = await getDDValuation(recordId)
    ddSelectedRecord.value = data
    ddIndexList.value = data?.parsed_data?.data || []
  } catch (e) {
    console.error('Failed to load DD detail:', e)
    ddIndexList.value = []
  } finally {
    ddDetailLoading.value = false
  }
}

function onDDDateChange() {
  ddSearchQuery.value = ''
  ddSortKey.value = ''
  loadDDIndexList(ddSelectedRecordId.value)
}

function ddSortBy(key) {
  if (ddSortKey.value === key) {
    ddSortAsc.value = !ddSortAsc.value
  } else {
    ddSortKey.value = key
    ddSortAsc.value = true
  }
}

function ddPercentileClass(pct) {
  if (pct < 20) return 'badge-success'
  if (pct < 30) return 'badge-success-light'
  if (pct <= 70) return 'badge-warning'
  if (pct <= 80) return 'badge-danger-light'
  return 'badge-danger'
}

function ddStatusClass(status) {
  if (!status) return ''
  if (status === '低估') return 'val-low'
  if (status === '高估') return 'val-high'
  return ''
}

async function loadHistory() {
  if (!selectedCode.value || !selectedMetric.value) return
  loading.value = true
  try {
    const { data } = await getValuationHistory(selectedCode.value, 60, selectedMetric.value)
    history.value = data.history || []
    latest.value = data.latest || null
    if (history.value.length > 1) {
      setTimeout(renderTrendChart, 100)
    }
  } catch (e) {
    console.error('Failed to load history:', e)
  } finally {
    loading.value = false
  }
}

// 点击外部关闭下拉
function handleOutsideClick(e) {
  if (!e.target.closest('.searchable-select')) {
    dropdownOpen.value = false
    searchQuery.value = ''
  }
}

function renderTrendChart() {
  if (!trendChartRef.value || history.value.length < 2) return

  if (trendChart) trendChart.dispose()
  trendChart = echarts.init(trendChartRef.value, isDark.value ? 'dark' : null)

  const sorted = [...history.value].reverse()
  const dates = sorted.map(r => r.snapshot_date)
  const valueData = sorted.map(r => r.current_value || null)
  const percentileData = sorted.map(r => r.percentile || null)

  const gridColor = isDark.value ? 'rgba(255, 255, 255, 0.06)' : '#f1f5f9'
  const textColor = isDark.value ? '#9aa0a6' : '#64748b'
  const metricName = latest.value?.metric_type || '估值'

  trendChart.setOption({
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      backgroundColor: isDark.value ? 'rgba(13, 18, 32, 0.95)' : '#ffffff',
      borderColor: isDark.value ? 'rgba(255, 255, 255, 0.1)' : '#e2e8f0',
      textStyle: { color: isDark.value ? '#e8eaed' : '#0f172a', fontSize: 12 },
      formatter: function(params) {
        let html = `<div style="font-weight:600;margin-bottom:4px">${params[0].axisValue}</div>`
        params.forEach(p => {
          html += `<div style="display:flex;align-items:center;gap:6px;font-size:12px">
            <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${p.color}"></span>
            ${p.seriesName}: <b>${p.value ?? '-'}</b>
          </div>`
        })
        return html
      }
    },
    legend: {
      data: [metricName, '分位点(%)'],
      bottom: 0,
      textStyle: { color: textColor, fontSize: 11 },
    },
    grid: { left: '10%', right: '10%', top: '8%', bottom: '18%' },
    xAxis: {
      type: 'category',
      data: dates,
      axisLabel: { color: textColor, fontSize: 10, rotate: dates.length > 15 ? 30 : 0 },
      axisLine: { lineStyle: { color: gridColor } },
    },
    yAxis: [
      {
        type: 'value',
        name: metricName,
        nameTextStyle: { color: textColor, fontSize: 10 },
        splitLine: { lineStyle: { color: gridColor } },
        axisLabel: { color: textColor, fontSize: 10 },
      },
      {
        type: 'value',
        name: '%',
        nameTextStyle: { color: textColor, fontSize: 10 },
        splitLine: { show: false },
        axisLabel: { color: textColor, fontSize: 10 },
        max: 100,
      },
    ],
    series: [
      {
        name: metricName,
        type: 'line',
        data: valueData,
        smooth: true,
        lineStyle: { width: 2, color: '#c9a84c' },
        itemStyle: { color: '#c9a84c' },
        symbol: 'circle',
        symbolSize: 5,
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(99,102,241,0.2)' },
            { offset: 1, color: 'rgba(99,102,241,0)' },
          ]),
        },
      },
      {
        name: '分位点(%)',
        type: 'line',
        yAxisIndex: 1,
        data: percentileData,
        smooth: true,
        lineStyle: { width: 2, color: '#f59e0b', type: 'dashed' },
        itemStyle: { color: '#f59e0b' },
        symbol: 'circle',
        symbolSize: 5,
        markArea: {
          silent: true,
          data: [
            [
              { yAxis: 0, itemStyle: { color: 'rgba(16,185,129,0.08)' } },
              { yAxis: 30 },
            ],
            [
              { yAxis: 30, itemStyle: { color: 'rgba(16,185,129,0.04)' } },
              { yAxis: 50 },
            ],
            [
              { yAxis: 50, itemStyle: { color: 'transparent' } },
              { yAxis: 70 },
            ],
            [
              { yAxis: 70, itemStyle: { color: 'rgba(245,158,11,0.04)' } },
              { yAxis: 85 },
            ],
            [
              { yAxis: 85, itemStyle: { color: 'rgba(239,68,68,0.06)' } },
              { yAxis: 100 },
            ],
          ],
        },
      },
    ],
  })
}

function getPercentileLevel(p) {
  if (p == null) return 'neutral'
  if (p < 30) return 'success'
  if (p <= 70) return 'warning'
  return 'danger'
}

// 来源图片
const showLightbox = ref(false)
const imageLoadError = ref(false)

const sourceImageUrl = computed(() => {
  const src = latest.value?.source_image
  if (!src) return null
  // 绝对路径如 /Users/.../data/images/2024-01-01/title/img_000.png
  // 转换为 /static/images/2024-01-01/title/img_000.png
  const idx = src.indexOf('data/images/')
  if (idx !== -1) {
    return '/static/images/' + src.substring(idx + 'data/images/'.length)
  }
  // 兜底：尝试直接用路径末段
  const parts = src.split('/')
  const dateIdx = parts.findIndex(p => /^\d{4}-\d{2}-\d{2}$/.test(p))
  if (dateIdx !== -1) {
    return '/static/images/' + parts.slice(dateIdx).join('/')
  }
  return null
})

onMounted(() => {
  loadIndexes()
  loadMarketTemperature()
  document.addEventListener('click', handleOutsideClick)
})
onUnmounted(() => {
  document.removeEventListener('click', handleOutsideClick)
})
// 切换指数或指标类型时重新加载数据
watch([selectedCode, selectedMetric], () => {
  imageLoadError.value = false
  if (selectedCode.value && selectedMetric.value) loadHistory()
  // 切换指数时，如果当前在分析 tab，也重新加载分析历史
  if (activeTab.value === 'analysis' && selectedCode.value) loadAnalysisHistory()
})
watch(activeTab, (tab) => {
  if (tab === 'analysis' && selectedCode.value) loadAnalysisHistory()
})
watch(outerTab, (tab) => {
  if (tab === 'dd-image' && ddRecords.value.length === 0) loadDDRecords()
})

defineExpose({ loadHistory })
</script>

<template>
  <div class="valuation-page">
    <!-- Outer Tab Bar -->
    <div class="outer-tab-bar">
      <button :class="['outer-tab-btn', { active: outerTab === 'index' }]" @click="outerTab = 'index'">
        <svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/>
        </svg>
        指数估值
      </button>
      <button :class="['outer-tab-btn', { active: outerTab === 'dd-image' }]" @click="outerTab = 'dd-image'">
        <svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"/>
        </svg>
        螺丝钉图片估值
      </button>
      <button :class="['outer-tab-btn', { active: outerTab === 'super-value' }]" @click="outerTab = 'super-value'; loadSuperValue()">
        <svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6"/>
        </svg>
        超性价比
      </button>
    </div>

    <!-- ════════ Index Tab (outerTab === 'index') ════════ -->
    <template v-if="outerTab === 'index'">

    <!-- Index Selector (searchable) -->
    <div class="card selector-card">
      <label class="selector-label">选择指数</label>
      <div class="searchable-select" @click.stop>
        <input
          v-model="searchQuery"
          class="input-field select-search-input"
          :placeholder="selectedCode && !dropdownOpen ? (indexes.find(idx => idx.index_code === selectedCode)?.index_name || selectedCode) : '搜索指数名称或代码...'"
          @focus="onSearchFocus"
          @input="dropdownOpen = true"
        />
        <div v-if="dropdownOpen" class="select-dropdown">
          <div
            v-for="idx in filteredIndexes"
            :key="idx.index_code"
            :class="['select-option', { active: idx.index_code === selectedCode }]"
            @click="selectIndex(idx.index_code)"
          >
            <span class="option-name">{{ idx.index_name || idx.index_code }}</span>
            <span class="option-code">{{ idx.index_code }}</span>
          </div>
          <div v-if="filteredIndexes.length === 0" class="select-empty">无匹配结果</div>
        </div>
      </div>
      <span v-if="selectedIndexInfo" class="selector-meta">
        最新: {{ selectedIndexInfo.latest_date || '-' }}
      </span>
      <button class="btn-ghost btn-sm" @click="handleRefreshPrices" :disabled="refreshingPrices" style="margin-left:auto;">
        <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24" :class="{ spinning: refreshingPrices }">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
        </svg>
        刷新行情
      </button>
    </div>

    <!-- Current Index Header -->
    <div v-if="selectedCode" class="card index-header-card">
      <div class="index-header-left">
        <div class="index-name-wrapper" @mouseenter="onIndexNameEnter" @mouseleave="onIndexNameLeave">
          <div class="index-header-name">
            {{ selectedIndexName }}
            <svg class="index-info-icon" width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>
            </svg>
          </div>
          <Transition name="fade">
            <div v-if="showIndexInfo" class="index-info-popover">
              <div v-if="indexInfoLoading" class="index-info-loading">
                <span class="spinner-sm"></span> 加载中...
              </div>
              <div v-else class="index-info-text">{{ indexInfoText }}</div>
            </div>
          </Transition>
        </div>
        <div class="index-header-code">{{ selectedCode }}</div>
        <div v-if="latest" class="index-header-metrics">
          <span class="hdr-metric">
            <span class="term-with-tip">{{ latest.metric_type || 'PE' }}<span class="term-tip">{{ metricGlossary[latest.metric_type] || metricGlossary['市盈率'] }}</span></span>: <b>{{ latest.current_value ?? '-' }}</b>
          </span>
          <span class="hdr-divider">|</span>
          <span class="hdr-metric">
            <span class="term-with-tip">百分位<span class="term-tip">{{ metricGlossary['百分位'] }}</span></span>: <b :class="getPercentileLevel(latest.percentile) === 'success' ? 'val-low' : getPercentileLevel(latest.percentile) === 'danger' ? 'val-high' : ''">{{ latest.percentile != null ? latest.percentile + '%' : '-' }}</b>
          </span>
          <span class="hdr-divider">|</span>
          <span :class="['hdr-status', 'status-' + getPercentileLevel(latest.percentile)]">
            {{ latest.percentile == null ? '-' : latest.percentile < 30 ? '低估' : latest.percentile <= 70 ? '合理' : '高估' }}
          </span>
        </div>
      </div>
      <button class="btn btn-primary btn-ai-analysis" @click="showConfirmRun = true" :disabled="analysisLoading">
        <svg v-if="!analysisLoading" width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"/>
        </svg>
        <span v-if="analysisLoading" class="spinner-sm"></span>
        {{ analysisLoading ? '分析中...' : 'AI 市场分析' }}
        <span v-if="runningCount > 1" class="running-badge">{{ runningCount }} 个分析中</span>
        <span class="ai-agent-tooltip">指数深度分析师</span>
      </button>
    </div>

    <!-- Confirm Dialog -->
    <Teleport to="body">
      <div v-if="showConfirmRun" class="modal-overlay" @click.self="showConfirmRun = false">
        <div class="modal-dialog">
          <h3 class="modal-title">AI 市场分析</h3>
          <p class="modal-desc">将基于「{{ selectedIndexName }}」的估值数据和最新财经新闻，调用 AI 生成市场分析报告。</p>
          <p class="modal-warn">此操作将消耗 Token，确认继续？</p>
          <div class="modal-actions">
            <button class="btn btn-outline" @click="showConfirmRun = false">取消</button>
            <button class="btn btn-primary" @click="handleRunAnalysis" title="使用「市场日报分析师」Agent 执行分析">确认分析</button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- Main Tab Bar -->
    <div class="tab-bar">
      <button :class="['tab-btn', { active: activeTab === 'valuation' }]" @click="activeTab = 'valuation'">估值历史</button>
      <button :class="['tab-btn', { active: activeTab === 'analysis' }]" @click="activeTab = 'analysis'">AI 分析历史</button>
    </div>

    <!-- Metric Type Tabs (only for valuation tab) -->
    <div v-if="activeTab === 'valuation' && availableMetrics.length > 1" class="metric-tabs">
      <button
        v-for="m in availableMetrics"
        :key="m"
        @click="selectMetric(m)"
        :class="['metric-tab', { active: selectedMetric === m }]"
      >
        {{ m }}
      </button>
    </div>
    <div v-else-if="activeTab === 'valuation' && availableMetrics.length === 1" class="metric-tabs">
      <span class="metric-tab active">{{ availableMetrics[0] }}</span>
    </div>

    <!-- Loading -->
    <div v-if="loading" class="loading-state">
      <div class="spinner-lg"></div>
      <span>加载中...</span>
    </div>

    <!-- No Data -->
    <div v-else-if="indexes.length === 0" class="empty-state">
      <EmptyState
        icon="chart"
        title="暂无估值数据"
        description="请先解析图片获取估值数据"
      />
    </div>

    <!-- ════════ Valuation Tab Content ════════ -->
    <template v-if="activeTab === 'valuation' && latest">

      <!-- Metric Cards -->
      <div class="metric-grid">
        <div class="card metric-card">
          <div class="metric-label">
            <span class="term-with-tip">当前 {{ latest.metric_type || '值' }}<span class="term-tip">{{ metricGlossary[latest.metric_type] || '' }}</span></span>
          </div>
          <div class="metric-value">{{ latest.current_value ?? '-' }}</div>
        </div>
        <div :class="['card', 'metric-card', 'metric-' + getPercentileLevel(latest.percentile)]">
          <div class="metric-label">
            <span class="term-with-tip">分位点<span class="term-tip">{{ metricGlossary['分位点'] }}</span></span>
          </div>
          <div class="metric-value">{{ latest.percentile != null ? latest.percentile + '%' : '-' }}</div>
        </div>
        <div class="card metric-card">
          <div class="metric-label">当前点位</div>
          <div class="metric-value">{{ latest.current_point ?? '-' }}</div>
        </div>
        <div class="card metric-card">
          <div class="metric-label">涨跌幅</div>
          <div :class="['metric-value', latest.change_pct >= 0 ? 'val-high' : 'val-low']">
            {{ latest.change_pct != null ? latest.change_pct + '%' : '-' }}
          </div>
        </div>
      </div>

      <!-- Source Image -->
      <div v-if="sourceImageUrl && !imageLoadError" class="card source-image-card">
        <h3 class="card-title">来源图片 <span v-if="latest.snapshot_date" class="count-badge">{{ latest.snapshot_date }}</span></h3>
        <div class="source-image-wrap" @click="showLightbox = true">
          <img :src="sourceImageUrl" alt="估值来源图" class="source-image" loading="lazy" @error="imageLoadError = true" />
          <div class="image-zoom-hint">
            <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0zM10 7v3m0 0v3m0-3h3m-3 0H7"/>
            </svg>
            点击放大
          </div>
        </div>
      </div>

      <!-- Lightbox -->
      <Teleport to="body">
        <div v-if="showLightbox && sourceImageUrl" class="lightbox-overlay" @click.self="showLightbox = false">
          <button class="lightbox-close" @click="showLightbox = false">&times;</button>
          <img :src="sourceImageUrl" alt="估值来源图" class="lightbox-img" />
        </div>
      </Teleport>

      <!-- Table Header Tooltip (fixed positioned, escapes overflow) -->
      <Teleport to="body">
        <Transition name="fade">
          <div v-if="tableTip.show" class="table-tip-fixed" :style="{ left: tableTip.x + 'px', top: tableTip.y + 'px' }">
            {{ tableTip.text }}
          </div>
        </Transition>
      </Teleport>

      <!-- Valuation Range Bar -->
      <div class="card range-card">
        <div class="range-header">
          <span class="range-title">{{ latest.metric_type || '估值' }} 估值区间</span>
          <span class="range-zscore" v-if="latest.zscore != null">
            <span class="term-with-tip">Z分数<span class="term-tip">{{ metricGlossary['Z分数'] }}</span></span>: {{ latest.zscore }}
          </span>
        </div>

        <!-- Gauge visualization -->
        <div class="gauge-container">
          <div class="gauge-bar">
            <div class="gauge-zone zone-low" style="width: 30%">
              <span class="zone-label">低估</span>
            </div>
            <div class="gauge-zone zone-mid" style="width: 40%">
              <span class="zone-label">合理</span>
            </div>
            <div class="gauge-zone zone-high" style="width: 30%">
              <span class="zone-label">高估</span>
            </div>
            <!-- Current position marker -->
            <div
              v-if="latest.percentile != null"
              class="gauge-marker"
              :style="{ left: Math.min(100, Math.max(0, latest.percentile)) + '%' }"
            >
              <div class="marker-label">{{ latest.percentile }}%</div>
              <div class="marker-arrow"></div>
            </div>
          </div>

          <!-- Value labels -->
          <div class="gauge-labels">
            <div class="gauge-label">
              <span class="label-key">最小</span>
              <span class="label-val">{{ latest.min_value ?? '-' }}</span>
            </div>
            <div class="gauge-label">
              <span class="label-key val-low"><span class="term-with-tip">机会<span class="term-tip">{{ metricGlossary['机会值'] }}</span></span></span>
              <span class="label-val val-low">{{ latest.opportunity_value ?? '-' }}</span>
            </div>
            <div class="gauge-label">
              <span class="label-key"><span class="term-with-tip">中位<span class="term-tip">{{ metricGlossary['中位数'] }}</span></span></span>
              <span class="label-val">{{ latest.median ?? '-' }}</span>
            </div>
            <div class="gauge-label">
              <span class="label-key val-high"><span class="term-with-tip">危险<span class="term-tip">{{ metricGlossary['危险值'] }}</span></span></span>
              <span class="label-val val-high">{{ latest.danger_value ?? '-' }}</span>
            </div>
            <div class="gauge-label">
              <span class="label-key">最大</span>
              <span class="label-val">{{ latest.max_value ?? '-' }}</span>
            </div>
          </div>
        </div>

        <!-- Average -->
        <div class="range-avg" v-if="latest.avg_value != null">
          平均值: {{ latest.avg_value }}
        </div>
      </div>

      <!-- Trend Chart -->
      <div v-if="history.length > 1" class="card trend-card">
        <h3 class="card-title">估值趋势 <span class="count-badge">{{ history.length }} 条</span></h3>
        <div ref="trendChartRef" class="trend-chart"></div>
        <div class="percentile-legend">
          <span class="legend-item"><span class="legend-color" style="background:rgba(16,185,129,0.15)"></span>低估区 (&lt;30%)</span>
          <span class="legend-item"><span class="legend-color" style="background:rgba(156,163,175,0.08)"></span>合理区 (30-70%)</span>
          <span class="legend-item"><span class="legend-color" style="background:rgba(239,68,68,0.12)"></span>高估区 (&gt;70%)</span>
        </div>
      </div>

      <!-- No trend data hint -->
      <div v-else class="card hint-card">
        <svg width="20" height="20" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>
        </svg>
        <span>需要至少 2 条数据才能显示趋势图，当前 {{ history.length }} 条</span>
      </div>

      <!-- History Table -->
      <div v-if="history.length > 0" class="card">
        <h3 class="card-title">历史记录 <span class="count-badge">{{ history.length }} 条</span></h3>
        <div class="table-wrap">
          <table class="data-table">
            <thead>
              <tr>
                <th>日期</th>
                <th><span class="term-with-tip th-tip" @mouseenter="onTableTipEnter($event, '估值指标类型，如市盈率(PE)、市净率(PB)等。')" @mouseleave="onTableTipLeave">指标</span></th>
                <th>当前值</th>
                <th>点位</th>
                <th><span class="term-with-tip th-tip" @mouseenter="onTableTipEnter($event, metricGlossary['分位点'])" @mouseleave="onTableTipLeave">分位点</span></th>
                <th><span class="term-with-tip th-tip" @mouseenter="onTableTipEnter($event, metricGlossary['机会值'])" @mouseleave="onTableTipLeave">机会值</span></th>
                <th><span class="term-with-tip th-tip" @mouseenter="onTableTipEnter($event, metricGlossary['中位数'])" @mouseleave="onTableTipLeave">中位数</span></th>
                <th><span class="term-with-tip th-tip" @mouseenter="onTableTipEnter($event, metricGlossary['危险值'])" @mouseleave="onTableTipLeave">危险值</span></th>
                <th><span class="term-with-tip th-tip" @mouseenter="onTableTipEnter($event, metricGlossary['Z分数'])" @mouseleave="onTableTipLeave">Z分数</span></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in history" :key="row.id">
                <td class="td-date">{{ row.snapshot_date }}</td>
                <td><span class="badge badge-neutral">{{ row.metric_type || '-' }}</span></td>
                <td class="td-val">{{ row.current_value ?? '-' }}</td>
                <td>{{ row.current_point ?? '-' }}</td>
                <td>
                  <span :class="['badge', 'badge-' + getPercentileLevel(row.percentile)]">
                    {{ row.percentile != null ? row.percentile + '%' : '-' }}
                  </span>
                </td>
                <td class="val-low">{{ row.opportunity_value ?? '-' }}</td>
                <td>{{ row.median ?? '-' }}</td>
                <td class="val-high">{{ row.danger_value ?? '-' }}</td>
                <td>{{ row.zscore ?? '-' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </template>

    <!-- ════════ Analysis Tab Content ════════ -->
    <template v-if="activeTab === 'analysis'">

      <!-- Analysis Loading -->
      <div v-if="analysisLoading" class="card analysis-loading-card">
        <div class="analysis-loading-inner">
          <div class="spinner-lg"></div>
          <div class="analysis-loading-text">
            <div class="loading-title">AI 正在分析「{{ selectedIndexName }}」...</div>
            <div class="loading-desc">正在检索最新财经新闻并生成分析报告，可切换指数查看其他分析</div>
          </div>
        </div>
      </div>

      <!-- Latest Analysis Result -->
      <div v-if="analysisResult && !analysisLoading" class="card analysis-result-card">
        <div class="analysis-result-header">
          <h3 class="card-title">分析报告</h3>
          <div class="analysis-meta">
            <span class="meta-item">{{ analysisResult.agent_name }}</span>
            <span v-if="analysisResult.token_usage" class="meta-item">{{ analysisResult.token_usage }} tokens</span>
            <span class="meta-item">{{ formatAnalysisTime(analysisResult.created_at) }}</span>
          </div>
        </div>
        <div class="analysis-content markdown-body" v-html="renderMarkdown(analysisResult.result)"></div>
      </div>

      <!-- History List -->
      <div class="card">
        <h3 class="card-title">
          分析历史
          <span class="count-badge">{{ analysisHistory.length }} 条</span>
        </h3>

        <div v-if="historyLoading" class="loading-state">
          <div class="spinner-sm"></div>
          <span>加载中...</span>
        </div>

        <div v-else-if="analysisHistory.length === 0" class="empty-hint">
          <EmptyState
            icon="analysis"
            title="暂无分析记录"
            description="点击上方「AI 市场分析」按钮开始"
          />
        </div>

        <div v-else class="history-list">
          <div v-for="item in analysisHistory" :key="item.id" class="history-item">
            <div class="history-item-main" @click="viewHistoryItem(item)">
              <div class="history-item-top">
                <span class="history-date">{{ formatAnalysisTime(item.created_at) }}</span>
                <span class="history-index">{{ item.index_name || item.index_code }}</span>
                <span v-if="item.agent_name" class="history-agent">{{ item.agent_name }}</span>
              </div>
              <div class="history-item-preview">
                {{ (item.result || '').substring(0, 100) }}...
              </div>
            </div>
            <div class="history-item-actions">
              <span v-if="item.token_usage" class="history-tokens">{{ item.token_usage }} tokens</span>
              <button class="btn-icon btn-icon-danger" @click.stop="handleDeleteHistory(item.id)" title="删除">
                <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/>
                </svg>
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- Viewing History Detail (overlay) -->
      <Teleport to="body">
        <div v-if="viewingHistory" class="modal-overlay" @click.self="closeHistoryDetail">
          <div class="modal-dialog modal-lg">
            <div class="modal-header">
              <h3 class="modal-title">分析报告详情</h3>
              <button class="modal-close" @click="closeHistoryDetail">&times;</button>
            </div>
            <div class="modal-meta-bar">
              <span>{{ viewingHistory.index_name || viewingHistory.index_code }}</span>
              <span>{{ viewingHistory.agent_name }}</span>
              <span v-if="viewingHistory.token_usage">{{ viewingHistory.token_usage }} tokens</span>
              <span>{{ formatAnalysisTime(viewingHistory.created_at) }}</span>
            </div>
            <div class="modal-body markdown-body" v-html="renderMarkdown(viewingHistory.result)"></div>
          </div>
        </div>
      </Teleport>
    </template>

    </template><!-- end outerTab === 'index' -->

    <!-- ════════ DD Image Tab (outerTab === 'dd-image') ════════ -->
    <template v-if="outerTab === 'dd-image'">
      <div v-if="ddLoading" class="loading-state">
        <div class="spinner-lg"></div>
        <span>加载中...</span>
      </div>

      <div v-else-if="!ddRecords.length" class="empty-state">
        <EmptyState
          icon="image"
          title="暂无螺丝钉图片估值数据"
          description="请先在「图片浏览 → 螺丝钉估值」中上传并解析图片"
        />
      </div>

      <template v-else>
        <!-- Toolbar: Date Selector + Summary -->
        <div class="dd-toolbar card">
          <div class="dd-toolbar-left">
            <div class="dd-toolbar-title">🔩 螺丝钉指数估值</div>
            <div class="dd-toolbar-meta">
              <b>{{ ddIndexList.length }}</b> 个指数
              <span v-if="ddSelectedRecord?.market_temperature != null">
                · 市场温度 <b>{{ ddSelectedRecord.market_temperature }}</b>
              </span>
            </div>
          </div>
          <div class="dd-toolbar-right">
            <select v-model="ddSelectedRecordId" class="dd-date-select" @change="onDDDateChange">
              <option v-for="r in ddRecords" :key="r.id" :value="r.id">
                {{ r.update_date || r.created_at?.slice(0, 10) }}
              </option>
            </select>
          </div>
        </div>

        <!-- Search -->
        <div class="card dd-search-card">
          <input
            v-model="ddSearchQuery"
            class="input-field"
            placeholder="搜索指数名称或代码..."
            style="width:100%"
          />
        </div>

        <!-- Index List Table -->
        <div class="card dd-index-table-card">
          <div v-if="ddDetailLoading" class="loading-state" style="padding:2rem">
            <div class="spinner-sm"></div>
            <span>加载中...</span>
          </div>
          <div v-else class="dd-table-wrap">
            <table class="data-table dd-index-table">
              <thead>
                <tr>
                  <th @click="ddSortBy('index_name')" class="sortable">
                    指数名称 {{ ddSortKey === 'index_name' ? (ddSortAsc ? '↑' : '↓') : '' }}
                  </th>
                  <th>代码</th>
                  <th @click="ddSortBy('pe')" class="sortable">
                    PE {{ ddSortKey === 'pe' ? (ddSortAsc ? '↑' : '↓') : '' }}
                  </th>
                  <th @click="ddSortBy('pe_percentile')" class="sortable">
                    PE% {{ ddSortKey === 'pe_percentile' ? (ddSortAsc ? '↑' : '↓') : '' }}
                  </th>
                  <th>PB</th>
                  <th>PB%</th>
                  <th>股息率</th>
                  <th>ROE</th>
                  <th @click="ddSortBy('valuation_status')" class="sortable">
                    估值 {{ ddSortKey === 'valuation_status' ? (ddSortAsc ? '↑' : '↓') : '' }}
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(item, idx) in ddFilteredList" :key="idx">
                  <td class="td-name">{{ item.index_name || '-' }}</td>
                  <td class="td-code">{{ item.index_code || '-' }}</td>
                  <td class="td-val">{{ item.pe ?? '-' }}</td>
                  <td>
                    <span v-if="item.pe_percentile != null" :class="['badge', ddPercentileClass(item.pe_percentile)]">
                      {{ item.pe_percentile }}%
                    </span>
                    <span v-else>-</span>
                  </td>
                  <td class="td-val">{{ item.pb ?? '-' }}</td>
                  <td>
                    <span v-if="item.pb_percentile != null" :class="['badge', ddPercentileClass(item.pb_percentile)]">
                      {{ item.pb_percentile }}%
                    </span>
                    <span v-else>-</span>
                  </td>
                  <td>{{ item.dividend_yield ?? '-' }}</td>
                  <td>{{ item.roe ?? '-' }}</td>
                  <td>
                    <span v-if="item.valuation_status" :class="['dd-status', ddStatusClass(item.valuation_status)]">
                      {{ item.valuation_status }}
                    </span>
                    <span v-else>-</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-if="!ddDetailLoading && !ddFilteredList.length" class="empty-state" style="padding:2rem">
            <p>无匹配指数</p>
          </div>
        </div>
      </template>
    </template><!-- end outerTab === 'dd-image' -->

    <!-- ════════ Super Value Tab (outerTab === 'super-value') ════════ -->
    <template v-if="outerTab === 'super-value'">
      <div class="super-value-page">
        <div v-if="superValueLoading" class="loading-state">
          <div class="spinner-lg"></div>
          <span>扫描中...</span>
        </div>
        <div v-else-if="superValueData">
          <div class="sv-header">
            <div class="sv-summary">
              <span>扫描 {{ superValueData.total_scanned }} 个指数</span>
              <span>·</span>
              <span>数据范围 {{ superValueData.data_range }}</span>
              <span>·</span>
              <span>{{ superValueData.scan_time }}</span>
            </div>
          </div>
          <div v-if="superValueData.opportunities?.length" class="sv-list">
            <div v-for="(item, i) in superValueData.opportunities" :key="item.index_code" class="sv-card">
              <div class="sv-rank">#{{ i + 1 }}</div>
              <div class="sv-main">
                <div class="sv-top">
                  <span class="sv-name">{{ item.index_name }}</span>
                  <span class="sv-score" :class="item.score >= 70 ? 'sv-score-high' : item.score >= 55 ? 'sv-score-mid' : ''">{{ item.score }}分</span>
                  <span class="sv-level" :class="'sv-level-' + item.valuation_level">{{ item.valuation_level }}</span>
                </div>
                <div class="sv-metrics">
                  <span>百分位 <b>{{ item.current_percentile }}%</b></span>
                  <span v-if="item.current_value">· {{ item.current_value }}</span>
                  <span v-if="item.zscore != null">· Z-score <b>{{ item.zscore }}</b></span>
                  <span v-if="item.consecutive_drop_days > 0">· 连续下跌 <b>{{ item.consecutive_drop_days }}天</b></span>
                  <span v-if="item.drop_7d > 0">· 7日跌 <b>{{ item.drop_7d }}%</b></span>
                  <span class="sv-source">{{ item.data_source }} · {{ item.data_points }}天</span>
                </div>
                <div v-if="item.tags?.length" class="sv-tags">
                  <span v-for="tag in item.tags" :key="tag" class="sv-tag">{{ tag }}</span>
                </div>
                <div class="sv-summary-text">{{ item.summary }}</div>
                <!-- 打分明细 -->
                <div v-if="item.score_breakdown" class="sv-breakdown">
                  <div v-for="(dim, key) in item.score_breakdown" :key="key" class="sv-breakdown-item">
                    <span class="sv-breakdown-label">{{ breakdownLabels[key] }}</span>
                    <div class="sv-breakdown-bar">
                      <div class="sv-breakdown-fill" :style="{ width: (dim.score / dim.max * 100) + '%' }"></div>
                    </div>
                    <span class="sv-breakdown-score">{{ dim.score }}/{{ dim.max }}</span>
                    <span class="sv-breakdown-detail">{{ dim.detail }}</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
          <div v-else class="empty-state" style="padding:2rem">
            <p>暂无超性价比指数（所有指数评分均低于 40 分）</p>
          </div>

          <!-- 增强策略分析 -->
          <div class="sv-strategy-section">
            <div class="sv-strategy-header">
              <h3>🧠 增强策略分析</h3>
              <button
                v-if="!strategyData && !strategyLoading"
                class="btn btn-primary btn-sm"
                @click="loadStrategy"
              >AI 深度分析</button>
              <span v-if="strategyLoading" class="spinner-sm"></span>
            </div>

            <div v-if="strategyLoading" class="loading-state" style="padding:1.5rem">
              <div class="spinner-lg"></div>
              <span>增强策略分析师正在分析每个低估指数的机会与风险...</span>
            </div>

            <div v-else-if="strategyData">
              <div v-if="strategyData.agent_name || strategyData.token_usage" class="sv-strategy-meta">
                <span v-if="strategyData.agent_name">{{ strategyData.agent_name }}</span>
                <span v-if="strategyData.token_usage">· {{ strategyData.token_usage }} tokens</span>
              </div>
              <div v-if="strategyData.overall_summary" class="sv-strategy-summary">
                {{ strategyData.overall_summary }}
              </div>
              <div class="sv-strategy-list">
                <div v-for="s in strategyData.strategies" :key="s.index_code" class="sv-strategy-card">
                  <div class="sv-strategy-top">
                    <span class="sv-strategy-name">{{ s.index_name }}</span>
                    <span :class="['sv-action-badge', 'sv-action-' + (s.action || '').replace(/[立即买入分批建仓观望回避]/g, m => ({'立即买入':'buy','分批建仓':'buy','观望':'hold','回避':'avoid'}[m] || 'hold'))]">
                      {{ s.action }}
                    </span>
                    <span :class="['sv-type-badge', s.opportunity_type === '真低估' ? 'sv-type-good' : s.opportunity_type === '价值陷阱' ? 'sv-type-bad' : 'sv-type-neutral']">
                      {{ s.opportunity_type }}
                    </span>
                  </div>
                  <div class="sv-strategy-detail">
                    <div v-if="s.recovery_time" class="sv-strategy-row">
                      <span class="sv-strategy-label">预期恢复</span>
                      <span>{{ s.recovery_time }}</span>
                    </div>
                    <div v-if="s.catalysts?.length" class="sv-strategy-row">
                      <span class="sv-strategy-label">催化剂</span>
                      <span>{{ s.catalysts.join('、') }}</span>
                    </div>
                    <div v-if="s.risk_factors?.length" class="sv-strategy-row">
                      <span class="sv-strategy-label">风险</span>
                      <span>{{ s.risk_factors.join('、') }}</span>
                    </div>
                    <div v-if="s.action_detail" class="sv-strategy-action">{{ s.action_detail }}</div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </template>
  </div>
  <ConfirmDialog
    :visible="confirm.visible"
    :title="confirm.title"
    :message="confirm.message"
    :danger="confirm.danger"
    @confirm="() => confirm.onConfirm?.()"
    @cancel="confirm.visible = false"
  />
  <AppToast />
</template>

<style scoped>
.valuation-page {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
  font-family: 'SF Pro Display', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}

/* Market Temperature Card */
.market-temp-card {
  background: linear-gradient(135deg, var(--color-bg-secondary), var(--color-bg-primary));
  border: 1px solid var(--color-border);
  padding: 1rem 1.25rem;
}

.market-temp-header {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.market-temp-icon {
  width: 40px;
  height: 40px;
  border-radius: 10px;
  background: linear-gradient(135deg, var(--color-primary-50), var(--color-primary-100));
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--color-primary-600);
}

.market-temp-info {
  flex: 1;
}

.market-temp-label {
  font-size: 0.75rem;
  color: var(--color-text-secondary);
  margin-bottom: 0.25rem;
}

.market-temp-value {
  font-size: 1.5rem;
  font-weight: 700;
  color: var(--color-text-primary);
}

.market-temp-badge {
  font-size: 0.75rem;
  font-weight: 600;
  padding: 0.375rem 0.75rem;
  border-radius: 999px;
}

.market-temp-badge.temp-低温 {
  background: linear-gradient(135deg, #dcfce7, #bbf7d0);
  color: #166534;
  border: 1px solid #86efac;
}

.market-temp-badge.temp-适中 {
  background: linear-gradient(135deg, #fef9c3, #fef08a);
  color: #854d0e;
  border: 1px solid #fde047;
}

.market-temp-badge.temp-高温 {
  background: linear-gradient(135deg, #fee2e2, #fecaca);
  color: #991b1b;
  border: 1px solid #fca5a5;
}

.market-temp-badge.temp-未知 {
  background: linear-gradient(135deg, var(--color-bg-secondary), var(--color-bg-primary));
  color: var(--color-text-secondary);
  border: 1px solid var(--color-border);
}

.market-temp-desc {
  font-size: 0.8125rem;
  color: var(--color-text-secondary);
  margin-top: 0.5rem;
  padding-top: 0.5rem;
  border-top: 1px solid var(--color-border-light);
}

.market-temp-date {
  font-size: 0.75rem;
  color: var(--color-text-tertiary);
  margin-top: 0.375rem;
}

.card-title {
  font-size: 1.1rem;
  font-weight: 700;
  color: var(--color-text-primary);
  margin: 0 0 1.25rem 0;
  display: flex;
  align-items: center;
  gap: 0.6rem;
  letter-spacing: -0.01em;
}

.count-badge {
  font-size: 0.75rem;
  font-weight: 600;
  background: linear-gradient(135deg, var(--color-primary-50), var(--color-primary-100));
  color: var(--color-primary-700);
  padding: 0.2rem 0.6rem;
  border-radius: 999px;
  border: 1px solid var(--color-primary-200);
}

/* Selector */
.selector-card {
  display: flex;
  align-items: center;
  gap: 1rem;
  padding: 1rem 1.25rem;
  background: linear-gradient(135deg, var(--color-bg-card) 0%, var(--color-bg-hover) 100%);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
  position: relative;
  z-index: 1;
}

.selector-label {
  font-size: 0.9rem;
  font-weight: 600;
  color: var(--color-text-primary);
  white-space: nowrap;
}

.searchable-select {
  position: relative;
  flex: 1;
  max-width: 420px;
}

.select-search-input {
  width: 100%;
  font-size: 0.95rem;
  padding: 0.65rem 1rem;
  border-radius: var(--radius-md);
  border: 2px solid var(--color-border);
  transition: all 0.2s ease;
}

.select-search-input:focus {
  border-color: var(--color-primary-400);
  box-shadow: 0 0 0 3px var(--color-primary-100);
}

.select-dropdown {
  position: absolute;
  top: 100%;
  left: 0;
  right: 0;
  z-index: var(--z-modal);
  margin-top: 6px;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: 0 10px 40px rgba(0, 0, 0, 0.12), 0 2px 8px rgba(0, 0, 0, 0.08);
  max-height: 300px;
  overflow-y: auto;
  backdrop-filter: blur(10px);
}

.select-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.7rem 1rem;
  font-size: 0.95rem;
  cursor: pointer;
  transition: all 0.15s ease;
  border-bottom: 1px solid var(--color-border-light);
}

.select-option:last-child { border-bottom: none; }
.select-option:hover { background: var(--color-primary-50); }
.select-option.active { background: var(--color-primary-100); color: var(--color-primary-700); font-weight: 600; }

.option-name { font-weight: 600; color: var(--color-text-primary); }
.option-code { font-size: 0.8rem; color: var(--color-text-muted); font-family: 'SF Mono', 'Fira Code', monospace; }

.select-empty {
  padding: 1.25rem;
  text-align: center;
  font-size: 0.9rem;
  color: var(--color-text-muted);
}

.selector-meta {
  font-size: 0.85rem;
  color: var(--color-text-muted);
  white-space: nowrap;
}

/* Metric Tabs */
.metric-tabs {
  display: flex;
  gap: 0.6rem;
  padding: 0 0.25rem;
  flex-wrap: wrap;
}

.metric-tab {
  font-size: 0.9rem;
  font-weight: 600;
  padding: 0.5rem 1.25rem;
  border-radius: var(--radius-lg);
  border: 2px solid var(--color-border);
  background: var(--color-bg-card);
  color: var(--color-text-secondary);
  cursor: pointer;
  transition: all 0.2s ease;
}

.metric-tab:hover {
  border-color: var(--color-primary-400);
  color: var(--color-primary-600);
  background: var(--color-primary-50);
}

.metric-tab.active {
  background: linear-gradient(135deg, var(--color-primary-500), var(--color-primary-600));
  color: white;
  border-color: var(--color-primary-500);
  box-shadow: 0 4px 12px var(--color-primary-200);
}

/* Loading & Empty */
.loading-state {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.75rem;
  padding: 3rem;
  color: var(--color-text-muted);
  font-size: 0.875rem;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.75rem;
  padding: 3rem;
  color: var(--color-text-muted);
}

.empty-state p { font-size: 0.95rem; margin: 0; }

/* Metric Grid */
.metric-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 1rem;
}

.metric-card {
  text-align: center;
  padding: 1.25rem;
  background: linear-gradient(135deg, var(--color-bg-card) 0%, var(--color-bg-hover) 100%);
  border-radius: var(--radius-lg);
  border: 1px solid var(--color-border);
  transition: all 0.2s ease;
}

.metric-card:hover {
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.08);
}

.metric-label {
  font-size: 0.85rem;
  color: var(--color-text-muted);
  margin-bottom: 0.5rem;
  font-weight: 500;
}

.metric-value {
  font-size: 1.5rem;
  font-weight: 800;
  color: var(--color-text-primary);
  letter-spacing: -0.02em;
}

.metric-success .metric-value { color: #059669; }
.metric-warning .metric-value { color: #d97706; }
.metric-danger .metric-value { color: #dc2626; }

.val-low { color: #059669; font-weight: 700; }
.val-high { color: #dc2626; font-weight: 700; }

/* Range / Gauge */
.range-card {
  padding: 1.5rem;
  background: linear-gradient(135deg, var(--color-bg-card) 0%, var(--color-bg-hover) 100%);
  border-radius: var(--radius-lg);
  border: 1px solid var(--color-border);
}

.range-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 1.5rem;
}

.range-title {
  font-size: 1rem;
  font-weight: 700;
  color: var(--color-text-primary);
}

.range-zscore {
  font-size: 0.85rem;
  color: var(--color-text-muted);
  background: var(--color-bg-input);
  padding: 0.3rem 0.75rem;
  border-radius: var(--radius-md);
  font-weight: 600;
}

.gauge-container {
  padding: 0 0.5rem;
}

.gauge-bar {
  position: relative;
  height: 44px;
  border-radius: 22px;
  overflow: visible;
  display: flex;
  box-shadow: inset 0 3px 6px rgba(0,0,0,0.08);
}

.gauge-zone {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
}

.zone-low {
  background: linear-gradient(90deg, #86efac, #bbf7d0);
  border-radius: 22px 0 0 22px;
}

.zone-mid {
  background: linear-gradient(90deg, #fef08a, #fde68a);
}

.zone-high {
  background: linear-gradient(90deg, #fca5a5, #f87171);
  border-radius: 0 22px 22px 0;
}

.zone-label {
  font-size: 0.8rem;
  font-weight: 700;
  color: rgba(0,0,0,0.5);
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

.gauge-marker {
  position: absolute;
  top: -10px;
  transform: translateX(-50%);
  z-index: 10;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.marker-label {
  background: linear-gradient(135deg, var(--color-primary-600), var(--color-primary-700));
  color: white;
  font-size: 0.8rem;
  font-weight: 800;
  padding: 0.35rem 0.75rem;
  border-radius: var(--radius-md);
  white-space: nowrap;
  box-shadow: 0 4px 12px rgba(201, 168, 76, 0.4);
}

.marker-arrow {
  width: 0;
  height: 0;
  border-left: 6px solid transparent;
  border-right: 6px solid transparent;
  border-top: 6px solid var(--color-primary-600);
}

.gauge-labels {
  display: flex;
  justify-content: space-between;
  margin-top: 1rem;
  padding: 0 0.25rem;
}

.gauge-label {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.25rem;
}

.label-key {
  font-size: 0.8rem;
  color: var(--color-text-muted);
  font-weight: 600;
}

.label-val {
  font-size: 0.95rem;
  font-weight: 700;
  color: var(--color-text-primary);
}

.range-avg {
  text-align: center;
  font-size: 0.85rem;
  color: var(--color-text-muted);
  margin-top: 1rem;
  padding-top: 0.75rem;
  border-top: 2px solid var(--color-border-light);
}

/* Trend Chart */
.trend-chart {
  height: 350px;
  background: linear-gradient(135deg, var(--color-bg-card) 0%, var(--color-bg-hover) 100%);
  border-radius: var(--radius-lg);
  padding: 1rem;
}

/* Hint Card */
.hint-card {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 1.25rem 1.5rem;
  color: var(--color-text-muted);
  font-size: 0.95rem;
  background: linear-gradient(135deg, var(--color-bg-card) 0%, var(--color-bg-hover) 100%);
  border-radius: var(--radius-lg);
  border: 1px dashed var(--color-border);
}

/* Table */
.table-wrap {
  overflow-x: auto;
  border-radius: var(--radius-lg);
  border: 1px solid var(--color-border);
}

.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.95rem;
}

.data-table th {
  text-align: left;
  padding: 1rem 1.25rem;
  font-weight: 700;
  font-size: 0.85rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--color-text-primary);
  background: linear-gradient(135deg, var(--color-bg-hover) 0%, var(--color-bg-input) 100%);
  border-bottom: 2px solid var(--color-border);
  white-space: nowrap;
}

.data-table td {
  padding: 0.85rem 1.25rem;
  color: var(--color-text-secondary);
  border-bottom: 1px solid var(--color-border-light);
}

.data-table tbody tr {
  transition: all 0.15s ease;
}

.data-table tbody tr:hover {
  background: var(--color-primary-50);
  transform: scale(1.002);
}

.data-table tbody tr:last-child td { border-bottom: none; }

.td-date {
  font-weight: 700;
  color: var(--color-text-primary);
  white-space: nowrap;
  font-size: 0.95rem;
}

.td-val {
  font-weight: 700;
  font-size: 1rem;
}

/* Source Image */
.source-image-card {
  padding: 1.5rem;
  background: linear-gradient(135deg, var(--color-bg-card) 0%, var(--color-bg-hover) 100%);
  border-radius: var(--radius-lg);
  border: 1px solid var(--color-border);
}

.source-image-wrap {
  position: relative;
  cursor: pointer;
  border-radius: var(--radius-lg);
  overflow: hidden;
  max-height: 400px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.1);
}

.source-image {
  width: 100%;
  height: auto;
  max-height: 400px;
  object-fit: contain;
  display: block;
  background: var(--color-bg-input);
}

.image-zoom-hint {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.35rem;
  padding: 0.5rem;
  background: linear-gradient(transparent, rgba(0,0,0,0.6));
  color: white;
  font-size: 0.75rem;
  font-weight: 500;
  opacity: 0;
  transition: opacity 0.2s;
}

.source-image-wrap:hover .image-zoom-hint { opacity: 1; }

/* Lightbox */
.lightbox-overlay {
  position: fixed;
  inset: 0;
  z-index: var(--z-lightbox);
  background: rgba(0, 0, 0, 0.85);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 2rem;
}

.lightbox-close {
  position: absolute;
  top: 1rem;
  right: 1.5rem;
  background: none;
  border: none;
  color: white;
  font-size: 2rem;
  cursor: pointer;
  z-index: 10000;
  line-height: 1;
  padding: 0.5rem;
}

.lightbox-img {
  max-width: 95vw;
  max-height: 90vh;
  object-fit: contain;
  border-radius: var(--radius-md);
}

/* ── Index Header ─────────────────────────────────── */
.index-header-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1.25rem;
  padding: 1.25rem 1.5rem;
  background: linear-gradient(135deg, var(--color-bg-card) 0%, var(--color-bg-hover) 100%);
  border-radius: var(--radius-lg);
  border: 1px solid var(--color-border);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
}

.index-header-left {
  display: flex;
  align-items: center;
  gap: 1rem;
  flex-wrap: wrap;
}

.index-header-name {
  font-size: 1.4rem;
  font-weight: 800;
  color: var(--color-text-primary);
  letter-spacing: -0.02em;
}

.index-header-code {
  font-size: 0.85rem;
  font-family: 'SF Mono', 'Fira Code', monospace;
  color: var(--color-text-muted);
  background: var(--color-bg-input);
  padding: 0.25rem 0.6rem;
  border-radius: var(--radius-md);
  font-weight: 600;
}

.index-header-metrics {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  font-size: 0.95rem;
  color: var(--color-text-secondary);
}

.hdr-metric b { font-weight: 700; }
.hdr-divider {
  color: var(--color-border);
  font-weight: 300;
}

.hdr-status {
  font-size: 0.85rem;
  font-weight: 700;
  padding: 0.3rem 0.75rem;
  border-radius: 999px;
  letter-spacing: 0.02em;
}

.status-success {
  background: linear-gradient(135deg, rgba(16,185,129,0.15), rgba(16,185,129,0.05));
  color: #059669;
  border: 1px solid rgba(16,185,129,0.2);
}

.status-warning {
  background: linear-gradient(135deg, rgba(245,158,11,0.15), rgba(245,158,11,0.05));
  color: #d97706;
  border: 1px solid rgba(245,158,11,0.2);
}

.status-danger {
  background: linear-gradient(135deg, rgba(239,68,68,0.15), rgba(239,68,68,0.05));
  color: #dc2626;
  border: 1px solid rgba(239,68,68,0.2);
}

.status-neutral {
  background: var(--color-bg-input);
  color: var(--color-text-muted);
  border: 1px solid var(--color-border);
}

.btn-ai-analysis {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  white-space: nowrap;
  flex-shrink: 0;
  font-size: 0.95rem;
  font-weight: 600;
  padding: 0.65rem 1.25rem;
  border-radius: var(--radius-lg);
  background: linear-gradient(135deg, var(--color-primary-500), var(--color-primary-600));
  color: white;
  box-shadow: 0 4px 12px var(--color-primary-200);
  transition: all 0.2s ease;
}

.btn-ai-analysis:hover {
  box-shadow: 0 6px 20px var(--color-primary-300);
}

/* ── Tab Bar ──────────────────────────────────────── */
.tab-bar {
  display: flex;
  gap: 0;
  border-bottom: 2px solid var(--color-border);
  padding-bottom: 0;
}

.tab-btn {
  padding: 0.75rem 1.5rem;
  font-size: 1rem;
  font-weight: 600;
  color: var(--color-text-muted);
  background: none;
  border: none;
  border-bottom: 3px solid transparent;
  cursor: pointer;
  transition: all 0.2s ease;
  margin-bottom: -2px;
}

.tab-btn:hover {
  color: var(--color-text-secondary);
  background: var(--color-bg-hover);
}

.tab-btn.active {
  color: var(--color-primary-600);
  border-bottom-color: var(--color-primary-500);
  background: var(--color-primary-50);
}

/* ── Analysis Loading ─────────────────────────────── */
.analysis-loading-card {
  padding: 3rem;
  background: linear-gradient(135deg, var(--color-bg-card) 0%, var(--color-bg-hover) 100%);
  border-radius: var(--radius-lg);
  border: 1px solid var(--color-border);
}

.analysis-loading-inner {
  display: flex;
  align-items: center;
  gap: 1.5rem;
}

.loading-title {
  font-size: 1.15rem;
  font-weight: 700;
  color: var(--color-text-primary);
}

.loading-desc {
  font-size: 0.95rem;
  color: var(--color-text-muted);
  margin-top: 0.35rem;
}

/* ── Analysis Result ──────────────────────────────── */
.analysis-result-card {
  padding: 2rem;
  background: linear-gradient(135deg, var(--color-bg-card) 0%, var(--color-bg-hover) 100%);
  border-radius: var(--radius-lg);
  border: 1px solid var(--color-border);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.06);
}

.analysis-result-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 1.5rem;
  flex-wrap: wrap;
  gap: 0.75rem;
}

.analysis-result-header .card-title {
  margin: 0;
}

.analysis-meta {
  display: flex;
  align-items: center;
  gap: 1rem;
  font-size: 0.85rem;
  color: var(--color-text-muted);
}

.meta-item {
  display: flex;
  align-items: center;
  gap: 0.35rem;
}

.analysis-content {
  font-size: 1rem;
  line-height: 1.8;
  color: var(--color-text-secondary);
}

/* ── Markdown Body ────────────────────────────────── */
.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3) {
  color: var(--color-text-primary);
  margin-top: 1.5em;
  margin-bottom: 0.75em;
  font-weight: 700;
}

.markdown-body :deep(h1) { font-size: 1.5rem; }
.markdown-body :deep(h2) { font-size: 1.3rem; }
.markdown-body :deep(h3) { font-size: 1.15rem; }

.markdown-body :deep(p) {
  margin: 0.75em 0;
}

.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  padding-left: 1.75em;
  margin: 0.5em 0;
}

.markdown-body :deep(li) {
  margin: 0.25em 0;
}

.markdown-body :deep(strong) {
  color: var(--color-text-primary);
  font-weight: 600;
}

.markdown-body :deep(code) {
  background: var(--color-bg-input);
  padding: 0.1em 0.3em;
  border-radius: 3px;
  font-size: 0.85em;
}

.markdown-body :deep(blockquote) {
  border-left: 3px solid var(--color-primary-300);
  padding-left: 1em;
  margin: 0.75em 0;
  color: var(--color-text-muted);
}

.markdown-body :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 0.75em 0;
  font-size: 0.85em;
}

.markdown-body :deep(th),
.markdown-body :deep(td) {
  border: 1px solid var(--color-border);
  padding: 0.4em 0.6em;
  text-align: left;
}

.markdown-body :deep(th) {
  background: var(--color-bg-input);
  font-weight: 600;
}

/* ── History List ─────────────────────────────────── */
.empty-hint {
  text-align: center;
  padding: 2.5rem;
  color: var(--color-text-muted);
  font-size: 1rem;
}

.history-list {
  display: flex;
  flex-direction: column;
}

.history-item {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1.25rem;
  padding: 1.25rem 0;
  border-bottom: 1px solid var(--color-border-light);
  transition: all 0.15s ease;
}

.history-item:hover {
  background: var(--color-primary-50);
  border-radius: var(--radius-md);
  padding-left: 1rem;
  padding-right: 1rem;
}

.history-item:last-child { border-bottom: none; }

.history-item-main {
  flex: 1;
  cursor: pointer;
  min-width: 0;
}

.history-item-main:hover .history-item-preview {
  color: var(--color-text-secondary);
}

.history-item-top {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 0.5rem;
  flex-wrap: wrap;
}

.history-date {
  font-size: 0.95rem;
  font-weight: 700;
  color: var(--color-text-primary);
}

.history-index {
  font-size: 0.8rem;
  background: linear-gradient(135deg, var(--color-primary-50), var(--color-primary-100));
  color: var(--color-primary-700);
  padding: 0.2rem 0.6rem;
  border-radius: 999px;
  font-weight: 600;
  border: 1px solid var(--color-primary-200);
}

.history-agent {
  font-size: 0.8rem;
  color: var(--color-text-muted);
  font-weight: 500;
}

.history-item-preview {
  font-size: 0.9rem;
  color: var(--color-text-muted);
  line-height: 1.6;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  transition: color 0.15s;
}

.history-item-actions {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  flex-shrink: 0;
}

.history-tokens {
  font-size: 0.8rem;
  color: var(--color-text-muted);
  white-space: nowrap;
}

.btn-icon-danger {
  background: none;
  border: none;
  color: var(--color-text-muted);
  cursor: pointer;
  padding: 0.4rem;
  border-radius: var(--radius-md);
  transition: all 0.15s ease;
}

.btn-icon-danger:hover {
  color: #dc2626;
  background: rgba(239,68,68,0.1);
  transform: scale(1.1);
}

/* ── Modal ────────────────────────────────────────── */
.modal-overlay {
  position: fixed;
  inset: 0;
  z-index: var(--z-modal);
  background: rgba(0,0,0,0.6);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 2rem;
}

.modal-dialog {
  background: var(--color-bg-card);
  border-radius: var(--radius-xl);
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.2);
  padding: 2.5rem;
  max-width: 520px;
  width: 100%;
  max-height: 80vh;
  overflow-y: auto;
  border: 1px solid var(--color-border);
}

.modal-lg {
  max-width: 850px;
}

.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 1.5rem;
}

.modal-header .modal-title { margin: 0; }

.modal-close {
  background: none;
  border: none;
  font-size: 1.75rem;
  color: var(--color-text-muted);
  cursor: pointer;
  padding: 0.35rem 0.6rem;
  line-height: 1;
  border-radius: var(--radius-md);
  transition: all 0.15s ease;
}

.modal-close:hover {
  color: var(--color-text-primary);
  background: var(--color-bg-hover);
}

.modal-title {
  font-size: 1.35rem;
  font-weight: 800;
  color: var(--color-text-primary);
  margin: 0 0 1rem;
  letter-spacing: -0.02em;
}

.modal-desc {
  font-size: 1rem;
  color: var(--color-text-secondary);
  margin: 0 0 0.75rem;
  line-height: 1.6;
}

.modal-warn {
  font-size: 0.95rem;
  color: #d97706;
  margin: 0 0 2rem;
  font-weight: 600;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 1rem;
}

.modal-meta-bar {
  display: flex;
  align-items: center;
  gap: 1rem;
  font-size: 0.85rem;
  color: var(--color-text-muted);
  padding-bottom: 1.25rem;
  border-bottom: 2px solid var(--color-border-light);
  margin-bottom: 1.5rem;
  flex-wrap: wrap;
}

.modal-body {
  font-size: 0.875rem;
  line-height: 1.7;
  color: var(--color-text-secondary);
}

/* ── Spinner ──────────────────────────────────────── */
.spinner-sm {
  width: 14px;
  height: 14px;
  border: 2px solid var(--color-border);
  border-top-color: var(--color-primary-500);
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.spinner-lg {
  width: 32px;
  height: 32px;
  border: 3px solid var(--color-border);
  border-top-color: var(--color-primary-500);
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
}

/* ── Index Info Popover ───────────────────────────── */
.index-name-wrapper {
  position: relative;
  display: inline-block;
}

.index-info-icon {
  display: inline;
  vertical-align: middle;
  margin-left: 4px;
  color: var(--color-text-muted);
  opacity: 0.6;
  cursor: help;
  transition: opacity 0.2s;
}

.index-name-wrapper:hover .index-info-icon {
  opacity: 1;
  color: var(--color-primary-500);
}

.index-info-popover {
  position: absolute;
  top: calc(100% + 8px);
  left: 0;
  z-index: 200;
  width: 320px;
  background: var(--color-bg-hover);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-lg);
  padding: 0.85rem 1rem;
  font-size: 0.8rem;
  line-height: 1.65;
  color: var(--color-text-secondary);
  backdrop-filter: blur(8px);
}

.index-info-popover::before {
  content: '';
  position: absolute;
  top: -6px;
  left: 16px;
  width: 12px;
  height: 12px;
  background: var(--color-bg-hover);
  border-left: 1px solid var(--color-border);
  border-top: 1px solid var(--color-border);
  transform: rotate(45deg);
}

.index-info-loading {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  color: var(--color-text-muted);
  font-size: 0.8rem;
}

.index-info-text {
  white-space: pre-wrap;
}

/* ── Financial Term Tooltips ──────────────────────── */
/* 表格表头 tooltip 用 JS 定位，不需要 CSS hover */
.th-tip { cursor: help; border-bottom: 1px dashed var(--color-text-muted); }

/* ── AI Agent Tooltip ── */
.btn-ai-analysis {
  position: relative;
}

.ai-agent-tooltip {
  position: absolute;
  top: calc(100% + 8px);
  left: 50%;
  transform: translateX(-50%);
  padding: 0.4rem 0.7rem;
  font-size: 0.7rem;
  font-weight: 600;
  color: white;
  background: linear-gradient(135deg, #0d1220, #1a1f35);
  border-radius: var(--radius-md, 8px);
  white-space: nowrap;
  opacity: 0;
  visibility: hidden;
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
  border-bottom-color: #0d1220;
}

.btn-ai-analysis:hover .ai-agent-tooltip {
  opacity: 1;
  visibility: visible;
}

.running-badge {
  font-size: 0.6rem;
  padding: 0.1rem 0.35rem;
  background: #f59e0b;
  color: white;
  border-radius: 8px;
  margin-left: 0.25rem;
  font-weight: 600;
}

/* ── 螺丝钉估值 Tab ── */
.dd-summary {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.dd-summary-title {
  font-size: 1rem;
  font-weight: 600;
  color: var(--color-text-primary);
}

.dd-summary-meta {
  font-size: 0.8rem;
  color: var(--color-text-muted);
}

/* DD Toolbar */
.dd-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1rem 1.25rem;
}

.dd-toolbar-left {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.dd-toolbar-title {
  font-size: 1.1rem;
  font-weight: 700;
  color: var(--color-text-primary);
}

.dd-toolbar-meta {
  font-size: 0.8rem;
  color: var(--color-text-secondary);
}

.dd-toolbar-meta b {
  color: var(--color-primary-500);
}

.dd-date-select {
  padding: 0.5rem 0.75rem;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: var(--color-bg-primary);
  color: var(--color-text-primary);
  font-size: 0.85rem;
  cursor: pointer;
}

.dd-search-card {
  padding: 0.75rem 1rem;
}

/* DD Index Table */
.dd-index-table-card {
  padding: 0;
  overflow: hidden;
}

.dd-index-table-card .dd-table-wrap {
  overflow-x: auto;
}

.dd-index-table {
  font-size: 0.82rem;
  width: 100%;
}

.dd-index-table th {
  font-size: 0.75rem;
  white-space: nowrap;
  position: sticky;
  top: 0;
  background: var(--color-bg-secondary);
  z-index: 1;
}

.dd-index-table th.sortable {
  cursor: pointer;
  user-select: none;
}

.dd-index-table th.sortable:hover {
  color: var(--color-primary-500);
}

.dd-index-table td {
  padding: 0.5rem 0.6rem;
  border-bottom: 1px solid var(--color-border-light);
}

.dd-index-table tr:hover td {
  background: var(--color-bg-hover);
}

.badge-success-light {
  background: #dcfce7;
  color: #166534;
}

.badge-danger-light {
  background: #fee2e2;
  color: #991b1b;
}

.td-name {
  font-weight: 600;
  white-space: nowrap;
}

.td-code {
  font-family: monospace;
  font-size: 0.75rem;
  color: var(--color-text-muted);
}

.td-val {
  font-variant-numeric: tabular-nums;
}

.dd-status {
  font-weight: 600;
  font-size: 0.8rem;
}

/* expand transition */
.expand-enter-active,
.expand-leave-active {
  transition: all 0.25s ease;
  max-height: 600px;
}

.expand-enter-from,
.expand-leave-to {
  max-height: 0;
  opacity: 0;
}

/* ── Outer Tab Bar ── */
.outer-tab-bar {
  display: flex;
  gap: 0.5rem;
  padding: 0.25rem;
  background: var(--color-bg-card);
  border-radius: var(--radius-lg);
  border: 1px solid var(--color-border);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
}

.outer-tab-btn {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.6rem;
  padding: 0.85rem 1.25rem;
  font-size: 1rem;
  font-weight: 600;
  color: var(--color-text-muted);
  background: transparent;
  border: 2px solid transparent;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all 0.2s ease;
}

.outer-tab-btn:hover {
  color: var(--color-text-secondary);
  background: var(--color-bg-hover);
}

.outer-tab-btn.active {
  color: var(--color-primary-600);
  background: linear-gradient(135deg, var(--color-primary-50), var(--color-primary-100));
  border-color: var(--color-primary-300);
  box-shadow: 0 2px 8px var(--color-primary-100);
}

.outer-tab-btn.active svg {
  color: var(--color-primary-500);
}

/* ── DD Image Summary ── */

/* DD record right section */
.dd-record-right {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  flex-shrink: 0;
}

.dd-record-time {
  font-size: 0.8rem;
  color: var(--color-text-muted);
  font-family: 'SF Mono', 'Fira Code', monospace;
}

.percentile-legend {
  display: flex;
  justify-content: center;
  gap: 1.5rem;
  margin-top: 0.5rem;
  font-size: 0.75rem;
  color: var(--text-tertiary);
}

.percentile-legend .legend-item {
  display: flex;
  align-items: center;
  gap: 0.35rem;
}

.percentile-legend .legend-color {
  display: inline-block;
  width: 12px;
  height: 12px;
  border-radius: 2px;
}

/* ── 超性价比 ── */

.super-value-page {
  padding: 0;
}

.sv-header {
  margin-bottom: 1rem;
}

.sv-summary {
  display: flex;
  gap: 0.5rem;
  font-size: 0.8rem;
  color: var(--color-text-muted);
}

.sv-list {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.sv-card {
  display: flex;
  gap: 0.75rem;
  padding: 1rem;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  transition: border-color 0.15s;
}

.sv-card:hover {
  border-color: var(--color-primary-300);
}

.sv-rank {
  font-size: 1.5rem;
  font-weight: 700;
  color: var(--color-text-muted);
  min-width: 2.5rem;
  text-align: center;
  line-height: 1;
  padding-top: 0.25rem;
}

.sv-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}

.sv-top {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.sv-name {
  font-weight: 600;
  font-size: 0.95rem;
}

.sv-score {
  font-size: 0.85rem;
  font-weight: 700;
  padding: 0.15rem 0.5rem;
  border-radius: 4px;
  background: var(--color-bg-secondary);
  color: var(--color-text-secondary);
}

.sv-score-high {
  background: #fef2f2;
  color: #dc2626;
}

.sv-score-mid {
  background: #fffbeb;
  color: #d97706;
}

.sv-level {
  font-size: 0.75rem;
  padding: 0.1rem 0.4rem;
  border-radius: 4px;
}

.sv-level-极度低估 { background: #fef2f2; color: #dc2626; }
.sv-level-低估 { background: #fff7ed; color: #ea580c; }
.sv-level-偏低 { background: #fefce8; color: #ca8a04; }
.sv-level-适中 { background: #f0fdf4; color: #16a34a; }

.sv-metrics {
  font-size: 0.8rem;
  color: var(--color-text-secondary);
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem;
}

.sv-metrics b {
  color: var(--color-text-primary);
}

.sv-source {
  margin-left: auto;
  font-size: 0.7rem;
  opacity: 0.5;
}

.sv-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
}

.sv-tag {
  font-size: 0.7rem;
  padding: 0.1rem 0.4rem;
  background: var(--color-primary-50);
  color: var(--color-primary-600);
  border-radius: 4px;
}

.sv-summary-text {
  font-size: 0.8rem;
  color: var(--color-text-muted);
  line-height: 1.5;
}

/* ── 打分明细 ── */

.sv-breakdown {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  margin-top: 0.4rem;
  padding-top: 0.4rem;
  border-top: 1px dashed var(--color-border-light);
}

.sv-breakdown-item {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.72rem;
}

.sv-breakdown-label {
  min-width: 4rem;
  color: var(--color-text-muted);
  flex-shrink: 0;
}

.sv-breakdown-bar {
  width: 50px;
  height: 4px;
  background: var(--color-border-light);
  border-radius: 2px;
  overflow: hidden;
  flex-shrink: 0;
}

.sv-breakdown-fill {
  height: 100%;
  background: var(--color-primary-500);
  border-radius: 2px;
  transition: width 0.3s;
}

.sv-breakdown-score {
  min-width: 2rem;
  text-align: right;
  font-weight: 600;
  color: var(--color-text-secondary);
}

.sv-breakdown-detail {
  color: var(--color-text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ── 增强策略 ── */

.sv-strategy-section {
  margin-top: 1.5rem;
  padding-top: 1.5rem;
  border-top: 2px solid var(--color-border);
}

.sv-strategy-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 1rem;
}

.sv-strategy-header h3 {
  font-size: 1rem;
  font-weight: 600;
}

.btn-sm {
  padding: 0.35rem 0.75rem;
  font-size: 0.8rem;
}

.sv-strategy-meta {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  margin-bottom: 0.5rem;
  display: flex;
  gap: 0.25rem;
}

.sv-strategy-summary {
  font-size: 0.85rem;
  line-height: 1.6;
  color: var(--color-text-secondary);
  padding: 0.75rem 1rem;
  background: var(--color-bg-secondary);
  border-radius: var(--radius-md);
  margin-bottom: 1rem;
}

.sv-strategy-list {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.sv-strategy-card {
  padding: 1rem;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.sv-strategy-top {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}

.sv-strategy-name {
  font-weight: 600;
  font-size: 0.95rem;
}

.sv-action-badge {
  font-size: 0.75rem;
  padding: 0.15rem 0.5rem;
  border-radius: 4px;
  font-weight: 600;
}

.sv-action-buy { background: #dcfce7; color: #16a34a; }
.sv-action-hold { background: #fef3c7; color: #d97706; }
.sv-action-avoid { background: #fef2f2; color: #dc2626; }

.sv-type-badge {
  font-size: 0.7rem;
  padding: 0.1rem 0.4rem;
  border-radius: 4px;
  margin-left: auto;
}

.sv-type-good { background: #dcfce7; color: #16a34a; }
.sv-type-bad { background: #fef2f2; color: #dc2626; }
.sv-type-neutral { background: #f3f4f6; color: #6b7280; }

.sv-strategy-detail {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  font-size: 0.8rem;
}

.sv-strategy-row {
  display: flex;
  gap: 0.5rem;
}

.sv-strategy-label {
  color: var(--color-text-muted);
  min-width: 3.5rem;
  flex-shrink: 0;
}

.sv-strategy-action {
  margin-top: 0.35rem;
  padding-top: 0.35rem;
  border-top: 1px solid var(--color-border-light);
  color: var(--color-text-secondary);
  line-height: 1.5;
}

/* ── 移动端响应式 ────────────────────────────────────────── */
@media (max-width: 768px) {
  /* 选择器卡片 */
  .selector-card {
    flex-wrap: wrap;
    padding: 0.75rem;
    gap: 0.5rem;
  }

  .selector-label {
    font-size: 0.85rem;
    width: 100%;
  }

  .searchable-select {
    max-width: 100%;
    width: 100%;
  }

  .select-search-input {
    font-size: 0.9rem;
    padding: 0.75rem 1rem;
  }

  .select-dropdown {
    max-height: 250px;
  }

  .select-option {
    padding: 0.85rem 1rem;
    font-size: 0.9rem;
  }

  .selector-meta {
    font-size: 0.75rem;
    width: 100%;
  }

  .btn-ghost.btn-sm {
    width: 100%;
    justify-content: center;
    padding: 0.6rem;
    min-height: 44px;
  }

  /* 指数头部 */
  .index-header-card {
    flex-direction: column;
    align-items: flex-start;
    gap: 0.75rem;
  }

  .index-header-left {
    width: 100%;
  }

  .index-name-wrapper {
    width: 100%;
  }

  .index-header-name {
    font-size: 1rem;
  }

  /* 历史列表 */
  .history-item {
    flex-direction: column;
    gap: 0.75rem;
  }

  .history-item-main {
    width: 100%;
  }

  .history-item-actions {
    width: 100%;
    justify-content: space-between;
  }

  .history-item-preview {
    white-space: normal;
    font-size: 0.85rem;
  }

  /* 弹窗适配 */
  .modal-overlay {
    padding: 1rem;
  }

  .modal-dialog {
    max-width: 100%;
    margin: 0;
  }

  .modal-lg {
    max-width: 100%;
  }

  /* 按钮触摸区域 */
  .btn-icon-danger {
    min-width: 44px;
    min-height: 44px;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  /* hover 预览改为始终显示 */
  .history-item-main:hover .history-item-preview {
    color: var(--color-text-muted);
  }

  /* 表格横向滚动 */
  .valuation-table-wrap {
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
  }

  /* 网格布局调整 */
  .valuation-cards {
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 0.75rem;
  }

  /* 指标选择器 */
  .metric-tabs {
    flex-wrap: wrap;
    gap: 0.5rem;
  }

  .metric-tab {
    padding: 0.5rem 0.75rem;
    font-size: 0.8rem;
    min-height: 40px;
  }

  /* 图表容器 */
  .chart-container {
    height: 250px;
  }

  /* 估值卡片 */
  .valuation-grid {
    grid-template-columns: 1fr 1fr;
    gap: 0.75rem;
  }

  .valuation-item {
    padding: 0.75rem;
  }

  .valuation-label {
    font-size: 0.75rem;
  }

  .valuation-value {
    font-size: 1.1rem;
  }
}
</style>
