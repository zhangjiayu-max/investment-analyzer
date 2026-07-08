<script setup>
import { ref, onMounted, onUnmounted, computed } from 'vue'
import {
  syncArticles, fetchArticles, fetchArticle, addArticle,
  downloadArticleImages, analyzeArticleImages, getAnalyzeStatus, cancelAnalyze, reanalyzeImage, getReanalyzeStatus,
} from '../api'
import ConfirmDialog from './ConfirmDialog.vue'
import AppToast from './AppToast.vue'
import { useToast } from '../composables/useToast'

const { showToast } = useToast()

const articles = ref([])
const selectedArticle = ref(null)
const records = ref([])
const loading = ref(false)
const syncing = ref(false)
const statusFilter = ref('')
const searchQuery = ref('')
const previewImage = ref(null)
const recordStatusFilter = ref('all')
const recordMetricFilter = ref('all')
const analyzingRecordId = ref(null)
const cancelling = ref(false)
const reanalyzePoller = ref(null)

// 添加文章
const addUrl = ref('')
const adding = ref(false)
async function handleAddArticle() {
  const url = addUrl.value.trim()
  if (!url) return
  adding.value = true
  try {
    const { data } = await addArticle(url)
    if (data.ok) {
      showToast('已提交，正在解析下载分析...', 'success')
      addUrl.value = ''
      await loadArticles()
      // 自动打开新文章详情
      const { data: detail } = await fetchArticle(data.article_id)
      if (detail) openArticle(detail)
      // 启动轮询等待分析完成
      startAddPolling(data.article_id)
    } else {
      showToast(data.message || '文章已存在', 'warning')
      if (data.article_id) {
        const { data: detail } = await fetchArticle(data.article_id)
        if (detail) openArticle(detail)
      }
    }
  } catch (e) {
    showToast('添加失败: ' + e.message, 'error')
  } finally {
    adding.value = false
  }
}

// 轮询新添加文章的状态
let addPoller = null
function startAddPolling(articleId) {
  if (addPoller) clearInterval(addPoller)
  addPoller = setInterval(async () => {
    try {
      const { data } = await fetchArticle(articleId)
      if (!data) return
      // 更新列表中的状态
      syncArticleInList(data.id, { status: data.status, title: data.title, ...data })
      // 如果打开了这篇文章，刷新详情
      if (selectedArticle.value?.id === data.id) {
        selectedArticle.value = data
        if (data.analysis_records) records.value = data.analysis_records
      }
      // 分析完成或出错，停止轮询
      if (data.status === 'analyzed' || data.status === 'error') {
        clearInterval(addPoller)
        addPoller = null
        await loadArticles()
        showToast(data.status === 'analyzed' ? '分析完成' : '分析出错', data.status === 'analyzed' ? 'success' : 'error')
      }
    } catch { /* ignore */ }
  }, 3000)
}

const filteredRecords = computed(() => {
  let list = records.value
  if (recordStatusFilter.value !== 'all') {
    list = list.filter(r => r.status === recordStatusFilter.value)
  }
  if (recordMetricFilter.value !== 'all') {
    list = list.filter(r => (r.metric_type || '未知') === recordMetricFilter.value)
  }
  return list
})

const recordCounts = computed(() => {
  const c = { all: records.value.length, success: 0, error: 0, pending: 0, cancelled: 0, timeout: 0, analyzing: 0 }
  records.value.forEach(r => {
    if (r.status === 'success') c.success++
    else if (r.status === 'error') c.error++
    else if (r.status === 'cancelled') c.cancelled++
    else if (r.status === 'timeout') c.timeout++
    else if (r.status === 'analyzing') c.analyzing++
    else c.pending++
  })
  return c
})

const availableMetrics = computed(() => {
  const set = new Set()
  records.value.forEach(r => set.add(r.metric_type || '未知'))
  return ['all', ...Array.from(set)]
})

const metricCounts = computed(() => {
  const c = { all: records.value.length }
  records.value.forEach(r => {
    const m = r.metric_type || '未知'
    c[m] = (c[m] || 0) + 1
  })
  return c
})

function metricBadgeClass(mt) {
  if (!mt) return 'badge-neutral'
  if (mt.includes('市盈率')) return 'badge-info'
  if (mt.includes('市净率')) return 'badge-success'
  if (mt.includes('市销率')) return 'badge-warning'
  if (mt.includes('市现率')) return 'badge-purple'
  if (mt.includes('股息率')) return 'badge-orange'
  if (mt.includes('风险溢价')) return 'badge-pink'
  return 'badge-neutral'
}

