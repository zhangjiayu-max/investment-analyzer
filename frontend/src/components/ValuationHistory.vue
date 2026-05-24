<script setup>
import { ref, watch, onMounted, onUnmounted, computed } from 'vue'
import * as echarts from 'echarts'
import { marked } from 'marked'
import { listValuationIndexes, getValuationHistory, runAnalysis, listAnalysisHistory, getAnalysisHistoryDetail, deleteAnalysisHistory } from '../api'
import { isDark } from '../composables/useTheme'

const indexes = ref([])
const selectedCode = ref('')   // 当前选中的 index_code
const selectedMetric = ref('') // 当前选中的 metric_type
const history = ref([])
const latest = ref(null)
const loading = ref(false)
const trendChartRef = ref(null)
let trendChart = null

// ── AI 分析相关 ──────────────────────────────────────
const activeTab = ref('valuation') // 'valuation' | 'analysis'
const analysisLoading = ref(false)
const analysisResult = ref(null) // 当前分析结果 {id, result, agent_name, token_usage, created_at}
const analysisHistory = ref([])
const historyLoading = ref(false)
const viewingHistory = ref(null) // 正在查看的历史详情
const showConfirmRun = ref(false)

// 搜索相关
const searchQuery = ref('')
const dropdownOpen = ref(false)

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
}

function selectMetric(metric) {
  selectedMetric.value = metric
}

const selectedIndexName = computed(() => {
  if (!selectedCode.value) return ''
  const idx = indexes.value.find(i => i.index_code === selectedCode.value)
  return idx?.index_name || selectedCode.value
})

function renderMarkdown(text) {
  try { return marked(text || '') } catch { return text || '' }
}

