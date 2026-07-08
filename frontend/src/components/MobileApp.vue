<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { createTask, getFinanceQuoteBar } from '../api'
import TaskList from './TaskList.vue'
import TaskDetail from './TaskDetail.vue'
import ArticleManagement from './ArticleManagement.vue'
import ValuationHistory from './ValuationHistory.vue'
import ImageGallery from './ImageGallery.vue'
import ChatView from './ChatView.vue'
import AuthorArticles from './AuthorArticles.vue'
import LinkedArticles from './LinkedArticles.vue'
import BondMarket from './BondMarket.vue'
import RagAnalysis from './RagAnalysis.vue'
import RagTestPage from './RagTestPage.vue'
import PortfolioManagement from './PortfolioManagement.vue'
import AdminAgentsPage from './AdminAgentsPage.vue'
import TokenUsagePage from './TokenUsagePage.vue'
import BadCasePage from './BadCasePage.vue'
import EvalSuitePage from './EvalSuitePage.vue'
import HealthScore from './HealthScore.vue'
import ShadowModePage from './ShadowModePage.vue'
import QualityDashboard from './QualityDashboard.vue'
import Dashboard from './Dashboard.vue'
import MarketIntelligence from './MarketIntelligence.vue'
import KnowledgeBase from './KnowledgeBase.vue'
import SystemConfigPage from './SystemConfigPage.vue'
import { isDark, toggleDark } from '../composables/useTheme'

const activePage = ref(localStorage.getItem('activePage') || 'chat')
const showMoreMenu = ref(false)

watch(activePage, (val) => {
  localStorage.setItem('activePage', val)
})

// ── 底部 Tab 栏配置 ──
const tabs = [
  { key: 'dashboard', label: '看板', icon: 'dashboard' },
  { key: 'chat', label: '对话', icon: 'chat' },
  { key: 'portfolio', label: '持仓', icon: 'portfolio' },
  { key: 'valuation', label: '估值', icon: 'valuation' },
  { key: '__more__', label: '更多', icon: 'more' },
]

// ── 更多菜单项 ──
const moreItems = [
  { key: 'market-intelligence', label: '市场热点', icon: 'fire' },
  { key: 'articles', label: '文章管理', icon: 'articles' },
  { key: 'gallery', label: '估值图片', icon: 'gallery' },
  { key: 'author', label: '作者文章', icon: 'author' },
  { key: 'linked', label: '个人文档', icon: 'link' },
  { key: 'knowledge', label: '知识库', icon: 'book' },
  { key: 'rag', label: 'RAG 分析', icon: 'rag' },
  { key: 'rag-test', label: '命中测试', icon: 'test' },
  { key: 'bond', label: '债市温度', icon: 'bond' },
  { key: 'admin-agents', label: 'Agent 管理', icon: 'admin' },
  { key: 'token-usage', label: 'Token 用量', icon: 'token' },
  { key: 'system-config', label: '系统配置', icon: 'config' },
  { key: 'quality-dashboard', label: '质量仪表盘', icon: 'chart' },
  { key: 'bad-cases', label: 'Bad Case', icon: 'bug' },
  { key: 'eval-suite', label: '评测集', icon: 'check' },
  { key: 'health', label: '健康分', icon: 'health' },
  { key: 'shadow', label: 'Shadow Mode', icon: 'shadow' },
]

function navigate(key) {
  if (key === '__more__') {
    showMoreMenu.value = !showMoreMenu.value
    return
  }
  showMoreMenu.value = false
  activePage.value = key
}

