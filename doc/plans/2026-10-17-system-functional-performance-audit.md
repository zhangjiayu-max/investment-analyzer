# 2026-10-17 系统功能、业务逻辑与耗时排查报告

本轮结论：系统已有并行 Agent、后台任务、缓存、SSE 回放、批量行情等基础，但存在账务计算偏差、前后端数据契约断裂、历史价格污染和失效的超时保护。优先级应是业务正确性 → 功能闭环 → 等待与成本 → 可观测性；现阶段直接扩大 Agent 数量或叠加缓存，会放大部分问题。

**范围与证据口径**

- 以当前工作区为准，包括原有未提交改动；没有修改业务代码，没有提交、部署、改写生产数据或发起付费 LLM 请求。本报告是本轮新增文件。
- 覆盖导航中的 37 页：普通用户 15 页、开发者模式额外 22 页；核对前端入口、相关 API、服务与数据库关键链路。开发者低频功能以源码抽查为主，不能等同于逐按钮端到端验收。
- 当前本地服务的 OpenAPI 内容含 592 个路径、651 个操作定义。只是接口盘点，并未执行全部操作。
- 证据标记：**运行**＝现有服务响应/日志/只读数据库；**隔离复现**＝提取真实函数并用内存数据库或 mock 输入执行；**代码确认**＝调用链或数据契约足以确认；**优化机会**＝还需要基准测量。隔离复现不代表已证明生产账目受到同样影响。
- P1：账务/结论可能错误、主要功能不可用或执行控制失效，应优先修复。P2：性能、恢复体验、质量与治理问题。部分 P1 明确标记为“潜伏”，当前开关或数据尚未触发。
- 不宣称发现已发生的真实资金损失，也不把历史最长耗时当成当前性能基准。

**一、逐项功能覆盖**

| 可见性 | 功能 | 本轮核对重点 | 判断与对应问题 |
|---|---|---|---|
| 普通 | 每日看板 | 聚合加载、KeepAlive、任务恢复、批次请求 | 重复加载、辅助任务阻塞核心展示，见 28、31、32 |
| 普通 | 市场热点 | 缓存、任务触发、终态恢复 | 已完成任务恢复后仍空白，见 28 |
| 普通 | 机会雷达 | 事件扫描、关注信号、任务恢复、回测 | 信号回测失效、扫描凭据丢失、采集可合并，见 10、14、34 |
| 普通 | 债市分析 | API、图表与加载生命周期 | 未确认本页特有阻断；债市缓存表为空，需区分实时源和本地落库，见 40 |
| 普通 | AI 对话 | 快速路径、Pipeline、专家、取消、SSE、记忆 | 超时无效、指定专家/恢复参数丢失、取消状态反转等，见 15～20、25、27、29、35 |
| 普通 | 持仓管理 | 账户、买卖确认、成本、转换、现金、行情 | 成本与转换口径、事务、请求竞态，见 01～03、05、11、16、29、31、32 |
| 普通 | 智能补仓 | 估值取数、预算、定投条件、单标的调用 | 在线估值重复等待，4%策略存在潜伏错误，见 13、14 |
| 普通 | 风险与提示 | 预警加载、激活刷新、批处理 | KeepAlive 重复请求；预警输入受估值和持仓正确性影响，见 01、14、31 |
| 普通 | 决策档案 | 创建、预检查、草稿、执行关联 | DB 导出被另一账本覆盖，见 06；执行依赖 03 |
| 普通 | 决策中心 | 账本/准确率协议、运行/回测/止盈 | 结果重复解包且未接异步合同，见 07、12 |
| 普通 | 财务总览 | 净资产、盈亏、现金流、支出联动 | 市值/盈亏字段漂移；规划辅助接口断链，见 08、09 |
| 普通 | 资金桶 | CRUD、调拨、持仓分配 | 同桶转账增余额、非原子更新、附加路由前缀不一致，见 04 |
| 普通 | 全账户诊断 | 后台计算、画像、历史、重算、缓存 | 已有后台任务与短缓存；受账务、画像和行情输入影响，需加数据版本验收，见 01、11、16、40 |
| 普通 | 文章管理 | 采集、图片分析、解析、轮询、索引 | 文档解析同步阻塞风险；文章相关删除索引不一致，见 16、23 |
| 普通 | 估值数据 | 指数切换、历史加载、任务恢复 | 切换竞态可能展示错指数；全局最新日期不能替代逐指数新鲜度，见 29、40 |
| 开发者 | 收益归因 | 页面入口、并行加载、金融数据依赖 | 源码抽查；输入依赖成本与历史窗口，见 01、11；未进行历史样本逐笔归因验收 |
| 开发者 | 行为诊断 | 入口、加载、错误反馈 | 源码抽查；未确认本页特有阻断，需基于真实交易序列作金标准验收 |
| 开发者 | 决策准确率 | 入口、分模块数据、回测口径 | 不应把错误收益窗口用于准确率及权重反馈，见 10、12 |
| 开发者 | 配置偏离 | 配置计算、备用金约束、一键决策 | 已有备用金约束；创建决策受 DB 导出冲突影响，见 06 |
| 开发者 | 策略沙盒 | 参数、回测、历史加载 | 源码抽查；共享历史净值缓存会污染窗口，见 11；本轮未跑所有策略回测 |
| 开发者 | 估值图片 | 检索、识别、轮询 | 源码抽查；沿用通用轮询需防重入，见 30 |
| 开发者 | 蒸馏知识 | 加载、过滤、孤儿清理 | FTS 清理可能误删其他类型有效索引，见 22 |
| 开发者 | 作者文章 | 加载、轮询、删除与检索 | 删除后索引残留，见 23 |
| 开发者 | 个人文档 | 上传、解析、索引、删除、同步 | 删除残留、同步路径错误及阻塞，见 16、23、24 |
| 开发者 | Agent 管理 | 配置、Prompt 版本、工具、回归任务 | 已有版本/回归入口；评估可信度与权限边界需修，见 26、41 |
| 开发者 | 分析记录 | 筛选、详情、评分、trace | 评估消息归属/并行指标错误，trace 并发串号，见 26、38 |
| 开发者 | 功能分析 | 导航入口、使用统计加载 | 源码抽查；未核验埋点遗漏率或跨设备统计准确率 |
| 开发者 | Token 用量 | 时间窗口、分 Tab 加载、停轮询 | 已有停轮询；重试总预算、空库启动需修，见 20、37 |
| 开发者 | 系统配置 | 配置读取/更新、重置、可见性 | 前端开发者模式不提供服务端权限控制，见 41 |
| 开发者 | 数据健康 | 聚合数据、响应解包、日期口径 | 页面解包错误；监控口径不足，见 36、40 |
| 开发者 | 质量仪表盘 | 并行加载、评分来源 | 底层评估存在错评、指标恒零，见 26 |
| 开发者 | Bad Case | 加载、根因与统计 | 源码抽查；应先修评分归属，避免基于错分生成优化建议，见 26 |
| 开发者 | 评测集 | 用例、恢复、并行加载、清理 | 现有测试漏掉主要合同错误；全链路回归待补，见 07、26、37 |
| 开发者 | 健康分 | 入口、allSettled 部分容错 | 已有部分失败容错；需验证缺数据时分数语义，见 40 |
| 开发者 | Shadow Mode | 配置、原生 fetch、结果读取 | 源码抽查；原生 fetch 应纳入统一协议与访问控制验收，见 27、41；未发付费对比 |
| 开发者 | 策略回测 | 入口、参数、历史序列 | 共享净值缓存窗口有风险，见 11；未证明所有独立回测器均有 12 的问题 |
| 开发者 | 能力中心 | 工具加载、并行展示、调试入口 | 已有可视化工具入口；需把可调用与数据健康、权限分开，见 40、41 |

