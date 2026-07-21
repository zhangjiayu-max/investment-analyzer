<script setup>
import { ref, onMounted, onUnmounted, computed } from 'vue'

const props = defineProps({
  // 是否默认展开
  defaultOpen: { type: Boolean, default: false },
  // 最大保留条数
  maxItems: { type: Number, default: 100 },
})

const emit = defineEmits(['navigate'])

const notifications = ref([])
const unreadCount = ref(0)
const isOpen = ref(props.defaultOpen)
const filterCategory = ref('') // '' 表示全部
let eventSource = null
let reconnectTimer = null

// 分类标签映射
const categoryLabels = {
  watchlist_signal_change: '信号变更',
  valuation: '估值预警',
  strategy: '策略触发',
  info: '系统通知',
  warning: '风险提示',
  error: '错误',
  success: '成功',
}

const categoryList = computed(() => {
  const cats = [...new Set(notifications.value.map(n => n.category || n.type).filter(Boolean))]
  return cats
})

const filteredNotifications = computed(() => {
  const sorted = [...notifications.value].sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0))
  if (!filterCategory.value) return sorted
  return sorted.filter(n => (n.category || n.type) === filterCategory.value)
})

function connectStream() {
  if (eventSource) eventSource.close()
  const protocol = window.location.protocol === 'https:' ? 'https:' : 'http:'
  const host = window.location.host
  eventSource = new EventSource(`${protocol}//${host}/api/notifications/stream`)

  eventSource.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data)
      data.read = false
      data.receivedAt = Date.now()
      notifications.value.unshift(data)
      unreadCount.value++
      if (notifications.value.length > props.maxItems) {
        notifications.value = notifications.value.slice(0, props.maxItems)
      }
      // 浏览器原生通知（可选，需要权限）
      if (typeof Notification !== 'undefined' && Notification.permission === 'granted') {
        try {
          new Notification(data.title || '投资分析助手', {
            body: data.message || '',
            tag: data.category || 'ia',
          })
        } catch { /* silent */ }
      }
    } catch (e) {
      console.error('[NotificationCenter] parse error:', e)
    }
  }

  eventSource.onerror = () => {
    if (eventSource) eventSource.close()
    if (reconnectTimer) clearTimeout(reconnectTimer)
    reconnectTimer = setTimeout(connectStream, 5000)
  }
}

function togglePanel() {
  isOpen.value = !isOpen.value
  if (isOpen.value && unreadCount.value > 0) {
    // 展开时不自动清零，等用户点"全部已读"
  }
}

function markAsRead(n) {
  if (!n.read) {
    n.read = true
    if (unreadCount.value > 0) unreadCount.value--
  }
}

function markAllAsRead() {
  notifications.value.forEach(n => { n.read = true })
  unreadCount.value = 0
}

function clearAll() {
  notifications.value = []
  unreadCount.value = 0
}

function getIcon(n) {
  const cat = n.category || n.type
  const icons = {
    watchlist_signal_change: '🔔',
    valuation: '📊',
    strategy: '🎯',
    info: 'ℹ️',
    warning: '⚠️',
    error: '❌',
    success: '✅',
  }
  return icons[cat] || '📌'
}

