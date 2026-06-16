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
    // 清理多余的空行和空格
    const cleaned = text
      .replace(/\n{3,}/g, '\n\n')  // 3个以上连续空行合并为2个
      .replace(/\n\s*\n\s*\n/g, '\n\n')  // 清理包含空格的空行
      .replace(/^\s+$/gm, '')  // 清理只有空格的行
      .trim()
    const html = marked(cleaned)
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
