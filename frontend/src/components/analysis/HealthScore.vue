<script setup>
import { ref, onMounted, computed } from 'vue'
import {
  calculateHealthScore, getTodayHealthScore,
  getHealthHistory, getStockBondRatio, getFearGreedIndex,
} from '../../api'
import { useToast } from '../../composables/useToast'
import EmptyState from '../ui/EmptyState.vue'

const { showToast } = useToast()

// 五维度术语词典
const DIMENSION_TIPS = {
  quality: '持仓基金的综合质量评分（选品能力），满分200',
  diversification: '资产配置分散程度，避免单一标的或行业过度集中',
  valuation: '持仓估值水平是否合理，结合历史分位判断',
  behavior: '持有时长、追涨杀跌等行为评估，鼓励长期持有',
  risk: '止损、再平衡等风控纪律执行情况',
}

const loading = ref(false)
const score = ref(null)
const history = ref([])
const stockBond = ref(null)
const fearGreed = ref(null)

const scoreColor = computed(() => {
  if (!score.value) return '#999'
  const s = score.value.total_score
  if (s >= 800) return '#10b981'
  if (s >= 600) return '#22c55e'
  if (s >= 400) return '#f59e0b'
  if (s >= 200) return '#f97316'
  return '#ef4444'
})

const scoreLevel = computed(() => {
  if (!score.value) return ''
  const s = score.value.total_score
  if (s >= 800) return '优秀'
  if (s >= 600) return '良好'
  if (s >= 400) return '一般'
  if (s >= 200) return '较差'
  return '危险'
})

const dimensions = computed(() => {
  if (!score.value) return []
  const sc = score.value.scores || score.value
  return [
    { key: 'quality', label: '选品质量', score: sc.quality || 0, max: 200 },
    { key: 'diversification', label: '分散配置', score: sc.diversification || 0, max: 200 },
    { key: 'valuation', label: '估值合理', score: sc.valuation || 0, max: 200 },
    { key: 'behavior', label: '持有行为', score: sc.behavior || 0, max: 200 },
    { key: 'risk', label: '风控纪律', score: sc.risk || 0, max: 200 },
  ]
})

const adviceList = computed(() => {
  if (!score.value) return []
  return score.value.advice || []
})

async function loadData() {
  loading.value = true
  try {
    const [scoreResp, histResp, sbResp, fgResp] = await Promise.allSettled([
      getTodayHealthScore(),
      getHealthHistory(30),
      getStockBondRatio(),
      getFearGreedIndex(),
    ])

    if (scoreResp.status === 'fulfilled') {
      const d = scoreResp.value.data
      score.value = d.result || d
    }
    if (histResp.status === 'fulfilled') {
      history.value = histResp.value.data.scores || []
    }
    if (sbResp.status === 'fulfilled') {
      stockBond.value = sbResp.value.data.result || sbResp.value.data
    }
    if (fgResp.status === 'fulfilled') {
      fearGreed.value = fgResp.value.data.result || fgResp.value.data
    }
  } catch (e) {
    showToast('加载失败: ' + e.message, 'error')
  } finally {
    loading.value = false
  }
}

async function recalculate() {
  loading.value = true
  try {
    const { data } = await calculateHealthScore()
    score.value = data.result || data
    showToast('健康分已更新', 'success')
    // 重新加载历史
    const histResp = await getHealthHistory(30)
    history.value = histResp.data.scores || []
  } catch (e) {
    showToast('计算失败: ' + e.message, 'error')
  } finally {
    loading.value = false
  }
}

const fearGreedColor = computed(() => {
  if (!fearGreed.value) return '#999'
  const s = fearGreed.value.score
  if (s <= 20) return '#ef4444'
  if (s <= 40) return '#f97316'
  if (s <= 60) return '#f59e0b'
  if (s <= 80) return '#22c55e'
  return '#10b981'
})

onMounted(loadData)
</script>

