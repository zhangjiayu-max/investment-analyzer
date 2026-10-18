# 系统治理全功能审查

审查日期：2026-10-18。范围以 `frontend/src/navigation.js` 的“系统治理”分组为准，共 13 个页面，包含主要子功能、前端 API、后端路由、数据库和实际执行链。

结论：建议优先修复执行闭环和统计可信度，再做功能扩展。存在按钮无法执行、任务未完成就宣布成功、配置被覆盖、实验未接入真实执行、评分与回测口径错误等问题。页面可打开、构建通过，并不能证明治理功能有效。

本次交付为审查报告，未修改业务实现。浏览了本地运行应用的 13 个页面；没有手动提交配置、运行真实 LLM 评测、执行工具调试、删除记录或扫码登录。浏览本身会触发应用现有埋点、行情查询；健康分 GET `/today` 在当天没有记录时还会自动计算并保存，这是现有读取接口的副作用。隔离复现均使用临时/内存数据库和替代的 LLM/行情返回。

优先级：P1 表示应优先修复的功能失效、数据破坏或结果误导；P2 表示随后修复的准确性、可靠性与体验问题。升级建议不等同于已发生的生产事故。

## 逐项结论

| 功能 | 最高优先级 | 主要结论 |
|---|---|---|
| Agent 管理 | P1 | Prompt 重启被覆盖；版本串用可跨 Agent 回滚；部分版本与回归接口不存在 |
| 分析记录 | P2 | 评分失败仍标已评估；Agent 筛选写死 ID |
| 功能分析 | P2 | 会话身份丢失、访问次数口径错误、功能点击未接入 |
| Token 用量 | P1 | 小时查询 SQL 注入；Trace 契约失配；费用与时间范围失真 |
| 系统配置 | P1 | 保存/重置抹掉分类描述；存在不生效的配置 |
| 数据健康 | P1 | 表级最新时间掩盖标的缺失；静态知识误判过期 |
| 质量仪表盘 | P1 | 评分量纲混用；成功状态统计错误；部分时间筛选无效 |
| Bad Case | P2 | 重复转评测用例；同步批量分析阻塞；闭环状态不足 |
| 评测集 | P1 | 运行按钮报错；异步提交被当完成；A/B 不使用候选 Prompt |
| 健康分 | P1 | 重算未等待任务；估值字段不匹配；缺数据仍输出确定建议 |
| Shadow Mode | P1 | 无生产调用入口；LLM 返回类型不匹配；流量设置未生效 |
| 策略回测 | P1 | 定投注资被当收益；基准与策略曲线量纲不同 |
| 能力中心 | P2 | 次数与最近记录字段失配；扩展能力重复统计；写工具调试无确认 |

## 1. Agent 管理

检查了对话/分析 Agent 列表、编辑、启停、提示词生成与优化、版本、回滚、回归记录、运行缓存及启动初始化。

- **P1：自定义专家 Prompt 重启丢失。** 初始化对已有专家无条件更新 `system_prompt` 等字段，而启动会执行初始化。临时库修改 `fund_analyst` 后再次初始化，修改被代码默认值覆盖。证据：[初始化覆盖](/Users/xiaoyuer/projects/investment-analyzer/backend/db/agents.py:972)、[启动初始化](/Users/xiaoyuer/projects/investment-analyzer/backend/app.py:332)。建议默认配置只补缺失，升级使用显式迁移，并保留用户版本。
- **P1：切换 Agent 后历史版本串用，后端还允许跨 Agent 回滚。** 前端没有随选择清空版本缓存；后端只检查版本存在，没有核对版本所属 Agent 和类型。Node 与临时 FastAPI 测试复现：A 的历史版本可以返回 HTTP 200 并写入 B。证据：[选择状态](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/components/agent/AdminAgentsPage.vue:55)、[回滚](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/admin/agents.py:205)。建议前端按类型和 ID 隔离状态，后端强制验证归属。
- **P1：分析 Agent 版本/回滚、所有 Agent 回归查询路径错误。** 前端请求 `/agent/...`，实际路由为 `/analysis-agents/...` 与 `/agents/...`，隔离请求确认 404。证据：[前端接口](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/api/index.js:354)、[后端接口](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/admin/agents.py:216)。建议统一接口并区分加载失败与无记录。
- **P2：回滚未清理专家缓存。** 普通编辑会清缓存，回滚直接写库；预热缓存后回滚可出现数据库新 Prompt、运行时旧 Prompt。证据：[回滚更新](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/admin/agents.py:212)。建议所有发布入口复用统一服务。
- **P2：重要操作确认组件不统一。** 存在手写确认弹窗，部分保存/启停直接提交。建议遵循项目 `ConfirmDialog` 规范，确认中展示目标 Agent 和变更摘要。

