import { afterEach, describe, expect, it, vi } from 'vitest'
import { toMarkdown, markdownFilename, downloadMarkdown } from '../utils/markdownExport'

afterEach(() => vi.restoreAllMocks())

describe('analysis Markdown export', () => {
  it('preserves the entire original Markdown including tables and code', () => {
    const md = '## 结论\n\n| 指数 | PE |\n| --- | --- |\n| 沪深300 | 12 |\n\n```js\nconst x = 1\n```\n' + '完整正文'.repeat(2000)
    expect(toMarkdown(md)).toBe(md)
  })

  it('converts structured JSON and objects without losing nested analysis or fields', () => {
    const data = { summary: '市场综述', recommendations: [{ fund_code: '000001', reason: '**长期持有**', amount: 0 }], risk_warning: '关注回撤', extra: { verified: false } }
    const md = toMarkdown(data)
    expect(toMarkdown(JSON.stringify(data))).toBe(md)
    for (const text of ['市场综述', '000001', '**长期持有**', '0', '关注回撤', 'false', 'extra']) expect(md).toContain(text)
    expect(md).not.toContain('[object Object]')
    expect(md).toContain('## 摘要')
    expect(md).toContain('## 推荐')
  })

  it('handles serialized report wrappers, fenced JSON and empty results', () => {
    expect(toMarkdown('{"text":"## 原始报告\\n正文"}')).toContain('## 原始报告\n正文')
    expect(toMarkdown('```json\n{"summary":"完整摘要"}\n```')).toContain('完整摘要')
    for (const value of [null, undefined, '', '  ', {}, []]) expect(toMarkdown(value)).toBe('')
  })

  it('creates safe descriptive Markdown filenames', () => {
    const name = markdownFilename('基金/分析:报告.md', '2026-10-20 10:12:00', 42)
    expect(name).toBe('基金-分析-报告-2026-10-20-42.md')
    expect(markdownFilename(' /:*?"<>|\n ')).toMatch(/\.md$/)
    expect(markdownFilename('x'.repeat(400)).length).toBeLessThan(200)
  })

  it('downloads the full UTF-8 report with metadata and releases its URL later', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    const create = vi.fn(() => 'blob:report')
    const revoke = vi.fn()
    vi.stubGlobal('URL', { createObjectURL: create, revokeObjectURL: revoke })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    try {
      downloadMarkdown('## 原文\n- 完整结果', { title: '全景诊断', createdAt: '2026-10-20 10:12:00', recordId: 42 })
      const blob = create.mock.calls[0][0]
      expect(blob.type).toBe('text/markdown;charset=utf-8')
      const text = await new Promise(resolve => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.readAsText(blob) })
      expect(text).toContain('# 全景诊断')
      expect(text).toContain('2026-10-20 10:12:00')
      expect(text).toContain('42')
      expect(text).toContain('## 原文\n- 完整结果')
      expect(click).toHaveBeenCalledOnce()
      expect(document.querySelector('a[download]')).toBeNull()
      expect(revoke).not.toHaveBeenCalled()
      vi.runAllTimers()
      expect(revoke).toHaveBeenCalledWith('blob:report')
    } finally { vi.useRealTimers(); vi.unstubAllGlobals() }
  })
})