另检查了导航外的家庭规划、Obsidian 同步、后台调度、统一响应中间件和数据层。导航 key/组件映射目前已补齐，不应重复报告“移动端缺少全部功能页面”。

**二、账务与金融数据正确性**

**01 · P1 · 持仓重算把后续买入成本分摊到历史卖出。隔离复现。**

[portfolio.py:861](/Users/xiaoyuer/projects/investment-analyzer/backend/db/portfolio.py:861) 先累计全部买入，再于 885 行处理全部卖出。买 100 份@1 → 全部卖出 → 买 100 份@2，最终 100 份成本被算成 150，正确应为 200。会污染持仓盈亏及依赖它的补仓/风控。

建议按交易日期与确定顺序逐笔重放，明确分红成本口径。验收覆盖买卖交错、全部清仓后再买、补录旧交易、重复重算不漂移。

**02 · P1 · 基金转换直接复制源基金份额、净值。代码确认。**

[portfolio.py:1238](/Users/xiaoyuer/projects/investment-analyzer/backend/db/portfolio.py:1238) 将源基金 `actual_shares/confirmed_price` 写作目标买入。忽略费用时，源基金 100 份@2 转入净值 1 的目标应为 200 份，当前写 100 份。新建目标持仓同样使用源净值；自动确认还需核对目标代码传递。

建议分别记录源净值、目标净值、转换费用和目标确认份额，双方同事务入账。验收净值不同、费用非零、目标持仓已有/没有、确认中断与重试。

**03 · P1 · 交易确认、持仓重算和现金入账非原子，状态守卫不足以保证并发幂等。代码确认。**

[portfolio.py:1118](/Users/xiaoyuer/projects/investment-analyzer/backend/db/portfolio.py:1118) 先提交 confirmed，随后重算持仓，1201 行另连接加现金。中途异常可留下“已确认、现金未入账”，而 1068 行又拒绝重复确认。UPDATE 没有 `status='pending'` 条件，先 SELECT 再 UPDATE 也不是并发状态锁。

建议交易、持仓、现金流水同事务，条件更新状态，并以交易 ID 唯一约束现金流水。验收在每个阶段注入失败、并发双确认、重试补偿，现金只能记一次。

**04 · P1 · 同桶调拨凭空增加余额，跨桶调拨缺校验和事务。隔离复现。**

[bucket_engine.py:113](/Users/xiaoyuer/projects/investment-analyzer/backend/services/finance/bucket_engine.py:113) 未拒绝同桶或负金额，125、126 行分别更新。余额 100 的同一个桶调拨 10 后变为 110。路由接受任意 float；[bucket_routes.py:12](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/portfolio/bucket_routes.py:12) 的 `/buckets` 附加操作路径也未与前端 `/api/buckets` 对齐。基本桶 CRUD 与附加联动路由需分别验收。

建议正金额、不同桶、余额校验与原子扣增，增加调拨流水/幂等键。验收资产守恒、负值/零/同桶拒绝、竞争扣款不超支。

**05 · P1（潜伏）· 基金专属申购费可能放大 100 倍。隔离复现。**

[fee_calculator.py:84](/Users/xiaoyuer/projects/investment-analyzer/backend/services/fee_calculator.py:84) 约定 0.15 表示 0.15%，但只在数值大于 1 时除 100。输入金额 1000、专属费率 0.15，算出费用 150，而按该模块乘法口径应为 1.5。当前生产 `subscription_fee` 全 NULL，尚无生产触发证据；不能把这一例说成所有当前交易费都算错。

