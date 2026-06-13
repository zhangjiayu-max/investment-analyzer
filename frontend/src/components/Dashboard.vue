<script setup>
import { ref, computed, onMounted, onActivated } from 'vue'
import { getDashboard, runAnalysis, runPanoramaAnalysis, getHotTopics, getDailyReport, regenerateDailyReport, submitDailyReportFeedback, listPanoramaRecords, getHotspotsAnalysis, getLatestHotspotsAnalysis, getRecommendations, getRecommendationStats, submitRecommendationFeedback, getBondRecommend, listBondRecommendRecords, autoVerifyRecommendations, fetchRecentValuations, getBondMarketTemperature, getHotspotsRelate, getRebalancingSuggestion } from '../api'
import GaugeChart from './charts/GaugeChart.vue'
import ConfirmDialog from './ConfirmDialog.vue'
import AppToast from './AppToast.vue'
import Skeleton from './ui/Skeleton.vue'
import EmptyState from './ui/EmptyState.vue'
import { useToast } from '../composables/useToast'
import { renderMarkdown } from '../composables/useMarkdown'
import DOMPurify from 'dompurify'

const { showToast } = useToast()

const emit = defineEmits(['navigate'])

// ── 二次确认弹窗 ──
const confirm = ref({ visible: false, title: '', message: '', danger: false, onConfirm: null })

const bondTemperature = ref(null)

async function loadBondTemperature() {
  try {
    const { data } = await getBondMarketTemperature()
    bondTemperature.value = data?.current || null
  } catch (_) {}
}

function getBondTempLabel(temp) {
  if (temp == null) return ''
  if (temp <= 30) return '低温（适合买入）'
  if (temp <= 50) return '偏低'
  if (temp <= 70) return '偏高'
  return '高温（谨慎买入）'
}

const loading = ref(true)
const fetchingValuation = ref(false)
const error = ref(null)
const data = ref(null)

// ── 市场热点（自动加载+AI增强） ──
const hotTopics = ref(null)
const hotTopicsFetchedAt = ref('')
const hotTopicsLoading = ref(true)
const hotspotLoading = ref(false)
const hotspotError = ref(false)
const hotspotsRelate = ref(null) // 热点→指数关联数据

// 聚合所有关联行业和持仓
const allSectors = computed(() => {
  if (!hotspotsRelate.value) return []
  const set = new Set()
  for (const item of hotspotsRelate.value) {
    for (const s of (item.sectors || [])) set.add(s)
  }
  return [...set]
})
const allRelatedHoldings = computed(() => {
  if (!hotspotsRelate.value) return []
  const seen = new Set()
  const result = []
  for (const item of hotspotsRelate.value) {
    for (const h of (item.related_holdings || [])) {
      if (!seen.has(h.fund_code)) {
        seen.add(h.fund_code)
        result.push(h)
      }
    }
  }
  return result
})

// ── 每日日报自动加载 ──
const dailyReport = ref(null)
const dailyReportLoading = ref(true)
const dailyReportRegenerating = ref(false)
const showBriefing = ref(true)
const briefingFeedback = ref({ rating: null, sending: false, sent: false, showComment: false, comment: '' })

