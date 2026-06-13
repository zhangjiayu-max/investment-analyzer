<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import {
  listAuthorArticles, getAuthorArticle,
  deleteAuthorArticle, crawlSingleAuthorArticle,
  extractAuthorArticle, createAuthorArticle,
} from '../api'
import ConfirmDialog from './ConfirmDialog.vue'
import AppToast from './AppToast.vue'
import { useToast } from '../composables/useToast'

const { showToast } = useToast()

const articles = ref([])
const stats = ref({ total: 0, pending: 0, crawling: 0, done: 0, error: 0 })
const loading = ref(false)
const selectedArticle = ref(null)
const extractUrl = ref('')
const extracting = ref(false)
const extractedData = ref(null)
const statusFilter = ref('')
const searchQuery = ref('')

const confirm = ref({ visible: false, title: '', message: '', danger: false, action: null })
function showConfirm(title, message, action, danger = false) {
  confirm.value = { visible: true, title, message, danger, action, loading: false }
}
async function onConfirmOk() {
  if (!confirm.value.action) return
  confirm.value.loading = true
  try { await confirm.value.action() }
  finally { confirm.value.loading = false; confirm.value.visible = false; confirm.value.action = null }
}
function onConfirmCancel() {
  confirm.value.visible = false
  confirm.value.action = null
}

let pollTimer = null

onMounted(() => loadArticles())
onUnmounted(() => { if (pollTimer) clearInterval(pollTimer) })

async function loadArticles() {
  loading.value = true
  try {
    const { data } = await listAuthorArticles({ limit: 500 })
    articles.value = data.articles || []
    stats.value = data.stats || {}
    // 如果当前选中的文章在列表中，同步完整状态
    if (selectedArticle.value) {
      const updated = articles.value.find(a => a.id === selectedArticle.value.id)
      if (updated) {
        selectedArticle.value.status = updated.status
        selectedArticle.value.content_text = updated.content_text || selectedArticle.value.content_text
        selectedArticle.value.summary = updated.summary || selectedArticle.value.summary
      }
    }
  } catch (e) {
    console.error(e)
  } finally {
    loading.value = false
  }
}

