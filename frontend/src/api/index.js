import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 120000,
})

// ── 任务 API ──────────────────────────────────────

/** 创建任务（提交链接） */
export function createTask(url) {
  return api.post('/tasks', { url })
}

/** 任务列表 */
export function listTasks(limit = 50) {
  return api.get('/tasks', { params: { limit } })
}

/** 任务详情 */
export function getTask(taskId) {
  return api.get(`/tasks/${taskId}`)
}

/** 删除任务 */
export function deleteTask(taskId) {
  return api.delete(`/tasks/${taskId}`)
}

/** 获取任务图片 */
export function getTaskImages(taskId) {
  return api.get(`/tasks/${taskId}/images`)
}

/** 自由问答 */
export function chat(question, context = '') {
  return api.post('/chat', { question, context })
}

/** 获取 K 线图数据 */
export function getChart(symbol, days = 180, isFund = false) {
  return api.get(`/chart/${symbol}`, { params: { days, fund: isFund } })
}

/** 分析任务中的所有图片 */
export function analyzeTaskImages(taskId) {
  return api.post(`/tasks/${taskId}/analyze-images`, {}, { timeout: 300000 })
}

// ── 估值数据 API ──────────────────────────────────────

/** 解析图片并存储估值数据 */
export function parseAndSaveValuation(path, modelType = 'mimo', snapshotDate = null) {
  return api.post('/valuations/parse', { path, model_type: modelType, snapshot_date: snapshotDate }, { timeout: 300000 })
}

/** 列出所有有估值数据的指数 */
export function listValuationIndexes() {
  return api.get('/valuations')
}

/** 查询某指数的估值历史 */
export function getValuationHistory(indexCode, days = 30, metricType = null) {
  const params = { days }
  if (metricType) params.metric_type = metricType
  return api.get(`/valuations/${indexCode}`, { params })
}

// ── 轮询工具 ──────────────────────────────────────

/**
 * 轮询任务状态直到完成
 * @param {number} taskId
 * @param {function} onProgress - 每次轮询回调
 * @param {number} interval - 轮询间隔 ms
 */
export function pollTask(taskId, onProgress, interval = 2000) {
  let stopped = false

  const check = async () => {
    if (stopped) return
    try {
      const { data } = await getTask(taskId)
      onProgress(data)
      if (data.status === 'done' || data.status === 'error') {
        return
      }
    } catch (e) {
      console.error('Poll error:', e)
    }
    if (!stopped) {
      setTimeout(check, interval)
    }
  }

  check()

  return () => { stopped = true }
}

// ── 文章管理 API ──────────────────────────────────────

/** 添加文章（粘贴链接，自动解析+下载+分析） */
export function addArticle(url) {
  return api.post('/articles/add', { url })
}

/** 同步文章 */
export function syncArticles() {
  return api.post('/articles/sync')
}

/** 文章列表 */
export function fetchArticles(status = '') {
  const params = { _t: Date.now() }
  if (status) params.status = status
  return api.get('/articles', { params })
}

/** 文章详情（含分析记录） */
export function fetchArticle(id) {
  return api.get(`/articles/${id}`, { params: { _t: Date.now() } })
}

/** 下载文章图片 */
export function downloadArticleImages(id) {
  return api.post(`/articles/${id}/download`, {}, { timeout: 120000 })
}

/** 分析文章所有图片（异步触发） */
export function analyzeArticleImages(id) {
  return api.post(`/articles/${id}/analyze`, {}, { timeout: 30000 })
}

/** 查询分析任务进度 */
export function getAnalyzeStatus(id) {
  return api.get(`/articles/${id}/analyze-status`)
}

/** 取消分析任务 */
export function cancelAnalyze(id) {
  return api.post(`/articles/${id}/cancel-analyze`)
}

/** 重新分析单张图片（异步触发） */
export function reanalyzeImage(recordId) {
  return api.post(`/records/${recordId}/reanalyze`, {}, { timeout: 10000 })
}