升级方向：统一“草稿 → 固定集验证 → 发布 → 生效版本 → 回滚”，展示数据库版本和实际运行版本，避免多套 Prompt 系统分离。

## 2. 分析记录

检查了筛选、分页、详情、质量评分、记录状态、后台评估和反馈回写。

- **P2：Judge 失败仍记为已评估。** 评分异常降级为 0，后台仍写回并设置 `has_eval=1`，页面评分按钮随之消失。隔离复现确认失败评分被回写为 0。证据：[失败降级](/Users/xiaoyuer/projects/investment-analyzer/backend/agent/eval/eval_scorer.py:545)、[无条件回写](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/analysis/analysis_log.py:167)、[已评估标记](/Users/xiaoyuer/projects/investment-analyzer/backend/db/agent_analysis_log.py:198)。建议独立保存评估任务状态、错误和重试次数，成功后才写分数，同一记录去重执行。
- **P2：Agent 筛选 ID 写死。** 列表与新库初始化插入顺序不一致，迁移/删除后也不能保证 ID 稳定。证据：[硬编码筛选](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/components/analysis/AnalysisLogPage.vue:281)、[初始化顺序](/Users/xiaoyuer/projects/investment-analyzer/backend/db/analysis.py:53)。建议从真实 Agent 列表生成筛选项。

升级方向：一条记录关联源任务、工具证据、模型/Prompt 版本、成本、质量结果；评分失败和业务分析失败分开显示，并支持定位原始输出。

## 3. 功能分析

检查了页面进入/离开、点击/完成事件、批量上报、会话身份、排行与趋势 SQL。

- **P2：常规 sendBeacon 上报丢失会话身份。** 前端只在 fetch 回退时发送 `X-Session-Id`；Beacon 载荷不含身份，后端统一记为 `anonymous`。隔离捕获载荷确认本地 ID 存在但未发送。证据：[上报](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/composables/useTracking.js:73)、[后端取身份](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/admin/feature_usage.py:62)。另外永久 localStorage 标识不等同于一次访问会话，应明确 UV 与 session 定义。
- **P2：访问量和点击量把其他事件也计入。** 页面排行 `COUNT(*)` 未筛 `page_enter`；功能排行未筛 `feature_click`。临时库输入一次进入、离开、点击、完成，得到访问 4 次、点击 2 次。证据：[排行 SQL](/Users/xiaoyuer/projects/investment-analyzer/backend/db/feature_usage.py:96)。建议按事件类型分别聚合。
- **P2：点击/完成埋点尚未接到业务按钮。** 全前端搜索只有 composable 定义/导出，实际调用只有 Home 的进入/离开，因此“最常用功能”不能反映业务操作。证据：[事件接口](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/composables/useTracking.js:150)。移动端也未接入同样的页面事件。
- **P2：后台停留和关闭时长不可靠。** 隐藏/关闭只 flush，没有补当前页 leave；后台时间又会累计到下次页面离开。fetch 对 HTTP 失败也未检查响应状态。证据：[生命周期](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/composables/useTracking.js:107)。建议按可见活动时间计时，补 session 结束事件，明确失败重试策略。

升级方向：先得到可信的访问、有效停留、开始/完成/失败数据，再增加价值评分。频繁使用本身不足以代表投资价值。

## 4. Token 用量

检查了总览、预算、费用、模型/调用方分布、小时趋势、明细分页、性能、Trace、记账和清空确认。