建议固定费率单位，不按大小猜单位；另明确申购总额与外扣法。验收 0%、0.15%、1%、1.5%、默认费率回退。

**06 · P1 · 两套决策模块重名导出，决策档案调用到错误函数。测试失败与代码确认。**

[db/__init__.py:258](/Users/xiaoyuer/projects/investment-analyzer/backend/db/__init__.py:258) 导出旧 `decisions` 的 create/get/list/update，318 行又被 `investment_decisions` 同名函数覆盖。[decisions.py:116](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/decision/decisions.py:116) 仍传 `source_type/target_type/summary`，新函数不支持，必然 TypeError。抽测中相关 5 个用例均在此失败，不能简单当成过期测试忽略。

建议领域内显式导入或使用清晰别名，检查四个同名导出的全部调用方。验收两套账本分别创建、列表筛选、详情及状态迁移，互不串表。

**07 · P1 · 决策中心重复读取 data，且三个操作未接后台任务。隔离复现与代码确认。**

[api/index.js:3073](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/api/index.js:3073) 已返回 `r.data`；[DecisionCenter.vue:50](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/components/decision/DecisionCenter.vue:50) 再读 `res.data`。实际列表 `[{id:42}]` 在页面变成空数组。运行决策/回测/止盈后端已返回 task_id，但页面仍按同步 verdict、processed、scanned 显示结果：可能错误提示失败或立即提示处理 0 条。

建议统一 API 返回层级，三个动作接入任务状态，终态刷新数据。验收真实协议 mock 下列表、准确率、详情、running→done/error 均能展示正确结果。

**08 · P1 · 财务总览持仓市值、浮盈亏、收益率读错字段，降级成 0。代码确认。**

[finance_dashboard.py:45](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/dashboard/finance_dashboard.py:45) 取 `total_market_value/total_float_pnl/total_return_pct`，而 [portfolio.py:475](/Users/xiaoyuer/projects/investment-analyzer/backend/db/portfolio.py:475) 返回 `total_value/total_profit/profit_rate`。`.get(...,0)` 掩盖了契约漂移。

建议统一汇总 DTO 及收益率单位；缺关键字段应报可诊断错误而非伪装零资产。验收与持仓页同一快照的总额、现金、市值、盈亏逐字段一致。

**09 · P1 · 家庭规划存在路由、类型和资金流口径三层不一致。部分隔离复现。**

[finance_routes.py:11](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/dashboard/finance_routes.py:11) 注册 `/finance/...`，前端请求 `/api/finance/...`，当前服务 OpenAPI 也证实前者。即使对齐路径，[finance_planner.py:28](/Users/xiaoyuer/projects/investment-analyzer/backend/services/finance/finance_planner.py:28) 将现金 dict 除 6，62/156 行把持仓 list 当 dict 调 `.items()`，隔离得到 TypeError/AttributeError。预测还将“月投入”加进现金，未按真实收入、支出及资产间转移构建现金流。

建议一并修路径、数据类型和月度收支模型，使用自然月份。验收现金转投资不凭空增加总资产、有债务/月收入/支出时的现金余额守恒。

**10 · P1 · 关注信号回测三处错误串联，当前已有到期积压。运行与代码确认。**

[watchlist_backtest.py:159](/Users/xiaoyuer/projects/investment-analyzer/backend/services/advisor/watchlist_backtest.py:159) 给 `update_signal_backtest(bt_id, **fields)` 传第二个位置 dict，必 TypeError。同文件 101 行查 `trade_date`，实际列为 `nav_date`；失败后 115 行无日期约束取实时价，可能把未来净值用于历史复盘。只读统计共 9 条未完成，其中 8 条已到期；今天日志对应 8 次签名错误。

建议同时修签名、日期列和价格时间边界；历史数据缺失保持 pending/缺数据，不以未来价格补齐。验收历史复盘完全不调用实时价，并能完成到期记录入库。

**11 · P1 · 净值内存缓存忽略查询时间窗口，历史分析可能看到未来数据。隔离复现。**

[fund_data_service.py:440](/Users/xiaoyuer/projects/investment-analyzer/backend/services/fund/fund_data_service.py:440) 缓存键只有基金代码，命中后不重新筛 start/end；筛选子集又被写回同一键。请求 end_date=2026-08-01，可返回 2026-10-16 记录；短窗口也可能污染后续长窗口。

建议缓存完整序列并每次切片，或把完整窗口纳入缓存键。验收先最新后历史、先短后长、先历史后最新、刷新前后，缓存命中与冷读严格同结果。

**12 · P1 · 统一决策回测标的与基准区间不一致，历史 as_of_date 没有限制终价。代码确认。**

[decision_backtest.py:145](/Users/xiaoyuer/projects/investment-analyzer/backend/services/advisor/decision_backtest.py:145) 给不同入场日期决策统一取最近至少 30 天基准，终价取持仓当前价。as_of_date 仅用于筛到期记录，不约束价格。186 行还把结果用于主题权重反哺，错误不止影响报表。

建议每笔使用同起止日期的标的/基准历史价格，区分建议回测与实际成交收益，并保存价格日期。验收回放历史日期不使用之后数据；不同持有期分别计算基准；缺价格时不更新学习权重。

**13 · P1（潜伏）· 4%定投的买点与建仓前提错误。隔离复现。**

[dip_4pct_strategy.py:235](/Users/xiaoyuer/projects/investment-analyzer/backend/services/dip_4pct_strategy.py:235) 取 `type` 而非 `transaction_type`，265 行查不存在的 `holdings` 表而非 `portfolio_holdings`；估值为空不拦截。实际买价 1、现价 1.05、估值 None，仍返回“首次建仓”、triggered=True、累计投入 0。当前开关为 false，属于启用前必须修复项。

