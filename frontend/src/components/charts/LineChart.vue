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
  markPoints: { type: Array, default: () => [] },  // [{ type: 'buy'|'sell', date, price, shares, amount }]
  zoomable: { type: Boolean, default: true },
  zoomThreshold: { type: Number, default: 60 },
  height: { type: String, default: '300px' },
  clickLock: { type: Boolean, default: false },  // 点击锁定 tooltip
})

const chartRef = ref(null)
const { theme, isDark, getTooltipOpts, getGridOpts, getCategoryAxis, getValueAxis, getDataZoomOpts } = useChartTheme()

function getOption(echarts) {
  const colors = theme.value.colors.series
  const seriesConfig = props.series.map((s, i) => {
    const color = s.color || colors[i % colors.length]
    const config = {
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
          label: { formatter: ml.label, color: theme.value.textColor, fontSize: 10, fontFamily: theme.value.fontMono },
          lineStyle: { color: ml.color || theme.value.colors.warning },
        })),
      } : undefined,
    }

    // 买卖点标记（仅第一条线）
    if (i === 0 && props.markPoints.length) {
      config.markPoint = {
        symbol: 'pin',
        symbolSize: 28,
        label: { show: false },
        data: props.markPoints.map(p => ({
          name: p.type === 'buy' ? '买入' : '卖出',
          coord: [p.date, p.price],
          value: `¥${(p.amount || 0).toLocaleString()}`,
          symbol: p.type === 'buy' ? 'arrow' : 'arrow',
          symbolRotate: p.type === 'buy' ? 180 : 0,  // 买入箭头朝下指向价格线
          itemStyle: { color: p.type === 'buy' ? '#ef4444' : '#22c55e' },
          tooltip: {
            formatter: () => {
              const shareStr = p.shares ? `${p.shares.toLocaleString()}份` : ''
              const amtStr = p.amount ? `¥${p.amount.toLocaleString()}` : ''
              return `${p.type === 'buy' ? '🔴 买入' : '🟢 卖出'}<br/>日期: ${p.date}<br/>价格: ${p.price}<br/>${shareStr}${shareStr && amtStr ? ' | ' : ''}${amtStr}`
            },
          },
        })),
      }
    }

    return config
  })

  const yAxes = props.yNames.map((name, i) =>
    getValueAxis(name, { position: i === 0 ? 'left' : 'right', ...(i > 0 ? { splitLine: { show: false } } : {}) })
  )

  return {
    backgroundColor: 'transparent',
    tooltip: {
      ...getTooltipOpts(),
      trigger: 'axis',
      triggerOn: props.clickLock ? 'click' : 'mousemove',
      alwaysShowContent: props.clickLock,
    },
    axisPointer: {
      link: [{ xAxisIndex: 'all' }],
      label: { backgroundColor: isDark.value ? '#1e293b' : '#f8fafc' },
    },
    legend: {
      data: props.series.map(s => s.name),
      bottom: 0,
      textStyle: { color: theme.value.textColor, fontSize: 11, fontFamily: theme.value.fontMono },
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
