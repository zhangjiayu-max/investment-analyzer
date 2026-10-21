import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { useStreamingState } from '../composables/useStreamingState'
import StreamIndicator from '../components/chat/StreamIndicator.vue'

let host
afterEach(() => { host?.unmount(); vi.useRealTimers() })

describe('degraded delivery terminal streaming state', () => {
  it('stops executing as soon as the terminal answer arrives without waiting for done', () => {
    vi.useFakeTimers()
    let streaming
    host = mount(defineComponent({ setup() { streaming = useStreamingState(); return () => null } }))
    streaming.startStream(31, new AbortController())
    streaming.handleStreamEvent(31, { type: 'answer', data: {
      content: '可读综合正文', execution_status: 'degraded', completion_status: 'degraded', quality_status: 'degraded',
      delivery_message: '分析已生成，部分核验未完成。',
    } })
    const state = streaming.getStreamState(31)
    expect(state.sending).toBe(false)
    expect(state.streamStatus).toBe('degraded')
    expect(state.progressPct).toBe(100)
    expect(state.elapsedTimer).toBeNull()
    expect(streaming.isAnyStreaming.value).toBe(false)
  })

  it('renders the generated pending review status without an executing pulse', () => {
    host = mount(StreamIndicator, { props: { currentStream: {
      streamStatus: 'degraded', statusMessage: '分析已生成，部分核验未完成。',
      completedSpecialists: [], activeSpecialists: [], completedCrossReviews: [],
      crossReviewSpecialists: [], totalPhases: 6, progressPct: 100,
    } }, global: { stubs: { Icon: true } } })
    expect(host.text()).toContain('分析已生成，部分核验未完成')
    expect(host.find('.dot-pulse').exists()).toBe(false)
    expect(host.find('.message-bubble.typing').exists()).toBe(false)
  })
})
