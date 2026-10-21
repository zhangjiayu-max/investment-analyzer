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

## 八、第 2 批执行记录：移动端与全站体系（2026-10-21）

第 1 批只覆盖了桌面端几个页面。本批把 39 个页面 + iPhone 视口（390×844）+ 亮暗双主题全部扫过，重点补移动端。

### 8.1 关键发现：移动端没有独立实现

`MobileApp.vue` 只是壳（687 行），页面全部**复用桌面组件**（`Dashboard` / `ChatView` / `PortfolioManagement` / `EventRadarPage` …）。
因此桌面组件的响应式能力就是移动端的真实表现，而 **99 个组件里有 28 个完全没有 `@media`**，断点值散成 14 种（768/760/720/680/640/600/540/520/480…）。

扫描方法：Playwright 移动视口逐页加载，统计横向溢出元素、被截断文本、`<11px` 文字。

| 指标 | 改造前 | 改造后 |
|---|---|---|
| 小字元素总数（<11.5px） | ≈2400 | ≈260 |
| 小字超标页面数 | 15 / 22 | 7 / 22 |
| emoji 总数 | 305 | 79（-74%） |
| 裸用未定义 CSS 变量 | 0（第 1 批已修） | 0 |

### 8.2 修复项

| 问题 | 文件 | 说明 |
|---|---|---|
| **首屏落地页是后台文章管理** | `useAppNavigation.js` | 默认 `'articles'` → `'dashboard'`。新用户第一眼看到的是 CMS 双栏表格，不是看板 |
| **移动端表格列被裁** | `style.css` | 原 `table{display:block}` 使 thead/tbody 列宽计算失效，单元格溢出到视口外被 `overflow-x:hidden` 裁掉。补 `thead/tbody{display:table;width:max-content;min-width:100%}` 复原列宽计算 |
| **KPI 卡彩虹竖线** | `EventRadarPage.vue` | 6 种色（灰/红/橙/橙/青/蓝）在手机上红橙、青蓝不可分辨。收敛为：中性=普通计数 / 风险色=持仓影响 / 主色=当前筛选 |
| **KPI 渐变竖线错位成"金蓝交错"** | `KnowledgeBase.vue` | 根因是 `editorial-card::before` 用 gold→primary 渐变，卡片高度不一致时每张截到的色段不同。KPI 网格内改为纯色 |
| **移动端 KPI 栅格孤项** | `EventRadarPage.vue` | 5 张卡在 2 列栅格留半格空洞 → 末项跨满整行 |
| **事件卡日期被压断** | `EventRadarPage.vue` | 时间列 48px 装不下 `2026-10-31`，折成 `2026-10-`/`31`。移动端改横排 |
| **持仓页头部按钮溢出** | `PortfolioManagement.vue` | `header-actions` 是 nowrap 横向 flex，4 个按钮超出 390px 被裁 → 移动端允许换行 |
| **报错裸奔** | `PortfolioRiskOverview.vue` | 纯红字 → 图标 + 淡红容器 + 边框 + 「重试」按钮 |
| **底部导航图标语言不一** | `MobileApp.vue` | 5 个手写 SVG（持仓是三层叠菱形）→ 统一 `Icon` 组件 |
| **无数据却涂红** | `DecisionAccuracy.vue` | 已验证 0 条时 `0.0%` 用 `--color-danger`，被读成"判断全错"。改为中性色 + `—` + 说明 |
| **财务总览页头参差** | `FamilyFinanceDashboard.vue` | 移动端塌成竖排后按钮宽度不一 → 统一铺满等宽 |
| **标签字号过小** | 8 个组件 | `keyword-tag`(10.56px)/`chip`(10.24px)/`event-type-tag`(9.92px) 等 15 处提到 0.7rem(11.2px)。组件 scoped 样式特异性高于全局，必须改在组件内 |
| **emoji 换图标** | 20+ 组件 | 知识库分类、通知中心类型、信号灯、共振、分析页签、评级、点赞、关闭/编辑/删除等；`✓ ✗ ⚠` 单色符号保留 |

