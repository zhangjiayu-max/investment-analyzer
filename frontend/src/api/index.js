import api from './http'
import './interceptors'

// 拦截器在 interceptors.js 中注册

// ── 任务 API（新路径: /api/task/*）─────────────────────────────────────

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
  return api.get(`/task/${taskId}/images`)
}

/** 分析任务中的所有图片 */
export function analyzeTaskImages(taskId) {
  return api.post(`/tasks/${taskId}/analyze-images`, {}, { timeout: 300000 })
}

// ── 对话 API（新路径: /api/conversation/*）─────────────────────────────────────

/** 自由问答 */
export function chat(question, context = '') {
  return api.post('/chat', { question, context })
}

// ── 图表 API ──────────────────────────────────────

/** 获取 K 线图数据 */
export function getChart(symbol, days = 180, isFund = false) {
  return api.get(`/chart/${symbol}`, { params: { days, fund: isFund } })
}

// ── 估值数据 API（新路径: /api/valuation/*）──────────────────────────────────────

/** 解析图片并存储估值数据 */
export function parseAndSaveValuation(path, modelType = 'mimo', snapshotDate = null) {
  return api.post('/valuation/parse', { path, model_type: modelType, snapshot_date: snapshotDate }, { timeout: 300000 })
}

/** 批量并发解析多张估值图片 */
export function parseValuationBatch(paths, modelType = 'mimo') {
  return api.post('/valuation/parse-batch', { paths, model_type: modelType }, { timeout: 600000 })
}

/** 解析螺丝钉估值表图片（多指数表格数据） */
export function parseDDImage(path, modelType = 'mimo') {
  return api.post('/valuation/parse-dd', { path, model_type: modelType }, { timeout: 300000 })
}

/** 异步解析螺丝钉估值表图片（推荐，不受页面切换影响） */
export function parseDDImageAsync(path, modelType = 'mimo') {
  return api.post('/valuation/parse-dd-async', { path, model_type: modelType })
}

/** 批量异步解析螺丝钉估值表图片 */
export function parseDDBatchAsync(paths, modelType = 'mimo') {
  return api.post('/valuation/parse-dd-batch-async', { paths, model_type: modelType }, { timeout: 30000 })
}

/** 查询螺丝钉图片解析任务状态 */
export function getDDParseTask(taskId) {
  return api.get(`/valuation/parse-dd-task/${taskId}`)
}

/** 轮询螺丝钉图片解析任务（返回取消函数） */
export function pollDDParseTask(taskId, onProgress, interval = 3000) {
  let stopped = false
  const check = async () => {
    if (stopped) return
    try {
      const { data } = await getDDParseTask(taskId)
      onProgress(data)
      if (data.status === 'done' || data.status === 'error') return
    } catch (e) {
      console.error('DD parse poll error:', e)
    }
    if (!stopped) setTimeout(check, interval)
  }
  check()
  return () => { stopped = true }
}

/** 列出所有有估值数据的指数 */
export function listValuationIndexes() {
  return api.get('/valuation/indexes')
}

/** 查询某指数的估值历史 */
export function getValuationHistory(indexCode, days = 30, metricType = null) {
  const params = { days }
  if (metricType) params.metric_type = metricType
  return api.get(`/valuation/history/${indexCode}`, { params })
}

/** 获取估值数据新鲜度 */
export function getValuationFreshness() {
  return api.get('/valuation/freshness')
}

/** 超性价比指数识别 */
export function getSuperValue() {
  return api.get('/valuation/super-value')
}

/** 增强策略分析（LLM 判断机会 vs 陷阱） */
export function getEnhancedStrategy() {
  return api.get('/valuation/enhanced-strategy', { timeout: 150000 })
}

/** 刷新指数实时价格 */
export function refreshValuationPrices() {
  return api.post('/valuation/refresh-prices', {}, { timeout: 60000 })
}

/** 列出螺丝钉估值记录 */
export function listDDValuations() {
  return api.get('/valuation/dd/list')
}

/** 获取螺丝钉估值记录详情 */
export function getDDValuation(id) {
  return api.get(`/valuation/dd/${id}`)
}

/** 获取最新市场温度 */
export function getMarketTemperature() {
  return api.get('/valuation/market-temperature')
}

/** 获取螺丝钉指数列表 */
export function getDDIndexes(ddId = null) {
  const params = ddId ? { dd_id: ddId } : {}
  return api.get('/valuation/dd/indexes', { params })
}

/** 统一估值查询（智能降级） */
export function getUnifiedValuation(indexCode = null, metricType = '市盈率', source = 'all', maxDays = 7) {
  const params = { metric_type: metricType, source, max_days: maxDays }
  if (indexCode) params.index_code = indexCode
  return api.get('/valuation/unified', { params })
}

