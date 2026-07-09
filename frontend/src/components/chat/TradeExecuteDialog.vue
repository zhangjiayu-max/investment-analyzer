<script setup>
import { ref, watch, computed, reactive } from 'vue'

const props = defineProps({
  visible: Boolean,
  recommendation: { type: Object, default: null },  // { index_name, index_code, direction, target_fund_code, target_fund_name }
  candidateFunds: { type: Array, default: () => [] }, // [{fund_code, fund_name, in_holdings}]
  loading: { type: Boolean, default: false },
})
const emit = defineEmits(['close', 'confirm'])

const selectedFundCode = ref('')
const selectedFundName = ref('')
const manualCode = ref('')  // 用户手动输入的基金代码
const amount = ref(null)
const useManualInput = ref(false)

const direction = computed(() => props.recommendation?.direction || '')
const actionType = computed(() => {
  if (direction.value === 'up') return '买入'
  if (direction.value === 'down') return '卖出'
  return '持有'
})

const actionTypeApi = computed(() => {
  if (direction.value === 'up') return 'buy'
  if (direction.value === 'down') return 'sell'
  return ''
})

// 选中的基金代码（下拉选中 或 手动输入）
const finalFundCode = computed(() => {
  if (useManualInput.value) return manualCode.value.trim()
  return selectedFundCode.value
})

const finalFundName = computed(() => {
  if (useManualInput.value) return manualCode.value.trim() || '手动输入'
  return selectedFundName.value
})

const canConfirm = computed(() => {
  return !!finalFundCode.value && !!actionTypeApi.value && !props.loading
})

// 当弹窗打开或推荐变更时，重置表单 + 预选基金
watch(() => [props.visible, props.recommendation?.id], ([v]) => {
  if (!v || !props.recommendation) return
  // 预选 target_fund_code 或候选列表第一个
  const targetCode = props.recommendation.target_fund_code
  if (targetCode) {
    selectedFundCode.value = targetCode
    selectedFundName.value = props.recommendation.target_fund_name || targetCode
  } else if (props.candidateFunds.length > 0) {
    const first = props.candidateFunds[0]
    selectedFundCode.value = first.fund_code
    selectedFundName.value = first.fund_name
  } else {
    selectedFundCode.value = ''
    selectedFundName.value = ''
    useManualInput.value = true  // 无候选，自动切到手动输入
  }
  manualCode.value = ''
  amount.value = null
})

function selectFund(fund) {
  selectedFundCode.value = fund.fund_code
  selectedFundName.value = fund.fund_name
  useManualInput.value = false
}

function toggleManualInput() {
  useManualInput.value = !useManualInput.value
  if (!useManualInput.value) {
    // 切回下拉时清空手动输入
    manualCode.value = ''
  }
}

function handleConfirm() {
  if (!canConfirm.value) return
  emit('confirm', {
    type: actionTypeApi.value,
    fund_code: finalFundCode.value,
    fund_name: finalFundName.value,
    amount: amount.value || undefined,
  })
}

function handleBackdropClick() {
  emit('close')
}
</script>

