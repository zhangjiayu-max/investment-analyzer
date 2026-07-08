<script setup>
import { ref, watch, computed } from 'vue'
import { getKycQuestionnaire, submitKyc } from '../api'

const props = defineProps({
  visible: Boolean,
})
const emit = defineEmits(['close', 'completed'])

const loading = ref(false)
const submitting = ref(false)
const questions = ref([])
const dimensions = ref({})
const answers = ref({})        // {dim: value | [values]}
const errorMsg = ref('')

watch(() => props.visible, async (v) => {
  if (v) await loadData()
})

async function loadData() {
  loading.value = true
  errorMsg.value = ''
  try {
    const { data } = await getKycQuestionnaire()
    questions.value = data.questionnaire.questions
    dimensions.value = data.questionnaire.dimensions
    // 预填当前画像
    const p = data.profile || {}
    const a = {}
    for (const q of questions.value) {
      const dim = q.dimension
      const cur = p[dim]
      if (dimensions.value[dim]?.multiple) {
        a[dim] = Array.isArray(cur) ? [...cur] : []
      } else {
        a[dim] = cur || ''
      }
    }
    answers.value = a
  } catch (e) {
    errorMsg.value = '加载问卷失败：' + (e.response?.data?.detail || e.message || e)
  } finally {
    loading.value = false
  }
}

function selectOption(dim, value, multiple) {
  if (multiple) {
    const arr = Array.isArray(answers.value[dim]) ? [...answers.value[dim]] : []
    const idx = arr.indexOf(value)
    if (idx >= 0) arr.splice(idx, 1)
    else arr.push(value)
    answers.value[dim] = arr
  } else {
    answers.value[dim] = value
  }
}

function isSelected(dim, value, multiple) {
  if (multiple) return (answers.value[dim] || []).includes(value)
  return answers.value[dim] === value
}

const canSubmit = computed(() => {
  return !!answers.value.risk_tolerance
    && !!answers.value.loss_tolerance
    && !submitting.value
    && !loading.value
})

async function handleSubmit() {
  submitting.value = true
  errorMsg.value = ''
  try {
    const { data } = await submitKyc(answers.value)
    emit('completed', data.profile)
    emit('close')
  } catch (e) {
    errorMsg.value = '提交失败：' + (e.response?.data?.detail || e.message || e)
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <Teleport to="body">
    <Transition name="fade">
      <div v-if="visible" class="kyc-backdrop" @click.self="emit('close')">
        <div class="kyc-box bg-mesh-card">
          <!-- Header -->
          <div class="kyc-header">
            <div class="kyc-title-wrap editorial-card">
              <div>
                <span class="terminal-label kyc-eyebrow">RISK PROFILE</span>
                <h3 class="kyc-title editorial-title-lg">我的投资画像</h3>
                <p class="kyc-subtitle">完成画像后，AI 顾问会更懂你的风险偏好与需求</p>
              </div>
            </div>
            <button class="kyc-close" @click="emit('close')" title="关闭">✕</button>
          </div>

          <!-- Loading -->
          <div v-if="loading" class="kyc-loading">
            <div class="spinner"></div>
            <span>加载问卷中…</span>
          </div>

          <!-- Body -->
          <div v-else class="kyc-body">
            <div v-if="errorMsg" class="kyc-error">{{ errorMsg }}</div>

            <div v-for="(q, idx) in questions" :key="q.id" class="kyc-question reveal-stagger">
              <div class="q-head">
                <span class="terminal-label q-eyebrow">Q{{ idx + 1 }}</span>
                <span class="q-text editorial-title">{{ q.question }}</span>
                <span v-if="q.help" class="q-help terminal-label">{{ q.help }}</span>
              </div>
              <div class="q-options">
                <button
                  v-for="opt in (dimensions[q.dimension]?.options || [])"
                  :key="opt.value"
                  :class="['q-option', { selected: isSelected(q.dimension, opt.value, dimensions[q.dimension]?.multiple) }]"
                  @click="selectOption(q.dimension, opt.value, dimensions[q.dimension]?.multiple)"
                >
                  <span class="q-opt-label">{{ opt.label }}</span>
                  <span v-if="opt.desc" class="q-opt-desc">{{ opt.desc }}</span>
                </button>
              </div>
            </div>
          </div>

          <!-- Footer -->
          <div class="kyc-footer">
            <span class="kyc-tip">带风险偏好、亏损承受度为核心必填项，其余可后续在对话中自动学习</span>
            <div class="kyc-actions">
              <button class="btn-secondary" @click="emit('close')" :disabled="submitting">稍后再说</button>
              <button class="btn-primary" @click="handleSubmit" :disabled="!canSubmit">
                <span v-if="submitting" class="spinner sm"></span>
                {{ submitting ? '保存中…' : '保存画像' }}
              </button>
            </div>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.kyc-backdrop {
  position: fixed;
  inset: 0;
  z-index: var(--z-modal);
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.45);
  backdrop-filter: blur(4px);
}

.kyc-box {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-lg);
  width: 100%;
  max-width: 620px;
  max-height: 88vh;
  margin: 0 1rem;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.kyc-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding: 1.1rem 1.4rem;
  border-bottom: 1px solid var(--color-border);
}

