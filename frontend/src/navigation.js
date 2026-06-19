export const navItems = [
  { key: 'dashboard', label: '每日看板', icon: 'dashboard', hot: true },
  { key: 'market-intelligence', label: '市场热点', icon: 'fire' },
  { key: 'chat', label: 'AI 对话', icon: 'chat' },
  { key: 'articles', label: '文章管理', icon: 'articles' },
  { key: 'valuation', label: '估值数据', icon: 'valuation', hot: true },
  { key: 'gallery', label: '估值图片', icon: 'gallery' },
  { key: 'portfolio', label: '持仓管理', icon: 'portfolio', hot: true },
  {
    key: 'group-knowledge',
    label: '知识库',
    icon: 'author',
    children: [
      { key: 'author', label: '作者文章', icon: 'author' },
      { key: 'linked', label: '个人文档', icon: 'link' },
      { key: 'knowledge', label: '蒸馏知识', icon: 'book' },
      { key: 'rag-test', label: '命中测试', icon: 'rag' },
      { key: 'rag', label: 'RAG 分析', icon: 'rag' },
    ],
  },
  {
    key: 'group-bond',
    label: '债券分析',
    icon: 'bond',
    children: [
      { key: 'bond', label: '债市市场温度', icon: 'bond' },
    ],
  },
  { key: 'admin-agents', label: 'Agent 管理', icon: 'admin' },
  { key: 'token-usage', label: 'Token 用量', icon: 'token' },
  { key: 'system-config', label: '系统配置', icon: 'config' },
  {
    key: 'group-evolution',
    label: '进化系统',
    icon: 'evolution',
    children: [
      { key: 'quality-dashboard', label: '质量仪表盘', icon: 'chart' },
      { key: 'bad-cases', label: 'Bad Case', icon: 'bug' },
      { key: 'eval-suite', label: '评测集', icon: 'check' },
    ],
  },
]

export const flatNavItems = navItems.flatMap(item => item.children || item)
