// Dashboard E2E 测试 — 真实浏览器验证关键交互

import { test, expect } from '@playwright/test'

test.describe('Dashboard 页面', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/')
    // 等待 Dashboard 加载
    await page.waitForSelector('.dashboard, .dash-header, .page-title', { timeout: 10000 })
  })

  test('页面加载正常，标题显示', async ({ page }) => {
    const title = await page.textContent('.page-title')
    expect(title).toContain('每日投资决策看板')
  })

  test('债市温度仪表盘显示', async ({ page }) => {
    // 等待温度数据加载
    const gauge = page.locator('.temp-gauge-card')
    await expect(gauge).toBeVisible({ timeout: 10000 })
  })

  test('热点新闻卡片存在', async ({ page }) => {
    // 热点卡片应该存在
    const newsCard = page.locator('.news-list, .card-empty, .card-loading')
    await expect(newsCard.first()).toBeVisible({ timeout: 10000 })
  })
})

test.describe('对话页面', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/')
    // 点击 AI 对话导航
    await page.click('text=AI 对话')
    await page.waitForTimeout(1000)
  })

  test('对话列表加载', async ({ page }) => {
    // 对话列表应该显示
    const convList = page.locator('.conv-list, .conv-item')
    await expect(convList.first()).toBeVisible({ timeout: 10000 })
  })

  test('新建对话按钮可点击', async ({ page }) => {
    const newBtn = page.locator('.btn-new-conv, text=新建')
    await expect(newBtn.first()).toBeVisible()
    await newBtn.first().click()
    // 应该有 toast 提示
    await page.waitForTimeout(1000)
  })

  test('对话切换不串状态', async ({ page }) => {
    // 点击第一个对话
    const firstConv = page.locator('.conv-item').first()
    await firstConv.click()
    await page.waitForTimeout(500)

    // 点击第二个对话（如果存在）
    const secondConv = page.locator('.conv-item').nth(1)
    if (await secondConv.isVisible()) {
      await secondConv.click()
      await page.waitForTimeout(500)
      // sending 状态应该为 false（输入框可用）
      const input = page.locator('.chat-input, textarea')
      await expect(input.first()).toBeEnabled()
    }
  })
})

test.describe('持仓管理页面', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/')
    await page.click('text=持仓管理')
    await page.waitForTimeout(1000)
  })

  test('页面加载正常', async ({ page }) => {
    // 持仓页面应该有内容
    const content = page.locator('.portfolio-page, .holdings-list, .card')
    await expect(content.first()).toBeVisible({ timeout: 10000 })
  })

  test('风险预警卡片存在', async ({ page }) => {
    // 预警面板应该存在
    const alertPanel = page.locator('.alert-panel, text=风险预警')
    await expect(alertPanel.first()).toBeVisible({ timeout: 10000 })
  })

  test('巡检按钮可点击', async ({ page }) => {
    const scanBtn = page.locator('text=巡检')
    if (await scanBtn.isVisible()) {
      await scanBtn.click()
      await page.waitForTimeout(2000)
    }
  })
})

test.describe('估值页面', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/')
    await page.click('text=估值数据')
    await page.waitForTimeout(1000)
  })

  test('页面加载正常', async ({ page }) => {
    const content = page.locator('.valuation-page, .trend-chart, .card')
    await expect(content.first()).toBeVisible({ timeout: 10000 })
  })

  test('趋势图渲染', async ({ page }) => {
    // ECharts 图表应该有 canvas
    const canvas = page.locator('canvas')
    await expect(canvas.first()).toBeVisible({ timeout: 10000 })
  })
})
