<script setup>
/**
 * AlertBell — 主动提醒铃铛
 *
 * P0-B：顶栏铃铛 + 未读数 badge + 下拉提醒面板。
 * 数据源：portfolio_alerts 表（由 alert_scanner.py 定时写入）。
 * 每 60 秒轮询未读数，点击展开最近 10 条。
 */
import { ref, onMounted, onUnmounted, computed } from 'vue'
import Icon from './ui/Icon.vue'
import { listAlerts, getUnreadAlertCount, markAlertRead } from '../api'

const emit = defineEmits(['navigate'])

const unreadCount = ref(0)
const alerts = ref([])
const open = ref(false)
const loading = ref(false)
let pollTimer = null

async function refreshUnread() {
  try {
    const { data } = await getUnreadAlertCount()
    unreadCount.value = data?.count || 0
  } catch { /* 静默失败 */ }
}

async function loadAlerts() {
  loading.value = true
  try {
    const { data } = await listAlerts(false, 10)
    alerts.value = data?.alerts || []
  } catch { /* 静默失败 */ }
  loading.value = false
}

async function togglePanel() {
  open.value = !open.value
  if (open.value && alerts.value.length === 0) {
    await loadAlerts()
  }
}

async function handleClickAlert(a) {
  // 标记已读
  if (!a.is_read) {
    try {
      await markAlertRead(a.id)
      a.is_read = 1
      unreadCount.value = Math.max(0, unreadCount.value - 1)
    } catch { /* 静默失败 */ }
  }
  // 跳转：根据 alert_type 推断目标页面
  const target = _routeForAlert(a)
  if (target) {
    emit('navigate', target)
    open.value = false
  }
}

function _routeForAlert(a) {
  if (!a.alert_type) return null
  if (a.alert_type.startsWith('valuation_')) return 'valuation'
  if (a.alert_type === 'concentration_high' || a.alert_type === 'loss_warning') return 'portfolio'
  if (a.alert_type === 'recommendation_verified') return 'decisions'
  if (a.alert_type === 'event_radar') return 'event-radar'
  // 关注列表信号跳转到机会雷达页面
  if (a.alert_type && a.alert_type.startsWith('watchlist_')) return 'event-radar'
  return null
}

const displayCount = computed(() => unreadCount.value > 99 ? '99+' : unreadCount.value)

function severityIcon(sev) {
  if (sev === 'warning') return 'alert-triangle'
  if (sev === 'danger') return 'alert-octagon'
  return 'info'
}

function severityClass(sev) {
  return `alert-sev-${sev || 'info'}`
}

// 事件雷达：按 alert_type 区分图标
function eventTypeIcon(a) {
  if (a.alert_type === 'event_radar') return 'satellite'
  // 关注列表信号
  if (a.alert_type && a.alert_type.startsWith('watchlist_')) return 'bookmark'
  return null
}

// 统一图标选择：优先事件类型图标，否则按 severity
function alertIcon(a) {
  return eventTypeIcon(a) || severityIcon(a.severity)
}

// 事件雷达：按 title 前缀判断 4 级分级
function alertClass(a) {
  if (a.alert_type === 'event_radar') {
    if (a.title && a.title.includes('[持仓影响]')) return 'alert-event-holding'
    if (a.title && a.title.includes('[关注机会]')) return 'alert-event-watchlist'
    if (a.title && a.title.includes('[建仓机会]')) return 'alert-event-opportunity'
    return 'alert-event-watch'
  }
  // 关注列表上车信号
  if (a.alert_type && a.alert_type.startsWith('watchlist_')) {
    return 'alert-event-watchlist'
  }
  return severityClass(a.severity)
}