建议统一字段/表名，未知估值返回不可判断，以已执行记录、基金与账户维度取买点。验收上涨时不触发下跌补仓、缺估值不推断低估、触发幂等。

**三、耗时、执行控制与 LLM 成本**

**14 · P2 · 智能补仓串行在线估值；单标的 sizing 重算全组合；失败源反复尝试。代码与运行日志确认。**

[smart_add_planner.py:1290](/Users/xiaoyuer/projects/investment-analyzer/backend/services/advisor/smart_add_planner.py:1290) 逐仓串行生成；2010/2012 行 PE 失败后 PB 继续 enable_online=True；[decision_pipeline.py:166](/Users/xiaoyuer/projects/investment-analyzer/backend/services/advisor/decision_pipeline.py:166) 单标的仍调用全组合。失败估值缺短暂失败缓存。

另 [portfolio.py:2419](/Users/xiaoyuer/projects/investment-analyzer/backend/db/portfolio.py:2419) ttfund 不可用会清 skip 缓存、重试已经持续失败的 akshare，之后还可能再试 ttfund。今天两类重仓接口各出现 256 次失败，行业配置 96 次失败，说明失败源仍持续消耗调用。事件雷达三来源可受限并发，同一扫描相同指数的价格请求可合并。

建议同指数批量预取、一次分析共享快照、失败短缓存、数据源半开探测及总预算；单标的只算本标的或复用组合结果。验收记录每次分析外部调用次数、同 key 合并率、失败源调用上限，再测耗时，不能先承诺倍数。

**15 · P1 · 专家超时包装没有真正限制等待，Pipeline 超时位置无效。隔离复现。**

[orchestrator.py:3115](/Users/xiaoyuer/projects/investment-analyzer/backend/agent/core/orchestrator.py:3115) 使用 `with ThreadPoolExecutor`，超时后退出上下文仍等待线程。设置 10ms 超时、200ms worker，实际约 222ms 才返回。3169 行重试同类；[pipeline.py:1629](/Users/xiaoyuer/projects/investment-analyzer/backend/agent/core/pipeline.py:1629) 无 timeout 的 as_completed 先等完成，再对已完成 future 使用 result(timeout=300)，无法限制执行时长。

建议统一 deadline 与取消轮询，等待方超时和底层 HTTP 请求截止均落实。注意 shutdown(wait=False) 也不会终止运行中的线程：现有 akshare 包装虽然能及时返回，但每次建新线程池，持续挂起时仍缺总在途上限。验收超时返回时间、取消响应时间、残留线程/在途请求上限。

**16 · P1/P2 · 多个“异步后台”函数实际同步阻塞事件循环。隔离复现与代码确认。**

[app.py:309](/Users/xiaoyuer/projects/investment-analyzer/backend/app.py:309) 的指数回填 async 函数没有 await，循环直接网络拉取；[app.py:409](/Users/xiaoyuer/projects/investment-analyzer/backend/app.py:409) 后台初始化直接加载 Chroma/Embedding/记忆维护。create_task 不会让同步工作自动进入线程。净值刷新路由 [portfolio.py:1080](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/portfolio/portfolio.py:1080)、20点定时刷新、18点诊断也有同类调用；文档解析/encode 亦需审查。

隔离执行原指数回填函数、替换为每次 60ms 的三个假 fetch，10ms 的同循环心跳延后到约 190ms。此为机制验证，不是生产延迟统计。

建议阻塞 I/O 进入受限线程池，子进程用异步 API，长任务与 HTTP 服务隔离并管理并发。验收后台运行同时持续探测只读 API，记录 event-loop lag 与 P95。

**17 · P2 · 完整对话在外层和 Pipeline 重复准备上下文。代码确认。**

[conversations.py:1390](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/conversation/conversations.py:1390) 已做澄清/RAG，1450 行统一上下文，1770 行组合事实；[orchestrator.py:5965](/Users/xiaoyuer/projects/investment-analyzer/backend/agent/core/orchestrator.py:5965) 进入 Pipeline 未传这些结果，Pipeline 816/942 行重新理解/检索，子查询还可串行多次检索。外层事实约束也可能未被复用。

建议一次 prepared-context 与数据版本贯穿，区分首次准备和增量补查。已有规则澄清和缓存，不能声称所有请求必有两次澄清 LLM。验收同一问题 RAG 次数、外部取数次数及送模上下文一致性。

**18 · P1 · 默认 Pipeline 分发未传指定专家与恢复状态。代码确认。**

[orchestrator.py:5965](/Users/xiaoyuer/projects/investment-analyzer/backend/agent/core/orchestrator.py:5965) 未传 target_specialists、resume_from、resume_from_checkpoint；[pipeline.py:166](/Users/xiaoyuer/projects/investment-analyzer/backend/agent/core/pipeline.py:166) 有 resumed_state，但上游未映射。恢复逻辑排在 Pipeline 分发之后。可能重新跑已完成专家，或忽略指定专家。

建议先识别恢复请求，映射检查点；指定专家作为路由约束，快速路径也检查。验收中断恢复不重跑已完成步骤，指定专家不会被静默替换。

**19 · P1 · 普通聊天/单专家停止后仍可完成并覆盖 cancelled。代码确认。**

[conversations.py:1530](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/conversation/conversations.py:1530) 普通 LLM、1634 行单专家等待缺取消检查，1557/1689 行完成后无条件持久化 completed。取消端点虽写 cancelled，后续结果仍可反转终态。

