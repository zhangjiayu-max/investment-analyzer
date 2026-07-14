<script setup>
import { ref, onMounted, computed, nextTick } from 'vue'
import {
  listBadCases, createEvalFromBadCase,
  getRootCauseStats, batchAnalyzeRootCause, analyzeSingleRootCause,
} from '../api'
import ConfirmDialog from './ConfirmDialog.vue'
import AppToast from './AppToast.vue'
import { useToast } from '../composables/useToast'

const { showToast } = useToast()
const confirm = ref({ visible: false, title: '', message: '', danger: false, onConfirm: null })

const cases = ref([])
const loading = ref(false)
const converting = ref(false)
const analyzing = ref(false)
const filterSource = ref('')
const filterType = ref('')
const selectedCase = ref(null)
const rootCauseStats = ref(null)
const showStatsPanel = ref(false)
const statsLoading = ref(false)
const mobileView = ref('list')  // 移动端视图: 'list' | 'detail'

// 根因标签样式映射
const rootCauseStyles = {
  data_missing: { label: '数据缺失', color: '#f59e0b', bg: '#fef3c7' },
  reasoning_error: { label: '推理错误', color: '#ef4444', bg: '#fecaca' },
  knowledge_gap: { label: '知识不足', color: '#8b5cf6', bg: '#ede9fe' },
  format_issue: { label: '格式问题', color: '#6b7280', bg: '#f3f4f6' },
  hallucination: { label: '幻觉编造', color: '#dc2626', bg: '#fee2e2' },
  irrelevant: { label: '答非所问', color: '#f97316', bg: '#ffedd5' },
  outdated_info: { label: '信息过时', color: '#0891b2', bg: '#cffafe' },
  tone_issue: { label: '语气问题', color: '#84cc16', bg: '#ecfccb' },
  other: { label: '其他', color: '#6b7280', bg: '#f3f4f6' },
}

// 加载根因统计
async function loadRootCauseStats() {
  statsLoading.value = true
  try {
    const { data } = await getRootCauseStats()
    rootCauseStats.value = data
  } catch (e) {
    console.error('加载根因统计失败:', e)
  } finally {
    statsLoading.value = false
  }
}

