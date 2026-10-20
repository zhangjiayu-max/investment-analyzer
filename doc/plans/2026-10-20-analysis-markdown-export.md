# 分析 Agent 产出 Markdown 复制与导出

目标：12 个分析 Agent 在所有结果展示入口均支持复制 Markdown 与下载 `.md` 文件，已有历史结果同样可用。

设计：复用共享剪贴板兼容逻辑；新增 Markdown 序列化工具和轻量操作组件。复制保留原始 Markdown，下载补充报告标题、真实记录时间和 ID。结构化对象/JSON 转为标题、列表和字段正文，保留全部字段，不从 HTML 反推、不截断结果。空结果、明确失败和运行占位不提供操作。按钮停止冒泡，避免触发卡片折叠。

覆盖清单：

| Agent | 入口 |
| --- | --- |
| 市场日报分析师 | BriefingCard |
| 分散度分析师 | PortfolioManagement 分散度解读、AnalysisCard |
| 全景诊断分析师 | AnalysisCard、PortfolioHealthCard 的 AgentResultCard |
| 基金深度分析师 | AnalysisCard |
| 交易复盘分析师 | AnalysisCard、交易页内联结果 |
| 情景推演分析师 | PortfolioManagement 情景推演结果 |
| 指定基金分析师 | AnalysisCard |
| 热点分析专家 | HotspotsCard |
| 债券配置顾问 | CashManagementCard |
| 指数深度分析师 | ValuationHistory 当前结果、历史列表每条记录及历史详情 |
| 市场情报分析师 | MarketIntelligence |
| 增强策略分析师 | ValuationHistory 深度解读和增强策略 |

共同入口：AnalysisLogPage 独立分析及对话协作详情、AnalysisComparison 两侧报告、BadCasePage 原始分析、RagAnalysis 检索分析详情、PortfolioHealthCard 再平衡建议、ChatMessage 对话及专家结果（已有操作统一下载工具）、EvalSuitePage 评测完整结果、CapabilityCenter 调试结果。调试接口新增 `full_result`，用于下载完整结果，避免使用 5000 字展示预览。RAG 详情导出当前检索片段，标题标记为检索结果。

实施与验证：

- [x] 先写并运行 Markdown 保真、对象/JSON 转换、合法文件名和下载测试，确认缺失功能导致失败。
- [x] 实现工具及 MarkdownActions，测试复制成功/拒绝/兼容回退、导出、无内容、运行状态、事件不冒泡。
- [x] 接入共享卡片及覆盖清单内所有入口，保留工作区既有改动。
- [x] 运行相关组件回归与前端测试，执行 Vite 构建（临时输出目录，避免覆盖 static）。
- [x] 检查 diff 和覆盖清单，报告实际验证结果。

验证记录：相关前端 59 项、后端调试与能力回归 12 项测试通过。最终前端全量 365 项通过、6 项 alertLifecycleGovernance 测试失败（工作区其他告警功能的待完成改动，非本次 Markdown 修改范围）。生产构建通过，产物验证于 /tmp/investment-analyzer-markdown-build，未覆盖 static。独立只读审查发现的调试截断及 Bad Case 入口遗漏已修复，并分别加入长正文回归测试。

2026-10-20 追补：用户反馈估值数据的 AI 分析历史列表缺少操作。此前仅覆盖报告正文和详情弹窗，现为每条已完成历史记录直接提供复制 MD、导出 MD，兼容无 status 的旧记录；使用完整 result，阻止点击冒泡，无需打开详情。新增列表长报告保真和运行中/失败/空结果隐藏按钮的回归测试，相关 5 个测试文件共 67 项通过。已执行默认 npm run build 更新本地服务实际读取的 static/，修正此前仅构建至临时目录导致页面未更新的问题。

浏览器验证：针对本地服务 /app 的实际构建产物，用拦截 API 的历史报告样本在 1440px、390px 两种宽度检查；列表复制与完整 Markdown 一致，导出 .md 包含完整报告，操作不打开详情弹窗，列表未超出视口。已人工查看手机宽度截图，确认按钮可见。
