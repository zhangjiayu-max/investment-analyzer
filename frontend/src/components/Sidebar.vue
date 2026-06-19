<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { isDark, toggleDark } from '../composables/useTheme'
import { getTokenUsageBudget, getKycQuestionnaire } from '../api'
import { navItems } from '../navigation'
import Icon from './ui/Icon.vue'
import KycWizard from './KycWizard.vue'

const props = defineProps({
  activePage: String,
  showKyc: { type: Boolean, default: false },
})
const emit = defineEmits(['navigate', 'close-kyc'])

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
// ── KYC 投资画像引导 ──────────────────────────────
const localShowKyc = ref(false)
const kycVisible = computed(() => props.showKyc || localShowKyc.value)
async function checkKycNeeded() {
  try {
    const { data } = await getKycQuestionnaire()
    if (!data.profile?.kyc_completed) localShowKyc.value = true
  } catch { /* silent */ }
}
function onKycCompleted() {
  // 画像更新后可在此触发刷新等操作
}

onMounted(() => {
  fetchTokenBudget()
  tokenTimer = setInterval(fetchTokenBudget, 60000) // 每分钟刷新
  checkKycNeeded()
})
onUnmounted(() => { clearInterval(tokenTimer) })

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
      <button @click="localShowKyc = true" class="nav-item kyc-entry" title="完善投资画像，让 AI 更懂你">
        <Icon name="evolution" size="18" class="nav-icon" />
        <span class="nav-label">我的投资画像</span>
      </button>
      <button @click="toggleDark()" class="nav-item theme-toggle" :title="isDark ? '切换到亮色模式' : '切换到暗色模式'">
        <Icon :name="isDark ? 'sun' : 'moon'" size="18" class="nav-icon" />
        <span class="nav-label">{{ isDark ? '亮色模式' : '暗色模式' }}</span>
      </button>
    </div>
  </aside>

  <!-- KYC 投资画像弹窗 -->
  <KycWizard :visible="kycVisible" @close="localShowKyc = false; emit('close-kyc')" @completed="onKycCompleted" />
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
  gap: 0.6rem;
  padding: 0.9rem 0.85rem;
  border-bottom: 1px solid var(--color-border);
}

.logo-icon {
  width: 32px;
  height: 32px;
  background: var(--color-primary);
  border-radius: var(--radius-sm);
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-size: 0.72rem;
  font-weight: 800;
  flex-shrink: 0;
  letter-spacing: -0.02em;
}
.dark .logo-icon {
  background: var(--color-primary);
}

