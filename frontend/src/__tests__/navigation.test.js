import { describe, expect, it } from 'vitest'
import { flatNavItems, navItems, getVisibleNavItems } from '../navigation.js'
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

  it('contains grouped navigation items for knowledge and system governance sections', () => {
    const groups = navItems.filter(item => item.children).map(item => item.key)

    expect(groups).toContain('group-knowledge')
    expect(groups).toContain('group-system')
  })

  it('keeps RAG debugging pages out of primary navigation', () => {
    const keys = flatNavItems.map(item => item.key)

    expect(keys).not.toContain('rag')
    expect(keys).not.toContain('rag-test')
  })

  describe('功能精简可见性 (getVisibleNavItems)', () => {
    it('hides the devOnly system-governance group by default', () => {
      const keys = getVisibleNavItems(false).map(g => g.key)
      expect(keys).not.toContain('group-system')
    })

    it('hides hidden leaf items by default but keeps hot core pages', () => {
      const visible = getVisibleNavItems(false)
      const leafKeys = visible.flatMap(g => g.children || g).map(i => i.key)

      // 下沉页面默认隐藏
      expect(leafKeys).not.toContain('attribution')
      expect(leafKeys).not.toContain('knowledge')
      // 核心页面保留
      expect(leafKeys).toContain('dashboard')
      expect(leafKeys).toContain('chat')
      expect(leafKeys).toContain('event-radar')
      expect(leafKeys).toContain('health-v2')
      expect(visible.find(g => g.key === 'group-knowledge').children.map(i => i.key)).toContain('gallery')
    })

    it('shows everything in dev mode', () => {
      const visible = getVisibleNavItems(true)
      const keys = visible.map(g => g.key)
      const leafKeys = visible.flatMap(g => g.children || g).map(i => i.key)

      expect(keys).toContain('group-system')
      expect(leafKeys).toContain('attribution')
      expect(leafKeys).toContain('admin-agents')
    })
  })
})
