import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { shallowMount, flushPromises } from '@vue/test-utils'
import http from '../api/http'
import '../api/interceptors'
import ArticleManagement from '../components/knowledge/ArticleManagement.vue'

const { showToast } = vi.hoisted(() => ({ showToast: vi.fn() }))
vi.mock('../composables/useToast', () => ({ useToast: () => ({ showToast }) }))

const wrappers = []
const originalAdapter = http.defaults.adapter
let articles, requests, respond
const deferred = () => {
  let resolve
  const promise = new Promise(r => { resolve = r })
  return { promise, resolve }
}
const article = (id, status = 'pending') => ({ id, title: `文章${id}`, status, analysis_records: [] })
const success = data => ({ code: 0, message: 'ok', data })
beforeEach(() => {
  vi.useFakeTimers()
  vi.clearAllMocks()
  articles = [article(1), article(2)]
  requests = []
  respond = async config => {
    if (config.url === '/articles') return success({ articles })
    if (config.url === '/articles/add') return success({ article_id: 3 })
    if (/\/articles\/\d+$/.test(config.url)) return success(articles.find(a => a.id === Number(config.url.split('/').at(-1))))
    return success({ message: '已开始' })
  }
  // 使用实际 API 与拦截器，模拟 ResponseWrapper 已移除 ok 的线上协议。
  http.defaults.adapter = async config => {
    requests.push(config.url)
    return { data: await respond(config), status: 200, statusText: 'OK', headers: {}, config }
  }
})
afterEach(() => {
  wrappers.splice(0).forEach(w => w.unmount())
  http.defaults.adapter = originalAdapter
  vi.useRealTimers()
})
async function render() {
  const wrapper = shallowMount(ArticleManagement)
  wrappers.push(wrapper)
  await flushPromises()
  return wrapper.vm.$.setupState
}

it('shows a newly added article after unwrapping success and clears filters hiding it', async () => {
  const state = await render()
  articles = [article(3), ...articles]
  state.statusFilter = 'analyzed'
  state.searchQuery = '旧文章'
  state.addUrl = 'https://mp.weixin.qq.com/s/new'
  await state.handleAddArticle()
  await flushPromises()
  expect(state.addUrl).toBe('')
  expect(state.filteredArticles[0].id).toBe(3)
  expect(state.selectedArticle.id).toBe(3)
  expect(showToast).toHaveBeenCalledWith('已提交，正在解析下载分析...', 'success')
})

it('starts download polling after an unwrapped successful request', async () => {
  const state = await render()
  await state.openArticle(articles[0])
  await state.handleDownload(1)
  expect(state.selectedArticle.status).toBe('downloading')
  articles[0] = article(1, 'downloaded')
  await vi.advanceTimersByTimeAsync(2000)
  expect(state.selectedArticle.status).toBe('downloaded')
  expect(showToast).not.toHaveBeenCalled()
})

it('starts analysis polling after an unwrapped successful request', async () => {
  const state = await render()
  await state.openArticle(articles[0])
  await state.handleAnalyze(1)
  expect(state.selectedArticle.status).toBe('analyzing')
  await vi.advanceTimersByTimeAsync(1500)
  expect(requests).toContain('/articles/1/analyze-status')
  expect(showToast).not.toHaveBeenCalled()
})

it('keeps cancel progress after an unwrapped successful cancellation', async () => {
  articles[0].status = 'analyzing'
  const state = await render()
  await state.openArticle(articles[0])
  await state.handleCancelAnalyze(1)
  expect(state.cancelling).toBe(true)
  expect(showToast).not.toHaveBeenCalled()
})

it('starts record retry after an unwrapped successful request', async () => {
  articles[0].analysis_records = [{ id: 11, status: 'error' }]
  const state = await render()
  await state.openArticle(articles[0])
  await state.handleReanalyze(11)
  expect(state.analyzingRecords.has(11)).toBe(true)
  expect(state.records[0].status).toBe('analyzing')
  expect(showToast).toHaveBeenCalledWith('重新分析已开始，后台执行中...', 'success')
})

it('keeps article B selected when the older detail request for A returns last', async () => {
  const state = await render()
  const pending = deferred()
  const fallback = respond
  respond = config => config.url === '/articles/1' ? pending.promise : fallback(config)
  const first = state.openArticle(articles[0])
  await state.openArticle(articles[1])
  pending.resolve(success(articles[0]))
  await first
  expect(state.selectedArticle.id).toBe(2)
})

