<script setup>
/**
 * OnboardingWelcome — 首次访问引导卡片
 *
 * 首次进入应用时弹出，引导用户了解三大核心入口。
 * 通过 localStorage 'hasVisited' 标记，点击"不再提示"后不再弹出。
 * 复用 Teleport + Transition 模式，保持非 AI 美学风格。
 */
import Icon from './ui/Icon.vue'

const emit = defineEmits(['close', 'navigate'])

const entries = [
  {
    key: 'valuation',
    icon: 'chart',
    title: '估值分析',
    desc: '查看主要指数估值分位，识别低估机会与高估风险',
    page: 'valuation',
  },
  {
    key: 'portfolio',
    icon: 'portfolio',
    title: '持仓管理',
    desc: '导入持仓，获得分散度、风险敞口、费用全景诊断',
    page: 'portfolio',
  },
  {
    key: 'chat',
    icon: 'chat',
    title: 'AI 对话',
    desc: '与多专家 Agent 对话，获取个性化投资建议与共振标记',
    page: 'chat',
  },
]

function handleNavigate(page) {
  emit('navigate', page)
  handleClose()
}

function handleClose() {
  localStorage.setItem('hasVisited', '1')
  emit('close')
}
</script>

<template>
  <Teleport to="body">
    <Transition name="fade">
      <div class="onboarding-mask" @click.self="handleClose">
        <div class="onboarding-card">
          <button class="onboarding-close" @click="handleClose" title="关闭">
            <Icon name="x" size="18" />
          </button>

          <div class="onboarding-header">
            <h2 class="editorial-title-lg">欢迎使用投资分析助手</h2>
            <p class="onboarding-subtitle">
              多 Agent 协作的投资分析系统，融合估值、持仓、机构动向三重信号
            </p>
          </div>

          <div class="onboarding-entries">
            <button
              v-for="e in entries"
              :key="e.key"
              class="entry-card"
              @click="handleNavigate(e.page)"
            >
              <div class="entry-icon">
                <Icon :name="e.icon" size="24" />
              </div>
              <h3 class="entry-title">{{ e.title }}</h3>
              <p class="entry-desc">{{ e.desc }}</p>
              <span class="entry-cta">
                进入
                <Icon name="chevron-right" size="12" />
              </span>
            </button>
          </div>

          <div class="onboarding-footer">
            <button class="btn-secondary" @click="handleClose">不再提示</button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.onboarding-mask {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 9999;
  padding: 1.5rem;
}

.onboarding-card {
  position: relative;
  width: 100%;
  max-width: 720px;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-xl);
  padding: 2rem 2.25rem 1.5rem;
  max-height: 90vh;
  overflow-y: auto;
}

.onboarding-close {
  position: absolute;
  top: 0.75rem;
  right: 0.75rem;
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-sm);
  color: var(--color-text-muted);
  transition: all var(--transition-fast);
}
.onboarding-close:hover {
  background: var(--color-bg-hover);
  color: var(--color-text-primary);
}

.onboarding-header {
  text-align: center;
  margin-bottom: 1.75rem;
}
.onboarding-header .editorial-title-lg {
  font-size: 1.375rem;
  margin: 0 0 0.5rem;
}
.onboarding-subtitle {
  font-size: 0.875rem;
  color: var(--color-text-secondary);
  margin: 0;
  line-height: 1.6;
}

.onboarding-entries {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 1rem;
  margin-bottom: 1.75rem;
}

.entry-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  padding: 1.25rem 1rem;
  background: var(--color-bg-input);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  transition: all var(--transition-normal);
  cursor: pointer;
}
.entry-card:hover {
  border-color: var(--color-primary-border);
  background: var(--color-primary-bg-weak);
  transform: translateY(-2px);
  box-shadow: var(--shadow-md);
}

.entry-icon {
  width: 48px;
  height: 48px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: var(--color-primary-bg);
  color: var(--color-primary);
  margin-bottom: 0.75rem;
}

.entry-title {
  font-size: 1rem;
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0 0 0.375rem;
}

.entry-desc {
  font-size: 0.75rem;
  color: var(--color-text-secondary);
  line-height: 1.55;
  margin: 0 0 0.75rem;
  flex: 1;
}

.entry-cta {
  display: inline-flex;
  align-items: center;
  gap: 0.2rem;
  font-size: 0.75rem;
  font-weight: 500;
  color: var(--color-primary);
}

.onboarding-footer {
  text-align: center;
}
.btn-secondary {
  padding: 0.5rem 1.25rem;
  border-radius: var(--radius-sm);
  border: 1px solid var(--color-border);
  background: transparent;
  color: var(--color-text-secondary);
  font-size: 0.8125rem;
  cursor: pointer;
  transition: all var(--transition-fast);
}
.btn-secondary:hover {
  background: var(--color-bg-hover);
  color: var(--color-text-primary);
}

.fade-enter-active, .fade-leave-active {
  transition: opacity var(--transition-normal);
}
.fade-enter-from, .fade-leave-to {
  opacity: 0;
}

@media (max-width: 640px) {
  .onboarding-entries {
    grid-template-columns: 1fr;
  }
  .onboarding-card {
    padding: 1.5rem 1.25rem 1rem;
  }
}
</style>
