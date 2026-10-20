<script setup>
/**
 * AlertBell — 主动提醒铃铛
 *
 * P0-B：顶栏铃铛 + 未读数 badge + 下拉提醒面板。
 * 数据源：portfolio_alerts 表（由 alert_scanner.py 定时写入）。
 * 定期同步未读数，展开时同步合并提醒；支持未读与最近记录。
 */
import { ref, onMounted, onUnmounted, computed, useId } from 'vue'
import Icon from '../ui/Icon.vue'
import { listAlerts, getUnreadAlertCount, markAlertRead, acknowledgeAlert } from '../../api'

const emit = defineEmits(['navigate'])

const unreadCount = ref(0)
const alerts = ref([])
const open = ref(false)
const loading = ref(false)
const ackLoading = ref({})  // alertId -> loading
const readLoading = ref({})
const unreadOnly = ref(true)
const loadError = ref('')
const actionError = ref('')
const countError = ref(false)
const root = ref(null)
const panel = ref(null)
const trigger = ref(null)
const panelId = `alert-panel-${useId()}`
const panelStyle = ref({})
let pollTimer = null
let disposed = false
let countVersion = 0
let listVersion = 0
let listTask = null
let loadedFilter = null
const alertId = a => a.latest_id || a.id
const mutating = computed(() => Object.values(ackLoading.value).some(Boolean) || Object.values(readLoading.value).some(Boolean))

async function refreshUnread() {
  const version = ++countVersion
  try {
    const { data } = await getUnreadAlertCount()
    if (disposed || version !== countVersion) return
    unreadCount.value = Math.max(0, Number(data?.count) || 0)
    countError.value = false
  } catch {
    if (!disposed && version === countVersion) countError.value = true
  }
}

async function loadAlerts() {
  if (listTask) {
    await listTask
    if (disposed || !open.value || loadedFilter === unreadOnly.value) return
    return loadAlerts()
  }
  if (disposed || mutating.value) return
  const filter = unreadOnly.value
  const version = ++listVersion
  loading.value = true; loadError.value = ''
  listTask = (async () => {
    try {
      const { data } = await listAlerts(filter, 10)
      if (disposed || version !== listVersion || filter !== unreadOnly.value) return
      alerts.value = data?.alerts || []
      loadedFilter = filter
    } catch {
      if (!disposed && version === listVersion && filter === unreadOnly.value) {
        loadError.value = '提醒加载失败，请重试'
      }
    } finally {
      if (!disposed) loading.value = false
      listTask = null
    }
  })()
  await listTask
}

async function refreshPanel() {
  await Promise.all([refreshUnread(), open.value ? loadAlerts() : Promise.resolve()])
}

function positionPanel() {
  if (!open.value || !trigger.value) return
  const rect = trigger.value.getBoundingClientRect()
  const width = Math.min(380, window.innerWidth - 24)
  const top = Math.min(Math.max(12, rect.bottom + 8), window.innerHeight - 12)
  panelStyle.value = {
    left: `${Math.max(12, Math.min(rect.right - width, window.innerWidth - width - 12))}px`,
    top: `${top}px`, width: `${width}px`,
    maxHeight: `${Math.max(0, Math.min(520, window.innerHeight - top - 12))}px`,
  }
}

async function togglePanel() {
  open.value = !open.value
  if (open.value) {
    actionError.value = ''
    positionPanel()
    await refreshPanel()
  }
}

async function setFilter(value) {
  if (unreadOnly.value === value) return
  unreadOnly.value = value
  loadedFilter = null
  alerts.value = []
  await loadAlerts()
}

function navigate(target) { open.value = false; emit('navigate', target) }

async function handleClickAlert(a) {
  const id = alertId(a)
  if (!id || mutating.value) return
  actionError.value = ''
  if (!a.is_read) {
    readLoading.value[id] = true
    ++listVersion; ++countVersion
    try {
      await markAlertRead(id)
      if (disposed) return
      a.is_read = 1
      a.unread_count = 0
      await refreshUnread()
    } catch {
      if (!disposed) actionError.value = '标记已读失败，请重试'
      return
    } finally {
      readLoading.value[id] = false
    }
  }
  if (!disposed) navigate(_routeForAlert(a) || 'alert-center')
}

async function handleAcknowledgeAlert(a, status) {
  const id = alertId(a)
  if (!id || mutating.value) return
  actionError.value = ''
  ackLoading.value[id] = true
  ++listVersion
  try {
    await acknowledgeAlert(id, status)
    if (!disposed) a.acknowledged_status = status
  } catch {
    if (!disposed) actionError.value = '保存处理状态失败，请重试'
  } finally { ackLoading.value[id] = false }
}

