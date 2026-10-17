# 分析 Agent 功能检查报告

检查日期：2026-10-17（北京时间）。检查对象：当前工作区源码、实际运行的 8000 端口服务、`data/valuations.db` 的只读快照，以及外部服务最小探测。

## 结论与验证边界

**不能判定全部正常。** 12 个独立分析 Agent 均有配置和功能入口，但发现实际数据源不可用、失败被标记为成功、配置未参与执行、任务串用和公共质量控制失效等问题。

对话专家配置有 12 行，但只有 11 个唯一标识：择时分析师与市场分析师共用 `market_analyst`，加载时互相覆盖。其配置的 33 种工具都有定义和分发处理器；这只能证明代码接线存在，不能证明外部数据查询成功。

本次未修改业务代码、未运行真实数据库初始化或数据迁移、未重启服务。功能失败路径使用临时数据库、内存 SQLite、原函数和 Mock 复现；外部只进行了最小连通性/查询验证，没有对全部 Agent 逐一运行完整投资分析。因此，下表中的历史成功不是当前完整功能验收通过。

## 当前依赖实测

| 依赖 | 本次结果 | 能证明什么 |
|---|---|---|
| MIMO `mimo-v2.6-pro` | 最小请求 1.12 秒返回 `OK` | 当前工作区选中的模型可访问；未证明完整工具循环正常 |
| 盈米 MCP | 初始化及工具列表成功，返回 69 个工具 | 协议及鉴权可用；未逐个查询 |
| 天天基金 ttskill | 已安装，但 `logged_in=false`；直接基金查询报“需要登录” | 当前天天基金工具链无法正常完成该查询；登录记录过期时间为 2026-08-26 |
| 东方财富金融数据查询 | 按现有客户端路径和 `question` 参数请求，返回 HTTP 404 | `eastmoney_finance_data` 所用接口当前不可用；不能据此说东方财富全部服务不可用 |
| 正在运行的后端 | Agent 列表、分析日志、三条旧记录状态查询 HTTP 200 | 基础 API 可访问；旧记录仍返回 `running` |

天天基金涉及基金经理、净值、持仓及筛选等能力，影响基金分析、穿透分析和相关兜底数据源。东方财富客户端对金融数据查询错误捕获后返回空字典，分析继续执行时应明确说明数据缺失。代码位置：[ttfund_client.py](/Users/xiaoyuer/projects/investment-analyzer/backend/mcp/ttfund_client.py:202)、[eastmoney_client.py](/Users/xiaoyuer/projects/investment-analyzer/backend/mcp/eastmoney_client.py:135)。

## 12 个独立分析 Agent 逐项核对

所有默认分析 Agent 还共同受“初始化覆盖自定义提示词”影响，见问题 6。

