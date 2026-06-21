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
    ('llm.temperature_analysis', '0.3', '分析任务温度', 'llm'),
    ('llm.temperature_vision', '0.1', '图片分析温度', 'llm'),
    ('llm.max_tokens_report', '8192', '报告最大token', 'llm'),
    ('llm.max_tokens_chat', '8000', '对话最大token', 'llm'),
    ('llm.max_tokens_analysis', '8000', '分析最大token', 'llm'),

    # 内容截断限制
    ('truncation.article_content', '8000', '文章内容截断长度', 'llm'),
    ('truncation.context', '3000', '上下文截断长度', 'llm'),
    ('truncation.rag_context', '4000', 'RAG上下文截断长度', 'llm'),
    ('truncation.history_messages', '20', '历史消息保留条数', 'llm'),
    ('truncation.tool_result', '4000', '工具结果截断长度', 'llm'),

    # 补仓跌幅预警
    ('alert.buy_drop_pct', '4', '补仓后跌幅预警阈值（%）', 'alert'),
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
