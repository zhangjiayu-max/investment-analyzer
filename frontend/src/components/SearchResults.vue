<!-- 全局搜索结果页 -->
<script setup>
import { ref, watch, computed } from 'vue'
import { globalSearch } from '../api'
import EmptyState from './ui/EmptyState.vue'

const props = defineProps({
  query: { type: String, default: '' },
})

const emit = defineEmits(['navigate'])

const loading = ref(false)
const results = ref({ knowledge: [], funds: [], valuations: [] })
const error = ref('')

const totalCount = computed(() =>
  results.value.knowledge.length + results.value.funds.length + results.value.valuations.length
)

const hasResults = computed(() => totalCount.value > 0)

// 知识库内容类型映射
const knowledgeTypeMap = {
  article: { label: '文章' },
  book: { label: '书籍' },
  concept: { label: '概念' },
  strategy: { label: '策略' },
  report: { label: '报告' },
}

// 估值百分位等级
function percentileLevel(p) {
  if (p <= 20) return { text: '低估', color: 'var(--color-success)' }
  if (p <= 40) return { text: '偏低', color: 'var(--color-info)' }
  if (p <= 60) return { text: '适中', color: 'var(--color-text-secondary)' }
  if (p <= 80) return { text: '偏高', color: 'var(--color-warning)' }
  return { text: '高估', color: 'var(--color-danger)' }
}

// 知识库结果跳转目标
function knowledgePage(item) {
  if (item.content_type === 'article') return 'articles'
  if (item.content_type === 'book' || item.content_type === 'concept' || item.content_type === 'strategy') return 'knowledge'
  return 'knowledge'
}

// 高亮搜索词
function highlightText(text, query) {
  if (!text || !query) return text
  const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return text.replace(new RegExp(`(${escaped})`, 'gi'), '<mark>$1</mark>')
}

async function doSearch(q) {
  if (!q || !q.trim()) {
    results.value = { knowledge: [], funds: [], valuations: [] }
    error.value = ''
    return
  }
  loading.value = true
  error.value = ''
  try {
    const { data } = await globalSearch(q.trim())
    if (data?.ok !== false) {
      results.value = {
        knowledge: data.data?.knowledge || [],
        funds: data.data?.funds || [],
        valuations: data.data?.valuations || [],
      }
    } else {
      error.value = data?.error || '搜索失败'
    }
  } catch (e) {
    error.value = e.message || '搜索请求失败'
    results.value = { knowledge: [], funds: [], valuations: [] }
  } finally {
    loading.value = false
  }
}

watch(() => props.query, (q) => { doSearch(q) }, { immediate: true })
</script>