function formatTimeAgo(ts) {
  if (!ts) return ''
  const now = new Date()
  const t = new Date(ts.includes('T') ? ts : ts.replace(' ', 'T'))
  const diff = (now - t) / 1000
  if (diff < 60) return '刚刚'
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`
  return `${Math.floor(diff / 86400)} 天前`
}

function handleClickOutside(e) {
  const root = document.querySelector('.alert-bell-root')
  if (root && !root.contains(e.target)) {
    open.value = false
  }
}

onMounted(() => {
  refreshUnread()
  // 动态轮询：页面活跃时 20 秒，非活跃时 60 秒
  const POLL_ACTIVE = 20000
  const POLL_IDLE = 60000
  const scheduleNext = () => {
    const delay = document.hasFocus() ? POLL_ACTIVE : POLL_IDLE
    pollTimer = setTimeout(() => {
      refreshUnread()
      scheduleNext()
    }, delay)
  }
  scheduleNext()
  document.addEventListener('click', handleClickOutside)
})
onUnmounted(() => {
  if (pollTimer) clearTimeout(pollTimer)
  document.removeEventListener('click', handleClickOutside)
})
</script>

<template>
  <div class="alert-bell-root">
    <button class="bell-btn" @click="togglePanel" :title="`主动提醒（${unreadCount} 条未读）`">
      <Icon name="bell" size="16" />
      <span v-if="unreadCount > 0" class="bell-badge">{{ displayCount }}</span>
    </button>

    <Teleport to="body">
      <Transition name="fade">
        <div v-if="open" class="alert-panel" @click.stop>
          <div class="alert-panel-header">
            <span class="alert-panel-title">主动提醒</span>
            <span v-if="unreadCount > 0" class="alert-panel-count">{{ unreadCount }} 条未读</span>
          </div>
          <div class="alert-panel-body">
            <div v-if="loading" class="alert-empty">
              <Icon name="spinner" size="14" class="spinning" /> 加载中...
            </div>
            <div v-else-if="alerts.length === 0" class="alert-empty">
              <Icon name="check-circle" size="14" /> 暂无提醒
            </div>
            <div
              v-for="a in alerts"
              :key="a.id"
              class="alert-row"
              :class="[alertClass(a), { 'alert-unread': !a.is_read }]"
              @click="handleClickAlert(a)"
            >
              <Icon :name="alertIcon(a)" size="13" class="alert-icon" />
              <div class="alert-content">
                <div class="alert-title">{{ a.title }}</div>
                <div v-if="a.content" class="alert-text">{{ a.content }}</div>
                <div class="alert-meta">
                  <span class="alert-time">{{ formatTimeAgo(a.latest_at || a.created_at) }}</span>
                  <span v-if="a.cnt && a.cnt > 1" class="alert-cnt">×{{ a.cnt }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<style scoped>
.alert-bell-root {
  position: relative;
  display: inline-flex;
}
.bell-btn {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  background: transparent;
  border: none;
  color: var(--color-text-secondary);
  cursor: pointer;
  border-radius: 6px;
  transition: all 0.15s;
}
.bell-btn:hover {
  background: var(--color-bg-secondary);
  color: var(--color-text-primary);
}
.bell-badge {
  position: absolute;
  top: -2px;
  right: -2px;
  min-width: 14px;
  height: 14px;
  padding: 0 3px;
  background: #dc2626;
  color: white;
  border-radius: 7px;
  font-size: 9px;
  font-weight: 700;
  line-height: 14px;
  text-align: center;
}

/* 提醒面板（Teleport 到 body，需用全局类） */
.alert-panel {
  position: fixed;
  top: 44px;
  right: 12px;
  width: 340px;
  max-height: 480px;
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);
  z-index: 1000;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
.alert-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  border-bottom: 1px solid var(--color-border-light);
  background: var(--color-bg-secondary);
}
.alert-panel-title {
  font-weight: 600;
  font-size: 13px;
  color: var(--color-text-primary);
}
.alert-panel-count {
  font-size: 11px;
  color: #dc2626;
}
.alert-panel-body {
  flex: 1;
  overflow-y: auto;
  padding: 4px 0;
}
.alert-empty {
  padding: 20px;
  text-align: center;
  font-size: 12px;
  color: var(--color-text-tertiary);
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
}
.alert-row {
  display: flex;
  gap: 8px;
  padding: 8px 14px;
  cursor: pointer;
  border-bottom: 1px solid var(--color-border-lighter);
  transition: background 0.1s;
}
.alert-row:hover {
  background: var(--color-bg-secondary);
}
.alert-row:last-child {
  border-bottom: none;
}
.alert-unread {
  background: rgba(59, 130, 246, 0.04);
}
.alert-icon {
  flex-shrink: 0;
  margin-top: 1px;
}
.alert-content {
  flex: 1;
  min-width: 0;
}
.alert-title {
  font-size: 12px;
  font-weight: 500;
  color: var(--color-text-primary);
  line-height: 1.4;
}
.alert-text {
  font-size: 11px;
  color: var(--color-text-secondary);
  line-height: 1.4;
  margin-top: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.alert-meta {
  display: flex;
  gap: 8px;
  margin-top: 4px;
  font-size: 10px;
  color: var(--color-text-tertiary);
}
.alert-cnt {
  background: var(--color-bg-tertiary);
  padding: 0 4px;
  border-radius: 3px;
}

/* 严重度图标颜色 */
.alert-sev-info .alert-icon { color: #3b82f6; }
.alert-sev-warning .alert-icon { color: #f59e0b; }
.alert-sev-danger .alert-icon { color: #dc2626; }

/* 事件雷达 3 级分级样式 */
.alert-event-holding .alert-icon { color: #dc2626; }
.alert-event-watchlist .alert-icon { color: #ea580c; }
.alert-event-opportunity .alert-icon { color: #d97706; }
.alert-event-watch .alert-icon { color: #2563eb; }
.alert-event-holding { border-left: 2px solid #dc2626; }
.alert-event-watchlist { border-left: 2px solid #ea580c; }
.alert-event-opportunity { border-left: 2px solid #d97706; }
.alert-event-watch { border-left: 2px solid #2563eb; }

.fade-enter-active, .fade-leave-active {
  transition: opacity 0.15s, transform 0.15s;
}
.fade-enter-from, .fade-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
</style>
