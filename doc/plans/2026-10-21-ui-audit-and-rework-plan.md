# UI 视觉审计与改造方案

日期：2026-10-21
范围：`frontend/src` 全组件（light / dark 双主题）
方法：代码层量化扫描（字号 / 色值 / 圆角 / emoji / `!important`）+ Playwright 运行时真实截图（1440×900，亮暗各一轮）

---

## 一、量化体检结果

| 指标 | 实测 | 判定 |
|---|---|---|
| 字号种类 | **97 种**（2479 处声明），其中 10 种 < 12px，最小 8.8px（0.55rem） | 排版碎片化，无字阶 |
| 唯一色值 | **183 个**（1410 处引用），含 Material 旧色系残留 | 色彩脱离 token |
| 圆角种类 | **19 种**，`3px`(109 处) 与 `4px`(144 处) 并存 | 半径不成体系 |
| emoji 硬编码 | **305 处** | 与金融终端气质冲突 |
| `!important` | **63 处**（PortfolioManagement 单文件 30 处） | 样式层打架 |
| 内联 `:style` | **186 处** | 绕过设计系统 |
| 超大单文件 | PortfolioManagement 10780 行 / EventRadarPage 8206 行 | 样式难以统一 |

主要"脏"文件排行（emoji / hex / `!important`）：

```
emoji  !imp  hex  file
  117    30  123  components/portfolio/PortfolioManagement.vue
   25     1   30  components/quality/EvalSuitePage.vue
   19     1  144  components/chat/ChatMessage.vue
   12     1  313  components/market/EventRadarPage.vue
```

---

## 二、P0 — 明确缺陷（观感破功 / 语义错误 / 功能降级）

### P0-1（初判有误，已修正）暗色对话气泡

初判"暗色下 AI 气泡变整块灰白"。**像素级复核后确认误判**：气泡实际计算值为 `rgb(22, 28, 44)`（`--color-bg-secondary` 暗色值），文字 `rgb(230, 237, 243)`，对比度正常。原先的判断来自截图缩放后的视觉误差，不作修改。

真正的问题是下面 P0-5：变量缺失导致大面积样式静默失效。

### P0-2 涨跌色语义反用

`EventRadarPage.vue:2306`

```
:style="{ color: d.verify_7d_change_pct >= 0 ? '#4caf50' : '#f44336' }"
```

涨用绿（`#4caf50`）、跌用红（`#f44336`），既违反项目自身 token 约定（`--color-profit: #dc2626` 红涨绿跌），也用了 Material 旧色。

改造：`>= 0 ? 'var(--color-profit)' : 'var(--color-loss)'`。

### P0-3 每日简报 Markdown 没被解析

`composables/useDashboardHelpers.js:53` 的 `renderBriefing()` 只处理 `**加粗**` 和 `- 列表`，然后把所有 `\n` 换成 `<br>`。结果是 `### 1. 市场逻辑`、`#### 3. 估值温度计` 原样带井号显示，整篇日报呈现为"未排版纯文本"。

配套问题：`BriefingCard.vue` 的 `.briefing-body` 只定义了 `ul / li / strong`，缺 `h1-h4 / p / blockquote / code / table / ol / hr`，即使解析了也没有层级。

改造：
1. `renderBriefing` 改用项目已依赖的 `marked`（`ChatMessage` 同款渲染管线），保留 DOMPurify。
2. `.briefing-body` 补齐标题层级、段间距（`p + p { margin-top: .75em }`）、引用块、表格样式，与 `.prose` 对齐或直接复用 `.prose`。

### P0-4 加载态留下大块空白

看板"市场行情总览"卡片在加载时只显示一行"5 分钟缓存加载中..."，但卡片被撑到约 170px 高；"持仓预警"同理空转 230px。首屏视觉上就是几张空白大卡。

根因见 P0-5：骨架条的 `background: var(--color-skeleton)` 变量未定义 → 背景无效 → 骨架完全透明。

改造：补全 `--color-skeleton` 定义，骨架条即恢复可见。

### P0-5 31 个 CSS 变量用而未定义，194 处样式静默失效

扫描 `components/**/*.vue` 中所有 `var(--x)` 裸引用（无 fallback），与 `style.css` 已定义变量求差：

```
--font-jet            43 处  等宽数字字体失效（金融界面对齐依赖它）
--color-bg-tertiary   36 处  背景透明
--color-bg-primary    13 处
--color-surface-hover 13 处
--color-down           9 处  跌色丢失  ← 涨跌配色半边失效
--color-up             7 处  涨色丢失
--radius-full          7 处  药丸圆角变直角
--color-overlay        6 处  弹窗遮罩透明
--color-skeleton       2 处  骨架屏不可见
... 共 31 个变量 / 194 处
```

