<script setup>
const props = defineProps({
  sending: { type: Boolean, default: false },
  inputText: { type: String, default: '' },
  statusMessage: { type: String, default: '' },
})

const emit = defineEmits(['send', 'cancel', 'update:inputText'])

function handleKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    emit('send')
  }
}

function handleInput(e) {
  emit('update:inputText', e.target.value)
}
</script>

<template>
  <div :class="['chat-input-area', { 'is-sending': sending }]">
    <div v-if="sending" class="input-progress-bar">
      <div class="input-progress-fill"></div>
    </div>
    <form @submit.prevent="emit('send')" class="chat-form">
      <textarea
        :value="inputText"
        :placeholder="sending ? '正在执行中，请稍候...' : '输入消息...'"
        class="chat-input"
        :disabled="sending"
        @keydown="handleKeydown"
        @input="handleInput"
        rows="1"
      ></textarea>
      <button v-if="sending" type="button" @click="emit('cancel')" class="btn-stop" title="终止执行">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
          <rect x="6" y="6" width="12" height="12" rx="2"/>
        </svg>
      </button>
      <button v-else type="submit" :disabled="!inputText.trim()" class="btn-send">
        <svg width="20" height="20" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 12h14M12 5l7 7-7 7"/>
        </svg>
      </button>
    </form>
    <div v-if="sending" class="sending-hint">
      <span class="sending-spinner"></span>
      <span class="sending-text">{{ statusMessage || 'AI 正在分析中...' }}</span>
    </div>
  </div>
</template>

<style scoped>
.chat-input-area {
  padding: 0.75rem 1.25rem;
  border-top: 1px solid var(--color-border);
  background: var(--color-bg-card);
  position: relative;
}

.chat-input-area.is-sending {
  border-top-color: var(--color-primary-300);
}

.input-progress-bar {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 2px;
  background: var(--color-primary-100);
  overflow: hidden;
}

.input-progress-fill {
  height: 100%;
  width: 30%;
  background: linear-gradient(90deg, var(--color-primary-400), var(--color-primary-600));
  border-radius: 2px;
  animation: progressSlide 1.5s ease-in-out infinite;
}

@keyframes progressSlide {
  0% { transform: translateX(-100%); }
  50% { transform: translateX(233%); }
  100% { transform: translateX(-100%); }
}

.chat-form {
  display: flex;
  gap: 0.5rem;
  align-items: flex-end;
}

.chat-input {
  flex: 1;
  resize: none;
  padding: 0.6rem 0.85rem;
  font-size: 0.85rem;
  line-height: 1.5;
  max-height: 120px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-input);
  color: var(--color-text-primary);
  outline: none;
  transition: border-color var(--transition-fast);
}

.chat-input:focus {
  border-color: var(--color-primary-400);
}

.is-sending .chat-input {
  border-color: var(--color-primary-300);
  background: var(--color-primary-50, rgba(201, 168, 76, 0.03));
  animation: inputPulse 2s ease-in-out infinite;
}

@keyframes inputPulse {
  0%, 100% { border-color: var(--color-primary-200); }
  50% { border-color: var(--color-primary-400); }
}

.btn-send {
  width: 40px;
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-md);
  background: var(--color-primary-500);
  color: white;
  transition: all var(--transition-fast);
  flex-shrink: 0;
}

.btn-send:hover:not(:disabled) {
  background: var(--color-primary-600);
}

.btn-send:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.btn-stop {
  width: 40px;
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-md);
  background: #ef4444;
  color: white;
  transition: all var(--transition-fast);
  flex-shrink: 0;
  animation: pulseStop 2s ease-in-out infinite;
}

.btn-stop:hover {
  background: #dc2626;
  transform: scale(1.05);
}

@keyframes pulseStop {
  0%, 100% { box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.3); }
  50% { box-shadow: 0 0 0 6px rgba(239, 68, 68, 0); }
}

.sending-hint {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  margin-top: 0.4rem;
  padding: 0.2rem 0;
}

.sending-spinner {
  width: 12px;
  height: 12px;
  border: 2px solid var(--color-primary-200);
  border-top-color: var(--color-primary-500);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  flex-shrink: 0;
}

.sending-text {
  font-size: 0.72rem;
  color: var(--color-primary-500);
  font-weight: 500;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>
