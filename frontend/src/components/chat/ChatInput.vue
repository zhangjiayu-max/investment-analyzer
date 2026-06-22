<script setup>
import { ref, computed, watch, nextTick } from 'vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  sending: { type: Boolean, default: false },
  inputText: { type: String, default: '' },
  statusMessage: { type: String, default: '' },
  agents: { type: Array, default: () => [] },
})

// 专家 icon 文字 → Icon 语义 name 映射
const ICON_MAP = {
  chart: 'chart', research: 'search', shield: 'shield-check', pie: 'pie-chart',
  robot: 'bot', newspaper: 'newspaper', search: 'search', bull: 'trending-up',
}
function getIcon(icon) { return ICON_MAP[icon] || 'bot' }

// ── 快捷指令 ──
const QUICK_COMMANDS = [
  { icon: 'chart', label: '估值', text: '帮我分析当前主要指数的估值水平，哪些处于低估区？' },
  { icon: 'portfolio', label: '持仓', text: '诊断我的持仓，分析分散度和风险敞口' },
  { icon: 'newspaper', label: '解读', text: '解读这篇文章：' },
  { icon: 'scale', label: '配置', text: '根据我的风险偏好，给出资产配置建议' },
  { icon: 'activity', label: '体检', text: '给我的投资组合做一次全面体检' },
]

function insertCommand(cmd) {
  emit('update:inputText', cmd.text)
  nextTick(() => {
    const ta = textareaRef.value
    if (ta) {
      ta.focus()
      const len = cmd.text.length
      ta.setSelectionRange(len, len)
    }
  })
}

const emit = defineEmits(['send', 'cancel', 'update:inputText'])

// ── 复制输入内容 ──
const copyInputTimer = ref(null)
const inputCopied = ref(false)

function copyInputText() {
  const text = props.inputText || ''
  if (!text.trim()) return
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(() => {
      flashInputCopied()
    }).catch(() => {
      fallbackCopy(text)
      flashInputCopied()
    })
  } else {
    fallbackCopy(text)
    flashInputCopied()
  }
}

function fallbackCopy(text) {
  const ta = document.createElement('textarea')
  ta.value = text
  ta.readOnly = true
  ta.style.position = 'fixed'
  ta.style.top = '-9999px'
  ta.style.opacity = '0'
  document.body.appendChild(ta)
  ta.select()
  ta.setSelectionRange(0, text.length)
  try { document.execCommand('copy') } catch (e) {}
  document.body.removeChild(ta)
}

function flashInputCopied() {
  inputCopied.value = true
  if (copyInputTimer.value) clearTimeout(copyInputTimer.value)
  copyInputTimer.value = setTimeout(() => { inputCopied.value = false }, 1500)
}

// ── @mention 状态 ──
const showMention = ref(false)
const mentionQuery = ref('')
const mentionIndex = ref(0)
const textareaRef = ref(null)

// 根据输入过滤 Agent 列表
const filteredAgents = computed(() => {
  const q = mentionQuery.value.toLowerCase()
  if (!q) return props.agents
  return props.agents.filter(a =>
    a.name.toLowerCase().includes(q) ||
    (a.agent_key && a.agent_key.toLowerCase().includes(q)) ||
    (a.description && a.description.toLowerCase().includes(q))
  )
})

// 监听过滤列表变化，重置选中索引
watch(filteredAgents, () => { mentionIndex.value = 0 })

// 检测 @mention 触发
function handleInput(e) {
  const val = e.target.value
  emit('update:inputText', val)
  checkMentionTrigger(val, e.target.selectionStart)
}

function checkMentionTrigger(text, cursorPos) {
  // 从光标位置往前找最近的 @
  const before = text.slice(0, cursorPos)
  const atIndex = before.lastIndexOf('@')
  if (atIndex === -1) {
    closeMention()
    return
  }
  // @ 前面必须是空格或行首
  if (atIndex > 0 && !/\s/.test(text[atIndex - 1])) {
    closeMention()
    return
  }
  // @ 后面不能有空格（说明已经选完了）
  const afterAt = before.slice(atIndex + 1)
  if (/\s/.test(afterAt) && afterAt.length > 0) {
    closeMention()
    return
  }
  mentionQuery.value = afterAt
  showMention.value = true
}

function closeMention() {
  showMention.value = false
  mentionQuery.value = ''
  mentionIndex.value = 0
}

