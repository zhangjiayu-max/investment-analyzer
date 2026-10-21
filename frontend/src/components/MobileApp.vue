<script setup>
import { ref, computed, onMounted, onUnmounted, watch, defineAsyncComponent } from 'vue'
import { createTask, getFinanceQuoteBar } from '../api'
// 首屏必需组件保持静态 import（首屏即用，避免 Loading 闪烁）
import Dashboard from './Dashboard.vue'
import ChatView from './ChatView.vue'
import { isDark, toggleDark } from '../composables/useTheme'
import AlertBell from './task/AlertBell.vue'
import MobileNotificationCenter from './mobile/MobileNotificationCenter.vue'
import Icon from './ui/Icon.vue'
import { mobileMoreGroups, mobileTabs, getVisibleMobileMoreGroups } from '../mobileNavigation.js'
import { useAppNavigation } from '../composables/useAppNavigation'
import { isDevMode, setDevMode } from '../navigation.js'
import { trackPageEnter, trackPageLeave } from '../composables/useTracking'

// 懒加载组件：defineAsyncComponent 让 Vite 自动按动态 import 做代码分割
// 首屏 JS 预计降 40%+，低频页面（系统管理/质量评测等）按需加载

import TaskList from './task/TaskList.vue'
import TaskDetail from './task/TaskDetail.vue'
const ArticleManagement = defineAsyncComponent(() => import('./knowledge/ArticleManagement.vue'))
const ValuationHistory = defineAsyncComponent(() => import('./valuation/ValuationHistory.vue'))
const ImageGallery = defineAsyncComponent(() => import('./knowledge/ImageGallery.vue'))
const AuthorArticles = defineAsyncComponent(() => import('./knowledge/AuthorArticles.vue'))
const LinkedArticles = defineAsyncComponent(() => import('./knowledge/LinkedArticles.vue'))
const BondMarket = defineAsyncComponent(() => import('./valuation/BondMarket.vue'))
const RagAnalysis = defineAsyncComponent(() => import('./agent/RagAnalysis.vue'))
const RagTestPage = defineAsyncComponent(() => import('./agent/RagTestPage.vue'))
const PortfolioManagement = defineAsyncComponent(() => import('./portfolio/PortfolioManagement.vue'))
const AlertCenter = defineAsyncComponent(() => import('./task/AlertCenter.vue'))
const GoalBucketsPage = defineAsyncComponent(() => import('./decision/GoalBucketsPage.vue'))
const FamilyFinanceDashboard = defineAsyncComponent(() => import('./family/FamilyFinanceDashboard.vue'))
const DecisionRecordsPage = defineAsyncComponent(() => import('./decision/DecisionRecordsPage.vue'))
const BehaviorDiagnosis = defineAsyncComponent(() => import('./market/BehaviorDiagnosis.vue'))
const AdminAgentsPage = defineAsyncComponent(() => import('./agent/AdminAgentsPage.vue'))
const TokenUsagePage = defineAsyncComponent(() => import('./quality/TokenUsagePage.vue'))
const BadCasePage = defineAsyncComponent(() => import('./quality/BadCasePage.vue'))
const EvalSuitePage = defineAsyncComponent(() => import('./quality/EvalSuitePage.vue'))
const HealthScore = defineAsyncComponent(() => import('./analysis/HealthScore.vue'))
const ShadowModePage = defineAsyncComponent(() => import('./agent/ShadowModePage.vue'))
const QualityDashboard = defineAsyncComponent(() => import('./quality/QualityDashboard.vue'))
const MarketIntelligence = defineAsyncComponent(() => import('./market/MarketIntelligence.vue'))
const EventRadarPage = defineAsyncComponent(() => import('./market/EventRadarPage.vue'))
const KnowledgeBase = defineAsyncComponent(() => import('./knowledge/KnowledgeBase.vue'))
const SystemConfigPage = defineAsyncComponent(() => import('./quality/SystemConfigPage.vue'))
const CapabilityCenter = defineAsyncComponent(() => import('./agent/CapabilityCenter.vue'))
const SmartAddPlan = defineAsyncComponent(() => import('./finance/SmartAddPlan.vue'))
const AllocationDashboard = defineAsyncComponent(() => import('./portfolio/AllocationDashboard.vue'))
const AttributionReport = defineAsyncComponent(() => import('./market/AttributionReport.vue'))
const DecisionAccuracy = defineAsyncComponent(() => import('./decision/DecisionAccuracy.vue'))
const StrategySandboxPage = defineAsyncComponent(() => import('./decision/StrategySandboxPage.vue'))
const DataHealthDashboard = defineAsyncComponent(() => import('./quality/DataHealthDashboard.vue'))
const StrategyBacktest = defineAsyncComponent(() => import('./decision/StrategyBacktest.vue'))
const AnalysisLogPage = defineAsyncComponent(() => import('./analysis/AnalysisLogPage.vue'))
const HealthDashboardV2 = defineAsyncComponent(() => import('./health/HealthDashboardV2.vue'))
const DecisionCenter = defineAsyncComponent(() => import('./decision/DecisionCenter.vue'))
const FeatureUsagePage = defineAsyncComponent(() => import('./analysis/FeatureUsagePage.vue'))

