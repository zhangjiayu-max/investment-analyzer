<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import {
  createGoalBucket,
  deleteGoalBucket,
  listGoalBuckets,
  updateGoalBucket,
  syncGoalBuckets,
  listDecisions,
  getDecisionPrecheck,
} from '../api'
import { useToast } from '../composables/useToast'
import ConfirmDialog from './ConfirmDialog.vue'
import Icon from './ui/Icon.vue'

const { showToast } = useToast()

const BUCKET_TYPES = [
  { value: 'emergency', label: '备用金桶', desc: '安全和流动性优先，禁止高波动资产' },
  { value: 'stable', label: '稳健增值桶', desc: '1-3 年资金，关注回撤和赎回摩擦' },
  { value: 'long_term', label: '长期权益桶', desc: '3 年以上资金，用于估值和再平衡' },
  { value: 'opportunity', label: '机会资金桶', desc: '低估或主题机会，控制比例和退出条件' },
  { value: 'learning', label: '学习试错桶', desc: '小仓位实验，不影响主组合' },
]

const RISK_LEVELS = [
  { value: 'very_low', label: '极低' },
  { value: 'low', label: '低' },
  { value: 'medium', label: '中' },
  { value: 'medium_high', label: '中高' },
  { value: 'high', label: '高' },
]

const loading = ref(false)
const saving = ref(false)
const deleting = ref(false)
const items = ref([])
const summary = ref({})
const editingId = ref(null)
const confirmDelete = ref({ visible: false, item: null })

const form = reactive({
  name: '',
  bucket_type: 'emergency',
  target_amount: 0,
  current_amount: 0,
  target_ratio: 0,
  risk_level: 'very_low',
  liquidity_days: 1,
  priority: 1,
  notes: '',
})

const activeType = computed(() => BUCKET_TYPES.find(t => t.value === form.bucket_type) || BUCKET_TYPES[0])
const sortedItems = computed(() => items.value || [])
const emergencyReady = computed(() => {
  const bucket = summary.value?.emergency_bucket
  return bucket && Number(bucket.progress_pct || 0) >= 100
})

function money(value) {
  const n = Number(value || 0)
  return n.toLocaleString('zh-CN', { maximumFractionDigits: 0 })
}

function pct(value) {
  const n = Number(value || 0)
  return `${Math.round(n * 100)}%`
}

function typeLabel(type) {
  return BUCKET_TYPES.find(t => t.value === type)?.label || type
}

function riskLabel(level) {
  return RISK_LEVELS.find(r => r.value === level)?.label || level || '未设置'
}

function resetForm() {
  editingId.value = null
  Object.assign(form, {
    name: '',
    bucket_type: 'emergency',
    target_amount: 0,
    current_amount: 0,
    target_ratio: 0,
    risk_level: 'very_low',
    liquidity_days: 1,
    priority: 1,
    notes: '',
  })
}

