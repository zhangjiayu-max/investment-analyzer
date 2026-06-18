<template>
  <div class="quality-page">
    <div class="page-header">
      <h2 class="page-title">📊 质量仪表盘</h2>
      <p class="page-desc">LLM 产出质量监控与评估</p>
    </div>

    <!-- Loading -->
    <div v-if="loading" class="loading-state">
      <div class="spinner-lg"></div>
      <span>加载中...</span>
    </div>

    <div v-else>
      <!-- Summary Cards -->
      <div class="summary-grid">
        <div class="card summary-card">
          <div class="summary-label">评测平均分</div>
          <div class="summary-value" :class="scoreClass(summary.eval_avg_score)">
            {{ summary.eval_avg_score?.toFixed(1) || '-' }}
          </div>
          <div class="summary-sub">/ 10</div>
        </div>
        <div class="card summary-card">
          <div class="summary-label">优秀 (≥7分)</div>
          <div class="summary-value score-good">
            {{ summary.eval_good_count || 0 }}
          </div>
        </div>
        <div class="card summary-card">
          <div class="summary-label">较差 (<5分)</div>
          <div class="summary-value" :class="summary.eval_bad_count > 0 ? 'score-bad' : ''">
            {{ summary.eval_bad_count || 0 }}
          </div>
        </div>
        <div class="card summary-card">
          <div class="summary-label">总评测次数</div>
          <div class="summary-value">
            {{ summary.scored_count || 0 }}
          </div>
        </div>
      </div>

      <!-- Stats Row -->
      <div class="stats-row">
        <div class="card stat-card">
          <span class="stat-num">{{ summary.scored_count || 0 }}</span>
          <span class="stat-label">已评估</span>
        </div>
        <div class="card stat-card">
          <span class="stat-num">{{ summary.total_feedback || 0 }}</span>
          <span class="stat-label">总反馈</span>
        </div>
        <div class="card stat-card stat-card-warn">
          <span class="stat-num">{{ summary.low_quality_count || 0 }}</span>
          <span class="stat-label">低分产出</span>
        </div>
      </div>

      <!-- Trend Chart -->
      <div class="card chart-card">
        <div class="card-header">
          <h3>质量趋势</h3>
          <select v-model="trendDays" @change="loadTrend" class="day-select">
            <option :value="7">近 7 天</option>
            <option :value="30">近 30 天</option>
            <option :value="90">近 90 天</option>
          </select>
        </div>
        <div v-if="trend.length" class="trend-chart">
          <div class="trend-bars">
            <div v-for="item in trend" :key="item.day" class="trend-bar-group">
              <div class="trend-bars-inner">
                <div class="trend-bar bar-accuracy" :style="{ height: barHeight(item.avg_accuracy) }" :title="`数据: ${item.avg_accuracy?.toFixed(1)}`"></div>
                <div class="trend-bar bar-logic" :style="{ height: barHeight(item.avg_logic) }" :title="`逻辑: ${item.avg_logic?.toFixed(1)}`"></div>
                <div class="trend-bar bar-action" :style="{ height: barHeight(item.avg_actionability) }" :title="`可执行: ${item.avg_actionability?.toFixed(1)}`"></div>
              </div>
              <div class="trend-date">{{ item.day?.slice(5) }}</div>
            </div>
          </div>
          <div class="trend-legend">
            <span class="legend-item"><i class="legend-dot bar-accuracy"></i> 数据</span>
            <span class="legend-item"><i class="legend-dot bar-logic"></i> 逻辑</span>
            <span class="legend-item"><i class="legend-dot bar-action"></i> 可执行</span>
          </div>
        </div>
        <div v-else class="empty-state" style="padding:2rem">
          <p>暂无趋势数据</p>
        </div>
      </div>

      <!-- Agent Performance -->
      <div class="card">
        <div class="card-header">
          <h3>🤖 Agent 评分对比</h3>
        </div>
        <div v-if="agentStats.length" class="agent-grid">
          <div v-for="agent in agentStats" :key="agent.analysis_type" class="agent-card">
            <div class="agent-header">
              <span class="agent-name">{{ agent.agent_name }}</span>
              <span class="agent-score" :style="{ color: scoreColor(agent.avg_score) }">
                {{ agent.avg_score?.toFixed(1) || '-' }}
              </span>
            </div>
            <div class="agent-bar">
              <div class="agent-bar-fill" :style="{ width: (agent.avg_score * 10) + '%', background: scoreColor(agent.avg_score) }"></div>
            </div>
            <div class="agent-meta">
              <span>{{ agent.case_count }} 用例</span>
              <span>{{ agent.run_count }} 次运行</span>
              <span v-if="agent.good_count" class="text-success">{{ agent.good_count }} 优秀</span>
              <span v-if="agent.bad_count" class="text-danger">{{ agent.bad_count }} 较差</span>
            </div>
          </div>
        </div>
        <div v-else class="empty-state" style="padding:2rem">
          <p>暂无评测数据</p>
        </div>
      </div>

      <!-- Agent Runtime Performance -->
      <div class="card">
        <div class="card-header">
          <h3>⚡ Agent 运行监控</h3>
          <div class="card-header-actions">
            <select v-model="perfDays" @change="refreshPerf" class="day-select">
              <option :value="7">近 7 天</option>
              <option :value="30">近 30 天</option>
              <option :value="90">近 90 天</option>
            </select>
          </div>
        </div>

        <!-- 概览卡片 -->
        <div class="perf-overview">
          <div class="perf-stat-card">
            <span class="perf-stat-value">{{ perfStats.total_runs }}</span>
            <span class="perf-stat-label">总调用</span>
          </div>
          <div class="perf-stat-card">
            <span class="perf-stat-value">{{ formatDuration(perfStats.avg_duration_ms) }}</span>
            <span class="perf-stat-label">平均耗时</span>
          </div>
          <div class="perf-stat-card">
            <span class="perf-stat-value" :class="perfStats.success_rate >= 90 ? 'text-success' : perfStats.success_rate >= 70 ? '' : 'text-danger'">
              {{ perfStats.success_rate }}%
            </span>
            <span class="perf-stat-label">成功率</span>
          </div>
          <div class="perf-stat-card">
            <span class="perf-stat-value" :class="perfStats.slow_calls > 0 ? 'text-danger' : ''">{{ perfStats.slow_calls }}</span>
            <span class="perf-stat-label">慢调用(&gt;30s)</span>
          </div>
        </div>

        <!-- 按 Agent 分组表格 -->
        <div v-if="perfByAgent.length" class="perf-agent-table">
          <div class="perf-agent-header">
            <span class="col-name">Agent</span>
            <span class="col-runs">调用</span>
            <span class="col-avg">平均耗时</span>
            <span class="col-max">最大耗时</span>
            <span class="col-rate">成功率</span>
            <span class="col-slow">慢调用</span>
          </div>
          <div v-for="a in perfByAgent" :key="a.agent_key" class="perf-agent-row">
            <span class="col-name">{{ a.agent_name }}</span>
            <span class="col-runs">{{ a.runs }}</span>
            <span class="col-avg">{{ formatDuration(a.avg_duration_ms) }}</span>
            <span class="col-max">{{ formatDuration(a.max_duration_ms) }}</span>
            <span class="col-rate">
              <span class="rate-bar">
                <span class="rate-bar-fill" :style="{ width: a.success_rate + '%', background: a.success_rate >= 90 ? '#10b981' : a.success_rate >= 70 ? '#f59e0b' : '#ef4444' }"></span>
              </span>
              <span :class="a.success_rate >= 90 ? 'text-success' : a.success_rate >= 70 ? '' : 'text-danger'">{{ a.success_rate }}%</span>
            </span>
            <span class="col-slow" :class="a.slow_calls > 0 ? 'text-danger' : ''">{{ a.slow_calls }}</span>
          </div>
        </div>
        <div v-else class="empty-state" style="padding:2rem">
          <p>暂无运行数据</p>
        </div>
      </div>

      <!-- 对话质量评估 -->
      <div class="card">
        <div class="card-header">
          <h3>💬 对话质量评估</h3>
          <div class="card-header-actions">
            <select v-model="convEvalDays" @change="loadConvEvalStats" class="day-select">
              <option :value="7">近 7 天</option>
              <option :value="30">近 30 天</option>
              <option :value="90">近 90 天</option>
            </select>
          </div>
        </div>

        <!-- 对话质量概览 -->
        <div v-if="convEvalStats" class="conv-eval-summary">
          <div class="conv-eval-stats-grid">
            <div class="conv-eval-stat">
              <div class="conv-eval-stat-value">{{ convEvalStats.total_evals || 0 }}</div>
              <div class="conv-eval-stat-label">总评估数</div>
            </div>
            <div class="conv-eval-stat">
              <div class="conv-eval-stat-value" :class="scoreClass(convEvalStats.avg_auto_score / 10)">
                {{ convEvalStats.avg_auto_score?.toFixed(0) || '-' }}
              </div>
              <div class="conv-eval-stat-label">平均分</div>
            </div>
            <div class="conv-eval-stat">
              <div class="conv-eval-stat-value score-good">{{ convEvalStats.high_score_count || 0 }}</div>
              <div class="conv-eval-stat-label">高质量 (≥80)</div>
            </div>
            <div class="conv-eval-stat">
              <div class="conv-eval-stat-value" :class="convEvalStats.low_score_count > 0 ? 'score-bad' : ''">
                {{ convEvalStats.low_score_count || 0 }}
              </div>
              <div class="conv-eval-stat-label">低质量 (<60)</div>
            </div>
          </div>

          <!-- 按复杂度统计 -->
          <div v-if="convEvalStats.by_complexity?.length" class="conv-eval-complexity">
            <h4>按复杂度分布</h4>
            <div class="complexity-bars">
              <div v-for="item in convEvalStats.by_complexity" :key="item.complexity" class="complexity-bar-item">
                <div class="complexity-label">{{ complexityLabel(item.complexity) }}</div>
                <div class="complexity-bar">
                  <div
                    class="complexity-bar-fill"
                    :style="{ width: (item.avg_score || 0) + '%', backgroundColor: scoreColor(item.avg_score) }"
                  ></div>
                </div>
                <div class="complexity-score">{{ item.avg_score?.toFixed(0) || '-' }}</div>
                <div class="complexity-count">{{ item.count }}次</div>
              </div>
            </div>
          </div>

          <!-- 对话质量趋势 -->
          <div v-if="convEvalStats.trend?.length" class="conv-eval-trend">
            <h4>每日趋势</h4>
            <div class="trend-mini-chart">
              <div v-for="item in convEvalStats.trend" :key="item.date" class="trend-mini-bar-group">
                <div class="trend-mini-bar" :style="{ height: barHeight(item.avg_score / 10) }"></div>
                <div class="trend-mini-date">{{ item.date?.slice(5) }}</div>
              </div>
            </div>
          </div>
        </div>

        <div v-else class="empty-state" style="padding:2rem">
          <p>暂无对话质量评估数据</p>
        </div>
      </div>

      <!-- 进化效果统计 -->
      <div class="card">
        <div class="card-header">
          <h3>🔄 进化效果统计</h3>
          <button class="btn-text" @click="loadEvolutionStats">刷新</button>
        </div>

        <div v-if="evolutionStats" class="evolution-summary">
          <!-- 概览卡片 -->
          <div class="evolution-stats-grid">
            <div class="evolution-stat">
              <div class="evolution-stat-value">{{ evolutionStats.low_score_count || 0 }}</div>
              <div class="evolution-stat-label">低分对话</div>
            </div>
            <div class="evolution-stat">
              <div class="evolution-stat-value">{{ evolutionStats.feedback_count || 0 }}</div>
              <div class="evolution-stat-label">自动反馈</div>
            </div>
            <div class="evolution-stat">
              <div class="evolution-stat-value">{{ evolutionStats.suggestion_count || 0 }}</div>
              <div class="evolution-stat-label">高分建议</div>
            </div>
            <div class="evolution-stat">
              <div class="evolution-stat-value" :class="evolutionStats.alert_count > 0 ? 'score-bad' : ''">
                {{ evolutionStats.alert_count || 0 }}
              </div>
              <div class="evolution-stat-label">专家告警</div>
            </div>
          </div>

          <!-- 质量趋势 -->
          <div v-if="evolutionStats.trend?.length" class="evolution-trend">
            <h4>质量趋势</h4>
            <div class="trend-mini-chart">
              <div v-for="item in evolutionStats.trend" :key="item.date" class="trend-mini-bar-group">
                <div class="trend-mini-bar" :style="{ height: barHeight(item.avg_score / 10) }"></div>
                <div class="trend-mini-date">{{ item.date?.slice(5) }}</div>
              </div>
            </div>
          </div>

          <!-- 专家表现 -->
          <div v-if="evolutionStats.expert_stats?.length" class="evolution-experts">
            <h4>专家表现</h4>
            <div class="expert-alert-list">
              <div v-for="expert in evolutionStats.expert_stats" :key="expert.agent_key" class="expert-alert-item">
                <span class="expert-alert-name">{{ expert.agent_name }}</span>
                <span class="expert-alert-rate" :class="expert.avg_success_rate < 0.8 ? 'score-bad' : ''">
                  {{ (expert.avg_success_rate * 100).toFixed(0) }}% 成功率
                </span>
                <span class="expert-alert-count">{{ expert.alert_count }} 次告警</span>
              </div>
            </div>
          </div>
        </div>

        <div v-else class="empty-state" style="padding:2rem">
          <p>暂无进化统计数据</p>
        </div>
      </div>

      <!-- 评估建议 -->
      <div class="card">
        <div class="card-header">
          <h3>💡 评估建议</h3>
          <button class="btn-text" @click="loadEvalSuggestions">刷新</button>
        </div>

        <div v-if="evalSuggestions.length" class="suggestion-list">
          <div v-for="item in evalSuggestions" :key="item.id" class="suggestion-item">
            <div class="suggestion-header">
              <span class="suggestion-title">{{ item.name }}</span>
              <span class="suggestion-score" :style="{ color: scoreColor(item.auto_score) }">
                {{ item.auto_score?.toFixed(0) }}分
              </span>
            </div>
            <div class="suggestion-meta">
              <span class="suggestion-conv">对话 #{{ item.conversation_id }}</span>
              <span class="suggestion-type">{{ item.analysis_type }}</span>
            </div>
            <div v-if="item.expected_quality" class="suggestion-quality">
              期望质量: {{ item.expected_quality }}
            </div>
            <div class="suggestion-actions">
              <button class="btn-accept" @click="handleAcceptSuggestion(item.id)">接受</button>
              <button class="btn-reject" @click="handleRejectSuggestion(item.id)">拒绝</button>
            </div>
          </div>
        </div>

        <div v-else class="empty-state" style="padding:2rem">
          <p>暂无评估建议</p>
        </div>
      </div>

      <!-- 最近对话评估列表 -->
      <div class="card">
        <div class="card-header">
          <h3>📋 最近对话评估</h3>
          <button class="btn-text" @click="loadConvEvaluations">刷新</button>
        </div>
        <div v-if="convEvaluations.length" class="conv-eval-list">
          <div v-for="item in convEvaluations" :key="item.id" class="conv-eval-item">
            <div class="conv-eval-item-header">
              <span class="conv-eval-item-title">{{ item.conversation_title || '未命名对话' }}</span>
              <span class="conv-eval-item-score" :style="{ color: scoreColor(item.auto_score) }">
                {{ item.auto_score?.toFixed(0) || '-' }}
              </span>
            </div>
            <div class="conv-eval-item-meta">
              <span class="conv-eval-item-complexity">{{ complexityLabel(item.complexity) }}</span>
              <span class="conv-eval-item-specialists">{{ item.specialist_count }}个专家</span>
              <span class="conv-eval-item-time">{{ item.created_at?.slice(5, 16) }}</span>
            </div>
            <div v-if="item.user_score" class="conv-eval-item-user-score">
              用户评分: {{ item.user_score }}/5
            </div>
          </div>
        </div>
        <div v-else class="empty-state" style="padding:2rem">
          <p>暂无对话评估记录</p>
        </div>
      </div>

      <!-- Low Quality List -->
      <div class="card">
        <div class="card-header">
          <h3>🚨 低分产出（Bad Cases）</h3>
          <span class="badge badge-danger">{{ lowQualityItems.length }}</span>
        </div>
        <div v-if="lowQualityItems.length" class="lq-list">
          <div v-for="item in lowQualityItems" :key="item.id" class="lq-item">
            <div class="lq-header">
              <span class="lq-caller">{{ item.caller }}</span>
              <span class="lq-score">{{ item.overall_score?.toFixed(1) }} 分</span>
              <span class="lq-time">{{ item.created_at?.slice(5, 16) }}</span>
            </div>
            <div class="lq-scores">
              <span :class="dimClass(item.score_data_accuracy)">数据{{ item.score_data_accuracy }}</span>
              <span :class="dimClass(item.score_logic)">逻辑{{ item.score_logic }}</span>
              <span :class="dimClass(item.score_actionability)">可执行{{ item.score_actionability }}</span>
            </div>
            <div v-if="item.input_summary" class="lq-input">{{ item.input_summary }}</div>
            <div v-if="item.comment" class="lq-comment">💬 {{ item.comment }}</div>
          </div>
        </div>
        <div v-else class="empty-state" style="padding:2rem">
          <p>暂无低分产出 👍</p>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { getQualitySummary, getQualityTrend, getLowQualityItems, getEvalStatsByAgent, getPerformanceStats, getPerformanceByAgent, getConversationEvalStats, listConversationEvaluations, getEvolutionStats, listEvalSuggestions, acceptEvalSuggestion, rejectEvalSuggestion, getExpertAlerts } from '../api'

