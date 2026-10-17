<script setup>
import { computed, onMounted, ref } from 'vue'
import { getTTFundDependencyStatus, startTTFundLogin } from '../../api/dependencies'
import ConfirmDialog from '../layout/ConfirmDialog.vue'

const status = ref(null)
const checking = ref(false)
const loggingIn = ref(false)
const confirmLogin = ref(false)
const loginMessage = ref('')
const loginError = ref(false)

const state = computed(() => {
  if (checking.value) return 'checking'
  if (!status.value || status.value.ok === false || status.value.state === 'error') return 'error'
  if (status.value.installed === false) return 'not_installed'
  if (status.value.logged_in === true) return 'ready'
  if (status.value.state === 'expired' || status.value.expired === true) return 'expired'
  return 'login_required'
})
const label = computed(() => ({
  checking: '正在检查', ready: '已登录', expired: '登录已过期',
  login_required: '需要扫码登录', not_installed: '未安装', error: '状态检查失败',
}[state.value]))
const description = computed(() => ({
  checking: '正在读取天天基金登录状态。',
  ready: '基金分析可使用天天基金数据。',
  expired: '天天基金授权已过期，部分基金分析数据暂不可用，请重新扫码登录。',
  login_required: '完成扫码登录后，基金分析可使用天天基金数据。',
  not_installed: '天天基金依赖尚未安装，请联系系统管理员配置后刷新状态。',
  error: '暂时无法确认天天基金登录状态，请刷新重试。',
}[state.value]))
const canLogin = computed(() => status.value?.installed === true)

async function refreshStatus() {
  if (checking.value) return
  checking.value = true
  try {
    const { data } = await getTTFundDependencyStatus()
    // 只保存状态字段，CLI 原始输出及凭证不会进入展示内容。
    status.value = {
      ok: data?.ok, installed: data?.installed, logged_in: data?.logged_in,
      state: data?.state, expired: data?.expired,
    }
  } catch {
    status.value = { ok: false }
  } finally {
    checking.value = false
  }
}

async function login() {
  if (!confirmLogin.value || loggingIn.value) return
  confirmLogin.value = false
  loggingIn.value = true
  loginError.value = false
  loginMessage.value = '请在打开的浏览器中扫码登录；完成扫码后，系统会更新状态。'
  try {
    const { data } = await startTTFundLogin()
    if (data?.ok !== true) throw new Error('login_incomplete')
    loginMessage.value = '登录流程已完成，请检查登录状态；如未更新，可点击刷新状态。'
    await refreshStatus()
  } catch {
    loginError.value = true
    loginMessage.value = '登录未完成，请确认浏览器中的扫码结果，然后刷新状态或重试。'
  } finally {
    loggingIn.value = false
  }
}

onMounted(refreshStatus)
</script>

<template>
  <section class="dependency-panel editorial-card" data-testid="analysis-dependencies" aria-labelledby="analysis-dependencies-title">
    <div class="dependency-header">
      <h3 id="analysis-dependencies-title" class="editorial-title-lg">分析依赖</h3>
      <span class="dependency-caption">基金数据授权</span>
    </div>
    <div class="dependency-row">
      <div class="dependency-copy" aria-live="polite">
        <div class="dependency-name">
          <strong>天天基金</strong>
          <span :class="['dependency-status', `is-${state}`]">
            <span class="status-dot" aria-hidden="true"></span>{{ label }}
          </span>
        </div>
        <p>{{ description }}</p>
      </div>
      <div class="dependency-actions">
        <button class="btn-outline btn-sm" data-testid="ttfund-refresh" :disabled="checking || loggingIn" @click="refreshStatus">
          {{ checking ? '检查中…' : '刷新状态' }}
        </button>
        <button v-if="canLogin" class="btn-primary btn-sm" data-testid="ttfund-login" :disabled="checking || loggingIn" @click="confirmLogin = true">
          {{ loggingIn ? '等待扫码…' : state === 'ready' ? '重新扫码登录' : '扫码登录' }}
        </button>
      </div>
    </div>
    <p v-if="loginMessage" :class="['login-message', { 'is-error': loginError }]" role="status">{{ loginMessage }}</p>
    <ConfirmDialog
      :visible="confirmLogin"
      title="天天基金扫码登录"
      message="将打开天天基金登录浏览器，请使用本人账号扫码授权。确认开始登录吗？"
      confirm-text="开始扫码登录"
      @confirm="login"
      @cancel="confirmLogin = false"
    />
  </section>
</template>

<style scoped>
.dependency-panel { padding: 1.25rem; border-left: 3px solid var(--color-primary); }
.dependency-header, .dependency-name, .dependency-row, .dependency-actions { display: flex; align-items: center; }
.dependency-header { gap: 0.75rem; margin-bottom: 1rem; }
.dependency-header h3 { font-size: 1.05rem; margin: 0; }
.dependency-caption { font-size: 0.75rem; color: var(--color-text-muted); }
.dependency-row { justify-content: space-between; gap: 1rem; }
.dependency-name { flex-wrap: wrap; gap: 0.75rem; }
.dependency-name strong { font-size: 0.9rem; color: var(--color-text-primary); }
.dependency-copy p, .login-message { font-size: 0.8rem; line-height: 1.7; color: var(--color-text-secondary); margin: 0.5rem 0 0; }
.dependency-status { display: inline-flex; align-items: center; gap: 0.35rem; color: var(--color-warning); font-size: 0.75rem; }
.status-dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.is-ready { color: var(--color-success); }
.is-error { color: var(--color-danger); }
.is-checking, .is-not_installed { color: var(--color-text-muted); }
.dependency-actions { gap: 0.5rem; flex-shrink: 0; }
.login-message { padding: 0.75rem; background: var(--color-info-bg); border-radius: var(--radius-md); margin-top: 1rem; }
.login-message.is-error { color: var(--color-danger); background: var(--color-danger-bg); }
@media (max-width: 640px) {
  .dependency-row { align-items: flex-start; flex-direction: column; }
  .dependency-actions { width: 100%; }
  .dependency-actions button { flex: 1; min-height: 44px; }
}
</style>
