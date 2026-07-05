"""P2-4.1: 修复 index_valuations.percentile 字段格式，统一为 float。

设计稿：2026-07-05-预警与建议逻辑增强.md §4.1
背景：percentile 字段历史存在三种格式：
    - float（13.89）
    - 字符串（"99.22%"）
    - None / 空字符串
导致 dca_add 评分中估值维度比较失效（如 "13.89%" > 35 误判为高估）。

修复规则：把字符串格式的 percentile 转为 float，None/空保持 None。

用法：
    cd backend && python3 -m scripts.fix_percentile_format

可选参数：
    --dry-run    仅打印待修复记录，不执行 UPDATE
"""
import sqlite3
import sys
from pathlib import Path


def parse_percentile(val):
    """把 percentile 字段统一解析为 float 或 None。"""
    if val is None or val == "":
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        try:
            return float(val.replace('%', '').strip())
        except ValueError:
            return None
    return None


def main(dry_run: bool = False):
    db_path = Path(__file__).parent.parent.parent / "data" / "valuations.db"
    if not db_path.exists():
        print(f"数据库不存在: {db_path}")
        return 1

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        # 查询所有非空 percentile
        rows = conn.execute(
            "SELECT id, index_code, snapshot_date, percentile FROM index_valuations "
            "WHERE percentile IS NOT NULL ORDER BY id"
        ).fetchall()

        total = len(rows)
        if total == 0:
            print("无 percentile 数据，无需修复")
            return 0

        # 分类统计
        already_float = 0
        to_fix = []
        unparseable = []

        for r in rows:
            raw = r["percentile"]
            parsed = parse_percentile(raw)
            if parsed is None:
                unparseable.append(r)
            elif isinstance(raw, (int, float)):
                already_float += 1
            elif isinstance(raw, str):
                to_fix.append((r, parsed))

        print(f"扫描 {total} 条记录：")
        print(f"  - 已是 float 类型: {already_float} 条")
        print(f"  - 字符串待修复:    {len(to_fix)} 条")
        print(f"  - 无法解析:        {len(unparseable)} 条")

        # 打印无法解析的样本
        if unparseable:
            print("\n无法解析的记录（前 5 条）:")
            for r in unparseable[:5]:
                print(f"  id={r['id']} code={r['index_code']} date={r['snapshot_date']} "
                      f"percentile={r['percentile']!r}")

        if not to_fix:
            print("\n无需修复，所有 percentile 已是 float 类型")
            return 0

        # 打印待修复样本
        print(f"\n待修复记录（前 5 条）:")
        for r, parsed in to_fix[:5]:
            print(f"  id={r['id']} code={r['index_code']} date={r['snapshot_date']} "
                  f"'{r['percentile']}' -> {parsed}")

        if dry_run:
            print(f"\n[dry-run] 未执行 UPDATE，共 {len(to_fix)} 条待修复")
            return 0

        # 执行修复
        fixed = 0
        for r, parsed in to_fix:
            conn.execute(
                "UPDATE index_valuations SET percentile = ? WHERE id = ?",
                (parsed, r["id"]),
            )
            fixed += 1

        conn.commit()
        print(f"\n修复完成：成功更新 {fixed} 条记录")

        # 验证
        str_remaining = conn.execute(
            "SELECT COUNT(*) as cnt FROM index_valuations "
            "WHERE typeof(percentile) = 'text'"
        ).fetchone()["cnt"]
        print(f"验证：剩余字符串格式 percentile {str_remaining} 条")

        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    sys.exit(main(dry_run=dry))