- **P1：小时统计 SQL 注入。** `date` 原样拼接进 SQL；临时库确认构造日期可绕过日期和 embedding 过滤。证据：[SQL 拼接](/Users/xiaoyuer/projects/investment-analyzer/backend/db/token_usage.py:279)、[路由透传](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/admin/token_usage.py:140)。建议日期强类型校验并使用参数占位符，补负向验证。
- **P1：Trace 数据层级失配，新库还可能直接 500。** 后端返回 `items: 聚合对象`，前端期待顶层 `token_usage/tool_audit_logs`；另无条件查询尚未创建的 `rag_logs`。隔离复现有数据却显示 0 条、新初始化库缺表报错。证据：[响应](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/admin/token_usage.py:154)、[前端读取](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/components/quality/TokenUsagePage.vue:236)、[缺表查询](/Users/xiaoyuer/projects/investment-analyzer/backend/db/token_usage.py:330)。建议统一响应模型并在初始化建齐审计表。
- **P2：今日/本周/月估算口径错误。** 今日费用依赖当前明细页，初始为空就显示 0，历史记录也可能算今日；任意所选区间标成 7 天费用，再乘 4.3 估月。Node 复现 2020 年记录被计入今日、30 天 30 元被推算月费 129 元。证据：[费用计算](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/components/quality/TokenUsagePage.vue:314)。建议服务端分别返回今日、近 7 天、本月、所选区间汇总。
- **P2：时间筛选与费用统计不一致。** 性能固定查 7 天，明细首次进入也可能保留 7 天；调用方分组未排除 `embedding_index`，但总览会排除；本地记账与 UTC 截止时间混用。证据：[性能查询](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/components/quality/TokenUsagePage.vue:198)、[调用方 SQL](/Users/xiaoyuer/projects/investment-analyzer/backend/db/token_usage.py:129)。建议统一窗口、时区和成本分类，性能/总览分开存状态。

升级方向：按业务任务归集模型、工具和重试成本；展示缓存节省、失败浪费、预算降级原因。模型单价与供应商真实账单本次未核验。

## 5. 系统配置

检查了分类、搜索、单项编辑、重置、缓存失效、类型校验、配置消费和内嵌依赖状态组件。

- **P1：保存与重置破坏配置元数据。** `INSERT OR REPLACE` 只写 key/value/time，description 和 category 被表默认值覆盖。临时库复现单项保存后描述清空、分类变 general；重置后 462 项全部变 general 且描述为空。证据：[保存](/Users/xiaoyuer/projects/investment-analyzer/backend/db/config.py:793)、[重置](/Users/xiaoyuer/projects/investment-analyzer/backend/db/config.py:809)。建议 upsert 只更新值；重置时恢复完整默认元数据并使用单事务。
- **P2：部分开关/参数没有真实消费。** `llm.timeout_default` 未接入调用入口，实际超时固定 180 秒；`strategy_backtest.enabled` 也未接入回测执行。证据：[超时配置定义](/Users/xiaoyuer/projects/investment-analyzer/backend/db/config.py:65)、[实际超时](/Users/xiaoyuer/projects/investment-analyzer/backend/services/llm/llm_service.py:15)。建议逐项标注即时/重启生效、实际消费位置，未生效项撤下或接通。
- **P2：配置仅校验字符串。** 缺少数值范围、JSON/枚举/时间格式、相关阈值关系校验；保存失败主要输出控制台。证据：[请求模型](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/admin/config.py:11)、[错误处理](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/components/quality/SystemConfigPage.vue:79)。建议用配置注册表驱动输入控件和服务端验证，重要变更展示差异和确认。

已有可保留实现：分析依赖组件有安装/登录/过期/失败状态、刷新和扫码确认，相关 11 项现有测试通过。它目前位于系统配置页，建议数据健康提供可导航的依赖入口。

## 6. 数据健康

检查了估值、持仓、文章、知识库、对话、决策、作者文章、荐股、债市、自选等 10 类来源，以及后端指数覆盖率。

