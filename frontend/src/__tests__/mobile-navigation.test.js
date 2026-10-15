import { describe, expect, it } from 'vitest'
import { flatNavItems } from '../navigation.js'
import { mobileMoreGroups, mobilePageKeys, mobileTabs } from '../mobileNavigation.js'

describe('mobile navigation config', () => {
  it('keeps every desktop navigation page reachable on mobile', () => {
    const mobileKeys = new Set(mobilePageKeys)
    const missing = flatNavItems
      .filter(item => !item.external && item.key !== 'analysis')
      .filter(item => !mobileKeys.has(item.key))
      .map(item => item.key)

    expect(missing).toEqual([])
  })

  it('does not duplicate primary tabs in the more menu', () => {
    const tabKeys = new Set(mobileTabs.map(item => item.key))
    const duplicateKeys = mobileMoreGroups
      .flatMap(group => group.items)
      .filter(item => tabKeys.has(item.key))
      .map(item => item.key)

    expect(duplicateKeys).toEqual([])
  })
})
