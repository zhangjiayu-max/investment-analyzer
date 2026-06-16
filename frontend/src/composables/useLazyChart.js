/**
 * Composable for lazy-loading echarts and managing chart lifecycle.
 * Replaces `import * as echarts from 'echarts'` with dynamic import,
 * reducing the initial bundle by ~1MB.
 */
import { ref, onMounted, onUnmounted, watch } from 'vue'

let echartsModule = null
let echartsLoadPromise = null

async function loadEcharts() {
  if (echartsModule) return echartsModule
  if (echartsLoadPromise) return echartsLoadPromise

  echartsLoadPromise = import('echarts').then(mod => {
    echartsModule = mod
    return mod
  }).catch(err => {
    echartsLoadPromise = null
    throw err
  })

  return echartsLoadPromise
}

export function useLazyChart(chartRef, optionFn, deps = []) {
  let chart = null

  const loading = ref(true)
  const error = ref(null)

  async function initChart() {
    if (!chartRef.value) return
    try {
      const echarts = await loadEcharts()
      loading.value = false
      if (chart) chart.dispose()

      // Detect dark mode from parent element or document
      const isDark = document.documentElement.classList.contains('dark')
      chart = echarts.init(chartRef.value, isDark ? 'dark' : null)
      chart.setOption(optionFn(echarts))
    } catch (e) {
      error.value = e
      loading.value = false
    }
  }

  async function updateChart() {
    if (!chart) return
    try {
      const echarts = echartsModule || await loadEcharts()
      chart.setOption(optionFn(echarts))
    } catch (_) {}
  }

  function handleResize() {
    chart?.resize()
  }

  onMounted(() => {
    initChart()
    window.addEventListener('resize', handleResize)
  })

  onUnmounted(() => {
    chart?.dispose()
    chart = null
    window.removeEventListener('resize', handleResize)
  })

  // Watch dependencies
  if (deps.length) {
    watch(deps, () => {
      updateChart()
    }, { deep: true })
  }

  return { loading, error, updateChart, handleResize }
}