- **P1：单条新数据掩盖其他标的缺失/过期。** 表级 `MAX(date)` 不能证明每只持仓/指数都可用。内存库中三条持仓分别为今天、NULL、2020 年，卡片仍显示今天和正常。证据：[估值 MAX](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/admin/data_health.py:18)、[持仓 MAX](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/admin/data_health.py:30)。后端已有指数覆盖率，前端未展示。建议以“应覆盖标的集合”为分母，展示缺失名单及覆盖率。
- **P2：静态资料套用市场数据阈值。** 知识库、对话、决策均按 3/7 天判断红黄绿；后端的 `reference_content` 提示未被使用。证据：[前端阈值](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/components/quality/DataHealthDashboard.vue:72)。知识长期未新增不等于失效，应按来源定义规则。
- **P2：空数据、未配置、解析错误都算过期。** 后端统一转成 999 天。证据：[缺失处理](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/admin/data_health.py:137)。建议独立显示未配置、无记录、抓取失败、已过期、部分可用。

升级方向：显示数据日期与采集时间、交易日 SLA、覆盖率和受影响功能；提供针对缺失项的修复入口及修复后验证。

## 7. 质量仪表盘

检查了评测概览、趋势、Agent 对比、运行监控、对话评估、进化统计、建议接受/拒绝和低分列表。

- **P1：10 分制与 25 分制混写。** 每日/A-B 五项总分直接写通用 `eval_runs.score`，页面按 /10，优秀阈值却为 7。真实页面已显示 `chat 22.0`、`quality_evaluator 20.0`。证据：[25 分写入](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/analysis/eval_system.py:191)、[优秀阈值](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/admin/eval.py:584)。建议保存原始分、满分、归一化分、评分标准版本，并迁移历史数据。
- **P1：成功任务可能统计为失败。** 性能只认可 `success`，真实运行还有 `completed`；内存 SQL 复现一条 completed 被算为 0% 成功。运行中任务也进入失败分母。证据：[统计](/Users/xiaoyuer/projects/investment-analyzer/backend/db/token_usage.py:167)、[实际状态](/Users/xiaoyuer/projects/investment-analyzer/backend/db/agents.py:1253)。建议统一状态枚举，成功率仅按明确的终态集合计算。
- **P2：对话质量时间筛选不生效。** UI 的 7/30/90 天未透传；DB 概览查所有时间，趋势固定 7 天。证据：[页面](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/components/quality/QualityDashboard.vue:502)、[DB](/Users/xiaoyuer/projects/investment-analyzer/backend/db/eval.py:813)。建议全链路透传同一窗口。
- **P2：概览、Agent、趋势、低分列表来源/阈值不同。** 待评、跳过和失败的 0 分有时参与均值、有时排除。隔离 SQL 中 0、NULL、8 三条记录得到 Agent 均分 4、概览均分 8。证据：[概览](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/admin/eval.py:581)、[Agent 对比](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/admin/eval.py:632)、[趋势/低分来源](/Users/xiaoyuer/projects/investment-analyzer/backend/db/dashboard.py:396)。建议所有卡片可下钻到同一个过滤集合。
- **P2：接受评估建议不幂等。** 不检查 pending，先创建用例再更新 accepted；重复请求可重复创建，较老建议还可能因只查最近 50 条而查不到。证据：[接受流程](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/admin/eval.py:871)。建议按 ID 查询、原子状态转换及来源唯一键，并补重要操作确认。

升级方向：区分模型输出质量、数据可靠性、执行可靠性和用户反馈；显示有效样本量、评分覆盖率及可比较的版本趋势。

## 8. Bad Case

检查了分析/对话两个来源、筛选、详情、根因分析与分布、单条转用例、批量转化和确认机制。

- **P2：批量转用例去重失效。** 把完整 description 放进集合，却用短 marker 做元素全等比较。隔离重复执行两次均 converted=1、skipped=0。证据：[查重](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/admin/eval.py:363)、[描述写入](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/admin/eval.py:416)。建议结构化 source_type/source_id 唯一约束，而不是解析说明文本。
- **P2：根因分析阻塞事件循环。** async 路由直接运行同步串行 LLM，批量处理时同进程其他请求会受影响；前端超时也没有恢复凭据。证据：[路由](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/analysis/portfolio_ai.py:318)、[批处理](/Users/xiaoyuer/projects/investment-analyzer/backend/infra/root_cause_analyzer.py:261)。建议持久后台任务、线程池、逐项状态、限流与重试。
- **P2：批量重要操作缺少标准确认。** 单条转用例已有确认，批量根因分析/批量转化应展示范围并使用 ConfirmDialog。证据：[批量分析](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/components/quality/BadCasePage.vue:216)。

