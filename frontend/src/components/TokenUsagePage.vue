<script setup>
import { ref, computed, onMounted, onUnmounted, onActivated, onDeactivated, watch } from 'vue'
import {
  getTokenUsage, getTokenUsageRecent, getTokenUsageSummary, getTokenUsageByCaller,
  getTokenUsageDaily, getPerformanceStats, getPerformanceByAgent, getRunningAgents,
  clearTokenUsage, getTokenUsageCost, getTokenUsageByModel, getTokenUsageHourly,
  getTokenUsageTrace, getTokenUsageBudget
} from '../api'
import ConfirmDialog from './ConfirmDialog.vue'

// ── 费用计算（A3 修复：与后端 infra/cost_tracker.py 统一定价） ──
const MODEL_PRICING = {
  'deepseek-chat': { prompt: 0.5, completion: 2.0 },
  'deepseek-reasoner': { prompt: 1.0, completion: 4.0 },
  'deepseek-v4-flash': { prompt: 0.5, completion: 2.0 },
  'deepseek-v4-pro': { prompt: 1.0, completion: 4.0 },
  'mimo': { prompt: 0.3, completion: 1.2 },
  'mimo-v2.5-pro': { prompt: 0.3, completion: 1.2 },
  'mimo-v2.5': { prompt: 0.3, completion: 1.2 },
  'ollama': { prompt: 0.0, completion: 0.0 },
  'qwen3-vl': { prompt: 0.0, completion: 0.0 },
}

function calcCost(model, promptTokens, completionTokens) {
  // A3 修复：模糊匹配模型名，与后端 get_cost_estimate 逻辑一致
  const modelLower = (model || '').toLowerCase()
  let p = null
  for (const key in MODEL_PRICING) {
    if (modelLower.includes(key)) {
      p = MODEL_PRICING[key]
      break
    }
  }
  if (!p) p = { prompt: 0.5, completion: 2.0 }
  return (promptTokens / 1000000 * p.prompt) + (completionTokens / 1000000 * p.completion)
}

function formatCost(cost) {
  if (cost < 0.01) return `¥${cost.toFixed(4)}`
  return `¥${cost.toFixed(2)}`
}

// ── Tab 管理 ──
const activeTab = ref('overview')
const tabLoaded = ref({ overview: false, details: false, perf: false, trace: false })

// ── 数据状态 ──
const loading = ref(true)
const summary = ref({ today: {}, total_calls: 0, total_tokens: 0, avg_per_call: 0 })
const daily = ref([])
const byCaller = ref([])
const byModel = ref([])
const hourly = ref([])
const costData = ref({ total_cost: 0, by_model: [] })
const budgetData = ref({ used: 0, limit: 500000, remaining: 500000, percentage: 0 })
const records = ref([])
const page = ref(1)
const pageSize = 15
const total = ref(0)
const totalPages = computed(() => Math.ceil(total.value / pageSize) || 1)
const perfStats = ref({ total_runs: 0, avg_duration_ms: 0, max_duration_ms: 0, slow_calls: 0, unique_agents: 0 })
const perfByAgent = ref([])
const confirm = ref({ visible: false, title: '', message: '', danger: false, onConfirm: null })

// ── 调用明细筛选 ──
const detailDays = ref(7)
const detailCaller = ref('')
const detailModel = ref('')

// ── B2：全局时间范围选择器 ──
const globalDays = ref(7)

// ── Trace 查询 ──
const traceInput = ref('')
const traceResult = ref(null)
const traceLoading = ref(false)

// ── 运行中的 Agent ──
const runningAgents = ref([])
let runningTimer = null

// ── Label 映射 ──
const LABEL_MAP = {
  orchestrator: 'Orchestrator 主控',
  clarify: '需求澄清',
  article_analysis: '文章分析',
  agent_chat: 'Agent 对话',
  agent_tools: 'Agent 工具调用',
  chat: '自由问答',
  market_analysis: '市场日报分析师',
  diversification_analysis: '分散度分析师',
  portfolio_analysis: '持仓 AI 分析',
  daily_report: '每日简报',
  hotspots_analysis: '热点分析专家',
  bond_recommend: '债券配置顾问',
  panorama_analysis: '全景诊断分析师',
  trade_review: '交易复盘分析师',
  deep_dive: '基金深度分析师',
  what_if: '情景推演分析师',
  index_deep_analysis: '指数深度分析师',
}

const AGENT_NAME_MAP = {
  valuation_expert: '估值专家',
  market_analyst: '择时分析师',
  risk_assessor: '风险评估师',
  allocation_advisor: '资产配置师',
  fund_analyst: '基金分析师',
}

function callerLabel(c) {
  if (!c) return '旧记录（无标注）'
  if (c.startsWith('specialist:')) {
    const key = c.slice('specialist:'.length)
    return AGENT_NAME_MAP[key] || c
  }
  return LABEL_MAP[c] || c
}

// ── 模型颜色 ──
const MODEL_COLORS = [
  '#3b82f6', '#8b5cf6', '#10b981', '#f59e0b', '#ef4444', '#06b6d4', '#ec4899', '#84cc16'
]

function modelColor(idx) {
  return MODEL_COLORS[idx % MODEL_COLORS.length]
}

// ── 运行中的 Agent ──
async function loadRunningAgents() {
  try {
    const { data } = await getRunningAgents()
    runningAgents.value = data.agents || []
  } catch (e) { /* ignore */ }
}

function startPolling() {
  loadRunningAgents()
  runningTimer = setInterval(loadRunningAgents, 3000)
}

function stopPolling() {
  if (runningTimer) {
    clearInterval(runningTimer)
    runningTimer = null
  }
}

// ── 数据加载 ──
async function loadOverviewData() {
  loading.value = true
  try {
    // B2 修复：各板块统一使用 globalDays 时间窗口
    const d = globalDays.value
    const [r1, r2, r3, r4, r5, r6, r7, r8] = await Promise.all([
      getTokenUsageSummary(d),
      getTokenUsageDaily(d),
      getTokenUsageByCaller(d),
      getTokenUsageByModel(d),
      getTokenUsageCost(d),
      getTokenUsageHourly(),
      getTokenUsageBudget(),
      getPerformanceStats(d),
    ])
    summary.value = r1.data
    daily.value = r2.data.items || []
    byCaller.value = r3.data.items || []
    byModel.value = r4.data.items || []
    costData.value = r5.data
    hourly.value = r6.data.items || []
    budgetData.value = r7.data
    perfStats.value = r8.data
  } catch (e) {
    console.error('Failed to load overview data:', e)
  } finally {
    loading.value = false
  }
}

// B2：全局时间范围切换时重新加载所有数据
function loadAllData() {
  loadOverviewData()
  if (tabLoaded.value.details) {
    detailDays.value = globalDays.value
    page.value = 1
    loadRecords()
  }
  if (tabLoaded.value.perf) loadPerfData()
}

async function loadRecords() {
  try {
    // B1 修复：传入 caller/model 筛选参数，让下拉框真正生效
    const { data } = await getTokenUsageRecent(page.value, pageSize, detailDays.value, detailCaller.value, detailModel.value)
    records.value = data.records || []
    total.value = data.total || 0
  } catch (e) {
    console.error('Failed to load records:', e)
  }
}

async function loadPerfData() {
  try {
    const [r1, r2] = await Promise.all([
      getPerformanceStats(7),
      getPerformanceByAgent(7),
    ])
    perfStats.value = r1.data
    perfByAgent.value = r2.data.items || []
  } catch (e) {
    console.error('Failed to load perf data:', e)
  }
}

function goPage(p) {
  if (p < 1 || p > totalPages.value) return
  page.value = p
  loadRecords()
}

function switchTab(tab) {
  activeTab.value = tab
  if (tab === 'overview' && !tabLoaded.value.overview) {
    loadOverviewData()
    tabLoaded.value.overview = true
  } else if (tab === 'details' && !tabLoaded.value.details) {
    loadRecords()
    tabLoaded.value.details = true
  } else if (tab === 'perf' && !tabLoaded.value.perf) {
    loadPerfData()
    tabLoaded.value.perf = true
  }
}