function formatBriefingTime(ts) {
  if (!ts) return ''
  const d = new Date(ts.replace(' ', 'T'))
  if (isNaN(d)) return ts
  return `${d.getMonth() + 1}月${d.getDate()}日 ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

function renderBriefing(text) {
  if (!text) return ''
  const html = text
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/^\s*[-*]\s+(.*)/gm, '<li>$1</li>')
    .replace(/(<li>.*<\/li>\n?)+/gs, m => `<ul>${m}</ul>`)
    .replace(/\n/g, '<br>')
  return DOMPurify.sanitize(html)
}

function confirmRegenerateReport() {
  confirm.value = {
    visible: true,
    title: '重新生成简报',
    message: '将使用「市场日报分析师」重新生成今日市场简报，是否继续？',
    danger: false,
    onConfirm: async () => {
      confirm.value.visible = false
      dailyReportRegenerating.value = true
      try {
        const { data } = await regenerateDailyReport()
        if (data.ok) {
          const { data: res } = await getDailyReport()
          if (res?.has_report) dailyReport.value = res.report
        }
      } catch (e) {
        console.error('重新生成简报失败:', e)
      } finally {
        dailyReportRegenerating.value = false
      }
    }
  }
}

function toggleBriefingFeedback(rating) {
  if (briefingFeedback.value.sending) return
  // 再次点击相同按钮 = 取消
  if (briefingFeedback.value.sent && briefingFeedback.value.rating === rating) {
    briefingFeedback.value = { rating: null, sending: false, sent: false, showComment: false, comment: '' }
    return
  }
  // 切换 rating
  briefingFeedback.value.rating = rating
  briefingFeedback.value.sent = false
  // 点踩显示输入框，点赞直接提交
  if (rating === 'unhelpful') {
    briefingFeedback.value.showComment = true
  } else {
    submitBriefingFeedback('helpful')
  }
}

async function submitBriefingFeedback(rating, comment = '') {
  briefingFeedback.value.sending = true
  try {
    const reportSummary = dailyReport.value?.result?.substring(0, 500) || ''
    await submitDailyReportFeedback({ rating, comment, reportSummary })
    briefingFeedback.value.sent = true
    briefingFeedback.value.showComment = false
  } catch (e) {
    console.error('反馈提交失败:', e)
  } finally {
    briefingFeedback.value.sending = false
  }
}

async function submitBriefingFeedbackWithComment() {
  const comment = briefingFeedback.value.comment.trim()
  if (!comment) return
  await submitBriefingFeedback('unhelpful', comment)
}

// ── 全景诊断 AI 分析 ──

onMounted(async () => {
  // 先加载缓存的最新热点分析结果（要在 loadDailyReport 之前，防止被覆盖）
  try {
    const { data: latest } = await getLatestHotspotsAnalysis()
    if (latest?.recommendations?.length) {
      hotspotsAnalysis.value = latest
    }
  } catch (_) {}

  // 自动验证 pending 推荐（在加载历史之前，确保 stats 是最新的）
  try { await autoVerifyRecommendations() } catch (_) {}

  await Promise.all([
    loadDashboard(),
    loadHotTopics(),
    loadDailyReport(),
    loadRecHistory(),
    loadBondTemperature(),
  ])

  // 自动加载最新全景诊断结果
  try {
    const { data: recs } = await listPanoramaRecords(1)
    console.log('panorama records:', recs)
    if (recs?.records?.length) {
      panoramaResult.value = recs.records[0]
      console.log('panoramaResult set:', panoramaResult.value)
    }
  } catch (e) {
    console.error('load panorama failed:', e)
  }

  // 自动加载调仓分析（快速，无需等待 AI）
  try {
    const { data: res } = await getRebalancingSuggestion()
    if (res && !res.error) {
      rebalanceResult.value = res
      showRebalance.value = true
    }
  } catch (_) {}

  // 自动加载最新债券推荐结果
  try {
    const { data: bondRecs } = await listBondRecommendRecords(1)
    if (bondRecs?.records?.length) {
      const rec = bondRecs.records[0]
      bondResult.value = typeof rec.result_data === 'string' ? JSON.parse(rec.result_data) : rec.result_data
    }
  } catch (e) {
    console.error('load bond recommend failed:', e)
  }
})

// KeepAlive 组件激活时重新加载数据（切换页面回来时触发）
onActivated(async () => {
  await Promise.all([
    loadDashboard(),
    loadHotTopics(),
    loadDailyReport(),
    loadRecHistory(),
    loadBondTemperature(),
  ])
})

async function loadDailyReport() {
  dailyReportLoading.value = true
  try {
    const { data: res } = await getDailyReport()
    if (res?.has_report && res?.report) {
      dailyReport.value = res.report
    }
  } catch (e) {
    // 静默失败
  } finally {
    dailyReportLoading.value = false
  }
}

async function loadHotTopics() {
  hotTopicsLoading.value = true
  try {
    const { data: res } = await getHotTopics()
    if (res?.news?.length) {
      hotTopics.value = res.news
      // 只在没有本地分析时间时，才用后端返回的时间
      if (!hotTopicsAnalyzedAt.value) {
        hotTopicsFetchedAt.value = res.fetched_at || ''
      }
      // 自动加载热点→指数关联
      loadHotspotsRelate()
    }
  } catch (e) {
    // 静默失败，不影响看板主流程
  } finally {
    hotTopicsLoading.value = false
  }
}

async function loadHotspotsRelate() {
  try {
    const { data } = await getHotspotsRelate()
    hotspotsRelate.value = data.items || []
  } catch (_) {}
}

// 本地记录的热点分析时间
const hotTopicsAnalyzedAt = ref('')

async function loadDashboard() {
  loading.value = true
  error.value = null
  try {
    const { data: res } = await getDashboard()
    data.value = res
  } catch (e) {
    error.value = e.response?.data?.detail || e.message || '加载失败'
  } finally {
    loading.value = false
  }
}

async function handleFetchValuations() {
  fetchingValuation.value = true
  try {
    const { data: res } = await fetchRecentValuations()
    // 只更新低估指数数据，不重新加载整个 Dashboard（避免页面闪 loading）
    const { data: dashData } = await getDashboard()
    if (data.value) {
      data.value.undervalued_indexes = dashData.undervalued_indexes
      data.value.undervalued_data_date = dashData.undervalued_data_date
      data.value.undervalued_updated_at = res.checked_at || dashData.undervalued_updated_at
    }
  } catch (e) {
    console.error('抓取估值失败:', e)
  } finally {
    fetchingValuation.value = false
  }
}

// ── 估值评估颜色 ──
const assessmentColors = {
  extreme: { bg: '#dc2626', label: '极度低估' },
  undervalued: { bg: '#f59e0b', label: '低估' },
  slightly_low: { bg: '#10b981', label: '偏低' },
  fair: { bg: '#6b7280', label: '合理' },
  slightly_high: { bg: '#f59e0b', label: '偏高' },
  overvalued: { bg: '#ef4444', label: '高估' },
  extreme_high: { bg: '#dc2626', label: '极度高估' },
}

function getPercentileColor(p) {
  if (p <= 10) return '#dc2626'
  if (p <= 25) return '#f59e0b'
  if (p <= 40) return '#10b981'
  if (p <= 60) return '#6b7280'
  if (p <= 80) return '#f59e0b'
  return '#ef4444'
}

function formatMoney(v) {
  if (v == null) return '—'
  if (Math.abs(v) >= 1e8) return (v / 1e8).toFixed(2) + '亿'
  if (Math.abs(v) >= 1e4) return (v / 1e4).toFixed(2) + '万'
  return v.toFixed(2)
}

function formatPct(v) {
  if (v == null) return '—'
  return (v * 100).toFixed(1) + '%'
}

// ── 推荐验证历史 ──
const recHistory = ref(null)
const recStats = ref(null)
const showVerify = ref(false)

async function loadRecHistory() {
  try {
    const [histRes, statsRes] = await Promise.all([
      getRecommendations(20),
      getRecommendationStats(),
    ])
    recHistory.value = histRes.data?.recommendations || []
    recStats.value = statsRes.data
  } catch (_) {}
}

// ── 推荐反馈（进化系统） ──
const feedbackSending = ref({})

async function submitFeedback(rec, rating) {
  if (feedbackSending.value[rec.id]) return
  feedbackSending.value[rec.id] = rating
  try {
    await submitRecommendationFeedback(rec.id, { rating, comment: '' })
    showToast(rating === 'helpful' ? '已标记有用' : '已标记反馈', 'success')
  } catch (e) {
    showToast('提交失败', 'error')
  } finally {
    setTimeout(() => { feedbackSending.value[rec.id] = false }, 2000)
  }
}

// ── 生成市场热点（结构化推荐） ──
const hotspotsAnalysis = ref(null)
const bondLoading = ref(false)
const bondResult = ref(null)

async function handleBondRecommend() {
  bondLoading.value = true
  bondResult.value = null
  try {
    const { data: res } = await getBondRecommend()
    bondResult.value = res.result
    // 重新加载 dashboard 获取债券数据的实际日期
    await loadDashboard()
  } catch (e) {
    showToast('AI 债券推荐失败：' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    bondLoading.value = false
  }
}

function confirmBondRecommend() {
  confirm.value = {
    visible: true,
    title: 'AI 债券推荐',
    message: '将使用「债券配置顾问」分析债券市场并生成配置建议，是否继续？',
    danger: false,
    onConfirm: () => { confirm.value.visible = false; handleBondRecommend() }
  }
}

async function generateHotspots() {
  hotspotLoading.value = true
  hotspotsAnalysis.value = null
  hotspotError.value = false
  try {
    const { data } = await getHotspotsAnalysis()
    hotspotsAnalysis.value = data
    // 记录分析完成的时间
    hotTopicsAnalyzedAt.value = new Date().toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
    hotTopicsFetchedAt.value = hotTopicsAnalyzedAt.value
    // 生成后自动验证旧的 pending 推荐，并刷新统计
    try { await autoVerifyRecommendations() } catch (_) {}
    loadRecHistory()
  } catch (e) {
    hotspotError.value = true
    hotspotsAnalysis.value = {
      summary: '分析失败',
      recommendations: [],
      analysis_text: e.response?.data?.detail || e.message,
    }
  } finally {
    hotspotLoading.value = false
  }
}

function confirmHotspots() {
  confirm.value = {
    visible: true,
    title: '今日热门机会',
    message: '将使用「热点分析专家」分析市场热点并生成投资建议，是否继续？',
    danger: false,
    onConfirm: () => { confirm.value.visible = false; generateHotspots() }
  }
}

// ── 再平衡 AI 分析 ──
const rebalanceLoading = ref(false)
const rebalanceResult = ref(null)
const showRebalance = ref(false)

// ── 全景诊断 AI 分析 ──
const panoramaLoading = ref(false)
const panoramaResult = ref(null)

async function generatePanorama() {
  panoramaLoading.value = true
  panoramaResult.value = null
  try {
    const { data: res } = await runPanoramaAnalysis()
    panoramaResult.value = res
    // 重新获取持仓健康度数据（不触发全局 loading）
    const { data: dashData } = await getDashboard()
    if (data.value && dashData.portfolio_health) {
      data.value.portfolio_health = dashData.portfolio_health
      data.value.portfolio_updated_at = dashData.portfolio_updated_at
    }
  } catch (e) {
    panoramaResult.value = { error: '分析失败: ' + (e.response?.data?.detail || e.message) }
  } finally {
    panoramaLoading.value = false
  }
}

// ── 生成再平衡建议 ──
async function generateRebalance() {
  rebalanceLoading.value = true
  rebalanceResult.value = null
  showRebalance.value = true
  try {
    const { data: res } = await getRebalancingSuggestion()
    rebalanceResult.value = res
  } catch (e) {
    rebalanceResult.value = { error: '分析失败: ' + (e.response?.data?.detail || e.message) }
  } finally {
    rebalanceLoading.value = false
  }
}

function handleRebalance() {
  if (rebalanceResult.value) {
    confirm.value = {
      visible: true,
      title: '刷新调仓分析',
      message: '将重新计算持仓偏离度和目标配比，是否继续？',
      danger: false,
      onConfirm: () => { confirm.value.visible = false; generateRebalance() }
    }
  } else {
    generateRebalance()
  }
}

// ── 辅助 ──
const concentrationColor = { low: '#10b981', moderate: '#f59e0b', high: '#ef4444' }

function _cmpTemp(prev, current) {
  if (prev == null || current == null) return '--'
  const diff = current - prev
  const arrow = diff > 0 ? '↑' : diff < 0 ? '↓' : '→'
  return `${prev}° → ${current}° ${arrow}${Math.abs(diff).toFixed(0)}°`
}
const concentrationIcon = { low: '✅', moderate: '⚡', high: '⚠️' }
</script>

<template>
  <div class="dashboard">
    <!-- Header -->
    <div class="dash-header">
      <div>
        <h2 class="page-title">每日投资决策看板</h2>
        <p class="page-desc">{{ data?.date || '加载中...' }} · 整合估值、持仓、零钱，辅助决策</p>
      </div>
    </div>

    <!-- Global error -->
    <div v-if="error && !loading" class="dash-error card">
      <svg width="24" height="24" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4.5c-.77-.833-2.694-.833-3.464 0L3.34 16.5c-.77.833.192 2.5 1.732 2.5z"/>
      </svg>
      <p>{{ error }}</p>
      <button class="btn-secondary" @click="loadDashboard">重试</button>
    </div>

    <!-- 数据新鲜度警告 -->
    <div v-if="data?.data_freshness?.stale_count > 0" class="stale-warning card">
      <svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24" style="flex-shrink:0; stroke: #f59e0b;">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>
      </svg>
      <div class="stale-warning-body">
        <span class="stale-warning-title">{{ data.data_freshness.stale_count }} 条指数估值数据超过 10 天未更新</span>
        <span class="stale-warning-list">{{ data.data_freshness.stale_indexes.map(i => i.name + '(' + i.stale_days + '天)').join('、') }}</span>
      </div>
      <button class="btn-ghost btn-sm" @click="emit('navigate', 'valuation')">查看 →</button>
    </div>

    <!-- 每日简报 -->
    <div v-if="dailyReport && !dailyReportLoading" class="briefing-card card">
      <div class="briefing-header" @click="showBriefing = !showBriefing">
        <div class="briefing-title-row">
          <svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24" class="briefing-icon">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M19 20H5a2 2 0 01-2-2V6a2 2 0 012-2h10a2 2 0 012 2v1m2 13a2 2 0 01-2-2V7m2 13a2 2 0 002-2V9a2 2 0 00-2-2h-2m-4-3H9M7 16h6M7 8h6v4H7V8z"/>
          </svg>
          <span class="briefing-label">每日市场简报</span>
          <span class="briefing-time">{{ formatBriefingTime(dailyReport.created_at) }}</span>
          <button
            class="btn-ai-action btn-briefing-gen"
            :class="{ 'btn-loading': dailyReportRegenerating }"
            :disabled="dailyReportRegenerating"
            @click.stop="confirmRegenerateReport"
          >
            <svg :class="['icon-spin', { 'spinning': dailyReportRegenerating }]" width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
            </svg>
            <span>重新生成</span>
            <span class="ai-agent-tooltip">市场日报分析师</span>
          </button>
        </div>
        <svg :class="['briefing-chevron', { 'briefing-chevron-up': showBriefing }]" width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/>
        </svg>
      </div>
      <Transition name="slide-fade">
        <div v-if="showBriefing" class="briefing-body" v-html="renderBriefing(dailyReport.result)"></div>
      </Transition>
      <div v-if="showBriefing" class="briefing-footer">
        <div class="briefing-feedback">
          <button
            class="rec-feedback-btn helpful"
            :class="{ active: briefingFeedback.rating === 'helpful' }"
            :disabled="briefingFeedback.sending"
            @click="toggleBriefingFeedback('helpful')"
            title="点赞"
          >
            <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 10h4.764a2 2 0 011.789 2.894l-3.5 7A2 2 0 0115.263 21h-4.017c-.163 0-.326-.02-.485-.06L7 20m7-10V5a2 2 0 00-2-2h-.095c-.5 0-.905.405-.905.905 0 .714-.211 1.412-.608 2.006L7 11v9m7-10h-2M7 20H5a2 2 0 01-2-2v-6a2 2 0 012-2h2.5"/>
            </svg>
          </button>
          <button
            class="rec-feedback-btn unhelpful"
            :class="{ active: briefingFeedback.rating === 'unhelpful' }"
            :disabled="briefingFeedback.sending"
            @click="toggleBriefingFeedback('unhelpful')"
            title="点踩"
          >
            <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 14H5.236a2 2 0 01-1.789-2.894l3.5-7A2 2 0 018.736 3h4.018c.163 0 .326.02.485.06L17 4m-7 10v5a2 2 0 002 2h.095c.5 0 .905-.405.905-.905 0-.714.211-1.412.608-2.006L17 13V4m-7 10h2m5-10h2a2 2 0 012 2v6a2 2 0 01-2 2h-2.5"/>
            </svg>
          </button>
          <span v-if="briefingFeedback.sent && !briefingFeedback.showComment" class="briefing-feedback-hint">感谢反馈</span>
        </div>
        <Transition name="slide-fade">
          <div v-if="briefingFeedback.showComment" class="briefing-comment-box">
            <input
              v-model="briefingFeedback.comment"
              class="briefing-comment-input"
              placeholder="请描述问题，帮助我们改进..."
              @keydown.enter="submitBriefingFeedbackWithComment"
            />
            <button
              class="briefing-comment-submit"
              :disabled="briefingFeedback.sending || !briefingFeedback.comment.trim()"
              @click="submitBriefingFeedbackWithComment"
            >提交</button>
          </div>
        </Transition>
      </div>
    </div>

    <!-- 债市温度仪表盘 -->
    <div v-if="!loading && bondTemperature" class="temp-gauges-row">
      <div class="temp-gauge-card card">
        <GaugeChart
          :value="bondTemperature.temperature ?? 0"
          title="债市温度"
          height="180px"
        />
        <div class="temp-gauge-label">
          <span class="temp-gauge-desc">{{ getBondTempLabel(bondTemperature.temperature) }}</span>
        </div>
      </div>
    </div>

    <!-- Loading Skeleton -->
    <div v-if="loading" class="dash-loading">
      <Skeleton variant="title" width="40%" />
      <Skeleton variant="text" :count="3" />
      <div class="dash-grid" style="margin-top:1rem">
        <Skeleton variant="card" />
        <Skeleton variant="card" />
        <Skeleton variant="card" />
        <Skeleton variant="card" />
      </div>
    </div>

    <!-- 2x2 Grid -->
    <div v-if="!loading" class="dash-grid">
      <!-- ── Card 1: 低估指数 ── -->
      <div class="dash-card card">
        <div class="card-header">
          <div class="card-title-row">
            <svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24" class="card-icon">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/>
            </svg>
            <span>今日低估指数</span>
          </div>
          <div class="card-header-actions">
            <span class="card-data-time">{{ data?.undervalued_updated_at || data?.undervalued_data_date || '' }}</span>
            <span v-if="data?.undervalued_indexes?.length" class="card-badge">{{ data.undervalued_indexes.length }}只</span>
            <button
              class="btn-ai-action btn-card-refresh"
              :class="{ 'btn-loading': fetchingValuation }"
              :disabled="fetchingValuation"
              @click="handleFetchValuations"
            >
              <svg :class="['icon-spin', { 'spinning': fetchingValuation }]" width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
              </svg>
              <span>{{ fetchingValuation ? '抓取中...' : '刷新' }}</span>
            </button>
          </div>
        </div>
        <div v-if="!data?.undervalued_indexes?.length" class="card-empty">
          <EmptyState
            icon="chart"
            title="暂无低估指数数据"
            description="通过文章导入估值数据后自动展示"
            action-text="去导入"
            @action="emit('navigate', 'articles')"
          />
        </div>
        <div v-else class="card-body">
          <!-- 摘要指标条 -->
          <div class="health-metrics">
            <div class="metric-item">
              <span class="metric-label">低估数量</span>
              <span class="metric-value">{{ data.undervalued_indexes.length }} 只</span>
            </div>
            <div class="metric-item">
              <span class="metric-label">最低百分位</span>
              <span class="metric-value" :style="{ color: getPercentileColor(data.undervalued_indexes[0]?.percentile) }">
                {{ data.undervalued_indexes[0]?.percentile }}%
              </span>
            </div>
            <div class="metric-item">
              <span class="metric-label">极度低估</span>
              <span class="metric-value" style="color: #dc2626">
                {{ data.undervalued_indexes.filter(i => i.assessment_level === 'extreme').length }} 只
              </span>
            </div>
          </div>

          <div v-for="idx in data.undervalued_indexes.slice(0, 10)" :key="idx.index_code" class="index-row" @click="emit('navigate', 'valuation')">
            <div class="index-info">
              <span class="index-name">{{ idx.index_name || idx.index_code }}</span>
              <span class="index-meta">{{ idx.metric_type }} {{ idx.current_value }}</span>
            </div>
            <div class="index-percentile">
              <div class="percentile-bar-bg">
                <div class="percentile-bar" :style="{ width: idx.percentile + '%', background: getPercentileColor(idx.percentile) }"></div>
                <div class="percentile-marks">
                  <span class="mark" style="left:20%">20</span>
                  <span class="mark" style="left:40%">40</span>
                  <span class="mark" style="left:60%">60</span>
                  <span class="mark" style="left:80%">80</span>
                </div>
              </div>
              <div class="percentile-value" :style="{ color: getPercentileColor(idx.percentile) }">
                {{ idx.percentile }}%
              </div>
            </div>
            <span class="assessment-tag" :style="{ background: (assessmentColors[idx.assessment_level] || assessmentColors.fair).bg + '18', color: (assessmentColors[idx.assessment_level] || assessmentColors.fair).bg }">
              {{ idx.assessment }}
            </span>
          </div>
          <button class="btn-link" @click="emit('navigate', 'valuation')">查看全部估值 →</button>
        </div>
      </div>

      <!-- ── Card 2: 持仓健康度 + 再平衡 ── -->
      <div class="dash-card card">
        <div class="card-header">
          <div class="card-title-row">
            <svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24" class="card-icon">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M2 7l10-4 10 4-10 4-10-4zM2 17l10 4 10-4M2 12l10 4 10-4"/>
            </svg>
            <span>持仓健康度</span>
          </div>
          <div class="card-header-actions">
            <span v-if="data?.portfolio_health" class="card-data-time">{{ data?.portfolio_updated_at || '' }}</span>
            <button
              v-if="data?.portfolio_health"
              class="btn-ai-action"
              :class="{ 'btn-loading': rebalanceLoading }"
              :disabled="rebalanceLoading"
              @click="handleRebalance"
            >
              <svg :class="['icon-spin', { 'spinning': rebalanceLoading }]" width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
              </svg>
              <span>{{ rebalanceResult ? '重新生成' : 'AI 再平衡建议' }}</span>
              <span class="ai-agent-tooltip">全景诊断分析师</span>
            </button>
            <button
              v-if="data?.portfolio_health"
              class="btn-ai-action"
              :class="{ 'btn-loading': panoramaLoading }"
              :disabled="panoramaLoading"
              @click="generatePanorama"
            >
              <svg :class="['icon-spin', { 'spinning': panoramaLoading }]" width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
              </svg>
              <span>{{ panoramaLoading ? '分析中...' : 'AI 全景诊断' }}</span>
              <span class="ai-agent-tooltip">全景诊断分析师</span>
            </button>
          </div>
        </div>
        <div v-if="!data?.portfolio_health" class="card-empty">
          <EmptyState
            icon="briefcase"
            title="暂无持仓数据"
            description="在持仓管理中添加基金后自动分析"
            action-text="去添加"
            @action="emit('navigate', 'portfolio')"
          />
        </div>
        <div v-else class="card-body">
          <div class="health-metrics">
            <div class="metric-item">
              <span class="metric-label">持有基金</span>
              <span class="metric-value">{{ data.portfolio_health.holding_count }} 只</span>
            </div>
            <div class="metric-item">
              <span class="metric-label">总市值</span>
              <span class="metric-value">{{ formatMoney(data.portfolio_health.total_value) }}</span>
            </div>
            <div class="metric-item">
              <span class="metric-label">总盈亏</span>
              <span :class="['metric-value', (data.portfolio_health.total_profit || 0) >= 0 ? 'profit' : 'loss']">
                {{ formatMoney(data.portfolio_health.total_profit) }}
                ({{ (data.portfolio_health.profit_rate * 100).toFixed(1) }}%)
              </span>
            </div>
          </div>

          <!-- 集中度 -->
          <div class="concentration-row">
            <span class="concentration-icon">{{ concentrationIcon[data.portfolio_health.concentration_level] || '✅' }}</span>
            <div>
              <div class="concentration-text">{{ data.portfolio_health.concentration_assessment }}</div>
              <div class="concentration-bar-bg">
                <div
                  class="concentration-bar"
                  :style="{ width: Math.min(data.portfolio_health.top3_concentration, 100) + '%', background: concentrationColor[data.portfolio_health.concentration_level] || '#10b981' }"
                ></div>
              </div>
            </div>
          </div>

          <!-- 类型分布 -->
          <div v-if="Object.keys(data.portfolio_health.type_distribution || {}).length" class="type-dist">
            <div v-for="(v, k) in data.portfolio_health.type_distribution" :key="k" class="type-item">
              <span class="type-name">{{ k }}</span>
              <div class="type-bar-bg">
                <div class="type-bar" :style="{ width: Math.min(v / (data.portfolio_health.total_value || 1) * 100, 100) + '%' }"></div>
              </div>
              <span class="type-pct">{{ (v / (data.portfolio_health.total_value || 1) * 100).toFixed(0) }}%</span>
            </div>
          </div>

          <!-- AI 再平衡建议 -->
          <div v-if="showRebalance" class="rebalance-section">
            <div v-if="rebalanceLoading" class="card-loading">
              <div class="spinner"></div>
              <p>正在分析偏离度...</p>
            </div>
            <div v-else-if="rebalanceResult && !rebalanceResult.error" class="rebalance-result">
              <!-- 偏离度指示 -->
              <div class="rebalance-header">
                <span class="rebalance-title">调仓分析</span>
                <span :class="['drift-badge', rebalanceResult.drift_level]">
                  {{ { balanced: '已平衡', slight: '轻微偏离', significant: '显著偏离' }[rebalanceResult.drift_level] || '未知' }}
                </span>
                <span class="market-tag">{{ rebalanceResult.market_level }} · {{ rebalanceResult.market_avg_percentile }}%</span>
              </div>

              <!-- 配比对比 -->
              <div class="allocation-compare">
                <div class="alloc-row" v-for="cat in ['equity','bond','index','hybrid','money','qdii','cash']" :key="cat"
                  v-show="(rebalanceResult.current_allocation[cat] || 0) > 0.001 || (rebalanceResult.target_allocation[cat] || 0) > 0.001">
                  <span class="alloc-label">{{ {equity:'股票型',bond:'债券型',index:'指数型',hybrid:'混合型',money:'货币型',qdii:'QDII',cash:'现金'}[cat] || cat }}</span>
                  <div class="alloc-bars">
                    <div class="alloc-bar-current" :style="{ width: Math.min((rebalanceResult.current_allocation[cat]||0)*100, 100) + '%' }"></div>
                    <div class="alloc-bar-target" :style="{ width: Math.min((rebalanceResult.target_allocation[cat]||0)*100, 100) + '%' }"></div>
                  </div>
                  <span class="alloc-values">
                    <span class="alloc-current">{{ ((rebalanceResult.current_allocation[cat]||0)*100).toFixed(0) }}%</span>
                    <span class="alloc-arrow">→</span>
                    <span class="alloc-target">{{ ((rebalanceResult.target_allocation[cat]||0)*100).toFixed(0) }}%</span>
                  </span>
                </div>
              </div>

              <!-- 调仓建议 -->
              <div v-if="rebalanceResult.suggestions?.length" class="rebalance-suggestions">
                <div v-for="(s, i) in rebalanceResult.suggestions" :key="i" class="suggestion-item">
                  <span :class="['suggestion-action', s.action]">
                    {{ {buy:'买入',sell:'卖出',buy_index:'定投',deploy_cash:'配置现金',reserve_cash:'保留现金'}[s.action] || s.action }}
                  </span>
                  <span class="suggestion-detail">
                    {{ s.fund_name || s.category }} · {{ s.reason }}
                    <span v-if="s.amount_range" class="suggestion-amount">{{ s.amount_range }}</span>
                  </span>
                </div>
              </div>
            </div>
            <div v-else-if="rebalanceResult?.error" class="rebalance-result">
              <span class="rebalance-content" style="color: var(--text-muted)">{{ rebalanceResult.error }}</span>
            </div>
          </div>

          <!-- AI 全景诊断结果 -->
          <div v-if="panoramaResult && !panoramaResult.error" class="panorama-section">
            <div class="panorama-header">
              <span class="panorama-title">AI 全景诊断</span>
              <span class="panorama-time">{{ panoramaResult.created_at?.slice(0, 16) }}</span>
            </div>
            <div class="panorama-content markdown-body" v-html="renderMarkdown(panoramaResult.result_data || panoramaResult.result)"></div>
          </div>
          <div v-else-if="panoramaResult?.error" class="panorama-section">
            <span class="panorama-error">{{ panoramaResult.error }}</span>
          </div>

          <div class="card-actions">
            <button class="btn-ghost btn-sm" @click="emit('navigate', 'portfolio')">查看全部持仓 →</button>
          </div>
        </div>
      </div>

      <!-- ── Card 3: 今日热门机会 ── -->
      <div class="dash-card card">
        <div class="card-header">
          <div class="card-title-row">
            <svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24" class="card-icon">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M13 10V3L4 14h7v7l9-11h-7z"/>
            </svg>
            <span>今日热门机会</span>
          </div>
          <div class="card-header-actions">
            <span v-if="hotTopicsFetchedAt" class="card-data-time">{{ hotTopicsFetchedAt }}</span>
            <button
              v-if="!hotspotLoading"
              class="btn-ai-action"
              @click="confirmHotspots"
            >
            <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"/>
            </svg>
            <span>{{ hotspotsAnalysis ? '重新分析' : 'AI 分析' }}</span>
            <span class="ai-agent-tooltip">热点分析专家</span>
          </button>
          </div>
        </div>

        <!-- 自动加载的新闻列表（始终显示） -->
        <div v-if="hotTopics?.length && !hotspotLoading" class="card-body news-list" :class="{ 'news-list-compact': hotspotsAnalysis }">
          <div v-for="(item, i) in hotTopics.slice(0, hotspotsAnalysis ? 3 : 4)" :key="i" class="news-item">
            <a v-if="item.url" :href="item.url" target="_blank" rel="noopener" class="news-title">{{ item.title }}</a>
            <span v-else class="news-title">{{ item.title }}</span>
            <p v-if="!hotspotsAnalysis" class="news-summary">{{ item.summary?.slice(0, 120) }}{{ item.summary?.length > 120 ? '...' : '' }}</p>
            <!-- 关联指数和持仓 -->
            <div v-if="hotspotsRelate?.[i]?.sectors?.length" class="news-relate">
              <div class="news-sectors">
                <span v-for="s in hotspotsRelate[i].sectors" :key="s" class="sector-tag">{{ s }}</span>
              </div>
              <div v-if="hotspotsRelate[i].related_indexes?.length" class="news-indexes">
                <span class="relate-label">相关指数：</span>
                <span v-for="idx in hotspotsRelate[i].related_indexes.slice(0, 3)" :key="idx.index_code" class="index-link" @click="emit('navigate', 'valuation')">
                  {{ idx.index_name }}
                  <em v-if="idx.percentile != null" :style="{ color: getPercentileColor(idx.percentile) }">{{ idx.percentile }}%</em>
                </span>
              </div>
              <div v-if="hotspotsRelate[i].related_holdings?.length" class="news-holdings">
                <span class="relate-label">持仓关联：</span>
                <span v-for="h in hotspotsRelate[i].related_holdings" :key="h.fund_code" class="holding-tag">
                  {{ h.fund_name }}
                </span>
              </div>
            </div>
            <div class="news-meta">
              <span class="news-source">{{ item.source }}</span>
              <span v-if="item.date" class="news-date">{{ item.date?.slice(0, 10) }}</span>
            </div>
          </div>
          <p v-if="!hotspotsAnalysis" class="hotspots-hint">点击「AI 分析」获取结构化投资机会推荐</p>
        </div>

        <!-- 热点数据加载中 -->
        <div v-if="hotTopicsLoading && !hotspotLoading" class="card-empty">
          <div class="spinner" style="width:20px;height:20px;border-width:2px"></div>
          <p style="margin-top:0.5rem">加载今日热点...</p>
        </div>

        <!-- 无数据 → 提示用户点击 -->
        <div v-if="!hotTopics?.length && !hotTopicsLoading && !hotspotLoading" class="card-empty">
          <p>点击「AI 分析」获取今日市场热点</p>
          <p class="card-empty-hint">AI 基于最新财经新闻生成板块解读和投资机会分析</p>
        </div>

        <!-- AI 加载中 -->
        <div v-if="hotspotLoading" class="card-loading">
          <div class="spinner"></div>
          <p>正在分析市场热点...</p>
        </div>

        <!-- AI 结果（结构化推荐卡片） -->
        <div v-if="hotspotsAnalysis && !hotspotLoading" class="card-body hotspots-body">
          <!-- 关联行业和持仓概览 -->
          <div v-if="hotspotsRelate?.length" class="hotspots-relate-summary">
            <span class="relate-label">涉及行业：</span>
            <span v-for="s in allSectors" :key="s" class="sector-tag">{{ s }}</span>
            <span v-if="allRelatedHoldings.length" class="relate-holding-hint">· 持仓关联 {{ allRelatedHoldings.length }} 只</span>
          </div>
          <p class="hotspots-summary">{{ hotspotsAnalysis.summary }}</p>
          <div v-for="(rec, i) in hotspotsAnalysis.recommendations" :key="i" class="rec-card">
            <div class="rec-main">
              <span :class="['rec-badge', 'rec-' + rec.direction]">
                {{ rec.direction === 'up' ? '关注' : rec.direction === 'down' ? '回避' : '观察' }}
              </span>
              <span class="rec-name rec-name-link" @click="emit('navigate', 'valuation')">{{ rec.index_name }}</span>
              <span v-if="rec.index_code" class="rec-code">{{ rec.index_code }}</span>
              <span v-if="rec.percentile != null" class="rec-pct" :style="{ color: getPercentileColor(rec.percentile) }">
                {{ rec.percentile }}%
              </span>
              <span v-if="rec.user_portfolio" :class="['rec-portfolio', 'rp-' + rec.user_portfolio]">
                {{ rec.user_portfolio === 'already_have' ? '已持有' : rec.user_portfolio === 'can_add' ? '可加仓' : rec.user_portfolio === 'reduce' ? '应减仓' : '新机会' }}
              </span>
              <span class="rec-reason">{{ rec.reason }}</span>
              <span :class="['rec-conf', 'conf-' + rec.confidence]">
                {{ rec.confidence === 'high' ? '高置信度' : rec.confidence === 'medium' ? '中置信度' : '低置信度' }}
              </span>
            </div>
            <div class="rec-actions">
              <button class="rec-feedback-btn helpful" :class="{ active: feedbackSending[rec.id] === 'helpful' }"
                :disabled="feedbackSending[rec.id]" @click="submitFeedback(rec, 'helpful')"
                title="有用">
                <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 10h4.764a2 2 0 011.789 2.894l-3.5 7A2 2 0 0115.263 21h-4.017c-.163 0-.326-.02-.485-.06L7 20m7-10V5a2 2 0 00-2-2h-.095c-.5 0-.905.405-.905.905 0 .714-.211 1.412-.608 2.006L7 11v9m7-10h-2M7 20H5a2 2 0 01-2-2v-6a2 2 0 012-2h2.5"/>
                </svg>
              </button>
              <button class="rec-feedback-btn unhelpful" :class="{ active: feedbackSending[rec.id] === 'unhelpful' }"
                :disabled="feedbackSending[rec.id]" @click="submitFeedback(rec, 'unhelpful')"
                title="不准确">
                <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 14H5.236a2 2 0 01-1.789-2.894l3.5-7A2 2 0 018.736 3h4.018a2 2 0 01.485.06l3.76.94m-7 10v5a2 2 0 002 2h.096c.5 0 .905-.405.905-.904 0-.715.211-1.413.608-2.008L17 13V4m-7 10h2m5-10h2a2 2 0 012 2v6a2 2 0 01-2 2h-2.5"/>
                </svg>
              </button>
            </div>
          </div>
          <div v-if="hotspotsAnalysis.recommendations?.length === 0 && hotspotsAnalysis.summary" class="card-empty">
            <p>暂无明确推荐</p>
          </div>
          <details v-if="hotspotsAnalysis.analysis_text" class="hotspots-details">
            <summary class="hotspots-details-summary">查看完整分析 ↓</summary>
            <div class="hotspots-content">{{ hotspotsAnalysis.analysis_text }}</div>
          </details>
        </div>

        <!-- 推荐验证历史 -->
        <div v-if="recStats" class="verify-bar" @click="showVerify = !showVerify">
          <span class="verify-label">推荐验证</span>
          <span class="verify-stat">总计 {{ recStats.total }} 条</span>
          <span v-if="recStats.verified > 0" class="verify-stat correct">正确 {{ recStats.correct }}</span>
          <span v-if="recStats.verified > 0" class="verify-stat wrong">错误 {{ recStats.wrong }}</span>
          <span v-if="recStats.flat > 0" class="verify-stat flat">平局 {{ recStats.flat }}</span>
          <span v-if="recStats.pending_not_due > 0" class="verify-stat pending">待到期 {{ recStats.pending_not_due }}</span>
          <span v-if="recStats.accuracy != null" class="verify-accuracy">命中率 {{ recStats.accuracy }}%</span>
          <span v-else class="verify-accuracy pending">暂无验证数据</span>
          <svg width="12" height="12" fill="none" stroke="currentColor" viewBox="0 0 24 24" :class="{ rotated: showVerify }">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/>
          </svg>
        </div>
        <div v-if="showVerify" class="verify-detail">
          <div class="verify-hint">验证规则：T+5 交易日后自动对比行情，涨跌 &lt;2% 为平局；观察方向对比沪深300</div>
          <div v-if="recStats.watch_total > 0" class="verify-watch-stats">
            <span>观察方向：{{ recStats.watch_total }} 条</span>
            <span v-if="recStats.watch_correct > 0" class="correct">跑赢基准 {{ recStats.watch_correct }}</span>
            <span v-if="recStats.watch_wrong > 0" class="wrong">跑输基准 {{ recStats.watch_wrong }}</span>
          </div>
          <div v-if="recHistory?.length" class="verify-list">
            <div v-for="rec in recHistory.slice(0, 10)" :key="rec.id" class="verify-item">
              <span :class="['rec-badge', 'rec-' + rec.direction]">
                {{ rec.direction === 'up' ? '关注' : rec.direction === 'down' ? '回避' : '观察' }}
              </span>
              <span class="verify-name">{{ rec.index_name }}</span>
              <span :class="['verify-status', 'vs-' + rec.status]">
                {{ rec.status === 'correct' ? '✅' : rec.status === 'wrong' ? '❌' : rec.status === 'flat' ? '➡️' : '⏳' }}
                {{ rec.status === 'correct' ? '正确' : rec.status === 'wrong' ? '错误' : rec.status === 'flat' ? '平局' : '待验证' }}
                <template v-if="rec.change_pct != null">({{ rec.change_pct > 0 ? '+' : '' }}{{ rec.change_pct }}%)</template>
                <template v-if="rec.benchmark_change_pct != null"> vs 基准{{ rec.benchmark_change_pct > 0 ? '+' : '' }}{{ rec.benchmark_change_pct }}%</template>
              </span>
            </div>
          </div>
        </div>
      </div>

      <!-- ── Card 4: 零钱配置 ── -->
      <div class="dash-card card">
        <div class="card-header">
          <div class="card-title-row">
            <svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24" class="card-icon">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>
            </svg>
            <span>零钱配置</span>
          </div>
          <div class="card-header-actions">
            <span v-if="data?.cash_management" class="card-data-time">{{ data?.cash_updated_at || '' }}</span>
            <button
              v-if="data?.cash_management?.balance > 0"
              class="btn-ai-action"
              :class="{ 'btn-loading': bondLoading }"
              @click="confirmBondRecommend"
              :disabled="bondLoading"
            >
            <svg v-if="bondLoading" class="icon-spin spinning" width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
            </svg>
            <svg v-else width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>
            </svg>
            <span>{{ bondLoading ? '分析中...' : bondResult ? '重新分析' : 'AI 债券推荐' }}</span>
            <span class="ai-agent-tooltip">债券配置顾问</span>
          </button>
          </div>
        </div>
        <div class="card-body">
          <div class="cash-balance-row">
            <span class="cash-label">可用零钱</span>
            <span class="cash-value">¥{{ (data?.cash_management?.balance || 0).toLocaleString() }}</span>
            <span v-if="data?.cash_management?.suggestion?.cash_ratio != null" class="cash-ratio-tag">
              占比 {{ (data.cash_management.suggestion.cash_ratio * 100).toFixed(0) }}%
            </span>
          </div>
          <div v-if="data?.cash_management?.cash_details" class="cash-details-row">
            <span v-for="(bal, uid) in data.cash_management.cash_details" :key="uid" class="cash-detail-tag">
              {{ uid === '小鱼儿' ? '🐟' : '🌸' }} {{ uid }} ¥{{ bal.toLocaleString() }}
            </span>
          </div>

          <!-- 现金预警 + 权益机会 -->
          <div v-if="data?.cash_management?.suggestion?.alerts?.length" class="cash-alerts">
            <div v-for="(alert, i) in data.cash_management.suggestion.alerts" :key="i"
              :class="['cash-alert', alert.level]">
              <span class="cash-alert-icon">{{ {warning:'⚠️',info:'ℹ️',opportunity:'💡'}[alert.level] || '📌' }}</span>
              <span>{{ alert.message }}</span>
            </div>
          </div>

          <div v-if="data?.cash_management?.bond_market" class="bond-info">
            <div class="bond-temp-row">
              <span class="bond-temp-label">债市温度</span>
              <span :class="['bond-temp-value', { 'bond-cold': data.cash_management.bond_market.temperature <= 30, 'bond-hot': data.cash_management.bond_market.temperature > 70 }]">
                {{ data.cash_management.bond_market.temperature }}°
              </span>
              <span class="bond-yield">10Y收益率 {{ data.cash_management.bond_market.yield_val }}%</span>
            </div>
            <!-- 趋势对比 -->
            <div v-if="data.cash_management.bond_market.trend" class="bond-trend">
              <div class="trend-item">
                <span class="trend-label">较一周前</span>
                <span :class="['trend-val', { 'trend-up': (data.cash_management.bond_market.trend.week_ago_yield || 0) < (data.cash_management.bond_market.yield_val || 0) }]">
                  {{ _cmpTemp(data.cash_management.bond_market.trend.week_ago_temp, data.cash_management.bond_market.temperature) }}
                </span>
              </div>
              <div class="trend-item">
                <span class="trend-label">较一月前</span>
                <span :class="['trend-val', { 'trend-up': (data.cash_management.bond_market.trend.month_ago_yield || 0) < (data.cash_management.bond_market.yield_val || 0) }]">
                  {{ _cmpTemp(data.cash_management.bond_market.trend.month_ago_temp, data.cash_management.bond_market.temperature) }}
                </span>
              </div>
              <div class="trend-note">温度↑=利率↓=债价高，温度↓=利率↑=债价低</div>
            </div>
          </div>
          <div v-else class="bond-info bond-unavailable">
            <p>债市数据暂不可用</p>
          </div>

          <div v-if="data?.cash_management?.suggestion?.allocation?.length" class="allocation-section">
            <p class="allocation-summary">{{ data.cash_management.suggestion.summary }}</p>
            <div class="allocation-chart">
              <div v-for="(item, i) in data.cash_management.suggestion.allocation" :key="i" class="allocation-bar-item">
                <div class="alloc-bar-label">
                  <span>{{ item.name }}</span>
                  <span class="alloc-bar-pct">{{ item.ratio }}%</span>
                </div>
                <div class="alloc-bar-bg">
                  <div class="alloc-bar-fill" :style="{ width: item.ratio + '%', background: ['#c9a84c', '#10b981', '#f59e0b'][i % 3] }"></div>
                </div>
                <div class="alloc-bar-desc">{{ item.desc }}</div>
              </div>
            </div>
            <div class="alloc-money">
              <div v-for="(item, i) in data.cash_management.suggestion.allocation" :key="'m'+i" class="alloc-money-item">
                <span class="alloc-money-name">{{ item.name }}</span>
                <span class="alloc-money-val">≈ ¥{{ ((data.cash_management.balance || 0) * item.ratio / 100).toLocaleString() }}</span>
              </div>
            </div>
          </div>

          <!-- AI 债券推荐结果 -->
          <div v-if="bondResult" class="bond-ai-result">
            <div class="bond-ai-loading" v-if="bondLoading">
              <div class="spinner"></div>
              <p>AI 分析中...</p>
            </div>
            <template v-if="!bondLoading && bondResult">
              <div class="bond-ai-header">
                <span class="bond-ai-summary">{{ bondResult.summary }}</span>
                <span class="bond-ai-market">{{ bondResult.market_assessment }}</span>
              </div>
              <div v-if="bondResult.policy_analysis" class="bond-ai-trend">
                <span class="bond-ai-label">政策环境：</span>{{ bondResult.policy_analysis }}
              </div>
              <div v-if="bondResult.trend_analysis" class="bond-ai-trend">
                <span class="bond-ai-label">趋势判断：</span>{{ bondResult.trend_analysis }}
              </div>
              <div v-if="bondResult.current_bond_analysis" class="bond-ai-analysis">
                <span class="bond-ai-label">持仓评估：</span>{{ bondResult.current_bond_analysis }}
              </div>
              <div v-if="bondResult.recommendations?.length" class="bond-rec-list">
                <div v-for="(rec, i) in bondResult.recommendations" :key="i" class="bond-rec-item">
                  <div class="bond-rec-head">
                    <span class="bond-rec-name">{{ rec.fund_name }}</span>
                    <span class="bond-rec-code">{{ rec.fund_code }}</span>
                    <span class="bond-rec-type">{{ rec.fund_type }}</span>
                  </div>
                  <div class="bond-rec-body">
                    <span class="bond-rec-reason">{{ rec.reason }}</span>
                    <span class="bond-rec-amount">≈ ¥{{ (rec.amount || 0).toLocaleString() }}</span>
                    <span class="bond-rec-desc">{{ rec.amount_desc }}</span>
                  </div>
                </div>
              </div>
              <div v-if="bondResult.note" class="bond-rec-alt">
                {{ bondResult.note }}
              </div>
            </template>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- 二次确认弹窗 -->
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
.dashboard {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  max-width: 1280px;
}

.dash-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
}

.refresh-btn {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  font-size: 0.8rem;
  white-space: nowrap;
}

.refresh-btn svg.spinning {
  animation: spin 1s linear infinite;
}

.dash-error {
  padding: 2rem;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.75rem;
  color: var(--color-text-muted);
}

/* ── 每日简报 ── */
.briefing-card {
  margin-bottom: 1rem;
  padding: 0;
  overflow: hidden;
  border: 1px solid rgba(212, 168, 67, 0.18);
  background: linear-gradient(135deg, rgba(212, 168, 67, 0.04) 0%, var(--color-bg-card) 100%);
}
.briefing-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.85rem 1.25rem;
  cursor: pointer;
  user-select: none;
}
.briefing-header:hover {
  background: rgba(212, 168, 67, 0.05);
}
.briefing-title-row {
  display: flex;
  align-items: center;
  gap: 0.6rem;
}
.briefing-icon {
  color: var(--color-primary);
  flex-shrink: 0;
}
.briefing-label {
  font-weight: 700;
  font-size: 0.95rem;
  color: var(--color-text-primary);
}
.briefing-time {
  font-size: 0.78rem;
  color: var(--color-text-muted);
  background: var(--color-primary-50);
  padding: 0.15rem 0.5rem;
  border-radius: 999px;
  margin-right: 0.5rem;
}
.btn-briefing-gen {
  padding: 0.3rem 0.65rem;
  font-size: 0.75rem;
}
.btn-card-refresh {
  padding: 0.3rem 0.65rem;
  font-size: 0.75rem;
}
.briefing-chevron {
  color: var(--color-text-muted);
  transition: transform 0.25s ease;
  flex-shrink: 0;
}
.briefing-chevron-up {
  transform: rotate(180deg);
}
.briefing-body {
  padding: 0 1.25rem 1rem;
  font-size: 0.88rem;
  line-height: 1.7;
  color: var(--color-text-secondary);
}
.briefing-body :deep(ul) {
  margin: 0.3rem 0;
  padding-left: 1.2rem;
}
.briefing-body :deep(li) {
  margin-bottom: 0.2rem;
}
.briefing-body :deep(strong) {
  color: var(--color-text-primary);
  font-weight: 600;
}
.briefing-footer {
  display: flex;
  justify-content: flex-end;
  padding: 0.5rem 1.25rem 0.75rem;
  border-top: 1px solid var(--color-border);
}
.briefing-feedback {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}
.briefing-feedback-hint {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  margin-left: 0.3rem;
}
.briefing-comment-box {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.6rem;
  width: 100%;
}
.briefing-comment-input {
  flex: 1;
  padding: 0.45rem 0.75rem;
  font-size: 0.82rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg);
  color: var(--color-text-primary);
  outline: none;
  transition: border-color 0.2s;
}
.briefing-comment-input:focus {
  border-color: var(--color-primary);
}
.briefing-comment-input::placeholder {
  color: var(--color-text-muted);
}
.briefing-comment-submit {
  padding: 0.45rem 1rem;
  font-size: 0.82rem;
  font-weight: 600;
  color: #fff;
  background: var(--color-primary);
  border: none;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: opacity 0.2s;
  white-space: nowrap;
}
.briefing-comment-submit:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.briefing-comment-submit:hover:not(:disabled) {
  opacity: 0.9;
}
.slide-fade-enter-active {
  transition: all 0.25s ease-out;
}
.slide-fade-leave-active {
  transition: all 0.2s ease-in;
}
.slide-fade-enter-from,
.slide-fade-leave-to {
  opacity: 0;
  max-height: 0;
  padding-top: 0;
  padding-bottom: 0;
}

/* ── 数据新鲜度警告 ── */
.stale-warning {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.75rem 1rem;
  background: var(--color-warning-bg);
  border: 1px solid var(--color-warning-border, #fbbf24);
  border-radius: 8px;
  margin-bottom: 1rem;
}
.stale-warning-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}
.stale-warning-title {
  font-size: 0.9rem;
  font-weight: 600;
  color: var(--color-warning-text, #92400e);
}
.stale-warning-list {
  font-size: 0.8rem;
  color: var(--color-warning-text-secondary, #a16207);
  line-height: 1.4;
}

/* ── AI 债券推荐 ── */
.bond-ai-result {
  margin-top: 1rem;
  padding: 0.75rem;
  background: var(--color-info-bg);
  border: 1px solid var(--color-border);
  border-radius: 8px;
}
.bond-ai-loading {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 1rem;
  justify-content: center;
}
.bond-ai-header {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  margin-bottom: 0.5rem;
}
.bond-ai-summary {
  font-size: 1rem;
  font-weight: 600;
  color: var(--color-primary);
}
.bond-ai-market {
  font-size: 0.8rem;
  color: var(--color-text-secondary);
}
.bond-ai-analysis {
  font-size: 0.8rem;
  color: var(--color-text-secondary);
  margin-bottom: 0.5rem;
  line-height: 1.4;
}
.bond-ai-label {
  font-weight: 600;
  color: var(--color-text-primary);
}
.bond-rec-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.bond-rec-item {
  padding: 0.5rem;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: 6px;
}
.bond-rec-head {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  flex-wrap: wrap;
}
.bond-rec-name {
  font-weight: 600;
  font-size: 0.85rem;
  color: var(--color-text-primary);
}
.bond-rec-code {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  font-family: monospace;
}
.bond-rec-type {
  font-size: 0.7rem;
  padding: 1px 6px;
  border-radius: 4px;
  background: var(--color-primary-100);
  color: var(--color-primary-700);
}
.bond-rec-body {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-top: 0.3rem;
  flex-wrap: wrap;
}
.bond-rec-reason {
  font-size: 0.78rem;
  color: var(--color-text-secondary);
  flex: 1;
}
.bond-rec-amount {
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--color-success, #059669);
}
.bond-rec-desc {
  font-size: 0.7rem;
  color: var(--color-text-muted);
}
.bond-rec-alt {
  margin-top: 0.4rem;
  font-size: 0.78rem;
  color: var(--color-text-muted);
  font-style: italic;
}

/* ── Grid ── */
.dash-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1rem;
}

@media (max-width: 768px) {
  .dash-grid {
    grid-template-columns: 1fr;
  }
  .health-metrics {
    grid-template-columns: 1fr;
  }
}

/* ── Card ── */
.dash-card {
  padding: 1.25rem 1.5rem;
  display: flex;
  flex-direction: column;
  gap: 1rem;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-sm);
  transition: box-shadow 0.3s cubic-bezier(0.4, 0, 0.2, 1), border-color 0.3s ease;
  position: relative;
  overflow: hidden;
  min-height: 420px;
  max-height: 540px;
}

.dash-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: linear-gradient(90deg, var(--color-primary), var(--color-primary-light));
  opacity: 0;
  transition: opacity 0.3s ease;
}

.dash-card:hover {
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.4), 0 0 24px rgba(212, 168, 67, 0.06);
  border-color: rgba(212, 168, 67, 0.2);
}

.dash-card:hover::before {
  opacity: 1;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
}

.card-title-row {
  display: flex;
  align-items: center;
  gap: 0.65rem;
  font-weight: 700;
  font-size: 1.05rem;
  color: var(--color-text-primary);
  letter-spacing: -0.01em;
}

.card-icon {
  color: var(--color-primary);
  flex-shrink: 0;
  width: 20px;
  height: 20px;
}

.card-badge {
  font-size: 0.72rem;
  padding: 0.2rem 0.6rem;
  border-radius: 999px;
  background: var(--color-primary-50);
  color: var(--color-primary);
  font-weight: 700;
  border: 1px solid rgba(212, 168, 67, 0.2);
}

.card-body {
  display: flex;
  flex-direction: column;
  gap: 0.65rem;
  flex: 1;
  overflow-y: auto;
  min-height: 0;
}

.card-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 2.5rem 0;
  text-align: center;
  color: var(--color-text-muted);
  font-size: 0.9rem;
}

.card-empty-hint {
  font-size: 0.8rem;
  margin-top: 0.35rem;
  opacity: 0.8;
}

.card-loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 2.5rem 0;
  gap: 1rem;
  color: var(--color-text-muted);
  font-size: 0.9rem;
}

.spinner {
  width: 28px;
  height: 28px;
  border: 3px solid var(--color-border);
  border-top-color: var(--color-primary);
  border-radius: 50%;
  animation: spin 0.8s cubic-bezier(0.4, 0, 0.2, 1) infinite;
}

.card-actions {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.5rem;
}

/* ── Card Header Actions ── */
.card-header-actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.btn-icon-refresh {
  background: none;
  border: none;
  color: var(--color-text-muted);
  cursor: pointer;
  padding: 0.35rem;
  border-radius: var(--radius-md);
  transition: all 0.2s ease;
  display: flex;
  align-items: center;
  justify-content: center;
}

.btn-icon-refresh:hover {
  color: var(--color-primary);
  background: var(--color-primary-bg);
}

.btn-icon-refresh:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.icon-refresh {
  transition: transform 0.3s ease;
}

.icon-refresh.spinning {
  animation: spin 1s linear infinite;
}

/* ── AI Button Group ── */
.ai-btn-group {
  display: flex;
  align-items: center;
  gap: 0.35rem;
}

.btn-toggle {
  background: none;
  border: 1px solid var(--color-border-light);
  color: var(--color-text-muted);
  cursor: pointer;
  padding: 0.35rem;
  border-radius: var(--radius-md);
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s ease;
}

.btn-toggle:hover {
  background: var(--color-bg-hover);
  color: var(--color-text-secondary);
  border-color: var(--color-border);
}

.toggle-arrow {
  transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.toggle-arrow.expanded {
  transform: rotate(180deg);
}

/* ── AI Action Button ── */
.btn-ai-action {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.45rem 0.85rem;
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--color-primary);
  background: linear-gradient(135deg, var(--color-primary-bg), rgba(212, 168, 67, 0.08));
  border: 1px solid rgba(212, 168, 67, 0.2);
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
  background: linear-gradient(135deg, rgba(212, 168, 67, 0.15), rgba(212, 168, 67, 0.12));
  border-color: rgba(212, 168, 67, 0.4);
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(212, 168, 67, 0.15);
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
  background: linear-gradient(135deg, rgba(212, 168, 67, 0.08), rgba(212, 168, 67, 0.05));
  border-color: rgba(212, 168, 67, 0.15);
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

.btn-ai-regenerate {
  padding: 0.45rem;
  background: transparent;
  border-color: transparent;
}

.btn-ai-regenerate:hover {
  background: var(--color-primary-bg);
  border-color: rgba(212, 168, 67, 0.2);
}

/* ── AI Agent Tooltip ── */
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
  border-radius: var(--radius-md);
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

.btn-ai-action:hover .ai-agent-tooltip {
  opacity: 1;
  visibility: visible;
}

/* ── Icon Spin Animation ── */
.icon-spin {
  transition: transform 0.3s ease;
}

.icon-spin.spinning {
  animation: spin 1s linear infinite;
}

/* ── 低估指数 ── */
.index-row {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.5rem 0.5rem;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
}

.index-row:hover {
  background: var(--color-bg-hover);
}

.index-info {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 0 0 auto;
  width: 105px;
}

.index-name {
  font-size: 0.9rem;
  font-weight: 700;
  color: var(--color-text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.index-meta {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  font-weight: 500;
}

.index-percentile {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  flex: 1;
  min-width: 0;
}

.percentile-bar-bg {
  flex: 1;
  height: 8px;
  background: var(--color-bg-input);
  border-radius: 4px;
  overflow: visible;
  min-width: 50px;
  position: relative;
}

.percentile-bar {
  height: 100%;
  border-radius: 4px;
  transition: width 0.6s cubic-bezier(0.4, 0, 0.2, 1);
}

.percentile-marks {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  pointer-events: none;
}

.percentile-marks .mark {
  position: absolute;
  top: -14px;
  transform: translateX(-50%);
  font-size: 0.6rem;
  color: var(--color-text-tertiary);
  opacity: 0.5;
}

.percentile-marks .mark::after {
  content: '';
  position: absolute;
  left: 50%;
  top: 100%;
  width: 1px;
  height: 4px;
  background: var(--color-text-tertiary);
  opacity: 0.3;
}

.percentile-value {
  font-size: 0.88rem;
  font-weight: 800;
  width: 42px;
  text-align: right;
  flex-shrink: 0;
}

.assessment-tag {
  font-size: 0.7rem;
  font-weight: 700;
  padding: 0.2rem 0.55rem;
  border-radius: var(--radius-sm);
  white-space: nowrap;
  flex-shrink: 0;
  letter-spacing: 0.03em;
}

.btn-link {
  background: none;
  border: none;
  color: var(--color-primary);
  font-size: 0.88rem;
  font-weight: 600;
  cursor: pointer;
  padding: 0.4rem 0;
  text-align: left;
  align-self: flex-start;
  transition: all 0.2s ease;
}

.btn-link:hover {
  color: var(--color-primary-dark);
}

/* ── 持仓健康度 ── */
.health-metrics {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 0.75rem;
  padding: 0.75rem;
  background: linear-gradient(135deg, var(--color-bg-card), var(--color-bg-card));
  border-radius: var(--radius-lg);
  border: 1px solid var(--color-border-light);
}

.metric-item {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.metric-label {
  font-size: 0.78rem;
  color: var(--color-text-muted);
  font-weight: 600;
}

.metric-value {
  font-size: 1.1rem;
  font-weight: 800;
  color: var(--color-text-primary);
  letter-spacing: -0.02em;
}

.metric-value.profit {
  color: #dc2626;
}

.metric-value.loss {
  color: #059669;
}

.concentration-row {
  display: flex;
  align-items: flex-start;
  gap: 0.75rem;
  padding: 0.75rem;
  border-top: 1px solid var(--color-border-light);
  border-bottom: 1px solid var(--color-border-light);
  background: var(--color-bg-card);
  border-radius: var(--radius-md);
}

.concentration-icon {
  font-size: 1.25rem;
  line-height: 1.4;
}

.concentration-text {
  font-size: 0.85rem;
  color: var(--color-text-secondary);
  margin-bottom: 0.35rem;
  font-weight: 500;
}

.concentration-bar-bg {
  height: 7px;
  background: var(--color-bg-input);
  border-radius: 6px;
  overflow: hidden;
  max-width: 220px;
}

.concentration-bar {
  height: 100%;
  border-radius: 6px;
  transition: width 0.5s cubic-bezier(0.4, 0, 0.2, 1);
}

/* 类型分布 */
.type-dist {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  padding: 0.5rem 0;
}

.type-item {
  display: flex;
  align-items: center;
  gap: 0.6rem;
}

.type-name {
  font-size: 0.8rem;
  color: var(--color-text-secondary);
  width: 55px;
  flex-shrink: 0;
  font-weight: 600;
}

.type-bar-bg {
  flex: 1;
  height: 7px;
  background: var(--color-bg-input);
  border-radius: 6px;
  overflow: hidden;
  max-width: 180px;
}

.type-bar {
  height: 100%;
  background: linear-gradient(90deg, var(--color-primary), var(--color-primary-light));
  border-radius: 6px;
  transition: width 0.5s cubic-bezier(0.4, 0, 0.2, 1);
}

.type-pct {
  font-size: 0.78rem;
  color: var(--color-text-muted);
  width: 32px;
  text-align: right;
  font-weight: 600;
}

/* ── AI 再平衡 ── */
.rebalance-section {
  border-top: 1px solid var(--color-border-light);
  padding-top: 0.75rem;
  margin-top: 0.5rem;
}

.rebalance-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.75rem;
  flex-wrap: wrap;
}

.rebalance-title {
  font-size: 0.95rem;
  font-weight: 700;
  color: var(--color-primary);
}

.drift-badge {
  font-size: 0.7rem;
  font-weight: 700;
  padding: 0.15rem 0.5rem;
  border-radius: var(--radius-sm);
  letter-spacing: 0.03em;
}
.drift-badge.balanced { background: rgba(16, 185, 129, 0.12); color: #059669; }
.drift-badge.slight { background: rgba(245, 158, 11, 0.12); color: #d97706; }
.drift-badge.significant { background: rgba(239, 68, 68, 0.12); color: #dc2626; }

.market-tag {
  font-size: 0.7rem;
  color: var(--color-text-muted);
  margin-left: auto;
}

.rebalance-result {
  display: flex;
  flex-direction: column;
  gap: 0.85rem;
}

/* 配比对比 */
.allocation-compare {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  background: linear-gradient(135deg, var(--color-bg-card), var(--color-bg-card));
  border-radius: var(--radius-lg);
  padding: 0.75rem;
  border: 1px solid var(--color-border-light);
}

.alloc-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.8rem;
}

.alloc-label {
  width: 3.5rem;
  font-weight: 600;
  color: var(--color-text-secondary);
  flex-shrink: 0;
  font-size: 0.75rem;
}

.alloc-bars {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2px;
  height: 16px;
  position: relative;
}

.alloc-bar-current {
  height: 7px;
  background: var(--color-primary);
  border-radius: 3px;
  opacity: 0.7;
  transition: width 0.6s cubic-bezier(0.4, 0, 0.2, 1);
}

.alloc-bar-target {
  height: 7px;
  background: var(--color-accent, #10b981);
  border-radius: 3px;
  opacity: 0.4;
  transition: width 0.6s cubic-bezier(0.4, 0, 0.2, 1);
}

.alloc-values {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  flex-shrink: 0;
  width: 5.5rem;
  font-size: 0.75rem;
}

.alloc-current { font-weight: 700; color: var(--color-primary); }
.alloc-arrow { color: var(--color-text-muted); font-size: 0.7rem; }
.alloc-target { font-weight: 600; color: var(--color-accent, #10b981); }

/* 调仓建议 */
.rebalance-suggestions {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.suggestion-item {
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
  padding: 0.5rem 0.65rem;
  background: var(--color-bg-card);
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border-light);
  font-size: 0.82rem;
}

.suggestion-action {
  font-size: 0.7rem;
  font-weight: 700;
  padding: 0.1rem 0.4rem;
  border-radius: var(--radius-sm);
  flex-shrink: 0;
  letter-spacing: 0.02em;
}
.suggestion-action.buy, .suggestion-action.buy_index { background: rgba(16, 185, 129, 0.12); color: #059669; }
.suggestion-action.sell { background: rgba(239, 68, 68, 0.12); color: #dc2626; }
.suggestion-action.deploy_cash { background: rgba(59, 130, 246, 0.12); color: #2563eb; }
.suggestion-action.reserve_cash { background: rgba(245, 158, 11, 0.12); color: #d97706; }

.suggestion-detail {
  color: var(--color-text-secondary);
  line-height: 1.5;
}

.suggestion-amount {
  display: inline-block;
  margin-left: 0.25rem;
  font-weight: 600;
  color: var(--color-primary);
}

/* ── 全景诊断 ── */
.panorama-section {
  border-top: 1px solid var(--color-border-light);
  padding-top: 0.75rem;
  margin-top: 0.75rem;
}
.panorama-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 0.5rem;
}
.panorama-title {
  font-size: 0.95rem;
  font-weight: 700;
  color: var(--color-primary);
}
.panorama-time {
  font-size: 0.75rem;
  color: var(--color-text-muted);
}
.panorama-content {
  font-size: 0.85rem;
  line-height: 1.7;
  color: var(--color-text-secondary);
  max-height: 400px;
  overflow-y: auto;
  background: linear-gradient(135deg, var(--color-bg-card), var(--color-bg-card));
  border-radius: var(--radius-lg);
  padding: 1rem;
  border: 1px solid var(--color-border-light);
}
.panorama-content :deep(strong) {
  color: var(--color-text-primary);
  font-weight: 600;
}
.panorama-content :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 0.5rem 0;
  font-size: 0.8rem;
}
.panorama-content :deep(th),
.panorama-content :deep(td) {
  padding: 0.4rem 0.6rem;
  border: 1px solid var(--color-border-light);
  text-align: left;
}
.panorama-content :deep(th) {
  background: var(--color-bg-hover);
  font-weight: 600;
}
.panorama-error {
  color: var(--color-danger);
  font-size: 0.85rem;
}

/* ── 热门机会 ── */
.hotspots-relate-summary {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.3rem;
  margin-bottom: 0.75rem;
  padding-bottom: 0.5rem;
  border-bottom: 1px dashed var(--color-border);
}

.relate-holding-hint {
  font-size: 0.7rem;
  color: var(--color-text-muted);
}

.hotspots-body {
  max-height: 450px;
  overflow-y: auto;
}

.hotspots-summary {
  font-size: 0.95rem;
  font-weight: 700;
  color: var(--color-text-primary);
  margin-bottom: 1rem;
  line-height: 1.7;
  padding-bottom: 0.85rem;
  border-bottom: 1px solid var(--color-border-light);
}

.rec-card {
  display: flex;
  align-items: flex-start;
  gap: 0.6rem;
  padding: 0.9rem;
  border-radius: var(--radius-lg);
  background: linear-gradient(135deg, var(--color-bg-card), var(--color-bg-card));
  margin-bottom: 0.7rem;
  border: 1px solid var(--color-border-light);
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
}

.rec-card:hover {
  background: linear-gradient(135deg, rgba(13, 18, 32, 0.95), rgba(255, 255, 255, 0.04 0.7));
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05);
}

.rec-main {
  display: flex;
  align-items: center;
  gap: 0.7rem;
  flex: 1;
  min-width: 0;
  flex-wrap: wrap;
}

.rec-code {
  font-size: 0.7rem;
  color: var(--color-text-muted);
  font-family: monospace;
  flex-shrink: 0;
}

.rec-portfolio {
  font-size: 0.65rem;
  padding: 0.15rem 0.4rem;
  border-radius: var(--radius-sm);
  white-space: nowrap;
  flex-shrink: 0;
  font-weight: 600;
}
.rp-already_have { background: rgba(212, 168, 67, 0.12); color: #d4b65a; }
.rp-can_add { background: rgba(16, 185, 129, 0.12); color: #10b981; }
.rp-reduce { background: rgba(239, 68, 68, 0.12); color: #ef4444; }
.rp-new { background: rgba(245, 158, 11, 0.12); color: #f59e0b; }

.rec-actions {
  display: flex;
  gap: 0.2rem;
  flex-shrink: 0;
  padding-top: 0.15rem;
}

.rec-feedback-btn {
  width: 28px;
  height: 28px;
  border: none;
  border-radius: var(--radius-sm);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  color: var(--color-text-muted);
  opacity: 0.4;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  padding: 0;
}

.rec-feedback-btn:hover {
  opacity: 1;
  transform: scale(1.15);
}

.rec-feedback-btn.helpful:hover,
.rec-feedback-btn.helpful.active {
  color: #10b981;
  background: rgba(16, 185, 129, 0.12);
  opacity: 1;
}

.rec-feedback-btn.unhelpful:hover,
.rec-feedback-btn.unhelpful.active {
  color: #ef4444;
  background: rgba(239, 68, 68, 0.12);
  opacity: 1;
}

.rec-feedback-btn:disabled {
  cursor: default;
}

.rec-badge {
  font-size: 0.7rem;
  font-weight: 700;
  padding: 0.2rem 0.5rem;
  border-radius: var(--radius-sm);
  white-space: nowrap;
  flex-shrink: 0;
  letter-spacing: 0.02em;
}
.rec-up { background: rgba(16, 185, 129, 0.12); color: #10b981; }
.rec-down { background: rgba(239, 68, 68, 0.12); color: #ef4444; }
.rec-watch { background: rgba(245, 158, 11, 0.12); color: #f59e0b; }

.rec-name {
  font-size: 0.88rem;
  font-weight: 600;
  color: var(--color-text-primary);
  flex-shrink: 0;
}

.rec-name-link {
  cursor: pointer;
  transition: color 0.2s;
}

.rec-name-link:hover {
  color: var(--color-primary);
  text-decoration: underline;
}

.rec-pct {
  font-size: 0.75rem;
  font-weight: 700;
  padding: 0.1rem 0.35rem;
  border-radius: var(--radius-sm);
  background: var(--color-bg-input);
  flex-shrink: 0;
}

.rec-reason {
  font-size: 0.78rem;
  color: var(--color-text-muted);
  flex: 1;
  min-width: 0;
  line-height: 1.4;
}

.rec-conf {
  font-size: 0.65rem;
  padding: 0.15rem 0.4rem;
  border-radius: var(--radius-sm);
  white-space: nowrap;
  flex-shrink: 0;
  font-weight: 600;
}
.conf-high { background: rgba(16, 185, 129, 0.12); color: #10b981; }
.conf-medium { background: rgba(245, 158, 11, 0.12); color: #f59e0b; }
.conf-low { background: rgba(107, 114, 128, 0.12); color: #6b7280; }

.hotspots-details {
  margin-top: 0.75rem;
}
.hotspots-details-summary {
  font-size: 0.8rem;
  color: var(--color-primary);
  cursor: pointer;
  user-select: none;
  font-weight: 500;
  transition: color 0.2s;
}
.hotspots-details-summary:hover {
  color: var(--color-primary-hover);
}

.hotspots-content {
  font-size: 0.85rem;
  line-height: 1.7;
  color: var(--color-text-secondary);
  white-space: pre-wrap;
  margin-top: 0.75rem;
  padding: 0.75rem 1rem;
  background: linear-gradient(135deg, var(--color-primary-bg), rgba(212, 168, 67, 0.04));
  border: 1px solid rgba(212, 168, 67, 0.1);
  border-radius: var(--radius-md);
}

.news-list {
  max-height: 360px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}

.news-list-compact {
  max-height: 200px;
  gap: 0.4rem;
  border-bottom: 1px dashed var(--color-border);
  padding-bottom: 0.5rem;
  margin-bottom: 0.25rem;
}

.news-item {
  padding: 0.65rem 0.75rem;
  border-radius: var(--radius-md);
  border-left: 3px solid var(--color-primary-300);
  background: linear-gradient(135deg, var(--color-bg-input), var(--color-bg-card));
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
}

.news-item:hover {
  background: var(--color-bg-hover);
  border-left-color: var(--color-primary);
}

.news-title {
  font-size: 0.88rem;
  font-weight: 600;
  color: var(--color-text-primary);
  text-decoration: none;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  transition: color 0.2s;
}

.news-title:hover {
  color: var(--color-primary);
}

.news-summary {
  font-size: 0.78rem;
  color: var(--color-text-muted);
  margin: 0.3rem 0 0;
  line-height: 1.5;
}

.news-meta {
  display: flex;
  gap: 0.6rem;
  font-size: 0.7rem;
  color: var(--color-text-muted);
  margin-top: 0.3rem;
}

.news-source {
  color: var(--color-primary);
  font-weight: 600;
}

/* 热点→指数关联 */
.news-relate {
  margin-top: 0.4rem;
  padding-top: 0.4rem;
  border-top: 1px dashed var(--color-border);
}

.news-sectors {
  display: flex;
  flex-wrap: wrap;
  gap: 0.3rem;
  margin-bottom: 0.3rem;
}

.sector-tag {
  font-size: 0.65rem;
  padding: 0.1rem 0.4rem;
  border-radius: 4px;
  background: rgba(99,102,241,0.1);
  color: #6366f1;
  font-weight: 600;
}

.news-indexes, .news-holdings {
  font-size: 0.72rem;
  color: var(--color-text-secondary);
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.3rem;
  margin-top: 0.2rem;
}

.relate-label {
  color: var(--color-text-muted);
  font-size: 0.68rem;
}

.index-link {
  cursor: pointer;
  color: var(--color-primary);
  font-weight: 500;
}

.index-link:hover { text-decoration: underline; }
.index-link em {
  font-style: normal;
  font-size: 0.65rem;
  margin-left: 0.15rem;
}

.holding-tag {
  font-size: 0.65rem;
  padding: 0.1rem 0.4rem;
  border-radius: 4px;
  background: rgba(16,185,129,0.1);
  color: #10b981;
  font-weight: 500;
}

.hotspots-hint {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  text-align: center;
  margin: 0.4rem 0 0;
}

/* ── 零钱配置 ── */
.cash-balance-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  padding: 0.35rem 0;
}

.cash-label {
  font-size: 0.85rem;
  color: var(--color-text-muted);
}

.cash-value {
  font-size: 1.5rem;
  font-weight: 700;
  color: var(--color-text-primary);
  letter-spacing: -0.02em;
}

.cash-ratio-tag {
  font-size: 0.7rem;
  font-weight: 600;
  padding: 0.1rem 0.45rem;
  border-radius: var(--radius-sm);
  background: rgba(212, 168, 67, 0.08);
  color: var(--color-primary);
}

.cash-details-row {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.15rem;
  padding-bottom: 0.25rem;
}

.cash-detail-tag {
  font-size: 0.65rem;
  color: var(--color-text-muted);
  background: var(--color-bg-hover);
  padding: 0.12rem 0.4rem;
  border-radius: var(--radius-sm);
  white-space: nowrap;
}

.card-data-time {
  font-size: 0.6rem;
  color: var(--color-text-muted);
  font-weight: 400;
  margin-left: 0.25rem;
  opacity: 0.7;
}

.cash-alerts {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  margin: 0.35rem 0;
}

.cash-alert {
  display: flex;
  align-items: flex-start;
  gap: 0.35rem;
  font-size: 0.78rem;
  line-height: 1.5;
  padding: 0.4rem 0.6rem;
  border-radius: var(--radius-md);
}
.cash-alert.warning { background: rgba(239, 68, 68, 0.06); color: #b91c1c; }
.cash-alert.info { background: rgba(59, 130, 246, 0.06); color: #1d4ed8; }
.cash-alert.opportunity { background: rgba(16, 185, 129, 0.06); color: #047857; }

.cash-alert-icon { flex-shrink: 0; }

.bond-info {
  padding: 0.6rem 0;
}

.bond-temp-row {
  display: flex;
  align-items: center;
  gap: 0.6rem;
}

.bond-temp-label {
  font-size: 0.85rem;
  color: var(--color-text-muted);
}

.bond-temp-value {
  font-size: 1.15rem;
  font-weight: 700;
  padding: 0.15rem 0.6rem;
  border-radius: var(--radius-sm);
  background: var(--color-bg-input);
  transition: transform 0.2s;
}

.bond-temp-value:hover {
  transform: scale(1.05);
}

.bond-temp-value.bond-cold {
  color: #3b82f6;
  background: rgba(59, 130, 246, 0.08);
}

.bond-temp-value.bond-hot {
  color: #ef4444;
  background: rgba(239, 68, 68, 0.08);
}

.bond-yield {
  font-size: 0.8rem;
  color: var(--color-text-muted);
  margin-left: auto;
}

.bond-trend {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  margin-top: 0.5rem;
  padding-top: 0.5rem;
  border-top: 1px solid var(--color-border-light);
}

.trend-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.78rem;
}

.trend-label {
  color: var(--color-text-muted);
  width: 68px;
  flex-shrink: 0;
}

.trend-val {
  color: var(--color-text-secondary);
  font-weight: 500;
}

.trend-val.trend-up {
  color: var(--color-danger);
}

.trend-note {
  font-size: 0.68rem;
  color: var(--color-text-muted);
  margin-top: 0.2rem;
  font-style: italic;
}

.bond-unavailable p {
  font-size: 0.85rem;
  color: var(--color-text-muted);
}

.allocation-section {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
  padding-top: 0.6rem;
  border-top: 1px solid var(--color-border-light);
}

.allocation-summary {
  font-size: 0.85rem;
  color: var(--color-text-secondary);
  line-height: 1.6;
  margin: 0;
}

.allocation-chart {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.allocation-bar-item {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}

.alloc-bar-label {
  display: flex;
  justify-content: space-between;
  font-size: 0.8rem;
  color: var(--color-text-secondary);
}

.alloc-bar-pct {
  font-weight: 700;
  color: var(--color-text-primary);
}

.alloc-bar-bg {
  height: 8px;
  background: var(--color-bg-input);
  border-radius: 4px;
  overflow: hidden;
}

.alloc-bar-fill {
  height: 100%;
  border-radius: 4px;
  transition: width 0.6s cubic-bezier(0.4, 0, 0.2, 1);
  background: linear-gradient(90deg, var(--color-primary-400), var(--color-primary));
}

.alloc-bar-desc {
  font-size: 0.7rem;
  color: var(--color-text-muted);
}

.alloc-money {
  display: flex;
  flex-wrap: wrap;
  gap: 0.6rem;
  padding-top: 0.35rem;
}

.alloc-money-item {
  display: flex;
  gap: 0.35rem;
  font-size: 0.78rem;
  color: var(--color-text-muted);
}

.alloc-money-name {
  color: var(--color-text-secondary);
}

.alloc-money-val {
  font-weight: 600;
}

/* ── 推荐验证历史 ── */
.verify-bar {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.5rem 0.65rem;
  border-top: 1px solid var(--color-border);
  cursor: pointer;
  font-size: 0.78rem;
  user-select: none;
  transition: background 0.2s;
}
.verify-bar:hover {
  background: var(--color-bg-hover);
}
.verify-label {
  font-weight: 600;
  color: var(--color-text-primary);
}
.verify-stat {
  color: var(--color-text-muted);
}
.verify-stat.correct { color: #10b981; }
.verify-stat.wrong { color: #ef4444; }
.verify-accuracy {
  margin-left: auto;
  font-weight: 700;
  color: var(--color-primary);
}
.verify-accuracy.pending {
  color: var(--color-text-muted);
  font-weight: 400;
}
.verify-bar svg {
  transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  color: var(--color-text-muted);
}
.verify-bar svg.rotated {
  transform: rotate(180deg);
}

.verify-list {
  border-top: 1px solid var(--color-border);
  max-height: 300px;
  overflow-y: auto;
  padding: 0.4rem 0;
}
.verify-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.35rem 0.65rem;
  font-size: 0.78rem;
  transition: background 0.15s;
}
.verify-item:hover {
  background: var(--color-bg-hover);
}
.verify-name {
  flex: 1;
  color: var(--color-text-primary);
  font-weight: 500;
}
.verify-status {
  font-size: 0.7rem;
  white-space: nowrap;
}
.vs-correct { color: #10b981; }
.vs-wrong { color: #ef4444; }
.vs-flat { color: #f59e0b; }
.vs-pending { color: var(--color-text-muted); }

.verify-stat.flat { color: #f59e0b; }
.verify-stat.pending { color: var(--color-text-muted); }

.verify-detail {
  border-top: 1px solid var(--color-border);
  padding: 0.5rem 0.65rem;
}
.verify-hint {
  font-size: 0.72rem;
  color: var(--color-text-muted);
  margin-bottom: 0.4rem;
  line-height: 1.5;
}
.verify-watch-stats {
  display: flex;
  gap: 0.8rem;
  font-size: 0.75rem;
  margin-bottom: 0.4rem;
  color: var(--color-text-secondary);
}
.verify-watch-stats span {
  white-space: nowrap;
}

/* ── 市场温度仪表盘 ── */
.temp-gauges-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 1rem;
  margin-bottom: 1rem;
}

.temp-gauge-card {
  text-align: center;
  padding: 0.75rem;
}

.temp-gauge-label {
  margin-top: -0.5rem;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.temp-gauge-desc {
  font-size: 0.875rem;
  font-weight: 600;
  color: var(--color-text-primary);
}

.temp-gauge-hint {
  font-size: 0.75rem;
  color: var(--color-text-tertiary);
}

/* ── 移动端响应式 ────────────────────────────────────────── */
@media (max-width: 768px) {
  /* 卡片高度调整 */
  .dash-card {
    min-height: auto;
    max-height: none;
    padding: 1rem;
  }

  /* 网格布局 */
  .dash-grid {
    grid-template-columns: 1fr;
    gap: 0.75rem;
  }

  .health-metrics {
    grid-template-columns: 1fr;
  }

  /* 按钮触摸区域 */
  .card-icon {
    width: 24px;
    height: 24px;
  }

  .btn-ai-action {
    min-width: 44px;
    min-height: 44px;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  /* 温度仪表盘 */
  .temp-gauges-row {
    grid-template-columns: 1fr;
    gap: 0.75rem;
  }

  /* 验证栏 */
  .verify-item {
    padding: 0.5rem 0.65rem;
    min-height: 44px;
  }

  /* 推荐卡片 */
  .rec-card {
    padding: 0.75rem;
  }

  .rec-grid {
    grid-template-columns: 1fr;
    gap: 0.75rem;
  }
}
</style>