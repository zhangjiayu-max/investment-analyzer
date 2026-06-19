# 投资分析器 UI 重设计方案

> 日期：2026-06-19  
> 目标：从"能用但丑"升级为专业理财工具风格  
> 参考：招商银行App、雪球、且慢、蚂蚁财富、Linear

---

## 一、当前问题诊断

| 问题 | 具体表现 | 严重度 |
|------|----------|--------|
| 配色不专业 | Indigo 紫蓝色太"科技感"，不像理财工具 | 高 |
| 视觉层级混乱 | 所有元素同样权重，缺少主次关系 | 高 |
| 卡片没有层次感 | 平面化，阴影太弱，边框生硬 | 中 |
| 表格太朴素 | 无条纹、无 hover、数字不对齐 | 高 |
| 按钮风格不统一 | 圆角、字号、padding 随意 | 中 |
| 移动端粗糙 | 底部 Tab 栏简陋，顶部栏无质感 | 中 |
| 字体层级缺失 | 标题和正文大小差异不够 | 中 |
| 暗色主题割裂 | 部分硬编码颜色，黑灰不统一 | 中 |

---

## 二、设计语言定义

### 2.1 色彩系统

#### 亮色主题（Light）

```
主色 Primary：
  --color-primary:       #1e40af  (深海蓝，沉稳专业)
  --color-primary-light: #3b82f6  (亮蓝，hover/链接)
  --color-primary-50:    #eff6ff  (极浅蓝，背景)
  --color-primary-100:   #dbeafe  (浅蓝，选中态)
  --color-primary-200:   #bfdbfe  (边框蓝)

辅色 Accent：  
  --color-gold:          #c9a84c  (金色，暗色主题主色 + 亮色高亮)
  --color-gold-light:    #f0d97a  (浅金)

涨跌色（中国市场惯例 红涨绿跌）：
  --color-profit:        #dc2626  (红，涨)
  --color-profit-bg:     #fef2f2  (浅红背景)
  --color-loss:          #059669  (绿，跌)
  --color-loss-bg:       #ecfdf5  (浅绿背景)

背景层级：
  --color-bg:            #f7f8fa  (页面背景，微暖灰)
  --color-bg-card:       #ffffff  (卡片白)
  --color-bg-sidebar:    #fafbfc  (侧边栏，比页面略亮)
  --color-bg-input:      #f3f4f6  (输入框灰)
  --color-bg-hover:      #f0f4ff  (hover 浅蓝)

边框：
  --color-border:        #e5e7eb  (常规边框)
  --color-border-light:  #f3f4f6  (浅边框)
  --color-border-strong: #d1d5db  (强边框)

文字层级：
  --color-text-primary:   #111827  (标题/正文，接近黑)
  --color-text-secondary: #4b5563   (次要文字)
  --color-text-muted:     #9ca3af   (辅助文字)
  --color-text-disabled:  #d1d5db   (禁用)
```

#### 暗色主题（Dark）

```
主色 Primary（暗色用金色）：
  --color-primary:       #d4a843  (金色)
  --color-primary-light: #f0d97a  (浅金)
  --color-primary-50:    rgba(212,168,67,0.08)
  --color-primary-100:   rgba(212,168,67,0.14)

背景层级（蓝调深色，交易终端感）：
  --color-bg:            #0d1117  (GitHub Dark 风格)
  --color-bg-card:       #161b22  (卡片)
  --color-bg-sidebar:    #010409  (侧边栏，最深)
  --color-bg-input:      #21262d  (输入框)
  --color-bg-hover:      #1c2128  (hover)

边框：
  --color-border:        #30363d
  --color-border-light:  #21262d

文字：
  --color-text-primary:   #e6edf3
  --color-text-secondary: #7d8590
  --color-text-muted:     #484f58
```

### 2.2 圆角系统

