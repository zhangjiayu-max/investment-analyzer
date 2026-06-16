<!-- 通用折线图：用于收益曲线、净值走势等 -->
<script setup>
import { ref } from 'vue'
import { useLazyChart } from '../../composables/useLazyChart'
import { useChartTheme } from '../../composables/useChartTheme'

const props = defineProps({
  dates: { type: Array, default: () => [] },
  series: { type: Array, default: () => [] },
  yNames: { type: Array, default: () => [''] },
  area: { type: Boolean, default: true },
  smooth: { type: Boolean, default: true },
  markLines: { type: Array, default: () => [] },
  zoomable: { type: Boolean, default: true },
  zoomThreshold: { type: Number, default: 60 },
  height: { type: String, default: '300px' },
})

const chartRef = ref(null)
const { theme, isDark, getTooltipOpts, getGridOpts, getCategoryAxis, getValueAxis, getDataZoomOpts } = useChartTheme()

function getOption(echarts) {
  const colors = theme.value.colors.series
  const seriesConfig = props.series.map((s, i) => {
    const color = s.color || colors[i % colors.length]
    return {
      name: s.name,
      type: s.type || 'line',
      yAxisIndex: s.yAxisIndex || 0,
      data: s.data,
      smooth: props.smooth,
      symbol: 'circle',
      symbolSize: 4,
      lineStyle: { width: 2, color },
      itemStyle: { color },
      areaStyle: props.area ? {
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: color.replace(')', ',0.2)').replace('rgb', 'rgba') },
          { offset: 1, color: color.replace(')', ',0)').replace('rgb', 'rgba') },
        ]),
      } : undefined,
      markLine: props.markLines.length ? {
        silent: true,
        symbol: 'none',
        lineStyle: { type: 'dashed', width: 1 },
        data: props.markLines.map(ml => ({
          yAxis: ml.yAxis,
          label: { formatter: ml.label, color: theme.value.textColor, fontSize: 10 },
          lineStyle: { color: ml.color || theme.value.colors.warning },
        })),
      } : undefined,
    }
  })

  const yAxes = props.yNames.map((name, i) =>
    getValueAxis(name, { position: i === 0 ? 'left' : 'right', ...(i > 0 ? { splitLine: { show: false } } : {}) })
  )

  return {
    backgroundColor: 'transparent',
    tooltip: {
      ...getTooltipOpts(),
      trigger: 'axis',
    },
    legend: {
      data: props.series.map(s => s.name),
      bottom: 0,
      textStyle: { color: theme.value.textColor, fontSize: 11 },
    },
    grid: getGridOpts(),
    xAxis: getCategoryAxis(props.dates),
    yAxis: yAxes,
    dataZoom: props.zoomable ? getDataZoomOpts(props.dates.length, props.zoomThreshold) : undefined,
    series: seriesConfig,
  }
}

useLazyChart(chartRef, getOption, [() => props.dates, () => props.series, isDark])
</script>

<template>
  <div ref="chartRef" :style="{ height, width: '100%' }"></div>
</template>
