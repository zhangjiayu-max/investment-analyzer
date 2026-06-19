<script setup>
/**
 * AnimatedNumber — 数字滚动动画
 *
 * 值变化时用 requestAnimationFrame ease-out 插值滚动。
 * 用于汇总卡、行情价等关键数字展示。
 *
 * 用法：<AnimatedNumber :value="12345.67" format="money" /> 或 <AnimatedNumber :value="0.35" format="percent" />
 */
import { ref, watch, onMounted, onUnmounted } from 'vue'

const props = defineProps({
  value: { type: Number, default: 0 },
  duration: { type: Number, default: 600 },
  format: { type: String, default: 'plain' }, // plain | percent | money
  decimals: { type: Number, default: -1 }, // -1=自动
  mono: { type: Boolean, default: true },
  prefix: { type: String, default: '' },
  suffix: { type: String, default: '' },
})

const display = ref(0)
let rafId = null

function easeOutCubic(t) { return 1 - Math.pow(1 - t, 3) }

function animate(from, to) {
  const start = performance.now()
  const dur = props.duration
  function step(now) {
    const elapsed = now - start
    const t = Math.min(elapsed / dur, 1)
    display.value = from + (to - from) * easeOutCubic(t)
    if (t < 1) rafId = requestAnimationFrame(step)
  }
  if (rafId) cancelAnimationFrame(rafId)
  rafId = requestAnimationFrame(step)
}

function formatNum(v) {
  let decimals = props.decimals
  if (decimals < 0) {
    if (props.format === 'percent') decimals = 2
    else if (props.format === 'money') decimals = 2
    else decimals = Math.abs(v) >= 100 ? 0 : 2
  }
  let str = v.toFixed(decimals)
  // 千分位
  const parts = str.split('.')
  parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  str = parts.join('.')
  if (props.format === 'percent') str += '%'
  if (props.format === 'money') str = '¥' + str
  return props.prefix + str + props.suffix
}

onMounted(() => animate(0, props.value))
watch(() => props.value, (nv, ov) => animate(ov ?? 0, nv))
onUnmounted(() => { if (rafId) cancelAnimationFrame(rafId) })
</script>

<template>
  <span :class="{ 'num-mono': mono }">{{ formatNum(display) }}</span>
</template>

<style scoped>
.num-mono { font-family: var(--font-num-mono); font-variant-numeric: tabular-nums; }
</style>