另有 `ActionCard.vue` 使用 `var(--surface-2, #f8f9fa)` 等旧命名 + 硬编码浅色 fallback，暗色下回落到浅底（实测 `action-section` 渲染为 `rgb(248,249,250)`）。

改造：在 `style.css` 增加「兼容别名层」，把历史变量名统一映射到主 token（亮/暗各一套）；对确实需要独立值的（`--color-bg-tertiary`、`--color-skeleton`、`--color-purple*` 等）给出亮暗双值。

---

## 三、P1 — 视觉体系断裂（看起来"不专业"的主因）

### P1-1 按钮色彩语义失控

「机会雷达」单张事件卡内并排三个按钮：`估算金额影响`（绿色描边）、`深度解读影响`（紫色描边）、`受益标的推荐`（红色描边）。三种颜色、三个语义，用户无法判断主次。

「持仓管理」页头 5 个按钮（刷新净值 / 新建买入 / 手动录入 / 导出 CSV / 导入 CSV）权重几乎相同，仅"新建买入"是实心。

改造：确立按钮层级规范 —— 每屏 1 个 primary（实心主色），其余 secondary（描边）/ ghost（无边框）；语义色仅用于"危险/警示"（删除、撤销），不用于普通动作。

### P1-2 KPI 卡彩虹竖条

「机会雷达」5 张 KPI 卡各配一条不同颜色的左侧竖条 + 同色底部描边（黑 / 红 / 橙 / 黄 / 蓝），且第 5 张明显更宽，栅格不齐。其中黄色数字 `#d97706` 在白底对比度约 3.1:1，未达 WCAG AA 4.5:1。

改造：统一为中性竖条（`--color-border-strong`）+ 仅"风险/机会"两类用语义色；数字统一 `--font-num-mono` 深色；栅格用等宽 `grid-template-columns: repeat(5, minmax(0,1fr))`。

### P1-3 两套筛选语言并存

同一页里左半边是 pill 药丸筛选（全部 / 持仓影响 / 关注机会…），右半边是下划线条 tab（进行中 / 已过期 / 已到 / 全部），字号 11px 左右。同层级筛选用了两种交互外观。

改造：定一个「筛选条」组件 —— 内容维度用 pill，时间/状态维度用 segmented control（同一容器内的分段按钮），两者视觉重量区分开但共用一套 token。

### P1-4 侧边栏父子同时高亮 + emoji 噪声

分组头「持仓管理」与子项「持仓管理」同时显示激活态（都是浅蓝底 + 左侧 3px 蓝线），视觉重复且分不清当前在哪一层。每个热门项右侧挂 `🔥` emoji，专业金融终端里显得廉价。

改造：分组头激活态降为"文字加深"（不加底色）；子项才用底色 + 竖线。`🔥` 换成 6px 橙红实心圆点（已有 `.heat-dot` 可复用），或直接去掉。

### P1-5 对话正文没有排版层级

AI 回答里 `未覆盖盲点`（加粗）、正文段落（灰 13px）、`总体风险判断：中`（加粗）、`质量校验未通过…`（加粗）、`⚠ 代码核实提示：`（emoji + 浅灰块）全部处在同一视觉层级，段落之间几乎无间距，长回答读起来是一坨。

改造：
- 建立 `h4`（小标题，600 字重 + 上间距 1em）、`p`（段间距 .6em）、`strong`（主文字色 + 600）三级。
- 「质量校验未通过」这类结论升级为 callout（左侧 3px 警示条 + 警示底色），不要用加粗正文伪装。
- `⚠` 换成 `Icon name="warning"`，`总体风险判断` 的"中/高/低"改用 `StatusBadge`。

### P1-6 报错信息裸奔

「持仓管理」顶部卡片里 `风险资料读取或获取失败: Not Found，请刷新核对。` 是纯红字直接铺在卡片里，没有图标、没有容器、没有重试入口。

改造：统一错误态为 `AsyncStateBlock`（图标 + 标题 + 说明 + 操作按钮）。

---

## 四、P2 — 一致性清扫

