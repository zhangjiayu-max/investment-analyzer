// ECharts 共享主题配置 composable
// 统一配色、字体、网格、tooltip 风格，适配暗色/亮色模式
import { computed } from 'vue'
import { isDark } from './useTheme'

export function useChartTheme() {
  const theme = computed(() => {
    const dark = isDark.value
    return {
      // 基础色彩
      backgroundColor: 'transparent',
      textColor: dark ? '#9aa0a6' : '#64748b',
      titleColor: dark ? '#e8eaed' : '#0f172a',
      gridColor: dark ? 'rgba(255,255,255,0.06)' : '#f1f5f9',
      borderColor: dark ? 'rgba(255,255,255,0.1)' : '#e2e8f0',

      // 金融配色（涨红跌绿，中国市场惯例）
      colors: {
        profit: '#dc2626',     // 红色 = 盈利/上涨
        loss: '#059669',       // 绿色 = 亏损/下跌
        primary: '#c9a84c',    // 暖金色（主色调）
        accent: '#0ea5e9',     // 天蓝色
        warning: '#f59e0b',    // 琥珀色
        danger: '#ef4444',     // 红色
        series: [              // 多系列配色
          '#c9a84c', '#10b981', '#f59e0b', '#ef4444',
          '#8b5cf6', '#06b6d4', '#f97316', '#ec4899',
        ],
      },

      // 百分位色带（估值用）
      percentileBands: [
        { min: 0, max: 20, color: 'rgba(16,185,129,0.15)', label: '极度低估' },
        { min: 20, max: 40, color: 'rgba(16,185,129,0.08)', label: '低估' },
        { min: 40, max: 60, color: 'rgba(156,163,175,0.05)', label: '合理' },
        { min: 60, max: 80, color: 'rgba(245,158,11,0.08)', label: '偏高' },
        { min: 80, max: 100, color: 'rgba(239,68,68,0.12)', label: '高估' },
      ],
    }
  })

  /** 通用 tooltip 配置 */
  function getTooltipOpts(extra = {}) {
    const t = theme.value
    return {
      backgroundColor: isDark.value ? 'rgba(13,18,32,0.95)' : '#ffffff',
      borderColor: t.borderColor,
      borderWidth: 1,
      textStyle: { color: t.titleColor, fontSize: 12 },
      ...extra,
    }
  }

  /** 通用 grid 配置 */
  function getGridOpts(opts = {}) {
    return {
      left: opts.left || '10%',
      right: opts.right || '10%',
      top: opts.top || '8%',
      bottom: opts.bottom || '18%',
      containLabel: true,
    }
  }

  /** 通用 xAxis category 配置 */
  function getCategoryAxis(data, opts = {}) {
    const t = theme.value
    return {
      type: 'category',
      data,
      axisLabel: { color: t.textColor, fontSize: 10, rotate: data.length > 15 ? 30 : 0, ...opts.labelOpts },
      axisLine: { lineStyle: { color: t.gridColor } },
      axisTick: { show: false },
      ...opts,
    }
  }

  /** 通用 yAxis value 配置 */
  function getValueAxis(name, opts = {}) {
    const t = theme.value
    return {
      type: 'value',
      name,
      nameTextStyle: { color: t.textColor, fontSize: 10 },
      splitLine: { lineStyle: { color: t.gridColor, type: 'dashed' } },
      axisLabel: { color: t.textColor, fontSize: 10 },
      axisLine: { show: false },
      axisTick: { show: false },
      ...opts,
    }
  }

  /** dataZoom 配置 */
  function getDataZoomOpts(dataLen, threshold = 60) {
    const t = theme.value
    const start = dataLen > threshold ? Math.max(0, (1 - threshold / dataLen) * 100) : 0
    return [
      { type: 'inside', start, end: 100 },
      {
        type: 'slider', start, end: 100, height: 20, bottom: 4,
        borderColor: 'transparent',
        backgroundColor: isDark.value ? 'rgba(255,255,255,0.04)' : '#f8fafc',
        fillerColor: 'rgba(201,168,76,0.12)',
        handleStyle: { color: t.colors.primary, borderColor: t.colors.primary },
        textStyle: { color: t.textColor, fontSize: 10 },
      },
    ]
  }

  return { theme, isDark, getTooltipOpts, getGridOpts, getCategoryAxis, getValueAxis, getDataZoomOpts }
}
