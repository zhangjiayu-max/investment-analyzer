<!-- 通用饼图/环形图：用于持仓分布、行业配置等 -->
<script setup>
import { ref, watch, onMounted, onUnmounted, computed } from 'vue'
import * as echarts from 'echarts'
import { useChartTheme } from '../../composables/useChartTheme'

const props = defineProps({
  data: { type: Array, default: () => [] },
  title: { type: String, default: '' },
  innerRadius: { type: Number, default: 45 },
  outerRadius: { type: Number, default: 75 },
  showLabel: { type: Boolean, default: true },
  legendPosition: { type: String, default: 'right' },
  height: { type: String, default: '280px' },
})

const chartRef = ref(null)
let chart = null
const { theme, isDark } = useChartTheme()

function getTooltipOpts() {
  return {
    backgroundColor: isDark.value ? 'rgba(13,18,32,0.95)' : '#ffffff',
    borderColor: theme.value.borderColor,
    borderWidth: 1,
    textStyle: { color: theme.value.titleColor, fontSize: 12 },
  }
}

const option = computed(() => {
  const colors = theme.value.colors.series
  const seriesData = props.data.map((d, i) => ({
    name: d.name,
    value: d.value,
    itemStyle: { color: d.color || colors[i % colors.length] },
  }))

  const legend = props.legendPosition === 'none' ? { show: false } : {
    show: true,
    orient: props.legendPosition === 'right' ? 'vertical' : 'horizontal',
    [props.legendPosition === 'right' ? 'right' : 'bottom']: 8,
    [props.legendPosition === 'right' ? 'top' : 'left']: 'center',
    textStyle: { color: theme.value.textColor, fontSize: 11 },
    icon: 'circle',
    itemWidth: 8,
    itemHeight: 8,
    itemGap: 8,
  }

  return {
    backgroundColor: 'transparent',
    title: props.title ? {
      text: props.title,
      left: 'center',
      top: 8,
      textStyle: { color: theme.value.titleColor, fontSize: 13, fontWeight: 600 },
    } : undefined,
    tooltip: {
      ...getTooltipOpts(),
      trigger: 'item',
      formatter: p => `<b>${p.name}</b><br/>金额: ¥${p.value.toLocaleString()}<br/>占比: ${p.percent}%`,
    },
    legend,
    series: [{
      type: 'pie',
      radius: [`${props.innerRadius}%`, `${props.outerRadius}%`],
      center: props.legendPosition === 'right' ? ['40%', '50%'] : ['50%', '55%'],
      avoidLabelOverlap: true,
      itemStyle: { borderRadius: 6, borderColor: isDark.value ? '#1e1e22' : '#ffffff', borderWidth: 2 },
      label: {
        show: props.showLabel,
        formatter: '{b}\n{d}%',
        color: theme.value.textColor,
        fontSize: 11,
      },
      emphasis: {
        label: { show: true, fontSize: 13, fontWeight: 'bold' },
        itemStyle: { shadowBlur: 10, shadowColor: 'rgba(0,0,0,0.2)' },
      },
      data: seriesData,
    }],
  }
})

function renderChart() {
  if (!chartRef.value) return
  if (chart) chart.dispose()
  chart = echarts.init(chartRef.value, isDark.value ? 'dark' : null)
  chart.setOption(option.value)
}

function handleResize() { chart?.resize() }

watch(() => [() => props.data, isDark.value], () => {
  if (chart) chart.setOption(option.value)
}, { deep: true })

onMounted(() => {
  renderChart()
  window.addEventListener('resize', handleResize)
})

onUnmounted(() => {
  chart?.dispose()
  window.removeEventListener('resize', handleResize)
})
</script>

<template>
  <div ref="chartRef" :style="{ height, width: '100%' }"></div>
</template>
