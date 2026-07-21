"""系统配置 CRUD — system_config 表操作"""

import time as _time
from db._conn import _get_conn

# 2026-07-13 性能优化：配置进程级缓存（dict + TTL），消除 N+1 DB 往返
_CONFIG_CACHE: dict[str, tuple[str, float]] = {}
_CONFIG_CACHE_TTL = 60.0  # 秒

# 默认配置（首次启动时写入）
DEFAULT_CONFIGS = [
    # 估值阈值
    ('valuation.undervalued_percentile', '30', '低估百分位阈值（低于此值视为低估）', 'valuation'),
    ('valuation.overvalued_percentile', '70', '高估百分位阈值（高于此值视为高估）', 'valuation'),
    ('valuation.extreme_undervalued', '10', '极度低估百分位', 'valuation'),
    ('valuation.extreme_overvalued', '90', '极度高估百分位', 'valuation'),
    ('valuation.freshness_days', '30', '估值数据有效期（天）', 'valuation'),

    # 债券温度阈值
    ('bond.temp_cold', '30', '债券温度-冷（低于此值为冷）', 'bond'),
    ('bond.temp_cool', '50', '债券温度-凉', 'bond'),
    ('bond.temp_warm', '70', '债券温度-温（高于此值为热）', 'bond'),

    # 集中度阈值
    ('concentration.top3_high', '60', '前3集中度-高（%）', 'portfolio'),
    ('concentration.top3_moderate', '40', '前3集中度-中（%）', 'portfolio'),
    ('concentration.single_fund_high', '25', '单基金集中度-高（%）', 'portfolio'),

    # 现金比例
    ('cash.ratio_warning', '0.20', '现金比例预警（高于此值）', 'portfolio'),
    ('cash.ratio_low', '0.03', '现金比例过低（低于此值）', 'portfolio'),

    # LLM 参数
    ('llm.temperature_default', '0.3', '默认温度', 'llm'),
    # 仲裁模型配置
    ('arbitration.model', '', '仲裁模型名（空则使用默认ARBITRATION_MODEL）', 'arbitration'),
    ('llm.temperature_analysis', '0.3', '分析任务温度', 'llm'),
    ('llm.temperature_vision', '0.1', '图片分析温度', 'llm'),
    ('llm.temperature_eval', '0.2', '评测任务温度', 'llm'),
    ('llm.max_tokens_report', '8192', '报告最大token', 'llm'),
    ('llm.max_tokens_chat', '8000', '对话最大token', 'llm'),
    ('llm.max_tokens_analysis', '8000', '分析最大token', 'llm'),
    ('llm.max_tokens_eval', '2000', '评测最大token', 'llm'),
    ('llm.max_tokens_eval_score', '500', '评测打分最大token', 'llm'),
    ('llm.max_tokens_agent', '8000', 'Agent默认最大token', 'llm'),
    ('llm.max_tokens_orchestrator', '8192', 'Orchestrator最大token', 'llm'),
    ('llm.max_tokens_orchestrator_summary', '1000', 'Orchestrator摘要最大token', 'llm'),
    ('llm.max_tokens_rag_compress', '1500', 'RAG压缩最大token', 'llm'),
    ('llm.max_tokens_vision', '8000', '视觉模型最大token', 'llm'),
    ('llm.max_tokens_fusion', '2000', '估值融合最大token', 'llm'),
    ('llm.max_tokens_tool', '1500', '工具调用最大token', 'llm'),
    ('llm.max_tokens_rewrite', '50', '查询改写最大token', 'llm'),
    ('llm.max_tokens_dashboard_summary', '200', 'Dashboard摘要最大token', 'llm'),
    ('llm.max_tokens_valuation_summary', '800', '估值摘要最大token', 'llm'),
    ('llm.temperature_agent', '0.3', 'Agent任务温度', 'llm'),
    ('llm.temperature_arbitration', '0.2', '仲裁任务温度', 'llm'),
    ('llm.temperature_fusion', '0.2', '估值融合温度', 'llm'),
    ('llm.temperature_tool', '0.2', '工具调用温度', 'llm'),
    ('llm.temperature_rewrite', '0.1', '查询改写温度', 'llm'),
    ('llm.timeout_default', '120', 'LLM调用默认超时（秒）', 'llm'),
    ('llm.timeout_vision', '120', '视觉模型调用超时（秒）', 'llm'),
    ('llm.timeout_short', '15', '短任务超时（秒，如新闻摘要）', 'llm'),

    # LLM 成本控制开关：默认关闭自动触发类调用
    ('llm_cost.auto_daily_report', 'false', '自动市场日报', 'llm_cost'),
    ('llm_cost.auto_daily_eval', 'false', '每日评测 Pipeline', 'llm_cost'),
    ('llm_cost.auto_conversation_eval', 'false', '对话结束自动评测', 'llm_cost'),
    ('llm_cost.auto_conversation_summary', 'false', '对话摘要自动生成', 'llm_cost'),
    ('llm_cost.page_llm_summary', 'false', '页面分析 LLM 总结', 'llm_cost'),
    ('llm_cost.llm_judge_eval', 'false', 'LLM-as-Judge 评分', 'llm_cost'),
    ('llm_cost.root_cause_analyzer', 'false', '自动根因分析', 'llm_cost'),
    ('llm_cost.auto_shadow_mode', 'false', 'Shadow Mode 静默运行', 'llm_cost'),
    # 第二轮降本新增开关（默认关闭，主链路已有规则兜底）
    ('llm_cost.rag_query_rewrite', 'false', 'RAG 查询 LLM 重写（关闭走 jieba）', 'llm_cost'),
    ('llm_cost.kyc_learning', 'false', 'KYC 画像自动学习', 'llm_cost'),
    ('llm_cost.feedback_learning', 'false', '反馈画像自动学习', 'llm_cost'),
    ('llm_cost.memory_summarize', 'false', '对话历史 LLM 压缩（关闭走截取回退）', 'llm_cost'),
    ('llm_cost.daily_advisor_llm', 'false', '持仓信号 LLM 行业评分', 'llm_cost'),

    # 内容截断限制
    ('truncation.article_content', '8000', '文章内容截断长度', 'llm'),
    ('truncation.context', '3000', '上下文截断长度', 'llm'),
    ('truncation.rag_context', '4000', 'RAG上下文截断长度', 'llm'),
    ('truncation.history_messages', '20', '历史消息保留条数', 'llm'),
    ('truncation.tool_result', '4000', '工具结果截断长度', 'llm'),

    # 补仓跌幅预警
    ('alert.buy_drop_pct', '4', '补仓后跌幅预警阈值（%）', 'alert'),
    # P1-3: 预警财经新闻结合（默认关闭，开启后调用 MCP SearchFinancialNews）
    ('alert.news_integration', 'false', '是否启用预警财经新闻结合（MCP 调用，默认关闭）', 'alert'),
    ('alert.news_cache_ttl', '30', '新闻缓存分钟数', 'alert'),
    ('alert.news_per_fund', '3', '单基金新闻条数上限', 'alert'),
    # 前瞻性事件雷达（LLM 相关，默认关闭）
    ('alerts.event_radar_enabled', 'false', '前瞻性事件雷达总开关', 'alerts'),
    ('alerts.event_radar_lookforward_days', '14', '前瞻视野天数（1-14）', 'alerts'),
    ('alerts.event_radar_max_events', '15', '单次扫描最多提取事件数', 'alerts'),
    ('alerts.event_radar_min_confidence', '0.4', '低于此置信度不推送', 'alerts'),
    ('alerts.event_radar_scan_time', '20:00', '每日扫描时间 HH:MM', 'alerts'),
    ('alerts.event_radar_news_sources', 'yingmi,eastmoney,akshare', '新闻源（逗号分隔）', 'alerts'),
    ('alerts.event_radar_max_candidate_funds', '5', '建仓机会卡片最多展示基金数', 'alerts'),
    ('alerts.event_radar_verify_window_days', '3', '事件落地后验证窗口天数（T+N）', 'alerts'),
    ('alerts.event_radar_verify_enabled', 'true', '事件落地验证开关（非LLM相关，默认开启）', 'alerts'),
    ('alerts.watchlist_signal_enabled', 'true', '关注列表上车信号扫描开关（非LLM相关，默认开启）', 'alerts'),
    ('alerts.watchlist_drop_threshold', '3', '关注基金单日跌幅%阈值触发上车提醒', 'alerts'),
    ('alerts.health_score_scan_enabled', 'true', '健康分预警扫描开关（非LLM相关，默认开启）', 'alerts'),
    ('alerts.health_score_threshold', '60', '健康分预警阈值（低于此值触发预警）', 'alerts'),
    ('alerts.valuation_failure_scan_enabled', 'true', '估值查询失败预警开关（闭环兜底监控，默认开启）', 'alerts'),
    # 2026-07-17 新增：大盘大跌预警（捕捉系统性风险）
    ('alerts.market_index_drop_scan_enabled', 'true', '大盘指数当日跌幅监控开关（预警核心能力，默认开启）', 'alerts'),
    ('alerts.market_index_warn_threshold', '2', '大盘指数当日跌幅 warning 阈值（%）', 'alerts'),
    ('alerts.market_index_danger_threshold', '4', '大盘指数当日跌幅 danger 阈值（%）', 'alerts'),
    # 2026-07-17 新增：持仓当日跌幅（区别于累计亏损）
    ('alerts.daily_drop_scan_enabled', 'true', '持仓基金当日跌幅扫描开关（区别于累计亏损，默认开启）', 'alerts'),
    ('alerts.daily_drop_threshold', '3', '持仓基金当日跌幅阈值（%，当日跌≥此值触发预警）', 'alerts'),
    # 2026-07-17 新增：资金流向异常监控
    ('alerts.capital_flow_scan_enabled', 'true', '南向资金异常流出监控开关（默认开启）', 'alerts'),
    # 2026-07-17 新增：交易时段缩短扫描间隔
    ('alerts.trading_hours_scan_interval_minutes', '5', '交易时段（9:00-15:00）扫描间隔分钟数（默认5，捕捉盘内异动）', 'alerts'),

    # 估值查询闭环兜底（非LLM相关，默认开启）
    ('valuation.online_fallback_enabled', 'true', '估值在线兜底总开关（本地表无数据时自动查akshare/天天基金）', 'valuation'),
    ('valuation.online_fallback_timeout_ms', '5000', '在线兜底单渠道超时（毫秒）', 'valuation'),
    ('valuation.online_cache_ttl', '3600', '在线兜底结果内存缓存TTL（秒）', 'valuation'),
    ('valuation.monitoring_enabled', 'true', '估值查询监控日志开关', 'valuation'),

    # 业务常量
    ('portfolio.default_account', '花无缺', '默认账户名', 'portfolio'),
    ('portfolio.default_user_id', 'default', '默认用户ID', 'portfolio'),
    ('portfolio.users', '小鱼儿,花无缺', '用户列表（逗号分隔）', 'portfolio'),
    ('portfolio.snapshot_max_count', '365', '持仓快照最大保留条数', 'portfolio'),

    # 每日持仓提示
    ('daily_advice.enabled', 'true', '是否启用每日持仓提示', 'daily_advice'),
    ('daily_advice.base_dca_amount', '500', '基础定投金额', 'daily_advice'),
    ('daily_advice.dca_drop_step_pct', '4', '4%定投法跌幅档位', 'daily_advice'),
    ('daily_advice.max_dca_steps', '3', '最大加仓档位', 'daily_advice'),
    ('daily_advice.min_cash_pct', '5', '现金最低保留比例（%）', 'daily_advice'),
    ('daily_advice.max_cash_use_pct_per_signal', '10', '单条建议最多使用现金比例（%）', 'daily_advice'),
    ('daily_advice.default_single_position_pct', '15', '未设置画像时的单标的默认上限（%）', 'daily_advice'),
    ('daily_advice.add_valuation_max_percentile', '35', '加仓建议最高估值百分位', 'daily_advice'),
    ('daily_advice.reduce_valuation_min_percentile', '80', '减仓复核最低估值百分位', 'daily_advice'),
    ('daily_advice.down_days_watch', '3', '连续下跌观察阈值', 'daily_advice'),
    ('daily_advice.down_days_action', '5', '连续下跌行动阈值', 'daily_advice'),
    ('daily_advice.recent_buy_cooldown_days', '10', '补仓冷静期（天）', 'daily_advice'),
    ('daily_advice.recent_buy_max_count', '2', '冷静期内最多买入次数', 'daily_advice'),

    # 指数代码
    ('index.hs300_code', '000300.SH', '沪深300指数代码', 'index'),

    # 成本路由模型映射（增强6）
    ('cost_routing.enabled', 'true', '是否启用成本路由', 'cost_routing'),
    ('cost_routing.conservative_model', 'mimo-v2.5-pro', '保守模式统一模型', 'cost_routing'),
    ('cost_routing.orchestrator_model', 'mimo-v2.5-pro', '编排器模型', 'cost_routing'),
    ('cost_routing.valuation_expert_model', 'mimo-v2.5-pro', '估值专家模型', 'cost_routing'),
    ('cost_routing.allocation_advisor_model', 'mimo-v2.5-pro', '配置专家模型', 'cost_routing'),
    ('cost_routing.fund_analyst_model', 'mimo-v2.5-pro', '基金分析模型', 'cost_routing'),
    ('cost_routing.risk_assessor_model', 'mimo-v2.5-pro', '风控专家模型', 'cost_routing'),
    ('cost_routing.market_analyst_model', 'deepseek-v4-flash', '市场分析模型', 'cost_routing'),
    ('cost_routing.behavioral_coach_model', 'mimo-v2.5-pro', '行为辅导模型', 'cost_routing'),
    ('cost_routing.cross_review_model', 'mimo-v2.5-pro', '交叉审阅模型', 'cost_routing'),
    # P2: 模型分级（趋势/文本类用便宜模型，推理/仲裁类用强模型）
    ('cost_routing.macro_strategist_model', 'deepseek-v4-flash', '宏观策略师模型（趋势判断）', 'cost_routing'),
    ('cost_routing.article_expert_model', 'deepseek-v4-flash', '文章解读专家模型（文本摘要）', 'cost_routing'),
    ('cost_routing.arbitrator_model', 'mimo-v2.5-pro', '仲裁专家模型（需强推理）', 'cost_routing'),
    ('cost_routing.debate_arbitrator_model', 'mimo-v2.5-pro', '辩论仲裁模型（需强推理）', 'cost_routing'),

    # 多智能体决策增强开关（P0-P3）
    ('agent.risk_veto_enabled', 'true', 'P0-A: 风险专家硬约束否决权', 'agent'),
    ('agent.portfolio_impact_enabled', 'true', 'P0-B: 专家结论持仓影响标注', 'agent'),
    ('pipeline.debate_enabled', 'true', 'P1: 对抗式辩论节点（冲突时触发）', 'pipeline'),
    ('pipeline.enhanced_plan_enabled', 'true', 'P3: 强化 Plan-and-Execute', 'pipeline'),

    # 对话链路增强开关（Reflection 自纠错 + 工具校验 + 合规过滤）
    ('pipeline.reflection_enabled', 'true', 'Reflection 阶段（自评质量问题）', 'pipeline'),
    ('pipeline.reflection_self_correct_enabled', 'true', 'Reflection 自纠错循环（低置信重跑专家）', 'pipeline'),
    ('pipeline.reflection_confidence_threshold', '-20', '触发重跑的置信度阈值（×100，-20 即 -0.2）', 'pipeline'),
    ('tools.validate_result_enabled', 'true', '工具结果校验（PE/PB/分位范围）+ 重试', 'tools'),
    ('pipeline.compliance_filter_enabled', 'true', '合规过滤（保本/稳赚等违规表述）', 'pipeline'),
    ('pipeline.fund_code_warning_enabled', 'true', '基金代码风险标注（含代码时追加核实提示）', 'pipeline'),

    # RAG 检索增强 + 交互式澄清
    ('rag.subquery_expansion_enabled', 'true', 'RAG 子查询展开（多角度检索提升召回）', 'rag'),
    ('rag.intent_driven_types_enabled', 'true', 'RAG intent 驱动 content_types 过滤', 'rag'),
    ('pipeline.clarification_interactive_enabled', 'true', '交互式澄清（前端展示选项 + 续答恢复）', 'pipeline'),

    # 多智能体对话降本增效开关
    ('router.enabled', 'true', '是否启用智能路由', 'router'),
    ('router.use_llm_fallback', 'true', '规则未命中时是否用 LLM 兜底', 'router'),
    ('router.default_complexity', 'medium', '默认复杂度', 'router'),
    ('cache.semantic.enabled', 'true', '是否启用语义缓存', 'cache'),
    ('cache.ttl_minutes', '30', '专家结果缓存 TTL（分钟）', 'cache'),
    ('cache.similarity_threshold', '0.92', '语义缓存相似度阈值', 'cache'),
    ('early_stop.enabled', 'true', '是否启用早停机制', 'early_stop'),
    ('early_stop.min_specialists', '2', '早停检查的最少专家数', 'early_stop'),
    ('validator.enabled', 'true', '是否启用轻量反思', 'validator'),
    ('validator.max_repair_attempts', '1', 'Validator 最大修复次数', 'validator'),
    ('validator.llm_check_enabled', 'false', '是否启用 LLM 质检（默认关闭以节约成本）', 'validator'),

    # 视觉模型切换（运行时可切换，无需重启）
    ('vision.provider', 'mimo', '视觉模型提供商（ollama / mimo）', 'vision'),
    ('vision.ollama.api_key', 'ollama', 'Ollama API Key', 'vision'),
    ('vision.ollama.base_url', 'http://localhost:11434/v1', 'Ollama Base URL', 'vision'),
    ('vision.ollama.model', 'qwen3-vl:8b', 'Ollama 视觉模型名', 'vision'),
    ('vision.mimo.api_key', 'tp-cztoehx9kc6uqpwm53adzok8agg84zfokfje362cqmfjzprg', 'MiMo API Key', 'vision'),
    ('vision.mimo.base_url', 'https://token-plan-cn.xiaomimimo.com/v1', 'MiMo Base URL', 'vision'),
    ('vision.mimo.model', 'mimo-v2.5', 'MiMo 视觉模型名', 'vision'),

    # 专家调度上限
    ('max_specialists.simple', '1', '简单任务最大专家数', 'orchestrator'),
    ('max_specialists.medium', '2', '中等任务最大专家数', 'orchestrator'),
    ('max_specialists.complex', '4', '复杂任务最大专家数', 'orchestrator'),

    # P1-1/P1-2/P1-3：多智能体结论价值最大化
    ('agent.persist_conclusions', 'true', '是否持久化多智能体结论到 analysis_conclusions', 'agent'),
    ('agent.reuse_recent_conclusions', 'true', '是否跨对话复用 48h 内同标的结论（开启后专家能看到同支基金近期的历史分析结论）', 'agent'),
    ('agent.reuse_conclusions_hours', '48', '结论复用时间窗口（小时）', 'agent'),
    ('agent.link_cross_system_refs', 'true', '是否激活 cross_system_references 桥接（链接结论到已接受决策）', 'agent'),

    # P2-1：长对话超时保护
    ('conversation.warn_at_minutes', '5', '长对话警告阈值（分钟）', 'conversation'),
    ('conversation.abort_at_minutes', '8', '长对话硬收尾阈值（分钟）', 'conversation'),

    # LLM 调用优化：冲突检测缓存 + cross_review 单轮意见 + ReAct 压缩
    ('agent.conflict_detect_cache', 'true', '冲突检测结果缓存（cross_review 前后原始专家列表相同时复用，省 1 次 LLM 调用）', 'agent'),
    ('agent.cross_review_opinion_mode', 'true', 'cross_review 单轮意见模式（不调用工具，仅 1 次 LLM）；false 回退到旧 ReAct 模式', 'agent'),
    ('agent.react_tool_result_max_chars', '1500', 'ReAct 工具结果截断阈值（字符数，原 3000）', 'agent'),
    ('agent.react_compress_history', 'true', '是否压缩历史 tool 消息为摘要（避免 ReAct context 膨胀）', 'agent'),

    # Agent 决策质量增强（自我反思 + 工具广播 + Agentic RAG，默认开启 — 质量优先）
    ('agent.self_reflection_enabled', 'true', '单专家自我反思开关：专家生成分析后自评4维度，发现缺口时重试补充', 'agent'),
    ('agent.self_reflection_max_retry', '1', '自我反思触发后的最大重试次数', 'agent'),
    ('agent.tool_broadcast_enabled', 'true', '工具结果广播开关：白名单工具结果结构化提取写入黑板，后续专家直接引用避免重复查询', 'agent'),
    ('agent.tool_broadcast_max_entries', '10', '黑板工具广播区最大条目数（FIFO淘汰）', 'agent'),
    ('agent.agentic_rag_enabled', 'true', 'Agentic RAG开关：3阶段主动检索策略（信息缺口判断→主动检索→充分性自检）', 'agent'),
    ('agent.agentic_rag_max_rounds', '2', 'Agentic RAG每个信息缺口最大检索轮数', 'agent'),

    # M1/M4/M6 多Agent整体增强开关（2026-07-16 Phase 1）
    ('agent.question_type_routing_enabled', 'true', '问题类型感知路由开关：纯规则分类5类问题（归因/预测/操作/对比/通用），强制追加对应专家', 'agent'),
    ('agent.agentic_rag_hard_limit_enabled', 'true', 'Agentic RAG硬性限制开关：检索类工具单独计数，超过agentic_rag_max_rounds强制拦截并进入分析', 'agent'),
    ('tool.southbound_capital_enabled', 'true', '南向资金（港股通）查询工具开关：akshare stock_hsgt_south_net_flow_in_em 数据源', 'tool'),
    ('tool.policy_news_enabled', 'true', '政策新闻聚合工具开关：盈米+东方财富+央视多源聚合，按重要性分级', 'tool'),
    ('tool.online_valuation_query_enabled', 'true', '主动在线估值查询工具开关：专家主动调用akshare查最新估值（不受自动兜底开关控制），默认true', 'tool'),

    # M3/M5/M2/M7 多Agent整体增强开关（2026-07-16 Phase 2/3）
    ('agent.force_devil_advocate_enabled', 'true', '强制魔鬼代言人开关：交叉审阅disagreements为空时二次提示强制反驳，串行最后位专家注入质疑角色', 'agent'),
    ('agent.devil_advocate_model', 'deepseek-v4-flash', '魔鬼代言人使用的轻量模型（控制成本）', 'agent'),
    ('agent.deep_synthesis_enabled', 'true', '综合报告深度保留开关：5段结构(核心结论/推理链条/分歧反驳/操作建议/风险提示)，结论长度300字，max_tokens提升至3000', 'agent'),
    ('agent.industry_fundamentalist_enabled', 'true', '行业基本面分析师开关：自下而上行业景气度分析（批价/动销/库存/产能），补全估值/风险/配置之外的维度', 'agent'),
    ('agent.behavioral_advisor_enabled', 'true', '行为金融学专家开关：识别追涨杀跌/损失厌恶/处置效应/锚定效应等6大偏差，给行为纠偏建议', 'agent'),
    ('agent.self_reflection_cross_check_enabled', 'false', '自我反思跨专家盲点检查开关（默认关，需手动开）：反思增加第5维度，检查跨专家盲点', 'agent'),

    # 持仓幻觉修复 + 分析质量提升（2026-07-16）
    ('agent.cross_review_force_on_complexity', 'true', '复杂度强制交叉审阅开关：complex/medium即使无冲突也强制触发交叉审阅（魔鬼代言人/盲点检查）', 'agent'),
    ('agent.specialist_quota_enabled', 'true', '专家数量配额开关：complex≥3、medium≥2，不足时按优先级补充风险/配置/市场专家', 'agent'),
    ('agent.complexity_min_specialists_complex', '3', 'complex复杂度最少专家数', 'agent'),
    ('agent.complexity_min_specialists_medium', '2', 'medium复杂度最少专家数', 'agent'),
    ('agent.absolutism_filter_enabled', 'true', '绝对化措辞过滤开关：最终回答后处理替换"一定/必然/绝对/肯定"为概率性表述', 'agent'),

    # 对话质量仲裁与时机判断完全优化（conv#131 修复，2026-07-21）
    # 所有新开关默认 false，符合项目规范"新 LLM 相关开关默认 false"
    ('agent.arbitration_consistency_guard_enabled', 'false', 'P0-A 仲裁-综合一致性硬校验：检测综合报告操作建议与仲裁裁决方向冲突，冲突时追加警告', 'agent'),
    ('agent.timing_judgment_enforced', 'false', 'P0-B 时机判断强制注入：综合报告 prompt 强制要求估值时机/盈亏时机/执行时机三问', 'agent'),
    ('agent.profit_not_loss_principle_enabled', 'false', 'P0-C 止盈不止损原则注入：综合报告 prompt 强制要求亏损标的禁止清仓（除非满足4条件之一）', 'agent'),
    ('agent.sell_timing_guard_enabled', 'false', 'P1-E 卖出时机守卫：检测卖出操作但未提供估值分位/盈亏状态时追加警告', 'agent'),
    ('agent.synthesis_tool_summary_enabled', 'false', 'P2-F 综合报告工具结果汇总注入：综合阶段注入黑板工具广播结构化数据', 'agent'),

    # 机会雷达深度研究增强（2026-07-21）
    # L1/L2 为 LLM 相关，默认 false；L3 为非 LLM 相关，默认 true
    ('opportunity.llm_policy_analysis_enabled', 'false', 'L1 政策解读 LLM 化：对 watch/can_buy 候选调用 LLM 做政策实质解读，调整 score ±5~8', 'opportunity'),
    ('opportunity.llm_deep_review_enabled', 'false', 'L2 深度推理评审：对 can_buy 候选调用 LLM 做多维度权衡评审，可降级不可升级', 'opportunity'),
    ('opportunity.benchmark_backtest_enabled', 'true', 'L3 回测基准化：引入沪深300超额收益，超额≥2%才算命中（非LLM相关，默认开启）', 'opportunity'),

    # 理财决策升级 6 项配置（默认全部开启 — 最强版本）
    ('attribution.enabled', 'true', '收益归因分析开关', 'decision_upgrade'),
    ('behavior_diagnosis.enabled', 'true', '行为金融诊断开关', 'decision_upgrade'),
    ('decision_accuracy.enabled', 'true', '决策准确率追踪开关', 'decision_upgrade'),
    ('strategy_backtest.enabled', 'true', '策略库+回测开关', 'decision_upgrade'),
    ('portfolio_optimizer.enabled', 'true', '组合优化引擎开关', 'decision_upgrade'),
    ('valuation_forecast.enabled', 'true', '估值预测信号开关', 'decision_upgrade'),

    # 智能补仓计划器
    ('smart_add.enabled', 'true', '智能补仓计划器总开关', 'smart_add'),
    ('smart_add.base_dca_pct', '4', '基础定投比例（年化，占总资产%）', 'smart_add'),
    ('smart_add.pyramid_enabled', 'true', '金字塔补仓引擎开关', 'smart_add'),
    ('smart_add.pool_pct', '15', '补仓资金池占总资产%', 'smart_add'),
    ('smart_add.pyramid_tiers', '10:15:5,20:25:10,30:30:15,40:20:20,50:10:25', '金字塔档位（亏损%:释放率:加仓占市值%）', 'smart_add'),
    ('smart_add.loss_threshold', '-10', '触发金字塔补仓的亏损阈值%', 'smart_add'),
    ('smart_add.max_single_position_pct', '25', '单标的占总仓位上限%', 'smart_add'),
    ('smart_add.valuation_pause_pct', '60', '估值分位回升到此值暂停引擎', 'smart_add'),
    ('smart_add.stale_days', '14', '估值数据过期天数阈值', 'smart_add'),
    ('smart_add.snapshot_enabled', 'true', '智能补仓建议快照落库开关（反事实决策验证，默认开启）', 'smart_add'),
    ('smart_add.hypothetical_enabled', 'true', '假设操作自动生成开关（每次建议自动创建假设交易，默认开启）', 'smart_add'),
    ('smart_add.max_add_vs_position_mult', '1.0', '单标的补仓金额上限=原市值×此倍数（2026-07-17从2.0降至1.0，配合档位重构）', 'smart_add'),

    # 多维度触发器（2026-07-17 新增）— 冷却期+趋势加仓+大跌定投
    ('smart_add.cooldown_days', '10', '冷却期天数：近N天内同基金买入次数超限则拦截', 'smart_add'),
    ('smart_add.max_buys_in_cooldown', '2', '冷却期内最大买入次数（含真实+假设交易）', 'smart_add'),
    ('smart_add.trend_signal_enabled', 'true', '趋势加仓信号开关（近期涨势好时小仓位试探）', 'smart_add'),
    ('smart_add.trend_lookback_days', '20', '趋势加仓回看天数（计算近N日涨幅）', 'smart_add'),
    ('smart_add.trend_min_gain_pct', '3.0', '趋势加仓最小涨幅%（近N日涨幅超过此值才触发）', 'smart_add'),
    ('smart_add.trend_position_pct', '5', '趋势加仓仓位上限%（占总资产，小仓位试探）', 'smart_add'),
    ('smart_add.trend_base_ratio', '5', '趋势加仓基数（占标的市值%，5%即小仓位试探）', 'smart_add'),
    ('smart_add.dip_signal_enabled', 'true', '大跌定投信号开关（连续大跌4%分批定投）', 'smart_add'),
    ('smart_add.dip_base_ratio', '8', '大跌定投基数（占标的市值%，8%因跌幅已确认略大于趋势）', 'smart_add'),
    ('smart_add.dca_drop_step_pct', '4', '大跌定投触发步长%（累计跌幅达此值触发定投）', 'smart_add'),
    ('smart_add.dca_tiers', '4:1.0,8:1.5,12:2.0', '大跌定投档位（跌幅%:月投倍数，逗号分隔）', 'smart_add'),

    # 退出信号（2026-07-17 新增）— 止盈/止损/暂停，默认关闭
    ('smart_add.exit_signal_enabled', 'true', '退出信号开关（止盈/止损/暂停）', 'smart_add'),
    ('smart_add.take_profit_broad_pct', '20', '宽基止盈阈值%（盈利超过此值建议减仓）', 'smart_add'),
    ('smart_add.take_profit_theme_pct', '30', '主题/行业止盈阈值%（盈利超过此值建议减仓）', 'smart_add'),
    ('smart_add.stop_loss_pct', '-30', '止损阈值%（亏损超过此值建议止损）', 'smart_add'),
    ('smart_add.stop_loss_valuation_pct', '50', '止损辅助条件：估值分位>此值才建议止损（低估时不建议止损）', 'smart_add'),
    ('smart_add.max_drawdown_from_peak_pct', '25', '从最高净值回撤超过此值建议止损', 'smart_add'),
    ('smart_add.max_consecutive_failed_adds', '3', '连续补仓失败次数上限（超过则暂停该标的）', 'smart_add'),

    # 价值平均法（2026-07-17 新增）— 默认关闭
    ('smart_add.va_enabled', 'true', '价值平均法引擎开关（替代DCA的市值驱动策略）', 'smart_add'),
    ('smart_add.va_target_growth_pct', '0.33', 'VA目标月增长%（默认=base_dca_pct/12≈0.33%）', 'smart_add'),
    ('smart_add.va_max_monthly_mult', '3.0', 'VA单月最大投入倍数（防止极端行情超额投入）', 'smart_add'),
    ('smart_add.va_allow_sell', 'false', 'VA是否允许卖出建议（默认false，保守）', 'smart_add'),

    # 网格交易（2026-07-17 新增）— 默认关闭
    ('smart_add.grid_enabled', 'true', '网格交易策略开关（估值合理区间30-70%时启用）', 'smart_add'),
    ('smart_add.grid_count', '5', '网格数量', 'smart_add'),
    ('smart_add.grid_range_pct', '20', '网格区间%（±20%）', 'smart_add'),

    # 基本面健康检查（2026-07-17 新增）— 默认关闭
    ('smart_add.fund_health_enabled', 'true', '基本面健康检查开关（经理变更/规模暴增/跟踪误差）', 'smart_add'),

    # ── Batch1 增强点 1：关注计划退出机制（2026-07-18，默认关闭） ──
    ('watchlist.exit_signal_enabled', 'false', '关注计划退出机制开关：true时巡检计算止盈/止损信号', 'watchlist'),
    ('watchlist.default_target_profit_pct', '30', '默认止盈百分比（用户未设时使用）', 'watchlist'),
    ('watchlist.default_stop_loss_pct', '10', '默认止损百分比（用户未设时使用）', 'watchlist'),

    # ── Batch1 增强点 2：异常波动预警（2026-07-18，默认关闭） ──
    ('watchlist.volatility_alert_enabled', 'false', '异常波动预警开关：true时巡检计算日/周涨跌幅及预警级别', 'watchlist'),
    ('watchlist.volatility_severe_daily_threshold', '-3.0', '日跌触发 severe 的阈值（百分比，-3.0 表示 -3%）', 'watchlist'),
    ('watchlist.volatility_severe_weekly_threshold', '-6.0', '周跌触发 severe 的阈值', 'watchlist'),
    ('watchlist.volatility_warning_daily_threshold', '-1.5', '日跌触发 warning 的阈值', 'watchlist'),
    ('watchlist.volatility_warning_weekly_threshold', '-3.0', '周跌触发 warning 的阈值', 'watchlist'),

    # ── Batch1 增强点 3：事件影响量化（2026-07-18，默认关闭） ──
    ('alerts.event_impact_quantification_enabled', 'false', '事件影响量化开关：true时LLM提取阶段输出影响幅度/方向/持续期', 'alerts'),
    ('alerts.event_impact_analysis_enabled', 'false', '事件深度解读开关：true时允许手动触发LLM个性化影响分析', 'alerts'),
    ('alerts.event_impact_analysis_cache_days', '7', '深度解读缓存天数', 'alerts'),

    # ── Batch2 增强点 1：关注计划自动剔除已上车（2026-07-19，默认关闭） ──
    ('watchlist.auto_mark_bought_enabled', 'false', '关注计划自动剔除已上车开关：portfolio买入时自动同步watchlist状态', 'watchlist'),

    # ── Batch2 增强点 2：事件影响金额估算（2026-07-19，默认关闭） ──
    ('alerts.event_impact_amount_enabled', 'false', '事件影响金额估算开关：实时计算事件对用户持仓的金额影响', 'alerts'),

    # ── Batch2 增强点 3：事件置信度时间衰减（2026-07-19，默认关闭） ──
    ('alerts.event_confidence_time_decay_enabled', 'false', '事件置信度时间衰减开关：未验证的过期事件自动降权', 'alerts'),

    # ── O-6/O-7/O-8（2026-07-21）：机会雷达与事件雷达剩余优化项开关 ──
    # O-6: 方向关键词推断（neutral 事件基于正/负向关键词推断 positive/negative）
    ('alerts.event_direction_infer_enabled', 'true', '事件方向关键词推断开关：neutral 事件基于关键词推断方向（默认开启）', 'alerts'),
    # O-7: 置信度动态调整 v2（在板块准确率基础上叠加 sources/matched_holdings/时效性）
    ('alerts.confidence_dynamic_adjust_enabled', 'true', '置信度动态调整 v2 开关：叠加 sources 数量/持仓命中/时效性三维度（默认开启）', 'alerts'),
    # O-8: 启动时一键 backfill 历史数据（sources/impact/direction/confidence/opportunity/watchlist）
    ('alerts.auto_backfill_on_startup_enabled', 'true', '启动时一键 backfill 开关：启动时回补历史事件/机会/watchlist 数据（默认开启）', 'alerts'),
    # O-4: watchlist current_percentile fallback（无 index_code 时从 fund_metadata 或净值回撤估算）
    ('watchlist.fallback_percentile_enabled', 'true', 'watchlist 分位 fallback 开关：无 index_code 时从 tracking_index 或净值回撤估算（默认开启）', 'watchlist'),

    # ── P2-F（2026-07-21）P0/P1 开关注册到 system_config（前端配置面板可管理） ──
    ('watchlist.multidim_signal_enabled', 'true', '多维信号接入开关（技术/资金/情绪三维）', 'watchlist'),
    ('watchlist.multidim_signal_timeout_seconds', '5', '多维信号获取超时秒数', 'watchlist'),
    ('watchlist.signal_expiry_enabled', 'true', '信号有效期开关（green 5/10 天自动降级）', 'watchlist'),
    ('watchlist.moving_profit_target_enabled', 'true', '移动止盈开关（pnl>=20% 后回撤 5%）', 'watchlist'),
    ('watchlist.breakeven_stop_loss_enabled', 'true', '保本止损开关（pnl>=10% 后回落至 5%）', 'watchlist'),
    ('watchlist.time_stop_loss_enabled', 'true', '时间止损开关（持有 30 天 + pnl<3%）', 'watchlist'),
    ('watchlist.composite_rule_enabled', 'true', '复合规则开关（双重看空降级 red）', 'watchlist'),
    ('watchlist.three_way_resonance_enabled', 'true', '三重共振开关（同向三维升级/降级）', 'watchlist'),
    ('watchlist.history_hitrate_feedback_enabled', 'true', '历史命中率反哺 confidence 开关', 'watchlist'),
    ('watchlist.review_date_precision_enabled', 'true', 'review_date 交易日精度开关', 'watchlist'),
    ('watchlist.global_indicator_cache_ttl_minutes', '30', '全局指标缓存 TTL（分钟）', 'watchlist'),
    ('watchlist.yingmi_diagnosis_enabled', 'false', '盈米基金诊断开关', 'watchlist'),

    # ── P2-A（2026-07-21）回测闭环深化：自适应阈值 ──
    ('watchlist.adaptive_threshold_enabled', 'true', '自适应阈值开关：根据历史命中率动态调整 target_percentile', 'watchlist'),
    ('watchlist.adaptive_min_samples', '5', '自适应阈值最小样本量（reviewed < 此值不调整）', 'watchlist'),

    # ── P2-B（2026-07-21）多维信号+宏观扩展 ──
    ('watchlist.macro_signal_enabled', 'true', '宏观信号维度开关（LPR/SHIBOR/美债/汇率/政策）', 'watchlist'),

    # ── P2-C（2026-07-21）组合层信号共振 ──
    ('watchlist.resonance_detection_enabled', 'true', '共振检测开关：多标的同向共振 + 持仓系统性风险预警', 'watchlist'),
    ('watchlist.resonance_strong_threshold', '3', '强共振基金数阈值（green/退出 >= 此值触发 strong）', 'watchlist'),
    ('watchlist.resonance_ratio_threshold', '0.3', '强共振比例阈值（green/退出占总数 >= 此值触发 strong）', 'watchlist'),
    ('watchlist.resonance_bearish_count_threshold', '3', '强看空共振基金数阈值（red >= 此值触发 strong_bearish）', 'watchlist'),
    ('watchlist.resonance_bearish_ratio_threshold', '0.5', '强看空共振比例阈值（red 占总数 >= 此值触发 strong_bearish）', 'watchlist'),
    ('watchlist.systemic_loss_threshold', '-10.0', '持仓系统性风险：严重亏损阈值（profit_rate <= 此值视为严重亏损，单位 %）', 'watchlist'),
    ('watchlist.systemic_drop_threshold', '-3.0', '持仓系统性风险：今日大跌阈值（today_change_pct <= 此值视为大跌，单位 %）', 'watchlist'),
    ('watchlist.systemic_ratio_threshold', '0.4', '持仓系统性风险：触发比例阈值（严重亏损或今日大跌持仓数 / 总持仓 >= 此值则系统性风险=True）', 'watchlist'),
    ('watchlist.systemic_max_display', '5', '持仓系统性风险：triggered_holdings 最大返回数量', 'watchlist'),

    # ── P2-D（2026-07-21）信号变更主动通知 ──
    ('watchlist.signal_change_alert_enabled', 'true', '信号变更 alert 开关：信号灯变更时生成 alert', 'watchlist'),
    ('watchlist.signal_change_sse_enabled', 'true', '信号变更 SSE 推送开关：信号灯变更时实时推送到前端', 'watchlist'),
]


