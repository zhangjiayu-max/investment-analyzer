<!-- 通用仪表盘：用于市场温度、估值温度等 0-100 指标 -->
<script setup>
import { ref, computed } from 'vue'
import { useLazyChart } from '../../composables/useLazyChart'
import { useChartTheme } from '../../composables/useChartTheme'

const props = defineProps({
  value: { type: Number, default: 0 },
  title: { type: String, default: '' },
  min: { type: Number, default: 0 },
  max: { type: Number, default: 100 },
  unit: { type: String, default: '' },
  segments: {
    type: Array,
    default: () => [
      { from: 0, to: 20, color: '#3b82f6' },
      { from: 20, to: 40, color: '#60a5fa' },
      { from: 40, to: 60, color: '#34d399' },
      { from: 60, to: 80, color: '#f59e0b' },
      { from: 80, to: 100, color: '#ef4444' },
    ],
  },
  height: { type: String, default: '200px' },
})

const chartRef = ref(null)
const { isDark } = useChartTheme()

function getOption(echarts) {
  return {
    backgroundColor: 'transparent',
    series: [
      {
        type: 'gauge',
        min: props.min,
        max: props.max,
        startAngle: 210,
        endAngle: -30,
        progress: { show: true, width: 18, roundCap: true },
        pointer: { show: true, length: '60%', width: 5, itemStyle: { color: 'auto' } },
        axisLine: {
          lineStyle: {
            width: 18,
            color: props.segments.map(s => [s.to / props.max, s.color]),
          },
        },
        axisTick: { show: false },
        splitLine: { show: false },
        axisLabel: {
          distance: 24,
          color: isDark.value ? '#9aa0a6' : '#64748b',
          fontSize: 11,
          formatter: v => v === props.min || v === props.max ? v : '',
        },
        title: {
          show: !!props.title,
          offsetCenter: [0, '70%'],
          fontSize: 13,
          color: isDark.value ? '#e8eaed' : '#0f172a',
          fontWeight: 600,
        },
        detail: {
          valueAnimation: true,
          fontSize: 28,
          fontWeight: 700,
          color: 'auto',
          offsetCenter: [0, '40%'],
          formatter: v => `${v}${props.unit}`,
        },
        data: [{ value: props.value, name: props.title }],
      },
    ],
  }
}

useLazyChart(chartRef, getOption, [() => props.value, isDark])
</script>

<template>
  <div ref="chartRef" :style="{ height, width: '100%' }"></div>
</template>
