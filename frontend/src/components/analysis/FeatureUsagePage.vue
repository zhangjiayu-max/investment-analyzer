<script setup>
import { ref, computed, onMounted } from 'vue'
import { getFeatureUsageStats } from '../../api'

const loading = ref(true)
const stats = ref({
  total_sessions: 0,
  total_actions: 0,
  page_ranking: [],
  feature_ranking: [],
  daily_trend: [],
})
const days = ref(30)

async function loadData() {
  loading.value = true
  try {
    const { data } = await getFeatureUsageStats(days.value)
    stats.value = data || {}
  } catch (e) {
    console.error('加载功能使用统计失败:', e)
  } finally {
    loading.value = false
  }
}

onMounted(loadData)

// 页面标签中文名映射
const pageLabelMap = {
  dashboard: '每日看板',
  'market-intelligence': '市场热点',
  'event-radar': '机会雷达',
  bond: '债市分析',
  chat: 'AI 对话',
  portfolio: '持仓管理',
  'smart-add': '智能补仓',
  'alert-center': '风险与提示',
  decisions: '决策档案',
  attribution: '收益归因',
  behavior: '行为诊断',
  accuracy: '决策准确率',
  'allocation-dashboard': '配置偏离',
  'strategy-sandbox': '策略沙盒',
  'family-finance': '财务总览',
  'goal-buckets': '资金桶',
  articles: '文章管理',
  valuation: '估值数据',
  gallery: '估值图片',
  knowledge: '蒸馏知识',
  author: '作者文章',
  linked: '个人文档',
  rag: 'RAG 分析',
  'admin-agents': 'Agent 管理',
  'analysis-log': '分析记录',
  'feature-usage': '功能分析',
  'token-usage': 'Token 用量',
  'system-config': '系统配置',
  'data-health': '数据健康',
  'quality-dashboard': '质量仪表盘',
  'bad-cases': 'Bad Case',
  'eval-suite': '评测集',
  health: '健康分',
  'health-v2': '全账户诊断',
  shadow: 'Shadow Mode',
  'strategy-backtest': '策略回测',
  'capability-center': '能力中心',
}

function pageLabel(key) {
  return pageLabelMap[key] || key
}

function formatDuration(ms) {
  if (!ms || ms <= 0) return '—'
  if (ms < 60000) return `${Math.round(ms / 1000)}秒`
  const min = Math.floor(ms / 60000)
  const sec = Math.round((ms % 60000) / 1000)
  return sec > 0 ? `${min}分${sec}秒` : `${min}分`
}

// 页面排行最大值（用于条形图宽度）
const maxPageVisits = computed(() => {
  return Math.max(1, ...stats.value.page_ranking.map(p => p.visits || 0))
})

const maxFeatureClicks = computed(() => {
  return Math.max(1, ...stats.value.feature_ranking.map(f => f.clicks || 0))
})

// 每日趋势最大值
const maxDailyActions = computed(() => {
  return Math.max(1, ...stats.value.daily_trend.map(d => d.actions || 0))
})

// 趋势图SVG点坐标
const trendPoints = computed(() => {
  const trend = stats.value.daily_trend
  if (!trend.length) return ''
  const w = 100, h = 100
  const maxVal = maxDailyActions.value
  const step = trend.length > 1 ? w / (trend.length - 1) : 0
  return trend.map((d, i) => {
    const x = i * step
    const y = h - (d.actions / maxVal) * h * 0.9 - 5
    return `${x},${y}`
  }).join(' ')
})
</script>

