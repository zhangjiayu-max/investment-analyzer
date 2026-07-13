<script setup>
import { ref, computed, onMounted, onActivated } from 'vue'
import {
  listAlerts, getUnreadAlertCount, markAlertRead, deleteAlert, scanPortfolioAlerts,
  dailyAdviceAPI, getAlertHistory, comprehensiveInterpretation, acknowledgeAlert,
} from '../api'
import { useToast } from '../composables/useToast'
import ConfirmDialog from './ConfirmDialog.vue'
import Skeleton from './ui/Skeleton.vue'
import { renderMarkdown } from '../composables/useMarkdown'

const emit = defineEmits(['navigate'])

// ── Tab 切换 ──
const activeTab = ref('advice') // 'advice' | 'alerts' | 'merged'

// ── 每日持仓提示 state ──
const dailyAdvice = ref(null)
const dailyAdviceLoading = ref(false)
const dailyAdviceError = ref(null)
const aiResponseMap = ref({})        // signalId -> { loading, text, error }
const adviceActionLoading = ref({})  // signalId -> action type

// P1-3.3 AI 综合解读
const comprehensiveLoading = ref(false)
const comprehensiveResult = ref(null)
const comprehensiveError = ref(null)

// ── 风险预警 state ──
const alerts = ref([])
const unreadAlertCount = ref(0)
const alertScanning = ref(false)
const alertHistoryMap = ref({})  // alertId -> { loading, history, show }
const collapsedInfoAlerts = ref(new Set())  // 折叠的 info 级预警 key
const ackLoading = ref({})  // alertId -> loading（业务确认状态更新中）

// ── ConfirmDialog ──
const confirmState = ref({ visible: false, title: '', message: '', danger: false, onConfirm: null })

// ── 统计 ──
const pendingAdviceCount = computed(() => {
  if (!dailyAdvice.value?.signals) return 0
  return dailyAdvice.value.signals.filter(s => s.severity === 'actionable').length
})
const dangerAlertCount = computed(() => alerts.value.filter(a => a.severity === 'danger').length)

// ── 每日持仓提示函数 ──
async function loadDailyAdvice() {
  dailyAdviceLoading.value = true
  dailyAdviceError.value = null
  try {
    const { data } = await dailyAdviceAPI.getToday()
    dailyAdvice.value = data
  } catch (e) {
    dailyAdviceError.value = e?.response?.data?.detail || e?.message || '加载失败'
    console.error('loadDailyAdvice error:', e)
  } finally {
    dailyAdviceLoading.value = false
  }
}

async function handleAdviceAction(signalId, action) {
  adviceActionLoading.value[signalId] = action
  try {
    if (action === 'create-candidate') {
      await dailyAdviceAPI.createCandidate(signalId)
      useToast().showToast('已保存为决策候选', 'success')
    } else if (action === 'ignore') {
      await dailyAdviceAPI.ignore(signalId)
      useToast().showToast('已忽略', 'info')
      await loadDailyAdvice()
    } else if (action === 'ask-ai') {
      aiResponseMap.value[signalId] = { loading: true, text: '', error: null }
      const { data } = await dailyAdviceAPI.askAI(signalId)
      aiResponseMap.value[signalId] = {
        loading: false,
        text: data?.ai_response || data?.ai_explanation || data?.message || '暂无回复',
        error: null,
      }
    } else if (action === 'refresh') {
      await dailyAdviceAPI.run()
      useToast().showToast('提示已刷新', 'success')
      await loadDailyAdvice()
    }
  } catch (e) {
    const msg = e?.response?.data?.detail || e?.message || '操作失败'
    if (action === 'ask-ai') {
      aiResponseMap.value[signalId] = { loading: false, text: '', error: msg }
    } else {
      useToast().showToast(msg, 'error')
    }
  } finally {
    adviceActionLoading.value[signalId] = null
  }
}

// P1-3.3 AI 综合解读（LLM 默认关闭，用户主动触发）
async function handleComprehensiveInterpretation() {
  comprehensiveLoading.value = true
  comprehensiveError.value = null
  try {
    const { data } = await comprehensiveInterpretation()
    comprehensiveResult.value = data
    if (data?.cached) useToast().showToast('命中 30 分钟缓存', 'info')
  } catch (e) {
    const status = e?.response?.status
    if (status === 403) {
      comprehensiveError.value = 'AI 综合解读已关闭（llm_cost.daily_advice_ai_interpretation=false），需在系统配置中开启'
    } else if (status === 400) {
      comprehensiveError.value = '今日无信号，无需解读'
    } else {
      comprehensiveError.value = e?.response?.data?.detail || e?.message || 'AI 解读失败'
    }
  } finally {
    comprehensiveLoading.value = false
  }
}

function formatAdviceAmount(amount) {
  if (!amount && amount !== 0) return ''
  if (amount >= 10000) return `¥${(amount / 10000).toFixed(1)}万`
  return `¥${amount.toLocaleString()}`
}

function severityLabel(level) {
  return { actionable: '可行动', watch: '观察', blocked: '风险拦截', info: '信息' }[level] || level
}

function severityClass(level) {
  return `advice-severity-${level || 'info'}`
}

function tagLabel(tag) {
  const map = {
    dca_tier1: '一档定投', dca_tier2: '二档定投', dca_tier3: '三档定投',
    pause_buy: '暂缓加仓', reduce_review: '减仓复核', hold: '继续持有', rebalance: '再平衡',
  }
  return map[tag] || tag
}

function adviceSummaryClass(headline) {
  if (!headline) return ''
  if (headline.includes('加仓') || headline.includes('定投')) return 'advice-headline-buy'
  if (headline.includes('减仓') || headline.includes('降低')) return 'advice-headline-sell'
  if (headline.includes('观望') || headline.includes('持有')) return 'advice-headline-hold'
  if (headline.includes('刷新') || headline.includes('数据')) return 'advice-headline-data'
  return ''
}

// ── 风险预警函数 ──
async function loadAlerts() {
  try {
    const { data } = await listAlerts(true, 50)
    alerts.value = data.alerts || []
    const { data: cnt } = await getUnreadAlertCount()
    unreadAlertCount.value = cnt.count
  } catch (e) { /* silently ignore */ }
}