```
--radius-xs:  4px   (小标签、badge)
--radius-sm:  6px   (小按钮、输入框)
--radius-md:  8px   (常规按钮)
--radius-lg:  12px  (卡片)
--radius-xl:  16px  (大卡片、弹窗)
--radius-2xl: 20px  (特殊容器)
--radius-full: 9999px (胶囊、圆形)
```

### 2.3 阴影系统

```
--shadow-xs:    0 1px 2px rgba(0,0,0,0.03)
--shadow-sm:    0 1px 3px rgba(0,0,0,0.04), 0 1px 2px rgba(0,0,0,0.03)
--shadow-md:    0 4px 8px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04)
--shadow-lg:    0 8px 16px rgba(0,0,0,0.08), 0 2px 4px rgba(0,0,0,0.04)
--shadow-xl:    0 16px 32px rgba(0,0,0,0.10), 0 4px 8px rgba(0,0,0,0.06)
--shadow-glow:  0 0 0 3px rgba(30,64,175,0.12)  (focus ring)

暗色主题阴影：
--shadow-sm:    0 1px 3px rgba(0,0,0,0.4)
--shadow-md:    0 4px 8px rgba(0,0,0,0.5)
--shadow-lg:    0 8px 16px rgba(0,0,0,0.6)
--shadow-xl:    0 16px 32px rgba(0,0,0,0.7)
```

### 2.4 字体层级

```
--font-sans: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif
--font-mono: 'SF Mono', 'JetBrains Mono', 'Menlo', monospace
--font-num:  'SF Pro Display', -apple-system, sans-serif  (数字专用)

字号层级：
  Display:  28px / 700 / 1.2   (大数字展示，如总资产)
  H1:       20px / 700 / 1.3   (页面标题)
  H2:       16px / 600 / 1.4   (卡片标题)
  H3:       14px / 600 / 1.4   (小节标题)
  Body:     14px / 400 / 1.6   (正文)
  Body-sm:  13px / 400 / 1.5   (次要正文)
  Caption:  12px / 400 / 1.4   (辅助说明)
  Label:    11px / 600 / 1.4   (标签文字，uppercase)

数字展示：
  .stat-xl:  32px / 700 / tabular-nums  (总资产等核心数字)
  .stat-lg:  24px / 700 / tabular-nums  (盈亏数字)
  .stat-md:  16px / 600 / tabular-nums  (表格数字)
  .stat-sm:  13px / 500 / tabular-nums  (小数字)
```

### 2.5 间距系统

```
--space-1:  4px
--space-2:  8px
--space-3:  12px
--space-4:  16px
--space-5:  20px
--space-6:  24px
--space-8:  32px
--space-10: 40px
--space-12: 48px
```

### 2.6 动效系统

```
--ease-out:     cubic-bezier(0.16, 1, 0.3, 1)
--ease-in:      cubic-bezier(0.4, 0, 1, 1)
--ease-in-out:  cubic-bezier(0.4, 0, 0.2, 1)
--ease-spring:  cubic-bezier(0.34, 1.56, 0.64, 1)

--duration-fast:    150ms
--duration-normal:  250ms
--duration-slow:    400ms
```

---

## 三、组件设计规范

### 3.1 全局卡片（.card）

**亮色**：
- 背景：#ffffff
- 边框：1px solid #e5e7eb
- 圆角：12px
- 阴影：shadow-sm
- hover：shadow-md + translateY(-1px)

**暗色**：
- 背景：#161b22
- 边框：1px solid #30363d
- 阴影：shadow-sm

```css
.card {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
  transition: box-shadow 0.25s var(--ease-out), transform 0.15s var(--ease-out);
}
.card:hover {
  box-shadow: var(--shadow-md);
}
```

**变体**：
- `.card-elevated`：更高阴影，用于重要数据展示
- `.card-flat`：无边框无阴影，用于内嵌区块
- `.card-highlight`：左侧 3px 主色边条，用于选中/高亮

### 3.2 按钮系统