建议统一执行状态机，结果下发及持久化前核对取消，条件更新终态，并传递取消预算。验收停止后前端不再追加结果、状态不反转、不会触发后续建议生成。

**20 · P2 · SDK、Tenacity、专家重试叠加，缺总请求预算。优化机会。**

[llm_service.py:49](/Users/xiaoyuer/projects/investment-analyzer/backend/services/llm/llm_service.py:49) OpenAI 客户端有 180s timeout，138 行起外层至多 4 次重试，专家层还有重试。没有统一总 deadline 时，局部超时不能代表用户总等待上限。配置明确统一 MIMO 且禁用 fallback，应尊重此约束，不能通过增加模型供应商掩盖重试问题。

建议只保留一个重试责任层，限定可重试错误，传入剩余预算、记录 attempt/reason/tokens。验收 429、连接失败、流中断、不可重试400时的总尝试次数和总耗时。未发真实 LLM 请求，不提供虚构最坏秒数或费用收益。

**四、RAG、知识、记忆与评估**

**21 · P1 · Chroma 候选排序方向反了。隔离复现。**

[rag.py:1714](/Users/xiaoyuer/projects/investment-analyzer/backend/services/rag/rag.py:1714) 存 rank=-distance；2398/2412 行按升序选 top-N/RRF。distance=0.7 的较远项排在 distance=0.1 的较近项前。候选提前截断后，reranker 无法保证找回已丢失的相关内容。

建议统一距离/相似度语义，检查每个消费方。验收精确匹配、近义匹配和无关文本的相对名次，另用固定集比较召回率。

**22 · P1 · 孤儿 FTS 清理跨类型误删。内存 SQLite 复现。**

[knowledge.py:231](/Users/xiaoyuer/projects/investment-analyzer/backend/db/knowledge.py:231) 用 knowledge_base.id 判定整个 knowledge_fts_content 的 reference_id，没有限定 content_type。有效作者文章、估值 ID 无法与知识卡片 ID 直接对照；隔离示例里两条其他业务有效索引被删除。

建议类型→源表明确映射，先 dry-run 输出候选，再事务清理。验收合法文章/估值/分析保留，仅确实缺源的对应类型被删除。

**23 · P2 · 作者文章/个人文档删除不完整，仍可能被 RAG 引用。代码确认。**

[articles.py:292](/Users/xiaoyuer/projects/investment-analyzer/backend/db/articles.py:292) 作者文章删除只删主表；[articles.py:492](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/knowledge/articles.py:492) 个人文档删除未统一清分块/FTS/Chroma。向量检索直接返回保存正文，源表不存在也未必过滤。普通 knowledge_base 删除已有 FTS/Chroma 清理，不应泛化为所有删除都有此问题。

建议删除流程覆盖源表、chunks、FTS、向量，失败可重试/补偿。验收删后关键词及向量均无法命中，部分失败能对账修复。

**24 · P2 · Obsidian 同步脚本路径错误，状态接口还可能假成功。路径验证。**

[knowledge.py:65](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/knowledge/knowledge.py:65) 拼为 backend/routers/scripts/sync_obsidian.py，真实文件在 backend/scripts；状态路径未核对 returncode 就可能 ok=True。async 路由直接 subprocess.run，修好路径后还会同步等待。

建议以明确 backend 根定位，检查退出码，使用受控后台/异步子进程。验收不存在路径报明确错误、成功有真实处理数量、同步中 API 不被阻塞。

**25 · P2 · 摘要游标混用消息数量与主键。代码确认。**

[memory.py:584](/Users/xiaoyuer/projects/investment-analyzer/backend/agent/memory/memory.py:584) 等位置存 len(messages)，[conversation_context.py:464](/Users/xiaoyuer/projects/investment-analyzer/backend/services/conversation/conversation_context.py:464) 却按 messages.id > up_to_message_id 取增量，另一方向还用主键与数量比较。可造成已摘要消息重复进 prompt，或错误复用旧摘要。

建议统一存“最后覆盖的真实消息 ID”，补迁移/失效逻辑。验收多会话交错产生非连续 ID 后，摘要覆盖区间与近期消息严格不重不漏。

**26 · P2 · 质量评估会错评消息，并行度公式恒零。部分隔离复现。**

[conversation_evaluator.py:76](/Users/xiaoyuer/projects/investment-analyzer/backend/agent/eval/conversation_evaluator.py:76) 取最后 assistant 而非传入 message_id，并用全对话 runs 评本轮；216 行 1-total/(avg*n) 代数恒等于 0，隔离 2s+3s 专家得到 parallelism=0。后台排队后新回复出现时，旧消息会挂新答案的评分。

建议按 message_id/trace_id 对齐，按真实起止区间衡量并行，核对 LLM 评分开关是否真正使用。验收指定旧消息、新旧并发评估、不同专家起止区间，评分与样本归属可追踪。

**五、前端加载、恢复、移动端闭环**

**27 · P1 · 全局任务追踪绕开统一解包，刷新恢复和完成通知受损。代码确认。**

[useTaskTracker.js:77](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/composables/useTaskTracker.js:77) 原生 fetch 直接判断 response.json() 顶层 status/tasks；后端实际在 data 内，127 行恢复可能把存在的任务当空删除。建议使用统一 API，验证结构后再删除本地项。验收刷新保留运行任务、完成通知一次、临时断网不清凭据。

**28 · P1 · 通用恢复新增终态语义，市场热点消费者未适配。隔离复现。**

