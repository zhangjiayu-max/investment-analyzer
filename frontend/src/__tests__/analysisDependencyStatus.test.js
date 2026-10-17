import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('../api/http', () => ({ default: { get: vi.fn(), post: vi.fn() } }))
vi.mock('../api', () => ({
  getSystemConfigs: vi.fn().mockResolvedValue({ data: { configs: [] } }),
  updateSystemConfig: vi.fn(),
  resetSystemConfigs: vi.fn(),
}))

import api from '../api/http'
import AnalysisDependencyStatus from '../components/quality/AnalysisDependencyStatus.vue'
import SystemConfigPage from '../components/quality/SystemConfigPage.vue'

const wrappers = []
function render(component = AnalysisDependencyStatus) {
  const wrapper = mount(component, { global: { stubs: { teleport: true } } })
  wrappers.push(wrapper)
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
  api.get.mockResolvedValue({ data: { ok: true, installed: true, logged_in: false, state: 'expired' } })
  api.post.mockResolvedValue({ data: { ok: true, message: '登录成功', output: 'auth_token=secret-token' } })
})
afterEach(() => wrappers.splice(0).forEach(wrapper => wrapper.unmount()))

describe('AnalysisDependencyStatus', () => {
  it.each([
    [{ installed: true, logged_in: true, state: 'ready' }, '已登录'],
    [{ installed: true, logged_in: false, state: 'expired' }, '登录已过期'],
    [{ installed: true, logged_in: false, state: 'login_required' }, '需要扫码登录'],
    [{ installed: false, logged_in: false }, '未安装'],
    [{ installed: true, logged_in: false, state: 'error', error: 'secret-token' }, '状态检查失败'],
  ])('loads and displays dependency state without starting login: %s', async (state, label) => {
    api.get.mockResolvedValue({ data: state })
    const wrapper = render()
    await flushPromises()
    expect(api.get).toHaveBeenCalledWith('/config/ttfund/status')
    expect(wrapper.text()).toContain('天天基金')
    expect(wrapper.text()).toContain(label)
    expect(wrapper.text()).not.toContain('secret-token')
    expect(api.post).not.toHaveBeenCalled()
  })

  it('requires ConfirmDialog approval and allows cancellation before a manual login', async () => {
    const wrapper = render()
    await flushPromises()
    await wrapper.get('[data-testid="ttfund-login"]').trigger('click')
    expect(api.post).not.toHaveBeenCalled()
    const dialog = wrapper.findComponent({ name: 'ConfirmDialog' })
    expect(dialog.props('visible')).toBe(true)
    dialog.vm.$emit('cancel')
    await flushPromises()
    expect(api.post).not.toHaveBeenCalled()
    await wrapper.get('[data-testid="ttfund-login"]').trigger('click')
    dialog.vm.$emit('confirm')
    await flushPromises()
    expect(api.post).toHaveBeenCalledTimes(1)
    expect(api.post).toHaveBeenCalledWith('/config/ttfund/login', {}, { timeout: 150000 })
    expect(wrapper.text()).not.toContain('secret-token')
    expect(wrapper.text()).not.toContain('auth_token')
  })

  it('explains the real browser scan flow while login is pending and prevents duplicate requests', async () => {
    let finish
    api.post.mockImplementation(() => new Promise(resolve => { finish = resolve }))
    const wrapper = render()
    await flushPromises()
    await wrapper.get('[data-testid="ttfund-login"]').trigger('click')
    wrapper.findComponent({ name: 'ConfirmDialog' }).vm.$emit('confirm')
    await flushPromises()
    expect(wrapper.text()).toContain('浏览器')
    expect(wrapper.text()).toContain('扫码')
    expect(wrapper.get('[data-testid="ttfund-login"]').attributes('disabled')).toBeDefined()
    expect(wrapper.find('img').exists()).toBe(false)
    finish({ data: { ok: true, output: 'do not render raw QR or tokens' } })
    await flushPromises()
    expect(wrapper.text()).not.toContain('do not render')
  })

  it('refreshes state manually and clears stale login state after a failed status request', async () => {
    api.get.mockResolvedValueOnce({ data: { installed: true, logged_in: true, state: 'ready' } })
    const wrapper = render()
    await flushPromises()
    expect(wrapper.text()).toContain('已登录')
    api.get.mockRejectedValueOnce(new Error('auth_token=secret-token'))
    await wrapper.get('[data-testid="ttfund-refresh"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('状态检查失败')
    expect(wrapper.text()).not.toContain('已登录')
    expect(wrapper.text()).not.toContain('secret-token')
    expect(api.post).not.toHaveBeenCalled()
  })

  it('displays a safe login failure without backend payload or automatic retries', async () => {
    api.post.mockRejectedValue(new Error('secret-token raw-login-output'))
    const wrapper = render()
    await flushPromises()
    await wrapper.get('[data-testid="ttfund-login"]').trigger('click')
    wrapper.findComponent({ name: 'ConfirmDialog' }).vm.$emit('confirm')
    await flushPromises()
    expect(wrapper.text()).toContain('登录未完成')
    expect(wrapper.text()).not.toContain('secret-token')
    expect(wrapper.text()).not.toContain('raw-login-output')
    expect(api.post).toHaveBeenCalledTimes(1)
  })

  it('does not offer an installation or login request for an absent dependency', async () => {
    api.get.mockResolvedValue({ data: { installed: false, logged_in: false } })
    const wrapper = render()
    await flushPromises()
    expect(wrapper.find('[data-testid="ttfund-login"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('未安装')
    expect(api.post).not.toHaveBeenCalled()
  })

  it('appears above the editable configuration filters', async () => {
    const wrapper = render(SystemConfigPage)
    await flushPromises()
    const panel = wrapper.get('[data-testid="analysis-dependencies"]').element
    const filters = wrapper.get('.filter-bar').element
    expect(panel.compareDocumentPosition(filters) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })
})
