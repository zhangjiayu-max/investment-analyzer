"""估值数据查询 — 从 SQLite 查询指数估值历史"""

import json
import os
import re
import sqlite3
import sys
from pathlib import Path


# 数据库路径：优先环境变量 > 当前目录/data/valuations.db > 当前目录/data/tasks.db
_db_env = os.environ.get("VALUATION_DB_PATH")
DB_CANDIDATES = [
    Path(_db_env) if _db_env else Path("./data/valuations.db"),
    Path("./data/tasks.db"),
]


def find_db() -> str:
    """找到第一个存在的数据库。"""
    for db in DB_CANDIDATES:
        if db.exists():
            return str(db)
    return str(DB_CANDIDATES[0])


def _get_conn(db_path: str = None) -> sqlite3.Connection:
    if not db_path:
        db_path = find_db()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def list_indexes(db_path: str = None) -> list[dict]:
    """列出所有有估值数据的指数。"""
    conn = _get_conn(db_path)
    # 尝试新 schema（有 metric_type）
    try:
        rows = conn.execute("""
            SELECT index_code, index_name, metric_type,
                   MAX(snapshot_date) as latest_date,
                   MIN(snapshot_date) as earliest_date, COUNT(*) as record_count
            FROM index_valuations
            GROUP BY index_code, COALESCE(metric_type, '市盈率')
            ORDER BY latest_date DESC, index_code
        """).fetchall()
    except sqlite3.OperationalError:
        # 旧 schema
        rows = conn.execute("""
            SELECT index_code, index_name, MAX(snapshot_date) as latest_date,
                   MIN(snapshot_date) as earliest_date, COUNT(*) as record_count
            FROM index_valuations
            GROUP BY index_code
            ORDER BY latest_date DESC
        """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def search_indexes(keyword: str, db_path: str = None) -> list[dict]:
    """按名称或代码模糊搜索指数。支持中文名、代码片段。"""
    conn = _get_conn(db_path)
    like = f"%{keyword}%"
    try:
        rows = conn.execute("""
            SELECT index_code, index_name, metric_type,
                   MAX(snapshot_date) as latest_date,
                   MIN(snapshot_date) as earliest_date, COUNT(*) as record_count
            FROM index_valuations
            WHERE index_code LIKE ? OR index_name LIKE ?
            GROUP BY index_code, COALESCE(metric_type, '市盈率')
            ORDER BY latest_date DESC
        """, (like, like)).fetchall()
    except sqlite3.OperationalError:
        rows = conn.execute("""
            SELECT index_code, index_name, MAX(snapshot_date) as latest_date,
                   MIN(snapshot_date) as earliest_date, COUNT(*) as record_count
            FROM index_valuations
            WHERE index_code LIKE ? OR index_name LIKE ?
            GROUP BY index_code
            ORDER BY latest_date DESC
        """, (like, like)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_dates(index_code: str, metric_type: str = None, db_path: str = None) -> list[str]:
    """列出某指数所有有数据的日期。可选按 metric_type 筛选。"""
    conn = _get_conn(db_path)
    if metric_type:
        rows = conn.execute("""
            SELECT DISTINCT snapshot_date FROM index_valuations
            WHERE index_code = ? AND metric_type = ?
            ORDER BY snapshot_date DESC
        """, (index_code, metric_type)).fetchall()
    else:
        rows = conn.execute("""
            SELECT DISTINCT snapshot_date FROM index_valuations
            WHERE index_code = ?
            ORDER BY snapshot_date DESC
        """, (index_code,)).fetchall()
    conn.close()
    return [r["snapshot_date"] for r in rows]


def get_history(index_code: str, days: int = 90, metric_type: str = None, db_path: str = None) -> list[dict]:
    """查询某指数最近 N 天的估值历史，按日期正序。可选按 metric_type 筛选。"""
    conn = _get_conn(db_path)
    if metric_type:
        rows = conn.execute("""
            SELECT * FROM index_valuations
            WHERE index_code = ? AND metric_type = ?
            ORDER BY snapshot_date ASC
            LIMIT ?
        """, (index_code, metric_type, days)).fetchall()
    else:
        rows = conn.execute("""
            SELECT * FROM index_valuations
            WHERE index_code = ?
            ORDER BY snapshot_date ASC
            LIMIT ?
        """, (index_code, days)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_latest(index_code: str, db_path: str = None) -> dict | None:
    """获取某指数最新一条估值。"""
    conn = _get_conn(db_path)
    row = conn.execute("""
        SELECT * FROM index_valuations
        WHERE index_code = ?
        ORDER BY snapshot_date DESC
        LIMIT 1
    """, (index_code,)).fetchone()
    conn.close()
    return dict(row) if row else None


def format_valuation(v: dict) -> str:
    """格式化一条估值数据为可读文本。支持新旧两种 schema。"""
    lines = []
    lines.append(f"指数: {v.get('index_name', '?')} ({v.get('index_code', '?')})")
    lines.append(f"日期: {v.get('snapshot_date', '?')}")

    # 兼容新旧 schema
    if v.get("metric_type"):
        lines.append(f"指标类型: {v['metric_type']}")

    # 新 schema
    current_value = v.get("current_value")
    if current_value is not None:
        lines.append(f"当前值: {current_value}")
    # 旧 schema 兼容
    elif v.get("pe_ttm") is not None:
        lines.append(f"当前值(PE): {v['pe_ttm']}")
    elif v.get("pb") is not None:
        lines.append(f"当前值(PB): {v['pb']}")

    if v.get("current_point"):
        lines.append(f"指数点位: {v['current_point']}")
    if v.get("change_pct"):
        lines.append(f"涨跌幅: {v['change_pct']}%")

    # 新 schema
    for field, label in [("percentile", "分位点"), ("danger_value", "危险值"),
                          ("median", "中位数"), ("opportunity_value", "机会值"),
                          ("max_value", "最大值"), ("min_value", "最小值"),
                          ("avg_value", "平均值"), ("zscore", "Z分数")]:
        val = v.get(field)
        if val is not None:
            lines.append(f"{label}: {val}")

    # 旧 schema 兼容（PE）
    for field, label in [("pe_percentile", "PE分位点"), ("pe_danger", "PE危险值"),
                          ("pe_median", "PE中位数"), ("pe_opportunity", "PE机会值"),
                          ("pe_max", "PE最大值"), ("pe_min", "PE最小值"),
                          ("pe_avg", "PE平均值"), ("pe_zscore", "PE-Z分数")]:
        if field in v and v[field] is not None:
            lines.append(f"{label}: {v[field]}")

    # 旧 schema 兼容（PB）
    for field, label in [("pb_percentile", "PB分位点"), ("pb_danger", "PB危险值"),
                          ("pb_median", "PB中位数"), ("pb_opportunity", "PB机会值"),
                          ("pb_max", "PB最大值"), ("pb_min", "PB最小值"),
                          ("pb_avg", "PB平均值")]:
        if field in v and v[field] is not None:
            lines.append(f"{label}: {v[field]}")

    return "\n".join(lines)


def _print_index_list(indexes: list[dict]):
    """格式化打印指数列表。"""
    if not indexes:
        print("未找到匹配的指数")
        return
    print(f"共 {len(indexes)} 个指数:\n")
    for idx in indexes:
        print(f"  {idx['index_code']:15s} {idx['index_name'] or '':10s} "
              f"{idx['earliest_date']} ~ {idx['latest_date']}  "
              f"共 {idx['record_count']} 条")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        # 列出所有指数
        indexes = list_indexes()
        if not indexes:
            print("数据库中暂无估值数据")
        else:
            print(f"共 {len(indexes)} 个指数有估值数据:\n")
            for idx in indexes:
                print(f"  {idx['index_code']:15s} {idx['index_name'] or '':10s} "
                      f"{idx['earliest_date']} ~ {idx['latest_date']}  "
                      f"共 {idx['record_count']} 条")
        print(f"\n用法:")
        print("  python query_valuation.py                 # 列出所有指数")
        print("  python query_valuation.py 红利             # 模糊搜索（中文名/代码片段）")
        print("  python query_valuation.py 931468.CSI       # 查看最新估值 + 可用日期")
        print("  python query_valuation.py 931468.CSI 30    # 查看历史记录")
        sys.exit(0)

    code = sys.argv[1]

    # 判断是模糊搜索还是精确查询
    # 精确代码格式：数字.SH / 数字.SZ / 数字.CSI 等，或纯 6 位数字
    is_code = bool(re.match(r'^\d{6}(\.\w+)?$', code))

    if not is_code:
        # 模糊搜索
        results = search_indexes(code)
        if not results:
            print(f"未找到包含「{code}」的指数")
            sys.exit(1)
        _print_index_list(results)
        sys.exit(0)

    # 精确查询
    if len(sys.argv) == 2:
        latest = get_latest(code)
        if not latest:
            # 尝试模糊搜索
            results = search_indexes(code)
            if results:
                print(f"未找到精确匹配 {code}，但找到以下相关指数:\n")
                _print_index_list(results)
            else:
                print(f"未找到 {code} 的估值数据")
            sys.exit(1)
        print("最新估值:\n")
        print(format_valuation(latest))
        dates = list_dates(code)
        if len(dates) > 1:
            print(f"\n共有 {len(dates)} 天数据: {', '.join(dates)}")
        sys.exit(0)

    days = int(sys.argv[2])
    history = get_history(code, days)
    if not history:
        print(f"未找到 {code} 的估值数据")
        sys.exit(1)

    print(f"{code} 最近 {len(history)} 条估值记录:\n")
    for v in history:
        print(format_valuation(v))
        print()