async function handleMarkAlertRead(alertId, title, severity) {
  try {
    // P0-1: 改为按 alert_type + related_fund_code + severity 分组（跨日合并后用 fund_code 而非 title）
    const target = alerts.value.find(a => a.latest_id === alertId)
    if (!target) return
    const sameGroup = alerts.value.filter(a =>
      a.alert_type === target.alert_type
      && (a.related_fund_code || '') === (target.related_fund_code || '')
      && a.severity === target.severity
    )
    for (const a of sameGroup) {
      await markAlertRead(a.latest_id)
    }
    alerts.value = alerts.value.filter(a =>
      !(a.alert_type === target.alert_type
        && (a.related_fund_code || '') === (target.related_fund_code || '')
        && a.severity === target.severity)
    )
    const cnt = sameGroup.reduce((s, a) => s + (a.cnt || 1), 0)
    unreadAlertCount.value = Math.max(0, unreadAlertCount.value - cnt)
  } catch (e) {
    useToast().showToast('操作失败', 'error')
  }
}

function handleDeleteAlert(alertId, title, severity) {
  const target = alerts.value.find(a => a.latest_id === alertId)
  if (!target) return
  confirmState.value = {
    visible: true,
    title: '删除预警',
    message: `确定删除「${title}」${target.cnt > 1 ? `（共 ${target.cnt} 条）` : ''}？该操作不可恢复。`,
    danger: true,
    onConfirm: async () => {
      confirmState.value.visible = false
      try {
        const sameGroup = alerts.value.filter(a =>
          a.alert_type === target.alert_type
          && (a.related_fund_code || '') === (target.related_fund_code || '')
          && a.severity === target.severity
        )
        for (const a of sameGroup) {
          await deleteAlert(a.latest_id)
        }
        alerts.value = alerts.value.filter(a =>
          !(a.alert_type === target.alert_type
            && (a.related_fund_code || '') === (target.related_fund_code || '')
            && a.severity === target.severity)
        )
        const cnt = sameGroup.reduce((s, a) => s + (a.cnt || 1), 0)
        unreadAlertCount.value = Math.max(0, unreadAlertCount.value - cnt)
        useToast().showToast('已删除', 'success')
      } catch (e) {
        useToast().showToast('操作失败', 'error')
      }
    },
  }
}

