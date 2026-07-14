<script setup>
import { ref, onMounted, computed } from 'vue'
import { useToast } from '../composables/useToast'

const { showToast } = useToast()

const configs = ref([])
const runs = ref([])
const stats = ref(null)
const loading = ref(false)
const showCreateForm = ref(false)
const selectedConfig = ref(null)
const activeTab = ref('configs') // 'configs' | 'runs' | 'stats'

// 新建表单
const form = ref({
  name: '',
  agent_type: 'ai',
  current_prompt: '',
  candidate_prompt: '',
  traffic_pct: 0.1,
})

const agentTypes = [
  { value: 'ai', label: 'AI 分析' },
  { value: 'panorama', label: '全景诊断' },
  { value: 'deep_dive', label: '基金深度' },
  { value: 'trade_review', label: '交易复盘' },
  { value: 'orchestrator', label: '对话编排' },
  { value: 'daily_report', label: '每日报告' },
]

async function loadConfigs() {
  loading.value = true
  try {
    const resp = await fetch('/api/shadow/configs?active_only=false')
    const data = await resp.json()
    configs.value = data.configs || []
  } catch (e) { console.error(e) }
  finally { loading.value = false }
}

async function loadRuns(configId) {
  try {
    const url = configId ? `/api/shadow/runs?config_id=${configId}&limit=50` : '/api/shadow/runs?limit=50'
    const resp = await fetch(url)
    const data = await resp.json()
    runs.value = data.runs || []
  } catch (e) { console.error(e) }
}

async function loadStats(configId) {
  try {
    const url = configId ? `/api/shadow/stats?config_id=${configId}` : '/api/shadow/stats'
    const resp = await fetch(url)
    stats.value = await resp.json()
  } catch (e) { console.error(e) }
}

async function createConfig() {
  if (!form.value.name || !form.value.candidate_prompt) {
    showToast('请填写名称和候选 Prompt', 'error')
    return
  }
  try {
    const resp = await fetch('/api/shadow/configs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(form.value),
    })
    const data = await resp.json()
    if (data.ok) {
      showToast('创建成功', 'success')
      showCreateForm.value = false
      form.value = { name: '', agent_type: 'ai', current_prompt: '', candidate_prompt: '', traffic_pct: 0.1 }
      await loadConfigs()
    }
  } catch (e) { showToast('创建失败', 'error') }
}

async function toggleConfig(config) {
  try {
    await fetch(`/api/shadow/configs/${config.id}/toggle`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ is_active: !config.is_active }),
    })
    config.is_active = config.is_active ? 0 : 1
    showToast(config.is_active ? '已启用' : '已禁用', 'success')
  } catch (e) { showToast('操作失败', 'error') }
}

async function deleteConfig(id) {
  if (!confirm('确定删除？相关执行记录也会被删除')) return
  try {
    await fetch(`/api/shadow/configs/${id}`, { method: 'DELETE' })
    showToast('已删除', 'success')
    await loadConfigs()
  } catch (e) { showToast('删除失败', 'error') }
}

function selectConfig(c) {
  selectedConfig.value = c
  activeTab.value = 'runs'
  loadRuns(c.id)
  loadStats(c.id)
}

function clearSelection() {
  selectedConfig.value = null
  loadRuns()
  loadStats()
}

function scoreColor(score) {
  if (score >= 8) return 'var(--color-profit)'
  if (score >= 6) return 'var(--color-text-secondary)'
  return 'var(--color-loss)'
}

onMounted(() => {
  loadConfigs()
  loadRuns()
  loadStats()
})
</script>

