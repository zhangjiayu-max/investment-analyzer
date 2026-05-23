<script setup>
import { ref, computed } from 'vue'
import { isDark, toggleDark } from '../composables/useTheme'

const props = defineProps({
  activePage: String,
})
const emit = defineEmits(['navigate'])

const navItems = [
  { key: 'chat', label: 'AI 对话', icon: 'chat' },
  { key: 'articles', label: '文章管理', icon: 'articles' },
  { key: 'valuation', label: '估值数据', icon: 'valuation' },
  { key: 'gallery', label: '图片浏览', icon: 'gallery' },
  {
    key: 'group-knowledge', label: '知识库', icon: 'author',
    children: [
      { key: 'author', label: '作者文章', icon: 'author' },
      { key: 'linked', label: '个人文档', icon: 'link' },
      { key: 'rag', label: 'RAG 分析', icon: 'rag' },
    ],
  },
  {
    key: 'group-bond', label: '债券分析', icon: 'bond',
    children: [
      { key: 'bond', label: '债市市场温度', icon: 'bond' },
    ],
  },
]

const expandedGroups = ref(new Set(
  navItems.filter(i => i.children).map(i => i.key)
))

function toggleGroup(key) {
  if (expandedGroups.value.has(key)) {
    expandedGroups.value.delete(key)
  } else {
    expandedGroups.value.add(key)
  }
}

function navigate(key) {
  emit('navigate', key)
}

// Auto-expand group when a child is active
const activeGroup = computed(() => {
  for (const item of navItems) {
    if (item.children?.some(c => c.key === props.activePage)) {
      return item.key
    }
  }
  return null
})
</script>

<template>
  <aside class="sidebar">
    <!-- Logo -->
    <div class="sidebar-logo">
      <div class="logo-icon">IA</div>
      <div class="logo-text">
        <span class="logo-title">投资分析助手</span>
        <span class="logo-sub">Investment Analyzer</span>
      </div>
    </div>

    <!-- Navigation -->
    <nav class="sidebar-nav">
      <template v-for="item in navItems" :key="item.key">
        <!-- Flat item (no children) -->
        <button
          v-if="!item.children"
          @click="navigate(item.key)"
          :class="['nav-item', { active: activePage === item.key }]"
        >
          <svg v-if="item.icon === 'chat'" class="nav-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"/>
          </svg>
          <svg v-else-if="item.icon === 'articles'" class="nav-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M19 20H5a2 2 0 01-2-2V6a2 2 0 012-2h10a2 2 0 012 2v1m2 13a2 2 0 01-2-2V7m2 13a2 2 0 002-2V9a2 2 0 00-2-2h-2m-4-3H9M7 16h6M7 8h6v4H7V8z"/>
          </svg>
          <svg v-else-if="item.icon === 'valuation'" class="nav-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/>
          </svg>
          <svg v-else-if="item.icon === 'gallery'" class="nav-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"/>
          </svg>
          <span class="nav-label">{{ item.label }}</span>
        </button>

        <!-- Group item (has children) -->
        <div v-else class="nav-group">
          <button
            @click="toggleGroup(item.key)"
            :class="['nav-item', 'nav-group-header', { active: activeGroup === item.key }]"
          >
            <svg v-if="item.icon === 'author'" class="nav-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253"/>
            </svg>
            <svg v-else-if="item.icon === 'bond'" class="nav-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6"/>
            </svg>
            <span class="nav-label">{{ item.label }}</span>
            <svg class="chevron" :class="{ expanded: expandedGroups.has(item.key) }" width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/>
            </svg>
          </button>
          <div v-show="expandedGroups.has(item.key)" class="nav-children">
            <button
              v-for="child in item.children"
              :key="child.key"
              @click="navigate(child.key)"
              :class="['nav-item', 'nav-child', { active: activePage === child.key }]"
            >
              <svg v-if="child.icon === 'author'" class="nav-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253"/>
              </svg>
              <svg v-else-if="child.icon === 'link'" class="nav-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1"/>
              </svg>
              <svg v-else-if="child.icon === 'rag'" class="nav-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
              </svg>
              <svg v-else-if="child.icon === 'bond'" class="nav-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6"/>
              </svg>
              <span class="nav-label">{{ child.label }}</span>
            </button>
          </div>
        </div>
      </template>
    </nav>

    <!-- Bottom actions -->
    <div class="sidebar-bottom">
      <button @click="toggleDark()" class="nav-item theme-toggle" :title="isDark ? '切换到亮色模式' : '切换到暗色模式'">
        <svg v-if="isDark" class="nav-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z"/>
        </svg>
        <svg v-else class="nav-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z"/>
        </svg>
        <span class="nav-label">{{ isDark ? '亮色模式' : '暗色模式' }}</span>
      </button>
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  width: var(--sidebar-width);
  height: 100vh;
  background: var(--color-bg-sidebar);
  border-right: 1px solid var(--color-border);
  display: flex;
  flex-direction: column;
  position: fixed;
  left: 0;
  top: 0;
  z-index: 40;
  transition: background-color var(--transition-normal), border-color var(--transition-normal);
}

