<template>
  <div :class="['eval-card', 'editorial-card', 'reveal-stagger', { 'eval-card--loading': loading, 'eval-card--collapsed': collapsed }]">
    <!-- 紧凑标题栏 -->
    <div class="eval-card__header" @click="toggleCollapse">
      <div class="eval-card__header-left">
        <span class="eval-card__title editorial-title">质量评估</span>
        <!-- message_id 标签（可复制） -->
        <span
          v-if="evaluation?.message_id"
          class="eval-card__msg-id font-jet"
          @click.stop="copyMessageId"
          :title="'点击复制消息ID: ' + evaluation.message_id"
        >
          #{{ evaluation.message_id }}
        </span>
        <!-- 总分徽章 -->
        <span v-if="evaluation" class="eval-card__score-badge font-jet" :style="{ background: scoreColor(evaluation.auto_score) }">
          {{ evaluation.auto_score?.toFixed(0) }}
        </span>
      </div>
      <div class="eval-card__header-right">
        <button
          v-if="!evaluation && !loading"
          class="eval-card__btn-eval"
          @click.stop="runEvaluation"
        >
          评估
        </button>
        <button
          v-if="evaluation"
          class="eval-card__btn-refresh"
          @click.stop="runEvaluation"
          title="重新评估"
        >
          ↻
        </button>
        <span v-if="evaluation" class="eval-card__toggle">
          {{ collapsed ? '▼' : '▲' }}
        </span>
      </div>
    </div>

    <!-- 加载状态 -->
    <div v-if="loading" class="eval-card__loading">
      <div class="eval-card__spinner"></div>
      <span>评估中...</span>
    </div>

    <!-- 展开的详情内容 -->
    <div v-if="(evaluation || llmEvaluation) && !loading && !collapsed" class="eval-card__content">
      <!-- 评估模式切换 -->
      <div class="eval-card__mode-switch">
        <button
          class="eval-card__mode-btn"
          :class="{ active: evalMode === 'rule' }"
          @click.stop="evalMode = 'rule'"
        >
          规则评估
        </button>
        <button
          class="eval-card__mode-btn"
          :class="{ active: evalMode === 'llm' }"
          @click.stop="evalMode = 'llm'"
        >
          LLM 评估
        </button>
      </div>

      <!-- 规则评估内容 -->
      <div v-if="evalMode === 'rule' && evaluation">
        <!-- 维度分数 -->
        <div class="eval-card__dimensions">
          <div v-for="(dim, key) in dimensionScores" :key="key" class="eval-card__dimension">
            <span class="eval-card__dim-icon">{{ dim.icon }}</span>
            <span class="eval-card__dim-name">{{ dim.name }}</span>
            <span class="eval-card__dim-score font-jet" :style="{ color: scoreColor(dim.score) }">{{ dim.score }}</span>
            <div class="eval-card__dim-bar">
              <div class="eval-card__dim-bar-fill" :style="{ width: dim.score + '%', backgroundColor: scoreColor(dim.score) }"></div>
            </div>
          </div>
        </div>

        <!-- 元数据 -->
        <div class="eval-card__meta">
          <span class="eval-card__meta-tag font-jet">{{ complexityLabel }}</span>
          <span class="eval-card__meta-tag font-jet">{{ evaluation.specialist_count || 0 }}专家</span>
          <span v-if="evaluation.duration_ms" class="eval-card__meta-tag font-jet">{{ formatDuration(evaluation.duration_ms) }}</span>
        </div>

        <!-- 改进建议 -->
        <div v-if="suggestions.length > 0" class="eval-card__suggestions">
          <div class="eval-card__suggestions-header">
            <span class="eval-card__suggestions-title">优化建议</span>
          </div>
          <div class="eval-card__suggestions-list">
            <div v-for="(s, idx) in suggestions" :key="idx" class="eval-card__suggestion-item reveal-stagger">{{ s }}</div>
          </div>
        </div>

        <!-- LLM 评估按钮 -->
        <div class="eval-card__actions">
          <button class="eval-card__btn-llm" @click.stop="runLLMEvaluation" :disabled="llmLoading">
            {{ llmLoading ? '评估中...' : 'LLM 深度评估' }}
          </button>
        </div>
      </div>

      <!-- LLM 评估内容 -->
      <div v-if="evalMode === 'llm'">
        <div v-if="llmLoading" class="eval-card__loading">
          <div class="eval-card__spinner"></div>
          <span>LLM 评估中，请稍候...</span>
        </div>
        <div v-else-if="llmEvaluation">
          <!-- LLM 维度分数 -->
          <div class="eval-card__dimensions">
            <div v-for="(dim, key) in llmDimensionScores" :key="key" class="eval-card__dimension">
              <span class="eval-card__dim-icon">{{ dim.icon }}</span>
              <span class="eval-card__dim-name">{{ dim.name }}</span>
              <span class="eval-card__dim-score font-jet" :style="{ color: scoreColor(dim.score) }">{{ dim.score }}</span>
              <div class="eval-card__dim-bar">
                <div class="eval-card__dim-bar-fill" :style="{ width: dim.score + '%', backgroundColor: scoreColor(dim.score) }"></div>
              </div>
            </div>
          </div>

          <!-- 优点 -->
          <div v-if="llmStrengths.length > 0" class="eval-card__feedback">
            <div class="eval-card__feedback-title">优点</div>
            <div v-for="(s, idx) in llmStrengths" :key="idx" class="eval-card__feedback-item reveal-stagger">{{ s }}</div>
          </div>

          <!-- 缺点 -->
          <div v-if="llmWeaknesses.length > 0" class="eval-card__feedback eval-card__feedback--warning">
            <div class="eval-card__feedback-title">不足</div>
            <div v-for="(w, idx) in llmWeaknesses" :key="idx" class="eval-card__feedback-item reveal-stagger">{{ w }}</div>
          </div>

          <!-- 建议 -->
          <div v-if="llmSuggestions.length > 0" class="eval-card__suggestions">
            <div class="eval-card__suggestions-header">
              <span class="eval-card__suggestions-title">改进建议</span>
            </div>
            <div class="eval-card__suggestions-list">
              <div v-for="(s, idx) in llmSuggestions" :key="idx" class="eval-card__suggestion-item reveal-stagger">{{ s }}</div>
            </div>
          </div>
        </div>
        <div v-else class="eval-card__empty">
          <button class="eval-card__btn-llm" @click.stop="runLLMEvaluation">
            开始 LLM 评估
          </button>
        </div>
      </div>

      <!-- 进化触发状态 -->
      <div v-if="evolutionResult" class="eval-card__evolution">
        <span v-if="evolutionResult.feedback_triggered" class="eval-card__evolution-tag eval-card__evolution-tag--success">反馈学习</span>
        <span v-if="evolutionResult.bad_case_marked" class="eval-card__evolution-tag eval-card__evolution-tag--warning">Bad Case</span>
        <span v-if="evolutionResult.eval_suggested" class="eval-card__evolution-tag eval-card__evolution-tag--success">Eval建议</span>
      </div>

      <!-- 用户评分 -->
      <div class="eval-card__user-rating">
        <div v-if="!userScoreSubmitted" class="eval-card__stars">
          <button
            v-for="star in 5"
            :key="star"
            class="eval-card__star"
            :class="{ 'eval-card__star--active': star <= userScore }"
            @click.stop="setUserScore(star)"
          >
            {{ star <= userScore ? '★' : '☆' }}
          </button>
          <button
            v-if="userScore > 0"
            class="eval-card__btn-submit"
            @click.stop="submitUserScore"
          >
            提交
          </button>
        </div>
        <div v-else class="eval-card__user-score-display">
          <span class="eval-card__user-score-label terminal-label">您的评价:</span>
          <span class="eval-card__user-score-stars">
            <span v-for="star in 5" :key="star" class="eval-card__star-display">
              {{ star <= userScore ? '★' : '☆' }}
            </span>
          </span>
          <span class="eval-card__user-score-value font-jet">{{ userScore }}/5</span>
        </div>
      </div>
    </div>

    <!-- 无评估状态 -->
    <div v-if="!evaluation && !loading && !collapsed" class="eval-card__empty">
      <span class="eval-card__empty-text">点击"评估"分析对话质量</span>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { evaluateConversation, getConversationEvaluation, submitConversationUserScore, evaluateWithLLMAgent, getLLMEvaluation } from '../../api'
