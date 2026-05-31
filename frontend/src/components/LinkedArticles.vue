<script setup>
import { ref, computed, onMounted } from 'vue'
import { listLinkedArticles, uploadDocument, downloadDocument, deleteLinkedArticle, getDocumentContent, embedDocument, getDocumentChunks, testRagSearch } from '../api'
import { renderMarkdown } from '../composables/useMarkdown'
import ConfirmDialog from './ConfirmDialog.vue'
import AppToast from './AppToast.vue'
import { useToast } from '../composables/useToast'

const { showToast } = useToast()

const documents = ref([])
const loading = ref(false)
const uploading = ref(false)
const searchQuery = ref('')
const selectedDoc = ref(null)
const previewContent = ref('')
const previewLoading = ref(false)

// Embed 相关
const embeddingIds = ref(new Set())

// 分块详情
const chunksDoc = ref(null)
const chunksData = ref([])
const chunksLoading = ref(false)

// 命中测试
const testQuery = ref('')
const testResults = ref(null)
const testLoading = ref(false)

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

const filteredDocs = computed(() => {
  if (!searchQuery.value.trim()) return documents.value
  const q = searchQuery.value.toLowerCase()
  return documents.value.filter(d => d.title?.toLowerCase().includes(q))
})

async function loadDocuments() {
  loading.value = true
  try {
    const { data } = await listLinkedArticles()
    documents.value = data
  } catch (e) {
    console.error('加载文档列表失败', e)
  } finally {
    loading.value = false
  }
}

function onFileInput(e) {
  const files = e.target.files
  if (!files?.length) return
  handleFiles(files)
  e.target.value = ''
}

async function handleFiles(files) {
  const allowed = ['.txt', '.md', '.pdf', '.docx', '.doc']
  for (const file of files) {
    const ext = '.' + file.name.split('.').pop().toLowerCase()
    if (!allowed.includes(ext)) {
      showToast(`不支持的文件类型: ${ext}，仅支持 .txt / .md / .pdf / .docx`, 'error')
      continue
    }
    uploading.value = true
    try {
      await uploadDocument(file)
    } catch (e) {
      showToast(`上传失败 (${file.name}): ` + (e.response?.data?.detail || e.message), 'error')
    }
  }
  uploading.value = false
  await loadDocuments()
}

async function previewDoc(item) {
  if (selectedDoc.value?.id === item.id) {
    selectedDoc.value = null
    previewContent.value = ''
    return
  }
  selectedDoc.value = item
  previewLoading.value = true
  previewContent.value = ''
  try {
    const { data } = await getDocumentContent(item.id)
    previewContent.value = data.content || ''
  } catch (e) {
    previewContent.value = '加载失败: ' + (e.response?.data?.detail || e.message)
  } finally {
    previewLoading.value = false
  }
}

async function downloadFile(item) {
  try {
    const { data } = await downloadDocument(item.id)
    const url = URL.createObjectURL(data)
    const a = document.createElement('a')
    a.href = url
    a.download = item.title + '.' + (item.file_type || '')
    a.click()
    URL.revokeObjectURL(url)
  } catch (e) {
    showToast('下载失败: ' + (e.response?.data?.detail || e.message), 'error')
  }
}

function removeDoc(item) {
  showConfirm('删除文档', `确定删除「${item.title}」？删除后不可恢复。`, async () => {
    await deleteLinkedArticle(item.id)
    if (selectedDoc.value?.id === item.id) { selectedDoc.value = null; previewContent.value = '' }
    if (chunksDoc.value?.id === item.id) { chunksDoc.value = null; chunksData.value = [] }
    await loadDocuments()
  }, true)
}

function confirmEmbed(item) {
  showConfirm('索引到知识库', `确定对「${item.title}」进行 Embedding 索引？`, async () => {
    await doEmbed(item)
  })
}

