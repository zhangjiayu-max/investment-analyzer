<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { isDark, toggleDark } from '../composables/useTheme'
import { getTokenUsageBudget } from '../api'
import Icon from './ui/Icon.vue'

const props = defineProps({
  activePage: String,
})
const emit = defineEmits(['navigate'])

// ── Token 预算指示器 ──────────────────────────────
const tokenUsed = ref(0)
const tokenLimit = ref(500000)
const tokenPct = ref(0)
const tokenMode = ref('normal')
const tokenLabel = computed(() => {
  const k = (tokenUsed.value / 1000).toFixed(0)
  const limitK = (tokenLimit.value / 1000).toFixed(0)
  return `${k}k / ${limitK}k`
})

async function fetchTokenBudget() {
  try {
    const { data } = await getTokenUsageBudget()
    tokenUsed.value = data.used || 0
    tokenLimit.value = data.limit || 500000
    tokenPct.value = Math.min(100, data.pct || 0)
    tokenMode.value = data.mode || 'normal'
  } catch { /* silent */ }
}

let tokenTimer = null
onMounted(() => {
  fetchTokenBudget()
  tokenTimer = setInterval(fetchTokenBudget, 60000) // 每分钟刷新
})
onUnmounted(() => { clearInterval(tokenTimer) })