const loading = ref(true)
const summary = ref({})
const trend = ref([])
const trendDays = ref(30)
const lowQualityItems = ref([])
const agentStats = ref([])

// Agent 运行时性能监控
const perfStats = ref({ total_runs: 0, avg_duration_ms: 0, max_duration_ms: 0, slow_calls: 0, unique_agents: 0, success_count: 0, error_count: 0, success_rate: 0 })
const perfByAgent = ref([])
const perfDays = ref(7)

// 对话质量评估
const convEvalStats = ref(null)
const convEvaluations = ref([])
const convEvalDays = ref(30)

// 进化系统
const evolutionStats = ref(null)
const evalSuggestions = ref([])
const expertAlerts = ref([])

onMounted(async () => {
  await Promise.all([
    loadSummary(), loadTrend(), loadLowQuality(), loadAgentStats(),
    loadPerfStats(), loadPerfByAgent(),
    loadConvEvalStats(), loadConvEvaluations(),
    loadEvolutionStats(), loadEvalSuggestions(), loadExpertAlerts(),
  ])
  loading.value = false
})

async function loadSummary() {
  try {
    const { data } = await getQualitySummary(30)
    summary.value = data
  } catch (e) {
    console.error('Load quality summary failed:', e)
  }
}

async function loadTrend() {
  try {
    const { data } = await getQualityTrend(trendDays.value)
    trend.value = data.trend || []
  } catch (e) {
    console.error('Load quality trend failed:', e)
  }
}

