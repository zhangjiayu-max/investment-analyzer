<script setup>
import { computed } from 'vue'
import { marked } from 'marked'

const props = defineProps({ result: Object })

const article = computed(() => props.result?.article || {})
const codesFound = computed(() => props.result?.codes_found || [])
const marketData = computed(() => props.result?.market_data || {})
const llmAnalysis = computed(() => props.result?.llm_analysis || '')

const renderedAnalysis = computed(() => {
  if (!llmAnalysis.value) return ''
  return marked(llmAnalysis.value)
})
</script>

<template>
  <div class="analysis-result">
    <!-- Article Card -->
    <div class="card">
      <div class="card-header-row">
        <div>
          <h3 class="article-title">{{ article.title || '未知标题' }}</h3>
          <div class="article-meta">
            <span v-if="article.author">{{ article.author }}</span>
            <span v-if="article.publish_time">{{ article.publish_time }}</span>
          </div>
        </div>
        <span class="badge badge-success">已识别 {{ codesFound.length }} 个标的</span>
      </div>
    </div>

    <!-- Codes -->
    <div v-if="codesFound.length" class="card">
      <h3 class="section-title">识别到的投资标的</h3>
      <div class="codes-grid">
        <div v-for="code in codesFound" :key="code" class="code-item">
          <div>
            <div class="code-name">{{ marketData[code]?.name || '未知' }}</div>
            <div class="code-id">{{ code }}</div>
          </div>
          <div v-if="marketData[code]?.recommendation">
            <span
              :class="[
                'badge',
                marketData[code].recommendation.includes('低估') ? 'badge-success' :
                marketData[code].recommendation.includes('合理') ? 'badge-warning' :
                'badge-danger'
              ]"
            >{{ marketData[code].recommendation }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- LLM Analysis -->
    <div v-if="llmAnalysis" class="card">
      <h3 class="section-title">AI 投资分析</h3>
      <div class="prose" v-html="renderedAnalysis"></div>
    </div>
  </div>
</template>

<style scoped>
.analysis-result {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.card {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
  padding: 1.25rem;
}

.card-header-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
}

.article-title {
  font-size: 1.05rem;
  font-weight: 700;
  color: var(--color-text-primary);
  margin: 0;
}

.article-meta {
  display: flex;
  gap: 1rem;
  margin-top: 0.35rem;
  font-size: 0.8rem;
  color: var(--color-text-muted);
}

.section-title {
  font-size: 0.95rem;
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0 0 0.75rem 0;
}

.codes-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 0.75rem;
}

.code-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.75rem;
  background: var(--color-bg-input);
  border-radius: var(--radius-md);
}

.code-name {
  font-weight: 600;
  font-size: 0.875rem;
  color: var(--color-text-primary);
}

.code-id {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  margin-top: 0.1rem;
}

.badge {
  display: inline-flex;
  align-items: center;
  padding: 0.2rem 0.5rem;
  border-radius: var(--radius-sm);
  font-size: 0.72rem;
  font-weight: 500;
}

.badge-success { background: rgba(16, 185, 129, 0.1); color: #059669; }
.badge-warning { background: rgba(245, 158, 11, 0.1); color: #d97706; }
.badge-danger { background: rgba(239, 68, 68, 0.1); color: #dc2626; }

/* 移动端适配 */
@media (max-width: 768px) {
  .card {
    padding: 1rem;
  }

  .card-header-row {
    flex-direction: column;
    align-items: flex-start;
    gap: 0.75rem;
  }

  .codes-grid {
    grid-template-columns: 1fr;
    gap: 0.5rem;
  }

  .code-item {
    padding: 0.65rem;
  }

  .article-title {
    font-size: 1rem;
  }
}
</style>
