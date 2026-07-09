<script setup>
import { ref, computed, onMounted } from 'vue'
import { getProfile } from '../../api'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  conversations: { type: Array, default: () => [] },
  selectedConv: { type: Object, default: null },
  streamStates: { type: Object, required: true },
  isTaskRunning: { type: Function, required: true },
  showMobileSidebar: { type: Boolean, default: false },
})

const emit = defineEmits(['select', 'new', 'delete', 'update:showMobileSidebar', 'open-kyc'])

// ── 画像面板 ──
const profileExpanded = ref(false)
const profile = ref(null)

const RISK_LABELS = { conservative: '保守', steady: '稳健', balanced: '平衡', aggressive: '进取', radical: '激进' }
const HORIZON_LABELS = { short: '短期(<1年)', medium: '中期(1-3年)', long: '长期(>3年)' }
const EXP_LABELS = { novice: '新手', intermediate: '中级', advanced: '高级', professional: '专业' }
const ASSET_LABELS = { index: '指数', fund: '基金', bond: '债券', stock: '股票', gold: '黄金', cash: '现金' }

async function loadProfile() {
  try {
    const { data } = await getProfile()
    profile.value = data
  } catch { /* silent */ }
}

onMounted(loadProfile)

const parsedAssets = computed(() => {
  if (!profile.value?.focus_assets) return []
  const v = profile.value.focus_assets
  return Array.isArray(v) ? v : (typeof v === 'string' ? JSON.parse(v) : [])
})
const parsedPositive = computed(() => {
  if (!profile.value?.positive_patterns) return []
  const v = profile.value.positive_patterns
  return Array.isArray(v) ? v : (typeof v === 'string' ? JSON.parse(v) : [])
})
const parsedNegative = computed(() => {
  if (!profile.value?.negative_patterns) return []
  const v = profile.value.negative_patterns
  return Array.isArray(v) ? v : (typeof v === 'string' ? JSON.parse(v) : [])
})

function formatTime(ts) {
  if (!ts) return ''
  const d = new Date(ts.replace(' ', 'T'))
  if (isNaN(d.getTime())) return ''
  const now = new Date()
  const isToday = d.toDateString() === now.toDateString()
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  const time = `${hh}:${mm}`
  if (isToday) return time
  return `${d.getMonth() + 1}/${d.getDate()} ${time}`
}

function handleSelect(conv) {
  emit('select', conv)
}

function handleNew() {
  emit('new')
}

function handleDelete(conv, e) {
  e.stopPropagation()
  emit('delete', conv)
}

function closeMobileSidebar() {
  emit('update:showMobileSidebar', false)
}

function openKycWizard() {
  window.dispatchEvent(new CustomEvent('open-kyc'))
}
</script>