async function loadLowQuality() {
  try {
    const { data } = await getLowQualityItems(20)
    lowQualityItems.value = data.items || []
  } catch (e) {
    console.error('Load low quality items failed:', e)
  }
}

async function loadAgentStats() {
  try {
    const { data } = await getEvalStatsByAgent()
    agentStats.value = data.agents || []
  } catch (e) {
    console.error('Load agent stats failed:', e)
  }
}

async function loadPerfStats() {
  try {
    const { data } = await getPerformanceStats(perfDays.value)
    perfStats.value = data
  } catch (e) {
    console.error('Load performance stats failed:', e)
  }
}

async function loadPerfByAgent() {
  try {
    const { data } = await getPerformanceByAgent(perfDays.value)
    perfByAgent.value = data.items || []
  } catch (e) {
    console.error('Load performance by agent failed:', e)
  }
}

async function loadConvEvalStats() {
  try {
    const { data } = await getConversationEvalStats()
    convEvalStats.value = data.stats || null
  } catch (e) {
    console.error('Load conversation eval stats failed:', e)
  }
}

async function loadConvEvaluations() {
  try {
    const { data } = await listConversationEvaluations(20)
    convEvaluations.value = data.evaluations || []
  } catch (e) {
    console.error('Load conversation evaluations failed:', e)
  }
}

