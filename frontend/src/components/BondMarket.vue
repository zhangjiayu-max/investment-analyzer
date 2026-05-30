<script setup>
import { ref, onMounted, onUnmounted, nextTick, computed } from 'vue'
import * as echarts from 'echarts'
import { getBondMarketTemperature } from '../api'

const chartRef = ref(null)
const loading = ref(true)
const error = ref(null)
const rawData = ref(null)
let chart = null

const current = computed(() => rawData.value?.current)
const history = computed(() => rawData.value?.history)

function getColorByDegree(degree) {
  if (degree <= 30) return '#3b82f6'
  if (degree <= 50) return '#60a5fa'
  if (degree <= 70) return '#f59e0b'
  return '#ef4444'
}

function getTempLabel(degree) {
  if (degree <= 30) return '低温（适合买入）'
  if (degree <= 50) return '偏低'
  if (degree <= 70) return '偏高'
  return '高温（谨慎买入）'
}

function renderChart(data) {
  if (!chartRef.value) return
  if (chart) chart.dispose()

  chart = echarts.init(chartRef.value)

  const dates = data.map(d => d.date)
  const yields = data.map(d => (parseFloat(d.yield) * 100).toFixed(2))
  const degrees = data.map(d => d.degree)

  const option = {
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(255,255,255,0.95)',
      borderColor: 'rgba(255, 255, 255, 0.1)',
      borderWidth: 1,
      textStyle: { color: '#1f2937', fontSize: 13 },
      formatter(params) {
        const date = params[0].axisValue
        let html = `<div style="font-weight:600;margin-bottom:4px">${date}</div>`
        params.forEach(p => {
          html += `<div style="display:flex;align-items:center;gap:6px">
            <span style="width:8px;height:8px;border-radius:50%;background:${p.color}"></span>
            ${p.seriesName}: <b>${p.value}${p.seriesName === '收益率' ? '%' : '°'}</b>
          </div>`
        })
        return html
      },
    },
    legend: {
      data: ['收益率', '债市温度'],
      top: 4,
      right: 16,
      textStyle: { color: '#9aa0a6', fontSize: 12 },
    },
    grid: {
      left: 56,
      right: 56,
      top: 48,
      bottom: 48,
    },
    dataZoom: [
      {
        type: 'inside',
        start: data.length > 120 ? Math.max(0, (1 - 120 / data.length) * 100) : 0,
        end: 100,
      },
      {
        type: 'slider',
        start: data.length > 120 ? Math.max(0, (1 - 120 / data.length) * 100) : 0,
        end: 100,
        height: 20,
        bottom: 8,
        borderColor: 'transparent',
        backgroundColor: 'rgba(255, 255, 255, 0.06)',
        fillerColor: 'rgba(99,102,241,0.15)',
        handleStyle: { color: '#c9a84c', borderColor: '#c9a84c' },
        textStyle: { color: '#5f6368', fontSize: 10 },
      },
    ],
    xAxis: {
      type: 'category',
      data: dates,
      axisLine: { lineStyle: { color: 'rgba(255, 255, 255, 0.1)' } },
      axisTick: { show: false },
      axisLabel: {
        color: '#5f6368',
        fontSize: 11,
        formatter(v) { return v.substring(0, 7) },
      },
    },
    yAxis: [
      {
        type: 'value',
        name: '收益率(%)',
        nameTextStyle: { color: '#5f6368', fontSize: 11, padding: [0, 40, 0, 0] },
        position: 'left',
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { lineStyle: { color: 'rgba(255, 255, 255, 0.06)', type: 'dashed' } },
        axisLabel: {
          color: '#5f6368',
          fontSize: 11,
          formatter: '{value}%',
        },
        scale: true,
      },
      {
        type: 'value',
        name: '温度',
        nameTextStyle: { color: '#5f6368', fontSize: 11, padding: [0, 0, 0, 40] },
        position: 'right',
        min: 0,
        max: 100,
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { show: false },
        axisLabel: { color: '#5f6368', fontSize: 11 },
      },
    ],
    visualMap: {
      show: false,
      seriesIndex: 1,
      dimension: 1,
      pieces: [
        { min: 0, max: 30, color: '#3b82f6' },
        { min: 30, max: 50, color: '#60a5fa' },
        { min: 50, max: 70, color: '#f59e0b' },
        { min: 70, max: 100, color: '#ef4444' },
      ],
    },
    series: [
      {
        name: '收益率',
        type: 'line',
        data: yields,
        yAxisIndex: 0,
        smooth: true,
        symbol: 'none',
        lineStyle: { width: 2, color: '#c9a84c' },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(99,102,241,0.25)' },
            { offset: 1, color: 'rgba(99,102,241,0.02)' },
          ]),
        },
      },
      {
        name: '债市温度',
        type: 'line',
        data: degrees,
        yAxisIndex: 1,
        smooth: true,
        symbol: 'none',
        lineStyle: { width: 1.5, type: 'dashed' },
      },
    ],
  }

  chart.setOption(option)

  // Mark area for temperature zones
  chart.setOption({
    series: [
      {
        markArea: {
          silent: true,
          data: [
            [{ yAxis: 0, itemStyle: { color: 'rgba(59,130,246,0.04)' } }, { yAxis: 30 }],
            [{ yAxis: 70, itemStyle: { color: 'rgba(239,68,68,0.04)' } }, { yAxis: 100 }],
          ],
        },
      },
      {},
    ],
  })
}