<template>
  <div class="feature-usage-page bg-mesh">
    <!-- 页面标题 -->
    <div class="page-header">
      <h2 class="page-title editorial-title-lg">功能分析</h2>
      <span class="page-desc editorial-subtitle">功能使用频率与价值评估</span>
      <div class="global-range">
        <span class="global-range-label">统计范围</span>
        <select v-model="days" @change="loadData" class="global-range-select">
          <option :value="7">近 7 天</option>
          <option :value="30">近 30 天</option>
          <option :value="90">近 90 天</option>
        </select>
      </div>
      <button class="btn-outline btn-sm" style="margin-left: auto;" @click="loadData" :disabled="loading">
        <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
        刷新
      </button>
    </div>

    <!-- 概览卡片 -->
    <div class="stat-cards">
      <div class="stat-card">
        <div class="stat-label">总会话数</div>
        <div class="stat-value font-jet">{{ stats.total_sessions }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">总操作数</div>
        <div class="stat-value font-jet">{{ stats.total_actions }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">最常用页面</div>
        <div class="stat-value-sm">{{ stats.page_ranking.length ? pageLabel(stats.page_ranking[0].page_key) : '—' }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">最常用功能</div>
        <div class="stat-value-sm">{{ stats.feature_ranking.length ? stats.feature_ranking[0].feature_key : '—' }}</div>
      </div>
    </div>

    <div v-if="loading" class="loading-state">加载中...</div>

    <template v-else>
      <!-- 页面使用排行 -->
      <div class="section-card" v-if="stats.page_ranking.length">
        <h3 class="section-title">页面使用排行</h3>
        <div class="ranking-list">
          <div v-for="p in stats.page_ranking" :key="p.page_key" class="ranking-item">
            <span class="rank-label">{{ pageLabel(p.page_key) }}</span>
            <div class="rank-bar-wrap">
              <div class="rank-bar" :style="{ width: (p.visits / maxPageVisits * 100) + '%' }"></div>
            </div>
            <span class="rank-count font-jet">{{ p.visits }} 次</span>
            <span class="rank-duration">{{ formatDuration(p.avg_duration_ms) }}</span>
          </div>
        </div>
      </div>

      <!-- 功能点击排行 -->
      <div class="section-card" v-if="stats.feature_ranking.length">
        <h3 class="section-title">功能点击排行</h3>
        <div class="ranking-list">
          <div v-for="f in stats.feature_ranking.slice(0, 20)" :key="f.feature_key" class="ranking-item">
            <span class="rank-label">{{ f.feature_key }}</span>
            <div class="rank-bar-wrap">
              <div class="rank-bar rank-bar-blue" :style="{ width: (f.clicks / maxFeatureClicks * 100) + '%' }"></div>
            </div>
            <span class="rank-count font-jet">{{ f.clicks }} 次</span>
          </div>
        </div>
      </div>

      <!-- 每日趋势 -->
      <div class="section-card" v-if="stats.daily_trend.length">
        <h3 class="section-title">每日使用趋势</h3>
        <div class="trend-chart">
          <svg viewBox="0 0 100 100" preserveAspectRatio="none" class="trend-svg">
            <polyline :points="trendPoints" fill="none" stroke="var(--color-primary-500)" stroke-width="1.5" vector-effect="non-scaling-stroke"/>
          </svg>
          <div class="trend-labels">
            <span v-if="stats.daily_trend[0]">{{ stats.daily_trend[0].day }}</span>
            <span v-if="stats.daily_trend[stats.daily_trend.length-1]">{{ stats.daily_trend[stats.daily_trend.length-1].day }}</span>
          </div>
        </div>
      </div>

      <!-- 空状态 -->
      <div v-if="!stats.page_ranking.length && !stats.feature_ranking.length" class="empty-state">
        <p>暂无使用数据</p>
        <p class="empty-hint">浏览各功能页面后，这里会展示使用统计</p>
      </div>
    </template>
  </div>
</template>

<style scoped>
.feature-usage-page {
  padding: 1.5rem;
  max-width: 1400px;
  margin: 0 auto;
}

.page-header {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 1.5rem;
  flex-wrap: wrap;
}
.page-title { margin: 0; font-size: 1.5rem; font-weight: 700; }
.page-desc { color: var(--color-text-secondary); font-size: 0.875rem; }

.global-range {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  margin-left: 1rem;
}
.global-range-label { font-size: 0.8rem; color: var(--color-text-secondary); }
.global-range-select {
  padding: 0.3rem 0.5rem;
  border-radius: var(--radius-sm);
  border: 1px solid var(--color-border-light);
  background: var(--color-bg-input);
  color: var(--color-text);
  font-size: 0.8rem;
}

.stat-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 1rem;
  margin-bottom: 1.5rem;
}
.stat-card {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  padding: 1rem 1.2rem;
}
.stat-label { font-size: 0.8rem; color: var(--color-text-secondary); margin-bottom: 0.4rem; }
.stat-value { font-size: 1.6rem; font-weight: 700; color: var(--color-text); }
.stat-value-sm { font-size: 1.1rem; font-weight: 600; color: var(--color-text); }

.section-card {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  padding: 1.2rem 1.5rem;
  margin-bottom: 1.2rem;
}
.section-title {
  font-size: 1rem;
  font-weight: 600;
  margin: 0 0 1rem 0;
  color: var(--color-text);
}

.ranking-list { display: flex; flex-direction: column; gap: 0.6rem; }
.ranking-item {
  display: grid;
  grid-template-columns: 120px 1fr 80px 80px;
  align-items: center;
  gap: 0.8rem;
  font-size: 0.875rem;
}
.rank-label { color: var(--color-text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.rank-bar-wrap {
  height: 22px;
  background: var(--color-bg-secondary);
  border-radius: var(--radius-sm);
  overflow: hidden;
}
.rank-bar {
  height: 100%;
  background: linear-gradient(90deg, var(--color-primary-400), var(--color-primary-600));
  border-radius: var(--radius-sm);
  transition: width 0.3s;
}
.rank-bar-blue {
  background: linear-gradient(90deg, #60a5fa, #2563eb);
}
.rank-count { text-align: right; color: var(--color-text); font-weight: 600; }
.rank-duration { color: var(--color-text-secondary); font-size: 0.8rem; text-align: right; }

.trend-chart { position: relative; }
.trend-svg { width: 100%; height: 150px; display: block; }
.trend-labels {
  display: flex;
  justify-content: space-between;
  font-size: 0.75rem;
  color: var(--color-text-secondary);
  margin-top: 0.4rem;
}

.loading-state, .empty-state {
  text-align: center;
  padding: 3rem;
  color: var(--color-text-secondary);
}
.empty-hint { font-size: 0.8rem; margin-top: 0.5rem; }

@media (max-width: 768px) {
  .feature-usage-page { padding: 1rem; }
  .ranking-item {
    grid-template-columns: 90px 1fr 60px;
  }
  .rank-duration { display: none; }
}
</style>
