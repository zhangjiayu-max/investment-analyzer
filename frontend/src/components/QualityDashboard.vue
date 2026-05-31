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
import { getQualitySummary, getQualityTrend, getLowQualityItems, getEvalStatsByAgent } from '../api'

const loading = ref(true)
const summary = ref({})
const trend = ref([])
const trendDays = ref(30)
const lowQualityItems = ref([])
const agentStats = ref([])

onMounted(async () => {
  await Promise.all([loadSummary(), loadTrend(), loadLowQuality(), loadAgentStats()])
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

function scoreClass(score) {
  if (!score) return ''
  if (score >= 7) return 'score-good'
  if (score >= 5) return 'score-ok'
  return 'score-bad'
}

function scoreColor(score) {
  if (!score || score <= 0) return 'var(--color-text-muted)'
  if (score >= 8) return '#10b981'
  if (score >= 6) return '#22c55e'
  if (score >= 4) return '#f59e0b'
  if (score >= 2) return '#f97316'
  return '#ef4444'
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
  margin-top: 0.25rem;
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
  padding: 0.35rem 0.75rem;
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
  margin-top: 0.25rem;
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
  margin-bottom: 0.35rem;
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
  margin-bottom: 0.35rem;
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
  margin-top: 0.25rem;
}

/* Responsive */
@media (max-width: 768px) {
  .summary-grid {
    grid-template-columns: repeat(2, 1fr);
  }
  .stats-row {
    flex-wrap: wrap;
  }
}
</style>