const { activePage } = useAppNavigation()
watch(activePage, (next, previous) => {
  if (previous) trackPageLeave(previous)
  trackPageEnter(next || 'dashboard')
}, { immediate: true })
onUnmounted(() => trackPageLeave(activePage.value || 'dashboard'))
const showMoreMenu = ref(false)
const showNotificationCenter = ref(false)
const devMode = ref(isDevMode())



// 扁平化 moreItems（兼容 currentPageLabel 查找，含隐藏项以便直接导航仍可显示标题）
const moreItems = mobileMoreGroups.flatMap(g => g.items)
// 更多菜单展示用：按开发者模式过滤
const visibleMoreGroups = computed(() => getVisibleMobileMoreGroups(devMode.value))

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
  'event-radar': EventRadarPage,
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
  'alert-center': AlertCenter,
  'decisions': DecisionRecordsPage,
  'behavior': BehaviorDiagnosis,
  'family-finance': FamilyFinanceDashboard,
  'goal-buckets': GoalBucketsPage,
  'admin-agents': AdminAgentsPage,
  'analysis-log': AnalysisLogPage,
  'token-usage': TokenUsagePage,
  'quality-dashboard': QualityDashboard,
  'bad-cases': BadCasePage,
  'eval-suite': EvalSuitePage,
  'health': HealthScore,
  'shadow': ShadowModePage,
  'system-config': SystemConfigPage,
  'capability-center': CapabilityCenter,
  'smart-add': SmartAddPlan,
  'allocation-dashboard': AllocationDashboard,
  'attribution': AttributionReport,
  'accuracy': DecisionAccuracy,
  'strategy-sandbox': StrategySandboxPage,
  'data-health': DataHealthDashboard,
  'strategy-backtest': StrategyBacktest,
  'health-v2': HealthDashboardV2,
  'decision-center': DecisionCenter,
  'feature-usage': FeatureUsagePage,
}

const pageComponent = computed(() => pageComponents[activePage.value] || null)
const currentPageLabel = computed(() => {
  const t = mobileTabs.find(t => t.key === activePage.value)
  if (t) return t.label
  const m = moreItems.find(m => m.key === activePage.value)
  return m?.label || ''
})