#### Primary（主按钮）
```
亮色：bg #1e40af, text #fff, radius 8px, padding 8px 16px
hover：bg #1d4ed8 + shadow-glow
active：bg #1e3a8a + scale(0.98)
disabled：opacity 0.5
```

#### Secondary（次按钮）
```
亮色：bg #fff, text #4b5563, border 1px #e5e7eb, radius 8px
hover：bg #f9fafb + border #d1d5db
```

#### Ghost（幽灵按钮）
```
亮色：bg transparent, text #4b5563, radius 8px
hover：bg #f3f4f6
```

#### Danger（危险按钮）
```
亮色：bg #dc2626, text #fff, radius 8px
hover：bg #b91c1c
```

#### 尺寸变体
```
btn-xs:  6px 10px / 11px
btn-sm:  6px 12px / 12px
btn-md:  8px 16px / 14px  (默认)
btn-lg:  10px 20px / 16px
```

### 3.3 输入框

```
亮色：
  bg: #f3f4f6
  border: 1px solid #e5e7eb
  radius: 8px
  padding: 8px 12px
  font-size: 14px
  
focus：
  border-color: #1e40af
  box-shadow: 0 0 0 3px rgba(30,64,175,0.12)
  bg: #ffffff

暗色：
  bg: #21262d
  border: 1px solid #30363d
  focus border: #d4a843
  focus shadow: 0 0 0 3px rgba(212,168,67,0.12)
```

### 3.4 表格

```css
.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

/* 表头 */
.data-table thead th {
  background: var(--color-bg-input);
  color: var(--color-text-secondary);
  font-weight: 600;
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  padding: 10px 12px;
  border-bottom: 1px solid var(--color-border);
  position: sticky;
  top: 0;
  z-index: 1;
}

/* 表体 */
.data-table tbody td {
  padding: 10px 12px;
  border-bottom: 1px solid var(--color-border-light);
  color: var(--color-text-primary);
}

/* 斑马纹 */
.data-table tbody tr:nth-child(even) {
  background: var(--color-bg-input);
}

/* hover */
.data-table tbody tr:hover {
  background: var(--color-primary-50);
}

/* 数字列右对齐 */
.data-table .num {
  text-align: right;
  font-variant-numeric: tabular-nums;
  font-family: var(--font-num);
}
```

### 3.5 标签页（Tabs）

采用 **下划线风格**（非 pill），更专业：

```css
.tab-bar {
  display: flex;
  gap: 0;
  border-bottom: 1px solid var(--color-border);
  margin-bottom: 16px;
}

.tab-btn {
  padding: 10px 16px;
  font-size: 13px;
  font-weight: 500;
  color: var(--color-text-secondary);
  border: none;
  background: none;
  position: relative;
  transition: color 0.15s;
}

.tab-btn:hover {
  color: var(--color-text-primary);
}

.tab-btn.active {
  color: var(--color-primary);
  font-weight: 600;
}

.tab-btn.active::after {
  content: '';
  position: absolute;
  bottom: -1px;
  left: 8px;
  right: 8px;
  height: 2px;
  background: var(--color-primary);
  border-radius: 2px;
}
```

### 3.6 Badge / Tag

```
尺寸：padding 2px 8px, radius 999px, font 11px/600

颜色变体（亮色）：
  success: bg #ecfdf5, color #059669, border #a7f3d0
  warning: bg #fffbeb, color #d97706, border #fde68a
  danger:  bg #fef2f2, color #dc2626, border #fecaca
  info:    bg #eff6ff, color #2563eb, border #bfdbfe
  neutral: bg #f3f4f6, color #4b5563, border #e5e7eb

暗色变体：
  success: bg rgba(16,185,129,0.12), color #34d399
  warning: bg rgba(245,158,11,0.12), color #fbbf24
  danger:  bg rgba(239,68,68,0.12), color #f87171
  info:    bg rgba(212,168,67,0.12), color #d4a843
  neutral: bg #21262d, color #7d8590
```

### 3.7 弹窗 Modal

