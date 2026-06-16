<script setup>
const props = defineProps({
  visible: { type: Boolean, default: false },
  feedbackType: { type: String, default: '' },
  note: { type: String, default: '' },
})

const emit = defineEmits(['update:visible', 'update:note', 'skip', 'submit'])

function close() {
  emit('update:visible', false)
}
</script>

<template>
  <Teleport to="body">
    <Transition name="fade">
      <div v-if="visible" class="dialog-backdrop" @click.self="close">
        <div class="feedback-dialog">
          <div class="feedback-dialog-header">
            <span class="feedback-dialog-icon">{{ feedbackType === 'helpful' ? '👍' : '👎' }}</span>
            <span class="feedback-dialog-title">{{ feedbackType === 'helpful' ? '标记为有用' : '标记为需改进' }}</span>
          </div>
          <div class="feedback-dialog-body">
            <textarea
              :value="note"
              @input="emit('update:note', $event.target.value)"
              placeholder="可选：描述您的反馈意见，帮助我们改进..."
              class="feedback-textarea"
              rows="3"
            ></textarea>
          </div>
          <div class="feedback-dialog-actions">
            <button class="btn-secondary" @click="emit('skip')">跳过</button>
            <button class="btn-primary" @click="emit('submit')">提交反馈</button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.dialog-backdrop {
  position: fixed;
  inset: 0;
  z-index: var(--z-modal, 1000);
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0,0,0,0.4);
  backdrop-filter: blur(4px);
}

.feedback-dialog {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-lg, 0 10px 25px rgba(0,0,0,0.15));
  width: 100%;
  max-width: 420px;
  margin: 0 1rem;
  overflow: hidden;
}

.feedback-dialog-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 1.25rem 1.25rem 0.75rem;
}

.feedback-dialog-icon { font-size: 1.25rem; }

.feedback-dialog-title {
  font-size: 0.95rem;
  font-weight: 600;
  color: var(--color-text-primary);
}

.feedback-dialog-body { padding: 0 1.25rem; }

.feedback-textarea {
  width: 100%;
  padding: 0.6rem 0.75rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-input);
  color: var(--color-text-primary);
  font-size: 0.85rem;
  line-height: 1.5;
  resize: vertical;
  outline: none;
  transition: border-color 0.15s;
  box-sizing: border-box;
}

.feedback-textarea:focus { border-color: var(--color-primary-500, #3b82f6); }
.feedback-textarea::placeholder { color: var(--color-text-tertiary, #9ca3af); }

.feedback-dialog-actions {
  display: flex;
  gap: 0.5rem;
  padding: 1rem 1.25rem 1.25rem;
}

.feedback-dialog-actions .btn-secondary {
  flex: 1;
  padding: 0.55rem 1rem;
  font-size: 0.85rem;
  background: var(--color-bg-card);
  color: var(--color-text-secondary);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s;
}
.feedback-dialog-actions .btn-secondary:hover { background: var(--color-bg-hover); }

.feedback-dialog-actions .btn-primary {
  flex: 1;
  padding: 0.55rem 1rem;
  font-size: 0.85rem;
  background: linear-gradient(135deg, var(--color-primary-600, #2563eb), var(--color-primary-500, #3b82f6));
  color: white;
  border: none;
  border-radius: var(--radius-md);
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s;
}
.feedback-dialog-actions .btn-primary:hover {
  background: linear-gradient(135deg, var(--color-primary-700, #1d4ed8), var(--color-primary-600, #2563eb));
}

.fade-enter-active { transition: opacity 0.2s; }
.fade-leave-active { transition: opacity 0.15s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>
