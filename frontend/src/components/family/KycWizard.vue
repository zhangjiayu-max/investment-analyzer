<script setup>
import { ref, watch, computed, reactive } from 'vue'
import {
  getKycQuestionnaire, submitKyc, updateProfile, getProfile,
  listInvestmentGoals, createInvestmentGoal,
} from '../../api'

const props = defineProps({
  visible: Boolean,
})
const emit = defineEmits(['close', 'completed'])

const loading = ref(false)
const submitting = ref(false)
const currentStep = ref(1)  // 1=风险偏好, 2=财务画像, 3=投资目标
const questions = ref([])
const dimensions = ref({})
const answers = ref({})        // {dim: value | [values]} Step 1
const financial = reactive({   // Step 2 财务画像 2.0
  monthly_income: null,
  monthly_expense: null,
  emergency_fund_months: null,
  target_equity_ratio: null,
  max_single_position_pct: null,
  primary_goal: '',
  fund_usage: '',
  liquidity_needs: '',
  liabilities_summary: '',
  behavior_biases: [],
})
const goals = ref([])  // Step 3 投资目标列表（含 _isNew 标记）
const errorMsg = ref('')

// 选项常量
const PRIMARY_GOAL_OPTIONS = ['资产保值', '稳健增值', '积极增值', '养老储备', '教育储备', '购房储备']
const FUND_USAGE_OPTIONS = ['闲置资金', '工资结余', '理财资金', '应急备用']
const LIQUIDITY_OPTIONS = ['随时可能动用', '6 个月内不动', '1 年内不动', '长期不动']
const LIABILITIES_OPTIONS = ['无负债', '房贷', '车贷', '信用卡', '其他']
const BEHAVIOR_BIAS_OPTIONS = ['追涨杀跌', '过度自信', '损失厌恶', '锚定效应', '羊群效应', '无']
const GOAL_TYPE_OPTIONS = ['退休', '教育', '购房', '财富传承', '财务自由', '其他']

const monthly_surplus = computed(() => {
  const inc = financial.monthly_income ?? 0
  const exp = financial.monthly_expense ?? 0
  if (!inc && !exp) return null
  return Math.round((inc - exp) * 100) / 100
})

const STEPS = [
  { n: 1, label: '风险偏好' },
  { n: 2, label: '财务画像' },
  { n: 3, label: '投资目标' },
]

watch(() => props.visible, async (v) => {
  if (v) {
    currentStep.value = 1
    await loadData()
  }
})