升级方向：分页查询未处理案例，避免只看最近 200 条；增加负责人/改进任务/修复版本/回归结果/关闭理由，衡量修复率和复发率。

## 9. 评测集

检查了用例 CRUD、单条/批量执行、任务恢复、自动回归、每日评测、Prompt 版本/激活/A-B、统计和历史详情。

- **P1：单条与批量运行按钮直接报错。** `start` 已别名为 `startEvalTask`，执行仍调用未定义的 `start`。Node 运行实际 script：真实提交函数被调用 0 次，错误 `start is not defined`；批量仍可能弹“完成”。证据：[别名](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/components/quality/EvalSuitePage.vue:20)、[调用](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/components/quality/EvalSuitePage.vue:336)。不能只修变量名：任务类型 `eval_case` 与后端 `eval_case_run` 不一致，单任务 composable 也不能管理并发批次。
- **P1：部分分析把 running 凭据当最终输出。** 全景、基金深度、交易复盘接口已异步化，评测分支却直接序列化返回并评分；仅部分其他类型等待任务终态。隔离复现最终结果 ok=True，result_data 只有 task_id/status=running。证据：[评测分支](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/admin/eval.py:179)、[全景返回](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/analysis/panorama.py:51)。建议统一目标执行器，等待最终源记录再评分。
- **P1：A/B 只换版本标签，没有换 Prompt。** 输出生成器使用硬编码 prompt，从不加载候选 `prompt_content`；version 在输出生成后才用于标记。隔离 A/B 捕获到完全相同的 LLM messages；case_type 筛选也未使用。证据：[生成器](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/analysis/eval_system.py:124)、[版本标签](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/analysis/eval_system.py:176)、[比较](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/analysis/eval_system.py:501)。现有比较结果不能证明 Prompt 优劣，建议注入真实版本并固定用例、数据和模型参数。
- **P1：每日评测立即误报完成。** 后端只返回 task_id，页面却读取 report 并提示 0 条、均分 0。Node 已复现。证据：[前端](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/components/quality/EvalSuitePage.vue:198)、[异步接口](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/analysis/eval_system.py:413)。建议任务轮询/恢复，终态后读取报告。
- **P1：评分量纲混用。** 与质量看板共用同一污染来源，修复时需一起迁移历史分数，不能只改显示文案。
- **P2：自动回归未走真实目标 Agent。** 只接受 question，合法分析参数被跳过写 0；其余统一通用 LLM+RAG，不能验证目标编排和工具。证据：[回归](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/admin/eval.py:1076)、[实际执行器](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/admin/eval.py:1152)。建议按目标类型执行，并分别报告 passed/failed/skipped/error。
- **P2：激活版本不验证 ID/类型。** 先关闭类型 A，再按任意 ID 激活；内存复现不存在 ID 导致 A 无活跃版本、跨类型导致 B 两个活跃版本。证据：[激活 SQL](/Users/xiaoyuer/projects/investment-analyzer/backend/db/eval.py:1383)。建议事务内校验归属和唯一活跃约束，发布前用 ConfirmDialog 确认。
- **P2：历史详情引用当前用例。** 修改用例后旧 run 详情会展示新输入和标准。证据：[详情 JOIN](/Users/xiaoyuer/projects/investment-analyzer/backend/db/eval.py:561)。建议固化用例修订、输入、标准、Prompt/model/data 版本与完整输出快照。

升级方向：评测分成可离线验证的确定性契约检查和需要 LLM 的质量评估；先建立真实执行闭环，再增加定时回归、版本发布门槛和配对 A/B。

## 10. 健康分

检查了五维总分、重算、建议、历史、FED 股债性价比、恐贪指数、缓存、异步任务和数据库回读。