### 8.3 有意保留

- `🐟 小鱼儿 / 🌸 花无缺`：家庭成员身份标识，属用户数据
- `✓ ✗ ⚠`：单色文本符号，颜色由容器 CSS 控制，跟随主题
- `ChatMessage.vue` 中的 emoji：AI 生成内容，不属 UI 装饰
- 后端预警文本 / 评测用例名称里的 emoji：数据内容，应在生成端处理
- 导出到 Markdown 文本的 emoji（如 `📈 滚动收益分析`）：纯文本产物，不参与界面渲染

### 8.4 验证

```
移动端 22 页扫描   横向溢出 0；元素越界仅剩 table（横向滚动容器，符合预期）
小字 >11px         knowledge 582→0  chat 86→8  alert-center 193→43  event-radar 207→81
桌面 1440px 回归   table 仍为 display:table；5 页无 pageerror；KPI/简报排版正常
未定义变量          0 个 / token 185 个
```

**未完成**（下一批）：按钮权重体系（机会雷达单卡三色按钮）、两套筛选语言统一、`!important` 63 处、`mark` 高亮 9.6px、articles 页 69 处省略号截断。

---

## 九、第 3 批执行记录：按钮权重 / 层级 / 正文排版（2026-10-21）

### 9.1 按钮权重：从"三色并列"到"一个主操作"

机会雷达顶部三个按钮在模板上已经标了 `btn-secondary / btn-secondary / btn-primary`，但 `.verify-btn`、`.analyze-btn`、`.scan-btn` 各自又重复定义了整套外观（display / padding / border / background / hover / disabled），把全局权重覆盖掉了 —— 而且 `.analyze-btn:hover` 写死紫色 `#7c3aed`。

事件卡内更夸张：「估算金额影响」绿描边、「深度解读影响」紫描边、「受益标的推荐」红描边。三者权级相同，红色那条尤其容易被读成危险操作。

改法：删掉三套重复外观，全部回归 `.btn` 体系，只保留尺寸微调；卡内三按钮统一为中性描边，靠图标区分功能。

### 9.2 紫色不是随便用的，但缺 token

`#7c3aed` 在 EventRadar 出现 26 次，用途集中在「AI 解读 / 大师回测 / 组合智能」等区块的图标与左侧竖线 —— 用紫色标记"AI 生成内容"，这个设计意图是合理的，问题在于硬编码：`rgba(124,58,237,0.04)` 那层 4% 底色在暗色主题下几乎不可见。

新增三个 token（亮/暗双套）：

```
--color-ai          #7c3aed  → 暗色 #a78bfa
--color-ai-bg       rgba(124,58,237,.06) → 暗色 rgba(167,139,250,.12)
--color-ai-border   rgba(124,58,237,.26) → 暗色 rgba(167,139,250,.30)
```

同时改正「金额影响」面板的绿色 `#059669` —— 它表示"正影响"，但项目约定是红涨绿跌，改为 `--color-profit`。

### 9.3 侧边栏父子同时高亮

`Sidebar.vue` 的分组头用的是和子项**完全相同**的 `.active` 类（底色 + 金色渐变竖线），所以当子项激活时父子同时亮起，层级消失。

改为 `.nav-group-active`：只提亮文字与图标，不铺底色、不加竖条。

### 9.4 对话正文：标题只比正文大 0.3px

正文 `0.88rem`，而 `h1/h2/h3` **全部是 `0.9rem`** —— 层级只差 0.32px，等于不存在。段落间距 `0.3rem`（≈5px）也远小于 1.7 的行高。这就是"长回答读起来是一坨"的直接原因。

| 元素 | 旧 | 新 |
|---|---|---|
| h1 | 0.9rem | 1.02rem / 650 |
| h2 | 0.9rem | 0.96rem / 650 |
| h3 | 0.9rem | 0.92rem / 650 |
| h4 | 无 | 0.89rem / 650 |
| 段间距 | 0.3rem | 0.6rem（末段归零） |
| 行高 | 1.7 | 1.78 |

