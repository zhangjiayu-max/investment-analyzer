import { defineComponent, h, KeepAlive, ref } from 'vue'
import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import TermTooltip from '../components/ui/TermTooltip.vue'
const wrappers = []
let anchorRect
function render() {
  const container = document.createElement('div'); container.style.overflow = 'hidden'; document.body.append(container)
  const w = mount(TermTooltip, { props: { label: '资产总览', content: '已登记现金与基金资产汇总，不能据此确认家庭净资产。' }, attachTo: container }); wrappers.push(w); return w
}
beforeEach(() => {
  vi.stubGlobal('innerWidth', 320); vi.stubGlobal('innerHeight', 300)
  anchorRect = { top: 2, bottom: 22, left: 270, right: 310, width: 40, height: 20 }
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function () {
    return this.getAttribute('role') === 'tooltip' ? { width: 260, height: 100, top: 0, bottom: 100, left: 0, right: 260 } : anchorRect
  })
})
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); document.body.innerHTML = ''; vi.restoreAllMocks(); vi.unstubAllGlobals() })
describe('TermTooltip', () => {
  it('supports an icon-only trigger while keeping an accessible label', async () => {
    const w = mount(TermTooltip, { props: { label: '融资数据', content: '融资数据说明正文', iconOnly: true }, slots: { trigger: '<span class="info-icon">i</span>' } }); wrappers.push(w)
    expect(w.text()).toBe('i')
    expect(w.get('button').attributes('aria-label')).toBe('融资数据说明')
    await w.get('button').trigger('click'); await flushPromises()
    expect(document.body.querySelector('[role="tooltip"]').textContent).toBe('融资数据说明正文')
  })
  it('teleports beyond a clipped card and clamps the bubble at the top-right viewport edge', async () => {
    const w = render(); await w.get('button').trigger('focus'); await flushPromises()
    const tip = document.body.querySelector('[role="tooltip"]')
    expect(tip).not.toBeNull(); expect(tip.parentElement).toBe(document.body)
    expect(w.element.contains(tip)).toBe(false)
    expect(tip.style.position).toBe('fixed')
    expect(Number.parseFloat(tip.style.left)).toBeGreaterThanOrEqual(12)
    expect(Number.parseFloat(tip.style.left) + 260).toBeLessThanOrEqual(308)
    expect(Number.parseFloat(tip.style.top)).toBeGreaterThanOrEqual(22)
    expect(w.get('button').attributes('aria-describedby')).toBe(tip.id)
  })
  it('flips above a bottom-edge anchor and dismisses with Escape', async () => {
    anchorRect = { top: 280, bottom: 300, left: 0, right: 40, width: 40, height: 20 }
    const w = render(); await w.get('button').trigger('focus'); await flushPromises()
    const tip = document.body.querySelector('[role="tooltip"]')
    expect(Number.parseFloat(tip.style.top) + 100).toBeLessThan(280)
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); await flushPromises()
    expect(document.body.querySelector('[role="tooltip"]')).toBeNull()
    expect(w.get('button').attributes('aria-describedby')).toBeUndefined()
  })
  it('supports touch click toggling, outside dismissal and scrolling dismissal', async () => {
    const w = render(); await w.get('button').trigger('click'); await flushPromises()
    expect(document.body.querySelector('[role="tooltip"]')).not.toBeNull()
    await w.get('button').trigger('click'); await flushPromises()
    expect(document.body.querySelector('[role="tooltip"]')).toBeNull()
    await w.get('button').trigger('click'); await flushPromises()
    document.body.dispatchEvent(new Event('pointerdown', { bubbles: true })); await flushPromises()
    expect(document.body.querySelector('[role="tooltip"]')).toBeNull()
    await w.get('button').trigger('focus'); await flushPromises()
    window.dispatchEvent(new Event('scroll')); await flushPromises()
    expect(document.body.querySelector('[role="tooltip"]')).toBeNull()
  })
  it('dismisses a click-pinned explanation when keyboard focus leaves', async () => {
    const w = render(); await w.get('button').trigger('focus'); await w.get('button').trigger('click'); await flushPromises()
    expect(document.body.querySelector('[role="tooltip"]')).not.toBeNull()
    await w.get('button').trigger('blur'); await flushPromises()
    expect(document.body.querySelector('[role="tooltip"]')).toBeNull()
  })
  it('removes its teleported bubble when the cached page is deactivated', async () => {
    const visible = ref(true)
    const page = defineComponent({ render: () => h(TermTooltip, { label: '说明', content: '完整说明' }) })
    const other = defineComponent({ render: () => h('div', 'other') })
    const host = defineComponent({ setup: () => () => h(KeepAlive, null, { default: () => h(visible.value ? page : other) }) })
    const w = mount(host, { attachTo: document.body }); wrappers.push(w)
    await w.get('button').trigger('focus'); await flushPromises()
    expect(document.body.querySelector('[role="tooltip"]')).not.toBeNull()
    visible.value = false; await flushPromises()
    expect(document.body.querySelector('[role="tooltip"]')).toBeNull()
  })
})
