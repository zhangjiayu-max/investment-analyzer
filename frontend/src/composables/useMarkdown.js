// 统一 Markdown 渲染层
// 替代各组件中重复的 renderMarkdown 实现
import { marked } from 'marked'

marked.setOptions({
  breaks: true,
  gfm: true,
})

/**
 * 将 markdown 文本转为 HTML
 * @param {string} text
 * @returns {string}
 */
export function renderMarkdown(text) {
  if (!text) return ''
  try {
    return marked(text)
  } catch {
    return text
  }
}

export function useMarkdown() {
  return { renderMarkdown }
}
