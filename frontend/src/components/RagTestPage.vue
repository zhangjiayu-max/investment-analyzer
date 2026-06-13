<script setup>
import { ref } from 'vue'
import { testRagSearch } from '../api'
import { useToast } from '../composables/useToast'

const { showToast } = useToast()

// 知识类型选项
const typeOptions = [
  { key: 'all', label: '全部', icon: '🔍' },
  { key: 'book', label: '蒸馏书籍', icon: '📖' },
  { key: 'author_article', label: '作者文章', icon: '✍️' },
  { key: 'linked_doc', label: '个人文档', icon: '📎' },
  { key: 'skill', label: '技能文档', icon: '🛠️' },
]

const activeType = ref('all')
const testQuery = ref('')
const testLimit = ref(5)
const useRewrite = ref(false)
const testLoading = ref(false)
const testResults = ref(null)
const elapsedMs = ref(0)

async function runTest() {
  const q = testQuery.value.trim()
  if (!q) return

  testLoading.value = true
  testResults.value = null
  elapsedMs.value = 0

  const contentTypes = activeType.value === 'all' ? null : [activeType.value]
  const t0 = Date.now()

  try {
    const { data } = await testRagSearch(q, testLimit.value, contentTypes, useRewrite.value)
    elapsedMs.value = Date.now() - t0
    testResults.value = data.result || data
  } catch (e) {
    elapsedMs.value = Date.now() - t0
    showToast('测试失败: ' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    testLoading.value = false
  }
}

function getSourceLabel(src) {
  return src === 'both' ? '双路' : src === 'fts' ? '全文' : '向量'
}

function getSourceClass(src) {
  return 'src-' + (src || 'default')
}

function getScorePercent(score) {
  return Math.min(Math.max((score || 0) * 500, 0), 100)
}
</script>

<template>
  <div class="rag-test-page">
    <div class="page-header">
      <h2 class="page-title">🎯 命中测试</h2>
      <p class="page-desc">测试 RAG 检索的命中效果和速度，验证不同知识源的召回质量</p>
    </div>

    <!-- 类型选择 -->
    <div class="type-selector">
      <button
        v-for="opt in typeOptions"
        :key="opt.key"
        :class="['type-chip', { active: activeType === opt.key }]"
        @click="activeType = opt.key"
      >
        <span class="chip-icon">{{ opt.icon }}</span>
        <span class="chip-label">{{ opt.label }}</span>
      </button>
    </div>

    <!-- 搜索区域 -->
    <div class="search-area">
      <div class="search-row">
        <input
          v-model="testQuery"
          type="text"
          class="search-input"
          :placeholder="activeType === 'all' ? '输入查询词，如：安全边际、资产配置...' : `在${typeOptions.find(t => t.key === activeType)?.label || ''}中搜索...`"
          @keyup.enter="runTest"
        />
        <button class="btn-primary search-btn" :disabled="testLoading || !testQuery.trim()" @click="runTest">
          <span v-if="testLoading" class="btn-spinner"></span>
          {{ testLoading ? '检索中...' : '测试检索' }}
        </button>
      </div>
      <div class="search-options">
        <label class="option-toggle">
          <input type="checkbox" v-model="useRewrite" />
          <span class="toggle-label">Query Rewrite</span>
          <span class="toggle-hint">改写查询词以提高召回率</span>
        </label>
        <label class="option-limit">
          <span class="limit-label">返回条数</span>
          <select v-model="testLimit" class="limit-select">
            <option :value="3">3</option>
            <option :value="5">5</option>
            <option :value="10">10</option>
            <option :value="15">15</option>
          </select>
        </label>
      </div>
    </div>

    <!-- 结果区域 -->
    <div v-if="testResults" class="results-area">
      <!-- 诊断面板 -->
      <div class="diagnostics">
        <div class="diag-item diag-time">
          <span class="diag-value">{{ elapsedMs }}</span>
          <span class="diag-unit">ms</span>
        </div>
        <div class="diag-item">
          <span class="diag-value">{{ testResults.fts_count || 0 }}</span>
          <span class="diag-label">FTS5 命中</span>
        </div>
        <div class="diag-item">
          <span class="diag-value">{{ testResults.chroma_count || 0 }}</span>
          <span class="diag-label">向量命中</span>
        </div>
        <div v-if="testResults.timing?.source_breakdown" class="diag-item">
          <span class="diag-value">{{ testResults.timing.source_breakdown.both }}</span>
          <span class="diag-label">双路命中</span>
        </div>
        <div v-if="testResults.freshness_filtered" class="diag-item">
          <span class="diag-value">{{ testResults.freshness_filtered }}</span>
          <span class="diag-label">时效过滤</span>
        </div>
        <div class="diag-item">
          <span class="diag-value">{{ testResults.results?.length || 0 }}</span>
          <span class="diag-label">最终返回</span>
        </div>
      </div>

      <!-- Rewrite 信息 -->
      <div v-if="testResults.rewritten_query" class="rewrite-info">
        <span class="rewrite-label">Query Rewrite:</span>
        <span class="rewrite-original">"{{ testResults.original_query }}"</span>
        <span class="rewrite-arrow">→</span>
        <span class="rewrite-result">"{{ testResults.rewritten_query }}"</span>
      </div>

      <!-- 关键词 -->
      <div v-if="testResults.keywords?.length" class="keywords-bar">
        <span class="keywords-label">检索关键词：</span>
        <span v-for="kw in testResults.keywords" :key="kw" class="keyword-tag">{{ kw }}</span>
      </div>

      <!-- 结果列表 -->
      <div v-if="!testResults.results?.length" class="empty-results">
        <span class="empty-icon">📭</span>
        <p>无命中结果，试试换个查询词？</p>
      </div>

      <div v-else class="result-list">
        <div v-for="(r, i) in testResults.results" :key="i" class="result-card">
          <div class="result-top">
            <span class="result-rank">#{{ i + 1 }}</span>
            <span class="result-type">{{ r.label || r.content_type }}</span>
            <span :class="['result-source', getSourceClass(r.source)]">{{ getSourceLabel(r.source) }}</span>
            <span class="result-title">{{ r.title }}</span>
            <div class="result-score">
              <div class="score-bar">
                <div class="score-fill" :style="{ width: getScorePercent(r._score) + '%' }"></div>
              </div>
              <span class="score-num">{{ getScorePercent(r._score).toFixed(0) }}%</span>
            </div>
          </div>
          <pre class="result-body">{{ r.body?.slice(0, 400) }}{{ r.body?.length > 400 ? '...' : '' }}</pre>
        </div>
      </div>
    </div>

    <!-- 空状态 -->
    <div v-else-if="!testLoading" class="empty-state">
      <span class="empty-icon">🔬</span>
      <p class="empty-title">输入查询词开始测试</p>
      <p class="empty-hint">选择知识类型，输入查询词，查看检索命中效果</p>
    </div>
  </div>
</template>

<style scoped>
.rag-test-page {
  padding: 1.5rem;
  max-width: 1000px;
  margin: 0 auto;
}

.page-header {
  margin-bottom: 1.5rem;
}

.page-title {
  font-size: 1.25rem;
  font-weight: 700;
  margin: 0 0 0.25rem;
  color: var(--color-text-primary);
}

.page-desc {
  font-size: 0.85rem;
  color: var(--color-text-muted);
  margin: 0;
}

/* 类型选择器 */
.type-selector {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin-bottom: 1rem;
}

.type-chip {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  padding: 0.4rem 0.85rem;
  border: 1px solid var(--color-border);
  border-radius: 999px;
  background: var(--color-bg-card);
  color: var(--color-text-secondary);
  font-size: 0.85rem;
  cursor: pointer;
  transition: all 0.2s;
}

.type-chip:hover {
  border-color: var(--color-primary-300);
  color: var(--color-text-primary);
}

.type-chip.active {
  background: var(--color-primary-50);
  border-color: var(--color-primary-400);
  color: var(--color-primary-700);
  font-weight: 600;
}

.dark .type-chip.active {
  background: rgba(201, 168, 76, 0.12);
  color: var(--color-primary-300);
}

.chip-icon {
  font-size: 1rem;
}

/* 搜索区域 */
.search-area {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 1rem;
  margin-bottom: 1.5rem;
}

.search-row {
  display: flex;
  gap: 0.5rem;
}

.search-input {
  flex: 1;
  padding: 0.6rem 1rem;
  font-size: 0.9rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-input);
  color: var(--color-text-primary);
  transition: border-color 0.2s;
}

