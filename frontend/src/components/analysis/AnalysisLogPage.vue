<script setup>
import { ref, computed, onMounted } from 'vue'
import { listAnalysisLogs, getAnalysisLogDetail, evaluateAnalysisLog, listAgentRuns, getAgentRunDetail } from '../../api'

// ── Tab 切换：独立分析 vs 对话协作 ──
const activeTab = ref('independent')  // 'independent' | 'conversation'

// ════════════ 独立分析 Tab（原有逻辑） ════════════

// ── 数据 ──
const loading = ref(true)
const logs = ref([])
const total = ref(0)
const stats = ref({ today_total: 0, avg_duration: 0, avg_token: 0, eval_count: 0 })
const page = ref(1)
const pageSize = 30
const totalPages = computed(() => Math.ceil(total.value / pageSize) || 1)

// ── 筛选 ──
const filterAgent = ref('')
const filterType = ref('')
const filterStatus = ref('')

// ── 类型中文标签 ──
const TYPE_LABELS = {
  daily_report: '市场日报', diversification: '分散度', panorama: '全景诊断',
  deep_dive: '基金深度', trade_review: '交易复盘', what_if: '情景推演',
  fund_analysis: '指定基金', hotspots: '热点分析', bond_recommend: '债券推荐',
  index_analysis: '指数深度', market_intel: '市场情报', enhanced_strategy: '增强策略',
  portfolio_ai: 'AI持仓', health_score: '健康分',
}

// ── 详情抽屉 ──
const detailVisible = ref(false)
const detailLoading = ref(false)
const detailData = ref(null)
const detailResult = ref('')
const evalFeedback = ref(null)

// ── 加载列表 ──
async function loadLogs() {
  loading.value = true
  try {
    const params = {
      limit: pageSize,
      offset: (page.value - 1) * pageSize,
    }
    if (filterAgent.value) params.agent_id = filterAgent.value
    if (filterType.value) params.analysis_type = filterType.value
    if (filterStatus.value) params.status = filterStatus.value
    const res = await listAnalysisLogs(params)
    logs.value = res.data.logs || []
    total.value = res.data.total || 0
    stats.value = res.data.stats || {}
  } catch (e) {
    console.error('加载分析记录失败:', e)
  } finally {
    loading.value = false
  }
}

// ── 查看详情 ──
async function viewDetail(logId) {
  detailVisible.value = true
  detailLoading.value = true
  detailData.value = null
  detailResult.value = ''
  evalFeedback.value = null
  try {
    const res = await getAnalysisLogDetail(logId)
    detailData.value = res.data.log
    detailResult.value = res.data.source_result || ''
    evalFeedback.value = res.data.eval_feedback || null
  } catch (e) {
    console.error('加载详情失败:', e)
  } finally {
    detailLoading.value = false
  }
}

// ── 手动评分 ──
async function triggerEval(logId) {
  try {
    await evaluateAnalysisLog(logId)
    alert('评估已提交，稍后刷新查看结果')
    setTimeout(() => loadLogs(), 3000)
  } catch (e) {
    alert('评估失败: ' + (e.response?.data?.detail || e.message))
  }
}

// ── 辅助函数 ──
function formatTime(t) {
  if (!t) return '-'
  const d = new Date(t)
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  const hh = String(d.getHours()).padStart(2, '0')
  const mi = String(d.getMinutes()).padStart(2, '0')
  return `${mm}-${dd} ${hh}:${mi}`
}
function formatDuration(ms) {
  if (!ms) return '-'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}
function statusLabel(s) {
  return { done: '完成', running: '运行中', error: '失败' }[s] || s
}
function statusClass(s) {
  return { done: 'st-done', running: 'st-run', error: 'st-err' }[s] || ''
}
function scoreLabel(score) {
  if (score === null || score === undefined) return '-'
  return score.toFixed(1)
}
function scoreClass(score) {
  if (score === null || score === undefined) return 'sc-none'
  if (score >= 8) return 'sc-high'
  if (score >= 6) return 'sc-mid'
  return 'sc-low'
}
function typeLabel(t) {
  return TYPE_LABELS[t] || t
}
function resetFilters() {
  filterAgent.value = ''
  filterType.value = ''
  filterStatus.value = ''
  page.value = 1
  loadLogs()
}
function changePage(delta) {
  page.value = Math.max(1, Math.min(totalPages.value, page.value + delta))
  loadLogs()
}

// ════════════ 对话协作 Tab（新增） ════════════