async function loadEvolutionStats() {
  try {
    const { data } = await getEvolutionStats(30)
    evolutionStats.value = data.stats || null
  } catch (e) {
    console.error('Load evolution stats failed:', e)
  }
}

async function loadEvalSuggestions() {
  try {
    const { data } = await listEvalSuggestions('pending', 10)
    evalSuggestions.value = data.suggestions || []
  } catch (e) {
    console.error('Load eval suggestions failed:', e)
  }
}

async function loadExpertAlerts() {
  try {
    const { data } = await getExpertAlerts(7, 10)
    expertAlerts.value = data.alerts || []
  } catch (e) {
    console.error('Load expert alerts failed:', e)
  }
}

async function handleAcceptSuggestion(suggestionId) {
  try {
    const { data } = await acceptEvalSuggestion(suggestionId)
    if (data.ok) {
      // 移除已接受的建议
      evalSuggestions.value = evalSuggestions.value.filter(s => s.id !== suggestionId)
    }
  } catch (e) {
    console.error('Accept suggestion failed:', e)
  }
}

async function handleRejectSuggestion(suggestionId) {
  try {
    const { data } = await rejectEvalSuggestion(suggestionId)
    if (data.ok) {
      // 移除已拒绝的建议
      evalSuggestions.value = evalSuggestions.value.filter(s => s.id !== suggestionId)
    }
  } catch (e) {
    console.error('Reject suggestion failed:', e)
  }
}