```
Overlay: bg rgba(0,0,0,0.5) + backdrop-blur(4px)
Container:
  bg: var(--color-bg-card)
  radius: 16px
  shadow: shadow-xl
  max-width: 560px (默认), 90vw
  max-height: 85vh
  
动画：
  overlay: opacity 0 → 1 (200ms)
  content: scale(0.96) translateY(8px) → scale(1) translateY(0) (250ms spring)
```

### 3.8 涨跌数字展示

```css
.stat-profit {
  color: #dc2626;  /* 红 */
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}
.stat-loss {
  color: #059669;  /* 绿 */
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}
.stat-neutral {
  color: var(--color-text-secondary);
  font-variant-numeric: tabular-nums;
}
```

---

## 四、页面级设计

### 4.1 侧边栏 Sidebar

**设计理念**：安静、不抢注意力，当前页一目了然

```
宽度：232px
背景：亮色 #fafbfc / 暗色 #010409
右边框：1px solid var(--color-border)

Logo 区域：
  高度：56px
  背景：微渐变 linear-gradient(135deg, #f0f4ff, #fafbfc)
  Logo 图标：36x36px，圆角 8px，渐变 linear-gradient(135deg, #1e40af, #3b82f6)
  字体：14px/700 "投资分析助手"
  
导航项：
  高度：36px
  padding：8px 12px
  圆角：8px
  font-size：13px
  font-weight：500
  gap：8px (icon + label)
  
  默认：color var(--color-text-secondary)
  hover：bg var(--color-bg-hover), color var(--color-text-primary)
  active：
    bg var(--color-primary-50)
    color var(--color-primary)
    font-weight 600
    左侧 3px 竖条 var(--color-primary)
    
分组标题：
  font-size：11px
  font-weight：600
  color：var(--color-text-muted)
  text-transform：uppercase
  letter-spacing：0.08em
  padding：12px 12px 4px
  
子项：
  padding-left：32px
  font-size：12px
  高度：32px

Token 指示器：
  胶囊形状，bg var(--color-bg-input), radius 999px
  padding：8px 12px
  进度条：高 4px，圆角 2px

底部主题切换：
  padding：12px
  上边框：1px solid var(--color-border)
```

### 4.2 主内容区

```
背景：亮色 #f7f8fa / 暗色 #0d1117
padding：24px 32px (桌面) / 12px (移动)
max-width：1280px
margin：0 auto
```

### 4.3 Dashboard 每日看板

**页面标题区**：
```
H1 "每日投资决策看板" — 20px/700
日期胶囊：display inline-flex, padding 4px 12px, radius 999px
  bg var(--color-primary-50), color var(--color-primary)
  font-size 12px/600
```

**2x2 网格**：
```
grid-template-columns: 1fr 1fr
gap: 16px
移动端：1fr

每张卡片：
  padding: 16px
  radius: 12px
  hover: shadow-md
  
卡片标题：16px/600 + 右侧操作按钮
卡片正文：13px/400
```

**BriefingCard 简报卡片**：
```
左侧 3px 渐变条 var(--color-primary)
背景微渐变：linear-gradient(135deg, primary-50, bg-card)
可折叠
```

**温度仪表盘**：
```
卡片内居中
下方显示温度描述文字
```

### 4.4 PortfolioManagement 持仓管理

**页面标题**：
```
H1 "持仓管理" + 操作按钮组（刷新净值、新建买入、手动录入）
```

**待确认交易 Banner**：
```
bg: #fffbeb (warning bg)
border: 1px solid #fde68a
radius: 8px
padding: 12px 16px
左侧黄色警告图标
```

**风险预警面板**：
```
可折叠
bg: var(--color-bg-card)
border-left: 3px solid var(--color-warning)
```

**标签页栏**：
```
下划线风格（见 3.5）
6个 tab：持仓列表 | 分散度分析 | 交易行为分析 | AI 分析 | 策略配置 | 关注列表
```