async function handleExtract() {
  const url = extractUrl.value.trim()
  if (!url) return
  extracting.value = true
  extractedData.value = null
  try {
    const { data } = await extractAuthorArticle(url)
    extractedData.value = data
  } catch (e) {
    showToast('提取失败: ' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    extracting.value = false
  }
}

async function handleSaveExtracted() {
  if (!extractedData.value) return
  try {
    await createAuthorArticle(extractedData.value)
    extractedData.value = null
    extractUrl.value = ''
    await loadArticles()
  } catch (e) {
    showToast('保存失败: ' + (e.response?.data?.detail || e.message), 'error')
  }
}

function cancelExtract() {
  extractedData.value = null
  extractUrl.value = ''
}

async function handleCrawlSingle(id) {
  try {
    await crawlSingleAuthorArticle(id)
    // 立即更新选中文章状态
    if (selectedArticle.value?.id === id) {
      selectedArticle.value.status = 'crawling'
    }
    startPolling()
  } catch (e) {
    showToast('爬取失败: ' + e.message, 'error')
  }
}

async function handleDelete(id) {
  try {
    await deleteAuthorArticle(id)
    if (selectedArticle.value?.id === id) selectedArticle.value = null
    await loadArticles()
  } catch (e) {
    showToast('删除失败: ' + e.message, 'error')
  }
}

async function openArticle(article) {
  // 先立即显示基本信息，避免点击无响应
  selectedArticle.value = { ...article }
  // 后台加载完整详情
  try {
    const { data } = await getAuthorArticle(article.id)
    // 确保用户没有切换到其他文章
    if (selectedArticle.value?.id === article.id) {
      selectedArticle.value = data
    }
  } catch {
    // 加载失败时保留基本信息
  }
}

function startPolling() {
  if (pollTimer) return
  pollTimer = setInterval(async () => {
    await loadArticles()
    const pending = stats.value.pending || 0
    const crawling = stats.value.crawling || 0
    if (pending === 0 && crawling === 0) {
      clearInterval(pollTimer)
      pollTimer = null
      // 轮询结束后，如果还有选中的文章，刷新其详情
      if (selectedArticle.value) {
        try {
          const { data } = await getAuthorArticle(selectedArticle.value.id)
          selectedArticle.value = data
        } catch {}
      }
    }
  }, 3000)
}

const filteredArticles = computed(() => {
  let list = articles.value
  if (statusFilter.value) list = list.filter(a => a.status === statusFilter.value)
  if (searchQuery.value.trim()) {
    const q = searchQuery.value.trim().toLowerCase()
    list = list.filter(a =>
      (a.title || '').toLowerCase().includes(q) ||
      (a.summary || '').toLowerCase().includes(q)
    )
  }
  // 按时间排序（最新优先）
  list = [...list].sort((a, b) => {
    const timeA = parseTimeToTimestamp(a.publish_time || a.created_at || '')
    const timeB = parseTimeToTimestamp(b.publish_time || b.created_at || '')
    return timeB - timeA
  })
  return list
})

function parseTimeToTimestamp(timeStr) {
  if (!timeStr) return 0
  // 处理 "2026年5月7日 20:23" 格式
  const cnMatch = timeStr.match(/(\d{4})年(\d{1,2})月(\d{1,2})日\s*(\d{1,2}):(\d{2})/)
  if (cnMatch) {
    const [, year, month, day, hour, minute] = cnMatch
    return new Date(year, month - 1, day, hour, minute).getTime()
  }
  // 处理 "2026-05-23 17:33:15" 或 "2026-05-23T17:33:15" 格式
  return new Date(timeStr.replace('T', ' ')).getTime() || 0
}

function statusClass(s) {
  return { pending: 'badge-warning', crawling: 'badge-info', done: 'badge-success', error: 'badge-danger' }[s] || 'badge-neutral'
}

function statusLabel(s) {
  return { pending: '待爬取', crawling: '爬取中', done: '已完成', error: '失败' }[s] || s
}

function formatDate(ts) {
  if (!ts) return ''
  // 处理 "2026-05-27 22:54:54" 或 "2026-05-27T22:54:54" 格式
  return ts.replace('T', ' ').slice(0, 16)
}

function parseImages(img) {
  if (!img) return []
  if (Array.isArray(img)) return img
  try { return JSON.parse(img) } catch { return [] }
}

function imgUrl(url) {
  // 微信图片走代理绕过防盗链
  if (url && url.includes('mmbiz.qpic.cn')) {
    return '/api/proxy-image?url=' + encodeURIComponent(url)
  }
  return url
}
</script>

<template>
  <div class="author-page">
    <!-- URL extract bar -->
    <div class="extract-bar card">
      <form @submit.prevent="handleExtract" class="extract-form">
        <input v-model="extractUrl" type="url" placeholder="输入文章链接，自动提取标题、作者、摘要..." class="input-field extract-input" :disabled="extracting" />
        <button type="submit" class="btn-primary extract-btn" :disabled="extracting || !extractUrl.trim()">
          {{ extracting ? '提取中...' : '提取' }}
        </button>
      </form>
      <!-- Extracted preview -->
      <div v-if="extractedData" class="extract-preview">
        <div class="extract-info">
          <div class="extract-field"><span class="field-label">标题</span><span class="field-value">{{ extractedData.title || '-' }}</span></div>
          <div class="extract-field"><span class="field-label">作者</span><span class="field-value">{{ extractedData.author || '-' }}</span></div>
          <div class="extract-field"><span class="field-label">时间</span><span class="field-value">{{ extractedData.publish_time || '-' }}</span></div>
          <div class="extract-field"><span class="field-label">摘要</span><span class="field-value summary">{{ extractedData.summary || '-' }}</span></div>
        </div>
        <div class="extract-actions">
          <button @click="handleSaveExtracted" class="btn-primary btn-sm">保存入库</button>
          <button @click="cancelExtract" class="btn-ghost btn-sm">取消</button>
        </div>
      </div>
    </div>

    <!-- Toolbar -->
    <div class="toolbar">
      <button @click="loadArticles" :disabled="loading" class="btn-secondary toolbar-btn">
        <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
        刷新
      </button>
      <select v-model="statusFilter" class="input-field toolbar-select">
        <option value="">全部状态</option>
        <option value="pending">待爬取</option>
        <option value="crawling">爬取中</option>
        <option value="done">已完成</option>
        <option value="error">失败</option>
      </select>
      <input v-model="searchQuery" placeholder="搜索标题或摘要..." class="input-field toolbar-search" />
      <span class="toolbar-count">共 {{ filteredArticles.length }} 篇</span>
    </div>

    <div class="content-area">
      <!-- Article List -->
      <div class="article-list card">
        <div class="list-scroll">
          <table class="list-table">
            <thead>
              <tr>
                <th class="col-id">ID</th>
                <th class="col-title">标题</th>
                <th class="col-time">时间</th>
                <th class="col-status">状态</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="a in filteredArticles" :key="a.id"
                @click="openArticle(a)"
                :class="['list-row', { selected: selectedArticle?.id === a.id }]"
              >
                <td class="col-id">{{ a.id }}</td>
                <td class="col-title">
                  <span class="title-text">{{ a.title || '无标题' }}</span>
                  <span class="title-tooltip">{{ a.title || '无标题' }}</span>
                </td>
                <td class="col-time">{{ formatDate(a.publish_time || a.created_at) }}</td>
                <td class="col-status">
                  <span :class="['badge', statusClass(a.status)]">{{ statusLabel(a.status) }}</span>
                </td>
              </tr>
            </tbody>
          </table>
          <div v-if="!filteredArticles.length" class="list-empty">
            {{ articles.length ? '无匹配结果' : '暂无文章' }}
          </div>
        </div>
      </div>

      <!-- Detail Panel -->
      <div v-if="selectedArticle" class="detail-panel card">
        <div class="detail-header">
          <div class="detail-info">
            <h3 class="detail-title">{{ selectedArticle.title || '无标题' }}</h3>
            <div class="detail-meta">
              <span class="meta-item">
                <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>
                {{ formatDate(selectedArticle.publish_time) || '未知日期' }}
              </span>
              <span v-if="selectedArticle.article_type" class="meta-item">
                <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A1.994 1.994 0 013 12V7a4 4 0 014-4z"/></svg>
                {{ selectedArticle.article_type }}
              </span>
              <a v-if="selectedArticle.url" :href="selectedArticle.url" target="_blank" rel="noopener" class="meta-item meta-link">
                <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"/></svg>
                原文链接
              </a>
              <span :class="['badge', statusClass(selectedArticle.status)]">{{ statusLabel(selectedArticle.status) }}</span>
            </div>
          </div>
          <div class="detail-actions">
            <button
              @click="showConfirm('爬取全文', '确定要爬取这篇文章的全文？已有内容会被替换。', () => handleCrawlSingle(selectedArticle.id))"
              :disabled="selectedArticle.status === 'crawling'"
              class="btn-primary btn-green"
            >
              <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
              {{ selectedArticle.status === 'crawling' ? '爬取中...' : '重新爬取' }}
            </button>
            <button @click="showConfirm('删除文章', '确定删除这篇文章？删除后不可恢复。', () => handleDelete(selectedArticle.id), true)" class="btn-secondary btn-danger">
              删除
            </button>
          </div>
        </div>

        <!-- Summary -->
        <div v-if="selectedArticle.summary" class="detail-section">
          <h4 class="section-title">摘要</h4>
          <p class="summary-text">{{ selectedArticle.summary }}</p>
        </div>

        <!-- Content -->
        <div v-if="selectedArticle.content_text" class="detail-section">
          <h4 class="section-title">全文内容</h4>
          <div class="content-body" v-html="selectedArticle.content_html || selectedArticle.content_text"></div>
        </div>

        <!-- Images -->
        <div v-if="parseImages(selectedArticle.images).length" class="detail-section">
          <h4 class="section-title">文章配图 ({{ parseImages(selectedArticle.images).length }})</h4>
          <div class="article-images">
            <img
              v-for="(url, i) in parseImages(selectedArticle.images)" :key="i"
              :src="imgUrl(url)" loading="lazy" class="article-img"
            />
          </div>
        </div>
        <div v-else-if="selectedArticle.status === 'pending'" class="empty-records">
          <svg width="48" height="48" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253"/></svg>
          <p>文章未爬取，点击"爬取全文"获取内容</p>
        </div>
        <div v-else-if="selectedArticle.status === 'crawling'" class="empty-records">
          <svg class="spinner-icon" width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 2v4m0 12v4m-7.07-3.93l2.83-2.83m8.48-8.48l2.83-2.83M2 12h4m12 0h4M4.93 4.93l2.83 2.83m8.48 8.48l2.83 2.83"/></svg>
          <p>正在爬取中...</p>
        </div>
        <div v-else-if="selectedArticle.status === 'error'" class="empty-records">
          <svg width="48" height="48" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4.5c-.77-.833-2.694-.833-3.464 0L3.34 16.5c-.77.833.192 2.5 1.732 2.5z"/></svg>
          <p>爬取失败</p>
          <p v-if="selectedArticle.error_msg" class="error-msg">{{ selectedArticle.error_msg }}</p>
        </div>
      </div>

      <!-- Empty State -->
      <div v-if="!selectedArticle" class="empty-panel card">
        <svg width="64" height="64" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253"/></svg>
        <p class="empty-title">请从左侧选择一篇文章</p>
        <p class="empty-desc">选择后可查看摘要和全文内容</p>
      </div>
    </div>
  </div>

  <ConfirmDialog
    :visible="confirm.visible" :title="confirm.title" :message="confirm.message"
    :danger="confirm.danger" :loading="confirm.loading"
    confirm-text="确定" cancel-text="取消"
    @confirm="onConfirmOk" @cancel="onConfirmCancel"
  />
  <AppToast />
</template>

<style scoped>
.author-page {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 120px);
  height: calc(100dvh - 120px);
}

