<script setup>
import PortfolioRiskOverview from '../portfolio/PortfolioRiskOverview.vue'
import { ref, computed, watch } from 'vue'
import Icon from '../ui/Icon.vue'
import {
  listAlerts, getUnreadAlertCount, markAlertRead, deleteAlert, scanPortfolioAlerts,
  dailyAdviceAPI, getAlertHistory, comprehensiveInterpretation, acknowledgeAlert,
} from '../../api'
import { usePageRefresh } from '../../composables/usePageRefresh'
import { useToast } from '../../composables/useToast'
import ConfirmDialog from '../layout/ConfirmDialog.vue'
import Skeleton from '../ui/Skeleton.vue'
import { renderMarkdown } from '../../composables/useMarkdown'

const emit = defineEmits(['navigate'])

// ── Tab 切换 ──
const activeTab = ref('alerts') // 'advice' | 'alerts' | 'merged'

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
const alertsError = ref(null)
const alertsLoading = ref(false)
const alertScanResult = ref(null)
const lastSuccessfulReadAt = ref(null)
const lastScan = ref(null)
const alertOffset = ref(0)
const alertTotal = ref(0)
const activeRiskCount = ref(0)
const riskView = ref('active')
const actionDialog = ref({ visible: false, alert: null, status: '', reason: '', review_date: '' })
const unreadAlertCount = ref(0)
const alertScanning = ref(false)
const alertHistoryMap = ref({})  // alertId -> { loading, history, show }
const collapsedInfoAlerts = ref(new Set())  // 折叠的 info 级预警 key
const ackLoading = ref({})  // alertId -> loading（业务确认状态更新中）

// 筛选器（方案 E）
const severityFilter = ref('all')  // 'all' | 'danger_warning' | 'danger' | 'ignored'
const typeFilter = ref('all')       // 'all' | 具体 alert_type
const hideIgnoredInfo = ref(false)  // 默认隐藏已忽略的 info 级预警

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
  alertsLoading.value = true
  alertsError.value = null
  try {
    const { data } = await listAlerts(false, 50, {
      active_only: riskView.value === 'active', offset: alertOffset.value,
      severity: ['danger', 'warning', 'info', 'danger_warning'].includes(severityFilter.value) ? severityFilter.value : undefined,
      alert_type: typeFilter.value === 'all' ? undefined : typeFilter.value,
    })
    alerts.value = data.alerts || []
    alertTotal.value = data.total ?? alerts.value.length
    activeRiskCount.value = data.active_count ?? alerts.value.filter(a => a.risk_status !== 'resolved').length
    lastScan.value = data.last_scan || null
    if (!alertScanResult.value && data.last_scan) alertScanResult.value = data.last_scan
    const { data: cnt } = await getUnreadAlertCount()
    unreadAlertCount.value = cnt.count
    lastSuccessfulReadAt.value = new Date().toLocaleString('zh-CN')
  } catch (e) {
    alertsError.value = e?.response?.data?.detail || e?.message || '风险读取失败'
  } finally {
    alertsLoading.value = false
  }
}

async function refreshAll() {
  await Promise.all([loadAlerts(), loadDailyAdvice()])
}

async function handleMarkAlertRead(alertId) {
  const target = alerts.value.find(a => a.latest_id === alertId)
  if (!target) return
  try {
    await markAlertRead(alertId)
    const count = target.unread_count ?? (target.is_read ? 0 : target.cnt || 1)
    // 阅读只更新通知，活动风险事实继续展示。
    target.is_read = 1
    target.unread_count = 0
    unreadAlertCount.value = Math.max(0, unreadAlertCount.value - count)
  } catch (e) {
    useToast().showToast(e?.response?.data?.detail || e?.message || '操作失败', 'error')
  }
}

function handleDeleteAlert(alertId, title, severity) {
  const target = alerts.value.find(a => a.latest_id === alertId)
  if (!target) return
  if (target.risk_status !== 'resolved') {
    useToast().showToast('活动风险需完整巡检确认恢复后才能清理', 'info')
    return
  }
  confirmState.value = {
    visible: true,
    title: '删除已恢复预警',
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
    alertScanResult.value = data
    const incomplete = data.status !== 'complete' || data.failed > 0 || data.skipped > 0
    useToast().showToast(
      `${incomplete ? '检查未完成' : '巡检完成'}：新增 ${data.generated || 0}、更新 ${data.updated || 0}、恢复 ${data.resolved || 0}；失败 ${data.failed || 0}、未覆盖 ${data.skipped || 0}`,
      incomplete ? 'info' : 'success',
    )
  } catch (e) {
    alertScanResult.value = { status: 'failed', failed: 1, coverage: {}, error: e?.response?.data?.detail || e?.message || '巡检失败' }
    useToast().showToast('巡检失败：' + alertScanResult.value.error, 'error')
  } finally {
    await refreshAll()
    alertScanning.value = false
  }
}

