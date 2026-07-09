<script setup>
import { computed, onMounted, ref } from 'vue'
import { getBehaviorReport, getBehaviorScore } from '../api'
import { useToast } from '../composables/useToast'
import Icon from './ui/Icon.vue'
import EmptyState from './ui/EmptyState.vue'

const { showToast } = useToast()

// ── 周期选择 ──
const PERIOD_OPTIONS = [
  { value: 30, label: '近 30 天' },
  { value: 90, label: '近 90 天' },
  { value: 180, label: '近 180 天' },
  { value: 365, label: '近 1 年' },
]
const periodDays = ref(90)

// ── 数据状态 ──
const loading = ref(false)
const report = ref(null)
const score = ref(null)

// ── 综合偏差分级 ──
const scoreLevel = computed(() => {
  if (!score.value && !report.value) return ''
  const s = Number(score.value?.total_score ?? report.value?.bias_score ?? 0)
  if (s <= 30) return { label: '低', cls: 'low', desc: '交易行为较理性' }
  if (s <= 60) return { label: '中', cls: 'medium', desc: '存在部分行为偏差' }
  return { label: '高', cls: 'high', desc: '行为偏差明显，建议改善' }
})

const totalScore = computed(() => {
  return Number(score.value?.total_score ?? report.value?.bias_score ?? 0)
})

// ── 4 个偏差维度 ──
const biasDimensions = computed(() => {
  const r = report.value || {}
  const dims = r.dimensions || r.biases || []
  if (Array.isArray(dims) && dims.length) {
    return dims.map(d => ({
      key: d.key || d.type,
      label: d.label || d.name,
      score: d.score ?? 0,
      detail: d.detail || d.description || '',
    }))
  }
  // 兜底：从扁平字段构造
  return [
    { key: 'disposition', label: '处置效应', score: r.disposition_score ?? 0, detail: r.disposition_detail || '过早卖出盈利、过晚卖出亏损' },
    { key: 'anchoring', label: '锚定效应', score: r.anchoring_score ?? 0, detail: r.anchoring_detail || '过度依赖买入成本或历史高点' },
    { key: 'herding', label: '羊群效应', score: r.herding_score ?? 0, detail: r.herding_detail || '跟随市场情绪追涨杀跌' },
    { key: 'overtrading', label: '过度交易', score: r.overtrading_score ?? 0, detail: r.overtrading_detail || '交易频率过高、摩擦成本大' },
  ]
})

const adviceList = computed(() => {
  return report.value?.advice || report.value?.suggestions || []
})

function biasColor(s) {
  if (s <= 30) return 'var(--color-success)'
  if (s <= 60) return 'var(--color-warning)'
  return 'var(--color-danger)'
}