/* Extract bar */
.extract-bar {
  margin-bottom: 0.75rem;
  padding: 0.75rem 1rem;
}
.extract-form { display: flex; gap: 0.5rem; }
.extract-input { flex: 1; font-size: 0.85rem; }
.extract-btn { white-space: nowrap; padding: 0.5rem 1.25rem; font-size: 0.85rem; }
.extract-preview { margin-top: 0.75rem; padding-top: 0.75rem; border-top: 1px solid var(--color-border); display: flex; gap: 1rem; align-items: flex-start; }
.extract-info { flex: 1; display: flex; flex-direction: column; gap: 0.25rem; min-width: 0; }
.extract-field { display: flex; gap: 0.5rem; font-size: 0.8rem; }
.field-label { color: var(--color-text-muted); flex-shrink: 0; min-width: 2.5rem; }
.field-value { color: var(--color-text-primary); font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.field-value.summary { white-space: normal; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
.extract-actions { display: flex; gap: 0.5rem; flex-shrink: 0; }
.btn-sm { padding: 0.35rem 0.75rem; font-size: 0.8rem; }

/* Toolbar */
.toolbar {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 1rem;
  flex-wrap: wrap;
}

.toolbar-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.5rem 0.85rem;
  font-size: 0.8rem;
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

.toolbar-divider {
  width: 1px;
  height: 24px;
  background: var(--color-border);
  flex-shrink: 0;
}

.btn-green {
  background: linear-gradient(135deg, #059669, #10b981);
}

.btn-green:hover:not(:disabled) {
  background: linear-gradient(135deg, #047857, #059669);
}

.btn-danger {
  color: var(--color-danger);
  border-color: var(--color-danger);
}

.btn-danger:hover {
  background: rgba(239, 68, 68, 0.08);
}

/* Content Area */
.content-area {
  display: flex;
  gap: 1rem;
  flex: 1;
  min-height: 0;
}

/* Article List */
.article-list {
  width: 400px;
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
  table-layout: fixed;
}

.list-table thead {
  position: sticky;
  top: 0;
  z-index: 1;
}

.list-table th {
  background: var(--color-bg-input);
  padding: 0.6rem 0.5rem;
  text-align: left;
  font-size: 0.7rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--color-text-muted);
}

.list-table td {
  padding: 0.5rem 0.5rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.col-id {
  width: 50px;
  white-space: nowrap;
  font-size: 0.75rem;
  color: var(--color-text-muted);
  text-align: center;
}
.col-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 500;
  color: var(--color-text-primary);
  position: relative;
}
.col-title .title-text {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
}
.col-title .title-tooltip {
  display: none;
  position: absolute;
  left: 0;
  top: 100%;
  z-index: 10;
  background: var(--color-bg-elevated, #1e1e2e);
  color: var(--color-text-primary);
  padding: 0.5rem 0.75rem;
  border-radius: 6px;
  font-size: 0.8rem;
  white-space: normal;
  word-break: break-all;
  max-width: 400px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.3);
  pointer-events: none;
}
.col-title:hover .title-tooltip {
  display: block;
}
.col-time {
  width: 90px;
  white-space: nowrap;
  font-size: 0.75rem;
  color: var(--color-text-muted);
}
.col-status {
  width: 70px;
  white-space: nowrap;
  text-align: center;
}

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
  padding: 0.6rem 0.5rem;
}

.row-title {
  font-weight: 500;
  color: var(--color-text-primary);
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
  padding: 1.25rem;
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
  gap: 0.75rem;
  margin-top: 0.5rem;
  flex-wrap: wrap;
}

.meta-item {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.75rem;
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

.detail-actions .btn-primary,
.detail-actions .btn-secondary {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.5rem 0.85rem;
  font-size: 0.8rem;
}


/* Detail Sections */
.detail-section {
  margin-bottom: 1.25rem;
}

.section-title {
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--color-text-secondary);
  margin: 0 0 0.5rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.summary-text {
  font-size: 0.85rem;
  color: var(--color-text-primary);
  line-height: 1.7;
  margin: 0;
  padding: 0.75rem 1rem;
  background: var(--color-bg-input);
  border-radius: var(--radius-md);
  border-left: 3px solid var(--color-primary-400);
}

.content-body {
  font-size: 0.82rem;
  line-height: 1.8;
  color: var(--color-text-primary);
  max-height: 60vh;
  overflow-y: auto;
  padding: 1rem;
  background: var(--color-bg-input);
  border-radius: var(--radius-md);
}

.content-body :deep(p) {
  margin: 0 0 0.75rem;
}

.content-body :deep(img) {
  max-width: 100%;
  height: auto;
  border-radius: var(--radius-sm);
  margin: 0.5rem 0;
  display: block;
}

.content-body :deep(section),
.content-body :deep(div) {
  max-width: 100%;
}

.content-body :deep(blockquote) {
  border-left: 3px solid var(--color-primary-400);
  padding-left: 0.75rem;
  margin: 0.75rem 0;
  color: var(--color-text-secondary);
}

.content-body :deep(h2),
.content-body :deep(h3) {
  font-size: 0.95rem;
  font-weight: 600;
  margin: 1rem 0 0.5rem;
}

.article-images {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 0.5rem;
}

.article-img {
  width: 100%;
  border-radius: var(--radius-sm);
  border: 1px solid var(--color-border);
  cursor: pointer;
  transition: transform var(--transition-fast);
}

.article-img:hover {
  transform: scale(1.02);
}

/* Empty States */
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

.error-msg {
  font-size: 0.75rem;
  color: var(--color-danger);
  max-width: 400px;
  text-align: center;
}

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

.spinner-icon {
  animation: spin 1s linear infinite;
}

/* Responsive */
@media (max-width: 768px) {
  .content-area {
    flex-direction: column;
  }
  .article-list {
    width: 100%;
    max-height: 250px;
  }
  .toolbar {
    flex-wrap: wrap;
  }
  .toolbar-search {
    max-width: none;
  }
}

</style>
