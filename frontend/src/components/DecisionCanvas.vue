<script setup>
import { ref, computed, onMounted } from 'vue'
import { getDecisionCanvas } from '../api'

const emit = defineEmits(['navigate'])

const loading = ref(false)
const error = ref(null)
const data = ref(null)

// ── 数据加载 ──

async function loadCanvas() {
  loading.value = true
  error.value = null
  try {
    const { data: res } = await getDecisionCanvas()
    data.value = res
  } catch (e) {
    error.value = e.response?.data?.detail || e.message || '加载决策画布失败'
    data.value = { consensus: [], conflicts: [], actionable: [], learning: null }
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadCanvas()
})

// ── 计算属性 ──

const consensus = computed(() => data.value?.consensus || [])
const conflicts = computed(() => data.value?.conflicts || [])
const actionable = computed(() => data.value?.actionable || [])
const learning = computed(() => data.value?.learning)
const summary = computed(() => data.value?.summary)

// ── 条件判断框架 (从最近一次仲裁结果中提取) ──
const conditionFramework = computed(() => {
  // 从 conflicts 中提取 condition_framework 数据
  // 后端会在 specialist_results 的仲裁结果中返回 condition_framework
  const cf = data.value?.condition_framework
  if (Array.isArray(cf) && cf.length > 0) return cf
  // 也可能嵌套在 specialist_results 中
  const specialists = data.value?.specialist_results || []
  for (const sr of specialists) {
    if (sr.is_arbitration && Array.isArray(sr.condition_framework) && sr.condition_framework.length > 0) {
      return sr.condition_framework
    }
  }
  return []
})
const diverggenceAnalysis = computed(() => {
  const da = data.value?.diverggence_analysis
  if (da) return da
  const specialists = data.value?.specialist_results || []
  for (const sr of specialists) {
    if (sr.is_arbitration && sr.diverggence_analysis) return sr.diverggence_analysis
  }
  return ''
})
const keyVariables = computed(() => {
  const kv = data.value?.key_variables
  if (kv) return kv
  const specialists = data.value?.specialist_results || []
  for (const sr of specialists) {
    if (sr.is_arbitration && sr.key_variables) return sr.key_variables
  }
  return ''
})
const hasConditionFramework = computed(() => conditionFramework.value.length > 0)

const hasConsensus = computed(() => consensus.value.length > 0)
const hasConflicts = computed(() => conflicts.value.length > 0)
const hasActionable = computed(() => actionable.value.length > 0)
const isEmpty = computed(() => !hasConsensus.value && !hasConflicts.value && !hasActionable.value)

// ── 优先级映射 ──

function priorityLabel(index, item) {
  if (item.urgent) return { text: '紧急', cls: 'priority-urgent' }
  if (item.confidence >= 0.8) return { text: '高置信', cls: 'priority-high' }
  if (item.confidence >= 0.6) return { text: '中置信', cls: 'priority-mid' }
  return { text: '低置信', cls: 'priority-low' }
}

function confidencePct(c) {
  return `${Math.round((c || 0) * 100)}%`
}

function confidenceClass(conf) {
  // conf is a string like "75%" or a number
  const num = typeof conf === 'string' ? parseInt(conf) : conf
  if (num >= 80) return 'conf-high'
  if (num >= 60) return 'conf-mid'
  return 'conf-low'
}
</script>

