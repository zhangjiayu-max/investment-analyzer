<script setup>
import { ref, watch, onMounted, computed } from 'vue'
import { listPanoramaRecords, listDeepDiveRecords, listTradeReviewRecords, getPortfolioAiAnalysisRecord } from '../api'

const props = defineProps({
  recordType: { type: String, default: 'panorama' },
})

const emit = defineEmits(['back'])

/* ── 状态 ── */
const selectedType = ref(props.recordType)
const records = ref([])
const loading = ref(false)
const recordA = ref(null)
const recordB = ref(null)
const detailA = ref(null)
const detailB = ref(null)
const detailLoading = ref(false)

const typeOptions = [
  { value: 'panorama', label: '全景诊断' },
  { value: 'deepdive', label: '深度分析' },
  { value: 'trade-review', label: '交易复盘' },
]

const listFnMap = {
  panorama: listPanoramaRecords,
  deepdive: listDeepDiveRecords,
  'trade-review': listTradeReviewRecords,
}

/* ── 是否已选满 2 条 ── */
const selectedIds = computed(() => {
  const ids = []
  if (recordA.value) ids.push(recordA.value)
  if (recordB.value) ids.push(recordB.value)
  return ids
})

const isCompareReady = computed(() => selectedIds.value.length === 2)

/* ── 加载列表 ── */
async function loadRecords() {
  loading.value = true
  recordA.value = null
  recordB.value = null
  detailA.value = null
  detailB.value = null
  try {
    const fn = listFnMap[selectedType.value]
    if (fn) {
      const res = await fn(50)
      records.value = res?.data?.records || res?.data || []
    }
  } catch (e) {
    console.error('加载分析记录失败', e)
    records.value = []
  } finally {
    loading.value = false
  }
}

/* ── 切换类型 ── */
watch(selectedType, () => {
  loadRecords()
})

/* ── checkbox 选择逻辑：最多选 2 条 ── */
function toggleRecord(id) {
  if (recordA.value === id) {
    recordA.value = recordB.value
    recordB.value = null
    detailA.value = detailB.value
    detailB.value = null
    return
  }
  if (recordB.value === id) {
    recordB.value = null
    detailB.value = null
    return
  }
  if (!recordA.value) {
    recordA.value = id
  } else if (!recordB.value) {
    recordB.value = id
  } else {
    // 已选满 2 条，替换 B
    recordB.value = id
    detailB.value = null
  }
}

function isChecked(id) {
  return recordA.value === id || recordB.value === id
}

/* ── 加载详情 ── */
async function loadDetails() {
  if (!isCompareReady.value) return
  detailLoading.value = true
  try {
    const [resA, resB] = await Promise.all([
      getPortfolioAiAnalysisRecord(recordA.value),
      getPortfolioAiAnalysisRecord(recordB.value),
    ])
    detailA.value = resA?.data || null
    detailB.value = resB?.data || null
  } catch (e) {
    console.error('加载详情失败', e)
  } finally {
    detailLoading.value = false
  }
}

watch(isCompareReady, (ready) => {
  if (ready) loadDetails()
})

/* ── 返回列表视图 ── */
function goBack() {
  recordA.value = null
  recordB.value = null
  detailA.value = null
  detailB.value = null
  emit('back')
}