<template>
  <div class="shadow-page bg-mesh">
    <div class="page-header">
      <div>
        <h2 class="page-title editorial-title-lg">Shadow Mode</h2>
        <p class="page-desc editorial-subtitle">候选 Prompt 在生产环境静默运行、自动评分对比</p>
      </div>
      <div class="header-actions">
        <button class="btn-primary btn-sm" @click="showCreateForm = !showCreateForm">
          {{ showCreateForm ? '取消' : '+ 新建配置' }}
        </button>
      </div>
    </div>

    <!-- 创建表单 -->
    <Transition name="fade">
      <div v-if="showCreateForm" class="card create-form editorial-card">
        <div class="form-row">
          <label>名称</label>
          <input v-model="form.name" class="input-field" placeholder="如: 优化持仓分析prompt v2" />
        </div>
        <div class="form-row">
          <label>Agent 类型</label>
          <select v-model="form.agent_type" class="input-field">
            <option v-for="t in agentTypes" :key="t.value" :value="t.value">{{ t.label }}</option>
          </select>
        </div>
        <div class="form-row">
          <label>流量比例</label>
          <input v-model.number="form.traffic_pct" type="range" min="0.01" max="1" step="0.01" />
          <span class="pct-label">{{ (form.traffic_pct * 100).toFixed(0) }}%</span>
        </div>
        <div class="form-row">
          <label>当前 Prompt（参考）</label>
          <textarea v-model="form.current_prompt" class="input-field textarea" rows="3" placeholder="当前使用的 system prompt（可选，用于对比参考）"></textarea>
        </div>
        <div class="form-row">
          <label>候选 Prompt <span class="required">*</span></label>
          <textarea v-model="form.candidate_prompt" class="input-field textarea" rows="5" placeholder="新的 system prompt，将在线上静默测试"></textarea>
        </div>
        <button class="btn-primary" @click="createConfig">创建</button>
      </div>
    </Transition>

    <!-- 统计面板 -->
    <div v-if="stats" class="stats-bar">
      <div class="stat-card editorial-card reveal-stagger">
        <span class="stat-value font-jet-lg">{{ configs.length }}</span>
        <span class="stat-label terminal-label">配置数</span>
      </div>
      <div class="stat-card editorial-card reveal-stagger">
        <span class="stat-value font-jet-lg">{{ stats.total_runs || 0 }}</span>
        <span class="stat-label terminal-label">总运行次数</span>
      </div>
      <div class="stat-card editorial-card reveal-stagger">
        <span class="stat-value font-jet-lg" :style="{ color: scoreColor(stats.avg_prod_score || 0) }">{{ (stats.avg_prod_score || 0).toFixed(1) }}</span>
        <span class="stat-label terminal-label">Production 均分</span>
      </div>
      <div class="stat-card editorial-card reveal-stagger">
        <span class="stat-value font-jet-lg" :style="{ color: scoreColor(stats.avg_shadow_score || 0) }">{{ (stats.avg_shadow_score || 0).toFixed(1) }}</span>
        <span class="stat-label terminal-label">Shadow 均分</span>
      </div>
      <div class="stat-card editorial-card reveal-stagger">
        <span class="stat-value font-jet-lg">{{ ((stats.shadow_win_rate || 0) * 100).toFixed(0) }}%</span>
        <span class="stat-label terminal-label">Shadow 胜率</span>
      </div>
    </div>

    <!-- Tab 切换 -->
    <div class="tab-bar">
      <button :class="['tab', { active: activeTab === 'configs' }]" @click="activeTab = 'configs'">配置列表</button>
      <button :class="['tab', { active: activeTab === 'runs' }]" @click="activeTab = 'runs'">
        执行记录
        <span v-if="selectedConfig" class="tab-badge font-jet">{{ selectedConfig.name }}</span>
      </button>
    </div>

    <!-- 配置列表 -->
    <div v-if="activeTab === 'configs'" class="config-list">
      <div v-for="c in configs" :key="c.id" class="config-card editorial-card reveal-stagger">
        <div class="config-header">
          <span class="config-name">{{ c.name }}</span>
          <span :class="['status-dot', c.is_active ? 'active' : 'inactive']"></span>
          <span class="config-type terminal-label">{{ agentTypes.find(t => t.value === c.agent_type)?.label || c.agent_type }}</span>
        </div>
        <div class="config-stats">
          <span><span class="font-jet">{{ c.run_count || 0 }}</span> 运行</span>
          <span v-if="c.avg_prod_score">Production: <span class="font-jet">{{ Number(c.avg_prod_score).toFixed(1) }}</span></span>
          <span v-if="c.avg_shadow_score">Shadow: <span class="font-jet">{{ Number(c.avg_shadow_score).toFixed(1) }}</span></span>
        </div>
        <div class="config-actions">
          <button class="btn-link" @click="selectConfig(c)">查看详情</button>
          <button class="btn-link" @click="toggleConfig(c)">{{ c.is_active ? '禁用' : '启用' }}</button>
          <button class="btn-link btn-danger" @click="deleteConfig(c.id)">删除</button>
        </div>
      </div>
      <div v-if="!loading && configs.length === 0" class="empty-state">
        <p>暂无 Shadow 配置</p>
        <p class="text-muted">点击「新建配置」开始测试候选 Prompt</p>
      </div>
    </div>

    <!-- 执行记录 -->
    <div v-if="activeTab === 'runs'" class="runs-list">
      <div v-if="selectedConfig" class="selection-banner">
        <span>当前查看: {{ selectedConfig.name }}</span>
        <button class="btn-link" @click="clearSelection()">查看全部</button>
      </div>
      <div v-for="r in runs" :key="r.id" class="run-card editorial-card reveal-stagger">
        <div class="run-header">
          <span class="run-time font-jet">{{ r.created_at?.slice(0, 16) }}</span>
          <span class="run-agent terminal-label">{{ r.agent_type }}</span>
          <span class="run-duration font-jet">{{ r.duration_ms }}ms</span>
        </div>
        <div class="run-scores">
          <div class="score-item">
            <span class="score-label terminal-label">Production</span>
            <span class="score-value font-jet-lg" :style="{ color: scoreColor(r.production_score || 0) }">
              {{ (r.production_score || 0).toFixed(1) }}
            </span>
          </div>
          <div class="score-vs">vs</div>
          <div class="score-item">
            <span class="score-label terminal-label">Shadow</span>
            <span class="score-value font-jet-lg" :style="{ color: scoreColor(r.shadow_score || 0) }">
              {{ (r.shadow_score || 0).toFixed(1) }}
            </span>
          </div>
          <div class="score-winner terminal-label" v-if="r.shadow_score && r.production_score">
            {{ r.shadow_score > r.production_score + 0.5 ? 'SHADOW 胜' : r.production_score > r.shadow_score + 0.5 ? 'PRODUCTION 胜' : '持平' }}
          </div>
        </div>
        <div v-if="r.score_reason" class="run-reason">{{ r.score_reason }}</div>
      </div>
      <div v-if="runs.length === 0" class="empty-state">
        <p>暂无执行记录</p>
        <p class="text-muted">Shadow 运行需要接入到实际分析流程中才会产生数据</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.shadow-page { animation: fadeIn 0.2s ease; }
