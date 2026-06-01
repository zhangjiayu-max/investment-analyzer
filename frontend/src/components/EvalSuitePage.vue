<script setup>
import { ref, computed, onMounted, nextTick } from 'vue'
import {
  listEvalCases, createEvalCase, updateEvalCase, deleteEvalCase, runEvalCase,
  listEvalRuns, getEvalRunDetail, getEvalStats, listAgents,
} from '../api'
import ConfirmDialog from './ConfirmDialog.vue'
import AppToast from './AppToast.vue'
import { useToast } from '../composables/useToast'
import { renderMarkdown } from '../composables/useMarkdown'

const { showToast } = useToast()
const confirm = ref({ visible: false, title: '', message: '', danger: false, onConfirm: null })

const loading = ref(false)
const runningCases = ref(new Set()) // 正在运行的用例 ID 集合
const activeTab = ref('cases')

// Stats
const stats = ref({ total_cases: 0, active_cases: 0, total_runs: 0, avg_score: null })

// Cases
const cases = ref([])
const showForm = ref(false)
const editingId = ref(null)
const form = ref({ name: '', description: '', analysis_type: 'ai', input_params: '{}', expected_quality: '' })

// Runs
const runs = ref([])
const selectedRun = ref(null)
const runDetail = ref(null)

// 分析类型选项（从 Agent 列表动态加载）
const analysisTypeOptions = ref([
  { value: 'panorama', label: '全景诊断' },
  { value: 'deep_dive', label: '单基金深度' },
  { value: 'trade_review', label: '交易复盘' },
  { value: 'what_if', label: '情景推演' },
  { value: 'ai', label: 'AI 分析' },
  { value: 'diversification_ai', label: '分散度分析' },
  { value: 'orchestrator', label: '编排器（多Agent）' },
])

// 加载 Agent 列表，补充到分析类型选项
async function loadAgents() {
  try {
    const { data } = await listAgents()
    const agents = data.agents || []
    agents.forEach(agent => {
      // 用 agent_key（如 valuation_expert）作为 value
      const key = agent.agent_key || agent.name.toLowerCase().replace(/\s+/g, '_')
      if (!analysisTypeOptions.value.find(o => o.value === key)) {
        analysisTypeOptions.value.push({ value: key, label: `${agent.icon || '🤖'} ${agent.name}` })
      }
    })
  } catch (e) {
    console.error('Load agents failed:', e)
  }
}

function typeLabel(t) {
  const opt = analysisTypeOptions.value.find(o => o.value === t)
  return opt?.label || t
}

function confirmBeforeDelete(c) {
  confirm.value = {
    visible: true,
    title: '删除确认',
    message: `确定删除评测用例「${c.name}」吗？关联的运行记录也将删除。`,
    danger: true,
    onConfirm: () => { confirm.value.visible = false; doDelete(c.id) }
  }
}

async function doDelete(id) {
  try {
    await deleteEvalCase(id)
    await Promise.all([loadCases(), loadStats()])
    showToast('已删除', 'success')
  } catch (e) {
    showToast('删除失败: ' + e.message, 'error')
  }
}

async function loadStats() {
  try { const { data } = await getEvalStats(); stats.value = data } catch (e) { console.error(e) }
}

async function loadCases() {
  try { const { data } = await listEvalCases(); cases.value = data.cases || [] } catch (e) { console.error(e) }
}

async function loadRuns() {
  try { const { data } = await listEvalRuns(); runs.value = data.runs || [] } catch (e) { console.error(e) }
}

async function loadAll() {
  loading.value = true
  try { await Promise.all([loadStats(), loadCases(), loadRuns(), loadAgents()]) } finally { loading.value = false }
}

function openCreate() {
  form.value = { name: '', description: '', analysis_type: 'ai', input_params: '{}', expected_quality: '' }
  editingId.value = null
  showForm.value = true
}

function openEdit(c) {
  form.value = {
    name: c.name,
    description: c.description || '',
    analysis_type: c.analysis_type,
    input_params: c.input_params || '{}',
    expected_quality: c.expected_quality || '',
  }
  editingId.value = c.id
  showForm.value = true
  // 确保当前 analysis_type 在选项中
  if (!analysisTypeOptions.value.find(o => o.value === c.analysis_type)) {
    analysisTypeOptions.value.push({ value: c.analysis_type, label: c.analysis_type })
  }
  // 滚动到表单位置
  nextTick(() => {
    document.querySelector('.form-card')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  })
}

