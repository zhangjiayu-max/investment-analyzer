# 投资分析助手 — UI 设计规范

> 最后更新：2026-06-17  
> 覆盖范围：前端 Vue 3 + Tailwind CSS 4 + ECharts 6  
> 对应提交：UI redesign 两轮完成（18 个文件，+911/-194 行）

---

## 目录

1. [设计原则](#1-设计原则)
2. [色彩系统](#2-色彩系统)
3. [Design Token 速查](#3-design-token-速查)
4. [组件规范](#4-组件规范)
5. [动效规范](#5-动效规范)
6. [响应式断点](#6-响应式断点)
7. [暗色主题适配清单](#7-暗色主题适配清单)
8. [文件改动记录](#8-文件改动记录)

---

## 1. 设计原则

### 1.1 双主题，不混搭
- **亮色主题**：靛蓝（Indigo）为主色，冷静、专业
- **暗色主题**：金色（Gold）为主色，金融感、精致
- 禁止在亮色用金色 token，或在暗色用靛蓝 token

### 1.2 层次感来自三个维度
1. **背景色差**：card / input / hover 三层递进
2. **边框微光**：active 状态用 `var(--color-primary-border)` 而非纯色边框
3. **阴影与发光**：重要操作（primary button、活跃卡片）带主题色 glow

### 1.3 动效要「有弹性但不吵」
- 用 `--transition-spring` 做悬停/出现的弹性动效
- 脉冲、趋势箭头等有意义的动效用 CSS animation
- 禁止在 hover 状态用超过 300ms 的动效

---

## 2. 色彩系统

### 2.1 亮色主题（Indigo 系）

| 用途 | Token | 色值 |
|------|-------|------|
| 主色 | `--color-primary` | `#6366f1` |
| 主色浅 | `--color-primary-400` | `#818cf8` |
| 主色深 | `--color-primary-700` | `#4338ca` |
| 成功 | `--color-success` | `#10b981` |
| 警告 | `--color-warning` | `#f59e0b` |
| 危险 | `--color-danger` | `#ef4444` |
| 背景 | `--color-bg` | `#f1f5f9` |
| 卡片 | `--color-bg-card` | `#ffffff` |
| 输入区 | `--color-bg-input` | `#f1f5f9` |
| 悬停 | `--color-bg-hover` | `#eef2ff` |
| 边框 | `--color-border` | `#e2e8f0` |
| 文字主 | `--color-text-primary` | `#0f172a` |
| 文字次 | `--color-text-secondary` | `#475569` |
| 文字弱 | `--color-text-muted` | `#94a3b8` |

### 2.2 暗色主题（Gold 系）

| 用途 | Token | 色值 |
|------|-------|------|
| 主色 | `--color-primary` | `#d4a843` |
| 主色浅 | `--color-primary-400` | `#ddb756` |
| 主色深 | `--color-primary-700` | `#9a7830` |
| 成功 | `--color-success` | `#34d399` |
| 警告 | `--color-warning` | `#fbbf24` |
| 危险 | `--color-danger` | `#f87171` |
| 背景 | `--color-bg` | `#0c0c0f` |
| 卡片 | `--color-bg-card` | `#16161a` |
| 侧边栏 | `--color-bg-sidebar` | `#111114` |
| 输入区 | `--color-bg-input` | `#1e1e24` |
| 悬停 | `--color-bg-hover` | `#222228` |
| 边框 | `--color-border` | `rgba(255,255,255,0.07)` |
| 文字主 | `--color-text-primary` | `#f0ede8` |
| 文字次 | `--color-text-secondary` | `#b0aca6` |
| 文字弱 | `--color-text-muted` | `#787470` |

### 2.3 金融语义色（跨主题固定含义）

| 含义 | 亮色 | 暗色 | 用途 |
|------|------|------|------|
| 涨/盈利 | `#dc2626` | `#f87171` | 持仓盈利、收益为正 |
| 跌/亏损 | `#059669` | `#34d399` | 持仓亏损、收益为负 |
| 债市冷 | `#3b82f6` | `#60a5fa` | 温度仪表盘冷区 |
| 债市热 | `#ef4444` | `#f87171` | 温度仪表盘热区 |

> ⚠️ 注意：A 股惯例「红涨绿跌」，与欧美相反。代码中 `--color-profit` 和 `--color-loss` 已按此定义。

---

## 3. Design Token 速查

### 3.1 渐变（Gradient）

```css
--gradient-primary   /* 主色渐变：靛蓝→深靛蓝 / 金色→深金色 */
--gradient-accent   /* 主色→青色，用于强调区域 */
--gradient-warm     /* 警告橙→危险红，用于热点卡片顶条 */
--gradient-success  /* 成功绿渐变，用于零钱卡片顶条 */
--gradient-card-border  /* 卡片边框微光渐变 */
--gradient-card-shine  /* 卡片表面光泽扫过效果 */
```

**使用场景**
- 主按钮背景 → `--gradient-primary`
- 卡片 `::before` 顶条 → 根据卡片类型选对应渐变
- 发送按钮 → `--gradient-primary`

### 3.2 毛玻璃（Glassmorphism）

```css
--glass-bg: rgba(255,255,255,0.72);   /* 亮色 */
--glass-bg: rgba(22,22,26,0.8);     /* 暗色 */
--glass-border: rgba(255,255,255,0.4);
--glass-blur: 16px;
--glass-shadow: 0 8px 32px rgba(0,0,0,0.06);
```

**使用场景**：固定头部、固定底部 Tab 栏、遮罩层

```css
/* 标准毛玻璃类 */
.glass {
  background: var(--glass-bg);
  border: 1px solid var(--glass-border);
  backdrop-filter: blur(var(--glass-blur));
  -webkit-backdrop-filter: blur(var(--glass-blur));
  box-shadow: var(--glass-shadow);
}
```

### 3.3 微交互（Micro-interaction）

```css
--hover-lift: translateY(-1px);    /* 卡片/按钮悬停上浮 */
--press-scale: scale(0.98);        /* 按下缩小 */
--focus-ring: 0 0 0 3px rgba(99,102,241,0.15);  /* 输入框聚焦光环 */
--shadow-glow: 0 0 20px rgba(99,102,241,0.08);  /* 主题色发光 */
--transition-spring: 400ms cubic-bezier(0.34, 1.56, 0.64, 1);
```

### 3.4 圆角（Radius）

```css
--radius-sm: 6px;
--radius-md: 8px;
--radius-lg: 12px;
--radius-xl: 16px;
--radius-2xl: 20px;   /* 新增：Bottom Sheet、大卡片 */
```

### 3.5 间距（Spacing）

```css
--space-1: 4px;
--space-2: 8px;
--space-3: 12px;
--space-4: 16px;
--space-5: 20px;
--space-6: 24px;
--space-8: 32px;
```

---

## 4. 组件规范

### 4.1 卡片（.dash-card）

**标准结构**

```html
<div class="dash-card">
  <!-- ::before 自动渲染渐变顶条（悬停时 opacity:1）-->
  <!-- ::after 自动渲染角落发光（悬停时 opacity:0.08）-->
  <div class="card-header">...</div>
  <div class="card-body">...</div>
</div>
```

**样式要点**
- 默认：`box-shadow: var(--shadow-sm)`，无边框发光
- 悬停：`transform: var(--hover-lift)` + `box-shadow: var(--shadow-lg), var(--shadow-glow)` + `border-color: var(--color-primary-border)`
- 顶条渐变按卡片类型选择：
  - 低估指数 → `--gradient-accent`（蓝绿）
  - 持仓健康度 → `--gradient-primary`
  - 今日热点 → `--gradient-warm`（橙红）
  - 零钱配置 → `--gradient-success`（绿）

**暗色主题差异**
- AI 消息气泡背景用 `--color-bg-secondary`（而非 `--color-bg-input`）
- 卡片边框用 `--color-border`（透明度 0.07，更细腻）

### 4.2 按钮

| 类型 | 类名 | 背景 | 使用场景 |
|------|------|------|------|
| 主按钮 | `.btn-primary` | `--gradient-primary` | 提交、确认、发送 |
| 次按钮 | `.btn-secondary` | `var(--color-bg-card)` | 取消、返回 |
| 危险 | `.btn-danger` | 红色渐变 | 删除、停止 |
| 幽灵 | `.btn-ghost` | 透明 | 图标按钮、次要操作 |
| 轮廓 | `.btn-outline` | 透明+边框 | 切换状态 |

**按钮动效**
```css
.btn-primary:hover {
  transform: var(--hover-lift);
  box-shadow: 0 4px 12px var(--color-primary-glow-strong);
}
.btn-primary:active {
  transform: var(--press-scale);
  box-shadow: none;
}
```

### 4.3 对话气泡（ChatMessage）

```css
/* 用户消息：右对齐，渐变背景 */
.message.user .message-bubble {
  background: var(--gradient-primary);
  color: white;
  border-bottom-right-radius: 4px;  /* 小切口，更现代 */
  box-shadow: 0 2px 8px var(--color-primary-shadow);
  max-width: 88%;
  margin-left: auto;
}

/* AI 消息：左对齐，浅背景+边框 */
.message.assistant .message-bubble {
  background: var(--color-bg-input);
  color: var(--color-text-primary);
  border-bottom-left-radius: 4px;
  border: 1px solid var(--color-border-light);
}
.dark .message.assistant .message-bubble {
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border);
}
```

### 4.4 专家分析卡片（.specialist-item）

```css
.specialist-item {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  transition: border-color 0.3s, box-shadow 0.3s;
}
.specialist-item:hover {
  border-color: var(--color-primary-border);
  box-shadow: 0 2px 8px var(--color-primary-glow);
}
.specialist-item.running {
  border-color: var(--color-primary-border-strong);
  background: var(--color-primary-bg);
  box-shadow: 0 0 0 1px var(--color-primary-border);
}
```

### 4.5 表格

**行悬停**
```css
.data-table tr:hover td {
  background: var(--color-bg-hover);
}
```

**斑马纹**
```css
.data-table tr:nth-child(even) td {
  background: var(--color-bg-input);
}
.dark .data-table tr:nth-child(even) td {
  background: rgba(255,255,255,0.02);
}
```

**PE 百分位进度条**
```html
<!-- 使用 style.css 中定义的 .progress-bar-gradient -->
<div class="progress-bar-gradient">
  <div class="fill" :style="{ width: pePercent + '%' }"
       :class="pePercent > 80 ? 'extreme' : pePercent > 60 ? 'hot' : pePercent > 40 ? 'warm' : pePercent > 20 ? 'cool' : 'cold'">
  </div>
</div>
```

进度条配色（5 段）：
- `cold`（0-20%）→ 蓝 `#3b82f6`
- `cool`（20-40%）→ 浅蓝 `#60a5fa`
- `warm`（40-60%）→ 绿 `#34d399`
- `hot`（60-80%）→ 橙 `#f59e0b`
- `extreme`（80-100%）→ 红 `#ef4444`

### 4.6 仪表盘（GaugeChart）

5 段配色已在 `GaugeChart.vue` 的 `segments` prop 中定义：

```js
segments: [
  { from: 0,   to: 20,  color: '#3b82f6' },  // 极冷：蓝
  { from: 20,  to: 40,  color: '#60a5fa' },  // 冷：浅蓝
  { from: 40,  to: 60,  color: '#34d399' },  // 温：绿
  { from: 60,  to: 80,  color: '#f59e0b' },  // 热：橙
  { from: 80,  to: 100, color: '#ef4444' },  // 极热：红
]
```

---

## 5. 动效规范

### 5.1 标准动效

| 动效 | 触发 | 实现 |
|------|------|------|
| 卡片悬停上浮 | hover | `transform: var(--hover-lift)` + `transition: 0.3s cubic-bezier(0.4,0,0.2,1)` |
| 按钮按下 | active | `transform: var(--press-scale)` |
| 输入框聚焦 | focus | `box-shadow: var(--focus-ring)` |
| 页面切换 | 路由 | `transition: all var(--transition-spring)` |
| 趋势箭头 | 数据更新 | `.trend-arrow-up` / `.trend-arrow-down` CSS animation |

### 5.2 CSS 动画（已定义在 style.css）

```css
/* 趋势向上箭头，轻微上下浮动 */
@keyframes trend-up {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-2px); }
}
.trend-arrow-up { animation: trend-up 1.5s ease-in-out infinite; color: var(--color-danger); }

/* 趋势向下箭头 */
@keyframes trend-down {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(2px); }
}
.trend-arrow-down { animation: trend-down 1.5s ease-in-out infinite; color: var(--color-success); }

/* Tab 图标弹跳 */
@keyframes tabBounce {
  0% { transform: scale(1); }
  50% { transform: scale(1.15); }
  100% { transform: scale(1); }
}

/* 脉冲发光（用于 running 状态）*/
@keyframes pulse-indigo { /* 亮色 */ }
@keyframes pulse-gold { /* 暗色 */ }
.pulse-primary { animation: pulse-indigo 2s ease-in-out infinite; }
.dark .pulse-primary { animation: pulse-gold 2s ease-in-out infinite; }
```

### 5.3 动效禁止清单

- ❌ 页面加载时所有元素依次入场（太慢）
- ❌ hover 状态动效超过 300ms
- ❌ 同时有多个脉冲动画（视觉混乱）
- ❌ 移动端使用 hover 触发的动效（无 hover 设备）

---

## 6. 响应式断点

```css
/* 移动端横屏 / 大手机 */
@media (max-width: 768px) {
  .dash-grid { grid-template-columns: 1fr; }
  .app-main { padding: 0.75rem; margin-left: 0; }
  .mobile-tabbar { /* 显示 */ }
}

/* 平板 */
@media (min-width: 769px) and (max-width: 1024px) {
  .dash-grid { grid-template-columns: 1fr 1fr; gap: 0.75rem; }
  .app-main { padding: 1.25rem; }
}

/* 桌面 */
@media (min-width: 1025px) {
  .mobile-tabbar { display: none; }
  .sidebar { display: flex; }
}
```

### 6.1 移动端专项规范

- **触控区域**：所有按钮/菜单项 `min-height: 48px`
- **安全区域**：底部 Tab 栏加 `padding-bottom: env(safe-area-inset-bottom, 0)`
- **毛玻璃**：头部/底部固定栏用 `.glass` 类
- **更多菜单**：用 Bottom Sheet 风格（`.mobile-more-sheet`），带拖拽手柄（`::before` 伪元素）
- **表格横向滚动**：加 `overflow-x: auto; -webkit-overflow-scrolling: touch`

---

## 7. 暗色主题适配清单

以下样式必须在 `.dark` 选择器中**显式覆盖**，不能直接依赖 CSS 变量自动切换：

- [x] 卡片背景：`--color-bg-card: #16161a`
- [x] 侧边栏背景：`--color-bg-sidebar: #111114`
- [x] 主色系：金色 `#d4a843`（亮色为靛蓝 `#6366f1`）
- [x] 成功/警告/危险色：更亮的绿色/黄色/红色（暗色下对比度更高）
- [x] 边框透明度：`rgba(255,255,255,0.07)`（亮色为 `#e2e8f0`）
- [x] 毛玻璃背景：`rgba(22,22,26,0.8)`（亮色为 `rgba(255,255,255,0.72)`）
- [x] AI 消息气泡：`--color-bg-secondary`（而非 `--color-bg-input`）
- [x] 导航 active 状态：金色 glow（亮色为靛蓝 glow）
- [x] 脉冲动画：`pulse-gold`（亮色为 `pulse-indigo`）

---

## 8. 文件改动记录

### 第一轮（2026-06-16，sub-agent 超时前完成）

| 文件 | 改动行 | 主要内容 |
|------|---------|------|
| `src/style.css` | +400/-~50 | 设计 Token 全面重构 |
| `src/components/dashboard/UndervaluedIndexesCard.vue` | +26 | 卡片悬停发光 |
| `src/components/dashboard/HotspotsCard.vue` | +44 | 渐变顶条 warm |
| `src/components/dashboard/PortfolioHealthCard.vue` | +24 | 卡片动效 |
| `src/components/dashboard/CashManagementCard.vue` | +48 | 绿色渐变顶条 |
| `src/components/chat/ChatMessage.vue` | +51 | 气泡重设计 |
| `src/components/chat/ChatInput.vue` | +36 | 输入框升级 |
| `src/components/charts/GaugeChart.vue` | +9 | 5 段配色 |
| `src/components/MobileApp.vue` | +75 | 移动端全面优化 |

### 第二轮（2026-06-17，sub-agent 成功完成）

| 文件 | 改动行 | 主要内容 |
|------|---------|------|
| `src/components/dashboard/BriefingCard.vue` | +74 | 简报卡片升级 |
| `src/components/Sidebar.vue` | +47 | 侧边栏发光 + 渐变 |
| `src/components/ChatView.vue` | +59 | 毛玻璃头部 + 空状态 |
| `src/components/chat/ChatSidebar.vue` | +26 | 抽屉 + 发光指示器 |
| `src/components/MarketIntelligence.vue` | +39 | 趋势箭头 + 悬停 |
| `src/components/PortfolioManagement.vue` | +37 | 仓位进度条 + 斑马纹 |
| `src/components/ValuationHistory.vue` | +69 | PE 进度条 + 标签 |
| `src/components/StockChart.vue` | +22 | 图表容器发光 |
| `src/views/Home.vue` | +19 | 页面切换弹簧动画 |

**总计**：18 个文件，+911/-194 行，构建验证通过 ✅

---

## 9. 未来优化建议（待办）

- [ ] 情绪分析卡片（BriefingCard）增加情绪温度计动画
- [ ] 持仓健康度环形图（PortfolioHealthCard）用 ECharts 替换文字描述
- [ ] 零钱配置建议卡片增加「一键执行」操作流
- [ ] 对话流式输出时增加打字机光标动画
- [ ] 移动端手势：对话列表项左滑删除
- [ ] 深色模式自动跟随系统（目前需手动切换）
- [ ] 主题色允许用户自定义（目前固定靛蓝/金色）