async function fetchData() {
  loading.value = true
  error.value = null
  try {
    const { data } = await getBondMarketTemperature()
    rawData.value = data
    loading.value = false
    await nextTick()
    renderChart(data.history)
  } catch (e) {
    error.value = e.response?.data?.detail || e.message || '加载失败'
    loading.value = false
  }
}

function handleResize() {
  chart?.resize()
}

onMounted(() => {
  fetchData()
  window.addEventListener('resize', handleResize)
})

onUnmounted(() => {
  chart?.dispose()
  window.removeEventListener('resize', handleResize)
})
</script>

<template>
  <div class="bond-market">
    <!-- Header -->
    <div class="bond-header">
      <div>
        <h2 class="page-title">债市市场温度</h2>
        <p class="page-desc">数据来源：有知有行 · 10年期国债收益率与债市温度</p>
      </div>
      <a
        href="https://youzhiyouxing.cn/data/macro"
        target="_blank"
        rel="noopener noreferrer"
        class="btn-ghost open-link-btn"
      >
        <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"/>
        </svg>
        原站
      </a>
    </div>

    <!-- Current info card -->
    <div v-if="current" class="current-card card">
      <div class="current-grid">
        <div class="current-item">
          <span class="current-label">更新日期</span>
          <span class="current-value">{{ current.date }}</span>
        </div>
        <div class="current-item">
          <span class="current-label">债市温度</span>
          <span class="current-value temp-value" :style="{ color: getColorByDegree(current.temperature) }">
            {{ current.temperature }}°
            <span class="temp-tag">{{ getTempLabel(current.temperature) }}</span>
          </span>
        </div>
        <div class="current-item">
          <span class="current-label">10年期国债收益率</span>
          <span class="current-value rate-value">
            {{ (current.rate * 100).toFixed(2) }}%
          </span>
        </div>
      </div>
    </div>

    <!-- Chart -->
    <div class="chart-card card">
      <div ref="chartRef" class="chart-container"></div>
      <!-- Loading overlay -->
      <div v-if="loading" class="chart-overlay">
        <div class="spinner-large"></div>
        <p>加载债市数据...</p>
      </div>
      <!-- Error overlay -->
      <div v-if="error" class="chart-overlay">
        <svg width="48" height="48" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4.5c-.77-.833-2.694-.833-3.464 0L3.34 16.5c-.77.833.192 2.5 1.732 2.5z"/>
        </svg>
        <p>{{ error }}</p>
        <button class="btn-secondary" @click="fetchData">重试</button>
      </div>
    </div>

    <!-- Legend guide -->
    <div class="legend-guide card" v-if="!loading && !error">
      <h3 class="legend-title">温度解读</h3>
      <div class="legend-items">
        <div class="legend-item">
          <span class="legend-dot" style="background:#3b82f6"></span>
          <span class="legend-label">低温 (0-30°)</span>
          <span class="legend-hint">债券收益率高位，适合配置</span>
        </div>
        <div class="legend-item">
          <span class="legend-dot" style="background:#60a5fa"></span>
          <span class="legend-label">偏低 (30-50°)</span>
          <span class="legend-hint">收益率中高位，可适当配置</span>
        </div>
        <div class="legend-item">
          <span class="legend-dot" style="background:#f59e0b"></span>
          <span class="legend-label">偏高 (50-70°)</span>
          <span class="legend-hint">收益率中低位，注意风险</span>
        </div>
        <div class="legend-item">
          <span class="legend-dot" style="background:#ef4444"></span>
          <span class="legend-label">高温 (70-100°)</span>
          <span class="legend-hint">收益率低位，谨慎配置</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.bond-market {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.bond-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
}

.open-link-btn {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  font-size: 0.8rem;
  white-space: nowrap;
  padding: 0.4rem 0.75rem;
}

/* Current info card */
.current-card {
  padding: 1rem 1.25rem;
}

.current-grid {
  display: flex;
  gap: 2rem;
  flex-wrap: wrap;
}

.current-item {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.current-label {
  font-size: 0.75rem;
  color: var(--color-text-muted);
}

.current-value {
  font-size: 1.25rem;
  font-weight: 700;
  color: var(--color-text-primary);
}

.temp-value {
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
}

.temp-tag {
  font-size: 0.75rem;
  font-weight: 500;
  opacity: 0.8;
}

.rate-value {
  color: var(--color-primary-600);
}

/* Chart */
.chart-card {
  padding: 1rem;
  min-height: 480px;
  position: relative;
}

.chart-container {
  width: 100%;
  height: 500px;
}

.chart-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.75rem;
  color: var(--color-text-muted);
  font-size: 0.9rem;
  background: var(--color-bg-card);
  border-radius: var(--radius-lg);
  z-index: 10;
}

.chart-overlay svg {
  color: var(--color-text-muted);
  opacity: 0.5;
}


/* Legend guide */
.legend-guide {
  padding: 1rem 1.25rem;
}

.legend-title {
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--color-text-secondary);
  margin: 0 0 0.75rem 0;
}

.legend-items {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 0.5rem;
}

.legend-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.8rem;
}

.legend-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
}

.legend-label {
  font-weight: 500;
  color: var(--color-text-primary);
  white-space: nowrap;
}

.legend-hint {
  color: var(--color-text-muted);
  font-size: 0.75rem;
}

@media (max-width: 640px) {
  .bond-header {
    flex-direction: column;
  }

  .current-grid {
    flex-direction: column;
    gap: 0.75rem;
  }

  .chart-container {
    height: 300px;
  }

  .legend-items {
    grid-template-columns: 1fr;
  }
}
</style>