async function submitForm() {
  if (!form.value.name.trim()) return
  try {
    if (editingId.value) {
      await updateEvalCase(editingId.value, form.value)
      showToast('已更新', 'success')
    } else {
      await createEvalCase(form.value)
      showToast('已创建', 'success')
    }
    showForm.value = false
    editingId.value = null
    await Promise.all([loadCases(), loadStats()])
  } catch (e) {
    showToast('保存失败: ' + e.message, 'error')
  }
}

async function doRun(c) {
  if (runningCases.value.has(c.id)) return
  runningCases.value.add(c.id)
  try {
    const { data } = await runEvalCase(c.id)
    if (data.ok) {
      showToast(`「${c.name}」运行完成`, 'success')
      await Promise.all([loadRuns(), loadStats(), loadCases()])
    } else {
      showToast(`「${c.name}」失败: ${data.error || '未知错误'}`, 'error')
    }
  } catch (e) {
    showToast(`「${c.name}」出错: ${e.message}`, 'error')
  } finally {
    runningCases.value.delete(c.id)
  }
}

// 批量运行所有用例
async function runAll() {
  if (runningCases.value.size > 0) return
  showToast('开始批量运行...', 'info')
  // 并发执行所有用例
  const promises = cases.value.map(c => doRun(c))
  await Promise.all(promises)
  showToast('批量运行完成', 'success')
}

async function viewRunDetail(r) {
  try {
    const { data } = await getEvalRunDetail(r.id)
    selectedRun.value = r
    runDetail.value = data
  } catch (e) { console.error(e) }
}

function formatTime(ts) { return ts ? ts.slice(0, 16) : '' }
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
  if (score >= 8) return '#10b981'
  if (score >= 6) return '#22c55e'
  if (score >= 4) return '#f59e0b'
  if (score >= 2) return '#f97316'
  return '#ef4444'
}

function scoreLabel(score) {
  if (!score || score <= 0) return ''
  if (score >= 9) return '优秀'
  if (score >= 7) return '良好'
  if (score >= 5) return '合格'
  if (score >= 3) return '较差'
  return '不可用'
}

// 解析 result_data，提取 markdown 内容
function extractResult(resultData) {
  if (!resultData) return ''
  try {
    const obj = JSON.parse(resultData)
    // 按优先级提取内容
    if (obj.answer) return obj.answer
    if (obj.result) return obj.result
    if (obj.analysis) return obj.analysis
    // 否则格式化 JSON
    return JSON.stringify(obj, null, 2)
  } catch {
    return resultData
  }
}

// 检查是否是 markdown 内容
function isMarkdown(text) {
  return text && (text.includes('#') || text.includes('**') || text.includes('- ') || text.includes('\n'))
}

const latestRuns = computed(() => runs.value.slice(0, 30))

onMounted(loadAll)
</script>