[useAsyncTask.js:79](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/composables/useAsyncTask.js:79) 恢复 done/error 后 return true，但不回调；[MarketIntelligence.vue:335](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/components/market/MarketIntelligence.vue:335) 只在回调水合 data、idle 时加载。隔离：taskResult 有新闻、state=done，页面 data 仍 null。此项与当前未提交的通用恢复逻辑有关。

建议返回结构化恢复结果，并逐个消费者适配终态水合/缓存读取。验收 running/done/error/无任务四种刷新状态，看板热点同查。

**29 · P1 · 对话、指数、账户快速切换存在旧响应覆盖新选择。隔离复现及代码确认。**

[ChatView.vue:573](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/components/ChatView.vue:573) 加载后无条件覆盖 messages；[ValuationHistory.vue:661](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/components/valuation/ValuationHistory.vue:661)、[PortfolioManagement.vue:1525](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/components/portfolio/PortfolioManagement.vue:1525) 有同类归属问题。对话函数隔离按 B→A 顺序返回，得到 selected=B、displayed=A。

建议请求捕获 key/递增版本，返回时核对当前选择，取消旧只读请求。验收延迟乱序时标题、标的、账户与数据显示严格一致。

**30 · P1 · 通用轮询可重叠，reset 后旧响应复活任务并重复完成回调。隔离复现。**

[useAsyncTask.js:156](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/composables/useAsyncTask.js:156) async setInterval 没有在途锁/generation；reset 只停定时器，不使旧请求失效。隔离三个未返回 tick 发三个请求，reset 后释放旧 done，状态恢复为 done、完成回调执行 3 次，可连带重复触发后续扫描。

建议一次完成后再调度下次，加入 generation、task ID 快照及 AbortSignal，网络暂错区别于任务失败。验收慢请求不重叠，reset/换任务后旧响应无效，完成回调至多一次。

**31 · P2 · KeepAlive 初次进入 mounted+activated 重复加载。代码确认。**

[Home.vue:278](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/views/Home.vue:278) 使用 KeepAlive；[Dashboard.vue:253](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/components/Dashboard.vue:253) 与327行、持仓2637/2821、预警393/397两套生命周期均加载。持仓加载还包含自动确认交易，重复加载不全是无副作用读请求。

建议初次初始化与重新激活区分，统一刷新入口、在途去重、TTL/脏标记。验收初挂每个必要接口一次；重新激活只刷新失效数据。

**32 · P2 · 看板/持仓的辅助恢复形成串行瀑布，核心卡片开始加载太晚。代码确认。**

[Dashboard.vue:255](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/components/Dashboard.vue:255) 先串行恢复四类任务，无 running 时每类还查最近终态，再自动验证，之后才拉核心数据。[PortfolioManagement.vue:1521](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/components/portfolio/PortfolioManagement.vue:1521) 先自动确认、再依次余额/辅助数据，1572 行重复请求总览。

建议核心只读请求立即发，独立恢复/余额受限并行，辅助数据不阻塞首屏，总览复用。验收记录“核心请求发出时间/核心可见时间/完全稳定时间”，不能只看总接口完成时间。

**33 · P2 · 移动端有页面映射，但对话去执行、通知定位链路未接通。代码确认。**

[MobileApp.vue:130](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/components/MobileApp.vue:130) pageProps 未给 chat 传 onNavigate；ChatView 的执行跳转依赖该回调。[App.vue:43](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/App.vue:43) 通知仅改父 activePage，移动端另有内部状态，且 convId 未用于定位。

建议共用导航状态/动作处理，携带目标会话与待执行上下文。验收手机点“去执行”和任务通知，落到正确页面、账户与会话。

**34 · P2 · 机会雷达轮询把仍 running 当失败，丢失恢复凭据。代码确认。**

[EventRadarPage.vue:554](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/components/market/EventRadarPage.vue:554) 每轮写 finalStatus，却562行仅用 !finalStatus 判断超时，持续 running 达期限后走失败并删 task ID；恢复路径遇临时网络错误也删凭据，且没有同步 scanning 状态。

建议等待超时不等于业务失败，保留任务 ID 与后台继续提示，统一轮询实现。验收持续 running 十分钟、网络短断、刷新后恢复，不重复发起扫描。

**35 · P2 · SSE 可见页面中途断流被静默吞掉，缺自动状态收敛。代码确认。**

[api/index.js:443](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/api/index.js:443) 提前 EOF、467行非取消异常未向消费者下发断线状态；恢复主要靠 ChatView 再激活/visibility/pageshow。页面一直可见时可能停在执行中。

建议 disconnect 事件触发带退避的 execution-state/replay 核对，绝不把重连实现为自动重发昂贵分析。验收流异常、done前EOF、网络恢复均最终得到明确终态或可操作提示。

**36 · P2 · 数据健康页读取错 Axios 层级。代码确认。**

[DataHealthDashboard.vue:63](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/components/quality/DataHealthDashboard.vue:63) 得到 Axios response，64行却取 res.health，应取 res.data.health。建议加入真实协议组件测试；验收当前服务返回的10类健康项均可见。该问题与某源真的无数据不同。

**六、初始化、监控与服务边界**

**37 · P1 · 全新空库在导入阶段就失败，测试 fixture 来不及初始化。隔离运行确认。**

[cost_tracker.py:187](/Users/xiaoyuer/projects/investment-analyzer/backend/infra/cost_tracker.py:187) 导入时创建 BudgetController，144行立即读 system_config；调用链 db→token_usage→cost_tracker 在 init_db 前触发。使用全新 DB_PATH 运行抽测，11 个模块收集时报 no such table: system_config。这也是新环境/空库启动风险，不仅是测试问题。

