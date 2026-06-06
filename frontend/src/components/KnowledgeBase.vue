<script setup>
import { ref, onMounted, computed } from 'vue'
import { getKnowledgeStats, listKnowledge, searchKnowledge, deleteKnowledge } from '../api'
import { useToast } from '../composables/useToast'
import ConfirmDialog from './ConfirmDialog.vue'

const { showToast } = useToast()

const stats = ref(null)
const items = ref([])
const loading = ref(false)
const searchQuery = ref('')
const activeCategory = ref('')
const activeSubcategory = ref('')

const confirm = ref({ visible: false, title: '', message: '', danger: false, onConfirm: null })

// 分类映射
const categoryMap = {
  concept: { label: '概念', icon: '📚' },
  strategy: { label: '策略', icon: '🎯' },
  book: { label: '书籍', icon: '📖' },
  article: { label: '文章', icon: '📰' },
}

const subcategoryMap = {
  valuation: '估值',
  risk: '风险',
  fund: '基金',
  macro: '宏观',
  allocation: '配置',
  timing: '择时',
  concept: '概念',
  principle: '原则',
}

onMounted(async () => {
  await loadStats()
  await loadItems()
})

async function loadStats() {
  try {
    const { data } = await getKnowledgeStats()
    stats.value = data
  } catch (e) {
    console.error('Failed to load stats:', e)
  }
}

async function loadItems() {
  loading.value = true
  try {
    if (searchQuery.value) {
      const { data } = await searchKnowledge(searchQuery.value, activeCategory.value || null)
      items.value = data.results || []
    } else {
      const { data } = await listKnowledge(activeCategory.value || null, activeSubcategory.value || null)
      items.value = data.items || []
    }
  } catch (e) {
    console.error('Failed to load items:', e)
  } finally {
    loading.value = false
  }
}

function filterByCategory(cat) {
  activeCategory.value = activeCategory.value === cat ? '' : cat
  activeSubcategory.value = ''
  loadItems()
}

function filterBySubcategory(sub) {
  activeSubcategory.value = activeSubcategory.value === sub ? '' : sub
  loadItems()
}

function onSearch() {
  loadItems()
}

function handleDelete(item) {
  confirm.value = {
    visible: true,
    title: '删除知识条目',
    message: `确定删除「${item.title}」？`,
    danger: true,
    onConfirm: async () => {
      confirm.value.visible = false
      try {
        await deleteKnowledge(item.id)
        showToast('已删除', 'success')
        await loadStats()
        await loadItems()
      } catch (e) {
        showToast('删除失败', 'error')
      }
    }
  }
}

const filteredItems = computed(() => {
  return items.value
})
</script>

<template>
  <div class="knowledge-page">
    <ConfirmDialog
      :visible="confirm.visible"
      :title="confirm.title"
      :message="confirm.message"
      :danger="confirm.danger"
      @confirm="confirm.onConfirm?.()"
      @cancel="confirm.visible = false"
    />

    <!-- 页面标题 -->
    <div class="page-header">
      <h2 class="page-title">📚 投资知识库</h2>
      <p class="page-desc">管理蒸馏的投资知识、概念和策略</p>
    </div>

    <!-- 统计卡片 -->
    <div v-if="stats" class="stats-grid">
      <div class="stat-card">
        <div class="stat-value">{{ stats.total }}</div>
        <div class="stat-label">总知识条目</div>
      </div>
      <div v-for="(count, cat) in stats.categories" :key="cat" class="stat-card"
           :class="{ active: activeCategory === cat }" @click="filterByCategory(cat)">
        <div class="stat-icon">{{ categoryMap[cat]?.icon || '📄' }}</div>
        <div class="stat-value">{{ count }}</div>
        <div class="stat-label">{{ categoryMap[cat]?.label || cat }}</div>
      </div>
    </div>

    <!-- 搜索栏 -->
    <div class="search-bar">
      <input
        v-model="searchQuery"
        class="input-field search-input"
        placeholder="搜索知识条目..."
        @keyup.enter="onSearch"
      />
      <button class="btn-primary" @click="onSearch">搜索</button>
      <button v-if="searchQuery" class="btn-ghost" @click="searchQuery = ''; loadItems()">清除</button>
    </div>

    <!-- 子分类筛选 -->
    <div v-if="stats?.subcategories?.length" class="filter-bar">
      <span class="filter-label">筛选：</span>
      <button
        v-for="sub in stats.subcategories"
        :key="`${sub.category}-${sub.subcategory}`"
        :class="['filter-btn', { active: activeSubcategory === sub.subcategory }]"
        @click="filterBySubcategory(sub.subcategory)"
      >
        {{ subcategoryMap[sub.subcategory] || sub.subcategory }} ({{ sub.count }})
      </button>
    </div>

    <!-- 知识列表 -->
    <div v-if="loading" class="loading-state">
      <div class="spinner"></div>
      <span>加载中...</span>
    </div>

    <div v-else-if="filteredItems.length === 0" class="empty-state">
      <div class="empty-icon">📭</div>
      <p>暂无知识条目</p>
    </div>

    <div v-else class="knowledge-list">
      <div v-for="item in filteredItems" :key="item.id" class="knowledge-item">
        <div class="item-header">
          <span class="item-category" :class="item.category">
            {{ categoryMap[item.category]?.icon || '📄' }}
            {{ categoryMap[item.category]?.label || item.category }}
          </span>
          <span v-if="item.subcategory" class="item-subcategory">
            {{ subcategoryMap[item.subcategory] || item.subcategory }}
          </span>
          <span class="item-importance">
            重要性: {{ item.importance }}/10
          </span>
          <button class="btn-delete" @click="handleDelete(item)" title="删除">🗑️</button>
        </div>
        <h3 class="item-title">{{ item.title }}</h3>
        <div class="item-content markdown-body" v-html="renderContent(item.content)"></div>
        <div v-if="item.keywords?.length" class="item-keywords">
          <span v-for="kw in item.keywords" :key="kw" class="keyword-tag">{{ kw }}</span>
        </div>
        <div v-if="item.source" class="item-source">来源: {{ item.source }}</div>
      </div>
    </div>
  </div>