.search-input:focus {
  outline: none;
  border-color: var(--color-primary-400);
}

.search-btn {
  padding: 0.6rem 1.25rem;
  white-space: nowrap;
  display: flex;
  align-items: center;
  gap: 0.4rem;
}

.btn-spinner {
  display: inline-block;
  width: 14px;
  height: 14px;
  border: 2px solid rgba(255, 255, 255, 0.3);
  border-top-color: #fff;
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.search-options {
  display: flex;
  align-items: center;
  gap: 1.5rem;
  margin-top: 0.75rem;
  font-size: 0.8rem;
}

.option-toggle {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  cursor: pointer;
  color: var(--color-text-secondary);
}

.option-toggle input[type="checkbox"] {
  accent-color: var(--color-primary-500);
}

.toggle-label {
  font-weight: 500;
}

.toggle-hint {
  color: var(--color-text-muted);
  font-size: 0.75rem;
}

.option-limit {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  color: var(--color-text-secondary);
}

.limit-label {
  font-weight: 500;
}

.limit-select {
  padding: 0.2rem 0.4rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-bg-input);
  color: var(--color-text-primary);
  font-size: 0.8rem;
}

/* 诊断面板 */
.diagnostics {
  display: flex;
  flex-wrap: wrap;
  gap: 1rem;
  padding: 1rem;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  margin-bottom: 1rem;
}

