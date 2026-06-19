<script setup>
import { ref } from 'vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  text: { type: String, default: '' },
  agent: { type: String, default: '' },
})

const expanded = ref(false)
</script>

<template>
  <div v-if="text" class="reasoning-panel">
    <button class="reasoning-toggle" @click="expanded = !expanded">
      <Icon name="message-square-dot" size="14" class="reasoning-icon" />
      <span class="reasoning-label">思考过程<span v-if="agent" class="reasoning-agent"> · {{ agent }}</span></span>
      <Icon name="chevron-down" size="14" class="reasoning-chevron" :class="{ expanded }" />
    </button>
    <div v-show="expanded" class="reasoning-content">
      <pre>{{ text }}</pre>
    </div>
  </div>
</template>

<style scoped>
.reasoning-panel {
  margin-bottom: 0.5rem;
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  background: var(--color-bg-input);
  overflow: hidden;
}

.reasoning-toggle {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  width: 100%;
  padding: 0.45rem 0.7rem;
  background: none;
  border: none;
  cursor: pointer;
  color: var(--color-text-muted);
  font-size: 0.76rem;
  text-align: left;
  transition: background var(--transition-fast);
}
.reasoning-toggle:hover {
  background: var(--color-bg-hover);
}

.reasoning-icon {
  font-size: 0.85rem;
}

.reasoning-label {
  flex: 1;
  font-weight: 500;
}

.reasoning-agent {
  color: var(--color-text-muted);
  font-weight: 400;
}

.reasoning-chevron {
  transition: transform var(--transition-fast);
  font-size: 0.7rem;
  opacity: 0.6;
}
.reasoning-chevron.expanded {
  transform: rotate(180deg);
}

.reasoning-content {
  padding: 0.6rem 0.8rem;
  border-top: 1px solid var(--color-border-light);
  max-height: 360px;
  overflow-y: auto;
}

.reasoning-content pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: inherit;
  font-size: 0.76rem;
  line-height: 1.6;
  color: var(--color-text-secondary);
}
</style>
