<script setup>
import { ref, onMounted, computed } from 'vue'
import { getKnowledgeStats, getKnowledgeBooks, listKnowledge, searchKnowledge, deleteKnowledge } from '../../api'
import { useToast } from '../../composables/useToast'
import ConfirmDialog from '../layout/ConfirmDialog.vue'

const { showToast } = useToast()

const stats = ref(null)
const items = ref([])
const loading = ref(false)
const searchQuery = ref('')
const activeCategory = ref('')
const activeSubcategory = ref('')
const activeSource = ref('')
const books = ref([])

const confirm = ref({ visible: false, title: '', message: '', danger: false, onConfirm: null })

// 分类映射
const categoryMap = {
  concept: { label: '概念', icon: '📚' },
  strategy: { label: '策略', icon: '🎯' },
  book: { label: '书籍', icon: '📖' },
  article: { label: '文章', icon: '📰' },
  note: { label: '个人笔记', icon: '📝' },
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
  strategy: '策略',
  industry: '行业',
  tech: '技术',
  psychology: '心理学',
  decision: '决策',
  book: '读书',
  article: '文章',
  course: '课程',
  general: '通用',
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

async function loadBooks() {
  try {
    const { data } = await getKnowledgeBooks()
    books.value = data.books || []
  } catch (e) {
    console.error('Failed to load books:', e)
  }
}

async function loadItems() {
  loading.value = true
  try {
    if (searchQuery.value) {
      const { data } = await searchKnowledge(searchQuery.value, activeCategory.value || null)
      items.value = data.results || []
    } else {
      const source = activeSource.value || null
      const { data } = await listKnowledge(activeCategory.value || null, activeSubcategory.value || null, source)
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
  activeSource.value = ''
  if (activeCategory.value === 'book') {
    loadBooks()
  }
  loadItems()
}

function filterBySubcategory(sub) {
  activeSubcategory.value = activeSubcategory.value === sub ? '' : sub
  activeSource.value = ''
  loadItems()
}

function filterByBook(source) {
  activeSource.value = activeSource.value === source ? '' : source
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

function importanceClass(score) {
  const n = Number(score) || 0
  if (n >= 8) return 'num-gold'
  if (n >= 5) return 'text-warm'
  return 'text-muted'
}
</script>

<template>
  <div class="knowledge-page bg-mesh">
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
      <span class="terminal-label header-eyebrow">KNOWLEDGE ARCHIVE · 知识档案</span>
      <h2 class="page-title editorial-title-lg">投资知识库</h2>
      <p class="page-desc editorial-subtitle">管理蒸馏的投资知识、概念和策略</p>
      <div class="editorial-divider header-divider"></div>
    </div>

    <!-- 统计卡片 -->
    <div v-if="stats" class="stats-grid">
      <div class="stat-card editorial-card reveal-stagger">
        <span class="terminal-label stat-eyebrow">TOTAL</span>
        <div class="stat-value font-jet-lg num-gold">{{ stats.total }}</div>
        <div class="stat-label terminal-label">总知识条目</div>
      </div>
      <div v-for="(count, cat) in stats.categories" :key="cat" class="stat-card editorial-card reveal-stagger"
           :class="{ active: activeCategory === cat }" @click="filterByCategory(cat)">
        <div class="stat-icon">{{ categoryMap[cat]?.icon || '📄' }}</div>
        <div class="stat-value font-jet-lg">{{ count }}</div>
        <div class="stat-label terminal-label">{{ categoryMap[cat]?.label || cat }}</div>
      </div>
    </div>

    <!-- 搜索栏 -->
    <div class="search-bar">
      <span class="terminal-label search-eyebrow">SEARCH</span>
      <input
        v-model="searchQuery"
        class="input-field search-input"
        placeholder="搜索知识条目..."
        @keyup.enter="onSearch"
      />
      <button class="btn-primary" @click="onSearch">搜索</button>
      <button v-if="searchQuery" class="btn-ghost" @click="searchQuery = ''; loadItems()">清除</button>
    </div>

    <!-- 书籍列表 -->
    <div v-if="activeCategory === 'book' && books.length" class="books-section">
      <div class="books-header editorial-card-header">
        <h3 class="title">已蒸馏书籍</h3>
        <span class="meta font-jet">{{ books.length }} VOLUMES</span>
        <button v-if="activeSource" class="btn-ghost btn-sm books-back" @click="activeSource = ''; loadItems()">
          ← 返回全部
        </button>
      </div>
      <div class="books-grid">
        <div
          v-for="book in books"
          :key="book.source"
          :class="['book-card', 'editorial-card', 'reveal-stagger', { active: activeSource === book.source }]"
          @click="filterByBook(book.source)"
        >
          <div class="book-name">{{ book.source }}</div>
          <div class="book-meta">
            <span class="num-gold font-jet">{{ book.count }}</span>
            <span class="terminal-label"> 个知识点</span>
          </div>
        </div>
      </div>
    </div>

    <!-- 子分类筛选 -->
    <div v-if="stats?.subcategories?.length" class="filter-bar">
      <span class="terminal-label filter-label">FILTER · 筛选</span>
      <button
        v-for="sub in stats.subcategories"
        :key="`${sub.category}-${sub.subcategory}`"
        :class="['filter-btn', { active: activeSubcategory === sub.subcategory && activeCategory === sub.category }]"
        @click="activeCategory = sub.category; activeSubcategory = sub.subcategory; loadItems()"
      >
        <span>{{ categoryMap[sub.category]?.label || sub.category }}·{{ subcategoryMap[sub.subcategory] || sub.subcategory }}</span>
        <span class="filter-count font-jet">{{ sub.count }}</span>
      </button>
    </div>

    <!-- 知识列表 -->
    <div v-if="loading" class="loading-state">
      <div class="spinner"></div>
      <span class="terminal-label">LOADING · 加载中</span>
    </div>

    <div v-else-if="filteredItems.length === 0" class="empty-state">
      <div class="empty-icon terminal-label">NO RECORDS</div>
      <p class="empty-text editorial-subtitle">暂无知识条目</p>
    </div>

    <div v-else class="knowledge-list">
      <div v-for="item in filteredItems" :key="item.id" class="knowledge-item editorial-card reveal-stagger">
        <div class="item-header">
          <span class="item-category" :class="item.category">
            {{ categoryMap[item.category]?.icon || '📄' }}
            <span class="cat-label">{{ categoryMap[item.category]?.label || item.category }}</span>
          </span>
          <span v-if="item.subcategory" class="item-subcategory terminal-label">
            {{ subcategoryMap[item.subcategory] || item.subcategory }}
          </span>
          <span class="item-importance">
            <span class="terminal-label">重要性</span>
            <span class="font-jet" :class="importanceClass(item.importance)">{{ item.importance }}</span>
            <span class="terminal-label importance-sep">/10</span>
          </span>
          <button class="btn-delete" @click="handleDelete(item)" title="删除">🗑️</button>
        </div>
        <h3 class="item-title editorial-title">{{ item.title }}</h3>
        <div class="item-content markdown-body" v-html="renderContent(item.content)"></div>
        <div v-if="item.keywords?.length" class="item-keywords">
          <span v-for="kw in item.keywords" :key="kw" class="keyword-tag terminal-label">{{ kw }}</span>
        </div>
        <div v-if="item.source" class="item-source">
          <span class="terminal-label">SOURCE · 来源</span>
          <span class="font-jet">{{ item.source }}</span>
        </div>
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

/* ── 页面头部 ── */
.page-header {
  margin-bottom: 1.5rem;
}
.header-eyebrow {
  display: block;
  margin-bottom: 0.4rem;
}
.page-title {
  margin: 0 0 0.4rem 0;
}
.page-desc {
  margin: 0;
}
.header-divider {
  margin: 0.9rem 0 0 0;
}

/* ── 统计卡片（终端指标格） ── */
.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
  gap: 0.75rem;
  margin-bottom: 1.5rem;
}
.stat-card {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 0.9rem 1rem 0.9rem 1.1rem;
  text-align: left;
  cursor: pointer;
  transition: border-color 0.2s, background 0.2s, transform 0.2s;
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}
.stat-card:hover {
  border-color: var(--color-primary-300);
  transform: translateY(-1px);
}
.stat-card.active {
  background: var(--color-primary-50);
  border-color: var(--color-primary-400);
}
.stat-eyebrow {
  display: block;
}
.stat-icon {
  font-size: 1.1rem;
  line-height: 1;
  margin-bottom: 0.1rem;
}
.stat-value {
  font-size: 1.5rem;
  line-height: 1.1;
  color: var(--color-text-primary);
}
.stat-card.active .stat-value {
  color: var(--color-primary);
}
.stat-label {
  font-size: 0.7rem;
}

/* ── 搜索栏 ── */
.search-bar {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  margin-bottom: 1rem;
}
.search-eyebrow {
  flex-shrink: 0;
}
.search-input {
  flex: 1;
}

/* ── 筛选栏 ── */
.filter-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  align-items: center;
  margin-bottom: 1.5rem;
}
.filter-label {
  flex-shrink: 0;
}
.filter-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.4rem 0.85rem;
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
.filter-count {
  font-size: 0.7rem;
  opacity: 0.75;
}
.filter-btn.active .filter-count {
  opacity: 0.95;
}

/* ── 知识列表 ── */
.knowledge-list {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}
.knowledge-item {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 1.4rem 1.5rem 1.4rem 1.7rem;
  transition: border-color 0.2s, box-shadow 0.2s;
}
.knowledge-item:hover {
  border-color: var(--color-primary-300);
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.05);
}
.dark .knowledge-item:hover {
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.3);
}

