/** 读取对话事件；非终态 EOF 与短断均交给消费者只读核对，禁止自动重发分析。 */
export async function consumeConversationSse(response, onEvent, signal) {
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let terminal = false
  const dispatch = line => {
    if (!line.startsWith('data:')) return
    let event
    try { event = JSON.parse(line.slice(5).trim()) } catch (_) { return }
    if (signal.aborted) return
    if (['done', 'error'].includes(event.type) || (event.type === 'replay_end' && event.data?.status !== 'running')) terminal = true
    onEvent(event)
  }
  try {
    while (!signal.aborted) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop()
      for (const line of lines) dispatch(line.trimEnd())
    }
    buffer += decoder.decode()
    if (buffer) dispatch(buffer.trimEnd())
    if (!terminal && !signal.aborted) onEvent({ type: 'disconnect', data: { reason: 'eof' } })
  } finally {
    reader.releaseLock()
  }
}
