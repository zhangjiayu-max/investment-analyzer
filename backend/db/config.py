"""系统配置 CRUD — system_config 表操作"""

from db._conn import _get_conn

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
    ('cost_routing.conservative_model', 'deepseek-v4-flash', '保守模式统一模型', 'cost_routing'),
    ('cost_routing.orchestrator_model', 'deepseek-v4-pro', '编排器模型', 'cost_routing'),
    ('cost_routing.arbitrator_model', 'deepseek-v4-pro', '仲裁模型', 'cost_routing'),
    ('cost_routing.valuation_expert_model', 'deepseek-v4-flash', '估值专家模型', 'cost_routing'),
    ('cost_routing.allocation_advisor_model', 'deepseek-v4-flash', '配置专家模型', 'cost_routing'),
    ('cost_routing.fund_analyst_model', 'deepseek-v4-flash', '基金分析模型', 'cost_routing'),
    ('cost_routing.risk_assessor_model', 'deepseek-v4-pro', '风控专家模型', 'cost_routing'),
    ('cost_routing.market_analyst_model', 'deepseek-v4-pro', '市场分析模型', 'cost_routing'),
    ('cost_routing.behavioral_coach_model', 'deepseek-v4-pro', '行为辅导模型', 'cost_routing'),
    ('cost_routing.cross_review_model', 'deepseek-v4-flash', '交叉审阅模型', 'cost_routing'),

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
    """获取单个配置值。"""
    conn = _get_conn()
    row = conn.execute("SELECT value FROM system_config WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


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


def get_config_list(key: str, default: list[str] | None = None) -> list[str]:
    """获取逗号分隔的列表配置值。"""
    val = get_config(key, '')
    if not val:
        return default or []
    return [item.strip() for item in val.split(',') if item.strip()]


def list_configs(category: str = None) -> list[dict]:
    """列出所有配置（可按 category 过滤）。"""
    conn = _get_conn()
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


def update_config(key: str, value: str) -> bool:
    """更新配置值。返回是否成功。"""
    conn = _get_conn()
    cursor = conn.execute(
        "UPDATE system_config SET value = ?, updated_at = datetime('now','localtime') WHERE key = ?",
        (value, key)
    )
    conn.commit()
    return cursor.rowcount > 0


def reset_configs() -> int:
    """重置所有配置为默认值。返回重置数量。"""
    count = 0
    for key, value, _, _ in DEFAULT_CONFIGS:
        if update_config(key, value):
            count += 1
    return count
