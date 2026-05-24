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

/** 获取指数简介信息 */
export function getIndexInfo(indexCode, indexName = '') {
  const params = {}
  if (indexName) params.index_name = indexName
  return api.get(`/index-info/${indexCode}`, { params })
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

/** 获取单个 Agent 详情 */
export function getAgent(id) {
  return api.get(`/agents/${id}`)
}

/** 更新 Agent */
export function updateAgent(id, data) {
  return api.put(`/agents/${id}`, data)
}

/** 删除自定义 Agent */
export function deleteAgent(id) {
  return api.delete(`/agents/${id}`)
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

/**
 * SSE 流式发送消息，实时接收工具调用和回答。
 * @param {number} convId - 对话 ID
 * @param {string} content - 消息内容
 * @param {function} onEvent - 事件回调 (event: {type, data}) => void
 * @returns {AbortController} 用于取消请求
 */
export function sendMessageStream(convId, content, onEvent) {
  const controller = new AbortController()
  const baseURL = api.defaults.baseURL || ''

  fetch(`${baseURL}/conversations/${convId}/messages/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
    signal: controller.signal,
  }).then(async response => {
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      const lines = buffer.split('\n')
      buffer = lines.pop() // 保留不完整的行

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try {
            const event = JSON.parse(line.slice(6))
            onEvent(event)
          } catch (e) {
            // 忽略解析错误
          }
        }
      }
    }
    // 处理剩余 buffer
    if (buffer.startsWith('data: ')) {
      try {
        const event = JSON.parse(buffer.slice(6))
        onEvent(event)
      } catch (e) {}
    }
  }).catch(err => {
    if (err.name !== 'AbortError') {
      onEvent({ type: 'error', data: { message: err.message } })
    }
  })

  return controller
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

/** 获取文档分块详情 */
export function getDocumentChunks(id) {
  return api.get(`/linked-articles/${id}/chunks`)
}

/** RAG 命中测试 */
export function testRagSearch(query, limit = 5, contentTypes = null) {
  return api.post('/rag/test-search', { query, limit, content_types: contentTypes })
}

/** 删除文档 */
export function deleteLinkedArticle(id) {
  return api.delete(`/linked-articles/${id}`)
}

// ── AI 市场分析 API ──────────────────────────────────────

/** 触发 AI 分析 */
export function runAnalysis(indexCode, indexName, agentId = 1) {
  return api.post('/analysis/run', { index_code: indexCode, index_name: indexName, agent_id: agentId }, { timeout: 300000 })
}

/** 分析历史列表 */
export function listAnalysisHistory(indexCode = '', limit = 50) {
  const params = { limit }
  if (indexCode) params.index_code = indexCode
  return api.get('/analysis/history', { params })
}

/** 分析历史详情 */
export function getAnalysisHistoryDetail(id) {
  return api.get(`/analysis/history/${id}`)
}

/** 删除分析历史 */
export function deleteAnalysisHistory(id) {
  return api.delete(`/analysis/history/${id}`)
}

/** Agent 配置列表 */
export function listAnalysisAgents() {
  return api.get('/analysis-agents')
}

/** 更新 Agent 配置 */
export function updateAnalysisAgent(id, data) {
  return api.put(`/analysis-agents/${id}`, data)
}

// ── 债市数据 API ──────────────────────────────────────

/** 获取债市温度数据 */
export function getBondMarketTemperature() {
  return api.get('/bond/market-temperature')
}

// ── 持仓管理 API ──────────────────────────────────────

/** 获取所有持仓 */
export function listPortfolios() {
  return api.get('/portfolio')
}

/** 获取持仓汇总 */
export function getPortfolioSummary() {
  return api.get('/portfolio/summary')
}

/** 新增持仓 */
export function createPortfolio(data) {
  return api.post('/portfolio', data)
}

/** 获取单个持仓 */
export function getPortfolio(id) {
  return api.get(`/portfolio/${id}`)
}

/** 更新持仓 */
export function updatePortfolio(id, data) {
  return api.put(`/portfolio/${id}`, data)
}

/** 删除持仓 */
export function deletePortfolio(id) {
  return api.delete(`/portfolio/${id}`)
}

/** 获取持仓交易记录 */
export function listPortfolioTransactions(holdingId, limit = 100) {
  return api.get(`/portfolio/${holdingId}/transactions`, { params: { limit } })
}

/** 新增交易记录 */
export function createPortfolioTransaction(data) {
  return api.post('/portfolio/transactions', data)
}

/** 确认交易（填入 T+1 实际净值） */
export function confirmTransaction(txId, data) {
  return api.post(`/portfolio/transactions/${txId}/confirm`, data)
}

/** 标记卖出交易已到账 */
export function settleTransaction(txId) {
  return api.post(`/portfolio/transactions/${txId}/settle`)
}

/** 刷新所有持仓净值 */
export function refreshAllPortfolioPrices() {
  return api.post('/portfolio/refresh', {}, { timeout: 120000 })
}

/** 刷新单个持仓净值 */
export function refreshPortfolioPrice(holdingId) {
  return api.post(`/portfolio/${holdingId}/refresh`, {}, { timeout: 30000 })
}

// ── 基金信息查询 API ──────────────────────────────────────

/** 根据基金代码查询基本信息 */
export function lookupFundInfo(fundCode) {
  return api.get('/fund/lookup', { params: { code: fundCode } })
}

/** 获取基金持仓详情（重仓股、债券、资产配置） */
export function getFundHoldings(fundCode, year = null) {
  const params = { code: fundCode }
  if (year) params.year = year
  return api.get('/fund/holdings', { params, timeout: 30000 })
}

export default api
