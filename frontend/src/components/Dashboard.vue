<script setup>
import { ref, computed, onMounted, onActivated } from 'vue'
import { getDashboard, runAnalysis, runPanoramaAnalysis, pollPanoramaStatus, getHotTopics, getDailyReport, getDailyReportTask, regenerateDailyReport, submitDailyReportFeedback, listPanoramaRecords, triggerHotspotsAnalysis, getLatestHotspotsAnalysis, getRecommendations, getRecommendationStats, submitRecommendationFeedback, getBondRecommend, listBondRecommendRecords, autoVerifyRecommendations, fetchRecentValuations, getBondMarketTemperature, getHotspotsRelate, getRebalancingSuggestion, listTodayDecisions, updateDecisionStatus, completeDecisionAction, listDueDecisionReviews, submitDecisionReview, getDecisionPrecheck, getTodayOpportunities, scanDailyOpportunities, createDecisionFromOpportunity, watchOpportunity, dailyAdviceAPI } from '../api'
import GaugeChart from './charts/GaugeChart.vue'
import ConfirmDialog from './ConfirmDialog.vue'
import AppToast from './AppToast.vue'
import Skeleton from './ui/Skeleton.vue'
import { useToast } from '../composables/useToast'
import { renderMarkdown } from '../composables/useMarkdown'
import { getBondTempLabel } from '../composables/useDashboardHelpers'
import { useAsyncTask } from '../composables/useAsyncTask'

// Sub-components
import BriefingCard from './dashboard/BriefingCard.vue'
import UndervaluedIndexesCard from './dashboard/UndervaluedIndexesCard.vue'
import PortfolioHealthCard from './dashboard/PortfolioHealthCard.vue'
import HotspotsCard from './dashboard/HotspotsCard.vue'
import CashManagementCard from './dashboard/CashManagementCard.vue'
import DecisionActionList from './dashboard/DecisionActionList.vue'
import DecisionReviewList from './dashboard/DecisionReviewList.vue'
import Icon from './ui/Icon.vue'

const { showToast } = useToast()

// ── 异步任务 composables ──
const { taskState: hotspotsTaskState, start: startHotspotsTask, restore: restoreHotspotsTask } = useAsyncTask('hotspots_analysis')
const { taskState: reportTaskState, start: startReportTask, restore: restoreReportTask } = useAsyncTask('daily_report')
const { taskState: bondTaskState, start: startBondTask, restore: restoreBondTask } = useAsyncTask('bond_recommend')
const { taskState: rebalanceTaskState, start: startRebalanceTask, restore: restoreRebalanceTask } = useAsyncTask('rebalancing')

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
const decisions = ref([])
const decisionsLoading = ref(false)
const precheckStates = ref({})
const decisionReviews = ref([])
const decisionReviewsLoading = ref(false)

// ── 市场热点（自动加载+AI增强） ──
const hotTopics = ref(null)
const hotTopicsFetchedAt = ref('')
const hotTopicsLoading = ref(true)
const hotspotsRelate = ref(null)
const opportunities = ref([])
const opportunitiesLoading = ref(false)

// ── 每日持仓提示 ──
const dailyAdvice = ref(null)
const dailyAdviceLoading = ref(true)
const adviceTopSignals = computed(() => {
  if (!dailyAdvice.value?.signals) return []
  return dailyAdvice.value.signals
    .filter(s => s.severity === 'actionable' || s.severity === 'watch')
    .slice(0, 3)
})

async function loadDailyAdvice() {
  dailyAdviceLoading.value = true
  try {
    const { data } = await dailyAdviceAPI.getToday()
    dailyAdvice.value = data
  } catch (e) {
    console.error('loadDailyAdvice error:', e)
  } finally {
    dailyAdviceLoading.value = false
  }
}

