export const navItems = [
  { key: 'dashboard', label: '每日看板', icon: 'dashboard', hot: true,
    desc: '每日投资概览：持仓健康、市场行情、估值机会、决策待办' },

  // ── 市场雷达 ──
  {
    key: 'group-market',
    label: '市场雷达',
    icon: 'satellite',
    desc: '市场热点、前瞻事件、债市温度',
    children: [
      { key: 'market-intelligence', label: '市场热点', icon: 'fire',
        desc: '板块轮动热度、资金流向、涨跌停统计' },
      { key: 'event-radar', label: '机会雷达', icon: 'satellite', hot: true,
        desc: '前瞻事件扫描 + 关注基金上车信号 + 落地验证' },
      { key: 'bond', label: '债市分析', icon: 'bond',
        desc: '债市温度计、收益率曲线、债券基金推荐' },
    ],
  },

  { key: 'chat', label: 'AI 对话', icon: 'chat',
    desc: '多专家协作对话：估值分析、持仓诊断、决策建议' },

  // ── 持仓管理 ──
  {
    key: 'group-portfolio',
    label: '持仓管理',
    icon: 'portfolio',
    desc: '持仓、补仓、风险预警',
    children: [
      { key: 'portfolio', label: '持仓管理', icon: 'portfolio', hot: true,
        desc: '基金持仓CRUD、交易记录、盈亏分析、AI诊断' },
      { key: 'smart-add', label: '智能补仓', icon: 'trending-down',
        desc: '估值z-score加权定投 + 金字塔补仓计划' },
      { key: 'alert-center', label: '风险与提示', icon: 'warning', hot: true,
        desc: '持仓集中度、亏损预警、估值阈值、建议验证提醒' },
    ],
  },

  // ── 理财决策 ──
  {
    key: 'group-decision',
    label: '理财决策',
    icon: 'clipboard-list',
    desc: '决策档案、归因分析、行为诊断、策略验证',
    children: [
      { key: 'decisions', label: '决策档案', icon: 'clipboard-list', hot: true,
        desc: '投资决策记录、执行追踪、复盘评分' },
      { key: 'attribution', label: '收益归因', icon: 'chart',
        desc: 'Brinson收益归因：选股效应 vs 择时效应' },
      { key: 'behavior', label: '行为诊断', icon: 'brain',
        desc: '处置效应、锚定效应、羊群效应、过度交易诊断' },
      { key: 'accuracy', label: '决策准确率', icon: 'target',
        desc: '决策方向验证、准确率统计、趋势分析' },
      { key: 'allocation-dashboard', label: '配置偏离', icon: 'pie-chart',
        desc: '资产配置偏离度、再平衡建议' },
      { key: 'strategy-sandbox', label: '策略沙盒', icon: 'bar-chart',
        desc: '策略回测验证、参数调优、对比分析' },
    ],
  },

  // ── 家庭财务 ──
  {
    key: 'group-finance',
    label: '家庭财务',
    icon: 'wallet',
    desc: '家庭财务总览、资金桶管理',
    children: [
      { key: 'family-finance', label: '财务总览', icon: 'wallet',
        desc: '家庭资产负债、收支流水、财务健康度' },
      { key: 'goal-buckets', label: '资金桶', icon: 'wallet',
        desc: '按目标分桶管理资金：备用金、定投、增值' },
    ],
  },

  // ── 知识中心 ──
  {
    key: 'group-knowledge',
    label: '知识中心',
    icon: 'book',
    desc: '文章、估值数据、知识库、RAG检索',
    children: [
      { key: 'articles', label: '文章管理', icon: 'articles',
        desc: '公众号文章采集、图片解析、估值提取' },
      { key: 'valuation', label: '估值数据', icon: 'valuation', hot: true,
        desc: '指数PE/PB估值历史、百分位、AI市场分析' },
      { key: 'gallery', label: '估值图片', icon: 'gallery',
        desc: '估值图片归档、批量识别、日期检索' },
      { key: 'knowledge', label: '蒸馏知识', icon: 'book',
        desc: '书籍/文章蒸馏的知识卡片、分类检索' },
      { key: 'author', label: '作者文章', icon: 'author',
        desc: '公众号作者文章采集、管理、蒸馏' },
      { key: 'linked', label: '个人文档', icon: 'link',
        desc: '个人PDF/Markdown文档上传、向量化、检索' },
      { key: 'rag', label: 'RAG 分析', icon: 'rag',
        desc: 'RAG检索日志、命中率统计、查询重写测试' },
    ],
  },

  // ── 系统与进化 ──
  {
    key: 'group-system',
    label: '系统与进化',
    icon: 'config',
    desc: '系统管理、质量评估、进化闭环、实验功能',
    children: [
      { key: 'admin-agents', label: 'Agent 管理', icon: 'admin',
        desc: '专家Agent配置、Prompt版本管理、工具注册' },
      { key: 'token-usage', label: 'Token 用量', icon: 'token',
        desc: 'LLM调用token统计、预算监控、成本分析' },
      { key: 'system-config', label: '系统配置', icon: 'config',
        desc: '功能开关、阈值配置、API参数管理' },
      { key: 'data-health', label: '数据健康', icon: 'shield-check',
        desc: '数据完整性监控、缺失检测、质量评分' },
      { key: 'quality-dashboard', label: '质量仪表盘', icon: 'chart',
        desc: '对话质量趋势、低质量项、按Agent统计' },
      { key: 'bad-cases', label: 'Bad Case', icon: 'bug',
        desc: '低质量对话归因、根因分析、改进任务' },
      { key: 'eval-suite', label: '评测集', icon: 'check',
        desc: '评测用例管理、批量回归、Shadow对比' },
      { key: 'health', label: '健康分', icon: 'health',
        desc: '综合理财健康分、股债比例、恐贪指数' },
      { key: 'shadow', label: 'Shadow Mode', icon: 'shadow',
        desc: 'Prompt变更对比验证、AB测试' },
      { key: 'strategy-backtest', label: '策略回测', icon: 'line-chart',
        desc: '策略历史回测、有效前沿、风险平价' },
      { key: 'capability-center', label: '能力中心', icon: 'wrench',
        desc: 'MCP工具可视化、集成指南、能力统计' },
    ],
  },
]

export const flatNavItems = navItems.flatMap(item => item.children || item)
