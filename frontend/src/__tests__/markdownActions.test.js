import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import MarkdownActions from '../components/ui/MarkdownActions.vue'
import AgentResultCard from '../components/ui/AgentResultCard.vue'
import AnalysisCard from '../components/ui/AnalysisCard.vue'

const md = '## 建议\n\n| 基金 | 建议 |\n| --- | --- |\n| 000001 | 持有 |'
beforeEach(() => {
  vi.stubGlobal('isSecureContext', true)
  Object.defineProperty(window, 'isSecureContext', { value: true, configurable: true })
  Object.defineProperty(navigator, 'clipboard', { value: { writeText: vi.fn().mockResolvedValue() }, configurable: true })
  vi.stubGlobal('URL', { createObjectURL: vi.fn(() => 'blob:md'), revokeObjectURL: vi.fn() })
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
})
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('copies Markdown and reports success without triggering a parent click', async () => {
  const parentClick = vi.fn()
  const w = mount({ components: { MarkdownActions }, template: '<div @click="clicked"><MarkdownActions :content="md" /></div>', setup: () => ({ clicked: parentClick, md }) })
  await w.get('.markdown-actions__copy').trigger('click')
  await flushPromises()
  expect(navigator.clipboard.writeText).toHaveBeenCalledWith(md)
  expect(w.text()).toContain('已复制')
  expect(parentClick).not.toHaveBeenCalled()
  w.unmount()
})

it('falls back when Clipboard API is missing and reports a real failure', async () => {
  Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true })
  document.execCommand = vi.fn(() => false)
  const w = mount(MarkdownActions, { props: { content: md } })
  await w.get('.markdown-actions__copy').trigger('click')
  expect(w.text()).toContain('复制失败')
  expect(w.emitted('copied')).toBeUndefined()
  document.execCommand = vi.fn(() => true)
  await w.get('.markdown-actions__copy').trigger('click')
  expect(w.emitted('copied')[0]).toEqual([md])
  w.unmount()
})

it('handles rejected clipboard access without claiming success', async () => {
  navigator.clipboard.writeText.mockRejectedValue(new Error('denied'))
  document.execCommand = vi.fn(() => false)
  const w = mount(MarkdownActions, { props: { content: md } })
  await w.get('.markdown-actions__copy').trigger('click')
  await flushPromises()
  expect(w.text()).toContain('复制失败')
  expect(w.emitted('copied')).toBeUndefined()
  w.unmount()
})

it('downloads a named report and reports export failures', async () => {
  const w = mount(MarkdownActions, { props: { content: md, title: '基金分析', recordId: 5 } })
  await w.get('.markdown-actions__export').trigger('click')
  expect(URL.createObjectURL).toHaveBeenCalledOnce()
  expect(w.emitted('exported')).toHaveLength(1)
  URL.createObjectURL.mockImplementation(() => { throw new Error('unavailable') })
  await w.get('.markdown-actions__export').trigger('click')
  expect(w.text()).toContain('导出失败')
  w.unmount()
})

it('hides empty output actions and disables unavailable output', () => {
  const empty = mount(MarkdownActions, { props: { content: '  ' } })
  expect(empty.find('button').exists()).toBe(false)
  const disabled = mount(MarkdownActions, { props: { content: md, disabled: true } })
  expect(disabled.findAll('button').every(button => button.element.disabled)).toBe(true)
})

it('makes both shared report cards exportable and hides actions for unfinished results', () => {
  const analysis = mount(AnalysisCard, { props: { result: md, agentName: '全景诊断分析师' } })
  expect(analysis.find('.markdown-actions__export').exists()).toBe(true)
  for (const status of ['running', 'empty', 'error']) {
    const agent = mount(AgentResultCard, { props: { content: md, status } })
    expect(agent.find('.markdown-actions__export').exists()).toBe(false)
    agent.unmount()
  }
  analysis.unmount()
})