// ── 每日日报自动加载 ──
const dailyReport = ref(null)
const dailyReportLoading = ref(true)
const dailyReportRegenerating = computed(() => reportTaskState.value === 'submitting' || reportTaskState.value === 'running')
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
      await startReportTask(regenerateDailyReport, {
        onComplete: async () => {
          // 重新加载日报内容
          try {
            const { data: res } = await getDailyReport()
            if (res?.has_report) dailyReport.value = res.report
          } catch (e) {
            console.error('重新加载日报失败:', e)
          }
        },
        onError: (err) => {
          console.error('重新生成简报失败:', err)
        }
      })
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
  // 先恢复异步任务状态（如果页面切换前有运行中任务）
  const hotspotsRestored = restoreHotspotsTask()
  const reportRestored = restoreReportTask()
  restoreBondTask()
  restoreRebalanceTask()

  if (!hotspotsRestored) {
    // 没有运行中的任务，尝试加载已有缓存
    try {
      const { data: latest } = await getLatestHotspotsAnalysis()
      if (!latest?.stale && latest?.recommendations?.length) {
        hotspotsAnalysis.value = latest
      }
    } catch (_) {}
  }

  try { await autoVerifyRecommendations() } catch (_) {}

  await Promise.all([
    loadDashboard(),
    loadTodayDecisions(),
    loadDailyAdvice(),
  ])

  // 第二批：延迟 500ms 发送，避免 SQLite 单写锁竞争
  setTimeout(() => {
    Promise.all([
      loadDecisionReviews(),
      loadHotTopics(),
      loadOpportunities(),
    ]).catch(() => {})
  }, 500)

  // 第三批：延迟 1000ms 发送
  setTimeout(() => {
    Promise.all([
      loadDailyReport(),
      loadRecHistory(),
      loadBondTemperature(),
    ]).catch(() => {})
  }, 1000)

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
  restoreReportTask()
  restoreHotspotsTask()
  restoreBondTask()
  restoreRebalanceTask()
  // 如果简报没有运行中的任务，检查后端是否有自动生成中的任务
  if (!dailyReportLoading.value) {
    await checkDailyReportTask()
  }
  await Promise.all([
    loadDashboard(),
    loadTodayDecisions(),
    loadDailyAdvice(),
  ])
  setTimeout(() => {
    Promise.all([
      loadDecisionReviews(),
      loadHotTopics(),
      loadOpportunities(),
    ]).catch(() => {})
  }, 500)
  setTimeout(() => {
    Promise.all([
      loadDailyReport(),
      loadRecHistory(),
      loadBondTemperature(),
    ]).catch(() => {})
  }, 1000)
  // 恢复全景诊断最近一条结果
  try {
    const { data: recs } = await listPanoramaRecords(1)
    if (recs?.records?.length) {
      const rec = recs.records[0]
      // 如果最近一条还在 running，恢复轮询
      if (rec.status === 'running' || rec.execution_status === 'running') {
        panoramaLoading.value = true
        panoramaResult.value = { status: 'running', id: rec.id }
        pollPanoramaStatus(rec.id, async (status) => {
          if (status.status === 'done') {
            panoramaResult.value = { result: status.result, id: rec.id }
            const { data: dashData } = await getDashboard()
            if (data.value && dashData.portfolio_health) {
              data.value.portfolio_health = dashData.portfolio_health
              data.value.portfolio_updated_at = dashData.portfolio_updated_at
            }
            panoramaLoading.value = false
          } else if (status.status === 'error') {
            panoramaResult.value = { error: '分析失败: ' + (status.error || '未知错误') }
            panoramaLoading.value = false
          }
        })
      } else {
        panoramaResult.value = rec
      }
    }
  } catch (_) {}
})

async function loadDailyReport() {
  dailyReportLoading.value = true
  try {
    const { data: res } = await getDailyReport()
    if (res?.has_report && res?.report) {
      dailyReport.value = res.report
      dailyReportLoading.value = false
      return
    }
    // 没有今日报告，检查是否有运行中的异步任务
    await checkDailyReportTask()
  } catch (e) {
  } finally {
    if (!dailyReport.value) dailyReportLoading.value = false
  }
}

// 每日简报异步任务轮询
let _dailyReportPollTimer = null

async function checkDailyReportTask() {
  try {
    const { data: task } = await getDailyReportTask()
    if (task?.has_task && task.status === 'running') {
      // 有运行中的任务，显示"生成中"状态并开始轮询
      dailyReportLoading.value = true
      startDailyReportPolling()
      return true
    }
  } catch (_) {}
  return false
}

