import { navItems, getVisibleNavItems, isDevMode } from './navigation.js'

export const mobileTabs = [
  { key: 'dashboard', label: '看板', icon: 'dashboard' },
  { key: 'chat', label: '对话', icon: 'chat' },
  { key: 'portfolio', label: '持仓', icon: 'portfolio' },
  { key: 'valuation', label: '估值', icon: 'valuation' },
  { key: '__more__', label: '更多', icon: 'more' },
]

const tabKeys = new Set(mobileTabs.map(item => item.key))

// 全量分组（未过滤）：供测试与「开发者模式」下展示使用。
// 普通用户请使用 getVisibleMobileMoreGroups() 获取过滤后的菜单。
export const mobileMoreGroups = navItems
  .filter(group => group.children)
  .map(group => ({
    label: group.label,
    items: group.children.filter(item => !tabKeys.has(item.key)),
  }))
  .filter(group => group.items.length)

export const mobilePageKeys = [
  ...new Set([
    ...mobileTabs.filter(item => item.key !== '__more__'),
    ...mobileMoreGroups.flatMap(group => group.items),
  ].map(item => item.key)),
]

/**
 * 返回过滤后的「更多」菜单分组（按开发者模式隐藏 hidden/devOnly 项）。
 * 与桌面端 Sidebar 共用同一套可见性规则。
 */
export function getVisibleMobileMoreGroups(devMode = isDevMode()) {
  return getVisibleNavItems(devMode)
    .filter(group => group.children)
    .map(group => ({
      label: group.label,
      items: group.children.filter(item => !tabKeys.has(item.key)),
    }))
    .filter(group => group.items.length)
}