const confirm = ref({ visible: false, title: '', message: '', danger: false, action: null })
function showConfirm(title, message, action, danger = false) {
  confirm.value = { visible: true, title, message, danger, action, loading: false }
}
function onConfirmCancel() {
  confirm.value.visible = false
  confirm.value.action = null
}
async function onConfirmOk() {
  if (!confirm.value.action) return
  confirm.value.loading = true
  try { await confirm.value.action() }
  finally { confirm.value.loading = false; confirm.value.visible = false; confirm.value.action = null }
}

const filteredArticles = computed(() => {
  let list = articles.value
  if (statusFilter.value) list = list.filter(a => a.status === statusFilter.value)
  if (searchQuery.value.trim()) {
    const q = searchQuery.value.trim().toLowerCase()
    list = list.filter(a => (a.title || '').toLowerCase().includes(q) || String(a.seq).includes(q))
  }
  return list
})

const poller = ref(null)
onMounted(() => loadArticles())
onUnmounted(() => {
  if (poller.value) clearInterval(poller.value)
  clearReanalyzePoller()  // 清除所有重分析轮询
  if (addPoller) { clearInterval(addPoller); addPoller = null }
})

async function loadArticles() {
  loading.value = true
  try { const { data } = await fetchArticles(); articles.value = data.articles || [] }
  catch (e) { console.error(e) }
  finally { loading.value = false }
}

/** 刷新列表中某篇文章的状态（不重载整个列表） */
function syncArticleInList(articleId, fields) {
  const idx = articles.value.findIndex(a => a.id === articleId)
  if (idx !== -1) {
    Object.assign(articles.value[idx], fields)
  }
}

async function handleSync() {
  syncing.value = true
  try { await syncArticles(); await loadArticles() }
  finally { syncing.value = false }
}

async function openArticle(article) {
  clearPoller()
  clearReanalyzePoller()
  analyzingRecordId.value = null
  selectedArticle.value = article
  records.value = []
  try {
    const { data } = await fetchArticle(article.id)
    if (data.analysis_records?.length) records.value = data.analysis_records
    selectedArticle.value = data
    // 同步列表中的状态
    syncArticleInList(data.id, {
      status: data.status,
      error_count: data.error_count,
      success_count: data.success_count,
      pending_count: data.pending_count,
      total_records: data.total_records,
    })
    // 如果文章正在分析中，自动启动轮询
    if (data.status === 'analyzing') {
      startAnalyzePolling(data.id)
    }
  } catch (e) {
    console.error('Failed to load article detail:', e)
    selectedArticle.value = article
  }
}

function startAnalyzePolling(id) {
  clearPoller()
  let prevDone = 0
  let prevRecordId = null
  poller.value = setInterval(async () => {
    try {
      const { data: st } = await getAnalyzeStatus(id)
      if (!selectedArticle.value || selectedArticle.value.id !== id) { clearPoller(); return }
      selectedArticle.value.status = st.status
      syncArticleInList(id, { status: st.status })

      const curRecordId = st.progress?.current_record_id || null
      analyzingRecordId.value = curRecordId

      if (curRecordId !== prevRecordId || st.progress?.done > prevDone) {
        prevRecordId = curRecordId
        prevDone = st.progress?.done || 0
        const { data: detail } = await fetchArticle(id)
        if (detail.analysis_records) records.value = detail.analysis_records
        syncArticleInList(id, {
          success_count: detail.success_count,
          error_count: detail.error_count,
          pending_count: detail.pending_count,
        })
      }

      if (st.status === 'analyzed' || st.status === 'error' || st.status === 'downloaded') {
        clearPoller()
        analyzingRecordId.value = null
        cancelling.value = false
        await openArticle(selectedArticle.value)
      }
    } catch { clearPoller(); analyzingRecordId.value = null }
  }, 1500)
}

function clearPoller() {
  if (poller.value) { clearInterval(poller.value); poller.value = null }
}

async function handleDownload(id) {
  try {
    const { data } = await downloadArticleImages(id)
    if (!data.ok) { showToast(data.message || '下载失败', 'error'); return }
    selectedArticle.value.status = 'downloading'
    syncArticleInList(id, { status: 'downloading' })

    clearPoller()
    poller.value = setInterval(async () => {
      try {
        const { data: art } = await fetchArticle(id)
        selectedArticle.value.status = art.status
        syncArticleInList(id, { status: art.status })
        if (art.status === 'downloaded' || art.status === 'error') {
          clearPoller()
          await openArticle(selectedArticle.value)
        }
      } catch { clearPoller() }
    }, 2000)
  } catch (e) { showToast('下载失败: ' + e.message, 'error') }
}

