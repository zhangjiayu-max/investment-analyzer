/** 页面展示场景与后端情景协议的映射。利率单位为基点。 */
export const whatIfScenarios = [
  { key: 'market_drop', icon: '📉', label: '大盘跌20%' },
  { key: 'rate_hike', icon: '📈', label: '利率上升2%' },
  { key: 'sector_rotation', icon: '🔄', label: '行业轮动' },
  { key: 'custom', icon: '✏️', label: '自定义' },
]

export function buildWhatIfRequest(key, customText = '') {
  if (key === 'market_drop') return { scenario: 'market_drop', parameter: 20 }
  if (key === 'rate_hike') return { scenario: 'rate_hike', parameter: 200 }
  if (key === 'sector_rotation') return { scenario: 'custom', custom_scenario: '行业轮动：分析当前持仓在行业热点切换、风格轮动时的风险和应对策略。' }
  if (key === 'custom' && customText.trim()) return { scenario: 'custom', custom_scenario: customText.trim() }
  throw new Error('请选择有效情景并填写自定义情景描述')
}