const runsLoading = ref(false)
const runs = ref([])
const runsTotal = ref(0)
const runsStats = ref({ total: 0, today: 0, status_dist: {}, top_agents: [], avg_duration_ms: 0 })
const runsPage = ref(1)
const runsTotalPages = computed(() => Math.ceil(runsTotal.value / pageSize) || 1)

// 对话协作筛选
const filterConvId = ref('')
const filterAgentKey = ref('')
const filterRunStatus = ref('')

// 对话协作详情
const runDetailVisible = ref(false)
const runDetailLoading = ref(false)
const runDetailData = ref(null)

// 状态中文标签
const RUN_STATUS_LABELS = {
  success: '成功', completed: '完成', error: '错误',
  failed: '失败', timeout: '超时', cancelled: '已取消',
  running: '运行中', pending: '等待中',
}
function runStatusLabel(s) { return RUN_STATUS_LABELS[s] || s }
function runStatusClass(s) {
  if (['success', 'completed'].includes(s)) return 'st-done'
  if (['running', 'pending'].includes(s)) return 'st-run'
  if (['error', 'failed', 'timeout', 'cancelled'].includes(s)) return 'st-err'
  return ''
}

async function loadRuns() {
  runsLoading.value = true
  try {
    const params = {
      limit: pageSize,
      offset: (runsPage.value - 1) * pageSize,
    }
    if (filterConvId.value) params.conversation_id = filterConvId.value
    if (filterAgentKey.value) params.agent_key = filterAgentKey.value
    if (filterRunStatus.value) params.status = filterRunStatus.value
    const res = await listAgentRuns(params)
    const d = res.data
    runs.value = d.runs || []
    runsTotal.value = d.total || 0
    runsStats.value = d.stats || {}
  } catch (e) {
    console.error('加载对话协作记录失败:', e)
  } finally {
    runsLoading.value = false
  }
}

async function viewRunDetail(runId) {
  runDetailVisible.value = true
  runDetailLoading.value = true
  runDetailData.value = null
  try {
    const res = await getAgentRunDetail(runId)
    runDetailData.value = res.data.run
  } catch (e) {
    console.error('加载 agent_run 详情失败:', e)
  } finally {
    runDetailLoading.value = false
  }
}

function resetRunsFilters() {
  filterConvId.value = ''
  filterAgentKey.value = ''
  filterRunStatus.value = ''
  runsPage.value = 1
  loadRuns()
}
function changeRunsPage(delta) {
  runsPage.value = Math.max(1, Math.min(runsTotalPages.value, runsPage.value + delta))
  loadRuns()
}

// Tab 切换时按需加载
function switchTab(tab) {
  activeTab.value = tab
  if (tab === 'conversation' && runs.value.length === 0) {
    loadRuns()
  }
}

onMounted(() => loadLogs())
</script>