async function handleAnalyze(id) {
  try {
    const { data } = await analyzeArticleImages(id)
    if (!data.ok) { showToast(data.message || '分析失败', 'error'); return }
    selectedArticle.value.status = 'analyzing'
    syncArticleInList(id, { status: 'analyzing' })
    startAnalyzePolling(id)
  } catch (e) { showToast('分析失败: ' + e.message, 'error') }
}

async function handleCancelAnalyze(id) {
  if (cancelling.value) return
  cancelling.value = true
  try {
    const { data } = await cancelAnalyze(id)
    if (!data.ok) {
      showToast(data.message || '取消失败', 'error')
      cancelling.value = false
      return
    }
    // 立即更新按钮为"取消中"状态，轮询会检测到后端完成并刷新
    // 超时保护：如果 30 秒后后端还没响应，重置状态
    setTimeout(() => { cancelling.value = false }, 30000)
  } catch (e) {
    showToast('取消失败: ' + e.message, 'error')
    cancelling.value = false
  }
}

// 支持多个并发分析任务
const analyzingRecords = ref(new Set())
const reanalyzePollers = ref({})

async function handleReanalyze(recordId) {
  if (analyzingRecords.value.has(recordId)) return  // 该记录已在分析中
  try {
    const { data } = await reanalyzeImage(recordId)
    if (!data.ok) {
      await openArticle(selectedArticle.value)
      return
    }
    // 立即在 UI 上显示"分析中"状态
    analyzingRecords.value.add(recordId)
    const rec = records.value.find(r => r.id === recordId)
    if (rec) rec.status = 'analyzing'
    showToast('重新分析已开始，后台执行中...', 'success')

    // 轮询状态
    reanalyzePollers.value[recordId] = setInterval(async () => {
      try {
        const { data: st } = await getReanalyzeStatus(recordId)
        if (st.status === 'analyzing') {
          const r2 = records.value.find(r => r.id === recordId)
          if (r2) r2.status = 'analyzing'
          return
        }
        if (st.status === 'success' || st.status === 'error' || st.status === 'timeout') {
          clearReanalyzePoller(recordId)
          analyzingRecords.value.delete(recordId)
          await openArticle(selectedArticle.value)
        }
      } catch {
        clearReanalyzePoller(recordId)
        analyzingRecords.value.delete(recordId)
      }
    }, 1500)
  } catch (e) {
    showToast('重新分析失败: ' + e.message, 'error')
    clearReanalyzePoller(recordId)
    analyzingRecords.value.delete(recordId)
  }
}

function clearReanalyzePoller(recordId) {
  if (recordId) {
    if (reanalyzePollers.value[recordId]) {
      clearInterval(reanalyzePollers.value[recordId])
      delete reanalyzePollers.value[recordId]
    }
  } else {
    // 清除所有轮询
    Object.keys(reanalyzePollers.value).forEach(id => {
      clearInterval(reanalyzePollers.value[id])
    })
    reanalyzePollers.value = {}
    analyzingRecords.value.clear()
  }
}

function statusClass(s) {
  return {
    pending: 'badge-warning', downloading: 'badge-info', downloaded: 'badge-info',
    analyzing: 'badge-info', analyzed: 'badge-success', error: 'badge-danger',
  }[s] || 'badge-neutral'
}

function articleStatusClass(a) {
  if (a.status === 'analyzed' && a.error_count > 0) return 'badge-danger'
  return statusClass(a.status)
}

function statusLabel(s) {
  return { pending: '待处理', downloading: '下载中', downloaded: '已下载', analyzing: '分析中', analyzed: '已分析', error: '异常' }[s] || s
}

function articleStatusLabel(a) {
  if (a.status === 'analyzed' && a.error_count > 0) return `有失败`
  return statusLabel(a.status)
}

function recordStatusClass(s) {
  return {
    success: 'st-success', error: 'st-error', pending: 'st-pending',
    cancelled: 'st-cancelled', timeout: 'st-timeout', analyzing: 'st-analyzing',
  }[s] || 'st-pending'
}

function recordStatusLabel(s) {
  return {
    success: '成功', error: '失败', pending: '待分析',
    cancelled: '已取消', timeout: '超时', analyzing: '分析中',
  }[s] || '待分析'
}