function _routeForAlert(a) {
  if (!a.alert_type) return null
  if (a.alert_type.startsWith('valuation_')) return 'valuation'
  if (['concentration_high', 'loss_warning', 'loss_plus_undervalued'].includes(a.alert_type)) return 'portfolio'
  if (a.alert_type === 'recommendation_verified') return 'decisions'
  if (a.alert_type === 'event_radar') return 'event-radar'
  // 关注列表信号跳转到机会雷达页面
  if (a.alert_type && a.alert_type.startsWith('watchlist_')) return 'event-radar'
  // 健康分预警跳转到健康分页面
  if (a.alert_type === 'health_score_low') return 'health'
  if (['capital_outflow', 'market_index_drop'].includes(a.alert_type)) return 'market-intelligence'
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
  // 健康分预警
  if (a.alert_type === 'health_score_low') return 'health'
  // 估值查询失败预警
  if (a.alert_type === 'valuation_query_failed') return 'valuation'
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
  // 健康分预警
  if (a.alert_type === 'health_score_low') {
    return 'alert-health-warning'
  }
  return severityClass(a.severity)
}

function formatTimeAgo(ts) {
  if (!ts) return ''
  const now = new Date()
  const t = new Date(ts.includes('T') ? ts : ts.replace(' ', 'T'))
  if (!Number.isFinite(t.getTime())) return ''
  const diff = (now - t) / 1000
  if (diff < 60) return '刚刚'
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`
  return `${Math.floor(diff / 86400)} 天前`
}

function handleClickOutside(e) {
  if (!root.value?.contains(e.target) && !panel.value?.contains(e.target)) {
    open.value = false
  }
}

function onKeydown(e) {
  if (e.key === 'Escape' && open.value) { open.value = false; trigger.value?.focus() }
}
function scheduleNext() {
  if (disposed) return
  pollTimer = setTimeout(async () => {
    await refreshPanel()
    scheduleNext()
  }, document.hasFocus() ? 20000 : 60000)
}
onMounted(async () => {
  document.addEventListener('pointerdown', handleClickOutside)
  document.addEventListener('keydown', onKeydown)
  window.addEventListener('resize', positionPanel)
  await refreshUnread()
  scheduleNext()
})
onUnmounted(() => {
  disposed = true
  if (pollTimer) clearTimeout(pollTimer)
  document.removeEventListener('pointerdown', handleClickOutside)
  document.removeEventListener('keydown', onKeydown)
  window.removeEventListener('resize', positionPanel)
})
</script>

<template>
  <div ref="root" class="alert-bell-root">
    <button ref="trigger" type="button" class="bell-btn" @click="togglePanel" :title="countError ? '主动提醒：未读数更新失败，点击重试' : `主动提醒（${unreadCount} 条未读记录）`" :aria-label="countError ? '主动提醒，未读数更新失败' : `主动提醒，${unreadCount} 条未读记录`" :aria-expanded="open" :aria-controls="open ? panelId : undefined">
      <Icon name="bell" size="16" />
      <span v-if="unreadCount > 0" class="bell-badge">{{ displayCount }}</span>
    </button>

    <Teleport to="body">
      <Transition name="fade">
        <section v-if="open" :id="panelId" ref="panel" class="alert-panel" :style="panelStyle" role="region" aria-label="主动提醒" @click.stop>
          <div class="alert-panel-header">
            <span class="alert-panel-title">主动提醒</span>
            <span class="alert-panel-count">{{ countError ? '未读数更新失败' : `${unreadCount} 条未读记录` }}</span>
            <button type="button" class="alert-refresh" :disabled="loading || mutating" @click="refreshPanel">刷新</button>
          </div>
          <div class="alert-filters">
            <button type="button" :aria-pressed="unreadOnly" :disabled="mutating" @click="setFilter(true)">未读</button>
            <button type="button" class="alert-filter-recent" :aria-pressed="!unreadOnly" :disabled="mutating" @click="setFilter(false)">最近提醒</button>
            <span>相同类型与标的合并</span>
          </div>
          <div v-if="actionError" role="alert" class="alert-error">{{ actionError }}</div>
          <div v-if="loadError" role="alert" class="alert-error">{{ loadError }} <button type="button" class="alert-retry" @click="refreshPanel">重试</button></div>
          <div class="alert-panel-body">
            <div v-if="loading && !alerts.length" class="alert-empty">
              <Icon name="spinner" size="14" class="spinning" /> 加载中...
            </div>
            <div v-else-if="!loadError && alerts.length === 0" class="alert-empty">
              <Icon name="check-circle" size="14" /> {{ unreadOnly ? '暂无未读提醒' : '暂无提醒' }}
            </div>
            <div
              v-for="a in alerts"
              :key="alertId(a)"
              class="alert-row"
              :class="[alertClass(a), { 'alert-unread': !a.is_read }]"
            >
              <Icon :name="alertIcon(a)" size="13" class="alert-icon" />
              <div class="alert-content">
                <button type="button" class="alert-open" :disabled="mutating" @click="handleClickAlert(a)">
                <div class="alert-title">{{ a.title }}</div>
                <div v-if="a.content" class="alert-text">{{ a.content }}</div>
                <div class="alert-meta">
                  <span class="alert-time">{{ formatTimeAgo(a.latest_at || a.created_at) }}</span>
                  <span v-if="a.cnt && a.cnt > 1" class="alert-cnt">累计 {{ a.cnt }} 次</span>
                  <span v-if="a.reliability" :title="a.reliability.reason">{{ a.reliability.level === 'unknown' ? '未回测' : `可靠性：${a.reliability.label}` }}</span>
                  <span v-if="a.acknowledged_status === 'acknowledged'" class="alert-ack-tag ack-acknowledged">已采纳</span>
                  <span v-else-if="a.acknowledged_status === 'ignored'" class="alert-ack-tag ack-ignored">已忽略</span>
                </div>
                </button>
                <div v-if="!a.acknowledged_status" class="alert-ack-actions" @click.stop>
                  <button
                    class="alert-ack-btn ack-adopt"
                    :disabled="mutating"
                    @click="handleAcknowledgeAlert(a, 'acknowledged')"
                  >采纳</button>
                  <button
                    class="alert-ack-btn ack-skip"
                    :disabled="mutating"
                    @click="handleAcknowledgeAlert(a, 'ignored')"
                  >忽略</button>
                </div>
              </div>
            </div>
          </div>
          <footer class="alert-panel-footer">
            <span>已读仅表示查看，采纳不执行交易。</span>
            <button type="button" class="alert-view-all" @click="navigate('alert-center')">查看全部 →</button>
          </footer>
        </section>
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
  box-sizing: border-box;
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
  flex-shrink: 0;
  gap: 10px;
}
.alert-panel-header .alert-panel-count { margin-left: auto; }
.alert-filters { display: flex; align-items: center; gap: 8px; padding: 8px 14px; flex-shrink: 0; border-bottom: 1px solid var(--color-border-light); }
.alert-filters span { margin-left: auto; color: var(--color-text-tertiary); font-size: 10px; }
.alert-filters button, .alert-refresh, .alert-retry, .alert-view-all { border: 0; border-radius: 4px; padding: 3px 6px; background: transparent; color: var(--color-text-secondary); font: inherit; font-size: 11px; cursor: pointer; }
.alert-filters button[aria-pressed="true"] { color: var(--color-primary); background: var(--color-bg-secondary); font-weight: 600; }
.alert-refresh:disabled, .alert-filters button:disabled { opacity: .5; cursor: wait; }
.alert-error { padding: 8px 14px; color: #dc2626; font-size: 12px; }
.alert-panel-footer { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 8px 12px; border-top: 1px solid var(--color-border-light); flex-shrink: 0; }
.alert-panel-footer span { color: var(--color-text-tertiary); font-size: 10px; }
.alert-view-all { color: var(--color-primary); white-space: nowrap; }
.alert-open { display: block; width: 100%; padding: 0; border: 0; background: transparent; text-align: left; color: inherit; font: inherit; cursor: pointer; }
.alert-open:disabled { cursor: wait; }
.alert-open:focus-visible, .bell-btn:focus-visible { outline: 2px solid var(--color-primary); outline-offset: 2px; }
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
  min-height: 0;
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
  flex-wrap: wrap;
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

/* 业务确认状态标签 + 采纳/忽略按钮 */
.alert-ack-tag {
  padding: 0 5px;
  border-radius: 3px;
  font-size: 9px;
  font-weight: 600;
  line-height: 14px;
}
.alert-ack-tag.ack-acknowledged {
  background: rgba(22, 163, 74, 0.12);
  color: #16a34a;
}
.alert-ack-tag.ack-ignored {
  background: var(--color-bg-tertiary);
  color: var(--color-text-tertiary);
}
.alert-ack-actions {
  display: flex;
  gap: 4px;
  margin-top: 4px;
}
.alert-ack-btn {
  padding: 2px 8px;
  font-size: 10px;
  font-weight: 500;
  border-radius: 4px;
  border: 1px solid transparent;
  cursor: pointer;
  transition: all 0.15s;
  line-height: 1.4;
}
.alert-ack-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.alert-ack-btn.ack-adopt {
  background: rgba(22, 163, 74, 0.1);
  color: #16a34a;
  border-color: rgba(22, 163, 74, 0.3);
}
.alert-ack-btn.ack-adopt:not(:disabled):hover {
  background: rgba(22, 163, 74, 0.18);
}
.alert-ack-btn.ack-skip {
  background: var(--color-bg-secondary);
  color: var(--color-text-tertiary);
  border-color: var(--color-border-light);
}
.alert-ack-btn.ack-skip:not(:disabled):hover {
  background: var(--color-bg-tertiary);
  color: var(--color-text-secondary);
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

/* 健康分预警 */
.alert-health-warning .alert-icon { color: #dc2626; }
.alert-health-warning { border-left: 2px solid #dc2626; }

.fade-enter-active, .fade-leave-active {
  transition: opacity 0.15s, transform 0.15s;
}
.fade-enter-from, .fade-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
</style>