// 批量分析根因
async function batchAnalyze() {
  analyzing.value = true
  try {
    const { data } = await batchAnalyzeRootCause(50)
    showToast(`分析完成: ${data.analyzed} 条成功，${data.failed} 条失败`, 'success')
    await load()
    await loadRootCauseStats()
  } catch (e) {
    showToast('分析失败: ' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    analyzing.value = false
  }
}

// 分析单条根因
async function analyzeSingle(c) {
  try {
    const { data } = await analyzeSingleRootCause(c.source, c.id)
    if (data.ok) {
      c.root_cause = data.result.root_cause
      c.root_cause_detail = JSON.stringify(data.result)
      showToast(`根因: ${rootCauseStyles[data.result.root_cause]?.label || data.result.root_cause}`, 'success')
    }
  } catch (e) {
    showToast('分析失败: ' + (e.response?.data?.detail || e.message), 'error')
  }
}

// 解析根因详情 JSON
function parseRcDetail(detail) {
  if (!detail) return null
  try {
    return typeof detail === 'string' ? JSON.parse(detail) : detail
  } catch {
    return null
  }
}

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
  mobileView.value = 'detail'
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

onMounted(() => { load(); loadRootCauseStats() })
</script>

<template>
  <div class="badcase-page bg-mesh">
    <div class="page-header">
      <div>
        <h2 class="page-title editorial-title-lg">Bad Case 分析看板</h2>
        <p class="page-desc editorial-subtitle">用户标记为「没用」的模型产出，用于定位问题和持续优化</p>
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
        <button class="btn-secondary btn-sm" @click="showStatsPanel = !showStatsPanel">
          {{ showStatsPanel ? '隐藏根因' : '根因统计' }}
        </button>
        <button class="btn-primary btn-sm" @click="batchAnalyze" :disabled="analyzing">
          {{ analyzing ? '分析中...' : '批量分析根因' }}
        </button>
        <button class="btn-secondary" @click="load" :disabled="loading">
          {{ loading ? '加载中...' : '刷新' }}
        </button>
      </div>
    </div>

    <!-- Stats bar -->
    <div class="stats-bar">
      <div class="stat-card editorial-card">
        <span class="stat-value font-jet-lg">{{ stats.total }}</span>
        <span class="stat-label terminal-label">总 Bad Cases</span>
      </div>
      <div class="stat-card editorial-card">
        <span class="stat-value font-jet-lg">{{ stats.analysis }}</span>
        <span class="stat-label terminal-label">分析记录</span>
      </div>
      <div class="stat-card editorial-card">
        <span class="stat-value font-jet-lg">{{ stats.chat }}</span>
        <span class="stat-label terminal-label">对话反馈</span>
      </div>
      <div v-if="rootCauseStats" class="stat-card editorial-card">
        <span class="stat-value font-jet-lg">{{ rootCauseStats.total_analyzed }}</span>
        <span class="stat-label terminal-label">已分析根因</span>
      </div>
    </div>

    <!-- Root Cause Stats Panel -->
    <Transition name="fade">
      <div v-if="showStatsPanel && (rootCauseStats || statsLoading)" class="root-cause-panel editorial-card">
        <div v-if="statsLoading" class="rc-loading">
          <span class="rc-spinner"></span>
          <span>加载根因统计...</span>
        </div>
        <template v-else>
          <div class="rc-section">
            <div class="editorial-card-header">
              <span class="title">根因分布</span>
              <span class="meta">DISTRIBUTION</span>
            </div>
            <div class="rc-bars">
              <div v-for="item in rootCauseStats.by_cause" :key="item.root_cause" class="rc-bar-item reveal-stagger">
                <div class="rc-bar-label">
                  <span class="rc-dot" :style="{ background: rootCauseStyles[item.root_cause]?.color || '#999' }"></span>
                  {{ item.label }}
                  <span class="rc-bar-count font-jet">{{ item.count }} ({{ item.pct }}%)</span>
                </div>
                <div class="rc-bar-track">
                  <div class="rc-bar-fill" :style="{ width: item.pct + '%', background: rootCauseStyles[item.root_cause]?.color || '#999' }"></div>
                </div>
              </div>
            </div>
          </div>
          <div v-if="rootCauseStats.recent?.length" class="rc-section">
            <div class="editorial-card-header">
              <span class="title">最近分析</span>
              <span class="meta">RECENT</span>
            </div>
            <div class="rc-recent">
              <div v-for="r in rootCauseStats.recent" :key="r.source + r.id" class="rc-recent-item reveal-stagger">
                <span class="rc-tag" :style="{ color: rootCauseStyles[r.root_cause]?.color, background: rootCauseStyles[r.root_cause]?.bg }">
                  {{ r.label }}
                </span>
                <span class="rc-recent-detail">{{ r.detail }}</span>
              </div>
            </div>
          </div>
        </template>
      </div>
    </Transition>

    <!-- Bad Case list -->
    <div class="badcase-content">
      <div class="mobile-tab-switch">
        <button :class="['mobile-tab-btn', { active: mobileView === 'list' }]" @click="mobileView = 'list'">列表</button>
        <button :class="['mobile-tab-btn', { active: mobileView === 'detail' }]" @click="mobileView = 'detail'">详情</button>
      </div>
      <div class="badcase-list" :class="{ 'mobile-hidden': mobileView !== 'list' }">
        <div
          v-for="c in filteredCases"
          :key="c.source + '-' + c.id"
          :class="['badcase-card editorial-card reveal-stagger', { selected: selectedCase?.source === c.source && selectedCase?.id === c.id }]"
          @click="selectCase(c)"
        >
          <div class="badcase-header">
            <span class="badge badge-sm" :class="'badge-' + c.source">{{ sourceLabels[c.source] || c.source }}</span>
            <span class="badge badge-sm badge-type">{{ typeLabel(c) }}</span>
            <span v-if="c.root_cause" class="rc-tag rc-tag-sm" :style="{ color: rootCauseStyles[c.root_cause]?.color, background: rootCauseStyles[c.root_cause]?.bg }">
              {{ rootCauseStyles[c.root_cause]?.label || c.root_cause }}
            </span>
            <span class="badcase-time font-jet terminal-label">{{ c.created_at?.slice(0, 16) }}</span>
          </div>
          <div class="badcase-summary">{{ c.summary || '无摘要' }}</div>
          <div v-if="c.note" class="badcase-note">{{ c.note }}</div>
          <div v-if="!c.root_cause" class="badcase-actions-inline">
            <button class="btn-ghost btn-sm" @click.stop="analyzeSingle(c)">分析根因</button>
          </div>
        </div>
        <div v-if="!loading && filteredCases.length === 0" class="empty-state">
          <p>暂无 Bad Case</p>
          <p class="text-muted">用户标记「没用」的模型产出会出现在这里</p>
        </div>
        <div v-if="loading" class="loading-state">加载中...</div>
      </div>

      <!-- Detail panel -->
      <div class="badcase-detail editorial-card" :class="{ 'mobile-hidden': mobileView !== 'detail' }" v-if="selectedCase">
        <div class="detail-header">
          <div class="detail-header-tags">
            <span class="badge badge-sm" :class="'badge-' + selectedCase.source">{{ sourceLabels[selectedCase.source] || selectedCase.source }}</span>
            <span class="badge badge-sm badge-type">{{ typeLabel(selectedCase) }}</span>
          </div>
          <span class="detail-time font-jet terminal-label">{{ selectedCase.created_at }}</span>
        </div>

        <div class="detail-section">
          <h4 class="terminal-label">摘要</h4>
          <p>{{ selectedCase.summary || '无' }}</p>
        </div>

        <div class="detail-section">
          <h4 class="terminal-label">反馈原因</h4>
          <p>{{ selectedCase.note || '用户未填写原因' }}</p>
        </div>

        <div v-if="selectedCase.root_cause" class="detail-section">
          <h4 class="terminal-label">根因分析</h4>
          <div class="rc-detail">
            <span class="rc-tag" :style="{ color: rootCauseStyles[selectedCase.root_cause]?.color, background: rootCauseStyles[selectedCase.root_cause]?.bg }">
              {{ rootCauseStyles[selectedCase.root_cause]?.label || selectedCase.root_cause }}
            </span>
            <template v-if="selectedCase.root_cause_detail">
              <p v-if="parseRcDetail(selectedCase.root_cause_detail)?.detail" class="rc-detail-text">{{ parseRcDetail(selectedCase.root_cause_detail).detail }}</p>
              <p v-if="parseRcDetail(selectedCase.root_cause_detail)?.evidence" class="rc-evidence">证据: {{ parseRcDetail(selectedCase.root_cause_detail).evidence }}</p>
              <p v-if="parseRcDetail(selectedCase.root_cause_detail)?.suggestion" class="rc-suggestion">建议: {{ parseRcDetail(selectedCase.root_cause_detail).suggestion }}</p>
            </template>
          </div>
        </div>
        <div v-else class="detail-section">
          <h4 class="terminal-label">根因分析</h4>
          <button class="btn-primary btn-sm" @click="analyzeSingle(selectedCase)">分析根因</button>
        </div>

        <div class="detail-section">
          <h4 class="terminal-label">元数据</h4>
          <table class="meta-table">
            <thead>
              <tr><th>字段</th><th>值</th></tr>
            </thead>
            <tbody>
              <template v-if="selectedCase.source === 'analysis'">
                <tr><td>类型</td><td>{{ typeLabel(selectedCase) }}</td></tr>
                <tr><td>Agent ID</td><td><span class="font-jet">{{ selectedCase.metadata?.agent_id ?? '--' }}</span></td></tr>
                <tr><td>Token 用量</td><td><span class="font-jet">{{ selectedCase.metadata?.token_usage ?? '--' }}</span></td></tr>
              </template>
              <template v-else>
                <tr><td>Caller</td><td>{{ selectedCase.metadata?.caller || '--' }}</td></tr>
                <tr><td>Tags</td><td>{{ selectedCase.metadata?.tags || '--' }}</td></tr>
              </template>
            </tbody>
          </table>
        </div>

        <div class="detail-section">
          <h4 class="terminal-label">{{ selectedCase.source === 'analysis' ? '输入数据' : '用户输入' }}</h4>
          <pre class="code-block font-jet" v-if="selectedCase.source === 'analysis'">{{ (() => { try { return JSON.stringify(JSON.parse(selectedCase.input || '{}'), null, 2) } catch { return selectedCase.input || '无' } })() }}</pre>
          <p v-else>{{ selectedCase.input || '无' }}</p>
        </div>

        <div class="detail-section">
          <h4 class="terminal-label">{{ selectedCase.source === 'analysis' ? '分析结果' : '模型输出' }}</h4>
          <div class="result-preview font-jet">{{ selectedCase.output?.slice(0, 2000) || '无' }}</div>
        </div>

        <div class="detail-actions">
          <button class="btn-primary btn-sm" :disabled="converting" @click="confirmConvertToEval(selectedCase)">
            {{ converting ? '转化中...' : '转为 Eval 用例' }}
          </button>
        </div>
      </div>
      <div v-else class="badcase-detail empty-detail editorial-card" :class="{ 'mobile-hidden': mobileView !== 'detail' }">
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

/* Root Cause Tags */
.rc-tag {
  display: inline-flex;
  align-items: center;
  padding: 0.15rem 0.5rem;
  border-radius: var(--radius-sm);
  font-size: 0.72rem;
  font-weight: 600;
  white-space: nowrap;
}
.rc-tag-sm {
  padding: 0.1rem 0.4rem;
  font-size: 0.68rem;
}
.badcase-actions-inline {
  margin-top: 0.3rem;
}

/* Root Cause Stats Panel */
.root-cause-panel {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 1rem 1.25rem;
  margin-bottom: 1rem;
}
.rc-section {
  margin-bottom: 1rem;
}
.rc-section:last-child {
  margin-bottom: 0;
}
.rc-title {
  font-size: 0.82rem;
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0 0 0.6rem 0;
}
.rc-bars {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.rc-bar-item {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}
.rc-bar-label {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.78rem;
  color: var(--color-text-secondary);
}
.rc-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.rc-bar-count {
  margin-left: auto;
  font-weight: 600;
  color: var(--color-text-primary);
}
.rc-bar-track {
  height: 6px;
  background: var(--color-bg);
  border-radius: 3px;
  overflow: hidden;
}
.rc-bar-fill {
  height: 100%;
  border-radius: 3px;
  transition: width 0.3s ease;
}
.rc-recent {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}
.rc-recent-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.78rem;
}
.rc-recent-detail {
  color: var(--color-text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  min-width: 0;
}
.rc-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  padding: 1.5rem 1rem;
  color: var(--color-text-muted);
  font-size: 0.82rem;
}
.rc-spinner {
  width: 14px;
  height: 14px;
  border: 2px solid var(--color-border);
  border-top-color: var(--color-primary);
  border-radius: 50%;
  animation: rc-spin 0.8s linear infinite;
}
@keyframes rc-spin {
  to { transform: rotate(360deg); }
}

/* Root Cause Detail */
.rc-detail {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.rc-detail-text {
  font-size: 0.85rem;
  color: var(--color-text-primary);
  margin: 0;
}
.rc-evidence {
  font-size: 0.78rem;
  color: var(--color-text-muted);
  margin: 0;
  font-style: italic;
}
.rc-suggestion {
  font-size: 0.78rem;
  color: var(--color-primary);
  margin: 0;
}

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
  border-collapse: collapse;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  overflow: hidden;
}
.meta-table thead th {
  background: var(--color-bg-hover);
  color: var(--color-text-muted);
  font-size: 0.72rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  padding: 0.4rem 0.6rem;
  text-align: left;
  border-bottom: 1px solid var(--color-border);
}
.meta-table tbody td {
  padding: 0.35rem 0.6rem;
  color: var(--color-text-secondary);
  border-bottom: 1px solid var(--color-border-light);
}
.meta-table tbody tr:last-child td {
  border-bottom: none;
}
.meta-table tbody tr:nth-child(even) {
  background: var(--color-bg-hover);
}
.meta-table tbody tr:hover {
  background: var(--color-primary-50);
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

/* 移动端 tab 切换（仅移动端可见） */
.mobile-tab-switch {
  display: none;
}

@media (max-width: 768px) {
  .header-actions {
    flex-wrap: wrap;
  }
  .header-actions .input-field {
    flex: 1 1 100%;
  }
  .header-actions .btn-sm,
  .header-actions .btn-secondary {
    flex: 1 1 auto;
  }
  .badcase-content {
    grid-template-columns: 1fr;
  }
  .badcase-list {
    max-height: none;
  }
  .badcase-header {
    flex-wrap: wrap;
    gap: 0.35rem;
  }
  .badcase-time {
    margin-left: 0;
    flex-basis: 100%;
  }
  /* tab 切换显示 */
  .mobile-tab-switch {
    display: flex;
    gap: 0.25rem;
    padding: 2px;
    background: var(--color-bg-input);
    border-radius: var(--radius-sm);
    margin-bottom: 0.5rem;
  }
  .mobile-tab-btn {
    flex: 1;
    padding: 0.4rem 0.5rem;
    border: none;
    border-radius: calc(var(--radius-sm) - 1px);
    background: transparent;
    color: var(--color-text-secondary);
    font-size: 0.8rem;
    cursor: pointer;
    transition: all 0.15s;
  }
  .mobile-tab-btn.active {
    background: var(--color-bg-card);
    color: var(--color-text-primary);
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
  }
  .mobile-hidden {
    display: none;
  }
}
</style>