<template>
  <Teleport to="body">
    <Transition name="fade">
      <div v-if="visible && recommendation" class="ted-backdrop" @click.self="handleBackdropClick">
        <div class="ted-box bg-mesh-card">
          <!-- Header -->
          <div class="ted-header">
            <div>
              <span class="terminal-label ted-eyebrow">EXECUTE</span>
              <h3 class="ted-title editorial-title-lg">执行建议</h3>
            </div>
            <button class="ted-close" @click="emit('close')" title="关闭">✕</button>
          </div>

          <!-- Body -->
          <div class="ted-body">
            <!-- 建议摘要 -->
            <div class="ted-rec-summary">
              <div class="rec-summary-row">
                <span class="summary-label">建议标的</span>
                <span class="summary-value">
                  {{ recommendation.index_name || '未命名' }}
                  <span v-if="recommendation.index_code" class="font-jet summary-code">
                    ({{ recommendation.index_code }})
                  </span>
                </span>
              </div>
              <div class="rec-summary-row">
                <span class="summary-label">建议方向</span>
                <span class="summary-value" :class="'dir-' + direction">
                  {{ actionType }}
                </span>
              </div>
            </div>

            <!-- Loading 候选基金 -->
            <div v-if="loading" class="ted-loading">
              <div class="spinner sm"></div>
              <span>加载候选基金…</span>
            </div>

            <template v-else>
              <!-- 基金选择 -->
              <div class="form-section">
                <div class="section-head">
                  <h4 class="section-title editorial-title">选择基金</h4>
                  <button
                    v-if="candidateFunds.length > 0"
                    class="btn-toggle-manual"
                    @click="toggleManualInput"
                  >{{ useManualInput ? '使用候选列表' : '手动输入' }}</button>
                </div>

                <!-- 候选列表 -->
                <div v-if="!useManualInput && candidateFunds.length > 0" class="fund-list">
                  <button
                    v-for="fund in candidateFunds"
                    :key="fund.fund_code"
                    :class="['fund-item', { selected: selectedFundCode === fund.fund_code }]"
                    @click="selectFund(fund)"
                  >
                    <div class="fund-info">
                      <span class="fund-name">{{ fund.fund_name }}</span>
                      <span class="fund-code font-jet">{{ fund.fund_code }}</span>
                    </div>
                    <span v-if="fund.in_holdings" class="in-holdings-badge">已持有</span>
                  </button>
                </div>

                <!-- 手动输入 -->
                <div v-else class="manual-input-wrap">
                  <input
                    v-model="manualCode"
                    type="text"
                    placeholder="请输入基金代码（如 161725）"
                    class="manual-input"
                  />
                  <p class="manual-tip">
                    未找到相关基金，请手动输入基金代码后跳转持仓管理
                  </p>
                </div>

                <!-- 无候选且未手动输入的兜底 -->
                <p v-if="!useManualInput && candidateFunds.length === 0" class="empty-tip">
                  无候选基金，<button class="btn-link" @click="useManualInput = true">点击手动输入</button>
                </p>
              </div>

              <!-- 金额 -->
              <div class="form-section">
                <h4 class="section-title editorial-title">{{ actionType }}金额 / 份额</h4>
                <input
                  v-model.number="amount"
                  type="number"
                  :placeholder="actionType === '买入' ? '买入金额（元，可留空）' : '卖出份额（份，可留空）'"
                  min="0"
                  class="amount-input"
                />
                <p class="amount-tip">可留空，跳转后在持仓管理表单中补全</p>
              </div>
            </template>
          </div>

          <!-- Footer -->
          <div class="ted-footer">
            <button class="btn-secondary" @click="emit('close')" :disabled="loading">取消</button>
            <button
              class="btn-primary"
              @click="handleConfirm"
              :disabled="!canConfirm"
            >
              <span v-if="loading" class="spinner sm"></span>
              {{ loading ? '加载中…' : '确认跳转持仓管理' }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.ted-backdrop {
  position: fixed;
  inset: 0;
  z-index: var(--z-modal);
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.45);
  backdrop-filter: blur(4px);
}

.ted-box {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-lg);
  width: 100%;
  max-width: 520px;
  max-height: 88vh;
  margin: 0 1rem;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.ted-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding: 1.1rem 1.4rem;
  border-bottom: 1px solid var(--color-border);
}

.ted-eyebrow {
  display: block;
  margin-bottom: 0.2rem;
}

.ted-title {
  color: var(--color-text-primary);
  margin: 0;
}

.ted-close {
  background: none;
  border: none;
  color: var(--color-text-muted);
  font-size: 1.1rem;
  cursor: pointer;
  padding: 0.2rem 0.4rem;
  border-radius: var(--radius-sm);
  line-height: 1;
}
.ted-close:hover {
  background: var(--color-bg-hover);
  color: var(--color-text-primary);
}

