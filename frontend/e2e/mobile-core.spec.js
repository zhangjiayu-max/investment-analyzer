import { test, expect } from '@playwright/test'

test.describe('手机端核心理财流程', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('hasVisited', 'true')
      localStorage.setItem('activePage', 'dashboard')
    })
    await page.goto('/')
    await expect(page.locator('.mobile-app')).toBeVisible()
  })

  test('使用独立移动布局且页面不产生根级横向滚动', async ({ page }) => {
    await expect(page.locator('.mobile-tabbar')).toBeVisible()
    await expect(page.locator('.sidebar')).toHaveCount(0)

    expect(await page.evaluate(() => (
      document.documentElement.scrollWidth <= window.innerWidth
    ))).toBe(true)
  })

  test('底部导航可进入每日看板、AI 对话、持仓和估值', async ({ page }) => {
    const pages = [
      { label: '看板', selector: '.dashboard, .dash-header, .page-title' },
      { label: '对话', selector: '.chat-view, .chat-container, .conv-sidebar' },
      { label: '持仓', selector: '.portfolio-page' },
      { label: '估值', selector: '.valuation-page, .trend-chart, .card' },
    ]

    for (const item of pages) {
      await page.locator('.mobile-tabbar').getByRole('button', { name: item.label, exact: true }).click()
      await expect(page.locator(item.selector).first()).toBeVisible({ timeout: 15000 })
    }
  })

  test('更多菜单可进入机会雷达与桌面同步页面', async ({ page }) => {
    const routes = [
      { label: '机会雷达', selector: '.event-radar-page' },
      { label: '决策中心', selector: '.decision-center' },
      { label: '功能分析', selector: '.feature-usage-page' },
    ]

    for (const route of routes) {
      await page.locator('.mobile-tabbar').getByRole('button', { name: '更多', exact: true }).click()
      const moreSheet = page.locator('.mobile-more-sheet')
      await expect(moreSheet).toBeVisible()
      await moreSheet.locator('.mobile-more-item', { hasText: route.label }).click()
      await expect(page.locator(route.selector)).toBeVisible({ timeout: 15000 })
    }
  })
})
