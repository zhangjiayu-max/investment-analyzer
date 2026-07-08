<script setup>
import { ref, computed, onMounted } from 'vue'
import {
  listDecisions, updateDecisionStatus, submitDecisionReview, completeDecisionAction,
  getDecisionPrecheck, getExecutionStatus, getDecisionTimeline, createTransactionDraftFromDecision,
  getDecisionStats, listRecommendationCandidates, ignoreRecommendationCandidate,
  createDecisionFromCandidate, deferRecommendationCandidate,
} from '../api'
import { useToast } from '../composables/useToast'
import Icon from './ui/Icon.vue'
import ConfirmDialog from './ConfirmDialog.vue'

const { showToast } = useToast()

// ── 数据 ──
const loading = ref(false)
const decisions = ref([])
const precheckCache = ref({})  // { decisionId: precheckResult }
const executionMatches = ref({})  // { decisionId: matchInfo }
const draftingDecisionId = ref(null)
const candidates = ref([])
const decisionStats = ref(null)
const candidateActionId = ref(null)

// ── 筛选 ──
const activeFilter = ref('all')  // all | proposed | accepted | executed | reviewed | rejected

// ── 移动端 Tab ──
const mobileTab = ref('pending')  // pending | reviewing | completed

// ── 复盘弹窗 ──
const reviewModal = ref({ visible: false, decision: null })
const reviewForm = ref({
  outcome: 'helpful',
  result_note: '',
  profit_change: null,
  lesson: '',
})
const reviewSaving = ref(false)

// ── 确认弹窗 ──
const confirmState = ref({ visible: false, title: '', message: '', onConfirm: null })

// ── 时间线弹窗 ──
const timelineModal = ref({ visible: false, decision: null, loading: false, data: null })

async function openTimeline(decision) {
  timelineModal.value = { visible: true, decision, loading: true, data: null }
  try {
    const { data } = await getDecisionTimeline(decision.id)
    timelineModal.value.data = data
  } catch (e) {
    showToast('加载时间线失败', 'error')
    timelineModal.value.visible = false
  } finally {
    timelineModal.value.loading = false
  }
}

function timelineEventClass(eventType) {
  if (eventType === 'created') return 'tl-created'
  if (eventType.startsWith('status_')) return 'tl-status'
  if (eventType === 'action_done') return 'tl-action'
  if (eventType === 'reviewed') return 'tl-review'
  return 'tl-default'
}

// ── 统计 ──
const localStats = computed(() => {
  const all = decisions.value
  const proposed = all.filter(d => d.status === 'proposed').length
  const accepted = all.filter(d => d.status === 'accepted').length
  const executed = all.filter(d => d.status === 'executed').length
  const reviewed = all.filter(d => d.status === 'reviewed').length
  const reviewedItems = all.filter(d => d.status === 'reviewed' && d.review)
  const winCount = reviewedItems.filter(d => d.review.profit_change > 0).length
  const winRate = reviewedItems.length > 0 ? Math.round((winCount / reviewedItems.length) * 100) : 0
  return { proposed, accepted, executed, reviewed, winRate, total: all.length }
})

const stats = computed(() => {
  const remote = decisionStats.value
  if (!remote) return localStats.value
  return {
    proposed: remote.by_status?.proposed || 0,
    accepted: remote.by_status?.accepted || 0,
    executed: remote.by_status?.executed || 0,
    reviewed: remote.reviewed || remote.by_status?.reviewed || 0,
    winRate: remote.review_helpful_rate || 0,
    total: remote.total || 0,
  }
})

const insightText = computed(() => {
  const remote = decisionStats.value
  if (!remote || !remote.reviewed) return '复盘样本还少，先把已执行决策补齐结果和教训。'
  const profit = Number(remote.total_profit_change || 0)
  const lesson = remote.recent_lessons?.[0]
  const profitText = profit === 0 ? '累计盈亏持平' : `累计复盘盈亏 ${profit > 0 ? '+' : ''}¥${money(profit)}`
  return lesson
    ? `${profitText}，最近可复用经验：${lesson}`
    : `${profitText}，继续积累复盘经验。`
})

// ── 三栏分组 ──
const pendingDecisions = computed(() =>
  decisions.value.filter(d => ['proposed', 'accepted', 'deferred'].includes(d.status))
)
const reviewingDecisions = computed(() =>
  decisions.value.filter(d => d.status === 'executed')
)
const completedDecisions = computed(() =>
  decisions.value.filter(d => ['reviewed', 'rejected', 'expired'].includes(d.status))
)