<template>
  <div class="decision-canvas bg-mesh">
    <!-- Header -->
    <div class="canvas-header">
      <div class="canvas-title-row">
        <h2 class="canvas-title editorial-title-lg">决策画布</h2>
        <span v-if="summary" class="canvas-time terminal-label">{{ summary.generated_at }}</span>
      </div>
      <p v-if="summary" class="canvas-meta">
        <span class="font-jet">{{ summary.total }}</span> 条分析结论 · <span class="font-jet">{{ summary.consensus_count }}</span> 共识 · <span class="font-jet">{{ summary.conflict_count }}</span> 分歧 · <span class="font-jet">{{ summary.actionable_count }}</span> 可执行
      </p>
    </div>

    <!-- Loading -->
    <div v-if="loading" class="canvas-loading card editorial-card">
      <div class="skeleton skeleton-title"></div>
      <div class="skeleton skeleton-line"></div>
      <div class="skeleton skeleton-line short"></div>
    </div>

    <!-- Error -->
    <div v-else-if="error" class="canvas-error card editorial-card">
      <p>{{ error }}</p>
      <button class="btn-secondary" @click="loadCanvas">重试</button>
    </div>

    <!-- Empty state -->
    <div v-else-if="isEmpty" class="canvas-empty card editorial-card">
      <div class="empty-icon terminal-label">NO DATA</div>
      <h3 class="editorial-title">暂无决策画布数据</h3>
      <p>当各分析模块（日报、全景诊断、AI 对话等）运行后，<br>共识区与关注区会自动填充。</p>
      <button class="btn-secondary" @click="loadCanvas">
        刷新
      </button>
    </div>

    <template v-else>
      <!-- ══════════════════════════ 共识区 — 绿色 ══════════════════════════ -->
      <div v-if="hasConsensus" class="canvas-zone zone-consensus card editorial-card">
        <div class="zone-header editorial-card-header">
          <h3 class="zone-title title">共识区 — 多方验证，可信度高</h3>
          <span class="zone-count meta">{{ consensus.length }} 项</span>
        </div>
        <div class="zone-body">
          <div v-for="(item, idx) in consensus" :key="'c-' + idx" class="zone-item consensus-item reveal-stagger">
            <div class="item-main">
              <span class="item-badge" :class="item.direction === 'bullish' ? 'badge-bullish' : 'badge-bearish'">
                {{ item.direction_label }}
              </span>
              <div class="item-content">
                <p class="item-summary">{{ item.summary }}</p>
                <p v-if="item.reasoning" class="item-reasoning">{{ item.reasoning }}</p>
              </div>
              <span class="item-confidence font-jet">{{ confidencePct(item.confidence) }}</span>
            </div>
            <div class="item-sources">
              <span
                v-for="(cf, ci) in item.confirmations"
                :key="ci"
                class="source-tag confirmed"
              >
                {{ cf.source_label }} ✓
              </span>
              <span class="source-tag source-primary">
                {{ item.action_label }}
              </span>
            </div>
          </div>
        </div>
      </div>

      <!-- ══════════════════════════ 关注区 — 黄色 ══════════════════════════ -->
      <div v-if="hasConflicts" class="canvas-zone zone-conflicts card editorial-card">
        <div class="zone-header editorial-card-header">
          <h3 class="zone-title title">关注区 — 存在差异，需权衡</h3>
          <span class="zone-count meta">{{ conflicts.length }} 组冲突</span>
        </div>
        <div class="zone-body">
          <div v-for="(item, idx) in conflicts" :key="'x-' + idx" class="zone-item conflict-item reveal-stagger">
            <div class="conflict-target">
              <span class="conflict-tag terminal-label">DIVERGENCE</span>
              <strong>{{ item.target_subject }}</strong>
            </div>

            <!-- 双方观点 -->
            <div class="conflict-views">
              <!-- 看多方 -->
              <div class="conflict-view view-bullish">
                <div class="view-badge badge-bullish">
                  {{ item.bullish_view.action_label }}
                </div>
                <div class="view-body">
                  <span class="view-source">【{{ item.bullish_view.source_label }}说】</span>
                  {{ item.bullish_view.summary }}
                </div>
                <div class="view-confidence font-jet">{{ confidencePct(item.bullish_view.confidence) }}</div>
              </div>

              <!-- 看空方 -->
              <div class="conflict-view view-bearish">
                <div class="view-badge badge-bearish">
                  {{ item.bearish_view.action_label }}
                </div>
                <div class="view-body">
                  <span class="view-source">【{{ item.bearish_view.source_label }}说】</span>
                  {{ item.bearish_view.summary }}
                </div>
                <div class="view-confidence font-jet">{{ confidencePct(item.bearish_view.confidence) }}</div>
              </div>
            </div>

            <!-- 关键变量 -->
            <div v-if="item.conditional_advice" class="conflict-variables">
              <p class="conflict-advice">{{ item.conditional_advice.advice }}</p>
              <div class="conflict-paths">
                <span class="path path-a">→ {{ item.conditional_advice.path_a }}</span>
                <span class="path path-b">→ {{ item.conditional_advice.path_b }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- ══════════════════════════ 条件判断框架区 — 橙色 ══════════════════════════ -->
      <div v-if="hasConditionFramework" class="canvas-zone zone-condition card editorial-card">
        <div class="zone-header editorial-card-header">
          <h3 class="zone-title title">条件判断框架</h3>
          <span class="zone-count meta">{{ conditionFramework.length }} 种情景</span>
        </div>
        <div class="zone-body">
          <!-- 分歧根源 -->
          <div v-if="diverggenceAnalysis" class="condition-divergence">
            <div class="divergence-label terminal-label">分歧根源</div>
            <p class="divergence-text">{{ diverggenceAnalysis }}</p>
          </div>

          <!-- 条件判断表格 -->
          <div class="condition-table-wrap">
            <table class="condition-table">
              <thead>
                <tr>
                  <th>你的判断</th>
                  <th>对应行动</th>
                  <th class="th-confidence">置信度</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(row, idx) in conditionFramework" :key="'cf-' + idx">
                  <td class="td-condition">{{ row.condition }}</td>
                  <td class="td-action">{{ row.action }}</td>
                  <td class="td-confidence">
                    <span class="confidence-badge font-jet" :class="confidenceClass(row.confidence)">
                      {{ row.confidence }}
                    </span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <!-- 核心权衡 -->
          <div v-if="keyVariables" class="condition-tradeoff">
            <div class="tradeoff-label terminal-label">核心权衡</div>
            <p class="tradeoff-text">最终判断取决于你更看重哪个变量：{{ keyVariables }}</p>
          </div>
        </div>
      </div>

      <!-- ══════════════════════════ 建议区 — 蓝色 ══════════════════════════ -->
      <div v-if="hasActionable" class="canvas-zone zone-actionable card editorial-card">
        <div class="zone-header editorial-card-header">
          <h3 class="zone-title title">建议区 — 带条件的行动清单</h3>
          <span class="zone-count meta">{{ actionable.length }} 项行动</span>
        </div>
        <div class="zone-body">
          <div v-for="(item, idx) in actionable" :key="'a-' + idx" class="zone-item actionable-item reveal-stagger">
            <div class="actionable-row">
              <span :class="['action-priority', priorityLabel(idx, item).cls]">
                {{ priorityLabel(idx, item).text }}
              </span>
              <div class="actionable-main">
                <div class="actionable-header">
                  <strong>{{ item.action_label }}:</strong>
                  <span class="actionable-target">{{ item.target_subject }}</span>
                  <span class="source-tag">{{ item.source_label }}</span>
                </div>
                <p class="actionable-reason">
                  {{ item.summary }}
                </p>
              </div>
              <div class="actionable-meta">
                <span class="meta-conf">置信度 <span class="font-jet">{{ confidencePct(item.confidence) }}</span></span>
                <span class="meta-time terminal-label">{{ item.time_window || '24h' }}</span>
                <span v-if="item.condition_trigger" class="meta-trigger">
                  {{ item.condition_trigger }}
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- ══════════════════════════ 学习区 — 紫色 ══════════════════════════ -->
      <div v-if="learning" class="canvas-zone zone-learning card editorial-card">
        <div class="zone-header editorial-card-header">
          <h3 class="zone-title title">今日学到的框架</h3>
        </div>
        <div class="zone-body">
          <p class="learning-text">
            {{ learning.framework }}
          </p>
          <div v-if="learning.key_variables_seen?.length" class="learning-vars">
            <span class="learning-label terminal-label">核心变量</span>
            <span
              v-for="(v, vi) in learning.key_variables_seen"
              :key="vi"
              class="variable-tag font-jet"
            >
              {{ v }}
            </span>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.decision-canvas {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
  max-width: 900px;
}

/* ── Header ── */
.canvas-header {
  margin-bottom: 0.25rem;
}
.canvas-title-row {
  display: flex;
  align-items: baseline;
  gap: 0.75rem;
}
.canvas-title {
  margin: 0;
  font-size: inherit;
  font-weight: inherit;
  color: var(--color-text-primary);
}
.canvas-time {
  color: var(--color-text-muted);
  font-size: inherit;
}
.canvas-meta {
  margin: 0.25rem 0 0;
  font-size: 0.82rem;
  color: var(--color-text-secondary);
}

/* ── Loading / Error / Empty ── */
.canvas-loading { padding: 2rem; }
.canvas-error {
  padding: 2rem;
  text-align: center;
  color: var(--color-text-secondary);
}
.canvas-empty {
  padding: 3rem 2rem;
  text-align: center;
  color: var(--color-text-secondary);
}
.empty-icon { margin-bottom: 0.75rem; }
.canvas-empty h3 { color: var(--color-text-primary); margin-bottom: 0.5rem; }

/* ── Zone 通用 ── */
.canvas-zone {
  overflow: hidden;
  transition: all var(--transition-normal);
}
.canvas-zone:hover {
  box-shadow: var(--shadow-md);
}
.zone-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.85rem 1.25rem;
  border-bottom: 1px solid var(--color-border-light);
}
.zone-icon { font-size: 1.1rem; }
.zone-title {
  margin: 0;
  font-size: inherit;
  font-weight: inherit;
  flex: 1;
}
.zone-count {
  color: var(--color-text-muted);
  font-size: inherit;
  font-weight: 500;
}
.zone-body {
  padding: 0.75rem 1.25rem;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

/* ── 共识区（绿色） ── */
.zone-consensus {
  background: linear-gradient(135deg, #f0fdf4 0%, #ffffff 100%);
}
.zone-consensus .zone-header {
  background: rgba(5, 150, 105, 0.06);
}
.zone-consensus .zone-title { color: #065f46; }

/* ── 关注区（黄色） ── */
.zone-conflicts {
  background: linear-gradient(135deg, #fffbeb 0%, #ffffff 100%);
}
.zone-conflicts .zone-header {
  background: rgba(217, 119, 6, 0.06);
}
.zone-conflicts .zone-title { color: #92400e; }

/* ── 建议区（蓝色） ── */
.zone-actionable {
  background: linear-gradient(135deg, #eff6ff 0%, #ffffff 100%);
}
.zone-actionable .zone-header {
  background: rgba(37, 99, 235, 0.06);
}
.zone-actionable .zone-title { color: #1e40af; }

/* ── 条件判断框架区（橙色） ── */
.zone-condition {
  background: linear-gradient(135deg, #fff7ed 0%, #ffffff 100%);
}
.zone-condition .zone-header {
  background: rgba(234, 88, 12, 0.06);
}
.zone-condition .zone-title { color: #9a3412; }

.condition-divergence {
  padding: 0.75rem 1rem;
  background: #fff7ed;
  border: 1px solid #fed7aa;
  border-radius: var(--radius-sm);
}
.divergence-label {
  font-size: 0.8rem;
  font-weight: 600;
  color: #9a3412;
  margin-bottom: 0.35rem;
}
.divergence-text {
  margin: 0;
  font-size: 0.85rem;
  line-height: 1.6;
  color: var(--color-text-primary);
}

.condition-table-wrap {
  overflow-x: auto;
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-sm);
}
.condition-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.85rem;
}
.condition-table th {
  padding: 0.6rem 0.85rem;
  text-align: left;
  font-weight: 600;
  font-size: 0.78rem;
  color: var(--color-text-muted);
  background: var(--color-bg-secondary);
  border-bottom: 1px solid var(--color-border-light);
}
.condition-table th.th-confidence {
  text-align: center;
  width: 100px;
}
.condition-table td {
  padding: 0.6rem 0.85rem;
  border-bottom: 1px solid var(--color-border-light);
  color: var(--color-text-primary);
  line-height: 1.5;
}
.condition-table tr:last-child td {
  border-bottom: none;
}
.td-condition {
  font-weight: 500;
}
.td-action {
  color: var(--color-text-secondary);
}
.td-confidence {
  text-align: center;
}
.confidence-badge {
  display: inline-block;
  font-size: 0.75rem;
  padding: 2px 10px;
  border-radius: 10px;
  font-weight: 600;
}
.conf-high {
  background: #ecfdf5;
  color: #047857;
  border: 1px solid #a7f3d0;
}
.conf-mid {
  background: #fffbeb;
  color: #92400e;
  border: 1px solid #fde68a;
}
.conf-low {
  background: var(--color-bg-secondary);
  color: var(--color-text-muted);
  border: 1px solid var(--color-border-light);
}

.condition-tradeoff {
  padding: 0.75rem 1rem;
  background: #fffbeb;
  border: 1px solid #fde68a;
  border-radius: var(--radius-sm);
}
.tradeoff-label {
  font-size: 0.8rem;
  font-weight: 600;
  color: #92400e;
  margin-bottom: 0.35rem;
}
.tradeoff-text {
  margin: 0;
  font-size: 0.85rem;
  line-height: 1.6;
  color: var(--color-text-primary);
  font-style: italic;
}

/* ── 学习区（紫色） ── */
.zone-learning {
  background: linear-gradient(135deg, #f5f3ff 0%, #ffffff 100%);
}
.zone-learning .zone-header {
  background: rgba(124, 58, 237, 0.06);
}
.zone-learning .zone-title { color: #5b21b6; }

/* ── 通用 Item ── */
.zone-item {
  padding: 0.75rem 1rem;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  transition: all var(--transition-fast);
}
.zone-item:hover {
  border-color: var(--color-border-strong);
}

/* ── 共识 Item ── */
.item-main {
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
}
.item-badge {
  font-size: 0.7rem;
  padding: 2px 8px;
  border-radius: 10px;
  font-weight: 600;
  white-space: nowrap;
  margin-top: 2px;
}
.badge-bullish { background: #fef2f2; color: #b91c1c; }
.badge-bearish { background: #ecfdf5; color: #047857; }
.item-content { flex: 1; min-width: 0; }
.item-summary {
  margin: 0;
  font-size: 0.88rem;
  color: var(--color-text-primary);
  line-height: 1.5;
}
.item-reasoning {
  margin: 0.25rem 0 0;
  font-size: 0.8rem;
  color: var(--color-text-muted);
  line-height: 1.4;
}
.item-confidence {
  font-size: 0.78rem;
  color: var(--color-text-muted);
  font-weight: 500;
  white-space: nowrap;
}
.item-sources {
  margin-top: 0.5rem;
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
}

/* ── Source Tags ── */
.source-tag {
  font-size: 0.72rem;
  padding: 2px 8px;
  border-radius: 10px;
  font-weight: 500;
  background: var(--color-bg-secondary);
  color: var(--color-text-secondary);
  border: 1px solid var(--color-border-light);
}
.source-tag.confirmed {
  background: #f0fdf4;
  color: #065f46;
  border-color: #a7f3d0;
}
.source-tag.source-primary {
  background: var(--color-primary-50);
  color: var(--color-primary-700);
  border-color: var(--color-primary-200);
}

/* ── 冲突 Item ── */
.conflict-target {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  margin-bottom: 0.5rem;
  font-size: 0.92rem;
  color: var(--color-text-primary);
}
.conflict-tag { font-size: 1rem; }
.conflict-views {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}
.conflict-view {
  padding: 0.6rem 0.75rem;
  border-radius: var(--radius-sm);
  font-size: 0.82rem;
}
.view-bullish { background: #fef2f2; border: 1px solid #fecaca; }
.view-bearish { background: #ecfdf5; border: 1px solid #a7f3d0; }
.view-badge {
  font-size: 0.68rem;
  padding: 1px 7px;
  border-radius: 8px;
  font-weight: 600;
  display: inline-block;
  margin-bottom: 0.35rem;
}
.view-source { font-weight: 600; color: var(--color-text-primary); }
.view-body { line-height: 1.45; color: var(--color-text-secondary); }
.view-confidence {
  margin-top: 0.3rem;
  font-size: 0.72rem;
  color: var(--color-text-muted);
}
.conflict-variables {
  background: #fffbeb;
  border: 1px solid #fde68a;
  border-radius: var(--radius-sm);
  padding: 0.65rem 0.85rem;
}
.conflict-advice {
  margin: 0;
  font-size: 0.83rem;
  font-weight: 600;
  color: #92400e;
  line-height: 1.5;
}
.conflict-paths {
  margin-top: 0.4rem;
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}
.path {
  font-size: 0.78rem;
  color: var(--color-text-secondary);
}

/* ── 可行动 Item ── */
.actionable-row {
  display: flex;
  align-items: flex-start;
  gap: 0.65rem;
}
.action-priority {
  font-size: 0.72rem;
  padding: 2px 8px;
  border-radius: 10px;
  font-weight: 600;
  white-space: nowrap;
  margin-top: 1px;
}
.priority-urgent { background: #fef2f2; color: #b91c1c; border: 1px solid #fecaca; }
.priority-high { background: #ecfdf5; color: #047857; border: 1px solid #a7f3d0; }
.priority-mid { background: #fffbeb; color: #92400e; border: 1px solid #fde68a; }
.priority-low { background: var(--color-bg-secondary); color: var(--color-text-muted); }
.actionable-main { flex: 1; min-width: 0; }
.actionable-header {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  flex-wrap: wrap;
  margin-bottom: 0.25rem;
  font-size: 0.88rem;
}
.actionable-target {
  color: var(--color-primary-700);
  font-weight: 500;
}
.actionable-reason {
  margin: 0;
  font-size: 0.83rem;
  color: var(--color-text-secondary);
  line-height: 1.45;
}
.actionable-meta {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  font-size: 0.72rem;
  color: var(--color-text-muted);
  white-space: nowrap;
  text-align: right;
}
.meta-conf { font-weight: 500; }
.meta-trigger { color: var(--color-warning); font-weight: 500; }

/* ── 学习区 ── */
.learning-text {
  margin: 0;
  font-size: 0.9rem;
  line-height: 1.7;
  color: var(--color-text-primary);
  font-style: italic;
}
.learning-vars {
  margin-top: 0.65rem;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.35rem;
}
.learning-label {
  font-size: 0.78rem;
  color: var(--color-text-muted);
  font-weight: 500;
}
.variable-tag {
  font-size: 0.72rem;
  padding: 2px 10px;
  border-radius: 10px;
  background: rgba(124, 58, 237, 0.08);
  color: #6d28d9;
  border: 1px solid rgba(124, 58, 237, 0.2);
}

/* ── Skeleton ── */
.skeleton {
  background: linear-gradient(90deg, var(--color-bg-secondary) 25%, var(--color-border) 50%, var(--color-bg-secondary) 75%);
  background-size: 200% 100%;
  animation: shimmer 1.5s infinite;
  border-radius: var(--radius-sm);
}
.skeleton-title { height: 1.5rem; width: 60%; margin-bottom: 0.75rem; }
.skeleton-line { height: 0.85rem; margin-bottom: 0.5rem; }
.skeleton-line.short { width: 40%; }

@keyframes shimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}

/* ── Button ── */
.btn-secondary {
  padding: 0.5rem 1.25rem;
  font-size: 0.88rem;
  font-weight: 600;
  color: var(--color-primary);
  background: var(--color-primary-50);
  border: 1px solid var(--color-primary-200);
  border-radius: var(--radius-lg);
  cursor: pointer;
  transition: all 0.2s ease;
}
.btn-secondary:hover {
  background: var(--color-primary-100);
}

/* ── Responsive ── */
@media (max-width: 640px) {
  .conflict-views {
    grid-template-columns: 1fr;
  }
  .actionable-row {
    flex-direction: column;
    gap: 0.35rem;
  }
  .actionable-meta {
    flex-direction: row;
    gap: 0.5rem;
    text-align: left;
  }
  .zone-header {
    padding: 0.65rem 1rem;
  }
  .zone-body {
    padding: 0.5rem 1rem;
  }
  .condition-table th,
  .condition-table td {
    padding: 0.4rem 0.5rem;
    font-size: 0.78rem;
  }
}
</style>
