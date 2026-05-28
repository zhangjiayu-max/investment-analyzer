<script setup>
import { ref, onMounted } from 'vue'
import { getRagStats, getRagLogs, getAuthorArticle } from '../api'

const stats = ref(null)
const logs = ref([])
const loading = ref(false)
const selectedLog = ref(null)
const selectedResult = ref(null)
const activeTab = ref('stats')

onMounted(() => {
  loadStats()
  loadLogs()
})

async function loadStats() {
  loading.value = true
  try {
    const { data } = await getRagStats(30)
    stats.value = data
  } catch (e) {
    console.error('Failed to load RAG stats:', e)
  } finally {
    loading.value = false
  }
}

async function loadLogs() {
  try {
    const { data } = await getRagLogs(100)
    logs.value = data.logs || []
  } catch (e) {
    console.error('Failed to load RAG logs:', e)
  }
}

function formatDate(ts) {
  if (!ts) return ''
  return ts.replace('T', ' ').slice(0, 19)
}

function typeColor(type) {
  const map = {
    '估值': 'warning',
    '作者文章': 'success',
    '技能知识': 'purple',
    '文章': 'info',
    '分析记录': 'neutral',
  }
  return map[type] || 'neutral'
}

async function viewResultDetail(result) {
  selectedResult.value = result
}

function closeDetail() {
  selectedResult.value = null
}
</script>

<template>
  <div class="rag-page">
    <!-- Tab 切换 -->
    <div class="tab-bar">
      <button :class="['tab-btn', { active: activeTab === 'stats' }]" @click="activeTab = 'stats'">
        <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/>
        </svg>
        统计概览
      </button>
      <button :class="['tab-btn', { active: activeTab === 'logs' }]" @click="activeTab = 'logs'">
        <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"/>
        </svg>
        检索日志
      </button>
    </div>

    <!-- 统计概览 -->
    <div v-if="activeTab === 'stats'" class="stats-section">
      <div v-if="loading" class="loading-state">加载中...</div>
      <div v-else-if="stats" class="stats-grid">
        <!-- 核心指标 -->
        <div class="stat-card">
          <div class="stat-value">{{ stats.total }}</div>
          <div class="stat-label">总检索次数</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">{{ stats.avg_results }}</div>
          <div class="stat-label">平均命中数</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">{{ stats.top_keywords?.length || 0 }}</div>
          <div class="stat-label">独立关键词</div>
        </div>

        <!-- 热门关键词 -->
        <div class="stat-card wide">
          <h4 class="card-title">热门检索词</h4>
          <div class="keyword-cloud">
            <span v-for="kw in stats.top_keywords?.slice(0, 15)" :key="kw.keyword"
              :class="['keyword-tag', `size-${Math.min(Math.ceil(kw.count / 5), 4)}`]">
              {{ kw.keyword }}
              <span class="keyword-count">{{ kw.count }}</span>
            </span>
          </div>
        </div>

        <!-- 知识类型分布 -->
        <div class="stat-card">
          <h4 class="card-title">知识类型命中</h4>
          <div class="type-bars">
            <div v-for="t in stats.type_distribution" :key="t.type" class="type-bar-item">
              <div class="type-bar-label">{{ t.type }}</div>
              <div class="type-bar-track">
                <div class="type-bar-fill" :style="{ width: `${Math.min(t.count / (stats.total || 1) * 100, 100)}%` }"></div>
              </div>
              <div class="type-bar-count">{{ t.count }}</div>
            </div>
          </div>
        </div>

        <!-- 每日趋势 -->
        <div class="stat-card">
          <h4 class="card-title">近 7 天趋势</h4>
          <div class="daily-chart">
            <div v-for="d in stats.daily?.slice(0, 7)" :key="d.day" class="daily-bar">
              <div class="daily-bar-fill" :style="{ height: `${Math.min(d.count / 20 * 100, 100)}%` }"></div>
              <div class="daily-bar-label">{{ d.day?.slice(5) }}</div>
              <div class="daily-bar-count">{{ d.count }}</div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 检索日志 -->
    <div v-if="activeTab === 'logs'" class="logs-section">
      <div class="logs-list">
        <div v-for="log in logs" :key="log.id" class="log-item" @click="selectedLog = selectedLog?.id === log.id ? null : log">
          <div class="log-header">
            <span class="log-query">{{ log.query?.slice(0, 50) }}</span>
            <span class="log-time">{{ formatDate(log.created_at) }}</span>
          </div>
          <div class="log-meta">
            <span class="log-keywords">
              检索词: {{ log.keywords?.join(', ') || '-' }}
            </span>
            <span class="log-count">{{ log.results_count }} 条结果</span>
          </div>

          <!-- 展开详情 -->
          <div v-if="selectedLog?.id === log.id" class="log-detail">
            <div v-for="(r, i) in log.results" :key="i" class="log-result" @click="viewResultDetail(r)">
              <span :class="['result-type', `type-${typeColor(r.label)}`]">{{ r.label }}</span>
              <span class="result-title">{{ r.title }}</span>
              <span v-if="r.time" class="result-time">{{ r.time }}</span>
              <span class="result-preview">{{ r.body?.slice(0, 100) }}...</span>
              <svg class="result-arrow" width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/>
              </svg>
            </div>
          </div>
        </div>
        <div v-if="!logs.length" class="empty-state">暂无检索日志</div>
      </div>
    </div>

    <!-- 结果详情弹窗 -->
    <div v-if="selectedResult" class="detail-modal" @click.self="closeDetail">
      <div class="detail-panel">
        <div class="detail-header">
          <div class="detail-info">
            <span :class="['result-type', `type-${typeColor(selectedResult.label)}`]">{{ selectedResult.label }}</span>
            <h3 class="detail-title">{{ selectedResult.title }}</h3>
            <span class="detail-id">ID: {{ selectedResult.reference_id }}</span>
            <span v-if="selectedResult.time" class="detail-time">{{ selectedResult.time }}</span>
          </div>
          <button @click="closeDetail" class="btn-close">
            <svg width="20" height="20" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
            </svg>
          </button>
        </div>
        <div class="detail-content">
          <div class="content-body">{{ selectedResult.body }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.rag-page {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 120px);
}