建议避免模块导入阶段读业务表，初始化后懒加载预算/显式生命周期注入。验收全新空目录启动、已存在库升级、临时库测试均不依赖历史数据库。

**38 · P2 · request_id 使用 threading.local，在 asyncio 并发下串请求。隔离复现。**

[request_tracing.py:11](/Users/xiaoyuer/projects/investment-analyzer/backend/infra/request_tracing.py:11) 在线程级存上下文。同循环两请求 A/B 各设置后 await，实际返回 [(A,B),(B,B)]。响应头闭包仍可正确，内部日志/计时却已混淆，影响定位慢请求。

建议 ContextVar，finally reset token，并在进入线程工作时显式传播上下文。验收大量交错请求日志与响应 request_id 一致，后台任务 trace 归属明确。

**39 · P2 · 全局响应包装破坏 OpenAPI，且 SSE 异常兜底检查位置不对。运行与代码确认。**

[middleware.py:70](/Users/xiaoyuer/projects/investment-analyzer/backend/api/middleware.py:70) 包装所有 JSON。实际 GET /openapi.json 顶层仅 code/message/data，缺标准 openapi/paths，会破坏 Swagger/客户端工具的标准消费。今天 /api/notifications/stream 另有 No response returned 异常，发生在71行 await call_next；73行 response is None 兜底不能捕获这个抛错。日志证实异常存在，断线/竞态具体触发还需定向复现。

建议业务响应包装仅作用 /api 业务 JSON，明确排除 OpenAPI；评估改原生 ASGI 中间件、正确处理连接取消，保留正常异常可见性。验收 schema 标准解析、SSE 长连/主动断连/重连无伪500。

**40 · P2 · 健康探针、数据新鲜度和路径说明不能充分反映功能可用性。运行与代码确认。**

[app.py:2033](/Users/xiaoyuer/projects/investment-analyzer/backend/app.py:2033) 固定返回 ok；[data_health.py:16](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/admin/data_health.py:16) 多处只取 MAX 日期，无法反映关键指数/基金部分缺失。已有 list_index_freshness，可复用而不是另建一套标准。知识文档旧不等于无效，应区分长期知识与时效行情。

只读确认主库实际默认为项目根 data/valuations.db（约147.8MB、144表），backend/data/valuations.db 为0字节空库；项目说明存在混淆，不能把空库当线上数据缺失。主库 quick_check=ok。债市表0条也不代表实时债市接口必然不可用。

建议区分 liveness/readiness、依赖初始化/降级状态、关键标的覆盖率、行情日期、来源与最后成功时间；统一路径文档，定时任务有唯一执行/失败可见性。验收某一依赖失败时状态准确且核心已有能力仍可用。

**41 · P1（网络访问边界）· 管理/业务 API 缺统一服务端认证，图片代理未限制目标。代码确认。**

[app.py:69](/Users/xiaoyuer/projects/investment-analyzer/backend/app.py:69) CORS=*，默认启动监听0.0.0.0；[config.py:18](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/admin/config.py:18) 等管理接口没有认证依赖，当前 schema 651操作均未声明安全方案，源码也未见统一认证中间件。前端开发者模式仅隐藏导航。可访问服务的调用者可能读取配置/修改 Prompt 或业务数据；实际可达范围取决于部署网络，本轮未做公网探测。

[app.py:2064](/Users/xiaoyuer/projects/investment-analyzer/backend/app.py:2064) 图片代理允许任意 url 且跟随跳转，可访问不应由远程调用者指定的内部目标。未探测内部地址。

建议本地个人模式明确绑定loopback；共享部署提供认证与管理权限、配置敏感值脱敏。代理限定所需图片域名/协议并校验重定向后的地址。验收未认证不能读写管理/持仓，允许的微信图片正常使用、内部目标不可代理。

**42 · P2 · 前端体积仍有优化空间，但首屏并非加载整个图表库。隔离构建测量。**

核对临时构建文件：入口JS 453.00kB / gzip135.02kB，首屏CSS287.94/44.74kB；Vue82.29/31.73kB、Markdown65.71/21.36kB。异步图表 chunk1119.32/362.82kB，未在初始 HTML preload。此处gzip为Python gzip.compress默认压缩级别9的复测值，单位为十进制kB。已有 defineAsyncComponent 和图表动态import，不应误报全量ECharts进入首屏。

建议按 echarts/core 注册实际图表、清理公共CSS、复核ChatView/Dashboard静态依赖。验收使用相同设备/网络测LCP、首次图表出现及缓存后二次进入；不能按文件大小直接推断提速秒数。

**七、实际观测、验证结果与局限**

