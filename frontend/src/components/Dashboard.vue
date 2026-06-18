<script setup>
import { ref, computed, onMounted, onActivated } from 'vue'
import { getDashboard, runAnalysis, runPanoramaAnalysis, getHotTopics, getDailyReport, regenerateDailyReport, submitDailyReportFeedback, listPanoramaRecords, getHotspotsAnalysis, getLatestHotspotsAnalysis, getRecommendations, getRecommendationStats, submitRecommendationFeedback, getBondRecommend, listBondRecommendRecords, autoVerifyRecommendations, fetchRecentValuations, getBondMarketTemperature, getHotspotsRelate, getRebalancingSuggestion } from '../api'
import GaugeChart from './charts/GaugeChart.vue'
import ConfirmDialog from './ConfirmDialog.vue'
import AppToast from './AppToast.vue'
import Skeleton from './ui/Skeleton.vue'
import { useToast } from '../composables/useToast'
import { renderMarkdown } from '../composables/useMarkdown'
import { getBondTempLabel } from '../composables/useDashboardHelpers'

// Sub-components
import BriefingCard from './dashboard/BriefingCard.vue'
import UndervaluedIndexesCard from './dashboard/UndervaluedIndexesCard.vue'
import PortfolioHealthCard from './dashboard/PortfolioHealthCard.vue'
import HotspotsCard from './dashboard/HotspotsCard.vue'
import CashManagementCard from './dashboard/CashManagementCard.vue'

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
const hotspotsRelate = ref(null)

// ── 每日日报自动加载 ──
const dailyReport = ref(null)
const dailyReportLoading = ref(true)
const dailyReportRegenerating = ref(false)
const showBriefing = ref(true)
const briefingFeedback = ref({ rating: null, sending: false, sent: false, showComment: false, comment: '' })

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
  if (briefingFeedback.value.sent && briefingFeedback.value.rating === rating) {
    briefingFeedback.value = { rating: null, sending: false, sent: false, showComment: false, comment: '' }
    return
  }
  briefingFeedback.value.rating = rating
  briefingFeedback.value.sent = false
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

// ── BriefingCard event handler ──
function handleBriefingSubmitFeedback(type, rating) {
  if (type === 'toggle') {
    toggleBriefingFeedback(rating)
  } else if (type === 'submit-comment') {
    submitBriefingFeedbackWithComment()
  }
}

// ── 全景诊断 AI 分析 ──

onMounted(async () => {
  try {
    const { data: latest } = await getLatestHotspotsAnalysis()
    if (latest?.recommendations?.length) {
      hotspotsAnalysis.value = latest
    }
  } catch (_) {}

  try { await autoVerifyRecommendations() } catch (_) {}

  await Promise.all([
    loadDashboard(),
    loadHotTopics(),
    loadDailyReport(),
    loadRecHistory(),
    loadBondTemperature(),
  ])

  try {
    const { data: recs } = await listPanoramaRecords(1)
    if (recs?.records?.length) {
      panoramaResult.value = recs.records[0]
    }
  } catch (e) {
    console.error('load panorama failed:', e)
  }

  try {
    const { data: res } = await getRebalancingSuggestion()
    if (res && !res.error) {
      rebalanceResult.value = res
      showRebalance.value = true
    }
  } catch (_) {}

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
      if (!hotTopicsAnalyzedAt.value) {
        hotTopicsFetchedAt.value = res.fetched_at || ''
      }
      loadHotspotsRelate()
    }
  } catch (e) {
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
    hotTopicsAnalyzedAt.value = new Date().toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
    hotTopicsFetchedAt.value = hotTopicsAnalyzedAt.value
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

function handlePanorama() {
  if (panoramaResult.value) {
    confirm.value = {
      visible: true,
      title: '刷新全景诊断',
      message: '将重新进行持仓全景诊断分析，是否继续？',
      danger: false,
      onConfirm: () => { confirm.value.visible = false; generatePanorama() }
    }
  } else {
    generatePanorama()
  }
}
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
    <BriefingCard
      v-if="dailyReport && !dailyReportLoading"
      :daily-report="dailyReport"
      :regenerating="dailyReportRegenerating"
      :show-briefing="showBriefing"
      :feedback="briefingFeedback"
      @regenerate="confirmRegenerateReport"
      @toggle="showBriefing = !showBriefing"
      @submit-feedback="handleBriefingSubmitFeedback"
    />

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
      <!-- Card 1: 低估指数 -->
      <UndervaluedIndexesCard
        :undervalued-indexes="data?.undervalued_indexes || []"
        :undervalued-updated-at="data?.undervalued_updated_at || ''"
        :data-date="data?.undervalued_data_date || ''"
        :fetching-valuation="fetchingValuation"
        :count="data?.undervalued_indexes?.length || 0"
        @refresh="handleFetchValuations"
        @navigate="(page) => emit('navigate', page)"
      />

      <!-- Card 2: 持仓健康度 -->
      <PortfolioHealthCard
        :portfolio-health="data?.portfolio_health || null"
        :portfolio-updated-at="data?.portfolio_updated_at || ''"
        :rebalance-loading="rebalanceLoading"
        :rebalance-result="rebalanceResult"
        :show-rebalance="showRebalance"
        :panorama-loading="panoramaLoading"
        :panorama-result="panoramaResult"
        @rebalance="handleRebalance"
        @panorama="handlePanorama"
        @navigate="(page) => emit('navigate', page)"
      />

      <!-- Card 3: 今日热门机会 -->
      <HotspotsCard
        :hot-topics="hotTopics"
        :hot-topics-fetched-at="hotTopicsFetchedAt"
        :hot-topics-loading="hotTopicsLoading"
        :hotspot-loading="hotspotLoading"
        :hotspot-error="hotspotError"
        :hotspots-analysis="hotspotsAnalysis"
        :hotspots-relate="hotspotsRelate || []"
        :rec-history="recHistory"
        :rec-stats="recStats"
        :show-verify="showVerify"
        :feedback-sending="feedbackSending"
        @analyze="confirmHotspots"
        @feedback="submitFeedback"
        @navigate="(page) => emit('navigate', page)"
        @update:show-verify="showVerify = $event"
      />

      <!-- Card 4: 零钱配置 -->
      <CashManagementCard
        :cash-management="data?.cash_management || null"
        :cash-updated-at="data?.cash_updated_at || ''"
        :bond-loading="bondLoading"
        :bond-result="bondResult"
        @bond-recommend="confirmBondRecommend"
      />
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
  gap: 1.25rem;
  max-width: 1280px;
}