/* ── 格式化日期 ── */
function fmtDate(dateStr) {
  if (!dateStr) return '--'
  const d = new Date(dateStr)
  if (isNaN(d.getTime())) return dateStr
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

/* ── 初始化 ── */
onMounted(() => {
  loadRecords()
})
</script>

<template>
  <div class="comparison-page">
    <!-- 页头 -->
    <div class="page-header">
      <button class="btn-back" @click="goBack">
        <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7" />
        </svg>
        <span>返回</span>
      </button>
      <h2 class="page-title">分析对比</h2>
    </div>

    <!-- 类型选择器 -->
    <div class="filter-bar glass-card">
      <label class="filter-label">分析类型</label>
      <select v-model="selectedType" class="type-select">
        <option v-for="opt in typeOptions" :key="opt.value" :value="opt.value">
          {{ opt.label }}
        </option>
      </select>
      <span class="selected-count">
        已选 {{ selectedIds.length }} / 2 条
      </span>
    </div>

    <!-- 列表视图：尚未选满 2 条 -->
    <div v-if="!isCompareReady" class="list-view">
      <div v-if="loading" class="loading-state">
        <div class="spinner"></div>
        <span>加载中...</span>
      </div>

      <div v-else-if="records.length === 0" class="empty-state">
        <svg width="48" height="48" fill="none" stroke="currentColor" viewBox="0 0 24 24" class="empty-icon">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
        </svg>
        <p>暂无 {{ typeOptions.find(t => t.value === selectedType)?.label }} 记录</p>
      </div>

      <div v-else class="record-list">
        <div
          v-for="rec in records"
          :key="rec.id"
          :class="['record-item glass-card', { 'record-selected': isChecked(rec.id) }]"
          @click="toggleRecord(rec.id)"
        >
          <div class="record-checkbox">
            <input
              type="checkbox"
              :checked="isChecked(rec.id)"
              @click.stop
              @change="toggleRecord(rec.id)"
            />
          </div>
          <div class="record-content">
            <div class="record-summary">{{ rec.summary || '（无摘要）' }}</div>
            <div class="record-meta">
              <span class="record-date">{{ fmtDate(rec.created_at) }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 对比视图：已选 2 条 -->
    <div v-else class="compare-view">
      <div v-if="detailLoading" class="loading-state">
        <div class="spinner"></div>
        <span>加载详情中...</span>
      </div>

      <div v-else class="compare-grid">
        <!-- A 侧 -->
        <div class="compare-side glass-card">
          <div class="side-header">
            <span class="side-badge side-badge-a">A</span>
            <span class="side-date">{{ fmtDate(detailA?.created_at) }}</span>
          </div>
          <div class="side-section">
            <h4 class="section-title">摘要</h4>
            <p class="section-text">{{ detailA?.summary || '--' }}</p>
          </div>
          <div class="side-section">
            <h4 class="section-title">分析结果</h4>
            <div class="result-content" v-if="detailA?.result_data">
              <pre class="result-pre">{{ typeof detailA.result_data === 'string' ? detailA.result_data : JSON.stringify(detailA.result_data, null, 2) }}</pre>
            </div>
            <p v-else class="section-text text-muted">暂无结果数据</p>
          </div>
        </div>

        <!-- B 侧 -->
        <div class="compare-side glass-card">
          <div class="side-header">
            <span class="side-badge side-badge-b">B</span>
            <span class="side-date">{{ fmtDate(detailB?.created_at) }}</span>
          </div>
          <div class="side-section">
            <h4 class="section-title">摘要</h4>
            <p class="section-text">{{ detailB?.summary || '--' }}</p>
          </div>
          <div class="side-section">
            <h4 class="section-title">分析结果</h4>
            <div class="result-content" v-if="detailB?.result_data">
              <pre class="result-pre">{{ typeof detailB.result_data === 'string' ? detailB.result_data : JSON.stringify(detailB.result_data, null, 2) }}</pre>
            </div>
            <p v-else class="section-text text-muted">暂无结果数据</p>
          </div>
        </div>
      </div>

      <!-- 重新选择 -->
      <div class="compare-actions">
        <button class="btn-secondary" @click="recordA = null; recordB = null; detailA = null; detailB = null">
          重新选择
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.comparison-page {
  padding: var(--space-6);
  max-width: 1200px;
  margin: 0 auto;
}

/* ── 页头 ── */
.page-header {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  margin-bottom: var(--space-6);
}

.btn-back {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-card);
  color: var(--color-text-secondary);
  font-size: 14px;
  cursor: pointer;
  transition: all var(--transition-fast);
}
.btn-back:hover {
  background: var(--color-bg-hover);
  color: var(--color-primary);
  border-color: var(--color-primary-border);
}

.page-title {
  font-size: 20px;
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0;
}

/* ── Glass Card 通用 ── */
.glass-card {
  background: var(--glass-bg);
  backdrop-filter: blur(var(--glass-blur));
  -webkit-backdrop-filter: blur(var(--glass-blur));
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--glass-shadow);
}

/* ── 筛选栏 ── */
.filter-bar {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  padding: var(--space-4) var(--space-5);
  margin-bottom: var(--space-5);
}

