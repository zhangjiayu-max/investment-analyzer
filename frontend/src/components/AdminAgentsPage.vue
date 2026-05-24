<script setup>
import { ref, onMounted, computed } from 'vue'
import { listAgents, getAgent, updateAgent, deleteAgent, listAnalysisAgents, updateAnalysisAgent } from '../api'

const agents = ref([])
const analysisAgents = ref([])
const loading = ref(false)
const activeTab = ref('conversation') // 'conversation' | 'analysis'

// 当前查看/编辑的 Agent
const selectedAgent = ref(null)
const editingAgent = ref(null)
const saving = ref(false)
const showDeleteConfirm = ref(false)
const toast = ref({ show: false, message: '', type: 'success' })

function showToast(message, type = 'success') {
  toast.value = { show: true, message, type }
  setTimeout(() => { toast.value.show = false }, 2500)
}

async function loadAgents() {
  loading.value = true
  try {
    const [r1, r2] = await Promise.all([listAgents(), listAnalysisAgents()])
    agents.value = r1.data.agents || []
    analysisAgents.value = r2.data.agents || r2.data || []
  } catch (e) {
    console.error('Failed to load agents:', e)
  } finally {
    loading.value = false
  }
}

const currentList = computed(() =>
  activeTab.value === 'conversation' ? agents.value : analysisAgents.value
)

function selectAgent(agent) {
  selectedAgent.value = { ...agent }
  editingAgent.value = null
}

function startEdit() {
  editingAgent.value = { ...selectedAgent.value }
}

function cancelEdit() {
  editingAgent.value = null
}

