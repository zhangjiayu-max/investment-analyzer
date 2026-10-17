import { shallowMount, flushPromises } from '@vue/test-utils'
import { afterEach, expect, it, vi } from 'vitest'
vi.hoisted(() => { vi.stubGlobal('localStorage', { getItem: vi.fn(key => key === 'activePage' ? 'dashboard' : null), setItem: vi.fn(), removeItem: vi.fn() }) })
vi.mock('../composables/useMobile', async () => { const { ref } = await import('vue'); return { useMobile: () => ({ isMobile: ref(true) }) } })
vi.mock('../api', async original => { const api = await original(); return Object.fromEntries(Object.entries(api).map(([key, value]) => [key, typeof value === 'function' ? vi.fn(async () => ({ data: {} })) : value])) })
vi.mock('../components/portfolio/PortfolioManagement.vue', () => ({ default: { template: '<div />' } }))
vi.mock('../components/knowledge/ArticleManagement.vue', () => ({ default: { template: '<div />' } }))
import App from '../App.vue'
import MobileApp from '../components/MobileApp.vue'
const wrappers = []
afterEach(() => wrappers.splice(0).forEach(w => w.unmount()))
it('uses shared navigation for task notifications and passes chat execution navigation', async () => {
  const wrapper = shallowMount(App, { global: { stubs: { MobileApp: false } } }); wrappers.push(wrapper); await flushPromises()
  const mobile = wrapper.findComponent(MobileApp); const state = mobile.vm.$.setupState
  state.navigate('portfolio')
  wrapper.vm.$.setupState.handleViewResult(77); await flushPromises()
  expect(state.activePage).toBe('chat')
  expect(typeof state.pageProps.onNavigate).toBe('function')
  state.pageProps.onNavigate('portfolio'); await flushPromises()
  expect(state.activePage).toBe('portfolio')
  expect(wrapper.vm.$.setupState.activePage).toBe('portfolio')
})