- **P1：重算未接异步任务。** 后端返回 task_id，前端直接当评分并提示成功。证据：[后台任务](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/analysis/health_score.py:1116)、[前端](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/components/analysis/HealthScore.vue:97)。建议使用任务终态，失败显示原因，完成后再刷新。
- **P1：已存与即时结果字段不同。** 回读是 `score_quality/advice_json`，即时是 `scores.quality/advice`；前端仅支持后者，已有记录时可出现总分有值、维度为 0、建议消失。证据：[DB](/Users/xiaoyuer/projects/investment-analyzer/backend/db/health_score.py:123)、[回读](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/analysis/health_score.py:1146)、[前端维度](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/components/analysis/HealthScore.vue:49)。建议统一响应 DTO。
- **P1：估值字段读错。** 引擎读 pe_percentile/pb_percentile，DB 返回 metric_type+percentile。真实结构 percentile=95 的隔离样本得到“无有效百分位”、估值 100 分，风险 overvalued_pct=0。证据：[估值计算](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/analysis/health_score.py:303)、[DB 返回](/Users/xiaoyuer/projects/investment-analyzer/backend/db/valuations.py:185)。建议按指数代码和指标明确匹配，保留真实 0 分位。
- **P1：缺数据仍输出确定建议。** 空持仓/交易可得到 540 分及“整体投资行为健康”；全部市场数据接口失败仍输出恐贪 50、“市场情绪正常”。证据：[健康建议](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/analysis/health_score.py:586)、[恐贪默认值](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/analysis/health_score.py:852)。建议显示未知/覆盖不足及缺失原因，停止从默认分生成确定建议。
- **P1：FED 可把 PB 当 PE。** 未限定 metric_type，任意最新 current_value 被当 PE；债息缺失还用假设 2.5%。隔离 PB=1.5 时得“PE=1.5、利差64.17%、极度看好股票”。证据：[取值](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/analysis/health_score.py:704)。建议明确 PE 类型、数据时间和债息来源，缺失返回不可计算。
- **P2：重算可能读 5 分钟旧缓存；异常降级结果仍被标 done。** 证据：[缓存/降级](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/analysis/health_score.py:675)。建议 force 刷新、依赖数据版本和 explicit degraded 状态。
- **P2：权益比例按基金只数而非金额。** 99 万股票+1 万债基被当 50% 权益。证据：[配置比例](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/analysis/health_score.py:267)。建议金额加权并识别混合基金实际权益敞口。

升级方向：评估旧健康分与“全账户诊断”2.0 的边界，统一计算服务；每维度展示得分依据、覆盖率和来源日期，避免两套健康体系结论冲突。

## 11. Shadow Mode

检查了配置创建、六类 Agent、流量比例、候选 Prompt、启停删除、记录与筛选、评分/胜率和自动晋升。

- **P1：未接入生产请求。** 全后端检索 run_shadow 仅有定义，没有调用点；traffic_pct 只存储，不用于抽样。证据：[唯一执行定义](/Users/xiaoyuer/projects/investment-analyzer/backend/infra/shadow_mode.py:173)。因此配置启用不能证明实验实际运行。
- **P1：即使接入，返回类型也不匹配。** `_call_llm` 返回 ChatCompletion，却直接当字符串切片和入库；隔离合法 completion 复现 not subscriptable，返回 None 且不留失败记录。证据：[执行](/Users/xiaoyuer/projects/investment-analyzer/backend/infra/shadow_mode.py:202)、[字符串处理](/Users/xiaoyuer/projects/investment-analyzer/backend/infra/shadow_mode.py:266)。建议提取 message.content 并独立记录执行失败。
- **P2：async 内同步 LLM 阻塞；HTTP 错误也可能提示启停/删除成功。** 前端原生 fetch 忽略 resp.ok；读取失败只写控制台。证据：[前端](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/components/agent/ShadowModePage.vue:83)。建议使用统一 API、后台线程执行和明确状态，删除/重要操作统一 ConfirmDialog。
- **P2：人工创建路径无法自动晋升。** 没有传 prompt_version_id，而晋升跳过无版本 ID 的配置。证据：[创建](/Users/xiaoyuer/projects/investment-analyzer/backend/app.py:2183)、[晋升](/Users/xiaoyuer/projects/investment-analyzer/backend/infra/shadow_mode.py:366)。应明确人工实验与自动进化的发布能力。
- **P2：无效评分与胜率定义不统一。** Judge 失败填 5/5 计入统计，后端与 UI 判胜阈值分别 >0、>1、>0.5。证据：[降级](/Users/xiaoyuer/projects/investment-analyzer/backend/infra/shadow_mode.py:308)、[胜率](/Users/xiaoyuer/projects/investment-analyzer/backend/infra/shadow_mode.py:146)、[UI](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/components/agent/ShadowModePage.vue:257)。建议排除无效样本，统一阈值并显示有效样本量。