1. **字阶收敛**：97 种 → 7 档（`11 / 12 / 13 / 16 / 20 / 24 / 30`）。现有 `0.72 / 0.75 / 0.78 / 0.8 / 0.82 / 0.85rem` 六档实际只差 1–2px，全部并入相邻档；彻底禁止 < 11px（清理 `0.55 / 0.56 / 0.58 / 0.6rem`）。
2. **色值收敛**：183 → 走 token。优先清旧色系残留：`#999`(17) `#666`(12) `#1f2937`(13) `#4caf50`(7) `#ff9800`(7) `#f44336`(6) `#2080f0`(5) `#4096ff`(3) `#1890ff`(4)。
3. **圆角收敛**：19 种 → 4 档（`--radius-sm 6 / md 8 / lg 12 / full 999`），清掉与 `4px` 混用的 `3px` 和 `2px`。
4. **emoji → Icon**：305 处逐步替换。状态点用 `.heat-dot` / `StatusBadge`，动作图标用 `lucide-vue-next`（`Icon.vue` 显式 import，避免 bundle 膨胀）。
5. **去 `!important`**：`PortfolioManagement.vue` 30 处、`Sidebar.vue` 7 处优先，改用更高特异性选择器或作用域拆分。

---

## 五、建议改造顺序

| 阶段 | 内容 | 验收 |
|---|---|---|
| 第 1 批 ✅ | P0-2 ~ P0-5 | 见「七、第 1 批执行记录」 |
| 第 2 批 | P1-1 ~ P1-3（按钮 / KPI / 筛选条） | 每屏仅 1 个主按钮；KPI 卡同高同宽；筛选条单一组件 |
| 第 3 批 | P1-4 ~ P1-6 + P2 字阶与色值 | 侧边栏层级清晰；对话正文有排版层级；字号 ≤ 7 档 |
| 第 4 批 | P2 圆角 / emoji / `!important` 清扫 | emoji < 20 处；`!important` < 10 处；圆角 ≤ 4 档 |

> 注：`npm run build` 在本环境会因 vite 清空 `static/assets` 触发批量删除保护而失败（2593 模块已编译通过，非代码错误），改用 dev server 运行态验证。

---

## 七、第 1 批执行记录（2026-10-21）

| 改动 | 文件 | 说明 |
|---|---|---|
| 兼容别名层 | `style.css` | `:root` 新增 40 个变量定义、`.dark` 新增 14 个覆写；裸用未定义变量 194 处 → **0 处**，已定义变量 185 个 |
| 涨跌色修正 | `EventRadarPage.vue:2306` | `#4caf50/#f44336` → `var(--color-profit)/var(--color-loss)` |
| 简报 Markdown | `useDashboardHelpers.js`、`BriefingCard.vue` | `renderBriefing` 改走 `renderMarkdown`（marked + DOMPurify）；移除重复 sanitize |
| 简报排版 | `BriefingCard.vue` | `.briefing-body` 补 `h1-h6 / p / blockquote / ol / hr / code / pre / table / a` 共 16 条规则 |
| 状态色 token 化 | `ChatMessage.vue` | 评测徽章、等级徽章、复杂度徽章、采纳按钮共 11 处硬编码色 → token（暗色可跟随） |
| 环形进度修复 | `DecisionAccuracy.vue:332` | `--ring-pct: '62%'` → 数值 `62`（原 `calc(62% * 3.6deg)` 单位非法，弧长恒为 0） |

**运行态验证结果**

```
token 解析：--color-skeleton → rgb(233,237,242)   --color-up → rgb(220,38,38)
           --color-down → rgb(5,150,105)          --radius-full → 999px
           --font-jet → 'JetBrains Mono', ...
简报渲染：  字面量 "###" 0 处；h3 6 个（14.72px / 650 / primary）；ul 7 个
暗色残留：  action-section 的 rgb(248,249,250) 浅底已消除
控制台：    无 pageerror
```

**遗留**：`.action-section` 等旧命名组件（`ActionCard.vue`）的类名本身未重构，只是变量补全后暗色可跟随；P2 阶段再统一命名。

---

## 六、附：审计截图

`/tmp/ui-audit/dashboard.png`（看板空白加载态、蓝色错位竖线）
`/tmp/ui-audit/event-radar.png`（KPI 彩虹条、三色按钮、双筛选语言）
`/tmp/ui-audit/portfolio.png`（裸奔报错、五按钮同权重、父子同高亮 + 🔥）
`/tmp/ui-audit/chat.png`（正文无排版层级）
`/tmp/ui-audit/dark.png`（暗色截图，初判为浅色块，复核后为误判）
`/tmp/ui-audit/final-dashboard-light.png`（第 1 批修复后·亮色）
`/tmp/ui-audit/final-dashboard-dark.png`（第 1 批修复后·暗色，简报层级正常）
`/tmp/ui-audit/final-chat-dark.png`（第 1 批修复后·暗色对话，气泡 #161c2c 正常）
