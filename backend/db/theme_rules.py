"""主题规则 CRUD — theme_rules 表操作。

O-2（2026-07-22）：将 opportunity_engine.py 硬编码的 THEME_RULES 配置化，
支持后台动态增删改，DB 优先 + 硬编码兜底。

表结构：
- theme: 主题名称（UNIQUE）
- keywords_json / policy_terms_json / funds_json: JSON 数组
- future_direction: 未来方向说明
- index_code: 对应指数代码（O-3 技术指标/资金流向查询用）
- sector: 所属板块（O-3 板块级资金流向查询用，对齐 event_radar.SECTOR_TO_INDEX）
- active: 1=启用 0=停用
- priority: 排序优先级（越小越靠前）
"""

import json
import logging
from datetime import datetime

from db._conn import _get_conn

logger = logging.getLogger(__name__)


def init_theme_rules_tables(conn):
    """初始化 theme_rules 表 + 首次启动时种子数据。"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS theme_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            theme TEXT NOT NULL UNIQUE,
            keywords_json TEXT DEFAULT '[]',
            policy_terms_json TEXT DEFAULT '[]',
            funds_json TEXT DEFAULT '[]',
            future_direction TEXT DEFAULT '',
            index_code TEXT DEFAULT '',
            sector TEXT DEFAULT '',
            active INTEGER DEFAULT 1,
            priority INTEGER DEFAULT 100,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_theme_rules_active ON theme_rules(active, priority)"
    )

    # 首次启动：若表为空，从硬编码种子数据初始化
    count = conn.execute("SELECT COUNT(*) FROM theme_rules").fetchone()[0]
    if count == 0:
        _seed_theme_rules(conn)


def _seed_theme_rules(conn):
    """首次启动时从硬编码 THEME_RULES 种子化（与 opportunity_engine.py 保持一致）。"""
    # 种子数据：与 opportunity_engine.py 的 THEME_RULES 完全对齐
    # 同时补齐 index_code（来自 _THEME_INDEX_CODES）和 sector（来自 event_radar.SECTOR_TO_INDEX）
    seed_rules = [
        {
            "theme": "红利低波",
            "keywords": ["红利", "高股息", "分红", "中特估", "低波"],
            "policy_terms": ["政策", "新国九条", "分红", "央企", "市值管理"],
            "future_direction": "低利率和重视股东回报环境下，高股息资产具备中期配置关注度。",
            "funds": [
                {
                    "fund_code": "009051",
                    "fund_name": "易方达中证红利ETF联接发起式A",
                    "index_name": "中证红利",
                    "vehicle_type": "otc_fund",
                    "short_term_suitable": False,
                },
            ],
            "index_code": "H30269",
            "sector": "",  # 红利低波无直接对应 SECTOR_TO_INDEX 板块
            "priority": 10,
        },
        {
            "theme": "人工智能",
            "keywords": ["AI", "人工智能", "大模型", "算力", "数据中心"],
            "policy_terms": ["新质生产力", "人工智能", "算力", "数字经济"],
            "future_direction": "AI 应用、算力基础设施和国产替代仍是中长期产业方向。",
            "funds": [
                {
                    "fund_code": "159819",
                    "fund_name": "人工智能ETF",
                    "index_name": "人工智能",
                    "vehicle_type": "etf",
                    "short_term_suitable": True,
                },
            ],
            "index_code": "931071.CSI",
            "sector": "人工智能",
            "priority": 20,
        },
        {
            "theme": "半导体",
            "keywords": ["半导体", "芯片", "集成电路", "晶圆", "封测"],
            "policy_terms": ["自主可控", "国产替代", "半导体", "科技"],
            "future_direction": "国产替代和先进制造政策支持下，半导体方向具备高弹性但波动较大。",
            "funds": [
                {
                    "fund_code": "159995",
                    "fund_name": "芯片ETF",
                    "index_name": "芯片",
                    "vehicle_type": "etf",
                    "short_term_suitable": True,
                },
            ],
            "index_code": "H30184",
            "sector": "半导体",
            "priority": 30,
        },
        {
            "theme": "机器人",
            "keywords": ["机器人", "人形机器人", "自动化", "智能制造"],
            "policy_terms": ["机器人", "智能制造", "新质生产力"],
            "future_direction": "机器人处在产业化验证阶段，政策和新品催化会带来阶段性交易机会。",
            "funds": [
                {
                    "fund_code": "562500",
                    "fund_name": "机器人ETF",
                    "index_name": "机器人",
                    "vehicle_type": "etf",
                    "short_term_suitable": True,
                },
            ],
            "index_code": "H30590",
            "sector": "机器人",
            "priority": 40,
        },
        {
            "theme": "新能源",
            "keywords": ["新能源", "光伏", "储能", "锂电", "电池"],
            "policy_terms": ["新能源", "储能", "碳中和", "设备更新"],
            "future_direction": "新能源长期方向明确，但短线需要确认供需改善和价格企稳。",
            "funds": [
                {
                    "fund_code": "516160",
                    "fund_name": "新能源ETF",
                    "index_name": "新能源",
                    "vehicle_type": "etf",
                    "short_term_suitable": True,
                },
            ],
            "index_code": "399808",
            "sector": "新能源",
            "priority": 50,
        },
    ]

    for rule in seed_rules:
        conn.execute("""
            INSERT INTO theme_rules
                (theme, keywords_json, policy_terms_json, funds_json, future_direction,
                 index_code, sector, active, priority)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
        """, (
            rule["theme"],
            json.dumps(rule["keywords"], ensure_ascii=False),
            json.dumps(rule["policy_terms"], ensure_ascii=False),
            json.dumps(rule["funds"], ensure_ascii=False),
            rule["future_direction"],
            rule["index_code"],
            rule["sector"],
            rule["priority"],
        ))
    conn.commit()
    logger.info(f"[theme_rules] 种子数据初始化完成：{len(seed_rules)} 条")


def _row_to_rule(row) -> dict:
    """数据库行 → 主题规则 dict（与 opportunity_engine.THEME_RULES 格式对齐）。"""
    if row is None:
        return None
    item = dict(row)
    item["keywords"] = json.loads(item.pop("keywords_json") or "[]")
    item["policy_terms"] = json.loads(item.pop("policy_terms_json") or "[]")
    item["funds"] = json.loads(item.pop("funds_json") or "[]")
    item["active"] = bool(item.get("active", 1))
    return item


def list_theme_rules(active_only: bool = True) -> list[dict]:
    """列出主题规则。"""
    conn = _get_conn()
    try:
        if active_only:
            rows = conn.execute(
                "SELECT * FROM theme_rules WHERE active = 1 ORDER BY priority ASC, id ASC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM theme_rules ORDER BY active DESC, priority ASC, id ASC"
            ).fetchall()
    finally:
        conn.close()
    return [_row_to_rule(r) for r in rows]


def get_theme_rule(theme: str) -> dict | None:
    """按主题名查询（不区分 active）。"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM theme_rules WHERE theme = ?", (theme,)
        ).fetchone()
    finally:
        conn.close()
    return _row_to_rule(row) if row else None


