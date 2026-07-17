<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { createTask, getFinanceQuoteBar } from '../api'
import Icon from '../components/ui/Icon.vue'
import TaskList from '../components/task/TaskList.vue'
import TaskDetail from '../components/task/TaskDetail.vue'
import ArticleManagement from '../components/knowledge/ArticleManagement.vue'
import ValuationHistory from '../components/valuation/ValuationHistory.vue'
import ImageGallery from '../components/knowledge/ImageGallery.vue'
import ChatView from '../components/ChatView.vue'
import AuthorArticles from '../components/knowledge/AuthorArticles.vue'
import LinkedArticles from '../components/knowledge/LinkedArticles.vue'
import BondMarket from '../components/valuation/BondMarket.vue'
import RagAnalysis from '../components/agent/RagAnalysis.vue'
import RagTestPage from '../components/agent/RagTestPage.vue'
import PortfolioManagement from '../components/portfolio/PortfolioManagement.vue'
import AlertCenter from '../components/task/AlertCenter.vue'
import GoalBucketsPage from '../components/decision/GoalBucketsPage.vue'
import AllocationDashboard from '../components/portfolio/AllocationDashboard.vue'
import StrategySandboxPage from '../components/decision/StrategySandboxPage.vue'
import FamilyFinanceDashboard from '../components/family/FamilyFinanceDashboard.vue'
import DecisionRecordsPage from '../components/decision/DecisionRecordsPage.vue'
import AdminAgentsPage from '../components/agent/AdminAgentsPage.vue'
import AnalysisLogPage from '../components/analysis/AnalysisLogPage.vue'
import TokenUsagePage from '../components/quality/TokenUsagePage.vue'
import BadCasePage from '../components/quality/BadCasePage.vue'
import EvalSuitePage from '../components/quality/EvalSuitePage.vue'
import QualityDashboard from '../components/quality/QualityDashboard.vue'
import Dashboard from '../components/Dashboard.vue'
import MarketIntelligence from '../components/market/MarketIntelligence.vue'
import SystemConfigPage from '../components/quality/SystemConfigPage.vue'
import DataHealthDashboard from '../components/quality/DataHealthDashboard.vue'
import KnowledgeBase from '../components/knowledge/KnowledgeBase.vue'
import SearchResults from '../components/knowledge/SearchResults.vue'
import HealthScore from '../components/analysis/HealthScore.vue'
import ShadowModePage from '../components/agent/ShadowModePage.vue'
import AttributionReport from '../components/market/AttributionReport.vue'
import BehaviorDiagnosis from '../components/market/BehaviorDiagnosis.vue'
import DecisionAccuracy from '../components/decision/DecisionAccuracy.vue'
import StrategyBacktest from '../components/decision/StrategyBacktest.vue'
import CapabilityCenter from '../components/agent/CapabilityCenter.vue'
import SmartAddPlan from '../components/finance/SmartAddPlan.vue'
import EventRadarPage from '../components/market/EventRadarPage.vue'
import HealthDashboardV2 from '../components/health/HealthDashboardV2.vue'
import { pageComponentKeys } from '../pageRegistry'

const props = defineProps({
  activePage: String,
  searchQuery: { type: String, default: '' },
})
const emit = defineEmits(['navigate'])

// ── KeepAlive 页面（除 analysis 外的所有页面）──
const pageComponents = {
  dashboard: Dashboard,
  'family-finance': FamilyFinanceDashboard,
  'market-intelligence': MarketIntelligence,
  'event-radar': EventRadarPage,
  chat: ChatView,
  articles: ArticleManagement,
  valuation: ValuationHistory,
  gallery: ImageGallery,
  author: AuthorArticles,
  linked: LinkedArticles,
  rag: RagAnalysis,
  'rag-test': RagTestPage,
  bond: BondMarket,
  portfolio: PortfolioManagement,
  'alert-center': AlertCenter,
  'goal-buckets': GoalBucketsPage,
  'allocation-dashboard': AllocationDashboard,
  'strategy-sandbox': StrategySandboxPage,
  'decisions': DecisionRecordsPage,
  'admin-agents': AdminAgentsPage,
  'analysis-log': AnalysisLogPage,
  'token-usage': TokenUsagePage,
  'quality-dashboard': QualityDashboard,
  'bad-cases': BadCasePage,
  'eval-suite': EvalSuitePage,
  'system-config': SystemConfigPage,
  'data-health': DataHealthDashboard,
  knowledge: KnowledgeBase,
  search: SearchResults,
  health: HealthScore,
  shadow: ShadowModePage,
  attribution: AttributionReport,
  behavior: BehaviorDiagnosis,
  accuracy: DecisionAccuracy,
  'strategy-backtest': StrategyBacktest,
  'capability-center': CapabilityCenter,
  'smart-add': SmartAddPlan,
  'health-v2': HealthDashboardV2,
}