.diag-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  min-width: 70px;
}

.diag-time {
  padding-right: 1rem;
  border-right: 1px solid var(--color-border);
}

.diag-value {
  font-size: 1.4rem;
  font-weight: 700;
  color: var(--color-primary-600);
  line-height: 1.2;
}

.dark .diag-value {
  color: var(--color-primary-400);
}

.diag-unit {
  font-size: 0.7rem;
  color: var(--color-text-muted);
}

.diag-label {
  font-size: 0.7rem;
  color: var(--color-text-muted);
  margin-top: 0.15rem;
}

/* Rewrite 信息 */
.rewrite-info {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  margin-bottom: 0.75rem;
  background: #fef3c7;
  border-radius: var(--radius-md);
  font-size: 0.8rem;
}

.dark .rewrite-info {
  background: rgba(217, 119, 6, 0.1);
}

.rewrite-label {
  font-weight: 600;
  color: #92400e;
}

.dark .rewrite-label {
  color: #fbbf24;
}

.rewrite-original {
  color: #b45309;
  text-decoration: line-through;
}

.dark .rewrite-original {
  color: #d97706;
}

.rewrite-arrow {
  color: var(--color-text-muted);
}

.rewrite-result {
  font-weight: 600;
  color: #16a34a;
}

/* 关键词 */
.keywords-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.4rem;
  margin-bottom: 1rem;
  font-size: 0.8rem;
  color: var(--color-text-muted);
}

.keywords-label {
  font-weight: 500;
}

.keyword-tag {
  padding: 0.15rem 0.5rem;
  background: var(--color-bg-hover);
  border-radius: 999px;
  font-size: 0.75rem;
  color: var(--color-text-secondary);
}

