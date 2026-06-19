<script setup>
/**
 * ThermoMeter — 估值温度计
 *
 * 横向温度计，冷蓝→绿→琥珀→红，标记当前值。
 * 用于估值概览、市场温度展示，替代过重的 GaugeChart。
 *
 * 用法：<ThermoMeter :value="65" label="白酒估值温度" />
 */
import { computed } from 'vue'

const props = defineProps({
  value: { type: Number, default: 50 },     // 0-100
  min: { type: Number, default: 0 },
  max: { type: Number, default: 100 },
  label: { type: String, default: '' },
  showValue: { type: Boolean, default: true },
  height: { type: [Number, String], default: 8 },
})

const pct = computed(() => {
  const range = props.max - props.min || 1
  return Math.max(0, Math.min(100, ((props.value - props.min) / range) * 100))
})

// 温度等级
const level = computed(() => {
  const v = pct.value
  if (v < 20) return { label: '冰点', color: '#3b82f6' }
  if (v < 40) return { label: '偏冷', color: 'var(--color-loss)' }
  if (v < 60) return { label: '温和', color: 'var(--color-text-secondary)' }
  if (v < 80) return { label: '偏热', color: 'var(--color-warning)' }
  return { label: '过热', color: 'var(--color-profit)' }
})
</script>

<template>
  <div class="thermo-wrap">
    <div v-if="label" class="thermo-head">
      <span class="thermo-label">{{ label }}</span>
      <span v-if="showValue" class="thermo-val" :style="{ color: level.color }">{{ value.toFixed(0) }}° · {{ level.label }}</span>
    </div>
    <div class="thermo-track" :style="{ height: Number(height) + 'px' }">
      <div class="thermo-fill" :style="{ width: pct + '%', background: `linear-gradient(90deg, #3b82f6, var(--color-loss), var(--color-warning), var(--color-profit))` }" />
      <div class="thermo-marker" :style="{ left: pct + '%', background: level.color }" />
    </div>
  </div>
</template>

<style scoped>
.thermo-wrap { display: flex; flex-direction: column; gap: 0.3rem; }
.thermo-head { display: flex; justify-content: space-between; align-items: center; }
.thermo-label { font-size: 0.75rem; color: var(--color-text-secondary); font-weight: 500; }
.thermo-val { font-size: 0.75rem; font-weight: 600; font-variant-numeric: tabular-nums; }
.thermo-track {
  position: relative; width: 100%; border-radius: 4px;
  background: var(--color-bg-input); overflow: visible;
}
.thermo-fill {
  position: absolute; top: 0; left: 0; height: 100%;
  border-radius: 4px; opacity: 0.3;
  transition: width 0.6s cubic-bezier(0.22, 1, 0.36, 1);
}
.thermo-marker {
  position: absolute; top: -3px; bottom: -3px; width: 3px;
  border-radius: 2px;
  box-shadow: 0 0 0 1px var(--color-bg-card), 0 0 6px currentColor;
  transition: left 0.6s cubic-bezier(0.22, 1, 0.36, 1);
}
</style>
