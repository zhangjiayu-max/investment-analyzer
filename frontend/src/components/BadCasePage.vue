<script setup>
import { ref, onMounted, computed, nextTick } from 'vue'
import { listBadCases, createEvalFromBadCase } from '../api'
import ConfirmDialog from './ConfirmDialog.vue'
import AppToast from './AppToast.vue'
import { useToast } from '../composables/useToast'

const { showToast } = useToast()
const confirm = ref({ visible: false, title: '', message: '', danger: false, onConfirm: null })

const cases = ref([])
const loading = ref(false)
const converting = ref(false)
const filterSource = ref('')
const filterType = ref('')
const selectedCase = ref(null)

const sourceLabels = {
  analysis: '分析记录',
  chat: '对话反馈',
}

const analysisTypeLabels = {
  panorama: '全景诊断',
  deep_dive: '单基金深度',
  trade_review: '交易复盘',
  what_if: '情景推演',
  ai: 'AI 分析',
  diversification_ai: '分散度分析',
}

const callerLabels = {
  chat: '自由对话',
  agent_chat: 'Agent 对话',
  agent_tools: '工具对话',
  clarify: '需求澄清',
  article_analysis: '文章分析',
  daily_report: '每日报告',
}

function typeLabel(c) {
  if (c.source === 'chat') return callerLabels[c.type] || c.type || '对话'
  return analysisTypeLabels[c.type] || c.type || '分析'
}

const analysisTypes = computed(() => {
  const types = new Set(cases.value.filter(c => c.source === 'analysis').map(c => c.type).filter(Boolean))
  return Array.from(types)
})

const filteredCases = computed(() => {
  let result = cases.value
  if (filterSource.value) {
    result = result.filter(c => c.source === filterSource.value)
  }
  if (filterType.value) {
    result = result.filter(c => c.type === filterType.value)
  }
  return result
})

const stats = computed(() => {
  const analysisCount = cases.value.filter(c => c.source === 'analysis').length
  const chatCount = cases.value.filter(c => c.source === 'chat').length
  return { total: cases.value.length, analysis: analysisCount, chat: chatCount }
})

async function load() {
  loading.value = true
  try {
    const { data } = await listBadCases(filterSource.value || '', 200)
    cases.value = data.cases || []
  } catch (e) {
    console.error(e)
  } finally {
    loading.value = false
  }
}