const pageProps = computed(() => {
  if (activePage.value === 'dashboard') {
    return { onNavigate: (page) => navigate(page) }
  }
  // 与 Home.vue 对齐：需跨页跳转的页面透传 onNavigate
  if (['chat', 'alert-center', 'decisions', 'decision-center', 'family-finance', 'goal-buckets', 'portfolio', 'event-radar', 'smart-add'].includes(activePage.value)) {
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

function toggleDevMode() {
  devMode.value = !devMode.value
  setDevMode(devMode.value)
}
</script>

<template>
  <div class="mobile-app">
    <!-- 顶部栏 -->
    <header class="mobile-header">
      <span class="mobile-title editorial-title">{{ currentPageLabel || '投资分析助手' }}</span>
      <div class="mobile-header-actions">
        <AlertBell @navigate="navigate" />
        <button class="mobile-theme-btn" @click="toggleDark()" :title="isDark ? '亮色模式' : '暗色模式'">
          <svg v-if="isDark" width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z"/>
          </svg>
          <svg v-else width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z"/>
          </svg>
        </button>
      </div>
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
            <template v-for="group in visibleMoreGroups" :key="group.label">
              <div class="mobile-more-group-label">{{ group.label }}</div>
              <button
                v-for="item in group.items"
                :key="item.key"
                @click="navigate(item.key)"
                :class="['mobile-more-item', { active: activePage === item.key, hot: item.hot }]"
              >
                <span v-if="item.hot" class="mobile-more-hot"><Icon name="fire" size="11" /></span>
                <span class="mobile-more-label">{{ item.label }}</span>
              </button>
            </template>
          </div>
          <div class="mobile-more-footer">
            <button
              @click="toggleDevMode"
              :class="['mobile-dev-toggle', { active: devMode }]"
              :title="devMode ? '关闭开发者模式，回到精简视图' : '开启开发者模式，显示全部功能'"
            >
              <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"/>
                <circle cx="12" cy="12" r="3"/>
              </svg>
              <span>开发者模式</span>
            </button>
          </div>
        </div>
      </div>
    </Transition>

    <!-- 通知中心 -->
    <MobileNotificationCenter v-model="showNotificationCenter" />

    <!-- 底部 Tab 栏 -->
    <nav class="mobile-tabbar">
      <button
        v-for="tab in mobileTabs"
        :key="tab.key"
        @click="navigate(tab.key)"
        :class="['mobile-tab', { active: activePage === tab.key || (tab.key === '__more__' && showMoreMenu) }]"
      >
        <Icon :name="tab.icon" :size="22" class="mobile-tab-icon" />
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
.mobile-header-actions {
  display: flex;
  align-items: center;
  gap: 0.25rem;
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
  min-height: 0;           /* 允许内容区收缩，避免长看板把底部导航挤出视口 */
  min-width: 0;            /* 关键：覆盖 flex 默认 min-width:auto，防止子元素撑开 */
  width: 100%;
  overflow-y: auto;
  overflow-x: hidden;
  -webkit-overflow-scrolling: touch;
  overscroll-behavior-y: contain;
  overscroll-behavior-x: contain;  /* 阻止横向滚动链传播到外层 */
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

.mobile-more-group-label {
  grid-column: 1 / -1;
  font-size: 0.72rem;
  font-weight: 600;
  color: var(--color-text-tertiary);
  padding: 0.4rem 0 0.1rem;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  border-top: 1px solid var(--color-border-light);
  margin-top: 0.3rem;
}
.mobile-more-group-label:first-child {
  border-top: none;
  margin-top: 0;
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

/* 开发者模式切换 */
.mobile-more-footer {
  margin-top: 0.9rem;
  padding-top: 0.7rem;
  border-top: 1px solid var(--color-border-light);
}
.mobile-dev-toggle {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.45rem;
  width: 100%;
  padding: 0.6rem;
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border-light);
  background: transparent;
  color: var(--color-text-tertiary);
  font-size: 0.78rem;
  font-weight: 500;
  cursor: pointer;
  transition: all var(--transition-fast);
}
.mobile-dev-toggle.active {
  color: var(--color-primary);
  border-color: var(--color-primary-border);
  background: var(--color-primary-bg);
}

.mobile-more-hot {
  position: absolute;
  top: 4px;
  right: 4px;
  display: flex;
  color: var(--color-warning);
  line-height: 0;
  z-index: 1;
}
.mobile-more-item.hot {
  border-color: var(--color-warning-border, rgba(245, 158, 11, 0.3));
  background: var(--color-bg-input);
}
.mobile-more-item.hot.active {
  background: var(--color-primary-bg);
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
  font-size: 0.6875rem;
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
