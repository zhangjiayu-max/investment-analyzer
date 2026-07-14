<script setup>
import { computed, onMounted, ref } from 'vue'
import { getBehaviorReport, getBehaviorScore } from '../../api'
import { useToast } from '../../composables/useToast'
import Icon from '../ui/Icon.vue'
import EmptyState from '../ui/EmptyState.vue'

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

// ── 4 个偏差维度的含义说明（PC 悬停 / 移动端点击 ? 展开） ──
const BIAS_HINTS = {
  disposition: '过早卖出盈利标的、过晚卖出亏损标的，导致「赚小钱亏大钱」。分数越高说明该倾向越明显。',
  anchoring: '过度依赖买入成本或历史高点作为决策参考，忽视当前基本面变化。分数越高说明越受锚点束缚。',
  herding: '跟随市场情绪追涨杀跌，在高位买入、低位卖出。分数越高说明越容易盲从群体行为。',
  overtrading: '交易频率过高，手续费/印花税等摩擦成本侵蚀收益。分数越高说明换手越频繁。',
}

// 移动端无 hover，用 tap 切换含义提示
const activeHintKey = ref(null)
function toggleHint(key) {
  activeHintKey.value = activeHintKey.value === key ? null : key
}

// ── 4 个偏差维度 ──
const biasDimensions = computed(() => {
  const r = report.value || {}
  const dims = r.dimensions || r.biases || []
  if (Array.isArray(dims) && dims.length) {
    return dims.map(d => {
      const key = d.key || d.type
      return {
        key,
        label: d.label || d.name,
        score: d.score ?? 0,
        detail: d.detail || d.description || '',
        hint: BIAS_HINTS[key] || '',
      }
    })
  }
  // 兜底：从扁平字段构造
  return [
    { key: 'disposition', label: '处置效应', score: r.disposition_score ?? 0, detail: r.disposition_detail || '过早卖出盈利、过晚卖出亏损', hint: BIAS_HINTS.disposition },
    { key: 'anchoring', label: '锚定效应', score: r.anchoring_score ?? 0, detail: r.anchoring_detail || '过度依赖买入成本或历史高点', hint: BIAS_HINTS.anchoring },
    { key: 'herding', label: '羊群效应', score: r.herding_score ?? 0, detail: r.herding_detail || '跟随市场情绪追涨杀跌', hint: BIAS_HINTS.herding },
    { key: 'overtrading', label: '过度交易', score: r.overtrading_score ?? 0, detail: r.overtrading_detail || '交易频率过高、摩擦成本大', hint: BIAS_HINTS.overtrading },
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
          <span v-if="b.hint" class="bias-label-wrap" @click.stop="toggleHint(b.key)">
            <span class="bias-label editorial-title">{{ b.label }}</span>
            <span class="bias-hint-icon">?</span>
            <span class="bias-hint-popover" :class="{ 'is-active': activeHintKey === b.key }">{{ b.hint }}</span>
          </span>
          <span v-else class="bias-label editorial-title">{{ b.label }}</span>
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
/* 悬停含义提示 */
.bias-label-wrap {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  cursor: help;
}
.bias-hint-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: var(--color-bg-input);
  color: var(--color-text-muted);
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
  flex-shrink: 0;
}
.bias-hint-popover {
  position: absolute;
  bottom: calc(100% + 8px);
  left: 0;
  z-index: 20;
  width: max-content;
  max-width: 240px;
  padding: 8px 12px;
  background: var(--color-bg-card);
  color: var(--color-text-primary);
  font-size: 0.78rem;
  font-weight: 400;
  line-height: 1.55;
  border-radius: 6px;
  border: 1px solid var(--color-border);
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.18);
  opacity: 0;
  visibility: hidden;
  transition: opacity 0.18s ease, visibility 0.18s ease;
  pointer-events: none;
}
.bias-label-wrap:hover .bias-hint-popover,
.bias-hint-popover.is-active {
  opacity: 1;
  visibility: visible;
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