| 当前 ID | Agent | 对应入口 | 核对结果 |
|---|---|---|---|
| 1 | 市场日报分析师 | POST `/api/dashboard/daily-report/regenerate` | 配置和调用链存在，有历史成功；未做当前完整日报实跑 |
| 2 | 分散度分析师 | POST `/api/portfolio/analysis/diversification/ai-summary` | 当天有成功记录；穿透数据受天天基金登录和其他数据源情况影响，不能以生成报告等同于数据完整 |
| 3 | 全景诊断分析师 | POST `/api/portfolio/analysis/panorama` | 有历史成功，但有旧记录永久 running、任务复用响应缺少记录 ID 问题 |
| 4 | 基金深度分析师 | POST `/api/portfolio/analysis/deep-dive/{holding_id}` | 有历史成功；存在跨基金任务复用、旧记录 running，且基金数据链部分不可用 |
| 5 | 交易复盘分析师 | POST `/api/portfolio/analysis/trade-review` | 有历史成功；任务复用响应可能缺少前端需要的记录 ID，未完整实跑 |
| 8 | 债券配置顾问 | POST `/api/bond/ai-recommend` | 实际运行资产配置专家；独立 Agent 的提示词不参与执行，专家失败仍可能显示完成 |
| 9 | 指数深度分析师 | POST `/api/analysis/run` | 当前库 ID 正确；不同指数可能复用同一任务并错标结果；新库固定 ID 会错配 |
| 10 | 市场情报分析师 | POST `/api/market-intelligence/overview/trigger` | 有近期成功；模型失败可被外层任务记为 done 并缓存，禁用配置可被绕过 |
| 11 | 增强策略分析师 | GET `/api/valuation/enhanced-strategy` | 有历史成功和失败；禁用配置可被默认提示词绕过；统一日志有旧 running |
| 12 | 情景推演分析师 | POST `/api/portfolio/analysis/what-if` | 前端场景参数与后端分派不匹配；独立提示词未使用；专家失败被记为成功 |
| 13 | 热点分析专家 | POST `/api/dashboard/hotspots-analysis` | 有历史成功；失败可被外层任务记为 done；禁用配置可被绕过 |
| 14 | 指定基金分析师 | POST `/api/portfolio/analysis/fund-analysis` | 有历史成功；跨基金任务复用、缺少记录 ID 和旧 running 问题 |

## 已确认的问题

### 1. P1：部分外部数据源当前不可用

天天基金登录检查与真实基金查询均确认未登录；东方财富金融数据接口返回 404，见上述依赖实测。这是当前环境问题，不能用 Mock 测试通过或历史报告成功消除。

优先恢复天天基金登录；核对东方财富金融数据接口路径、服务权限和协议。不要将单个金融接口故障扩展为全部东方财富服务故障。

### 2. P1：不同标的复用同一个任务，可能展示错对象的分析

[index_analysis.py](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/analysis/index_analysis.py:107) 仅按任务类型 `index_analysis` 去重，没有匹配指数代码。指数 A 正在分析时请求指数 B，会得到 A 的任务 ID，且缺少分析历史 `id`。[ValuationHistory.vue](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/components/valuation/ValuationHistory.vue:329) 却将完成结果放进 B 的结果栏。

同类问题见 [deep_dive.py](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/analysis/deep_dive.py:232)、[fund_analysis.py](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/analysis/fund_analysis.py:125)；部分持仓页面使用缺失的 `data.id`，可能轮询 `/analysis/undefined/status`。Mock 已复现指数入口直接返回已有的任意同类任务。

### 3. P1：专家失败仍被包装成“分析完成”

[what_if.py](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/analysis/what_if.py:129) 与 [bond_recommend.py](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/analysis/bond_recommend.py:251) 只读取专家 `analysis` 文本，不检查 `status=failed`。Mock 返回失败状态和兜底文本时，情景推演仍保存默认 done，债券推荐还将该文本包装成 `summary=AI分析完成`，外层任务 done。

热点和市场情报也有相同状态传播问题：[hotspots.py](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/analysis/hotspots.py:291) 内层捕获异常返回普通字典，[外层](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/analysis/hotspots.py:50) 无条件 done；[market_intel.py](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/analysis/market_intel.py:904) 失败后仍继续缓存返回，[外层](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/analysis/market_intel.py:686) 标为 done。

### 4. P1：所有对话专家共用的自我反思链路无法运行

[self_reflection.py](/Users/xiaoyuer/projects/investment-analyzer/backend/agent/infra/self_reflection.py:50) 的 JSON 示例花括号未转义，执行 [.format()](/Users/xiaoyuer/projects/investment-analyzer/backend/agent/infra/self_reflection.py:143) 报 `KeyError: '\n  "sufficient"'`。实际调用复现返回 `None`，模型调用次数为 0。数据库开关是 true，但功能没有真正执行。

另外还有两层后续缺陷：同文件第 176 行将整个模型返回对象送入 JSON 解析；[multi_agent.py](/Users/xiaoyuer/projects/investment-analyzer/backend/agent/core/multi_agent.py:897) 对补充请求的模型返回对象直接 `len()`，均没有提取 `choices[0].message.content`。修复模板时应一起验证完整评估和补充链路。

