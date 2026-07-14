<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { getChart } from '../../api'
import { isDark } from '../../composables/useTheme'

const props = defineProps({
  symbol: String,
  name: String,
})

const chartRef = ref(null)
const loading = ref(true)
let chartInstance = null
let resizeObserver = null
let echartsModule = null

onMounted(async () => {
  // Load echarts and API data in parallel
  const [, plotlyData] = await Promise.all([
    import('echarts').then(m => { echartsModule = m }),
    getChart(props.symbol, 180).then(res => res.data).catch(e => {
      console.error('Chart load failed:', e)
      return null
    }),
  ])

  loading.value = false

  if (plotlyData?.data) {
    renderChart(plotlyData)
  }

  resizeObserver = new ResizeObserver(() => chartInstance?.resize())
  if (chartRef.value) resizeObserver.observe(chartRef.value)
})

onUnmounted(() => {
  chartInstance?.dispose()
  resizeObserver?.disconnect()
})

function calculateMA(data, period) {
  const result = []
  for (let i = 0; i < data.length; i++) {
    if (i < period - 1) {
      result.push(null)
    } else {
      let sum = 0
      for (let j = 0; j < period; j++) {
        sum += data[i - j]
      }
      result.push((sum / period).toFixed(2))
    }
  }
  return result
}

function renderChart(plotlyData) {
  if (!chartRef.value || !plotlyData?.data || !echartsModule) return

  const echarts = echartsModule
  chartInstance = echarts.init(chartRef.value, isDark.value ? 'dark' : null)

  const traces = plotlyData.data
  const candlestick = traces.find((t) => t.type === 'candlestick')
  const volume = traces.find((t) => t.type === 'bar' && t.yaxis === 'y2')

  if (!candlestick) return

  // Calculate MAs
  const closes = candlestick.close.map(Number)
  const ma5 = calculateMA(closes, 5)
  const ma10 = calculateMA(closes, 10)
  const ma20 = calculateMA(closes, 20)

  const bgColor = isDark.value ? '#0a0e1a' : '#ffffff'
  const textColor = isDark.value ? '#9aa0a6' : '#64748b'
  const gridColor = isDark.value ? 'rgba(255, 255, 255, 0.06)' : '#f1f5f9'

  const option = {
    backgroundColor: bgColor,
    title: {
      text: `${props.name} (${props.symbol})`,
      left: 'center',
      textStyle: {
        fontSize: 14,
        fontWeight: 700,
        fontFamily: "'Songti SC', 'STSong', 'SimSun', 'Noto Serif SC', serif",
        color: isDark.value ? '#e8eaed' : '#0f172a',
      },
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      backgroundColor: isDark.value ? 'rgba(13, 18, 32, 0.95)' : '#ffffff',
      borderColor: isDark.value ? 'rgba(255, 255, 255, 0.1)' : '#e2e8f0',
      textStyle: { color: isDark.value ? '#e8eaed' : '#0f172a', fontSize: 12 },
    },
    legend: {
      data: ['K线', 'MA5', 'MA10', 'MA20'],
      bottom: 30,
      textStyle: { color: textColor, fontSize: 11 },
      itemWidth: 14,
      itemHeight: 8,
    },
    grid: [
      { left: '10%', right: '5%', top: '12%', height: '50%' },
      { left: '10%', right: '5%', top: '72%', height: '15%' },
    ],
    xAxis: [
      {
        type: 'category',
        data: candlestick.x,
        gridIndex: 0,
        axisLabel: { show: false },
        axisLine: { lineStyle: { color: gridColor } },
        splitLine: { show: false },
      },
      {
        type: 'category',
        data: volume?.x || candlestick.x,
        gridIndex: 1,
        axisLabel: { fontSize: 10, color: textColor },
        axisLine: { lineStyle: { color: gridColor } },
        splitLine: { show: false },
      },
    ],
    yAxis: [
      {
        scale: true,
        gridIndex: 0,
        splitLine: { lineStyle: { color: gridColor } },
        axisLabel: { color: textColor, fontSize: 10 },
        axisLine: { show: false },
      },
      {
        scale: true,
        gridIndex: 1,
        splitNumber: 2,
        splitLine: { lineStyle: { color: gridColor } },
        axisLabel: { color: textColor, fontSize: 10 },
        axisLine: { show: false },
      },
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start: 60, end: 100 },
      {
        show: true,
        xAxisIndex: [0, 1],
        type: 'slider',
        bottom: 5,
        height: 15,
        start: 60,
        end: 100,
        borderColor: gridColor,
        fillerColor: isDark.value ? 'rgba(99,102,241,0.15)' : 'rgba(99,102,241,0.1)',
        handleStyle: { color: '#c9a84c' },
        textStyle: { color: textColor },
      },
    ],
    series: [
      {
        name: 'K线',
        type: 'candlestick',
        data: candlestick.x.map((_, i) => [
          candlestick.open[i],
          candlestick.close[i],
          candlestick.low[i],
          candlestick.high[i],
        ]),
        xAxisIndex: 0,
        yAxisIndex: 0,
        itemStyle: {
          color: '#ef4444',
          color0: '#22c55e',
          borderColor: '#ef4444',
          borderColor0: '#22c55e',
        },
      },
      {
        name: 'MA5',
        type: 'line',
        data: ma5,
        xAxisIndex: 0,
        yAxisIndex: 0,
        smooth: true,
        lineStyle: { width: 1, color: '#f59e0b' },
        symbol: 'none',
      },
      {
        name: 'MA10',
        type: 'line',
        data: ma10,
        xAxisIndex: 0,
        yAxisIndex: 0,
        smooth: true,
        lineStyle: { width: 1, color: '#3b82f6' },
        symbol: 'none',
      },
      {
        name: 'MA20',
        type: 'line',
        data: ma20,
        xAxisIndex: 0,
        yAxisIndex: 0,
        smooth: true,
        lineStyle: { width: 1, color: '#8b5cf6' },
        symbol: 'none',
      },
      ...(volume
        ? [
            {
              name: '成交量',
              type: 'bar',
              data: volume.y.map((v, i) => ({
                value: v,
                itemStyle: {
                  color: candlestick.close[i] >= candlestick.open[i] ? 'rgba(239,68,68,0.5)' : 'rgba(34,197,94,0.5)',
                },
              })),
              xAxisIndex: 1,
              yAxisIndex: 1,
            },
          ]
        : []),
    ],
  }

  chartInstance.setOption(option)
}
</script>

<template>
  <div class="chart-card card editorial-card">
    <div v-if="loading" class="chart-loading">
      <div class="spinner-lg"></div>
    </div>
    <div v-else ref="chartRef" class="chart-container"></div>
  </div>
</template>

<style scoped>
.chart-card {
  padding: 0.75rem;
  min-height: 300px;
  position: relative;
  transition: transform var(--transition-fast), box-shadow var(--transition-normal), border-color var(--transition-normal);
}
.chart-card:hover {
  transform: var(--hover-lift);
  box-shadow: var(--shadow-glow);
}

.chart-loading {
  height: 300px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.spinner-lg {
  width: 24px;
  height: 24px;
  border: 3px solid var(--color-border);
  border-top-color: var(--color-primary-500);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

.chart-container {
  height: 350px;
  width: 100%;
}

/* 移动端适配 */
@media (max-width: 768px) {
  .chart-card {
    min-height: 200px;
    padding: 0.5rem;
  }

  .chart-loading {
    height: 200px;
  }

  .chart-container {
    height: 250px;
  }
}
</style>