<template>
  <div class="evalsuite-page">
    <div class="page-header">
      <div>
        <h2 class="page-title">📊 评测集</h2>
        <p class="page-desc">运行测试用例，评估 Agent 分析质量</p>
      </div>
      <div class="header-actions">
        <button class="btn-secondary" @click="loadAll" :disabled="loading">
          {{ loading ? '加载中...' : '🔄 刷新' }}
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
        <span class="stat-label">平均分 /10</span>
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
        <div class="section-actions">
          <button class="btn-secondary btn-sm" @click="runAll" :disabled="runningCases.size > 0">
            {{ runningCases.size > 0 ? `运行中 (${runningCases.size})` : '▶ 全部运行' }}
          </button>
        </div>
        <button class="btn-primary btn-sm" @click="openCreate">+ 新建用例</button>
      </div>

      <!-- Create Form (新建时在顶部) -->
      <Transition name="expand">
        <div v-if="showForm && !editingId" class="form-card card">
          <h4>新建评测用例</h4>
          <div class="form-grid">
            <div class="form-row">
              <label>名称 *</label>
              <input v-model="form.name" class="input-field" placeholder="例: 沪深300估值分析" />
            </div>
            <div class="form-row">
              <label>分析类型</label>
              <select v-model="form.analysis_type" class="input-field">
                <option v-for="o in analysisTypeOptions" :key="o.value" :value="o.value">{{ o.label }}</option>
              </select>
            </div>
          </div>
          <div class="form-row">
            <label>描述</label>
            <input v-model="form.description" class="input-field" placeholder="用例说明" />
          </div>
          <div class="form-row">
            <label>参数 (JSON)</label>
            <textarea v-model="form.input_params" class="input-field code-input" rows="2"
              placeholder='{"question": "沪深300估值怎么样？"}'></textarea>
          </div>
          <div class="form-row">
            <label>预期质量标准</label>
            <textarea v-model="form.expected_quality" class="input-field" rows="3"
              placeholder="1. 必须引用具体数据&#10;2. 必须给出明确建议&#10;3. 必须附带风险提示"></textarea>
          </div>
          <div class="form-actions">
            <button class="btn-primary" @click="submitForm">创建</button>
            <button class="btn-secondary" @click="showForm = false">取消</button>
          </div>
        </div>
      </Transition>

      <!-- Case List -->
      <div v-if="!loading && cases.length === 0" class="empty-state">
        <p>暂无评测用例</p>
        <p class="text-muted">点击「新建用例」创建第一个评测</p>
      </div>

      <div v-else class="case-list">
        <template v-for="c in cases" :key="c.id">
          <!-- 用例卡片 -->
          <div class="case-card card">
            <div class="case-header">
              <div class="case-info">
                <span class="badge badge-sm" :class="'badge-' + c.analysis_type">{{ typeLabel(c.analysis_type) }}</span>
                <span class="case-name">{{ c.name }}</span>
              </div>
              <div class="case-meta">
                <span class="case-stat">运行 {{ c.run_count || 0 }} 次</span>
                <span v-if="c.avg_score" class="case-stat" :style="{ color: scoreColor(c.avg_score) }">
                  平均 {{ Number(c.avg_score).toFixed(1) }}分
                </span>
              </div>
            </div>
            <div v-if="c.description" class="case-desc">{{ c.description }}</div>
            <div class="case-actions">
              <button class="btn-primary btn-xs" :disabled="runningCases.has(c.id)" @click="doRun(c)">
                {{ runningCases.has(c.id) ? '⏳ 运行中' : '▶ 运行' }}
              </button>
              <button class="btn-secondary btn-xs" @click="openEdit(c)">✏️ 编辑</button>
              <button class="btn-danger btn-xs" @click="confirmBeforeDelete(c)">🗑️</button>
            </div>
          </div>

          <!-- 编辑表单：展开在用例下方 -->
          <Transition name="expand">
            <div v-if="showForm && editingId === c.id" class="form-card card">
              <h4>编辑评测用例</h4>
              <div class="form-grid">
                <div class="form-row">
                  <label>名称 *</label>
                  <input v-model="form.name" class="input-field" />
                </div>
                <div class="form-row">
                  <label>分析类型</label>
                  <select v-model="form.analysis_type" class="input-field">
                    <option v-for="o in analysisTypeOptions" :key="o.value" :value="o.value">{{ o.label }}</option>
                  </select>
                </div>
              </div>
              <div class="form-row">
                <label>描述</label>
                <input v-model="form.description" class="input-field" />
              </div>
              <div class="form-row">
                <label>参数 (JSON)</label>
                <textarea v-model="form.input_params" class="input-field code-input" rows="2"></textarea>
              </div>
              <div class="form-row">
                <label>预期质量标准</label>
                <textarea v-model="form.expected_quality" class="input-field" rows="3"></textarea>
              </div>
              <div class="form-actions">
                <button class="btn-primary" @click="submitForm">保存</button>
                <button class="btn-secondary" @click="showForm = false; editingId = null">取消</button>
              </div>
            </div>
          </Transition>
        </template>
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
                {{ Number(r.score).toFixed(0) }}/10
              </span>
              <span v-else class="run-score scoring">评分中...</span>
            </div>
          </div>
        </div>

        <!-- Run Detail -->
        <div class="run-detail" v-if="runDetail">
          <div class="detail-header">
            <h4 class="section-title">运行详情</h4>
            <button class="btn-secondary btn-xs" @click="runDetail = null; selectedRun = null">✕ 关闭</button>
          </div>

          <!-- Meta Grid -->
          <div class="detail-meta-grid">
            <div class="meta-item">
              <span class="meta-label">用例</span>
              <span class="meta-value">{{ runDetail.case_name }}</span>
            </div>
            <div class="meta-item">
              <span class="meta-label">类型</span>
              <span class="meta-value">{{ typeLabel(runDetail.analysis_type) }}</span>
            </div>
            <div class="meta-item">
              <span class="meta-label">耗时</span>
              <span class="meta-value">{{ formatDuration(runDetail.duration_ms) }}</span>
            </div>
            <div class="meta-item">
              <span class="meta-label">Token</span>
              <span class="meta-value">{{ (runDetail.token_usage || 0).toLocaleString() }}</span>
            </div>
          </div>

          <!-- Score Card -->
          <div v-if="runDetail.score !== null && runDetail.score > 0" class="score-card">
            <div class="score-big" :style="{ color: scoreColor(runDetail.score) }">
              {{ Number(runDetail.score).toFixed(0) }}
              <span class="score-max">/10</span>
            </div>
            <div class="score-label" :style="{ color: scoreColor(runDetail.score) }">
              {{ scoreLabel(runDetail.score) }}
            </div>
            <div v-if="runDetail.score_reason" class="score-reason">
              {{ runDetail.score_reason }}
            </div>
          </div>

          <!-- Error -->
          <div v-if="runDetail.error_msg" class="error-card">
            <span class="error-icon">❌</span>
            <span>{{ runDetail.error_msg }}</span>
          </div>

          <!-- Input Params -->
          <div v-if="runDetail.input_params && runDetail.input_params !== '{}'" class="detail-section">
            <h5>📥 输入参数</h5>
            <pre class="code-block">{{ (() => { try { return JSON.stringify(JSON.parse(runDetail.input_params), null, 2) } catch { return runDetail.input_params } })() }}</pre>
          </div>

          <!-- Expected Quality -->
          <div v-if="runDetail.expected_quality" class="detail-section">
            <h5>📋 预期质量标准</h5>
            <pre class="code-block quality-block">{{ runDetail.expected_quality }}</pre>
          </div>

          <!-- Full Result -->
          <div v-if="runDetail.result_data" class="detail-section">
            <h5>📤 完整结果</h5>
            <div v-if="isMarkdown(extractResult(runDetail.result_data))" class="result-content markdown-body" v-html="renderMarkdown(extractResult(runDetail.result_data))"></div>
            <pre v-else class="code-block result-block">{{ extractResult(runDetail.result_data) }}</pre>
          </div>
        </div>

        <div v-else class="run-detail empty-detail">
          <p class="text-muted">← 选择一条运行记录查看详情</p>
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