.tab-bar {
  margin-bottom: 1rem;
}

/* Stats Section */
.stats-section {
  flex: 1;
  overflow-y: auto;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 1rem;
}

.stat-card {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: 1.25rem;
}

.stat-card.wide {
  grid-column: span 3;
}

.stat-value {
  font-size: 2rem;
  font-weight: 700;
  color: var(--color-primary-600);
}

.stat-label {
  font-size: 0.8rem;
  color: var(--color-text-muted);
  margin-top: 0.25rem;
}

.card-title {
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0 0 1rem;
}

/* Keyword Cloud */
.keyword-cloud {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.keyword-tag {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.3rem 0.6rem;
  background: var(--color-bg-input);
  border-radius: var(--radius-sm);
  font-size: 0.8rem;
  color: var(--color-text-primary);
}

.keyword-tag.size-1 { font-size: 0.75rem; }
.keyword-tag.size-2 { font-size: 0.85rem; }
.keyword-tag.size-3 { font-size: 0.95rem; font-weight: 500; }
.keyword-tag.size-4 { font-size: 1.05rem; font-weight: 600; }

.keyword-count {
  font-size: 0.65rem;
  color: var(--color-text-muted);
}

/* Type Bars */
.type-bars {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.type-bar-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.type-bar-label {
  width: 60px;
  font-size: 0.75rem;
  color: var(--color-text-secondary);
  text-align: right;
}

.type-bar-track {
  flex: 1;
  height: 8px;
  background: var(--color-bg-input);
  border-radius: 4px;
  overflow: hidden;
}

.type-bar-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--color-primary-400), var(--color-primary-600));
  border-radius: 4px;
  transition: width 0.3s;
}

.type-bar-count {
  width: 30px;
  font-size: 0.75rem;
  color: var(--color-text-muted);
}

/* Daily Chart */
.daily-chart {
  display: flex;
  align-items: flex-end;
  gap: 0.5rem;
  height: 120px;
}

.daily-bar {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  height: 100%;
  justify-content: flex-end;
}

.daily-bar-fill {
  width: 100%;
  min-height: 4px;
  background: linear-gradient(180deg, var(--color-primary-400), var(--color-primary-600));
  border-radius: 4px 4px 0 0;
  transition: height 0.3s;
}