import { useToast } from '../../composables/useToast'

const props = defineProps({
  conversationId: {
    type: Number,
    required: true,
  },
  messageId: {
    type: Number,
    default: null,
  },
})

const { showToast } = useToast()

const loading = ref(false)
const llmLoading = ref(false)
const collapsed = ref(true)  // 默认折叠
const evaluation = ref(null)
const llmEvaluation = ref(null)
const userScore = ref(0)
const userScoreSubmitted = ref(false)
const evolutionResult = ref(null)
const evalMode = ref('rule')  // 'rule' 或 'llm'

// 维度配置（规则评估）
const dimensionConfig = {
  execution: { name: '执行', icon: '⚡' },
  data: { name: '数据', icon: '📊' },
  collaboration: { name: '协作', icon: '🤝' },
  response: { name: '响应', icon: '📝' },
}

// LLM 评估维度配置
const llmDimensionConfig = {
  data_accuracy: { name: '数据准确', icon: '📊' },
  logic_consistency: { name: '逻辑一致', icon: '🧠' },
  actionability: { name: '可执行', icon: '🎯' },
  user_understanding: { name: '用户理解', icon: '👤' },
}

// 计算属性
const dimensionScores = computed(() => {
  if (!evaluation.value?.auto_score_breakdown) return {}

  // 解析 JSON 字符串
  let breakdown = evaluation.value.auto_score_breakdown
  if (typeof breakdown === 'string') {
    try {
      breakdown = JSON.parse(breakdown)
    } catch (e) {
      return {}
    }
  }

  const result = {}
  for (const [key, config] of Object.entries(dimensionConfig)) {
    if (breakdown[key] !== undefined) {
      result[key] = {
        ...config,
        score: Math.round(breakdown[key]),
      }
    }
  }

  return result
})