const navItems = [
  { key: 'dashboard', label: '每日看板 🔥', icon: 'dashboard', hot: true },
  { key: 'market-intelligence', label: '市场热点', icon: 'fire' },
  { key: 'chat', label: 'AI 对话', icon: 'chat' },
  { key: 'articles', label: '文章管理', icon: 'articles' },
  { key: 'valuation', label: '估值数据 🔥', icon: 'valuation', hot: true },
  { key: 'gallery', label: '估值图片', icon: 'gallery' },
  { key: 'portfolio', label: '持仓管理 🔥', icon: 'portfolio', hot: true },
  {
    key: 'group-knowledge', label: '知识库', icon: 'author',
    children: [
      { key: 'author', label: '作者文章', icon: 'author' },
      { key: 'linked', label: '个人文档', icon: 'link' },
      { key: 'knowledge', label: '蒸馏知识', icon: 'book' },
      { key: 'rag-test', label: '命中测试', icon: 'rag' },
      { key: 'rag', label: 'RAG 分析', icon: 'rag' },
    ],
  },
  {
    key: 'group-bond', label: '债券分析', icon: 'bond',
    children: [
      { key: 'bond', label: '债市市场温度', icon: 'bond' },
    ],
  },
  { key: 'admin-agents', label: 'Agent 管理', icon: 'admin' },
  { key: 'token-usage', label: 'Token 用量', icon: 'token' },
  { key: 'system-config', label: '系统配置', icon: 'config' },
  {
    key: 'group-evolution', label: '进化系统', icon: 'evolution',
    children: [
      { key: 'quality-dashboard', label: '质量仪表盘', icon: 'chart' },
      { key: 'bad-cases', label: 'Bad Case', icon: 'bug' },
      { key: 'eval-suite', label: '评测集', icon: 'check' },
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
          :class="['nav-item', { active: activePage === item.key, 'nav-item-hot': item.hot }]"
        >
          <Icon :name="item.icon" size="18" class="nav-icon" />
          <span class="nav-label">{{ item.label }}</span>
        </button>

        <!-- Group item (has children) -->
        <div v-else class="nav-group">
          <button
            @click="toggleGroup(item.key)"
            :class="['nav-item', 'nav-group-header', { active: activeGroup === item.key }]"
          >
            <Icon :name="item.icon" size="18" class="nav-icon" />
            <span class="nav-label">{{ item.label }}</span>
            <Icon name="chevron-down" size="16" class="chevron" :class="{ expanded: expandedGroups.has(item.key) }" />
          </button>
          <div v-show="expandedGroups.has(item.key)" class="nav-children">
            <button
              v-for="child in item.children"
              :key="child.key"
              @click="navigate(child.key)"
              :class="['nav-item', 'nav-child', { active: activePage === child.key }]"
            >
              <Icon :name="child.icon" size="16" class="nav-icon" />
              <span class="nav-label">{{ child.label }}</span>
            </button>
          </div>
        </div>
      </template>
    </nav>

    <!-- Token 预算指示器 -->
    <div class="token-meter" :class="`token-${tokenMode}`">
      <div class="token-meter-header">
        <Icon name="evolution" size="16" class="token-meter-icon" />
        <span class="token-meter-label">今日 Token</span>
        <span class="token-meter-value">{{ tokenLabel }}</span>
      </div>
      <div class="token-bar-track">
        <div class="token-bar-fill" :style="{ width: tokenPct + '%' }"></div>
        <div class="token-bar-marker" :style="{ left: '80%' }"></div>
      </div>
      <div v-if="tokenMode === 'exceeded'" class="token-alert">
        <Icon name="warning" size="14" />
        <span>额度已用完</span>
      </div>
      <div v-else-if="tokenMode === 'warning'" class="token-alert token-alert-warn">
        <Icon name="info" size="14" />
        <span>接近上限，自动降级</span>
      </div>
    </div>

    <!-- Bottom actions -->
    <div class="sidebar-bottom">
      <button @click="toggleDark()" class="nav-item theme-toggle" :title="isDark ? '切换到亮色模式' : '切换到暗色模式'">
        <Icon :name="isDark ? 'sun' : 'moon'" size="18" class="nav-icon" />
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
  border-left: 2px solid var(--color-primary-500);
  padding-left: calc(0.75rem - 2px);
}

.dark .nav-item.active {
  background: var(--color-primary-bg);
  color: var(--color-primary-300);
  border-left: 2px solid var(--color-primary-500);
  box-shadow: 0 0 12px rgba(201, 168, 76, 0.08);
}

/* Hot items (持仓管理、估值数据) */
.nav-item-hot {
  color: #d97706 !important;
}
.nav-item-hot .nav-icon {
  color: #f59e0b;
  stroke: #f59e0b;
}
.nav-item-hot:hover {
  background: rgba(245, 158, 11, 0.1) !important;
}
.nav-item-hot.active {
  background: rgba(245, 158, 11, 0.15) !important;
  color: #b45309 !important;
}
.dark .nav-item-hot {
  color: #fbbf24 !important;
}
.dark .nav-item-hot .nav-icon {
  color: #fbbf24;
  stroke: #fbbf24;
}
.dark .nav-item-hot.active {
  background: rgba(245, 158, 11, 0.2) !important;
  color: #f59e0b !important;
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

/* ── Token 预算指示器 ──────────────────────────── */
.token-meter {
  margin: 0 0.5rem 0.25rem;
  padding: 0.6rem 0.75rem;
  border-radius: var(--radius-md);
  background: var(--color-bg-hover);
  transition: all var(--transition-normal);
}

.token-meter-header {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  margin-bottom: 0.4rem;
}

.token-meter-icon {
  width: 14px;
  height: 14px;
  color: var(--color-text-muted);
  flex-shrink: 0;
}

.token-meter-label {
  font-size: 0.7rem;
  color: var(--color-text-muted);
  flex: 1;
}

.token-meter-value {
  font-size: 0.7rem;
  font-weight: 600;
  color: var(--color-text-secondary);
  font-variant-numeric: tabular-nums;
}

.token-bar-track {
  height: 6px;
  border-radius: 3px;
  background: var(--color-border);
  position: relative;
  overflow: hidden;
}

.token-bar-fill {
  height: 100%;
  border-radius: 3px;
  transition: width 0.6s cubic-bezier(0.22, 1, 0.36, 1), background-color 0.4s ease;
  position: relative;
}

.token-bar-marker {
  position: absolute;
  top: -1px;
  bottom: -1px;
  width: 1px;
  background: var(--color-text-muted);
  opacity: 0.4;
}

/* Normal: green */
.token-normal .token-bar-fill {
  background: linear-gradient(90deg, #10b981, #34d399);
}
.token-normal .token-meter-icon {
  color: #10b981;
}

/* Warning: amber + pulse */
.token-warning .token-bar-fill {
  background: linear-gradient(90deg, #f59e0b, #fbbf24);
  animation: token-pulse 2s ease-in-out infinite;
}
.token-warning .token-meter-icon {
  color: #f59e0b;
}
.token-warning .token-meter-value {
  color: #d97706;
}
.token-warning .token-meter {
  background: rgba(245, 158, 11, 0.08);
}

/* Exceeded: red + shake */
.token-exceeded .token-bar-fill {
  background: linear-gradient(90deg, #ef4444, #f87171);
}
.token-exceeded .token-meter-icon {
  color: #ef4444;
  animation: token-shake 0.6s ease-in-out;
}
.token-exceeded .token-meter-value {
  color: #dc2626;
  font-weight: 700;
}
.token-exceeded .token-meter {
  background: rgba(239, 68, 68, 0.08);
  border: 1px solid rgba(239, 68, 68, 0.15);
}

.token-alert {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  margin-top: 0.35rem;
  font-size: 0.65rem;
  color: #ef4444;
  font-weight: 600;
}

.token-alert-warn {
  color: #d97706;
}

@keyframes token-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.7; }
}

@keyframes token-shake {
  0%, 100% { transform: translateX(0); }
  20% { transform: translateX(-2px); }
  40% { transform: translateX(2px); }
  60% { transform: translateX(-1px); }
  80% { transform: translateX(1px); }
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
    box-shadow: 0 -2px 10px rgba(0,0,0,0.3);
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
    padding: 0.6rem 0.75rem;
    font-size: 0.75rem;
  }

  .nav-icon {
    width: 24px;
    height: 24px;
  }

  .nav-label {
    font-size: 0.7rem;
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
    padding: 0.5rem 0.6rem;
    font-size: 0.65rem;
  }

  .nav-child .nav-icon {
    width: 18px;
    height: 18px;
  }

  .token-meter {
    display: none;
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