async function doEmbed(item) {
  embeddingIds.value.add(item.id)
  try {
    const { data } = await embedDocument(item.id)
    showToast(`索引完成，共 ${data.chunks_indexed} 个文本块已入库`, 'success')
    await loadDocuments()
  } catch (e) {
    showToast('索引失败: ' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    embeddingIds.value.delete(item.id)
  }
}

async function showChunks(item) {
  if (chunksDoc.value?.id === item.id) {
    chunksDoc.value = null
    chunksData.value = []
    return
  }
  chunksDoc.value = item
  chunksLoading.value = true
  chunksData.value = []
  try {
    const { data } = await getDocumentChunks(item.id)
    chunksData.value = data.chunks || []
  } catch (e) {
    chunksData.value = []
  } finally {
    chunksLoading.value = false
  }
}

function getEmbedStatus(item) {
  const s = item.embed_status || 'pending'
  return {
    pending: { label: '未索引', cls: 'status-pending' },
    embedding: { label: '索引中', cls: 'status-embedding' },
    done: { label: '已索引', cls: 'status-done' },
    failed: { label: '失败', cls: 'status-failed' },
  }[s] || { label: s, cls: 'status-pending' }
}

function formatSize(bytes) {
  if (!bytes) return '-'
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

function formatDate(ts) {
  if (!ts) return ''
  return ts.replace('T', ' ').slice(0, 16)
}

function getTypeBadge(type) {
  return { txt: 'TXT', md: 'MD', pdf: 'PDF', docx: 'DOCX', doc: 'DOC' }[type] || type?.toUpperCase() || '-'
}

function getTypeClass(type) {
  return 'type-' + (type || 'default')
}

async function testSearch() {
  if (!testQuery.value.trim()) return
  testLoading.value = true
  testResults.value = null
  try {
    const { data } = await testRagSearch(testQuery.value)
    testResults.value = data
  } catch (e) {
    showToast('测试失败: ' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    testLoading.value = false
  }
}

onMounted(loadDocuments)
</script>

<template>
  <div class="linked-page">
    <!-- Header -->
    <div class="page-header">
      <div class="header-left">
        <h2 class="page-title">个人文档</h2>
        <span class="doc-count">共 {{ documents.length }} 份文档</span>
      </div>
      <div class="header-actions">
        <div class="search-box">
          <svg class="search-icon" width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
          </svg>
          <input v-model="searchQuery" type="text" placeholder="搜索文件名..." class="search-input" />
        </div>
        <label class="btn-primary upload-btn" :class="{ uploading }">
          <svg v-if="!uploading" width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/>
          </svg>
          <svg v-else class="spinner" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M12 2v4m0 12v4m-7.07-3.93l2.83-2.83m8.48-8.48l2.83-2.83M2 12h4m12 0h4M4.93 4.93l2.83 2.83m8.48 8.48l2.83 2.83"/>
          </svg>
          {{ uploading ? '上传中...' : '上传文档' }}
          <input type="file" accept=".txt,.md,.pdf,.docx,.doc" multiple class="file-input-hidden" @change="onFileInput" :disabled="uploading" />
        </label>
      </div>
    </div>

    <!-- Content -->
    <div class="content-area">
      <!-- List -->
      <div class="doc-list" :class="{ 'has-panel': selectedDoc || chunksDoc }">
        <div v-if="loading" class="list-empty">加载中...</div>
        <div v-else-if="filteredDocs.length === 0" class="list-empty">
          {{ searchQuery ? '没有匹配的文档' : '还没有上传文档' }}
        </div>

        <table v-else class="doc-table">
          <thead>
            <tr>
              <th class="col-type">类型</th>
              <th class="col-name">文件名</th>
              <th class="col-status">索引状态</th>
              <th class="col-chunks">分块</th>
              <th class="col-size">大小</th>
              <th class="col-time">上传时间</th>
              <th class="col-actions">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in filteredDocs" :key="item.id"
              class="doc-row" :class="{ active: selectedDoc?.id === item.id || chunksDoc?.id === item.id }"
              @click="previewDoc(item)">
              <td class="col-type">
                <span :class="['type-badge', getTypeClass(item.file_type)]">{{ getTypeBadge(item.file_type) }}</span>
              </td>
              <td class="col-name">
                <span class="file-name">{{ item.title }}</span>
              </td>
              <td class="col-status">
                <span :class="['status-badge', getEmbedStatus(item).cls]">
                  {{ getEmbedStatus(item).label }}
                </span>
              </td>
              <td class="col-chunks">
                <span v-if="item.chunks_count" class="chunks-count" @click.stop="showChunks(item)">{{ item.chunks_count }}</span>
                <span v-else class="chunks-empty">-</span>
              </td>
              <td class="col-size">{{ formatSize(item.file_size) }}</td>
              <td class="col-time">{{ formatDate(item.created_at) }}</td>
              <td class="col-actions" @click.stop>
                <button @click="confirmEmbed(item)" class="action-btn embed-btn" :disabled="embeddingIds.has(item.id) || item.embed_status === 'embedding'" :title="embeddingIds.has(item.id) || item.embed_status === 'embedding' ? '索引中...' : '索引到知识库'">
                  <svg v-if="embeddingIds.has(item.id) || item.embed_status === 'embedding'" class="spinner" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M12 2v4m0 12v4m-7.07-3.93l2.83-2.83m8.48-8.48l2.83-2.83M2 12h4m12 0h4M4.93 4.93l2.83 2.83m8.48 8.48l2.83 2.83"/>
                  </svg>
                  <svg v-else width="15" height="15" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
                  </svg>
                </button>
                <button @click="showChunks(item)" class="action-btn" :class="{ 'btn-active': chunksDoc?.id === item.id }" title="查看分块">
                  <svg width="15" height="15" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 10h16M4 14h16M4 18h16"/>
                  </svg>
                </button>
                <button @click="downloadFile(item)" class="action-btn" title="下载">
                  <svg width="15" height="15" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/>
                  </svg>
                </button>
                <button @click="removeDoc(item)" class="action-btn delete-btn" title="删除">
                  <svg width="15" height="15" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/>
                  </svg>
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- Preview panel -->
      <div v-if="selectedDoc" class="preview-panel">
        <div class="preview-header">
          <div class="preview-title-row">
            <span :class="['type-badge', getTypeClass(selectedDoc.file_type)]">{{ getTypeBadge(selectedDoc.file_type) }}</span>
            <h3 class="preview-title">{{ selectedDoc.title }}</h3>
          </div>
          <button @click="selectedDoc = null; previewContent = ''" class="action-btn" title="关闭">
            <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
            </svg>
          </button>
        </div>
        <div class="preview-body">
          <div v-if="previewLoading" class="preview-loading">加载内容...</div>
          <div v-else-if="selectedDoc.file_type === 'md'" class="preview-markdown" v-html="renderMarkdown(previewContent)"></div>
          <pre v-else class="preview-text">{{ previewContent }}</pre>
        </div>
      </div>

      <!-- Chunks panel -->
      <div v-if="chunksDoc && !selectedDoc" class="preview-panel">
        <div class="preview-header">
          <div class="preview-title-row">
            <h3 class="preview-title">分块详情：{{ chunksDoc.title }}</h3>
            <span class="chunks-summary" v-if="chunksData.length">共 {{ chunksData.length }} 块</span>
          </div>
          <button @click="chunksDoc = null; chunksData = []" class="action-btn" title="关闭">
            <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
            </svg>
          </button>
        </div>
        <div class="preview-body">
          <div v-if="chunksLoading" class="preview-loading">加载分块...</div>
          <div v-else-if="chunksData.length === 0" class="preview-loading">暂无分块数据，请先索引</div>
          <div v-else class="chunks-list">
            <div v-for="chunk in chunksData" :key="chunk.id" class="chunk-item">
              <div class="chunk-header">
                <span class="chunk-index">#{{ chunk.chunk_index + 1 }}</span>
                <span class="chunk-size">{{ chunk.char_count }} 字</span>
              </div>
              <pre class="chunk-content">{{ chunk.content }}</pre>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 命中测试区域 -->
    <div class="test-section">
      <h3 class="section-title">命中测试</h3>
      <div class="test-bar">
        <input v-model="testQuery" type="text" placeholder="输入查询词测试检索效果..." class="test-input" @keyup.enter="testSearch" />
        <button class="btn-primary" :disabled="testLoading || !testQuery.trim()" @click="testSearch">
          {{ testLoading ? '检索中...' : '测试' }}
        </button>
      </div>
      <div v-if="testResults" class="test-results">
        <!-- 诊断信息 -->
        <div v-if="testResults.debug" class="test-debug">
          <span>FTS5 关键词命中: {{ testResults.debug.fts_count }} 条</span>
          <span>向量语义命中: {{ testResults.debug.vector_count }} 条</span>
          <span>向量库总量: {{ testResults.debug.total_in_chroma }} 条</span>
          <span :class="testResults.debug.chroma_available ? 'status-ok' : 'status-err'">
            {{ testResults.debug.chroma_available ? '向量检索可用' : '向量检索不可用' }}
          </span>
        </div>
        <div v-if="testResults.results.length === 0" class="test-empty">无命中结果</div>
        <div v-else class="test-result-list">
          <div v-for="(r, i) in testResults.results" :key="i" class="test-result-item">
            <div class="result-header">
              <span class="result-rank">#{{ i + 1 }}</span>
              <span class="result-type">{{ r.label }}</span>
              <span class="result-title">{{ r.title }}</span>
              <div class="result-score-bar">
                <div class="score-fill" :style="{ width: (r._score * 100) + '%' }"></div>
                <span class="score-text">{{ (r._score * 100)?.toFixed(0) }}%</span>
              </div>
            </div>
            <pre class="result-body">{{ r.body?.slice(0, 300) }}{{ r.body?.length > 300 ? '...' : '' }}</pre>
          </div>
        </div>
        <div v-if="testResults.keywords?.length" class="test-keywords">
          关键词：{{ testResults.keywords.join('、') }}
        </div>
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
.linked-page {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 120px);
  overflow-y: auto;
}

/* Header */
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1rem;
  flex-wrap: wrap;
  gap: 0.75rem;
}

.header-left {
  display: flex;
  align-items: baseline;
  gap: 0.75rem;
}

.doc-count {
  font-size: 0.8rem;
  color: var(--color-text-muted);
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.search-box {
  position: relative;
}

.search-icon {
  position: absolute;
  left: 0.65rem;
  top: 50%;
  transform: translateY(-50%);
  color: var(--color-text-muted);
  pointer-events: none;
}

.search-input {
  padding: 0.5rem 0.75rem 0.5rem 2.2rem;
  font-size: 0.85rem;
  width: 220px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-input);
  color: var(--color-text-primary);
  transition: border-color var(--transition-fast);
}

.search-input:focus {
  outline: none;
  border-color: var(--color-primary-400);
}

.upload-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.5rem 1rem;
  font-size: 0.85rem;
  cursor: pointer;
  position: relative;
}