升级方向：先只接通一个真实 Agent，具备抽样、并发和成本上限、输入快照、版本、执行/评分状态、人工发布与回滚，再扩展到所有类型。

## 12. 策略回测

检查了定投 fixed/ma/valuation、网格、二八、核心卫星、参数、费用、指标、曲线、基准、历史、保存、参数扫描和开关。

- **P1：定投注资被当收益。** 新资金注入后把绝对资产曲线直接用于风险指标。隔离横盘零手续费应无收益，却得到夏普 2.4032、年化波动 20.88%；价格跌 10% 被后续注资掩盖，最大回撤为 0。证据：[注资与计算](/Users/xiaoyuer/projects/investment-analyzer/backend/services/strategy/strategy_library.py:477)。建议用剔除外部现金流的单位净值计算 TWR/夏普/波动/回撤，投资者收益另报 XIRR。
- **P1：策略金额与归一化基准直接同轴。** 策略是几万金额，基准首值为 1，前端直接混入同一范围，且用下标而非日期对齐。证据：[基准](/Users/xiaoyuer/projects/investment-analyzer/backend/services/strategy/strategy_library.py:1075)、[图表](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/components/decision/StrategyBacktest.vue:93)。建议统一单位净值并按日期对齐。
- **P2：定投间隔“天”实际为数据行数。** 周/月快照与日净值产生不同实际间隔。证据：[触发](/Users/xiaoyuer/projects/investment-analyzer/backend/services/strategy/strategy_library.py:396)。建议明确交易日/日历日并统一数据频率。
- **P2：后端参数缺乏约束。** grid_pct=0 可除零，比例可超出范围，日期和金额未完整验证，开关也未消费。证据：[路由参数](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/analysis/strategy_backtest.py:30)。建议 Pydantic 类型、范围、关系校验及参数扫描数量上限。
- **P2：基准映射重复 key；核心卫星参数未执行。** 161022 映射被后项覆盖；core_code/satellites 公开参数未真正组成多标的回测。证据：[映射](/Users/xiaoyuer/projects/investment-analyzer/backend/services/strategy/strategy_library.py:1093)、[核心卫星执行](/Users/xiaoyuer/projects/investment-analyzer/backend/services/strategy/strategy_library.py:811)。建议校验唯一映射，实现多序列或移除不生效参数。
- **P2：保存失败仍返回成功，完成后不刷新历史。** 证据：[保存异常](/Users/xiaoyuer/projects/investment-analyzer/backend/services/strategy/strategy_library.py:1199)、[前端完成](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/components/decision/StrategyBacktest.vue:168)。建议分开报告计算与保存状态，历史记录带完整参数快照。

升级方向：明确真实/假设价格、债基回退、手续费、成交延迟、数据覆盖；修正口径后再做策略优选和样本外检验。导航描述中的有效前沿/风险平价也应与实际入口对应。

## 13. 能力中心

检查了总览、来源/分类/状态/关键词过滤、参数 schema、未暴露能力、接入指引、调用统计、最近调用、调试台与调试历史。

