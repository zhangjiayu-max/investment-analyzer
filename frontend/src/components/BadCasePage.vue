<script setup>
import { ref, onMounted, computed } from 'vue'
import { listBadCases } from '../api'

const cases = ref([])
const loading = ref(false)
const filterType = ref('')
const selectedCase = ref(null)

const analysisTypes = computed(() => {
  const types = new Set(cases.value.map(c => c.analysis_type).filter(Boolean))
  return ['', ...Array.from(types)]
})

const analysisTypeLabels = {
  panorama: '全景诊断',
  deep_dive: '单基金深度',
  trade_review: '交易复盘',
  what_if: '情景推演',
  ai: 'AI 分析',
  diversification_ai: '分散度分析',
}

function typeLabel(t) {
  return analysisTypeLabels[t] || t
}

const filteredCases = computed(() => {
  if (!filterType.value) return cases.value
  return cases.value.filter(c => c.analysis_type === filterType.value)
})

async function load() {
  loading.value = true
  try {
    const { data } = await listBadCases(filterType.value || '', 100)
    cases.value = data.cases || []
  } catch (e) {
    console.error(e)
  } finally {
    loading.value = false
  }
}

function selectCase(c) {
  selectedCase.value = c
}

onMounted(load)
</script>

<template>
  <div class="badcase-page">
    <div class="page-header">
      <div>
        <h2 class="page-title">Bad Case 分析看板</h2>
        <p class="page-desc">用户标记为「没用」的分析记录，用于定位 Agent 共性问题</p>
      </div>
      <div class="header-actions">
        <select v-model="filterType" class="input-field input-sm" @change="load">
          <option value="">全部类型</option>
          <option v-for="t in analysisTypes" :key="t" :value="t" v-if="t">{{ typeLabel(t) }}</option>
        </select>
        <button class="btn-secondary" @click="load" :disabled="loading">
          {{ loading ? '加载中...' : '刷新' }}
        </button>
      </div>
    </div>

    <!-- Stats bar -->
    <div class="stats-bar">
      <div class="stat-card">
        <span class="stat-value">{{ cases.length }}</span>
        <span class="stat-label">总 Bad Cases</span>
      </div>
      <div class="stat-card" v-for="t in analysisTypes" :key="t" v-if="t">
        <span class="stat-value">{{ cases.filter(c => c.analysis_type === t).length }}</span>
        <span class="stat-label">{{ typeLabel(t) }}</span>
      </div>
    </div>

    <!-- Bad Case list -->
    <div class="badcase-content">
      <div class="badcase-list">
        <div
          v-for="c in filteredCases"
          :key="c.id"
          :class="['badcase-card', { selected: selectedCase?.id === c.id }]"
          @click="selectCase(c)"
        >
          <div class="badcase-header">
            <span class="badge badge-sm" :class="'badge-' + c.analysis_type">{{ typeLabel(c.analysis_type) }}</span>
            <span class="badcase-time">{{ c.created_at?.slice(0, 16) }}</span>
          </div>
          <div class="badcase-summary">{{ c.summary || '无摘要' }}</div>
          <div v-if="c.feedback_note" class="badcase-note">📝 {{ c.feedback_note }}</div>
        </div>
        <div v-if="!loading && filteredCases.length === 0" class="empty-state">
          <p>暂无 Bad Case</p>
          <p class="text-muted">用户标记「没用」的分析会出现在这里</p>
        </div>
        <div v-if="loading" class="loading-state">加载中...</div>
      </div>

      <!-- Detail panel -->
      <div class="badcase-detail" v-if="selectedCase">
        <div class="detail-header">
          <h3>{{ typeLabel(selectedCase.analysis_type) }}</h3>
          <span class="detail-time">{{ selectedCase.created_at }}</span>
        </div>

        <div class="detail-section">
          <h4>摘要</h4>
          <p>{{ selectedCase.summary || '无' }}</p>
        </div>

        <div class="detail-section">
          <h4>反馈原因</h4>
          <p>{{ selectedCase.feedback_note || '用户未填写原因' }}</p>
        </div>

        <div class="detail-section">
          <h4>元数据</h4>
          <table class="meta-table">
            <tbody>
              <tr><td>Agent ID</td><td>{{ selectedCase.agent_id ?? '--' }}</td></tr>
              <tr><td>Token 用量</td><td>{{ selectedCase.token_usage ?? '--' }}</td></tr>
            </tbody>
          </table>
        </div>

        <div class="detail-section">
          <h4>输入数据</h4>
          <pre class="code-block">{{ (() => { try { return JSON.stringify(JSON.parse(selectedCase.input_data || '{}'), null, 2) } catch { return selectedCase.input_data || '无' } })() }}</pre>
        </div>

        <div class="detail-section">
          <h4>分析结果</h4>
          <div class="result-preview">{{ selectedCase.result_data?.slice(0, 2000) || '无' }}</div>
        </div>
      </div>
      <div v-else class="badcase-detail empty-detail">
        <p class="text-muted">选择一条记录查看详情</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.badcase-page {
  animation: fadeIn 0.2s ease;
}
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 1.25rem;
  gap: 1rem;
}
.header-actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-shrink: 0;
}
.input-sm {
  padding: 0.4rem 0.6rem;
  font-size: 0.82rem;
}
.stats-bar {
  display: flex;
  gap: 0.75rem;
  margin-bottom: 1.25rem;
  flex-wrap: wrap;
}
.stat-card {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: 0.6rem 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}
.stat-value {
  font-size: 1.25rem;
  font-weight: 700;
  color: var(--color-text-primary);
}
.stat-label {
  font-size: 0.72rem;
  color: var(--color-text-muted);
}
.badcase-content {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1rem;
  min-height: 500px;
}
.badcase-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  max-height: 70vh;
  overflow-y: auto;
}
.badcase-card {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: 0.75rem 1rem;
  cursor: pointer;
  transition: all 0.15s;
}
.badcase-card:hover {
  border-color: var(--color-primary);
  box-shadow: 0 1px 4px rgba(99,102,241,0.1);
}
.badcase-card.selected {
  border-color: var(--color-primary);
  background: var(--color-primary-50);
}
.dark .badcase-card.selected {
  background: var(--color-primary-bg);
}
.badcase-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.4rem;
}
.badcase-time {
  font-size: 0.75rem;
  color: var(--color-text-muted);
}
.badcase-summary {
  font-size: 0.85rem;
  color: var(--color-text-primary);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.badcase-note {
  font-size: 0.78rem;
  color: #d97706;
  margin-top: 0.3rem;
}
.badge-panorama { background: #6366f1; color: white; }
.badge-deep_dive { background: #06b6d4; color: white; }
.badge-trade_review { background: #f59e0b; color: white; }
.badge-what_if { background: #8b5cf6; color: white; }
.badge-ai { background: #10b981; color: white; }
.badge-diversification_ai { background: #ec4899; color: white; }

.badcase-detail {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: 1.25rem;
  max-height: 70vh;
  overflow-y: auto;
}
.empty-detail {
  display: flex;
  align-items: center;
  justify-content: center;
}
.detail-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 1rem;
}
.detail-header h3 {
  margin: 0;
  font-size: 1rem;
}
.detail-time {
  font-size: 0.78rem;
  color: var(--color-text-muted);
}
.detail-section {
  margin-bottom: 1rem;
}
.detail-section h4 {
  font-size: 0.82rem;
  color: var(--color-text-muted);
  margin: 0 0 0.35rem 0;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}
.detail-section p {
  font-size: 0.85rem;
  margin: 0;
  color: var(--color-text-primary);
}
.meta-table {
  width: 100%;
  font-size: 0.82rem;
}
.meta-table td {
  padding: 0.2rem 0;
  color: var(--color-text-secondary);
}
.meta-table td:first-child {
  color: var(--color-text-muted);
  width: 100px;
}
.code-block {
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  padding: 0.6rem;
  font-size: 0.75rem;
  max-height: 200px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
.result-preview {
  font-size: 0.82rem;
  line-height: 1.6;
  max-height: 300px;
  overflow-y: auto;
  white-space: pre-wrap;
}
.empty-state {
  text-align: center;
  padding: 3rem 1rem;
  color: var(--color-text-muted);
}
.loading-state {
  text-align: center;
  padding: 2rem;
  color: var(--color-text-muted);
}
</style>