| 项目 | 本轮结果 | 能说明什么/不能说明什么 |
|---|---|---|
| 本地健康API | HTTP200；一次curl约2.5ms，另一次Python探测约29ms | 仅证明当时本地轻量请求可响应，不是P95或重任务并发基准 |
| 数据健康API | 一次本地请求约17.3ms | 说明监控聚合接口可读；页面仍有36的解包错误 |
| 数据库检查 | 只读quick_check=ok，144表 | 无结构完整性损坏证据；不代表账务/金融口径正确 |
| 异步任务历史 | done372、error59、running1 | 历史状态快照；不得把全部error当当前回归或算生产失败率 |
| 今日自选回测 | 8次update_signal_backtest签名错误；8条到期未完成 | 10有当前运行证据，不是只有理论风险 |
| 今日行情失败 | 两类重仓接口各256次失败、行业配置96次 | 说明失败数据源反复调用；未逐条量化外部等待时长 |
| 今日分散度AI样本 | 单次分析阶段86.642s；prompt22368、completion2835、total25203tokens | 当前可用的单样本；不能代表所有AI对话，也不能把全部token等同计费输出 |
| 10月14日起分散度任务 | 2次，任务状态窗口均值约82s，最大91s；平均输入22347tokens | created/updated窗口含调度和状态更新，不是纯模型推理时间 |
| 10月14日起事件提取调用 | extractor8次、retry6次、nodesdup2次 | 有重试成本；不能把这些不同轮次简单算成某次请求的固定重试数 |
| 前端现有测试 | 15文件、121测试通过 | 没覆盖多个真实API合同与浏览器竞态；不是37页E2E通过 |
| 前端构建 | 临时outDir构建成功，约1.66s；未覆盖static | 证明可编译；体积见42，不等于运行交互正确 |
| 后端抽测初次 | 全新DB_PATH，11个模块收集错误 | 发现37的导入期读表问题，测试尚未执行 |
| 后端抽测绕过初始化后 | 13文件、89用例：82通过、7失败，约14.40s | 5个失败是06；另2个是schema测试持有旧DB_PATH引用，不等于生产缺列 |
| Python语法解析 | 排除虚拟环境，417个项目Python文件AST解析，0语法错误 | 仅语法检查，不是运行导入/类型/业务验证 |
| 请求ID、超时、排序、成本等 | 使用真实函数隔离验证，关键例子见对应项 | 证明函数机制；mock延迟不冒充线上压测数字 |

后端抽测文件：test_db_portfolio、test_db_expense、test_auto_confirm、test_sell_preview、test_db_watchlist、test_db_decisions_phase1、test_db_decisions_phase3、test_transaction_filter、test_portfolio_snapshot、test_async_tasks_stale、test_stream_channels、test_cancel_persist、test_schema_migration（均在 backend/tests）。

测试方式：临时 DB_PATH 隔离、关闭进程环境中的模型Key。首次暴露空库问题后，仅在临时库预建 system_config 以继续执行；该绕过不是项目修复，也不能掩盖首次失败。schema测试模块级 `from db._conn import DB_PATH` 保留旧路径，fixture后来patch实际连接路径，两者不一致导致2项失败，应修测试隔离。测试解释器为本机Python3.13；现有服务日志路径显示其运行环境是Python3.11，环境差异已保留。

前端命令：`npm test -- --reporter=dot`；`npm run build -- --outDir /tmp/investment-analyzer-frontend-audit-20261017 --emptyOutDir`。测试有mock/lifecycle/localStorage警告，未据此声称浏览器E2E通过。

未执行全量收费LLM回归、真实交易写入、所有策略历史回放、37页浏览器逐按钮验收或并发压测。因此没有P50/P95全系统基线，没有预测提速百分比，也没有宣称每个页面的全部业务都已查完。

**八、已有优化应保留**

- 持仓净值刷新已有盈米批量拉取和8线程兜底，问题是路由/调度调用位置及失败策略，不是全部串行。
- Agent执行/交叉审阅、外层澄清/RAG已有并行；工具广播、截断、检索次数限制也已存在。
- SSE已有独立producer、事件持久化、replay、心跳与发送互斥；主要缺口在部分取消、恢复参数和前台断流收敛。
- 通用任务恢复已有服务端对账及期限；应修消费者语义与在途竞态。
- 机会扫描已有running任务复用、后台线程；事件雷达已有多源容错、标题去重、时效衰减。
- 智能补仓已有组合共享预算、相关性降权；机会引擎部分回测已有成本、同期基准与风险指标，不能把统一决策回测的问题泛化为所有回测器。
- 桌面/移动共用导航过滤；低频页、图表已异步加载；机会雷达体检/评分已有按需请求；决策档案预检查已有每批5并发。
- 数据库已有WAL、配置60s缓存、基金内存/SQLite缓存、SQLite backup一致性备份。需要修语义和执行边界，而不是默认替换技术栈。

**九、建议整改顺序与验收门槛**

| 批次 | 范围 | 交付/验收要求 |
|---|---|---|
| A：账务与资产守恒 | 01～05、08；13启用前修复 | 逐笔交易重放、转换份额、现金/桶余额守恒、并发幂等与故障恢复；历史影响先只读对账，禁止盲目全库重算 |
| B：功能断链与数据口径 | 06、07、09～12、21、22、24、28、36、37 | 主要入口走通，历史请求不见未来数据，清理不误删，空库启动；补前后端真实合同测试 |
| C：执行控制与性能 | 14～20、27、29～35 | deadline贯穿、取消终态不反转、旧响应不覆盖、轮询不重入、核心请求先发、失败取数有上限 |
| D：长期可信度与治理 | 23、25、26、38～42 | 索引对账、摘要游标、正确评估归属、全链路trace、readiness、访问控制及前端资源基准 |

每批先用稳定样本建立基线，再改代码；一次修复对应一种根因与回归，不把所有问题合成一笔大重构。尤其回测/评估口径修好前，不应把当前分数或命中率直接当自动学习的可信训练信号。

建议补一套固定验收集：交错买卖/转换/费率/资金桶、两套决策账本合同、历史日期价格边界、近远向量排序、多类型索引清理、取消/恢复/断流、快速切换乱序、慢状态轮询、空库初始化。它比继续堆叠只验证函数能返回的单元测试更能覆盖当前风险。

性能采样分为页面核心可见、首个有效回答、最终答案可见、后处理/发送锁释放四个时点；同时记录RAG、行情、模型、工具、数据库、重试分段耗时、输入输出tokens、缓存来源/年龄、调用次数与事件循环延迟。取相同问题/数据快照、冷暖缓存及外部故障三组，至少得到足够重复样本后再报告P50/P95和优化收益。
