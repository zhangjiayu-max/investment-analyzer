<script setup>
import { ref, onMounted, computed } from 'vue'
import { listAgents, getAgent, updateAgent, deleteAgent, listAnalysisAgents, updateAnalysisAgent, generateAgentPrompt, listAgentVersions, rollbackAgentPrompt, listAnalysisAgentVersions, rollbackAnalysisAgentPrompt, getAgentRegressionResult } from '../api'
import ConfirmDialog from './ConfirmDialog.vue'
import AppToast from './AppToast.vue'
import { useToast } from '../composables/useToast'

const { showToast } = useToast()

const agents = ref([])
const analysisAgents = ref([])
const loading = ref(false)
const activeTab = ref('conversation') // 'conversation' | 'analysis'

// 当前查看/编辑的 Agent
const selectedAgent = ref(null)
const editingAgent = ref(null)
const saving = ref(false)
const showDeleteConfirm = ref(false)
const showGenerateDialog = ref(false)
const showOptimizeConfirm = ref(false)
const generateForm = ref({ name: '', description: '' })
const aiLoading = ref(false)

// 版本历史
const versions = ref([])
const versionsLoading = ref(false)
const showVersions = ref(false)
const showRollbackConfirm = ref(false)
const rollbackTarget = ref(null)

// 回归测试
const regressionResult = ref(null)
const regressionLoading = ref(false)
const showRegression = ref(false)

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
  // 重置回归测试状态
  regressionResult.value = null
  showRegression.value = false
  // 异步加载回归测试结果
  loadRegression()
}

async function loadRegression() {
  if (!selectedAgent.value) return
  regressionLoading.value = true
  try {
    const agentType = activeTab.value === 'conversation'
      ? (selectedAgent.value.is_specialist ? 'specialist' : 'conversation')
      : 'analysis'
    const { data } = await getAgentRegressionResult(selectedAgent.value.id, agentType)
    regressionResult.value = data
  } catch (e) {
    regressionResult.value = null
  } finally {
    regressionLoading.value = false
  }
}

function toggleRegression() {
  showRegression.value = !showRegression.value
}

function scoreColor(score) {
  if (!score || score <= 0) return 'var(--color-text-muted)'
  if (score >= 4.5) return '#10b981'
  if (score >= 3.5) return '#22c55e'
  if (score >= 2.5) return '#f59e0b'
  if (score >= 1.5) return '#f97316'
  return '#ef4444'
}

function statusIcon(status) {
  if (status === 'improved') return '📈'
  if (status === 'degraded') return '📉'
  if (status === 'error') return '❌'
  if (status === 'new') return '🆕'
  return '➡️'
}

function startEdit() {
  editingAgent.value = { ...selectedAgent.value }
}

function cancelEdit() {
  editingAgent.value = null
}