function handleAcknowledgeAlert(alert, status) {
  if (!alert.latest_id) return
  actionDialog.value = { visible: status === 'ignored', alert, status, reason: '', review_date: '' }
  if (status === 'acknowledged') confirmRiskAction()
}

function confirmRiskAction() {
  const { alert, status, reason, review_date } = actionDialog.value
  if (!alert) return
  if (status === 'ignored' && (!reason.trim() || !review_date)) {
    useToast().showToast('请填写忽略原因和复查日期', 'info')
    return
  }
  if (status === 'ignored' && review_date < new Date().toLocaleDateString('en-CA')) {
    useToast().showToast('复查日期不能早于今天', 'info')
    return
  }
  actionDialog.value.visible = false
  confirmState.value = {
    visible: true, title: status === 'acknowledged' ? '记录采纳' : '记录暂时忽略', danger: false,
    message: status === 'acknowledged'
      ? `将「${alert.title}」标记为处理中。此记录不代表已成交或风险已恢复。`
      : `忽略原因：${reason}；复查日期：${review_date}。活动风险继续展示，恶化时会重新提醒。`,
    onConfirm: async () => {
      confirmState.value.visible = false
      ackLoading.value[alert.latest_id] = true
      try {
        const details = status === 'ignored' ? { reason: reason.trim(), review_date } : {}
        await acknowledgeAlert(alert.latest_id, status, details)
        alert.acknowledged_status = status
        alert.action_reason = reason
        alert.review_date = review_date
        if (status === 'acknowledged' && alert.risk_status !== 'resolved') alert.risk_status = 'in_progress'
        actionDialog.value.visible = false
        useToast().showToast(status === 'acknowledged' ? '已记录采纳，成交请另行登记' : '已记录忽略及复查时间', 'info')
      } catch (e) {
        useToast().showToast(e?.response?.data?.detail || e?.message || '操作失败', 'error')
      } finally {
        ackLoading.value[alert.latest_id] = false
      }
    },
  }
}

function reviewIsDue(alert) {
  return alert.acknowledged_status === 'ignored' && alert.review_date && alert.review_date <= new Date().toLocaleDateString('en-CA')
}

function lifecycleLabel(status) {
  return { active: '持续风险', in_progress: '处理中（未确认成交）', recurred: '再次触发', resolved: '已恢复' }[status] || '待评估'
}

const incompleteCoverage = computed(() => Object.entries(alertScanResult.value?.coverage || {}).filter(([, item]) => item.status !== 'complete'))

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
    buy_drop_alert: ['查看持仓', '损失复核'],
    drawdown_alert: ['查看持仓', '止损评估'],
    concentration_alert: ['查看持仓', '调仓建议'],
    valuation_opportunity: ['查看估值图', '投前评估'],
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

// 排序：danger > warning > info，并应用筛选器
const sortedAlerts = computed(() => {
  let list = [...alerts.value]

  // severity 筛选
  if (severityFilter.value === 'danger') {
    list = list.filter(a => a.severity === 'danger')
  } else if (severityFilter.value === 'danger_warning') {
    list = list.filter(a => a.severity === 'danger' || a.severity === 'warning')
  } else if (severityFilter.value === 'ignored') {
    list = list.filter(a => a.acknowledged_status === 'ignored')
  }

  // 类型筛选
  if (typeFilter.value !== 'all') {
    list = list.filter(a => a.alert_type === typeFilter.value)
  }

  // 默认隐藏已忽略的 info（已忽略的 info 不再占位）
  if (hideIgnoredInfo.value && severityFilter.value !== 'ignored') {
    list = list.filter(a => !(a.severity === 'info' && a.acknowledged_status === 'ignored'))
  }

  // 排序：danger > warning > info
  const order = { danger: 0, warning: 1, info: 2 }
  return list.sort((a, b) => (order[a.severity] ?? 3) - (order[b.severity] ?? 3))
})

// 类型筛选下拉选项
const typeOptions = computed(() => {
  const types = new Set(alerts.value.map(a => a.alert_type).filter(Boolean))
  return [{ value: 'all', label: '全部类型' }, ...[...types].map(t => ({ value: t, label: t }))]
})