async function loadData() {
  loading.value = true
  errorMsg.value = ''
  try {
    const { data } = await getKycQuestionnaire()
    questions.value = data.questionnaire.questions
    dimensions.value = data.questionnaire.dimensions
    // 预填 Step 1
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

    // 预填 Step 2（财务画像 2.0）
    try {
      const { data: fullProfile } = await getProfile()
      financial.monthly_income = fullProfile.monthly_income ?? null
      financial.monthly_expense = fullProfile.monthly_expense ?? null
      financial.emergency_fund_months = fullProfile.emergency_fund_months ?? null
      financial.target_equity_ratio = fullProfile.target_equity_ratio ?? null
      financial.max_single_position_pct = fullProfile.max_single_position_pct ?? null
      financial.primary_goal = fullProfile.primary_goal || ''
      financial.fund_usage = fullProfile.fund_usage || ''
      financial.liquidity_needs = fullProfile.liquidity_needs || ''
      financial.liabilities_summary = fullProfile.liabilities_summary || ''
      financial.behavior_biases = Array.isArray(fullProfile.behavior_biases)
        ? [...fullProfile.behavior_biases]
        : []
    } catch (e) {
      // 忽略，Step 2 留空
    }

    // 预填 Step 3（已有投资目标）
    try {
      const { data: goalData } = await listInvestmentGoals()
      goals.value = (goalData.items || []).map(g => ({ ...g, _isNew: false }))
    } catch (e) {
      goals.value = []
    }
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

function toggleBehaviorBias(bias) {
  const arr = Array.isArray(financial.behavior_biases) ? [...financial.behavior_biases] : []
  // 「无」是互斥项
  if (bias === '无') {
    financial.behavior_biases = arr.includes('无') ? [] : ['无']
    return
  }
  const filtered = arr.filter(b => b !== '无')
  const idx = filtered.indexOf(bias)
  if (idx >= 0) filtered.splice(idx, 1)
  else filtered.push(bias)
  financial.behavior_biases = filtered
}

const newGoal = reactive({
  goal_type: '',
  target_amount: null,
  target_date: '',
  monthly_contribution: null,
})

function addGoal() {
  if (!newGoal.goal_type) {
    errorMsg.value = '请先选择目标类型'
    return
  }
  goals.value.push({
    goal_type: newGoal.goal_type,
    target_amount: newGoal.target_amount,
    target_date: newGoal.target_date,
    monthly_contribution: newGoal.monthly_contribution,
    priority: goals.value.length,
    _isNew: true,
  })
  newGoal.goal_type = ''
  newGoal.target_amount = null
  newGoal.target_date = ''
  newGoal.monthly_contribution = null
  errorMsg.value = ''
}

function removeGoal(idx) {
  goals.value.splice(idx, 1)
}

const step1Valid = computed(() => {
  return !!answers.value.risk_tolerance && !!answers.value.loss_tolerance
})

const canNext = computed(() => {
  if (submitting.value || loading.value) return false
  if (currentStep.value === 1) return step1Valid.value
  return true
})

function nextStep() {
  if (!canNext.value) return
  errorMsg.value = ''
  if (currentStep.value < 3) currentStep.value++
}

function prevStep() {
  errorMsg.value = ''
  if (currentStep.value > 1) currentStep.value--
}

async function handleSubmit() {
  submitting.value = true
  errorMsg.value = ''
  try {
    // Step 1：提交 KYC 问卷
    const { data: kycData } = await submitKyc(answers.value)

    // Step 2：更新财务画像字段（仅传非空字段）
    const finFields = {}
    if (financial.monthly_income != null) finFields.monthly_income = financial.monthly_income
    if (financial.monthly_expense != null) finFields.monthly_expense = financial.monthly_expense
    if (monthly_surplus.value != null) finFields.monthly_surplus = monthly_surplus.value
    if (financial.emergency_fund_months != null) finFields.emergency_fund_months = financial.emergency_fund_months
    if (financial.target_equity_ratio != null) finFields.target_equity_ratio = financial.target_equity_ratio
    if (financial.max_single_position_pct != null) finFields.max_single_position_pct = financial.max_single_position_pct
    if (financial.primary_goal) finFields.primary_goal = financial.primary_goal
    if (financial.fund_usage) finFields.fund_usage = financial.fund_usage
    if (financial.liquidity_needs) finFields.liquidity_needs = financial.liquidity_needs
    if (financial.liabilities_summary) finFields.liabilities_summary = financial.liabilities_summary
    if (financial.behavior_biases?.length) finFields.behavior_biases = financial.behavior_biases

    if (Object.keys(finFields).length > 0) {
      await updateProfile(finFields)
    }

    // Step 3：批量创建新投资目标（仅提交 _isNew 标记的）
    for (const g of goals.value) {
      if (g._isNew) {
        await createInvestmentGoal({
          goal_type: g.goal_type,
          target_amount: g.target_amount,
          target_date: g.target_date,
          monthly_contribution: g.monthly_contribution,
          priority: g.priority || 0,
        })
      }
    }

    emit('completed', kycData.profile)
    emit('close')
  } catch (e) {
    errorMsg.value = '保存失败：' + (e.response?.data?.detail || e.message || e)
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

          <!-- Stepper -->
          <div class="kyc-stepper">
            <div
              v-for="(s, i) in STEPS"
              :key="s.n"
              :class="['step-item', { active: currentStep === s.n, done: currentStep > s.n }]"
            >
              <div class="step-circle">{{ currentStep > s.n ? '✓' : s.n }}</div>
              <span class="step-label">{{ s.label }}</span>
              <span v-if="i < STEPS.length - 1" class="step-divider"></span>
            </div>
          </div>

          <!-- Loading -->
          <div v-if="loading" class="kyc-loading">
            <div class="spinner"></div>
            <span>加载问卷中…</span>
          </div>

          <!-- Body -->
          <div v-else class="kyc-body">
            <div v-if="errorMsg" class="kyc-error">{{ errorMsg }}</div>

            <!-- Step 1: 风险偏好 -->
            <div v-show="currentStep === 1">
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

            <!-- Step 2: 财务画像 -->
            <div v-show="currentStep === 2" class="financial-form">
              <div class="form-section">
                <h4 class="section-title editorial-title">月度收支</h4>
                <div class="form-grid">
                  <div class="form-row">
                    <label>月收入（元）</label>
                    <input v-model.number="financial.monthly_income" type="number" placeholder="如 20000" />
                  </div>
                  <div class="form-row">
                    <label>月支出（元）</label>
                    <input v-model.number="financial.monthly_expense" type="number" placeholder="如 12000" />
                  </div>
                  <div class="form-row">
                    <label>月结余（自动计算）</label>
                    <input :value="monthly_surplus ?? ''" disabled placeholder="由收入-支出计算" />
                  </div>
                  <div class="form-row">
                    <label>应急储备（月数）</label>
                    <input v-model.number="financial.emergency_fund_months" type="number" placeholder="如 6" min="0" />
                  </div>
                </div>
              </div>

              <div class="form-section">
                <h4 class="section-title editorial-title">配置偏好</h4>
                <div class="form-grid">
                  <div class="form-row">
                    <label>目标权益占比：{{ financial.target_equity_ratio ?? '—' }}%</label>
                    <input v-model.number="financial.target_equity_ratio" type="range" min="0" max="100" step="5" />
                  </div>
                  <div class="form-row">
                    <label>单一持仓上限：{{ financial.max_single_position_pct ?? '—' }}%</label>
                    <input v-model.number="financial.max_single_position_pct" type="range" min="0" max="50" step="5" />
                  </div>
                </div>
              </div>

              <div class="form-section">
                <h4 class="section-title editorial-title">投资偏好</h4>
                <div class="form-grid">
                  <div class="form-row">
                    <label>主要投资目标</label>
                    <select v-model="financial.primary_goal">
                      <option value="">未选择</option>
                      <option v-for="g in PRIMARY_GOAL_OPTIONS" :key="g" :value="g">{{ g }}</option>
                    </select>
                  </div>
                  <div class="form-row">
                    <label>资金用途</label>
                    <select v-model="financial.fund_usage">
                      <option value="">未选择</option>
                      <option v-for="g in FUND_USAGE_OPTIONS" :key="g" :value="g">{{ g }}</option>
                    </select>
                  </div>
                  <div class="form-row">
                    <label>流动性需求</label>
                    <select v-model="financial.liquidity_needs">
                      <option value="">未选择</option>
                      <option v-for="g in LIQUIDITY_OPTIONS" :key="g" :value="g">{{ g }}</option>
                    </select>
                  </div>
                  <div class="form-row">
                    <label>负债概况</label>
                    <select v-model="financial.liabilities_summary">
                      <option value="">未选择</option>
                      <option v-for="g in LIABILITIES_OPTIONS" :key="g" :value="g">{{ g }}</option>
                    </select>
                  </div>
                </div>
              </div>

              <div class="form-section">
                <h4 class="section-title editorial-title">行为偏差（多选）</h4>
                <div class="bias-options">
                  <button
                    v-for="b in BEHAVIOR_BIAS_OPTIONS"
                    :key="b"
                    :class="['bias-chip', { selected: financial.behavior_biases.includes(b) }]"
                    @click="toggleBehaviorBias(b)"
                  >{{ b }}</button>
                </div>
              </div>
            </div>

            <!-- Step 3: 投资目标 -->
            <div v-show="currentStep === 3" class="goals-form">
              <p class="step-intro">添加你的具体投资目标，AI 顾问会据此评估建议对目标的契合度。可添加多个，也可跳过。</p>

              <!-- 已有目标列表 -->
              <div v-if="goals.length > 0" class="goals-list">
                <div v-for="(g, idx) in goals" :key="idx" class="goal-card">
                  <div class="goal-info">
                    <span class="goal-type">{{ g.goal_type }}</span>
                    <span v-if="g.target_amount" class="goal-amount">{{ g.target_amount }} 万元</span>
                    <span v-if="g.target_date" class="goal-date">{{ g.target_date }}</span>
                    <span v-if="g.monthly_contribution" class="goal-monthly">月投入 {{ g.monthly_contribution }} 元</span>
                  </div>
                  <button class="goal-remove" @click="removeGoal(idx)" title="移除">✕</button>
                </div>
              </div>

              <!-- 新目标表单 -->
              <div class="goal-add-form">
                <h4 class="section-title editorial-title">添加目标</h4>
                <div class="form-grid">
                  <div class="form-row">
                    <label>目标类型</label>
                    <select v-model="newGoal.goal_type">
                      <option value="">未选择</option>
                      <option v-for="g in GOAL_TYPE_OPTIONS" :key="g" :value="g">{{ g }}</option>
                    </select>
                  </div>
                  <div class="form-row">
                    <label>目标金额（万元）</label>
                    <input v-model.number="newGoal.target_amount" type="number" placeholder="如 200" min="0" />
                  </div>
                  <div class="form-row">
                    <label>目标日期</label>
                    <input v-model="newGoal.target_date" type="date" />
                  </div>
                  <div class="form-row">
                    <label>月度投入（元）</label>
                    <input v-model.number="newGoal.monthly_contribution" type="number" placeholder="如 5000" min="0" />
                  </div>
                </div>
                <button class="btn-add-goal" @click="addGoal" :disabled="!newGoal.goal_type">+ 添加此目标</button>
              </div>
            </div>
          </div>

          <!-- Footer -->
          <div class="kyc-footer">
            <span class="kyc-tip">
              <template v-if="currentStep === 1">带风险偏好、亏损承受度为核心必填项，其余可后续在对话中自动学习</template>
              <template v-else-if="currentStep === 2">财务画像字段全可选，可后续在对话中自动学习</template>
              <template v-else>投资目标可添加多个或跳过</template>
            </span>
            <div class="kyc-actions">
              <button v-if="currentStep > 1" class="btn-secondary" @click="prevStep" :disabled="submitting">上一步</button>
              <button class="btn-secondary" @click="emit('close')" :disabled="submitting">稍后再说</button>
              <button v-if="currentStep < 3" class="btn-primary" @click="nextStep" :disabled="!canNext">下一步</button>
              <button v-else class="btn-primary" @click="handleSubmit" :disabled="!canNext">
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
  max-width: 640px;
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

/* Stepper */
.kyc-stepper {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0.9rem 1.4rem;
  border-bottom: 1px solid var(--color-border);
  background: var(--color-primary-bg-weak);
}

.step-item {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  color: var(--color-text-muted);
  font-size: 0.78rem;
  transition: all var(--transition-fast);
}
.step-item.active {
  color: var(--color-primary);
  font-weight: 600;
}
.step-item.done {
  color: var(--color-primary);
}

.step-circle {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  border: 1.5px solid var(--color-border-strong);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.72rem;
  background: var(--color-bg-input);
  flex-shrink: 0;
}
.step-item.active .step-circle {
  border-color: var(--color-primary);
  background: var(--color-primary-50);
}
.step-item.done .step-circle {
  border-color: var(--color-primary);
  background: var(--color-primary);
  color: #fff;
}

.step-divider {
  width: 28px;
  height: 1px;
  background: var(--color-border-strong);
  margin-left: 0.4rem;
}
.step-item.done .step-divider {
  background: var(--color-primary);
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

/* Step 1 题目 */
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

/* Step 2/3 表单 */
.financial-form,
.goals-form {
  display: flex;
  flex-direction: column;
  gap: 1.2rem;
}

.form-section {
  display: flex;
  flex-direction: column;
  gap: 0.7rem;
}

.section-title {
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0;
  padding-bottom: 0.4rem;
  border-bottom: 1px solid var(--color-border-light);
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 0.7rem 0.9rem;
}

.form-row {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}

.form-row label {
  font-size: 0.75rem;
  color: var(--color-text-secondary);
  font-weight: 500;
}

.form-row input[type="number"],
.form-row input[type="date"],
.form-row input[type="text"],
.form-row select {
  padding: 0.5rem 0.7rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-bg-input);
  font-size: 0.82rem;
  color: var(--color-text-primary);
  transition: all var(--transition-fast);
  width: 100%;
  font-family: inherit;
}

.form-row input:focus,
.form-row select:focus {
  outline: none;
  border-color: var(--color-primary);
  box-shadow: 0 0 0 2px var(--color-primary-glow);
}

.form-row input[type="range"] {
  width: 100%;
  accent-color: var(--color-primary);
  cursor: pointer;
  padding: 0.4rem 0;
}

.form-row input:disabled {
  background: var(--color-bg-hover);
  color: var(--color-text-muted);
  cursor: not-allowed;
}

.bias-options {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.bias-chip {
  padding: 0.35rem 0.85rem;
  border: 1px solid var(--color-border);
  border-radius: 999px;
  background: var(--color-bg-input);
  color: var(--color-text-secondary);
  font-size: 0.78rem;
  cursor: pointer;
  transition: all var(--transition-fast);
  font-family: inherit;
}

.bias-chip:hover {
  border-color: var(--color-primary);
}

.bias-chip.selected {
  border-color: var(--color-primary);
  background: var(--color-primary-50);
  color: var(--color-primary);
  font-weight: 600;
}

/* Step 3 目标卡片 */
.step-intro {
  font-size: 0.8rem;
  color: var(--color-text-muted);
  margin: 0 0 0.6rem 0;
  line-height: 1.5;
}

.goals-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.goal-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.6rem 0.9rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-input);
}

.goal-info {
  display: flex;
  gap: 0.8rem;
  align-items: center;
  flex-wrap: wrap;
  font-size: 0.8rem;
}

.goal-type {
  font-weight: 600;
  color: var(--color-primary);
}

.goal-amount,
.goal-monthly {
  color: var(--color-text-secondary);
}

.goal-date {
  color: var(--color-text-muted);
  font-size: 0.75rem;
}

.goal-remove {
  background: none;
  border: none;
  color: var(--color-text-muted);
  cursor: pointer;
  padding: 0.2rem 0.4rem;
  border-radius: var(--radius-sm);
  font-size: 0.85rem;
  line-height: 1;
}

.goal-remove:hover {
  background: var(--color-danger-bg);
  color: var(--color-danger);
}

.goal-add-form {
  padding-top: 0.8rem;
  border-top: 1px dashed var(--color-border);
  display: flex;
  flex-direction: column;
  gap: 0.7rem;
}

.btn-add-goal {
  align-self: flex-start;
  padding: 0.45rem 1rem;
  border: 1px dashed var(--color-primary);
  border-radius: var(--radius-md);
  background: var(--color-primary-50);
  color: var(--color-primary);
  font-size: 0.82rem;
  font-weight: 500;
  cursor: pointer;
  transition: all var(--transition-fast);
  font-family: inherit;
}

.btn-add-goal:hover:not(:disabled) {
  background: var(--color-primary-100);
}

.btn-add-goal:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* Footer */
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
  flex-wrap: wrap;
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
  .form-grid {
    grid-template-columns: 1fr;
  }
  .step-item .step-label {
    display: none;
  }
  .step-divider {
    width: 16px;
  }
  .kyc-actions {
    flex-direction: column-reverse;
  }
  .kyc-actions button {
    width: 100%;
  }
}
</style>