<template>
  <div class="analysis-log-page">
    <div class="page-header">
      <h2 class="page-title">分析记录</h2>
      <span class="page-sub">所有分析 Agent 执行记录统一查看，支持快速定位与质量评估</span>
    </div>

    <!-- Tab 切换 -->
    <div class="tab-bar">
      <button :class="['tab-btn', { active: activeTab === 'independent' }]" @click="switchTab('independent')">
        独立分析
        <span class="tab-count">{{ total }}</span>
      </button>
      <button :class="['tab-btn', { active: activeTab === 'conversation' }]" @click="switchTab('conversation')">
        对话协作
        <span class="tab-count">{{ runsTotal }}</span>
      </button>
    </div>

    <!-- ════════ 独立分析 Tab ════════ -->
    <template v-if="activeTab === 'independent'">
      <!-- 统计卡片 -->
      <div class="stats-row">
        <div class="stat-card">
          <div class="stat-value">{{ stats.today_total }}</div>
          <div class="stat-label">今日总数</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">{{ formatDuration(stats.avg_duration) }}</div>
          <div class="stat-label">平均耗时</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">{{ stats.avg_token }}</div>
          <div class="stat-label">平均 Token</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">{{ stats.eval_count }}</div>
          <div class="stat-label">已评估</div>
        </div>
      </div>

      <!-- 筛选栏 -->
      <div class="filter-bar">
        <select v-model="filterAgent" class="filter-select" @change="page = 1; loadLogs()">
          <option value="">全部 Agent</option>
          <option value="1">市场日报分析师</option>
          <option value="2">分散度分析师</option>
          <option value="3">全景诊断分析师</option>
          <option value="4">基金深度分析师</option>
          <option value="5">交易复盘分析师</option>
          <option value="6">情景推演分析师</option>
          <option value="7">热点分析专家</option>
          <option value="8">债券配置顾问</option>
          <option value="9">指数深度分析师</option>
          <option value="10">市场情报分析师</option>
          <option value="11">增强策略分析师</option>
        </select>
        <select v-model="filterType" class="filter-select" @change="page = 1; loadLogs()">
          <option value="">全部类型</option>
          <option v-for="(label, key) in TYPE_LABELS" :key="key" :value="key">{{ label }}</option>
        </select>
        <select v-model="filterStatus" class="filter-select" @change="page = 1; loadLogs()">
          <option value="">全部状态</option>
          <option value="done">完成</option>
          <option value="running">运行中</option>
          <option value="error">失败</option>
        </select>
        <button class="filter-btn" @click="resetFilters">重置</button>
        <button class="filter-btn refresh-btn" @click="loadLogs">刷新</button>
      </div>

      <!-- 记录列表 -->
      <div class="log-table-wrap">
        <table class="log-table" v-if="!loading && logs.length > 0">
          <thead>
            <tr>
              <th>时间</th>
              <th>Agent</th>
              <th>类型</th>
              <th>输入摘要</th>
              <th>耗时</th>
              <th>Token</th>
              <th>状态</th>
              <th>评分</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="log in logs" :key="log.id" @click="viewDetail(log.id)" class="log-row">
              <td class="col-time">{{ formatTime(log.created_at) }}</td>
              <td class="col-agent">{{ log.agent_name || '-' }}</td>
              <td class="col-type">{{ typeLabel(log.analysis_type) }}</td>
              <td class="col-summary">{{ log.input_summary || log.query?.slice(0, 40) || '-' }}</td>
              <td class="col-duration">{{ formatDuration(log.duration_ms) }}</td>
              <td class="col-token">{{ log.token_usage || '-' }}</td>
              <td><span class="status-tag" :class="statusClass(log.status)">{{ statusLabel(log.status) }}</span></td>
              <td><span class="score-tag" :class="scoreClass(log.eval_score)">{{ scoreLabel(log.eval_score) }}</span></td>
              <td class="col-actions" @click.stop>
                <button class="op-btn" @click="viewDetail(log.id)">详情</button>
                <button v-if="log.status === 'done' && !log.has_eval" class="op-btn op-eval" @click="triggerEval(log.id)">评分</button>
              </td>
            </tr>
          </tbody>
        </table>
        <div v-if="loading" class="empty-state">加载中...</div>
        <div v-if="!loading && logs.length === 0" class="empty-state">暂无分析记录</div>
      </div>

      <!-- 分页 -->
      <div class="pagination" v-if="totalPages > 1">
        <button class="page-btn" :disabled="page <= 1" @click="changePage(-1)">上一页</button>
        <span class="page-info">{{ page }} / {{ totalPages }}</span>
        <button class="page-btn" :disabled="page >= totalPages" @click="changePage(1)">下一页</button>
      </div>
    </template>

    <!-- ════════ 对话协作 Tab ════════ -->
    <template v-if="activeTab === 'conversation'">
      <!-- 统计卡片 -->
      <div class="stats-row">
        <div class="stat-card">
          <div class="stat-value">{{ runsStats.total }}</div>
          <div class="stat-label">总记录数</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">{{ runsStats.today }}</div>
          <div class="stat-label">今日新增</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">{{ formatDuration(runsStats.avg_duration_ms) }}</div>
          <div class="stat-label">平均耗时</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">{{ runsStats.status_dist?.success || runsStats.status_dist?.completed || 0 }}</div>
          <div class="stat-label">成功数</div>
        </div>
      </div>

      <!-- 专家 Top N 快览 -->
      <div class="agent-rank" v-if="runsStats.top_agents && runsStats.top_agents.length > 0">
        <div class="rank-title">专家执行 Top 10</div>
        <div class="rank-list">
          <div v-for="(a, i) in runsStats.top_agents" :key="a.agent_key" class="rank-item"
               @click="filterAgentKey = a.agent_key; runsPage = 1; loadRuns()">
            <span class="rank-no">{{ i + 1 }}</span>
            <span class="rank-name">{{ a.agent_name }}</span>
            <span class="rank-cnt">{{ a.cnt }}次</span>
            <span class="rank-dur">{{ formatDuration(a.avg_ms) }}</span>
            <span class="rank-success" v-if="a.success_cnt">成功{{ a.success_cnt }}</span>
          </div>
        </div>
      </div>

      <!-- 筛选栏 -->
      <div class="filter-bar">
        <input v-model="filterConvId" class="filter-input" placeholder="对话 ID"
               @keyup.enter="runsPage = 1; loadRuns()" />
        <input v-model="filterAgentKey" class="filter-input" placeholder="专家 key（如 valuation_expert）"
               @keyup.enter="runsPage = 1; loadRuns()" />
        <select v-model="filterRunStatus" class="filter-select" @change="runsPage = 1; loadRuns()">
          <option value="">全部状态</option>
          <option v-for="(label, key) in RUN_STATUS_LABELS" :key="key" :value="key">{{ label }}</option>
        </select>
        <button class="filter-btn" @click="resetRunsFilters">重置</button>
        <button class="filter-btn refresh-btn" @click="loadRuns">刷新</button>
      </div>

      <!-- 对话协作记录列表 -->
      <div class="log-table-wrap">
        <table class="log-table" v-if="!runsLoading && runs.length > 0">
          <thead>
            <tr>
              <th>时间</th>
              <th>对话ID</th>
              <th>专家</th>
              <th>查询</th>
              <th>耗时</th>
              <th>结果长度</th>
              <th>状态</th>
              <th>trace_id</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in runs" :key="r.id" @click="viewRunDetail(r.id)" class="log-row">
              <td class="col-time">{{ formatTime(r.created_at) }}</td>
              <td class="col-conv">{{ r.conversation_id || '-' }}</td>
              <td class="col-agent">
                <div>{{ r.agent_name || '-' }}</div>
                <div class="agent-key-hint">{{ r.agent_key }}</div>
              </td>
              <td class="col-summary">{{ r.query?.slice(0, 50) || '-' }}</td>
              <td class="col-duration">{{ formatDuration(r.duration_ms) }}</td>
              <td class="col-token">{{ r.result_len || 0 }}</td>
              <td><span class="status-tag" :class="runStatusClass(r.status)">{{ runStatusLabel(r.status) }}</span></td>
              <td class="col-trace" :title="r.trace_id">{{ r.trace_id ? r.trace_id.slice(0, 8) : '-' }}</td>
              <td class="col-actions" @click.stop>
                <button class="op-btn" @click="viewRunDetail(r.id)">详情</button>
              </td>
            </tr>
          </tbody>
        </table>
        <div v-if="runsLoading" class="empty-state">加载中...</div>
        <div v-if="!runsLoading && runs.length === 0" class="empty-state">暂无对话协作记录</div>
      </div>

      <!-- 分页 -->
      <div class="pagination" v-if="runsTotalPages > 1">
        <button class="page-btn" :disabled="runsPage <= 1" @click="changeRunsPage(-1)">上一页</button>
        <span class="page-info">{{ runsPage }} / {{ runsTotalPages }}</span>
        <button class="page-btn" :disabled="runsPage >= runsTotalPages" @click="changeRunsPage(1)">下一页</button>
      </div>
    </template>

    <!-- 详情抽屉（独立分析） -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="detailVisible" class="drawer-overlay" @click="detailVisible = false">
          <div class="drawer-panel" @click.stop>
            <div class="drawer-header">
              <h3>分析记录详情</h3>
              <button class="drawer-close" @click="detailVisible = false">×</button>
            </div>
            <div class="drawer-body" v-if="detailData">
              <div class="detail-meta">
                <div class="meta-row"><span class="meta-label">trace_id</span><span class="meta-value">{{ detailData.trace_id }}</span></div>
                <div class="meta-row"><span class="meta-label">Agent</span><span class="meta-value">{{ detailData.agent_name || '-' }}</span></div>
                <div class="meta-row"><span class="meta-label">类型</span><span class="meta-value">{{ typeLabel(detailData.analysis_type) }}</span></div>
                <div class="meta-row"><span class="meta-label">状态</span><span class="meta-value"><span class="status-tag" :class="statusClass(detailData.status)">{{ statusLabel(detailData.status) }}</span></span></div>
                <div class="meta-row"><span class="meta-label">耗时</span><span class="meta-value">{{ formatDuration(detailData.duration_ms) }}</span></div>
                <div class="meta-row"><span class="meta-label">Token</span><span class="meta-value">{{ detailData.token_usage || '-' }}</span></div>
                <div class="meta-row"><span class="meta-label">评分</span><span class="meta-value"><span class="score-tag" :class="scoreClass(detailData.eval_score)">{{ scoreLabel(detailData.eval_score) }}</span></span></div>
                <div class="meta-row"><span class="meta-label">输入摘要</span><span class="meta-value">{{ detailData.input_summary || '-' }}</span></div>
                <div class="meta-row"><span class="meta-label">创建时间</span><span class="meta-value">{{ detailData.created_at }}</span></div>
                <div class="meta-row" v-if="detailData.completed_at"><span class="meta-label">完成时间</span><span class="meta-value">{{ detailData.completed_at }}</span></div>
                <div class="meta-row" v-if="detailData.error_msg"><span class="meta-label">错误</span><span class="meta-value error-text">{{ detailData.error_msg }}</span></div>
              </div>
              <div class="detail-section">
                <h4>分析结果</h4>
                <div class="detail-result" v-html="detailResult"></div>
              </div>
              <div class="detail-section" v-if="evalFeedback">
                <h4>质量评估 <span class="eval-type-tag" v-if="evalFeedback.analysis_type">{{ evalFeedback.analysis_type }}</span></h4>
                <div class="eval-overview">
                  <div class="eval-overall">
                    <span class="eval-overall-label">综合评分</span>
                    <span class="eval-overall-score" :class="scoreClass(evalFeedback.overall_score)">{{ evalFeedback.overall_score ?? '-' }}</span>
                    <span class="eval-overall-reason" v-if="evalFeedback.overall_reason">{{ evalFeedback.overall_reason }}</span>
                  </div>
                </div>
                <div class="eval-metrics">
                  <div class="metric-item">
                    <div class="metric-head">
                      <span class="metric-label">数据准确性</span>
                      <span class="metric-score" :class="scoreClass(evalFeedback.score_data_accuracy)">{{ evalFeedback.score_data_accuracy ?? '-' }}</span>
                    </div>
                    <div class="metric-reason" v-if="evalFeedback.data_accuracy_reason">{{ evalFeedback.data_accuracy_reason }}</div>
                  </div>
                  <div class="metric-item">
                    <div class="metric-head">
                      <span class="metric-label">逻辑性</span>
                      <span class="metric-score" :class="scoreClass(evalFeedback.score_logic)">{{ evalFeedback.score_logic ?? '-' }}</span>
                    </div>
                    <div class="metric-reason" v-if="evalFeedback.logic_reason">{{ evalFeedback.logic_reason }}</div>
                  </div>
                  <div class="metric-item">
                    <div class="metric-head">
                      <span class="metric-label">可执行性</span>
                      <span class="metric-score" :class="scoreClass(evalFeedback.score_actionability)">{{ evalFeedback.score_actionability ?? '-' }}</span>
                    </div>
                    <div class="metric-reason" v-if="evalFeedback.actionability_reason">{{ evalFeedback.actionability_reason }}</div>
                  </div>
                </div>
              </div>
              <div class="detail-section eval-empty" v-else-if="detailData && detailData.status === 'done'">
                <h4>质量评估</h4>
                <div class="eval-empty-tip">暂无评估数据，可点击列表"评分"按钮触发评估</div>
              </div>
            </div>
            <div class="drawer-body" v-else-if="detailLoading">
              加载中...
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>

    <!-- 详情抽屉（对话协作） -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="runDetailVisible" class="drawer-overlay" @click="runDetailVisible = false">
          <div class="drawer-panel" @click.stop>
            <div class="drawer-header">
              <h3>对话协作产出详情</h3>
              <button class="drawer-close" @click="runDetailVisible = false">×</button>
            </div>
            <div class="drawer-body" v-if="runDetailData">
              <div class="detail-meta">
                <div class="meta-row"><span class="meta-label">run_id</span><span class="meta-value">{{ runDetailData.id }}</span></div>
                <div class="meta-row"><span class="meta-label">对话ID</span><span class="meta-value">{{ runDetailData.conversation_id || '-' }}</span></div>
                <div class="meta-row"><span class="meta-label">消息ID</span><span class="meta-value">{{ runDetailData.message_id || '-' }}</span></div>
                <div class="meta-row"><span class="meta-label">专家</span><span class="meta-value">{{ runDetailData.agent_name }} ({{ runDetailData.agent_key }})</span></div>
                <div class="meta-row"><span class="meta-label">状态</span><span class="meta-value"><span class="status-tag" :class="runStatusClass(runDetailData.status)">{{ runStatusLabel(runDetailData.status) }}</span></span></div>
                <div class="meta-row"><span class="meta-label">耗时</span><span class="meta-value">{{ formatDuration(runDetailData.duration_ms) }}</span></div>
                <div class="meta-row"><span class="meta-label">阶段</span><span class="meta-value">{{ runDetailData.run_phase || '-' }}</span></div>
                <div class="meta-row"><span class="meta-label">trace_id</span><span class="meta-value">{{ runDetailData.trace_id || '-' }}</span></div>
                <div class="meta-row"><span class="meta-label">创建时间</span><span class="meta-value">{{ runDetailData.created_at }}</span></div>
              </div>
              <div class="detail-section" v-if="runDetailData.query">
                <h4>输入查询</h4>
                <div class="detail-query">{{ runDetailData.query }}</div>
              </div>
              <div class="detail-section">
                <h4>专家分析产出</h4>
                <div class="detail-result">{{ runDetailData.result || '(空)' }}</div>
              </div>
              <div class="detail-section" v-if="runDetailData.tool_calls">
                <h4>工具调用</h4>
                <div class="detail-tool-calls">{{ runDetailData.tool_calls }}</div>
              </div>
            </div>
            <div class="drawer-body" v-else-if="runDetailLoading">
              加载中...
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<style scoped>
.analysis-log-page { padding: 1rem; max-width: 1400px; margin: 0 auto; }
.page-header { margin-bottom: 1rem; }
.page-title { font-size: 1.25rem; font-weight: 600; margin: 0; }
.page-sub { font-size: 0.8rem; color: var(--color-text-tertiary); }