.section-actions {
  display: flex;
  gap: 0.5rem;
}

.section-title {
  font-size: 0.9rem;
  font-weight: 600;
}

/* Form */
.form-card {
  padding: 1.25rem;
  margin-bottom: 1rem;
  background: var(--color-bg-secondary);
}

.form-card h4 {
  margin-bottom: 1rem;
  font-size: 1rem;
}

.form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.75rem;
}

.form-row {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  margin-bottom: 0.75rem;
}

.form-row label {
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--color-text-secondary);
}

.code-input {
  font-family: 'SF Mono', 'Fira Code', monospace;
  font-size: 0.8rem;
}

.form-actions {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.5rem;
}

/* Case List */
.case-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.case-card {
  padding: 0.85rem 1rem;
}

.case-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
}

.case-info {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.case-name {
  font-weight: 600;
  font-size: 0.9rem;
}

.case-meta {
  display: flex;
  gap: 0.75rem;
  font-size: 0.8rem;
  color: var(--color-text-muted);
}

.case-desc {
  font-size: 0.8rem;
  color: var(--color-text-secondary);
  margin: 0.35rem 0;
}

.case-actions {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.5rem;
}

/* Run List */
.run-list {
  width: 320px;
  flex-shrink: 0;
  border-right: 1px solid var(--color-border);
  padding-right: 1rem;
  overflow-y: auto;
}

.run-item {
  padding: 0.6rem 0.75rem;
  border-radius: var(--radius-md);
  cursor: pointer;
  margin-bottom: 0.35rem;
  transition: background 0.15s;
}

.run-item:hover {
  background: var(--color-bg-hover);
}

.run-item.selected {
  background: var(--color-primary-50);
  border-left: 3px solid var(--color-primary-500);
}

.run-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.run-case-name {
  font-weight: 600;
  font-size: 0.85rem;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.run-time {
  font-size: 0.72rem;
  color: var(--color-text-muted);
}

.run-sub {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-top: 0.25rem;
  padding-left: 1.5rem;
}

.run-duration {
  font-size: 0.75rem;
  color: var(--color-text-muted);
}

.run-score {
  font-weight: 700;
  font-size: 0.85rem;
}

.run-score.scoring {
  color: var(--color-text-muted);
  font-weight: 400;
  font-size: 0.75rem;
}

/* Run Detail */
.run-detail {
  flex: 1;
  padding-left: 1rem;
  overflow-y: auto;
}

.detail-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 1rem;
}

.detail-meta-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 0.75rem;
  margin-bottom: 1rem;
}

.meta-item {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}

.meta-label {
  font-size: 0.72rem;
  color: var(--color-text-muted);
}

.meta-value {
  font-size: 0.9rem;
  font-weight: 600;
}

/* Score Card */
.score-card {
  background: linear-gradient(135deg, var(--color-bg-secondary), var(--color-bg-primary));
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 1.25rem;
  text-align: center;
  margin-bottom: 1rem;
}