it('keeps the other record retry polling when one concurrent retry completes', async () => {
  articles[0].analysis_records = [{ id: 11, status: 'error' }, { id: 12, status: 'error' }]
  const state = await render()
  await state.openArticle(articles[0])
  await state.handleReanalyze(11)
  await state.handleReanalyze(12)
  const fallback = respond
  respond = config => config.url === '/records/11/reanalyze-status'
    ? success({ status: 'success' })
    : config.url === '/records/12/reanalyze-status'
      ? success({ status: 'analyzing' }) : fallback(config)
  await vi.advanceTimersByTimeAsync(1500)
  expect(state.analyzingRecords.has(11)).toBe(false)
  expect(state.analyzingRecords.has(12)).toBe(true)
  requests.length = 0
  await vi.advanceTimersByTimeAsync(1500)
  expect(requests).toContain('/records/12/reanalyze-status')
})

it('does not overwrite B with an in-flight download poll for A', async () => {
  const state = await render()
  await state.openArticle(articles[0])
  await state.handleDownload(1)
  const pending = deferred()
  const fallback = respond
  respond = config => config.url === '/articles/1' ? pending.promise : fallback(config)
  await vi.advanceTimersByTimeAsync(2000)
  await state.openArticle(articles[1])
  pending.resolve(success(article(1, 'downloaded')))
  await flushPromises()
  expect(state.selectedArticle.id).toBe(2)
  expect(state.selectedArticle.status).toBe('pending')
})

it('does not stop B analysis polling when an older status request for A returns', async () => {
  articles = [article(1, 'analyzing'), article(2, 'analyzing')]
  const state = await render()
  await state.openArticle(articles[0])
  const pending = deferred()
  const fallback = respond
  respond = config => config.url === '/articles/1/analyze-status'
    ? pending.promise : config.url.endsWith('/analyze-status')
      ? success({ status: 'analyzing', progress: {} }) : fallback(config)
  await vi.advanceTimersByTimeAsync(1500)
  await state.openArticle(articles[1])
  pending.resolve(success({ status: 'analyzed', progress: {} }))
  await flushPromises()
  await vi.advanceTimersByTimeAsync(1500)
  expect(requests).toContain('/articles/2/analyze-status')
})

it('does not replace B records with a delayed analysis detail refresh for A', async () => {
  articles = [article(1, 'analyzing'), article(2)]
  articles[1].analysis_records = [{ id: 22, status: 'success' }]
  const state = await render()
  await state.openArticle(articles[0])
  const pending = deferred()
  const fallback = respond
  respond = config => config.url === '/articles/1'
    ? pending.promise : config.url === '/articles/1/analyze-status'
      ? success({ status: 'analyzing', progress: { done: 1 } }) : fallback(config)
  await vi.advanceTimersByTimeAsync(1500)
  await state.openArticle(articles[1])
  pending.resolve(success({ ...articles[0], analysis_records: [{ id: 11, status: 'success' }] }))
  await flushPromises()
  expect(state.records[0].id).toBe(22)
})

it('ignores an outstanding download poll after the page unmounts', async () => {
  const state = await render()
  await state.openArticle(articles[0])
  await state.handleDownload(1)
  const pending = deferred()
  const fallback = respond
  respond = config => config.url === '/articles/1' ? pending.promise : fallback(config)
  await vi.advanceTimersByTimeAsync(2000)
  wrappers.at(-1).unmount()
  pending.resolve(success(article(1, 'downloaded')))
  await flushPromises()
  expect(state.selectedArticle.status).toBe('downloading')
})

it('shows the server rejection without pretending a download has started', async () => {
  const state = await render()
  await state.openArticle(articles[0])
  const fallback = respond
  respond = config => config.url.endsWith('/download')
    ? { code: 400, message: '正在下载中，请稍候', data: null } : fallback(config)
  const error = vi.spyOn(console, 'error').mockImplementation(() => {})
  try {
    await state.handleDownload(1)
    expect(state.selectedArticle.status).toBe('pending')
    expect(showToast).toHaveBeenCalledWith('下载失败: 正在下载中，请稍候', 'error')
  } finally { error.mockRestore() }
})
