<script setup>
import { ref, onMounted } from 'vue'
import { getDashboard, runAnalysis, runPanoramaAnalysis, getHotTopics, getDailyReport, listPanoramaRecords, getHotspotsAnalysis, getLatestHotspotsAnalysis, getRecommendations, getRecommendationStats, submitRecommendationFeedback, getBondRecommend } from '../api'

const emit = defineEmits(['navigate'])

const loading = ref(true)
const error = ref(null)
const data = ref(null)

// ── 市场热点（自动加载+AI增强） ──
const hotTopics = ref(null)
const hotTopicsLoading = ref(true)
const hotspotLoading = ref(false)
const hotspotError = ref(false)

// ── 每日日报自动加载 ──
const dailyReport = ref(null)
const dailyReportLoading = ref(true)

// ── 再平衡 AI 分析 ──
const rebalanceLoading = ref(false)
const rebalanceResult = ref(null)
const showRebalance = ref(false)

onMounted(async () => {
  // 先加载缓存的最新热点分析结果（要在 loadDailyReport 之前，防止被覆盖）
  try {
    const { data: latest } = await getLatestHotspotsAnalysis()
    if (latest?.recommendations?.length) {
      hotspotsAnalysis.value = latest
    }
  } catch (_) {}

  await Promise.all([
    loadDashboard(),
    loadHotTopics(),
    loadDailyReport(),
    loadRecHistory(),
  ])
  if (!rebalanceResult.value) {
    try {
      const { data: rec } = await listPanoramaRecords(1)
      if (rec?.records?.length && rec.records[0].result_data) {
        rebalanceResult.value = rec.records[0].result_data
        showRebalance.value = true
      }
    } catch (_) {}
  }
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
    }
  } catch (e) {
    // 静默失败，不影响看板主流程
  } finally {
    hotTopicsLoading.value = false
  }
}

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
const feedbackToast = ref({ show: false, message: '', type: '' })
const feedbackSending = ref({})

function showFeedbackToast(message, type = 'success') {
  feedbackToast.value = { show: true, message, type }
  setTimeout(() => { feedbackToast.value.show = false }, 2500)
}

async function submitFeedback(rec, rating) {
  if (feedbackSending.value[rec.id]) return
  feedbackSending.value[rec.id] = rating
  try {
    await submitRecommendationFeedback(rec.id, { rating, comment: '' })
    showFeedbackToast(rating === 'helpful' ? '已标记有用' : '已标记反馈')
  } catch (e) {
    showFeedbackToast('提交失败', 'error')
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
    const { data } = await getBondRecommend()
    bondResult.value = data.result
  } catch (e) {
    alert('AI 债券推荐失败：' + (e.response?.data?.detail || e.message))
  } finally {
    bondLoading.value = false
  }
}