function selectCase(c) {
  selectedCase.value = c
  // 移动端自动滚到详情面板
  nextTick(() => {
    const detail = document.querySelector('.badcase-detail')
    if (detail && window.innerWidth <= 768) {
      detail.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  })
}

function confirmConvertToEval(c) {
  const typeLabel = analysisTypeLabels[c.type] || callerLabels[c.type] || c.type || '未知'
  confirm.value = {
    visible: true,
    title: '转为 Eval 用例',
    message: `将这条 Bad Case（${typeLabel}）转化为评测用例？系统会自动分析失败原因并生成质量标准。`,
    danger: false,
    onConfirm: () => doConvertToEval(c),
  }
}

async function doConvertToEval(c) {
  confirm.value.visible = false
  converting.value = true
  try {
    const { data } = await createEvalFromBadCase(c.source, c.id)
    if (data.ok) {
      showToast(`已创建评测用例「${data.name}」`, 'success')
    } else {
      showToast('转化失败', 'error')
    }
  } catch (e) {
    showToast('转化失败: ' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    converting.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="badcase-page">
    <div class="page-header">
      <div>
        <h2 class="page-title">Bad Case 分析看板</h2>
        <p class="page-desc">用户标记为「没用」的模型产出，用于定位问题和持续优化</p>
      </div>
      <div class="header-actions">
        <select v-model="filterSource" class="input-field input-sm" @change="load">
          <option value="">全部来源</option>
          <option value="analysis">分析记录</option>
          <option value="chat">对话反馈</option>
        </select>
        <select v-if="filterSource === 'analysis' || !filterSource" v-model="filterType" class="input-field input-sm">
          <option value="">全部类型</option>
          <option v-for="t in analysisTypes" :key="t" :value="t">{{ analysisTypeLabels[t] || t }}</option>
        </select>
        <button class="btn-secondary" @click="load" :disabled="loading">
          {{ loading ? '加载中...' : '刷新' }}
        </button>
      </div>
    </div>

    <!-- Stats bar -->
    <div class="stats-bar">
      <div class="stat-card">
        <span class="stat-value">{{ stats.total }}</span>
        <span class="stat-label">总 Bad Cases</span>
      </div>
      <div class="stat-card">
        <span class="stat-value">{{ stats.analysis }}</span>
        <span class="stat-label">分析记录</span>
      </div>
      <div class="stat-card">
        <span class="stat-value">{{ stats.chat }}</span>
        <span class="stat-label">对话反馈</span>
      </div>
    </div>

    <!-- Bad Case list -->
    <div class="badcase-content">
      <div class="badcase-list">
        <div
          v-for="c in filteredCases"
          :key="c.source + '-' + c.id"
          :class="['badcase-card', { selected: selectedCase?.source === c.source && selectedCase?.id === c.id }]"
          @click="selectCase(c)"
        >
          <div class="badcase-header">
            <span class="badge badge-sm" :class="'badge-' + c.source">{{ sourceLabels[c.source] || c.source }}</span>
            <span class="badge badge-sm badge-type">{{ typeLabel(c) }}</span>
            <span class="badcase-time">{{ c.created_at?.slice(0, 16) }}</span>
          </div>
          <div class="badcase-summary">{{ c.summary || '无摘要' }}</div>
          <div v-if="c.note" class="badcase-note">📝 {{ c.note }}</div>
        </div>
        <div v-if="!loading && filteredCases.length === 0" class="empty-state">
          <p>暂无 Bad Case</p>
          <p class="text-muted">用户标记「没用」的模型产出会出现在这里</p>
        </div>
        <div v-if="loading" class="loading-state">加载中...</div>
      </div>

      <!-- Detail panel -->
      <div class="badcase-detail" v-if="selectedCase">
        <div class="detail-header">
          <div class="detail-header-tags">
            <span class="badge badge-sm" :class="'badge-' + selectedCase.source">{{ sourceLabels[selectedCase.source] || selectedCase.source }}</span>
            <span class="badge badge-sm badge-type">{{ typeLabel(selectedCase) }}</span>
          </div>
          <span class="detail-time">{{ selectedCase.created_at }}</span>
        </div>

        <div class="detail-section">
          <h4>摘要</h4>
          <p>{{ selectedCase.summary || '无' }}</p>
        </div>

        <div class="detail-section">
          <h4>反馈原因</h4>
          <p>{{ selectedCase.note || '用户未填写原因' }}</p>
        </div>

        <div class="detail-section">
          <h4>元数据</h4>
          <table class="meta-table">
            <tbody>
              <template v-if="selectedCase.source === 'analysis'">
                <tr><td>类型</td><td>{{ typeLabel(selectedCase) }}</td></tr>
                <tr><td>Agent ID</td><td>{{ selectedCase.metadata?.agent_id ?? '--' }}</td></tr>
                <tr><td>Token 用量</td><td>{{ selectedCase.metadata?.token_usage ?? '--' }}</td></tr>
              </template>
              <template v-else>
                <tr><td>Caller</td><td>{{ selectedCase.metadata?.caller || '--' }}</td></tr>
                <tr><td>Tags</td><td>{{ selectedCase.metadata?.tags || '--' }}</td></tr>
              </template>
            </tbody>
          </table>
        </div>

        <div class="detail-section">
          <h4>{{ selectedCase.source === 'analysis' ? '输入数据' : '用户输入' }}</h4>
          <pre class="code-block" v-if="selectedCase.source === 'analysis'">{{ (() => { try { return JSON.stringify(JSON.parse(selectedCase.input || '{}'), null, 2) } catch { return selectedCase.input || '无' } })() }}</pre>
          <p v-else>{{ selectedCase.input || '无' }}</p>
        </div>

        <div class="detail-section">
          <h4>{{ selectedCase.source === 'analysis' ? '分析结果' : '模型输出' }}</h4>
          <div class="result-preview">{{ selectedCase.output?.slice(0, 2000) || '无' }}</div>
        </div>

        <div class="detail-actions">
          <button class="btn-primary btn-sm" :disabled="converting" @click="confirmConvertToEval(selectedCase)">
            {{ converting ? '转化中...' : '🔄 转为 Eval 用例' }}
          </button>
        </div>
      </div>
      <div v-else class="badcase-detail empty-detail">
        <p class="text-muted">选择一条记录查看详情</p>
      </div>
    </div>
  </div>
  <ConfirmDialog
    :visible="confirm.visible"
    :title="confirm.title"
    :message="confirm.message"
    :danger="confirm.danger"
    @confirm="() => confirm.onConfirm?.()"
    @cancel="confirm.visible = false"
  />
  <AppToast />
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
  padding: 0.5rem 0.75rem;
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
  gap: 0.25rem;
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
  margin-bottom: 0.6rem;
}
.badcase-time {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  margin-left: auto;
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
.badge-analysis { background: #c9a84c; color: white; }
.badge-chat { background: #06b6d4; color: white; }
.badge-type { background: var(--color-bg); color: var(--color-text-secondary); border: 1px solid var(--color-border); }

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
.detail-header-tags {
  display: flex;
  gap: 0.5rem;
  align-items: center;
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
.detail-actions {
  margin-top: 1.25rem;
  padding-top: 1rem;
  border-top: 1px solid var(--color-border);
  display: flex;
  gap: 0.5rem;
}

@media (max-width: 768px) {
  .badcase-content {
    grid-template-columns: 1fr;
  }
}
</style>