.dash-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
}

.dash-error {
  padding: 2.5rem 2rem;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.75rem;
  color: var(--color-text-muted);
  text-align: center;
}

/* ── 数据新鲜度警告 ── */
.stale-warning {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.85rem 1.25rem;
  background: var(--color-warning-bg);
  border: 1px solid var(--color-warning-border, #fbbf24);
  border-radius: var(--radius-lg);
  margin-bottom: 0.5rem;
  transition: all var(--transition-fast);
}
.stale-warning:hover {
  box-shadow: var(--shadow-sm);
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
  font-size: 0.82rem;
  color: var(--color-warning-text-secondary, #a16207);
  line-height: 1.5;
}

/* ── Grid ── */
.dash-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1.25rem;
}

@media (max-width: 768px) {
  .dash-grid {
    grid-template-columns: 1fr;
    gap: 0.85rem;
  }
}

/* ── 市场温度仪表盘 ── */
.temp-gauges-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 1.25rem;
}

.temp-gauge-card {
  text-align: center;
  padding: 1.25rem 1rem;
  transition: all var(--transition-normal);
}
.temp-gauge-card:hover {
  box-shadow: var(--shadow-md);
  border-color: var(--color-primary-border-weak);
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
  font-size: 0.78rem;
  color: var(--color-text-tertiary);
  line-height: 1.5;
}

.dash-loading {
  padding: 1rem 0;
}

/* ── 通用按钮样式 (供 stale-warning 等使用) ── */
.btn-ghost {
  background: none;
  border: 1px solid var(--color-border);
  color: var(--color-text-secondary);
  cursor: pointer;
  border-radius: var(--radius-md);
  transition: all 0.2s ease;
}
.btn-ghost:hover {
  background: var(--color-bg-hover);
  border-color: var(--color-primary);
  color: var(--color-primary);
}
.btn-ghost.btn-sm {
  padding: 0.3rem 0.7rem;
  font-size: 0.8rem;
}
.btn-secondary {
  padding: 0.5rem 1.25rem;
  font-size: 0.88rem;
  font-weight: 600;
  color: var(--color-primary);
  background: var(--color-primary-bg);
  border: 1px solid var(--color-primary-border);
  border-radius: var(--radius-lg);
  cursor: pointer;
  transition: all 0.2s ease;
}
.btn-secondary:hover {
  background: var(--color-primary-bg-hover);
}

/* ── 移动端响应式 ── */
@media (max-width: 768px) {
  .temp-gauges-row {
    grid-template-columns: 1fr;
    gap: 0.75rem;
  }
  .dash-header {
    flex-direction: column;
  }
  .stale-warning {
    flex-direction: column;
    text-align: center;
    gap: 0.5rem;
  }
}
</style>
