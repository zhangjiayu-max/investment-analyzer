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
  ...new Set([
    ...mobileTabs.filter(item => item.key !== '__more__'),
    ...mobileMoreGroups.flatMap(group => group.items),
  ].map(item => item.key)),
]