function imageUrl(path) {
  if (!path) return ''
  // 统一从绝对/相对路径中提取 data/images/ 之后的相对部分
  const marker = 'data/images/'
  const idx = path.indexOf(marker)
  if (idx >= 0) {
    return `/static/images/${path.slice(idx + marker.length)}`
  }
  // 兜底：直接拼
  return `/static/images/${path}`
}

function openPreview(path) { previewImage.value = imageUrl(path) }
function closePreview() { previewImage.value = null }
</script>

<template>
  <div class="article-page bg-mesh">
    <!-- Toolbar -->
    <div class="toolbar">
      <form @submit.prevent="handleAddArticle" class="add-article-form">
        <input
          v-model="addUrl"
          type="url"
          placeholder="粘贴公众号链接，自动解析下载分析..."
          class="input-field add-url-input"
          :disabled="adding"
        />
        <button type="submit" :disabled="adding || !addUrl.trim()" class="btn-primary toolbar-btn">
          <svg v-if="adding" class="spinner-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v4m0 12v4m-7.07-3.93l2.83-2.83m8.48-8.48l2.83-2.83M2 12h4m12 0h4M4.93 4.93l2.83 2.83m8.48 8.48l2.83 2.83"/></svg>
          {{ adding ? '提交中...' : '添加文章' }}
        </button>
      </form>
      <div class="toolbar-divider"></div>
      <button @click="showConfirm('同步文章', '将从 articles.json 同步所有文章到数据库，确定继续吗？', handleSync)"
        :disabled="syncing || loading" class="btn-secondary toolbar-btn">
        <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
        同步
      </button>
      <button @click="loadArticles" :disabled="loading" class="btn-secondary toolbar-btn">
        <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
        刷新
      </button>
      <select v-model="statusFilter" class="input-field toolbar-select">
        <option value="">全部状态</option>
        <option value="pending">待处理</option>
        <option value="downloaded">已下载</option>
        <option value="analyzed">已分析</option>
        <option value="error">异常</option>
      </select>
      <input v-model="searchQuery" placeholder="搜索标题或序号..." class="input-field toolbar-search" />
      <span class="toolbar-count terminal-label">共 <span class="font-jet">{{ filteredArticles.length }}</span> 篇</span>
    </div>

    <div class="content-area">
      <!-- Article List -->
      <div class="article-list card editorial-card">
        <div class="list-scroll">
          <table class="list-table">
            <thead>
              <tr>
                <th class="col-seq">#</th>
                <th>标题</th>
                <th class="col-status">状态</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="a in filteredArticles" :key="a.id"
                @click="openArticle(a)"
                :class="['list-row', 'reveal-stagger', { selected: selectedArticle?.id === a.id }]"
              >
                <td class="col-seq font-jet">{{ a.seq || '-' }}</td>
                <td>
                  <div class="row-title" :title="a.title">{{ a.title || '无标题' }}</div>
                  <div v-if="a.publish_time" class="row-date font-jet">{{ a.publish_time.slice(0, 10) }}</div>
                </td>
                <td class="col-status">
                  <span :class="['badge', articleStatusClass(a)]">
                    {{ articleStatusLabel(a) }}
                    <span v-if="a.status === 'analyzed' && a.error_count > 0" class="error-count-badge">{{ a.error_count }}</span>
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
          <div v-if="!filteredArticles.length" class="list-empty">暂无文章</div>
        </div>
      </div>

      <!-- Detail Panel -->
      <div v-if="selectedArticle" class="detail-panel card editorial-card">
        <!-- Header -->
        <div class="detail-header">
          <div class="detail-info">
            <h3 class="detail-title editorial-title">{{ selectedArticle.title }}</h3>
            <div class="detail-meta">
              <span class="meta-item">
                <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>
                <span class="font-jet">{{ (selectedArticle.publish_time || '').slice(0, 10) || '未知日期' }}</span>
              </span>
              <span class="meta-item">
                <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>
                <span class="font-jet">{{ selectedArticle.image_count || 0 }}</span> <span class="terminal-label">张图片</span>
              </span>
              <a v-if="selectedArticle.url" :href="selectedArticle.url" target="_blank" rel="noopener" class="meta-item meta-link">
                <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"/></svg>
                原文
              </a>
              <span :class="['badge', articleStatusClass(selectedArticle)]">
                {{ articleStatusLabel(selectedArticle) }}
                <span v-if="selectedArticle.status === 'analyzed' && selectedArticle.error_count > 0" class="error-count-badge">{{ selectedArticle.error_count }}</span>
              </span>
              <span v-if="selectedArticle.total_records" class="meta-item">
                <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
                <span class="font-jet">{{ selectedArticle.success_count }}/{{ selectedArticle.total_records }}</span> <span class="terminal-label">成功</span>
              </span>
            </div>
          </div>
          <div class="detail-actions">
            <button @click="showConfirm('下载图片', '确定要下载该文章的所有图片吗？', () => handleDownload(selectedArticle.id))"
              v-if="selectedArticle.status === 'pending'"
              class="btn-primary">
              <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/></svg>
              下载图片
            </button>
            <button @click="showConfirm('分析图片', '将分析待处理和失败的图片（已成功的会跳过），确定继续吗？', () => handleAnalyze(selectedArticle.id))"
              v-if="(selectedArticle.status === 'downloaded' || selectedArticle.status === 'analyzed' || selectedArticle.status === 'error') && selectedArticle.status !== 'analyzing'"
              class="btn-primary btn-green">
              <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
              分析图片{{ recordCounts.pending + recordCounts.error + recordCounts.cancelled + recordCounts.timeout > 0 ? ' (' + (recordCounts.pending + recordCounts.error + recordCounts.cancelled + recordCounts.timeout) + ')' : '' }}
            </button>
            <button v-if="selectedArticle.status === 'analyzing'"
              @click="handleCancelAnalyze(selectedArticle.id)"
              :disabled="cancelling"
              class="btn-primary btn-cancel">
              <svg v-if="cancelling" class="spinner-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v4m0 12v4m-7.07-3.93l2.83-2.83m8.48-8.48l2.83-2.83M2 12h4m12 0h4M4.93 4.93l2.83 2.83m8.48 8.48l2.83 2.83"/></svg>
              <svg v-else width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
              {{ cancelling ? '取消中...' : '取消分析' }}
            </button>
          </div>
        </div>

        <!-- Record Filter Bar -->
        <div v-if="records.length" class="record-filter-bar">
          <button
            v-for="f in [
              { key: 'all', label: '全部' },
              { key: 'analyzing', label: '分析中' },
              { key: 'success', label: '成功' },
              { key: 'error', label: '失败' },
              { key: 'pending', label: '待分析' },
              { key: 'timeout', label: '超时' },
              { key: 'cancelled', label: '已取消' },
            ]"
            :key="f.key"
            @click="recordStatusFilter = f.key"
            :class="['filter-chip', { active: recordStatusFilter === f.key }]"
          >
            {{ f.label }}
            <span class="filter-count">{{ recordCounts[f.key] }}</span>
          </button>
        </div>
        <!-- Metric Type Filter -->
        <div v-if="availableMetrics.length > 2" class="record-filter-bar metric-filter-bar">
          <button
            v-for="m in availableMetrics"
            :key="m"
            @click="recordMetricFilter = m"
            :class="['filter-chip', 'metric-chip', { active: recordMetricFilter === m }]"
          >
            <span v-if="m !== 'all'" :class="['metric-dot', metricBadgeClass(m)]"></span>
            {{ m === 'all' ? '全部指标' : m }}
            <span class="filter-count">{{ metricCounts[m] || 0 }}</span>
          </button>
        </div>

        <!-- Image Grid -->
        <div v-if="records.length" class="record-grid">
          <div v-for="r in filteredRecords" :key="r.id" :class="['record-card', 'editorial-card', 'reveal-stagger', { 'record-analyzing': analyzingRecords.has(r.id) }]">
            <div class="record-thumb" @click="openPreview(r.image_path)">
              <img :src="imageUrl(r.image_path)" loading="lazy" />
              <span v-if="analyzingRecords.has(r.id)" class="record-status st-analyzing">
                <span class="analyzing-spinner"></span> 分析中
              </span>
              <span v-else :class="['record-status', recordStatusClass(r.status)]">
                {{ recordStatusLabel(r.status) }}
              </span>
            </div>
            <div class="record-info">
              <div class="record-code">
                <template v-if="r.index_code"><span class="font-jet">{{ r.index_code }}</span> {{ r.index_name || '' }}</template>
                <template v-else>未识别</template>
              </div>
              <div v-if="r.metric_type" :class="['record-metric-badge', metricBadgeClass(r.metric_type)]">{{ r.metric_type }}</div>
              <div v-if="r.status === 'error' && r.error_msg" class="record-error">{{ r.error_msg }}</div>
              <button @click="showConfirm('重新分析', '确定要重新分析这张图片吗？', () => handleReanalyze(r.id))"
                :class="['btn-ghost', 'record-retry', r.status === 'success' ? '' : 'retry-error']">
                <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
                {{ r.status === 'success' ? '重新分析' : '重试' }}
              </button>
              <div v-if="r.status === 'timeout' && r.error_msg" class="record-error">{{ r.error_msg }}</div>
            </div>
          </div>
        </div>
        <div v-if="records.length && !filteredRecords.length" class="empty-records">
          <svg width="36" height="36" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M3 4a1 1 0 011-1h16a1 1 0 011 1v2.586a1 1 0 01-.293.707l-6.414 6.414a1 1 0 00-.293.707V17l-4 4v-6.586a1 1 0 00-.293-.707L3.293 7.293A1 1 0 013 6.586V4z"/></svg>
          <p>没有{{ { error: '失败', success: '成功', pending: '待分析', timeout: '超时', cancelled: '已取消' }[recordStatusFilter] || '' }}的记录</p>
        </div>
        <div v-else-if="!records.length" class="empty-records">
          <svg width="48" height="48" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>
          <p>{{ selectedArticle.status === 'pending' ? '暂无图片，请先下载' : '暂无分析记录' }}</p>
        </div>
      </div>

      <!-- Empty State -->
      <div v-if="!selectedArticle" class="empty-panel card editorial-card">
        <svg width="64" height="64" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M19 20H5a2 2 0 01-2-2V6a2 2 0 012-2h10a2 2 0 012 2v1m2 13a2 2 0 01-2-2V7m2 13a2 2 0 002-2V9a2 2 0 00-2-2h-2m-4-3H9M7 16h6M7 8h6v4H7V8z"/></svg>
        <p class="empty-title">请从左侧选择一篇文章</p>
        <p class="empty-desc">选择后可下载图片并运行 AI 分析</p>
      </div>
    </div>

    <!-- Image Preview -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="previewImage" class="lightbox" @click.self="closePreview">
          <button @click="closePreview" class="lightbox-close">
            <svg width="24" height="24" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
          </button>
          <img :src="previewImage" />
        </div>
      </Transition>
    </Teleport>

    <ConfirmDialog
      :visible="confirm.visible" :title="confirm.title" :message="confirm.message"
      :danger="confirm.danger" :loading="confirm.loading"
      confirm-text="确定" cancel-text="取消"
      @confirm="onConfirmOk" @cancel="onConfirmCancel"
    />
    <AppToast />
  </div>