行内代码 `background: rgba(255,255,255,.06)` 在**亮色主题下是白底白块**，改为 `--color-bg-hover` + 边框。

### 9.5 一处回归与修复

第 2 批删 `.verify-btn` 时正则误伤，在媒体查询里留下截断选择器：

```css
.header-actions .scan-btn,
.header-actions .analyze-btn,
.header-actions            ← 少了 .verify-btn { ... }
```

导致紧邻的 `.main-tabs` 规则被吞进未闭合选择器，整段失效；移动端 5 个主 Tab 因此失去 `nowrap`，被压成竖排文字。已补回规则并给 `.main-tab` 加 `white-space: nowrap; flex-shrink: 0`，移动端隐藏数字徽章后恰好放下，超出时横向滚动。

**教训**：批量删 CSS 块必须复查括号平衡与选择器完整性，不能只看删除是否命中。

### 9.6 指标

| 指标 | 第 2 批后 | 本批后 |
|---|---|---|
| 硬编码 hex | 1383 | 1354 |
| CSS token | 185 | 188 |
| 未定义变量 | 0 | 0 |

---

## 十、第 4 批执行记录：可读性 / 移动布局 / 控件统一（2026-10-21）

### 10.1 后端字段直接漏给用户

估值页「数据健康」面板渲染的是 `final_source` 原文。实测该字段有 14 种取值：

```
expired_leiniuniu  local_failed_online_disabled  liuyi  leiniuniu  failed
online_akshare_cached  dd_luosiding  expired_akshare  expired_dd_luosiding
ttfund  ttfund_online  expired_ttfund  akshare  expired_ttfund_online
```

前端映射表只覆盖 6 个，其余 8 种**原样吐英文**给用户。

改法：不再堆硬编码映射，改为解析命名规律 `[expired_][online_]<source>[_cached]`：

| 原值 | 显示 |
|---|---|
| `local_failed_online_disabled` | 本地失败·在线停用 |
| `online_akshare_cached` | akshare·在线·缓存 |
| `expired_dd_luosiding` | 螺丝钉·过期 |
| `expired_ttfund_online` | 天天基金·过期·在线 |

新增数据源只需在 `SOURCE_NAMES` 加一项。

### 10.2 预警中心移动端三个硬伤

截图扫出来的，静态扫描抓不到（不表现为溢出或省略）：

1. **三栏 tab 被压成竖排单字** —— `.ac-tab` 内部是 label/eyebrow/badge 三个 span，但按钮本身没有 `display:flex`，被 flex 压缩后中文逐字换行。加 flex + `white-space:nowrap`，移动端隐藏英文副标（ADVICE/ALERTS/MERGED）
2. **「刷新风险快照 / 设置风险上限」溢出卡片** —— `.risk-head` 在移动端没改纵向，标题块把按钮组推出容器。改 `flex-direction:column` + 按钮等分
3. **面板头文字互相重叠** —— `.alert-panel-header` 有 5 个子元素却没有 `flex-wrap`，长文本 `strong` 挤压其余元素。补 wrap + `.title { min-width: 0 }`

### 10.3 筛选控件：同类控件两套形状

| 控件 | 知识库 | 分析日志 | 通知中心 | 文章管理 | 预警中心 |
|---|---|---|---|---|---|
| 圆角 | full | 6px | 12px | 999px | 12px |
| 选中态 | 主色底白字 | — | 主色底白字 | 主色底白字 | **青色 accent 底** |

统一为 `var(--radius-full)` + 主色底白字。

### 10.4 装饰竖线：渐变在长卡片上会露底

`.editorial-card::before` 是 `gold → primary` 渐变。在高度超过 ~120px 的卡片上，竖直方向会完整露出**中间的橙紫过渡段**（文章列表卡片、风险概览都出现了），观感像渲染错误。

3px 宽的装饰条承受不了渐变叙事，全局改为纯金色。第 2 批在知识库 KPI 网格里已单独修过一次，这次是根治。

