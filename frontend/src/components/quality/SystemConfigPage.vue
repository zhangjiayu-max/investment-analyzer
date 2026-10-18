<script setup>
import { ref, computed, onMounted } from 'vue'
import { getSystemConfigs, updateSystemConfig, resetSystemConfigs } from '../../api'
import ConfirmDialog from '../layout/ConfirmDialog.vue'
import AnalysisDependencyStatus from './AnalysisDependencyStatus.vue'

const loading = ref(true)
const configs = ref([])
const editingKey = ref(null)
const editingValue = ref('')
const confirm = ref({ visible: false, title: '', message: '', danger: false, onConfirm: null })
const filterCategory = ref('')
const searchQuery = ref('')
const error = ref('')
const saving = ref(false)

const categories = computed(() => {
  const cats = [...new Set(configs.value.map(c => c.category))]
  return cats.sort()
})

const filteredConfigs = computed(() => {
  let result = configs.value
  if (filterCategory.value) {
    result = result.filter(c => c.category === filterCategory.value)
  }
  if (searchQuery.value) {
    const q = searchQuery.value.toLowerCase()
    result = result.filter(c =>
      c.key.toLowerCase().includes(q) ||
      (c.description || '').toLowerCase().includes(q) ||
      String(c.value || '').toLowerCase().includes(q)
    )
  }
  return result
})

const categoryLabels = {
  valuation: '估值阈值',
  bond: '债券配置',
  portfolio: '持仓管理',
  llm: 'LLM 参数',
  watchlist: '关注列表信号',
  alerts: '预警系统',
  smart_add: '智能补仓',
  agent: 'Agent 系统',
  decision_upgrade: '理财决策升级',
  opportunity: '机会雷达',
  general: '通用',
}

async function loadConfigs() {
  loading.value = true
  error.value = ''
  try {
    const { data } = await getSystemConfigs()
    configs.value = data.configs || []
  } catch (e) {
    error.value = '配置加载失败：' + e.message
  } finally {
    loading.value = false
  }
}

function startEdit(key, value) {
  if (saving.value) return
  editingKey.value = key
  editingValue.value = value
}

function cancelEdit() {
  editingKey.value = null
  editingValue.value = ''
}

async function saveEdit(key) {
  if (saving.value) return
  const value = String(editingValue.value)
  const old = configs.value.find(c => c.key === key)?.value
  confirm.value = {
    visible: true, title: '保存配置',
    message: `${key}：${old} → ${value}。确认应用此变更？`, danger: false,
    onConfirm: async () => {
      if (saving.value) return
      saving.value = true
      error.value = ''
      try {
        await updateSystemConfig(key, value)
        await loadConfigs()
        cancelEdit()
      } catch (e) {
        error.value = '配置保存失败：' + (e.response?.data?.detail || e.message)
      } finally {
        saving.value = false
        confirm.value.visible = false
      }
    },
  }
}

function confirmReset() {
  if (saving.value) return
  confirm.value = {
    visible: true,
    title: '重置配置',
    message: '确定要将所有配置重置为默认值吗？此操作不可撤销。',
    danger: true,
    onConfirm: async () => {
      if (saving.value) return
      saving.value = true
      try {
        error.value = ''
        await resetSystemConfigs()
        await loadConfigs()
      } catch (e) {
        error.value = '配置重置失败：' + e.message
      } finally {
        saving.value = false
        confirm.value.visible = false
      }
    }
  }
}

function getCategoryLabel(cat) {
  return categoryLabels[cat] || cat
}

onMounted(loadConfigs)
</script>