// 选择 Agent
function selectAgent(agent) {
  const textarea = textareaRef.value
  if (!textarea) return
  const val = props.inputText
  const cursorPos = textarea.selectionStart
  const before = val.slice(0, cursorPos)
  const atIndex = before.lastIndexOf('@')
  if (atIndex === -1) return

  // 替换 @query 为 @Agent名称（空格结尾表示选完）
  const newText = val.slice(0, atIndex) + '@' + agent.name + ' ' + val.slice(cursorPos)
  emit('update:inputText', newText)
  closeMention()

  // 重新聚焦 textarea
  nextTick(() => {
    textarea.focus()
    const newPos = atIndex + agent.name.length + 2 // @名称 + 空格
    textarea.setSelectionRange(newPos, newPos)
  })
}

// 键盘事件
function handleKeydown(e) {
  if (showMention.value && filteredAgents.value.length > 0) {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      mentionIndex.value = (mentionIndex.value + 1) % filteredAgents.value.length
      return
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault()
      mentionIndex.value = (mentionIndex.value - 1 + filteredAgents.value.length) % filteredAgents.value.length
      return
    }
    if (e.key === 'Enter' || e.key === 'Tab') {
      e.preventDefault()
      selectAgent(filteredAgents.value[mentionIndex.value])
      return
    }
    if (e.key === 'Escape') {
      e.preventDefault()
      closeMention()
      return
    }
  }

  // 原有 Enter 发送逻辑
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    emit('send')
  }
}
</script>

<template>
  <div :class="['chat-input-area', { 'is-sending': sending }]">
    <div v-if="sending" class="input-progress-bar">
      <div class="input-progress-fill"></div>
    </div>
    <div v-if="!sending" class="quick-commands">
      <button v-for="cmd in QUICK_COMMANDS" :key="cmd.label" class="quick-cmd" @click="insertCommand(cmd)" :title="cmd.text">
        <Icon :name="cmd.icon" size="14" class="quick-cmd-icon" />
        <span class="quick-cmd-label">{{ cmd.label }}</span>
      </button>
    </div>
    <form @submit.prevent="emit('send')" class="chat-form">
      <div class="input-wrapper">
        <textarea
          ref="textareaRef"
          :value="inputText"
          :placeholder="sending ? '正在执行中，请稍候...' : '输入消息... @指定专家 (Shift+Enter 换行)'"
          class="chat-input"
          :disabled="sending"
          @keydown="handleKeydown"
          @input="handleInput"
          @blur="() => setTimeout(closeMention, 200)"
          rows="1"
        ></textarea>

        <!-- 复制输入内容按钮 -->
        <button
          v-if="inputText.trim() && !sending"
          type="button"
          class="btn-copy-input btn-ai-action"
          @click="copyInputText"
        >
          <Icon name="clipboard" size="14" />
          <span class="ai-agent-tooltip">{{ inputCopied ? '已复制' : '复制输入内容' }}</span>
        </button>

        <!-- @mention 下拉列表 -->
        <Transition name="mention">
          <div v-if="showMention && filteredAgents.length > 0" class="mention-dropdown">
            <div class="mention-header">选择专家</div>
            <div
              v-for="(agent, idx) in filteredAgents"
              :key="agent.agent_key || agent.id"
              :class="['mention-item', { active: idx === mentionIndex }]"
              @mousedown.prevent="selectAgent(agent)"
              @mouseenter="mentionIndex = idx"
            >
              <Icon :name="getIcon(agent.icon)" size="14" class="mention-icon" />
              <div class="mention-info">
                <span class="mention-name">{{ agent.name }}</span>
                <span class="mention-desc">{{ agent.description }}</span>
              </div>
            </div>
          </div>
        </Transition>
      </div>

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
    <div class="input-hints">
      <span v-if="!sending" class="input-hint-text">Shift+Enter 换行 · Enter 发送 · @指定专家</span>
    </div>
    <div v-if="sending" class="sending-hint">
      <span class="sending-spinner"></span>
      <span class="sending-text">{{ statusMessage || 'AI 正在分析中...' }}</span>
    </div>
  </div>
</template>