.logo-text {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.logo-title {
  font-size: 0.82rem;
  font-weight: 600;
  color: var(--color-text-primary);
  line-height: 1.2;
}

.logo-sub {
  font-size: 0.62rem;
  color: var(--color-text-muted);
  letter-spacing: 0.02em;
}

.sidebar-nav {
  flex: 1;
  padding: 0.4rem 0.4rem;
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
  overflow-y: auto;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.42rem 0.6rem;
  border-radius: var(--radius-sm);
  color: var(--color-text-secondary);
  font-size: 0.8rem;
  font-weight: 500;
  transition: all var(--transition-fast);
  text-align: left;
  width: 100%;
  position: relative;
}

.nav-item:hover {
  background: var(--color-bg-hover);
  color: var(--color-text-primary);
}

.nav-item.active {
  background: var(--color-primary-50);
  color: var(--color-primary);
  font-weight: 600;
}
.nav-item.active::before {
  content: '';
  position: absolute;
  left: 0;
  top: 25%;
  bottom: 25%;
  width: 3px;
  background: var(--color-primary);
  border-radius: 0 2px 2px 0;
}
.dark .nav-item.active {
  background: var(--color-primary-bg);
  color: var(--color-primary-400);
}

/* Hot items */
.nav-item-hot {
  color: #d97706 !important;
}
.nav-item-hot .nav-icon {
  color: #f59e0b;
}
.nav-item-hot:hover {
  background: rgba(245, 158, 11, 0.08) !important;
}
.nav-item-hot.active {
  background: rgba(245, 158, 11, 0.12) !important;
  color: #b45309 !important;
}
.dark .nav-item-hot { color: #fbbf24 !important; }
.dark .nav-item-hot .nav-icon { color: #fbbf24; }
.dark .nav-item-hot.active { background: rgba(245, 158, 11, 0.15) !important; color: #f59e0b !important; }

.nav-icon {
  width: 18px;
  height: 18px;
  flex-shrink: 0;
}

.nav-label {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex: 1;
}

.nav-group-header { position: relative; }

.chevron {
  margin-left: auto;
  flex-shrink: 0;
  transition: transform var(--transition-fast);
  opacity: 0.4;
}
.chevron.expanded { transform: rotate(90deg); }

.nav-children {
  padding-left: 0.6rem;
  display: flex;
  flex-direction: column;
  gap: 0.05rem;
  position: relative;
}
.nav-children::before {
  content: '';
  position: absolute;
  left: 0.85rem;
  top: 0;
  bottom: 0.3rem;
  width: 1px;
  background: var(--color-border);
}

.nav-child {
  padding: 0.35rem 0.6rem;
  font-size: 0.76rem;
  gap: 0.45rem;
  color: var(--color-text-muted);
}
.nav-child:hover { color: var(--color-text-primary); }
.nav-child .nav-icon { width: 15px; height: 15px; opacity: 0.6; }
.nav-child.active { color: var(--color-primary); background: var(--color-primary-bg); }
.nav-child.active .nav-icon { opacity: 1; }
.dark .nav-child.active { color: var(--color-primary-400); }

/* Token 预算指示器 */
.token-meter {
  margin: 0.25rem 0.4rem;
  padding: 0.55rem 0.7rem;
  border-radius: var(--radius-md);
  background: var(--color-bg-input);
  border: 1px solid var(--color-border-light);
}

.token-meter-header {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  margin-bottom: 0.35rem;
}

.token-meter-icon { width: 13px; height: 13px; color: var(--color-text-muted); flex-shrink: 0; }
.token-meter-label { font-size: 0.68rem; color: var(--color-text-muted); flex: 1; }
.token-meter-value { font-size: 0.68rem; font-weight: 600; color: var(--color-text-secondary); font-variant-numeric: tabular-nums; }

.token-bar-track {
  height: 5px;
  border-radius: 2.5px;
  background: var(--color-border);
  position: relative;
  overflow: hidden;
}

.token-bar-fill {
  height: 100%;
  border-radius: 2.5px;
  transition: width 0.6s cubic-bezier(0.22, 1, 0.36, 1);
}

.token-bar-marker {
  position: absolute;
  top: -1px;
  bottom: -1px;
  width: 1px;
  background: var(--color-text-muted);
  opacity: 0.3;
}

.token-normal .token-bar-fill { background: linear-gradient(90deg, #059669, #34d399); }
.token-normal .token-meter-icon { color: #059669; }

.token-warning .token-bar-fill { background: linear-gradient(90deg, #d97706, #fbbf24); }
.token-warning .token-meter-icon { color: #d97706; }
.token-warning .token-meter-value { color: #d97706; }
.token-warning .token-meter { background: rgba(217, 119, 6, 0.06); }

.token-exceeded .token-bar-fill { background: linear-gradient(90deg, #dc2626, #f87171); }
.token-exceeded .token-meter-icon { color: #dc2626; }
.token-exceeded .token-meter-value { color: #dc2626; font-weight: 700; }
.token-exceeded .token-meter { background: rgba(220, 38, 38, 0.06); border-color: rgba(220, 38, 38, 0.12); }

.token-alert {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  margin-top: 0.3rem;
  font-size: 0.62rem;
  color: #dc2626;
  font-weight: 600;
}
.token-alert-warn { color: #d97706; }

.sidebar-bottom {
  padding: 0.4rem;
  border-top: 1px solid var(--color-border);
}

.theme-toggle { color: var(--color-text-muted); }

.kyc-entry { color: var(--color-primary); }
.kyc-entry .nav-icon { color: var(--color-primary); }
.kyc-entry:hover { background: var(--color-primary-50); }
.dark .kyc-entry { color: var(--color-primary-400); }
.dark .kyc-entry:hover { background: var(--color-primary-bg); }

/* 注：移动端走 MobileApp.vue 独立布局，Sidebar 在移动端不渲染，无需媒体查询 */
</style>