<template>
  <div class="health-page bg-mesh">
    <div class="page-header">
      <div>
        <h2 class="editorial-title-lg">投资健康分</h2>
        <p class="page-desc">综合评估选品、配置、估值、行为、风控五大维度</p>
      </div>
      <button class="btn-primary" @click="recalculate" :disabled="loading">
        {{ loading ? '计算中...' : '重新计算' }}
      </button>
    </div>

    <!-- 主分数 -->
    <div v-if="score" class="score-hero card editorial-card">
      <div class="score-circle" :style="{ borderColor: scoreColor }">
        <span class="score-number font-jet-lg" :style="{ color: scoreColor }">{{ score.total_score }}</span>
        <span class="score-max terminal-label">/1000</span>
      </div>
      <div class="score-info">
        <span class="score-level editorial-title" :style="{ color: scoreColor }">{{ scoreLevel }}</span>
        <span class="score-date terminal-label">{{ score.date || score.score_date }}</span>
      </div>
    </div>
    <!-- 空 state：score 为 null 时引导用户操作 -->
    <div v-else-if="!loading" class="card editorial-card score-empty">
      <EmptyState
        icon="chart"
        title="暂无健康分数据"
        description="点击右上角'重新计算'生成最新评分，系统将综合评估选品、配置、估值、行为、风控五大维度"
        actionText="立即计算"
        @action="recalculate"
      />
    </div>

    <!-- 五维度 -->
    <div v-if="score" class="dimensions card editorial-card">
      <h3 class="editorial-title">五维度评分</h3>
      <div class="dim-grid">
        <div v-for="d in dimensions" :key="d.key" class="dim-item reveal-stagger">
          <div class="dim-header">
            <span class="term-with-tip dim-label">{{ d.label }}<span class="term-tip">{{ DIMENSION_TIPS[d.key] || '' }}</span></span>
            <span class="dim-score font-jet">{{ d.score }}/{{ d.max }}</span>
          </div>
          <div class="dim-bar">
            <div class="dim-fill" :style="{
              width: (d.score / d.max * 100) + '%',
              background: d.score >= d.max * 0.7 ? '#10b981' : d.score >= d.max * 0.4 ? '#f59e0b' : '#ef4444'
            }"></div>
          </div>
        </div>
      </div>
    </div>

    <!-- 股债性价比 -->
    <div v-if="stockBond && !stockBond.error" class="stock-bond card editorial-card">
      <div class="editorial-card-header"><h3 class="editorial-title"><span class="term-with-tip">股债性价比（FED模型）<span class="term-tip">FED模型 = 股票盈利收益率 - 10年国债收益率。利差越大，股票相对债券越有投资价值；利差为负时债券更优</span></span></h3></div>
      <div class="sb-grid">
        <div class="sb-item">
          <span class="sb-label terminal-label">沪深300 PE</span>
          <span class="sb-value font-jet">{{ stockBond.hs300_pe }}</span>
        </div>
        <div class="sb-item">
          <span class="sb-label terminal-label">盈利收益率</span>
          <span class="sb-value font-jet">{{ stockBond.earnings_yield }}%</span>
        </div>
        <div class="sb-item">
          <span class="sb-label terminal-label">10年国债</span>
          <span class="sb-value font-jet">{{ stockBond.bond_yield_10y }}%</span>
        </div>
        <div class="sb-item">
          <span class="sb-label terminal-label">利差</span>
          <span class="sb-value font-jet" :class="{ positive: stockBond.spread > 0, negative: stockBond.spread < 0 }">
            {{ stockBond.spread > 0 ? '+' : '' }}{{ stockBond.spread }}%
          </span>
        </div>
      </div>
      <div class="sb-signal">
        <span class="signal-badge" :class="{
          'signal-buy': stockBond.spread > 3,
          'signal-neutral': stockBond.spread >= 0 && stockBond.spread <= 3,
          'signal-sell': stockBond.spread < 0
        }">{{ stockBond.signal }}</span>
        <span class="sb-advice">{{ stockBond.advice }}</span>
      </div>
    </div>

    <!-- 恐贪指数 -->
    <div v-if="fearGreed && !fearGreed.error" class="fear-greed card editorial-card">
      <div class="editorial-card-header"><h3 class="editorial-title"><span class="term-with-tip">恐贪指数<span class="term-tip">市场恐惧与贪婪指数，综合换手率、涨跌比、波动率等指标。0=极度恐惧（逢低买入机会），100=极度贪婪（警惕回调）</span></span></h3></div>
      <div class="fg-main">
        <div class="fg-gauge">
          <div class="fg-bar">
            <div class="fg-fill" :style="{ width: fearGreed.score + '%', background: fearGreedColor }"></div>
          </div>
          <div class="fg-labels">
            <span>极度恐慌</span>
            <span>恐慌</span>
            <span>中性</span>
            <span>贪婪</span>
            <span>极度贪婪</span>
          </div>
        </div>
        <div class="fg-score" :style="{ color: fearGreedColor }">
          <span class="fg-number font-jet-lg">{{ fearGreed.score }}</span>
          <span class="fg-zone">{{ fearGreed.zone }}</span>
        </div>
      </div>
      <p class="fg-advice">{{ fearGreed.advice }}</p>
      <div class="fg-factors" v-if="fearGreed.factors">
        <div v-for="(val, key) in fearGreed.factors" :key="key" class="fg-factor reveal-stagger">
          <span class="factor-name">{{ key }}</span>
          <span class="factor-value font-jet">{{ val }}</span>
        </div>
      </div>
    </div>

    <!-- 改进建议 -->
    <div v-if="adviceList.length" class="advice card editorial-card">
      <div class="editorial-card-header"><h3 class="editorial-title">改进建议</h3></div>
      <ul>
        <li v-for="(a, i) in adviceList" :key="i" class="reveal-stagger">{{ a }}</li>
      </ul>
      <p v-if="score?.summary" class="ai-summary">{{ score.summary }}</p>
    </div>

    <!-- 历史趋势 -->
    <div v-if="history.length > 1" class="history card editorial-card">
      <div class="editorial-card-header"><h3 class="editorial-title">历史趋势</h3></div>
      <div class="history-list">
        <div v-for="h in history" :key="h.id || h.score_date" class="history-item reveal-stagger">
          <span class="hist-date terminal-label">{{ h.score_date }}</span>
          <div class="hist-bar">
            <div class="hist-fill" :style="{
              width: (h.total_score / 1000 * 100) + '%',
              background: h.total_score >= 600 ? '#10b981' : h.total_score >= 400 ? '#f59e0b' : '#ef4444'
            }"></div>
          </div>
          <span class="hist-score font-jet">{{ h.total_score }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.health-page { animation: fadeIn 0.2s ease; }
.page-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 16px; }
.page-header h2 { margin: 0; }
.page-desc { color: var(--color-text-muted); font-size: 0.85em; margin-top: 4px; }
.card { background: var(--color-card); border-radius: 12px; padding: 16px; margin-bottom: 12px; }