</template>

<style scoped>
.article-page {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 120px);
  height: calc(100dvh - 120px);
}

/* Toolbar */
.toolbar {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  margin-bottom: 1.25rem;
  flex-wrap: wrap;
}

.toolbar-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.55rem 0.95rem;
  font-size: 0.82rem;
}

.toolbar-select {
  width: auto;
  min-width: 100px;
  padding: 0.5rem 0.75rem;
  font-size: 0.8rem;
}

.toolbar-search {
  flex: 1;
  min-width: 160px;
  max-width: 240px;
  padding: 0.5rem 0.75rem;
  font-size: 0.8rem;
}

.toolbar-count {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  margin-left: auto;
}

.add-article-form {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex: 1;
  min-width: 280px;
}

.add-url-input {
  flex: 1;
  min-width: 200px;
  padding: 0.5rem 0.75rem;
  font-size: 0.8rem;
}

.toolbar-divider {
  width: 1px;
  height: 24px;
  background: var(--color-border);
  flex-shrink: 0;
}

/* Content Area */
.content-area {
  display: flex;
  gap: 1.25rem;
  flex: 1;
  min-height: 0;
}

/* Article List */
.article-list {
  width: 300px;
  flex-shrink: 0;
  padding: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.list-scroll {
  flex: 1;
  overflow-y: auto;
}

.list-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.8rem;
}