.ted-body {
  padding: 1rem 1.4rem;
  overflow-y: auto;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 1.1rem;
}

.ted-loading {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 2rem 0;
  justify-content: center;
  color: var(--color-text-muted);
  font-size: 0.85rem;
}

.ted-rec-summary {
  padding: 0.8rem 1rem;
  background: var(--color-primary-bg-weak);
  border: 1px solid var(--color-primary-border-weak);
  border-radius: var(--radius-md);
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.rec-summary-row {
  display: flex;
  align-items: center;
  gap: 0.8rem;
  font-size: 0.82rem;
}

.summary-label {
  color: var(--color-text-muted);
  min-width: 64px;
  font-weight: 500;
}

.summary-value {
  color: var(--color-text-primary);
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 0.3rem;
}

.summary-code {
  color: var(--color-text-secondary);
  font-weight: 400;
  font-size: 0.78rem;
}

.dir-up { color: var(--color-danger); }
.dir-down { color: var(--color-success, #16a34a); }
.dir-hold { color: var(--color-text-muted); }

.form-section {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.section-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.section-title {
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0;
}

.btn-toggle-manual {
  background: none;
  border: none;
  color: var(--color-primary);
  font-size: 0.75rem;
  cursor: pointer;
  padding: 0.2rem 0.4rem;
  text-decoration: underline;
}

.btn-toggle-manual:hover {
  color: var(--color-primary-hover);
}

.fund-list {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.fund-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.6rem 0.85rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-input);
  cursor: pointer;
  transition: all var(--transition-fast);
  text-align: left;
  font-family: inherit;
}

.fund-item:hover {
  border-color: var(--color-primary);
  background: var(--color-primary-50);
}

.fund-item.selected {
  border-color: var(--color-primary);
  background: var(--color-primary-50);
  box-shadow: 0 0 0 1px var(--color-primary) inset;
}

.fund-info {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}

.fund-name {
  font-size: 0.82rem;
  font-weight: 600;
  color: var(--color-text-primary);
}

.fund-code {
  font-size: 0.75rem;
  color: var(--color-text-muted);
}

.fund-item.selected .fund-name {
  color: var(--color-primary);
}

.in-holdings-badge {
  font-size: 0.68rem;
  padding: 0.15rem 0.5rem;
  border-radius: 999px;
  background: var(--color-primary-100);
  color: var(--color-primary);
  font-weight: 600;
}

.manual-input-wrap {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.manual-input,
.amount-input {
  padding: 0.55rem 0.75rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-bg-input);
  font-size: 0.85rem;
  color: var(--color-text-primary);
  width: 100%;
  font-family: inherit;
  transition: all var(--transition-fast);
}

.manual-input:focus,
.amount-input:focus {
  outline: none;
  border-color: var(--color-primary);
  box-shadow: 0 0 0 2px var(--color-primary-glow);
}

.manual-tip,
.amount-tip,
.empty-tip {
  font-size: 0.72rem;
  color: var(--color-text-muted);
  margin: 0;
  line-height: 1.4;
}

.btn-link {
  background: none;
  border: none;
  color: var(--color-primary);
  cursor: pointer;
  text-decoration: underline;
  font-size: inherit;
  padding: 0;
}

.ted-footer {
  padding: 0.8rem 1.4rem;
  border-top: 1px solid var(--color-border);
  display: flex;
  gap: 0.6rem;
  justify-content: flex-end;
}

.ted-footer button {
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
  .ted-box {
    margin: 0 0.5rem;
    max-height: 92vh;
  }
  .ted-header,
  .ted-body,
  .ted-footer {
    padding-left: 1rem;
    padding-right: 1rem;
  }
  .ted-footer {
    flex-direction: column-reverse;
  }
  .ted-footer button {
    width: 100%;
  }
}
</style>
