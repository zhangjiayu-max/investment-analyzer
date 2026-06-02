// 统一 Markdown 渲染层（带 XSS 防护）
// 替代各组件中重复的 renderMarkdown 实现
import { marked } from 'marked'
import DOMPurify from 'dompurify'

marked.setOptions({
  breaks: true,
  gfm: true,
})

/**
 * 将 markdown 文本转为安全的 HTML
 * 自动过滤 script/onerror 等 XSS 攻击向量
 * @param {string} text
 * @returns {string}
 */
export function renderMarkdown(text) {
  if (!text) return ''
  try {
    const html = marked(text)
    return DOMPurify.sanitize(html, {
      ALLOWED_TAGS: ['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'br', 'hr',
        'ul', 'ol', 'li', 'blockquote', 'pre', 'code', 'em', 'strong',
        'a', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'img', 'span', 'div'],
      ALLOWED_ATTR: ['href', 'src', 'alt', 'title', 'class', 'target', 'rel'],
    })
  } catch {
    return DOMPurify.sanitize(text)
  }
}

export function useMarkdown() {
  return { renderMarkdown }
}
