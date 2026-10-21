/** 兼容历史 failed+degraded；只派生展示状态，保留用户正文和原 metadata。 */
export function chatDeliveryStatus(msg = {}) {
  let metadata = msg.metadata || {}
  if (typeof metadata === 'string') {
    try { metadata = JSON.parse(metadata) } catch { metadata = {} }
  }
  const data = { ...metadata, ...msg }
  let status = data.execution_status
  const content = String(data.content || '').trim()
  const hasContent = content && !/^(?:⏳|❌|Connection error|执行失败|AI 回复失败|（执行失败|专家预算已用尽，未执行追加分析|专家执行超时，本轮未取得完整分析)/i.test(content)
  if (!['cancelled', 'timeout'].includes(status) && hasContent &&
      (data.recovery_type === 'experts_only' || data.completion_status === 'degraded' || status === 'degraded')) {
    status = 'degraded'
  }
  const reasons = [...(data.delivery_reasons || [])]
  const labels = {
    cross_review_budget_exhausted: '交叉审查因预算不足未完成',
    reflection_budget_exhausted: '反思审查因预算不足未完成',
    cross_review_failed: '交叉审查未完成',
  }
  for (const issue of data.quality_issues || []) {
    if (labels[issue]) reasons.push(labels[issue])
  }
  for (const specialist of data.specialist_results || []) {
    if (specialist.completion_status === 'truncated' || specialist.finish_reason === 'length') reasons.push('专家输出截断')
  }
  for (const [key, label] of [['reflection', '反思审查'], ['cross_review_status', '交叉审查']]) {
    const review = data[key]
    const reviewStatus = typeof review === 'object' ? review?.status : review
    if (reviewStatus === 'budget_exhausted') reasons.push(`${label}因预算不足未完成`)
    else if (['failed', 'timeout'].includes(reviewStatus)) reasons.push(`${label}未完成`)
  }
  return {
    execution_status: status,
    completion_status: status === 'degraded' ? 'degraded' : data.completion_status,
    quality_status: data.quality_status,
    recovery_type: data.recovery_type,
    delivery_reasons: [...new Set(reasons)],
    delivery_message: data.recovery_type === 'experts_only'
      ? '仅专家结果，综合报告未生成；专家观点尚未完成综合核验。'
      : data.delivery_message || '分析已生成，部分核验未完成。',
  }
}