function refreshPerf() {
  loadPerfStats()
  loadPerfByAgent()
}

function formatDuration(ms) {
  if (!ms) return '0ms'
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  return `${Math.floor(ms / 60000)}m${Math.floor((ms % 60000) / 1000)}s`
}

function scoreClass(score) {
  if (!score) return ''
  if (score >= 7) return 'score-good'
  if (score >= 5) return 'score-ok'
  return 'score-bad'
}

function scoreColor(score) {
  if (!score || score <= 0) return 'var(--color-text-muted)'
  // 支持 0-100 分制（对话质量评估）
  if (score > 10) {
    if (score >= 80) return '#10b981'
    if (score >= 60) return '#f59e0b'
    if (score >= 40) return '#f97316'
    return '#ef4444'
  }
  // 0-10 分制（原有评估）
  if (score >= 8) return '#10b981'
  if (score >= 6) return '#22c55e'
  if (score >= 4) return '#f59e0b'
  if (score >= 2) return '#f97316'
  return '#ef4444'
}

function complexityLabel(complexity) {
  const labels = {
    simple: '简单',
    medium: '中等',
    complex: '复杂',
    chat: '闲聊',
  }
  return labels[complexity] || complexity || '未知'
}

function dimClass(score) {
  if (!score) return ''
  if (score >= 7) return 'dim-good'
  if (score >= 5) return 'dim-ok'
  return 'dim-bad'
}