### 5. P1：工具历史压缩破坏工具调用配对

[multi_agent.py](/Users/xiaoyuer/projects/investment-analyzer/backend/agent/core/multi_agent.py:68) 删除中间工具回复，仍保留 assistant 的全部调用。输入 t0/t1/t2 三个调用和三个回复，实际压缩后只剩 t0/t2 回复，t1 没有响应。当前压缩开关为 true。

消息配对损坏已复现；是否被真实 MIMO 拒绝、返回何种错误，本次未发送这种损坏请求验证。不能将其描述为已经验证的线上 400。

### 6. P2：管理页修改的默认分析提示词在初始化时丢失

[analysis.py](/Users/xiaoyuer/projects/investment-analyzer/backend/db/analysis.py:72) 无条件用代码常量覆盖已有默认 Agent 提示词。临时数据库实测：初始化 → 保存自定义内容 → 再次初始化，自定义内容消失。虽然旧内容有版本备份，当前生效版本仍会被替换。

### 7. P2：部分 Agent 的提示词和禁用设置未实际生效

情景推演仅用“情景推演分析师”的配置取得日志 ID，实际执行风险/宏观专家，不使用其提示词：[what_if.py](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/analysis/what_if.py:89)。债券推荐同理，实际执行资产配置专家：[bond_recommend.py](/Users/xiaoyuer/projects/investment-analyzer/backend/routers/analysis/bond_recommend.py:233)。禁用后依然可以运行。

热点、市场情报、增强策略在查询不到启用 Agent 时回退到常量提示词继续运行，因而绕过禁用；不是所有功能都遵循同一种管理语义。

### 8. P2：前端情景参数使宏观场景分派失效

[PortfolioManagement.vue](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/components/portfolio/PortfolioManagement.vue:2205) 将“利率上升2%”等显示文案传给 [runWhatIf](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/api/index.js:2605)，后端却按 `rate_hike` 等 key 选择专家。因此加息等场景未进入宏观与风险双专家路径，而是落入默认风险专家。自定义场景也没有按后端 `custom/custom_scenario` 协议发送。

### 9. P2：旧分析记录永久停留 running

[get_analysis_record_status](/Users/xiaoyuer/projects/investment-analyzer/backend/db/portfolio.py:3625) 只读状态，不校验超时，也不关联异步任务终态。现有超时清理只处理 `async_tasks`。

真实数据库及线上状态接口均确认：全景诊断记录 113（2026-06-18）、指定基金记录 134（2026-06-23）、基金深度记录 183（2026-07-24）仍 running。[前端轮询](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/api/index.js:1716) 没有总时限。统一分析日志也有 3 条旧 running。

### 10. P2：当前运行库中的择时专家被覆盖

真实库中择时分析师 id=2564 和市场分析师 id=21831 都是 `market_analyst`。[loader](/Users/xiaoyuer/projects/investment-analyzer/backend/db/agents.py:1105) 直接按 key 写字典，实际仅市场分析师生效，择时专家独有的 `query_market_indices` 没有加载。

源码已有将择时专家迁移为 `market_timer` 的 [初始化迁移](/Users/xiaoyuer/projects/investment-analyzer/backend/db/agents.py:1010)，但当前真实库尚未应用。不要据此认定最新代码初始化后仍会重复。本次没有为验证迁移而修改真实库。

### 11. P2：专家配置字段丢失使部分个性化和防幻觉逻辑失效

[loader](/Users/xiaoyuer/projects/investment-analyzer/backend/db/agents.py:1090) 不返回 `knowledge_scope` 或 `agent_key`，下游却分别用它们裁剪 KYC 和启用基金代码检查。

