<!-- 通用纯 SVG 饼图组件 — 无外部依赖 -->
<script setup>
import { ref, computed } from 'vue'

const props = defineProps({
  /** [{name: string, value: number, color?: string}] */
  data: { type: Array, default: () => [] },
  size: { type: Number, default: 200 },
  innerRadius: { type: Number, default: 0 },
  /** 图例位置: 'right' | 'bottom' | 'none' */
  legendPosition: { type: String, default: 'right' },
  /** 格式化函数 (value, percent, name) => string */
  formatTooltip: { type: Function, default: null },
})

const hoveredIndex = ref(-1)

const total = computed(() => props.data.reduce((s, d) => s + d.value, 0))

const COLORS = [
  '#c9a84c', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6',
  '#06b6d4', '#f97316', '#ec4899', '#14b8a6', '#84cc16',
  '#3b82f6', '#a855f7', '#f43f5e', '#22d3ee', '#eab308',
]

function colorAt(i, d) {
  return d.color || COLORS[i % COLORS.length]
}

const slices = computed(() => {
  if (total.value <= 0) return []
  const cx = props.size / 2
  const cy = props.size / 2
  const r = (props.size / 2) - 4
  const ir = props.innerRadius
  let cumulative = 0

  return props.data.map((d, i) => {
    const pct = d.value / total.value
    const angle = pct * 360
    const startRad = (cumulative - 90) * Math.PI / 180
    const endRad = (cumulative + angle - 90) * Math.PI / 180
    cumulative += angle

    if (angle >= 360) {
      return {
        ...d,
        index: i,
        path: `M ${cx},${cy - r} A ${r},${r} 0 1,1 ${cx - 0.01},${cy - r} Z`,
        innerPath: ir > 0 ? `M ${cx},${cy - ir} A ${ir},${ir} 0 1,0 ${cx + 0.01},${cy - ir} Z` : '',
        color: colorAt(i, d),
        percent: (pct * 100).toFixed(1),
      }
    }

    const x1 = cx + r * Math.cos(startRad)
    const y1 = cy + r * Math.sin(startRad)
    const x2 = cx + r * Math.cos(endRad)
    const y2 = cy + r * Math.sin(endRad)
    const largeArc = angle > 180 ? 1 : 0

    let path = `M ${cx},${cy} L ${x1},${y1} A ${r},${r} 0 ${largeArc},1 ${x2},${y2} Z`

    // 如果有内半径，挖空中心
    if (ir > 0) {
      const ix1 = cx + ir * Math.cos(startRad)
      const iy1 = cy + ir * Math.sin(startRad)
      const ix2 = cx + ir * Math.cos(endRad)
      const iy2 = cy + ir * Math.sin(endRad)
      const innerLargeArc = angle > 180 ? 1 : 0
      path = `M ${x1},${y1} A ${r},${r} 0 ${largeArc},1 ${x2},${y2} L ${ix2},${iy2} A ${ir},${ir} 0 ${innerLargeArc},0 ${ix1},${iy1} Z`
    }

    return {
      ...d,
      index: i,
      path,
      color: colorAt(i, d),
      percent: (pct * 100).toFixed(1),
    }
  })
})

const tooltipText = computed(() => {
  if (hoveredIndex.value < 0 || !slices.value[hoveredIndex.value]) return ''
  const s = slices.value[hoveredIndex.value]
  if (props.formatTooltip) return props.formatTooltip(s.value, s.percent, s.name)
  return `${s.name}: ${s.percent}%`
})

// 图例分列
const legendItems = computed(() => slices.value)
</script>

<template>
  <div class="simple-pie-wrap" :class="`legend-${legendPosition}`">
    <div class="pie-svg-wrap">
      <svg :width="size" :height="size" :viewBox="`0 0 ${size} ${size}`">
        <path
          v-for="s in slices"
          :key="s.index"
          :d="s.path"
          :fill="s.color"
          stroke="#fff"
          stroke-width="2"
          :style="{ opacity: hoveredIndex >= 0 && hoveredIndex !== s.index ? 0.5 : 1, transition: 'opacity 0.2s' }"
          @mouseenter="hoveredIndex = s.index"
          @mouseleave="hoveredIndex = -1"
        />
        <!-- 中心 tooltip -->
        <text
          v-if="hoveredIndex >= 0"
          :x="size / 2"
          :y="size / 2"
          text-anchor="middle"
          dominant-baseline="central"
          fill="currentColor"
          :font-size="Math.max(11, size / 16)"
          font-weight="600"
        >
          {{ tooltipText }}
        </text>
      </svg>
    </div>
    <div v-if="legendPosition !== 'none'" class="pie-legend">
      <div
        v-for="s in slices"
        :key="s.index"
        class="legend-item"
        :class="{ active: hoveredIndex === s.index }"
        @mouseenter="hoveredIndex = s.index"
        @mouseleave="hoveredIndex = -1"
      >
        <span class="legend-dot" :style="{ background: s.color }"></span>
        <span class="legend-label">{{ s.name }}</span>
        <span class="legend-pct">{{ s.percent }}%</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.simple-pie-wrap {
  display: flex;
  align-items: center;
  gap: 16px;
}
.simple-pie-wrap.legend-bottom {
  flex-direction: column;
}
.simple-pie-wrap.legend-none .pie-legend {
  display: none;
}
.pie-svg-wrap {
  flex-shrink: 0;
}
.pie-svg-wrap svg {
  display: block;
}
.pie-legend {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 100px;
}
.legend-bottom .pie-legend {
  flex-direction: row;
  flex-wrap: wrap;
  justify-content: center;
  gap: 8px 16px;
}
.legend-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.8rem;
  color: var(--color-text-secondary, #666);
  cursor: default;
  padding: 2px 4px;
  border-radius: 4px;
  transition: background 0.15s;
}
.legend-item.active {
  background: var(--color-bg-hover, rgba(0,0,0,0.04));
}
.legend-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.legend-label {
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 120px;
}
.legend-pct {
  color: var(--color-text-muted, #999);
  font-size: 0.75rem;
  white-space: nowrap;
}
</style>