.page-header {
  display: flex; align-items: flex-start; justify-content: space-between;
  margin-bottom: 1.25rem; gap: 1rem;
}
.page-title { font-size: inherit; font-weight: inherit; color: var(--color-text-primary); }
.page-desc { font-size: inherit; color: var(--color-text-muted); margin: 0.25rem 0 0; }
.header-actions { display: flex; gap: 0.5rem; flex-shrink: 0; }

.create-form {
  padding: 1rem; margin-bottom: 1rem;
  display: flex; flex-direction: column; gap: 0.75rem;
}
.form-row { display: flex; flex-direction: column; gap: 0.3rem; }
.form-row label { font-size: 0.82rem; font-weight: 600; color: var(--color-text-secondary); }
.required { color: var(--color-loss); }
.textarea { resize: vertical; font-family: monospace; font-size: 0.8rem; }
.pct-label { font-size: 0.82rem; color: var(--color-text-muted); }

.stats-bar { display: flex; gap: 0.75rem; margin-bottom: 1rem; flex-wrap: wrap; }
.stat-card {
  background: var(--color-bg-card); border: 1px solid var(--color-border);
  border-radius: var(--radius-md); padding: 0.6rem 1rem;
  display: flex; flex-direction: column; gap: 0.25rem;
}
.stat-value { font-size: inherit; font-weight: inherit; color: var(--color-text-primary); }
.stat-label { font-size: inherit; color: var(--color-text-muted); }

