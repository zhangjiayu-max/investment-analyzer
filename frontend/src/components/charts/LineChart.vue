<!-- 通用折线图：用于收益曲线、净值走势、涨跌幅等 -->
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
  changeMode: { type: Boolean, default: false },  // 涨跌幅模式：红涨绿跌+零轴+渐变
})

const chartRef = ref(null)
const { theme, isDark, getTooltipOpts, getGridOpts, getCategoryAxis, getValueAxis, getDataZoomOpts } = useChartTheme()

function getOption(echarts) {
  const colors = theme.value.colors.series
  const profitColor = theme.value.colors.profit   // 红 #dc2626
  const lossColor = theme.value.colors.loss       // 绿 #059669
  const labelBg = isDark.value ? 'rgba(13,18,32,0.92)' : 'rgba(255,255,255,0.95)'

  const seriesConfig = props.series.map((s, i) => {
    const color = s.color || colors[i % colors.length]
    const isFirst = i === 0
    const useChangeColor = props.changeMode && isFirst  // 仅第一条线用涨跌色

    const config = {
      name: s.name,
      type: s.type || 'line',
      yAxisIndex: s.yAxisIndex || 0,
      data: s.data,
      smooth: props.smooth,
      symbol: 'circle',
      symbolSize: 4,
      lineStyle: { width: 2, color: useChangeColor ? profitColor : color },
      itemStyle: { color: useChangeColor ? profitColor : color },
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

    // 涨跌幅模式：用 visualMap 分段着色（>0红，<0绿）+ 渐变填充
    if (useChangeColor) {
      // 渐变填充：上部红色渐变，下部绿色渐变
      config.areaStyle = {
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: profitColor + '30' },     // 顶部淡红
          { offset: 0.5, color: profitColor + '08' },   // 中部接近透明
          { offset: 1, color: lossColor + '30' },       // 底部淡绿
        ]),
      }
    }

    // 买卖点标记（仅第一条线）
    // P0 优化（2026-08-02）：统一三角形样式 + 金额标签 + 主题色，消除硬编码
    if (isFirst && props.markPoints.length) {
      config.markPoint = {
        symbol: 'triangle',
        symbolSize: 16,
        data: props.markPoints.map(p => {
          const isBuy = p.type === 'buy'
          // 统一主题色：买入=profit红、卖出=loss绿（红涨绿跌中国市场惯例）
          const pointColor = isBuy ? profitColor : lossColor
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

  // 涨跌幅模式：添加零轴 markLine（0%基准线）
  const zeroAxisLine = props.changeMode ? [{
    yAxis: 0,
    label: { formatter: '0%', color: theme.value.textColor, fontSize: 10, fontFamily: theme.value.fontMono },
    lineStyle: { color: theme.value.colors.warning, type: 'solid', width: 1, opacity: 0.6 },
  }] : []

  // 合并 markLine 到第一条 series
  if (zeroAxisLine.length && seriesConfig.length) {
    const firstSeries = seriesConfig[0]
    if (firstSeries.markLine && firstSeries.markLine.data) {
      firstSeries.markLine.data.push(...zeroAxisLine)
    } else {
      firstSeries.markLine = {
        silent: true,
        symbol: 'none',
        lineStyle: { type: 'dashed', width: 1 },
        data: zeroAxisLine,
      }
    }
  }

  const yAxes = props.yNames.map((name, i) =>
    getValueAxis(name, {
      position: i === 0 ? 'left' : 'right',
      ...(i > 0 ? { splitLine: { show: false } } : {}),
      // 涨跌幅模式：Y轴用百分比格式
      ...(props.changeMode && i === 0 ? {
        axisLabel: {
          color: theme.value.textColor,
          fontSize: 11,
          fontFamily: theme.value.fontMono,
          formatter: (val) => (val > 0 ? '+' : '') + val.toFixed(1) + '%',
        },
      } : {}),
    })
  )

  // 涨跌幅模式：visualMap 分段着色（>0红，<=0绿）
  const visualMap = props.changeMode ? {
    show: false,
    pieces: [
      { gt: 0, color: profitColor },      // >0 红色
      { lte: 0, color: lossColor },       // <=0 绿色
    ],
    seriesIndex: 0,
    type: 'piecewise',
  } : undefined

  return {
    backgroundColor: 'transparent',
    tooltip: {
      ...getTooltipOpts(),
      trigger: 'axis',
      triggerOn: props.clickLock ? 'click' : 'mousemove',
      alwaysShowContent: props.clickLock,
      // 涨跌幅模式：tooltip 显示涨跌幅+净值
      formatter: props.changeMode ? (params) => {
        if (!Array.isArray(params) || !params.length) return ''
        const p = params[0]
        const val = typeof p.value === 'number' ? p.value : (typeof p.value === 'object' && p.value !== null ? p.value[1] : 0)
        const sign = val > 0 ? '+' : ''
        const color = val > 0 ? profitColor : (val < 0 ? lossColor : theme.value.textColor)
        return `<div style="font-size:12px">${p.axisValueLabel}<br/><span style="color:${color};font-weight:bold">${sign}${Number(val).toFixed(2)}%</span></div>`
      } : undefined,
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
    ...(visualMap ? { visualMap } : {}),
  }
}

useLazyChart(chartRef, getOption, [() => props.dates, () => props.series, isDark, () => props.changeMode])
</script>

<template>
  <div ref="chartRef" :style="{ height, width: '100%' }"></div>
</template>