async function load() {
  loading.value = true
  try {
    const { data } = await listGoalBuckets()
    items.value = data.items || []
    summary.value = data.summary || {}
  } catch (e) {
    showToast('加载资金桶失败: ' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    loading.value = false
  }
}

const syncing = ref(false)
async function syncFromPortfolio() {
  syncing.value = true
  try {
    const { data } = await syncGoalBuckets()
    items.value = data.buckets || items.value
    showToast(`已同步 ${data.synced || 0} 个资金桶`, 'success')
  } catch (e) {
    showToast('同步失败: ' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    syncing.value = false
  }
}

function editItem(item) {
  editingId.value = item.id
  Object.assign(form, {
    name: item.name || '',
    bucket_type: item.bucket_type || 'stable',
    target_amount: item.target_amount || 0,
    current_amount: item.current_amount || 0,
    target_ratio: item.target_ratio || 0,
    risk_level: item.risk_level || 'medium',
    liquidity_days: item.liquidity_days ?? 365,
    priority: item.priority ?? 3,
    notes: item.notes || '',
  })
}

async function save() {
  if (!form.name.trim()) {
    showToast('请填写资金桶名称', 'warning')
    return
  }
  saving.value = true
  try {
    const payload = {
      name: form.name.trim(),
      bucket_type: form.bucket_type,
      target_amount: Number(form.target_amount || 0),
      current_amount: Number(form.current_amount || 0),
      target_ratio: Number(form.target_ratio || 0),
      risk_level: form.risk_level,
      liquidity_days: Number(form.liquidity_days || 0),
      priority: Number(form.priority || 3),
      notes: form.notes.trim(),
    }
    if (editingId.value) {
      await updateGoalBucket(editingId.value, payload)
      showToast('资金桶已更新', 'success')
    } else {
      await createGoalBucket(payload)
      showToast('资金桶已创建', 'success')
    }
    resetForm()
    await load()
  } catch (e) {
    showToast('保存失败: ' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    saving.value = false
  }
}

function askDelete(item) {
  confirmDelete.value = { visible: true, item }
}

async function doDelete() {
  const item = confirmDelete.value.item
  if (!item) return
  deleting.value = true
  try {
    await deleteGoalBucket(item.id)
    showToast('资金桶已删除', 'success')
    confirmDelete.value = { visible: false, item: null }
    if (editingId.value === item.id) resetForm()
    await load()
  } catch (e) {
    showToast('删除失败: ' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    deleting.value = false
  }
}

onMounted(load)

// ── 决策预检查展示 ──
const recentDecisions = ref([])
const precheckMap = ref({})  // { decisionId: precheck }

async function loadRecentDecisions() {
  try {
    const { data } = await listDecisions('', 10)
    recentDecisions.value = (data.items || []).filter(d =>
      ['proposed', 'accepted'].includes(d.status)
    )
    // 逐个加载预检查
    recentDecisions.value.forEach(async d => {
      try {
        const { data: pc } = await getDecisionPrecheck(d.id)
        precheckMap.value[d.id] = pc
      } catch { /* silent */ }
    })
  } catch { /* silent */ }
}

function bucketCheckFor(decisionId) {
  const pc = precheckMap.value[decisionId]
  if (!pc || !pc.bucket_check) return null
  return pc.bucket_check
}

onMounted(loadRecentDecisions)
</script>

<template>
  <div class="goal-page bg-mesh">
    <header class="goal-header">
      <div>
        <h2 class="page-title editorial-title-lg">目标账户 / 资金桶</h2>
        <p class="page-desc">按资金用途、期限和风险边界分层，后续决策检查会读取这里的约束。</p>
      </div>
      <div class="header-actions">
        <button class="btn-secondary" @click="syncFromPortfolio" :disabled="syncing">
          <Icon :name="syncing ? 'spinner' : 'download'" size="16" />
          从持仓同步
        </button>
        <button class="btn-secondary" @click="load" :disabled="loading">
          <Icon :name="loading ? 'spinner' : 'refresh'" size="16" />
          刷新
        </button>
      </div>
    </header>

    <section class="summary-strip">
      <div class="summary-cell">
        <span class="terminal-label">资金桶</span>
        <strong class="font-jet">{{ summary.count || 0 }}</strong>
      </div>
      <div class="summary-cell">
        <span class="terminal-label">已归集</span>
        <strong class="font-jet">¥{{ money(summary.total_current_amount) }}</strong>
      </div>
      <div class="summary-cell">
        <span class="terminal-label">目标金额</span>
        <strong class="font-jet">¥{{ money(summary.total_target_amount) }}</strong>
      </div>
      <div :class="['summary-cell', emergencyReady ? 'ok' : 'warn']">
        <span class="terminal-label">备用金</span>
        <strong>{{ emergencyReady ? '已覆盖' : '需确认' }}</strong>
      </div>
    </section>

    <main class="goal-layout">
      <section class="bucket-list">
        <div v-if="loading && !sortedItems.length" class="empty-state">
          <Icon name="spinner" size="22" />
          <span>正在读取资金分层...</span>
        </div>
        <div v-else-if="!sortedItems.length" class="empty-state">
          <Icon name="wallet" size="26" />
          <span>还没有资金桶，先建立备用金桶和长期权益桶。</span>
        </div>
        <article
          v-for="item in sortedItems"
          :key="item.id"
          :class="['bucket-card', 'editorial-card', 'reveal-stagger', { active: editingId === item.id, blocked: item.guardrail_level === 'blocked_for_risk_assets' }]"
        >
          <div class="bucket-top">
            <div>
              <div class="bucket-title-row">
                <h3 class="editorial-title">{{ item.name }}</h3>
                <span class="type-badge">{{ typeLabel(item.bucket_type) }}</span>
              </div>
              <p>{{ item.notes || '未填写备注' }}</p>
            </div>
            <div class="bucket-actions">
              <button class="icon-btn" title="编辑" @click="editItem(item)">
                <Icon name="pencil" size="15" />
              </button>
              <button class="icon-btn danger" title="删除" @click="askDelete(item)">
                <Icon name="trash" size="15" />
              </button>
            </div>
          </div>
          <div class="progress-track">
            <div class="progress-bar" :style="{ width: `${Math.min(item.progress_pct || 0, 100)}%` }"></div>
          </div>
          <div class="bucket-meta">
            <span><span class="font-jet">¥{{ money(item.current_amount) }}</span> / <span class="font-jet">¥{{ money(item.target_amount) }}</span></span>
            <span>进度 <span class="font-jet">{{ item.progress_pct || 0 }}%</span></span>
            <span>目标占比 <span class="font-jet">{{ pct(item.target_ratio) }}</span></span>
            <span>风险 {{ riskLabel(item.risk_level) }}</span>
            <span><span class="font-jet">{{ item.liquidity_days || 0 }}</span> 天内可动用</span>
          </div>
          <div v-if="item.guardrail_level === 'blocked_for_risk_assets'" class="guardrail">
            <Icon name="shield-alert" size="14" />
            决策预检查会拦截使用此资金桶买入或加仓高波动资产。
          </div>
        </article>
      </section>

      <aside class="bucket-form editorial-card">
        <div class="form-head">
          <h3 class="editorial-title">{{ editingId ? '编辑资金桶' : '新增资金桶' }}</h3>
          <button v-if="editingId" class="btn-ghost btn-sm" @click="resetForm">取消编辑</button>
        </div>
        <label>
          名称
          <input v-model="form.name" class="input-field" placeholder="如：家庭备用金" />
        </label>
        <label>
          类型
          <select v-model="form.bucket_type" class="input-field">
            <option v-for="type in BUCKET_TYPES" :key="type.value" :value="type.value">{{ type.label }}</option>
          </select>
        </label>
        <p class="type-desc">{{ activeType.desc }}</p>
        <div class="form-grid">
          <label>
            目标金额
            <input v-model.number="form.target_amount" type="number" min="0" class="input-field" />
          </label>
          <label>
            当前金额
            <input v-model.number="form.current_amount" type="number" min="0" class="input-field" />
          </label>
          <label>
            目标占比
            <input v-model.number="form.target_ratio" type="number" min="0" max="1" step="0.01" class="input-field" />
          </label>
          <label>
            可动用天数
            <input v-model.number="form.liquidity_days" type="number" min="0" class="input-field" />
          </label>
          <label>
            风险等级
            <select v-model="form.risk_level" class="input-field">
              <option v-for="risk in RISK_LEVELS" :key="risk.value" :value="risk.value">{{ risk.label }}</option>
            </select>
          </label>
          <label>
            优先级
            <input v-model.number="form.priority" type="number" min="1" max="9" class="input-field" />
          </label>
        </div>
        <label>
          备注
          <textarea v-model="form.notes" class="input-field notes-input" rows="4" placeholder="用途、不可动用期限、家庭共识、退出条件等"></textarea>
        </label>
        <button class="btn-primary save-btn" @click="save" :disabled="saving">
          <Icon :name="saving ? 'spinner' : 'check'" size="16" />
          {{ editingId ? '保存修改' : '创建资金桶' }}
        </button>
      </aside>
    </main>

    <!-- 决策预检查展示 -->
    <section v-if="recentDecisions.length" class="precheck-section editorial-card">
      <h3 class="section-title editorial-title">
        <Icon name="shield-check" size="16" />
        决策预检查（资金桶拦截）
      </h3>
      <p class="section-desc">展示待执行决策与资金桶的约束关系。被拦截的决策无法执行加仓。</p>
      <div class="precheck-list">
        <article v-for="d in recentDecisions" :key="d.id" class="precheck-card reveal-stagger">
          <div class="precheck-top">
            <strong>{{ d.summary }}</strong>
            <span :class="['precheck-status', bucketCheckFor(d.id)?.blocked ? 'blocked' : 'ok']">
              {{ bucketCheckFor(d.id)?.blocked ? '拦截' : '通过' }}
            </span>
          </div>
          <div v-if="bucketCheckFor(d.id)" class="precheck-detail">
            <span>资金桶：{{ bucketCheckFor(d.id).bucket_name || '-' }}</span>
            <span v-if="bucketCheckFor(d.id).reason">原因：{{ bucketCheckFor(d.id).reason }}</span>
          </div>
          <div v-else class="precheck-detail">
            <span class="muted">加载中...</span>
          </div>
        </article>
      </div>
    </section>

    <ConfirmDialog
      :visible="confirmDelete.visible"
      title="删除资金桶"
      :message="`确定删除「${confirmDelete.item?.name || ''}」吗？相关历史决策不会删除，但之后无法再引用这个资金桶。`"
      confirm-text="删除"
      danger
      :loading="deleting"
      @confirm="doDelete"
      @cancel="confirmDelete = { visible: false, item: null }"
    />
  </div>
</template>

<style scoped>
.goal-page {
  padding: var(--space-6);
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.goal-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: var(--space-4);
}

.header-actions {
  display: flex;
  gap: var(--space-2);
  flex-shrink: 0;
}

.summary-strip {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-bg-card);
  overflow: hidden;
}

.summary-cell {
  padding: var(--space-4);
  border-right: 1px solid var(--color-border-light);
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.summary-cell:last-child { border-right: 0; }
.summary-cell span {
  font-size: inherit;
  color: var(--color-text-muted);
}
.summary-cell strong {
  font-size: 1.1rem;
  color: var(--color-text-primary);
}
.summary-cell.ok strong { color: var(--color-success); }
.summary-cell.warn strong { color: var(--color-warning); }

.goal-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 360px;
  gap: var(--space-5);
  align-items: start;
}

.bucket-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.bucket-card {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: var(--space-4);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  transition: border-color var(--transition-fast), box-shadow var(--transition-fast);
}
.bucket-card:hover,
.bucket-card.active {
  border-color: var(--color-primary-border-strong);
  box-shadow: var(--shadow-sm);
}
.bucket-card.blocked {
  border-left: 3px solid var(--color-warning);
}

.bucket-top {
  display: flex;
  justify-content: space-between;
  gap: var(--space-3);
}
.bucket-title-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}
.bucket-title-row h3 {
  margin: 0;
  font-size: 1rem;
  color: var(--color-text-primary);
}
.bucket-top p {
  margin: 6px 0 0;
  color: var(--color-text-secondary);
  font-size: 0.85rem;
  line-height: 1.6;
}

.type-badge {
  border: 1px solid var(--color-primary-border);
  background: var(--color-primary-bg);
  color: var(--color-primary);
  border-radius: var(--radius-sm);
  padding: 2px 7px;
  font-size: 0.74rem;
  font-weight: 600;
}

.bucket-actions {
  display: flex;
  gap: 6px;
}
.icon-btn {
  width: 30px;
  height: 30px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  color: var(--color-text-secondary);
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.icon-btn:hover { background: var(--color-bg-hover); color: var(--color-text-primary); }
.icon-btn.danger:hover { background: var(--color-danger-bg); color: var(--color-danger); }

.progress-track {
  height: 8px;
  background: var(--color-bg-input);
  border-radius: 999px;
  overflow: hidden;
}
.progress-bar {
  height: 100%;
  background: linear-gradient(90deg, var(--color-success), var(--color-primary));
  border-radius: inherit;
}

.bucket-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.bucket-meta span {
  background: var(--color-bg-input);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-sm);
  padding: 4px 8px;
  color: var(--color-text-secondary);
  font-size: 0.78rem;
}

.guardrail {
  display: flex;
  gap: 6px;
  align-items: center;
  color: var(--color-warning-text);
  background: var(--color-warning-bg);
  border: 1px solid var(--color-warning-border);
  border-radius: var(--radius-md);
  padding: 8px 10px;
  font-size: 0.8rem;
}

.bucket-form {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: var(--space-4);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  position: sticky;
  top: var(--space-4);
}
.form-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-2);
}
.form-head h3 {
  margin: 0;
  color: var(--color-text-primary);
  font-size: 1rem;
}
.bucket-form label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  color: var(--color-text-secondary);
  font-size: 0.8rem;
  font-weight: 600;
}
.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-3);
}
.type-desc {
  margin: 0;
  color: var(--color-text-muted);
  background: var(--color-bg-input);
  border-radius: var(--radius-md);
  padding: 9px 10px;
  line-height: 1.6;
}
.notes-input {
  resize: vertical;
  min-height: 96px;
}
.save-btn {
  min-height: 40px;
}