.list-table thead {
  position: sticky;
  top: 0;
  z-index: 1;
}

.list-table th {
  background: var(--color-bg-input);
  padding: 0.7rem 0.85rem;
  text-align: left;
  font-size: 0.72rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--color-text-muted);
}

.col-seq { width: 40px; }
.col-status { width: 70px; }

.list-row {
  cursor: pointer;
  transition: background var(--transition-fast);
  border-bottom: 1px solid var(--color-border-light);
}

.list-row:hover {
  background: var(--color-bg-hover);
}

.list-row.selected {
  background: var(--color-primary-50);
}

.dark .list-row.selected {
  background: var(--color-primary-bg);
}

.list-row td {
  padding: 0.7rem 0.85rem;
}

.list-row .col-seq {
  font-size: 0.7rem;
  color: var(--color-text-muted);
  font-family: monospace;
}

.row-title {
  font-weight: 500;
  color: var(--color-text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 160px;
}

.row-date {
  font-size: 0.72rem;
  color: var(--color-text-muted);
  margin-top: 0.2rem;
}

.list-empty {
  padding: 2rem;
  text-align: center;
  font-size: 0.8rem;
  color: var(--color-text-muted);
}

/* Detail Panel */
.detail-panel {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
  padding: 1.5rem;
}

.detail-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 1.25rem;
  gap: 1rem;
}

