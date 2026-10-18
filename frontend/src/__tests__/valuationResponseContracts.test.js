import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { shallowMount, flushPromises } from '@vue/test-utils'
import http from '../api/http'
import '../api/interceptors'
import ImageGallery from '../components/knowledge/ImageGallery.vue'
import ValuationHistory from '../components/valuation/ValuationHistory.vue'

const { showToast } = vi.hoisted(() => ({ showToast: vi.fn() }))
vi.mock('../composables/useToast', () => ({ useToast: () => ({ showToast }) }))
vi.mock('../composables/useTheme', () => ({ isDark: { value: false } }))
vi.mock('../composables/useAsyncTask', () => ({ useAsyncTask: () => ({
  taskState: { value: 'idle' }, start: vi.fn(), restore: vi.fn(), restoreFromServer: async () => false,
}) }))

const wrappers = []
let failedPaths = new Set()
let requestUrls = []
beforeEach(() => {
  failedPaths = new Set()
  requestUrls = []
  showToast.mockClear()
  http.defaults.adapter = async config => {
    requestUrls.push(config.url)
    let data = { records: [], indexes: [], dates: [], images: [], history: [], latest: null, total: 0 }
    let code = 0
    let message = 'ok'
    if (config.url.includes('/valuation/parse')) {
      const body = JSON.parse(config.data)
      if (failedPaths.has(body.path)) {
        code = 400
        message = '解析失败'
        data = null
      } else if (config.url.endsWith('parse-dd')) {
        data = { dd_id: 2, count: 1 }
      } else if (config.url.endsWith('parse-liuyi')) {
        data = { liuyi_id: 3, count: 1 }
      } else {
        data = { id: 1, type: 'valuation', index_code: '000300' }
      }
    }
    if (config.url.includes('/valuation/parse-dd-async')) data = { task_id: 7, status: 'pending' }
    if (config.url.includes('/valuation/parse-dd-task/7')) data = { id: 7, status: 'done', result_json: { count: 1, data: [] } }
    if (config.url.includes('/dd-images/upload')) data = { ok: true, path: '2026-10-18/uploaded.png' }
    if (config.url.includes('/valuation/online-query/')) data = { data: { source: 'akshare', percentile: 25 } }
    return { data: { code, message, data }, status: 200, statusText: 'OK', headers: {}, config }
  }
})
afterEach(() => {
  wrappers.splice(0).forEach(w => w.unmount())
  delete http.defaults.adapter
})
function render(component) {
  const wrapper = shallowMount(component)
  wrappers.push(wrapper)
  return wrapper.vm.$.setupState
}

it('counts all three valuation types as successful after the actual response interceptor removes ok', async () => {
  const state = render(ImageGallery)
  await flushPromises()
  state.confirmViBatchParse('2026-10-18', [
    { path: 'data/valuation_images/index.jpg', name: '指数.jpg' },
    { path: 'data/dd_images/table.jpg', name: '螺丝钉.jpg' },
    { path: 'data/liuyi_images/table.jpg', name: '六亿.jpg' },
  ])
  await state.confirm.onConfirm()
  expect(state.toasts.at(-1).message).toBe('批量识别完成：成功 3 张')
  expect(state.toasts.at(-1).type).toBe('success')
})

it('does not count an HTTP 200 response with a business error as a successful image', async () => {
  const state = render(ImageGallery)
  await flushPromises()
  failedPaths.add('bad.jpg')
  state.confirmViBatchParse('2026-10-18', [
    { path: 'good.jpg', name: '成功.jpg' }, { path: 'bad.jpg', name: '失败.jpg' },
  ])
  await state.confirm.onConfirm()
  expect(state.toasts.at(-1).message).toBe('批量识别完成：成功 1 张，失败 1 张')
})

it('recognizes successful online valuation results without a payload ok flag', async () => {
  const state = render(ValuationHistory)
  await flushPromises()
  state.selectedCode = '000300'
  await state.handleOnlineQuery()
  expect(showToast).toHaveBeenCalledWith('在线查询成功（来源：akshare）')
})

it('starts asynchronous parsing after uploading a screw valuation image', async () => {
  const state = render(ImageGallery)
  await flushPromises()
  const file = new File(['image'], '螺丝钉估值.png', { type: 'image/png' })

  await state.handleUpload({ target: { files: [file] } })
  await flushPromises()

  expect(requestUrls).toContain('/dd-images/upload')
  expect(requestUrls).toContain('/valuation/parse-dd-async')
})