function barHeight(score) {
  if (!score) return '0%'
  return `${(score / 10) * 100}%`
}
</script>

<style scoped>
.quality-page {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}

.page-header {
  margin-bottom: 0.5rem;
}

.page-title {
  font-size: 1.5rem;
  font-weight: 700;
  color: var(--color-text-primary);
}

.page-desc {
  font-size: 0.85rem;
  color: var(--color-text-muted);
  margin-top: 0.35rem;
}

/* Summary Grid */
.summary-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 1rem;
}

.summary-card {
  text-align: center;
  padding: 1.25rem 1rem;
}

.summary-label {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  margin-bottom: 0.5rem;
}

.summary-value {
  font-size: 2rem;
  font-weight: 700;
}

.summary-sub {
  font-size: 0.75rem;
  color: var(--color-text-muted);
}

.score-good { color: #16a34a; }
.score-ok { color: #ca8a04; }
.score-bad { color: #dc2626; }

/* Stats Row */
.stats-row {
  display: flex;
  gap: 1rem;
}

.stat-card {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 1rem 1.25rem;
}

.stat-num {
  font-size: 1.5rem;
  font-weight: 700;
  color: var(--color-primary-500);
}

.stat-card-warn .stat-num {
  color: #dc2626;
}

.stat-label {
  font-size: 0.8rem;
  color: var(--color-text-muted);
}

/* Chart */
.chart-card {
  padding: 1.25rem;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 1rem;
}

.card-header h3 {
  font-size: 1rem;
  font-weight: 600;
}

.day-select {
  padding: 0.45rem 0.85rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  font-size: 0.8rem;
  background: var(--color-bg-primary);
}

.trend-chart {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.trend-bars {
  display: flex;
  gap: 0.35rem;
  height: 150px;
  align-items: flex-end;
}

.trend-bar-group {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  height: 100%;
}

.trend-bars-inner {
  flex: 1;
  display: flex;
  gap: 2px;
  align-items: flex-end;
  width: 100%;
}

.trend-bar {
  flex: 1;
  min-height: 2px;
  border-radius: 2px 2px 0 0;
  transition: height 0.3s;
}

.bar-accuracy { background: #3b82f6; }
.bar-logic { background: #8b5cf6; }
.bar-action { background: #f59e0b; }

.trend-date {
  font-size: 0.65rem;
  color: var(--color-text-muted);
  margin-top: 0.35rem;
}

.trend-legend {
  display: flex;
  gap: 1rem;
  justify-content: center;
}

.legend-item {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  font-size: 0.75rem;
  color: var(--color-text-muted);
}

.legend-dot {
  width: 10px;
  height: 10px;
  border-radius: 2px;
  display: inline-block;
}

/* Agent Grid */
.agent-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 0.75rem;
}

.agent-card {
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: 1rem;
}

.agent-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 0.5rem;
}

.agent-name {
  font-weight: 600;
  font-size: 0.9rem;
}

.agent-score {
  font-size: 1.5rem;
  font-weight: 700;
}

.agent-bar {
  height: 6px;
  background: var(--color-border-light);
  border-radius: 3px;
  overflow: hidden;
  margin-bottom: 0.5rem;
}

.agent-bar-fill {
  height: 100%;
  border-radius: 3px;
  transition: width 0.5s ease;
}

.agent-meta {
  display: flex;
  gap: 0.75rem;
  font-size: 0.75rem;
  color: var(--color-text-muted);
}

.text-success { color: #16a34a; }
.text-danger { color: #dc2626; }

/* Low Quality List */
.lq-list {
  display: flex;
  flex-direction: column;
}

.lq-item {
  padding: 0.85rem 1rem;
  border-bottom: 1px solid var(--color-border-light);
}

.lq-item:last-child {
  border-bottom: none;
}

.lq-header {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 0.5rem;
}

.lq-caller {
  font-size: 0.75rem;
  font-weight: 600;
  padding: 0.15rem 0.5rem;
  background: var(--color-bg-secondary);
  border-radius: 4px;
}

.lq-score {
  font-weight: 700;
  color: #dc2626;
  font-size: 0.85rem;
}

.lq-time {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  margin-left: auto;
}

.lq-scores {
  display: flex;
  gap: 0.75rem;
  font-size: 0.75rem;
  margin-bottom: 0.5rem;
}

.dim-good { color: #16a34a; }
.dim-ok { color: #ca8a04; }
.dim-bad { color: #dc2626; }

.lq-input {
  font-size: 0.8rem;
  color: var(--color-text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.lq-comment {
  font-size: 0.8rem;
  color: var(--color-text-muted);
  margin-top: 0.35rem;
}

/* ── Agent 运行监控 ── */

.card-header-actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.day-select {
  padding: 0.45rem 0.85rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  font-size: 0.8rem;
  background: var(--color-bg-primary);
}

.perf-overview {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 0.75rem;
  margin-bottom: 1.25rem;
}

.perf-stat-card {
  text-align: center;
  padding: 1rem 0.75rem;
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.perf-stat-value {
  display: block;
  font-size: 1.5rem;
  font-weight: 700;
  color: var(--color-primary-500);
}

.perf-stat-label {
  display: block;
  font-size: 0.72rem;
  color: var(--color-text-muted);
  margin-top: 0.35rem;
}

.perf-agent-table {
  display: flex;
  flex-direction: column;
  font-size: 0.8rem;
}

.perf-agent-header {
  display: flex;
  align-items: center;
  padding: 0.5rem 0;
  border-bottom: 2px solid var(--color-border);
  font-weight: 600;
  color: var(--color-text-muted);
  font-size: 0.75rem;
}

.perf-agent-row {
  display: flex;
  align-items: center;
  padding: 0.6rem 0;
  border-bottom: 1px solid var(--color-border-light);
}

.perf-agent-row:last-child {
  border-bottom: none;
}

.col-name { flex: 2; font-weight: 500; }
.col-runs { flex: 1; text-align: center; }
.col-avg { flex: 1; text-align: center; }
.col-max { flex: 1; text-align: center; }
.col-rate { flex: 2; display: flex; align-items: center; gap: 0.5rem; }
.col-slow { flex: 1; text-align: center; }

.rate-bar {
  flex: 1;
  height: 6px;
  background: var(--color-border-light);
  border-radius: 3px;
  overflow: hidden;
}

.rate-bar-fill {
  height: 100%;
  border-radius: 3px;
  transition: width 0.5s ease;
}

/* ── 对话质量评估样式 ── */

.conv-eval-summary {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}

.conv-eval-stats-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 0.75rem;
}

.conv-eval-stat {
  text-align: center;
  padding: 1rem 0.75rem;
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.conv-eval-stat-value {
  font-size: 1.5rem;
  font-weight: 700;
  color: var(--color-text-primary);
}

.conv-eval-stat-label {
  font-size: 0.72rem;
  color: var(--color-text-muted);
  margin-top: 0.35rem;
}

.conv-eval-complexity h4,
.conv-eval-trend h4 {
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--color-text-secondary);
  margin-bottom: 0.75rem;
}

.complexity-bars {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.complexity-bar-item {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.complexity-label {
  width: 40px;
  font-size: 0.75rem;
  color: var(--color-text-muted);
}

.complexity-bar {
  flex: 1;
  height: 8px;
  background: var(--color-border-light);
  border-radius: 4px;
  overflow: hidden;
}

.complexity-bar-fill {
  height: 100%;
  border-radius: 4px;
  transition: width 0.5s ease;
}

.complexity-score {
  width: 35px;
  text-align: right;
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--color-text-primary);
}

.complexity-count {
  width: 40px;
  text-align: right;
  font-size: 0.72rem;
  color: var(--color-text-muted);
}

.conv-eval-trend {
  margin-top: 0.5rem;
}

.trend-mini-chart {
  display: flex;
  gap: 4px;
  height: 60px;
  align-items: flex-end;
}

.trend-mini-bar-group {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  height: 100%;
}

.trend-mini-bar {
  width: 100%;
  background: var(--color-primary-500);
  border-radius: 2px 2px 0 0;
  transition: height 0.3s ease;
  min-height: 2px;
}

.trend-mini-date {
  font-size: 0.6rem;
  color: var(--color-text-muted);
  margin-top: 4px;
}

/* 对话评估列表 */
.conv-eval-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.conv-eval-item {
  padding: 0.75rem;
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.conv-eval-item-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 0.5rem;
}

.conv-eval-item-title {
  font-size: 0.85rem;
  font-weight: 500;
  color: var(--color-text-primary);
}

.conv-eval-item-score {
  font-size: 1.1rem;
  font-weight: 700;
}

.conv-eval-item-meta {
  display: flex;
  gap: 0.75rem;
  font-size: 0.72rem;
  color: var(--color-text-muted);
}

.conv-eval-item-complexity {
  padding: 1px 6px;
  background: var(--color-primary-100);
  color: var(--color-primary-700);
  border-radius: 4px;
}

.conv-eval-item-user-score {
  margin-top: 0.5rem;
  font-size: 0.75rem;
  color: var(--color-text-secondary);
}

/* ── 进化效果统计样式 ── */

.evolution-summary {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}

.evolution-stats-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 0.75rem;
}

.evolution-stat {
  text-align: center;
  padding: 1rem 0.75rem;
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.evolution-stat-value {
  font-size: 1.5rem;
  font-weight: 700;
  color: var(--color-text-primary);
}

.evolution-stat-label {
  font-size: 0.72rem;
  color: var(--color-text-muted);
  margin-top: 0.35rem;
}

.evolution-trend h4,
.evolution-experts h4 {
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--color-text-secondary);
  margin-bottom: 0.75rem;
}

.evolution-trend {
  margin-top: 0.5rem;
}

.expert-alert-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.expert-alert-item {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.5rem;
  background: var(--color-bg-secondary);
  border-radius: var(--radius-sm);
}

.expert-alert-name {
  flex: 1;
  font-size: 0.85rem;
  font-weight: 500;
  color: var(--color-text-primary);
}

.expert-alert-rate {
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--color-text-secondary);
}

.expert-alert-count {
  font-size: 0.75rem;
  color: var(--color-text-muted);
}

/* ── 评估建议样式 ── */

.suggestion-list {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.suggestion-item {
  padding: 0.75rem;
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.suggestion-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 0.5rem;
}

.suggestion-title {
  font-size: 0.85rem;
  font-weight: 500;
  color: var(--color-text-primary);
}

.suggestion-score {
  font-size: 1.1rem;
  font-weight: 700;
}

.suggestion-meta {
  display: flex;
  gap: 0.75rem;
  font-size: 0.72rem;
  color: var(--color-text-muted);
  margin-bottom: 0.5rem;
}

.suggestion-quality {
  font-size: 0.75rem;
  color: var(--color-text-secondary);
  margin-bottom: 0.5rem;
  padding: 0.5rem;
  background: var(--color-bg-primary);
  border-radius: var(--radius-sm);
}

.suggestion-actions {
  display: flex;
  gap: 0.5rem;
  justify-content: flex-end;
}

.btn-accept,
.btn-reject {
  padding: 0.45rem 0.85rem;
  border: none;
  border-radius: 4px;
  font-size: 0.75rem;
  cursor: pointer;
  transition: background 0.2s;
}

.btn-accept {
  background: var(--color-primary-500);
  color: white;
}

.btn-accept:hover {
  background: var(--color-primary-600);
}

.btn-reject {
  background: var(--color-bg-primary);
  color: var(--color-text-secondary);
  border: 1px solid var(--color-border);
}

.btn-reject:hover {
  background: var(--color-bg-secondary);
}

/* Responsive */
@media (max-width: 768px) {
  .summary-grid {
    grid-template-columns: repeat(2, 1fr);
  }
  .stats-row {
    flex-wrap: wrap;
  }
  .perf-overview {
    grid-template-columns: repeat(2, 1fr);
  }
  .col-max {
    display: none;
  }
  .conv-eval-stats-grid {
    grid-template-columns: repeat(2, 1fr);
  }
  .evolution-stats-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}
</style>
