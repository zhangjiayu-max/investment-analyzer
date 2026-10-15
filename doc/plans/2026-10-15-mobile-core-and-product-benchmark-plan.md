# 移动端核心闭环与成熟理财模式对标实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 让每日看板、AI 对话、机会雷达、持仓管理及其决策入口在手机视口可达、可操作、可回归验证；同时将成熟理财产品的有效模式转化为有取舍的后续升级清单。

**Architecture:** 移动端不复制桌面侧栏，而是继续使用 `MobileApp.vue` 的底部 Tab + “更多”抽屉。抽屉菜单从桌面共用导航配置派生，避免两个导航清单长期漂移。自动化通过 Playwright 的手机项目验证核心页面能进入、页面主体存在且没有根级横向溢出。

**Tech Stack:** Vue 3、Vite、Vitest、Playwright、FastAPI。

---

## 文件边界

- `frontend/src/mobileNavigation.js`：移动 Tab、由桌面导航派生的“更多”分组与可达页面键。
- `frontend/src/components/MobileApp.vue`：使用共用移动导航配置，并补齐桌面可达页面的异步组件映射。
- `frontend/src/__tests__/mobile-navigation.test.js`：验证桌面导航与移动导航不漂移。
- `frontend/playwright.config.js`：增加 390px 手机项目。
- `frontend/e2e/mobile-core.spec.js`：验证四个重点模块和“更多”菜单的移动端进入、布局与回退能力。
- `doc/plans/2026-10-15-core-finance-product-benchmark.md`：记录 YNAB/Monarch、Vanguard/Wealthfront、Fidelity/Schwab 的可吸收模式、当前覆盖和优先级。

## Task 1：锁定移动导航契约

- [x] **Step 1: 编写失败的导航同步测试**

在 `frontend/src/__tests__/mobile-navigation.test.js` 中断言：`flatNavItems` 中的每一个桌面可达页面键，都在 `mobilePageKeys` 中；主 Tab 不应重复出现在“更多”菜单。

- [x] **Step 2: 运行测试，确认当前移动端遗漏页导致失败**

Run: `cd frontend && npm test -- mobile-navigation.test.js`

Expected: FAIL，缺少 `decision-center`、`feature-usage` 等桌面导航页面。

- [x] **Step 3: 创建派生式移动导航配置**

在 `frontend/src/mobileNavigation.js` 导出：

```js
import { navItems } from './navigation.js'

export const mobileTabs = [
  { key: 'dashboard', label: '看板', icon: 'dashboard' },
  { key: 'chat', label: '对话', icon: 'chat' },
  { key: 'portfolio', label: '持仓', icon: 'portfolio' },
  { key: 'valuation', label: '估值', icon: 'valuation' },
  { key: '__more__', label: '更多', icon: 'more' },
]

const tabKeys = new Set(mobileTabs.map(item => item.key))
export const mobileMoreGroups = navItems
  .filter(group => group.children)
  .map(group => ({
    label: group.label,
    items: group.children.filter(item => !tabKeys.has(item.key)),
  }))
  .filter(group => group.items.length)

export const mobilePageKeys = [
  ...new Set([...mobileTabs.filter(item => item.key !== '__more__'), ...mobileMoreGroups.flatMap(group => group.items)].map(item => item.key)),
]
```

- [x] **Step 4: 替换 `MobileApp.vue` 中重复的导航声明并补齐组件映射**

将本地 `tabs`、`moreGroups` 替换为从 `mobileNavigation.js` 导入；将 `DecisionCenter` 和 `FeatureUsagePage` 加入异步组件与 `pageComponents` 映射。保持 `moreItems` 只用于当前标题查找。

- [x] **Step 5: 运行单元测试**

Run: `cd frontend && npm test -- mobile-navigation.test.js`

Expected: PASS。

## Task 2：手机视口端到端回归

- [x] **Step 1: 编写失败的手机核心流程测试**

在 `frontend/e2e/mobile-core.spec.js` 中以 390 x 844 视口验证：

```js
test('uses the mobile shell without root horizontal overflow', async ({ page }) => {
  await page.goto('/')
  await expect(page.locator('.mobile-app')).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})
```

并验证底部 Tab 能抵达看板、对话、持仓与估值；“更多”中可进入机会雷达、决策中心、功能分析，且页面主体不为空。

- [x] **Step 2: 在 Playwright 配置中增加 mobile-chromium 项目并运行测试**

Run: `cd frontend && npx playwright test e2e/mobile-core.spec.js --project=mobile-chromium`

Expected: FAIL，直到移动端映射与可点击选择器完备。

- [x] **Step 3: 做最小修复并保留高密度页面的信息层级**

只修复测试暴露的映射、横向溢出、底部导航遮挡或点击不可达问题；表格保留内部横向滚动，不压缩数值内容。

- [x] **Step 4: 重新运行手机 E2E、全量前端单测与生产构建**

Run: `cd frontend && npx playwright test e2e/mobile-core.spec.js --project=mobile-chromium && npm test && npm run build`

Expected: all commands exit 0。

## Task 3：成熟产品对标与实施取舍

- [x] **Step 1: 编写对标决策文档**

比较三类成熟模式：

| 来源类型 | 吸收模式 | 本系统落点 | 取舍 |
| --- | --- | --- | --- |
| YNAB / Monarch | 预算与实际、类别超支预警 | 支出记录、月结余 | 下一阶段补预算上限与预警；不做复式记账 |
| Vanguard / Wealthfront | 目标优先、可投资资金校验、解释风险 | 资金桶、现金流、智能补仓 | 已接现金流上限；补“建议依据”展示 |
| Fidelity / Schwab | 今日行动收件箱、执行状态回写 | 每日看板、决策档案、交易计划 | 现有能力收敛展示；不另建重复中心 |

将事项按 P0（移动可靠性）、P1（预算对实际和今日行动排序）、P2（固定支出自动续期与提醒）排序，并明确不建设资讯流、社交跟投、自动下单。

- [ ] **Step 2: 提交文档与代码**

Run: `git add frontend doc/plans && git commit -m "feat: harden mobile core financial workflows"`

Expected: commit created after verification.