</template>

<script>
// 简单的 markdown 渲染
function renderContent(content) {
  if (!content) return ''
  return content
    .replace(/## (.+)/g, '<h3>$1</h3>')
    .replace(/### (.+)/g, '<h4>$1</h4>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\n- /g, '<br>• ')
    .replace(/\n/g, '<br>')
}
</script>

<style scoped>
.knowledge-page {
  padding: 1.5rem;
  max-width: 1200px;
  margin: 0 auto;
}

.page-header {
  margin-bottom: 1.5rem;
}

.page-title {
  font-size: 1.5rem;
  font-weight: 700;
  margin: 0 0 0.5rem 0;
}

.page-desc {
  color: var(--color-text-muted);
  margin: 0;
}

/* 统计卡片 */
.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: 1rem;
  margin-bottom: 1.5rem;
}

.stat-card {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 1rem;
  text-align: center;
  cursor: pointer;
  transition: all 0.2s;
}

.stat-card:hover {
  border-color: var(--color-primary-300);
}

.stat-card.active {
  background: var(--color-primary-50);
  border-color: var(--color-primary-400);
}

.stat-icon {
  font-size: 1.5rem;
  margin-bottom: 0.5rem;
}

.stat-value {
  font-size: 1.5rem;
  font-weight: 700;
  color: var(--color-primary);
}

.stat-label {
  font-size: 0.8rem;
  color: var(--color-text-muted);
}

/* 搜索栏 */
.search-bar {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 1rem;
}

.search-input {
  flex: 1;
}

/* 筛选栏 */
.filter-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  align-items: center;
  margin-bottom: 1.5rem;
}

.filter-label {
  font-size: 0.85rem;
  color: var(--color-text-muted);
}

.filter-btn {
  padding: 0.4rem 0.8rem;
  font-size: 0.8rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-full);
  background: var(--color-bg-card);
  cursor: pointer;
  transition: all 0.2s;
}

.filter-btn:hover {
  border-color: var(--color-primary-300);
}

.filter-btn.active {
  background: var(--color-primary);
  color: white;
  border-color: var(--color-primary);
}

/* 知识列表 */
.knowledge-list {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.knowledge-item {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 1.25rem;
}

.item-header {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 0.75rem;
}

.item-category {
  font-size: 0.75rem;
  padding: 0.2rem 0.6rem;
  border-radius: var(--radius-full);
  background: var(--color-bg-hover);
}

.item-category.concept { background: #dbeafe; color: #1d4ed8; }
.item-category.strategy { background: #dcfce7; color: #16a34a; }
.item-category.book { background: #fef3c7; color: #d97706; }

.item-subcategory {
  font-size: 0.75rem;
  color: var(--color-text-muted);
}

.item-importance {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  margin-left: auto;
}

.btn-delete {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 1rem;
  opacity: 0.5;
  transition: opacity 0.2s;
}

.btn-delete:hover {
  opacity: 1;
}

.item-title {
  font-size: 1.1rem;
  font-weight: 600;
  margin: 0 0 0.75rem 0;
}

.item-content {
  font-size: 0.9rem;
  line-height: 1.6;
  color: var(--color-text-secondary);
  margin-bottom: 0.75rem;
}

.item-content :deep(h3) {
  font-size: 1rem;
  font-weight: 600;
  margin: 1rem 0 0.5rem 0;
  color: var(--color-text-primary);
}

.item-content :deep(h4) {
  font-size: 0.9rem;
  font-weight: 600;
  margin: 0.75rem 0 0.35rem 0;
  color: var(--color-text-primary);
}

.item-keywords {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}

.keyword-tag {
  font-size: 0.7rem;
  padding: 0.15rem 0.5rem;
  background: var(--color-bg-hover);
  border-radius: var(--radius-full);
  color: var(--color-text-muted);
}

.item-source {
  font-size: 0.75rem;
  color: var(--color-text-muted);
}

/* 加载和空状态 */
.loading-state {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.75rem;
  padding: 3rem;
  color: var(--color-text-muted);
}

.empty-state {
  text-align: center;
  padding: 3rem;
  color: var(--color-text-muted);
}

.empty-icon {
  font-size: 2rem;
  margin-bottom: 0.5rem;
}
</style>
