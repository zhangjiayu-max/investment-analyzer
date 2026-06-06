import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 120000,
})

// ── 任务 API（新路径: /api/task/*）─────────────────────────────────────

/** 创建任务（提交链接） */
export function createTask(url) {
  return api.post('/task/create', { url })
}

/** 任务列表 */
export function listTasks(limit = 50) {
  return api.get('/task/list', { params: { limit } })
}

/** 任务详情 */
export function getTask(taskId) {
  return api.get(`/task/${taskId}`)
}

/** 删除任务 */
export function deleteTask(taskId) {
  return api.delete(`/task/${taskId}`)
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
  return api.post('/bond/ai-recommend', {}, { timeout: 120000 })
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

// ── Agent API（新路径: /api/agent/*）─────────────────────────────────────

/** 列出所有 Agent */
export function listAgents() {
  return api.get('/agent/list')
}

/** 创建自定义 Agent */
export function createAgent(data) {
  return api.post('/agent/create', data)
}

/** 获取单个 Agent 详情 */
export function getAgent(id) {
  return api.get(`/agent/${id}`)
}

/** 更新 Agent */
export function updateAgent(id, data) {
  return api.put(`/agent/${id}`, data)
}

/** 删除自定义 Agent */
export function deleteAgent(id) {
  return api.delete(`/agent/${id}`)
}

/** AI 生成/优化 Agent 提示词 */
export function generateAgentPrompt(data) {
  return api.post('/agent/generate-prompt', data, { timeout: 120000 })
}

/** 获取 Agent 提示词版本历史 */
export function listAgentVersions(agentId) {
  return api.get(`/agent/${agentId}/versions`)
}

/** 回滚 Agent 提示词到指定版本 */
export function rollbackAgentPrompt(agentId, versionId) {
  return api.post(`/agent/${agentId}/rollback/${versionId}`)
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

// ── 对话 API（新路径: /api/conversation/*）─────────────────────────────────────

/** 对话列表 */
export function listConversations() {
  return api.get('/conversation/list')
}

/** 创建对话 */
export function createConversation(data) {
  return api.post('/conversation/create', data)
}

/** 删除对话 */
export function deleteConversation(id) {
  return api.delete(`/conversation/${id}`)
}

/** 获取对话消息历史 */
export function getMessages(convId, limit = 50) {
  return api.get(`/conversation/${convId}/messages`, { params: { limit } })
}

/** 发送消息 */
export function sendMessage(convId, content) {
  return api.post(`/conversation/${convId}/messages`, { content }, { timeout: 120000 })
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

/** 列出知识条目 */
export function listKnowledge(category = null, subcategory = null, limit = 100) {
  const params = { limit }
  if (category) params.category = category
  if (subcategory) params.subcategory = subcategory
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

/** 手动抓取近期估值数据 */
export function fetchRecentValuations() {
  return api.post('/valuations/fetch-recent', {}, { timeout: 60000 })
}

/** 重新生成今日市场简报 */
export function regenerateDailyReport() {
  return api.post('/dashboard/daily-report/regenerate', {}, { timeout: 120000 })
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

/** 获取结构化热点分析（AI 推荐卡片） */
export function getHotspotsAnalysis() {
  return api.get('/dashboard/hotspots-analysis', { timeout: 90000 })
}

/** 获取最近一次热点分析缓存（刷新页面后还原） */
export function getLatestHotspotsAnalysis() {
  return api.get('/dashboard/hotspots-analysis/latest')
}

// ── 市场热点情报 ─────────────────────────────────────────

/** 市场热点情报概览（force=true 强制重新分析） */
export function getMarketIntelligenceOverview(force = false) {
  return api.get('/market-intelligence/overview', { params: { force }, timeout: 120000 })
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
  return api.get(`/portfolio/holding/${id}`)
}

/** 更新持仓 */
export function updatePortfolio(id, data) {
  return api.put(`/portfolio/holding/${id}`, data)
}

/** 删除持仓 */
export function deletePortfolio(id) {
  return api.delete(`/portfolio/holding/${id}`)
}

/** 获取持仓交易记录 */
export function listPortfolioTransactions(holdingId, limit = 100) {
  return api.get(`/portfolio/${holdingId}/transactions`, { params: { limit } })
}

/** 获取所有待确认交易 */
export function listPendingTransactions() {
  return api.get('/portfolio/pending-transactions')
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
  return api.get('/portfolio/rebalancing')
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
  return api.post('/portfolio/analysis/diversification/ai-summary', {}, { timeout: 120000 })
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
  return api.post('/portfolio/analysis/ai', { question }, { timeout: 300000 })
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

/** 模式1：全景诊断 */
export function runPanoramaAnalysis() {
  return api.post('/portfolio/analysis/panorama', {}, { timeout: 300000 })
}

/** 模式2：单基金深度分析 */
export function runDeepDiveAnalysis(holdingId) {
  return api.post(`/portfolio/analysis/deep-dive/${holdingId}`, {}, { timeout: 300000 })
}

/** 模式3：交易复盘 */
export function runTradeReview(startDate, endDate) {
  return api.post('/portfolio/analysis/trade-review', { start_date: startDate, end_date: endDate }, { timeout: 300000 })
}

/** 模式4：情景推演 */
export function runWhatIfAnalysis(scenario, parameter) {
  return api.post('/portfolio/analysis/what-if', { scenario, parameter }, { timeout: 300000 })
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

/** 列出情景推演记录 */
export function listWhatIfRecords(limit = 10) {
  return api.get('/portfolio/analysis/what-if/records', { params: { limit } })
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
  return api.post(`/eval/cases/${caseId}/run`, {}, { timeout: 600000 })
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

export default api