const complexityLabel = computed(() => {
  const labels = {
    simple: '简单',
    medium: '中等',
    complex: '复杂',
    chat: '闲聊',
  }
  return labels[evaluation.value?.complexity] || '未知'
})

const suggestions = computed(() => {
  if (!evaluation.value?.suggestions) return []

  let result = evaluation.value.suggestions

  // 如果是字符串，尝试解析 JSON
  if (typeof result === 'string') {
    try {
      result = JSON.parse(result)
    } catch (e) {
      // 如果解析失败，按逗号分割
      result = result.split(',').map(s => s.trim()).filter(Boolean)
    }
  }

  // 确保是数组
  if (!Array.isArray(result)) {
    return []
  }

  return result
})

// 方法
function toggleCollapse() {
  collapsed.value = !collapsed.value
}

function scoreColor(score) {
  if (score >= 80) return '#52c41a'
  if (score >= 60) return '#faad14'
  if (score >= 40) return '#fa8c16'
  return '#ff4d4f'
}

function formatDuration(ms) {
  if (!ms) return '-'
  const seconds = Math.floor(ms / 1000)
  if (seconds < 60) return `${seconds}秒`
  const minutes = Math.floor(seconds / 60)
  const remaining = seconds % 60
  return `${minutes}分${remaining}秒`
}

function setUserScore(score) {
  userScore.value = score
}

function copyMessageId() {
  if (!evaluation.value?.message_id) return

  const text = String(evaluation.value.message_id)
  navigator.clipboard.writeText(text).then(() => {
    showToast('已复制消息ID', 'success')
  }).catch(() => {
    // 降级方案
    const input = document.createElement('input')
    input.value = text
    document.body.appendChild(input)
    input.select()
    document.execCommand('copy')
    document.body.removeChild(input)
    showToast('已复制消息ID', 'success')
  })
}