### 10.5 关于 `!important` 63 处：评估后不动

集中在 `PortfolioManagement`（30 处），绝大多数是涨跌色覆盖（`.positive` / `.profit-up` 等语义类要压过表格单元格的默认色）。去掉需要逐个提升选择器特异性，**回归风险明显高于收益**（用户看不见）。保留。

### 10.6 指标

| 指标 | 第 3 批后 | 本批后 |
|---|---|---|
| emoji | 79 | 78 |
| 硬编码 hex | 1354 | 1352 |
| `!important` | 63 | 63（评估后保留） |
| 未定义变量 | 0 | 0 |

---

## 十一、第 5 批：知识库筛选的信息架构（2026-10-21）

前四批都是"把已有的东西修对"。这一批是唯一一次**改结构**——因为问题不在样式，在信息架构。

### 11.1 问题：31 个选项平铺

```
FILTER · 筛选
[书籍·原则 2439] [书籍·策略 2128] [书籍·case 1388] [书籍·indicator 739] [书籍·公式 632]
[书籍·心理学 452] [个人笔记·best_practice 120] [个人笔记·llm_engineering 105] ...
…共 5 行 31 个
```

三个叠加的问题：

1. **认知过载**：31 个同级选项，用户无法快速定位
2. **噪声**：其中 6 个是 `best_practice, performance` 这类**内容多维标签**被当成子分类，count 只有 1~2，占了 6 个位置却只对应 7 条数据
3. **中英混排**：`subcategoryMap` 只覆盖 17 项，缺 14 项 → `书籍·case`、`个人笔记·llm_engineering`

### 11.2 结构：分类 → 子分类

第一级（始终可见，6 个）：

```
FILTER · 分类   [全部 8852] [书籍 8214] [个人笔记 579] [概念 31] [基金指标 21] [策略 7]
```

第二级（选中分类后出现）：只显示该分类下的子分类，按 count 降序，

| 分类 | 子分类数 | 示例 |
|---|---|---|
| 书籍 | 7 | 全部 / 原则 2439 / 策略 2128 / 案例 1388 / 指标 739 / 公式 632 / 心理学 452 / 概念 56 |
| 个人笔记 | 11 | 全部 / 最佳实践 120 / LLM 工程 105 / 性能 89 / Prompt 工程 52 / 架构 49 … |
| 概念 / 策略 / 基金指标 | 1~4 | 估值 / 风险 / 宏观 … |

**两级必须视觉分权**：一级用实底主色，二级用透明底 + 主色浅底。同权重会让用户分不清"选了分类"和"选了细分"。

### 11.3 三个连带的布局问题

1. **筛选区原本在书籍网格之后** —— 选「书籍」时 37 本书的卡片网格把筛选推到屏幕外，等于看不见。移到网格之前。
2. **选子分类后书籍网格仍全量显示** —— 用户已经在查具体条目，37 本书的网格只会把结果推远。改为选子分类时隐藏书单（浏览模式 → 查找模式）。
3. **移动端 6 张 KPI 卡占近 500px** —— 把筛选整块顶出首屏。压缩内距（0.9→0.6rem）与数字字号（1.5→1.12rem）。

### 11.4 验证

桌面 1440 / 移动 390 各走完四步交互：

```
步骤1 默认        1 行 / 6 分类 / 0 子分类
步骤2 选书籍      2 行 / 子分类 8 个：全部 原则 策略 案例 指标 公式 心理学 概念
步骤3 选个人笔记  子分类 12 个：全部 最佳实践 LLM工程 性能 Prompt工程 架构 Agent RAG 调试 工具评测 DevOps 策略
步骤4 点子分类    activeSub=最佳实践 120，ovf=0，无 pageerror
```

中英混排归零。

### 11.5 遗留

条目正文里仍会出现 `分类: best_practice` 这类英文（属于 markdown 内容原文，不是筛选层），需要后端在入库或渲染时映射。属于数据治理，不在 UI 层处理。

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