// ── Trace 查询 ──
async function searchTrace() {
  if (!traceInput.value.trim()) return
  traceLoading.value = true
  traceResult.value = null
  try {
    const { data } = await getTokenUsageTrace(traceInput.value.trim())
    // 后端返回聚合结构：{ trace_id, token_usage, tool_audit_logs, agent_runs, rag_logs }
    traceResult.value = data || null
  } catch (e) {
    console.error('Failed to load trace:', e)
    traceResult.value = null
  } finally {
    traceLoading.value = false
  }
}

// ── 从明细表点击 Trace 跳转 ──
function jumpToTrace(traceId) {
  if (!traceId) return
  activeTab.value = 'trace'
  traceInput.value = traceId
  searchTrace()
}

// ── 计算属性 ──
const maxDailyTokens = computed(() => {
  let m = 0
  for (const d of daily.value) {
    const t = (d.prompt_tokens || 0) + (d.completion_tokens || 0) || d.tokens || 0
    if (t > m) m = t
  }
  return m || 1
})

const maxCallerTokens = computed(() => {
  let m = 0
  for (const c of byCaller.value) if (c.total_tokens > m) m = c.total_tokens
  return m || 1
})

const maxModelTokens = computed(() => {
  let m = 0
  for (const m2 of byModel.value) if (m2.total_tokens > m) m = m2.total_tokens
  return m || 1
})

const budgetPercentage = computed(() => {
  return budgetData.value.percentage || (budgetData.value.used / (budgetData.value.limit || 500000) * 100) || 0
})

const budgetColor = computed(() => {
  const pct = budgetPercentage.value
  if (pct >= 100) return 'var(--color-danger)'
  if (pct >= 80) return 'var(--color-warning)'
  return 'var(--color-primary-500)'
})

const budgetOverLimit = computed(() => budgetPercentage.value >= 100)

// 小时热力图数据 (24 格)
const hourlyMap = computed(() => {
  const map = new Array(24).fill(null).map((_, i) => ({ hour: i, calls: 0, tokens: 0 }))
  for (const h of hourly.value) {
    const idx = parseInt(h.hour)
    if (idx >= 0 && idx < 24) {
      map[idx] = { hour: idx, calls: h.calls, tokens: h.tokens }
    }
  }
  return map
})

function hourlyColor(tokens) {
  if (!tokens || tokens === 0) return 'var(--color-border)'
  if (tokens < 5000) return 'rgba(59, 130, 246, 0.25)'
  if (tokens < 20000) return 'rgba(59, 130, 246, 0.55)'
  return 'rgba(59, 130, 246, 0.85)'
}

function hourlyLabel(tokens) {
  if (!tokens || tokens === 0) return '0'
  if (tokens < 1000) return tokens.toString()
  return (tokens / 1000).toFixed(1) + 'K'
}

// 今日费用
const todayCost = computed(() => {
  let cost = 0
  for (const r of records.value) {
    cost += calcCost(r.model, r.prompt_tokens || 0, r.completion_tokens || 0)
  }
  return cost
})

// 本周费用
const weekCost = computed(() => costData.value.total_cost || 0)

// 本月费用估算（按周费用 * 4.3）
const monthCost = computed(() => (costData.value.total_cost || 0) * 4.3)

// Trace 汇总（聚合 token_usage + tool_audit_logs + agent_runs + rag_logs）
const traceSummary = computed(() => {
  if (!traceResult.value) return null
  const t = traceResult.value
  const tokenList = t.token_usage || []
  let totalTokens = 0
  let totalCost = 0
  for (const r of tokenList) {
    totalTokens += r.total_tokens || 0
    totalCost += calcCost(r.model, r.prompt_tokens || 0, r.completion_tokens || 0)
  }
  // 耗时：取所有事件的首末时间戳
  const allTimes = []
  for (const r of tokenList) if (r.created_at) allTimes.push(r.created_at)
  for (const r of t.tool_audit_logs || []) if (r.created_at) allTimes.push(r.created_at)
  for (const r of t.agent_runs || []) if (r.created_at) allTimes.push(r.created_at)
  for (const r of t.rag_logs || []) if (r.created_at) allTimes.push(r.created_at)
  let duration = 0
  if (allTimes.length >= 2) {
    const ts = allTimes.map(s => new Date(s.replace(' ', 'T')).getTime()).sort((a, b) => a - b)
    duration = ts[ts.length - 1] - ts[0]
  }
  return {
    token_calls: tokenList.length,
    tool_calls: (t.tool_audit_logs || []).length,
    agent_runs: (t.agent_runs || []).length,
    rag_calls: (t.rag_logs || []).length,
    tokens: totalTokens,
    cost: totalCost,
    duration,
  }
})

// 统一事件流：把 4 类记录合并成按时间排序的时间线
const traceEvents = computed(() => {
  if (!traceResult.value) return []
  const t = traceResult.value
  const events = []
  for (const r of t.token_usage || []) {
    events.push({ kind: 'llm', time: r.created_at, data: r })
  }
  for (const r of t.tool_audit_logs || []) {
    events.push({ kind: 'tool', time: r.created_at, data: r })
  }
  for (const r of t.agent_runs || []) {
    events.push({ kind: 'agent', time: r.created_at, data: r })
  }
  for (const r of t.rag_logs || []) {
    events.push({ kind: 'rag', time: r.created_at, data: r })
  }
  return events.sort((a, b) => {
    const ta = a.time ? new Date(a.time.replace(' ', 'T')).getTime() : 0
    const tb = b.time ? new Date(b.time.replace(' ', 'T')).getTime() : 0
    return ta - tb
  })
})

const EVENT_LABELS = {
  llm: 'LLM',
  tool: '工具',
  agent: 'Agent',
  rag: 'RAG',
}

// ── 分页 ──
function pages() {
  const pgs = []
  const tp = totalPages.value
  const cur = page.value
  const start = Math.max(1, cur - 2)
  const end = Math.min(tp, cur + 2)
  if (start > 1) pgs.push(1)
  if (start > 2) pgs.push('...')
  for (let i = start; i <= end; i++) pgs.push(i)
  if (end < tp - 1) pgs.push('...')
  if (end < tp) pgs.push(tp)
  return pgs
}

// ── 格式化函数 ──
function formatTime(ts) {
  if (!ts) return ''
  const d = new Date(ts.replace(' ', 'T'))
  const h = String(d.getHours()).padStart(2, '0')
  const m = String(d.getMinutes()).padStart(2, '0')
  return `${h}:${m}`
}

function formatDateTime(ts) {
  if (!ts) return ''
  const d = new Date(ts.replace(' ', 'T'))
  const M = d.getMonth() + 1
  const D = d.getDate()
  const h = String(d.getHours()).padStart(2, '0')
  const m = String(d.getMinutes()).padStart(2, '0')
  return `${M}/${D} ${h}:${m}`
}

function formatDuration(ms) {
  if (!ms) return '0ms'
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  return `${Math.floor(ms / 60000)}m${Math.floor((ms % 60000) / 1000)}s`
}

function formatDate(ts) {
  if (!ts) return ''
  const d = new Date(ts.replace(' ', 'T'))
  const M = d.getMonth() + 1
  const D = d.getDate()
  return `${M}/${D}`
}

function shortTrace(id) {
  if (!id) return ''
  return id.length > 12 ? id.slice(0, 8) + '…' : id
}

// ── 清空数据 ──
function confirmClearTokenUsage() {
  confirm.value = {
    visible: true,
    title: '清空 Token 用量数据',
    message: '确定要清空所有 Token 用量记录吗？此操作不可撤销。',
    danger: true,
    onConfirm: async () => {
      confirm.value.visible = false
      try {
        await clearTokenUsage()
        tabLoaded.value = { overview: false, details: false, perf: false, trace: false }
        loadOverviewData()
      } catch (e) {
        console.error('Failed to clear token usage:', e)
      }
    }
  }
}