/** AI 债券配置推荐 */
export function getBondRecommend() {
  return api.post('/bond/ai-recommend')
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

// ── 文章管理 API（新路径: /api/articles/*）─────────────────────────────────────

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

// ── Agent API（/api/agents/*）─────────────────────────────────────

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

/** AI 生成/优化 Agent 提示词 */
export function generateAgentPrompt(data) {
  return api.post('/agents/generate-prompt', data, { timeout: 120000 })
}

/** 获取 Agent 提示词版本历史 */
export function listAgentVersions(agentId) {
  return api.get(`/agents/${agentId}/versions`)
}

/** 回滚 Agent 提示词到指定版本 */
export function rollbackAgentPrompt(agentId, versionId) {
  return api.post(`/agents/${agentId}/rollback/${versionId}`)
}

/** 获取分析 Agent 提示词版本历史 */
export function listAnalysisAgentVersions(agentId) {
  return api.get(`/agent/${agentId}/versions`, { params: { agent_type: 'analysis' } })
}

/** 回滚分析 Agent 提示词到指定版本 */
export function rollbackAnalysisAgentPrompt(agentId, versionId) {
  return api.post(`/agent/${agentId}/rollback/${versionId}`, {}, { params: { agent_type: 'analysis' } })
}

/** 获取 Agent 回归测试结果 */
export function getAgentRegressionResult(agentId, agentType = 'conversation') {
  return api.get(`/agent/${agentId}/regression`, { params: { agent_type: agentType } })
}

// ── 对话 API（/api/conversations/*）─────────────────────────────────────

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
export function sendMessageStream(convId, content, onEvent, targetSpecialists = []) {
  const controller = new AbortController()
  const baseURL = api.defaults.baseURL || ''

  fetch(`${baseURL}/conversations/${convId}/messages/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content, target_specialists: targetSpecialists }),
    signal: controller.signal,
  }).then(async response => {
    // 检查 HTTP 错误状态（如 409 重复请求）
    if (!response.ok) {
      const body = await response.text().catch(() => '')
      let msg = '请求失败'
      try {
        const err = JSON.parse(body)
        msg = err.detail || msg
      } catch {}
      onEvent({ type: 'error', data: { message: msg, code: 'HTTP_ERROR', status: response.status } })
      return
    }

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
    // 忽略用户取消的错误
    if (err.name === 'AbortError') return

    // 网络错误（如息屏断开）不发送 error 事件，让前端通过 visibilitychange 恢复
    console.warn('SSE 连接断开:', err.message)
    // 不调用 onEvent，避免显示错误
  })

  return controller
}

/** 取消对话执行（切页面时通知后端更新 streaming 状态） */
export function cancelConversationExecution(convId) {
  return api.post(`/conversation/${convId}/cancel`)
}

export function getConversationExecutionState(convId) {
  return api.get(`/conversations/${convId}/execution-state`)
}

export function continueConversation(convId) {
  return api.post(`/conversations/${convId}/continue`)
}

export function retryConversationMessage(convId, messageId) {
  return api.post(`/conversations/${convId}/retry-message/${messageId}`)
}

/** 恢复中断的对话执行（跳过已完成的专家） */
export function resumeConversationStream(convId, onEvent) {
  const controller = new AbortController()
  const baseURL = api.defaults.baseURL || ''

  fetch(`${baseURL}/conversations/${convId}/resume`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    signal: controller.signal,
  }).then(async response => {
    if (!response.ok) {
      // HTTP 错误（如 400 无辅助回复）→ 转成 error 事件
      const body = await response.text().catch(() => '')
      let msg = '恢复执行失败'
      try {
        const err = JSON.parse(body)
        msg = err.detail || msg
      } catch {}
      onEvent({ type: 'error', data: { message: msg, code: 'RESUME_FAILED', status: response.status } })
      return
    }
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      const lines = buffer.split('\n')
      buffer = lines.pop()

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try {
            const event = JSON.parse(line.slice(6))
            onEvent(event)
          } catch (e) {}
        }
      }
    }
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

// ── Trace 执行链路 API ──────────────────────────────────────

/** 获取对话的执行链路列表 */
export function listTraces(convId, limit = 20) {
  return api.get(`/conversations/${convId}/traces`, { params: { limit } })
}

/** 获取单条执行链路详情 */
export function getTraceDetail(convId, traceId) {
  return api.get(`/conversations/${convId}/trace/${traceId}`)
}

// ── RAG 管理 API（新路径: /api/rag/*）─────────────────────────────────────

/** 重建 RAG 索引 */
export function reindexRag(limit = 1000) {
  return api.post('/rag/reindex', {}, { params: { limit }, timeout: 600000 })
}

/** 重建文章索引 */
export function reindexArticles(limit = 1000) {
  return api.post('/rag/reindex/articles', {}, { params: { limit }, timeout: 300000 })
}

// ── 知识库 API ──────────────────────────────────────

/** 获取知识库统计信息 */
export function getKnowledgeStats() {
  return api.get('/knowledge/stats')
}

/** 列出已蒸馏的书籍 */
export function getKnowledgeBooks() {
  return api.get('/knowledge/books')
}

/** 列出知识条目 */
export function listKnowledge(category = null, subcategory = null, source = null, limit = 100) {
  const params = { limit }
  if (category) params.category = category
  if (subcategory) params.subcategory = subcategory
  if (source) params.source = source
  return api.get('/knowledge/list', { params })
}

/** 搜索知识库 */
export function searchKnowledge(query, category = null, limit = 20) {
  const params = { q: query, limit }
  if (category) params.category = category
  return api.get('/knowledge/search', { params })
}

/** 删除知识条目 */
export function deleteKnowledge(id) {
  return api.delete(`/knowledge/${id}`)
}

/** 重建分析记录索引 */
export function reindexAnalysisRecords(limit = 1000) {
  return api.post('/rag/reindex/analysis', {}, { params: { limit }, timeout: 300000 })
}

/** 获取 RAG 索引统计 */
export function getRagIndexStats() {
  return api.get('/rag/stats')
}

/** 测试 RAG 搜索 */
export function testRagSearch(query, limit = 5, contentTypes = null, useRewrite = false) {
  return api.post('/rag/test-search', { query, limit, content_types: contentTypes, use_rewrite: useRewrite })
}

/** 提交 RAG 检索结果反馈 */
export function submitRagFeedback({ knowledgeId, contentType, query, rating, reasons = [] }) {
  return api.post('/rag/feedback', { knowledge_id: knowledgeId, content_type: contentType, query, rating, reasons })
}

/** 测试 Query Rewrite */
export function testQueryRewrite(query) {
  return api.get('/rag/rewrite', { params: { query } })
}

/** 获取 RAG 检索统计 */
export function getRagStats(days = 7) {
  return api.get('/rag-stats', { params: { days } })
}

/** 获取 RAG 检索日志 */
export function getRagLogs(limit = 100) {
  return api.get('/rag-logs', { params: { limit } })
}

/** 运行单次 RAG 质量评估 */
export function runRagEval(query, expectedTopics = []) {
  return api.post('/rag/eval/run', { query, expected_topics: expectedTopics }, { timeout: 60000 })
}

/** 运行完整 RAG 评估套件 */
export function runRagEvalSuite() {
  return api.post('/rag/eval/suite', {}, { timeout: 600000 })
}

/** 获取 RAG 评估结果 */
export function getRagEvalResults() {
  return api.get('/rag/eval/results')
}

// ── 图片浏览 API ──────────────────────────────────────

/** 列出所有分析记录，支持搜索 */
export function listGalleryRecords(search = '', limit = 200) {
  const params = { limit }
  if (search) params.search = search
  return api.get('/gallery', { params })
}

/** 上传螺丝钉估值图片 */
export function uploadDdImage(file) {
  const form = new FormData()
  form.append('file', file)
  return api.post('/dd-images/upload', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

/** 列出螺丝钉估值图片 */
export function listDdImages(date = null) {
  const params = {}
  if (date) params.date = date
  return api.get('/dd-images', { params })
}

/** 列出螺丝钉图片日期 */
export function listDdImageDates() {
  return api.get('/dd-images/dates')
}

/** 删除螺丝钉估值图片 */
export function deleteDdImage(path) {
  return api.delete(`/dd-images/${path}`)
}

/** 上传估值图片（用户上传的估值截图） */
export function uploadValuationImage(file) {
  const form = new FormData()
  form.append('file', file)
  return api.post('/valuation-images/upload', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

/** 列出估值图片 */
export function listValuationImages(date = null) {
  const params = {}
  if (date) params.date = date
  return api.get('/valuation-images', { params })
}

/** 列出估值图片日期 */
export function listValuationImageDates() {
  return api.get('/valuation-images/dates')
}

/** 删除估值图片 */
export function deleteValuationImage(path) {
  return api.delete(`/valuation-images/${path}`)
}

// ── 作者文章 API（新路径: /api/author-articles/*）─────────────────────────────────────

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

// ── 个人文档 API（新路径: /api/linked-articles/*）─────────────────────────────────────

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

/** 删除文档 */
export function deleteLinkedArticle(id) {
  return api.delete(`/linked-articles/${id}`)
}

// ── AI 市场分析 API ──────────────────────────────────────

/** 触发 AI 分析 */
export function runAnalysis(indexCode, indexName, agentId = 9) {
  return api.post('/analysis/run', { index_code: indexCode, index_name: indexName, agent_id: agentId })
}

/** 查询指数 AI 分析执行状态 */
export function getIndexAnalysisStatus(historyId) {
  return api.get(`/analysis/history/${historyId}/status`)
}

/** 轮询指数 AI 分析直到完成 */
export function pollIndexAnalysisStatus(historyId, onProgress, interval = 3000) {
  let cancelled = false
  const poll = async () => {
    while (!cancelled) {
      try {
        const { data } = await getIndexAnalysisStatus(historyId)
        onProgress(data)
        if (data.status === 'done' || data.status === 'error') return data
      } catch (e) {
        onProgress({ status: 'error', error: e.message })
        return null
      }
      await new Promise(r => setTimeout(r, interval))
    }
  }
  poll()
  return () => { cancelled = true }
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

// ── 每日看板 API ──────────────────────────────────────

/** 获取 Dashboard 聚合数据 */
export function getDashboard() {
  return api.get('/dashboard')
}

/** 获取今日市场热点（YingMi MCP） */
export function getHotTopics() {
  return api.get('/dashboard/hot-topics')
}

/** 热点→指数关联 */
export function getHotspotsRelate() {
  return api.post('/dashboard/hotspots-relate')
}

/** 获取今日自动生成的日报 */
export function getDailyReport() {
  return api.get('/dashboard/daily-report')
}

/** 查询最近一次每日简报异步任务状态 */
export function getDailyReportTask() {
  return api.get('/dashboard/daily-report/task')
}

/** 手动抓取近期估值数据 */
export function fetchRecentValuations() {
  return api.post('/valuations/fetch-recent', {}, { timeout: 60000 })
}

/** 重新生成今日市场简报 */
export function regenerateDailyReport() {
  return api.post('/dashboard/daily-report/regenerate')
}

/** 提交每日简报反馈 */
export function submitDailyReportFeedback({ rating, comment = '', reportSummary = '' }) {
  return api.post('/llm-feedback', {
    caller: 'daily_report',
    input_summary: '每日市场简报',
    output_summary: reportSummary,
    rating,
    tags: '',
    comment
  })
}

/** 触发结构化热点分析（异步） */
export function triggerHotspotsAnalysis() {
  return api.post('/dashboard/hotspots-analysis')
}

/** 获取最近一次热点分析缓存（刷新页面后还原） */
export function getLatestHotspotsAnalysis() {
  return api.get('/dashboard/hotspots-analysis/latest')
}

// ── 市场热点情报 ─────────────────────────────────────────

/** 市场热点情报概览（获取缓存） */
export function getMarketIntelligenceOverview() {
  return api.get('/market-intelligence/overview')
}

/** 触发市场热点情报分析 */
export function triggerMarketIntelligence() {
  return api.post('/market-intelligence/overview/trigger')
}

/** 单个板块深度分析 */
export function getSectorDetail(sectorName) {
  return api.get(`/market-intelligence/sector-detail/${encodeURIComponent(sectorName)}`)
}

/** 获取推荐验证历史 */
export function getRecommendations(limit = 50, status = '') {
  const params = { limit }
  if (status) params.status = status
  return api.get('/dashboard/recommendations', { params })
}

/** 获取推荐验证统计 */
export function getRecommendationStats() {
  return api.get('/dashboard/recommendations/stats')
}

/** 提交推荐反馈（点赞/点踩） */
export function submitRecommendationFeedback(recId, { rating, tags = '', comment = '' }) {
  return api.post(`/dashboard/recommendations/${recId}/feedback`, { rating, tags, comment })
}

/** 获取推荐反馈统计 */
export function getRecommendationFeedbackStats() {
  return api.get('/dashboard/recommendations/feedback-stats')
}

/** 自动验证 pending 推荐 */
export function autoVerifyRecommendations() {
  return api.get('/dashboard/recommendations/auto-verify')
}

/** 提交 LLM 输出反馈（进化系统） */
export function submitLlmFeedback({ caller, input_summary = '', output_summary = '', rating, tags = '', comment = '', score_data_accuracy = null, score_logic = null, score_actionability = null, target_type = '', target_id = null }) {
  return api.post('/llm-feedback', { caller, input_summary, output_summary, rating, tags, comment, score_data_accuracy, score_logic, score_actionability, target_type, target_id })
}

// ── 理财决策中枢 API ──────────────────────────────────────

/** 列出理财决策档案（可按状态过滤） */
export function listDecisions(status = '', limit = 50) {
  return api.get('/decisions', { params: { status, limit } })
}

/** 获取决策复盘统计 */
export function getDecisionStats(userId = 'default') {
  return api.get('/decisions/stats', { params: { user_id: userId } })
}

/** 获取单条决策详情 */
export function getDecision(decisionId) {
  return api.get(`/decisions/${decisionId}`)
}

/** 获取今日行动 */
export function listTodayDecisions(limit = 20) {
  return api.get('/decisions/today', { params: { limit } })
}

/** 从 AI 对话回复创建决策草案 */
export function createDecisionFromChat(payload) {
  return api.post('/decisions/from-chat', payload)
}

/** 获取决策执行前检查结果 */
export function getDecisionPrecheck(decisionId) {
  return api.get(`/decisions/${decisionId}/precheck`)
}

/** 更新决策状态 */
export function updateDecisionStatus(decisionId, status, userNote = '') {
  return api.put(`/decisions/${decisionId}/status`, { status, user_note: userNote })
}

/** 完成决策行动项 */
export function completeDecisionAction(decisionId, actionId) {
  return api.post(`/decisions/${decisionId}/actions/${actionId}/complete`)
}

/** 从决策生成待确认交易草稿 */
export function createTransactionDraftFromDecision(decisionId, payload = {}) {
  return api.post(`/decisions/${decisionId}/transaction-draft`, payload)
}

/** 列出 AI 建议候选 */
export function listRecommendationCandidates(status = 'new', limit = 20) {
  return api.get('/recommendation-candidates', { params: { status, limit } })
}

/** 忽略 AI 建议候选 */
export function ignoreRecommendationCandidate(candidateId) {
  return api.post(`/recommendation-candidates/${candidateId}/ignore`)
}

/** 延期 AI 建议候选 */
export function deferRecommendationCandidate(candidateId, deferredUntil) {
  return api.post(`/recommendation-candidates/${candidateId}/defer`, { deferred_until: deferredUntil })
}

/** 过期 AI 建议候选 */
export function expireRecommendationCandidates(userId = 'default') {
  return api.post('/recommendation-candidates/expire', {}, { params: { user_id: userId } })
}

/** 从结构化工具输出创建 AI 建议候选 */
export function createRecommendationCandidateFromTool(payload) {
  return api.post('/recommendation-candidates/from-tool', payload)
}

/** 从 AI 建议候选创建决策草案 */
export function createDecisionFromCandidate(candidateId, payload = {}) {
  return api.post(`/recommendation-candidates/${candidateId}/create-decision`, payload)
}

export function listDueDecisionReviews(limit = 20) {
  return api.get('/decisions/reviews/due', { params: { limit } })
}

export function submitDecisionReview(decisionId, payload) {
  return api.post(`/decisions/${decisionId}/review`, payload)
}

// ── 今日热门机会 2.0 ──────────────────────────────────────

export function getTodayOpportunities(limit = 8) {
  return api.get('/opportunities/today', { params: { limit } })
}

export function scanDailyOpportunities(data = {}) {
  return api.post('/opportunities/daily-scan', data, { timeout: 120000 })
}

export function createDecisionFromOpportunity(opportunityId) {
  return api.post(`/opportunities/${opportunityId}/create-decision`)
}

export function watchOpportunity(opportunityId) {
  return api.post(`/opportunities/${opportunityId}/watch`)
}

export function markOpportunityBought(opportunityId, data) {
  return api.post(`/opportunities/${opportunityId}/mark-bought`, data)
}

/** 触发决策多模型评审 */
export function triggerPeerReview(decisionId, reviewerTypes = ['suitability', 'evidence', 'counter', 'overconfidence']) {
  return api.post(`/decisions/${decisionId}/peer-review`, { reviewer_types: reviewerTypes })
}

/** 获取决策评审列表 */
export function getPeerReviews(decisionId) {
  return api.get(`/decisions/${decisionId}/peer-reviews`)
}

/** 直接创建决策（不通过对话） */
export function createDecision(payload) {
  return api.post('/decisions/create', payload)
}

/** 从行动卡片创建决策 */
export function createDecisionFromAction(action) {
  return api.post('/decisions/create', {
    decision_type: action.action_type === 'buy' ? 'buy' :
                   action.action_type === 'sell' ? 'sell' :
                   action.action_type === 'reduce' ? 'reduce' : 'rebalance',
    target_name: action.target_name,
    target_code: action.target_code,
    summary: action.reason,
    source: action.source || 'analysis',
    priority: action.priority,
  })
}

/** 获取待执行决策的执行状态（自动检测持仓变化匹配） */
export function getExecutionStatus(userId = 'default') {
  return api.get('/decisions/execution-status', { params: { user_id: userId } })
}

/** 获取决策时间线 */
export function getDecisionTimeline(decisionId) {
  return api.get(`/decisions/${decisionId}/timeline`)
}

/** 获取质量评分概览 */
export function getQualitySummary(days = 30) {
  return api.get('/eval/quality-summary', { params: { days } })
}

/** 获取质量评分趋势 */
export function getQualityTrend(days = 30) {
  return api.get('/eval/quality-trend', { params: { days } })
}

/** 获取低分产出列表 */
export function getLowQualityItems(limit = 20) {
  return api.get('/eval/low-quality', { params: { limit } })
}

/** 按 Agent 类型分组的评测统计 */
export function getEvalStatsByAgent() {
  return api.get('/eval/stats-by-agent')
}

// ── 债市数据 API ──────────────────────────────────────

/** 获取债市温度数据 */
export function getBondMarketTemperature() {
  return api.get('/bond/market-temperature')
}

/** 获取国债收益率曲线 */
export function getBondYieldCurve(country = 'china') {
  return api.get('/bond/yield-curve', { params: { country } })
}

/** 获取债市综合概况 */
export function getBondMarketOverview() {
  return api.get('/bond/market-overview')
}

// ── 持仓管理 API ──────────────────────────────────────

/** 获取所有持仓 */
export function listPortfolios() {
  return api.get('/portfolio')
}

/** 清空所有持仓数据 */
export function clearAllPortfolio() {
  return api.post('/portfolio/clear')
}

/** 获取零钱余额 */
export function getCashBalance(userId = 'default') {
  return api.get('/portfolio/cash', { params: { user_id: userId } })
}

/** 调整零钱余额（正数存入，负数支出） */
export function adjustCashBalance(amount, mode = 'add', userId = 'default') {
  return api.post('/portfolio/cash', { amount, mode, user_id: userId })
}

/** 获取基金净值历史 + 买卖点标记（用于交易行为图表） */
export function getFundNavHistory(fundCode, days = 365) {
  return api.get(`/portfolio/fund-nav-history/${fundCode}`, { params: { days } })
}

/** 获取持仓汇总 */
export function getPortfolioSummary(params = {}) {
  return api.get('/portfolio/summary', { params })
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

/** 获取所有待确认交易 */
export function listPendingTransactions() {
  return api.get('/portfolio/pending-transactions')
}

/** 获取交易操作审计日志 */
export function getAuditLog(params = {}) {
  const query = new URLSearchParams()
  if (params.fund_code) query.set('fund_code', params.fund_code)
  if (params.tx_id) query.set('tx_id', params.tx_id)
  if (params.limit) query.set('limit', params.limit)
  return api.get(`/portfolio/audit-log?${query.toString()}`)
}

/** 新增交易记录 */
export function createPortfolioTransaction(data) {
  return api.post('/portfolio/transactions', data)
}

/** 确认交易（填入 T+1 实际净值） */
export function confirmTransaction(txId, data) {
  return api.post(`/portfolio/transactions/${txId}/confirm`, data)
}

/** 自动确认已到确认日的待确认交易 */
export function autoConfirmPortfolioTransactions() {
  return api.post('/portfolio/transactions/auto-confirm', {}, { timeout: 120000 })
}

/** 标记卖出交易已到账 */
export function settleTransaction(txId) {
  return api.post(`/portfolio/transactions/${txId}/settle`)
}

/** 撤销待确认（pending）交易 */
export function deletePortfolioTransaction(txId) {
  return api.delete(`/portfolio/transactions/${txId}`)
}

/** 刷新所有持仓净值 */
export function refreshAllPortfolioPrices() {
  return api.post('/portfolio/refresh', {}, { timeout: 120000 })
}

/** 刷新单个持仓净值 */
export function refreshPortfolioPrice(holdingId) {
  return api.post(`/portfolio/${holdingId}/refresh`, {}, { timeout: 30000 })
}

/** 获取持仓分散度分析 */
export function getPortfolioDiversification() {
  return api.get('/portfolio/analysis/diversification')
}

/** 获取单只基金收益表现分析 */
export function getHoldingPerformance(holdingId) {
  return api.get(`/portfolio/analysis/${holdingId}/performance`)
}

/** 获取交易行为汇总分析 */
export function getTransactionSummary() {
  return api.get('/portfolio/analysis/transactions-summary')
}

/** 获取智能调仓建议 */
export function getRebalancingSuggestion() {
  return api.post('/portfolio/rebalancing/trigger')
}

/** 获取目标配置 / 偏离度驾驶舱 */
export function getAllocationDashboard() {
  return api.get('/portfolio/allocation-dashboard')
}

/** 运行确定性组合压力测试 */
export function runPortfolioStressTest(scenario = 'market_drop_20', customShocks = null) {
  return api.post('/portfolio/stress-test', { scenario, custom_shocks: customShocks })
}

/** 获取持仓基金经理概览 + 变更检测 */
export function getPortfolioManagerOverview() {
  return api.get('/fund-manager/portfolio/overview')
}

/** 获取指定基金的经理信息 */
export function getFundManager(fundCode) {
  return api.get(`/fund-manager/${fundCode}`)
}

/** 导出持仓为 CSV */
export function exportPortfolioCsv() {
  return api.get('/portfolio/export-csv', { responseType: 'blob' })
}

/** 从 CSV 导入持仓 */
export function importPortfolioCsv(file) {
  const formData = new FormData()
  formData.append('file', file)
  return api.post('/portfolio/import-csv-file', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

/** 获取家庭财务统一仪表盘 */
export function getFinanceDashboard() {
  return api.get('/finance-dashboard')
}

/** 获取投资趋势 */
export function getFinanceTrend(months = 12) {
  return api.get('/finance-dashboard/trend', { params: { months } })
}

/** 获取策略沙盒预设 */
export function getBacktestPresets() {
  return api.get('/strategy-sandbox/presets')
}

/** 运行策略回测 */
export function runBacktest(params) {
  return api.post('/strategy-sandbox/backtest', params)
}

/** 保存回测结果 */
export function saveBacktest(payload) {
  return api.post('/strategy-sandbox/save', payload)
}

/** 获取历史回测列表 */
export function listBacktests(limit = 20) {
  return api.get('/strategy-sandbox/history', { params: { limit } })
}

/** 获取单条回测详情 */
export function getBacktestDetail(backtestId) {
  return api.get(`/strategy-sandbox/${backtestId}`)
}

/** 删除回测记录 */
export function deleteBacktest(backtestId) {
  return api.delete(`/strategy-sandbox/${backtestId}`)
}

/** 关联回测到决策 */
export function linkBacktestToDecision(backtestId, decisionId) {
  return api.post(`/strategy-sandbox/${backtestId}/link-decision`, { decision_id: decisionId })
}

/** 获取调仓配置和策略预设 */
export function getRebalanceConfig() {
  return api.get('/portfolio/rebalance/config')
}

/** 更新调仓配置 */
export function updateRebalanceConfig(config) {
  return api.post('/portfolio/rebalance/config', config)
}

/** 获取调仓配置变更历史 */
export function getRebalanceConfigHistory(limit = 20) {
  return api.get('/portfolio/rebalance/config/history', { params: { limit } })
}

/** 获取指定版本的配置详情 */
export function getRebalanceConfigDetail(configId) {
  return api.get(`/portfolio/rebalance/config/${configId}`)
}

/** 回滚到指定配置版本 */
export function rollbackRebalanceConfig(configId) {
  return api.post(`/portfolio/rebalance/config/${configId}/rollback`)
}

// ── 分散度 AI 解读 API ──────────────────────────────────────

/** 触发 AI 分散度分析解读 */
export function runDiversificationAiSummary() {
  return api.post('/portfolio/analysis/diversification/ai-summary')
}

/** 查询今天是否已有 AI 分散度分析 */
export function getAiSummaryTodayStatus() {
  return api.get('/portfolio/analysis/ai-summary/today-status')
}

/** 跨基金持仓穿透分析 */
export function getPortfolioPenetration() {
  return api.get('/portfolio/analysis/penetration')
}

// ── AI 持仓分析 API ──────────────────────────────────────

/** 触发 AI 持仓分析 */
export function runPortfolioAiAnalysis(question = '') {
  return api.post('/portfolio/analysis/ai', { question })
}

/** 列出 AI 持仓分析记录 */
export function listPortfolioAiAnalysisRecords(limit = 20) {
  return api.get('/portfolio/analysis/ai-records', { params: { limit } })
}

/** 获取单条 AI 持仓分析记录详情 */
export function getPortfolioAiAnalysisRecord(id) {
  return api.get(`/portfolio/analysis/ai-records/${id}`)
}

/** 删除 AI 持仓分析记录 */
export function deletePortfolioAiAnalysisRecord(id) {
  return api.delete(`/portfolio/analysis/ai-records/${id}`)
}

/** 提交分析反馈（helpful/unhelpful） */
export function submitAnalysisFeedback(recordId, feedback, note = '') {
  return api.post(`/portfolio/analysis/feedback/${recordId}`, { feedback, note })
}

/** 提交对话反馈（helpful/unhelpful） */
export function submitChatFeedback(messageId, feedback, note = '', inputSummary = '', outputSummary = '') {
  return api.post('/chat/feedback', {
    message_id: messageId,
    feedback,
    note,
    input_summary: inputSummary,
    output_summary: outputSummary,
  })
}

/** 获取 Bad Cases 列表（统一：分析记录 + LLM 反馈） */
export function listBadCases(source = '', limit = 100) {
  const params = { limit }
  if (source) params.source = source
  return api.get('/portfolio/analysis/bad-cases', { params })
}

// ── AI 持仓分析 4 模式 API ──────────────────────────────────

/** 模式1：全景诊断（异步，立即返回 record_id） */
export function runPanoramaAnalysis() {
  return api.post('/portfolio/analysis/panorama', {}, { timeout: 30000 })
}

/** 查询全景诊断执行状态 */
export function getPanoramaStatus(recordId) {
  return api.get(`/portfolio/analysis/panorama/${recordId}/status`)
}

/** 轮询全景诊断直到完成 */
export function pollPanoramaStatus(recordId, onProgress, interval = 3000) {
  let cancelled = false
  const poll = async () => {
    while (!cancelled) {
      try {
        const { data } = await getPanoramaStatus(recordId)
        onProgress(data)
        if (data.status === 'done' || data.status === 'error') return data
      } catch (e) {
        onProgress({ status: 'error', error: e.message })
        return null
      }
      await new Promise(r => setTimeout(r, interval))
    }
  }
  poll()
  return () => { cancelled = true }
}

/** 模式2：单基金深度分析（异步） */
export function runDeepDiveAnalysis(holdingId) {
  return api.post(`/portfolio/analysis/deep-dive/${holdingId}`, {}, { timeout: 30000 })
}

/** 模式3：交易复盘（异步） */
export function runTradeReview(startDate, endDate) {
  return api.post('/portfolio/analysis/trade-review', { start_date: startDate, end_date: endDate }, { timeout: 30000 })
}

/** 模式4：指定基金分析（异步） */
export function runFundAnalysis(fundCode) {
  return api.post('/portfolio/analysis/fund-analysis', { fund_code: fundCode }, { timeout: 30000 })
}

/** 费率拖累分析 */
export function runFeeAnalysis() {
  return api.post('/portfolio/analysis/fee', {}, { timeout: 30000 })
}

/** 持仓相关性分析 */
export function runCorrelationAnalysis(lookbackDays = 252) {
  return api.post('/portfolio/analysis/correlation', { lookback_days: lookbackDays }, { timeout: 30000 })
}

/** 费率分析历史 */
export function listFeeRecords(limit = 10) {
  return api.get('/portfolio/analysis/fee/records', { params: { limit } })
}

/** 相关性分析历史 */
export function listCorrelationRecords(limit = 10) {
  return api.get('/portfolio/analysis/correlation/records', { params: { limit } })
}

/** 查询任意分析执行状态 */
export function getAnalysisStatus(recordId) {
  return api.get(`/portfolio/analysis/${recordId}/status`)
}

/** 轮询任意分析直到完成 */
export function pollAnalysisStatus(recordId, onProgress, interval = 3000) {
  let cancelled = false
  const poll = async () => {
    while (!cancelled) {
      try {
        const { data } = await getAnalysisStatus(recordId)
        onProgress(data)
        if (data.status === 'done' || data.status === 'error') return data
      } catch (e) {
        onProgress({ status: 'error', error: e.message })
        return null
      }
      await new Promise(r => setTimeout(r, interval))
    }
  }
  poll()
  return () => { cancelled = true }
}

/** 列出全景诊断记录 */
export function listPanoramaRecords(limit = 10) {
  return api.get('/portfolio/analysis/panorama/records', { params: { limit } })
}

/** 列出深度分析记录 */
export function listDeepDiveRecords(limit = 10) {
  return api.get('/portfolio/analysis/deep-dive/records', { params: { limit } })
}

/** 列出交易复盘记录 */
export function listTradeReviewRecords(limit = 10) {
  return api.get('/portfolio/analysis/trade-review/records', { params: { limit } })
}

/** 列出指定基金分析记录 */
export function listFundAnalysisRecords(limit = 10) {
  return api.get('/portfolio/analysis/fund-analysis/records', { params: { limit } })
}

/** 列出债券推荐记录 */
export function listBondRecommendRecords(limit = 5) {
  return api.get('/bond/ai-recommend/records', { params: { limit } })
}

// ── 风险预警 API ──────────────────────────────────────

/** 获取预警列表 */
export function listAlerts(unreadOnly = false, limit = 50) {
  return api.get('/portfolio/alerts', { params: { unread_only: unreadOnly, limit } })
}

/** 获取未读预警数量 */
export function getUnreadAlertCount() {
  return api.get('/portfolio/alerts/unread-count')
}

/** 标记预警为已读 */
export function markAlertRead(alertId) {
  return api.put(`/portfolio/alerts/${alertId}/read`)
}

/** 删除预警 */
export function deleteAlert(alertId) {
  return api.delete(`/portfolio/alerts/${alertId}`)
}

/** 生成预警 */
export function generateAlert(data) {
  return api.post('/portfolio/alerts/generate', data)
}

/** 持仓风险巡检 */
export function scanPortfolioAlerts() {
  return api.post('/portfolio/alerts/scan')
}

// ── 交易标签 API ──────────────────────────────────────

/** 添加交易标签 */
export function addTransactionTag(txId, tag) {
  return api.post(`/portfolio/transactions/${txId}/tags`, { tag })
}

/** 移除交易标签 */
export function removeTransactionTag(txId, tag) {
  return api.delete(`/portfolio/transactions/${txId}/tags/${encodeURIComponent(tag)}`)
}

/** 获取交易标签 */
export function getTransactionTags(txId) {
  return api.get(`/portfolio/transactions/${txId}/tags`)
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

// ── Token 用量 API ──────────────────────────────────────

/** 获取 Token 用量统计（总量/按天/按模型） */
export function getTokenUsage(days = 7) {
  return api.get('/token-usage', { params: { days } })
}

/** 获取最近 LLM 调用记录（分页） */
export function getTokenUsageRecent(page = 1, pageSize = 20, days = 7) {
  return api.get('/token-usage/recent', { params: { page, page_size: pageSize, days } })
}

/** 获取 Token 用量汇总（今日/累计） */
export function getTokenUsageSummary(days = 30) {
  return api.get('/token-usage/summary', { params: { days } })
}

/** 获取今日 token 预算使用情况 */
export function getTokenUsageBudget() {
  return api.get('/token-usage/budget')
}

/** 按 caller 分组统计 */
export function getTokenUsageByCaller(days = 7) {
  return api.get('/token-usage/by-caller', { params: { days } })
}

/** 按天获取趋势 */
export function getTokenUsageDaily(days = 30) {
  return api.get('/token-usage/daily', { params: { days } })
}

/** 清空所有 token 用量数据 */
export function clearTokenUsage() {
  return api.post('/token-usage/clear')
}

/** 获取费用估算 */
export function getTokenUsageCost(days = 7) {
  return api.get('/token-usage/cost', { params: { days } })
}

/** 按模型分组统计 */
export function getTokenUsageByModel(days = 7) {
  return api.get('/token-usage/by-model', { params: { days } })
}

/** 按小时统计 */
export function getTokenUsageHourly(date = null) {
  return api.get('/token-usage/hourly', { params: { date } })
}

/** Trace 链路查询 */
export function getTokenUsageTrace(traceId) {
  return api.get(`/token-usage/trace/${traceId}`)
}

// ── 性能监控 API ──────────────────────────────────────

/** 获取 Agent 调用性能统计 */
export function getPerformanceStats(days = 7) {
  return api.get('/performance/stats', { params: { days } })
}

/** 按 Agent 分组统计性能 */
export function getPerformanceByAgent(days = 7) {
  return api.get('/performance/by-agent', { params: { days } })
}

// ── 评测集 API (Eval Suite) ──────────────────────────

/** 列出评测用例 */
export function listEvalCases(analysisType = '', activeOnly = true) {
  const params = { active_only: activeOnly }
  if (analysisType) params.analysis_type = analysisType
  return api.get('/eval/cases', { params })
}

/** 创建评测用例 */
export function createEvalCase(data) {
  return api.post('/eval/cases', data)
}

/** 更新评测用例 */
export function updateEvalCase(caseId, data) {
  return api.put(`/eval/cases/${caseId}`, data)
}

/** 删除评测用例 */
export function deleteEvalCase(caseId) {
  return api.delete(`/eval/cases/${caseId}`)
}

/** 运行评测用例 */
export function runEvalCase(caseId) {
  return api.post(`/eval/cases/${caseId}/run`)
}

/** 列出评测运行记录 */
export function listEvalRuns(caseId = 0, limit = 50) {
  const params = { limit }
  if (caseId) params.case_id = caseId
  return api.get('/eval/runs', { params })
}

/** 获取单条运行记录详情 */
export function getEvalRunDetail(runId) {
  return api.get(`/eval/runs/${runId}`)
}

/** 获取评测统计概览 */
export function getEvalStats() {
  return api.get('/eval/stats')
}

/** 从 Bad Case 转化为 Eval Case */
export function createEvalFromBadCase(source, sourceId, name = '') {
  return api.post('/eval/cases/from-bad-case', { source, source_id: sourceId, name })
}

/** 批量将所有 Bad Case 转为 Eval Case */
export function batchConvertBadCases() {
  return api.post('/eval/cases/batch-from-bad-cases')
}

/** 从高价值对话提取 Eval Case */
export function extractEvalFromConversations(limit = 20) {
  return api.post(`/eval/cases/from-conversations?limit=${limit}`)
}

/** 运行自动回归测试 */
export function runAutoRegression(limit = 20) {
  return api.post(`/eval/auto-regression?limit=${limit}`)
}

// ── 对话质量评估 API ──────────────────────────────────────

/** 自动评估对话质量 */
export function evaluateConversation(conversationId) {
  return api.post(`/eval/conversation/${conversationId}`, {}, { timeout: 120000 })
}

/** 获取对话评估结果 */
export function getConversationEvaluation(conversationId, messageId = null) {
  const params = {}
  if (messageId) params.message_id = messageId
  return api.get(`/eval/conversation/${conversationId}`, { params })
}

/** 使用 LLM 进行智能评估（旧接口） */
export function evaluateConversationWithLLM(conversationId, messageId = null) {
  const params = {}
  if (messageId) params.message_id = messageId
  return api.post(`/eval/conversation/${conversationId}/llm`, {}, { params, timeout: 120000 })
}

/** 使用 LLM 评估 Agent 进行智能评估（新接口） */
export function evaluateWithLLMAgent(targetType, targetId, messageId = null) {
  const params = { target_type: targetType, target_id: targetId }
  if (messageId) params.message_id = messageId
  return api.post('/eval/llm', {}, { params, timeout: 120000 })
}

/** 获取 LLM 评估结果 */
export function getLLMEvaluation(targetType, targetId, messageId = null) {
  const params = {}
  if (messageId) params.message_id = messageId
  return api.get(`/eval/llm/${targetType}/${targetId}`, { params })
}

/** 获取 LLM 评估统计 */
export function getLLMEvalStats(days = 30) {
  return api.get('/eval/llm-stats', { params: { days } })
}

/** 获取用户偏好洞察 */
export function getUserInsights(userId = 'default') {
  return api.get(`/eval/user-insights/${userId}`)
}

/** 提交用户对对话的评分 */
export function submitConversationUserScore(conversationId, score, breakdown = {}, comment = '') {
  return api.post(`/eval/conversation/${conversationId}/user-score`, { score, breakdown, comment })
}

/** 获取对话评估统计 */
export function getConversationEvalStats() {
  return api.get('/eval/conversation-stats')
}

/** 列出对话评估记录 */
export function listConversationEvaluations(limit = 50, minScore = null) {
  const params = { limit }
  if (minScore !== null) params.min_score = minScore
  return api.get('/eval/conversation-list', { params })
}

// ── 进化系统 API ──────────────────────────────────────

/** 获取进化效果统计 */
export function getEvolutionStats(days = 30) {
  return api.get('/eval/evolution-stats', { params: { days } })
}

/** 获取评估建议（高分对话转化为 Eval 用例） */
export function listEvalSuggestions(status = null, limit = 50) {
  const params = { limit }
  if (status) params.status = status
  return api.get('/eval/suggestions', { params })
}

/** 接受评估建议 */
export function acceptEvalSuggestion(suggestionId) {
  return api.post(`/eval/suggestions/${suggestionId}/accept`)
}

/** 拒绝评估建议 */
export function rejectEvalSuggestion(suggestionId) {
  return api.post(`/eval/suggestions/${suggestionId}/reject`)
}

/** 获取专家表现告警 */
export function getExpertAlerts(days = 7, limit = 50) {
  return api.get('/eval/expert-alerts', { params: { days, limit } })
}

export function getFinanceQuoteBar() {
  return api.get('/finance/quote-bar')
}

export function getRunningAgents() {
  return api.get('/running-agents')
}

// ── 系统配置 API ──────────────────────────────────────

/** 获取所有系统配置 */
export function getSystemConfigs(category = null) {
  const params = {}
  if (category) params.category = category
  return api.get('/system-config', { params })
}

/** 获取单个配置 */
export function getSystemConfig(key) {
  return api.get(`/system-config/${key}`)
}

/** 更新单个配置 */
export function updateSystemConfig(key, value) {
  return api.put(`/system-config/${key}`, { value })
}

/** 重置所有配置为默认值 */
export function resetSystemConfigs() {
  return api.post('/system-config/reset')
}

// ── 关注列表 API ──────────────────────────────────────

/** 获取关注列表 */
export function listWatchlist(status = '', category = '') {
  const params = {}
  if (status) params.status = status
  if (category) params.category = category
  return api.get('/watchlist', { params })
}

/** 获取关注列表统计 */
export function getWatchlistSummary() {
  return api.get('/watchlist/summary')
}

/** 获取单条关注记录 */
export function getWatchlistItem(id) {
  return api.get(`/watchlist/${id}`)
}

/** 添加基金到关注列表 */
export function addToWatchlist(data) {
  return api.post('/watchlist', data)
}

/** 批量添加基金到关注列表 */
export function batchAddToWatchlist(items) {
  return api.post('/watchlist/batch', { items })
}

/** 更新关注记录 */
export function updateWatchlistItem(id, data) {
  return api.put(`/watchlist/${id}`, data)
}

/** 从关注列表移除 */
export function removeWatchlistItem(id) {
  return api.delete(`/watchlist/${id}`)
}

/** 批量刷新关注列表基金净值 */
export function refreshWatchlistNavs() {
  return api.post('/watchlist/refresh-navs', {}, { timeout: 60000 })
}

/** 标记为已买入 */
export function markWatchlistBought(id) {
  return api.post(`/watchlist/${id}/mark-bought`)
}

/** 查询基金信息并自动填充 */
export function lookupWatchlistFund(id) {
  return api.post(`/watchlist/${id}/lookup`, {}, { timeout: 30000 })
}

// ── 异步任务状态 API ──────────────────────────────────────

/** 查询异步任务状态 */
export function getAsyncTaskStatus(taskId) {
  return api.get(`/async-tasks/${taskId}/status`)
}

/** 列出异步任务 */
export function listAsyncTasks(taskType = '', status = '', limit = 50) {
  return api.get('/async-tasks', { params: { task_type: taskType, status, limit } })
}

// ── 用户画像 / KYC API（新路径: /api/profile/*）─────────────────────────────────────

/** 获取 KYC 问卷题库 + 当前画像 */
export function getKycQuestionnaire() {
  return api.get('/profile/kyc')
}

/** 提交 KYC 问卷答案 */
export function submitKyc(answers, source = 'questionnaire') {
  return api.post('/profile/kyc/submit', { answers, source })
}

/** 获取完整用户画像 */
export function getProfile() {
  return api.get('/profile')
}

/** 更新画像字段 */
export function updateProfile(data) {
  return api.put('/profile', data)
}

/** 获取目标账户 / 资金桶 */
export function listGoalBuckets() {
  return api.get('/profile/buckets')
}

/** 创建目标账户 / 资金桶 */
export function createGoalBucket(data) {
  return api.post('/profile/buckets', data)
}

/** 更新目标账户 / 资金桶 */
export function updateGoalBucket(id, data) {
  return api.put(`/profile/buckets/${id}`, data)
}

/** 删除目标账户 / 资金桶 */
export function deleteGoalBucket(id) {
  return api.delete(`/profile/buckets/${id}`)
}

/** 根据持仓自动同步资金桶 current_amount */
export function syncGoalBuckets() {
  return api.post('/profile/buckets/sync')
}

/** 全局搜索 */
export function globalSearch(q, limit = 5) {
  return api.get('/search/global', { params: { q, limit } })
}

/** 数据健康监控 */
export function getDataHealth() {
  return api.get('/data-health')
}

export default api

// ========== 综合理财健康分 ==========
export function calculateHealthScore() {
  return http.post('/api/health/calculate')
}
export function getTodayHealthScore() {
  return http.get('/api/health/today')
}
export function getHealthHistory(limit = 30) {
  return http.get(`/api/health/history?limit=${limit}`)
}
export function getStockBondRatio() {
  return http.get('/api/health/stock-bond-ratio')
}
export function getFearGreedIndex() {
  return http.get('/api/health/fear-greed')
}

// ========== 滚动收益分析 ==========
export function analyzeRollingReturn(data) {
  return http.post('/api/rolling/analyze', data)
}
export function analyzeRollingPortfolio(lookbackYears = 5) {
  return http.post('/api/rolling/portfolio', { lookback_years: lookbackYears })
}
export function analyzeRollingIndex(code, lookbackYears = 5) {
  return http.post('/api/rolling/index', { code, lookback_years: lookbackYears })
}
export function analyzeRollingFund(code, lookbackYears = 5) {
  return http.post('/api/rolling/fund', { code, lookback_years: lookbackYears })
}

// ========== 四笔钱归类 + 定投优化 ==========
export function classifyFourPots() {
  return http.get('/api/four-pots/classify')
}
export function getDcaOptimization() {
  return http.post('/api/four-pots/dca-optimization')
}

// ========== 情景推演 ==========
export function runWhatIf(scenario) {
  return http.post('/api/portfolio/analysis/what-if', { scenario })
}

// ========== 对比分析 AI 差异 ==========
export function runCompareDiff(recordA, recordB, type) {
  return http.post('/api/portfolio/analysis/compare-diff', { record_a: recordA, record_b: recordB, type })
}

// ── 每日持仓提示 API ──────────────────────────────────────

/** 每日持仓提示 */
export const dailyAdviceAPI = {
  /** 手动触发今日提示生成 */
  run: () => api.post('/daily-advice/run', { user_id: 'default', trigger_type: 'manual', force: true }),
  /** 获取今日提示概览 */
  getToday: () => api.get('/daily-advice/today', { params: { user_id: 'default' } }),
  /** 获取今日信号列表 */
  getSignals: () => api.get('/daily-advice/signals', { params: { user_id: 'default' } }),
  /** 标记信号为已读 */
  markRead: (signalId) => api.post(`/daily-advice/signals/${signalId}/read`),
  /** 忽略信号 */
  ignore: (signalId) => api.post(`/daily-advice/signals/${signalId}/ignore`),
  /** 从信号创建决策候选 */
  createCandidate: (signalId) => api.post(`/daily-advice/signals/${signalId}/create-candidate`),
  /** 问 AI 解读信号 */
  askAI: (signalId) => api.post(`/daily-advice/signals/${signalId}/ask-ai`, {}, { timeout: 120000 }),
}
