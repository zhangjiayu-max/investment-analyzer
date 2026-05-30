<script setup>
import { ref, computed, onMounted } from 'vue'
import {
  listEvalCases, createEvalCase, deleteEvalCase, runEvalCase,
  listEvalRuns, getEvalRunDetail, getEvalStats,
} from '../api'
import ConfirmDialog from './ConfirmDialog.vue'
import AppToast from './AppToast.vue'
import { useToast } from '../composables/useToast'

const { showToast } = useToast()
const confirm = ref({ visible: false, title: '', message: '', danger: false, onConfirm: null })

const loading = ref(false)
const running = ref(false)
const activeTab = ref('cases')

// Stats
const stats = ref({ total_cases: 0, active_cases: 0, total_runs: 0, avg_score: null })

// Cases
const cases = ref([])
const showCreateForm = ref(false)
const editForm = ref({ name: '', description: '', analysis_type: 'panorama', input_params: '{}', expected_quality: '' })

// Runs
const runs = ref([])
const selectedRun = ref(null)
const runDetail = ref(null)

const analysisTypeOptions = [
  { value: 'panorama', label: '全景诊断' },
  { value: 'deep_dive', label: '单基金深度' },
  { value: 'trade_review', label: '交易复盘' },
  { value: 'what_if', label: '情景推演' },
  { value: 'ai', label: 'AI 分析' },
  { value: 'diversification_ai', label: '分散度分析' },
]

function typeLabel(t) {
  return analysisTypeOptions.find(o => o.value === t)?.label || t
}

function confirmBeforeDelete(c) {
  confirm.value = {
    visible: true,
    title: '删除确认',
    message: `确定删除评测用例「${c.name}」吗？关联的运行记录也将删除。`,
    danger: true,
    onConfirm: () => {
      confirm.value.visible = false
      doDelete(c.id)
    }
  }
}

async function doDelete(id) {
  try {
    await deleteEvalCase(id)
    await Promise.all([loadCases(), loadStats()])
  } catch (e) {
    console.error(e)
    showToast('删除失败: ' + e.message, 'error')
  }
}

async function loadStats() {
  try {
    const { data } = await getEvalStats()
    stats.value = data
  } catch (e) {
    console.error(e)
  }
}

async function loadCases() {
  try {
    const { data } = await listEvalCases()
    cases.value = data.cases || []
  } catch (e) {
    console.error(e)
  }
}

async function loadRuns() {
  try {
    const { data } = await listEvalRuns()
    runs.value = data.runs || []
  } catch (e) {
    console.error(e)
  }
}