async function handleScanAlerts() {
  alertScanning.value = true
  try {
    const { data } = await scanPortfolioAlerts()
    if (data.generated > 0) {
      useToast().showToast(`巡检完成，发现 ${data.generated} 条新预警`, 'success')
      await loadAlerts()
    } else {
      useToast().showToast('巡检完成，未发现新的风险', 'success')
    }
  } catch (e) {
    useToast().showToast('巡检失败：' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    alertScanning.value = false
  }
}

// 业务确认状态：采纳/忽略（用于后续回测用户决策质量）
async function handleAcknowledgeAlert(alert, status) {
  const id = alert.latest_id
  if (!id) return
  ackLoading.value[id] = true
  try {
    await acknowledgeAlert(id, status)
    alert.acknowledged_status = status
    useToast().showToast(status === 'acknowledged' ? '已采纳' : '已忽略', 'info')
  } catch (e) {
    useToast().showToast('操作失败', 'error')
  } finally {
    ackLoading.value[id] = false
  }
}

// P1-3.1 预警历史对比
async function loadAlertHistory(alertId) {
  const cur = alertHistoryMap.value[alertId]
  if (cur?.show) {
    alertHistoryMap.value[alertId] = { ...cur, show: false }
    return
  }
  if (cur?.history) {
    alertHistoryMap.value[alertId] = { ...cur, show: true }
    return
  }
  alertHistoryMap.value[alertId] = { loading: true, history: null, show: true }
  try {
    const { data } = await getAlertHistory(alertId, 30)
    alertHistoryMap.value[alertId] = { loading: false, history: data.history || [], show: true }
  } catch (e) {
    alertHistoryMap.value[alertId] = {
      loading: false, history: null, show: true,
      error: e?.response?.data?.detail || e?.message || '加载失败',
    }
  }
}

// P1-3.2 预警→交易联动
function alertActionLabel(alertType) {
  const map = {
    buy_drop_alert: ['查看持仓', '快速补仓', '快速卖出'],
    drawdown_alert: ['查看持仓', '止损评估'],
    concentration_alert: ['查看持仓', '调仓建议'],
    valuation_opportunity: ['一键买入', '查看估值图'],
    valuation_alert: ['查看持仓', '减仓复核'],
    cash_idle: ['查看持仓'],
    stale_data: ['刷新净值'],
  }
  return map[alertType] || ['查看持仓']
}

function handleAlertAction(alert, actionLabel) {
  const fundCode = alert.related_fund_code || ''
  const fundName = alert.related_fund_name || fundCode
  if (actionLabel === '查看持仓') {
    emit('navigate', 'portfolio')
    return
  }
  if (actionLabel === '查看估值图') {
    emit('navigate', 'valuation')
    return
  }
  // 其他交易动作跳转持仓管理，提示基金代码
  if (fundCode) {
    useToast().showToast(`请在持仓管理中对 ${fundName}（${fundCode}）执行${actionLabel}`, 'info')
  }
  emit('navigate', 'portfolio')
}

// P1-3.1 info 级预警折叠
function alertKey(a) {
  // P0-1: 改用 alert_type + fund_code + severity 作为唯一键（与后端分组一致）
  return `${a.alert_type}:${a.related_fund_code || ''}:${a.severity}`
}
function toggleInfoAlert(a) {
  const k = alertKey(a)
  if (collapsedInfoAlerts.value.has(k)) {
    collapsedInfoAlerts.value.delete(k)
  } else {
    collapsedInfoAlerts.value.add(k)
  }
}
function isInfoCollapsed(a) {
  return collapsedInfoAlerts.value.has(alertKey(a))
}

// 排序：danger > warning > info
const sortedAlerts = computed(() => {
  const order = { danger: 0, warning: 1, info: 2 }
  return [...alerts.value].sort((a, b) => (order[a.severity] || 3) - (order[b.severity] || 3))
})

onMounted(() => {
  loadAlerts()
  loadDailyAdvice()
})
onActivated(() => {
  loadAlerts()
  loadDailyAdvice()
})
</script>

<template>
  <div class="alert-center bg-mesh">
    <!-- 页头 -->
    <header class="ac-header">
      <div class="ac-header-left">
        <div class="ac-title-block">
          <span class="terminal-label ac-eyebrow">RISK &amp; ALERT TERMINAL</span>
          <h2 class="ac-title editorial-title-lg">风险与提示中心</h2>
        </div>
        <span class="ac-stats">
          <span class="ac-stats-label">未读预警</span>
          <strong class="font-jet" :class="{ 'has-danger': dangerAlertCount > 0 }">{{ unreadAlertCount }}</strong>
          <span class="ac-stats-sep">·</span>
          <span class="ac-stats-label">待处理提示</span>
          <strong class="font-jet">{{ pendingAdviceCount }}</strong>
        </span>
      </div>
      <div class="ac-header-right">
        <button class="btn-ghost btn-sm" :class="{ 'btn-loading': alertScanning }" :disabled="alertScanning" @click="handleScanAlerts" title="持仓巡检">
          <svg :class="['icon-spin', { 'spinning': alertScanning }]" width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
          </svg>
          {{ alertScanning ? '巡检中...' : '巡检' }}
        </button>
        <button class="btn-ghost btn-sm" :class="{ 'btn-loading': dailyAdviceLoading }" :disabled="dailyAdviceLoading" @click="loadDailyAdvice" title="刷新提示">
          <svg :class="['icon-spin', { 'spinning': dailyAdviceLoading }]" width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h5M20 20v-5h-5M4.93 9a8 8 0 0113.14 0M19.07 15a8 8 0 01-13.14 0"/>
          </svg>
          刷新
        </button>
      </div>
    </header>

    <!-- Tab 切换 -->
    <div class="ac-tabs">
      <button :class="['ac-tab', { active: activeTab === 'advice' }]" @click="activeTab = 'advice'">
        <span class="ac-tab-label">今日持仓提示</span>
        <span class="terminal-label ac-tab-eyebrow">ADVICE</span>
        <span v-if="pendingAdviceCount > 0" class="ac-tab-badge font-jet">{{ pendingAdviceCount }}</span>
      </button>
      <button :class="['ac-tab', { active: activeTab === 'alerts' }]" @click="activeTab = 'alerts'">
        <span class="ac-tab-label">风险预警</span>
        <span class="terminal-label ac-tab-eyebrow">ALERTS</span>
        <span v-if="unreadAlertCount > 0" class="ac-tab-badge danger font-jet">{{ unreadAlertCount }}</span>
      </button>
      <button :class="['ac-tab', { active: activeTab === 'merged' }]" @click="activeTab = 'merged'">
        <span class="ac-tab-label">合并视图</span>
        <span class="terminal-label ac-tab-eyebrow">MERGED</span>
      </button>
    </div>

    <!-- 今日持仓提示 -->
    <section v-if="activeTab === 'advice' || activeTab === 'merged'" class="daily-advice-section editorial-card">
      <div class="advice-header editorial-card-header">
        <div class="advice-header-left">
          <h3 class="advice-title title">今日持仓提示</h3>
          <span v-if="dailyAdvice?.generated_at" class="advice-time">
            更新于 <span class="font-jet">{{ dailyAdvice.generated_at.slice(11, 16) }}</span>
          </span>
        </div>
        <div class="advice-header-right">
          <span v-if="dailyAdvice?.summary" class="advice-headline-badge" :class="adviceSummaryClass(dailyAdvice.summary.headline)">
            {{ dailyAdvice.summary.headline }}
          </span>
          <span v-if="dailyAdvice?.signals" class="advice-count-badge">
            <span class="font-jet">{{ dailyAdvice.signals.filter(s => s.severity === 'actionable').length }}</span> 可行动
            <span class="ac-stats-sep">·</span>
            <span class="font-jet">{{ dailyAdvice.signals.filter(s => s.severity === 'watch').length }}</span> 观察
          </span>
          <!-- P1-3.3 AI 综合解读按钮（LLM 默认关闭，用户主动触发） -->
          <button
            class="btn-secondary btn-sm"
            :disabled="comprehensiveLoading || !dailyAdvice?.signals?.length"
            @click="handleComprehensiveInterpretation"
            title="AI 综合解读今日全部信号（LLM 调用，需开启 llm_cost.daily_advice_ai_interpretation）"
          >
            {{ comprehensiveLoading ? '解读中...' : 'AI 综合解读' }}
          </button>
        </div>
      </div>

      <!-- AI 综合解读结果 -->
      <div v-if="comprehensiveLoading" class="comprehensive-loading">
        <svg class="icon-spin spinning" width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h5M20 20v-5h-5M4.93 9a8 8 0 0113.14 0M19.07 15a8 8 0 01-13.14 0"/>
        </svg>
        AI 正在综合解读今日全部信号...
      </div>
      <div v-else-if="comprehensiveError" class="comprehensive-error">
        <span>⚠ {{ comprehensiveError }}</span>
      </div>
      <div v-else-if="comprehensiveResult" class="comprehensive-result">
        <div class="comprehensive-result-header editorial-card-header">
          <strong class="title">AI 综合操作建议</strong>
          <span v-if="comprehensiveResult.cached" class="cached-tag">缓存命中</span>
          <span class="signal-count">
            基于 <span class="font-jet">{{ comprehensiveResult.signal_count }}</span> 条信号
          </span>
          <button class="btn-ghost btn-xs" @click="comprehensiveResult = null">✕</button>
        </div>
        <div class="comprehensive-text" v-html="renderMarkdown(comprehensiveResult.interpretation)"></div>
      </div>

      <div v-if="dailyAdviceLoading" class="advice-loading">
        <Skeleton variant="text" :count="2" />
      </div>

      <div v-else-if="dailyAdviceError" class="advice-error">
        <span>{{ dailyAdviceError }}</span>
        <button class="btn-ghost btn-sm" @click="loadDailyAdvice">重试</button>
      </div>

      <div v-else-if="dailyAdvice && dailyAdvice.signals && dailyAdvice.signals.length > 0" class="advice-cards-grid">
        <div
          v-for="signal in dailyAdvice.signals"
          :key="signal.id"
          :class="['advice-card', 'reveal-stagger', severityClass(signal.severity)]"
        >
          <div class="advice-card-header">
            <div class="advice-card-title">
              <span class="advice-fund-name">{{ signal.fund_name || signal.target_name || '未知' }}</span>
              <span class="advice-fund-code font-jet" v-if="signal.fund_code || signal.target_code">{{ signal.fund_code || signal.target_code }}</span>
            </div>
            <span class="advice-severity-tag" :class="severityClass(signal.severity)">{{ severityLabel(signal.severity) }}</span>
          </div>

          <div class="advice-card-body">
            <div class="advice-tags-row" v-if="signal.action_tag || signal.suggestion">
              <span class="advice-tag-chip" v-if="signal.action_tag">{{ tagLabel(signal.action_tag) }}</span>
              <span class="advice-suggestion" v-if="signal.suggestion">{{ signal.suggestion }}</span>
            </div>

            <div class="advice-amount" v-if="signal.suggested_amount">
              <span class="advice-amount-label">建议金额</span>
              <strong class="font-jet-lg">{{ formatAdviceAmount(signal.suggested_amount) }}</strong>
            </div>

            <div class="advice-evidence" v-if="signal.evidence && signal.evidence.length">
              <span class="advice-evidence-label">证据</span>
              <div class="advice-chips">
                <span v-for="(ev, idx) in signal.evidence" :key="idx" class="advice-chip font-jet">{{ ev }}</span>
              </div>
            </div>

            <div class="advice-counter-risk" v-if="signal.counter_risk">
              <span class="advice-counter-label">反方风险</span>
              <span class="advice-counter-text">{{ signal.counter_risk }}</span>
            </div>

            <div class="advice-blocked-reason" v-if="signal.severity === 'blocked' && signal.blocked_reason">
              <span class="advice-blocked-label">拦截</span>
              {{ signal.blocked_reason }}
            </div>
          </div>

          <!-- AI 回复区域 -->
          <div class="advice-ai-response" v-if="aiResponseMap[signal.id]">
            <div v-if="aiResponseMap[signal.id].loading" class="advice-ai-loading">
              <svg class="icon-spin spinning" width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h5M20 20v-5h-5M4.93 9a8 8 0 0113.14 0M19.07 15a8 8 0 01-13.14 0"/>
              </svg>
              AI 分析中...
            </div>
            <div v-else-if="aiResponseMap[signal.id].error" class="advice-ai-error">{{ aiResponseMap[signal.id].error }}</div>
            <div v-else class="advice-ai-text" v-html="renderMarkdown(aiResponseMap[signal.id].text)"></div>
          </div>

          <div class="advice-card-actions" v-if="signal.severity !== 'blocked'">
            <button class="btn-primary btn-sm" :disabled="adviceActionLoading[signal.id]" @click="handleAdviceAction(signal.id, 'create-candidate')">
              保存为决策
            </button>
            <button class="btn-secondary btn-sm" :disabled="aiResponseMap[signal.id]?.loading || adviceActionLoading[signal.id]" @click="handleAdviceAction(signal.id, 'ask-ai')">
              问 AI
            </button>
            <button class="btn-ghost btn-sm" :disabled="adviceActionLoading[signal.id]" @click="handleAdviceAction(signal.id, 'ignore')">
              忽略
            </button>
          </div>
          <div class="advice-card-actions" v-else>
            <button class="btn-secondary btn-sm" :disabled="adviceActionLoading[signal.id]" @click="handleAdviceAction(signal.id, 'ask-ai')">
              问 AI
            </button>
          </div>
        </div>
      </div>

      <div v-else-if="dailyAdvice" class="advice-empty">
        <span>✅ 今日暂无需行动的持仓提示</span>
        <button class="btn-ghost btn-sm" @click="handleAdviceAction(null, 'refresh')">手动生成</button>
      </div>
    </section>

    <!-- 风险预警 -->
    <section v-if="activeTab === 'alerts' || activeTab === 'merged'" class="alert-panel editorial-card">
      <div class="alert-panel-header editorial-card-header">
        <svg class="title-icon" width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>
        </svg>
        <strong class="title">风险预警</strong>
        <span v-if="unreadAlertCount > 0" class="alert-badge has-unread font-jet">{{ unreadAlertCount }}</span>
        <span v-if="dangerAlertCount > 0" class="danger-tag">
          <span class="terminal-label">DANGER</span>
          <span class="font-jet">{{ dangerAlertCount }}</span>
          条
        </span>
        <span class="terminal-label alert-panel-eyebrow">RISK FEED</span>
      </div>

      <div class="alert-list">
        <div v-if="alerts.length === 0" class="alert-empty">
          <span>暂无预警</span>
          <span class="alert-empty-hint">点击「巡检」主动扫描持仓风险</span>
        </div>

        <div v-for="a in sortedAlerts" :key="a.latest_id" :class="['alert-item', 'reveal-stagger', 'alert-' + a.severity]">
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
            <div class="alert-title">
              <span v-if="a.severity === 'danger'" class="severity-mark danger terminal-label">DANGER</span>
              <span v-else-if="a.severity === 'warning'" class="severity-mark warning terminal-label">WARNING</span>
              <span v-else class="severity-mark info terminal-label">INFO</span>
              {{ a.title }}
              <span v-if="a.cnt > 1" class="alert-count-badge font-jet">×{{ a.cnt }}</span>
              <span v-if="a.acknowledged_status === 'acknowledged'" class="alert-ack-tag ack-acknowledged">已采纳</span>
              <span v-else-if="a.acknowledged_status === 'ignored'" class="alert-ack-tag ack-ignored">已忽略</span>
            </div>

            <!-- info 级预警内容折叠（P1-3.1） -->
            <div v-if="a.content && a.severity !== 'info'" class="alert-content">{{ a.content }}</div>
            <div v-else-if="a.content && a.severity === 'info'">
              <div v-if="!isInfoCollapsed(a)" class="alert-content">{{ a.content }}</div>
              <button class="btn-ghost btn-xs toggle-info-btn" @click="toggleInfoAlert(a)">
                {{ isInfoCollapsed(a) ? '展开' : '折叠' }}
              </button>
            </div>

            <div class="alert-meta">
              <span class="alert-type-badge terminal-label">{{ a.alert_type }}</span>
              <span v-if="a.source === 'system_scan'" class="alert-source-badge scan">系统巡检</span>
              <span v-else-if="a.source === 'ai_analysis'" class="alert-source-badge ai">AI 对话</span>
              <span v-else-if="a.source === 'watchlist_patrol'" class="alert-source-badge ai">关注巡检</span>
              <span v-else class="alert-source-badge">{{ a.source }}</span>
              <!-- P0-2: 可靠性徽章 -->
              <span
                v-if="a.reliability"
                :class="['reliability-badge', `rel-${a.reliability.level}`]"
                :title="a.reliability.reason"
              >
                可靠性: {{ a.reliability.label }}
                <template v-if="a.reliability.win_rate !== null && a.reliability.win_rate !== undefined">
                  (<span class="font-jet">{{ a.reliability.win_rate }}%</span> / <span class="font-jet">{{ a.reliability.samples }}</span>样本)
                </template>
              </span>
              <!-- P0-1: 首次时间 + 最新时间 -->
              <span v-if="a.cnt > 1 && a.first_at" class="alert-time font-jet" :title="`首次：${a.first_at}`">
                {{ a.first_at?.slice(5, 10) }} ~ {{ a.latest_at?.slice(5, 10) }}
              </span>
              <span v-else class="alert-time font-jet">{{ a.latest_at }}</span>
            </div>

            <!-- P1-3.1 历史对比 -->
            <div v-if="a.related_fund_code" class="alert-history-section">
              <button class="btn-ghost btn-xs history-btn" @click="loadAlertHistory(a.latest_id)">
                {{ alertHistoryMap[a.latest_id]?.show ? '隐藏历史' : '查看近30天历史' }}
              </button>
              <div v-if="alertHistoryMap[a.latest_id]?.show" class="alert-history-content">
                <div v-if="alertHistoryMap[a.latest_id]?.loading" class="history-loading">加载中...</div>
                <div v-else-if="alertHistoryMap[a.latest_id]?.error" class="history-error">{{ alertHistoryMap[a.latest_id].error }}</div>
                <div v-else-if="alertHistoryMap[a.latest_id]?.history?.length">
                  <div class="history-summary">
                    近30天共 <strong class="font-jet">{{ alertHistoryMap[a.latest_id].history.length }}</strong> 次同类预警
                  </div>
                  <div v-for="h in alertHistoryMap[a.latest_id].history.slice(0, 5)" :key="h.id" class="history-item">
                    <span :class="['history-sev', 'terminal-label', `sev-${h.severity}`]">{{ h.severity }}</span>
                    <span class="history-time font-jet">{{ h.created_at }}</span>
                    <span class="history-title">{{ h.title }}</span>
                  </div>
                </div>
                <div v-else class="history-empty">暂无历史记录</div>
              </div>
            </div>

            <!-- P1-3.2 交易联动按钮 -->
            <div v-if="a.severity === 'danger' || a.severity === 'warning'" class="alert-action-buttons">
              <button
                v-for="label in alertActionLabel(a.alert_type)"
                :key="label"
                class="btn-secondary btn-xs"
                @click="handleAlertAction(a, label)"
              >
                {{ label }}
              </button>
            </div>

            <!-- P1-3: 相关财经新闻（MCP 检索，受 alert.news_integration 开关控制） -->
            <div v-if="a.related_news && a.related_news.length" class="alert-news-section">
              <div class="alert-news-label">相关财经新闻</div>
              <div v-for="(n, idx) in a.related_news" :key="idx" class="alert-news-item">
                <div class="alert-news-title-row">
                  <a v-if="n.news_url" :href="n.news_url" target="_blank" rel="noopener" class="alert-news-title">
                    {{ n.news_title }}
                  </a>
                  <span v-else class="alert-news-title">{{ n.news_title }}</span>
                  <span class="alert-news-source font-jet" v-if="n.news_source">
                    {{ n.news_source }}<template v-if="n.published_at"> · {{ n.published_at?.slice(5, 10) }}</template>
                  </span>
                </div>
                <div v-if="n.news_summary" class="alert-news-summary">{{ n.news_summary }}</div>
              </div>
            </div>
          </div>

          <div class="alert-actions">
            <button
              class="btn-ghost btn-sm ack-adopt-btn"
              :disabled="!!a.acknowledged_status || ackLoading[a.latest_id]"
              @click="handleAcknowledgeAlert(a, 'acknowledged')"
              title="采纳"
            >采纳</button>
            <button
              class="btn-ghost btn-sm ack-ignore-btn"
              :disabled="!!a.acknowledged_status || ackLoading[a.latest_id]"
              @click="handleAcknowledgeAlert(a, 'ignored')"
              title="忽略"
            >忽略</button>
            <button class="btn-ghost btn-sm" @click="handleMarkAlertRead(a.latest_id, a.title, a.severity)" title="标记已读">✔</button>
            <button class="btn-ghost btn-sm btn-danger-text" @click="handleDeleteAlert(a.latest_id, a.title, a.severity)" title="删除">✕</button>
          </div>
        </div>
      </div>
    </section>

    <ConfirmDialog
      :visible="confirmState.visible"
      :title="confirmState.title"
      :message="confirmState.message"
      :danger="confirmState.danger"
      @confirm="confirmState.onConfirm"
      @cancel="confirmState.visible = false"
    />
  </div>
</template>

<style scoped>
.alert-center {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

/* ── 页头 ── */
.ac-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  flex-wrap: wrap;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 0.875rem 1.25rem;
}
.ac-header-left {
  display: flex;
  align-items: center;
  gap: 1.25rem;
  flex-wrap: wrap;
}
.ac-title-block {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}
.ac-title {
  margin: 0;
  color: var(--color-text-primary);
}
.ac-eyebrow {
  color: var(--color-text-muted);
  opacity: 0.8;
}
.ac-stats {
  display: inline-flex;
  align-items: baseline;
  gap: 0.4rem;
  font-size: 0.82rem;
  color: var(--color-text-secondary);
  flex-wrap: wrap;
}
.ac-stats-label {
  font-size: 0.74rem;
  color: var(--color-text-muted);
}
.ac-stats strong {
  color: var(--color-text-primary);
  font-weight: 700;
  font-size: 0.95rem;
}
.ac-stats strong.has-danger {
  color: var(--color-loss);
}
.ac-stats-sep {
  color: var(--color-text-muted);
  opacity: 0.5;
}
.ac-header-right {
  display: flex;
  gap: 0.5rem;
}

/* ── Tab ── */
.ac-tabs {
  display: flex;
  gap: 0.25rem;
  border-bottom: 1px solid var(--color-border);
}
.ac-tab {
  background: none;
  border: none;
  padding: 0.625rem 1rem;
  font-size: 0.85rem;
  font-weight: 500;
  color: var(--color-text-secondary);
  cursor: pointer;
  border-bottom: 2px solid transparent;
  transition: all var(--transition-fast);
  display: inline-flex;
  align-items: baseline;
  gap: 0.4rem;
}
.ac-tab-label {
  font-family: var(--font-serif);
  letter-spacing: -0.01em;
}
.ac-tab-eyebrow {
  opacity: 0.55;
}
.ac-tab.active .ac-tab-eyebrow {
  opacity: 0.9;
}
.ac-tab:hover {
  color: var(--color-text-primary);
}
.ac-tab.active {
  color: var(--color-primary);
  border-bottom-color: var(--color-primary);
  font-weight: 600;
}
.ac-tab-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: 9px;
  font-size: 0.7rem;
  font-weight: 700;
  background: var(--color-primary);
  color: white;
}
.ac-tab-badge.danger {
  background: var(--color-loss);
}

