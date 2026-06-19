<script setup>
/**
 * StatusBadge — 统一状态徽章
 *
 * 四态：低估(绿)/合理(灰)/偏高(琥珀)/风险(红) + 涨(红)/跌(绿)
 * 替换各页零散 badge，统一视觉语言。
 *
 * 用法：<StatusBadge status="undervalued" /> 或 <StatusBadge status="profit" text="+3.5%" />
 */
import { computed } from 'vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  status: { type: String, required: true },
  // undervalued | fair | overvalued | risk | profit | loss
  text: { type: String, default: '' },
  size: { type: String, default: 'sm' }, // xs | sm
  icon: { type: String, default: '' }, // 可选图标 name
})

const STATUS_CONFIG = {
  undervalued: { label: '低估', color: 'var(--color-status-undervalued)', bg: 'var(--color-loss-bg)', border: 'var(--color-success-border)', icon: 'trending-down' },
  fair: { label: '合理', color: 'var(--color-status-fair)', bg: 'var(--color-bg-input)', border: 'var(--color-border)', icon: '' },
  overvalued: { label: '偏高', color: 'var(--color-status-overvalued)', bg: 'var(--color-warning-bg)', border: 'var(--color-warning-border)', icon: 'trending-up' },
  risk: { label: '风险', color: 'var(--color-status-risk)', bg: 'var(--color-danger-bg)', border: 'var(--color-danger-border)', icon: 'shield-alert' },
  profit: { label: '涨', color: 'var(--color-profit)', bg: 'var(--color-profit-bg)', border: 'var(--color-danger-border)', icon: 'arrow-up' },
  loss: { label: '跌', color: 'var(--color-loss)', bg: 'var(--color-loss-bg)', border: 'var(--color-success-border)', icon: 'arrow-down' },
}

const cfg = computed(() => STATUS_CONFIG[props.status] || STATUS_CONFIG.fair)
const displayText = computed(() => props.text || cfg.value.label)
const iconName = computed(() => props.icon || cfg.value.icon)
</script>

<template>
  <span class="status-badge" :class="`sb-${size}`"
    :style="{ color: cfg.color, background: cfg.bg, borderColor: cfg.border }">
    <Icon v-if="iconName" :name="iconName" :size="size === 'xs' ? 11 : 12" />
    <span>{{ displayText }}</span>
  </span>
</template>

<style scoped>
.status-badge {
  display: inline-flex;
  align-items: center;
  gap: 0.2rem;
  padding: 0.12rem 0.45rem;
  border-radius: 999px;
  font-size: 0.72rem;
  font-weight: 600;
  border: 1px solid transparent;
  line-height: 1.4;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
.sb-xs { font-size: 0.66rem; padding: 0.08rem 0.35rem; }
</style>