async function runEvaluation() {
  loading.value = true
  evolutionResult.value = null
  collapsed.value = false  // 评估时展开
  evalMode.value = 'rule'
  try {
    const { data } = await evaluateConversation(props.conversationId)
    if (data.ok) {
      evaluation.value = data.evaluation
      evolutionResult.value = data.evolution || null
      showToast('规则评估完成', 'success')
    } else {
      showToast('评估失败', 'error')
    }
  } catch (e) {
    console.error('评估失败:', e)
    showToast('评估失败: ' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    loading.value = false
  }
}

// LLM 评估
async function runLLMEvaluation() {
  llmLoading.value = true
  collapsed.value = false
  evalMode.value = 'llm'
  try {
    const { data } = await evaluateWithLLMAgent('conversation', props.conversationId, props.messageId)
    if (data.ok) {
      llmEvaluation.value = data.evaluation
      showToast('LLM 评估完成', 'success')
    } else {
      showToast('LLM 评估失败', 'error')
    }
  } catch (e) {
    console.error('LLM 评估失败:', e)
    showToast('LLM 评估失败: ' + (e.response?.data?.detail || e.message), 'error')
  } finally {
    llmLoading.value = false
  }
}

// 解析 JSON 字符串
function parseJsonField(field) {
  if (!field) return null
  if (typeof field === 'object') return field
  try {
    return JSON.parse(field)
  } catch {
    return null
  }
}

// LLM 评估维度分数
const llmDimensionScores = computed(() => {
  if (!llmEvaluation.value) return {}

  // 从 dimensions_json 解析
  const dimensions = parseJsonField(llmEvaluation.value.dimensions_json) || llmEvaluation.value.dimensions || {}

  const result = {}
  for (const [key, config] of Object.entries(llmDimensionConfig)) {
    const dim = dimensions[key]
    if (dim) {
      result[key] = {
        ...config,
        score: dim.score || 0,
        evidence: dim.evidence || '',
        issues: dim.issues || [],
      }
    }
  }
  return result
})

// LLM 评估建议
const llmSuggestions = computed(() => {
  if (!llmEvaluation.value) return []
  return parseJsonField(llmEvaluation.value.suggestions_json) || llmEvaluation.value.suggestions || []
})

// LLM 评估优点
const llmStrengths = computed(() => {
  if (!llmEvaluation.value) return []
  return parseJsonField(llmEvaluation.value.strengths_json) || llmEvaluation.value.strengths || []
})

// LLM 评估缺点
const llmWeaknesses = computed(() => {
  if (!llmEvaluation.value) return []
  return parseJsonField(llmEvaluation.value.weaknesses_json) || llmEvaluation.value.weaknesses || []
})

async function submitUserScore() {
  try {
    const { data } = await submitConversationUserScore(
      props.conversationId,
      userScore.value,
      {},
      ''
    )
    if (data.ok) {
      userScoreSubmitted.value = true
      showToast('感谢评价！', 'success')
    }
  } catch (e) {
    showToast('提交失败', 'error')
  }
}

// 加载评估数据
async function loadEvaluation() {
  if (!props.conversationId) {
    evaluation.value = null
    llmEvaluation.value = null
    return
  }

  // 加载规则评估
  try {
    const { data } = await getConversationEvaluation(props.conversationId, props.messageId)
    if (data.ok && data.evaluation) {
      evaluation.value = data.evaluation

      // 检查是否已有用户评分
      if (data.evaluation.user_score) {
        userScore.value = data.evaluation.user_score
        userScoreSubmitted.value = true
      }
    } else {
      evaluation.value = null
    }
  } catch (e) {
    evaluation.value = null
  }

  // 加载 LLM 评估
  try {
    const { data } = await getLLMEvaluation('conversation', props.conversationId, props.messageId)
    if (data.ok && data.evaluation) {
      llmEvaluation.value = data.evaluation
      // 如果有 LLM 评估，默认显示 LLM 模式
      if (data.evaluation.total_score > 0) {
        evalMode.value = 'llm'
      }
    } else {
      llmEvaluation.value = null
    }
  } catch (e) {
    llmEvaluation.value = null
  }
}

// 监听 conversationId 和 messageId 变化
watch([() => props.conversationId, () => props.messageId], () => {
  // 重置状态
  evaluation.value = null
  llmEvaluation.value = null
  userScore.value = 0
  userScoreSubmitted.value = false
  evolutionResult.value = null
  collapsed.value = true
  evalMode.value = 'rule'

  // 重新加载
  loadEvaluation()
})

// 初始化
onMounted(() => {
  loadEvaluation()
})
</script>

<style scoped>
.eval-card {
  background: var(--bg-primary, #fff);
  border: 1px solid var(--border-color, #e8e8e8);
  border-radius: 8px;
  margin-top: 12px;
  overflow: hidden;
  transition: all 0.2s;
}

.eval-card--loading {
  opacity: 0.7;
}

/* 标题栏 */
.eval-card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px;
  cursor: pointer;
  user-select: none;
}

.eval-card__header:hover {
  background: var(--bg-secondary, #f5f5f5);
}

.eval-card__header-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.eval-card__header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.eval-card__icon {
  font-size: 14px;
}

.eval-card__title {
  font-size: 13px;
  font-weight: inherit;
  color: var(--text-primary, #333);
}

.eval-card__score-badge {
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 12px;
  font-weight: 600;
  color: white;
  min-width: 28px;
  text-align: center;
}

.eval-card__msg-id {
  font-size: 11px;
  color: var(--text-secondary, #999);
  background: var(--bg-secondary, #f0f0f0);
  padding: 1px 6px;
  border-radius: 4px;
  cursor: pointer;
  transition: background 0.2s;
}

.eval-card__msg-id:hover {
  background: var(--border-color, #e0e0e0);
}

.eval-card__btn-eval,
.eval-card__btn-refresh {
  padding: 4px 10px;
  border: none;
  border-radius: 4px;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.2s;
}

.eval-card__btn-eval {
  background: var(--primary-color, #1890ff);
  color: white;
}

.eval-card__btn-eval:hover {
  background: var(--primary-hover, #40a9ff);
}

.eval-card__btn-refresh {
  background: transparent;
  color: var(--text-secondary, #666);
  font-size: 14px;
  padding: 2px 6px;
}

.eval-card__btn-refresh:hover {
  background: var(--bg-secondary, #f5f5f5);
}

.eval-card__toggle {
  font-size: 10px;
  color: var(--text-secondary, #666);
}

/* 加载状态 */
.eval-card__loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 16px;
  color: var(--text-secondary, #666);
  font-size: 12px;
}

.eval-card__spinner {
  width: 14px;
  height: 14px;
  border: 2px solid var(--border-color, #e8e8e8);
  border-top-color: var(--primary-color, #1890ff);
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* 展开内容 */
.eval-card__content {
  padding: 0 12px 12px;
}

/* 维度分数（紧凑横向） */
.eval-card__dimensions {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 8px;
  margin-bottom: 10px;
}

.eval-card__dimension {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
}

.eval-card__dim-icon {
  font-size: 12px;
}

.eval-card__dim-name {
  color: var(--text-secondary, #666);
  min-width: 24px;
}

.eval-card__dim-score {
  font-weight: 600;
  min-width: 24px;
  text-align: right;
  font-size: 11px;
}

.eval-card__dim-bar {
  flex: 1;
  height: 4px;
  background: var(--bg-secondary, #f5f5f5);
  border-radius: 2px;
  overflow: hidden;
}

.eval-card__dim-bar-fill {
  height: 100%;
  border-radius: 2px;
  transition: width 0.5s ease;
}

/* 元数据标签 */
.eval-card__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 10px;
}

.eval-card__meta-tag {
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 11px;
  background: var(--bg-secondary, #f5f5f5);
  color: var(--text-secondary, #666);
}

.eval-card__meta-tag--success {
  background: #f6ffed;
  color: #52c41a;
}

.eval-card__meta-tag--warning {
  background: #fffbe6;
  color: #faad14;
}

/* 改进建议 */
.eval-card__suggestions {
  padding: 8px;
  background: var(--bg-secondary, #f5f5f5);
  border-radius: 6px;
  margin-bottom: 10px;
}

.eval-card__suggestions-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
}

.eval-card__suggestions-icon {
  font-size: 12px;
}

.eval-card__suggestions-title {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-primary, #333);
}

.eval-card__suggestions-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.eval-card__suggestion-item {
  font-size: 11px;
  color: var(--text-secondary, #666);
  line-height: 1.5;
  padding-left: 16px;
  position: relative;
}

.eval-card__suggestion-item::before {
  content: '•';
  position: absolute;
  left: 4px;
  color: var(--primary-color, #1890ff);
}

/* 评估模式切换 */
.eval-card__mode-switch {
  display: flex;
  gap: 4px;
  margin-bottom: 10px;
  padding: 2px;
  background: var(--bg-secondary, #f0f0f0);
  border-radius: 6px;
}

.eval-card__mode-btn {
  flex: 1;
  padding: 6px 8px;
  border: none;
  border-radius: 4px;
  font-size: 11px;
  cursor: pointer;
  transition: all 0.2s;
  background: transparent;
  color: var(--text-secondary, #666);
}

.eval-card__mode-btn.active {
  background: white;
  color: var(--text-primary, #333);
  box-shadow: 0 1px 3px rgba(0,0,0,0.1);
}

.eval-card__mode-btn:hover:not(.active) {
  background: var(--bg-secondary, #e8e8e8);
}

/* LLM 评估按钮 */
.eval-card__actions {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--border-color, #e8e8e8);
}

.eval-card__btn-llm {
  width: 100%;
  padding: 8px 12px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  border: none;
  border-radius: 6px;
  font-size: 12px;
  cursor: pointer;
  transition: opacity 0.2s;
}

.eval-card__btn-llm:hover:not(:disabled) {
  opacity: 0.9;
}

.eval-card__btn-llm:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

/* LLM 评估反馈 */
.eval-card__feedback {
  margin-bottom: 10px;
  padding: 8px;
  background: #f6ffed;
  border-radius: 6px;
  border-left: 3px solid #52c41a;
}

.eval-card__feedback--warning {
  background: #fffbe6;
  border-left-color: #faad14;
}

.eval-card__feedback-title {
  font-size: 12px;
  font-weight: 600;
  margin-bottom: 6px;
  color: var(--text-primary, #333);
}

.eval-card__feedback-item {
  font-size: 11px;
  color: var(--text-secondary, #666);
  line-height: 1.5;
  padding-left: 12px;
  position: relative;
}

.eval-card__feedback-item::before {
  content: '•';
  position: absolute;
  left: 2px;
}

/* 进化状态 */
.eval-card__evolution {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 10px;
}

.eval-card__evolution-tag {
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 11px;
}

.eval-card__evolution-tag--success {
  background: #f6ffed;
  color: #52c41a;
}

.eval-card__evolution-tag--warning {
  background: #fffbe6;
  color: #faad14;
}

/* 用户评分 */
.eval-card__user-rating {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-top: 10px;
  border-top: 1px solid var(--border-color, #e8e8e8);
}

.eval-card__stars {
  display: flex;
  gap: 2px;
}

.eval-card__star {
  background: none;
  border: none;
  font-size: 16px;
  cursor: pointer;
  color: var(--border-color, #d9d9d9);
  transition: color 0.2s;
  padding: 0;
  line-height: 1;
}

.eval-card__star--active {
  color: #faad14;
}

.eval-card__star:hover {
  color: #faad14;
}

.eval-card__btn-submit {
  padding: 4px 10px;
  background: var(--primary-color, #1890ff);
  color: white;
  border: none;
  border-radius: 4px;
  font-size: 11px;
  cursor: pointer;
  transition: background 0.2s;
}

.eval-card__btn-submit:hover {
  background: var(--primary-hover, #40a9ff);
}

/* 已评价状态 */
.eval-card__user-score-display {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--text-secondary, #666);
}

.eval-card__user-score-label {
  font-size: inherit;
}

.eval-card__user-score-stars {
  display: flex;
  gap: 2px;
}

.eval-card__star-display {
  color: #faad14;
  font-size: 14px;
}

.eval-card__user-score-value {
  font-weight: 600;
  color: var(--primary-color, #1890ff);
}

/* 空状态 */
.eval-card__empty {
  padding: 12px;
  text-align: center;
}

.eval-card__empty-text {
  font-size: 12px;
  color: var(--text-secondary, #666);
}

/* 折叠状态 */
.eval-card--collapsed .eval-card__header {
  border-bottom: none;
}

/* 移动端适配 */
@media (max-width: 768px) {
  .eval-card__dimensions {
    grid-template-columns: 1fr;
    gap: 6px;
  }

  .eval-card__dimension {
    gap: 4px;
  }

  .eval-card__dim-name {
    min-width: 40px;
  }

  .eval-card__mode-switch {
    flex-direction: row;
  }

  .eval-card__mode-btn {
    font-size: 10px;
    padding: 5px 6px;
  }

  .eval-card__header {
    padding: 8px 10px;
  }

  .eval-card__title {
    font-size: 12px;
  }

  .eval-card__score-badge {
    font-size: 10px;
    padding: 1px 6px;
  }

  .eval-card__meta {
    flex-wrap: wrap;
    gap: 4px;
  }

  .eval-card__meta-tag {
    font-size: 10px;
    padding: 1px 4px;
  }

  .eval-card__suggestions {
    padding: 6px;
  }

  .eval-card__suggestion-item {
    font-size: 10px;
  }

  .eval-card__user-rating {
    flex-wrap: wrap;
    gap: 6px;
  }

  .eval-card__star {
    font-size: 14px;
  }

  .eval-card__btn-submit {
    font-size: 10px;
    padding: 3px 8px;
  }
}
</style>