.empty-state {
  min-height: 220px;
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-lg);
  color: var(--color-text-muted);
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  background: var(--color-bg-card);
}

@media (max-width: 1100px) {
  .goal-layout { grid-template-columns: 1fr; }
  .bucket-form { position: static; }
}

@media (max-width: 760px) {
  .goal-page { padding: var(--space-4); }
  .goal-header { flex-direction: column; }
  .summary-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .summary-cell:nth-child(2) { border-right: 0; }
  .summary-cell:nth-child(-n + 2) { border-bottom: 1px solid var(--color-border-light); }
  .bucket-top { flex-direction: column; }
  .form-grid { grid-template-columns: 1fr; }
}

/* ── 移动端响应式 (<768px) ── */
@media (max-width: 768px) {
  .goal-page {
    padding: var(--space-3);
  }

  /* 页头 */
  .goal-header {
    flex-direction: column;
    gap: var(--space-2);
  }
  .page-title { font-size: inherit; }
  .page-desc { font-size: 0.78rem; }

  /* 汇总条：单行显示 */
  .summary-strip {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .summary-cell {
    padding: var(--space-3);
  }
  .summary-cell strong {
    font-size: 1.2rem;
  }

  /* 布局：单列 */
  .goal-layout {
    grid-template-columns: 1fr;
  }
  .bucket-form { position: static; }

  /* 进度条放大 */
  .progress-track {
    height: 12px;
  }

  /* 金额字体放大 */
  .bucket-meta {
    gap: 6px;
  }
  .bucket-meta span {
    font-size: 0.85rem;
    padding: 6px 10px;
  }

  /* 卡片全宽 */
  .bucket-card {
    width: 100%;
  }

  /* 表单 */
  .form-grid { grid-template-columns: 1fr; }
  .bucket-form label {
    font-size: 0.85rem;
  }
  .input-field {
    font-size: 16px; /* 防止iOS缩放 */
  }
}

/* ── 决策预检查展示 ── */
.precheck-section {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-bg-card);
  padding: var(--space-4);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.section-title {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  font-size: 0.95rem;
  color: var(--color-text-primary);
}
.section-desc {
  margin: 0;
  font-size: 0.78rem;
  color: var(--color-text-muted);
}
.precheck-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.precheck-card {
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  padding: 10px 12px;
  background: var(--color-bg-input);
}
.precheck-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}
.precheck-top strong {
  font-size: 0.85rem;
  color: var(--color-text-primary);
}
.precheck-status {
  font-size: 0.72rem;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: var(--radius-sm);
}
.precheck-status.ok {
  color: var(--color-success);
  background: rgba(5, 150, 105, 0.1);
}
.precheck-status.blocked {
  color: var(--color-danger);
  background: rgba(220, 38, 38, 0.1);
}
.precheck-detail {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 6px;
  font-size: 0.76rem;
  color: var(--color-text-secondary);
}
.precheck-detail .muted {
  color: var(--color-text-muted);
}
</style>