async function loadAll() {
  loading.value = true
  try {
    await Promise.all([loadStats(), loadCases(), loadRuns()])
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editForm.value = { name: '', description: '', analysis_type: 'panorama', input_params: '{}', expected_quality: '' }
  showCreateForm.value = true
}

async function submitCreate() {
  if (!editForm.value.name.trim()) return
  try {
    await createEvalCase(editForm.value)
    showCreateForm.value = false
    await Promise.all([loadCases(), loadStats()])
  } catch (e) {
    showToast('创建失败: ' + e.message, 'error')
  }
}

async function doRun(c) {
  if (running.value) return
  running.value = true
  try {
    const { data } = await runEvalCase(c.id)
    if (data.ok) {
      await loadRuns()
      activeTab.value = 'runs'
      showToast('运行完成，评分中...', 'info')
      // 延迟刷新以获取异步评分结果
      setTimeout(async () => {
        await Promise.all([loadRuns(), loadStats(), loadCases()])
      }, 8000)
    } else {
      showToast('运行失败: ' + (data.error || '未知错误'), 'error')
    }
  } catch (e) {
    showToast('运行出错: ' + e.message, 'error')
  } finally {
    running.value = false
  }
}

async function viewRunDetail(r) {
  try {
    const { data } = await getEvalRunDetail(r.id)
    selectedRun.value = r
    runDetail.value = data
  } catch (e) {
    console.error(e)
  }
}

function formatTime(ts) {
  if (!ts) return ''
  return ts.slice(0, 16)
}

function formatDuration(ms) {
  if (!ms) return '-'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function runStatusIcon(r) {
  if (r.error_msg) return '❌'
  if (r.score !== null && r.score > 0) return '✅'
  return '⏳'
}

function scoreColor(score) {
  if (!score || score <= 0) return 'var(--color-text-muted)'
  if (score >= 4.5) return '#10b981'
  if (score >= 3.5) return '#22c55e'
  if (score >= 2.5) return '#f59e0b'
  if (score >= 1.5) return '#f97316'
  return '#ef4444'
}

const latestRuns = computed(() => runs.value.slice(0, 20))

onMounted(loadAll)
</script>

<template>
  <div class="evalsuite-page">
    <div class="page-header">
      <div>
        <h2 class="page-title">评测集</h2>
        <p class="page-desc">运行测试用例，评估 Agent 分析质量</p>
      </div>
      <div class="header-actions">
        <button class="btn-secondary" @click="loadAll" :disabled="loading">
          {{ loading ? '加载中...' : '刷新' }}
        </button>
      </div>
    </div>

    <!-- Stats -->
    <div class="stats-bar">
      <div class="stat-card">
        <span class="stat-value">{{ stats.total_cases || 0 }}</span>
        <span class="stat-label">总用例</span>
      </div>
      <div class="stat-card">
        <span class="stat-value">{{ stats.active_cases || 0 }}</span>
        <span class="stat-label">活跃用例</span>
      </div>
      <div class="stat-card">
        <span class="stat-value">{{ stats.total_runs || 0 }}</span>
        <span class="stat-label">运行次数</span>
      </div>
      <div class="stat-card">
        <span class="stat-value" :style="{ color: stats.avg_score ? scoreColor(stats.avg_score) : 'inherit' }">
          {{ stats.avg_score !== null ? Number(stats.avg_score).toFixed(1) : '-' }}
        </span>
        <span class="stat-label">平均分</span>
      </div>
    </div>

    <!-- Tabs -->
    <div class="tab-bar">
      <button :class="['tab-btn', { active: activeTab === 'cases' }]" @click="activeTab = 'cases'">
        评测用例 ({{ cases.length }})
      </button>
      <button :class="['tab-btn', { active: activeTab === 'runs' }]" @click="activeTab = 'runs'">
        运行记录 ({{ runs.length }})
      </button>
    </div>

    <!-- Cases Tab -->
    <div v-if="activeTab === 'cases'" class="tab-content">
      <div class="section-header">
        <span class="section-title">用例列表</span>
        <button class="btn-primary btn-sm" @click="openCreate">+ 新建用例</button>
      </div>

      <!-- Create Form -->
      <div v-if="showCreateForm" class="create-form card">
        <h4>新建评测用例</h4>
        <div class="form-row">
          <label>名称</label>
          <input v-model="editForm.name" class="input-field" placeholder="例: 全景诊断-正常持仓" />
        </div>
        <div class="form-row">
          <label>描述</label>
          <input v-model="editForm.description" class="input-field" placeholder="用例说明" />
        </div>
        <div class="form-row">
          <label>分析类型</label>
          <select v-model="editForm.analysis_type" class="input-field">
            <option v-for="o in analysisTypeOptions" :key="o.value" :value="o.value">{{ o.label }}</option>
          </select>
        </div>
        <div class="form-row">
          <label>参数 (JSON)</label>
          <textarea v-model="editForm.input_params" class="input-field code-input" rows="2"
            placeholder='{"holding_id": 1} 或 {}'></textarea>
        </div>
        <div class="form-row">
          <label>预期质量</label>
          <textarea v-model="editForm.expected_quality" class="input-field" rows="2"
            placeholder="期望的输出质量标准（可选）"></textarea>
        </div>
        <div class="form-actions">
          <button class="btn-primary" @click="submitCreate">保存</button>
          <button class="btn-secondary" @click="showCreateForm = false">取消</button>
        </div>
      </div>

      <!-- Case List -->
      <div v-if="!loading && cases.length === 0" class="empty-state">
        <p>暂无评测用例</p>
        <p class="text-muted">点击「新建用例」创建第一个评测</p>
      </div>

      <div v-else class="case-list">
        <div v-for="c in cases" :key="c.id" class="case-card card">
          <div class="case-header">
            <div class="case-info">
              <span class="badge badge-sm" :class="'badge-' + c.analysis_type">{{ typeLabel(c.analysis_type) }}</span>
              <span class="case-name">{{ c.name }}</span>
            </div>
            <div class="case-meta">
              <span class="case-stat">运行 {{ c.run_count || 0 }} 次</span>
              <span v-if="c.avg_score" class="case-stat">平均 {{ Number(c.avg_score).toFixed(1) }}分</span>
            </div>
          </div>
          <div v-if="c.description" class="case-desc">{{ c.description }}</div>
          <div class="case-actions">
            <button class="btn-primary btn-xs" :disabled="running" @click="doRun(c)">
              {{ running ? '运行中...' : '▶ 运行' }}
            </button>
            <button class="btn-danger btn-xs" @click="confirmBeforeDelete(c)">删除</button>
          </div>
        </div>
      </div>
    </div>

    <!-- Runs Tab -->
    <div v-if="activeTab === 'runs'" class="tab-content">
      <div class="runs-layout">
        <!-- Run List -->
        <div class="run-list">
          <h4 class="section-title">运行记录</h4>
          <div v-if="runs.length === 0" class="empty-state">
            <p>暂无运行记录</p>
            <p class="text-muted">在「用例」页点击运行按钮触发评测</p>
          </div>
          <div v-for="r in latestRuns" :key="r.id"
            :class="['run-item', { selected: selectedRun?.id === r.id }]"
            @click="viewRunDetail(r)">
            <div class="run-header">
              <span>{{ runStatusIcon(r) }}</span>
              <span class="run-case-name">{{ r.case_name || '-' }}</span>
              <span class="run-time">{{ formatTime(r.created_at) }}</span>
            </div>
            <div class="run-sub">
              <span class="badge-solid badge-xs" :class="'badge-' + r.analysis_type">{{ typeLabel(r.analysis_type) }}</span>
              <span class="run-duration">{{ formatDuration(r.duration_ms) }}</span>
              <span v-if="r.score !== null && r.score > 0" class="run-score" :style="{ color: scoreColor(r.score) }">
                {{ Number(r.score).toFixed(0) }}分
              </span>
              <span v-else class="run-score scoring">评分中...</span>
            </div>
          </div>
        </div>

        <!-- Run Detail -->
        <div class="run-detail" v-if="runDetail">
          <h4 class="section-title">运行详情</h4>
          <div class="detail-meta">
            <div><strong>用例:</strong> {{ runDetail.case_name }}</div>
            <div><strong>类型:</strong> {{ typeLabel(runDetail.analysis_type) }}</div>
            <div><strong>耗时:</strong> {{ formatDuration(runDetail.duration_ms) }}</div>
            <div><strong>Token:</strong> {{ runDetail.token_usage || 0 }}</div>
            <div v-if="runDetail.score !== null && runDetail.score > 0">
              <strong>评分:</strong>
              <span :style="{ color: scoreColor(runDetail.score), fontWeight: 700, fontSize: '1.1em' }">
                {{ Number(runDetail.score).toFixed(0) }} / 5
              </span>
            </div>
            <div v-if="runDetail.score_reason"><strong>评语:</strong> {{ runDetail.score_reason }}</div>
            <div v-if="runDetail.error_msg"><strong>错误:</strong> <span class="error-text">{{ runDetail.error_msg }}</span></div>
          </div>

          <div v-if="runDetail.result_summary" class="detail-section">
            <h5>结果摘要</h5>
            <pre class="code-block">{{ runDetail.result_summary }}</pre>
          </div>

          <div v-if="runDetail.result_data && runDetail.result_data.length > 100" class="detail-section">
            <h5>完整结果</h5>
            <pre class="code-block code-full">{{ runDetail.result_data }}</pre>
          </div>

          <div v-if="runDetail.expected_quality" class="detail-section">
            <h5>预期质量</h5>
            <pre class="code-block">{{ runDetail.expected_quality }}</pre>
          </div>

          <div v-if="runDetail.input_params && runDetail.input_params !== '{}'" class="detail-section">
            <h5>输入参数</h5>
            <pre class="code-block">{{ (() => { try { return JSON.stringify(JSON.parse(runDetail.input_params), null, 2) } catch { return runDetail.input_params } })() }}</pre>
          </div>
        </div>
        <div v-else class="run-detail empty-detail">
          <p class="text-muted">选择一条运行记录查看详情</p>
        </div>
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
.evalsuite-page {
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

/* Stats */
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
  min-width: 100px;
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

/* Tabs */
.tab-content {
  min-height: 300px;
}

/* Section */
.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 0.75rem;
}

.section-title {
  font-size: 0.9rem;
  font-weight: 600;
  color: var(--color-text-primary);
}

/* Create Form */
.create-form {
  padding: 1rem 1.25rem;
  margin-bottom: 1rem;
}

.create-form h4 {
  margin: 0 0 0.75rem;
  font-size: 0.95rem;
}

.form-row {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  margin-bottom: 0.6rem;
}

.form-row label {
  font-size: 0.78rem;
  font-weight: 600;
  color: var(--color-text-muted);
}

.code-input {
  font-family: 'SF Mono', 'Menlo', monospace;
  font-size: 0.78rem;
}

.form-actions {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.75rem;
}

/* Case List */
.case-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.case-card {
  padding: 0.75rem 1rem;
}

.case-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
}

.case-info {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.case-name {
  font-size: 0.9rem;
  font-weight: 600;
  color: var(--color-text-primary);
}

.case-meta {
  display: flex;
  gap: 0.75rem;
  font-size: 0.72rem;
  color: var(--color-text-muted);
}

.case-desc {
  font-size: 0.8rem;
  color: var(--color-text-secondary);
  margin-top: 0.35rem;
}

.case-actions {
  display: flex;
  gap: 0.4rem;
  margin-top: 0.5rem;
}

/* Badges */
.badge-panorama { background: #c9a84c; color: white; }
.badge-deep_dive { background: #06b6d4; color: white; }
.badge-trade_review { background: #f59e0b; color: white; }
.badge-what_if { background: #8b5cf6; color: white; }
.badge-ai { background: #10b981; color: white; }
.badge-diversification_ai { background: #ec4899; color: white; }

/* Runs Layout */
.runs-layout {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1rem;
  min-height: 400px;
}

.run-list {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  max-height: 70vh;
  overflow-y: auto;
}

.run-item {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  padding: 0.6rem 0.75rem;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all 0.15s;
}

.run-item:hover {
  border-color: var(--color-primary);
}

.run-item.selected {
  border-color: var(--color-primary);
  background: var(--color-primary-50);
}

.dark .run-item.selected {
  background: var(--color-primary-bg);
}

.run-header {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}

.run-case-name {
  flex: 1;
  font-size: 0.82rem;
  font-weight: 500;
  color: var(--color-text-primary);
}

.run-time {
  font-size: 0.7rem;
  color: var(--color-text-muted);
  white-space: nowrap;
}

.run-sub {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.run-duration {
  font-size: 0.7rem;
  color: var(--color-text-muted);
}

.run-score {
  font-size: 0.75rem;
  font-weight: 700;
  margin-left: auto;
}

.run-score.scoring {
  color: var(--color-text-muted);
  font-weight: 400;
  font-style: italic;
}

/* Run Detail */
.run-detail {
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

.detail-meta {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  font-size: 0.82rem;
  color: var(--color-text-secondary);
  margin-bottom: 1rem;
}

.error-text {
  color: #e53e3e;
}

.detail-section {
  margin-bottom: 0.75rem;
}

.detail-section h5 {
  font-size: 0.78rem;
  font-weight: 600;
  color: var(--color-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.03em;
  margin: 0 0 0.35rem;
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
  margin: 0;
}

.code-full {
  max-height: 400px;
}

.empty-state {
  text-align: center;
  padding: 3rem 1rem;
  color: var(--color-text-muted);
}

.text-muted {
  font-size: 0.82rem;
  color: var(--color-text-muted);
}

</style>