/* 统计卡片 */
.stats-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 0.75rem; margin-bottom: 1rem; }
.stat-card { background: var(--color-bg-card); border: 1px solid var(--color-border); border-radius: 8px; padding: 0.75rem 1rem; text-align: center; }
.stat-value { font-size: 1.4rem; font-weight: 700; color: var(--color-text-primary); }
.stat-label { font-size: 0.75rem; color: var(--color-text-tertiary); margin-top: 0.25rem; }

/* 筛选栏 */
.filter-bar { display: flex; gap: 0.5rem; margin-bottom: 1rem; flex-wrap: wrap; align-items: center; }
.filter-select { padding: 0.4rem 0.6rem; border: 1px solid var(--color-border); border-radius: 6px; background: var(--color-bg-card); color: var(--color-text-primary); font-size: 0.8rem; }
.filter-input { padding: 0.4rem 0.6rem; border: 1px solid var(--color-border); border-radius: 6px; background: var(--color-bg-card); color: var(--color-text-primary); font-size: 0.8rem; min-width: 140px; }
.filter-input::placeholder { color: var(--color-text-tertiary); }
.filter-btn { padding: 0.4rem 0.8rem; border: 1px solid var(--color-border); border-radius: 6px; background: var(--color-bg-card); color: var(--color-text-secondary); cursor: pointer; font-size: 0.8rem; }
.filter-btn:hover { border-color: var(--color-text-tertiary); }
.refresh-btn { margin-left: auto; }

