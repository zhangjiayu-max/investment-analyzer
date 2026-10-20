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
    if (config.url === '/proxy-image') {
      return { data: new Blob(['image'], { type: 'image/jpeg' }), status: 200, statusText: 'OK', headers: {}, config }
    }
    let data = { records: [], indexes: [], dates: [], images: [], history: [], latest: null, total: 0 }
    let code = 0
    let message = 'ok'
    if (config.method === 'post' && config.url.includes('/valuation/parse')) {
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
    if (config.url.includes('/valuation/parse-dd-async') || config.url.includes('/valuation/parse-liuyi-async')) data = { task_id: 7, status: 'pending' }
    if (config.url.includes('/valuation/parse-dd-task/7')) data = { id: 7, status: 'done', result_json: { count: 1, data: [] } }
    if (config.url.includes('/dd-images/upload')) data = { ok: true, path: '2026-10-18/uploaded.png' }
    if (config.url.includes('/liuyi-images/upload')) data = { ok: true, path: '2026-10-20/pasted.png' }
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

it('accepts a clipboard image exposed only in the file list', async () => {
  const state = render(ImageGallery)
  await flushPromises()
  state.activeTab = 'liuyi'
  const preventDefault = vi.fn()
  const file = new File(['image'], 'long-table.png', { type: 'image/png' })
  await state.handlePaste({ clipboardData: { items: [], files: [file] }, preventDefault })
  expect(preventDefault).toHaveBeenCalled()
  expect(requestUrls.filter(url => url === '/liuyi-images/upload')).toHaveLength(1)
})

it('keeps a successfully parsed liuyi card and shows the result', async () => {
  const wrapper = shallowMount(ImageGallery)
  wrappers.push(wrapper)
  const state = wrapper.vm.$.setupState
  await flushPromises()
  state.activeTab = 'liuyi'
  await flushPromises()
  state.liuyiImages = [{ path: '2026-10-20/table.png', name: '长表.png', date: '2026-10-20' }]
  state.confirmLiuyiParseImage(state.liuyiImages[0])
  await state.confirm.onConfirm()
  await flushPromises()
  expect(state.liuyiImages).toHaveLength(1)
  expect(state.liuyiImages[0].parsed).toBe(true)
  expect(wrapper.text()).toContain('识别完成')
  expect(wrapper.text()).toContain('1 个指数')
})

it('uploads WeChat HTML clipboard images through the image proxy', async () => {
  const state = render(ImageGallery)
  await flushPromises()
  state.activeTab = 'liuyi'
  const preventDefault = vi.fn()
  await state.handlePaste({
    clipboardData: {
      items: [], files: [],
      getData: type => type === 'text/html' ? '<img src="https://mmbiz.qpic.cn/example/640?wx_fmt=jpeg">' : '',
    },
    preventDefault,
  })
  expect(preventDefault).toHaveBeenCalled()
  expect(requestUrls).toContain('/proxy-image')
  expect(requestUrls.filter(url => url === '/liuyi-images/upload')).toHaveLength(1)
})

it('zooms valuation image previews with bounded controls and resets pan on reopen', async () => {
  const state = render(ImageGallery)
  await flushPromises()

  state.openPreview('/image.jpg')
  state.zoomPreview(1)
  expect(state.previewScale).toBe(1.25)
  state.zoomPreview(-1)
  expect(state.previewScale).toBe(1)
  state.resetPreviewZoom()
  state.zoomPreview(20)
  expect(state.previewScale).toBe(5)
  state.zoomPreview(-20)
  expect(state.previewScale).toBe(1)

  const wheelEvent = { deltaY: -1, clientX: 512, clientY: 384, preventDefault: vi.fn() }
  state.handlePreviewWheel(wheelEvent)
  expect(wheelEvent.preventDefault).toHaveBeenCalled()
  expect(state.previewScale).toBe(1.25)

  state.handlePreviewPointerDown({ button: 0, clientX: 10, clientY: 20, preventDefault: vi.fn() })
  state.handlePreviewPointerMove({ clientX: 30, clientY: 50 })
  expect(state.previewOffset).toEqual({ x: 20, y: 30 })
  state.handlePreviewPointerUp()

  state.closePreview()
  state.openPreview('/next-image.jpg')
  expect(state.previewScale).toBe(1)
  expect(state.previewOffset).toEqual({ x: 0, y: 0 })
})
