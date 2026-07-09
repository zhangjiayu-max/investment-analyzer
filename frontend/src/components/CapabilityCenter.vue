<script setup>
import { computed, onMounted, ref } from 'vue'
import {
  getCapabilitiesOverview,
  getCapabilitiesTools,
  getUnexposedMcp,
  getIntegrationGuide,
  getCapabilitiesStats,
  getCapabilitiesRecent,
  debugTool,
} from '../api'
import { useToast } from '../composables/useToast'
import Icon from './ui/Icon.vue'
import EmptyState from './ui/EmptyState.vue'

const { showToast } = useToast()

// ── 数据状态 ──
const overview = ref(null)
const tools = ref([])
const unexposed = ref(null)
const loading = ref(false)
const overviewLoading = ref(false)

// ── 筛选 ──
const filterSource = ref('')      // '' | builtin | ttfund | eastmoney | yingmi
const filterCategory = ref('')    // '' | 估值分析 | 持仓管理 ...
const filterStatus = ref('')      // '' | exposed | unexposed | disabled
const keyword = ref('')

// ── 展开/折叠 ──
const expandedTool = ref(null)     // 展开参数 schema 的工具名
const showUnexposedPanel = ref(false)

// ── 接入指引弹窗 ──
const guideModal = ref({ open: false, name: '', tool_name: '', source: '', cost: '', guide: '', loading: false })

// ── 来源元数据 ──
const SOURCE_META = {
  builtin:    { label: '内置',     color: 'var(--color-primary)', bg: 'var(--color-primary-bg)' },
  ttfund:     { label: '天天基金', color: '#e8590c', bg: 'rgba(232,89,12,0.10)' },
  eastmoney:  { label: '东方财富', color: '#1971c2', bg: 'rgba(25,113,194,0.10)' },
  yingmi:     { label: '盈米且慢', color: '#0ca678', bg: 'rgba(12,166,120,0.10)' },
}

const COST_META = {
  none: { label: '已接入', cls: 'cost-none' },
  low:  { label: '低成本', cls: 'cost-low' },
  high: { label: '需封装', cls: 'cost-high' },
}

// ── 分类列表（从 tools 派生）──
const categories = computed(() => {
  const set = new Set(tools.value.map(t => t.category))
  return Array.from(set).sort()
})

// ── 筛选后的工具列表 ──
const filteredTools = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  return tools.value.filter(t => {
    if (filterSource.value && t.source !== filterSource.value) return false
    if (filterCategory.value && t.category !== filterCategory.value) return false
    if (filterStatus.value === 'exposed' && !t.exposed) return false
    if (filterStatus.value === 'unexposed' && t.exposed) return false
    if (filterStatus.value === 'disabled' && t.enabled) return false
    if (kw) {
      const hay = `${t.name} ${t.description} ${t.category}`.toLowerCase()
      if (!hay.includes(kw)) return false
    }
    return true
  })
})

// ── 统计卡片数据 ──
const sourceCards = computed(() => {
  if (!overview.value) return []
  const by = overview.value.by_source || {}
  return Object.keys(SOURCE_META).map(key => ({
    key,
    label: SOURCE_META[key].label,
    color: SOURCE_META[key].color,
    bg: SOURCE_META[key].bg,
    total: by[key]?.total ?? 0,
    exposed: by[key]?.exposed ?? 0,
    unexposed: by[key]?.unexposed ?? 0,
  }))
})