.item-header {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.6rem;
  margin-bottom: 0.85rem;
}
.item-category {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.72rem;
  padding: 0.2rem 0.6rem;
  border-radius: var(--radius-full);
  background: var(--color-bg-hover);
  color: var(--color-text-secondary);
}
.item-category .cat-label {
  font-family: var(--font-mono-jet);
  letter-spacing: 0.02em;
}
.item-category.concept { background: #dbeafe; color: #1d4ed8; }
.item-category.strategy { background: #dcfce7; color: #16a34a; }
.item-category.book { background: #fef3c7; color: #d97706; }
.dark .item-category.concept { background: rgba(59, 130, 246, 0.18); color: #93c5fd; }
.dark .item-category.strategy { background: rgba(34, 197, 94, 0.18); color: #86efac; }
.dark .item-category.book { background: rgba(245, 158, 11, 0.18); color: #fcd34d; }

.item-subcategory {
  font-size: 0.68rem;
}
.item-importance {
  display: inline-flex;
  align-items: baseline;
  gap: 0.3rem;
  margin-left: auto;
}
.item-importance .font-jet {
  font-size: 0.9rem;
  font-weight: 600;
}
.importance-sep {
  opacity: 0.6;
}
.text-warm {
  color: var(--color-gold);
}
.dark .text-warm {
  color: var(--color-gold-light);
}
.text-muted {
  color: var(--color-text-muted);
}

.btn-delete {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 1rem;
  opacity: 0.45;
  transition: opacity 0.2s, transform 0.2s;
}
.btn-delete:hover {
  opacity: 1;
  transform: scale(1.1);
}

.item-title {
  font-size: 1.12rem;
  margin: 0 0 0.85rem 0;
  color: var(--color-text-primary);
}

.item-content {
  font-size: 0.92rem;
  line-height: 1.75;
  color: var(--color-text-secondary);
  margin-bottom: 0.85rem;
}
.item-content :deep(h3) {
  font-family: var(--font-serif);
  font-size: 1rem;
  font-weight: 600;
  margin: 1rem 0 0.5rem 0;
  color: var(--color-text-primary);
}
.item-content :deep(h4) {
  font-family: var(--font-serif);
  font-size: 0.92rem;
  font-weight: 600;
  margin: 0.75rem 0 0.35rem 0;
  color: var(--color-text-primary);
}

.item-keywords {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
  margin-bottom: 0.6rem;
}
.keyword-tag {
  font-size: 0.66rem;
  padding: 0.18rem 0.55rem;
  background: var(--color-bg-hover);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-full);
  color: var(--color-text-muted);
}

.item-source {
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
  font-size: 0.72rem;
  color: var(--color-text-muted);
  padding-top: 0.4rem;
  border-top: 1px dashed var(--color-border-light);
  margin-top: 0.2rem;
}
.item-source .font-jet {
  color: var(--color-text-secondary);
}

/* ── 书籍列表 ── */
.books-section {
  margin-bottom: 1.5rem;
}
.books-header {
  margin-bottom: 0.85rem;
}
.books-header .title {
  font-size: 1.02rem;
}
.books-back {
  margin-left: auto;
}
.btn-sm {
  padding: 0.25rem 0.6rem;
  font-size: 0.78rem;
}
.books-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 0.85rem;
}
.book-card {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 0.9rem 1rem 0.9rem 1.2rem;
  cursor: pointer;
  transition: border-color 0.2s, background 0.2s, transform 0.2s;
}
.book-card:hover {
  border-color: var(--color-primary-300);
  transform: translateY(-1px);
}
.book-card.active {
  background: var(--color-primary-50);
  border-color: var(--color-primary-400);
}
.book-name {
  font-family: var(--font-serif);
  font-size: 0.95rem;
  font-weight: 600;
  letter-spacing: -0.01em;
  margin-bottom: 0.4rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--color-text-primary);
}
.book-meta {
  display: inline-flex;
  align-items: baseline;
  gap: 0.25rem;
  font-size: 0.72rem;
  color: var(--color-text-muted);
}

/* ── 加载和空状态 ── */
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
  padding: 3rem 1rem;
  color: var(--color-text-muted);
}
.empty-icon {
  display: block;
  font-size: 0.7rem;
  margin-bottom: 0.6rem;
  letter-spacing: 0.15em;
}
.empty-text {
  margin: 0;
}

/* ── 移动端适配 ── */
@media (max-width: 768px) {
  .books-grid {
    grid-template-columns: repeat(2, 1fr);
    gap: 0.5rem;
  }
  .book-card {
    padding: 0.65rem 0.75rem 0.65rem 0.95rem;
  }
  .book-name {
    font-size: 0.82rem;
  }
  .search-bar {
    flex-direction: column;
    align-items: stretch;
    gap: 0.5rem;
  }
  .search-bar input {
    width: 100%;
  }
  .search-eyebrow {
    align-self: flex-start;
  }
  .filter-bar {
    flex-direction: column;
    align-items: stretch;
    gap: 0.5rem;
  }
  .item-importance {
    margin-left: 0;
  }
}

@media (max-width: 480px) {
  .books-grid {
    grid-template-columns: 1fr;
  }
}
</style>