**持仓列表表格**：
```
列：基金名称 | 代码 | 指数 | 持仓量 | 成本价 | 现价 | 市值 | 盈亏 | 盈亏率 | 今日变动 | 操作

表头：
  bg var(--color-bg-input)
  font 12px/600 uppercase
  color var(--color-text-secondary)
  
表体：
  font 13px
  数字列右对齐 tabular-nums
  斑马纹
  hover 高亮
  
盈亏数字：
  正：#dc2626 (红)
  负：#059669 (绿)
  font-weight 600
  
基金名称：
  font-weight 500
  color var(--color-text-primary)
  可点击进入详情
```

**分析面板**：
```
bg var(--color-bg-card)
radius 12px
padding 20px

统计数字卡片：
  4列网格
  每个统计：label 12px + value 20px/700 tabular-nums
```

**弹窗（新建买入等）**：
```
Overlay: rgba(0,0,0,0.5) + blur(4px)
Container: 560px max-width, radius 16px, shadow-xl
Header: 18px/600 + 关闭按钮
Body: 表单，label 12px/600 uppercase + input 14px
Footer: 右对齐按钮组
```

### 4.5 Quote Bar 理财彩蛋栏

```
bg: linear-gradient(135deg, #1e3a5f, #2d5a87)
亮色主题也用深蓝（突出对比）
radius: 8px
padding: 10px 16px
文字：#e8eaed 14px
理财语录 + 热词标签
热词标签：bg rgba(201,168,76,0.15), color #d4b65a, radius 999px
```

### 4.6 移动端 MobileApp

**顶部栏**：
```
height: 48px + safe-area-top
bg: rgba(255,255,255,0.8) + backdrop-blur(20px) saturate(180%)
border-bottom: 1px solid var(--color-border)
标题：16px/600 居中
右侧：主题切换按钮 36x36px
```

**内容区**：
```
padding: 12px
overflow-y: auto
```

**底部 Tab 栏**：
```
height: 56px + safe-area-bottom
bg: rgba(255,255,255,0.9) + backdrop-blur(20px)
border-top: 1px solid var(--color-border)

Tab 项：
  flex: 1
  flex-direction: column
  gap: 2px
  icon: 22px
  label: 10px/500
  
active 状态：
  color: var(--color-primary)
  顶部 3px 高亮条（20px宽，2px高）
  icon 轻微弹跳动画
```

**更多菜单 Bottom Sheet**：
```
Overlay: rgba(0,0,0,0.5) + blur(6px)
Sheet: 
  bg var(--color-bg-card)
  radius top: 20px
  max-height: 70vh
  
拖拽指示器：36x4px, radius 2px, opacity 0.3

网格：3列
每个项目：
  height: 56px
  radius: 12px
  bg var(--color-bg-input)
  font 13px
  active: bg var(--color-primary-50) + border var(--color-primary)
```

---

## 五、实施计划

### 第一阶段：全局基础（style.css）
- 重写所有 Design Tokens
- 重写按钮、输入框、表格、badge、modal 等全局样式
- 新增 .stat-number, .finance-positive/negative 等工具类

### 第二阶段：布局组件
- Sidebar 重做
- App.vue 主内容区
- MobileApp 顶部栏 + 底部 Tab

### 第三阶段：核心页面
- Dashboard 看板
- PortfolioManagement 持仓管理
- Home.vue Quote Bar

### 第四阶段：验证
- `npx vite build` 确认通过
- 逐页面检查亮色/暗色主题
- 检查移动端响应式

---

## 六、设计原则

1. **克制**：每个元素都有存在的理由，不堆砌装饰
2. **层级**：通过字号、颜色、阴影建立明确的主次关系
3. **数字优先**：理财工具的核心是数字，用 tabular-nums 和醒目颜色
4. **安静的专业感**：不像消费 App 那样花哨，而是像专业工具一样沉稳
5. **一致性**：所有卡片同样圆角，所有按钮同样动画，所有数字同样字体
6. **红涨绿跌**：中国市场惯例，不搞国际惯例
