<!-- 主题集中度分析：跨账户去重、同指数合并、主题HHI、碎片仓位 -->
<script setup>
import { computed, onMounted, ref } from 'vue'
import { getThemeAnalysis } from '../../api'
import { useToast } from '../../composables/useToast'
import { useChartTheme } from '../../composables/useChartTheme'
import { useLazyChart } from '../../composables/useLazyChart'
import PieChart from '../charts/PieChart.vue'
import Icon from '../ui/Icon.vue'

const { showToast } = useToast()
const { theme, isDark } = useChartTheme()

const loading = ref(false)
const data = ref(null)

const summary = computed(() => data.value?.summary || {})
const themes = computed(() => data.value?.themes || [])
const equityThemes = computed(() => themes.value.filter(t => !t.is_defensive))
const defensiveThemes = computed(() => themes.value.filter(t => t.is_defensive))
const crossAccount = computed(() => data.value?.duplicates?.cross_account || [])
const sameIndex = computed(() => data.value?.duplicates?.same_index || [])
const fragments = computed(() => data.value?.fragments || [])
const advice = computed(() => data.value?.advice || [])

// 权益主题环形图数据
const themePieData = computed(() => equityThemes.value.map(t => ({
  name: t.theme,
  value: Math.round(t.value),
})))

// 碎片仓位占组合比例（组合 = 持仓市值 + 现金）
const fragmentShare = computed(() => {
  const s = summary.value
  const assets = Number(s.total_value || 0) + Number(s.cash_balance || 0)
  return assets > 0 ? Number(s.fragment_total_value || 0) / assets : 0
})

// 主题盈亏条形图（横向，盈利红 / 亏损绿 —— A 股习惯）
const pnlChartRef = ref(null)

function pnlOption() {
  const t = theme.value
  const list = [...equityThemes.value].sort((a, b) => a.pnl - b.pnl)
  // 盈利红 / 亏损绿（与 --color-profit / --color-loss 亮暗主题一致）
  const profitColor = isDark.value ? '#f87171' : '#dc2626'
  const lossColor = isDark.value ? '#34d399' : '#059669'
  return {
    backgroundColor: 'transparent',
    grid: { left: 8, right: 24, top: 8, bottom: 8, containLabel: true },
    tooltip: {
      backgroundColor: isDark.value ? 'rgba(13,18,32,0.95)' : '#ffffff',
      borderColor: t.borderColor,
      borderWidth: 1,
      textStyle: { color: t.titleColor, fontSize: 12, fontFamily: t.fontMono },
      trigger: 'item',
      formatter: p => `<b>${p.name}</b><br/>盈亏: ¥${p.value.toLocaleString()}<br/>收益率: ${(list[p.dataIndex].pnl_rate * 100).toFixed(2)}%`,
    },
    xAxis: {
      type: 'value',
      axisLabel: { color: t.textColor, fontSize: 10, fontFamily: t.fontMono,
        formatter: v => (v >= 10000 || v <= -10000) ? `${(v / 10000).toFixed(1)}万` : v },
      splitLine: { lineStyle: { color: t.borderColor, type: 'dashed' } },
    },
    yAxis: {
      type: 'category',
      data: list.map(x => x.theme),
      axisLabel: { color: t.textColor, fontSize: 11, fontFamily: t.fontMono },
      axisLine: { lineStyle: { color: t.borderColor } },
    },
    series: [{
      type: 'bar',
      barMaxWidth: 16,
      itemStyle: { borderRadius: 3 },
      data: list.map(x => ({
        value: Math.round(x.pnl),
        itemStyle: { color: x.pnl < 0 ? lossColor : profitColor },
      })),
      label: {
        show: true, position: 'right',
        color: t.textColor, fontSize: 10, fontFamily: t.fontMono,
        formatter: p => `¥${Math.abs(p.value).toLocaleString()}`,
      },
    }],
  }
}

