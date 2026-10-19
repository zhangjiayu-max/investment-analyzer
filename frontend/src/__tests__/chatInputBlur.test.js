import { afterEach, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import ChatInput from '../components/chat/ChatInput.vue'

afterEach(() => vi.useRealTimers())

it('closes expert suggestions after blur while allowing a suggestion click', async () => {
  vi.useFakeTimers()
  const errors = []
  const wrapper = mount(ChatInput, {
    props: { inputText: '@', agents: [{ id: 1, name: '风险管理师', agent_key: 'risk' }] },
    global: { config: { errorHandler: error => errors.push(error) } },
  })
  const input = wrapper.find('textarea')
  input.element.value = '@'
  input.element.selectionStart = 1
  await input.trigger('input')
  expect(wrapper.text()).toContain('风险管理师')
  await input.trigger('blur')
  expect(errors).toEqual([])
  expect(wrapper.text()).toContain('风险管理师')
  await vi.advanceTimersByTimeAsync(200)
  expect(wrapper.text()).not.toContain('风险管理师')
  wrapper.unmount()
})