/* ── 每日持仓提示（迁移样式） ── */
.daily-advice-section {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 1.25rem;
}
.advice-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  margin-bottom: 1rem;
  flex-wrap: wrap;
}
.advice-header-left {
  display: flex;
  align-items: baseline;
  gap: 0.75rem;
  flex-wrap: wrap;
}
.advice-title {
  margin: 0;
  color: var(--color-text-primary);
}
.advice-time {
  display: inline-flex;
  align-items: baseline;
  gap: 0.3rem;
  font-size: 0.78rem;
  color: var(--color-text-muted);
}
.advice-header-right {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
}
.advice-headline-badge {
  font-size: 0.78rem;
  padding: 2px 10px;
  border-radius: 20px;
  font-weight: 500;
}
.advice-headline-buy { background: var(--color-profit-bg); color: var(--color-profit); }
.advice-headline-sell { background: var(--color-loss-bg); color: var(--color-loss); }
.advice-headline-hold { background: var(--color-warning-bg); color: var(--color-warning); }
.advice-headline-data { background: rgba(156, 163, 175, 0.12); color: var(--color-text-muted); }
.advice-count-badge {
  font-size: 0.75rem;
  color: var(--color-text-secondary);
  background: var(--color-border-light);
  padding: 2px 8px;
  border-radius: 12px;
}
.advice-loading { padding: 1rem 0; }
.advice-error {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  color: var(--color-danger);
  font-size: 0.85rem;
}
.advice-cards-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 0.875rem;
}
@media (max-width: 768px) {
  .advice-cards-grid { grid-template-columns: 1fr; }
}
.advice-card {
  border-radius: 10px;
  padding: 0.875rem;
  border: 1px solid var(--color-border-light);
  background: var(--color-bg-card);
  transition: box-shadow 0.2s;
}
.advice-card:hover { box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08); }
.advice-severity-actionable {
  border-left: 3px solid var(--color-profit);
  background: linear-gradient(90deg, var(--color-profit-bg) 0%, var(--color-bg-card) 40%);
}
.advice-severity-watch {
  border-left: 3px solid var(--color-warning);
  background: linear-gradient(90deg, var(--color-warning-bg) 0%, var(--color-bg-card) 40%);
}
.advice-severity-blocked {
  border-left: 3px solid #991b1b;
  background: linear-gradient(90deg, rgba(220, 38, 38, 0.12) 0%, var(--color-bg-card) 40%);
}
.advice-severity-info {
  border-left: 3px solid var(--color-text-muted);
}
.advice-card-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.5rem;
  margin-bottom: 0.625rem;
}
.advice-card-title { display: flex; flex-direction: column; gap: 2px; }
.advice-fund-name {
  font-weight: 600;
  font-size: 0.92rem;
  color: var(--color-text-primary);
}
.advice-fund-code {
  font-size: 0.72rem;
  color: var(--color-text-muted);
}
.advice-severity-tag {
  font-size: 0.7rem;
  padding: 1px 8px;
  border-radius: 10px;
  font-weight: 500;
  white-space: nowrap;
}
.advice-severity-tag.advice-severity-actionable { background: var(--color-profit-bg); color: var(--color-profit); }
.advice-severity-tag.advice-severity-watch { background: var(--color-warning-bg); color: var(--color-warning); }
.advice-severity-tag.advice-severity-blocked { background: rgba(220, 38, 38, 0.15); color: #991b1b; }
.advice-severity-tag.advice-severity-info { background: rgba(156, 163, 175, 0.12); color: var(--color-text-muted); }
.advice-card-body { display: flex; flex-direction: column; gap: 0.5rem; }
.advice-tags-row { display: flex; align-items: center; gap: 0.4rem; flex-wrap: wrap; }
.advice-tag-chip {
  font-size: 0.72rem;
  padding: 1px 8px;
  border-radius: 8px;
  background: var(--color-primary-50);
  color: var(--color-primary-800);
  font-weight: 500;
}
.advice-suggestion {
  font-size: 0.82rem;
  color: var(--color-text-secondary);
}
.advice-amount {
  display: flex;
  align-items: baseline;
  gap: 0.4rem;
  font-size: 0.82rem;
  color: var(--color-text-secondary);
}
.advice-amount-label {
  font-size: 0.72rem;
  color: var(--color-text-muted);
}
.advice-amount strong {
  color: var(--color-profit);
  font-size: 0.95rem;
}
.advice-evidence { display: flex; flex-direction: column; gap: 0.25rem; }
.advice-evidence-label {
  font-size: 0.72rem;
  color: var(--color-text-muted);
}
.advice-chips { display: flex; flex-wrap: wrap; gap: 0.3rem; }
.advice-chip {
  font-size: 0.68rem;
  padding: 1px 7px;
  border-radius: 6px;
  background: var(--color-border-light);
  color: var(--color-text-secondary);
}
.advice-counter-risk {
  font-size: 0.75rem;
  display: flex;
  gap: 0.3rem;
  align-items: flex-start;
}
.advice-counter-label {
  color: var(--color-warning);
  font-weight: 500;
  white-space: nowrap;
}
.advice-counter-text { color: var(--color-text-secondary); }
.advice-blocked-reason {
  display: flex;
  align-items: baseline;
  gap: 0.4rem;
  font-size: 0.78rem;
  color: #991b1b;
  background: rgba(220, 38, 38, 0.08);
  padding: 0.4rem 0.6rem;
  border-radius: 6px;
}
.advice-blocked-label {
  font-size: 0.68rem;
  font-weight: 600;
  letter-spacing: 0.02em;
  opacity: 0.85;
}
.advice-ai-response {
  margin-top: 0.5rem;
  padding: 0.625rem;
  background: var(--color-primary-50);
  border-radius: 8px;
  font-size: 0.82rem;
}
.advice-ai-loading {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  color: var(--color-text-muted);
}
.advice-ai-error { color: var(--color-danger); }
.advice-ai-text {
  color: var(--color-text-primary);
  line-height: 1.6;
}
.advice-ai-text :deep(p) { margin: 0.3rem 0; }
.advice-card-actions {
  display: flex;
  gap: 0.4rem;
  margin-top: 0.625rem;
  flex-wrap: wrap;
}
.advice-card-actions .btn-sm {
  font-size: 0.75rem;
  padding: 3px 10px;
}
.advice-empty {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.5rem 0;
  color: var(--color-text-secondary);
  font-size: 0.85rem;
}

/* ── AI 综合解读 ── */
.comprehensive-loading {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.75rem 1rem;
  background: var(--color-primary-50);
  border-radius: 8px;
  color: var(--color-text-secondary);
  font-size: 0.85rem;
  margin-bottom: 1rem;
}
.comprehensive-error {
  padding: 0.75rem 1rem;
  background: var(--color-loss-bg);
  border-radius: 8px;
  color: var(--color-loss);
  font-size: 0.82rem;
  margin-bottom: 1rem;
}
.comprehensive-result {
  background: linear-gradient(135deg, var(--color-primary-50) 0%, var(--color-bg-card) 100%);
  border: 1px solid var(--color-primary-bg);
  border-radius: 10px;
  padding: 1rem;
  margin-bottom: 1rem;
}
.comprehensive-result-header {
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
  flex-wrap: wrap;
}
.comprehensive-result-header strong {
  color: var(--color-primary);
  font-size: 0.9rem;
}
.cached-tag {
  font-size: 0.68rem;
  padding: 1px 6px;
  border-radius: 4px;
  background: var(--color-warning-bg);
  color: var(--color-warning);
  font-weight: 500;
}
.signal-count {
  display: inline-flex;
  align-items: baseline;
  gap: 0.25rem;
  font-size: 0.72rem;
  color: var(--color-text-muted);
  flex: 1;
}
.comprehensive-text {
  color: var(--color-text-primary);
  font-size: 0.85rem;
  line-height: 1.7;
}
.comprehensive-text :deep(p) { margin: 0.4rem 0; }
.comprehensive-text :deep(h1),
.comprehensive-text :deep(h2),
.comprehensive-text :deep(h3) {
  font-size: 0.95rem;
  margin: 0.6rem 0 0.3rem;
}

/* ── 风险预警（迁移样式） ── */
.alert-panel {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  overflow: hidden;
}
.alert-panel-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.75rem 1.25rem;
  background: var(--color-bg-hover);
  font-size: 0.85rem;
  font-weight: 600;
  border-bottom: 1px solid var(--color-border);
  margin-bottom: 0;
}
.alert-panel-eyebrow {
  margin-left: auto;
  opacity: 0.55;
}
.title-icon {
  color: var(--color-text-muted);
  flex-shrink: 0;
}
.alert-list {
  display: flex;
  flex-direction: column;
}
.alert-item {
  display: flex;
  align-items: flex-start;
  gap: 0.75rem;
  padding: 0.75rem 1.25rem;
  border-bottom: 1px solid var(--color-border);
  font-size: 0.82rem;
  transition: background-color var(--transition-fast);
}
.alert-item:hover { background: var(--color-bg-hover); }
.alert-item:last-child { border-bottom: none; }
.alert-danger { border-left: 3px solid var(--color-loss); }
.alert-warning { border-left: 3px solid var(--color-warning); }
.alert-info { border-left: 3px solid var(--color-info); }
.alert-icon {
  flex-shrink: 0;
  margin-top: 2px;
}
.alert-danger .alert-icon { color: var(--color-loss); }
.alert-warning .alert-icon { color: var(--color-warning); }
.alert-info .alert-icon { color: var(--color-info); }
.alert-body {
  flex: 1;
  min-width: 0;
}
.alert-title {
  font-weight: 600;
  color: var(--color-text-primary);
  display: flex;
  align-items: center;
  gap: 0.4rem;
  flex-wrap: wrap;
}
.severity-mark {
  font-size: 0.68rem;
  font-weight: 700;
  padding: 1px 6px;
  border-radius: 4px;
  letter-spacing: 0.02em;
}
.severity-mark.danger { background: var(--color-loss-bg); color: var(--color-loss); }
.severity-mark.warning { background: var(--color-warning-bg); color: var(--color-warning); }
.severity-mark.info { background: var(--color-bg-hover); color: var(--color-text-muted); }
.alert-content {
  color: var(--color-text-secondary);
  margin-top: 0.3rem;
  font-size: 0.8rem;
  line-height: 1.5;
}
.alert-meta {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.4rem;
  font-size: 0.72rem;
  color: var(--color-text-muted);
  flex-wrap: wrap;
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
.alert-source-badge.scan { background: var(--color-info-bg); color: var(--color-info); }
.alert-source-badge.ai { background: var(--color-primary-bg); color: var(--color-primary); }
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
  flex-direction: column;
  gap: 0.25rem;
  flex-shrink: 0;
  align-items: stretch;
}
.alert-actions .btn-sm {
  font-size: 0.72rem;
  padding: 2px 8px;
  white-space: nowrap;
}