.sidebar-logo {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 1.25rem 1rem;
  border-bottom: 1px solid var(--color-border);
}

.logo-icon {
  width: 36px;
  height: 36px;
  background: linear-gradient(135deg, var(--color-primary-500), var(--color-primary-700));
  border-radius: var(--radius-md);
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  font-size: 0.8rem;
  font-weight: 700;
  flex-shrink: 0;
}

.logo-text {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.logo-title {
  font-size: 0.9rem;
  font-weight: 700;
  color: var(--color-text-primary);
  line-height: 1.2;
}

.logo-sub {
  font-size: 0.65rem;
  color: var(--color-text-muted);
  letter-spacing: 0.02em;
}

.sidebar-nav {
  flex: 1;
  padding: 0.75rem 0.5rem;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  overflow-y: auto;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.6rem 0.75rem;
  border-radius: var(--radius-md);
  color: var(--color-text-secondary);
  font-size: 0.875rem;
  font-weight: 500;
  transition: all var(--transition-fast);
  text-align: left;
  width: 100%;
}

.nav-item:hover {
  background: var(--color-bg-hover);
  color: var(--color-text-primary);
}

.nav-item.active {
  background: var(--color-primary-50);
  color: var(--color-primary-700);
}

.dark .nav-item.active {
  background: var(--color-primary-bg);
  color: var(--color-primary-300);
}

.nav-icon {
  width: 20px;
  height: 20px;
  flex-shrink: 0;
}

.nav-label {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex: 1;
}

/* Group header */
.nav-group-header {
  position: relative;
}

.chevron {
  margin-left: auto;
  flex-shrink: 0;
  transition: transform var(--transition-fast);
  opacity: 0.5;
}

.chevron.expanded {
  transform: rotate(90deg);
}

/* Children */
.nav-children {
  padding-left: 0.5rem;
  display: flex;
  flex-direction: column;
  gap: 0.125rem;
}

.nav-child {
  padding: 0.45rem 0.75rem;
  font-size: 0.8rem;
  gap: 0.6rem;
}

.nav-child .nav-icon {
  width: 16px;
  height: 16px;
  opacity: 0.7;
}

.sidebar-bottom {
  padding: 0.5rem;
  border-top: 1px solid var(--color-border);
}

.theme-toggle {
  color: var(--color-text-muted);
}

/* Mobile responsive */
@media (max-width: 768px) {
  .sidebar {
    width: 100%;
    height: auto;
    position: fixed;
    bottom: 0;
    top: auto;
    flex-direction: row;
    border-right: none;
    border-top: 1px solid var(--color-border);
    padding: 0;
    box-shadow: 0 -2px 10px rgba(0,0,0,0.05);
  }

  .sidebar-logo {
    display: none;
  }

  .sidebar-nav {
    flex-direction: row;
    padding: 0.25rem;
    gap: 0;
    flex: 1;
    justify-content: center;
    overflow-x: auto;
    overflow-y: visible;
  }

  .nav-group {
    display: flex;
    flex-direction: column;
    align-items: center;
  }

  .nav-item {
    flex-direction: column;
    gap: 0.2rem;
    padding: 0.5rem 0.75rem;
    font-size: 0.65rem;
  }

  .nav-icon {
    width: 22px;
    height: 22px;
  }

  .nav-label {
    font-size: 0.6rem;
  }

  .chevron {
    display: none;
  }

  .nav-children {
    padding-left: 0;
    flex-direction: row;
    gap: 0;
  }

  .nav-child {
    padding: 0.35rem 0.5rem;
    font-size: 0.55rem;
  }

  .nav-child .nav-icon {
    width: 14px;
    height: 14px;
  }

  .sidebar-bottom {
    border-top: none;
    border-left: 1px solid var(--color-border);
    padding: 0.25rem;
    display: flex;
    align-items: center;
  }

  .theme-toggle {
    padding: 0.5rem;
  }
}
</style>