<template>
  <div class="config-page bg-mesh">
    <div class="page-header">
      <h2 class="page-title editorial-title-lg">系统配置</h2>
      <span class="page-desc editorial-subtitle">管理估值阈值、LLM 参数等业务配置</span>
      <div style="display: flex; gap: 0.5rem; margin-left: auto;">
        <button class="btn-outline btn-sm" @click="loadConfigs" :disabled="loading">
          <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
          刷新
        </button>
        <button class="btn-outline btn-sm btn-danger-text" @click="confirmReset" :disabled="saving">
          重置默认
        </button>
      </div>
    </div>

    <ConfirmDialog
      :visible="confirm.visible"
      :title="confirm.title"
      :message="confirm.message"
      :danger="confirm.danger"
      :loading="saving"
      @confirm="confirm.onConfirm"
      @cancel="confirm.visible = false"
    />

    <AnalysisDependencyStatus />
    <p v-if="error" role="alert" style="color: var(--color-danger)">{{ error }}</p>

    <!-- 筛选栏 -->
    <div class="filter-bar">
      <select v-model="filterCategory" class="input-field">
        <option value="">全部分类</option>
        <option v-for="cat in categories" :key="cat" :value="cat">{{ getCategoryLabel(cat) }}</option>
      </select>
      <input v-model="searchQuery" class="input-field" placeholder="搜索配置项..." />
    </div>

    <!-- Loading -->
    <div v-if="loading" class="loading-state">
      <div class="spinner-lg"></div>
      <span>加载中...</span>
    </div>

    <!-- 配置列表 -->
    <div v-else class="config-list">
      <div v-for="config in filteredConfigs" :key="config.key" class="config-item editorial-card reveal-stagger">
        <div class="config-info">
          <span class="config-key font-jet">{{ config.key }}</span>
          <span class="config-desc terminal-label">{{ config.description }}</span>
        </div>
        <div class="config-value">
          <template v-if="editingKey === config.key">
            <select v-if="config.choices" v-model="editingValue" class="input-field input-sm" :disabled="saving">
              <option v-for="choice in config.choices" :key="choice" :value="choice">{{ choice }}</option>
            </select>
            <textarea v-else-if="config.value_type === 'json'" v-model="editingValue" class="input-field input-sm font-jet" :disabled="saving" />
            <input v-else
              v-model="editingValue"
              :type="config.value_type === 'number' ? 'number' : config.value_type === 'time' ? 'time' : 'text'"
              :min="config.min" :max="config.max" :step="config.step" :disabled="saving"
              class="input-field input-sm font-jet"
              @keyup.enter="saveEdit(config.key)"
              @keyup.escape="cancelEdit"
            />
            <button class="btn-primary btn-sm" @click="saveEdit(config.key)">保存</button>
            <button class="btn-ghost btn-sm" @click="cancelEdit">取消</button>
          </template>
          <template v-else>
            <span class="config-val font-jet">{{ config.value }}</span>
            <button class="btn-ghost btn-sm" @click="startEdit(config.key, config.value)">编辑</button>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.config-page {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.page-header {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.page-title {
  font-size: inherit;
  font-weight: inherit;
}

.page-desc {
  font-size: inherit;
  font-weight: inherit;
  color: var(--color-text-muted);
}

.filter-bar {
  display: flex;
  gap: 0.75rem;
}

.input-field {
  padding: 0.5rem 0.75rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-card);
  color: var(--color-text-primary);
  font-size: 0.85rem;
}

.input-sm {
  padding: 0.45rem 0.75rem;
  font-size: 0.82rem;
}

.loading-state {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.75rem;
  padding: 3rem;
  color: var(--color-text-muted);
}

.spinner-lg {
  width: 32px;
  height: 32px;
  border: 3px solid var(--color-border);
  border-top-color: var(--color-primary-500);
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
}

@keyframes spin { to { transform: rotate(360deg); } }

.config-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.config-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.75rem 1rem;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  gap: 1rem;
}

.config-info {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  flex: 1;
  min-width: 0;
}

.config-key {
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--color-primary);
  font-family: inherit;
}

.config-desc {
  font-size: inherit;
  font-weight: inherit;
  color: var(--color-text-muted);
}

.config-value {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-shrink: 0;
}

.config-val {
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--color-text-primary);
  padding: 0.35rem 0.6rem;
  background: var(--color-bg-hover);
  border-radius: var(--radius-sm);
  min-width: 60px;
  text-align: center;
}

.btn-primary {
  background: var(--color-primary-500);
  color: white;
  border: none;
  padding: 0.45rem 0.85rem;
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: 0.82rem;
}

.btn-primary:hover {
  background: var(--color-primary-600);
}

.btn-ghost {
  background: none;
  border: none;
  color: var(--color-text-muted);
  cursor: pointer;
  padding: 0.45rem 0.6rem;
  font-size: 0.82rem;
}

.btn-ghost:hover {
  color: var(--color-text-primary);
}

.btn-outline {
  background: none;
  border: 1px solid var(--color-border);
  color: var(--color-text-primary);
  padding: 0.5rem 0.85rem;
  border-radius: var(--radius-md);
  cursor: pointer;
  font-size: 0.82rem;
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
}

.btn-outline:hover {
  background: var(--color-bg-hover);
}

.btn-sm {
  padding: 0.45rem 0.75rem;
  font-size: 0.78rem;
}

.btn-danger-text {
  color: #ef4444;
  border-color: #ef4444;
}

.btn-danger-text:hover {
  background: rgba(239, 68, 68, 0.1);
}

/* 移动端适配 */
@media (max-width: 768px) {
  .config-item {
    flex-direction: column;
    align-items: flex-start;
    gap: 0.5rem;
    padding: 0.75rem;
  }

  .config-info {
    width: 100%;
  }

  .config-value {
    width: 100%;
    justify-content: space-between;
  }

  .btn-primary,
  .btn-ghost,
  .btn-outline {
    min-height: 44px;
    padding: 0.5rem 1rem;
  }
}
</style>