/* 结果列表 */
.result-list {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.result-card {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  overflow: hidden;
  transition: border-color 0.2s;
}

.result-card:hover {
  border-color: var(--color-primary-300);
}

.result-top {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.6rem 1rem;
  background: var(--color-bg-hover);
  font-size: 0.85rem;
}

.result-rank {
  font-size: 0.75rem;
  font-weight: 700;
  color: var(--color-primary-600);
  min-width: 1.8rem;
}

.dark .result-rank {
  color: var(--color-primary-400);
}

.result-type {
  font-size: 0.7rem;
  font-weight: 600;
  padding: 0.15rem 0.45rem;
  border-radius: var(--radius-sm);
  background: var(--color-primary-50);
  color: var(--color-primary-700);
}

.dark .result-type {
  background: rgba(201, 168, 76, 0.15);
  color: var(--color-primary-300);
}

.result-source {
  font-size: 0.65rem;
  font-weight: 600;
  padding: 0.12rem 0.35rem;
  border-radius: var(--radius-sm);
}

.result-source.src-both {
  background: #dcfce7;
  color: #16a34a;
}

.result-source.src-fts {
  background: #dbeafe;
  color: #2563eb;
}

.result-source.src-chroma {
  background: #fef3c7;
  color: #d97706;
}

.dark .result-source.src-both {
  background: rgba(22, 163, 74, 0.15);
  color: #4ade80;
}

.dark .result-source.src-fts {
  background: rgba(37, 99, 235, 0.15);
  color: #60a5fa;
}

.dark .result-source.src-chroma {
  background: rgba(217, 119, 6, 0.15);
  color: #fbbf24;
}

.result-title {
  font-weight: 500;
  color: var(--color-text-primary);
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.result-score {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  min-width: 100px;
}

.score-bar {
  flex: 1;
  height: 6px;
  background: var(--color-border);
  border-radius: 3px;
  overflow: hidden;
}

.score-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--color-primary-400), var(--color-primary-600));
  border-radius: 3px;
  transition: width 0.3s ease;
}

.score-num {
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--color-primary-600);
  min-width: 2.8rem;
  text-align: right;
}

.dark .score-num {
  color: var(--color-primary-400);
}

.result-body {
  padding: 0.75rem 1rem;
  font-size: 0.85rem;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0;
  color: var(--color-text-secondary);
  max-height: 200px;
  overflow-y: auto;
}

/* 空状态 */
.empty-state {
  text-align: center;
  padding: 4rem 2rem;
  color: var(--color-text-muted);
}

.empty-icon {
  font-size: 3rem;
  display: block;
  margin-bottom: 1rem;
}

.empty-title {
  font-size: 1rem;
  font-weight: 600;
  margin: 0 0 0.5rem;
  color: var(--color-text-secondary);
}

.empty-hint {
  font-size: 0.85rem;
  margin: 0;
}

.empty-results {
  text-align: center;
  padding: 3rem;
  color: var(--color-text-muted);
}

.empty-results .empty-icon {
  font-size: 2rem;
}

.empty-results p {
  margin: 0.5rem 0 0;
  font-size: 0.85rem;
}

/* 移动端响应式 */
@media (max-width: 768px) {
  .rag-test-page {
    padding: 1rem;
  }

  .page-title {
    font-size: 1.1rem;
  }

  .type-selector {
    gap: 0.35rem;
  }

  .type-chip {
    padding: 0.35rem 0.65rem;
    font-size: 0.8rem;
  }

  .search-row {
    flex-direction: column;
  }

  .search-input {
    font-size: 16px; /* 防止 iOS 自动缩放 */
  }

  .search-btn {
    width: 100%;
    justify-content: center;
    min-height: 44px; /* 触控友好 */
  }

  .search-options {
    flex-wrap: wrap;
    gap: 0.75rem;
  }

  .diagnostics {
    gap: 0.5rem;
    padding: 0.75rem;
  }

  .diag-item {
    min-width: 60px;
  }

  .diag-time {
    padding-right: 0.5rem;
  }

  .diag-value {
    font-size: 1.2rem;
  }

  .rewrite-info {
    flex-wrap: wrap;
    font-size: 0.75rem;
  }

  .result-top {
    flex-wrap: wrap;
    gap: 0.35rem;
    padding: 0.5rem 0.75rem;
  }

  .result-title {
    width: 100%;
    order: -1;
  }

  .result-score {
    width: 100%;
    min-width: auto;
  }

  .result-body {
    font-size: 0.8rem;
    padding: 0.5rem 0.75rem;
  }
}
</style>
