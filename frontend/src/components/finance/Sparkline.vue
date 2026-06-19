<script setup>
/**
 * Sparkline — 迷你走势线
 *
 * 纯 SVG 实现，无依赖。用于列表行、卡片角标的迷你趋势展示。
 *
 * 用法：<Sparkline :data="[1,2,3,2,4]" /> 或 <Sparkline :data="navs" color="profit" />
 */
import { computed } from 'vue'

const props = defineProps({
  data: { type: Array, default: () => [] },
  width: { type: [Number, String], default: 60 },
  height: { type: [Number, String], default: 20 },
  color: { type: String, default: 'auto' }, // auto | profit | loss | primary
  strokeWidth: { type: [Number, String], default: 1.5 },
  fill: { type: Boolean, default: true }, // 是否填充渐变
})

const w = computed(() => Number(props.width) || 60)
const h = computed(() => Number(props.height) || 20)

const points = computed(() => {
  const d = props.data
  if (!d || d.length < 2) return []
  const min = Math.min(...d)
  const max = Math.max(...d)
  const range = max - min || 1
  const stepX = w.value / (d.length - 1)
  return d.map((v, i) => {
    const x = i * stepX
    const y = h.value - ((v - min) / range) * (h.value - 2) - 1
    return { x, y }
  })
})

const polylineStr = computed(() => points.value.map(p => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' '))
const areaPath = computed(() => {
  if (!points.value.length) return ''
  const pts = points.value
  let path = `M ${pts[0].x.toFixed(1)},${h.value} L ${pts[0].x.toFixed(1)},${pts[0].y.toFixed(1)}`
  for (let i = 1; i < pts.length; i++) path += ` L ${pts[i].x.toFixed(1)},${pts[i].y.toFixed(1)}`
  path += ` L ${pts[pts.length - 1].x.toFixed(1)},${h.value} Z`
  return path
})

const isUp = computed(() => {
  const d = props.data
  return d.length >= 2 && d[d.length - 1] >= d[0]
})

const colorVar = computed(() => {
  if (props.color === 'profit') return 'var(--color-profit)'
  if (props.color === 'loss') return 'var(--color-loss)'
  if (props.color === 'primary') return 'var(--color-primary)'
  return isUp.value ? 'var(--color-profit)' : 'var(--color-loss)'
})

const gradId = computed(() => 'spark-' + Math.random().toString(36).slice(2, 9))
</script>

<template>
  <svg :width="w" :height="h" :viewBox="`0 0 ${w} ${h}`" class="sparkline" v-if="points.length">
    <defs>
      <linearGradient :id="gradId" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" :stop-color="colorVar" stop-opacity="0.25" />
        <stop offset="100%" :stop-color="colorVar" stop-opacity="0" />
      </linearGradient>
    </defs>
    <path v-if="fill" :d="areaPath" :fill="`url(#${gradId})`" />
    <polyline :points="polylineStr" fill="none" :stroke="colorVar" :stroke-width="strokeWidth" stroke-linecap="round" stroke-linejoin="round" />
  </svg>
</template>

<style scoped>
.sparkline { display: inline-block; vertical-align: middle; flex-shrink: 0; }
</style>