useLazyChart(pnlChartRef, pnlOption, [equityThemes, isDark])

function money(v) {
  return Number(v || 0).toLocaleString('zh-CN', { maximumFractionDigits: 0 })
}
function pct(v, digits = 1) {
  return `${(Number(v || 0) * 100).toFixed(digits)}%`
}
function pnlClass(v) {
  return Number(v || 0) < 0 ? 'neg' : 'pos'
}
function pnlText(v) {
  const n = Number(v || 0)
  return n < 0 ? `-¥${money(Math.abs(n))}` : `+¥${money(n)}`
}
function hhiLevel() {
  // 阈值以后端为准（0.12 中度 / 0.18 高度），前后端共用一套口径
  const th = summary.value.hhi_thresholds || { moderate: 0.12, high: 0.18 }
  const h = Number(summary.value.hhi || 0)
  const level = summary.value.hhi_level || (h >= th.high ? 'high' : h >= th.moderate ? 'moderate' : 'low')
  if (level === 'high') return { label: '高度集中', cls: 'danger' }
  if (level === 'moderate') return { label: '中度集中', cls: 'warning' }
  return { label: '分散合理', cls: 'ok' }
}

async function load() {
  loading.value = true
  try {
    const { data: res } = await getThemeAnalysis()
    data.value = res
  } catch (e) {
    showToast('加载主题分析失败: ' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="theme-page bg-mesh">
    <header class="page-head">
      <div>
        <h2 class="page-title editorial-title-lg">主题集中度</h2>
        <p class="page-desc">持仓数量的分散 ≠ 风险的分散：跨账户去重、同指数合并后，看真实的风险暴露。</p>
      </div>
      <button class="btn-secondary" :disabled="loading" @click="load">
        <Icon :name="loading ? 'spinner' : 'refresh'" size="16" />
        刷新
      </button>
    </header>

    <div v-if="loading && !data" class="empty-panel">
      <Icon name="spinner" size="22" />
      <span>正在聚合持仓主题...</span>
    </div>

    <div v-else-if="!data || !summary.holding_count" class="empty-panel">
      <Icon name="pie-chart" size="26" />
      <span>暂无持仓数据</span>
    </div>

    <template v-else>
      <!-- 总览指标 -->
      <section class="metric-strip">
        <div class="metric-cell">
          <span class="terminal-label">活跃持仓</span>
          <strong class="font-jet">{{ summary.holding_count }} 只</strong>
        </div>
        <div class="metric-cell">
          <span class="terminal-label">有效主题数</span>
          <strong class="font-jet">{{ summary.effective_theme_count }} <small class="terminal-label">/ 表面 {{ summary.theme_count }} 个</small></strong>
        </div>
        <div class="metric-cell">
          <span class="terminal-label">集中度 HHI</span>
          <strong class="font-jet" :class="hhiLevel().cls">{{ summary.hhi }} · {{ hhiLevel().label }}</strong>
        </div>
        <div class="metric-cell">
          <span class="terminal-label">前三大主题</span>
          <strong class="font-jet" :class="{ danger: summary.top3_weight > 0.5 }">{{ pct(summary.top3_weight, 0) }}</strong>
        </div>
        <div class="metric-cell">
          <span class="terminal-label">重复 / 碎片</span>
          <strong class="font-jet">{{ summary.duplicate_fund_count + summary.duplicate_index_count }} 组 <small class="terminal-label">/</small> {{ summary.fragment_count }} 只</strong>
        </div>
      </section>

      <!-- 诊断提示 -->
      <section v-if="advice.length" class="guardrail-list">
        <div v-for="item in advice" :key="item" class="guardrail-item">
          <Icon name="shield-alert" size="15" />
          {{ item }}
        </div>
      </section>

      <!-- 图表区 -->
      <section class="chart-layout">
        <div class="editorial-card chart-card">
          <div class="section-title">
            <h3 class="editorial-title">权益主题分布</h3>
            <span class="terminal-label">现值 ¥{{ money(summary.equity_value) }}</span>
          </div>
          <PieChart :data="themePieData" legend-position="right" height="300px" />
          <div v-if="defensiveThemes.length" class="bond-note terminal-label">
            另有防守盘<template v-for="(d, i) in defensiveThemes" :key="d.theme">「{{ d.theme }}」¥{{ money(d.value) }}（占总持仓 {{ pct(d.weight, 1) }}）<template v-if="i < defensiveThemes.length - 1">、</template></template>，未计入上图
          </div>
        </div>
        <div class="editorial-card chart-card">
          <div class="section-title">
            <h3 class="editorial-title">主题盈亏</h3>
            <span class="terminal-label">权益主题合计 ¥{{ money(summary.equity_pnl) }}</span>
          </div>
          <div ref="pnlChartRef" style="height: 340px; width: 100%"></div>
        </div>
      </section>

      <!-- 重复持仓 -->
      <section v-if="crossAccount.length || sameIndex.length" class="editorial-card dup-card">
        <div class="section-title">
          <h3 class="editorial-title">重复持仓</h3>
          <span class="terminal-label">合并后可减少管理负担</span>
        </div>
        <div v-if="crossAccount.length" class="dup-group">
          <p class="terminal-label dup-group-label">同一基金 · 跨账户</p>
          <div class="table-row table-head dup-account-row">
            <span>基金</span><span>主题</span><span>合计现值</span><span>合计盈亏</span><span>分布</span>
          </div>
          <template v-for="d in crossAccount" :key="d.fund_code">
            <div class="table-row dup-account-row">
              <span class="dup-name">{{ d.fund_name }}</span>
              <span>{{ d.theme }}</span>
              <span class="font-jet">¥{{ money(d.total_value) }}</span>
              <span class="font-jet" :class="pnlClass(d.total_pnl)">{{ pnlText(d.total_pnl) }}</span>
              <span class="terminal-label">{{ d.positions.map(p => `${p.account} ¥${money(p.value)}`).join(' + ') }}</span>
            </div>
            <p v-if="d.keep_hint" class="keep-hint terminal-label">→ {{ d.keep_hint }}</p>
          </template>
        </div>
        <div v-if="sameIndex.length" class="dup-group">
          <p class="terminal-label dup-group-label">同一指数 · 不同基金</p>
          <div class="table-row table-head dup-index-row">
            <span>指数</span><span>主题</span><span>合计现值</span><span>持有基金</span>
          </div>
          <template v-for="d in sameIndex" :key="d.index_code">
            <div class="table-row dup-index-row">
              <span class="dup-name">{{ d.index_name }}</span>
              <span>{{ d.theme }}</span>
              <span class="font-jet">¥{{ money(d.total_value) }}</span>
              <span class="terminal-label">{{ d.funds.map(f => `${f.fund_name}（${f.account}）`).join(' / ') }}</span>
            </div>
            <p v-if="d.keep_hint" class="keep-hint terminal-label">→ {{ d.keep_hint }}</p>
          </template>
        </div>
      </section>

      <!-- 碎片仓位 -->
      <section class="editorial-card">
        <div class="section-title">
          <h3 class="editorial-title">碎片仓位</h3>
          <span class="terminal-label">现值 < ¥{{ money(summary.fragment_threshold) }} · 共 {{ fragments.length }} 只</span>
        </div>
        <div class="table-row table-head frag-row">
          <span>基金</span><span>账户</span><span>主题</span><span>现值</span><span>盈亏</span><span>收益率</span>
        </div>
        <div v-for="f in fragments" :key="f.fund_code + f.account" class="table-row frag-row">
          <span class="dup-name">{{ f.fund_name }}</span>
          <span class="terminal-label">{{ f.account }}</span>
          <span>{{ f.theme }}</span>
          <span class="font-jet">¥{{ money(f.value) }}</span>
          <span class="font-jet" :class="pnlClass(f.pnl)">{{ pnlText(f.pnl) }}</span>
          <span class="font-jet" :class="pnlClass(f.pnl)">{{ pct(f.pnl_rate, 2) }}</span>
        </div>
        <p class="fragment-hint terminal-label">
          碎片仓位合计 ¥{{ money(summary.fragment_total_value) }}（占组合 {{ pct(fragmentShare, 1) }}），单只涨跌 20% 对组合影响不足 {{ summary.fragment_max_impact_pct }}%。
          建议设定「只出新不进新」规则：达回本线或止损线即退出，或加仓至有意义的规模
        </p>
      </section>
    </template>
  </div>
</template>

<style scoped>
.theme-page {
  padding: 1.5rem 2rem;
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}

.metric-strip {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px;
}

.chart-layout {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
  gap: 1rem;
}

.chart-card {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.bond-note {
  text-align: center;
  padding-top: 0.25rem;
}

.dup-card {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.dup-group-label {
  margin: 0 0 0.5rem;
}

.table-row {
  display: grid;
  grid-template-columns: minmax(160px, 2fr) 90px 110px 110px minmax(140px, 2fr);
  gap: 8px;
  align-items: center;
  padding: 8px 4px;
  border-bottom: 0.5px solid var(--color-border-tertiary, rgba(128, 128, 128, 0.15));
  font-size: 13px;
}

.dup-group:last-child .table-row:not(.table-head):last-child {
  border-bottom: none;
}

.dup-card > .table-row,
.dup-card .table-row.table-head {
  grid-template-columns: minmax(160px, 2fr) 90px 110px 110px minmax(140px, 2fr);
}

/* 同指数表只有 4 列（指数/主题/合计现值/持有基金） */
.dup-index-row {
  grid-template-columns: minmax(160px, 2fr) 90px 110px minmax(140px, 2fr);
}

.keep-hint {
  margin: 0 0 0.6rem;
  padding-left: 4px;
  line-height: 1.5;
}

.editorial-card:not(.dup-card) .table-row {
  grid-template-columns: minmax(160px, 2fr) 80px 90px 110px 110px 90px;
}

.table-head {
  font-size: 12px;
  border-bottom: 1px solid var(--color-border-tertiary, rgba(128, 128, 128, 0.15));
}

.dup-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 盈亏配色遵循 A 股习惯：盈利红、亏损绿 */
.pos { color: var(--color-profit, #dc2626); }
.neg { color: var(--color-loss, #059669); }
.warning { color: var(--color-text-warning, #ba7517); }
.danger { color: var(--color-text-danger, #c94f4f); }
.ok { color: var(--color-text-success, #2e9e6b); }

.fragment-hint {
  margin: 0.75rem 0 0;
  line-height: 1.6;
}

@media (max-width: 768px) {
  .theme-page {
    padding: 1rem;
  }
  .table-row {
    font-size: 12px;
  }
  /* 碎片：保留 基金/现值/盈亏（隐藏 账户/主题/收益率） */
  .frag-row {
    grid-template-columns: minmax(110px, 2fr) 1fr 1fr !important;
  }
  .frag-row > :nth-child(2),
  .frag-row > :nth-child(3),
  .frag-row > :nth-child(6) {
    display: none;
  }
  /* 跨账户：保留 基金/合计现值/合计盈亏（隐藏 主题/分布） */
  .dup-account-row {
    grid-template-columns: minmax(110px, 2fr) 1fr 1fr !important;
  }
  .dup-account-row > :nth-child(2),
  .dup-account-row > :nth-child(5) {
    display: none;
  }
  /* 同指数：保留 指数/合计现值/持有基金（隐藏 主题） */
  .dup-index-row {
    grid-template-columns: minmax(110px, 2fr) 1fr minmax(70px, 1.2fr) !important;
  }
  .dup-index-row > :nth-child(2) {
    display: none;
  }
}
</style>
