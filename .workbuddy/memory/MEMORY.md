# 投资分析助手 项目长期笔记

## 架构关键点
- 多 Agent 三阶段协作：orchestrator.py orchestrate_stream（并行专家 → multi_agent.run_specialist_with_context 交叉审阅 → run_arbitration 仲裁 DeepSeek R1）
- Prompt 数据库化：agents 表 + analysis_agents 表两套并行（冗余），有版本管理+热更新+回归测试
- 专家从 db.agents.load_specialist_agents 加载（60s 缓存），非硬编码
- 记忆三层治理：memory_lifecycle.py（候选评分→晋升→压缩→遗忘），user_memories 表
- 上下文组装：memory.py build_user_memory_context（画像+持仓+话题+记忆）注入 orchestrator
- 反馈学习：feedback_learner.py get_preference_context

## KYC 画像体系（2026-06-19 新增，阶段一）
- user_profiles 表扩展 6 理财维度 + 4 元字段
- agent/kyc.py：问卷 + 画像读写 + kyc_profile_to_text（注入专家 prompt）
- agent/kyc_learner.py：对话中关键词触发 LLM 提取信号，高置信(>=0.7)回写
- multi_agent._inject_kyc_profile：持仓注入后插 <kyc_profile>，按 knowledge_scope.kyc_dimensions 裁剪
- routers/profile.py：/api/profile/kyc、/api/profile/kyc/submit、/api/profile[GET|PUT]
- 前端 KycWizard.vue + Sidebar 自动引导

## 熊市/回撤分析方法（2026-10-23 建立，可复用）
- 判断「还要跌多少」不看单点跌幅，用 **触及某回撤阈值 → 该轮真底部** 的条件分布。创业板 −35% 共 6 次（3 轮独立熊市）5 次继续跌，中位再跌 −40.7%
- 判断「还要多久」区分 **急跌段(3-6月)** 与 **磨底段(以年计)**；创业板到真底中位 20-50 个月
- 必报**不对称性**：深度回撤(<−25%)后 120 日反弹中位 +23.6%；三次大底后 12 个月 +40%~+112%
- 区分**杀估值 / 杀业绩**：沪深300 跌 20.2% 而 PE 分位仍 42.6% → 分母下修快于价格，属杀业绩，估值底未到
- 回撤口径陷阱：`cummax` 是历史最高（沪深300 含 2007 年 5877），做"本轮熊市"必须用**滚动 3 年高点**，否则会得出 −31% 的错误结论

## 用户画像（行为 vs 问卷背离，2026-10-23）
- 问卷：loss_tolerance=high、horizon=long、risk=balanced；**行为推断：low / medium**（权重 0.6，置信 0.8，样本 45 笔）
- 行为指标：avg_holding 29.5 天、月交易 6.33 次、止损执行率 0%、亏损处置纪律 40 分 → 倾向"扛到受不了再一次性砍"
- 做投资建议时必须按 **low 亏损承受度 + medium 期限** 口径设定仓位，不能用问卷口径
- 权益仓集中：消费/白酒 44.9% + 港股科技 15.8% + 医药 12.8% = 73.5%；现金 42%、债券 35% → 组合本身已防御，压力测试（beta 1.15）再跌 40% 总资产仅 −9.2%

## 估值数据链路（2026-10-23 补）
- `/api/valuations/fetch-recent` 只覆盖 6 个 A 股宽基（000300/000905/000852/399006/000016/399303），**港股与行业主题指数不会自动刷新估值**
- `/api/valuation/online-query/{code}` 能在线查（akshare→天天基金），但**结果不入库**（仅写 valuations_online L2 缓存，7天TTL）
- `buy-score` 的 valuation 维度走 `get_latest_valuation()`，**只读 index_valuations，不 fallback 到 valuations_online** → 未落库的指数分位恒为 None，评分退化为中性 50
- 补链路脚本：`backend/scripts/refresh_watchlist_index_valuation.py`（默认预演 / `--apply` 写入），遍历 watchlist 中 index_code 非空项 → `_online_fallback` → `save_valuation` → 回写 `current_percentile`
- 排查同一指数分位要注意**窗口口径**：930792 近10年分位 95.78% vs 近5年 97.25%，两条都对但不可混用