.kyc-title-wrap {
  display: flex;
  gap: 0.7rem;
  align-items: flex-start;
}

.kyc-eyebrow {
  display: block;
  margin-bottom: 0.2rem;
}

.kyc-title {
  color: var(--color-text-primary);
  margin: 0 0 0.2rem 0;
}

.kyc-subtitle {
  font-size: 0.78rem;
  color: var(--color-text-muted);
  margin: 0;
}

.kyc-close {
  background: none;
  border: none;
  color: var(--color-text-muted);
  font-size: 1.1rem;
  cursor: pointer;
  padding: 0.2rem 0.4rem;
  border-radius: var(--radius-sm);
  line-height: 1;
}
.kyc-close:hover {
  background: var(--color-bg-hover);
  color: var(--color-text-primary);
}

.kyc-loading {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 3rem;
  justify-content: center;
  color: var(--color-text-muted);
  font-size: 0.85rem;
}

.kyc-body {
  padding: 1rem 1.4rem;
  overflow-y: auto;
  flex: 1;
}

.kyc-error {
  background: var(--color-danger-bg);
  color: var(--color-danger);
  padding: 0.6rem 0.8rem;
  border-radius: var(--radius-md);
  font-size: 0.8rem;
  margin-bottom: 0.8rem;
}

.kyc-question {
  margin-bottom: 1.3rem;
}

.q-head {
  margin-bottom: 0.6rem;
}

.q-text {
  display: block;
  font-size: 0.88rem;
  font-weight: 600;
  color: var(--color-text-primary);
}

.q-help {
  display: block;
  font-size: 0.72rem;
  color: var(--color-text-muted);
  margin-top: 0.2rem;
}

.q-options {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.q-option {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 0.15rem;
  padding: 0.55rem 0.85rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-input);
  cursor: pointer;
  transition: all var(--transition-fast);
  text-align: left;
  min-width: 90px;
}

.q-option:hover {
  border-color: var(--color-primary);
  background: var(--color-primary-50);
}

.q-option.selected {
  border-color: var(--color-primary);
  background: var(--color-primary-50);
  box-shadow: 0 0 0 1px var(--color-primary) inset;
}

.dark .q-option.selected {
  background: var(--color-primary-bg);
}

.q-opt-label {
  font-size: 0.82rem;
  font-weight: 600;
  color: var(--color-text-primary);
}

.q-option.selected .q-opt-label {
  color: var(--color-primary);
}

.q-opt-desc {
  font-size: 0.68rem;
  color: var(--color-text-muted);
  line-height: 1.3;
}

.kyc-footer {
  padding: 0.8rem 1.4rem;
  border-top: 1px solid var(--color-border);
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}

.kyc-tip {
  font-size: 0.7rem;
  color: var(--color-text-muted);
  line-height: 1.4;
}

.kyc-actions {
  display: flex;
  gap: 0.6rem;
  justify-content: flex-end;
}

.kyc-actions button {
  padding: 0.6rem 1.3rem;
  font-size: 0.85rem;
  min-height: 40px;
  border-radius: var(--radius-md);
}

.spinner {
  width: 18px;
  height: 18px;
  border: 2px solid var(--color-border);
  border-top-color: var(--color-primary);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  display: inline-block;
}
.spinner.sm {
  width: 13px;
  height: 13px;
  border-width: 2px;
  vertical-align: middle;
  margin-right: 0.3rem;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

@media (max-width: 768px) {
  .kyc-box {
    margin: 0 0.5rem;
    max-height: 92vh;
  }
  .kyc-header,
  .kyc-body,
  .kyc-footer {
    padding-left: 1rem;
    padding-right: 1rem;
  }
  .q-option {
    min-width: calc(50% - 0.25rem);
  }
  .kyc-actions {
    flex-direction: column-reverse;
  }
  .kyc-actions button {
    width: 100%;
  }
}
</style>
