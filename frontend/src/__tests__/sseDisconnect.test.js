import { afterEach, expect, it, vi } from 'vitest'
import { sendMessageStream, clarifyAnswerStream, resumeConversationStream, replayConversationStream } from '../api'
const read = async () => { for (let i = 0; i < 20; i++) await Promise.resolve() }
afterEach(() => vi.unstubAllGlobals())
it.each([
  ['send', cb => sendMessageStream(1, 'hello', cb)],
  ['clarify', cb => clarifyAnswerStream(1, 'yes', 2, cb)],
  ['resume', cb => resumeConversationStream(1, cb)],
  ['replay', cb => replayConversationStream(1, 'channel', 0, cb)],
])('%s notifies consumers when EOF arrives before a terminal event', async (_, start) => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('data: {"type":"status","data":{"message":"working"}}\n\n')))
  const events = []; start(event => events.push(event)); await read()
  expect(events.map(e => e.type)).toEqual(['status', 'disconnect'])
})
it('network failures emit disconnect but a normal done stream does not', async () => {
  vi.stubGlobal('fetch', vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(new Response('data: {"type":"done","data":{}}\n\n')))
  const events = []; sendMessageStream(1, 'hello', e => events.push(e)); await read()
  expect(events[0].type).toBe('disconnect')
  const terminal = []; sendMessageStream(1, 'hello', e => terminal.push(e)); await read()
  expect(terminal.map(e => e.type)).toEqual(['done'])
})
it('does not emit disconnect after an intentional abort', async () => {
  vi.stubGlobal('fetch', vi.fn((_url, { signal }) => new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError'))))))
  const event = vi.fn(); sendMessageStream(1, 'hello', event).abort(); await read(); expect(event).not.toHaveBeenCalled()
})