function formatTime(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  const now = new Date()
  const diff = (now - d) / 1000
  if (diff < 60) return '刚刚'
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`
  return d.toLocaleString()
}

function onNotificationClick(n) {
  markAsRead(n)
  // 信号变更通知点击后跳转到关注列表
  if (n.category === 'watchlist_signal_change' && n.data?.fund_code) {
    emit('navigate', 'watchlist')
    isOpen.value = false
  }
}

onMounted(() => {
  connectStream()
  // 请求浏览器通知权限（可选）
  if (typeof Notification !== 'undefined' && Notification.permission === 'default') {
    try { Notification.requestPermission() } catch { /* silent */ }
  }
})

onUnmounted(() => {
  if (eventSource) eventSource.close()
  if (reconnectTimer) clearTimeout(reconnectTimer)
})
</script>

<template>
  <div class="notification-center">
    <!-- 触发按钮 -->
    <button
      class="notif-trigger nav-item"
      :class="{ active: isOpen }"
      title="通知中心"
      @click="togglePanel"
    >
      <svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
              d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9"/>
      </svg>
      <span class="nav-label">通知</span>
      <span v-if="unreadCount > 0" class="notif-badge">{{ unreadCount > 99 ? '99+' : unreadCount }}</span>
    </button>

    <!-- 下拉面板 -->
    <Teleport to="body">
      <Transition name="notif-fade">
        <div v-if="isOpen" class="notif-overlay" @click.self="isOpen = false">
          <div class="notif-panel">
            <!-- 头部 -->
            <div class="notif-header">
              <div class="notif-title-group">
                <span class="editorial-title">通知中心</span>
                <span v-if="unreadCount > 0" class="unread-pill">{{ unreadCount }} 未读</span>
              </div>
              <div class="notif-actions">
                <button v-if="unreadCount > 0" class="btn-ghost btn-sm" @click="markAllAsRead">全部已读</button>
                <button v-if="notifications.length > 0" class="btn-ghost btn-sm btn-danger-text" @click="clearAll">清空</button>
                <button class="btn-ghost btn-sm" @click="isOpen = false">关闭</button>
              </div>
            </div>

            <!-- 分类筛选 -->
            <div v-if="categoryList.length > 0" class="notif-filter-bar">
              <button
                :class="['filter-chip', { active: !filterCategory }]"
                @click="filterCategory = ''"
              >全部 ({{ notifications.length }})</button>
              <button
                v-for="cat in categoryList"
                :key="cat"
                :class="['filter-chip', { active: filterCategory === cat }]"
                @click="filterCategory = cat"
              >{{ categoryLabels[cat] || cat }}</button>
            </div>

            <!-- 通知列表 -->
            <div class="notif-list">
              <div v-if="filteredNotifications.length === 0" class="notif-empty">
                <svg width="42" height="42" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5"
                        d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9"/>
                </svg>
                <p class="empty-title">暂无通知</p>
                <p class="empty-hint">关注列表信号变更、估值预警等会在此实时显示</p>
              </div>

              <div
                v-for="(n, idx) in filteredNotifications"
                :key="(n.timestamp || 0) + '-' + idx"
                :class="['notif-item', { unread: !n.read, [`cat-${n.category || n.type}`]: true }]"
                @click="onNotificationClick(n)"
              >
                <span class="notif-icon">{{ getIcon(n) }}</span>
                <div class="notif-content">
                  <div class="notif-title-text">{{ n.title }}</div>
                  <div class="notif-message">{{ n.message }}</div>
                  <div class="notif-meta">
                    <span class="notif-time">{{ formatTime(n.timestamp) }}</span>
                    <span v-if="n.category || n.type" class="notif-cat-tag">{{ categoryLabels[n.category || n.type] || n.category || n.type }}</span>
                    <span v-if="n.data?.fund_name" class="notif-fund">· {{ n.data.fund_name }}</span>
                  </div>
                </div>
                <span v-if="!n.read" class="unread-dot"></span>
              </div>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<style scoped>
.notif-center {
  position: relative;
}

.notif-trigger {
  position: relative;
}

.notif-badge {
  position: absolute;
  top: 4px;
  right: 4px;
  min-width: 18px;
  height: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 9px;
  background: var(--color-loss, #ef4444);
  color: white;
  font-size: 0.65rem;
  font-weight: 700;
  padding: 0 5px;
  border: 2px solid var(--color-bg-card, #1f2937);
}

.notif-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.3);
  z-index: 200;
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
}

.notif-panel {
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  width: 420px;
  max-width: 90vw;
  background: var(--color-bg-card, #1f2937);
  box-shadow: var(--shadow-lg, 0 10px 25px rgba(0,0,0,0.2));
  display: flex;
  flex-direction: column;
  border-left: 1px solid var(--color-border, #374151);
}

.notif-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1rem 1.25rem;
  border-bottom: 1px solid var(--color-border, #374151);
  flex-shrink: 0;
}

.notif-title-group {
  display: flex;
  align-items: center;
  gap: 0.6rem;
}

.unread-pill {
  padding: 0.15rem 0.5rem;
  border-radius: 10px;
  background: var(--color-loss-bg, rgba(239,68,68,0.15));
  color: var(--color-loss, #ef4444);
  font-size: 0.7rem;
  font-weight: 600;
}

.notif-actions {
  display: flex;
  gap: 0.35rem;
}

.notif-filter-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
  padding: 0.6rem 1rem;
  border-bottom: 1px solid var(--color-border, #374151);
  flex-shrink: 0;
}

.filter-chip {
  padding: 0.25rem 0.65rem;
  border-radius: 12px;
  border: 1px solid var(--color-border, #374151);
  background: transparent;
  color: var(--color-text-muted, #9ca3af);
  font-size: 0.75rem;
  cursor: pointer;
  transition: all 0.15s;
}

.filter-chip:hover {
  background: var(--color-bg-hover, rgba(255,255,255,0.05));
}

.filter-chip.active {
  background: var(--color-primary-500, #3b82f6);
  color: white;
  border-color: var(--color-primary-500, #3b82f6);
}

.notif-list {
  flex: 1;
  overflow-y: auto;
  padding: 0.5rem;
}

.notif-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 3rem 1.5rem;
  color: var(--color-text-muted, #9ca3af);
  text-align: center;
}

.notif-empty svg {
  color: var(--color-text-tertiary, #4b5563);
  margin-bottom: 1rem;
}

.empty-title {
  font-size: 0.95rem;
  font-weight: 600;
  margin: 0.25rem 0;
}

.empty-hint {
  font-size: 0.8rem;
  color: var(--color-text-tertiary, #4b5563);
  margin: 0;
  line-height: 1.5;
}

.notif-item {
  display: flex;
  gap: 0.75rem;
  padding: 0.75rem 0.9rem;
  border-radius: var(--radius-md, 8px);
  margin-bottom: 0.35rem;
  background: var(--color-bg-input, rgba(255,255,255,0.03));
  border: 1px solid transparent;
  cursor: pointer;
  transition: all 0.15s;
  position: relative;
}

.notif-item:hover {
  background: var(--color-bg-hover, rgba(255,255,255,0.06));
  border-color: var(--color-border, #374151);
}

.notif-item.unread {
  background: var(--color-primary-bg, rgba(59,130,246,0.08));
  border-color: var(--color-primary-500, rgba(59,130,246,0.3));
}

.notif-item.cat-watchlist_signal_change.unread {
  background: var(--color-gold-bg, rgba(245,158,11,0.1));
  border-color: var(--color-gold, rgba(245,158,11,0.4));
}

.notif-item.cat-warning.unread,
.notif-item.cat-error.unread {
  background: var(--color-loss-bg, rgba(239,68,68,0.08));
  border-color: var(--color-loss, rgba(239,68,68,0.3));
}

.notif-icon {
  font-size: 1.15rem;
  flex-shrink: 0;
  line-height: 1.3;
}

.notif-content {
  flex: 1;
  min-width: 0;
}

.notif-title-text {
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--color-text-primary, #f3f4f6);
  margin-bottom: 0.2rem;
  line-height: 1.4;
}

.notif-message {
  font-size: 0.78rem;
  color: var(--color-text-secondary, #d1d5db);
  margin-bottom: 0.35rem;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.notif-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.35rem;
  font-size: 0.7rem;
  color: var(--color-text-tertiary, #6b7280);
}

.notif-cat-tag {
  padding: 0.1rem 0.4rem;
  border-radius: 4px;
  background: var(--color-bg-hover, rgba(255,255,255,0.08));
  font-weight: 500;
}

.notif-fund {
  font-style: italic;
}

.unread-dot {
  position: absolute;
  top: 50%;
  right: 0.6rem;
  transform: translateY(-50%);
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--color-primary-500, #3b82f6);
  box-shadow: 0 0 0 3px var(--color-bg-card, #1f2937);
}

.notif-item.cat-watchlist_signal_change .unread-dot {
  background: var(--color-gold, #f59e0b);
}

.notif-item.cat-warning .unread-dot,
.notif-item.cat-error .unread-dot {
  background: var(--color-loss, #ef4444);
}

/* 通用按钮样式（与 Sidebar 一致） */
.btn-ghost {
  background: none;
  border: none;
  color: var(--color-text-muted, #9ca3af);
  cursor: pointer;
  padding: 0.35rem 0.5rem;
  font-size: 0.78rem;
  border-radius: var(--radius-sm, 4px);
  transition: all 0.15s;
}

.btn-ghost:hover {
  background: var(--color-bg-hover, rgba(255,255,255,0.06));
  color: var(--color-text-primary, #f3f4f6);
}

.btn-sm {
  padding: 0.35rem 0.55rem;
  font-size: 0.75rem;
}

.btn-danger-text {
  color: var(--color-loss, #ef4444);
}

.btn-danger-text:hover {
  background: var(--color-loss-bg, rgba(239,68,68,0.1));
  color: var(--color-loss, #ef4444);
}

/* 过渡动画 */
.notif-fade-enter-active,
.notif-fade-leave-active {
  transition: opacity 0.2s;
}

.notif-fade-enter-active .notif-panel,
.notif-fade-leave-active .notif-panel {
  transition: transform 0.25s cubic-bezier(0.34, 1.1, 0.64, 1);
}

.notif-fade-enter-from,
.notif-fade-leave-to {
  opacity: 0;
}

.notif-fade-enter-from .notif-panel,
.notif-fade-leave-to .notif-panel {
  transform: translateX(100%);
}

/* 移动端适配 */
@media (max-width: 768px) {
  .notif-panel {
    width: 100vw;
  }
}
</style>