.filter-label {
  font-size: 14px;
  font-weight: 500;
  color: var(--color-text-secondary);
  white-space: nowrap;
}

.type-select {
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-card);
  color: var(--color-text-primary);
  font-size: 14px;
  cursor: pointer;
  outline: none;
  transition: border-color var(--transition-fast);
}
.type-select:focus {
  border-color: var(--color-primary);
  box-shadow: var(--focus-ring);
}

.selected-count {
  margin-left: auto;
  font-size: 13px;
  color: var(--color-text-muted);
  font-variant-numeric: tabular-nums;
}

/* ── 加载 / 空状态 ── */
.loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-12) 0;
  color: var(--color-text-muted);
  font-size: 14px;
}

.spinner {
  width: var(--spinner-lg);
  height: var(--spinner-lg);
  border: 3px solid var(--color-border);
  border-top-color: var(--color-primary);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin {
  to { transform: rotate(360deg); }
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-12) 0;
  color: var(--color-text-muted);
}
.empty-icon {
  opacity: 0.4;
}

/* ── 记录列表 ── */
.record-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.record-item {
  display: flex;
  align-items: flex-start;
  gap: var(--space-4);
  padding: var(--space-4) var(--space-5);
  cursor: pointer;
  transition: all var(--transition-fast);
  user-select: none;
}
.record-item:hover {
  border-color: var(--color-primary-border);
  box-shadow: var(--shadow-md);
}
.record-selected {
  border-color: var(--color-primary) !important;
  background: var(--color-primary-bg-weak) !important;
}

.record-checkbox {
  padding-top: 2px;
  flex-shrink: 0;
}
.record-checkbox input[type="checkbox"] {
  width: 18px;
  height: 18px;
  accent-color: var(--color-primary);
  cursor: pointer;
}

.record-content {
  flex: 1;
  min-width: 0;
}

.record-summary {
  font-size: 14px;
  color: var(--color-text-primary);
  line-height: 1.6;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.record-meta {
  margin-top: var(--space-2);
}
.record-date {
  font-size: 12px;
  color: var(--color-text-muted);
  font-variant-numeric: tabular-nums;
}

/* ── 对比视图 ── */
.compare-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-5);
}

.compare-side {
  padding: var(--space-5);
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.side-header {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding-bottom: var(--space-3);
  border-bottom: 1px solid var(--color-border-light);
}

.side-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: var(--radius-sm);
  font-size: 13px;
  font-weight: 700;
  color: var(--color-text-inverse);
}
.side-badge-a {
  background: var(--gradient-primary);
}
.side-badge-b {
  background: var(--gradient-accent);
}

.side-date {
  font-size: 13px;
  color: var(--color-text-muted);
  font-variant-numeric: tabular-nums;
}

.side-section {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.section-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text-secondary);
  margin: 0;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.section-text {
  font-size: 14px;
  color: var(--color-text-primary);
  line-height: 1.7;
  margin: 0;
}
.text-muted {
  color: var(--color-text-muted);
}

.result-content {
  background: var(--color-bg-secondary);
  border-radius: var(--radius-md);
  padding: var(--space-4);
  overflow: auto;
  max-height: 500px;
}

.result-pre {
  margin: 0;
  font-family: var(--font-sans);
  font-size: 13px;
  line-height: 1.7;
  color: var(--color-text-primary);
  white-space: pre-wrap;
  word-break: break-word;
}

/* ── 底部操作 ── */
.compare-actions {
  display: flex;
  justify-content: center;
  margin-top: var(--space-6);
}

.btn-secondary {
  padding: var(--space-2) var(--space-5);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-card);
  color: var(--color-text-secondary);
  font-size: 14px;
  cursor: pointer;
  transition: all var(--transition-fast);
}
.btn-secondary:hover {
  background: var(--color-bg-hover);
  border-color: var(--color-primary-border);
  color: var(--color-primary);
}

/* ── 响应式 ── */
@media (max-width: 768px) {
  .comparison-page {
    padding: var(--space-4);
  }
  .compare-grid {
    grid-template-columns: 1fr;
  }
  .filter-bar {
    flex-wrap: wrap;
  }
}
</style>