// ── 加载 ──
async function load() {
  loading.value = true
  try {
    const { data } = await listDecisions('', 200)
    decisions.value = data.items || []
    // 仅对待执行决策按需加载预检查（分批避免并发风暴）
    const pendingIds = decisions.value
      .filter(d => ['proposed', 'accepted', 'deferred'].includes(d.status))
      .map(d => d.id)
    loadPrechecksBatched(pendingIds)
    // 异步加载执行状态匹配
    loadExecutionStatus()
    loadCandidates()
    loadDecisionStats()
  } catch (e) {
    showToast('加载决策失败: ' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    loading.value = false
  }
}

async function loadCandidates() {
  try {
    const { data } = await listRecommendationCandidates('new', 20)
    candidates.value = data.items || []
  } catch { /* silent */ }
}

async function loadDecisionStats() {
  try {
    const { data } = await getDecisionStats()
    decisionStats.value = data
  } catch { /* silent */ }
}

async function loadExecutionStatus() {
  try {
    const { data } = await getExecutionStatus()
    const matches = data.matches || []
    const map = {}
    for (const m of matches) {
      map[m.decision_id] = m
    }
    executionMatches.value = map
  } catch { /* silent */ }
}

async function loadPrecheck(decisionId) {
  if (precheckCache.value[decisionId]) return
  try {
    const { data } = await getDecisionPrecheck(decisionId)
    precheckCache.value[decisionId] = data
  } catch { /* silent */ }
}

/** 分批加载预检查，每批 5 个并发，避免 N+1 请求风暴 */
async function loadPrechecksBatched(decisionIds, batchSize = 5) {
  for (let i = 0; i < decisionIds.length; i += batchSize) {
    const batch = decisionIds.slice(i, i + batchSize)
    await Promise.all(batch.map(id => loadPrecheck(id)))
  }
}

// ── 状态操作 ──
function confirmAction(title, message, onConfirm) {
  confirmState.value = { visible: true, title, message, onConfirm }
}

async function doStatusUpdate(decisionId, status, note = '') {
  try {
    await updateDecisionStatus(decisionId, status, note)
    showToast(`决策已${statusLabel(status)}`, 'success')
    await load()
  } catch (e) {
    showToast('操作失败: ' + (e.response?.data?.detail || e.message), 'error')
  }
}

function acceptDecision(decisionId) {
  confirmAction('接受决策', '确认接受此决策？接受后可执行。', () => doStatusUpdate(decisionId, 'accepted'))
}
function rejectDecision(decisionId) {
  confirmAction('拒绝决策', '确认拒绝此决策？拒绝后不可再执行。', () => doStatusUpdate(decisionId, 'rejected'))
}
function deferDecision(decisionId) {
  confirmAction('暂缓决策', '确认暂缓此决策？后续可重新接受。', () => doStatusUpdate(decisionId, 'deferred'))
}
function executeDecision(decisionId) {
  confirmAction('执行决策', '确认此决策已执行？执行后进入待复盘状态。', () => doStatusUpdate(decisionId, 'executed'))
}

function canCreateTransactionDraft(decision) {
  return ['add', 'buy', 'reduce', 'sell'].includes(decision.decision_type)
}

function createDraftFromDecision(decision) {
  const precheck = precheckCache.value[decision.id]
  const blockers = precheck?.blockers || []
  const message = blockers.length
    ? `执行前检查发现阻断项：${blockers.join('；')}。请先处理后再生成交易草稿。`
    : '将根据该决策生成一笔待确认交易草稿。此操作不会确认交易，也不会直接修改真实持仓。是否继续？'
  confirmAction('生成交易草稿', message, async () => {
    if (blockers.length) return
    draftingDecisionId.value = decision.id
    try {
      const { data } = await createTransactionDraftFromDecision(decision.id)
      showToast(`已生成交易草稿 #${data.transaction_id}`, 'success')
      precheckCache.value = {}
      await load()
    } catch (e) {
      const detail = e.response?.data?.detail
      const msg = typeof detail === 'object' ? (detail.error || JSON.stringify(detail)) : (detail || e.message)
      showToast('生成交易草稿失败: ' + msg, 'error')
    } finally {
      draftingDecisionId.value = null
    }
  })
}

function saveCandidateAsDecision(candidate) {
  confirmAction(
    '保存为决策',
    `将「${candidate.summary}」保存为待执行决策草案。保存后仍需执行前检查，不会直接交易。是否继续？`,
    async () => {
      candidateActionId.value = candidate.id
      try {
        const { data } = await createDecisionFromCandidate(candidate.id, { review_days: 30 })
        showToast(`已保存为决策 #${data.decision_id}`, 'success')
        precheckCache.value = {}
        await load()
      } catch (e) {
        const detail = e.response?.data?.detail
        const msg = typeof detail === 'object' ? (detail.error || JSON.stringify(detail)) : (detail || e.message)
        showToast('保存候选失败: ' + msg, 'error')
      } finally {
        candidateActionId.value = null
      }
    }
  )
}

function ignoreCandidate(candidate) {
  confirmAction(
    '忽略建议',
    `确认忽略「${candidate.summary}」？忽略后不会生成决策。`,
    async () => {
      candidateActionId.value = candidate.id
      try {
        await ignoreRecommendationCandidate(candidate.id)
        showToast('已忽略建议', 'success')
        await loadCandidates()
      } catch (e) {
        showToast('忽略失败: ' + (e.response?.data?.detail || e.message), 'error')
      } finally {
        candidateActionId.value = null
      }
    }
  )
}

function deferCandidate(candidate) {
  const d = new Date()
  d.setDate(d.getDate() + 7)
  const deferredUntil = d.toISOString().slice(0, 10)
  confirmAction(
    '稍后处理',
    `将「${candidate.summary}」延期到 ${deferredUntil} 再处理。`,
    async () => {
      candidateActionId.value = candidate.id
      try {
        await deferRecommendationCandidate(candidate.id, deferredUntil)
        showToast('已延期处理建议', 'success')
        await loadCandidates()
      } catch (e) {
        showToast('延期失败: ' + (e.response?.data?.detail || e.message), 'error')
      } finally {
        candidateActionId.value = null
      }
    }
  )
}

function confirmExecutionFromMatch(decisionId) {
  const match = executionMatches.value[decisionId]
  const txInfo = match ? `${match.tx_count}笔交易，买入${match.buy_shares}份/卖出${match.sell_shares}份` : ''
  confirmAction(
    '确认执行',
    `系统检测到持仓变化：${txInfo}。确认将此决策标记为已执行？`,
    async () => {
      await doStatusUpdate(decisionId, 'executed')
      delete executionMatches.value[decisionId]
    }
  )
}

async function completeAction(decisionId, actionId) {
  try {
    await completeDecisionAction(decisionId, actionId)
    showToast('行动项已完成', 'success')
    await load()
  } catch (e) {
    showToast('操作失败: ' + (e.response?.data?.detail || e.message), 'error')
  }
}

// ── 复盘 ──
function openReview(decision) {
  reviewModal.value = { visible: true, decision }
  reviewForm.value = {
    outcome: 'helpful',
    result_note: '',
    profit_change: null,
    lesson: '',
  }
}

async function submitReview() {
  const decision = reviewModal.value.decision
  if (!decision) return
  reviewSaving.value = true
  try {
    await submitDecisionReview(decision.id, {
      outcome: reviewForm.value.outcome,
      result_note: reviewForm.value.result_note,
      profit_change: reviewForm.value.profit_change,
      lesson: reviewForm.value.lesson,
    })
    showToast('复盘已提交', 'success')
    reviewModal.value = { visible: false, decision: null }
    await load()
  } catch (e) {
    showToast('复盘失败: ' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    reviewSaving.value = false
  }
}

// ── 辅助函数 ──
function statusLabel(status) {
  return { proposed: '提案', accepted: '已接受', rejected: '已拒绝', deferred: '已暂缓', executed: '已执行', expired: '已过期', reviewed: '已复盘' }[status] || status
}

function statusClass(status) {
  return {
    proposed: 'status-proposed',
    accepted: 'status-accepted',
    rejected: 'status-rejected',
    deferred: 'status-deferred',
    executed: 'status-executed',
    expired: 'status-expired',
    reviewed: 'status-reviewed',
  }[status] || 'status-proposed'
}

function decisionTypeLabel(type) {
  return { add: '配置', buy: '买入', rebalance: '再平衡', watch: '观察', hold: '持有', reduce: '减仓', sell: '卖出' }[type] || type || '决策'
}

function candidateSourceLabel(candidate) {
  const map = {
    panorama: '全景诊断',
    deep_dive: '深度分析',
    trade_review: '交易复盘',
    fund_analysis: '基金分析',
    ai: 'AI持仓分析',
  }
  return map[candidate.scenario_type] || candidate.scenario_type || candidate.source_type || 'AI分析'
}

function decisionTypeClass(type) {
  return { add: 'type-add', buy: 'type-add', rebalance: 'type-rebalance', watch: 'type-watch', hold: 'type-hold', reduce: 'type-reduce', sell: 'type-sell' }[type] || 'type-watch'
}

function targetText(item) {
  return item.target_name || item.target_code || item.target_type || '组合'
}

function formatDate(dateStr) {
  if (!dateStr) return ''
  const d = new Date(dateStr)
  return `${d.getMonth() + 1}/${d.getDate()}`
}

function money(value) {
  return Number(value || 0).toLocaleString('zh-CN', { maximumFractionDigits: 0 })
}

function precheckItems(decisionId) {
  const precheck = precheckCache.value[decisionId]
  if (!precheck || !precheck.exists) return []
  const items = []
  for (const blocker of precheck.blockers || []) {
    items.push({ level: 'error', text: blocker })
  }
  for (const warning of precheck.warnings || []) {
    items.push({ level: 'warn', text: warning })
  }
  if (precheck.bucket_check) {
    const bc = precheck.bucket_check
    if (bc.blocked) {
      items.push({ level: 'error', text: `资金桶拦截：${bc.reason || '不允许高风险资产'}` })
    } else {
      items.push({ level: 'ok', text: `资金桶：${bc.bucket_name || '匹配'}` })
    }
  }
  if (precheck.allocation_check) {
    const ac = precheck.allocation_check
    if (ac.exceeds_limit) {
      items.push({ level: 'warn', text: `占比超限：加仓后 ${Math.round((ac.after_ratio || 0) * 100)}%` })
    } else {
      items.push({ level: 'ok', text: `占比正常` })
    }
  }
  return items
}

onMounted(load)
</script>

<template>
  <div class="decisions-page bg-mesh">
    <!-- 页头 -->
    <header class="page-head">
      <div>
        <h2 class="page-title editorial-title-lg">决策档案</h2>
        <p class="page-desc">记录每一次买卖决策，追踪执行和复盘，形成理财闭环。</p>
      </div>
      <button class="btn-secondary" @click="load" :disabled="loading">
        <Icon :name="loading ? 'spinner' : 'refresh'" size="16" />
        刷新
      </button>
    </header>

    <!-- 统计条 -->
    <section class="stats-strip">
      <div class="stat-cell">
        <span class="terminal-label">提案</span>
        <strong class="font-jet">{{ stats.proposed }}</strong>
      </div>
      <div class="stat-cell">
        <span class="terminal-label">已接受</span>
        <strong class="font-jet">{{ stats.accepted }}</strong>
      </div>
      <div class="stat-cell">
        <span class="terminal-label">已执行</span>
        <strong class="font-jet">{{ stats.executed }}</strong>
      </div>
      <div class="stat-cell">
        <span class="terminal-label">已复盘</span>
        <strong class="font-jet">{{ stats.reviewed }}</strong>
      </div>
      <div class="stat-cell" :class="{ win: stats.winRate >= 60 }">
        <span class="terminal-label">胜率</span>
        <strong class="font-jet">{{ stats.winRate }}%</strong>
      </div>
      <div class="stat-cell">
        <span class="terminal-label">总计</span>
        <strong class="font-jet">{{ stats.total }}</strong>
      </div>
    </section>

    <!-- AI 建议候选 -->
    <section class="candidate-panel editorial-card">
      <div class="candidate-head editorial-card-header">
        <div>
          <h3 class="editorial-title">AI 建议候选</h3>
          <p>{{ insightText }}</p>
        </div>
        <span class="candidate-count meta terminal-label">{{ candidates.length }} 条待处理</span>
      </div>
      <div v-if="candidates.length" class="candidate-list">
        <article v-for="c in candidates" :key="c.id" class="candidate-item reveal-stagger">
          <div class="candidate-main">
            <div class="candidate-topline">
              <span :class="['type-badge', decisionTypeClass(c.action_type)]">{{ decisionTypeLabel(c.action_type) }}</span>
              <strong>{{ targetText(c) }}</strong>
            </div>
            <p>{{ c.summary }}</p>
            <div class="candidate-meta">
              <span><Icon name="sparkles" size="13" /> {{ candidateSourceLabel(c) }}</span>
              <span><Icon name="shield-check" size="13" /> {{ c.confidence || 'medium' }}</span>
              <span v-if="c.suggested_amount"><Icon name="banknote" size="13" /> <span class="font-jet">¥{{ money(c.suggested_amount) }}</span></span>
            </div>
          </div>
          <div class="candidate-actions">
            <button
              class="btn-sm btn-primary"
              :disabled="candidateActionId === c.id"
              @click="saveCandidateAsDecision(c)"
            >
              {{ candidateActionId === c.id ? '处理中...' : '保存为决策' }}
            </button>
            <button
              class="btn-sm btn-ghost"
              :disabled="candidateActionId === c.id"
              @click="deferCandidate(c)"
            >
              稍后
            </button>
            <button
              class="btn-sm btn-ghost"
              :disabled="candidateActionId === c.id"
              @click="ignoreCandidate(c)"
            >
              忽略
            </button>
          </div>
        </article>
      </div>
      <div v-else class="candidate-empty">
        <Icon name="sparkles" size="18" />
        <span>暂无新的 AI 建议候选</span>
      </div>
    </section>

    <!-- 移动端 Tab 切换 -->
    <div class="mobile-tabs">
      <button :class="['mobile-tab', { active: mobileTab === 'pending' }]" @click="mobileTab = 'pending'">
        待执行 <span class="tab-count font-jet">{{ pendingDecisions.length }}</span>
      </button>
      <button :class="['mobile-tab', { active: mobileTab === 'reviewing' }]" @click="mobileTab = 'reviewing'">
        待复盘 <span class="tab-count font-jet">{{ reviewingDecisions.length }}</span>
      </button>
      <button :class="['mobile-tab', { active: mobileTab === 'completed' }]" @click="mobileTab = 'completed'">
        已完成 <span class="tab-count font-jet">{{ completedDecisions.length }}</span>
      </button>
    </div>

    <!-- 三栏看板 -->
    <div class="kanban">
      <!-- 待执行 -->
      <section :class="['kanban-col', { 'mobile-hidden': mobileTab !== 'pending' }]">
        <header class="col-head">
          <h3 class="editorial-title">待执行</h3>
          <span class="col-count font-jet terminal-label">{{ pendingDecisions.length }}</span>
        </header>
        <div v-if="loading && !pendingDecisions.length" class="col-empty">
          <Icon name="spinner" size="20" />
        </div>
        <div v-else-if="!pendingDecisions.length" class="col-empty">
          <Icon name="clipboard-list" size="24" />
          <span>暂无待执行决策</span>
        </div>
        <article
          v-for="d in pendingDecisions"
          :key="d.id"
          :class="['decision-card', 'editorial-card', 'reveal-stagger', statusClass(d.status)]"
        >
          <div class="card-top">
            <span :class="['type-badge', decisionTypeClass(d.decision_type)]">{{ decisionTypeLabel(d.decision_type) }}</span>
            <span :class="['status-tag', statusClass(d.status)]">{{ statusLabel(d.status) }}</span>
          </div>
          <h4 class="card-title editorial-title">{{ targetText(d) }}</h4>
          <p class="card-summary">{{ d.summary }}</p>
          <div class="card-meta">
            <span class="terminal-label"><Icon name="calendar" size="13" /> {{ formatDate(d.created_at) }}</span>
            <span class="terminal-label"><Icon name="file-text" size="13" /> {{ d.source_type === 'chat' ? 'AI对话' : d.source_type === 'dashboard' ? '看板' : d.source_type }}</span>
            <button class="timeline-link" @click="openTimeline(d)"><Icon name="clock" size="13" /> 时间线</button>
          </div>
          <!-- 预检查 -->
          <div v-if="precheckItems(d.id).length" class="precheck-list">
            <div
              v-for="(pc, i) in precheckItems(d.id)"
              :key="i"
              :class="['precheck-item', `pc-${pc.level}`]"
            >
              <Icon :name="pc.level === 'ok' ? 'success' : pc.level === 'warn' ? 'warning' : 'error'" size="13" />
              {{ pc.text }}
            </div>
          </div>
          <!-- 行动项 -->
          <div v-if="d.actions?.length" class="action-list">
            <div v-for="a in d.actions" :key="a.id" class="action-item">
              <Icon :name="a.status === 'done' ? 'success' : 'circle'" size="13" />
              <span :class="{ done: a.status === 'done' }">{{ a.title }}</span>
              <button v-if="a.status !== 'done'" class="action-btn" @click="completeAction(d.id, a.id)">完成</button>
            </div>
          </div>
          <!-- 操作按钮 -->
          <div class="card-actions">
            <template v-if="d.status === 'proposed'">
              <button class="btn-sm btn-primary" @click="acceptDecision(d.id)">接受</button>
              <button
                v-if="canCreateTransactionDraft(d)"
                class="btn-sm btn-secondary"
                :disabled="draftingDecisionId === d.id"
                @click="createDraftFromDecision(d)"
              >
                {{ draftingDecisionId === d.id ? '生成中...' : '生成交易草稿' }}
              </button>
              <button class="btn-sm btn-ghost" @click="deferDecision(d.id)">暂缓</button>
              <button class="btn-sm btn-ghost danger" @click="rejectDecision(d.id)">拒绝</button>
            </template>
            <template v-else-if="d.status === 'accepted'">
              <button
                v-if="canCreateTransactionDraft(d)"
                class="btn-sm btn-secondary"
                :disabled="draftingDecisionId === d.id"
                @click="createDraftFromDecision(d)"
              >
                {{ draftingDecisionId === d.id ? '生成中...' : '生成交易草稿' }}
              </button>
              <button class="btn-sm btn-primary" @click="executeDecision(d.id)">已执行</button>
              <button class="btn-sm btn-ghost" @click="deferDecision(d.id)">暂缓</button>
            </template>
            <template v-else-if="d.status === 'deferred'">
              <button class="btn-sm btn-primary" @click="acceptDecision(d.id)">重新接受</button>
              <button class="btn-sm btn-ghost danger" @click="rejectDecision(d.id)">拒绝</button>
            </template>
          </div>
          <!-- 执行状态自动匹配提示 -->
          <div v-if="d.status === 'accepted' && executionMatches[d.id]" class="execution-match-bar">
            <div class="match-info">
              <Icon name="check-circle" size="14" />
              <span>检测到已执行？匹配 <span class="font-jet">{{ executionMatches[d.id].tx_count }}</span> 笔交易</span>
              <span class="match-detail">
                <template v-if="executionMatches[d.id].buy_shares > 0">买入 <span class="font-jet">{{ executionMatches[d.id].buy_shares }}</span> 份</template>
                <template v-if="executionMatches[d.id].sell_shares > 0"> / 卖出 <span class="font-jet">{{ executionMatches[d.id].sell_shares }}</span> 份</template>
              </span>
            </div>
            <button class="btn-sm btn-primary" @click="confirmExecutionFromMatch(d.id)">确认执行</button>
          </div>
        </article>
      </section>

      <!-- 待复盘 -->
      <section :class="['kanban-col', { 'mobile-hidden': mobileTab !== 'reviewing' }]">
        <header class="col-head">
          <h3 class="editorial-title">待复盘</h3>
          <span class="col-count font-jet terminal-label">{{ reviewingDecisions.length }}</span>
        </header>
        <div v-if="!reviewingDecisions.length" class="col-empty">
          <Icon name="clock" size="24" />
          <span>暂无待复盘决策</span>
        </div>
        <article
          v-for="d in reviewingDecisions"
          :key="d.id"
          :class="['decision-card', 'editorial-card', 'reveal-stagger', statusClass(d.status)]"
        >
          <div class="card-top">
            <span :class="['type-badge', decisionTypeClass(d.decision_type)]">{{ decisionTypeLabel(d.decision_type) }}</span>
            <span :class="['status-tag', statusClass(d.status)]">{{ statusLabel(d.status) }}</span>
          </div>
          <h4 class="card-title editorial-title">{{ targetText(d) }}</h4>
          <p class="card-summary">{{ d.summary }}</p>
          <div class="card-meta">
            <span class="terminal-label"><Icon name="calendar" size="13" /> {{ formatDate(d.created_at) }}</span>
            <span v-if="d.review_at" class="terminal-label"><Icon name="alarm-clock" size="13" /> 复盘到期 {{ formatDate(d.review_at) }}</span>
            <button class="timeline-link" @click="openTimeline(d)"><Icon name="clock" size="13" /> 时间线</button>
          </div>
          <div class="card-actions">
            <button class="btn-sm btn-primary" @click="openReview(d)">
              <Icon name="pencil" size="13" />
              去复盘
            </button>
          </div>
        </article>
      </section>

      <!-- 已完成 -->
      <section :class="['kanban-col', { 'mobile-hidden': mobileTab !== 'completed' }]">
        <header class="col-head">
          <h3 class="editorial-title">已完成</h3>
          <span class="col-count font-jet terminal-label">{{ completedDecisions.length }}</span>
        </header>
        <div v-if="!completedDecisions.length" class="col-empty">
          <Icon name="check" size="24" />
          <span>暂无已完成决策</span>
        </div>
        <article
          v-for="d in completedDecisions"
          :key="d.id"
          :class="['decision-card', 'editorial-card', 'reveal-stagger', 'completed', statusClass(d.status)]"
        >
          <div class="card-top">
            <span :class="['type-badge', decisionTypeClass(d.decision_type)]">{{ decisionTypeLabel(d.decision_type) }}</span>
            <span :class="['status-tag', statusClass(d.status)]">{{ statusLabel(d.status) }}</span>
          </div>
          <h4 class="card-title editorial-title">{{ targetText(d) }}</h4>
          <p class="card-summary">{{ d.summary }}</p>
          <div class="card-meta">
            <span class="terminal-label"><Icon name="calendar" size="13" /> {{ formatDate(d.created_at) }}</span>
            <button class="timeline-link" @click="openTimeline(d)"><Icon name="clock" size="13" /> 时间线</button>
          </div>
          <!-- 复盘结果 -->
          <div v-if="d.review" class="review-result">
            <div class="review-outcome terminal-label" :class="`outcome-${d.review.outcome}`">
              {{ { helpful: '有帮助', neutral: '一般', unhelpful: '没帮助' }[d.review.outcome] || d.review.outcome }}
            </div>
            <div v-if="d.review.profit_change != null" class="review-pnl font-jet" :class="d.review.profit_change >= 0 ? 'positive' : 'negative'">
              {{ d.review.profit_change >= 0 ? '+' : '' }}¥{{ money(d.review.profit_change) }}
            </div>
            <p v-if="d.review.lesson" class="review-lesson">{{ d.review.lesson }}</p>
          </div>
        </article>
      </section>
    </div>

    <!-- 复盘弹窗 -->
    <div v-if="reviewModal.visible" class="modal-overlay" @click.self="reviewModal.visible = false">
      <div class="modal-card">
        <header class="modal-head">
          <h3 class="editorial-title">决策复盘</h3>
          <button class="icon-btn" @click="reviewModal.visible = false"><Icon name="close" size="18" /></button>
        </header>
        <div class="modal-body">
          <div class="modal-info">
            <strong>{{ targetText(reviewModal.decision) }}</strong>
            <span>{{ reviewModal.decision?.summary }}</span>
          </div>
          <label>
            复盘结果
            <select v-model="reviewForm.outcome" class="input-field">
              <option value="helpful">有帮助</option>
              <option value="neutral">一般</option>
              <option value="unhelpful">没帮助</option>
            </select>
          </label>
          <label>
            盈亏金额（可选）
            <input v-model.number="reviewForm.profit_change" type="number" step="0.01" class="input-field" placeholder="如 1500 或 -800" />
          </label>
          <label>
            结果说明
            <textarea v-model="reviewForm.result_note" class="input-field" rows="3" placeholder="实际执行后发生了什么"></textarea>
          </label>
          <label>
            经验教训
            <textarea v-model="reviewForm.lesson" class="input-field" rows="3" placeholder="下次遇到类似情况应该怎么做"></textarea>
          </label>
        </div>
        <footer class="modal-foot">
          <button class="btn-ghost" @click="reviewModal.visible = false">取消</button>
          <button class="btn-primary" @click="submitReview" :disabled="reviewSaving">
            <Icon :name="reviewSaving ? 'spinner' : 'check'" size="16" />
            提交复盘
          </button>
        </footer>
      </div>
    </div>

    <!-- 时间线弹窗 -->
    <div v-if="timelineModal.visible" class="modal-overlay" @click.self="timelineModal.visible = false">
      <div class="modal-card modal-wide">
        <header class="modal-head">
          <h3 class="editorial-title">决策时间线</h3>
          <button class="icon-btn" @click="timelineModal.visible = false"><Icon name="close" size="18" /></button>
        </header>
        <div class="modal-body">
          <div v-if="timelineModal.loading" class="tl-loading">
            <Icon name="spinner" size="24" />
            <span>加载中...</span>
          </div>
          <div v-else-if="timelineModal.data" class="tl-container">
            <div class="tl-header">
              <strong class="editorial-title">{{ timelineModal.data.summary }}</strong>
              <span :class="['status-tag', statusClass(timelineModal.data.status)]">{{ statusLabel(timelineModal.data.status) }}</span>
            </div>
            <div class="tl-list">
              <div
                v-for="(ev, i) in timelineModal.data.timeline"
                :key="i"
                :class="['tl-item', 'reveal-stagger', timelineEventClass(ev.event_type)]"
              >
                <div class="tl-dot">{{ ev.icon }}</div>
                <div class="tl-content">
                  <div class="tl-label">{{ ev.label }}</div>
                  <div class="tl-time terminal-label">{{ ev.time }}</div>
                  <div v-if="ev.detail && typeof ev.detail === 'string'" class="tl-detail">{{ ev.detail }}</div>
                  <div v-if="ev.lesson" class="tl-lesson">
                    <Icon name="lightbulb" size="13" /> {{ ev.lesson }}
                  </div>
                  <div v-if="ev.profit_change != null" class="tl-pnl font-jet" :class="ev.profit_change >= 0 ? 'positive' : 'negative'">
                    {{ ev.profit_change >= 0 ? '+' : '' }}¥{{ money(ev.profit_change) }}
                  </div>
                </div>
              </div>
              <div v-if="!timelineModal.data.timeline?.length" class="tl-empty">
                暂无时间线数据
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 确认弹窗 -->
    <ConfirmDialog
      :visible="confirmState.visible"
      :title="confirmState.title"
      :message="confirmState.message"
      confirm-text="确认"
      :loading="false"
      @confirm="() => { confirmState.visible = false; confirmState.onConfirm?.() }"
      @cancel="confirmState.visible = false"
    />
  </div>
</template>

<style scoped>
.decisions-page {
  padding: var(--space-6);
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.page-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: var(--space-4);
}
.page-title {
  margin: 0;
  font-size: inherit;
  color: var(--color-text-primary);
}
.page-desc {
  margin: 4px 0 0;
  color: var(--color-text-secondary);
  font-size: 0.85rem;
}

/* 统计条 */
.stats-strip {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-bg-card);
  overflow: hidden;
}
.stat-cell {
  padding: var(--space-3) var(--space-4);
  border-right: 1px solid var(--color-border-light);
  display: flex;
  flex-direction: column;
  gap: 4px;
  text-align: center;
}
.stat-cell:last-child { border-right: 0; }
.stat-cell span {
  font-size: inherit;
  color: var(--color-text-muted);
}
.stat-cell strong {
  font-size: 1.15rem;
  color: var(--color-text-primary);
  font-variant-numeric: tabular-nums;
}
.stat-cell.win strong { color: var(--color-success); }

/* AI 建议候选 */
.candidate-panel {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-bg-card);
  padding: var(--space-4);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.candidate-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-4);
}
.candidate-head h3 {
  margin: 0;
  font-size: 0.95rem;
  color: var(--color-text-primary);
}
.candidate-head p {
  margin: 4px 0 0;
  color: var(--color-text-secondary);
  font-size: 0.8rem;
  line-height: 1.45;
}
.candidate-count {
  flex-shrink: 0;
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-sm);
  padding: 3px 8px;
  color: var(--color-text-muted);
  font-size: inherit;
  background: var(--color-bg-input);
}
.candidate-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.candidate-item {
  border-top: 1px solid var(--color-border-light);
  padding-top: var(--space-3);
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: var(--space-3);
  align-items: center;
}
.candidate-main {
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-width: 0;
}
.candidate-topline {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}
.candidate-topline strong {
  font-size: 0.9rem;
  color: var(--color-text-primary);
  overflow-wrap: anywhere;
}
.candidate-main p {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: 0.8rem;
  line-height: 1.45;
}
.candidate-meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  color: var(--color-text-muted);
  font-size: 0.74rem;
}
.candidate-meta span {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.candidate-actions {
  display: flex;
  gap: 6px;
  justify-content: flex-end;
}
.candidate-empty {
  border-top: 1px solid var(--color-border-light);
  padding-top: var(--space-3);
  display: flex;
  align-items: center;
  gap: var(--space-2);
  color: var(--color-text-muted);
  font-size: 0.8rem;
}

/* 看板布局 */
.kanban {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-4);
  align-items: start;
}
.kanban-col {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.col-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding-bottom: var(--space-2);
  border-bottom: 2px solid var(--color-border);
}
.col-head h3 {
  margin: 0;
  font-size: 0.9rem;
  color: var(--color-text-primary);
}
.col-count {
  background: var(--color-bg-input);
  border: 1px solid var(--color-border-light);
  border-radius: 999px;
  padding: 1px 8px;
  font-size: inherit;
  color: var(--color-text-secondary);
  font-variant-numeric: tabular-nums;
}
.col-empty {
  min-height: 160px;
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-lg);
  color: var(--color-text-muted);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  background: var(--color-bg-card);
  font-size: 0.82rem;
}