<template>
  <div class="search-results-page bg-mesh">
    <!-- 页头 -->
    <div class="page-header">
      <h1 class="page-title editorial-title-lg">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/>
        </svg>
        搜索结果
      </h1>
      <p v-if="query && !loading" class="page-subtitle">
        关于「<strong>{{ query }}</strong>」共找到 <span class="font-jet">{{ totalCount }}</span> 条结果
      </p>
    </div>

    <!-- Loading -->
    <div v-if="loading" class="loading-container">
      <div class="spinner"></div>
      <p class="loading-text">正在搜索...</p>
    </div>

    <!-- Error -->
    <div v-else-if="error" class="error-banner">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="12" r="10"/><path d="M12 8v4m0 4h.01"/>
      </svg>
      <span>{{ error }}</span>
    </div>

    <!-- 空状态 -->
    <EmptyState
      v-else-if="!hasResults && query"
      icon="search"
      title="未找到相关结果"
      :description="`没有找到与「${query}」相关的内容，请尝试其他关键词`"
    />

    <!-- 未输入查询 -->
    <EmptyState
      v-else-if="!query"
      icon="search"
      title="输入关键词开始搜索"
      description="搜索知识库、基金、估值指数等"
    />

    <!-- 结果列表 -->
    <template v-else>
      <!-- 知识库 -->
      <section v-if="results.knowledge.length" class="result-group">
        <div class="group-header editorial-card-header">
          <h2 class="group-title title">知识库</h2>
          <span class="group-count font-jet">{{ results.knowledge.length }}</span>
        </div>
        <div class="result-cards">
          <div
            v-for="item in results.knowledge"
            :key="item.reference_id"
            class="result-card editorial-card reveal-stagger"
            @click="emit('navigate', knowledgePage(item))"
          >
            <div class="card-top">
              <span class="type-badge terminal-label">
                {{ knowledgeTypeMap[item.content_type]?.label || item.content_type }}
              </span>
            </div>
            <h3
              class="card-title"
              v-html="highlightText(item.title, query)"
            />
            <p
              v-if="item.body"
              class="card-body"
              v-html="highlightText(item.body.slice(0, 200), query)"
            />
            <div class="card-action">
              <span>查看详情</span>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M5 12h14m-7-7l7 7-7 7"/>
              </svg>
            </div>
          </div>
        </div>
      </section>

      <!-- 基金 -->
      <section v-if="results.funds.length" class="result-group">
        <div class="group-header editorial-card-header">
          <h2 class="group-title title">基金持仓</h2>
          <span class="group-count font-jet">{{ results.funds.length }}</span>
        </div>
        <div class="result-cards">
          <div
            v-for="item in results.funds"
            :key="item.fund_code"
            class="result-card fund-card editorial-card reveal-stagger"
            @click="emit('navigate', 'portfolio')"
          >
            <div class="fund-main">
              <div class="fund-info">
                <h3
                  class="card-title"
                  v-html="highlightText(item.fund_name, query)"
                />
                <span class="fund-code font-jet">{{ item.fund_code }}</span>
              </div>
              <div class="fund-values">
                <div class="fund-value-item">
                  <span class="value-label terminal-label">持有份额</span>
                  <span class="value-num">{{ Number(item.shares).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) }}</span>
                </div>
                <div class="fund-value-item">
                  <span class="value-label terminal-label">最新净值</span>
                  <span class="value-num">{{ Number(item.current_nav).toFixed(4) }}</span>
                </div>
                <div class="fund-value-item">
                  <span class="value-label terminal-label">市值</span>
                  <span class="value-num value-highlight">
                    {{ Number(item.current_value).toLocaleString('zh-CN', { style: 'currency', currency: 'CNY' }) }}
                  </span>
                </div>
              </div>
            </div>
            <div class="card-action">
              <span>查看持仓</span>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M5 12h14m-7-7l7 7-7 7"/>
              </svg>
            </div>
          </div>
        </div>
      </section>

      <!-- 估值 -->
      <section v-if="results.valuations.length" class="result-group">
        <div class="group-header editorial-card-header">
          <h2 class="group-title title">指数估值</h2>
          <span class="group-count font-jet">{{ results.valuations.length }}</span>
        </div>
        <div class="result-cards">
          <div
            v-for="item in results.valuations"
            :key="item.index_code"
            class="result-card valuation-card editorial-card reveal-stagger"
            @click="emit('navigate', 'valuation')"
          >
            <div class="valuation-main">
              <div class="valuation-info">
                <h3
                  class="card-title"
                  v-html="highlightText(item.index_name, query)"
                />
                <span class="index-code font-jet">{{ item.index_code }}</span>
              </div>
              <div class="valuation-values">
                <div class="valuation-item">
                  <span class="value-label terminal-label">估值</span>
                  <span class="value-num">{{ Number(item.current_value).toFixed(2) }}</span>
                </div>
                <div class="valuation-item">
                  <span class="value-label terminal-label">百分位</span>
                  <div class="percentile-wrap">
                    <span
                      class="percentile-badge"
                      :style="{ color: percentileLevel(item.percentile).color, backgroundColor: percentileLevel(item.percentile).color + '14' }"
                    >
                      {{ percentileLevel(item.percentile).text }}
                    </span>
                    <span class="value-num">{{ Number(item.percentile).toFixed(1) }}%</span>
                  </div>
                </div>
                <div v-if="item.snapshot_date" class="valuation-item">
                  <span class="value-label terminal-label">更新日期</span>
                  <span class="value-num value-muted font-jet">{{ item.snapshot_date }}</span>
                </div>
              </div>
            </div>
            <div class="card-action">
              <span>查看估值详情</span>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M5 12h14m-7-7l7 7-7 7"/>
              </svg>
            </div>
          </div>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.search-results-page {
  padding: var(--space-6);
  max-width: 960px;
  margin: 0 auto;
}

/* ── 页头 ── */
.page-header {
  margin-bottom: var(--space-6);
}

.page-title {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: inherit;
  font-weight: inherit;
  color: var(--color-text-primary);
  margin: 0 0 var(--space-1);
}

