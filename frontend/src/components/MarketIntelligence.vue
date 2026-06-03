<template>
  <div class="intel-page">
    <div class="page-header">
      <div>
        <h2 class="page-title">🔥 市场热点情报</h2>
        <p class="page-desc">基于新闻、时事、宏观环境，AI 推断热门板块与投资机会</p>
      </div>
      <div class="header-actions">
        <span v-if="data?.fetched_at" class="data-time">{{ data.fetched_at }}</span>
        <button class="btn-primary" :disabled="loading" @click="loadData(true)">
          <svg v-if="!loading" width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
          </svg>
          <span>{{ loading ? '分析中...' : '刷新分析' }}</span>
        </button>
      </div>
    </div>

    <!-- Loading -->
    <div v-if="loading && !data" class="loading-state">
      <div class="spinner-lg"></div>
      <span>正在聚合多源数据并分析...</span>
    </div>

    <div v-else-if="data">
      <!-- 综合研判 -->
      <div v-if="data.summary" class="card summary-card">
        <div class="card-header">
          <h3>🧠 综合研判</h3>
        </div>
        <p class="summary-text">{{ data.summary }}</p>
      </div>

      <!-- 主体：左侧板块排行 + 右侧详情 -->
      <div class="intel-main">
        <!-- 左侧：热门板块排行 -->
        <div class="card sectors-panel">
          <div class="card-header">
            <h3>🔥 热门板块</h3>
            <span class="badge">{{ data.sectors?.length || 0 }}</span>
          </div>
          <div v-if="data.sectors?.length" class="sector-list">
            <div
              v-for="(s, i) in data.sectors"
              :key="i"
              class="sector-item"
              :class="{ active: selectedSector === i }"
              @click="selectedSector = i"
            >
              <span class="sector-rank" :class="'heat-' + (s.heat || 'low')">{{ i + 1 }}</span>
              <span class="sector-name">{{ s.name }}</span>
              <span class="sector-heat-tag" :class="'heat-' + (s.heat || 'low')">
                {{ heatLabel(s.heat) }}
              </span>
              <span class="sector-outlook" :class="'outlook-' + (s.outlook || '').replace(/[利好利空中性]/g, m => outlookMap[m])">
                {{ s.outlook || '-' }}
              </span>
            </div>
          </div>
          <div v-else class="empty-state" style="padding:1.5rem">
            <p>暂无板块分析</p>
          </div>
        </div>

        <!-- 右侧：选中板块详情 -->
        <div v-if="activeSector" class="card detail-panel">
          <div class="card-header">
            <h3>{{ activeSector.name }} <span class="detail-heat" :class="'heat-' + (activeSector.heat || 'low')">{{ heatLabel(activeSector.heat) }}</span></h3>
            <span class="detail-outlook" :class="'outlook-' + (activeSector.outlook || '').replace(/[利好利空中性]/g, m => outlookMap[m])">
              {{ activeSector.outlook || '' }}
            </span>
          </div>

          <!-- 催化剂 -->
          <div v-if="activeSector.catalysts?.length" class="detail-section">
            <h4>📰 催化剂</h4>
            <ul class="catalyst-list">
              <li v-for="(c, ci) in activeSector.catalysts" :key="ci">{{ c }}</li>
            </ul>
          </div>

          <!-- 推断逻辑 -->
          <div v-if="activeSector.reason" class="detail-section">
            <h4>💡 投资逻辑</h4>
            <p class="reason-text">{{ activeSector.reason }}</p>
          </div>

          <!-- 关联指数 -->
          <div class="detail-section">
            <h4>📊 关联指数</h4>
            <div v-if="activeSector.related_indexes?.length" class="index-grid">
              <div v-for="idx in activeSector.related_indexes" :key="idx.index_code" class="index-card">
                <div class="index-name">{{ idx.index_name }}</div>
                <div class="index-metric">
                  <span>{{ idx.metric_type || 'PE' }}: {{ idx.current_value }}</span>
                </div>
                <div class="index-pct" :style="{ color: pctColor(idx.percentile) }">
                  百分位 {{ idx.percentile != null ? idx.percentile.toFixed(0) + '%' : 'N/A' }}
                </div>
              </div>
            </div>
            <p v-else class="text-muted">暂无跟踪指数匹配该板块</p>
          </div>

          <!-- 关联基金 -->
          <div class="detail-section">
            <h4>💰 关联持仓</h4>
            <div v-if="activeSector.related_funds?.length" class="fund-list">
              <div v-for="f in activeSector.related_funds" :key="f.fund_code" class="fund-item">
                <span class="fund-name">{{ f.fund_name }}</span>
                <span class="fund-tag">已持仓</span>
                <span v-if="f.profit_rate != null" class="fund-pct" :class="f.profit_rate >= 0 ? 'text-success' : 'text-danger'">
                  {{ (f.profit_rate * 100).toFixed(1) }}%
                </span>
              </div>
            </div>
            <p v-else class="text-muted">当前无持仓涉及该板块</p>
          </div>

          <!-- 关联新闻 -->
          <div v-if="activeSector.catalysts?.length" class="detail-section">
            <h4>📰 相关新闻</h4>
            <div class="catalyst-news">
              <div v-for="(c, ci) in activeSector.catalysts" :key="ci" class="catalyst-item">
                <span class="catalyst-bullet">•</span>
                <span>{{ c }}</span>
              </div>
            </div>
          </div>

          <!-- 匹配关键词 -->
          <div v-if="activeSector.keywords?.length" class="detail-section">
            <h4>🏷️ 匹配关键词</h4>
            <div class="keyword-tags">
              <span v-for="kw in activeSector.keywords" :key="kw" class="kw-tag">{{ kw }}</span>
            </div>
          </div>
        </div>

        <div v-else class="card detail-panel empty-detail">
          <p>← 点击左侧板块查看详情</p>
        </div>
      </div>

      <!-- 今日要闻 -->
      <div v-if="data.news?.length" class="card">
        <div class="card-header">
          <h3>📰 今日要闻</h3>
          <span class="badge">{{ data.news.length }}</span>
        </div>
        <div class="news-list">
          <div v-for="(n, i) in data.news" :key="i" class="news-item">
            <a v-if="n.url" :href="n.url" target="_blank" rel="noopener" class="news-title">{{ n.title }}</a>
            <span v-else class="news-title">{{ n.title }}</span>
            <p class="news-summary">{{ n.summary?.slice(0, 120) }}{{ n.summary?.length > 120 ? '...' : '' }}</p>
            <div class="news-meta">
              <span>{{ n.source }}</span>
              <span v-if="n.date">{{ n.date?.slice(0, 10) }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 宏观环境 -->
      <div v-if="data.macro" class="card">
        <div class="card-header">
          <h3>📊 宏观环境</h3>
        </div>
        <div class="macro-grid">
          <div v-if="data.macro.bond?.temperature != null" class="macro-item">
            <span class="macro-label">债券温度</span>
            <span class="macro-value">{{ data.macro.bond.temperature }}°</span>
          </div>
          <div v-if="data.macro.bond?.rate" class="macro-item">
            <span class="macro-label">十年期国债</span>
            <span class="macro-value">{{ data.macro.bond.rate }}%</span>
          </div>
          <div v-if="data.macro.policy?.lpr" class="macro-item">
            <span class="macro-label">LPR (1Y/5Y)</span>
            <span class="macro-value">{{ data.macro.policy.lpr['1y'] || '-' }} / {{ data.macro.policy.lpr['5y'] || '-' }}</span>
          </div>
          <div v-if="data.macro.policy?.cpi?.latest" class="macro-item">
            <span class="macro-label">CPI</span>
            <span class="macro-value">{{ data.macro.policy.cpi.latest }}%</span>
          </div>
        </div>
      </div>

      <!-- 热门话题 -->
      <div v-if="data.hot_topics?.length" class="card">
        <div class="card-header">
          <h3>💬 热门话题</h3>
          <span class="badge">{{ data.hot_topics.length }}</span>
        </div>
        <div class="topics-list">
          <div v-for="(t, i) in data.hot_topics" :key="i" class="topic-item">
            <span class="topic-title">{{ t.title }}</span>
            <span v-if="t.summary" class="topic-summary">{{ t.summary?.slice(0, 80) }}</span>
          </div>
        </div>
      </div>
    </div>

    <div v-else-if="!loading" class="empty-state">
      <p>点击「刷新分析」获取市场热点情报</p>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { getMarketIntelligenceOverview } from '../api'

const loading = ref(false)
const data = ref(null)
const selectedSector = ref(0)

const outlookMap = { '利好': 'good', '利空': 'bad', '中性': 'neutral' }

const activeSector = computed(() => {
  if (!data.value?.sectors?.length) return null
  return data.value.sectors[selectedSector.value] || null
})

onMounted(() => {
  loadData()
})

async function loadData(force = false) {
  loading.value = true
  try {
    const { data: res } = await getMarketIntelligenceOverview(force)
    data.value = res
    selectedSector.value = 0
  } catch (e) {
    console.error('市场情报加载失败:', e)
  } finally {
    loading.value = false
  }
}

function heatLabel(heat) {
  if (heat === 'high') return '🔴 高热'
  if (heat === 'medium') return '🟡 温热'
  return '🟢 平淡'
}

function pctColor(pct) {
  if (pct == null) return 'var(--color-text-muted)'
  if (pct < 30) return '#16a34a'
  if (pct < 70) return 'var(--color-text-secondary)'
  return '#dc2626'
}
</script>

<style scoped>
.intel-page {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
}

.page-title {
  font-size: 1.5rem;
  font-weight: 700;
  color: var(--color-text-primary);
}

.page-desc {
  font-size: 0.85rem;
  color: var(--color-text-muted);
  margin-top: 0.25rem;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.data-time {
  font-size: 0.75rem;
  color: var(--color-text-muted);
}

.btn-primary {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.5rem 1rem;
  background: var(--color-primary-500);
  color: white;
  border: none;
  border-radius: var(--radius-md);
  font-size: 0.8rem;
  font-weight: 500;
  cursor: pointer;
  transition: opacity 0.2s;
}

.btn-primary:hover { opacity: 0.9; }
.btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }

/* ── 综合研判 ── */

.summary-card {
  padding: 1.25rem;
}

.summary-text {
  font-size: 0.95rem;
  line-height: 1.7;
  color: var(--color-text-secondary);
  margin-top: 0.5rem;
}

/* ── 主体布局 ── */

.intel-main {
  display: grid;
  grid-template-columns: 320px 1fr;
  gap: 1rem;
  align-items: start;
}

/* ── 板块排行 ── */

.sectors-panel {
  padding: 1rem;
}

.sector-list {
  display: flex;
  flex-direction: column;
}

.sector-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.65rem 0.5rem;
  border-bottom: 1px solid var(--color-border-light);
  cursor: pointer;
  border-radius: var(--radius-sm);
  transition: background 0.15s;
}

