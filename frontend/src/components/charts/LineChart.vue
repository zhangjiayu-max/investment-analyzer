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
    // P0 优化（2026-08-02）：统一三角形样式 + 金额标签 + 主题色，消除硬编码
    if (i === 0 && props.markPoints.length) {
      const labelBg = isDark.value ? 'rgba(13,18,32,0.92)' : 'rgba(255,255,255,0.95)'
      config.markPoint = {
        symbol: 'triangle',
        symbolSize: 16,
        data: props.markPoints.map(p => {
          const isBuy = p.type === 'buy'
          // 统一主题色：买入=profit红、卖出=loss绿（红涨绿跌中国市场惯例）
          const pointColor = isBuy ? theme.value.colors.profit : theme.value.colors.loss
          return {
            name: isBuy ? '买入' : '卖出',
            coord: [p.date, p.yValue != null ? p.yValue : p.price],
            value: p.amount || '',
            symbol: 'triangle',
            symbolRotate: isBuy ? 0 : 180,       // ▲朝上=买入、▼朝下=卖出
            symbolOffset: isBuy ? [0, -8] : [0, 8],
            itemStyle: {
              color: pointColor,
              borderColor: '#ffffff',
              borderWidth: 2,
            },
            label: {
              show: !!p.amount,
              position: isBuy ? 'top' : 'bottom',
              distance: 4,
              formatter: `¥${(p.amount || 0).toLocaleString()}`,
              color: pointColor,
              fontSize: 10,
              fontWeight: 'bold',
              backgroundColor: labelBg,
              borderColor: pointColor + '40',
              borderWidth: 1,
              padding: [2, 5],
              borderRadius: 3,
            },
            tooltip: {
              formatter: () => {
                const shareStr = p.shares ? `${p.shares.toLocaleString()}份` : ''
                const amtStr = p.amount ? `¥${p.amount.toLocaleString()}` : ''
                return `${isBuy ? '🔴 买入' : '🟢 卖出'}<br/>日期: ${p.date}<br/>价格: ${p.price}<br/>${shareStr}${shareStr && amtStr ? ' | ' : ''}${amtStr}`
              },
            },
          }
        }),
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