.tab-bar { display: flex; gap: 0; margin-bottom: 1rem; border-bottom: 1px solid var(--color-border); }
.tab {
  padding: 0.6rem 1rem; font-size: 0.85rem; font-weight: 600;
  color: var(--color-text-muted); background: transparent; border: none;
  cursor: pointer; border-bottom: 2px solid transparent;
  transition: all 0.15s;
}
.tab.active { color: var(--color-primary); border-bottom-color: var(--color-primary); }
.tab-badge {
  margin-left: 0.4rem; padding: 0.1rem 0.4rem;
  background: var(--color-primary-bg); border-radius: var(--radius-sm);
  font-size: 0.7rem;
}

.config-list { display: flex; flex-direction: column; gap: 0.5rem; }
.config-card {
  background: var(--color-bg-card); border: 1px solid var(--color-border);
  border-radius: var(--radius-md); padding: 0.75rem 1rem;
}
.config-header { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.4rem; }
.config-name { font-weight: 600; font-size: 0.9rem; color: var(--color-text-primary); }
.status-dot { width: 8px; height: 8px; border-radius: 50%; }
.status-dot.active { background: var(--color-profit); }
.status-dot.inactive { background: var(--color-text-muted); }
.config-type { font-size: inherit; color: var(--color-text-muted); margin-left: auto; }
.config-stats { display: flex; gap: 1rem; font-size: 0.78rem; color: var(--color-text-muted); margin-bottom: 0.4rem; }
.config-actions { display: flex; gap: 0.75rem; }

.runs-list { display: flex; flex-direction: column; gap: 0.5rem; }
.selection-banner {
  display: flex; align-items: center; justify-content: space-between;
  padding: 0.5rem 0.75rem; background: var(--color-primary-bg);
  border-radius: var(--radius-md); font-size: 0.82rem; margin-bottom: 0.5rem;
}
.run-card {
  background: var(--color-bg-card); border: 1px solid var(--color-border);
  border-radius: var(--radius-md); padding: 0.75rem 1rem;
}
.run-header { display: flex; gap: 0.75rem; font-size: 0.78rem; color: var(--color-text-muted); margin-bottom: 0.5rem; }
.run-duration { margin-left: auto; }
.run-scores { display: flex; align-items: center; gap: 0.75rem; margin-bottom: 0.4rem; }
.score-item { display: flex; flex-direction: column; align-items: center; gap: 0.15rem; }
.score-label { font-size: inherit; color: var(--color-text-muted); }
.score-value { font-size: inherit; font-weight: inherit; }
.score-vs { font-size: 0.8rem; color: var(--color-text-muted); }
.score-winner { margin-left: auto; font-size: inherit; font-weight: inherit; }
.run-reason { font-size: 0.78rem; color: var(--color-text-muted); font-style: italic; }

.empty-state { text-align: center; padding: 3rem 1rem; color: var(--color-text-muted); }
.btn-sm { padding: 0.4rem 0.75rem; font-size: 0.8rem; }
.btn-link { background: none; border: none; color: var(--color-primary); font-size: 0.78rem; cursor: pointer; padding: 0; }
.btn-link:hover { text-decoration: underline; }
.btn-danger { color: var(--color-loss); }

@media (max-width: 768px) {
  .stats-bar { flex-wrap: wrap; }
  .stat-card { flex: 1; min-width: 80px; }
}
</style>
