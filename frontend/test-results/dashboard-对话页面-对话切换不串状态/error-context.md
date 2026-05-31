# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: dashboard.spec.js >> 对话页面 >> 对话切换不串状态
- Location: e2e/dashboard.spec.js:52:3

# Error details

```
Test timeout of 30000ms exceeded while running "beforeEach" hook.
```

```
Error: page.goto: net::ERR_ABORTED; maybe frame was detached?
Call log:
  - navigating to "http://localhost:8000/", waiting until "load"

```

# Test source

```ts
  1   | // Dashboard E2E 测试 — 真实浏览器验证关键交互
  2   | 
  3   | import { test, expect } from '@playwright/test'
  4   | 
  5   | test.describe('Dashboard 页面', () => {
  6   |   test.beforeEach(async ({ page }) => {
  7   |     await page.goto('/')
  8   |     // 等待 Dashboard 加载
  9   |     await page.waitForSelector('.dashboard, .dash-header, .page-title', { timeout: 10000 })
  10  |   })
  11  | 
  12  |   test('页面加载正常，标题显示', async ({ page }) => {
  13  |     const title = await page.textContent('.page-title')
  14  |     expect(title).toContain('每日投资决策看板')
  15  |   })
  16  | 
  17  |   test('债市温度仪表盘显示', async ({ page }) => {
  18  |     // 等待温度数据加载
  19  |     const gauge = page.locator('.temp-gauge-card')
  20  |     await expect(gauge).toBeVisible({ timeout: 10000 })
  21  |   })
  22  | 
  23  |   test('热点新闻卡片存在', async ({ page }) => {
  24  |     // 热点卡片应该存在
  25  |     const newsCard = page.locator('.news-list, .card-empty, .card-loading')
  26  |     await expect(newsCard.first()).toBeVisible({ timeout: 10000 })
  27  |   })
  28  | })
  29  | 
  30  | test.describe('对话页面', () => {
  31  |   test.beforeEach(async ({ page }) => {
> 32  |     await page.goto('/')
      |                ^ Error: page.goto: net::ERR_ABORTED; maybe frame was detached?
  33  |     // 点击 AI 对话导航
  34  |     await page.click('text=AI 对话')
  35  |     await page.waitForTimeout(1000)
  36  |   })
  37  | 
  38  |   test('对话列表加载', async ({ page }) => {
  39  |     // 对话列表应该显示
  40  |     const convList = page.locator('.conv-list, .conv-item')
  41  |     await expect(convList.first()).toBeVisible({ timeout: 10000 })
  42  |   })
  43  | 
  44  |   test('新建对话按钮可点击', async ({ page }) => {
  45  |     const newBtn = page.locator('.btn-new-conv, text=新建')
  46  |     await expect(newBtn.first()).toBeVisible()
  47  |     await newBtn.first().click()
  48  |     // 应该有 toast 提示
  49  |     await page.waitForTimeout(1000)
  50  |   })
  51  | 
  52  |   test('对话切换不串状态', async ({ page }) => {
  53  |     // 点击第一个对话
  54  |     const firstConv = page.locator('.conv-item').first()
  55  |     await firstConv.click()
  56  |     await page.waitForTimeout(500)
  57  | 
  58  |     // 点击第二个对话（如果存在）
  59  |     const secondConv = page.locator('.conv-item').nth(1)
  60  |     if (await secondConv.isVisible()) {
  61  |       await secondConv.click()
  62  |       await page.waitForTimeout(500)
  63  |       // sending 状态应该为 false（输入框可用）
  64  |       const input = page.locator('.chat-input, textarea')
  65  |       await expect(input.first()).toBeEnabled()
  66  |     }
  67  |   })
  68  | })
  69  | 
  70  | test.describe('持仓管理页面', () => {
  71  |   test.beforeEach(async ({ page }) => {
  72  |     await page.goto('/')
  73  |     await page.click('text=持仓管理')
  74  |     await page.waitForTimeout(1000)
  75  |   })
  76  | 
  77  |   test('页面加载正常', async ({ page }) => {
  78  |     // 持仓页面应该有内容
  79  |     const content = page.locator('.portfolio-page, .holdings-list, .card')
  80  |     await expect(content.first()).toBeVisible({ timeout: 10000 })
  81  |   })
  82  | 
  83  |   test('风险预警卡片存在', async ({ page }) => {
  84  |     // 预警面板应该存在
  85  |     const alertPanel = page.locator('.alert-panel, text=风险预警')
  86  |     await expect(alertPanel.first()).toBeVisible({ timeout: 10000 })
  87  |   })
  88  | 
  89  |   test('巡检按钮可点击', async ({ page }) => {
  90  |     const scanBtn = page.locator('text=巡检')
  91  |     if (await scanBtn.isVisible()) {
  92  |       await scanBtn.click()
  93  |       await page.waitForTimeout(2000)
  94  |     }
  95  |   })
  96  | })
  97  | 
  98  | test.describe('估值页面', () => {
  99  |   test.beforeEach(async ({ page }) => {
  100 |     await page.goto('/')
  101 |     await page.click('text=估值数据')
  102 |     await page.waitForTimeout(1000)
  103 |   })
  104 | 
  105 |   test('页面加载正常', async ({ page }) => {
  106 |     const content = page.locator('.valuation-page, .trend-chart, .card')
  107 |     await expect(content.first()).toBeVisible({ timeout: 10000 })
  108 |   })
  109 | 
  110 |   test('趋势图渲染', async ({ page }) => {
  111 |     // ECharts 图表应该有 canvas
  112 |     const canvas = page.locator('canvas')
  113 |     await expect(canvas.first()).toBeVisible({ timeout: 10000 })
  114 |   })
  115 | })
  116 | 
```