// ── 页面组件映射 ──
const pageComponents = {
  dashboard: Dashboard,
  'market-intelligence': MarketIntelligence,
  chat: ChatView,
  articles: ArticleManagement,
  valuation: ValuationHistory,
  gallery: ImageGallery,
  author: AuthorArticles,
  linked: LinkedArticles,
  knowledge: KnowledgeBase,
  rag: RagAnalysis,
  'rag-test': RagTestPage,
  bond: BondMarket,
  portfolio: PortfolioManagement,
  'admin-agents': AdminAgentsPage,
  'token-usage': TokenUsagePage,
  'quality-dashboard': QualityDashboard,
  'bad-cases': BadCasePage,
  'eval-suite': EvalSuitePage,
  'health': HealthScore,
  'shadow': ShadowModePage,
  'system-config': SystemConfigPage,
}

const pageComponent = computed(() => pageComponents[activePage.value] || null)
const currentPageLabel = computed(() => {
  const t = tabs.find(t => t.key === activePage.value)
  if (t) return t.label
  const m = moreItems.find(m => m.key === activePage.value)
  return m?.label || ''
})

const pageProps = computed(() => {
  if (activePage.value === 'dashboard') {
    return { onNavigate: (page) => navigate(page) }
  }
  return {}
})

const isChatPage = computed(() => activePage.value === 'chat')

// ── 分析页状态 ──
const taskListRef = ref(null)
const currentTaskId = ref(null)
const analysisUrl = ref('')
const submitting = ref(false)

async function onSubmitAnalysis() {
  const trimmed = analysisUrl.value.trim()
  if (!trimmed) return
  submitting.value = true
  try {
    const { data } = await createTask(trimmed)
    currentTaskId.value = data.task_id
    analysisUrl.value = ''
    activePage.value = 'analysis'
    taskListRef.value?.loadTasks()
  } catch (e) {
    alert('创建任务失败: ' + e.message)
  } finally {
    submitting.value = false
  }
}

function onSelectTask(taskId) {
  currentTaskId.value = taskId
  activePage.value = 'analysis'
}

function onBack() {
  currentTaskId.value = null
}
</script>

<template>
  <div class="mobile-app">
    <!-- 顶部栏 -->
    <header class="mobile-header">
      <span class="mobile-title editorial-title">{{ currentPageLabel || '投资分析助手' }}</span>
      <button class="mobile-theme-btn" @click="toggleDark" :title="isDark ? '亮色模式' : '暗色模式'">
        <svg v-if="isDark" width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z"/>
        </svg>
        <svg v-else width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z"/>
        </svg>
      </button>
    </header>

    <!-- 内容区 -->
    <div :class="['mobile-content', { 'is-chat': isChatPage }]">
      <!-- 分析页（特殊处理） -->
      <template v-if="activePage === 'analysis'">
        <div v-if="!currentTaskId" class="mobile-analysis">
          <h2 class="mobile-page-title editorial-title">AI 投资分析</h2>
          <form @submit.prevent="onSubmitAnalysis" class="mobile-analysis-form">
            <input
              v-model="analysisUrl"
              type="url"
              placeholder="粘贴公众号文章链接..."
              class="input-field"
              :disabled="submitting"
            />
            <button type="submit" :disabled="submitting || !analysisUrl.trim()" class="btn-primary">
              {{ submitting ? '提交中...' : '开始分析' }}
            </button>
          </form>
          <TaskList ref="taskListRef" @select="onSelectTask" @newTask="currentTaskId = null" />
        </div>
        <TaskDetail v-else :taskId="currentTaskId" @back="onBack" />
      </template>

      <!-- 常规页面 -->
      <KeepAlive v-else>
        <component :is="pageComponent" v-bind="pageProps" />
      </KeepAlive>
    </div>

    <!-- 更多菜单弹层 -->
    <Transition name="fade">
      <div v-if="showMoreMenu" class="mobile-more-overlay" @click.self="showMoreMenu = false">
        <div class="mobile-more-sheet">
          <div class="mobile-more-header">
            <span class="editorial-title">全部功能</span>
            <button @click="showMoreMenu = false" class="mobile-more-close">
              <svg width="20" height="20" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
              </svg>
            </button>
          </div>
          <div class="mobile-more-grid">
            <button
              v-for="item in moreItems"
              :key="item.key"
              @click="navigate(item.key)"
              :class="['mobile-more-item', { active: activePage === item.key }]"
            >
              <span class="mobile-more-label">{{ item.label }}</span>
            </button>
          </div>
        </div>
      </div>
    </Transition>

    <!-- 底部 Tab 栏 -->
    <nav class="mobile-tabbar">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        @click="navigate(tab.key)"
        :class="['mobile-tab', { active: activePage === tab.key || (tab.key === '__more__' && showMoreMenu) }]"
      >
        <!-- 看板 -->
        <svg v-if="tab.icon === 'dashboard'" class="mobile-tab-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6"/>
        </svg>
        <!-- 对话 -->
        <svg v-else-if="tab.icon === 'chat'" class="mobile-tab-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"/>
        </svg>
        <!-- 持仓 -->
        <svg v-else-if="tab.icon === 'portfolio'" class="mobile-tab-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M2 7l10-4 10 4-10 4-10-4zM2 17l10 4 10-4M2 12l10 4 10-4"/>
        </svg>
        <!-- 估值 -->
        <svg v-else-if="tab.icon === 'valuation'" class="mobile-tab-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/>
        </svg>
        <!-- 更多 -->
        <svg v-else-if="tab.icon === 'more'" class="mobile-tab-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M4 6h16M4 12h16M4 18h16"/>
        </svg>
        <span class="mobile-tab-label">{{ tab.label }}</span>
      </button>
    </nav>
  </div>