def create_theme_rule(
    theme: str,
    keywords: list[str] | None = None,
    policy_terms: list[str] | None = None,
    funds: list[dict] | None = None,
    future_direction: str = "",
    index_code: str = "",
    sector: str = "",
    priority: int = 100,
) -> int:
    """新增主题规则，返回 id。冲突时抛 ValueError。"""
    conn = _get_conn()
    try:
        cur = conn.execute("""
            INSERT INTO theme_rules
                (theme, keywords_json, policy_terms_json, funds_json, future_direction,
                 index_code, sector, active, priority)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
        """, (
            theme,
            json.dumps(keywords or [], ensure_ascii=False),
            json.dumps(policy_terms or [], ensure_ascii=False),
            json.dumps(funds or [], ensure_ascii=False),
            future_direction,
            index_code,
            sector,
            priority,
        ))
        conn.commit()
        return cur.lastrowid
    except Exception as e:
        if "UNIQUE" in str(e):
            raise ValueError(f"主题 '{theme}' 已存在")
        raise
    finally:
        conn.close()


def update_theme_rule(theme: str, **fields) -> bool:
    """更新主题规则。支持 fields：keywords/policy_terms/funds/future_direction/
    index_code/sector/active/priority。"""
    if not fields:
        return False

    # JSON 字段自动序列化
    json_fields = {"keywords", "policy_terms", "funds"}
    json_columns = {"keywords": "keywords_json", "policy_terms": "policy_terms_json", "funds": "funds_json"}

    set_parts = []
    values = []
    for k, v in fields.items():
        if k in json_fields:
            set_parts.append(f"{json_columns[k]} = ?")
            values.append(json.dumps(v or [], ensure_ascii=False))
        elif k in {"future_direction", "index_code", "sector", "priority", "active"}:
            set_parts.append(f"{k} = ?")
            values.append(v)
        # 忽略未知字段

    if not set_parts:
        return False

    set_parts.append("updated_at = datetime('now','localtime')")
    values.append(theme)

    conn = _get_conn()
    try:
        cur = conn.execute(
            f"UPDATE theme_rules SET {', '.join(set_parts)} WHERE theme = ?",
            values,
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_theme_rule(theme: str, soft: bool = True) -> bool:
    """删除主题规则。soft=True 时软删除（active=0），否则物理删除。"""
    conn = _get_conn()
    try:
        if soft:
            cur = conn.execute(
                "UPDATE theme_rules SET active = 0, updated_at = datetime('now','localtime') WHERE theme = ?",
                (theme,),
            )
        else:
            cur = conn.execute("DELETE FROM theme_rules WHERE theme = ?", (theme,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_theme_index_code(theme: str) -> str:
    """便捷查询：按主题名取 index_code。"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT index_code FROM theme_rules WHERE theme = ? AND active = 1",
            (theme,),
        ).fetchone()
    finally:
        conn.close()
    return row["index_code"] if row else ""