<template>
  <!-- 移动端遮罩 -->
  <div v-if="showMobileSidebar" class="mobile-sidebar-overlay" @click="closeMobileSidebar"></div>
  <!-- 对话列表 -->
  <div :class="['conv-sidebar', { 'mobile-open': showMobileSidebar }]">
    <div class="conv-header">
      <h3>对话</h3>
      <button @click="handleNew()" class="btn-new-conv" title="新建对话">
        <svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/>
        </svg>
      </button>
    </div>
    <div class="conv-list">
      <div
        v-for="conv in conversations" :key="conv.id"
        @click="handleSelect(conv)"
        :class="['conv-item', { active: selectedConv?.id === conv.id, streaming: streamStates.has(conv.id) || isTaskRunning(conv.id) }]"
      >
        <div class="conv-icon">
          <span v-if="streamStates.has(conv.id) || isTaskRunning(conv.id)" class="conv-streaming-indicator">●</span>
          <span v-else>🤖</span>
        </div>
        <div class="conv-info">
          <div class="conv-title">{{ conv.title }}</div>
          <div class="conv-meta">
            <span v-if="isTaskRunning(conv.id)" class="conv-task-status">后台执行中...</span>
            <span class="conv-time font-jet">{{ formatTime(conv.updated_at) }}</span>
          </div>
        </div>
        <button @click="handleDelete(conv, $event)" class="btn-delete-conv" title="删除">
          <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
          </svg>
        </button>
      </div>
      <div v-if="!conversations.length" class="conv-empty">
        <p>暂无对话</p>
        <p class="conv-empty-hint">开始你的第一次投资分析对话吧</p>
        <button class="conv-empty-btn" @click="emit('new')">
          <Icon name="plus" size="12" />
          新建对话
        </button>
      </div>
    </div>

    <!-- 画像面板 -->
    <div class="profile-section">
      <button class="profile-toggle" @click="profileExpanded = !profileExpanded">
        <Icon name="circle-user" size="14" />
        <span>我的画像</span>
        <Icon :name="profileExpanded ? 'chevron-up' : 'chevron-down'" size="12" class="toggle-arrow" />
      </button>
      <Transition name="slide">
        <div v-if="profileExpanded && profile" class="profile-card">
          <div class="dim-row" v-if="profile.risk_tolerance">
            <span class="dim-label">风险偏好</span>
            <span class="dim-value">{{ RISK_LABELS[profile.risk_tolerance] || profile.risk_tolerance }}</span>
          </div>
          <div class="dim-row" v-if="profile.investment_horizon">
            <span class="dim-label">投资期限</span>
            <span class="dim-value">{{ HORIZON_LABELS[profile.investment_horizon] || profile.investment_horizon }}</span>
          </div>
          <div class="dim-row" v-if="profile.investment_experience">
            <span class="dim-label">投资经验</span>
            <span class="dim-value">{{ EXP_LABELS[profile.investment_experience] || profile.investment_experience }}</span>
          </div>
          <div class="dim-row" v-if="parsedAssets.length">
            <span class="dim-label">关注资产</span>
            <div class="asset-tags">
              <span v-for="a in parsedAssets" :key="a" class="asset-tag">{{ ASSET_LABELS[a] || a }}</span>
            </div>
          </div>
          <div v-if="profile.feedback_summary" class="feedback-summary">
            <Icon name="lightbulb" size="12" />
            <span>{{ profile.feedback_summary }}</span>
          </div>
          <div v-if="parsedPositive.length" class="pattern-section">
            <span class="pattern-label positive-label">✓ 偏好</span>
            <div class="pattern-tags">
              <span v-for="p in parsedPositive" :key="p" class="pattern-tag positive">{{ p }}</span>
            </div>
          </div>
          <div v-if="parsedNegative.length" class="pattern-section">
            <span class="pattern-label negative-label">✗ 避免</span>
            <div class="pattern-tags">
              <span v-for="p in parsedNegative" :key="p" class="pattern-tag negative">{{ p }}</span>
            </div>
          </div>
          <button class="btn-edit-profile" @click="openKycWizard">
            <Icon name="edit" size="12" /> 编辑画像
          </button>
        </div>
      </Transition>
    </div>
  </div>
</template>

<style scoped>
/* ── 对话侧边栏 ── */
.conv-sidebar {
  width: 280px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  border-right: 1px solid var(--color-border);
  background: var(--color-bg-input);
}

.conv-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1.1rem 1.25rem;
  border-bottom: 1px solid var(--color-border);
}

.conv-header h3 {
  margin: 0;
  font-size: 0.95rem;
  font-weight: 600;
}

.btn-new-conv {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-md);
  color: var(--color-primary-600);
  background: var(--color-primary-50);
  transition: all var(--transition-fast);
  box-shadow: 0 0 0 0 var(--color-primary-glow);
}
.btn-new-conv:hover {
  background: var(--color-primary-100);
  box-shadow: 0 0 10px var(--color-primary-glow);
  transform: var(--hover-lift);
}

.conv-list {
  flex: 1;
  overflow-y: auto;
  padding: 0.6rem;
}

.conv-item {
  display: flex;
  align-items: center;
  gap: 0.65rem;
  padding: 0.75rem 0.75rem;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all var(--transition-fast);
  position: relative;
}
.conv-item:hover {
  background: var(--color-bg-hover);
  transform: translateX(2px);
}
.conv-item.active {
  background: var(--color-primary-50);
  box-shadow: inset 3px 0 6px -2px var(--color-primary-glow-strong);
}
/* 左侧发光指示器 */
.conv-item.active::before {
  content: '';
  position: absolute;
  left: 0;
  top: 20%;
  bottom: 20%;
  width: 3px;
  background: var(--gradient-primary);
  border-radius: 0 2px 2px 0;
  box-shadow: 0 0 6px var(--color-primary-glow-strong);
}
.dark .conv-item.active {
  background: var(--color-primary-bg);
}

.conv-icon {
  font-size: 1.3rem;
  flex-shrink: 0;
  position: relative;
}

.conv-item.streaming {
  background: var(--color-primary-50);
}

.conv-streaming-indicator {
  color: var(--color-primary);
  animation: pulse-streaming 1.5s ease-in-out infinite;
  font-size: 1rem;
}

@keyframes pulse-streaming {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}

.conv-info {
  flex: 1;
  min-width: 0;
}