/** 查询单张图片重新分析状态 */
export function getReanalyzeStatus(recordId) {
  return api.get(`/records/${recordId}/reanalyze-status`)
}

// ── Agent 对话 API ──────────────────────────────────────

/** 列出所有 Agent */
export function listAgents() {
  return api.get('/agents')
}

/** 创建自定义 Agent */
export function createAgent(data) {
  return api.post('/agents', data)
}

/** 对话列表 */
export function listConversations() {
  return api.get('/conversations')
}

/** 创建对话 */
export function createConversation(data) {
  return api.post('/conversations', data)
}

/** 删除对话 */
export function deleteConversation(id) {
  return api.delete(`/conversations/${id}`)
}

/** 获取对话消息历史 */
export function getMessages(convId, limit = 50) {
  return api.get(`/conversations/${convId}/messages`, { params: { limit } })
}

/** 发送消息 */
export function sendMessage(convId, content) {
  return api.post(`/conversations/${convId}/messages`, { content }, { timeout: 120000 })
}

/** 重建 RAG 索引 */
export function reindexRag() {
  return api.post('/rag/reindex')
}

/** 获取 RAG 检索统计 */
export function getRagStats(days = 7) {
  return api.get('/rag-stats', { params: { days } })
}

/** 获取 RAG 检索日志 */
export function getRagLogs(limit = 100) {
  return api.get('/rag-logs', { params: { limit } })
}

// ── 图片浏览 API ──────────────────────────────────────

/** 列出所有分析记录，支持搜索 */
export function listGalleryRecords(search = '', limit = 200) {
  const params = { limit }
  if (search) params.search = search
  return api.get('/gallery', { params })
}

// ── 作者文章 API ──────────────────────────────────────

/** 从 Excel 导入作者文章 */
export function importAuthorArticles() {
  return api.post('/author-articles/import')
}

/** 从 URL 提取文章信息 */
export function extractAuthorArticle(url) {
  return api.post('/author-articles/extract', { url }, { timeout: 30000 })
}

/** 直接创建作者文章 */
export function createAuthorArticle(data) {
  return api.post('/author-articles', data)
}

/** 批量爬取所有 pending 文章 */
export function crawlAuthorArticles() {
  return api.post('/author-articles/crawl', {}, { timeout: 600000 })
}

/** 作者文章列表 */
export function listAuthorArticles(params = {}) {
  return api.get('/author-articles', { params })
}

/** 作者文章详情 */
export function getAuthorArticle(id) {
  return api.get(`/author-articles/${id}`)
}

/** 删除作者文章 */
export function deleteAuthorArticle(id) {
  return api.delete(`/author-articles/${id}`)
}

/** 爬取单篇作者文章 */
export function crawlSingleAuthorArticle(id) {
  return api.post(`/author-articles/${id}/crawl`, {}, { timeout: 120000 })
}

// ── 个人文档 API ──────────────────────────────────────

/** 文档列表 */
export function listLinkedArticles(limit = 200) {
  return api.get('/linked-articles', { params: { limit } })
}

/** 上传文档 */
export function uploadDocument(file) {
  const form = new FormData()
  form.append('file', file)
  return api.post('/linked-articles', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 300000,
  })
}

/** 下载文档 */
export function downloadDocument(id) {
  return api.get(`/linked-articles/${id}/download`, { responseType: 'blob' })
}

/** 获取文档内容 */
export function getDocumentContent(id) {
  return api.get(`/linked-articles/${id}/content`)
}

/** 对文档做 embedding */
export function embedDocument(id) {
  return api.post(`/linked-articles/${id}/embed`, {}, { timeout: 300000 })
}

/** 删除文档 */
export function deleteLinkedArticle(id) {
  return api.delete(`/linked-articles/${id}`)
}

// ── 债市数据 API ──────────────────────────────────────

/** 获取债市温度数据 */
export function getBondMarketTemperature() {
  return api.get('/bond/market-temperature')
}

export default api
