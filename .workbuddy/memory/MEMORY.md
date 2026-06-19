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