[基金代码检查](/Users/xiaoyuer/projects/investment-analyzer/backend/agent/core/multi_agent.py:953) 因缺 key 直接跳过；补 key 后还有 `json.loads(dict)` 的类型错误被吞，工具日志实际保存的是字典参数。Mock 已验证这种输入不会进入持仓核对。此结论指该特定检查失效，不代表系统所有反幻觉机制都失效。

### 12. P2：新库默认 ID 与前端默认指数专家不一致

当前库 ID9 确实是指数深度分析师，但干净初始化的 ID9 是债券配置顾问，指数专家为 ID10。[前端](/Users/xiaoyuer/projects/investment-analyzer/frontend/src/api/index.js:852) 与请求模型默认使用 9，新部署可能用错提示词。临时数据库初始化已验证。另热点历史仍硬编码 ID13，新库热点实际 ID8，可能归属错误。

### 13. P3：尚未接入业务入口的统一执行器不能保存历史

[analysis_runner.py](/Users/xiaoyuer/projects/investment-analyzer/backend/agent/core/analysis_runner.py:140) 调用 `create_analysis_history` 缺 `index_code/index_name/valuation_context`，Mock 模型返回正常文本后，实际记录保存报 TypeError，被吞掉，返回 `record_id=None`，历史记录数为 0。

当前检索没有发现业务调用方，因此不是上述 12 个入口当前必然触发的缺陷，应与生产入口问题分开处理。

## 对话专家检查范围

| 专家 | 配置工具数 | 当前证据 |
|---|---:|---|
| 估值分析师 | 9 | 有历史运行，工具定义/分发存在；受公共反思/压缩缺陷影响 |
| 风险管理师 | 12 | 同上，情景推演复用该专家 |
| 资产配置师 | 11 | 同上，债券配置复用该专家 |
| 择时分析师 | 12 | 当前被同 key 的市场分析师覆盖 |
| 市场分析师 | 11 | 当前 `market_analyst` 的生效配置 |
| 基金分析师 | 14 | 有历史运行；天天基金工具不可用、特定基金代码检查跳过 |
| 文章解读专家 | 8 | 有历史运行；未完整验证文章抓取及观点核验 |
| 宏观策略师 | 13 | 有历史运行；未完整验证全部宏观数据工具 |
| 行业基本面分析师 | 9 | 有历史运行；东方财富金融查询接口不可用 |
| 行为金融学专家 | 4 | 有历史运行；未完整验收当前交易行为分析 |
| 可转债分析师 | 4 | 当前运行表中没有该 key 的运行记录；仅完成配置/工具接线检查 |
| 量化技术分析师 | 3 | 同上，不能据配置存在判定技术分析功能已正常验收 |

主专家与交叉审阅原生工具路径均包含 `reasoning_content` 回传逻辑，Mock 两轮调用确认有保留；没有将该项列为缺陷。

## 测试与后续处理

- 前端：20 个测试文件，159 项通过。
- 后端：`python3 -m pytest backend/tests -q --disable-warnings --tb=short`，665 项通过、15 项失败，用时 396.78 秒。失败原因分类见后续核对记录；不能将 15 项失败直接理解为 15 个 Agent 故障。
- 最小复现：提示词初始化覆盖、统一执行器保存失败、自反思模板异常、工具消息丢失回复、专家失败被包装成功、错误任务复用等均已使用隔离输入验证。
- 当前在线服务使用 Python 3.11，进程启动早于部分 10 月 17 日源码变更；本轮后端自动测试使用工作区可用的 Python 3.13。线上响应和工作区源码不能自动视为同一版本，报告已分别标注证据来源。

建议处理顺序：先恢复数据依赖和失败状态传播，阻止错标分析对象；再修复公共反思/工具历史链路；随后统一 Agent 配置语义、任务终态清理和稳定标识，补充真实入口的集成验收。完整分析验收应逐项核对数据来源、结果对象、失败提示、刷新恢复和历史记录，而不只检查是否生成了文字。