.upload-btn.uploading {
  opacity: 0.7;
  pointer-events: none;
}

.file-input-hidden {
  position: absolute;
  inset: 0;
  opacity: 0;
  cursor: pointer;
}

/* Content area */
.content-area {
  display: flex;
  gap: 1rem;
  flex: 1;
  min-height: 0;
}

.doc-list {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
}

.doc-list.has-panel {
  flex: 0 0 420px;
}

.list-empty {
  text-align: center;
  padding: 3rem;
  color: var(--color-text-muted);
  font-size: 0.85rem;
}

/* Table */
.doc-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.85rem;
}

.doc-table th {
  text-align: left;
  padding: 0.6rem 0.75rem;
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--color-text-muted);
  border-bottom: 1px solid var(--color-border);
  white-space: nowrap;
}

.doc-table td {
  padding: 0.6rem 0.75rem;
  border-bottom: 1px solid var(--color-border-light, var(--color-border));
}

.doc-row {
  cursor: pointer;
  transition: background var(--transition-fast);
}

.doc-row:hover {
  background: var(--color-bg-hover);
}

.doc-row.active {
  background: var(--color-primary-50);
}

.dark .doc-row.active {
  background: rgba(201, 168, 76, 0.1);
}

.col-type { width: 60px; }
.col-status { width: 80px; }
.col-chunks { width: 50px; text-align: center; }
.col-size { width: 80px; font-size: 0.75rem; color: var(--color-text-muted); }
.col-time { width: 130px; font-size: 0.75rem; color: var(--color-text-muted); white-space: nowrap; }
.col-actions { width: 130px; text-align: center; }

