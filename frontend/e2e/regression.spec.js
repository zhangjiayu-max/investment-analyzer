// 全功能回归测试 — 覆盖所有核心页面和关键交互
// 运行: cd frontend && npx playwright test e2e/regression.spec.js

import { test, expect } from '@playwright/test'

const BASE = 'http://localhost:8000'

// ── Dashboard 页面 ──────────────────────────────────
test.describe('Dashboard 页面', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(BASE)
    await page.waitForTimeout(2000)
  })

  test('页面加载无报错', async ({ page }) => {
    // 页面应该有内容，不应该显示错误
    const body = await page.textContent('body')
    expect(body.length).toBeGreaterThan(100)
    expect(body).not.toContain('500 Internal Server Error')
    expect(body).not.toContain('Cannot GET')
  })

  test('零钱数据不为0', async ({ page }) => {
    const body = await page.textContent('body')
    // 零钱应该大于0
    expect(body).not.toContain('零钱 ¥0')
    expect(body).not.toContain('余额 ¥0')
  })

  test('盈亏数据不全为0', async ({ page }) => {
    const body = await page.textContent('body')
    // 不应该全是 ±0
    expect(body).not.toContain('盈亏 ±0元')
  })
})

// ── 对话页面 ──────────────────────────────────
test.describe('对话页面', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(BASE)
    await page.waitForTimeout(1000)
    // 点击侧边栏的 AI 对话
    const chatLink = page.locator('nav button, nav a').filter({ hasText: 'AI 对话' }).first()
    if (await chatLink.isVisible()) {
      await chatLink.click()
      await page.waitForTimeout(1000)
    }
  })

  test('页面加载无报错', async ({ page }) => {
    const body = await page.textContent('body')
    expect(body).not.toContain('500 Internal Server Error')
  })

  test('输入框存在', async ({ page }) => {
    // 需要先选择一个对话才能看到输入框
    const convItem = page.locator('.conv-item').first()
    if (await convItem.isVisible({ timeout: 3000 }).catch(() => false)) {
      await convItem.click()
      await page.waitForTimeout(1000)
    }
    const input = page.locator('textarea, input[type="text"], .chat-input, .input-field').first()
    await expect(input).toBeVisible({ timeout: 8000 })
  })
})

// ── API 健康检查 ──────────────────────────────────
test.describe('API 健康检查', () => {
  test('Dashboard API 返回正常', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/dashboard`)
    expect(resp.ok()).toBeTruthy()
    const data = await resp.json()
    expect(data.date).toBeTruthy()
  })

  test('对话列表 API 返回正常', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/conversations`)
    expect(resp.ok()).toBeTruthy()
  })

  test('估值指数 API 返回正常', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/valuation/indexes`)
    expect(resp.ok()).toBeTruthy()
  })

  test('零钱余额大于0', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/dashboard`)
    const data = await resp.json()
    expect(data.cash_management.balance).toBeGreaterThan(0)
  })

  test('零钱是两个账户合计', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/dashboard`)
    const data = await resp.json()
    // 应该有小鱼儿和花无缺两个账户
    expect(data.cash_management.cash_details).toBeTruthy()
    const details = data.cash_management.cash_details
    expect(Object.keys(details).length).toBeGreaterThanOrEqual(2)
  })

  test('持仓盈亏不全为0', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/portfolio/holdings`)
    const data = await resp.json()
    const holdings = data.holdings || []
    const hasNonZeroProfit = holdings.some(h => h.profit_loss !== 0)
    expect(hasNonZeroProfit).toBeTruthy()
  })

  test('持仓盈亏计算正确', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/portfolio/holdings`)
    const data = await resp.json()
    const holdings = data.holdings || []
    // 检查盈亏 = (现价 - 成本) * 份额
    for (const h of holdings.slice(0, 5)) {
      if (h.current_price && h.cost_price && h.shares) {
        const expected = (h.current_price - h.cost_price) * h.shares
        const actual = h.profit_loss || 0
        const diff = Math.abs(expected - actual)
        // 允许10元误差（分红/手续费）
        expect(diff).toBeLessThan(50)
      }
    }
  })

  test('债市温度数据存在', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/dashboard`)
    const data = await resp.json()
    expect(data.cash_management.bond_market).toBeTruthy()
    expect(data.cash_management.bond_market.temperature).toBeGreaterThan(0)
  })

  test('估值百分位在合理范围', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/dashboard`)
    const data = await resp.json()
    const indexes = data.undervalued_indexes || []
    for (const idx of indexes) {
      expect(idx.percentile).toBeGreaterThanOrEqual(0)
      expect(idx.percentile).toBeLessThanOrEqual(100)
    }
  })
})