// ── 生命周期 ──
onMounted(() => {
  loadOverviewData()
  startPolling()
})

// KeepAlive 切换页面时停止轮询，切回时恢复（避免切走后仍每 3s 请求）
onDeactivated(stopPolling)
onActivated(() => {
  loadRunningAgents()
  if (!runningTimer) startPolling()
})
onUnmounted(stopPolling)

// 监听明细筛选变化（B1 修复：增加 caller/model 监听）
watch([detailDays, detailCaller, detailModel], () => {
  page.value = 1
  loadRecords()
})

// ── B3：specialist 调用归组展示 ──
// 同一 trace_id + 同一专家（去掉 #turnN/#summary 后缀）的多轮调用归组为一行
const groupedRecords = computed(() => {
  const groups = new Map()
  for (const r of records.value) {
    const isMultiTurn = /#turn\d+|#summary/.test(r.caller || '')
    if (!isMultiTurn) {
      groups.set(`single-${r.id}`, { ...r, is_group: false })
      continue
    }
    const baseCaller = (r.caller || '').replace(/#turn\d+/, '').replace(/#summary/, '')
    const traceKey = r.trace_id || 'no-trace'
    const key = `${traceKey}|${baseCaller}`
    if (!groups.has(key)) {
      groups.set(key, {
        ...r,
        caller: baseCaller,
        is_group: true,
        turn_count: 0,
        total_tokens_sum: 0,
        prompt_tokens_sum: 0,
        completion_tokens_sum: 0,
        turns: [],
        first_time: r.created_at,
      })
    }
    const g = groups.get(key)
    g.turn_count += 1
    g.total_tokens_sum += r.total_tokens || 0
    g.prompt_tokens_sum += r.prompt_tokens || 0
    g.completion_tokens_sum += r.completion_tokens || 0
    g.turns.push({
      turn: (r.caller || '').match(/#turn(\d+)/)?.[1] || 's',
      tokens: r.total_tokens || 0,
      time: r.created_at,
    })
  }
  return Array.from(groups.values())
})

function expandGroup(group) {
  if (!group.is_group) return
  // 切换展开状态（用 _expanded 标记）
  group._expanded = !group._expanded
}
</script>

<template>
  <div class="token-usage-page bg-mesh">
    <!-- 页面标题 -->
    <div class="page-header">
      <h2 class="page-title editorial-title-lg">Token 用量</h2>
      <span class="page-desc editorial-subtitle">LLM 调用消耗统计</span>
      <!-- B2：全局时间范围选择器，所有板块统一引用 -->
      <div class="global-range">
        <span class="global-range-label">统计范围</span>
        <select v-model="globalDays" @change="loadAllData" class="global-range-select">
          <option :value="1">今日</option>
          <option :value="7">近 7 天</option>
          <option :value="30">近 30 天</option>
          <option :value="90">近 90 天</option>
        </select>
      </div>
      <div style="display: flex; gap: 0.5rem; margin-left: auto;">
        <button class="btn-outline btn-sm" @click="loadOverviewData" :disabled="loading">
          <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
          刷新
        </button>
        <button class="btn-outline btn-sm btn-danger-text" @click="confirmClearTokenUsage">
          清空数据
        </button>
      </div>
    </div>

    <ConfirmDialog
      :visible="confirm.visible"
      :title="confirm.title"
      :message="confirm.message"
      :danger="confirm.danger"
      @confirm="confirm.onConfirm"
      @cancel="confirm.visible = false"
    />

    <!-- 运行中的 Agent -->
    <div v-if="runningAgents.length > 0" class="running-agents-bar">
      <div class="running-agents-title">
        <span class="running-pulse"></span>
        运行中的 Agent ({{ runningAgents.length }})
      </div>
      <div class="running-agents-list">
        <div v-for="a in runningAgents" :key="a.id" class="running-agent-item">
          <span class="running-agent-name">{{ a.agent }}</span>
          <span class="running-agent-task">{{ a.task }}</span>
          <span class="running-agent-time font-jet">{{ a.elapsed_s }}s</span>
        </div>
      </div>
    </div>

    <!-- 预算进度条 + 费用概览 -->
    <div class="budget-section editorial-card">
      <div class="budget-header">
        <span class="budget-title terminal-label">今日 Token 预算</span>
        <span class="budget-amount font-jet">{{ (budgetData.used || 0).toLocaleString() }} / {{ (budgetData.limit || 500000).toLocaleString() }}</span>
        <span class="budget-pct font-jet-lg" :style="{ color: budgetColor }">{{ budgetPercentage.toFixed(1) }}%</span>
      </div>
      <div class="budget-bar-bg">
        <div
          class="budget-bar-fill"
          :class="{ 'pulse-warn': budgetOverLimit }"
          :style="{
            width: Math.min(budgetPercentage, 100) + '%',
            background: budgetColor
          }"
        ></div>
        <div class="budget-warn-line" :style="{ left: '80%' }" title="预警线 80%"></div>
      </div>
      <div class="budget-footer">
        <span class="budget-hint terminal-label">
          <template v-if="budgetPercentage >= 100">已超出今日限额</template>
          <template v-else-if="budgetPercentage >= 80">接近限额，请注意用量</template>
          <template v-else>正常 · 预警线 80% ({{ ((budgetData.limit || 500000) * 0.8).toLocaleString() }})</template>
        </span>
        <div class="cost-row">
          <span class="cost-item terminal-label">今日 <strong class="font-jet">{{ formatCost(todayCost) }}</strong></span>
          <span class="cost-item terminal-label">本周(7d) <strong class="font-jet">{{ formatCost(weekCost) }}</strong></span>
          <span class="cost-item terminal-label">本月(估) <strong class="font-jet">{{ formatCost(monthCost) }}</strong></span>
        </div>
      </div>
    </div>

    <!-- Tab 切换 -->
    <div class="tab-bar">
      <button :class="['tab-btn', { active: activeTab === 'overview' }]" @click="switchTab('overview')">
        用量总览
      </button>
      <button :class="['tab-btn', { active: activeTab === 'details' }]" @click="switchTab('details')">
        调用明细
      </button>
      <button :class="['tab-btn', { active: activeTab === 'perf' }]" @click="switchTab('perf')">
        Agent 性能
      </button>
      <button :class="['tab-btn', { active: activeTab === 'trace' }]" @click="switchTab('trace')">
        Trace 链路
      </button>
    </div>

    <!-- ═══════════════════════════════════════ -->
    <!-- Tab 1: 用量总览 -->
    <!-- ═══════════════════════════════════════ -->
    <template v-if="activeTab === 'overview'">
      <div v-if="loading" class="loading-state">
        <div class="spinner-lg"></div>
        <span>加载中...</span>
      </div>

      <template v-else>
        <!-- 汇总卡片 -->
        <div class="summary-row">
          <div class="summary-card editorial-card reveal-stagger">
            <div class="summary-label terminal-label">今日消耗</div>
            <div class="summary-value font-jet-lg">{{ (summary.today?.total || 0).toLocaleString() }}</div>
            <div class="summary-sub terminal-label">tokens</div>
          </div>
          <div class="summary-card editorial-card reveal-stagger">
            <div class="summary-label terminal-label">今日调用</div>
            <div class="summary-value font-jet-lg">{{ summary.today?.calls || 0 }}</div>
            <div class="summary-sub terminal-label">次 LLM 调用</div>
          </div>
          <div class="summary-card editorial-card reveal-stagger">
            <div class="summary-label terminal-label">平均每次</div>
            <div class="summary-value font-jet-lg">{{ summary.avg_per_call || 0 }}</div>
            <div class="summary-sub terminal-label">tokens / call</div>
          </div>
          <div class="summary-card editorial-card reveal-stagger">
            <div class="summary-label terminal-label">近 30 天累计</div>
            <div class="summary-value font-jet-lg">{{ (summary.total_tokens || 0).toLocaleString() }}</div>
            <div class="summary-sub terminal-label">tokens</div>
          </div>
          <div class="summary-card editorial-card reveal-stagger">
            <div class="summary-label terminal-label">近 7 天费用</div>
            <div class="summary-value font-jet-lg num-gold">{{ formatCost(costData.total_cost || 0) }}</div>
            <div class="summary-sub terminal-label">CNY</div>
          </div>
        </div>

        <!-- 近 30 天双色堆叠柱状图 -->
        <div class="card chart-card editorial-card">
          <div class="chart-header editorial-card-header">
            <h4 class="chart-title title">近 30 天趋势</h4>
            <div class="chart-legend meta">
              <span class="legend-item"><span class="legend-dot" style="background: var(--color-primary-600)"></span> Prompt tokens</span>
              <span class="legend-item"><span class="legend-dot" style="background: rgba(59, 130, 246, 0.4)"></span> Completion tokens</span>
            </div>
          </div>
          <div v-if="daily.length === 0" class="chart-empty">暂无数据</div>
          <div v-else class="bar-chart">
            <div
              v-for="d in daily"
              :key="d.day"
              class="bar-item"
              :title="`${d.day}\nPrompt: ${((d.prompt_tokens || d.tokens || 0)).toLocaleString()}\nCompletion: ${(d.completion_tokens || 0).toLocaleString()}\n总计: ${((d.prompt_tokens || 0) + (d.completion_tokens || 0) || d.tokens || 0).toLocaleString()}\n调用: ${d.calls} 次`"
            >
              <div class="bar-stack">
                <div
                  class="bar-segment bar-completion"
                  :style="{ height: ((d.completion_tokens || 0) / maxDailyTokens * 100) + '%' }"
                ></div>
                <div
                  class="bar-segment bar-prompt"
                  :style="{ height: ((d.prompt_tokens || d.tokens || 0) / maxDailyTokens * 100) + '%' }"
                ></div>
              </div>
              <div class="bar-label font-jet">{{ formatDate(d.day) }}</div>
            </div>
          </div>
        </div>

        <!-- 两列：按调用方 + 按模型 -->
        <div class="charts-row">
          <!-- 按调用方 -->
          <div class="card chart-card editorial-card">
            <div class="editorial-card-header">
              <h4 class="chart-title title">按调用方分布</h4>
              <span class="meta">近 7 天</span>
            </div>
            <div v-if="byCaller.length === 0" class="chart-empty">暂无数据</div>
            <div v-else class="caller-list">
              <div v-for="item in byCaller" :key="item.caller" class="caller-item reveal-stagger">
                <div class="caller-row">
                  <span class="caller-name">{{ callerLabel(item.caller) }}</span>
                  <span class="caller-tokens font-jet">{{ (item.total_tokens || 0).toLocaleString() }}</span>
                </div>
                <div class="caller-bar-bg">
                  <div class="caller-bar-fill" :style="{ width: (item.total_tokens / maxCallerTokens * 100) + '%' }"></div>
                </div>
                <div class="caller-sub terminal-label">{{ item.calls }} 次调用</div>
              </div>
            </div>
          </div>

          <!-- 按模型分布 -->
          <div class="card chart-card editorial-card">
            <div class="editorial-card-header">
              <h4 class="chart-title title">按模型分布</h4>
              <span class="meta">近 7 天</span>
            </div>
            <div v-if="byModel.length === 0" class="chart-empty">暂无数据</div>
            <div v-else class="model-list">
              <div v-for="(item, idx) in byModel" :key="item.model" class="model-item reveal-stagger">
                <div class="model-row">
                  <span class="model-name" :style="{ color: modelColor(idx) }">● {{ item.model }}</span>
                  <span class="model-tokens font-jet">{{ (item.total_tokens || 0).toLocaleString() }}</span>
                </div>
                <div class="model-bar-bg">
                  <div class="model-bar-fill" :style="{ width: (item.total_tokens / maxModelTokens * 100) + '%', background: modelColor(idx) }"></div>
                </div>
                <div class="model-sub">
                  <span class="terminal-label">{{ item.calls }} 次调用</span>
                  <span class="model-cost font-jet num-gold">{{ formatCost(calcCost(item.model, item.prompt_tokens || 0, item.completion_tokens || 0)) }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- 今日小时分布热力图 -->
        <div class="card chart-card editorial-card">
          <div class="editorial-card-header">
            <h4 class="chart-title title">今日小时分布</h4>
            <span class="meta">24h</span>
          </div>
          <div v-if="hourly.length === 0" class="chart-empty">暂无数据</div>
          <div v-else class="heatmap-container">
            <div class="heatmap-grid">
              <div
                v-for="h in hourlyMap"
                :key="h.hour"
                class="heatmap-cell"
                :style="{ background: hourlyColor(h.tokens) }"
                :title="`${h.hour}:00 - ${h.hour}:59\n调用: ${h.calls} 次\nToken: ${(h.tokens || 0).toLocaleString()}`"
              >
                <span class="heatmap-hour font-jet">{{ String(h.hour).padStart(2, '0') }}</span>
                <span class="heatmap-val font-jet">{{ hourlyLabel(h.tokens) }}</span>
              </div>
            </div>
            <div class="heatmap-legend">
              <span class="hm-legend-item terminal-label"><span class="hm-dot" style="background: var(--color-border)"></span> 0</span>
              <span class="hm-legend-item terminal-label"><span class="hm-dot" style="background: rgba(59, 130, 246, 0.25)"></span> 1-5K</span>
              <span class="hm-legend-item terminal-label"><span class="hm-dot" style="background: rgba(59, 130, 246, 0.55)"></span> 5-20K</span>
              <span class="hm-legend-item terminal-label"><span class="hm-dot" style="background: rgba(59, 130, 246, 0.85)"></span> &gt;20K</span>
            </div>
          </div>
        </div>
      </template>
    </template>

    <!-- ═══════════════════════════════════════ -->
    <!-- Tab 2: 调用明细 -->
    <!-- ═══════════════════════════════════════ -->
    <template v-if="activeTab === 'details'">
      <div class="card editorial-card">
        <!-- 筛选栏 -->
        <div class="filter-bar">
          <div class="filter-item">
            <label class="filter-label terminal-label">天数</label>
            <select v-model="detailDays" class="filter-select">
              <option :value="1">今天</option>
              <option :value="3">近 3 天</option>
              <option :value="7">近 7 天</option>
              <option :value="14">近 14 天</option>
              <option :value="30">近 30 天</option>
            </select>
          </div>
          <div class="filter-item">
            <label class="filter-label terminal-label">调用方</label>
            <select v-model="detailCaller" class="filter-select">
              <option value="">全部</option>
              <option v-for="c in byCaller" :key="c.caller" :value="c.caller">{{ callerLabel(c.caller) }}</option>
            </select>
          </div>
          <div class="filter-item">
            <label class="filter-label terminal-label">模型</label>
            <select v-model="detailModel" class="filter-select">
              <option value="">全部</option>
              <option v-for="m in byModel" :key="m.model" :value="m.model">{{ m.model }}</option>
            </select>
          </div>
        </div>

        <div class="table-header editorial-card-header">
          <h4 class="chart-title title" style="margin:0;">调用记录</h4>
          <span class="table-total meta">共 <span class="font-jet">{{ total }}</span> 条{{ groupedRecords.length !== records.length ? `（归组展示 ${groupedRecords.length} 行）` : '' }}</span>
        </div>

        <div v-if="records.length === 0" class="chart-empty">暂无数据</div>
        <div v-else class="table-wrap">
          <table class="usage-table">
            <thead>
              <tr>
                <th>时间</th>
                <th>调用方</th>
                <th>模型</th>
                <th class="num">Prompt</th>
                <th class="num">Completion</th>
                <th class="num">总计</th>
                <th class="num">费用</th>
                <th>Trace</th>
              </tr>
            </thead>
            <tbody>
              <template v-for="(r, idx) in groupedRecords" :key="r.id || idx">
                <!-- B3：归组行（specialist 多轮 ReAct） -->
                <tr v-if="r.is_group" class="group-row" @click="expandGroup(r)">
                  <td class="time-cell font-jet">{{ formatDateTime(r.first_time) }}</td>
                  <td>
                    <span class="caller-tag">{{ callerLabel(r.caller || '') }}</span>
                    <span class="turn-badge font-jet">{{ r.turn_count }} 轮</span>
                    <span class="expand-hint">{{ r._expanded ? '▾' : '▸' }}</span>
                  </td>
                  <td class="model-cell">{{ r.model }}</td>
                  <td class="num font-jet">{{ r.prompt_tokens_sum.toLocaleString() }}</td>
                  <td class="num font-jet">{{ r.completion_tokens_sum.toLocaleString() }}</td>
                  <td class="num bold font-jet">{{ r.total_tokens_sum.toLocaleString() }}</td>
                  <td class="num font-jet num-gold">{{ formatCost(calcCost(r.model, r.prompt_tokens_sum, r.completion_tokens_sum)) }}</td>
                  <td>
                    <a v-if="r.trace_id" class="trace-link font-jet" @click.stop="jumpToTrace(r.trace_id)">{{ shortTrace(r.trace_id) }}</a>
                    <span v-else class="trace-none">-</span>
                  </td>
                </tr>
                <!-- 归组展开后的子行 -->
                <template v-if="r.is_group && r._expanded">
                  <tr v-for="(t, tIdx) in r.turns" :key="`${r.id}-${tIdx}`" class="sub-row">
                    <td class="time-cell sub-time font-jet">{{ formatDateTime(t.time) }}</td>
                    <td class="sub-caller">└ turn {{ t.turn }}</td>
                    <td class="model-cell sub-cell">{{ r.model }}</td>
                    <td class="num sub-cell">—</td>
                    <td class="num sub-cell">—</td>
                    <td class="num sub-cell font-jet">{{ t.tokens.toLocaleString() }}</td>
                    <td class="num sub-cell font-jet">{{ formatCost(calcCost(r.model, 0, t.tokens)) }}</td>
                    <td class="sub-cell">—</td>
                  </tr>
                </template>
                <!-- 普通行（非归组） -->
                <tr v-else>
                  <td class="time-cell font-jet">{{ formatDateTime(r.created_at) }}</td>
                  <td><span class="caller-tag">{{ callerLabel(r.caller || '') }}</span></td>
                  <td class="model-cell">{{ r.model }}</td>
                  <td class="num font-jet">{{ (r.prompt_tokens || 0).toLocaleString() }}</td>
                  <td class="num font-jet">{{ (r.completion_tokens || 0).toLocaleString() }}</td>
                  <td class="num bold font-jet">{{ (r.total_tokens || 0).toLocaleString() }}</td>
                  <td class="num font-jet num-gold">{{ formatCost(calcCost(r.model, r.prompt_tokens || 0, r.completion_tokens || 0)) }}</td>
                  <td>
                    <a v-if="r.trace_id" class="trace-link font-jet" @click="jumpToTrace(r.trace_id)">{{ shortTrace(r.trace_id) }}</a>
                    <span v-else class="trace-none">-</span>
                  </td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>

        <!-- 分页 -->
        <div v-if="totalPages > 1" class="pagination">
          <button class="page-btn font-jet" :disabled="page <= 1" @click="goPage(page - 1)">‹</button>
          <template v-for="p in pages()" :key="p">
            <button v-if="p === '...'" class="page-btn page-dots font-jet" disabled>…</button>
            <button v-else :class="['page-btn', 'font-jet', { active: p === page }]" @click="goPage(p)">{{ p }}</button>
          </template>
          <button class="page-btn font-jet" :disabled="page >= totalPages" @click="goPage(page + 1)">›</button>
        </div>
      </div>
    </template>

    <!-- ═══════════════════════════════════════ -->
    <!-- Tab 3: Agent 性能 -->
    <!-- ═══════════════════════════════════════ -->
    <template v-if="activeTab === 'perf'">
      <div class="card perf-section editorial-card">
        <div class="editorial-card-header">
          <h4 class="chart-title title">Agent 性能监控</h4>
          <span class="meta">近 7 天</span>
        </div>

        <!-- 性能概览 -->
        <div class="perf-stats">
          <div class="perf-stat">
            <span class="perf-stat-value font-jet-lg">{{ perfStats.total_runs }}</span>
            <span class="perf-stat-label terminal-label">总调用</span>
          </div>
          <div class="perf-stat">
            <span class="perf-stat-value font-jet-lg">{{ formatDuration(perfStats.avg_duration_ms) }}</span>
            <span class="perf-stat-label terminal-label">平均耗时</span>
          </div>
          <div class="perf-stat">
            <span class="perf-stat-value font-jet-lg">{{ formatDuration(perfStats.max_duration_ms) }}</span>
            <span class="perf-stat-label terminal-label">最慢</span>
          </div>
          <div class="perf-stat">
            <span class="perf-stat-value font-jet-lg" :class="{ 'warn': perfStats.slow_calls > 0 }">{{ perfStats.slow_calls }}</span>
            <span class="perf-stat-label terminal-label">慢调用 (&gt;30s)</span>
          </div>
          <div class="perf-stat">
            <span class="perf-stat-value font-jet-lg">{{ perfStats.unique_agents }}</span>
            <span class="perf-stat-label terminal-label">Agent 数</span>
          </div>
        </div>

        <!-- 按 Agent 分组 -->
        <div class="editorial-card-header" style="margin-top: 1.5rem;">
          <h4 class="chart-title title">按 Agent 分组</h4>
        </div>
        <div v-if="perfByAgent.length === 0" class="chart-empty">暂无数据</div>
        <div v-else class="perf-agent-list">
          <div v-for="a in perfByAgent" :key="a.agent_key" class="perf-agent-item reveal-stagger">
            <div class="perf-agent-row">
              <span class="perf-agent-name">{{ a.agent_name || a.agent_key }}</span>
              <span class="perf-agent-avg font-jet">{{ formatDuration(a.avg_duration_ms) }}</span>
            </div>
            <div class="perf-agent-bar-bg">
              <div class="perf-agent-bar-fill" :style="{ width: Math.min((a.avg_duration_ms / (perfStats.max_duration_ms || 1)) * 100, 100) + '%' }"></div>
            </div>
            <div class="perf-agent-sub">
              <span class="terminal-label">{{ a.runs }} 次调用</span>
              <span v-if="a.slow_calls > 0" class="warn-text">{{ a.slow_calls }} 次慢调用</span>
              <span class="perf-agent-max terminal-label">最慢 <span class="font-jet">{{ formatDuration(a.max_duration_ms) }}</span></span>
            </div>
          </div>
        </div>
      </div>
    </template>

    <!-- ═══════════════════════════════════════ -->
    <!-- Tab 4: Trace 链路 -->
    <!-- ═══════════════════════════════════════ -->
    <template v-if="activeTab === 'trace'">
      <div class="card editorial-card">
        <div class="editorial-card-header">
          <h4 class="chart-title title">Trace 链路查询</h4>
        </div>

        <!-- 输入框 + 查询按钮 -->
        <div class="trace-input-bar">
          <input
            v-model="traceInput"
            type="text"
            class="trace-input"
            placeholder="输入 Trace ID..."
            @keyup.enter="searchTrace"
          />
          <button class="btn-primary btn-sm" @click="searchTrace" :disabled="traceLoading">
            {{ traceLoading ? '查询中...' : '查询' }}
          </button>
        </div>

        <!-- Loading -->
        <div v-if="traceLoading" class="loading-state">
          <div class="spinner-lg"></div>
          <span>查询中...</span>
        </div>

        <!-- 空结果 -->
        <div v-else-if="traceResult === null" class="chart-empty">
          输入 Trace ID 查询完整调用链路
        </div>
        <div v-else-if="traceEvents.length === 0" class="chart-empty">
          未找到该 Trace ID 对应的记录
        </div>

        <!-- Trace 完整链路 -->
        <div v-else class="trace-timeline">
          <div class="trace-header-info">
            <span class="trace-id-label terminal-label">Trace ID</span>
            <span class="trace-id-value font-jet">{{ traceInput }}</span>
          </div>

          <!-- 汇总卡片 -->
          <div v-if="traceSummary" class="trace-summary">
            <span class="trace-sum-item">LLM <strong class="font-jet">{{ traceSummary.token_calls }}</strong></span>
            <span class="trace-sum-item">工具 <strong class="font-jet">{{ traceSummary.tool_calls }}</strong></span>
            <span class="trace-sum-item">Agent <strong class="font-jet">{{ traceSummary.agent_runs }}</strong></span>
            <span class="trace-sum-item">RAG <strong class="font-jet">{{ traceSummary.rag_calls }}</strong></span>
            <span class="trace-sum-item"><strong class="font-jet">{{ traceSummary.tokens.toLocaleString() }}</strong> tokens</span>
            <span class="trace-sum-item font-jet num-gold">{{ formatCost(traceSummary.cost) }}</span>
            <span class="trace-sum-item font-jet" v-if="traceSummary.duration > 0">耗时 {{ formatDuration(traceSummary.duration) }}</span>
          </div>

          <div class="timeline-container">
            <div v-for="(ev, idx) in traceEvents" :key="idx" class="timeline-node">
              <div class="timeline-dot" :class="'dot-' + ev.kind"></div>
              <div v-if="idx < traceEvents.length - 1" class="timeline-line"></div>
              <div class="timeline-content">
                <div class="timeline-time font-jet">{{ formatDateTime(ev.data.created_at) }}</div>
                <!-- LLM 调用 -->
                <div v-if="ev.kind === 'llm'" class="timeline-body">
                  <span class="timeline-caller">{{ callerLabel(ev.data.caller || '') }}</span>
                  <span class="timeline-model">{{ ev.data.model }}</span>
                  <span class="timeline-tokens font-jet">{{ (ev.data.total_tokens || 0).toLocaleString() }} tokens</span>
                  <span class="timeline-cost font-jet num-gold">{{ formatCost(calcCost(ev.data.model, ev.data.prompt_tokens || 0, ev.data.completion_tokens || 0)) }}</span>
                </div>
                <div v-if="ev.kind === 'llm'" class="timeline-detail font-jet">
                  Prompt: {{ (ev.data.prompt_tokens || 0).toLocaleString() }} · Completion: {{ (ev.data.completion_tokens || 0).toLocaleString() }}
                </div>
                <!-- 工具调用 -->
                <div v-else-if="ev.kind === 'tool'" class="timeline-body">
                  <span class="timeline-caller">{{ EVENT_LABELS.tool }} {{ ev.data.tool_name }}</span>
                  <span :class="['timeline-status', ev.data.success ? 'ok' : 'err']">{{ ev.data.success ? '✓ 成功' : '✗ 失败' }}</span>
                  <span class="timeline-tokens font-jet" v-if="ev.data.duration_ms">{{ formatDuration(ev.data.duration_ms) }}</span>
                </div>
                <div v-else-if="ev.kind === 'tool'" class="timeline-detail">
                  <span v-if="ev.data.error_category && ev.data.error_category !== 'none'" class="timeline-error">错误: {{ ev.data.error_category }}</span>
                </div>
                <!-- Agent 执行 -->
                <div v-else-if="ev.kind === 'agent'" class="timeline-body">
                  <span class="timeline-caller">{{ EVENT_LABELS.agent }} {{ ev.data.agent_name }}</span>
                  <span class="timeline-key">{{ ev.data.agent_key }}</span>
                  <span :class="['timeline-status', ev.data.status === 'success' || !ev.data.status ? 'ok' : 'err']">{{ ev.data.status || 'success' }}</span>
                  <span class="timeline-tokens font-jet" v-if="ev.data.duration_ms">{{ formatDuration(ev.data.duration_ms) }}</span>
                </div>
                <div v-else-if="ev.kind === 'agent' && ev.data.query" class="timeline-detail">
                  Query: {{ ev.data.query.slice(0, 100) }}{{ ev.data.query.length > 100 ? '...' : '' }}
                </div>
                <!-- RAG 检索 -->
                <div v-else-if="ev.kind === 'rag'" class="timeline-body">
                  <span class="timeline-caller">{{ EVENT_LABELS.rag }} 检索</span>
                  <span class="timeline-tokens font-jet">{{ ev.data.results_count || 0 }} 条结果</span>
                  <span class="timeline-detail-inline font-jet">FTS: {{ ev.data.fts_count || 0 }} · Chroma: {{ ev.data.chroma_count || 0 }}</span>
                </div>
                <div v-else-if="ev.kind === 'rag' && ev.data.query" class="timeline-detail">
                  Query: {{ ev.data.query.slice(0, 100) }}{{ ev.data.query.length > 100 ? '...' : '' }}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.token-usage-page {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.page-header {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.page-title {
  color: var(--color-text-primary);
  margin: 0;
}

.page-desc {
  font-size: inherit;
  font-weight: inherit;
  color: var(--color-text-muted);
}

/* ── B2：全局时间范围选择器 ── */
.global-range {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  margin-left: 0.5rem;
}

.global-range-label {
  font-size: 0.75rem;
  color: var(--color-text-muted);
}

.global-range-select {
  padding: 0.25rem 0.5rem;
  font-size: 0.8rem;
  border: 1px solid var(--color-border, #d1d5db);
  border-radius: var(--radius-sm, 4px);
  background: var(--color-bg-input, #f9fafb);
  color: var(--color-text-primary);
  cursor: pointer;
}

.global-range-select:focus {
  outline: none;
  border-color: var(--color-primary, #3b82f6);
}

/* ── B3：归组行 + 展开子行 ── */
.group-row {
  cursor: pointer;
  background: var(--color-bg-hover, #f9fafb);
  transition: background 0.15s;
}

.group-row:hover {
  background: var(--color-primary-50, #eff6ff);
}

.turn-badge {
  display: inline-block;
  margin-left: 0.4rem;
  padding: 0.1rem 0.4rem;
  font-size: 0.65rem;
  font-weight: 600;
  color: var(--color-primary, #3b82f6);
  background: var(--color-primary-50, #eff6ff);
  border-radius: 8px;
}

.expand-hint {
  margin-left: 0.3rem;
  font-size: 0.7rem;
  color: var(--color-text-muted);
}

.sub-row {
  background: var(--color-bg-secondary, #f3f4f6);
}

.sub-time {
  padding-left: 1.5rem !important;
  font-size: 0.72rem;
  color: var(--color-text-muted);
}

.sub-caller {
  padding-left: 1.5rem !important;
  font-size: 0.72rem;
  color: var(--color-text-muted);
}

.sub-cell {
  font-size: 0.72rem;
  color: var(--color-text-muted);
}

/* ── 运行中的 Agent ── */
.running-agents-bar {
  background: linear-gradient(135deg, rgba(201, 168, 76, 0.05), rgba(201, 168, 76, 0.02));
  border: 1px solid rgba(201, 168, 76, 0.2);
  border-radius: var(--radius-md);
  padding: 0.75rem 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.running-agents-title {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--color-primary);
}

.running-pulse {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--color-success);
  animation: pulse 1.5s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.5; transform: scale(1.3); }
}

.running-agents-list {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.running-agent-item {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  padding: 0.5rem 0.85rem;
  font-size: 0.78rem;
}

.running-agent-name {
  font-weight: 600;
  color: var(--color-text-primary);
}

.running-agent-task {
  color: var(--color-text-muted);
}

.running-agent-time {
  color: var(--color-primary);
  font-weight: 500;
  font-family: inherit;
}

/* ── 预算进度条 ── */
.budget-section {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 1rem 1.25rem;
}

.budget-header {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 0.5rem;
}

.budget-title {
  font-size: inherit;
  font-weight: inherit;
  color: var(--color-text-primary);
}

.budget-amount {
  font-size: 0.8rem;
  color: var(--color-text-secondary);
  font-variant-numeric: inherit;
  font-family: inherit;
}

.budget-pct {
  font-size: 0.9rem;
  font-weight: inherit;
  margin-left: auto;
}

.budget-bar-bg {
  position: relative;
  height: 24px;
  background: var(--color-bg-input, #f3f4f6);
  border-radius: var(--radius-sm);
  overflow: hidden;
}

.budget-bar-fill {
  height: 100%;
  border-radius: var(--radius-sm);
  transition: width 0.4s ease, background 0.3s ease;
  position: relative;
  z-index: 1;
}

.budget-bar-fill.pulse-warn {
  animation: pulse-danger 1.5s ease-in-out infinite;
}

@keyframes pulse-danger {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.6; }
}

.budget-warn-line {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 2px;
  background: var(--color-warning);
  opacity: 0.4;
  z-index: 2;
}

.budget-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 0.5rem;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.budget-hint {
  font-size: 0.72rem;
  color: var(--color-text-muted);
}

.cost-row {
  display: flex;
  gap: 1rem;
  flex-wrap: wrap;
}

.cost-item {
  font-size: 0.75rem;
  color: var(--color-text-secondary);
}

.cost-item strong {
  color: var(--color-text-primary);
  font-weight: 600;
}

/* ── Tab 切换 ── */
.tab-bar {
  display: flex;
  gap: 0.25rem;
  border-bottom: 2px solid var(--color-border);
  padding-bottom: 0;
}

.tab-btn {
  font-size: 0.85rem;
  font-weight: 500;
  padding: 0.6rem 1rem;
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
  transition: all 0.15s;
  margin-bottom: -2px;
}

.tab-btn:hover {
  color: var(--color-primary-600);
}

.tab-btn.active {
  color: var(--color-primary-600);
  border-bottom-color: var(--color-primary-500);
  background: var(--color-primary-50);
  border-radius: var(--radius-sm) var(--radius-sm) 0 0;
}

/* ── Summary Cards ── */
.summary-row {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 0.75rem;
}

.summary-card {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 1rem 1.25rem;
  text-align: center;
}

.summary-label {
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--color-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin-bottom: 0.5rem;
}

.summary-value {
  font-size: 1.5rem;
  font-weight: inherit;
  color: var(--color-primary-600);
  line-height: 1.2;
}

.summary-sub {
  font-size: 0.72rem;
  color: var(--color-text-muted);
  margin-top: 0.2rem;
}

/* ── Charts ── */
.charts-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1rem;
}

@media (max-width: 768px) {
  .charts-row {
    grid-template-columns: 1fr;
  }
}

.chart-card {
  padding: 1rem 1.25rem;
}

.chart-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 0.75rem;
}

.chart-title {
  font-size: inherit;
  font-weight: inherit;
  color: var(--color-text-primary);
  margin: 0;
}

.chart-empty {
  text-align: center;
  padding: 2rem;
  font-size: 0.85rem;
  color: var(--color-text-muted);
}

.chart-legend {
  display: flex;
  gap: 1rem;
}

.legend-item {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.72rem;
  color: var(--color-text-secondary);
}

.legend-dot {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 2px;
}

/* ── 双色堆叠柱状图 ── */
.bar-chart {
  display: flex;
  align-items: flex-end;
  gap: 2px;
  height: 140px;
  padding-top: 8px;
}

.bar-item {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 3px;
  height: 100%;
  justify-content: flex-end;
  cursor: help;
}

.bar-stack {
  width: 100%;
  max-width: 20px;
  display: flex;
  flex-direction: column-reverse;
  height: 100%;
  justify-content: flex-end;
}

.bar-segment {
  width: 100%;
  min-height: 0;
  transition: height 0.3s ease;
}

.bar-prompt {
  background: var(--color-primary-600);
  border-radius: 0 0 2px 2px;
}

.bar-completion {
  background: rgba(59, 130, 246, 0.4);
  border-radius: 2px 2px 0 0;
}

.bar-label {
  font-size: 0.7rem;
  color: var(--color-text-muted);
  white-space: nowrap;
}

/* ── 按调用方 ── */
.caller-list {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}

.caller-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.caller-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.caller-name {
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--color-text-primary);
}

.caller-tokens {
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--color-primary-600);
}

.caller-bar-bg {
  height: 6px;
  background: var(--color-bg-input, #f3f4f6);
  border-radius: 3px;
  overflow: hidden;
}

.caller-bar-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--color-primary-400), var(--color-primary-500));
  border-radius: 3px;
  transition: width 0.3s ease;
}

.caller-sub {
  font-size: 0.68rem;
  color: var(--color-text-muted);
}

/* ── 按模型分布 ── */
.model-list {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}

.model-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.model-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.model-name {
  font-size: 0.8rem;
  font-weight: 600;
  font-family: 'SF Mono', 'Menlo', monospace;
}

.model-tokens {
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--color-text-primary);
}

.model-bar-bg {
  height: 6px;
  background: var(--color-bg-input, #f3f4f6);
  border-radius: 3px;
  overflow: hidden;
}

.model-bar-fill {
  height: 100%;
  border-radius: 3px;
  transition: width 0.3s ease;
}

.model-sub {
  display: flex;
  justify-content: space-between;
  font-size: 0.68rem;
  color: var(--color-text-muted);
}

.model-cost {
  font-weight: 600;
  color: var(--color-text-secondary);
}

/* ── 小时热力图 ── */
.heatmap-container {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.heatmap-grid {
  display: grid;
  grid-template-columns: repeat(12, 1fr);
  gap: 4px;
}

.heatmap-cell {
  aspect-ratio: 1;
  border-radius: var(--radius-sm);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  cursor: help;
  transition: transform 0.15s;
}

.heatmap-cell:hover {
  transform: scale(1.1);
  z-index: 1;
}

.heatmap-hour {
  font-size: 0.6rem;
  font-weight: 600;
  color: var(--color-text-muted);
}

.heatmap-val {
  font-size: 0.7rem;
  color: var(--color-text-muted);
}

.heatmap-legend {
  display: flex;
  gap: 1rem;
  justify-content: flex-end;
}

.hm-legend-item {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.68rem;
  color: var(--color-text-muted);
}

.hm-dot {
  display: inline-block;
  width: 12px;
  height: 12px;
  border-radius: 2px;
}

/* ── 表格 ── */
.table-wrap {
  overflow-x: auto;
}

.usage-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.8rem;
}

.usage-table th {
  text-align: left;
  font-weight: 600;
  color: var(--color-text-muted);
  font-size: 0.72rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  padding: 0.5rem 0.75rem;
  border-bottom: 1px solid var(--color-border);
}

.usage-table td {
  padding: 0.5rem 0.75rem;
  border-bottom: 1px solid var(--color-border-light, #f3f4f6);
  color: var(--color-text-secondary);
}

.usage-table tbody tr:hover {
  background: var(--color-bg-hover, #f9fafb);
}

.usage-table .num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.usage-table .bold {
  font-weight: 600;
  color: var(--color-text-primary);
}

.time-cell {
  white-space: nowrap;
  font-size: 0.75rem;
}

.model-cell {
  font-family: 'SF Mono', 'Menlo', monospace;
  font-size: 0.75rem;
  max-width: 140px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.caller-tag {
  display: inline-block;
  font-size: 0.72rem;
  font-weight: 500;
  background: var(--color-primary-50);
  color: var(--color-primary-700);
  padding: 0.15rem 0.45rem;
  border-radius: 999px;
  white-space: nowrap;
}

.trace-link {
  color: var(--color-primary-600);
  cursor: pointer;
  font-size: 0.72rem;
  font-family: 'SF Mono', 'Menlo', monospace;
  text-decoration: underline;
  text-decoration-style: dashed;
}

.trace-link:hover {
  color: var(--color-primary-700);
}

.trace-none {
  color: var(--color-text-muted);
}

/* ── 筛选栏 ── */
.filter-bar {
  display: flex;
  gap: 1rem;
  padding: 1rem 1.25rem;
  flex-wrap: wrap;
}

.filter-item {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.filter-label {
  font-size: 0.68rem;
  font-weight: 600;
  color: var(--color-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.filter-select {
  font-size: 0.8rem;
  padding: 0.35rem 0.6rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-bg-card);
  color: var(--color-text-primary);
  cursor: pointer;
}

.filter-select:focus {
  outline: none;
  border-color: var(--color-primary-400);
}

/* ── 表格头 ── */
.table-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 1.25rem 0.5rem;
}

.table-total {
  font-size: 0.78rem;
  color: var(--color-text-muted);
}

/* ── 性能监控 ── */
.perf-section {
  padding: 1rem 1.25rem;
}

.perf-stats {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(130px, 1fr));
  gap: 0.75rem;
}

.perf-stat {
  text-align: center;
  padding: 0.6rem;
  background: var(--color-bg, #f9fafb);
  border-radius: var(--radius-sm);
}

.perf-stat-value {
  display: block;
  font-size: 1.15rem;
  font-weight: inherit;
  color: var(--color-text-primary);
}

.perf-stat-value.warn {
  color: var(--color-danger);
}

.perf-stat-label {
  font-size: 0.68rem;
  color: var(--color-text-muted);
  margin-top: 0.2rem;
}

.perf-agent-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.perf-agent-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.perf-agent-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.perf-agent-name {
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--color-text-primary);
}

.perf-agent-avg {
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--color-primary-600);
}

.perf-agent-bar-bg {
  height: 6px;
  background: var(--color-bg-input, #f3f4f6);
  border-radius: 3px;
  overflow: hidden;
}

.perf-agent-bar-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--color-primary-400), var(--color-primary-500));
  border-radius: 3px;
  transition: width 0.3s ease;
}

.perf-agent-sub {
  display: flex;
  gap: 0.75rem;
  font-size: 0.68rem;
  color: var(--color-text-muted);
}

.warn-text {
  color: var(--color-danger);
  font-weight: 500;
}

/* ── Trace 链路 ── */
.trace-input-bar {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 1rem;
}

.trace-input {
  flex: 1;
  font-size: 0.85rem;
  padding: 0.5rem 0.75rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-bg-card);
  color: var(--color-text-primary);
  font-family: 'SF Mono', 'Menlo', monospace;
}

.trace-input:focus {
  outline: none;
  border-color: var(--color-primary-400);
}

.btn-primary {
  background: var(--color-primary-500);
  color: white;
  border: none;
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-weight: 500;
  transition: background 0.15s;
}

.btn-primary:hover {
  background: var(--color-primary-600);
}

.btn-primary:disabled {
  opacity: 0.5;
  cursor: default;
}

.btn-sm {
  font-size: 0.8rem;
  padding: 0.4rem 0.85rem;
}

.trace-timeline {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.trace-header-info {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  background: var(--color-primary-50, #eff6ff);
  border-radius: var(--radius-sm);
}

.trace-id-label {
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--color-text-muted);
}

.trace-id-value {
  font-size: 0.8rem;
  font-family: 'SF Mono', 'Menlo', monospace;
  color: var(--color-primary-600);
  font-weight: 600;
}

.timeline-container {
  display: flex;
  flex-direction: column;
  gap: 0;
}

.timeline-node {
  display: flex;
  gap: 0.75rem;
  position: relative;
  padding-bottom: 1rem;
}

.timeline-dot {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  flex-shrink: 0;
  margin-top: 4px;
  z-index: 1;
  background: var(--color-primary);
}

/* 4 类事件圆点配色（C1：改用 CSS 变量） */
.dot-llm { background: var(--color-primary, #3b82f6); }
.dot-tool { background: var(--color-warning); }
.dot-agent { background: var(--color-info); }
.dot-rag { background: var(--color-success); }

.timeline-key {
  font-size: 0.68rem;
  color: var(--color-text-muted);
  font-family: 'SF Mono', 'Menlo', monospace;
}

.timeline-status {
  font-size: 0.72rem;
  font-weight: 600;
  padding: 0.05rem 0.35rem;
  border-radius: var(--radius-sm, 4px);
}

.timeline-status.ok {
  color: var(--color-success, #22c55e);
  background: rgba(34, 197, 94, 0.1);
}

.timeline-status.err {
  color: var(--color-danger, #ef4444);
  background: rgba(239, 68, 68, 0.1);
}

.timeline-error {
  color: var(--color-danger, #ef4444);
  font-size: 0.72rem;
}

.timeline-detail-inline {
  font-size: 0.7rem;
  color: var(--color-text-muted);
}

.timeline-line {
  position: absolute;
  left: 5px;
  top: 16px;
  bottom: 0;
  width: 2px;
  background: var(--color-border);
}

.timeline-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}

.timeline-time {
  font-size: 0.72rem;
  color: var(--color-text-muted);
  font-family: 'SF Mono', 'Menlo', monospace;
}

.timeline-body {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  flex-wrap: wrap;
}

.timeline-caller {
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--color-text-primary);
}

.timeline-model {
  font-size: 0.72rem;
  font-family: 'SF Mono', 'Menlo', monospace;
  color: var(--color-text-secondary);
  background: var(--color-bg, #f3f4f6);
  padding: 0.1rem 0.4rem;
  border-radius: 4px;
}

.timeline-tokens {
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--color-primary-600);
  font-variant-numeric: tabular-nums;
}

.timeline-cost {
  font-size: 0.72rem;
  color: var(--color-text-secondary);
}

.timeline-detail {
  font-size: 0.68rem;
  color: var(--color-text-muted);
}

.trace-summary {
  display: flex;
  gap: 1rem;
  flex-wrap: wrap;
  padding: 0.75rem 1rem;
  background: var(--color-bg, #f9fafb);
  border-radius: var(--radius-sm);
  border-top: 2px solid var(--color-primary-500);
}

.trace-sum-item {
  font-size: 0.8rem;
  color: var(--color-text-secondary);
}

.trace-sum-item strong {
  color: var(--color-text-primary);
  font-weight: 700;
}

/* ── Loading ── */
.loading-state {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.75rem;
  padding: 3rem;
  color: var(--color-text-muted);
  font-size: 0.875rem;
}

.spinner-lg {
  width: 32px;
  height: 32px;
  border: 3px solid var(--color-border);
  border-top-color: var(--color-primary-500);
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
}

@keyframes spin { to { transform: rotate(360deg); } }

/* ── 分页 ── */
.pagination {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.35rem;
  padding: 0.75rem 1.25rem;
  border-top: 1px solid var(--color-border-light, #f3f4f6);
}

.page-btn {
  min-width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.8rem;
  font-weight: 500;
  color: var(--color-text-secondary);
  background: none;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all 0.15s;
  padding: 0 0.4rem;
}

.page-btn:hover:not(:disabled):not(.page-dots) {
  border-color: var(--color-primary-400);
  color: var(--color-primary-600);
  background: var(--color-primary-50, #eff6ff);
}

.page-btn.active {
  background: var(--color-primary-500);
  color: white;
  border-color: var(--color-primary-500);
}

.page-btn:disabled {
  opacity: 0.4;
  cursor: default;
}

.page-dots {
  border: none;
  color: var(--color-text-muted);
}

/* ── 通用按钮 ── */
.btn-outline {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  color: var(--color-text-secondary);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.8rem;
  font-weight: 500;
  padding: 0.4rem 0.85rem;
  transition: all 0.15s;
}

.btn-outline:hover:not(:disabled) {
  border-color: var(--color-primary-400);
  color: var(--color-primary-600);
}

.btn-outline:disabled {
  opacity: 0.5;
  cursor: default;
}

.btn-danger-text {
  color: var(--color-danger);
  border-color: var(--color-danger);
}

.btn-danger-text:hover {
  background: rgba(239, 68, 68, 0.1);
}

/* ── 响应式 ── */
@media (max-width: 768px) {
  .summary-row {
    grid-template-columns: 1fr 1fr;
  }

  .heatmap-grid {
    grid-template-columns: repeat(6, 1fr);
  }

  .filter-bar {
    flex-direction: column;
  }

  .tab-btn {
    font-size: 0.78rem;
    padding: 0.5rem 0.6rem;
  }

  .cost-row {
    flex-direction: column;
    gap: 0.25rem;
  }
}
</style>