</template>

<style scoped>
.mobile-app {
  display: flex;
  flex-direction: column;
  height: 100vh;
  height: 100dvh;
  background: var(--color-bg);
  overflow: hidden;
}

/* ── 顶部栏 ── */
.mobile-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.7rem 1rem;
  padding-top: calc(0.7rem + env(safe-area-inset-top, 0px));
  background: var(--glass-bg);
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
  position: sticky;
  top: 0;
  z-index: 40;
  backdrop-filter: blur(24px) saturate(180%);
  -webkit-backdrop-filter: blur(24px) saturate(180%);
  box-shadow: var(--shadow-sm);
}

.mobile-header::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  bottom: -1px;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--color-gold) 50%, transparent);
  opacity: 0.35;
}

.mobile-title {
  font-size: 1.05rem;
  color: var(--color-text-primary);
  letter-spacing: -0.02em;
}

.mobile-theme-btn {
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-md);
  color: var(--color-text-secondary);
  background: transparent;
  border: none;
  cursor: pointer;
  transition: all var(--transition-fast);
}
.mobile-theme-btn:hover {
  background: var(--color-bg-hover);
  color: var(--color-text-primary);
}
.mobile-theme-btn:active {
  transform: var(--press-scale);
}

/* ── 内容区 ── */
.mobile-content {
  flex: 1;
  overflow-y: auto;
  -webkit-overflow-scrolling: touch;
  overscroll-behavior-y: contain;
  padding: 0.75rem;
  padding-bottom: calc(0.75rem + env(safe-area-inset-bottom, 0px));
}

/* ChatView 自行管理滚动，外层不滚动 */
.mobile-content.is-chat {
  display: flex;
  flex-direction: column;
  overflow: hidden;
  padding: 0;
}

