<script setup>
import { ref, computed, onMounted } from 'vue'
import { getTokenUsage, getTokenUsageRecent, getTokenUsageSummary, getTokenUsageByCaller, getTokenUsageDaily, getPerformanceStats, getPerformanceByAgent } from '../api'

const loading = ref(true)
const summary = ref({ today: {}, total_calls: 0, total_tokens: 0, avg_per_call: 0 })
const daily = ref([])
const byCaller = ref([])
const records = ref([])
const page = ref(1)
const pageSize = 15
const total = ref(0)
const totalPages = computed(() => Math.ceil(total.value / pageSize) || 1)
const perfStats = ref({ total_runs: 0, avg_duration_ms: 0, max_duration_ms: 0, slow_calls: 0, unique_agents: 0 })
const perfByAgent = ref([])
const activePerfTab = ref('stats')

async function loadRecords() {
  try {
    const { data } = await getTokenUsageRecent(page.value, pageSize, 7)
    records.value = data.records || []
    total.value = data.total || 0
  } catch (e) {
    console.error('Failed to load records:', e)
  }
}

function goPage(p) {
  if (p < 1 || p > totalPages.value) return
  page.value = p
  loadRecords()
}

async function loadAll() {
  loading.value = true
  try {
    const [r1, r2, r3, r4, r5] = await Promise.all([
      getTokenUsageSummary(30),
      getTokenUsageDaily(30),
      getTokenUsageByCaller(7),
      getPerformanceStats(7),
      getPerformanceByAgent(7),
    ])
    summary.value = r1.data
    daily.value = r2.data.items || []
    byCaller.value = r3.data.items || []
    perfStats.value = r4.data
    perfByAgent.value = r5.data.items || []
  } catch (e) {
    console.error('Failed to load token usage:', e)
  } finally {
    loading.value = false
  }
  loadRecords()
}

function pages() {
  const pgs = []
  const total = totalPages.value
  const cur = page.value
  const start = Math.max(1, cur - 2)
  const end = Math.min(total, cur + 2)
  if (start > 1) pgs.push(1)
  if (start > 2) pgs.push('...')
  for (let i = start; i <= end; i++) pgs.push(i)
  if (end < total - 1) pgs.push('...')
  if (end < total) pgs.push(total)
  return pgs
}

onMounted(loadAll)

function formatTime(ts) {
  if (!ts) return ''
  const d = new Date(ts.replace(' ', 'T'))
  const h = String(d.getHours()).padStart(2, '0')
  const m = String(d.getMinutes()).padStart(2, '0')
  return `${h}:${m}`
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
  // specialist:xxx → 显示 Agent 名称
  if (c.startsWith('specialist:')) {
    const key = c.slice('specialist:'.length)
    return AGENT_NAME_MAP[key] || c
  }
  return LABEL_MAP[c] || c
}

const maxDailyTokens = computed(() => {
  let m = 0
  for (const d of daily.value) if (d.tokens > m) m = d.tokens
  return m || 1
})

const maxCallerTokens = computed(() => {
  let m = 0
  for (const c of byCaller.value) if (c.total_tokens > m) m = c.total_tokens
  return m || 1
})

</script>