.sector-item:last-child { border-bottom: none; }
.sector-item:hover { background: var(--color-bg-secondary); }
.sector-item.active { background: var(--color-primary-50); border-left: 3px solid var(--color-primary-500); }

.sector-rank {
  width: 22px;
  height: 22px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  font-size: 0.7rem;
  font-weight: 700;
  color: white;
}

.sector-rank.heat-high { background: #ef4444; }
.sector-rank.heat-medium { background: #f59e0b; }
.sector-rank.heat-low { background: #6b7280; }

.sector-name {
  flex: 1;
  font-weight: 600;
  font-size: 0.85rem;
}

.sector-heat-tag {
  font-size: 0.65rem;
  padding: 0.1rem 0.4rem;
  border-radius: 4px;
}

.sector-heat-tag.heat-high { background: #fef2f2; color: #dc2626; }
.sector-heat-tag.heat-medium { background: #fffbeb; color: #d97706; }
.sector-heat-tag.heat-low { background: #f3f4f6; color: #6b7280; }

.sector-outlook {
  font-size: 0.7rem;
  font-weight: 600;
  padding: 0.1rem 0.4rem;
  border-radius: 4px;
}

.outlook-good { background: #ecfdf5; color: #059669; }
.outlook-bad { background: #fef2f2; color: #dc2626; }
.outlook-neutral { background: #f3f4f6; color: #6b7280; }

/* ── 详情面板 ── */

.detail-panel {
  padding: 1.25rem;
}

.detail-panel .card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 1rem;
}

.detail-panel .card-header h3 {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.detail-heat {
  font-size: 0.7rem;
  font-weight: 500;
}

.detail-outlook {
  font-size: 0.8rem;
  font-weight: 600;
  padding: 0.2rem 0.6rem;
  border-radius: 4px;
}

.detail-section {
  margin-bottom: 1.25rem;
}

.detail-section h4 {
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--color-text-muted);
  margin-bottom: 0.5rem;
}

.catalyst-list {
  list-style: none;
  padding: 0;
  margin: 0;
}

.catalyst-list li {
  font-size: 0.85rem;
  color: var(--color-text-secondary);
  padding: 0.35rem 0;
  border-bottom: 1px solid var(--color-border-light);
}

.catalyst-list li:last-child { border-bottom: none; }

.reason-text {
  font-size: 0.85rem;
  line-height: 1.6;
  color: var(--color-text-secondary);
}

.catalyst-news {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}

.catalyst-item {
  display: flex;
  gap: 0.4rem;
  font-size: 0.8rem;
  color: var(--color-text-secondary);
  line-height: 1.5;
}

.catalyst-bullet {
  color: var(--color-primary-500);
  font-weight: 700;
  flex-shrink: 0;
}

.keyword-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
}

.kw-tag {
  font-size: 0.7rem;
  padding: 0.15rem 0.5rem;
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border);
  border-radius: 10px;
  color: var(--color-text-muted);
}

.index-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 0.5rem;
}

.index-card {
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  padding: 0.65rem 0.75rem;
}

.index-name {
  font-size: 0.8rem;
  font-weight: 600;
  margin-bottom: 0.25rem;
}

.index-metric {
  font-size: 0.75rem;
  color: var(--color-text-muted);
}

.index-pct {
  font-size: 0.8rem;
  font-weight: 700;
  margin-top: 0.25rem;
}

.fund-list {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}

.fund-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.4rem 0;
}

.fund-name {
  flex: 1;
  font-size: 0.85rem;
  font-weight: 500;
}

.fund-tag {
  font-size: 0.65rem;
  background: var(--color-primary-50);
  color: var(--color-primary-600);
  padding: 0.1rem 0.4rem;
  border-radius: 4px;
}

.fund-pct {
  font-size: 0.8rem;
  font-weight: 600;
}

.empty-detail {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 200px;
  color: var(--color-text-muted);
}

/* ── 新闻列表 ── */

.news-list {
  display: flex;
  flex-direction: column;
}

.news-item {
  padding: 0.75rem 0;
  border-bottom: 1px solid var(--color-border-light);
}

.news-item:last-child { border-bottom: none; }

.news-title {
  font-size: 0.9rem;
  font-weight: 500;
  color: var(--color-primary-600);
  text-decoration: none;
}

.news-title:hover { text-decoration: underline; }

.news-summary {
  font-size: 0.8rem;
  color: var(--color-text-secondary);
  margin-top: 0.25rem;
  line-height: 1.5;
}

.news-meta {
  display: flex;
  gap: 0.75rem;
  font-size: 0.7rem;
  color: var(--color-text-muted);
  margin-top: 0.35rem;
}

/* ── 宏观环境 ── */

.macro-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 0.75rem;
}

.macro-item {
  text-align: center;
  padding: 0.75rem;
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
}

.macro-label {
  display: block;
  font-size: 0.7rem;
  color: var(--color-text-muted);
  margin-bottom: 0.35rem;
}

.macro-value {
  display: block;
  font-size: 1.2rem;
  font-weight: 700;
  color: var(--color-primary-500);
}

/* ── 热门话题 ── */

.topics-list {
  display: flex;
  flex-direction: column;
}

.topic-item {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  padding: 0.5rem 0;
  border-bottom: 1px solid var(--color-border-light);
}

.topic-item:last-child { border-bottom: none; }

.topic-title {
  font-size: 0.85rem;
  font-weight: 500;
}

.topic-summary {
  font-size: 0.75rem;
  color: var(--color-text-muted);
}

/* ── Utilities ── */

.badge {
  font-size: 0.7rem;
  padding: 0.15rem 0.5rem;
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border);
  border-radius: 10px;
  color: var(--color-text-muted);
}

.text-success { color: #16a34a; }
.text-danger { color: #dc2626; }
.text-muted { color: var(--color-text-muted); font-size: 0.8rem; }

.loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.75rem;
  padding: 3rem;
  color: var(--color-text-muted);
}

.empty-state {
  text-align: center;
  padding: 2rem;
  color: var(--color-text-muted);
}

/* ── Responsive ── */

@media (max-width: 768px) {
  .intel-main {
    grid-template-columns: 1fr;
  }
  .page-header {
    flex-direction: column;
  }
  .macro-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}
</style>
