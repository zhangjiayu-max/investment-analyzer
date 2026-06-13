/**
 * RAG 命中测试 composable — 复用于个人文档、知识库、作者文档等页面
 *
 * 用法：
 *   const { testQuery, testResults, testLoading, elapsedMs, runTest, resetTest } = useRagTest()
 *   await runTest('资产配置', { contentTypes: ['book'], limit: 5 })
 */
import { ref } from 'vue'
import { testRagSearch } from '../api'

export function useRagTest() {
  const testQuery = ref('')
  const testResults = ref(null)
  const testLoading = ref(false)
  const elapsedMs = ref(0)

  /**
   * 执行命中测试
   * @param {string} query - 查询词（为空则用 testQuery）
   * @param {object} options - 可选参数
   * @param {string[]} options.contentTypes - 内容类型过滤，如 ['book']、['author_article']
   * @param {number} options.limit - 返回结果数，默认 5
   * @param {boolean} options.useRewrite - 是否启用 Query Rewrite
   */
  async function runTest(query, options = {}) {
    const q = query || testQuery.value
    if (!q.trim()) return

    testLoading.value = true
    testResults.value = null
    elapsedMs.value = 0

    const t0 = Date.now()
    try {
      const { data } = await testRagSearch(
        q,
        options.limit || 5,
        options.contentTypes || null,
        options.useRewrite || false,
      )
      elapsedMs.value = Date.now() - t0
      testResults.value = data.result || data
    } catch (e) {
      elapsedMs.value = Date.now() - t0
      throw e
    } finally {
      testLoading.value = false
    }
  }

  function resetTest() {
    testQuery.value = ''
    testResults.value = null
    elapsedMs.value = 0
  }

  return {
    testQuery,
    testResults,
    testLoading,
    elapsedMs,
    runTest,
    resetTest,
  }
}