async function saveEdit() {
  if (!editingAgent.value) return
  saving.value = true
  try {
    const { id, name, description, system_prompt, knowledge_scope, icon, is_active } = editingAgent.value
    if (activeTab.value === 'conversation') {
      await updateAgent(id, { name, description, system_prompt, knowledge_scope, icon })
    } else {
      await updateAnalysisAgent(id, { name, description, system_prompt, is_active })
    }
    showToast('保存成功')
    selectedAgent.value = { ...editingAgent.value }
    editingAgent.value = null
    await loadAgents()
  } catch (e) {
    showToast('保存失败: ' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    saving.value = false
  }
}

async function handleDelete() {
  if (!selectedAgent.value) return
  try {
    await deleteAgent(selectedAgent.value.id)
    showToast('删除成功')
    selectedAgent.value = null
    showDeleteConfirm.value = false
    await loadAgents()
  } catch (e) {
    showToast('删除失败: ' + (e.response?.data?.detail || e.message), 'error')
    showDeleteConfirm.value = false
  }
}

function getAgentIcon(icon) {
  const icons = {
    chart: '📊', research: '🔍', shield: '🛡️', pie: '🥧', robot: '🤖',
  }
  return icons[icon] || '🤖'
}

onMounted(loadAgents)
</script>

<template>
  <div class="admin-agents-page">
    <div class="page-header">
      <h2 class="page-title">Agent 管理</h2>
      <span class="page-desc">查看和编辑所有 Agent 的配置与系统提示词</span>
    </div>

    <!-- Tab Bar -->
    <div class="tab-bar">
      <button :class="['tab-btn', { active: activeTab === 'conversation' }]" @click="activeTab = 'conversation'; selectedAgent = null; editingAgent = null">
        对话 Agent <span class="tab-count">{{ agents.length }}</span>
      </button>
      <button :class="['tab-btn', { active: activeTab === 'analysis' }]" @click="activeTab = 'analysis'; selectedAgent = null; editingAgent = null">
        分析 Agent <span class="tab-count">{{ analysisAgents.length }}</span>
      </button>
    </div>

    <!-- Loading -->
    <div v-if="loading" class="loading-state">
      <div class="spinner-lg"></div>
      <span>加载中...</span>
    </div>

    <div v-else class="agent-layout">
      <!-- Agent List -->
      <div class="agent-list card">
        <div
          v-for="agent in currentList"
          :key="agent.id"
          :class="['agent-item', { active: selectedAgent?.id === agent.id }]"
          @click="selectAgent(agent)"
        >
          <span class="agent-icon">{{ getAgentIcon(agent.icon) }}</span>
          <div class="agent-item-info">
            <div class="agent-item-name">
              {{ agent.name }}
              <span v-if="agent.is_preset" class="badge-preset">预设</span>
              <span v-if="agent.is_active === 0" class="badge-inactive">已停用</span>
            </div>
            <div class="agent-item-desc">{{ agent.description || '暂无描述' }}</div>
          </div>
        </div>
        <div v-if="currentList.length === 0" class="empty-hint">暂无 Agent</div>
      </div>

      <!-- Agent Detail -->
      <div class="agent-detail card">
        <template v-if="selectedAgent">
          <div class="detail-header">
            <div class="detail-title-row">
              <span class="detail-icon">{{ getAgentIcon(editingAgent?.icon || selectedAgent.icon) }}</span>
              <h3 class="detail-name">{{ editingAgent?.name || selectedAgent.name }}</h3>
              <span v-if="selectedAgent.is_preset" class="badge-preset">预设</span>
            </div>
            <div class="detail-actions" v-if="!editingAgent">
              <button class="btn btn-primary btn-sm" @click="startEdit">编辑</button>
              <button
                v-if="!selectedAgent.is_preset && activeTab === 'conversation'"
                class="btn btn-outline btn-sm btn-danger"
                @click="showDeleteConfirm = true"
              >删除</button>
            </div>
            <div class="detail-actions" v-else>
              <button class="btn btn-primary btn-sm" @click="saveEdit" :disabled="saving">
                {{ saving ? '保存中...' : '保存' }}
              </button>
              <button class="btn btn-outline btn-sm" @click="cancelEdit">取消</button>
            </div>
          </div>

          <!-- View Mode -->
          <template v-if="!editingAgent">
            <div class="detail-section">
              <div class="detail-label">描述</div>
              <div class="detail-value">{{ selectedAgent.description || '—' }}</div>
            </div>
            <div class="detail-section">
              <div class="detail-label">系统提示词</div>
              <pre class="prompt-block">{{ selectedAgent.system_prompt }}</pre>
            </div>
            <div v-if="selectedAgent.knowledge_scope" class="detail-section">
              <div class="detail-label">知识范围</div>
              <pre class="prompt-block prompt-sm">{{ selectedAgent.knowledge_scope }}</pre>
            </div>
          </template>

          <!-- Edit Mode -->
          <template v-else>
            <div class="edit-field">
              <label class="edit-label">名称</label>
              <input v-model="editingAgent.name" class="input-field" />
            </div>
            <div class="edit-field">
              <label class="edit-label">描述</label>
              <input v-model="editingAgent.description" class="input-field" />
            </div>
            <div class="edit-field">
              <label class="edit-label">系统提示词</label>
              <textarea v-model="editingAgent.system_prompt" class="input-field prompt-textarea" rows="20"></textarea>
            </div>
            <div v-if="activeTab === 'conversation'" class="edit-field">
              <label class="edit-label">知识范围 (JSON)</label>
              <textarea v-model="editingAgent.knowledge_scope" class="input-field prompt-textarea" rows="4"></textarea>
            </div>
            <div v-if="activeTab === 'analysis'" class="edit-field">
              <label class="edit-label">状态</label>
              <label class="toggle-label">
                <input type="checkbox" v-model="editingAgent.is_active" :true-value="1" :false-value="0" />
                <span>{{ editingAgent.is_active ? '启用' : '停用' }}</span>
              </label>
            </div>
          </template>
        </template>

        <div v-else class="empty-detail">
          <svg width="48" height="48" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/>
          </svg>
          <p>选择左侧 Agent 查看详情</p>
        </div>
      </div>
    </div>

    <!-- Delete Confirm -->
    <Teleport to="body">
      <div v-if="showDeleteConfirm" class="modal-overlay" @click.self="showDeleteConfirm = false">
        <div class="modal-dialog">
          <h3 class="modal-title">确认删除</h3>
          <p class="modal-desc">确定删除 Agent「{{ selectedAgent?.name }}」？此操作不可撤销。</p>
          <div class="modal-actions">
            <button class="btn btn-outline" @click="showDeleteConfirm = false">取消</button>
            <button class="btn btn-danger" @click="handleDelete">确认删除</button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- Toast -->
    <Transition name="fade">
      <div v-if="toast.show" :class="['toast', 'toast-' + toast.type]">{{ toast.message }}</div>
    </Transition>
  </div>
</template>

<style scoped>
.admin-agents-page {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.page-header {
  display: flex;
  align-items: baseline;
  gap: 0.75rem;
}

.page-title {
  font-size: 1.2rem;
  font-weight: 700;
  color: var(--color-text-primary);
  margin: 0;
}

.page-desc {
  font-size: 0.8rem;
  color: var(--color-text-muted);
}

/* Tab Bar */
.tab-bar {
  display: flex;
  gap: 0;
  border-bottom: 1px solid var(--color-border);
}

.tab-btn {
  padding: 0.5rem 1.25rem;
  font-size: 0.85rem;
  font-weight: 500;
  color: var(--color-text-muted);
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  cursor: pointer;
  transition: all 0.2s;
  display: flex;
  align-items: center;
  gap: 0.4rem;
}

.tab-btn:hover { color: var(--color-text-secondary); }
.tab-btn.active {
  color: var(--color-primary-600);
  border-bottom-color: var(--color-primary-500);
}

.tab-count {
  font-size: 0.7rem;
  background: var(--color-bg-input);
  color: var(--color-text-muted);
  padding: 0.1rem 0.4rem;
  border-radius: 999px;
}

/* Layout */
.agent-layout {
  display: grid;
  grid-template-columns: 280px 1fr;
  gap: 1rem;
  min-height: 500px;
}

/* Agent List */
.agent-list {
  padding: 0.5rem;
  overflow-y: auto;
  max-height: 70vh;
}

.agent-item {
  display: flex;
  align-items: center;
  gap: 0.65rem;
  padding: 0.65rem 0.75rem;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: background 0.15s;
}

.agent-item:hover { background: var(--color-bg-hover); }
.agent-item.active {
  background: var(--color-primary-50);
  border-left: 3px solid var(--color-primary-500);
}

.agent-icon { font-size: 1.3rem; flex-shrink: 0; }

.agent-item-info { min-width: 0; }

.agent-item-name {
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--color-text-primary);
  display: flex;
  align-items: center;
  gap: 0.35rem;
}

.agent-item-desc {
  font-size: 0.72rem;
  color: var(--color-text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-top: 0.15rem;
}

.badge-preset {
  font-size: 0.6rem;
  font-weight: 600;
  background: var(--color-primary-50);
  color: var(--color-primary-600);
  padding: 0.1rem 0.35rem;
  border-radius: 999px;
}

.badge-inactive {
  font-size: 0.6rem;
  font-weight: 600;
  background: rgba(239,68,68,0.1);
  color: #dc2626;
  padding: 0.1rem 0.35rem;
  border-radius: 999px;
}

/* Agent Detail */
.agent-detail {
  padding: 1.25rem;
  overflow-y: auto;
  max-height: 70vh;
}

.detail-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 1.25rem;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.detail-title-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.detail-icon { font-size: 1.5rem; }

.detail-name {
  font-size: 1.1rem;
  font-weight: 700;
  color: var(--color-text-primary);
  margin: 0;
}

.detail-actions {
  display: flex;
  gap: 0.5rem;
}

.btn-sm {
  font-size: 0.78rem;
  padding: 0.35rem 0.85rem;
}

.btn-danger {
  color: #dc2626;
  border-color: #fca5a5;
}

.btn-danger:hover {
  background: rgba(239,68,68,0.08);
}

.detail-section {
  margin-bottom: 1.25rem;
}

.detail-label {
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--color-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-bottom: 0.4rem;
}

.detail-value {
  font-size: 0.85rem;
  color: var(--color-text-secondary);
  line-height: 1.6;
}

.prompt-block {
  background: var(--color-bg-input);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: 1rem;
  font-size: 0.8rem;
  line-height: 1.7;
  color: var(--color-text-secondary);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 400px;
  overflow-y: auto;
  font-family: 'SF Mono', 'Menlo', 'Monaco', monospace;
}

.prompt-sm { max-height: 200px; }

/* Edit Mode */
.edit-field {
  margin-bottom: 1rem;
}

.edit-label {
  display: block;
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--color-text-muted);
  margin-bottom: 0.35rem;
}

.prompt-textarea {
  font-family: 'SF Mono', 'Menlo', 'Monaco', monospace;
  font-size: 0.8rem;
  line-height: 1.6;
  resize: vertical;
  min-height: 200px;
}

.toggle-label {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.85rem;
  color: var(--color-text-secondary);
  cursor: pointer;
}

.toggle-label input[type="checkbox"] {
  width: 16px;
  height: 16px;
}

/* Empty States */
.empty-detail {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.75rem;
  padding: 3rem;
  color: var(--color-text-muted);
}

.empty-detail p { font-size: 0.85rem; margin: 0; }

.empty-hint {
  text-align: center;
  padding: 2rem;
  color: var(--color-text-muted);
  font-size: 0.85rem;
}

/* Loading */
.loading-state {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.75rem;
  padding: 3rem;
  color: var(--color-text-muted);
  font-size: 0.875rem;
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

/* Modal */
.modal-overlay {
  position: fixed;
  inset: 0;
  z-index: var(--z-modal);
  background: rgba(0,0,0,0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 2rem;
}

.modal-dialog {
  background: var(--color-bg-card);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  padding: 2rem;
  max-width: 420px;
  width: 100%;
}

.modal-title {
  font-size: 1.1rem;
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0 0 0.75rem;
}

.modal-desc {
  font-size: 0.85rem;
  color: var(--color-text-secondary);
  margin: 0 0 1.5rem;
  line-height: 1.5;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.75rem;
}

/* Toast */
.toast {
  position: fixed;
  top: 1.5rem;
  right: 1.5rem;
  z-index: 9999;
  padding: 0.65rem 1.25rem;
  border-radius: var(--radius-md);
  font-size: 0.85rem;
  font-weight: 500;
  box-shadow: var(--shadow-lg);
}

.toast-success { background: #059669; color: white; }
.toast-error { background: #dc2626; color: white; }

/* Responsive */
@media (max-width: 768px) {
  .agent-layout {
    grid-template-columns: 1fr;
  }
  .agent-list {
    max-height: 250px;
  }
}
</style>