.file-name {
  font-weight: 500;
  color: var(--color-text-primary);
}

.type-badge {
  display: inline-block;
  font-size: 0.6rem;
  font-weight: 700;
  padding: 0.1rem 0.35rem;
  border-radius: var(--radius-sm);
  letter-spacing: 0.02em;
}

.type-txt { background: #dbeafe; color: #2563eb; }
.type-md { background: #d1fae5; color: #059669; }
.type-pdf { background: #fee2e2; color: #dc2626; }
.type-docx, .type-doc { background: rgba(201, 168, 76, 0.10); color: #a88a3a; }
.type-default { background: #f3f4f6; color: #6b7280; }
.dark .type-txt { background: rgba(37,99,235,0.2); color: #60a5fa; }
.dark .type-md { background: rgba(5,150,105,0.2); color: #34d399; }
.dark .type-pdf { background: rgba(220,38,38,0.2); color: #f87171; }
.dark .type-docx, .dark .type-doc { background: rgba(201, 168, 76, 0.18); color: #d4b65a; }
.dark .type-default { background: rgba(107,114,128,0.2); color: #9ca3af; }

/* Status badges */
.status-badge {
  display: inline-block;
  font-size: 0.65rem;
  font-weight: 600;
  padding: 0.15rem 0.4rem;
  border-radius: var(--radius-sm);
}

.status-pending { background: #f3f4f6; color: #6b7280; }
.status-embedding { background: #dbeafe; color: #2563eb; }
.status-done { background: #d1fae5; color: #059669; }
.status-failed { background: #fee2e2; color: #dc2626; }
.dark .status-pending { background: rgba(107,114,128,0.2); color: #9ca3af; }
.dark .status-embedding { background: rgba(37,99,235,0.2); color: #60a5fa; }
.dark .status-done { background: rgba(5,150,105,0.2); color: #34d399; }
.dark .status-failed { background: rgba(220,38,38,0.2); color: #f87171; }

/* Chunks count */
.chunks-count {
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--color-primary-600);
  cursor: pointer;
}

.chunks-count:hover {
  text-decoration: underline;
}

.chunks-empty {
  color: var(--color-text-muted);
  font-size: 0.75rem;
}

.doc-actions {
  display: flex;
  gap: 0.25rem;
  justify-content: center;
}

.action-btn {
  padding: 0.35rem;
  border-radius: var(--radius-md);
  color: var(--color-text-muted);
  transition: all var(--transition-fast);
}

.action-btn:hover {
  background: var(--color-bg-hover);
  color: var(--color-text-primary);
}

.action-btn.btn-active {
  color: var(--color-primary-600);
  background: var(--color-primary-50);
}

.dark .action-btn.btn-active {
  background: rgba(201, 168, 76, 0.15);
}

.delete-btn:hover {
  color: #ef4444;
  background: rgba(239, 68, 68, 0.08);
}

.embed-btn:hover {
  color: var(--color-primary-600);
  background: var(--color-primary-50);
}

.dark .embed-btn:hover {
  background: rgba(201, 168, 76, 0.15);
}

.embed-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.spinner {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

/* Preview panel */
.preview-panel {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  overflow: hidden;
}

.preview-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.75rem 1rem;
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
}

.preview-title-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  min-width: 0;
}

.preview-title {
  font-size: 0.9rem;
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chunks-summary {
  font-size: 0.75rem;
  color: var(--color-text-muted);
}

.preview-body {
  flex: 1;
  overflow-y: auto;
  padding: 1rem;
}

.preview-loading {
  text-align: center;
  padding: 2rem;
  color: var(--color-text-muted);
}

.preview-text {
  font-size: 0.85rem;
  line-height: 1.7;
  color: var(--color-text-primary);
  white-space: pre-wrap;
  word-break: break-word;
  font-family: inherit;
  margin: 0;
}

.preview-markdown {
  font-size: 0.85rem;
  line-height: 1.7;
}

.preview-markdown :deep(h1),
.preview-markdown :deep(h2),
.preview-markdown :deep(h3) {
  margin-top: 1em;
  margin-bottom: 0.5em;
  color: var(--color-text-primary);
}

.preview-markdown :deep(p) {
  margin: 0.5em 0;
}

.preview-markdown :deep(blockquote) {
  border-left: 3px solid var(--color-primary-400);
  padding-left: 1em;
  color: var(--color-text-secondary);
  margin: 0.75em 0;
}

.preview-markdown :deep(code) {
  background: var(--color-bg-input);
  padding: 0.15em 0.4em;
  border-radius: var(--radius-sm);
  font-size: 0.85em;
}

.preview-markdown :deep(pre) {
  background: var(--color-bg-input);
  padding: 0.75rem;
  border-radius: var(--radius-md);
  overflow-x: auto;
}

/* Chunks list */
.chunks-list {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.chunk-item {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  overflow: hidden;
}

.chunk-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.4rem 0.75rem;
  background: var(--color-bg-hover);
  font-size: 0.75rem;
}

.chunk-index {
  font-weight: 600;
  color: var(--color-primary-600);
}

.chunk-size {
  color: var(--color-text-muted);
}

.chunk-content {
  padding: 0.5rem 0.75rem;
  font-size: 0.8rem;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0;
  max-height: 200px;
  overflow-y: auto;
  color: var(--color-text-primary);
}

/* Test section */
.test-section {
  margin-top: 1.5rem;
  padding-top: 1.5rem;
  border-top: 1px solid var(--color-border);
}

.section-title {
  font-size: 0.95rem;
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0 0 0.75rem 0;
}

.test-bar {
  display: flex;
  gap: 0.5rem;
}

.test-input {
  flex: 1;
  padding: 0.5rem 0.75rem;
  font-size: 0.85rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-input);
  color: var(--color-text-primary);
}

.test-input:focus {
  outline: none;
  border-color: var(--color-primary-400);
}

.test-results {
  margin-top: 0.75rem;
}

.test-debug {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
  padding: 0.5rem 0.75rem;
  margin-bottom: 0.5rem;
  background: var(--color-bg-hover);
  border-radius: var(--radius-md);
  font-size: 0.75rem;
  color: var(--color-text-secondary);
}

.test-debug .status-ok {
  color: #16a34a;
  font-weight: 600;
}

.test-debug .status-err {
  color: #dc2626;
  font-weight: 600;
}

.result-rank {
  font-size: 0.7rem;
  font-weight: 700;
  color: var(--color-primary-600);
  min-width: 1.5rem;
}

.test-empty {
  text-align: center;
  padding: 1.5rem;
  color: var(--color-text-muted);
  font-size: 0.85rem;
}

.test-result-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.test-result-item {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  overflow: hidden;
}

.result-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.4rem 0.75rem;
  background: var(--color-bg-hover);
  font-size: 0.8rem;
}

.result-type {
  font-size: 0.65rem;
  font-weight: 600;
  padding: 0.1rem 0.35rem;
  border-radius: var(--radius-sm);
  background: var(--color-primary-50);
  color: var(--color-primary-700);
}

.dark .result-type {
  background: rgba(201, 168, 76, 0.15);
  color: var(--color-primary-300);
}

.result-title {
  font-weight: 500;
  color: var(--color-text-primary);
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.result-score {
  font-size: 0.7rem;
  color: var(--color-text-muted);
  font-family: monospace;
}

.result-score-bar {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  min-width: 80px;
}

.score-fill {
  height: 6px;
  background: linear-gradient(90deg, var(--color-primary-400), var(--color-primary-600));
  border-radius: 3px;
  min-width: 4px;
  transition: width 0.3s ease;
}

.score-text {
  font-size: 0.7rem;
  font-weight: 600;
  color: var(--color-primary-600);
  min-width: 2.5rem;
  text-align: right;
}

.result-body {
  padding: 0.5rem 0.75rem;
  font-size: 0.8rem;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0;
  color: var(--color-text-secondary);
  max-height: 150px;
  overflow-y: auto;
}

.test-keywords {
  margin-top: 0.5rem;
  font-size: 0.75rem;
  color: var(--color-text-muted);
}

@media (max-width: 768px) {
  .page-header {
    flex-direction: column;
    align-items: flex-start;
  }
  .header-actions {
    width: 100%;
  }
  .search-input {
    width: 100%;
    flex: 1;
  }
  .content-area {
    flex-direction: column;
  }
  .doc-list.has-panel {
    flex: none;
    max-height: 300px;
  }
  .col-status, .col-chunks {
    display: none;
  }
}
</style>