// AI 优化当前提示词
async function aiOptimize() {
  if (!editingAgent.value?.system_prompt) return
  aiLoading.value = true
  try {
    const { data } = await generateAgentPrompt({
      name: editingAgent.value.name,
      description: editingAgent.value.description,
      current_prompt: editingAgent.value.system_prompt,
      mode: 'optimize',
    })
    if (data.prompt) {
      editingAgent.value.system_prompt = data.prompt
      showToast('AI 优化完成，请检查后保存')
    }
  } catch (e) {
    showToast('AI 优化失败: ' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    aiLoading.value = false
  }
}

// 打开 AI 生成对话框
function openGenerateDialog() {
  generateForm.value = {
    name: editingAgent.value?.name || '',
    description: editingAgent.value?.description || '',
  }
  showGenerateDialog.value = true
}

// AI 从零生成提示词
async function aiGenerate() {
  if (!generateForm.value.name) return
  aiLoading.value = true
  showGenerateDialog.value = false
  try {
    const { data } = await generateAgentPrompt({
      name: generateForm.value.name,
      description: generateForm.value.description,
      mode: 'generate',
    })
    if (data.prompt) {
      if (!editingAgent.value) {
        editingAgent.value = { name: generateForm.value.name, description: generateForm.value.description, system_prompt: '' }
      }
      editingAgent.value.system_prompt = data.prompt
      if (generateForm.value.name) editingAgent.value.name = generateForm.value.name
      if (generateForm.value.description) editingAgent.value.description = generateForm.value.description
      showToast('AI 生成完成，请检查后保存')
    }
  } catch (e) {
    showToast('AI 生成失败: ' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    aiLoading.value = false
  }
}

// 版本历史
async function loadVersions() {
  if (!selectedAgent.value) return
  versionsLoading.value = true
  try {
    const agentId = selectedAgent.value.id
    const { data } = activeTab.value === 'conversation'
      ? await listAgentVersions(agentId)
      : await listAnalysisAgentVersions(agentId)
    versions.value = data.versions || []
  } catch (e) {
    console.error('Failed to load versions:', e)
  } finally {
    versionsLoading.value = false
  }
}

function toggleVersions() {
  showVersions.value = !showVersions.value
  if (showVersions.value && versions.value.length === 0) loadVersions()
}

function formatVersionTime(ts) {
  if (!ts) return ''
  const d = new Date(ts.replace(' ', 'T'))
  const M = d.getMonth() + 1
  const D = d.getDate()
  const h = String(d.getHours()).padStart(2, '0')
  const m = String(d.getMinutes()).padStart(2, '0')
  return `${M}/${D} ${h}:${m}`
}

function confirmRollback(version) {
  rollbackTarget.value = version
  showRollbackConfirm.value = true
}

async function doRollback() {
  if (!rollbackTarget.value || !selectedAgent.value) return
  try {
    const agentId = selectedAgent.value.id
    const versionId = rollbackTarget.value.id
    const { data } = activeTab.value === 'conversation'
      ? await rollbackAgentPrompt(agentId, versionId)
      : await rollbackAnalysisAgentPrompt(agentId, versionId)
    showToast('已回滚到 v' + rollbackTarget.value.version)
    selectedAgent.value.system_prompt = data.system_prompt
    showRollbackConfirm.value = false
    rollbackTarget.value = null
    // 刷新版本列表
    await loadVersions()
    await loadAgents()
  } catch (e) {
    showToast('回滚失败: ' + (e.response?.data?.detail || e.message), 'error')
    showRollbackConfirm.value = false
  }
}

async function saveEdit() {
  if (!editingAgent.value) return
  saving.value = true
  try {
    const { id, name, description, system_prompt, knowledge_scope, icon, is_active } = editingAgent.value
    const promptChanged = system_prompt !== selectedAgent.value?.system_prompt
    if (activeTab.value === 'conversation') {
      await updateAgent(id, { name, description, system_prompt, knowledge_scope, icon })
    } else {
      await updateAnalysisAgent(id, { name, description, system_prompt, is_active })
    }
    showToast('保存成功')
    if (promptChanged) {
      showToast('Prompt 已变更，回归测试将在后台运行...', 'info')
    }
    selectedAgent.value = { ...editingAgent.value }
    editingAgent.value = null
    await loadAgents()
    // 延迟加载回归测试结果
    if (promptChanged) {
      setTimeout(loadRegression, 10000)
    }
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

function copyPrompt(text) {
  navigator.clipboard.writeText(text).then(() => {
    showToast('已复制到剪贴板')
  }).catch(() => {
    showToast('复制失败', 'error')
  })
}

function getAgentIcon(icon) {
  const icons = {
    chart: '📊', research: '🔍', shield: '🛡️', pie: '🥧', robot: '🤖',
    newspaper: '📰', search: '🔍', bull: '🐂',
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
              <span class="agent-id-tag">#{{ agent.id }}</span>
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
              <button class="btn-primary btn-sm" @click="startEdit">编辑</button>
              <button
                v-if="!selectedAgent.is_preset && activeTab === 'conversation'"
                class="btn-danger-outline btn-sm"
                @click="showDeleteConfirm = true"
              >删除</button>
            </div>
            <div class="detail-actions" v-else>
              <button class="btn-primary btn-sm" @click="saveEdit" :disabled="saving">
                {{ saving ? '保存中...' : '保存' }}
              </button>
              <button class="btn-outline btn-sm" @click="cancelEdit">取消</button>
            </div>
          </div>

          <!-- View Mode -->
          <template v-if="!editingAgent">
            <div class="detail-section">
              <div class="detail-label">描述</div>
              <div class="detail-value">{{ selectedAgent.description || '—' }}</div>
            </div>
            <div class="detail-section">
              <div class="detail-label">
                <span>系统提示词</span>
                <button class="copy-btn" @click="copyPrompt(selectedAgent.system_prompt)" title="复制">
                  <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"/></svg>
                </button>
              </div>
              <pre class="prompt-block">{{ selectedAgent.system_prompt }}</pre>
            </div>
            <div v-if="selectedAgent.knowledge_scope" class="detail-section">
              <div class="detail-label">知识范围</div>
              <pre class="prompt-block prompt-sm">{{ selectedAgent.knowledge_scope }}</pre>
            </div>

            <!-- Version History -->
            <div class="detail-section">
              <button class="version-toggle" @click="toggleVersions">
                <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
                {{ showVersions ? '收起版本历史' : '查看版本历史' }}
                <span v-if="versions.length" class="version-count">{{ versions.length }}</span>
              </button>
              <Transition name="fade">
                <div v-if="showVersions" class="version-list">
                  <div v-if="versionsLoading" class="version-loading">
                    <span class="spinner-sm"></span> 加载中...
                  </div>
                  <div v-else-if="versions.length === 0" class="version-empty">暂无历史版本</div>
                  <div v-else v-for="v in versions" :key="v.id" class="version-item">
                    <div class="version-info">
                      <span class="version-tag">v{{ v.version }}</span>
                      <span class="version-time">{{ formatVersionTime(v.created_at) }}</span>
                      <span class="version-preview">{{ (v.system_prompt || '').substring(0, 60) }}...</span>
                    </div>
                    <button class="btn btn-outline btn-xs version-rollback-btn" @click="confirmRollback(v)">回滚</button>
                  </div>
                </div>
              </Transition>
            </div>

            <!-- Regression Test Results -->
            <div class="detail-section">
              <button class="version-toggle" @click="toggleRegression">
                <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/></svg>
                {{ showRegression ? '收起回归测试' : '查看回归测试' }}
              </button>
              <Transition name="fade">
                <div v-if="showRegression" class="regression-panel">
                  <div v-if="regressionLoading" class="version-loading">
                    <span class="spinner-sm"></span> 加载中...
                  </div>
                  <div v-else-if="!regressionResult || regressionResult.status === 'none'" class="version-empty">
                    暂无回归测试记录
                  </div>
                  <div v-else-if="regressionResult.status === 'running'" class="regression-status running">
                    <span class="spinner-sm"></span> 回归测试运行中...
                  </div>
                  <div v-else-if="regressionResult.status === 'error'" class="regression-status error">
                    ❌ 测试失败: {{ regressionResult.error }}
                  </div>
                  <div v-else-if="regressionResult.status === 'completed'">
                    <div v-if="regressionResult.message" class="version-empty">{{ regressionResult.message }}</div>
                    <template v-else>
                      <div class="regression-summary">
                        <div class="regression-stat">
                          <span class="stat-num">{{ regressionResult.summary?.total || 0 }}</span>
                          <span class="stat-lbl">总用例</span>
                        </div>
                        <div class="regression-stat improved">
                          <span class="stat-num">{{ regressionResult.summary?.improved || 0 }}</span>
                          <span class="stat-lbl">📈 提升</span>
                        </div>
                        <div class="regression-stat degraded">
                          <span class="stat-num">{{ regressionResult.summary?.degraded || 0 }}</span>
                          <span class="stat-lbl">📉 退步</span>
                        </div>
                        <div class="regression-stat">
                          <span class="stat-num">{{ regressionResult.summary?.unchanged || 0 }}</span>
                          <span class="stat-lbl">➡️ 持平</span>
                        </div>
                      </div>
                      <div class="regression-cases">
                        <div v-for="c in regressionResult.cases" :key="c.case_id" class="regression-case">
                          <span class="regression-status-icon">{{ statusIcon(c.status) }}</span>
                          <span class="regression-case-name">{{ c.case_name }}</span>
                          <span class="regression-score" :style="{ color: scoreColor(c.score) }">
                            {{ c.score?.toFixed(1) || '-' }}分
                          </span>
                          <span v-if="c.old_avg" class="regression-old-avg">
                            (旧: {{ c.old_avg?.toFixed(1) }})
                          </span>
                        </div>
                      </div>
                    </template>
                  </div>
                </div>
              </Transition>
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
              <div class="prompt-toolbar">
                <button class="btn-outline btn-xs" @click="showOptimizeConfirm = true" :disabled="aiLoading || !editingAgent.system_prompt">
                  <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>
                  {{ aiLoading ? '生成中...' : 'AI 优化' }}
                </button>
                <button class="btn-outline btn-xs" @click="openGenerateDialog" :disabled="aiLoading">
                  <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6v6m0 0v6m0-6h6m-6 0H6"/></svg>
                  AI 生成
                </button>
              </div>
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
            <button class="btn-secondary" @click="showDeleteConfirm = false">取消</button>
            <button class="btn-danger" @click="handleDelete">确认删除</button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- Rollback Confirm -->
    <Teleport to="body">
      <div v-if="showRollbackConfirm" class="modal-overlay" @click.self="showRollbackConfirm = false">
        <div class="modal-dialog">
          <h3 class="modal-title">确认回滚</h3>
          <p class="modal-desc">将回滚到 v{{ rollbackTarget?.version }}（{{ formatVersionTime(rollbackTarget?.created_at) }}）。当前提示词会自动保存为新版本，确认继续？</p>
          <div class="modal-actions">
            <button class="btn-secondary" @click="showRollbackConfirm = false">取消</button>
            <button class="btn-primary" @click="doRollback">确认回滚</button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- AI Optimize Confirm -->
    <Teleport to="body">
      <div v-if="showOptimizeConfirm" class="modal-overlay" @click.self="showOptimizeConfirm = false">
        <div class="modal-dialog">
          <h3 class="modal-title">AI 优化提示词</h3>
          <p class="modal-desc">AI 将基于当前提示词进行优化重写，补充缺失的结构（如 Few-shot 示例、负面约束等）。优化后内容将替换当前提示词，请确认继续？</p>
          <div class="modal-actions">
            <button class="btn-secondary" @click="showOptimizeConfirm = false">取消</button>
            <button class="btn-primary" @click="showOptimizeConfirm = false; aiOptimize()">确认优化</button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- AI Generate Dialog -->
    <Teleport to="body">
      <div v-if="showGenerateDialog" class="modal-overlay" @click.self="showGenerateDialog = false">
        <div class="modal-dialog">
          <h3 class="modal-title">AI 生成提示词</h3>
          <p class="modal-desc">描述 Agent 的角色和职责，AI 将为你生成专业的系统提示词。</p>
          <div class="edit-field">
            <label class="edit-label">Agent 名称</label>
            <input v-model="generateForm.name" class="input-field" placeholder="如：基金定投顾问" />
          </div>
          <div class="edit-field">
            <label class="edit-label">角色描述</label>
            <textarea v-model="generateForm.description" class="input-field" rows="3" placeholder="如：帮助用户制定基金定投计划，分析定投收益，给出定投策略建议"></textarea>
          </div>
          <div class="modal-actions">
            <button class="btn-secondary" @click="showGenerateDialog = false">取消</button>
            <button class="btn-primary" @click="aiGenerate" :disabled="!generateForm.name || aiLoading">
              {{ aiLoading ? '生成中...' : '开始生成' }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- Toast -->
    <AppToast />
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
  padding: 0.2rem 0.5rem;
  border-radius: 999px;
}

.badge-inactive {
  font-size: 0.6rem;
  font-weight: 600;
  background: rgba(239,68,68,0.1);
  color: #dc2626;
  padding: 0.2rem 0.5rem;
  border-radius: 999px;
}

.agent-id-tag {
  font-size: 0.65rem;
  font-weight: 500;
  color: var(--color-text-tertiary);
  margin-left: 0.25rem;
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

.btn-danger-outline {
  color: #dc2626;
  border-color: #fca5a5;
}

.btn-danger-outline:hover {
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
  margin-bottom: 0.6rem;
  display: flex;
  align-items: center;
  gap: 0.4rem;
}

.copy-btn {
  background: none;
  border: none;
  color: var(--color-text-muted);
  cursor: pointer;
  padding: 2px;
  border-radius: 4px;
  display: flex;
  align-items: center;
  transition: all 0.15s;
}

.copy-btn:hover {
  color: var(--color-primary-600);
  background: var(--color-bg-hover);
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
  margin-bottom: 0.5rem;
}

.prompt-textarea {
  font-family: 'SF Mono', 'Menlo', 'Monaco', monospace;
  font-size: 0.8rem;
  line-height: 1.6;
  resize: vertical;
  min-height: 200px;
}

.prompt-toolbar {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}

/* Version History */
.version-toggle {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  background: none;
  border: none;
  color: var(--color-primary-600);
  font-size: 0.8rem;
  font-weight: 500;
  cursor: pointer;
  padding: 0;
  transition: color 0.15s;
}

.version-toggle:hover { color: var(--color-primary-700); }

.version-count {
  font-size: 0.65rem;
  background: var(--color-primary-50);
  color: var(--color-primary-600);
  padding: 0.2rem 0.5rem;
  border-radius: 999px;
}

.version-list {
  margin-top: 0.75rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  overflow: hidden;
  max-height: 300px;
  overflow-y: auto;
}

.version-loading,
.version-empty {
  padding: 1.25rem;
  text-align: center;
  font-size: 0.8rem;
  color: var(--color-text-muted);
}

.version-loading { display: flex; align-items: center; justify-content: center; gap: 0.5rem; }

.version-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  padding: 0.6rem 0.85rem;
  border-bottom: 1px solid var(--color-border-light);
  transition: background 0.15s;
}

.version-item:last-child { border-bottom: none; }
.version-item:hover { background: var(--color-bg-hover); }

.version-info {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  min-width: 0;
  flex: 1;
}

.version-tag {
  font-size: 0.72rem;
  font-weight: 600;
  background: var(--color-bg-input);
  color: var(--color-text-secondary);
  padding: 0.2rem 0.5rem;
  border-radius: var(--radius-sm);
  white-space: nowrap;
}

.version-time {
  font-size: 0.72rem;
  color: var(--color-text-muted);
  white-space: nowrap;
}

.version-preview {
  font-size: 0.72rem;
  color: var(--color-text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.version-rollback-btn {
  flex-shrink: 0;
  font-size: 0.68rem;
  padding: 0.2rem 0.5rem;
  color: var(--color-primary-600);
  border-color: var(--color-primary-300);
}

.version-rollback-btn:hover {
  background: var(--color-primary-50);
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

/* Responsive */
@media (max-width: 768px) {
  .agent-layout {
    grid-template-columns: 1fr;
  }
  .agent-list {
    max-height: 250px;
  }
}

/* Regression Test */
.regression-panel {
  margin-top: 0.75rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  overflow: hidden;
}

.regression-status {
  padding: 1.25rem;
  text-align: center;
  font-size: 0.85rem;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
}

.regression-status.running { color: var(--color-primary-600); }
.regression-status.error { color: #dc2626; }

.regression-summary {
  display: flex;
  gap: 0.75rem;
  padding: 0.75rem;
  border-bottom: 1px solid var(--color-border-light);
  flex-wrap: wrap;
}

.regression-stat {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.25rem;
  min-width: 60px;
}

.regression-stat .stat-num {
  font-size: 1.1rem;
  font-weight: 700;
  color: var(--color-text-primary);
}

.regression-stat .stat-lbl {
  font-size: 0.68rem;
  color: var(--color-text-muted);
}

.regression-stat.improved .stat-num { color: #10b981; }
.regression-stat.degraded .stat-num { color: #ef4444; }

.regression-cases {
  max-height: 250px;
  overflow-y: auto;
}

.regression-case {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  border-bottom: 1px solid var(--color-border-light);
  font-size: 0.8rem;
}

.regression-case:last-child { border-bottom: none; }

.regression-status-icon { font-size: 0.9rem; flex-shrink: 0; }

.regression-case-name {
  flex: 1;
  color: var(--color-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.regression-score {
  font-weight: 700;
  font-size: 0.85rem;
}

.regression-old-avg {
  font-size: 0.72rem;
  color: var(--color-text-muted);
}
</style>