.score-big {
  font-size: 3rem;
  font-weight: 800;
  line-height: 1;
}

.score-max {
  font-size: 1.25rem;
  font-weight: 400;
  color: var(--color-text-muted);
}

.score-label {
  font-size: 1rem;
  font-weight: 600;
  margin-top: 0.25rem;
}

.score-reason {
  font-size: 0.8rem;
  color: var(--color-text-secondary);
  margin-top: 0.75rem;
  padding-top: 0.75rem;
  border-top: 1px solid var(--color-border-light);
  text-align: left;
  line-height: 1.5;
}

/* Error Card */
.error-card {
  background: #fef2f2;
  border: 1px solid #fecaca;
  border-radius: var(--radius-md);
  padding: 0.75rem 1rem;
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
  margin-bottom: 1rem;
  font-size: 0.85rem;
  color: #991b1b;
}

/* Detail Sections */
.detail-section {
  margin-bottom: 1rem;
}

.detail-section h5 {
  font-size: 0.85rem;
  font-weight: 600;
  margin-bottom: 0.5rem;
  color: var(--color-text-secondary);
}

.code-block {
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: 0.75rem 1rem;
  font-size: 0.8rem;
  font-family: 'SF Mono', 'Fira Code', monospace;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 400px;
  overflow-y: auto;
}

.quality-block {
  background: #fefce8;
  border-color: #fde047;
}

.result-block {
  max-height: 600px;
}

.result-content {
  background: var(--color-bg-primary);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: 1rem 1.25rem;
  max-height: 600px;
  overflow-y: auto;
  font-size: 0.85rem;
  line-height: 1.6;
}

.result-content :deep(h1),
.result-content :deep(h2),
.result-content :deep(h3) {
  margin-top: 1rem;
  margin-bottom: 0.5rem;
  font-weight: 700;
}

.result-content :deep(h1) { font-size: 1.1rem; }
.result-content :deep(h2) { font-size: 1rem; }
.result-content :deep(h3) { font-size: 0.95rem; }

.result-content :deep(p) {
  margin-bottom: 0.5rem;
}

.result-content :deep(ul),
.result-content :deep(ol) {
  padding-left: 1.5rem;
  margin-bottom: 0.5rem;
}

.result-content :deep(li) {
  margin-bottom: 0.25rem;
}

.result-content :deep(strong) {
  font-weight: 700;
  color: var(--color-text-primary);
}

.result-content :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 0.5rem 0;
  font-size: 0.8rem;
}

.result-content :deep(th),
.result-content :deep(td) {
  padding: 0.4rem 0.6rem;
  border: 1px solid var(--color-border);
  text-align: left;
}

.result-content :deep(th) {
  background: var(--color-bg-secondary);
  font-weight: 600;
}

.result-content :deep(code) {
  background: var(--color-bg-secondary);
  padding: 0.15rem 0.35rem;
  border-radius: 3px;
  font-size: 0.8rem;
  font-family: 'SF Mono', 'Fira Code', monospace;
}

.result-content :deep(pre) {
  background: var(--color-bg-secondary);
  padding: 0.75rem;
  border-radius: var(--radius-md);
  overflow-x: auto;
  margin: 0.5rem 0;
}

.result-content :deep(pre code) {
  background: none;
  padding: 0;
}

.result-content :deep(blockquote) {
  border-left: 3px solid var(--color-primary-400);
  padding-left: 0.75rem;
  margin: 0.5rem 0;
  color: var(--color-text-secondary);
}

.empty-detail {
  display: flex;
  align-items: center;
  justify-content: center;
}

.runs-layout {
  display: flex;
  gap: 1rem;
}

/* Buttons */
.btn-xs {
  padding: 0.3rem 0.6rem;
  font-size: 0.75rem;
}

.btn-sm {
  padding: 0.45rem 0.85rem;
  font-size: 0.8rem;
}

/* Transition */
.expand-enter-active,
.expand-leave-active {
  transition: all 0.2s ease;
  overflow: hidden;
}

.expand-enter-from,
.expand-leave-to {
  opacity: 0;
  max-height: 0;
}

.expand-enter-to,
.expand-leave-from {
  opacity: 1;
  max-height: 500px;
}

/* Responsive */
@media (max-width: 768px) {
  .runs-layout {
    flex-direction: column;
  }
  .run-list {
    width: 100%;
    border-right: none;
    border-bottom: 1px solid var(--color-border);
    padding-right: 0;
    padding-bottom: 1rem;
  }
  .form-grid {
    grid-template-columns: 1fr;
  }
  .detail-meta-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}
</style>