.conv-title {
  font-size: 0.82rem;
  font-weight: 500;
  color: var(--color-text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.conv-meta {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  margin-top: 0.15rem;
}

.conv-time {
  font-size: 0.65rem;
  color: var(--color-text-muted);
}

.conv-task-status {
  font-size: 0.65rem;
  color: var(--color-primary-600);
  background: var(--color-primary-50);
  padding: 0.05rem 0.3rem;
  border-radius: var(--radius-sm);
  animation: pulse-streaming 1.5s ease-in-out infinite;
}

.btn-delete-conv {
  opacity: 0;
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-sm);
  color: var(--color-text-muted);
  transition: all var(--transition-fast);
  flex-shrink: 0;
}

.conv-item:hover .btn-delete-conv {
  opacity: 1;
}

.btn-delete-conv:hover {
  color: var(--color-danger);
  background: rgba(239, 68, 68, 0.1);
}

.conv-empty {
  padding: 2rem 1rem;
  text-align: center;
  color: var(--color-text-muted);
}

.conv-empty p { margin: 0; font-size: 0.82rem; }
.conv-empty-hint { font-size: 0.75rem; margin-top: 0.35rem !important; }
.conv-empty-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  margin-top: 0.75rem;
  padding: 0.4rem 0.75rem;
  border-radius: var(--radius-sm);
  border: 1px solid var(--color-primary-border);
  background: var(--color-primary-bg-weak);
  color: var(--color-primary);
  font-size: 0.75rem;
  font-weight: 500;
  cursor: pointer;
  transition: all var(--transition-fast);
}
.conv-empty-btn:hover {
  background: var(--color-primary-bg);
  transform: translateY(-1px);
}

.mobile-sidebar-overlay {
  display: block;
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.4);
  z-index: 49;
}

@media (max-width: 768px) {
  .mobile-sidebar-overlay {
    display: block;
  }
  .conv-sidebar {
    position: fixed;
    left: 0;
    top: 0;
    bottom: 0;
    width: 280px;
    max-width: 80vw;
    z-index: 50;
    transform: translateX(-100%);
    transition: transform 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
    box-shadow: none;
  }
  .conv-sidebar.mobile-open {
    transform: translateX(0);
    box-shadow: 4px 0 24px rgba(0, 0, 0, 0.2);
  }

  .btn-new-conv {
    width: 44px;
    height: 44px;
  }

  .btn-delete-conv {
    min-width: 44px;
    min-height: 44px;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .conv-item {
    padding: 0.8rem 0.6rem;
    min-height: 48px;
  }
}

/* ── 画像面板 ── */
.profile-section {
  border-top: 1px solid var(--color-border);
}

.profile-toggle {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.7rem 1rem;
  font-size: 0.8rem;
  color: var(--color-text-secondary);
  transition: all var(--transition-fast);
}
.profile-toggle:hover {
  color: var(--color-primary);
  background: var(--color-primary-50);
}
.toggle-arrow { margin-left: auto; }

.profile-card {
  padding: 0.6rem 1rem 0.8rem;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  font-size: 0.75rem;
}

.dim-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
}
.dim-label { color: var(--color-text-muted); flex-shrink: 0; }
.dim-value { color: var(--color-text-primary); font-weight: 500; }

.asset-tags { display: flex; gap: 0.3rem; flex-wrap: wrap; justify-content: flex-end; }
.asset-tag {
  font-size: 0.65rem;
  padding: 0.1rem 0.4rem;
  border-radius: var(--radius-sm);
  background: var(--color-primary-50);
  color: var(--color-primary-600);
}

.feedback-summary {
  display: flex;
  align-items: flex-start;
  gap: 0.4rem;
  font-size: 0.7rem;
  color: var(--color-text-secondary);
  padding: 0.4rem;
  border-radius: var(--radius-sm);
  background: var(--color-bg-hover);
  line-height: 1.4;
}

.pattern-section {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}
.pattern-label { font-size: 0.7rem; font-weight: 500; }
.positive-label { color: var(--color-success, #10b981); }
.negative-label { color: var(--color-danger, #ef4444); }

.pattern-tags { display: flex; flex-wrap: wrap; gap: 0.25rem; }
.pattern-tag {
  font-size: 0.6rem;
  padding: 0.1rem 0.35rem;
  border-radius: var(--radius-sm);
}
.pattern-tag.positive {
  background: rgba(16, 185, 129, 0.1);
  color: var(--color-success, #10b981);
}
.pattern-tag.negative {
  background: rgba(239, 68, 68, 0.1);
  color: var(--color-danger, #ef4444);
}

.btn-edit-profile {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.35rem;
  padding: 0.4rem;
  font-size: 0.75rem;
  color: var(--color-primary-600);
  background: var(--color-primary-50);
  border-radius: var(--radius-md);
  transition: all var(--transition-fast);
}
.btn-edit-profile:hover {
  background: var(--color-primary-100);
}

.slide-enter-active, .slide-leave-active {
  transition: all 0.2s ease;
  overflow: hidden;
}
.slide-enter-from, .slide-leave-to {
  max-height: 0;
  opacity: 0;
  padding-top: 0;
  padding-bottom: 0;
}
.slide-enter-to, .slide-leave-from {
  max-height: 400px;
  opacity: 1;
}
</style>