.score-hero { display: flex; align-items: center; gap: 24px; padding: 24px; }
.score-circle {
  width: 100px; height: 100px; border-radius: 50%;
  border: 4px solid; display: flex; flex-direction: column;
  align-items: center; justify-content: center; flex-shrink: 0;
}
.score-number { font-size: 2em; font-weight: inherit; line-height: 1; }
.score-max { font-size: inherit; color: var(--color-text-muted); }
.score-info { display: flex; flex-direction: column; gap: 4px; }
.score-level { font-size: 1.3em; font-weight: inherit; }
.score-date { color: var(--color-text-muted); font-size: 0.85em; }

.dimensions h3 { margin: 0 0 12px; }
.dim-grid { display: flex; flex-direction: column; gap: 10px; }
.dim-header { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.dim-icon { font-size: 1.1em; }
.dim-label { flex: 1; font-weight: 500; }
.dim-score { color: var(--color-text-muted); font-size: 0.9em; }
.dim-bar { height: 8px; background: var(--color-bg); border-radius: 4px; overflow: hidden; }
.dim-fill { height: 100%; border-radius: 4px; transition: width 0.6s ease; }

.stock-bond h3, .fear-greed h3, .advice h3, .history h3 { margin: 0; }
.sb-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 12px; }
.sb-item { text-align: center; }
.sb-label { display: block; font-size: inherit; color: var(--color-text-muted); margin-bottom: 4px; }
.sb-value { font-size: 1.2em; font-weight: 700; }
.sb-value.positive { color: #ef4444; }
.sb-value.negative { color: #10b981; }
.sb-signal { display: flex; align-items: center; gap: 8px; }
.signal-badge { padding: 4px 12px; border-radius: 4px; font-weight: 600; font-size: 0.85em; }
.signal-buy { background: rgba(16,185,129,0.15); color: #10b981; }
.signal-neutral { background: rgba(245,158,11,0.15); color: #f59e0b; }
.signal-sell { background: rgba(239,68,68,0.15); color: #ef4444; }
.sb-advice { color: var(--color-text-muted); font-size: 0.9em; }

.fg-main { display: flex; align-items: center; gap: 16px; margin-bottom: 12px; }
.fg-gauge { flex: 1; }
.fg-bar { height: 12px; background: var(--color-bg); border-radius: 6px; overflow: hidden; margin-bottom: 4px; }
.fg-fill { height: 100%; border-radius: 6px; transition: width 0.6s ease; }
.fg-labels { display: flex; justify-content: space-between; font-size: 0.7em; color: var(--color-text-muted); }
.fg-score { text-align: center; flex-shrink: 0; }
.fg-number { display: block; font-size: 2em; font-weight: inherit; }
.fg-zone { font-size: 0.9em; font-weight: 600; }
.fg-advice { color: var(--color-text-muted); font-size: 0.9em; margin-bottom: 8px; }
.fg-factors { display: flex; gap: 12px; flex-wrap: wrap; }
.fg-factor { font-size: 0.8em; }
.factor-name { color: var(--color-text-muted); margin-right: 4px; }
.factor-value { font-weight: 600; }

.advice ul { padding-left: 20px; margin: 0 0 8px; }
.advice li { margin-bottom: 6px; font-size: 0.9em; }
.ai-summary { color: var(--color-text-muted); font-size: 0.85em; font-style: italic; border-top: 1px solid var(--color-border); padding-top: 8px; margin: 8px 0 0; }

.history-list { display: flex; flex-direction: column; gap: 6px; }
.history-item { display: flex; align-items: center; gap: 8px; }
.hist-date { width: 80px; font-size: inherit; color: var(--color-text-muted); flex-shrink: 0; }
.hist-bar { flex: 1; height: 6px; background: var(--color-bg); border-radius: 3px; overflow: hidden; }
.hist-fill { height: 100%; border-radius: 3px; transition: width 0.6s ease; }
.hist-score { width: 40px; text-align: right; font-weight: 600; font-size: 0.85em; }

@media (max-width: 768px) {
  .sb-grid { grid-template-columns: repeat(2, 1fr); }
  .score-hero { flex-direction: column; text-align: center; }
  .score-circle { width: 80px; height: 80px; }
  .score-number { font-size: 1.6em; }
  .page-header { flex-direction: column; gap: 8px; }
  .page-header .btn-primary { width: 100%; }
  .dim-header { font-size: 0.85em; }
  .fg-labels { font-size: 0.6em; }
  .fg-number { font-size: 1.5em; }
  .history-item { font-size: 0.8em; }
}

@media (max-width: 480px) {
  .score-circle { width: 70px; height: 70px; }
  .score-number { font-size: 1.4em; }
  .sb-grid { grid-template-columns: 1fr 1fr; gap: 8px; }
  .sb-value { font-size: 1em; }
  .dim-grid { gap: 8px; }
  .advice li { font-size: 0.85em; }
}
</style>
