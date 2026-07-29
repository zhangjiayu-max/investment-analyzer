# 移动端性能与加载优化方案

**日期**：2026-07-29
**范围**：性能与加载（渐进式优化，不重构组件结构）
**目标**：首屏 JS 体积降低 40%+，修复移动端复制 bug，统一图表 resize

## 一、问题现状

| # | 问题 | 影响 | 文件 |
|---|------|------|------|
| 1 | 35+ 页面组件静态 import 全量打包 | 首屏 JS 偏大，慢速网络加载慢 | MobileApp.vue:1-42, Home.vue:1-44 |
| 2 | ChatMessage fallbackCopy 移动端失效 | 消息内容复制在 iOS Safari/微信失败 | ChatMessage.vue:373-388 |
| 3 | BondMarket/HealthDashboardV2 各自监听 resize | 重复代码，KeepAlive 切回不 resize | BondMarket.vue:212-223 |
| 4 | 图片 loading="lazy" 但无 decoding="async" | 解码阻塞主线程 | 多处 img 标签 |

## 二、优化方案

### 2.1 页面组件懒加载（核心，预计首屏 JS 降 40%+）

**方案**：MobileApp.vue 和 Home.vue 的页面组件改为 `defineAsyncComponent`，Vite 自动按动态 import 做代码分割。

**改造规则**：
- 首屏必需（Dashboard / ChatView）：保持静态 import（首屏即用）
- 高频页面（PortfolioManagement / ValuationHistory）：`defineAsyncComponent` + 默认 LoadingComponent
- 低频页面（AdminAgentsPage / ShadowModePage / EvalSuitePage 等系统页）：`defineAsyncComponent`
- 已有 `defineAsyncComponent` import 但未落地（App.vue:2），本次落地

**代码示例**：
```js
import { defineAsyncComponent } from 'vue'

// 首屏保持静态
import Dashboard from './Dashboard.vue'
import ChatView from './ChatView.vue'

// 懒加载
const PortfolioManagement = defineAsyncComponent(() => import('./portfolio/PortfolioManagement.vue'))
const ValuationHistory = defineAsyncComponent(() => import('./valuation/ValuationHistory.vue'))
// ... 其余 30+ 组件同理
```

**统一 Loading 组件**：新建 `MobilePageLoading.vue`，30px 居中 spinner，避免白屏。

### 2.2 ChatMessage 复制功能修复

**方案**：抽取 ChatView.copyToClipboard 为共享 composable `useClipboard.js`，ChatMessage 改用它。

**根因**（memory 已记录）：
- `top:-9999px` 在 iOS Safari 16+ select 失败
- 缺 `fontSize:16px` 触发 iOS 自动缩放
- 未先 `focus()` 再 `select()`

**改造**：
- 新建 `composables/useClipboard.js`，导出 `copyToClipboard(text, opts)` 函数
- ChatView.vue 和 ChatMessage.vue 都改用该 composable
- 其他用 `navigator.clipboard.writeText` 的组件（CapabilityCenter/RagAnalysis/AdminAgentsPage 等）也统一接入

### 2.3 图表 resize 统一

**方案**：BondMarket.vue 和 HealthDashboardV2.vue 改用 `useLazyChart` composable。

**额外增强**：useLazyChart 增加 `onActivated` 钩子处理 KeepAlive 切回时的 resize。

```js
// useLazyChart.js 新增
import { onActivated } from 'vue'
onActivated(() => {
  chart?.resize()
})
```

### 2.4 图片解码优化

**方案**：全局给 `<img loading="lazy">` 补充 `decoding="async"`，避免解码阻塞主线程。

涉及文件：ValuationHistory.vue / ChatMessage.vue / ImageGrid.vue / AuthorArticles.vue / ArticleManagement.vue / ImageGallery.vue

## 三、验证方式

1. **bundle 体积**：`npm run build` 后对比 dist/assets JS 文件数量和大小
2. **首屏加载**：Chrome DevTools Network 面板，模拟 4G，对比首屏 JS 下载量
3. **复制功能**：iOS Safari + 微信内置浏览器实测消息复制
4. **图表 resize**：横竖屏切换 + KeepAlive 切回测试

## 四、风险与回滚

- **风险**：defineAsyncComponent 改造后，组件加载失败需有 ErrorComponent 兜底
- **回滚**：git revert 即可，无数据层改动
