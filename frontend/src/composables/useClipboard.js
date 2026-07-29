/**
 * 共享剪贴板 composable
 *
 * 抽取自 ChatView.copyToClipboard，修复 ChatMessage.fallbackCopy 在移动端的 bug：
 * - top:-9999px 在 iOS Safari 16+ select 失败 → 改为 top:0 + opacity:0
 * - 缺 fontSize:16px 触发 iOS 自动缩放
 * - 未先 focus() 再 select()
 *
 * 移动端/非安全上下文优先同步 execCommand（保留用户手势），
 * 桌面端 HTTPS 用现代 Clipboard API。
 */

/**
 * 复制文本到剪贴板
 * @param {string} text - 要复制的文本
 * @param {object} opts - 可选参数
 * @param {HTMLElement} opts.btnEl - 触发复制的按钮元素，用于添加 .copied 类反馈
 * @param {Function} opts.onSuccess - 成功回调
 * @param {Function} opts.onError - 失败回调
 * @returns {boolean} 是否成功（同步路径返回真实结果，异步路径返回 true）
 */
export function copyToClipboard(text, opts = {}) {
  const { btnEl, onSuccess, onError } = opts

  const fallbackExec = () => {
    try {
      const input = document.createElement('textarea')
      input.value = text
      // iOS Safari 16+ / 微信内置浏览器关键兼容点：
      // 1. 必须在 viewport 内（top:-9999px 会导致 select 失败）
      // 2. opacity:0 而非 display:none（display:none 无法 select）
      // 3. fontSize >= 16px 防止 iOS 自动缩放
      // 4. 必须先 focus() 再 select()
      input.setAttribute('readonly', '')
      input.style.position = 'fixed'
      input.style.top = '0'
      input.style.left = '0'
      input.style.width = '1px'
      input.style.height = '1px'
      input.style.padding = '0'
      input.style.border = 'none'
      input.style.outline = 'none'
      input.style.boxShadow = 'none'
      input.style.background = 'transparent'
      input.style.opacity = '0'
      input.style.fontSize = '16px'
      document.body.appendChild(input)

      // iOS Safari 需要先 focus 再 select，且 setSelectionRange 必须 readOnly=false
      input.focus()
      input.select()
      input.setSelectionRange(0, input.value.length)

      // 部分微信 Android 还需要 Selection API
      let ok = document.execCommand('copy')
      if (!ok) {
        try {
          const range = document.createRange()
          range.selectNodeContents(input)
          const sel = window.getSelection()
          sel.removeAllRanges()
          sel.addRange(range)
          ok = document.execCommand('copy')
        } catch (e2) {
          ok = false
        }
      }

      document.body.removeChild(input)
      return ok
    } catch (e) {
      console.warn('[clipboard] execCommand 失败:', e)
      return false
    }
  }

  const finish = (success) => {
    if (success && btnEl) {
      btnEl.classList.add('copied')
      setTimeout(() => btnEl.classList.remove('copied'), 1500)
    }
    if (success) {
      onSuccess?.()
    } else {
      onError?.()
    }
  }

  // 移动端/非安全上下文优先同步 execCommand（必须在用户手势内执行）
  // 否则 navigator.clipboard.writeText 异步回调中已脱离手势，iOS Safari / 微信浏览器
  // 会"假成功"（Promise resolve 或 execCommand 返回 true，但剪贴板实际未写入）
  const isMobile = /Android|iPhone|iPad|iPod|Mobile|MicroMessenger/i.test(navigator.userAgent)
  const isSecureContext = typeof window !== 'undefined' && window.isSecureContext

  if (isMobile || !isSecureContext) {
    // 同步路径：保留用户手势上下文
    const ok = fallbackExec()
    finish(ok)
    return ok
  }

  // 桌面端 HTTPS：用现代 Clipboard API
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(() => finish(true)).catch(() => {
      const ok = fallbackExec()
      finish(ok)
    })
    return true
  }

  const ok = fallbackExec()
  finish(ok)
  return ok
}
