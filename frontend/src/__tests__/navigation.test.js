import { describe, expect, it } from 'vitest'
import { flatNavItems, navItems } from '../navigation.js'
import { pageComponentKeys } from '../pageRegistry.js'

describe('navigation config', () => {
  it('keeps sidebar navigation and page components in sync', () => {
    const pageKeys = new Set(pageComponentKeys)
    const missing = flatNavItems
      .filter(item => !item.external && item.key !== 'analysis')
      .filter(item => !pageKeys.has(item.key))
      .map(item => item.key)

    expect(missing).toEqual([])
  })

  it('contains grouped navigation items for knowledge and evolution sections', () => {
    const groups = navItems.filter(item => item.children).map(item => item.key)

    expect(groups).toContain('group-knowledge')
    expect(groups).toContain('group-evolution')
  })
})