def init_default_configs(conn=None):
    """初始化默认配置（仅写入不存在的 key）。"""
    own_conn = conn is None
    if own_conn:
        conn = _get_conn()
    for key, value, description, category in DEFAULT_CONFIGS:
        try:
            conn.execute(
                "INSERT OR IGNORE INTO system_config (key, value, description, category) VALUES (?, ?, ?, ?)",
                (key, value, description, category)
            )
        except Exception:
            pass
    if own_conn:
        conn.commit()
        conn.close()


def get_config(key: str, default: str = '') -> str:
    """获取单个配置值（带 60s 进程级缓存）。

    2026-07-13 性能优化：dashboard 单次请求可能调用 170+ 次 get_config，
    每次开关 SQLite 连接 + 2 条 PRAGMA，是主要耗时来源。配置极少变更，缓存 60 秒。
    update_config / reset_configs 会自动清缓存。
    """
    now = _time.monotonic()
    cached = _CONFIG_CACHE.get(key)
    if cached is not None:
        val, ts = cached
        if (now - ts) < _CONFIG_CACHE_TTL:
            return val
    conn = _get_conn()
    try:
        row = conn.execute("SELECT value FROM system_config WHERE key = ?", (key,)).fetchone()
        val = row[0] if row else default
    finally:
        conn.close()
    _CONFIG_CACHE[key] = (val, now)
    return val


