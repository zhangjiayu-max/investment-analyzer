// Composables 单元测试

import { describe, it, expect } from 'vitest'

describe('useMarkdown', () => {
  it('空文本返回空字符串', async () => {
    const { renderMarkdown } = await import('../composables/useMarkdown.js')
    expect(renderMarkdown('')).toBe('')
    expect(renderMarkdown(null)).toBe('')
    expect(renderMarkdown(undefined)).toBe('')
  })

  it('粗体文本正确转换', async () => {
    const { renderMarkdown } = await import('../composables/useMarkdown.js')
    const result = renderMarkdown('**加粗**')
    expect(result).toContain('<strong>加粗</strong>')
  })

  it('列表正确转换', async () => {
    const { renderMarkdown } = await import('../composables/useMarkdown.js')
    const result = renderMarkdown('- 项目1\n- 项目2')
    expect(result).toContain('<li>')
    expect(result).toContain('项目1')
  })

  it('链接正确转换', async () => {
    const { renderMarkdown } = await import('../composables/useMarkdown.js')
    const result = renderMarkdown('[百度](https://baidu.com)')
    expect(result).toContain('href="https://baidu.com"')
    expect(result).toContain('百度')
  })

  it('表格正确转换', async () => {
    const { renderMarkdown } = await import('../composables/useMarkdown.js')
    const result = renderMarkdown('| A | B |\n|---|---|\n| 1 | 2 |')
    expect(result).toContain('<table>')
    expect(result).toContain('<td>')
  })
})

describe('useChartTheme', () => {
  it('theme 返回完整配置对象', async () => {
    // Mock useTheme
    vi.mock('../composables/useTheme.js', () => ({
      isDark: { value: false },
    }))

    const { useChartTheme } = await import('../composables/useChartTheme.js')
    const { theme } = useChartTheme()

    expect(theme.value).toHaveProperty('backgroundColor')
    expect(theme.value).toHaveProperty('textColor')
    expect(theme.value).toHaveProperty('colors')
    expect(theme.value.colors).toHaveProperty('series')
    expect(theme.value.colors.series.length).toBeGreaterThan(0)
  })

  it('getTooltipOpts 返回 tooltip 配置', async () => {
    vi.mock('../composables/useTheme.js', () => ({
      isDark: { value: false },
    }))

    const { useChartTheme } = await import('../composables/useChartTheme.js')
    const { getTooltipOpts } = useChartTheme()
    const opts = getTooltipOpts()

    expect(opts).toHaveProperty('backgroundColor')
    expect(opts).toHaveProperty('borderColor')
    expect(opts).toHaveProperty('textStyle')
  })

  it('getGridOpts 返回 grid 配置', async () => {
    vi.mock('../composables/useTheme.js', () => ({
      isDark: { value: false },
    }))

    const { useChartTheme } = await import('../composables/useChartTheme.js')
    const { getGridOpts } = useChartTheme()
    const opts = getGridOpts()

    expect(opts).toHaveProperty('left')
    expect(opts).toHaveProperty('right')
    expect(opts).toHaveProperty('top')
    expect(opts).toHaveProperty('bottom')
    expect(opts).toHaveProperty('containLabel')
  })

  it('getDataZoomOpts 根据数据量返回配置', async () => {
    vi.mock('../composables/useTheme.js', () => ({
      isDark: { value: false },
    }))

    const { useChartTheme } = await import('../composables/useChartTheme.js')
    const { getDataZoomOpts } = useChartTheme()

    // 数据量小于阈值，不启用缩放
    const opts1 = getDataZoomOpts(30, 60)
    expect(opts1[0].start).toBe(0)

    // 数据量大于阈值，启用缩放
    const opts2 = getDataZoomOpts(120, 60)
    expect(opts2[0].start).toBeGreaterThan(0)
  })
})