<style scoped>
.chat-input-area {
  padding: 1rem 1.5rem;
  padding-bottom: calc(1rem + env(safe-area-inset-bottom, 0px));
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

.quick-commands {
  display: flex;
  gap: 0.4rem;
  flex-wrap: wrap;
  margin-bottom: 0.5rem;
}
.quick-cmd {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  padding: 0.3rem 0.6rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-input);
  color: var(--color-text-secondary);
  font-size: 0.72rem;
  cursor: pointer;
  transition: all var(--transition-fast);
}
.quick-cmd:hover {
  border-color: var(--color-primary);
  color: var(--color-primary);
  background: var(--color-primary-50);
}
.dark .quick-cmd:hover { background: var(--color-primary-bg); }
.quick-cmd-icon { font-size: 0.85rem; }

.chat-form {
  display: flex;
  gap: 0.6rem;
  align-items: flex-end;
}

.input-wrapper {
  flex: 1;
  position: relative;
}

.chat-input {
  width: 100%;
  resize: none;
  padding: 0.75rem 1rem;
  font-size: 0.88rem;
  line-height: 1.6;
  max-height: 120px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-bg-input);
  color: var(--color-text-primary);
  outline: none;
  transition: border-color var(--transition-fast), box-shadow var(--transition-fast);
  box-sizing: border-box;
}

.chat-input:focus {
  border-color: var(--color-primary-400);
  box-shadow: var(--focus-ring);
}

/* 复制输入按钮 */
.btn-copy-input {
  position: absolute;
  right: 10px;
  top: 10px;
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-bg-card);
  color: var(--color-text-secondary);
  cursor: pointer;
  opacity: 0;
  transition: opacity var(--transition-fast), color var(--transition-fast), border-color var(--transition-fast);
  z-index: 10;
}
.input-wrapper:hover .btn-copy-input { opacity: 1; }
.btn-copy-input:hover {
  color: var(--color-primary);
  border-color: var(--color-primary-border);
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

/* ── @mention 下拉 ── */
.mention-dropdown {
  position: absolute;
  bottom: calc(100% + 6px);
  left: 0;
  right: 0;
  max-height: 260px;
  overflow-y: auto;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: 0 -4px 20px rgba(0, 0, 0, 0.12);
  z-index: 100;
}

.mention-header {
  padding: 0.5rem 0.75rem;
  font-size: 0.7rem;
  color: var(--color-text-muted);
  border-bottom: 1px solid var(--color-border);
  font-weight: 600;
  letter-spacing: 0.03em;
}

.mention-item {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.55rem 0.75rem;
  cursor: pointer;
  transition: background var(--transition-fast);
}

.mention-item:hover,
.mention-item.active {
  background: var(--color-primary-50, rgba(201, 168, 76, 0.08));
}

.mention-icon {
  font-size: 1.2rem;
  flex-shrink: 0;
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-bg-secondary, rgba(0,0,0,0.03));
  border-radius: var(--radius-md);
}

.mention-info {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.mention-name {
  font-size: 0.82rem;
  font-weight: 600;
  color: var(--color-text-primary);
}

.mention-desc {
  font-size: 0.7rem;
  color: var(--color-text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 280px;
}

/* mention 动画 */
.mention-enter-active { transition: all 0.15s ease-out; }
.mention-leave-active { transition: all 0.1s ease-in; }
.mention-enter-from, .mention-leave-to {
  opacity: 0;
  transform: translateY(6px);
}

.btn-send {
  width: 42px;
  height: 42px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-lg);
  background: var(--gradient-primary);
  color: white;
  transition: all var(--transition-fast);
  flex-shrink: 0;
  box-shadow: 0 2px 6px var(--color-primary-shadow);
}

.btn-send:hover:not(:disabled) {
  box-shadow: 0 4px 12px var(--color-primary-glow-strong);
  transform: translateY(-1px);
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
  gap: 0.5rem;
  margin-top: 0.5rem;
  padding: 0.25rem 0;
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
  font-size: 0.75rem;
  color: var(--color-primary-500);
  font-weight: 500;
}

/* ── Input Hints ── */
.input-hints {
  display: flex;
  justify-content: flex-end;
  padding: 0.15rem 0 0;
}
.input-hint-text {
  font-size: 0.65rem;
  color: var(--color-text-muted);
  opacity: 0.6;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

@media (max-width: 768px) {
  .chat-input-area { padding: 0.6rem 0.75rem; }
  .chat-input { font-size: 0.88rem; padding: 0.5rem 0.6rem; }
  .btn-send { width: 38px; height: 38px; }
  .btn-stop { width: 36px; height: 36px; }
  .mention-dropdown { max-height: 200px; }
  .mention-desc { max-width: 180px; }
}
</style>
