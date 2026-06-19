<script setup>
/**
 * PercentileBar — 估值百分位条
 *
 * 5 档色带（极度低估绿 → 合理灰 → 高估红），标记线指向 value。
 * 用于估值表格、估值详情。
 *
 * 用法：<PercentileBar :value="35" /> 或 <PercentileBar :value="0.35" :raw="false" />
 */
import { computed } from 'vue'

const props = defineProps({
  value: { type: Number, default: 0 },
  raw: { type: Boolean, default: true }, // true: 0-100, false: 0-1
  showLabel: { type: Boolean, default: true },
  height: { type: [Number, String], default: 6 },
  showScale: { type: Boolean, default: false },
})

const pct = computed(() => {
  const v = props.raw ? props.value : props.value * 100
  return Math.max(0, Math.min(100, v))
})

// 5 档色带
const bands = [
  { from: 0, to: 20, color: 'var(--color-loss-strong)', label: '极度低估' },
  { from: 20, to: 40, color: 'var(--color-loss)', label: '低估' },
  { from: 40, to: 60, color: 'var(--color-text-muted)', label: '合理' },
  { from: 60, to: 80, color: 'var(--color-warning)', label: '偏高' },
  { from: 80, to: 100, color: 'var(--color-profit)', label: '高估' },
]

const statusLabel = computed(() => {
  const v = pct.value
  for (const b of bands) if (v >= b.from && v < b.to) return b.label
  return v >= 80 ? '高估' : '合理'
})
const statusColor = computed(() => {
  const v = pct.value
  for (const b of bands) if (v >= b.from && v < b.to) return b.color
  return v >= 80 ? 'var(--color-profit)' : 'var(--color-text-muted)'
})
</script>

<template>
  <div class="pct-bar-wrap">
    <div class="pct-bar" :style="{ height: Number(height) + 'px' }">
      <div class="pct-band" v-for="b in bands" :key="b.from"
        :style="{ left: b.from + '%', width: (b.to - b.from) + '%', background: b.color }" />
      <div class="pct-marker" :style="{ left: pct + '%' }" />
    </div>
    <span v-if="showLabel" class="pct-label" :style="{ color: statusColor }">
      {{ pct.toFixed(0) }}% · {{ statusLabel }}
    </span>
  </div>
</template>

<style scoped>
.pct-bar-wrap { display: inline-flex; align-items: center; gap: 0.4rem; }
.pct-bar { position: relative; width: 80px; border-radius: 3px; overflow: hidden; display: flex; }
.pct-band { position: absolute; top: 0; bottom: 0; opacity: 0.35; }
.pct-marker {
  position: absolute; top: -2px; bottom: -2px; width: 2px;
  background: var(--color-text-primary);
  border-radius: 1px;
  box-shadow: 0 0 0 1px var(--color-bg-card);
}
.pct-label { font-size: 0.72rem; font-weight: 600; font-variant-numeric: tabular-nums; white-space: nowrap; }
</style>