## 重要断层（已修复/待修复）
- ✅ F1 记忆读写断层：memory.py build_user_memory_context 已补 get_memories（2026-06-19）
- ✅ reasoning_content 流式给前端（阶段二，2026-06-19）：_call_llm_stream + reasoning_chunk/answer_chunk + ReasoningPanel + ChatView 增量拼接
- ✅ 估值融合层（阶段三，2026-06-19）：valuation_fusion.py + analyze_stock enable_fusion
- ✅ 盈米 MCP 接入工具体系（阶段三，2026-06-19）：6 工具 + _call_yingmi，真实数据可用
- ✅ RAG 个性化重排（阶段三，2026-06-19）：rerank_results 加 user_id + 关注品种/历史主题加权

## 理财专家团队（阶段四，2026-06-19）
- 3 个新编排专家：wealth_advisor（专属理财顾问，全维度画像）/behavior_coach（行为金融辅导师）/macro_strategist（宏观策略师），db.agents._init_wealth_specialists 初始化
- 主动关怀预警：agent/wealth_advisor.py generate_proactive_alerts（持仓回撤超亏损承受度+估值极端）+ /api/profile/alerts
- 需求澄清路由已含新专家；最终答案注入 KYC 画像
- 多轮澄清深度升级 + 完整主控综合层 + Agent表合并 = 后续增强（未做）

## UI 美化升级（2026-06-19，专业金融终端风格）
- 方案：/Users/xiaoyuer/.workbuddy/plans/blazing-cascade-darwin.md
- 阶段1完成：lucide-vue-next 图标库 + Icon.vue 适配层（显式 import tree-shake）+ style.css 金融终端 token（13px/冷灰/涨跌色阶/fin-table/mono数字）+ Sidebar/ChatInput/ReasoningPanel/Home emoji 清理 + 删死媒体查询
- 阶段2完成：finance/ 6 组件（Sparkline/AnimatedNumber/PercentileBar/ThermoMeter/StatusBadge/TickerBar）+ App.vue 行情顶栏
- ⏳ 阶段3（核心页深化）：Dashboard/PortfolioManagement/ValuationHistory/ChatMessage/MarketIntelligence 接入金融组件 + 散落 emoji 清理 + 硬编码色走 token
- ⏳ 阶段4（移动端统一+收尾）：MobileApp 去 emoji/去重 + 硬编码色清扫 + UI_DESIGN_SPEC.md 同步
- 关键：Icon.vue 必须显式 import（勿用 import *，bundle 暴增600KB）；亮色深海蓝/暗色金色双主题不统一主色

## 知识库治理层（2026-10-23 建立，P0→P3）
- 文档 `docs/KNOWLEDGE_GOVERNANCE.md`；CLI `backend/scripts/kb_governance.py`（默认预演，`--apply` 落库）
- 核心模块 `services/knowledge_governance.py`（判定规则+schema+运行时元数据缓存+框架路由+实体消歧）、`services/user_lessons.py`（从真实交易生成教训）
- `knowledge_base` 新增 5 列：domain / school / temporal_type / retrieval_count / last_retrieved_at
- **流派按「书」映射**（37 本手工表），不按原子 —— 一本书方法论自洽，按原子分类会自相矛盾
- `build_rag_context_with_details(query, content_types, limit, domains, framework)`：domains 默认 ["investment"]，framework 默认取 `rag.default_framework`=value
- 上下文每条证据带 `[流派:xx]`，召回到对立流派时最前面插 `[知识库流派披露]` 要求标明出处
- 关键顺序：治理加权必须在 `[:limit]` 截断**之前**，否则改变不了入选集合
- 新增条目必须显式索引（`--index-lessons`），且 `content_type` 必须等于 `knowledge_base.category`（两处 KNOWLEDGE_TYPES 都要加）
- ⚠️ `scripts/sync_knowledge_to_rag.py` 会重复索引（写 content_type='knowledge'），**不要运行**
- 未做：ChromaDB 历史向量未重建（domain/school 元数据只进新建索引）；流派只影响排序不影响候选池

## 开发惯例
- 加表字段用 _add_column_if_not_exists（db/_utils），幂等安全
- 路由：APIRouter(tags=[...]) + 完整路径 /api/xxx，app.py import + include_router
- 前端：api/index.js 用 import api from './http'（baseURL 带 /api），函数 export function
- 弹窗：Teleport to body + Transition name="fade" + .dialog-backdrop
- 单用户 user_id='default' 硬编码

## 运行环境
- 后端测试用系统 python /Library/Frameworks/Python.framework/Versions/3.13/bin/python3（装了 openai 等依赖）；managed python 3.13.12 缺项目依赖
- 前端构建用 managed node 22.22.2
- init_db 在 db/__init__.py:140（非 db/core.py，AGENTS.md 描述与实现不符）