async function handleRunAnalysis() {
  if (!selectedCode.value) return
  showConfirmRun.value = false
  analysisLoading.value = true
  analysisResult.value = null
  try {
    const { data } = await runAnalysis(selectedCode.value, selectedIndexName.value)
    analysisResult.value = {
      id: data.id,
      result: data.result,
      agent_name: '市场日报分析师',
      token_usage: data.token_usage || 0,
      created_at: new Date().toISOString(),
    }
    // 刷新历史列表
    loadAnalysisHistory()
  } catch (e) {
    console.error('Analysis failed:', e)
    analysisResult.value = { result: '分析失败：' + (e.response?.data?.detail || e.message), error: true }
  } finally {
    analysisLoading.value = false
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
  if (!confirm('确定删除这条分析记录？')) return
  try {
    await deleteAnalysisHistory(id)
    analysisHistory.value = analysisHistory.value.filter(h => h.id !== id)
    if (viewingHistory.value?.id === id) viewingHistory.value = null
  } catch (e) {
    console.error('Failed to delete:', e)
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

  const gridColor = isDark.value ? '#334155' : '#f1f5f9'
  const textColor = isDark.value ? '#94a3b8' : '#64748b'
  const metricName = latest.value?.metric_type || '估值'

  trendChart.setOption({
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      backgroundColor: isDark.value ? '#334155' : '#ffffff',
      borderColor: isDark.value ? '#475569' : '#e2e8f0',
      textStyle: { color: isDark.value ? '#f1f5f9' : '#0f172a', fontSize: 12 },
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
        lineStyle: { width: 2, color: '#6366f1' },
        itemStyle: { color: '#6366f1' },
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
  document.addEventListener('click', handleOutsideClick)
})
onUnmounted(() => {
  document.removeEventListener('click', handleOutsideClick)
})
watch(selectedMetric, loadHistory)
watch(activeTab, (tab) => {
  if (tab === 'analysis' && selectedCode.value) loadAnalysisHistory()
})

defineExpose({ loadHistory })
</script>

<template>
  <div class="valuation-page">
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
    </div>

    <!-- Current Index Header -->
    <div v-if="selectedCode" class="card index-header-card">
      <div class="index-header-left">
        <div class="index-header-name">{{ selectedIndexName }}</div>
        <div class="index-header-code">{{ selectedCode }}</div>
        <div v-if="latest" class="index-header-metrics">
          <span class="hdr-metric">{{ latest.metric_type || 'PE' }}: <b>{{ latest.current_value ?? '-' }}</b></span>
          <span class="hdr-divider">|</span>
          <span class="hdr-metric">百分位: <b :class="getPercentileLevel(latest.percentile) === 'success' ? 'val-low' : getPercentileLevel(latest.percentile) === 'danger' ? 'val-high' : ''">{{ latest.percentile != null ? latest.percentile + '%' : '-' }}</b></span>
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
            <button class="btn btn-primary" @click="handleRunAnalysis">确认分析</button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- Main Tab Bar -->
    <div v-if="selectedCode" class="tab-bar">
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
      <svg width="48" height="48" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/>
      </svg>
      <p>暂无估值数据，请先解析图片</p>
    </div>

    <!-- ════════ Valuation Tab Content ════════ -->
    <template v-if="activeTab === 'valuation' && latest">

      <!-- Metric Cards -->
      <div class="metric-grid">
        <div class="card metric-card">
          <div class="metric-label">当前 {{ latest.metric_type || '值' }}</div>
          <div class="metric-value">{{ latest.current_value ?? '-' }}</div>
        </div>
        <div :class="['card', 'metric-card', 'metric-' + getPercentileLevel(latest.percentile)]">
          <div class="metric-label">分位点</div>
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
      <div v-if="sourceImageUrl" class="card source-image-card">
        <h3 class="card-title">来源图片 <span v-if="latest.snapshot_date" class="count-badge">{{ latest.snapshot_date }}</span></h3>
        <div class="source-image-wrap" @click="showLightbox = true">
          <img :src="sourceImageUrl" alt="估值来源图" class="source-image" loading="lazy" />
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

      <!-- Valuation Range Bar -->
      <div class="card range-card">
        <div class="range-header">
          <span class="range-title">{{ latest.metric_type || '估值' }} 估值区间</span>
          <span class="range-zscore" v-if="latest.zscore != null">Z分数: {{ latest.zscore }}</span>
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
              <span class="label-key val-low">机会</span>
              <span class="label-val val-low">{{ latest.opportunity_value ?? '-' }}</span>
            </div>
            <div class="gauge-label">
              <span class="label-key">中位</span>
              <span class="label-val">{{ latest.median ?? '-' }}</span>
            </div>
            <div class="gauge-label">
              <span class="label-key val-high">危险</span>
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
                <th>指标</th>
                <th>当前值</th>
                <th>点位</th>
                <th>分位点</th>
                <th>机会值</th>
                <th>中位数</th>
                <th>危险值</th>
                <th>Z分数</th>
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
            <div class="loading-title">AI 正在分析中...</div>
            <div class="loading-desc">正在检索最新财经新闻并生成分析报告，请稍候</div>
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
          暂无分析记录，点击上方「AI 市场分析」按钮开始
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
  </div>
</template>

<style scoped>
.valuation-page {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.card-title {
  font-size: 0.95rem;
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0 0 1rem 0;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.count-badge {
  font-size: 0.7rem;
  font-weight: 500;
  background: var(--color-bg-input);
  color: var(--color-text-muted);
  padding: 0.1rem 0.4rem;
  border-radius: 999px;
}

/* Selector */
.selector-card {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.75rem 1rem;
}

.selector-label {
  font-size: 0.8rem;
  font-weight: 500;
  color: var(--color-text-secondary);
  white-space: nowrap;
}

.searchable-select {
  position: relative;
  flex: 1;
  max-width: 400px;
}

.select-search-input {
  width: 100%;
  font-size: 0.85rem;
}

.select-dropdown {
  position: absolute;
  top: 100%;
  left: 0;
  right: 0;
  z-index: 100;
  margin-top: 4px;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-lg);
  max-height: 280px;
  overflow-y: auto;
}

.select-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.55rem 0.75rem;
  font-size: 0.85rem;
  cursor: pointer;
  transition: background 0.15s;
}

.select-option:hover { background: var(--color-bg-hover); }
.select-option.active { background: var(--color-primary-50); color: var(--color-primary-600); }

.option-name { font-weight: 500; }
.option-code { font-size: 0.75rem; color: var(--color-text-muted); font-family: monospace; }

.select-empty {
  padding: 1rem;
  text-align: center;
  font-size: 0.8rem;
  color: var(--color-text-muted);
}

.selector-meta {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  white-space: nowrap;
}

/* Metric Tabs */
.metric-tabs {
  display: flex;
  gap: 0.5rem;
  padding: 0 0.25rem;
}

.metric-tab {
  font-size: 0.8rem;
  font-weight: 500;
  padding: 0.4rem 1rem;
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border);
  background: var(--color-bg-card);
  color: var(--color-text-secondary);
  cursor: pointer;
  transition: all 0.2s;
}

.metric-tab:hover {
  border-color: var(--color-primary-300);
  color: var(--color-primary-600);
}

.metric-tab.active {
  background: var(--color-primary-500);
  color: white;
  border-color: var(--color-primary-500);
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

.empty-state p { font-size: 0.875rem; margin: 0; }

/* Metric Grid */
.metric-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 0.75rem;
}

.metric-card {
  text-align: center;
  padding: 1rem;
}

.metric-label {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  margin-bottom: 0.35rem;
}

.metric-value {
  font-size: 1.25rem;
  font-weight: 700;
  color: var(--color-text-primary);
}

.metric-success .metric-value { color: #059669; }
.metric-warning .metric-value { color: #d97706; }
.metric-danger .metric-value { color: #dc2626; }

.val-low { color: #059669; }
.val-high { color: #dc2626; }

/* Range / Gauge */
.range-card { padding: 1.25rem; }

.range-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 1.25rem;
}

.range-title {
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--color-text-primary);
}

.range-zscore {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  background: var(--color-bg-input);
  padding: 0.2rem 0.5rem;
  border-radius: var(--radius-sm);
}

.gauge-container {
  padding: 0 0.5rem;
}

.gauge-bar {
  position: relative;
  height: 36px;
  border-radius: 18px;
  overflow: visible;
  display: flex;
  box-shadow: inset 0 2px 4px rgba(0,0,0,0.06);
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
  border-radius: 18px 0 0 18px;
}

.zone-mid {
  background: linear-gradient(90deg, #fef08a, #fde68a);
}

.zone-high {
  background: linear-gradient(90deg, #fca5a5, #f87171);
  border-radius: 0 18px 18px 0;
}

.zone-label {
  font-size: 0.7rem;
  font-weight: 600;
  color: rgba(0,0,0,0.4);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.gauge-marker {
  position: absolute;
  top: -8px;
  transform: translateX(-50%);
  z-index: 10;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.marker-label {
  background: var(--color-primary-600);
  color: white;
  font-size: 0.7rem;
  font-weight: 700;
  padding: 0.2rem 0.5rem;
  border-radius: var(--radius-sm);
  white-space: nowrap;
  box-shadow: 0 2px 6px rgba(79, 70, 229, 0.4);
}

.marker-arrow {
  width: 0;
  height: 0;
  border-left: 5px solid transparent;
  border-right: 5px solid transparent;
  border-top: 5px solid var(--color-primary-600);
}

.gauge-labels {
  display: flex;
  justify-content: space-between;
  margin-top: 0.75rem;
  padding: 0 0.25rem;
}

.gauge-label {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.15rem;
}

.label-key {
  font-size: 0.65rem;
  color: var(--color-text-muted);
  font-weight: 500;
}

.label-val {
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--color-text-primary);
}

.range-avg {
  text-align: center;
  font-size: 0.75rem;
  color: var(--color-text-muted);
  margin-top: 0.75rem;
  padding-top: 0.5rem;
  border-top: 1px solid var(--color-border-light);
}

/* Trend Chart */
.trend-chart { height: 280px; }

/* Hint Card */
.hint-card {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 1rem 1.25rem;
  color: var(--color-text-muted);
  font-size: 0.8rem;
}

/* Table */
.table-wrap { overflow-x: auto; }

.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.8rem;
}

.data-table th {
  text-align: left;
  padding: 0.6rem 0.6rem;
  font-weight: 600;
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--color-text-muted);
  border-bottom: 1px solid var(--color-border);
  white-space: nowrap;
}

.data-table td {
  padding: 0.5rem 0.6rem;
  color: var(--color-text-secondary);
  border-bottom: 1px solid var(--color-border-light);
}

.data-table tbody tr:hover { background: var(--color-bg-hover); }

.td-date { font-weight: 500; color: var(--color-text-primary); white-space: nowrap; }
.td-val { font-weight: 600; }

/* Source Image */
.source-image-card { padding: 1.25rem; }

.source-image-wrap {
  position: relative;
  cursor: pointer;
  border-radius: var(--radius-md);
  overflow: hidden;
  max-height: 400px;
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
  gap: 1rem;
  padding: 1rem 1.25rem;
}

.index-header-left {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  flex-wrap: wrap;
}

.index-header-name {
  font-size: 1.1rem;
  font-weight: 700;
  color: var(--color-text-primary);
}

.index-header-code {
  font-size: 0.75rem;
  font-family: monospace;
  color: var(--color-text-muted);
  background: var(--color-bg-input);
  padding: 0.15rem 0.4rem;
  border-radius: var(--radius-sm);
}

.index-header-metrics {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.8rem;
  color: var(--color-text-secondary);
}

.hdr-metric b { font-weight: 600; }
.hdr-divider { color: var(--color-border); }

.hdr-status {
  font-size: 0.7rem;
  font-weight: 600;
  padding: 0.15rem 0.5rem;
  border-radius: 999px;
}

.status-success { background: rgba(16,185,129,0.1); color: #059669; }
.status-warning { background: rgba(245,158,11,0.1); color: #d97706; }
.status-danger { background: rgba(239,68,68,0.1); color: #dc2626; }
.status-neutral { background: var(--color-bg-input); color: var(--color-text-muted); }

.btn-ai-analysis {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  white-space: nowrap;
  flex-shrink: 0;
}

/* ── Tab Bar ──────────────────────────────────────── */
.tab-bar {
  display: flex;
  gap: 0;
  border-bottom: 1px solid var(--color-border);
  padding-bottom: 0;
}

.tab-btn {
  padding: 0.5rem 1.25rem;
  font-size: 0.85rem;
  font-weight: 500;
  color: var(--color-text-muted);
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  cursor: pointer;
  transition: all 0.2s;
}

.tab-btn:hover {
  color: var(--color-text-secondary);
}

.tab-btn.active {
  color: var(--color-primary-600);
  border-bottom-color: var(--color-primary-500);
}

/* ── Analysis Loading ─────────────────────────────── */
.analysis-loading-card {
  padding: 2.5rem;
}

.analysis-loading-inner {
  display: flex;
  align-items: center;
  gap: 1rem;
}

.loading-title {
  font-size: 0.95rem;
  font-weight: 600;
  color: var(--color-text-primary);
}

.loading-desc {
  font-size: 0.8rem;
  color: var(--color-text-muted);
  margin-top: 0.25rem;
}

/* ── Analysis Result ──────────────────────────────── */
.analysis-result-card {
  padding: 1.5rem;
}

.analysis-result-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 1rem;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.analysis-result-header .card-title {
  margin: 0;
}

.analysis-meta {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  font-size: 0.75rem;
  color: var(--color-text-muted);
}

.meta-item {
  display: flex;
  align-items: center;
  gap: 0.25rem;
}

.analysis-content {
  font-size: 0.875rem;
  line-height: 1.7;
  color: var(--color-text-secondary);
}

/* ── Markdown Body ────────────────────────────────── */
.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3) {
  color: var(--color-text-primary);
  margin-top: 1.25em;
  margin-bottom: 0.5em;
}

.markdown-body :deep(h1) { font-size: 1.25rem; }
.markdown-body :deep(h2) { font-size: 1.1rem; }
.markdown-body :deep(h3) { font-size: 1rem; }

.markdown-body :deep(p) {
  margin: 0.5em 0;
}

.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  padding-left: 1.5em;
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
  padding: 2rem;
  color: var(--color-text-muted);
  font-size: 0.85rem;
}

.history-list {
  display: flex;
  flex-direction: column;
}

.history-item {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
  padding: 0.85rem 0;
  border-bottom: 1px solid var(--color-border-light);
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
  gap: 0.5rem;
  margin-bottom: 0.3rem;
  flex-wrap: wrap;
}

.history-date {
  font-size: 0.8rem;
  font-weight: 500;
  color: var(--color-text-primary);
}

.history-index {
  font-size: 0.7rem;
  background: var(--color-primary-50);
  color: var(--color-primary-600);
  padding: 0.1rem 0.4rem;
  border-radius: 999px;
}

.history-agent {
  font-size: 0.7rem;
  color: var(--color-text-muted);
}

.history-item-preview {
  font-size: 0.78rem;
  color: var(--color-text-muted);
  line-height: 1.5;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  transition: color 0.15s;
}

.history-item-actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-shrink: 0;
}

.history-tokens {
  font-size: 0.7rem;
  color: var(--color-text-muted);
  white-space: nowrap;
}

.btn-icon-danger {
  background: none;
  border: none;
  color: var(--color-text-muted);
  cursor: pointer;
  padding: 0.25rem;
  border-radius: var(--radius-sm);
  transition: all 0.15s;
}

.btn-icon-danger:hover {
  color: #dc2626;
  background: rgba(239,68,68,0.08);
}

/* ── Modal ────────────────────────────────────────── */
.modal-overlay {
  position: fixed;
  inset: 0;
  z-index: var(--z-modal);
  background: rgba(0,0,0,0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 2rem;
}

.modal-dialog {
  background: var(--color-bg-card);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  padding: 2rem;
  max-width: 500px;
  width: 100%;
  max-height: 80vh;
  overflow-y: auto;
}

.modal-lg {
  max-width: 780px;
}

.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 1rem;
}

.modal-header .modal-title { margin: 0; }

.modal-close {
  background: none;
  border: none;
  font-size: 1.5rem;
  color: var(--color-text-muted);
  cursor: pointer;
  padding: 0.25rem 0.5rem;
  line-height: 1;
}

.modal-close:hover { color: var(--color-text-primary); }

.modal-title {
  font-size: 1.1rem;
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0 0 0.75rem;
}

.modal-desc {
  font-size: 0.85rem;
  color: var(--color-text-secondary);
  margin: 0 0 0.5rem;
  line-height: 1.5;
}

.modal-warn {
  font-size: 0.8rem;
  color: #d97706;
  margin: 0 0 1.5rem;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.75rem;
}

.modal-meta-bar {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  font-size: 0.75rem;
  color: var(--color-text-muted);
  padding-bottom: 1rem;
  border-bottom: 1px solid var(--color-border-light);
  margin-bottom: 1rem;
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
</style>