/* Tab 切换条 */
.tab-bar { display: flex; gap: 0.25rem; margin-bottom: 1rem; border-bottom: 1px solid var(--color-border); }
.tab-btn { padding: 0.5rem 1rem; border: none; background: transparent; color: var(--color-text-secondary); cursor: pointer; font-size: 0.85rem; border-bottom: 2px solid transparent; transition: all 0.15s; }
.tab-btn:hover { color: var(--color-text-primary); }
.tab-btn.active { color: var(--color-text-primary); border-bottom-color: var(--color-accent, #4fd1c5); font-weight: 600; }
.tab-count { display: inline-block; padding: 0.05rem 0.4rem; margin-left: 0.35rem; background: var(--color-bg-secondary); color: var(--color-text-tertiary); border-radius: 8px; font-size: 0.68rem; font-weight: 500; }
.tab-btn.active .tab-count { background: rgba(79, 209, 197, 0.15); color: var(--color-accent, #4fd1c5); }

/* 专家排名（对话协作 Tab） */
.agent-rank { background: var(--color-bg-card); border: 1px solid var(--color-border); border-radius: 8px; padding: 0.75rem 1rem; margin-bottom: 1rem; }
.rank-title { font-size: 0.78rem; color: var(--color-text-tertiary); margin-bottom: 0.5rem; }
.rank-list { display: flex; flex-wrap: wrap; gap: 0.5rem; }
.rank-item { display: flex; align-items: center; gap: 0.35rem; padding: 0.25rem 0.6rem; background: var(--color-bg-secondary); border-radius: 12px; font-size: 0.75rem; cursor: pointer; transition: all 0.15s; }
.rank-item:hover { background: var(--color-bg-tertiary); }
.rank-no { color: var(--color-text-tertiary); font-weight: 600; min-width: 16px; }
.rank-name { color: var(--color-text-primary); font-weight: 500; }
.rank-cnt { color: var(--color-accent, #4fd1c5); }
.rank-dur { color: var(--color-text-tertiary); }
.rank-success { color: #16a34a; font-size: 0.7rem; }

/* 对话协作表格新增列 */
.col-conv { white-space: nowrap; color: var(--color-text-secondary); text-align: center; }
.col-trace { font-family: monospace; font-size: 0.72rem; color: var(--color-text-tertiary); }
.agent-key-hint { font-size: 0.65rem; color: var(--color-text-tertiary); margin-top: 1px; }

/* 详情抽屉新增（对话协作） */
.detail-query { font-size: 0.78rem; color: var(--color-text-secondary); background: var(--color-bg-secondary); padding: 0.75rem; border-radius: 6px; line-height: 1.6; white-space: pre-wrap; word-break: break-word; }
.detail-tool-calls { font-size: 0.72rem; font-family: monospace; color: var(--color-text-secondary); background: var(--color-bg-secondary); padding: 0.75rem; border-radius: 6px; white-space: pre-wrap; word-break: break-word; max-height: 200px; overflow-y: auto; }

/* 表格 */
.log-table-wrap { background: var(--color-bg-card); border: 1px solid var(--color-border); border-radius: 8px; overflow-x: auto; -webkit-overflow-scrolling: touch; }
.log-table { width: 100%; border-collapse: collapse; }
.log-table th { padding: 0.6rem 0.5rem; text-align: left; font-size: 0.75rem; font-weight: 600; color: var(--color-text-tertiary); border-bottom: 1px solid var(--color-border); background: var(--color-bg-secondary); }
.log-table td { padding: 0.5rem 0.5rem; font-size: 0.8rem; border-bottom: 1px solid var(--color-border); color: var(--color-text-primary); }
.log-row { cursor: pointer; transition: background 0.1s; }
.log-row:hover { background: var(--color-bg-secondary); }
.col-time { white-space: nowrap; color: var(--color-text-secondary); }
.col-agent { white-space: nowrap; }
.col-type { white-space: nowrap; }
.col-summary { max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.col-duration, .col-token { text-align: right; white-space: nowrap; }
.col-actions { white-space: nowrap; }

/* 状态标签 */
.status-tag { display: inline-block; padding: 0.1rem 0.5rem; border-radius: 4px; font-size: 0.7rem; font-weight: 500; }
.st-done { background: rgba(34, 197, 94, 0.15); color: #16a34a; }
.st-run { background: rgba(59, 130, 246, 0.15); color: #2563eb; }
.st-err { background: rgba(239, 68, 68, 0.15); color: #dc2626; }

/* 评分标签 */
.score-tag { display: inline-block; padding: 0.1rem 0.4rem; border-radius: 4px; font-size: 0.7rem; font-weight: 600; min-width: 28px; text-align: center; }
.sc-high { background: rgba(34, 197, 94, 0.15); color: #16a34a; }
.sc-mid { background: rgba(245, 158, 11, 0.15); color: #d97706; }
.sc-low { background: rgba(239, 68, 68, 0.15); color: #dc2626; }
.sc-none { color: var(--color-text-tertiary); }

/* 操作按钮 */
.op-btn { padding: 0.2rem 0.5rem; border: 1px solid var(--color-border); border-radius: 4px; background: var(--color-bg-card); color: var(--color-text-secondary); cursor: pointer; font-size: 0.72rem; margin-right: 0.25rem; }
.op-btn:hover { border-color: var(--color-text-tertiary); }
.op-eval { color: #ea580c; border-color: #ea580c; }
.op-eval:hover { background: rgba(234, 88, 12, 0.1); }

/* 分页 */
.pagination { display: flex; align-items: center; justify-content: center; gap: 1rem; margin-top: 1rem; }
.page-btn { padding: 0.3rem 0.8rem; border: 1px solid var(--color-border); border-radius: 6px; background: var(--color-bg-card); cursor: pointer; font-size: 0.8rem; }
.page-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.page-info { font-size: 0.8rem; color: var(--color-text-secondary); }

/* 空状态 */
.empty-state { padding: 2rem; text-align: center; color: var(--color-text-tertiary); }

/* 详情抽屉 */
.drawer-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.4); z-index: 1000; display: flex; justify-content: flex-end; }
.drawer-panel { width: 600px; max-width: 90vw; background: var(--color-bg-card); height: 100%; overflow-y: auto; box-shadow: -4px 0 20px rgba(0,0,0,0.1); }
.drawer-header { display: flex; align-items: center; justify-content: space-between; padding: 1rem 1.5rem; border-bottom: 1px solid var(--color-border); position: sticky; top: 0; background: var(--color-bg-card); z-index: 1; }
.drawer-header h3 { margin: 0; font-size: 1.1rem; }
.drawer-close { background: none; border: none; font-size: 1.5rem; cursor: pointer; color: var(--color-text-tertiary); }
.drawer-body { padding: 1.5rem; }
.detail-meta { margin-bottom: 1.5rem; }
.meta-row { display: flex; padding: 0.4rem 0; border-bottom: 1px solid var(--color-border); }
.meta-label { width: 80px; flex-shrink: 0; font-size: 0.78rem; color: var(--color-text-tertiary); }
.meta-value { font-size: 0.82rem; color: var(--color-text-primary); word-break: break-all; }
.error-text { color: #dc2626; }
.detail-section h4 { font-size: 0.9rem; margin: 0 0 0.5rem; }
.detail-result { font-size: 0.8rem; line-height: 1.7; color: var(--color-text-primary); white-space: pre-wrap; word-break: break-word; background: var(--color-bg-secondary); padding: 1rem; border-radius: 6px; }

/* 质量评估区块 */
.eval-type-tag { display: inline-block; padding: 0.05rem 0.4rem; margin-left: 0.4rem; background: var(--color-bg-secondary); color: var(--color-text-tertiary); border-radius: 8px; font-size: 0.68rem; font-weight: 500; }
.eval-overview { background: var(--color-bg-secondary); border-radius: 6px; padding: 0.75rem 1rem; margin-bottom: 0.5rem; }
.eval-overall { display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap; }
.eval-overall-label { font-size: 0.75rem; color: var(--color-text-tertiary); }
.eval-overall-score { display: inline-block; padding: 0.1rem 0.5rem; border-radius: 4px; font-size: 0.9rem; font-weight: 700; min-width: 32px; text-align: center; }
.eval-overall-reason { font-size: 0.75rem; color: var(--color-text-secondary); flex: 1 1 100%; margin-top: 0.25rem; line-height: 1.5; }
.eval-metrics { display: flex; flex-direction: column; gap: 0.5rem; }
.metric-item { background: var(--color-bg-secondary); border-radius: 6px; padding: 0.6rem 0.85rem; }
.metric-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.25rem; }
.metric-label { font-size: 0.78rem; color: var(--color-text-secondary); font-weight: 500; }
.metric-score { display: inline-block; padding: 0.05rem 0.45rem; border-radius: 4px; font-size: 0.78rem; font-weight: 700; min-width: 28px; text-align: center; }
.metric-reason { font-size: 0.72rem; color: var(--color-text-tertiary); line-height: 1.55; }
.eval-empty .eval-empty-tip { font-size: 0.75rem; color: var(--color-text-tertiary); padding: 0.75rem 1rem; background: var(--color-bg-secondary); border-radius: 6px; }

/* 动画 */
.fade-enter-active, .fade-leave-active { transition: opacity 0.2s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }

/* 响应式 */
@media (max-width: 768px) {
  .analysis-log-page { padding: 0.5rem; }
  .page-title { font-size: 1.1rem; }
  .page-sub { font-size: 0.72rem; }
  .stats-row { grid-template-columns: repeat(2, 1fr); gap: 0.5rem; margin-bottom: 0.75rem; }
  .stat-card { padding: 0.5rem 0.75rem; }
  .stat-value { font-size: 1.1rem; }
  .stat-label { font-size: 0.68rem; }
  /* 筛选栏：下拉框占满宽度，按钮单独行 */
  .filter-bar { gap: 0.4rem; }
  .filter-select { flex: 1 1 calc(50% - 0.4rem); min-width: 0; font-size: 0.72rem; padding: 0.35rem 0.4rem; }
  .filter-btn { padding: 0.35rem 0.6rem; font-size: 0.72rem; }
  .refresh-btn { margin-left: 0; flex: 1 1 100%; }
  /* 表格：允许横向滚动，设置最小宽度避免列挤压 */
  .log-table { font-size: 0.7rem; min-width: 680px; }
  .log-table th, .log-table td { padding: 0.4rem 0.3rem; }
  .col-summary { max-width: 120px; }
  /* 分页按钮增大点击区域 */
  .page-btn { padding: 0.4rem 1rem; font-size: 0.75rem; }
  /* 详情抽屉全屏 */
  .drawer-panel { width: 100vw; max-width: 100vw; }
  .drawer-header { padding: 0.75rem 1rem; }
  .drawer-header h3 { font-size: 1rem; }
  .drawer-body { padding: 1rem; }
  .meta-label { width: 64px; font-size: 0.7rem; }
  .meta-value { font-size: 0.72rem; }
  .detail-result { font-size: 0.72rem; padding: 0.75rem; }
}

@media (max-width: 480px) {
  .stats-row { grid-template-columns: 1fr 1fr; }
  .filter-select { flex: 1 1 100%; }
}
</style>
