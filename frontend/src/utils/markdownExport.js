// 分析结果导出：直接使用原始内容，结构化产出转换为 Markdown。
const LABELS = {
  summary: '摘要', text: '分析正文', result: '分析结果', result_data: '分析结果',
  analysis: '分析', analysis_text: '完整分析', content: '正文',
  recommendations: '推荐', reason: '理由', direction: '方向', confidence: '置信度',
  fund_code: '基金代码', fund_name: '基金名称', fund_type: '基金类型',
  index_code: '指数代码', index_name: '指数名称', percentile: '估值分位',
  amount: '金额', amount_desc: '金额说明', risk_warning: '风险提示',
  market_assessment: '市场判断', policy_analysis: '政策分析', trend_analysis: '趋势分析',
  current_bond_analysis: '现有债券持仓分析', note: '说明',
  overall_summary: '整体判断', strategies: '策略', deep_analysis: '深度解读',
  opportunity_type: '机会类型', action: '建议操作', action_detail: '操作说明',
  catalysts: '潜在催化剂（待核验）', risk_factors: '风险因素', recovery_time: '预期恢复时间',
  sectors: '热门板块', name: '名称', outlook: '展望', keywords: '关键词',
  forecast_1w: '未来一周', forecast_2w: '未来两周', watchlist_takeaway: '关注提示',
  news: '新闻', cctv_news: '央视新闻', cctv_signal: '政策信号', macro: '宏观环境',
  title: '标题', url: '链接', source: '来源', date: '日期', created_at: '生成时间',
  fetched_at: '数据时间', agent_name: '分析 Agent', token_usage: 'Token 用量',
  actions: '行动建议', evidence: '分析依据', warnings: '提示',
}

export function toMarkdown(value, level = 2) {
  if (value == null) return ''
  if (typeof value === 'string') {
    if (!value.trim()) return ''
    const candidate = value.trim().replace(/^```(?:json)?\s*\n([\s\S]*?)\n```$/i, '$1')
    if (/^[{[]/.test(candidate)) {
      try { return toMarkdown(JSON.parse(candidate), level) } catch { /* 原始 Markdown 原样保留 */ }
    }
    return value
  }
  if (Array.isArray(value)) {
    return value.map((item, index) => {
      const body = toMarkdown(item, level + 1)
      if (!body) return ''
      return typeof item === 'object' && item !== null
        ? `${'#'.repeat(Math.min(level, 6))} ${index + 1}\n\n${body}`
        : `- ${body.replace(/\n/g, '\n  ')}`
    }).filter(Boolean).join('\n\n')
  }
  if (typeof value === 'object') {
    return Object.entries(value).map(([key, item]) => {
      const body = toMarkdown(item, level + 1)
      if (!body) return ''
      return `${'#'.repeat(Math.min(level, 6))} ${LABELS[key] || key}\n\n${body}`
    }).filter(Boolean).join('\n\n')
  }
  return String(value)
}

export function markdownFilename(title = '分析报告', createdAt = '', recordId = null) {
  const safeTitle = String(title).replace(/\.md$/i, '').replace(/[\\/:*?"<>|\u0000-\u001f]/g, '-').trim().replace(/[. ]+$/g, '').slice(0, 120) || '分析报告'
  const date = String(createdAt).match(/^\d{4}-\d{2}-\d{2}/)?.[0]
  const id = recordId == null ? '' : String(recordId).replace(/[^\w-]/g, '').slice(0, 32)
  return [safeTitle, date, id].filter(Boolean).join('-') + '.md'
}

export function downloadMarkdown(content, { title = '分析报告', createdAt = '', recordId = null } = {}) {
  const body = toMarkdown(content)
  if (!body) return false
  const metadata = [createdAt && `生成时间：${createdAt}`, recordId != null && `记录 ID：${recordId}`].filter(Boolean)
  const report = `# ${String(title).replace(/[\r\n]+/g, ' ')}\n\n${metadata.length ? metadata.join('  \n') + '\n\n' : ''}${body}`
  const blob = new Blob([report], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  try {
    anchor.href = url
    anchor.download = markdownFilename(title, createdAt, recordId)
    document.body.appendChild(anchor)
    anchor.click()
  } finally {
    anchor.remove()
    // 给浏览器读取下载链接留出时间，避免立即释放导致移动端下载失败。
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  return true
}