.daily-bar-label {
  font-size: 0.6rem;
  color: var(--color-text-muted);
  margin-top: 0.3rem;
}

.daily-bar-count {
  font-size: 0.65rem;
  font-weight: 500;
  color: var(--color-text-secondary);
}

/* Logs Section */
.logs-section {
  flex: 1;
  overflow-y: auto;
}

.logs-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.log-item {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: 0.75rem 1rem;
  cursor: pointer;
  transition: all var(--transition-fast);
}

.log-item:hover {
  border-color: var(--color-primary-400);
}

.log-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 0.4rem;
}

.log-query {
  font-size: 0.85rem;
  font-weight: 500;
  color: var(--color-text-primary);
}

.log-time {
  font-size: 0.7rem;
  color: var(--color-text-muted);
}

.log-meta {
  display: flex;
  gap: 1rem;
  font-size: 0.75rem;
  color: var(--color-text-secondary);
}

.log-count {
  color: var(--color-primary-600);
  font-weight: 500;
}

/* Log Detail */
.log-detail {
  margin-top: 0.75rem;
  padding-top: 0.75rem;
  border-top: 1px solid var(--color-border);
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.log-result {
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
  padding: 0.5rem;
  background: var(--color-bg-input);
  border-radius: var(--radius-sm);
}

.result-type {
  display: inline-flex;
  padding: 0.15rem 0.4rem;
  border-radius: var(--radius-sm);
  font-size: 0.65rem;
  font-weight: 500;
  flex-shrink: 0;
}

.type-warning { background: var(--color-warning-bg); color: #d97706; }
.type-success { background: var(--color-success-bg); color: #059669; }
.type-purple { background: rgba(139, 92, 246, 0.1); color: #7c3aed; }
.type-info { background: var(--color-info-bg); color: #2563eb; }
.type-neutral { background: var(--color-bg-input); color: var(--color-text-muted); }

.dark .type-warning { color: #fbbf24; }
.dark .type-success { color: #34d399; }
.dark .type-purple { background: rgba(139, 92, 246, 0.15); color: #a78bfa; }
.dark .type-info { color: #60a5fa; }

.result-title {
  font-size: 0.8rem;
  font-weight: 500;
  color: var(--color-text-primary);
  flex-shrink: 0;
}

.result-time {
  font-size: 0.7rem;
  color: var(--color-text-muted);
  flex-shrink: 0;
  white-space: nowrap;
}

.result-preview {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.result-arrow {
  margin-left: auto;
  color: var(--color-text-muted);
  flex-shrink: 0;
}

.log-result {
  cursor: pointer;
}

.log-result:hover {
  background: var(--color-bg-hover);
}

/* Detail Modal */
.detail-modal {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: var(--z-modal);
  backdrop-filter: blur(4px);
}

.detail-panel {
  background: var(--color-bg-card);
  border-radius: var(--radius-lg);
  width: 90%;
  max-width: 700px;
  max-height: 80vh;
  display: flex;
  flex-direction: column;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
}

.detail-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  padding: 1.25rem;
  border-bottom: 1px solid var(--color-border);
}

.detail-info {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.detail-title {
  font-size: 1.1rem;
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0;
}

.detail-id {
  font-size: 0.75rem;
  color: var(--color-text-muted);
}

.detail-time {
  font-size: 0.75rem;
  color: var(--color-text-muted);
}

.btn-close {
  background: none;
  border: none;
  cursor: pointer;
  color: var(--color-text-muted);
  padding: 0.25rem;
}

.btn-close:hover {
  color: var(--color-text-primary);
}

.detail-content {
  flex: 1;
  overflow-y: auto;
  padding: 1.25rem;
}

.content-body {
  font-size: 0.85rem;
  line-height: 1.8;
  color: var(--color-text-primary);
  white-space: pre-wrap;
}

.empty-state {
  text-align: center;
  padding: 3rem;
  color: var(--color-text-muted);
  font-size: 0.85rem;
}

.loading-state {
  text-align: center;
  padding: 3rem;
  color: var(--color-text-muted);
}

/* Responsive */
@media (max-width: 768px) {
  .stats-grid {
    grid-template-columns: 1fr;
  }
  .stat-card.wide {
    grid-column: span 1;
  }
}
</style>