// ── 加载数据 ──
async function loadOverview() {
  overviewLoading.value = true
  try {
    const { data } = await getCapabilitiesOverview()
    overview.value = data
  } catch (e) {
    showToast('加载总览失败: ' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    overviewLoading.value = false
  }
}

async function loadTools() {
  loading.value = true
  try {
    const { data } = await getCapabilitiesTools()
    tools.value = data.tools || []
  } catch (e) {
    showToast('加载能力清单失败: ' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    loading.value = false
  }
}

async function loadUnexposed() {
  try {
    const { data } = await getUnexposedMcp()
    unexposed.value = data
  } catch (e) {
    console.error('加载未暴露能力失败:', e)
  }
}

// ── 交互 ──
function toggleExpand(name) {
  expandedTool.value = expandedTool.value === name ? null : name
}

function paramEntries(params) {
  if (!params || typeof params !== 'object') return []
  const props = params.properties || {}
  const required = params.required || []
  return Object.keys(props).map(k => ({
    name: k,
    type: props[k]?.type || 'any',
    desc: props[k]?.description || '',
    required: required.includes(k),
  }))
}

async function viewGuide(tool) {
  guideModal.value = {
    open: true,
    name: tool.name,
    tool_name: '',
    source: tool.source,
    cost: tool.cost,
    guide: '',
    loading: true,
  }
  try {
    const { data } = await getIntegrationGuide(tool.name)
    guideModal.value.tool_name = data.tool_name
    guideModal.value.guide = data.guide
  } catch (e) {
    guideModal.value.guide = '加载失败: ' + (e.response?.data?.detail || e.message)
    showToast('加载接入指引失败', 'error')
  } finally {
    guideModal.value.loading = false
  }
}

function closeGuide() {
  guideModal.value.open = false
}

function copyGuide() {
  const text = guideModal.value.guide
  if (!text) return
  navigator.clipboard.writeText(text).then(
    () => showToast('已复制到剪贴板', 'success'),
    () => showToast('复制失败，请手动选择', 'warning'),
  )
}

function resetFilters() {
  filterSource.value = ''
  filterCategory.value = ''
  filterStatus.value = ''
  keyword.value = ''
}

// ── Tab 切换 ──
const activeTab = ref('list')   // 'list' | 'stats' | 'debug'
const statsLoaded = ref(false)

function switchTab(tab) {
  activeTab.value = tab
  if (tab === 'stats' && !statsLoaded.value) loadStats()
}

// ── 调用统计 ──
const statsDays = ref(7)
const stats = ref(null)
const statsLoading = ref(false)
const expandedStatTool = ref(null)
const toolRecentMap = ref({})   // tool_name → { loading, records }

async function loadStats() {
  statsLoading.value = true
  try {
    const { data } = await getCapabilitiesStats(statsDays.value)
    stats.value = data
    statsLoaded.value = true
  } catch (e) {
    showToast('加载调用统计失败: ' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    statsLoading.value = false
  }
}

async function expandStatTool(name) {
  if (expandedStatTool.value === name) {
    expandedStatTool.value = null
    return
  }
  expandedStatTool.value = name
  if (!toolRecentMap.value[name]) {
    toolRecentMap.value = { ...toolRecentMap.value, [name]: { loading: true, records: [] } }
    try {
      const { data } = await getCapabilitiesRecent(name, 5)
      toolRecentMap.value = {
        ...toolRecentMap.value,
        [name]: { loading: false, records: data.records || data || [] },
      }
    } catch (e) {
      toolRecentMap.value = {
        ...toolRecentMap.value,
        [name]: { loading: false, records: [], error: e.message },
      }
    }
  }
}

const statsByDayBars = computed(() => {
  const arr = stats.value?.by_day || []
  if (!arr.length) return []
  const max = Math.max(...arr.map(d => d.calls || 0), 1)
  const W = 800, H = 200, pad = 20, gap = 3
  const barW = Math.max((W - pad * 2 - gap * (arr.length - 1)) / arr.length, 1)
  return arr.map((d, i) => {
    const calls = d.calls || 0
    const success = d.success ?? 0
    const failed = d.failed ?? Math.max(calls - success, 0)
    const h = (calls / max) * (H - pad * 2)
    const fh = calls > 0 ? (failed / calls) * h : 0
    const sh = Math.max(h - fh, 0)
    return {
      x: pad + i * (barW + gap),
      barW,
      y_success: pad + (h - sh),
      sh,
      y_failed: pad + h - fh,
      fh,
      date: d.date,
      calls,
      success,
      failed,
    }
  })
})

const statsErrorCategories = computed(() => {
  const obj = stats.value?.by_error_category || {}
  const colors = {
    none: 'var(--color-success, #0ca678)',
    tool_error: 'var(--color-danger, #e8590c)',
    timeout: 'var(--color-warning, #f59f00)',
    validation_failed: '#1971c2',
  }
  const labels = {
    none: '无错误',
    tool_error: '工具错误',
    timeout: '超时',
    validation_failed: '参数校验失败',
  }
  const entries = Object.keys(obj).map(k => ({
    key: k,
    count: obj[k] || 0,
    label: labels[k] || k,
    color: colors[k] || 'var(--color-text-muted)',
  }))
  const max = Math.max(...entries.map(e => e.count), 1)
  return entries.map(e => ({ ...e, pct: (e.count / max) * 100 }))
})

function formatPercent(v) {
  if (v == null) return '—'
  return (v * 100).toFixed(1) + '%'
}
function formatMs(v) {
  if (v == null) return '—'
  return Math.round(v)
}

// ── 调试台 ──
const debugToolName = ref('')
const debugArgs = ref({})
const debugRunning = ref(false)
const debugResult = ref(null)   // { success, duration_ms, trace_id, result, error, raw }
const debugResultCollapsed = ref(false)
const debugHistory = ref([])
const DEBUG_HISTORY_KEY = 'cap_debug_history'

const exposedTools = computed(() => tools.value.filter(t => t.exposed))
const selectedDebugTool = computed(() => tools.value.find(t => t.name === debugToolName.value))

const selectedDebugParams = computed(() => {
  const t = selectedDebugTool.value
  if (!t || !t.parameters) return []
  const props = t.parameters.properties || {}
  const required = t.parameters.required || []
  return Object.keys(props).map(k => ({
    name: k,
    type: props[k]?.type || 'any',
    desc: props[k]?.description || '',
    required: required.includes(k),
    enum: props[k]?.enum || null,
  }))
})

function selectDebugTool(name) {
  debugToolName.value = name
  debugArgs.value = {}
  debugResult.value = null
}

function buildDebugArgs() {
  const out = {}
  for (const p of selectedDebugParams.value) {
    const raw = debugArgs.value[p.name]
    if (raw === undefined || raw === '') continue
    if (p.type === 'number' || p.type === 'integer') {
      out[p.name] = Number(raw)
    } else if (p.type === 'boolean') {
      out[p.name] = !!raw
    } else if (p.type === 'array') {
      out[p.name] = String(raw).split(',').map(s => s.trim()).filter(Boolean)
    } else {
      out[p.name] = raw
    }
  }
  return out
}

async function runDebug() {
  if (!debugToolName.value) {
    showToast('请先选择工具', 'warning')
    return
  }
  debugRunning.value = true
  debugResult.value = null
  const args = buildDebugArgs()
  const startTs = Date.now()
  try {
    const { data } = await debugTool(debugToolName.value, args)
    debugResult.value = {
      success: data.success !== false,
      duration_ms: data.duration_ms ?? (Date.now() - startTs),
      trace_id: data.trace_id || '',
      result: data.result ?? data,
      error: data.error || '',
      raw: data,
    }
    saveDebugHistory({
      time: new Date().toISOString(),
      tool: debugToolName.value,
      args,
      duration_ms: debugResult.value.duration_ms,
      success: debugResult.value.success,
    })
  } catch (e) {
    debugResult.value = {
      success: false,
      duration_ms: Date.now() - startTs,
      trace_id: '',
      result: null,
      error: e.response?.data?.detail || e.message,
      raw: null,
    }
    showToast('调试执行失败: ' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    debugRunning.value = false
  }
}

function copyDebugResult() {
  if (!debugResult.value) return
  const text = JSON.stringify(debugResult.value.raw ?? debugResult.value.result, null, 2)
  navigator.clipboard.writeText(text).then(
    () => showToast('已复制结果', 'success'),
    () => showToast('复制失败', 'warning'),
  )
}

function rerunDebug() {
  runDebug()
}

function loadDebugHistoryFromStorage() {
  try {
    const raw = localStorage.getItem(DEBUG_HISTORY_KEY)
    debugHistory.value = raw ? JSON.parse(raw) : []
  } catch {
    debugHistory.value = []
  }
}

function saveDebugHistory(item) {
  debugHistory.value = [item, ...debugHistory.value].slice(0, 10)
  try {
    localStorage.setItem(DEBUG_HISTORY_KEY, JSON.stringify(debugHistory.value))
  } catch {}
}

function loadFromHistory(item) {
  debugToolName.value = item.tool
  debugArgs.value = { ...(item.args || {}) }
  debugResult.value = null
}

function highlightJson(obj) {
  if (obj == null) return ''
  let json
  try {
    json = JSON.stringify(obj, null, 2)
  } catch {
    json = String(obj)
  }
  const escaped = json.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  return escaped
    .replace(/("(?:\\.|[^"\\])*")(\s*:)/g, '<span class="j-key">$1</span>$2')
    .replace(/:\s*("(?:\\.|[^"\\])*")/g, ': <span class="j-str">$1</span>')
    .replace(/:\s*(-?\d+\.?\d*(?:e[+-]?\d+)?)/gi, ': <span class="j-num">$1</span>')
    .replace(/:\s*(true|false|null)/g, ': <span class="j-bool">$1</span>')
}

onMounted(() => {
  loadOverview()
  loadTools()
  loadUnexposed()
  loadDebugHistoryFromStorage()
})
</script>

<template>
  <div class="cap-page">
    <header class="page-header">
      <div>
        <h2 class="page-title editorial-title-lg">能力中心</h2>
        <p class="page-desc">系统所有工具能力可视化 + MCP 扩展入口。聚合内置工具与天天基金/东方财富/盈米三个 MCP 客户端，共 {{ overview?.total ?? '—' }} 个能力。</p>
      </div>
    </header>

    <!-- Tab 导航 -->
    <nav class="cap-tabs">
      <button class="cap-tab" :class="{ active: activeTab === 'list' }" @click="switchTab('list')">
        <Icon name="wrench" :size="16" /> 能力清单
      </button>
      <button class="cap-tab" :class="{ active: activeTab === 'stats' }" @click="switchTab('stats')">
        <Icon name="chart" :size="16" /> 调用统计
      </button>
      <button class="cap-tab" :class="{ active: activeTab === 'debug' }" @click="switchTab('debug')">
        <Icon name="bug" :size="16" /> 调试台
      </button>
    </nav>

    <!-- ── Tab 1：能力清单 ── -->
    <div v-if="activeTab === 'list'" class="tab-pane">
    <!-- 总览卡片 -->
    <section class="overview-section editorial-card" v-if="overview">
      <div class="overview-summary">
        <div class="summary-item">
          <span class="summary-num font-jet">{{ overview.total }}</span>
          <span class="summary-label terminal-label">总能力</span>
        </div>
        <div class="summary-item">
          <span class="summary-num font-jet positive">{{ overview.exposed }}</span>
          <span class="summary-label terminal-label">已暴露</span>
        </div>
        <div class="summary-item">
          <span class="summary-num font-jet warning">{{ overview.unexposed }}</span>
          <span class="summary-label terminal-label">可扩展</span>
        </div>
      </div>
      <div class="source-cards">
        <div
          v-for="card in sourceCards"
          :key="card.key"
          class="source-card"
          :class="{ active: filterSource === card.key }"
          :style="{ '--card-color': card.color, '--card-bg': card.bg }"
          @click="filterSource = filterSource === card.key ? '' : card.key"
        >
          <div class="source-head">
            <span class="source-label">{{ card.label }}</span>
            <span class="source-key font-jet">{{ card.key }}</span>
          </div>
          <div class="source-stats">
            <span class="source-total font-jet">{{ card.total }}</span>
            <span class="source-split">
              <span class="exposed font-jet">{{ card.exposed }}</span>
              <span class="sep">/</span>
              <span class="unexposed font-jet" v-if="card.unexposed">{{ card.unexposed }}</span>
              <span class="unexposed font-jet" v-else>0</span>
            </span>
          </div>
          <div class="source-legend">
            <span>已暴露 {{ card.exposed }}</span>
            <span v-if="card.unexposed">未暴露 {{ card.unexposed }}</span>
          </div>
        </div>
      </div>
    </section>

    <!-- 筛选栏 -->
    <section class="filter-bar editorial-card">
      <div class="filter-group">
        <label class="filter-label terminal-label">来源</label>
        <select v-model="filterSource" class="filter-select font-jet">
          <option value="">全部</option>
          <option v-for="c in sourceCards" :key="c.key" :value="c.key">{{ c.label }}</option>
        </select>
      </div>
      <div class="filter-group">
        <label class="filter-label terminal-label">分类</label>
        <select v-model="filterCategory" class="filter-select font-jet">
          <option value="">全部</option>
          <option v-for="cat in categories" :key="cat" :value="cat">{{ cat }}</option>
        </select>
      </div>
      <div class="filter-group">
        <label class="filter-label terminal-label">状态</label>
        <select v-model="filterStatus" class="filter-select font-jet">
          <option value="">全部</option>
          <option value="exposed">已暴露</option>
          <option value="unexposed">未暴露</option>
          <option value="disabled">已禁用</option>
        </select>
      </div>
      <div class="filter-group search-group">
        <label class="filter-label terminal-label">搜索</label>
        <input v-model="keyword" class="filter-input font-jet" placeholder="工具名 / 描述" />
      </div>
      <button class="filter-reset" @click="resetFilters" v-if="filterSource || filterCategory || filterStatus || keyword">
        <Icon name="x" :size="14" /> 重置
      </button>
      <div class="filter-count font-jet">{{ filteredTools.length }} / {{ tools.length }}</div>
    </section>

    <!-- 能力列表 -->
    <section class="tools-section">
      <div v-if="loading" class="loading-state">
        <div class="spinner"></div>
        <span>加载能力清单...</span>
      </div>

      <EmptyState
        v-else-if="!filteredTools.length"
        title="无匹配能力"
        description="尝试调整筛选条件或重置筛选"
      />

      <div v-else class="tools-grid">
        <article
          v-for="tool in filteredTools"
          :key="tool.name + tool.source"
          class="tool-card"
          :class="{ expanded: expandedTool === tool.name, unexposed: !tool.exposed }"
          @click="toggleExpand(tool.name)"
        >
          <div class="tool-head">
            <span class="tool-name font-jet">{{ tool.name }}</span>
            <div class="tool-badges">
              <span
                class="badge source-badge"
                :style="{ color: SOURCE_META[tool.source]?.color, background: SOURCE_META[tool.source]?.bg }"
              >{{ SOURCE_META[tool.source]?.label || tool.source }}</span>
              <span class="badge category-badge">{{ tool.category }}</span>
            </div>
          </div>

          <p class="tool-desc">{{ tool.description || '无描述' }}</p>

          <div class="tool-meta">
            <span class="meta-tag" :class="tool.exposed ? 'tag-exposed' : 'tag-unexposed'">
              {{ tool.exposed ? '已暴露' : '未暴露' }}
            </span>
            <span v-if="tool.exposed" class="meta-tag" :class="tool.enabled ? 'tag-enabled' : 'tag-disabled'">
              {{ tool.enabled ? '已启用' : '已禁用' }}
            </span>
            <span v-if="!tool.exposed" class="meta-tag" :class="COST_META[tool.cost]?.cls">
              {{ COST_META[tool.cost]?.label || tool.cost }}
            </span>
            <span v-if="tool.async_required" class="meta-tag tag-async">异步</span>
          </div>

          <!-- 参数 schema（展开时） -->
          <div v-if="expandedTool === tool.name" class="tool-detail" @click.stop>
            <div v-if="tool.exposed && paramEntries(tool.parameters).length" class="param-list">
              <div class="detail-title">参数 schema</div>
              <div v-for="p in paramEntries(tool.parameters)" :key="p.name" class="param-row">
                <span class="param-name font-jet">{{ p.name }}</span>
                <span class="param-type font-jet">{{ p.type }}</span>
                <span v-if="p.required" class="param-required">必填</span>
                <span class="param-desc">{{ p.desc }}</span>
              </div>
              <div v-if="!paramEntries(tool.parameters).length" class="empty-params">无参数</div>
            </div>
            <div v-else-if="tool.exposed" class="empty-params">该工具无参数 schema</div>

            <!-- 未暴露能力的接入指引入口 -->
            <div v-if="!tool.exposed" class="integration-block">
              <div class="integration-info">
                <div class="info-row">
                  <span class="info-label">来源</span>
                  <span class="info-val font-jet">{{ SOURCE_META[tool.source]?.label || tool.source }}</span>
                </div>
                <div class="info-row" v-if="tool.skill_id">
                  <span class="info-label">Skill ID</span>
                  <span class="info-val font-jet">{{ tool.skill_id }}</span>
                </div>
                <div class="info-row" v-if="tool.endpoint">
                  <span class="info-label">Endpoint</span>
                  <span class="info-val font-jet">{{ tool.endpoint }}</span>
                </div>
                <div class="info-row">
                  <span class="info-label">Python 方法</span>
                  <span class="info-val" :class="tool.has_method ? 'positive' : 'warning'">
                    {{ tool.has_method ? '已封装' : '需新增封装' }}
                  </span>
                </div>
                <div class="info-row">
                  <span class="info-label">接入成本</span>
                  <span class="info-val" :class="tool.cost === 'low' ? 'positive' : 'warning'">
                    {{ COST_META[tool.cost]?.label || tool.cost }}
                  </span>
                </div>
              </div>
              <button class="guide-btn" @click.stop="viewGuide(tool)">
                <Icon name="code" :size="14" /> 查看接入指引
              </button>
            </div>
          </div>
        </article>
      </div>
    </section>

    <!-- 扩展面板 -->
    <section class="extend-panel editorial-card" v-if="unexposed && unexposed.total">
      <button class="extend-toggle" @click="showUnexposedPanel = !showUnexposedPanel">
        <Icon :name="showUnexposedPanel ? 'chevron-down' : 'chevron-right'" :size="16" />
        <span class="extend-title">MCP 扩展面板</span>
        <span class="extend-count font-jet">{{ unexposed.total }} 个未暴露能力</span>
        <span class="extend-split">
          <span class="low">低成本 {{ unexposed.low_cost }}</span>
          <span class="high">需封装 {{ unexposed.high_cost }}</span>
        </span>
      </button>

      <div v-if="showUnexposedPanel" class="extend-body">
        <div class="extend-group" v-if="unexposed.ttfund.length">
          <h4 class="group-title">
            <span class="group-dot" style="background: #e8590c"></span>
            天天基金 ttfund
            <span class="group-count font-jet">{{ unexposed.ttfund.length }}</span>
          </h4>
          <div class="extend-items">
            <div v-for="item in unexposed.ttfund" :key="item.name" class="extend-item">
              <div class="ext-head">
                <span class="ext-name font-jet">{{ item.name }}</span>
                <span class="ext-cost" :class="item.cost === 'low' ? 'cost-low' : 'cost-high'">
                  {{ COST_META[item.cost]?.label || item.cost }}
                </span>
              </div>
              <p class="ext-desc">{{ item.description }}</p>
              <code class="ext-skill font-jet">{{ item.skill_id }}</code>
            </div>
          </div>
        </div>

        <div class="extend-group" v-if="unexposed.eastmoney.length">
          <h4 class="group-title">
            <span class="group-dot" style="background: #1971c2"></span>
            东方财富 eastmoney
            <span class="group-count font-jet">{{ unexposed.eastmoney.length }}</span>
          </h4>
          <div class="extend-items">
            <div v-for="item in unexposed.eastmoney" :key="item.name" class="extend-item">
              <div class="ext-head">
                <span class="ext-name font-jet">{{ item.name }}</span>
                <span class="ext-cost" :class="item.cost === 'low' ? 'cost-low' : 'cost-high'">
                  {{ COST_META[item.cost]?.label || item.cost }}
                </span>
              </div>
              <p class="ext-desc">{{ item.description }}</p>
              <code class="ext-skill font-jet">{{ item.endpoint }}</code>
            </div>
          </div>
        </div>

        <!-- 后续扩展方向 -->
        <div class="future-section">
          <h4 class="group-title">
            <Icon name="sparkles" :size="14" />
            后续扩展方向
          </h4>
          <ul class="future-list">
            <li><strong>MCP 动态发现</strong> — 盈米 yingmi_client 已有 list_tools() 方法，可运行时发现新能力</li>
            <li><strong>工具热插拔</strong> — tool_registry 表已有 enabled 字段，支持运行时启停工具</li>
            <li><strong>能力依赖图</strong> — 展示哪些专家 Agent 使用哪些工具，辅助配置优化</li>
            <li><strong>新 MCP 接入向导</strong> — 标准化新 MCP 客户端接入流程（endpoint + API key + 工具映射）</li>
            <li><strong>能力使用统计</strong> — 从 tool_audit_logs 聚合调用频次/成功率，展示在能力卡片上</li>
          </ul>
        </div>
      </div>
    </section>
    </div><!-- /Tab 1：能力清单 -->

    <!-- ── Tab 2：调用统计 ── -->
    <div v-if="activeTab === 'stats'" class="tab-pane stats-pane">
      <!-- 顶部控制栏 -->
      <section class="editorial-card stats-toolbar">
        <div class="days-group">
          <button v-for="d in [1, 7, 30, 90]" :key="d"
            class="days-btn font-jet" :class="{ active: statsDays === d }"
            @click="statsDays = d; loadStats()">
            {{ d }}天
          </button>
        </div>
        <button class="refresh-btn" @click="loadStats" :disabled="statsLoading">
          <Icon name="refresh" :size="14" /> 刷新
        </button>
      </section>

      <div v-if="statsLoading && !stats" class="loading-state">
        <div class="spinner"></div>
        <span>加载调用统计...</span>
      </div>

      <template v-else-if="stats">
        <!-- 总览卡片 -->
        <section class="editorial-card stats-overview">
          <div class="stat-card">
            <span class="stat-num font-jet">{{ stats.total_calls }}</span>
            <span class="stat-label terminal-label">总调用次数</span>
          </div>
          <div class="stat-card">
            <span class="stat-num font-jet positive">{{ formatPercent(stats.overall_success_rate) }}</span>
            <span class="stat-label terminal-label">成功率</span>
          </div>
          <div class="stat-card">
            <span class="stat-num font-jet">{{ formatMs(stats.avg_duration_ms) }} ms</span>
            <span class="stat-label terminal-label">平均耗时</span>
          </div>
          <div class="stat-card">
            <span class="stat-num font-jet warning">{{ stats.total_failed }}</span>
            <span class="stat-label terminal-label">失败次数</span>
          </div>
        </section>

        <!-- 调用趋势图 -->
        <section class="editorial-card stats-chart" v-if="statsByDayBars.length">
          <h3 class="block-title">调用趋势</h3>
          <svg class="day-chart" viewBox="0 0 800 200" preserveAspectRatio="none">
            <g v-for="(bar, i) in statsByDayBars" :key="i" class="day-bar-group">
              <rect :x="bar.x" :y="bar.y_success" :width="bar.barW" :height="Math.max(bar.sh, 0)"
                fill="var(--color-primary)" class="bar-success" />
              <rect v-if="bar.fh > 0" :x="bar.x" :y="bar.y_failed" :width="bar.barW" :height="Math.max(bar.fh, 0)"
                fill="var(--color-danger, #e8590c)" class="bar-failed" />
              <title>{{ bar.date }} | 调用 {{ bar.calls }} | 成功 {{ bar.success }} | 失败 {{ bar.failed }}</title>
            </g>
          </svg>
          <div class="chart-legend">
            <span><i class="dot dot-primary"></i> 成功</span>
            <span><i class="dot dot-danger"></i> 失败</span>
          </div>
        </section>

        <!-- 工具调用明细表 -->
        <section class="editorial-card stats-table-wrap">
          <h3 class="block-title">工具调用明细</h3>
          <div class="stats-table-scroll">
            <table class="stats-table">
              <thead>
                <tr>
                  <th>工具名</th>
                  <th>调用次数</th>
                  <th>成功率</th>
                  <th>平均耗时</th>
                  <th>最大耗时</th>
                  <th>P95</th>
                </tr>
              </thead>
              <tbody>
                <template v-for="t in (stats.by_tool || [])" :key="t.tool_name">
                  <tr class="tool-row" :class="{ warn: (t.success_rate ?? 1) < 0.9 }"
                      @click="expandStatTool(t.tool_name)">
                    <td class="font-jet">{{ t.tool_name }}</td>
                    <td class="font-jet">{{ t.total_calls }}</td>
                    <td class="font-jet" :class="{ 'warn-text': (t.success_rate ?? 1) < 0.9 }">
                      {{ formatPercent(t.success_rate) }}
                    </td>
                    <td class="font-jet">{{ formatMs(t.avg_duration_ms) }} ms</td>
                    <td class="font-jet">{{ formatMs(t.max_duration_ms) }} ms</td>
                    <td class="font-jet">{{ formatMs(t.p95_duration_ms) }} ms</td>
                  </tr>
                  <tr v-if="expandedStatTool === t.tool_name" class="tool-expand-row">
                    <td colspan="6">
                      <div class="expand-detail">
                        <!-- 错误分类分布 -->
                        <div class="err-cats-block" v-if="t.error_categories">
                          <div class="sub-title">错误分类分布</div>
                          <div v-for="(cnt, cat) in t.error_categories" :key="cat" class="err-cat-row">
                            <span class="err-cat-label">{{ cat }}</span>
                            <div class="err-cat-bar">
                              <div class="err-cat-fill"
                                :style="{ width: ((cnt || 0) / Math.max(t.total_calls || 1, 1) * 100) + '%' }">
                              </div>
                            </div>
                            <span class="err-cat-num font-jet">{{ cnt }}</span>
                          </div>
                        </div>
                        <!-- 最近调用记录 -->
                        <div class="recent-records">
                          <div class="sub-title">最近调用</div>
                          <div v-if="toolRecentMap[t.tool_name]?.loading" class="mini-loading">加载中...</div>
                          <div v-else-if="toolRecentMap[t.tool_name]?.records?.length" class="recent-list">
                            <div v-for="(r, i) in toolRecentMap[t.tool_name].records" :key="i" class="recent-item">
                              <span class="recent-status" :class="r.success ? 'ok' : 'fail'">{{ r.success ? '✓' : '✗' }}</span>
                              <span class="recent-time font-jet">{{ r.called_at || r.created_at || '' }}</span>
                              <span class="recent-dur font-jet">{{ formatMs(r.duration_ms) }}ms</span>
                              <span v-if="!r.success && r.error" class="recent-err">{{ r.error }}</span>
                            </div>
                          </div>
                          <div v-else class="mini-loading">暂无记录</div>
                        </div>
                      </div>
                    </td>
                  </tr>
                </template>
                <tr v-if="!(stats.by_tool || []).length">
                  <td colspan="6" class="empty-params">暂无工具调用数据</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <!-- 错误分类总分布 -->
        <section class="editorial-card stats-error-cats" v-if="statsErrorCategories.length">
          <h3 class="block-title">错误分类分布</h3>
          <div class="err-cat-chart">
            <div v-for="e in statsErrorCategories" :key="e.key" class="err-cat-bar-row">
              <span class="err-cat-label">{{ e.label }}</span>
              <div class="err-cat-track">
                <div class="err-cat-fill" :style="{ width: e.pct + '%', background: e.color }"></div>
              </div>
              <span class="err-cat-num font-jet">{{ e.count }}</span>
            </div>
          </div>
        </section>
      </template>
    </div><!-- /Tab 2：调用统计 -->

    <!-- ── Tab 3：调试台 ── -->
    <div v-if="activeTab === 'debug'" class="tab-pane debug-pane">
      <section class="editorial-card debug-section">
        <!-- 工具选择 -->
        <div class="debug-block">
          <label class="block-title">选择工具</label>
          <select v-model="debugToolName" @change="selectDebugTool(debugToolName)" class="debug-tool-select font-jet">
            <option value="">— 请选择 —</option>
            <option v-for="t in exposedTools" :key="t.name" :value="t.name">{{ t.name }}</option>
          </select>
          <p v-if="selectedDebugTool" class="debug-tool-desc">{{ selectedDebugTool.description }}</p>
        </div>

        <!-- 参数表单 -->
        <div class="debug-block" v-if="selectedDebugTool">
          <label class="block-title">参数</label>
          <div v-if="selectedDebugParams.length" class="param-form">
            <div v-for="p in selectedDebugParams" :key="p.name" class="param-field">
              <label class="param-field-label">
                <span class="param-field-name font-jet">{{ p.name }}</span>
                <span v-if="p.required" class="req-star">*</span>
                <span class="param-field-type font-jet">{{ p.type }}</span>
              </label>
              <p v-if="p.desc" class="param-field-desc">{{ p.desc }}</p>
              <select v-if="p.enum" v-model="debugArgs[p.name]" class="param-input font-jet">
                <option value="">—</option>
                <option v-for="opt in p.enum" :key="opt" :value="opt">{{ opt }}</option>
              </select>
              <input v-else-if="p.type === 'number' || p.type === 'integer'"
                v-model="debugArgs[p.name]" type="number" class="param-input font-jet" />
              <label v-else-if="p.type === 'boolean'" class="param-toggle">
                <input type="checkbox" v-model="debugArgs[p.name]" />
                <span>{{ debugArgs[p.name] ? 'true' : 'false' }}</span>
              </label>
              <input v-else-if="p.type === 'array'"
                v-model="debugArgs[p.name]" class="param-input font-jet" placeholder="逗号分隔" />
              <input v-else
                v-model="debugArgs[p.name]" class="param-input font-jet" />
            </div>
          </div>
          <div v-else class="empty-params">该工具无参数</div>
        </div>

        <!-- 执行按钮 -->
        <div class="debug-block" v-if="selectedDebugTool">
          <button class="run-btn" @click="runDebug" :disabled="debugRunning">
            <Icon :name="debugRunning ? 'spinner' : 'zap'" :size="16" />
            {{ debugRunning ? '执行中...' : '▶ 运行调试' }}
          </button>
        </div>

        <!-- 结果展示 -->
        <div class="debug-block" v-if="debugResult">
          <div class="debug-info-bar">
            <span class="info-chip">耗时 {{ formatMs(debugResult.duration_ms) }} ms</span>
            <span class="info-chip" :class="debugResult.success ? 'chip-ok' : 'chip-fail'">
              {{ debugResult.success ? '✅ 成功' : '❌ 失败' }}
            </span>
            <span v-if="debugResult.trace_id" class="info-chip font-jet">trace: {{ debugResult.trace_id }}</span>
            <div class="info-actions">
              <button class="mini-btn" @click="debugResultCollapsed = !debugResultCollapsed">
                <Icon :name="debugResultCollapsed ? 'chevron-right' : 'chevron-down'" :size="14" />
                {{ debugResultCollapsed ? '展开' : '折叠' }}
              </button>
              <button class="mini-btn" @click="copyDebugResult"><Icon name="copy" :size="14" /> 复制</button>
              <button class="mini-btn" @click="rerunDebug"><Icon name="refresh" :size="14" /> 重新运行</button>
            </div>
          </div>
          <div v-if="debugResult.error" class="debug-error-box">
            <span class="err-label">错误：</span>{{ debugResult.error }}
          </div>
          <pre v-if="!debugResultCollapsed && debugResult.result != null"
            class="debug-result-pre font-jet" v-html="highlightJson(debugResult.result)"></pre>
        </div>
      </section>

      <!-- 历史调试记录 -->
      <section class="editorial-card debug-history" v-if="debugHistory.length">
        <h3 class="block-title">调试历史（本会话）</h3>
        <div class="history-list">
          <div v-for="(h, i) in debugHistory" :key="i" class="history-item" @click="loadFromHistory(h)">
            <span class="hist-time font-jet">{{ (h.time || '').replace('T', ' ').slice(0, 19) }}</span>
            <span class="hist-tool font-jet">{{ h.tool }}</span>
            <span class="hist-args font-jet">{{ JSON.stringify(h.args || {}) }}</span>
            <span class="hist-dur font-jet">{{ formatMs(h.duration_ms) }}ms</span>
            <span class="hist-status" :class="h.success ? 'ok' : 'fail'">{{ h.success ? '✓' : '✗' }}</span>
          </div>
        </div>
      </section>
    </div><!-- /Tab 3：调试台 -->

    <!-- 接入指引弹窗 -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="guideModal.open" class="guide-modal-mask" @click.self="closeGuide">
          <div class="guide-modal">
            <header class="guide-modal-head">
              <div>
                <h3 class="guide-modal-title">接入指引 — <span class="font-jet">{{ guideModal.name }}</span></h3>
                <div class="guide-modal-meta">
                  <span class="badge source-badge">{{ SOURCE_META[guideModal.source]?.label || guideModal.source }}</span>
                  <span class="meta-tag" :class="COST_META[guideModal.cost]?.cls">{{ COST_META[guideModal.cost]?.label || guideModal.cost }}</span>
                  <span v-if="guideModal.tool_name" class="tool-name-preview font-jet">→ {{ guideModal.tool_name }}</span>
                </div>
              </div>
              <button class="guide-close" @click="closeGuide"><Icon name="x" :size="18" /></button>
            </header>
            <div class="guide-modal-body">
              <div v-if="guideModal.loading" class="loading-state">
                <div class="spinner"></div>
                <span>生成接入指引...</span>
              </div>
              <pre v-else class="guide-code font-jet">{{ guideModal.guide }}</pre>
            </div>
            <footer class="guide-modal-foot">
              <span class="foot-hint">按指引手动粘贴到对应文件后，重启后端生效。</span>
              <div class="foot-actions">
                <button class="btn-secondary" @click="closeGuide">关闭</button>
                <button class="btn-primary" @click="copyGuide" :disabled="!guideModal.guide">
                  <Icon name="copy" :size="14" /> 复制
                </button>
              </div>
            </footer>
          </div>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<style scoped>
.cap-page {
  padding: var(--space-6);
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: var(--space-4);
  flex-wrap: wrap;
}
.page-desc {
  color: var(--color-text-muted);
  font-size: 0.85rem;
  margin-top: 4px;
  max-width: 720px;
}

/* ── 总览卡片 ── */
.overview-section {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}
.overview-summary {
  display: flex;
  gap: var(--space-6);
  padding-bottom: var(--space-3);
  border-bottom: 1px solid var(--color-border);
}
.summary-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.summary-num {
  font-size: 1.6rem;
  font-weight: 600;
  color: var(--color-text-primary);
  line-height: 1.1;
}
.summary-num.positive { color: var(--color-success, #0ca678); }
.summary-num.warning { color: var(--color-warning, #e8590c); }
.summary-label {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  letter-spacing: 0.05em;
}

.source-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: var(--space-3);
}
.source-card {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: var(--space-3);
  cursor: pointer;
  transition: all 0.15s;
  background: var(--color-bg-input);
  border-left: 3px solid var(--card-color);
}
.source-card:hover {
  border-color: var(--card-color);
  transform: translateY(-1px);
}
.source-card.active {
  background: var(--card-bg);
  border-color: var(--card-color);
}
.source-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}
.source-label {
  font-size: 0.9rem;
  font-weight: 600;
  color: var(--card-color);
}
.source-key {
  font-size: 0.7rem;
  color: var(--color-text-muted);
}
.source-stats {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 4px;
}
.source-total {
  font-size: 1.3rem;
  font-weight: 600;
  color: var(--color-text-primary);
}
.source-split {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 0.85rem;
}
.source-split .exposed { color: var(--color-success, #0ca678); }
.source-split .sep { color: var(--color-text-muted); }
.source-split .unexposed { color: var(--color-warning, #e8590c); }
.source-legend {
  display: flex;
  justify-content: space-between;
  font-size: 0.72rem;
  color: var(--color-text-muted);
}

/* ── 筛选栏 ── */
.filter-bar {
  display: flex;
  gap: var(--space-3);
  align-items: flex-end;
  flex-wrap: wrap;
}
.filter-group {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.filter-label {
  font-size: 0.7rem;
  color: var(--color-text-muted);
  letter-spacing: 0.05em;
}
.filter-select, .filter-input {
  padding: 6px 10px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-input);
  color: var(--color-text-primary);
  font-size: 0.85rem;
  min-width: 120px;
}
.filter-select:focus, .filter-input:focus {
  outline: none;
  border-color: var(--color-primary);
}
.search-group .filter-input { min-width: 200px; }
.filter-reset {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 6px 10px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-input);
  color: var(--color-text-muted);
  font-size: 0.8rem;
  cursor: pointer;
  transition: all 0.15s;
}
.filter-reset:hover { color: var(--color-primary); border-color: var(--color-primary); }
.filter-count {
  margin-left: auto;
  font-size: 0.8rem;
  color: var(--color-text-muted);
  align-self: center;
}

/* ── 加载/空态 ── */
.loading-state {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-3);
  padding: var(--space-8);
  color: var(--color-text-muted);
}
.spinner {
  width: 20px;
  height: 20px;
  border: 2px solid var(--color-border);
  border-top-color: var(--color-primary);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* ── 能力卡片网格 ── */
.tools-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: var(--space-3);
}
.tool-card {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: var(--space-3);
  cursor: pointer;
  transition: all 0.15s;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.tool-card:hover {
  border-color: var(--color-primary);
  box-shadow: 0 2px 8px rgba(0,0,0,0.04);
}
.tool-card.unexposed {
  border-style: dashed;
  background: var(--color-bg-input);
}
.tool-card.expanded {
  grid-column: 1 / -1;
  cursor: default;
}
.tool-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 8px;
}
.tool-name {
  font-size: 0.92rem;
  font-weight: 600;
  color: var(--color-text-primary);
  word-break: break-all;
}
.tool-badges {
  display: flex;
  gap: 4px;
  flex-shrink: 0;
}
.badge {
  font-size: 0.7rem;
  padding: 2px 6px;
  border-radius: var(--radius-sm);
  white-space: nowrap;
}
.source-badge {
  border: 1px solid currentColor;
}
.category-badge {
  background: var(--color-bg-input);
  color: var(--color-text-muted);
  border: 1px solid var(--color-border);
}
.tool-desc {
  font-size: 0.82rem;
  color: var(--color-text-muted);
  line-height: 1.5;
  margin: 0;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.tool-card.expanded .tool-desc {
  -webkit-line-clamp: unset;
  overflow: visible;
}
.tool-meta {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.meta-tag {
  font-size: 0.7rem;
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--color-border);
  color: var(--color-text-muted);
}
.tag-exposed { color: var(--color-success, #0ca678); border-color: var(--color-success, #0ca678); }
.tag-unexposed { color: var(--color-warning, #e8590c); border-color: var(--color-warning, #e8590c); }
.tag-enabled { color: var(--color-success, #0ca678); border-color: var(--color-success, #0ca678); }
.tag-disabled { color: var(--color-text-muted); }
.tag-async { color: #1971c2; border-color: #1971c2; }
.cost-none { color: var(--color-success, #0ca678); border-color: var(--color-success, #0ca678); }
.cost-low { color: #0ca678; border-color: #0ca678; }
.cost-high { color: #e8590c; border-color: #e8590c; }

/* ── 工具详情 ── */
.tool-detail {
  margin-top: var(--space-2);
  padding-top: var(--space-2);
  border-top: 1px dashed var(--color-border);
}
.detail-title {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  letter-spacing: 0.05em;
  margin-bottom: 6px;
}
.param-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.param-row {
  display: grid;
  grid-template-columns: 140px 80px 50px 1fr;
  gap: 8px;
  align-items: center;
  font-size: 0.8rem;
  padding: 4px 6px;
  background: var(--color-bg-input);
  border-radius: var(--radius-sm);
}
.param-name { font-weight: 600; color: var(--color-text-primary); }
.param-type { color: #1971c2; }
.param-required {
  font-size: 0.68rem;
  color: var(--color-warning, #e8590c);
  border: 1px solid var(--color-warning, #e8590c);
  border-radius: var(--radius-sm);
  padding: 0 4px;
  text-align: center;
}
.param-desc { color: var(--color-text-muted); }
.empty-params {
  font-size: 0.8rem;
  color: var(--color-text-muted);
  font-style: italic;
}

.integration-block {
  margin-top: var(--space-3);
  padding: var(--space-3);
  background: var(--color-bg-input);
  border-radius: var(--radius-md);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.integration-info {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.info-row {
  display: grid;
  grid-template-columns: 100px 1fr;
  gap: 8px;
  font-size: 0.82rem;
}
.info-label { color: var(--color-text-muted); }
.info-val { color: var(--color-text-primary); }
.info-val.positive { color: var(--color-success, #0ca678); }
.info-val.warning { color: var(--color-warning, #e8590c); }
.guide-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border: 1px solid var(--color-primary);
  border-radius: var(--radius-md);
  background: var(--color-primary-bg);
  color: var(--color-primary);
  font-size: 0.82rem;
  cursor: pointer;
  transition: all 0.15s;
  align-self: flex-start;
}
.guide-btn:hover { background: var(--color-primary); color: #fff; }

/* ── 扩展面板 ── */
.extend-panel {
  padding: 0;
  overflow: hidden;
}
.extend-toggle {
  width: 100%;
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-4);
  background: transparent;
  border: none;
  cursor: pointer;
  color: var(--color-text-primary);
  font-size: 0.9rem;
  text-align: left;
}
.extend-toggle:hover { background: var(--color-bg-input); }
.extend-title { font-weight: 600; }
.extend-count {
  font-size: 0.78rem;
  color: var(--color-text-muted);
  margin-left: 8px;
}
.extend-split {
  margin-left: auto;
  display: flex;
  gap: var(--space-3);
  font-size: 0.75rem;
}
.extend-split .low { color: #0ca678; }
.extend-split .high { color: #e8590c; }

.extend-body {
  padding: 0 var(--space-4) var(--space-4);
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  border-top: 1px dashed var(--color-border);
}
.extend-group, .future-section {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding-top: var(--space-3);
}
.group-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.88rem;
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0;
}
.group-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}
.group-count {
  font-size: 0.72rem;
  color: var(--color-text-muted);
  font-weight: 400;
}
.extend-items {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: var(--space-2);
}
.extend-item {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  padding: var(--space-2) var(--space-3);
  background: var(--color-bg-input);
}
.ext-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}
.ext-name {
  font-size: 0.82rem;
  font-weight: 600;
  color: var(--color-text-primary);
}
.ext-cost {
  font-size: 0.68rem;
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  border: 1px solid currentColor;
}
.ext-cost.cost-low { color: #0ca678; }
.ext-cost.cost-high { color: #e8590c; }
.ext-desc {
  font-size: 0.76rem;
  color: var(--color-text-muted);
  margin: 4px 0;
  line-height: 1.4;
}
.ext-skill {
  font-size: 0.7rem;
  color: var(--color-text-muted);
  background: var(--color-bg-card);
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--color-border);
}

.future-list {
  margin: 0;
  padding-left: var(--space-4);
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.future-list li {
  font-size: 0.8rem;
  color: var(--color-text-muted);
  line-height: 1.5;
}
.future-list strong {
  color: var(--color-text-primary);
  font-weight: 600;
}

/* ── 接入指引弹窗 ── */
.guide-modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  padding: var(--space-4);
}
.guide-modal {
  background: var(--color-bg-card);
  border-radius: var(--radius-lg);
  width: min(820px, 100%);
  max-height: 85vh;
  display: flex;
  flex-direction: column;
  border: 1px solid var(--color-border);
}
.guide-modal-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  padding: var(--space-4);
  border-bottom: 1px solid var(--color-border);
}
.guide-modal-title {
  font-size: 1rem;
  font-weight: 600;
  margin: 0 0 6px;
  color: var(--color-text-primary);
}
.guide-modal-meta {
  display: flex;
  gap: 6px;
  align-items: center;
  flex-wrap: wrap;
}
.tool-name-preview {
  font-size: 0.78rem;
  color: var(--color-text-muted);
}
.guide-close {
  border: none;
  background: transparent;
  cursor: pointer;
  color: var(--color-text-muted);
  padding: 4px;
  border-radius: var(--radius-sm);
}
.guide-close:hover { background: var(--color-bg-input); color: var(--color-text-primary); }
.guide-modal-body {
  flex: 1;
  overflow: auto;
  padding: var(--space-4);
}
.guide-code {
  margin: 0;
  font-size: 0.78rem;
  line-height: 1.6;
  color: var(--color-text-primary);
  white-space: pre-wrap;
  word-break: break-word;
  font-family: var(--font-mono, 'JetBrains Mono', monospace);
}
.guide-modal-foot {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: var(--space-3) var(--space-4);
  border-top: 1px solid var(--color-border);
  gap: var(--space-3);
}
.foot-hint {
  font-size: 0.78rem;
  color: var(--color-text-muted);
}
.foot-actions {
  display: flex;
  gap: var(--space-2);
}
.btn-secondary, .btn-primary {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 6px 14px;
  border-radius: var(--radius-md);
  font-size: 0.82rem;
  cursor: pointer;
  border: 1px solid var(--color-border);
  transition: all 0.15s;
}
.btn-secondary {
  background: var(--color-bg-input);
  color: var(--color-text-primary);
}
.btn-secondary:hover { border-color: var(--color-text-muted); }
.btn-primary {
  background: var(--color-primary);
  color: #fff;
  border-color: var(--color-primary);
}
.btn-primary:hover { opacity: 0.9; }
.btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }

.fade-enter-active, .fade-leave-active { transition: opacity 0.2s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }

@media (max-width: 640px) {
  .cap-page { padding: var(--space-3); }
  .source-cards { grid-template-columns: 1fr 1fr; }
  .tools-grid { grid-template-columns: 1fr; }
  .filter-bar { flex-direction: column; align-items: stretch; }
  .filter-count { margin-left: 0; }
  .param-row { grid-template-columns: 1fr; gap: 2px; }
  .guide-modal { max-height: 92vh; }
}

/* ═══════════════════ Tab 布局 ═══════════════════ */
.cap-tabs {
  display: flex;
  gap: 4px;
  border-bottom: 1px solid var(--color-border);
  padding: 0 var(--space-1);
}
.cap-tab {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 16px;
  border: none;
  background: transparent;
  color: var(--color-text-muted);
  font-size: 0.85rem;
  cursor: pointer;
  border-bottom: 2px solid transparent;
  transition: all 0.15s;
}
.cap-tab:hover { color: var(--color-text-primary); }
.cap-tab.active {
  color: var(--color-primary);
  border-bottom-color: var(--color-primary);
  font-weight: 600;
}
.tab-pane {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

/* ── 调用统计 ── */
.stats-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3);
}
.days-group {
  display: inline-flex;
  gap: 4px;
  background: var(--color-bg-input);
  border-radius: var(--radius-md);
  padding: 3px;
}
.days-btn {
  padding: 4px 12px;
  border: none;
  background: transparent;
  color: var(--color-text-muted);
  font-size: 0.8rem;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all 0.15s;
}
.days-btn:hover { color: var(--color-text-primary); }
.days-btn.active {
  background: var(--color-primary);
  color: #fff;
}
.refresh-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 6px 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-input);
  color: var(--color-text-primary);
  font-size: 0.8rem;
  cursor: pointer;
  transition: all 0.15s;
}
.refresh-btn:hover:not(:disabled) {
  border-color: var(--color-primary);
  color: var(--color-primary);
}
.refresh-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.stats-overview {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: var(--space-3);
  padding: var(--space-4);
}
.stat-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  align-items: flex-start;
}
.stat-num {
  font-size: 1.5rem;
  font-weight: 600;
  color: var(--color-text-primary);
  line-height: 1.1;
}
.stat-num.positive { color: var(--color-success, #0ca678); }
.stat-num.warning { color: var(--color-danger, #e8590c); }
.stat-label {
  font-size: 0.72rem;
  color: var(--color-text-muted);
  letter-spacing: 0.05em;
}

.block-title {
  font-size: 0.9rem;
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0 0 var(--space-3);
}
.sub-title {
  font-size: 0.78rem;
  color: var(--color-text-muted);
  letter-spacing: 0.04em;
  margin-bottom: 6px;
}

.stats-chart { padding: var(--space-4); }
.day-chart {
  width: 100%;
  height: 200px;
  display: block;
}
.day-bar-group { cursor: pointer; }
.day-bar-group:hover .bar-success { opacity: 0.85; }
.chart-legend {
  display: flex;
  gap: var(--space-4);
  margin-top: var(--space-2);
  font-size: 0.75rem;
  color: var(--color-text-muted);
}
.dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 2px;
  margin-right: 4px;
  vertical-align: middle;
}
.dot-primary { background: var(--color-primary); }
.dot-danger { background: var(--color-danger, #e8590c); }

.stats-table-wrap { padding: var(--space-4); }
.stats-table-scroll { overflow-x: auto; }
.stats-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.82rem;
}
.stats-table th {
  text-align: left;
  padding: 8px 10px;
  color: var(--color-text-muted);
  font-size: 0.75rem;
  font-weight: 500;
  letter-spacing: 0.04em;
  border-bottom: 1px solid var(--color-border);
  white-space: nowrap;
}
.stats-table td {
  padding: 8px 10px;
  border-bottom: 1px solid var(--color-border);
  white-space: nowrap;
}
.tool-row { cursor: pointer; transition: background 0.12s; }
.tool-row:hover { background: var(--color-bg-input); }
.tool-row.warn { background: rgba(230, 57, 70, 0.06); }
.tool-row.warn:hover { background: rgba(230, 57, 70, 0.1); }
.warn-text { color: var(--color-danger, #e8590c); font-weight: 600; }
.tool-expand-row td { background: var(--color-bg-input); }
.expand-detail {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-4);
  padding: var(--space-3);
}
.err-cats-block, .recent-records {
  display: flex;
  flex-direction: column;
}
.err-cat-row {
  display: grid;
  grid-template-columns: 120px 1fr 40px;
  gap: 8px;
  align-items: center;
  margin-bottom: 4px;
}
.err-cat-label { font-size: 0.78rem; color: var(--color-text-primary); }
.err-cat-bar, .err-cat-track {
  height: 12px;
  background: var(--color-bg-card);
  border-radius: var(--radius-sm);
  overflow: hidden;
  border: 1px solid var(--color-border);
}
.err-cat-fill {
  height: 100%;
  background: var(--color-primary);
  transition: width 0.3s;
}
.err-cat-num {
  font-size: 0.78rem;
  color: var(--color-text-muted);
  text-align: right;
}
.mini-loading {
  font-size: 0.78rem;
  color: var(--color-text-muted);
  font-style: italic;
}
.recent-list { display: flex; flex-direction: column; gap: 4px; }
.recent-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 0.76rem;
  padding: 3px 6px;
  background: var(--color-bg-card);
  border-radius: var(--radius-sm);
}
.recent-status { font-weight: 600; }
.recent-status.ok { color: var(--color-success, #0ca678); }
.recent-status.fail { color: var(--color-danger, #e8590c); }
.recent-time { color: var(--color-text-muted); }
.recent-dur { color: var(--color-text-primary); }
.recent-err {
  color: var(--color-danger, #e8590c);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}

.stats-error-cats { padding: var(--space-4); }
.err-cat-chart { display: flex; flex-direction: column; gap: 8px; }
.err-cat-bar-row {
  display: grid;
  grid-template-columns: 140px 1fr 50px;
  gap: 10px;
  align-items: center;
}

/* ── 调试台 ── */
.debug-section {
  padding: var(--space-4);
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}
.debug-block { display: flex; flex-direction: column; gap: 8px; }
.debug-tool-select {
  padding: 8px 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-input);
  color: var(--color-text-primary);
  font-size: 0.85rem;
  max-width: 400px;
}
.debug-tool-select:focus {
  outline: none;
  border-color: var(--color-primary);
}
.debug-tool-desc {
  font-size: 0.8rem;
  color: var(--color-text-muted);
  margin: 0;
  line-height: 1.5;
}
.param-form {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: var(--space-3);
}
.param-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: var(--space-2) var(--space-3);
  background: var(--color-bg-input);
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border);
}
.param-field-label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.82rem;
}
.param-field-name { font-weight: 600; color: var(--color-text-primary); }
.req-star { color: var(--color-danger, #e8590c); font-weight: 700; }
.param-field-type { color: #1971c2; font-size: 0.72rem; }
.param-field-desc {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  margin: 0;
  line-height: 1.4;
}
.param-input {
  padding: 6px 10px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-bg-card);
  color: var(--color-text-primary);
  font-size: 0.82rem;
}
.param-input:focus {
  outline: none;
  border-color: var(--color-primary);
}
.param-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 0.8rem;
  color: var(--color-text-primary);
  cursor: pointer;
}
.param-toggle input { cursor: pointer; }

.run-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 20px;
  border: none;
  border-radius: var(--radius-md);
  background: var(--color-primary);
  color: #fff;
  font-size: 0.85rem;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s;
  align-self: flex-start;
}
.run-btn:hover:not(:disabled) { opacity: 0.9; }
.run-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.debug-info-bar {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
  padding: var(--space-2) var(--space-3);
  background: var(--color-bg-input);
  border-radius: var(--radius-md);
  margin-bottom: 8px;
}
.info-chip {
  font-size: 0.76rem;
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  color: var(--color-text-primary);
}
.info-chip.chip-ok { color: var(--color-success, #0ca678); border-color: var(--color-success, #0ca678); }
.info-chip.chip-fail { color: var(--color-danger, #e8590c); border-color: var(--color-danger, #e8590c); }
.info-actions {
  margin-left: auto;
  display: flex;
  gap: 6px;
}
.mini-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-bg-card);
  color: var(--color-text-muted);
  font-size: 0.74rem;
  cursor: pointer;
  transition: all 0.12s;
}
.mini-btn:hover { color: var(--color-primary); border-color: var(--color-primary); }
.debug-error-box {
  padding: var(--space-2) var(--space-3);
  background: rgba(230, 57, 70, 0.08);
  border: 1px solid var(--color-danger, #e8590c);
  border-radius: var(--radius-md);
  font-size: 0.8rem;
  color: var(--color-danger, #e8590c);
  margin-bottom: 8px;
  word-break: break-word;
}
.err-label { font-weight: 600; }
.debug-result-pre {
  margin: 0;
  padding: var(--space-3);
  background: var(--color-bg-input);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  font-size: 0.78rem;
  line-height: 1.55;
  color: var(--color-text-primary);
  overflow: auto;
  max-height: 420px;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: var(--font-mono, 'JetBrains Mono', monospace);
}
.debug-result-pre :deep(.j-key) { color: #1971c2; }
.debug-result-pre :deep(.j-str) { color: var(--color-success, #0ca678); }
.debug-result-pre :deep(.j-num) { color: #e8590c; }
.debug-result-pre :deep(.j-bool) { color: #9c36b5; }

.debug-history { padding: var(--space-4); }
.history-list { display: flex; flex-direction: column; gap: 4px; }
.history-item {
  display: grid;
  grid-template-columns: 150px 160px 1fr 70px 30px;
  gap: 10px;
  align-items: center;
  padding: 6px 10px;
  background: var(--color-bg-input);
  border-radius: var(--radius-sm);
  font-size: 0.76rem;
  cursor: pointer;
  transition: all 0.12s;
  border: 1px solid transparent;
}
.history-item:hover { border-color: var(--color-primary); }
.hist-time { color: var(--color-text-muted); }
.hist-tool { color: var(--color-text-primary); font-weight: 600; }
.hist-args {
  color: var(--color-text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.hist-dur { color: var(--color-text-primary); }
.hist-status { font-weight: 700; text-align: center; }
.hist-status.ok { color: var(--color-success, #0ca678); }
.hist-status.fail { color: var(--color-danger, #e8590c); }

@media (max-width: 640px) {
  .stats-overview { grid-template-columns: 1fr 1fr; }
  .expand-detail { grid-template-columns: 1fr; }
  .stats-toolbar { flex-direction: column; align-items: stretch; }
  .history-item { grid-template-columns: 1fr; gap: 2px; }
  .param-form { grid-template-columns: 1fr; }
  .info-actions { margin-left: 0; width: 100%; }
}
</style>