function startDailyReportPolling(interval = 3000) {
  stopDailyReportPolling()
  _dailyReportPollTimer = setInterval(async () => {
    try {
      const { data: task } = await getDailyReportTask()
      if (!task?.has_task || task.status !== 'running') {
        // 任务完成或不存在，重新加载报告
        stopDailyReportPolling()
        const { data: res } = await getDailyReport()
        if (res?.has_report && res?.report) {
          dailyReport.value = res.report
        }
        dailyReportLoading.value = false
      }
    } catch (e) {
      stopDailyReportPolling()
      dailyReportLoading.value = false
    }
  }, interval)
}

function stopDailyReportPolling() {
  if (_dailyReportPollTimer) {
    clearInterval(_dailyReportPollTimer)
    _dailyReportPollTimer = null
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

async function loadOpportunities() {
  opportunitiesLoading.value = true
  try {
    const { data: res } = await getTodayOpportunities()
    opportunities.value = res.items || []
  } catch (e) {
    opportunities.value = []
  } finally {
    opportunitiesLoading.value = false
  }
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

async function loadTodayDecisions() {
  decisionsLoading.value = true
  try {
    const { data: res } = await listTodayDecisions()
    decisions.value = res.items || []
  } catch (e) {
    console.warn('加载今日行动失败:', e)
  } finally {
    decisionsLoading.value = false
  }
}

async function loadDecisionReviews() {
  decisionReviewsLoading.value = true
  try {
    const { data: res } = await listDueDecisionReviews()
    decisionReviews.value = res.items || []
  } catch (e) {
    console.warn('加载待复盘失败:', e)
  } finally {
    decisionReviewsLoading.value = false
  }
}

async function handleDecisionStatusChange(decisionId, status) {
  try {
    await updateDecisionStatus(decisionId, status)
    const labels = { accepted: '已接受', deferred: '已暂缓', rejected: '已拒绝' }
    showToast(labels[status] || '状态已更新', 'success')
    await loadTodayDecisions()
    await loadDecisionReviews()
  } catch (e) {
    showToast('更新行动失败', 'error')
  }
}

async function handleCompleteDecisionAction(decisionId, actionId) {
  try {
    await completeDecisionAction(decisionId, actionId)
    showToast('行动项已完成', 'success')
    await loadTodayDecisions()
    await loadDecisionReviews()
  } catch (e) {
    showToast('完成行动项失败', 'error')
  }
}

async function handleDecisionPrecheck(decisionId) {
  const current = precheckStates.value[decisionId]
  if (current?.expanded && current?.result) {
    precheckStates.value = {
      ...precheckStates.value,
      [decisionId]: { ...current, expanded: false },
    }
    return
  }
  precheckStates.value = {
    ...precheckStates.value,
    [decisionId]: { loading: true, expanded: true, result: current?.result || null },
  }
  try {
    const { data: result } = await getDecisionPrecheck(decisionId)
    precheckStates.value = {
      ...precheckStates.value,
      [decisionId]: { loading: false, expanded: true, result },
    }
  } catch (e) {
    showToast('执行前检查失败', 'error')
    precheckStates.value = {
      ...precheckStates.value,
      [decisionId]: { loading: false, expanded: false, result: null },
    }
  }
}

async function handleSubmitDecisionReview(decisionId, outcome) {
  const outcomeLabels = {
    helpful: '这条建议有帮助',
    neutral: '这条建议一般',
    unhelpful: '这条建议无帮助',
  }
  try {
    await submitDecisionReview(decisionId, {
      outcome,
      result_note: outcomeLabels[outcome] || '已完成复盘',
      lesson: outcome === 'unhelpful' ? '后续需要降低类似建议权重' : '',
    })
    showToast('复盘已记录', 'success')
    await loadDecisionReviews()
    await loadTodayDecisions()
  } catch (e) {
    showToast('提交复盘失败', 'error')
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

// 热点分析 loading/error 状态由异步任务驱动
const hotspotLoading = computed(() => hotspotsTaskState.value === 'submitting' || hotspotsTaskState.value === 'running')
const hotspotError = computed(() => hotspotsTaskState.value === 'error')

async function handleBondRecommend() {
  bondLoading.value = true
  bondResult.value = null
  await startBondTask(getBondRecommend, {
    onComplete: (result) => {
      bondResult.value = result?.result || result
      bondLoading.value = false
      loadDashboard()
    },
    onError: (err) => {
      showToast('AI 债券推荐失败：' + err, 'error')
      bondLoading.value = false
    }
  })
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
  hotspotsAnalysis.value = null
  await startHotspotsTask(triggerHotspotsAnalysis, {
    onComplete: async (result) => {
      hotspotsAnalysis.value = result
      hotTopicsAnalyzedAt.value = new Date().toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
      hotTopicsFetchedAt.value = hotTopicsAnalyzedAt.value
      try {
        const { data: opp } = await scanDailyOpportunities({ force_refresh: true })
        opportunities.value = opp.items || []
      } catch (e) {
        console.warn('机会引擎刷新失败:', e)
      }
      try { autoVerifyRecommendations() } catch (_) {}
      loadRecHistory()
    },
    onError: (err) => {
      console.error('热点分析失败:', err)
      hotspotsAnalysis.value = {
        summary: '分析失败',
        recommendations: [],
        analysis_text: err,
      }
    }
  })
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

async function handleCreateOpportunityDecision(item) {
  try {
    await createDecisionFromOpportunity(item.id)
    showToast('已存为理财决策草案', 'success')
    await Promise.all([loadTodayDecisions(), loadDecisionReviews(), loadOpportunities()])
  } catch (e) {
    showToast('保存决策失败', 'error')
  }
}

async function handleWatchOpportunity(item) {
  try {
    await watchOpportunity(item.id)
    showToast('已加入观察列表', 'success')
    await loadOpportunities()
  } catch (e) {
    showToast('加入观察失败', 'error')
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
    panoramaResult.value = { status: 'running', id: res.id }
    pollPanoramaStatus(res.id, async (status) => {
      if (status.status === 'done') {
        panoramaResult.value = { result: status.result, id: res.id }
        const { data: dashData } = await getDashboard()
        if (data.value && dashData.portfolio_health) {
          data.value.portfolio_health = dashData.portfolio_health
          data.value.portfolio_updated_at = dashData.portfolio_updated_at
        }
        panoramaLoading.value = false
      } else if (status.status === 'error') {
        panoramaResult.value = { error: '分析失败: ' + (status.error || '未知错误') }
        panoramaLoading.value = false
      }
    })
  } catch (e) {
    panoramaResult.value = { error: '分析失败: ' + (e.response?.data?.detail || e.message) }
    panoramaLoading.value = false
  }
}

async function generateRebalance() {
  rebalanceLoading.value = true
  rebalanceResult.value = null
  showRebalance.value = true
  await startRebalanceTask(getRebalancingSuggestion, {
    onComplete: (result) => {
      rebalanceResult.value = result
      rebalanceLoading.value = false
    },
    onError: (err) => {
      rebalanceResult.value = { error: '分析失败: ' + err }
      rebalanceLoading.value = false
    }
  })
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
      <Icon name="warning" size="18" class="stale-warning-icon" />
      <div class="stale-warning-body">
        <span class="stale-warning-title">{{ data.data_freshness.stale_count }} 条指数估值数据超过 10 天未更新</span>
        <span class="stale-warning-list">{{ data.data_freshness.stale_indexes.map(i => i.name + '(' + i.stale_days + '天)').join('、') }}</span>
      </div>
      <button class="btn-ghost btn-sm" @click="emit('navigate', 'valuation')">查看 <Icon name="arrow-right" size="12" /></button>
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

    <!-- 今日持仓提示 Top 3 摘要 -->
    <div v-if="dailyAdvice || dailyAdviceLoading" class="advice-summary-card card">
      <div class="advice-summary-header">
        <h3 class="advice-summary-title">📋 今日持仓提示</h3>
        <span v-if="dailyAdvice?.summary?.headline" class="advice-summary-headline">{{ dailyAdvice.summary.headline }}</span>
        <span v-if="dailyAdvice?.generated_at" class="advice-summary-time">{{ dailyAdvice.generated_at.slice(11, 16) }}</span>
      </div>
      <div v-if="dailyAdviceLoading" class="advice-summary-loading">
        <Skeleton variant="text" :count="2" />
      </div>
      <div v-else-if="adviceTopSignals.length > 0" class="advice-summary-list">
        <div v-for="signal in adviceTopSignals" :key="signal.id" class="advice-summary-item">
          <span :class="['advice-summary-dot', `dot-${signal.severity}`]"></span>
          <span class="advice-summary-fund">{{ signal.fund_name || signal.target_name || '未知' }}</span>
          <span class="advice-summary-tag" v-if="signal.action_tag">{{ signal.action_tag }}</span>
          <span class="advice-summary-suggestion" v-if="signal.suggestion">{{ signal.suggestion }}</span>
        </div>
      </div>
      <div v-else class="advice-summary-empty">✅ 今日暂无需行动的提示</div>
      <button class="btn-secondary btn-sm advice-summary-btn" @click="emit('navigate', 'portfolio')">
        查看持仓行动工作台 →
      </button>
    </div>

    <DecisionActionList
      :decisions="decisions"
      :loading="decisionsLoading"
      :precheck-states="precheckStates"
      @status-change="handleDecisionStatusChange"
      @complete-action="handleCompleteDecisionAction"
      @precheck="handleDecisionPrecheck"
    />

    <DecisionReviewList
      :reviews="decisionReviews"
      :loading="decisionReviewsLoading"
      @submit-review="handleSubmitDecisionReview"
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
        :opportunities="opportunities"
        :opportunities-loading="opportunitiesLoading"
        :hotspots-relate="hotspotsRelate || []"
        :rec-history="recHistory"
        :rec-stats="recStats"
        :show-verify="showVerify"
        :feedback-sending="feedbackSending"
        @analyze="confirmHotspots"
        @feedback="submitFeedback"
        @create-decision="handleCreateOpportunityDecision"
        @watch-opportunity="handleWatchOpportunity"
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
.stale-warning-icon { color: var(--color-warning); flex-shrink: 0; }
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

.dash-grid > * {
  transition: all var(--transition-normal);
}
.dash-grid > *:hover {
  transform: var(--hover-lift);
}

/* ── 日期胶囊 ── */
.dash-header .page-desc {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
}
.dash-header .page-desc::after {
  content: '';
  display: inline-block;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-success);
  animation: pulse-dot 2s ease-in-out infinite;
}
@keyframes pulse-dot {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.5; transform: scale(0.8); }
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
  padding: 1.5rem 1rem;
  transition: all var(--transition-normal);
  position: relative;
  overflow: hidden;
}
.temp-gauge-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--color-primary-border-weak), transparent);
  pointer-events: none;
}
.temp-gauge-card:hover {
  box-shadow: var(--shadow-elevated);
  border-color: var(--color-primary-border-weak);
  transform: var(--hover-lift);
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

/* ── 今日持仓提示摘要 ── */
.advice-summary-card {
  margin-bottom: 1rem;
}
.advice-summary-header {
  display: flex;
  align-items: center;
  gap: 0.625rem;
  flex-wrap: wrap;
  margin-bottom: 0.75rem;
}
.advice-summary-title {
  margin: 0;
  font-size: 1rem;
  font-weight: 600;
  color: var(--color-text-primary);
}
.advice-summary-headline {
  font-size: 0.8rem;
  padding: 2px 10px;
  border-radius: 20px;
  background: var(--color-primary-50);
  color: var(--color-primary-800);
  font-weight: 500;
}
.advice-summary-time {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  margin-left: auto;
}
.advice-summary-loading {
  padding: 0.5rem 0;
}
.advice-summary-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  margin-bottom: 0.75rem;
}
.advice-summary-item {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.82rem;
  color: var(--color-text-secondary);
  flex-wrap: wrap;
}
.advice-summary-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.dot-actionable { background: var(--color-profit); }
.dot-watch { background: var(--color-warning); }
.dot-blocked { background: #991b1b; }
.dot-info { background: var(--color-text-muted); }
.advice-summary-fund {
  font-weight: 500;
  color: var(--color-text-primary);
}
.advice-summary-tag {
  font-size: 0.7rem;
  padding: 1px 6px;
  border-radius: 6px;
  background: var(--color-primary-50);
  color: var(--color-primary-800);
}
.advice-summary-suggestion {
  color: var(--color-text-muted);
  font-size: 0.78rem;
}
.advice-summary-empty {
  font-size: 0.85rem;
  color: var(--color-text-secondary);
  margin-bottom: 0.75rem;
}
.advice-summary-btn {
  width: 100%;
}
</style>