async function generateHotspots() {
  hotspotLoading.value = true
  hotspotsAnalysis.value = null
  hotspotError.value = false
  try {
    const { data } = await getHotspotsAnalysis()
    hotspotsAnalysis.value = data
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

// ── 生成再平衡建议 ──
async function generateRebalance() {
  rebalanceLoading.value = true
  rebalanceResult.value = null
  showRebalance.value = true
  try {
    const { data: res } = await runPanoramaAnalysis()
    rebalanceResult.value = res.result
  } catch (e) {
    rebalanceResult.value = '分析失败: ' + (e.response?.data?.detail || e.message)
  } finally {
    rebalanceLoading.value = false
  }
}

async function regenerateRebalance() {
  rebalanceLoading.value = true
  rebalanceResult.value = null
  showRebalance.value = true
  try {
    const { data: res } = await runPanoramaAnalysis()
    rebalanceResult.value = res.result
  } catch (e) {
    rebalanceResult.value = '分析失败: ' + (e.response?.data?.detail || e.message)
  } finally {
    rebalanceLoading.value = false
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
      <button class="btn-ghost refresh-btn" @click="loadDashboard" :disabled="loading">
        <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24" :class="{ spinning: loading }">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
        </svg>
        刷新
      </button>
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
          <span v-if="data?.undervalued_indexes?.length" class="card-badge">{{ data.undervalued_indexes.length }}只</span>
        </div>
        <div v-if="!data?.undervalued_indexes?.length" class="card-empty">
          <p>暂无低估指数数据</p>
          <p class="card-empty-hint">通过文章导入估值数据后自动展示</p>
        </div>
        <div v-else class="card-body">
          <div v-for="idx in data.undervalued_indexes.slice(0, 5)" :key="idx.index_code" class="index-row" @click="emit('navigate', 'valuation')">
            <div class="index-info">
              <span class="index-name">{{ idx.index_name || idx.index_code }}</span>
              <span class="index-meta">{{ idx.metric_type }} {{ idx.current_value }}</span>
            </div>
            <div class="index-percentile">
              <div class="percentile-bar-bg">
                <div class="percentile-bar" :style="{ width: idx.percentile + '%', background: getPercentileColor(idx.percentile) }"></div>
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
          <button
            v-if="data?.portfolio_health && !rebalanceLoading"
            class="btn-primary btn-sm"
            @click="rebalanceResult ? (showRebalance = !showRebalance) : generateRebalance()"
          >
            {{ showRebalance ? '收起建议' : rebalanceResult ? '展开建议' : 'AI 再平衡建议' }}
          </button>
        </div>
        <div v-if="!data?.portfolio_health" class="card-empty">
          <p>暂无持仓数据</p>
          <p class="card-empty-hint">在持仓管理中添加基金后自动分析</p>
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
              <p>正在生成再平衡建议...</p>
            </div>
            <div v-else class="rebalance-result">
              <div class="rebalance-header">
                <span class="rebalance-title">AI 再平衡建议</span>
                <div class="rebalance-header-actions">
                  <button class="btn-ghost btn-sm" @click="regenerateRebalance">重新生成</button>
                  <button class="btn-ghost btn-sm" @click="showRebalance = false">收起</button>
                </div>
              </div>
              <div class="rebalance-content">{{ rebalanceResult }}</div>
            </div>
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
          <button
            v-if="!hotspotLoading"
            class="btn-primary btn-sm"
            @click="generateHotspots"
          >{{ hotspotsAnalysis ? '重新分析' : 'AI 分析' }}</button>
        </div>

        <!-- 自动加载的新闻列表 -->
        <div v-if="hotTopics?.length && !hotspotsAnalysis && !hotspotLoading" class="card-body news-list">
          <div v-for="(item, i) in hotTopics.slice(0, 4)" :key="i" class="news-item">
            <a v-if="item.url" :href="item.url" target="_blank" rel="noopener" class="news-title">{{ item.title }}</a>
            <span v-else class="news-title">{{ item.title }}</span>
            <p class="news-summary">{{ item.summary?.slice(0, 120) }}{{ item.summary?.length > 120 ? '...' : '' }}</p>
            <div class="news-meta">
              <span class="news-source">{{ item.source }}</span>
              <span v-if="item.date" class="news-date">{{ item.date?.slice(0, 10) }}</span>
            </div>
          </div>
          <p class="hotspots-hint">点击「AI 分析」获取结构化投资机会推荐</p>
        </div>

        <!-- 热点数据加载中 -->
        <div v-if="hotTopicsLoading && !hotspotsAnalysis && !hotspotLoading" class="card-empty">
          <div class="spinner" style="width:20px;height:20px;border-width:2px"></div>
          <p style="margin-top:0.5rem">加载今日热点...</p>
        </div>

        <!-- 无数据 → 提示用户点击 -->
        <div v-if="!hotTopics?.length && !hotTopicsLoading && !hotspotsAnalysis && !hotspotLoading" class="card-empty">
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
          <p class="hotspots-summary">{{ hotspotsAnalysis.summary }}</p>
          <div v-for="(rec, i) in hotspotsAnalysis.recommendations" :key="i" class="rec-card">
            <div class="rec-main">
              <span :class="['rec-badge', 'rec-' + rec.direction]">
                {{ rec.direction === 'up' ? '关注' : rec.direction === 'down' ? '回避' : '观察' }}
              </span>
              <span class="rec-name">{{ rec.index_name }}</span>
              <span v-if="rec.index_code" class="rec-code">{{ rec.index_code }}</span>
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
          <span v-if="recStats.accuracy != null" class="verify-accuracy">命中率 {{ recStats.accuracy }}%</span>
          <span v-else class="verify-accuracy pending">暂无验证数据</span>
          <svg width="12" height="12" fill="none" stroke="currentColor" viewBox="0 0 24 24" :class="{ rotated: showVerify }">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/>
          </svg>
        </div>
        <div v-if="showVerify && recHistory?.length" class="verify-list">
          <div v-for="rec in recHistory.slice(0, 10)" :key="rec.id" class="verify-item">
            <span :class="['rec-badge', 'rec-' + rec.direction]">
              {{ rec.direction === 'up' ? '关注' : rec.direction === 'down' ? '回避' : '观察' }}
            </span>
            <span class="verify-name">{{ rec.index_name }}</span>
            <span :class="['verify-status', 'vs-' + rec.status]">
              {{ rec.status === 'correct' ? '✅' : rec.status === 'wrong' ? '❌' : '⏳' }}
              {{ rec.status === 'correct' ? '正确' : rec.status === 'wrong' ? '错误' : '待验证' }}
              <template v-if="rec.change_pct != null">({{ rec.change_pct > 0 ? '+' : '' }}{{ rec.change_pct }}%)</template>
            </span>
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
          <button
            v-if="data?.cash_management?.balance > 0"
            class="btn-primary btn-sm"
            @click="handleBondRecommend"
            :disabled="bondLoading"
          >{{ bondResult ? '重新分析' : 'AI 债券推荐' }}</button>
        </div>
        <div class="card-body">
          <div class="cash-balance-row">
            <span class="cash-label">可用零钱</span>
            <span class="cash-value">¥{{ (data?.cash_management?.balance || 0).toLocaleString() }}</span>
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
                  <div class="alloc-bar-fill" :style="{ width: item.ratio + '%', background: ['#6366f1', '#10b981', '#f59e0b'][i % 3] }"></div>
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
            <template v-if="!bondLoading">
              <div class="bond-ai-header">
                <span class="bond-ai-summary">{{ bondResult.summary }}</span>
                <span class="bond-ai-market">{{ bondResult.market_assessment }}</span>
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

  <!-- 反馈 Toast -->
  <Teleport to="body">
    <Transition name="fade">
      <div v-if="feedbackToast.show" :class="['toast', 'toast-' + feedbackToast.type]">
        {{ feedbackToast.message }}
      </div>
    </Transition>
  </Teleport>
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

@keyframes spin {
  to { transform: rotate(360deg); }
}

.dash-error {
  padding: 2rem;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.75rem;
  color: var(--color-text-muted);
}

/* ── 数据新鲜度警告 ── */
.stale-warning {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.75rem 1rem;
  background: #fef3c7;
  border: 1px solid #fbbf24;
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
  color: #92400e;
}
.stale-warning-list {
  font-size: 0.8rem;
  color: #a16207;
  line-height: 1.4;
}

/* ── AI 债券推荐 ── */
.bond-ai-result {
  margin-top: 1rem;
  padding: 0.75rem;
  background: #f0f9ff;
  border: 1px solid #bae6fd;
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
  color: #0369a1;
}
.bond-ai-market {
  font-size: 0.8rem;
  color: #0284c7;
}
.bond-ai-analysis {
  font-size: 0.8rem;
  color: #475569;
  margin-bottom: 0.5rem;
  line-height: 1.4;
}
.bond-ai-label {
  font-weight: 600;
  color: #334155;
}
.bond-rec-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.bond-rec-item {
  padding: 0.5rem;
  background: white;
  border: 1px solid #e2e8f0;
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
  color: #0f172a;
}
.bond-rec-code {
  font-size: 0.75rem;
  color: #64748b;
  font-family: monospace;
}
.bond-rec-type {
  font-size: 0.7rem;
  padding: 1px 6px;
  border-radius: 4px;
  background: #dbeafe;
  color: #1d4ed8;
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
  color: #475569;
  flex: 1;
}
.bond-rec-amount {
  font-size: 0.85rem;
  font-weight: 600;
  color: #059669;
}
.bond-rec-desc {
  font-size: 0.7rem;
  color: #94a3b8;
}
.bond-rec-alt {
  margin-top: 0.4rem;
  font-size: 0.78rem;
  color: #78716c;
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
}

/* ── Card ── */
.dash-card {
  padding: 1rem 1.25rem;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
}

.card-title-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-weight: 600;
  font-size: 0.95rem;
  color: var(--color-text-primary);
}

.card-icon {
  color: var(--color-primary);
  flex-shrink: 0;
}

.card-badge {
  font-size: 0.7rem;
  padding: 0.15rem 0.5rem;
  border-radius: 999px;
  background: var(--color-primary-bg);
  color: var(--color-primary);
  font-weight: 600;
}

.card-body {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.card-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 2rem 0;
  text-align: center;
  color: var(--color-text-muted);
  font-size: 0.85rem;
}

.card-empty-hint {
  font-size: 0.75rem;
  margin-top: 0.25rem;
}

.card-loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 2rem 0;
  gap: 0.75rem;
  color: var(--color-text-muted);
  font-size: 0.85rem;
}

.spinner {
  width: 24px;
  height: 24px;
  border: 2.5px solid var(--color-border);
  border-top-color: var(--color-primary);
  border-radius: 50%;
  animation: spin 0.7s linear infinite;
}

.card-actions {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.25rem;
}

/* ── 低估指数 ── */
.index-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.4rem 0.35rem;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: background var(--transition-fast);
}

.index-row:hover {
  background: var(--color-bg-hover);
}

.index-info {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 0 0 auto;
  width: 100px;
}

.index-name {
  font-size: 0.82rem;
  font-weight: 600;
  color: var(--color-text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.index-meta {
  font-size: 0.65rem;
  color: var(--color-text-muted);
}

.index-percentile {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  flex: 1;
  min-width: 0;
}

.percentile-bar-bg {
  flex: 1;
  height: 5px;
  background: var(--color-bg-input);
  border-radius: 4px;
  overflow: hidden;
  min-width: 40px;
}

.percentile-bar {
  height: 100%;
  border-radius: 4px;
  transition: width 0.4s ease;
}

.percentile-value {
  font-size: 0.8rem;
  font-weight: 700;
  width: 34px;
  text-align: right;
  flex-shrink: 0;
}

.assessment-tag {
  font-size: 0.6rem;
  font-weight: 600;
  padding: 0.05rem 0.35rem;
  border-radius: 3px;
  white-space: nowrap;
  flex-shrink: 0;
  letter-spacing: 0.02em;
}

.btn-link {
  background: none;
  border: none;
  color: var(--color-primary);
  font-size: 0.78rem;
  cursor: pointer;
  padding: 0.25rem 0;
  text-align: left;
  align-self: flex-start;
}

.btn-link:hover {
  text-decoration: underline;
}

/* ── 持仓健康度 ── */
.health-metrics {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 0.5rem;
}

.metric-item {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}

.metric-label {
  font-size: 0.7rem;
  color: var(--color-text-muted);
}

.metric-value {
  font-size: 1rem;
  font-weight: 700;
  color: var(--color-text-primary);
}

.metric-value.profit {
  color: var(--color-danger);
}

.metric-value.loss {
  color: var(--color-success);
}

.concentration-row {
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
  padding: 0.5rem 0;
  border-top: 1px solid var(--color-border-light);
  border-bottom: 1px solid var(--color-border-light);
}

.concentration-icon {
  font-size: 1.1rem;
  line-height: 1.4;
}

.concentration-text {
  font-size: 0.8rem;
  color: var(--color-text-secondary);
  margin-bottom: 0.25rem;
}

.concentration-bar-bg {
  height: 5px;
  background: var(--color-bg-input);
  border-radius: 3px;
  overflow: hidden;
  max-width: 200px;
}

.concentration-bar {
  height: 100%;
  border-radius: 3px;
  transition: width 0.3s ease;
}

/* 类型分布 */
.type-dist {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}

.type-item {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}

.type-name {
  font-size: 0.72rem;
  color: var(--color-text-secondary);
  width: 48px;
  flex-shrink: 0;
}

.type-bar-bg {
  flex: 1;
  height: 5px;
  background: var(--color-bg-input);
  border-radius: 3px;
  overflow: hidden;
  max-width: 160px;
}

.type-bar {
  height: 100%;
  background: var(--color-primary);
  border-radius: 3px;
}

.type-pct {
  font-size: 0.72rem;
  color: var(--color-text-muted);
  width: 28px;
  text-align: right;
}

/* ── AI 再平衡 ── */
.rebalance-section {
  border-top: 1px solid var(--color-border-light);
  padding-top: 0.5rem;
}

.rebalance-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.rebalance-title {
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--color-primary);
}

.rebalance-result {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.rebalance-content {
  font-size: 0.82rem;
  line-height: 1.6;
  color: var(--color-text-secondary);
  white-space: pre-wrap;
  max-height: 360px;
  overflow-y: auto;
  background: var(--color-bg-input);
  border-radius: var(--radius-sm);
  padding: 0.75rem;
}

/* ── 热门机会 ── */
.hotspots-body {
  max-height: 420px;
  overflow-y: auto;
}

.hotspots-summary {
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--color-text-primary);
  margin-bottom: 0.75rem;
  line-height: 1.5;
}

.rec-card {
  display: flex;
  align-items: flex-start;
  gap: 0.25rem;
  padding: 0.5rem 0.5rem;
  border-radius: var(--radius-sm);
  background: var(--color-bg-input);
  margin-bottom: 0.4rem;
}

.rec-main {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex: 1;
  min-width: 0;
  flex-wrap: wrap;
}

.rec-code {
  font-size: 0.65rem;
  color: var(--color-text-muted);
  font-family: monospace;
  flex-shrink: 0;
}

.rec-portfolio {
  font-size: 0.6rem;
  padding: 0.1rem 0.35rem;
  border-radius: 3px;
  white-space: nowrap;
  flex-shrink: 0;
  font-weight: 600;
}
.rp-already_have { background: #6366f118; color: #818cf8; }
.rp-can_add { background: #10b98118; color: #10b981; }
.rp-reduce { background: #ef444418; color: #ef4444; }
.rp-new { background: #f59e0b18; color: #f59e0b; }

.rec-actions {
  display: flex;
  gap: 0.15rem;
  flex-shrink: 0;
  padding-top: 0.1rem;
}

.rec-feedback-btn {
  width: 24px;
  height: 24px;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  color: var(--color-text-muted);
  opacity: 0.4;
  transition: all 0.15s;
  padding: 0;
}

.rec-feedback-btn:hover {
  opacity: 1;
}

.rec-feedback-btn.helpful:hover,
.rec-feedback-btn.helpful.active {
  color: #10b981;
  background: #10b98118;
  opacity: 1;
}

.rec-feedback-btn.unhelpful:hover,
.rec-feedback-btn.unhelpful.active {
  color: #ef4444;
  background: #ef444418;
  opacity: 1;
}

.rec-feedback-btn:disabled {
  cursor: default;
}

.rec-badge {
  font-size: 0.65rem;
  font-weight: 700;
  padding: 0.15rem 0.4rem;
  border-radius: 3px;
  white-space: nowrap;
  flex-shrink: 0;
}
.rec-up { background: #10b98118; color: #10b981; }
.rec-down { background: #ef444418; color: #ef4444; }
.rec-watch { background: #f59e0b18; color: #f59e0b; }

.rec-name {
  font-size: 0.82rem;
  font-weight: 600;
  color: var(--color-text-primary);
  flex-shrink: 0;
}

.rec-reason {
  font-size: 0.72rem;
  color: var(--color-text-muted);
  flex: 1;
  min-width: 0;
}

.rec-conf {
  font-size: 0.6rem;
  padding: 0.1rem 0.3rem;
  border-radius: 3px;
  white-space: nowrap;
  flex-shrink: 0;
}
.conf-high { background: #10b98118; color: #10b981; }
.conf-medium { background: #f59e0b18; color: #f59e0b; }
.conf-low { background: #6b728018; color: #6b7280; }

.hotspots-details {
  margin-top: 0.5rem;
}
.hotspots-details-summary {
  font-size: 0.75rem;
  color: var(--color-primary);
  cursor: pointer;
  user-select: none;
}
.hotspots-details-summary:hover {
  opacity: 0.8;
}

.hotspots-content {
  font-size: 0.82rem;
  line-height: 1.6;
  color: var(--color-text-secondary);
  white-space: pre-wrap;
  margin-top: 0.5rem;
  padding: 0.5rem;
  background: var(--color-bg-input);
  border-radius: var(--radius-sm);
}

.news-list {
  max-height: 340px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.news-item {
  padding: 0.5rem 0.5rem;
  border-radius: var(--radius-sm);
  border-left: 3px solid var(--color-primary-200);
  background: var(--color-bg-input);
  transition: background var(--transition-fast);
}

.news-item:hover {
  background: var(--color-bg-hover);
}

.news-title {
  font-size: 0.82rem;
  font-weight: 600;
  color: var(--color-text-primary);
  text-decoration: none;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.news-title:hover {
  color: var(--color-primary);
}

.news-summary {
  font-size: 0.72rem;
  color: var(--color-text-muted);
  margin: 0.25rem 0 0;
  line-height: 1.4;
}

.news-meta {
  display: flex;
  gap: 0.5rem;
  font-size: 0.65rem;
  color: var(--color-text-muted);
  margin-top: 0.2rem;
}

.news-source {
  color: var(--color-primary);
  font-weight: 500;
}

.hotspots-hint {
  font-size: 0.7rem;
  color: var(--color-text-muted);
  text-align: center;
  margin: 0.25rem 0 0;
}

/* ── 零钱配置 ── */
.cash-balance-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  padding: 0.25rem 0;
}

.cash-label {
  font-size: 0.8rem;
  color: var(--color-text-muted);
}

.cash-value {
  font-size: 1.35rem;
  font-weight: 700;
  color: var(--color-text-primary);
}

.bond-info {
  padding: 0.5rem 0;
}

.bond-temp-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.bond-temp-label {
  font-size: 0.78rem;
  color: var(--color-text-muted);
}

.bond-temp-value {
  font-size: 1.1rem;
  font-weight: 700;
  padding: 0.1rem 0.5rem;
  border-radius: 6px;
  background: var(--color-bg-input);
}

.bond-temp-value.bond-cold {
  color: #3b82f6;
}

.bond-temp-value.bond-hot {
  color: #ef4444;
}

.bond-yield {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  margin-left: auto;
}

.bond-trend {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  margin-top: 0.4rem;
  padding-top: 0.4rem;
  border-top: 1px solid var(--color-border-light);
}

.trend-item {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.72rem;
}

.trend-label {
  color: var(--color-text-muted);
  width: 64px;
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
  font-size: 0.62rem;
  color: var(--color-text-muted);
  margin-top: 0.15rem;
  font-style: italic;
}

.bond-unavailable p {
  font-size: 0.78rem;
  color: var(--color-text-muted);
}

.allocation-section {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  padding-top: 0.5rem;
  border-top: 1px solid var(--color-border-light);
}

.allocation-summary {
  font-size: 0.8rem;
  color: var(--color-text-secondary);
  line-height: 1.5;
  margin: 0;
}

.allocation-chart {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.allocation-bar-item {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}

.alloc-bar-label {
  display: flex;
  justify-content: space-between;
  font-size: 0.75rem;
  color: var(--color-text-secondary);
}

.alloc-bar-pct {
  font-weight: 600;
  color: var(--color-text-primary);
}

.alloc-bar-bg {
  height: 6px;
  background: var(--color-bg-input);
  border-radius: 3px;
  overflow: hidden;
}

.alloc-bar-fill {
  height: 100%;
  border-radius: 3px;
  transition: width 0.3s ease;
}

.alloc-bar-desc {
  font-size: 0.65rem;
  color: var(--color-text-muted);
}

.alloc-money {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  padding-top: 0.25rem;
}

.alloc-money-item {
  display: flex;
  gap: 0.3rem;
  font-size: 0.72rem;
  color: var(--color-text-muted);
}

.alloc-money-name {
  color: var(--color-text-secondary);
}

.alloc-money-val {
  font-weight: 600;
}

/* ── Buttons ── */
.btn-sm {
  font-size: 0.75rem;
  padding: 0.3rem 0.65rem;
}


/* ── 推荐验证历史 ── */
.verify-bar {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.4rem 0.5rem;
  border-top: 1px solid var(--color-border);
  cursor: pointer;
  font-size: 0.72rem;
  user-select: none;
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
  transition: transform 0.2s;
  color: var(--color-text-muted);
}
.verify-bar svg.rotated {
  transform: rotate(180deg);
}

.verify-list {
  border-top: 1px solid var(--color-border);
  max-height: 300px;
  overflow-y: auto;
  padding: 0.3rem 0;
}
.verify-item {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.3rem 0.5rem;
  font-size: 0.72rem;
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
  font-size: 0.65rem;
  white-space: nowrap;
}
.vs-correct { color: #10b981; }
.vs-wrong { color: #ef4444; }
.vs-pending { color: var(--color-text-muted); }
</style>

<style>
.toast {
  position: fixed;
  top: 1rem;
  right: 1rem;
  z-index: 10000;
  padding: 0.6rem 1rem;
  border-radius: 8px;
  font-size: 0.82rem;
  font-weight: 500;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  box-shadow: 0 4px 12px rgba(0,0,0,0.15);
  color: var(--color-text-primary);
  pointer-events: none;
}
.toast-success { border-color: #10b981; color: #10b981; }
.toast-error { border-color: #ef4444; color: #ef4444; }
</style>