<script setup>
import { computed } from 'vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  signals: {
    type: Object,
    default: null,
  },
  compact: {
    type: Boolean,
    default: false,
  },
})

const marketChips = computed(() => props.signals?.market?.chips || [])
const opportunityChips = computed(() => props.signals?.opportunity?.chips || [])
const decisionChips = computed(() => props.signals?.decision?.chips || [])
const knowledgeChips = computed(() => props.signals?.knowledge?.chips || [])
const regressionChips = computed(() => props.signals?.regression?.chips || [])
const marketHighlights = computed(() => props.signals?.market?.highlights || [])
const opportunityHighlights = computed(() => props.signals?.opportunity?.highlights || [])
const decisionHighlights = computed(() => props.signals?.decision?.highlights || [])
const knowledgeHighlights = computed(() => props.signals?.knowledge?.highlights || [])
const regressionHighlights = computed(() => props.signals?.regression?.highlights || [])

function toneClass(tone) {
  return {
    good: 'tone-good',
    warn: 'tone-warn',
    danger: 'tone-danger',
    info: 'tone-info',
    muted: 'tone-muted',
  }[tone] || 'tone-muted'
}
</script>

<template>
  <div v-if="signals" class="shared-signals-card card editorial-card">
    <div class="card-header editorial-card-header">
      <div>
        <h3 class="title"><Icon name="link" size="15" class="title-icon" /> 共享信号</h3>
        <p class="shared-signals-summary">{{ signals.summary || '暂无共享信号' }}</p>
      </div>
      <span class="badge">{{ signals.updated_at || '实时' }}</span>
    </div>

    <p v-if="signals.recommendation" class="shared-signals-reco">{{ signals.recommendation }}</p>

    <div class="shared-signals-grid" :class="{ compact }">
      <section class="signal-block">
        <div class="signal-block-head">
          <span class="terminal-label">市场</span>
          <span v-if="signals.market?.summary" class="signal-block-summary">{{ signals.market.summary }}</span>
        </div>
        <div class="signal-chip-row">
          <span
            v-for="chip in marketChips"
            :key="chip.label"
            class="signal-chip"
            :class="toneClass(chip.tone)"
          >
            {{ chip.label }} {{ chip.value }}
          </span>
        </div>
        <ul v-if="marketHighlights.length && !compact" class="signal-list">
          <li v-for="(item, i) in marketHighlights.slice(0, 3)" :key="i">{{ item }}</li>
        </ul>
      </section>

      <section class="signal-block">
        <div class="signal-block-head">
          <span class="terminal-label">机会</span>
          <span v-if="signals.opportunity?.summary" class="signal-block-summary">{{ signals.opportunity.summary }}</span>
        </div>
        <div class="signal-chip-row">
          <span
            v-for="chip in opportunityChips"
            :key="chip.label"
            class="signal-chip"
            :class="toneClass(chip.tone)"
          >
            {{ chip.label }} {{ chip.value }}
          </span>
        </div>
        <ul v-if="opportunityHighlights.length && !compact" class="signal-list">
          <li v-for="(item, i) in opportunityHighlights.slice(0, 3)" :key="i">{{ item }}</li>
        </ul>
      </section>

      <section class="signal-block">
        <div class="signal-block-head">
          <span class="terminal-label">决策</span>
          <span v-if="signals.decision?.summary" class="signal-block-summary">{{ signals.decision.summary }}</span>
        </div>
        <div class="signal-chip-row">
          <span
            v-for="chip in decisionChips"
            :key="chip.label"
            class="signal-chip"
            :class="toneClass(chip.tone)"
          >
            {{ chip.label }} {{ chip.value }}
          </span>
        </div>
        <ul v-if="decisionHighlights.length && !compact" class="signal-list">
          <li v-for="(item, i) in decisionHighlights.slice(0, 3)" :key="i">{{ item }}</li>
        </ul>
      </section>

      <section class="signal-block">
        <div class="signal-block-head">
          <span class="terminal-label">知识</span>
          <span v-if="signals.knowledge?.summary" class="signal-block-summary">{{ signals.knowledge.summary }}</span>
        </div>
        <div class="signal-chip-row">
          <span
            v-for="chip in knowledgeChips"
            :key="chip.label"
            class="signal-chip"
            :class="toneClass(chip.tone)"
          >
            {{ chip.label }} {{ chip.value }}
          </span>
        </div>
        <ul v-if="knowledgeHighlights.length && !compact" class="signal-list">
          <li v-for="(item, i) in knowledgeHighlights.slice(0, 3)" :key="i">{{ item }}</li>
        </ul>
      </section>

      <section class="signal-block">
        <div class="signal-block-head">
          <span class="terminal-label">回归</span>
          <span v-if="signals.regression?.summary" class="signal-block-summary">{{ signals.regression.summary }}</span>
        </div>
        <div class="signal-chip-row">
          <span
            v-for="chip in regressionChips"
            :key="chip.label"
            class="signal-chip"
            :class="toneClass(chip.tone)"
          >
            {{ chip.label }} {{ chip.value }}
          </span>
        </div>
        <ul v-if="regressionHighlights.length && !compact" class="signal-list">
          <li v-for="(item, i) in regressionHighlights.slice(0, 3)" :key="i">{{ item }}</li>
        </ul>
      </section>
    </div>
  </div>
</template>

<style scoped>
.shared-signals-card {
  padding: 1rem;
}

.shared-signals-summary {
  margin-top: 0.35rem;
  color: var(--color-text-secondary);
  font-size: 0.88rem;
  line-height: 1.7;
}

.shared-signals-reco {
  margin-top: 0.75rem;
  padding: 0.65rem 0.75rem;
  border-radius: var(--radius-sm);
  background: var(--color-primary-50);
  color: var(--color-primary-700);
  font-size: 0.85rem;
}

.shared-signals-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.75rem;
  margin-top: 0.85rem;
}

.shared-signals-grid.compact {
  grid-template-columns: 1fr;
}

.signal-block {
  padding: 0.8rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-bg-secondary);
}

.signal-block-head {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  margin-bottom: 0.6rem;
}

.signal-block-summary {
  font-size: 0.8rem;
  color: var(--color-text-muted);
  line-height: 1.6;
}

.signal-chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
}

.signal-chip {
  padding: 0.18rem 0.5rem;
  border-radius: 999px;
  font-size: 0.72rem;
  font-weight: 600;
}

.tone-good { background: var(--color-success-bg); color: var(--color-success); }
.tone-warn { background: var(--color-warning-bg); color: var(--color-warning); }
.tone-danger { background: var(--color-danger-bg); color: var(--color-danger); }
.tone-info { background: var(--color-info-bg); color: var(--color-info); }
.tone-muted { background: var(--color-bg-hover); color: var(--color-text-muted); }

.signal-list {
  margin: 0.7rem 0 0;
  padding-left: 1rem;
  color: var(--color-text-secondary);
  font-size: 0.8rem;
  line-height: 1.6;
}

@media (max-width: 768px) {
  .shared-signals-grid {
    grid-template-columns: 1fr;
  }
}
</style>