/* 决策卡片 */
.decision-card {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: var(--space-4);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  transition: border-color var(--transition-fast), box-shadow var(--transition-fast);
}
.decision-card:hover {
  border-color: var(--color-primary-border-strong);
  box-shadow: var(--shadow-sm);
}
.decision-card.completed {
  opacity: 0.78;
}

.card-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.type-badge {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  padding: 2px 8px;
  font-size: 0.72rem;
  font-weight: 600;
}
.type-add { color: #dc2626; border-color: rgba(220,38,38,0.2); background: rgba(220,38,38,0.06); }
.type-reduce, .type-sell { color: #059669; border-color: rgba(5,150,105,0.2); background: rgba(5,150,105,0.06); }
.type-watch { color: var(--color-text-secondary); }
.type-rebalance { color: #7c3aed; border-color: rgba(124,58,237,0.2); background: rgba(124,58,237,0.06); }
.type-hold { color: var(--color-text-muted); }

.status-tag {
  font-size: 0.7rem;
  font-weight: 600;
  padding: 2px 7px;
  border-radius: var(--radius-sm);
}
.status-proposed { color: #d97706; background: rgba(217,119,6,0.1); }
.status-accepted { color: #2563eb; background: rgba(37,99,235,0.1); }
.status-executed { color: #7c3aed; background: rgba(124,58,237,0.1); }
.status-reviewed { color: #059669; background: rgba(5,150,105,0.1); }
.status-rejected { color: var(--color-text-muted); background: var(--color-bg-input); }
.status-deferred { color: #d97706; background: rgba(217,119,6,0.08); }
.status-expired { color: var(--color-text-muted); background: var(--color-bg-input); }

.card-title {
  margin: 0;
  font-size: 0.95rem;
  color: var(--color-text-primary);
}
.card-summary {
  margin: 0;
  font-size: 0.82rem;
  color: var(--color-text-secondary);
  line-height: 1.5;
}
.card-meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  font-size: 0.74rem;
  color: var(--color-text-muted);
}
.card-meta span {
  display: flex;
  align-items: center;
  gap: 4px;
}

/* 预检查 */
.precheck-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  border-top: 1px solid var(--color-border-light);
  padding-top: var(--space-2);
}
.precheck-item {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 0.76rem;
}
.pc-ok { color: var(--color-success); }
.pc-warn { color: var(--color-warning); }
.pc-error { color: var(--color-danger); }

/* 行动项 */
.action-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  border-top: 1px solid var(--color-border-light);
  padding-top: var(--space-2);
}
.action-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.78rem;
  color: var(--color-text-secondary);
}
.action-item .done {
  text-decoration: line-through;
  color: var(--color-text-muted);
}
.action-btn {
  margin-left: auto;
  font-size: 0.7rem;
  color: var(--color-primary);
  background: none;
  border: none;
  cursor: pointer;
  padding: 2px 6px;
}
.action-btn:hover { text-decoration: underline; }

/* 操作按钮 */
.card-actions {
  display: flex;
  gap: 6px;
  margin-top: var(--space-1);
}
.btn-sm {
  padding: 4px 12px;
  border-radius: var(--radius-sm);
  font-size: 0.76rem;
  font-weight: 600;
  cursor: pointer;
  transition: all var(--transition-fast);
  display: flex;
  align-items: center;
  gap: 4px;
}
.btn-sm.btn-primary {
  background: var(--color-primary);
  color: #fff;
  border: 1px solid var(--color-primary);
}
.btn-sm.btn-primary:hover { background: var(--color-primary-700); }
.btn-sm.btn-ghost {
  background: transparent;
  color: var(--color-text-secondary);
  border: 1px solid var(--color-border);
}
.btn-sm.btn-ghost:hover { background: var(--color-bg-hover); color: var(--color-text-primary); }
.btn-sm.btn-ghost.danger:hover { background: var(--color-danger-bg); color: var(--color-danger); border-color: var(--color-danger); }

/* 复盘结果 */
.review-result {
  border-top: 1px solid var(--color-border-light);
  padding-top: var(--space-2);
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
}
.review-outcome {
  font-size: 0.72rem;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: var(--radius-sm);
}
.outcome-helpful { color: var(--color-success); background: rgba(5,150,105,0.1); }
.outcome-neutral { color: var(--color-text-muted); background: var(--color-bg-input); }
.outcome-unhelpful { color: var(--color-danger); background: rgba(220,38,38,0.1); }
.review-pnl {
  font-size: 0.85rem;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.review-pnl.positive { color: #dc2626; }
.review-pnl.negative { color: #059669; }
.review-lesson {
  flex-basis: 100%;
  margin: 4px 0 0;
  font-size: 0.78rem;
  color: var(--color-text-secondary);
  line-height: 1.5;
  font-style: italic;
}

/* 弹窗 */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  z-index: 100;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-4);
}
.modal-card {
  background: var(--color-bg-card);
  border-radius: var(--radius-lg);
  width: 100%;
  max-width: 480px;
  max-height: 90vh;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
}
.modal-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: var(--space-4);
  border-bottom: 1px solid var(--color-border);
}
.modal-head h3 {
  margin: 0;
  font-size: 1rem;
  color: var(--color-text-primary);
}
.modal-body {
  padding: var(--space-4);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.modal-body label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 0.8rem;
  color: var(--color-text-secondary);
  font-weight: 600;
}
.modal-info {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: var(--space-3);
  background: var(--color-bg-input);
  border-radius: var(--radius-md);
}
.modal-info strong {
  font-size: 0.9rem;
  color: var(--color-text-primary);
}
.modal-info span {
  font-size: 0.78rem;
  color: var(--color-text-secondary);
}
.modal-foot {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  padding: var(--space-4);
  border-top: 1px solid var(--color-border);
}

.icon-btn {
  width: 30px;
  height: 30px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  color: var(--color-text-secondary);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  background: none;
}
.icon-btn:hover { background: var(--color-bg-hover); color: var(--color-text-primary); }

/* 时间线链接 */
.timeline-link {
  background: none;
  border: none;
  color: var(--color-text-muted);
  font-size: 0.75rem;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 0;
  transition: color 0.15s;
}
.timeline-link:hover { color: var(--color-primary); }

/* 时间线弹窗 */
.modal-wide { max-width: 560px; }
.tl-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  padding: var(--space-8) 0;
  color: var(--color-text-muted);
}
.tl-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: var(--space-4);
}
.tl-header strong {
  font-size: 0.95rem;
  color: var(--color-text-primary);
}
.tl-list {
  position: relative;
  padding-left: 28px;
}
.tl-list::before {
  content: '';
  position: absolute;
  left: 11px;
  top: 8px;
  bottom: 8px;
  width: 2px;
  background: var(--color-border);
}
.tl-item {
  position: relative;
  padding-bottom: var(--space-4);
}
.tl-item:last-child { padding-bottom: 0; }
.tl-dot {
  position: absolute;
  left: -28px;
  top: 2px;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  background: var(--color-bg-card);
  border: 2px solid var(--color-border);
  z-index: 1;
}
.tl-item.tl-created .tl-dot { border-color: var(--color-primary); background: var(--color-primary-light, #eef2ff); }
.tl-item.tl-status .tl-dot { border-color: var(--color-success); background: #ecfdf5; }
.tl-item.tl-action .tl-dot { border-color: var(--color-warning, #f59e0b); background: #fffbeb; }
.tl-item.tl-review .tl-dot { border-color: #8b5cf6; background: #f5f3ff; }
.tl-content {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.tl-label {
  font-size: 0.88rem;
  font-weight: 500;
  color: var(--color-text-primary);
}
.tl-time {
  font-size: inherit;
  color: var(--color-text-muted);
}
.tl-detail {
  font-size: 0.8rem;
  color: var(--color-text-secondary);
  margin-top: 2px;
  line-height: 1.5;
}
.tl-lesson {
  font-size: 0.8rem;
  color: #8b5cf6;
  margin-top: 2px;
  display: flex;
  align-items: center;
  gap: 4px;
}
.tl-pnl {
  font-size: 0.82rem;
  font-weight: 600;
  margin-top: 2px;
}
.tl-pnl.positive { color: var(--color-success); }
.tl-pnl.negative { color: var(--color-danger, #ef4444); }
.tl-empty {
  text-align: center;
  color: var(--color-text-muted);
  padding: var(--space-6) 0;
  font-size: 0.85rem;
}

/* 响应式 */
/* 移动端 Tab */
.mobile-tabs {
  display: none;
  gap: 4px;
  background: var(--color-bg-input);
  border-radius: var(--radius-lg);
  padding: 4px;
}
.mobile-tab {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 8px 12px;
  border: none;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--color-text-secondary);
  font-size: 0.82rem;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s;
}
.mobile-tab.active {
  background: var(--color-bg-card);
  color: var(--color-primary);
  box-shadow: var(--shadow-sm);
}
.tab-count {
  font-size: 0.7rem;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border-light);
  border-radius: 999px;
  padding: 1px 6px;
  font-variant-numeric: tabular-nums;
}
.mobile-tab.active .tab-count {
  background: var(--color-primary-bg);
  border-color: var(--color-primary-border);
  color: var(--color-primary);
}
.mobile-hidden {
  display: none !important;
}

@media (max-width: 1100px) {
  .kanban { grid-template-columns: 1fr; }
  .mobile-tabs { display: flex; }
  .stats-strip { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .stat-cell:nth-child(3) { border-right: 0; }
  .stat-cell:nth-child(-n + 3) { border-bottom: 1px solid var(--color-border-light); }
}
@media (max-width: 640px) {
  .decisions-page { padding: var(--space-4); }
  .stats-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .stat-cell:nth-child(2) { border-right: 0; }
  .stat-cell:nth-child(-n + 4) { border-bottom: 1px solid var(--color-border-light); }
  .mobile-tabs { flex-wrap: nowrap; }
  .candidate-head {
    flex-direction: column;
    gap: var(--space-2);
  }
  .candidate-item {
    grid-template-columns: 1fr;
    align-items: stretch;
  }
  .candidate-actions {
    justify-content: stretch;
  }
  .candidate-actions .btn-sm {
    flex: 1;
    justify-content: center;
  }
}

/* ── 移动端响应式 (<768px) ── */
@media (max-width: 768px) {
  .decisions-page {
    padding: var(--space-3);
    padding-bottom: 80px; /* 为底部固定栏留空间 */
  }

  /* 页头 */
  .page-head {
    flex-direction: column;
    gap: var(--space-2);
  }
  .page-title { font-size: 1.1rem; }
  .page-desc { font-size: 0.78rem; }

  /* 统计条: 3列2行 */
  .stats-strip {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
  .stat-cell:nth-child(3) { border-right: 0; }
  .stat-cell:nth-child(-n + 3) { border-bottom: 1px solid var(--color-border-light); }
  .stat-cell {
    padding: var(--space-2) var(--space-3);
  }
  .stat-cell strong { font-size: 1rem; }

  /* 看板：单列全宽 */
  .kanban {
    grid-template-columns: 1fr;
  }

  /* 决策卡片全宽 */
  .decision-card {
    width: 100%;
  }

  /* 卡片操作按钮 */
  .card-actions {
    flex-wrap: wrap;
  }
  .btn-sm {
    flex: 1;
    min-width: 0;
    justify-content: center;
    padding: 8px 12px;
    font-size: 0.8rem;
  }

  /* 复盘弹窗 */
  .modal-overlay {
    padding: var(--space-2);
    align-items: flex-end;
  }
  .modal-card {
    max-height: 85vh;
    border-radius: var(--radius-lg) var(--radius-lg) 0 0;
  }
}
</style>