def get_config_int(key: str, default: int = 0) -> int:
    """获取整数配置值。"""
    val = get_config(key, str(default))
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def get_config_float(key: str, default: float = 0.0) -> float:
    """获取浮点数配置值。"""
    val = get_config(key, str(default))
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def get_config_bool(key: str, default: bool = False) -> bool:
    """获取布尔配置值。

    接受字符串：'true'/'1'/'yes'/'on'（不区分大小写）→ True，其余 → False。
    """
    val = get_config(key, str(default).lower())
    if not val:
        return default
    return str(val).strip().lower() in ("true", "1", "yes", "on")


def get_config_list(key: str, default: list[str] | None = None) -> list[str]:
    """获取逗号分隔的列表配置值。"""
    val = get_config(key, '')
    if not val:
        return default or []
    return [item.strip() for item in val.split(',') if item.strip()]


def list_configs(category: str = None) -> list[dict]:
    """列出所有配置（可按 category 过滤）。"""
    conn = _get_conn()
    try:
        if category:
            rows = conn.execute(
                "SELECT key, value, description, category, updated_at FROM system_config WHERE category = ? ORDER BY key",
                (category,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT key, value, description, category, updated_at FROM system_config ORDER BY category, key"
            ).fetchall()
        return [
            {"key": r[0], "value": r[1], "description": r[2], "category": r[3], "updated_at": r[4]}
            for r in rows
        ]
    finally:
        conn.close()


def update_config(key: str, value: str) -> bool:
    """更新配置值。返回是否成功。"""
    conn = _get_conn()
    try:
        cursor = conn.execute(
            "UPDATE system_config SET value = ?, updated_at = datetime('now','localtime') WHERE key = ?",
            (value, key)
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()
        # 清除缓存（无论是否更新成功，确保下次读取拿到最新值）
        _CONFIG_CACHE.pop(key, None)


def reset_configs() -> int:
    """重置所有配置为默认值。返回重置数量。"""
    count = 0
    for key, value, _, _ in DEFAULT_CONFIGS:
        if update_config(key, value):
            count += 1
    # 清除全部缓存
    _CONFIG_CACHE.clear()
    return count