.detail-info {
  flex: 1;
  min-width: 0;
}

.detail-title {
  font-size: 1.1rem;
  font-weight: 700;
  color: var(--color-text-primary);
  margin: 0;
  line-height: 1.3;
}

.detail-meta {
  display: flex;
  align-items: center;
  gap: 0.85rem;
  margin-top: 0.6rem;
  flex-wrap: wrap;
}

.meta-item {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  font-size: 0.78rem;
  color: var(--color-text-muted);
}

.meta-link {
  color: var(--color-primary-500);
  text-decoration: none;
  font-weight: 500;
  transition: color 0.15s;
}

.meta-link:hover { color: var(--color-primary-700); text-decoration: underline; }

.detail-actions {
  display: flex;
  gap: 0.5rem;
  flex-shrink: 0;
}

.detail-actions .btn-primary {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.55rem 0.95rem;
  font-size: 0.82rem;
}

.btn-green {
  background: linear-gradient(135deg, var(--color-success), var(--color-success));
}

.btn-green:hover {
  background: linear-gradient(135deg, var(--color-success), var(--color-success));
  opacity: 0.9;
}

.btn-cancel {
  background: linear-gradient(135deg, var(--color-danger), var(--color-danger));
}

.btn-cancel:hover {
  background: linear-gradient(135deg, var(--color-danger), var(--color-danger));
  opacity: 0.9;
}

.btn-cancel:disabled {
  opacity: 0.7;
  cursor: not-allowed;
}

.spinner-icon {
  animation: spin 1s linear infinite;
}

/* Record Filter Bar */
.record-filter-bar {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 1rem;
  flex-wrap: wrap;
}

.filter-chip {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  padding: 0.4rem 0.8rem;
  border-radius: 999px;
  font-size: 0.75rem;
  font-weight: 500;
  color: var(--color-text-secondary);
  background: var(--color-bg-input);
  border: 1px solid transparent;
  transition: all var(--transition-fast);
}

.filter-chip:hover {
  border-color: var(--color-border);
  color: var(--color-text-primary);
}

.filter-chip.active {
  background: var(--color-primary-500);
  color: white;
  border-color: var(--color-primary-500);
}

.dark .filter-chip.active {
  background: var(--color-primary-600);
}

.filter-count {
  font-size: 0.65rem;
  font-weight: 600;
  background: rgba(0,0,0,0.1);
  padding: 0.05rem 0.35rem;
  border-radius: 999px;
  min-width: 1.2em;
  text-align: center;
}

.filter-chip.active .filter-count {
  background: rgba(255,255,255,0.25);
}

/* Record Grid */
.record-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 0.75rem;
}

.record-card {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  overflow: hidden;
  transition: all var(--transition-fast);
}

.record-card:hover {
  border-color: var(--color-primary-300);
  box-shadow: var(--shadow-md);
}

.record-thumb {
  position: relative;
  aspect-ratio: 4/3;
  background: var(--color-bg-input);
  cursor: pointer;
  overflow: hidden;
}

.record-thumb img {
  width: 100%;
  height: 100%;
  object-fit: contain;
  padding: 0.25rem;
  transition: transform 0.3s;
}

.record-card:hover .record-thumb img {
  transform: scale(1.03);
}

.record-status {
  position: absolute;
  top: 0.5rem;
  right: 0.5rem;
  padding: 0.15rem 0.4rem;
  border-radius: var(--radius-sm);
  font-size: 0.65rem;
  font-weight: 600;
  color: white;
}

.st-success { background: var(--color-success); }
.st-error { background: var(--color-danger); }
.st-pending { background: var(--color-text-muted); }
.st-cancelled { background: var(--color-text-muted); }
.st-timeout { background: var(--color-warning); }
.st-analyzing {
  background: var(--color-primary-500);
  display: flex;
  align-items: center;
  gap: 0.3rem;
}