.page-subtitle {
  font-size: 0.875rem;
  color: var(--color-text-secondary);
  margin: 0;
}

.page-subtitle strong {
  color: var(--color-primary);
}

/* ── Loading ── */
.loading-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 4rem 0;
  gap: var(--space-3);
}

.spinner {
  width: var(--spinner-lg);
  height: var(--spinner-lg);
  border: 3px solid var(--color-border-light);
  border-top-color: var(--color-primary);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.loading-text {
  font-size: 0.875rem;
  color: var(--color-text-muted);
  margin: 0;
}

/* ── Error ── */
.error-banner {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  background: var(--color-danger-bg);
  border: 1px solid var(--color-danger-border);
  border-radius: var(--radius-md);
  color: var(--color-danger);
  font-size: 0.875rem;
}

/* ── 结果分组 ── */
.result-group {
  margin-bottom: var(--space-8);
}

.group-header {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-4);
  padding-bottom: var(--space-3);
  border-bottom: 1px solid var(--color-border-light);
}

.group-icon {
  font-size: 1.125rem;
}

.group-title {
  font-size: 1rem;
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0;
}

.group-count {
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--color-primary);
  background: var(--color-primary-bg);
  padding: 1px 8px;
  border-radius: var(--radius-xl);
}

/* ── 结果卡片 ── */
.result-cards {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.result-card {
  background: var(--glass-bg);
  backdrop-filter: blur(var(--glass-blur));
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-lg);
  padding: var(--space-4) var(--space-5);
  cursor: pointer;
  transition: all var(--transition-fast);
}

.result-card:hover {
  border-color: var(--color-primary-border);
  box-shadow: var(--shadow-md), var(--shadow-glow);
  transform: var(--hover-lift);
}

.card-top {
  margin-bottom: var(--space-2);
}

.type-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 0.75rem;
  font-weight: 500;
  color: var(--color-text-secondary);
  background: var(--color-bg-secondary);
  padding: 2px 10px;
  border-radius: var(--radius-xl);
}

.card-title {
  font-size: 0.9375rem;
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0 0 var(--space-1);
  line-height: 1.5;
}

.card-title :deep(mark) {
  background: rgba(37, 99, 235, 0.15);
  color: var(--color-primary);
  border-radius: 2px;
  padding: 0 2px;
}

.card-body {
  font-size: 0.8125rem;
  color: var(--color-text-secondary);
  line-height: 1.6;
  margin: 0 0 var(--space-2);
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.card-body :deep(mark) {
  background: rgba(37, 99, 235, 0.15);
  color: var(--color-primary);
  border-radius: 2px;
  padding: 0 2px;
}

.card-action {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 0.75rem;
  font-weight: 500;
  color: var(--color-primary);
  opacity: 0;
  transition: opacity var(--transition-fast);
}

.result-card:hover .card-action {
  opacity: 1;
}

/* ── 基金卡片 ── */
.fund-main {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.fund-info {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.fund-code {
  font-size: 0.75rem;
  font-family: var(--font-mono);
  color: var(--color-text-muted);
  background: var(--color-bg-secondary);
  padding: 1px 6px;
  border-radius: var(--radius-xs);
}

.fund-values {
  display: flex;
  gap: var(--space-6);
  flex-wrap: wrap;
}

.fund-value-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.value-label {
  font-size: 0.6875rem;
  color: var(--color-text-muted);
}

.value-num {
  font-size: 0.875rem;
  font-family: var(--font-num);
  font-weight: 600;
  color: var(--color-text-primary);
}

.value-highlight {
  color: var(--color-primary);
}

.value-muted {
  color: var(--color-text-muted);
  font-weight: 400;
}

/* ── 估值卡片 ── */
.valuation-main {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.valuation-info {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.index-code {
  font-size: 0.75rem;
  font-family: var(--font-mono);
  color: var(--color-text-muted);
  background: var(--color-bg-secondary);
  padding: 1px 6px;
  border-radius: var(--radius-xs);
}

.valuation-values {
  display: flex;
  gap: var(--space-6);
  flex-wrap: wrap;
}

.valuation-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.percentile-wrap {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.percentile-badge {
  font-size: 0.6875rem;
  font-weight: 600;
  padding: 1px 8px;
  border-radius: var(--radius-xl);
}
</style>
