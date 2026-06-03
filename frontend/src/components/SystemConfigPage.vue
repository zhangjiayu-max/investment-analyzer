<script setup>
import { ref, computed, onMounted } from 'vue'
import { getSystemConfigs, updateSystemConfig, resetSystemConfigs } from '../api'
import ConfirmDialog from './ConfirmDialog.vue'

const loading = ref(true)
const configs = ref([])
const editingKey = ref(null)
const editingValue = ref('')
const confirm = ref({ visible: false, title: '', message: '', danger: false, onConfirm: null })
const filterCategory = ref('')
const searchQuery = ref('')

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
      c.description.toLowerCase().includes(q) ||
      c.value.toLowerCase().includes(q)
    )
  }
  return result
})

const categoryLabels = {
  valuation: '📊 估值阈值',
  bond: '💰 债券配置',
  portfolio: '📈 持仓管理',
  llm: '🤖 LLM 参数',
  general: '⚙️ 通用',
}

async function loadConfigs() {
  loading.value = true
  try {
    const { data } = await getSystemConfigs()
    configs.value = data.configs || []
  } catch (e) {
    console.error('Failed to load configs:', e)
  } finally {
    loading.value = false
  }
}

function startEdit(key, value) {
  editingKey.value = key
  editingValue.value = value
}

function cancelEdit() {
  editingKey.value = null
  editingValue.value = ''
}

async function saveEdit(key) {
  try {
    await updateSystemConfig(key, editingValue.value)
    const item = configs.value.find(c => c.key === key)
    if (item) item.value = editingValue.value
    editingKey.value = null
    editingValue.value = ''
  } catch (e) {
    console.error('Failed to update config:', e)
  }
}

function confirmReset() {
  confirm.value = {
    visible: true,
    title: '重置配置',
    message: '确定要将所有配置重置为默认值吗？此操作不可撤销。',
    danger: true,
    onConfirm: async () => {
      confirm.value.visible = false
      try {
        await resetSystemConfigs()
        await loadConfigs()
      } catch (e) {
        console.error('Failed to reset configs:', e)
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
  <div class="config-page">
    <div class="page-header">
      <h2 class="page-title">系统配置</h2>
      <span class="page-desc">管理估值阈值、LLM 参数等业务配置</span>
      <div style="display: flex; gap: 0.5rem; margin-left: auto;">
        <button class="btn-outline btn-sm" @click="loadConfigs" :disabled="loading">
          <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
          刷新
        </button>
        <button class="btn-outline btn-sm btn-danger-text" @click="confirmReset">
          重置默认
        </button>
      </div>
    </div>

    <ConfirmDialog
      :visible="confirm.visible"
      :title="confirm.title"
      :message="confirm.message"
      :danger="confirm.danger"
      @confirm="confirm.onConfirm"
      @cancel="confirm.visible = false"
    />

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
      <div v-for="config in filteredConfigs" :key="config.key" class="config-item">
        <div class="config-info">
          <span class="config-key">{{ config.key }}</span>
          <span class="config-desc">{{ config.description }}</span>
        </div>
        <div class="config-value">
          <template v-if="editingKey === config.key">
            <input
              v-model="editingValue"
              class="input-field input-sm"
              @keyup.enter="saveEdit(config.key)"
              @keyup.escape="cancelEdit"
            />
            <button class="btn-primary btn-sm" @click="saveEdit(config.key)">保存</button>
            <button class="btn-ghost btn-sm" @click="cancelEdit">取消</button>
          </template>
          <template v-else>
            <span class="config-val">{{ config.value }}</span>
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
  font-size: 1.25rem;
  font-weight: 700;
}

.page-desc {
  font-size: 0.82rem;
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
  padding: 0.35rem 0.6rem;
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
  gap: 0.25rem;
  flex: 1;
  min-width: 0;
}

.config-key {
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--color-primary);
  font-family: monospace;
}

.config-desc {
  font-size: 0.78rem;
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
  padding: 0.25rem 0.5rem;
  background: var(--color-bg-hover);
  border-radius: var(--radius-sm);
  min-width: 60px;
  text-align: center;
}

.btn-primary {
  background: var(--color-primary-500);
  color: white;
  border: none;
  padding: 0.35rem 0.75rem;
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
  padding: 0.35rem 0.5rem;
  font-size: 0.82rem;
}

.btn-ghost:hover {
  color: var(--color-text-primary);
}

.btn-outline {
  background: none;
  border: 1px solid var(--color-border);
  color: var(--color-text-primary);
  padding: 0.4rem 0.75rem;
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
  padding: 0.35rem 0.6rem;
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