// ── 加载数据 ──
async function loadAll() {
  loading.value = true
  try {
    const [reportResp, scoreResp] = await Promise.allSettled([
      getBehaviorReport(periodDays.value),
      getBehaviorScore(),
    ])
    if (reportResp.status === 'fulfilled') {
      const d = reportResp.value.data
      report.value = d.result || d
    }
    if (scoreResp.status === 'fulfilled') {
      const d = scoreResp.value.data
      score.value = d.result || d
    }
  } catch (e) {
    showToast('加载行为诊断失败: ' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    loading.value = false
  }
}

onMounted(loadAll)
</script>

<template>
  <div class="behavior-page bg-mesh">
    <header class="page-header">
      <div>
        <h2 class="page-title editorial-title-lg">行为诊断报告</h2>
        <p class="page-desc">识别处置、锚定、羊群与过度交易四类行为偏差。</p>
      </div>
      <div class="header-actions">
        <select v-model="periodDays" class="input-field period-select" @change="loadAll">
          <option v-for="opt in PERIOD_OPTIONS" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
        </select>
        <button class="btn-primary" @click="loadAll" :disabled="loading">
          <Icon :name="loading ? 'spinner' : 'refresh'" size="16" />
          {{ loading ? '加载中...' : '刷新' }}
        </button>
      </div>
    </header>

    <!-- 综合偏差分 -->
    <section v-if="report || score" class="score-hero editorial-card reveal-stagger">
      <div class="score-circle" :style="{ borderColor: biasColor(totalScore) }">
        <span class="score-number font-jet-lg" :style="{ color: biasColor(totalScore) }">{{ totalScore }}</span>
        <span class="score-max terminal-label">/100</span>
      </div>
      <div class="score-info">
        <span class="score-level editorial-title" :class="scoreLevel?.cls">{{ scoreLevel?.label || '-' }}</span>
        <span class="score-desc terminal-label">{{ scoreLevel?.desc || '' }}</span>
        <span v-if="report?.period" class="score-period terminal-label">统计周期：{{ report.period }}</span>
      </div>
    </section>

    <!-- 加载中 -->
    <div v-if="loading && !report" class="loading-state editorial-card">
      <Icon name="spinner" size="22" />
      <span>正在生成行为诊断...</span>
    </div>

    <!-- 空状态 -->
    <EmptyState
      v-else-if="!report && !score && !loading"
      icon="chart"
      title="暂无行为诊断数据"
      description="需要足够的交易记录才能识别行为偏差，先在持仓管理中补全交易。"
    />

    <!-- 4 个偏差卡片 -->
    <section v-if="report" class="bias-grid">
      <article
        v-for="b in biasDimensions"
        :key="b.key"
        class="bias-card editorial-card reveal-stagger"
      >
        <div class="bias-head">
          <span class="bias-label editorial-title">{{ b.label }}</span>
          <strong class="bias-score font-jet" :style="{ color: biasColor(b.score) }">{{ b.score }}</strong>
        </div>
        <div class="bias-bar">
          <div class="bias-fill" :style="{ width: Math.min(b.score, 100) + '%', background: biasColor(b.score) }"></div>
        </div>
        <p class="bias-detail">{{ b.detail }}</p>
      </article>
    </section>

    <!-- 改善建议 -->
    <section v-if="adviceList.length" class="advice-section editorial-card">
      <div class="section-head editorial-card-header">
        <h3 class="editorial-title">改善建议</h3>
      </div>
      <ul class="advice-list">
        <li v-for="(a, i) in adviceList" :key="i" class="reveal-stagger">{{ a }}</li>
      </ul>
      <p v-if="report?.summary" class="ai-summary">{{ report.summary }}</p>
    </section>
  </div>
</template>

<style scoped>
.behavior-page {
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
}

.header-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.period-select {
  padding: 6px 10px;
  font-size: 0.85rem;
  min-width: 130px;
}

/* 综合分 */
.score-hero {
  display: flex;
  align-items: center;
  gap: var(--space-5);
  padding: var(--space-5);
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
}
.score-circle {
  width: 100px;
  height: 100px;
  border-radius: 50%;
  border: 4px solid;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.score-number {
  font-size: 2em;
  font-weight: 700;
  line-height: 1;
}
.score-max {
  font-size: inherit;
  color: var(--color-text-muted);
}
.score-info {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.score-level {
  font-size: 1.4rem;
  font-weight: 700;
}
.score-level.low { color: var(--color-success); }
.score-level.medium { color: var(--color-warning); }
.score-level.high { color: var(--color-danger); }
.score-desc {
  color: var(--color-text-secondary);
  font-size: 0.9rem;
}
.score-period {
  color: var(--color-text-muted);
  font-size: 0.8rem;
}

.loading-state {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  padding: var(--space-6);
  color: var(--color-text-muted);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-bg-card);
}

/* 偏差卡片 */
.bias-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-3);
}
.bias-card {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: var(--space-4);
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.bias-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.bias-label {
  font-size: 1rem;
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0;
}
.bias-score {
  font-size: 1.3rem;
  font-weight: 700;
}
.bias-bar {
  height: 8px;
  background: var(--color-bg-input);
  border-radius: 4px;
  overflow: hidden;
}
.bias-fill {
  height: 100%;
  border-radius: 4px;
  transition: width 0.6s ease;
}
.bias-detail {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: 0.85rem;
  line-height: 1.6;
}

/* 改善建议 */
.section-head {
  margin-bottom: var(--space-3);
}
.section-head h3 { margin: 0; }
.advice-list {
  padding-left: 20px;
  margin: 0 0 8px;
}
.advice-list li {
  margin-bottom: 6px;
  font-size: 0.9rem;
  color: var(--color-text-primary);
  line-height: 1.6;
}
.ai-summary {
  color: var(--color-text-muted);
  font-size: 0.85rem;
  font-style: italic;
  border-top: 1px solid var(--color-border);
  padding-top: 8px;
  margin: 8px 0 0;
}

@media (max-width: 768px) {
  .behavior-page { padding: var(--space-3); }
  .page-header { flex-direction: column; }
  .header-actions { width: 100%; }
  .score-hero { flex-direction: column; text-align: center; }
  .bias-grid { grid-template-columns: 1fr; }
  .score-circle { width: 80px; height: 80px; }
  .score-number { font-size: 1.6em; }
}
</style>
