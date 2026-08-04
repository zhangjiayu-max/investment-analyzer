"""审计并迁移历史 0-1 比例制估值百分位。"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db._conn import _get_conn


_TARGETS = {
    "index_valuations": "percentile",
    "smart_add_plans": "valuation_percentile",
    "theme_opportunities": "valuation_percentile",
    "theme_opportunity_backtests": "entry_percentile",
}


def migrate_percentile_scale(conn, apply: bool = False) -> dict:
    """返回迁移审计结果；只有 apply=True 时更新数据。

    每张表独立事务：某张表异常不会影响其他表的迁移进度。
    """
    result = {}
    for table, column in _TARGETS.items():
        count = conn.execute(
            f"SELECT COUNT(*) AS n FROM {table} WHERE {column} > 0 AND {column} < 1"
        ).fetchone()["n"]
        result[table] = {"candidates": count, "updated": 0, "error": None}

    if not apply:
        return result

    for table, column in _TARGETS.items():
        if result[table]["candidates"] == 0:
            continue
        conn.execute("BEGIN")
        try:
            cursor = conn.execute(
                f"UPDATE {table} SET {column} = ROUND({column} * 100, 4) "
                f"WHERE {column} > 0 AND {column} < 1"
            )
            result[table]["updated"] = cursor.rowcount
            conn.commit()
        except Exception as exc:  # pragma: no cover - 保留错误信息便于运维排查
            conn.rollback()
            result[table]["error"] = str(exc)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="提交迁移；默认仅审计")
    args = parser.parse_args()

    conn = _get_conn()
    try:
        result = migrate_percentile_scale(conn, apply=args.apply)
    finally:
        conn.close()
    print(json.dumps({"mode": "apply" if args.apply else "dry-run", **result}, ensure_ascii=False))


if __name__ == "__main__":
    main()