- **P2：调用次数与最近记录字段不匹配。** 后端按工具返回 calls，UI 读 total_calls；最近记录返回 items，UI 读 records 后回退整个对象。隔离 Node 复现 records 不是数组；真实页面有调用、次数列为空，展开仍“暂无记录”。证据：[次数](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/components/agent/CapabilityCenter.vue:800)、[最近记录](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/components/agent/CapabilityCenter.vue:224)、[后端 calls](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/admin/capabilities.py:442)、[后端 items](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/admin/capabilities.py:533)。错误分布还以缺失的 total_calls 为分母，应同步修正。
- **P2：已接入能力还计为未暴露。** 静态清单保留 fund_manager/fund_nav/fund_condition/fund_holding、finance_data/macro_data，实际已有对应前缀工具，页面同列“已暴露”和“未暴露”。证据：[静态清单](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/admin/capabilities.py:73)、[已注册工具](/Users/xiaoyuer/projects/investment-analyzer/backend/tools/__init__.py:758)。建议统一稳定能力 ID，从注册表映射去重，区分代码接入与实际可用状态。
- **P2：调试写工具缺少确认与启用检查。** 调试选择所有 exposed 工具，接口只校验暴露，不验证启用状态或副作用；可执行写预警/决策的工具。隔离用替代执行器验证无确认直接分发写工具，未执行真实写入。证据：[调试](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/components/agent/CapabilityCenter.vue:350)、[后端放行](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/admin/capabilities.py:573)、[写预警实现](/Users/xiaoyuer/projects/investment-analyzer/backend/tools/__init__.py:3288)。建议工具声明副作用，默认只读调试；写操作展示影响并使用 ConfirmDialog，后端校验 enabled。
- **P2：工具超时是事后检测。** execute_tool 在同步调用结束后才检查时长，不能把调试台“30 秒保护”理解为真正取消执行。证据：[执行与超时](/Users/xiaoyuer/projects/investment-analyzer/backend/tools/__init__.py:1293)。建议明确超时/取消语义，耗时工具通过持久任务执行。

升级方向：展示实际可用性、最近探测、依赖凭据、Agent 使用关系、调用量和失败率；提供受控的只读样例调试及统一审计链路。

## 建议实施顺序与验收

### 第一批：防止配置损坏与错误结论

1. 修 SQL 参数绑定、配置元数据保存、专家 Prompt 初始化覆盖及跨 Agent 回滚。
2. 接通评测、每日评测、健康分异步任务；批量任务按终态汇总。
3. 统一评分量纲并迁移历史；修健康分估值/FED/缺数据处理和定投现金流算法。
4. 实现真实 Prompt A/B 与 Shadow 最小闭环；未接通时页面明确显示未接入。

验收：保存/重启/回滚不损坏用户配置；无效版本不能改变当前版本；任务未终结不显示完成；候选 Prompt 改动确实改变执行输入；失败评估不生成有效分数；横盘定投风险指标不因注资产生虚假收益。

### 第二批：让统计与记录可追溯

修正 Trace/能力中心契约、成功状态、时间窗口、成本分类、埋点会话与事件口径、覆盖率、幂等来源、历史输入快照；为每个失败态提供重试或定位入口。

验收：概览能下钻到同一集合；翻明细页不改变今日费用；已知一次访问只计一次；缺失标的不被另一条新记录掩盖；重复转用例/接受建议只产生一条记录。

### 第三批：统一治理工作台

建议保留现有页面入口并逐步统一数据模型，按“运行与成本、数据与依赖、质量改进、配置发布、策略实验”组织，贯通问题来源 → 根因 → 改进 → 回归 → 发布 → 观察 → 回滚。

此项是后续产品设计方向，尚未实施；先修可信度，再决定页面合并和导航调整。

## 验证记录与边界

- 前端现有测试：`npm test -- --reporter=dot`，23 个文件，**175 passed**。
- 后端相关现有测试：`python3 -m pytest backend/tests/test_agent_data_health.py backend/tests/test_agent_core_health_regressions.py backend/tests/test_dependency_admin_async.py backend/tests/test_backtest_history_regressions.py backend/tests/test_llm_cost_controls.py backend/tests/test_analysis_route_health.py -q`，**133 passed**。
- 前端构建：`npm run build -- --outDir /tmp/investment-governance-audit-build-20261018`，**成功**；输出在临时目录，未覆盖 static。存在大图表包提示。
- 核心缺陷另外通过 Node VM 执行实际 Vue script、Python 抽取真实函数并替代外部依赖、临时 SQLite/FastAPI 请求复现。执行任务/版本/评分/成本/回测复现不连接生产数据库，不消耗真实 LLM。
- 13 个页面已在本地浏览器打开核对；能力中心统计空列/错误空状态、质量看板超出 10 分的历史结果已由实际页面确认。未穷举所有写操作的真实端到端行为。
- 未核验供应商账单、真实 LLM 输出质量、外部行情供应稳定性、扫码登录和部署环境多进程行为。未运行整个后端全量测试套件。
- 现有测试通过与本报告缺陷并不矛盾：现有覆盖侧重部分服务回归，治理页面的关键 API 契约、评分标准、发布有效性和统计语义仍有明显空缺。