<template>
  <div class="token-usage-page">
    <div class="page-header">
      <h2 class="page-title">Token 用量</h2>
      <span class="page-desc">LLM 调用消耗统计</span>
      <button class="btn btn-outline btn-sm" style="margin-left: auto;" @click="loadAll" :disabled="loading">
        <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
        刷新
      </button>
    </div>

    <!-- Loading -->
    <div v-if="loading" class="loading-state">
      <div class="spinner-lg"></div>
      <span>加载中...</span>
    </div>

    <template v-else>
      <!-- Summary Cards -->
      <div class="summary-row">
        <div class="summary-card">
          <div class="summary-label">今日消耗</div>
          <div class="summary-value">{{ (summary.today?.total || 0).toLocaleString() }}</div>
          <div class="summary-sub">tokens</div>
        </div>
        <div class="summary-card">
          <div class="summary-label">今日调用</div>
          <div class="summary-value">{{ summary.today?.calls || 0 }}</div>
          <div class="summary-sub">次 LLM 调用</div>
        </div>
        <div class="summary-card">
          <div class="summary-label">平均每次</div>
          <div class="summary-value">{{ summary.avg_per_call || 0 }}</div>
          <div class="summary-sub">tokens / call</div>
        </div>
        <div class="summary-card">
          <div class="summary-label">近 30 天累计</div>
          <div class="summary-value">{{ (summary.total_tokens || 0).toLocaleString() }}</div>
          <div class="summary-sub">tokens</div>
        </div>
        <div class="summary-card">
          <div class="summary-label">近 30 天调用</div>
          <div class="summary-value">{{ summary.total_calls || 0 }}</div>
          <div class="summary-sub">次 LLM 调用</div>
        </div>
      </div>

      <!-- Charts Row -->
      <div class="charts-row">
        <!-- Daily Trend -->
        <div class="card chart-card">
          <h4 class="chart-title">近 30 天趋势</h4>
          <div v-if="daily.length === 0" class="chart-empty">暂无数据</div>
          <div v-else class="bar-chart">
            <div v-for="d in daily" :key="d.day" class="bar-item" :title="`${d.day}: ${(d.tokens || 0).toLocaleString()} tokens (${d.calls} 次调用)`">
              <div class="bar-fill" :style="{ height: (d.tokens / maxDailyTokens * 100) + '%' }"></div>
              <div class="bar-label">{{ formatDate(d.day) }}</div>
            </div>
          </div>
        </div>

        <!-- By Caller -->
        <div class="card chart-card">
          <h4 class="chart-title">按调用方分布 (近 7 天)</h4>
          <div v-if="byCaller.length === 0" class="chart-empty">暂无数据</div>
          <div v-else class="caller-list">
            <div v-for="item in byCaller" :key="item.caller" class="caller-item">
              <div class="caller-row">
                <span class="caller-name">{{ callerLabel(item.caller) }}</span>
                <span class="caller-tokens">{{ (item.total_tokens || 0).toLocaleString() }}</span>
              </div>
              <div class="caller-bar-bg">
                <div class="caller-bar-fill" :style="{ width: (item.total_tokens / maxCallerTokens * 100) + '%' }"></div>
              </div>
              <div class="caller-sub">{{ item.calls }} 次调用</div>
            </div>
          </div>
        </div>
      </div>

      <!-- Performance Monitoring -->
      <div class="card perf-section">
        <div class="perf-header">
          <h4 class="chart-title" style="margin:0;">Agent 性能监控 (近 7 天)</h4>
          <div class="perf-tabs">
            <button :class="['perf-tab', { active: activePerfTab === 'stats' }]" @click="activePerfTab = 'stats'">概览</button>
            <button :class="['perf-tab', { active: activePerfTab === 'agents' }]" @click="activePerfTab = 'agents'">按 Agent</button>
          </div>
        </div>

        <div v-if="activePerfTab === 'stats'" class="perf-stats">
          <div class="perf-stat">
            <span class="perf-stat-value">{{ perfStats.total_runs }}</span>
            <span class="perf-stat-label">总调用</span>
          </div>
          <div class="perf-stat">
            <span class="perf-stat-value">{{ formatDuration(perfStats.avg_duration_ms) }}</span>
            <span class="perf-stat-label">平均耗时</span>
          </div>
          <div class="perf-stat">
            <span class="perf-stat-value">{{ formatDuration(perfStats.max_duration_ms) }}</span>
            <span class="perf-stat-label">最慢</span>
          </div>
          <div class="perf-stat">
            <span class="perf-stat-value" :class="{ 'warn': perfStats.slow_calls > 0 }">{{ perfStats.slow_calls }}</span>
            <span class="perf-stat-label">慢调用 (>30s)</span>
          </div>
          <div class="perf-stat">
            <span class="perf-stat-value">{{ perfStats.unique_agents }}</span>
            <span class="perf-stat-label">Agent 数</span>
          </div>
        </div>

        <div v-if="activePerfTab === 'agents'">
          <div v-if="perfByAgent.length === 0" class="chart-empty">暂无数据</div>
          <div v-else class="perf-agent-list">
            <div v-for="a in perfByAgent" :key="a.agent_key" class="perf-agent-item">
              <div class="perf-agent-row">
                <span class="perf-agent-name">{{ a.agent_name || a.agent_key }}</span>
                <span class="perf-agent-avg">{{ formatDuration(a.avg_duration_ms) }}</span>
              </div>
              <div class="perf-agent-bar-bg">
                <div class="perf-agent-bar-fill" :style="{ width: Math.min((a.avg_duration_ms / (perfStats.max_duration_ms || 1)) * 100, 100) + '%' }"></div>
              </div>
              <div class="perf-agent-sub">
                <span>{{ a.runs }} 次调用</span>
                <span v-if="a.slow_calls > 0" class="warn-text">⚠ {{ a.slow_calls }} 次慢调用</span>
                <span class="perf-agent-max">最慢 {{ formatDuration(a.max_duration_ms) }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Recent Calls Table -->
      <div class="card">
        <div class="table-header">
          <h4 class="chart-title" style="margin:0;">最近调用记录</h4>
          <span class="table-total">共 {{ total }} 条</span>
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
              </tr>
            </thead>
            <tbody>
              <tr v-for="r in records" :key="r.id">
                <td class="time-cell">{{ formatTime(r.created_at) }}</td>
                <td><span class="caller-tag">{{ callerLabel(r.caller || '') }}</span></td>
                <td class="model-cell">{{ r.model }}</td>
                <td class="num">{{ (r.prompt_tokens || 0).toLocaleString() }}</td>
                <td class="num">{{ (r.completion_tokens || 0).toLocaleString() }}</td>
                <td class="num bold">{{ (r.total_tokens || 0).toLocaleString() }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <!-- Pagination -->
        <div v-if="totalPages > 1" class="pagination">
          <button class="page-btn" :disabled="page <= 1" @click="goPage(page - 1)">‹</button>
          <template v-for="p in pages()" :key="p">
            <button v-if="p === '...'" class="page-btn page-dots" disabled>…</button>
            <button v-else :class="['page-btn', { active: p === page }]" @click="goPage(p)">{{ p }}</button>
          </template>
          <button class="page-btn" :disabled="page >= totalPages" @click="goPage(page + 1)">›</button>
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
  font-size: 1.2rem;
  font-weight: 700;
  color: var(--color-text-primary);
  margin: 0;
}

.page-desc {
  font-size: 0.8rem;
  color: var(--color-text-muted);
}

/* Summary Cards */
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
  margin-bottom: 0.35rem;
}