.analyzing-spinner {
  width: 10px;
  height: 10px;
  border: 2px solid rgba(255,255,255,0.3);
  border-top-color: white;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

.record-analyzing {
  border-color: var(--color-primary-400) !important;
  box-shadow: 0 0 0 2px rgba(99, 102, 241, 0.2), var(--shadow-md) !important;
  animation: pulse-border 2s ease-in-out infinite;
}

@keyframes pulse-border {
  0%, 100% { box-shadow: 0 0 0 2px rgba(99, 102, 241, 0.2), var(--shadow-md); }
  50% { box-shadow: 0 0 0 4px rgba(99, 102, 241, 0.3), var(--shadow-md); }
}

.record-info {
  padding: 0.85rem 1rem;
}

.record-code {
  font-size: 0.82rem;
  font-weight: 600;
  color: var(--color-text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.record-metric {
  font-size: 0.72rem;
  color: var(--color-text-muted);
  margin-top: 0.2rem;
}

.record-metric-badge {
  display: inline-block;
  font-size: 0.65rem;
  font-weight: 600;
  padding: 0.1rem 0.4rem;
  border-radius: var(--radius-sm);
  margin-top: 0.2rem;
}

.metric-filter-bar {
  padding-top: 0.25rem;
}

.metric-chip {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
}

.metric-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  display: inline-block;
}

.badge-purple { background: var(--color-info-bg); color: var(--color-primary); }
.badge-orange { background: var(--color-warning-bg); color: var(--color-warning); }
.badge-pink { background: var(--color-danger-bg); color: var(--color-danger); }
.badge-purple .metric-dot { background: var(--color-primary); }
.badge-orange .metric-dot { background: var(--color-warning); }
.badge-pink .metric-dot { background: var(--color-danger); }
.badge-success .metric-dot { background: var(--color-success); }
.badge-info .metric-dot { background: var(--color-primary); }
.badge-warning .metric-dot { background: var(--color-warning); }
.badge-neutral .metric-dot { background: var(--color-text-muted); }

.record-error {
  font-size: 0.72rem;
  color: var(--color-danger);
  margin-top: 0.5rem;
  line-clamp: 2;
  -webkit-line-clamp: 2;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  overflow: hidden;
  line-height: 1.5;
}

.record-retry {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  margin-top: 0.5rem;
  padding: 0.25rem 0.5rem;
  font-size: 0.7rem;
  color: var(--color-primary-600);
  border-radius: var(--radius-sm);
}

.record-retry:hover {
  background: var(--color-primary-50);
}

.record-retry.retry-error {
  color: var(--color-danger);
}

.record-retry.retry-error:hover {
  background: var(--color-danger-bg);
}

/* Empty Records */
.empty-records {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.75rem;
  padding: 3rem;
  color: var(--color-text-muted);
}

.empty-records p {
  font-size: 0.85rem;
  margin: 0;
}

/* Empty Panel */
.empty-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: var(--color-text-muted);
  gap: 0.5rem;
  padding: 3rem;
}

.empty-title {
  font-size: 0.9rem;
  font-weight: 600;
  margin: 0;
  color: var(--color-text-secondary);
}

.empty-desc {
  font-size: 0.8rem;
  margin: 0;
}

/* Lightbox */
.lightbox {
  position: fixed;
  inset: 0;
  z-index: var(--z-lightbox);
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0,0,0,0.8);
  backdrop-filter: blur(8px);
  padding: 2rem;
}

.lightbox img {
  max-width: 90vw;
  max-height: 90vh;
  border-radius: var(--radius-lg);
  box-shadow: 0 25px 50px rgba(0,0,0,0.5);
}

.lightbox-close {
  position: absolute;
  top: 1rem;
  right: 1rem;
  color: rgba(255,255,255,0.7);
  padding: 0.5rem;
  border-radius: var(--radius-md);
  transition: all var(--transition-fast);
}

.lightbox-close:hover {
  color: white;
  background: rgba(255,255,255,0.1);
}


.error-count-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 1.1em;
  height: 1.1em;
  padding: 0 0.3em;
  margin-left: 0.25rem;
  font-size: 0.6rem;
  font-weight: 700;
  background: var(--color-danger-bg);
  color: var(--color-danger);
  border-radius: 999px;
}

.badge-danger .error-count-badge {
  background: rgba(255, 255, 255, 0.25);
  color: white;
}

/* Responsive */
@media (max-width: 768px) {
  .content-area {
    flex-direction: column;
  }
  .article-list {
    width: 100%;
    max-height: 200px;
  }
  .toolbar {
    flex-wrap: wrap;
  }
  .toolbar-search {
    max-width: none;
  }
}
</style>