/* ── 分析页 ── */
.mobile-analysis {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.mobile-page-title {
  font-size: 1.1rem;
  color: var(--color-text-primary);
}

.mobile-analysis-form {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

/* ── 更多菜单弹层 — Bottom Sheet ── */
.mobile-more-overlay {
  position: fixed;
  inset: 0;
  background: var(--color-overlay);
  z-index: 50;
  display: flex;
  align-items: flex-end;
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
  animation: fade-in 0.2s ease;
}
@keyframes fade-in {
  from { opacity: 0; }
  to { opacity: 1; }
}

.mobile-more-sheet {
  width: 100%;
  max-height: 70vh;
  background: var(--color-bg-card);
  border-radius: var(--radius-2xl) var(--radius-2xl) 0 0;
  padding: 0.75rem 1rem 1rem;
  overflow-y: auto;
  -webkit-overflow-scrolling: touch;
  padding-bottom: calc(1rem + env(safe-area-inset-bottom, 0px));
  animation: sheet-up 0.3s cubic-bezier(0.34, 1.2, 0.64, 1);
  box-shadow: var(--shadow-lg);
}
@keyframes sheet-up {
  from { transform: translateY(100%); }
  to { transform: translateY(0); }
}
.mobile-more-sheet::before {
  content: '';
  display: block;
  width: 36px;
  height: 4px;
  border-radius: 2px;
  background: var(--color-text-muted);
  opacity: 0.4;
  margin: 0 auto 0.75rem;
}

.mobile-more-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 1rem;
  font-size: 1rem;
  color: var(--color-text-primary);
}

.mobile-more-close {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-md);
  color: var(--color-text-secondary);
  background: transparent;
  border: none;
  cursor: pointer;
}

.mobile-more-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 0.6rem;
}

.mobile-more-item {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0.95rem 0.5rem;
  border-radius: var(--radius-lg);
  background: var(--color-bg-input);
  border: 1px solid var(--color-border-light);
  color: var(--color-text-secondary);
  font-size: 0.85rem;
  font-weight: 500;
  cursor: pointer;
  transition: all var(--transition-fast);
  min-height: 52px;
  position: relative;
  overflow: hidden;
}
.mobile-more-item::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 2px;
  background: transparent;
  transition: background var(--transition-fast);
}

.mobile-more-item.active {
  background: var(--color-primary-bg);
  border-color: var(--color-primary-border);
  color: var(--color-primary);
  font-weight: 600;
}
.mobile-more-item.active::before {
  background: var(--gradient-primary);
}

.mobile-more-item:active {
  transform: scale(0.95);
  background: var(--color-bg-hover);
}

/* ── 底部 Tab 栏 ── */
.mobile-tabbar {
  display: flex;
  align-items: stretch;
  background: var(--glass-bg);
  border-top: 1px solid var(--color-border);
  flex-shrink: 0;
  position: sticky;
  bottom: 0;
  z-index: 40;
  padding-bottom: env(safe-area-inset-bottom, 0);
  backdrop-filter: blur(24px) saturate(180%);
  -webkit-backdrop-filter: blur(24px) saturate(180%);
  box-shadow: var(--shadow-sm);
}

.mobile-tab {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.15rem;
  padding: 0.45rem 0;
  color: var(--color-text-muted);
  background: transparent;
  border: none;
  cursor: pointer;
  transition: color var(--transition-fast), transform var(--transition-fast);
  -webkit-tap-highlight-color: transparent;
  position: relative;
}

.mobile-tab.active {
  color: var(--color-primary);
}

/* Active indicator */
.mobile-tab.active::after {
  content: '';
  position: absolute;
  top: 0;
  left: 50%;
  transform: translateX(-50%);
  width: 28px;
  height: 3px;
  border-radius: 2px;
  background: linear-gradient(90deg, var(--color-gold) 0%, var(--color-primary) 100%);
  box-shadow: 0 0 12px var(--color-primary-glow-strong);
}

/* Tab icon animation */
.mobile-tab:active .mobile-tab-icon {
  transform: scale(0.85);
}
.mobile-tab.active .mobile-tab-icon {
  animation: tabBounce 0.3s ease;
}

@keyframes tabBounce {
  0% { transform: scale(1); }
  50% { transform: scale(1.15); }
  100% { transform: scale(1); }
}

.mobile-tab-icon {
  width: 23px;
  height: 23px;
  transition: transform var(--transition-fast);
}

.mobile-tab-label {
  font-size: 0.66rem;
  font-weight: 600;
  letter-spacing: -0.01em;
}

/* ── 动画 ── */
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.25s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