.summary-value {
  font-size: 1.5rem;
  font-weight: 700;
  color: var(--color-primary-600);
  line-height: 1.2;
}

.summary-sub {
  font-size: 0.72rem;
  color: var(--color-text-muted);
  margin-top: 0.2rem;
}

/* Charts Row */
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

.chart-title {
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0 0 0.75rem;
}

.chart-empty {
  text-align: center;
  padding: 2rem;
  font-size: 0.85rem;
  color: var(--color-text-muted);
}

/* Bar Chart (Daily Trend) */
.bar-chart {
  display: flex;
  align-items: flex-end;
  gap: 2px;
  height: 120px;
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
}

.bar-fill {
  width: 100%;
  max-width: 20px;
  background: linear-gradient(to top, var(--color-primary-400), var(--color-primary-500));
  border-radius: 2px 2px 0 0;
  min-height: 2px;
  transition: height 0.3s ease;
  cursor: help;
}

.bar-label {
  font-size: 0.55rem;
  color: var(--color-text-muted);
  white-space: nowrap;
}

/* Caller List */
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
  background: var(--color-bg-input);
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

/* Table */
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
  border-bottom: 1px solid var(--color-border-light);
  color: var(--color-text-secondary);
}

.usage-table tbody tr:hover {
  background: var(--color-bg-hover);
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
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.caller-tag {
  display: inline-block;
  font-size: 0.7rem;
  font-weight: 500;
  background: var(--color-primary-50);
  color: var(--color-primary-700);
  padding: 0.15rem 0.45rem;
  border-radius: 999px;
  white-space: nowrap;
}

/* Loading */
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

/* Table Header */
.table-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1rem 1.25rem 0;
}

.table-total {
  font-size: 0.78rem;
  color: var(--color-text-muted);
}

/* Performance Monitoring */
.perf-section {
  padding: 1rem 1.25rem;
}

.perf-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 0.75rem;
}

.perf-tabs {
  display: flex;
  gap: 0.25rem;
}

.perf-tab {
  font-size: 0.75rem;
  font-weight: 500;
  padding: 0.25rem 0.6rem;
  background: none;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  color: var(--color-text-secondary);
  cursor: pointer;
  transition: all 0.15s;
}

.perf-tab.active {
  background: var(--color-primary-50);
  border-color: var(--color-primary-400);
  color: var(--color-primary-600);
}

.perf-stats {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(130px, 1fr));
  gap: 0.75rem;
}

.perf-stat {
  text-align: center;
  padding: 0.6rem;
  background: var(--color-bg);
  border-radius: var(--radius-sm);
}

.perf-stat-value {
  display: block;
  font-size: 1.15rem;
  font-weight: 700;
  color: var(--color-text-primary);
}

.perf-stat-value.warn {
  color: #e53e3e;
}

.perf-stat-label {
  font-size: 0.68rem;
  color: var(--color-text-muted);
  margin-top: 0.1rem;
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
  background: var(--color-bg-input);
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
  color: #e53e3e;
  font-weight: 500;
}

/* Pagination */
.pagination {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.25rem;
  padding: 0.75rem 1.25rem;
  border-top: 1px solid var(--color-border-light);
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
  background: var(--color-primary-50);
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
</style>
