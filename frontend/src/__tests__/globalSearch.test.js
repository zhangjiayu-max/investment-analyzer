import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
const { globalSearch } = vi.hoisted(() => ({ globalSearch: vi.fn() }))
vi.mock('../api', () => ({ globalSearch }))
import SearchResults from '../components/knowledge/SearchResults.vue'
const wrappers = []
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r }); return { promise, resolve } }
const response = title => ({ data: { data: { knowledge: [{ title, body: '', reference_id: '1', content_type: 'article' }], funds: [], valuations: [] } } })
const render = query => { const w = mount(SearchResults, { props: { query }, global: { stubs: { EmptyState: true } } }); wrappers.push(w); return w }
beforeEach(() => vi.resetAllMocks())
afterEach(() => wrappers.splice(0).forEach(w => w.unmount()))
it('ignores old results that arrive after a new query', async () => {
  const old = deferred(); globalSearch.mockReturnValueOnce(old.promise).mockResolvedValueOnce(response('新结果'))
  const w = render('旧'); await w.setProps({ query: '新' }); await flushPromises()
  old.resolve(response('旧结果')); await flushPromises()
  expect(w.text()).toContain('新结果'); expect(w.text()).not.toContain('旧结果')
})
it('clears loading when a query is cleared and ignores its in-flight request', async () => {
  const old = deferred(); globalSearch.mockReturnValueOnce(old.promise)
  const w = render('旧'); await w.setProps({ query: '' })
  expect(w.find('.loading-container').exists()).toBe(false)
  old.resolve(response('旧结果')); await flushPromises()
  expect(w.find('.result-card').exists()).toBe(false)
})
it('renders result text literally while still highlighting matches', async () => {
  globalSearch.mockResolvedValue(response('<img src=x onerror=alert(1)>沪深300'))
  const w = render('沪深300'); await flushPromises()
  expect(w.find('.card-title img').exists()).toBe(false)
  expect(w.get('.card-title').text()).toBe('<img src=x onerror=alert(1)>沪深300')
  expect(w.get('.card-title mark').text()).toBe('沪深300')
})
it('refreshes when the same query is submitted again', async () => {
  globalSearch.mockResolvedValueOnce(response('首次结果')).mockResolvedValueOnce(response('刷新结果'))
  const w = render('沪深300'); await flushPromises()
  await w.setProps({ refreshKey: 1 }); await flushPromises()
  expect(globalSearch).toHaveBeenCalledTimes(2)
  expect(w.text()).toContain('刷新结果')
})