watch([severityFilter, typeFilter, riskView], () => {
  alertOffset.value = 0
  loadAlerts()
})
function changeAlertPage(delta) {
  alertOffset.value = Math.max(0, alertOffset.value + delta * 50)
  loadAlerts()
}

usePageRefresh(refreshAll)
</script>

<template>
  <div class="alert-center bg-mesh">
    <PortfolioRiskOverview :refresh-key="alerts" />
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
        <button class="btn-ghost btn-sm" :class="{ 'btn-loading': dailyAdviceLoading }" :disabled="dailyAdviceLoading" @click="refreshAll" title="刷新风险与提示">
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
        <span class="comprehensive-error-text"><Icon name="warning" size="14" /> {{ comprehensiveError }}</span>
      </div>
      <div v-else-if="comprehensiveResult" class="comprehensive-result">
        <div class="comprehensive-result-header editorial-card-header">
          <strong class="title">AI 综合操作建议</strong>
          <span v-if="comprehensiveResult.cached" class="cached-tag">缓存命中</span>
          <span class="signal-count">
            基于 <span class="font-jet">{{ comprehensiveResult.signal_count }}</span> 条信号
          </span>
          <button class="btn-ghost btn-xs" title="关闭" @click="comprehensiveResult = null"><Icon name="close" size="14" /></button>
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
        <span class="advice-empty-text"><Icon name="success" size="15" /> 今日暂无需行动的持仓提示</span>
        <button class="btn-ghost btn-sm" @click="handleAdviceAction(null, 'refresh')">手动生成</button>
      </div>
    </section>

    <!-- 风险预警 -->
    <section v-if="activeTab === 'alerts' || activeTab === 'merged'" class="alert-panel editorial-card">
      <div class="alert-panel-header editorial-card-header">
        <svg class="title-icon" width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>
        </svg>
        <strong class="title">活动风险 {{ activeRiskCount }} · 通知未读 {{ unreadAlertCount }}</strong>
        <span v-if="unreadAlertCount > 0" class="alert-badge has-unread font-jet">{{ unreadAlertCount }}</span>
        <span v-if="dangerAlertCount > 0" class="danger-tag">
          <span class="terminal-label">DANGER</span>
          <span class="font-jet">{{ dangerAlertCount }}</span>
          条
        </span>
        <span class="terminal-label alert-panel-eyebrow">RISK FEED</span>
      </div>

      <div class="alert-list">
        <div class="risk-status-panel">
          <p>已读、采纳或暂时忽略后，活动风险仍会展示；只有完整巡检确认触发条件消失才自动恢复。</p>
          <p v-if="lastSuccessfulReadAt">最后成功读取 {{ lastSuccessfulReadAt }}</p>
          <p v-if="lastScan">最近巡检 {{ lastScan.last_scan || lastScan.scanned_at || '时间未知' }}</p>
          <label>风险范围
            <select v-model="riskView" class="filter-select">
              <option value="active">所有活动风险（含已读）</option>
              <option value="all">全部风险（含已恢复）</option>
            </select>
          </label>
        </div>
        <div v-if="alertsError" class="comprehensive-error" role="alert">风险读取失败：{{ alertsError }}。保留上次已读取事实，请重试。</div>
        <div v-if="alertScanResult" class="risk-status-panel" :class="{ 'scan-incomplete': alertScanResult.status !== 'complete' }" role="status">
          <strong>{{ alertScanResult.status === 'complete' ? '巡检完成' : '检查未完成' }}</strong>
          · 新增 {{ alertScanResult.generated || 0 }} · 更新 {{ alertScanResult.updated || 0 }} · 恢复 {{ alertScanResult.resolved || 0 }}
          · 失败 {{ alertScanResult.failed || 0 }} · 未覆盖 {{ alertScanResult.skipped || 0 }}
          <p v-if="alertScanResult.error">{{ alertScanResult.error }}</p>
          <p v-for="[name, item] in incompleteCoverage" :key="name">{{ name }}：{{ item.reason || item.status }}<span v-if="item.details"> {{ JSON.stringify(item.details) }}</span></p>
          <p v-if="alertScanResult.status !== 'complete'">未覆盖的检查不能判断安全，既有风险继续保留。</p>
        </div>
        <div v-if="alertsLoading" class="alert-empty">读取风险中…</div>
        <div v-else-if="alerts.length === 0 && !alertsError" class="alert-empty">
          <span>当前范围暂无已记录风险</span>
          <span class="alert-empty-hint">结合最近巡检覆盖情况判断，未覆盖项目仍待核对。</span>
        </div>

        <!-- 筛选器工具条（方案 E） -->
        <div class="alert-filters">
          <div class="filter-group">
            <button :class="['filter-chip', { active: severityFilter === 'all' }]" @click="severityFilter = 'all'">全部</button>
            <button :class="['filter-chip', { active: severityFilter === 'danger_warning' }]" @click="severityFilter = 'danger_warning'">仅高优</button>
            <button :class="['filter-chip', { active: severityFilter === 'danger' }]" @click="severityFilter = 'danger'">仅 DANGER</button>
            <button :class="['filter-chip', { active: severityFilter === 'ignored' }]" @click="severityFilter = 'ignored'">已忽略</button>
          </div>
          <select v-model="typeFilter" class="filter-select">
            <option v-for="opt in typeOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
          </select>

        </div>

        <div v-if="sortedAlerts.length === 0 && alerts.length > 0" class="alert-empty">
          <span>当前筛选下无预警</span>
          <button class="btn-ghost btn-xs" @click="severityFilter = 'all'; typeFilter = 'all'">清除筛选</button>
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
              <span class="alert-ack-tag">{{ lifecycleLabel(a.risk_status) }}</span>
              <span v-if="a.is_read" class="alert-ack-tag">通知已读</span>
              <span v-if="a.acknowledged_status === 'acknowledged'" class="alert-ack-tag ack-acknowledged">已采纳（未确认成交）</span>
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

            <div class="risk-facts">
              <p>事实日期：{{ a.fact_date || '未提供，待核对' }} · 首次发现：{{ a.first_detected_at || a.first_at || '未知' }}</p>
              <p>最后确认：{{ a.last_confirmed_at || a.latest_at || '未知' }} · 恶化版本 {{ a.revision || 1 }} · 第 {{ a.episode || 1 }} 次触发</p>
              <p>恢复条件：{{ a.recovery_condition || '需要完整覆盖该类别的成功巡检确认，不以已读或采纳代替' }}</p>
              <p v-if="a.review_date">忽略原因：{{ a.action_reason }}；复查日期：{{ a.review_date }} <strong v-if="reviewIsDue(a)">复查已到期，请重新评估</strong></p>
              <p v-if="a.resolved_at">恢复确认：{{ a.resolved_at }}</p>
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
                    生命周期记录 <strong class="font-jet">{{ alertHistoryMap[a.latest_id].history.length }}</strong> 次同类预警
                  </div>
                  <div v-for="h in alertHistoryMap[a.latest_id].history.slice(0, 5)" :key="h.id" class="history-item">
                    <span :class="['history-sev', 'terminal-label', `sev-${h.severity}`]">{{ h.event_type || h.severity }}</span>
                    <span class="history-time font-jet">{{ h.created_at }}</span>
                    <span class="history-title">{{ h.title || h.evidence }}</span>
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
              :disabled="a.risk_status === 'resolved' || ackLoading[a.latest_id]"
              @click="handleAcknowledgeAlert(a, 'acknowledged')"
              title="采纳进入处理中，不代表成交"
            >记录采纳</button>
            <button
              class="btn-ghost btn-sm ack-ignore-btn"
              :disabled="a.risk_status === 'resolved' || ackLoading[a.latest_id]"
              @click="handleAcknowledgeAlert(a, 'ignored')"
              title="记录原因与复查日期，风险继续保留"
            >暂时忽略</button>
            <button class="btn-ghost btn-sm" :disabled="!!a.is_read" @click="handleMarkAlertRead(a.latest_id)" title="仅标记通知已读">已读</button>
            <button v-if="a.risk_status === 'resolved'" class="btn-ghost btn-sm btn-danger-text" @click="handleDeleteAlert(a.latest_id, a.title, a.severity)" title="清理已恢复通知">清理</button>
          </div>
        </div>
      </div>
    </section>

    <div class="alert-filters" v-if="alertTotal > 50 || alertOffset > 0">
      <button class="btn-secondary btn-sm" :disabled="alertOffset === 0 || alertsLoading" @click="changeAlertPage(-1)">上一页</button>
      <span>{{ alertOffset + 1 }}–{{ Math.min(alertOffset + 50, alertTotal) }} / {{ alertTotal }} 条</span>
      <button class="btn-secondary btn-sm" :disabled="alertOffset + 50 >= alertTotal || alertsLoading" @click="changeAlertPage(1)">下一页</button>
    </div>
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="actionDialog.visible" class="risk-action-backdrop" @click.self="actionDialog.visible = false">
          <form class="risk-action-dialog editorial-card" @submit.prevent="confirmRiskAction">
            <h3>暂时忽略 · {{ actionDialog.alert?.title }}</h3>
            <p>此记录保留活动风险。恶化或再次触发会重新提醒。</p>
            <label>原因<textarea v-model="actionDialog.reason" required maxlength="500" rows="3" placeholder="说明暂不处理的依据" /></label>
            <label>复查日期<input v-model="actionDialog.review_date" type="date" required /></label>
            <div class="alert-action-buttons">
              <button class="btn-secondary" type="button" @click="actionDialog.visible = false">取消</button>
              <button class="btn-primary" type="submit">核对并记录</button>
            </div>
          </form>
        </div>
      </Transition>
    </Teleport>
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
.risk-status-panel, .risk-facts { padding: 0.6rem 1rem; color: var(--color-text-secondary); font-size: 0.76rem; line-height: 1.6; overflow-wrap: anywhere; }
.risk-status-panel p, .risk-facts p { margin: 0.25rem 0; }
.risk-facts { padding: 0.4rem 0; }
.scan-incomplete { background: var(--color-warning-bg); border-left: 3px solid var(--color-warning); }
.risk-action-backdrop { position: fixed; inset: 0; z-index: 1000; display: grid; place-items: center; padding: 1rem; background: rgba(0,0,0,.55); }
.risk-action-dialog { width: min(100%, 30rem); padding: 1.5rem; background: var(--color-bg-card); }
.risk-action-dialog label { display: grid; gap: .4rem; margin: 1rem 0; }
.risk-action-dialog textarea, .risk-action-dialog input { width: 100%; padding: .6rem; border: 1px solid var(--color-border); border-radius: 6px; background: var(--color-bg-input); color: var(--color-text-primary); }

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
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.4rem;
  white-space: nowrap;
  flex: 1 1 auto;
  min-width: 0;
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
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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
  /* 三个中文标签在 390px 下放不下英文副标，隐藏后正好；标签本身省略而非换行 */
  .ac-tab-eyebrow { display: none; }
  .alert-panel-eyebrow { display: none; }
  .ac-tab { padding: 0.6rem 0.4rem; font-size: 0.8rem; gap: 0.3rem; }
  .ac-tabs { overflow-x: auto; scrollbar-width: none; }
  .ac-tabs::-webkit-scrollbar { display: none; }
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
  flex-wrap: wrap;            /* 5 个子元素在窄屏必须能换行，否则互相挤压重叠 */
  gap: 0.5rem;
  padding: 0.75rem 1.25rem;
  background: var(--color-bg-hover);
  font-size: 0.85rem;
  font-weight: 600;
  border-bottom: 1px solid var(--color-border);
  margin-bottom: 0;
}
.alert-panel-header .title {
  min-width: 0;
  flex: 1 1 auto;
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
  font-size: 0.7rem;
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

/* 筛选器工具条（方案 E） */
.alert-filters {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  margin-bottom: 0.5rem;
  background: var(--color-bg-sunken, rgba(0, 0, 0, 0.2));
  border-radius: 6px;
  flex-wrap: wrap;
}
.filter-group {
  display: flex;
  gap: 0.25rem;
}
.filter-chip {
  padding: 0.2rem 0.6rem;
  border: 1px solid var(--color-border, rgba(255, 255, 255, 0.1));
  background: transparent;
  color: var(--color-text-secondary);
  border-radius: var(--radius-full);
  font-size: 0.72rem;
  cursor: pointer;
  transition: all 0.15s ease;
}
.filter-chip:hover {
  border-color: var(--color-accent, #4fd1c5);
  color: var(--color-text-primary);
}
.filter-chip.active {
  background: var(--color-primary);
  color: var(--color-bg-primary, #0a0e1a);
  border-color: var(--color-accent, #4fd1c5);
  font-weight: 600;
}
.filter-select {
  padding: 0.2rem 0.4rem;
  background: var(--color-bg-input, rgba(0, 0, 0, 0.3));
  color: var(--color-text-primary);
  border: 1px solid var(--color-border, rgba(255, 255, 255, 0.1));
  border-radius: 4px;
  font-size: 0.72rem;
  cursor: pointer;
  max-width: 160px;
}
.filter-select option {
  background: var(--color-bg-primary, #0a0e1a);
}
.ignore-all-btn {
  margin-left: auto;
  color: var(--color-text-tertiary);
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
  font-size: 0.7rem;
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
  font-size: 0.7rem;
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