if (import.meta.env.DEV) {
  const missingComponents = pageComponentKeys.filter(key => !pageComponents[key])
  if (missingComponents.length) console.warn('[navigation] Missing page components:', missingComponents)
}

const pageComponent = computed(() => pageComponents[props.activePage] || null)
const pageProps = computed(() => {
  if (props.activePage === 'dashboard') {
    return { onNavigate: (page) => emit('navigate', page) }
  }
  if (props.activePage === 'search') {
    return { query: props.searchQuery, onNavigate: (page) => emit('navigate', page) }
  }
  if (props.activePage === 'alert-center') {
    return { onNavigate: (page) => emit('navigate', page) }
  }
  if (props.activePage === 'portfolio') {
    return { onNavigate: (page) => emit('navigate', page) }
  }
  if (props.activePage === 'chat') {
    return { onNavigate: (page) => emit('navigate', page) }
  }
  return {}
})

const taskListRef = ref(null)
const currentTaskId = ref(null)
const url = ref('')
const submitting = ref(false)

// ── 全局理财彩蛋 ──
const quoteText = ref('')
const hotKeywords = ref([])
const quoteVisible = ref(true)
let quoteTimer = null

async function loadQuoteBar() {
  try {
    const { data } = await getFinanceQuoteBar()
    quoteText.value = data.quote || ''
    hotKeywords.value = data.hot_keywords || []
    quoteVisible.value = true
  } catch (_) {
    // fallback 本地语录
    const fallbacks = [
      '别人贪婪时恐惧，别人恐惧时贪婪。—— 巴菲特',
      '投资不是比谁聪明，而是比谁更有耐心。',
      '复利是世界第八大奇迹。—— 爱因斯坦',
      '市场永远在波动，情绪稳定才是最大的优势。',
    ]
    quoteText.value = fallbacks[Math.floor(Math.random() * fallbacks.length)]
  }
}

function switchQuote() {
  quoteVisible.value = false
  setTimeout(async () => {
    await loadQuoteBar()
    quoteVisible.value = true
  }, 300)
}

function startQuoteRotation() {
  stopQuoteRotation()
  quoteTimer = setInterval(switchQuote, 10000)
}
function stopQuoteRotation() {
  if (quoteTimer) { clearInterval(quoteTimer); quoteTimer = null }
}

onMounted(() => {
  loadQuoteBar()
  startQuoteRotation()
})
onUnmounted(() => {
  stopQuoteRotation()
})

async function onSubmit() {
  const trimmed = url.value.trim()
  if (!trimmed) return
  submitting.value = true
  try {
    const { data } = await createTask(trimmed)
    currentTaskId.value = data.task_id
    url.value = ''
    emit('navigate', 'analysis')
    taskListRef.value?.loadTasks()
  } catch (e) {
    alert('创建任务失败: ' + e.message)
  } finally {
    submitting.value = false
  }
}

function onSelectTask(taskId) {
  currentTaskId.value = taskId
  emit('navigate', 'analysis')
}

function onNewTask() {
  currentTaskId.value = null
}

function onBack() {
  currentTaskId.value = null
}
</script>

<template>
  <div class="home">
    <!-- 全局理财彩蛋栏 -->
    <div class="global-quote-bar" @click="switchQuote">
      <Transition name="qfade" mode="out-in">
        <div v-if="quoteVisible" key="quote" class="quote-content">
          <Icon name="lightbulb" size="14" class="quote-icon" />
          <span class="quote-main">{{ quoteText }}</span>
          <span class="quote-hint">点击换一条</span>
        </div>
        <div v-else key="loading" class="quote-content" style="opacity:0.3">加载中...</div>
      </Transition>
      <div v-if="hotKeywords.length" class="quote-hot">
        <span v-for="kw in hotKeywords" :key="kw" class="quote-hot-tag">{{ kw }}</span>
      </div>
    </div>
    <!-- AI 分析页（持续挂载，用 v-show 切换可见性，避免 TaskDetail 被卸载丢状态） -->
    <div v-show="activePage === 'analysis'" class="page-section">
      <div v-if="!currentTaskId" class="card analysis-card">
        <div class="analysis-header">
          <h2 class="page-title">AI 投资分析</h2>
          <p class="page-desc">粘贴公众号文章链接，AI 自动解读投资机会</p>
        </div>
        <form @submit.prevent="onSubmit" class="analysis-form">
          <input
            v-model="url"
            type="url"
            placeholder="https://mp.weixin.qq.com/s/..."
            class="input-field analysis-input"
            :disabled="submitting"
          />
          <button
            type="submit"
            :disabled="submitting || !url.trim()"
            class="btn-primary analysis-btn"
          >
            <svg v-if="submitting" class="spinner" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M12 2v4m0 12v4m-7.07-3.93l2.83-2.83m8.48-8.48l2.83-2.83M2 12h4m12 0h4M4.93 4.93l2.83 2.83m8.48 8.48l2.83 2.83"/>
            </svg>
            {{ submitting ? '提交中...' : '开始分析' }}
          </button>
        </form>
        <p class="analysis-hint">支持微信公众号文章链接</p>
        <div class="task-list-section">
          <TaskList
            ref="taskListRef"
            @select="onSelectTask"
            @newTask="onNewTask"
          />
        </div>
      </div>
      <TaskDetail v-else :taskId="currentTaskId" @back="onBack" />
    </div>

    <!-- 其他页面使用 KeepAlive 缓存（ChatView 的流式状态由 useStreamingState composable 管理，不受 KeepAlive 影响） -->
    <KeepAlive>
      <component v-if="activePage !== 'analysis' && pageComponent" :is="pageComponent" :key="activePage" v-bind="pageProps" />
    </KeepAlive>
  </div>