/* 业务确认状态标签 */
.alert-ack-tag {
  font-size: 0.68rem;
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 4px;
  white-space: nowrap;
}
.alert-ack-tag.ack-acknowledged {
  background: var(--color-profit-bg);
  color: var(--color-profit);
}
.alert-ack-tag.ack-ignored {
  background: var(--color-bg-hover);
  color: var(--color-text-muted);
}

/* 采纳/忽略按钮 */
.ack-adopt-btn {
  color: var(--color-profit) !important;
}
.ack-adopt-btn:not(:disabled):hover {
  background: var(--color-profit-bg) !important;
}
.ack-ignore-btn {
  color: var(--color-text-muted) !important;
}
.ack-ignore-btn:not(:disabled):hover {
  background: var(--color-bg-hover) !important;
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
  background: var(--color-loss);
  color: white;
}
.alert-count-badge {
  display: inline-block;
  background: var(--color-primary);
  color: white;
  font-size: 0.65rem;
  font-weight: 700;
  padding: 1px 6px;
  border-radius: 10px;
}
.danger-tag {
  display: inline-flex;
  align-items: baseline;
  gap: 0.3rem;
  font-size: 0.72rem;
  color: var(--color-loss);
  font-weight: 600;
}

/* ── P1-3.1 历史对比 ── */
.alert-history-section {
  margin-top: 0.4rem;
}
.history-btn {
  font-size: 0.7rem;
  color: var(--color-text-muted);
}
.alert-history-content {
  margin-top: 0.4rem;
  padding: 0.5rem 0.625rem;
  background: var(--color-bg-hover);
  border-radius: 6px;
  font-size: 0.75rem;
}
.history-loading, .history-error, .history-empty {
  color: var(--color-text-muted);
  font-size: 0.75rem;
}
.history-error { color: var(--color-danger); }
.history-summary {
  color: var(--color-text-secondary);
  margin-bottom: 0.4rem;
}
.history-summary strong {
  color: var(--color-warning);
  font-weight: 700;
}
.history-item {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.2rem 0;
  border-top: 1px solid var(--color-border-light);
  font-size: 0.72rem;
}
.history-sev {
  font-size: 0.65rem;
  font-weight: 700;
  padding: 1px 5px;
  border-radius: 3px;
}
.history-sev.sev-danger { background: var(--color-loss-bg); color: var(--color-loss); }
.history-sev.sev-warning { background: var(--color-warning-bg); color: var(--color-warning); }
.history-sev.sev-info { background: var(--color-bg-hover); color: var(--color-text-muted); }
.history-time {
  color: var(--color-text-muted);
  white-space: nowrap;
}
.history-title {
  color: var(--color-text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ── P1-3.2 交易联动 ── */
.alert-action-buttons {
  display: flex;
  gap: 0.3rem;
  margin-top: 0.5rem;
  flex-wrap: wrap;
}
.toggle-info-btn {
  font-size: 0.68rem;
  color: var(--color-text-muted);
  margin-top: 0.2rem;
}

/* ── P0-2 可靠性徽章 ── */
.reliability-badge {
  font-size: 0.68rem;
  padding: 1px 6px;
  border-radius: 4px;
  font-weight: 500;
  cursor: help;
  white-space: nowrap;
}
.reliability-badge.rel-high {
  background: var(--color-profit-bg);
  color: var(--color-profit);
}
.reliability-badge.rel-medium {
  background: var(--color-warning-bg);
  color: var(--color-warning);
}
.reliability-badge.rel-low {
  background: var(--color-loss-bg);
  color: var(--color-loss);
}
.reliability-badge.rel-unknown {
  background: var(--color-bg-hover);
  color: var(--color-text-muted);
}

/* ── P1-3 相关财经新闻 ── */
.alert-news-section {
  margin-top: 0.5rem;
  padding: 0.5rem 0.625rem;
  background: var(--color-primary-50);
  border-radius: 6px;
  border-left: 2px solid var(--color-primary);
}
.alert-news-label {
  font-size: 0.7rem;
  font-weight: 600;
  color: var(--color-primary);
  margin-bottom: 0.3rem;
}
.alert-news-item {
  padding: 0.25rem 0;
  border-top: 1px solid var(--color-border-light);
}
.alert-news-item:first-of-type {
  border-top: none;
}
.alert-news-title-row {
  display: flex;
  align-items: baseline;
  gap: 0.4rem;
  flex-wrap: wrap;
}
.alert-news-title {
  font-size: 0.78rem;
  color: var(--color-text-primary);
  font-weight: 500;
  text-decoration: none;
}
a.alert-news-title:hover {
  color: var(--color-primary);
  text-decoration: underline;
}
.alert-news-source {
  font-size: 0.68rem;
  color: var(--color-text-muted);
  white-space: nowrap;
}
.alert-news-summary {
  font-size: 0.72rem;
  color: var(--color-text-secondary);
  margin-top: 0.15rem;
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* ── 通用 ── */
.icon-spin.spinning {
  animation: spin 1s linear infinite;
}
@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
</style>