</template>

<style scoped>
.home {
  max-width: 1200px;
  margin: 0 auto;
}

/* ── 页面切换过渡 ── */
.page-section {
  animation: pageIn 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
}
@keyframes pageIn {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* ── 全局理财彩蛋栏 ── */
.global-quote-bar {
  background: var(--gradient-quote);
  border-radius: var(--radius-lg);
  padding: 0.7rem 1.25rem;
  margin-bottom: 1.25rem;
  display: flex;
  align-items: center;
  justify-content: space-between;
  cursor: pointer;
  user-select: none;
  min-height: 40px;
  gap: 0.75rem;
  border: 1px solid rgba(255, 255, 255, 0.06);
  box-shadow: var(--shadow-elevated);
  transition: all var(--transition-fast);
}
.global-quote-bar:hover {
  box-shadow: var(--shadow-floating);
  transform: var(--hover-lift);
}
.quote-content {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  min-width: 0;
  flex: 1;
}
.quote-icon {
  flex-shrink: 0;
  font-size: 0.9rem;
}
.quote-main {
  color: #e8eaed;
  font-size: 0.88rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  line-height: 1.5;
}
.quote-hint {
  color: var(--color-primary-300);
  font-size: 0.65rem;
  opacity: 0.5;
  flex-shrink: 0;
}
.dark .quote-hint {
  color: #d4b65a;
}
.quote-hot {
  display: flex;
  gap: 0.4rem;
  flex-shrink: 0;
  flex-wrap: wrap;
}
.quote-hot-tag {
  background: rgba(255, 255, 255, 0.1);
  color: #93c5fd;
  font-size: 0.72rem;
  padding: 0.2rem 0.55rem;
  border-radius: 999px;
  white-space: nowrap;
  border: 1px solid rgba(255, 255, 255, 0.08);
  transition: all var(--transition-fast);
}
.global-quote-bar:hover .quote-hot-tag {
  background: rgba(255, 255, 255, 0.15);
}
.dark .quote-hot-tag {
  background: rgba(212, 168, 67, 0.12);
  color: #d4b65a;
  border-color: rgba(212, 168, 67, 0.15);
}
.dark .global-quote-bar:hover .quote-hot-tag {
  background: rgba(212, 168, 67, 0.18);
}
.qfade-enter-active, .qfade-leave-active {
  transition: opacity 0.3s ease, transform 0.3s ease;
}
.qfade-enter-from {
  opacity: 0;
  transform: translateY(6px);
}
.qfade-leave-to {
  opacity: 0;
  transform: translateY(-6px);
}

.page-section {
  animation: fadeIn 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
}

@keyframes fadeIn {
  from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: translateY(0); }
}

/* Analysis card */
.analysis-card {
  padding: 0;
  overflow: hidden;
}

.analysis-header {
  text-align: center;
  padding: 2.5rem 2rem 1.5rem;
}

.analysis-form {
  display: flex;
  gap: 0.75rem;
  max-width: 640px;
  margin: 0 auto;
  padding: 0 2rem;
}

.analysis-input {
  flex: 1;
  padding: 0.75rem 1rem;
  font-size: 0.9rem;
}

.analysis-btn {
  padding: 0.75rem 1.5rem;
  white-space: nowrap;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.analysis-hint {
  text-align: center;
  font-size: 0.78rem;
  color: var(--color-text-muted);
  margin: 0.85rem 0 0 0;
  padding-bottom: 1.5rem;
  line-height: 1.5;
}

.task-list-section {
  border-top: 1px solid var(--color-border);
}

@media (max-width: 640px) {
  .analysis-form {
    flex-direction: column;
    padding: 0 1.5rem;
  }
  .analysis-btn {
    justify-content: center;
  }
}
</style>
