"""P1-3.2: 清理 daily_advice_signal 历史重复预警。

设计稿：2026-07-05-预警与建议逻辑增强.md §3.2
背景：2026-06-25 同一天为同一基金生成 6 次 daily_advice_signal 预警，
      因为旧代码每次运行 daily_advice 都会写入 portfolio_alerts。
      当前代码（daily_position_advisor.py:176-177）已注释掉写入逻辑，
      但历史数据需要清理。

清理规则：同一基金同一天的 daily_advice_signal 仅保留最新一条（id 最大）。

用法：
    cd backend && python -m scripts.cleanup_daily_advice_alerts
"""
import sqlite3
from pathlib import Path
from collections import defaultdict


def main():
    db_path = Path(__file__).parent.parent.parent / "data" / "valuations.db"
    if not db_path.exists():
        print(f"数据库不存在: {db_path}")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        # 查询所有 daily_advice_signal 预警
        rows = conn.execute("""
            SELECT id, related_fund_code, date(created_at) as alert_date, created_at
            FROM portfolio_alerts
            WHERE alert_type = 'daily_advice_signal'
            ORDER BY related_fund_code, alert_date, id
        """).fetchall()

        total = len(rows)
        if total == 0:
            print("无 daily_advice_signal 预警，无需清理")
            return

        # 按 fund_code + date 分组，每组只保留最后一条（id 最大）
        groups: dict[str, list[int]] = defaultdict(list)
        for r in rows:
            key = f"{r['related_fund_code']}:{r['alert_date']}"
            groups[key].append(r['id'])

        to_delete: list[int] = []
        for key, ids in groups.items():
            if len(ids) > 1:
                # 保留最后一条（id 最大，因为已按 id 排序）
                to_delete.extend(ids[:-1])

        if not to_delete:
            print(f"共 {total} 条 daily_advice_signal 预警，无重复数据")
            return

        # 执行删除
        placeholders = ",".join(["?"] * len(to_delete))
        conn.execute(
            f"DELETE FROM portfolio_alerts WHERE id IN ({placeholders})",
            to_delete,
        )
        conn.commit()

        # 统计
        remaining = total - len(to_delete)
        print(f"清理完成：删除 {len(to_delete)} 条重复预警，保留 {remaining} 条")
        print(f"清理前: {total} 条，清理后: {remaining} 条")

        # 输出分组详情
        print("\n分组详情：")
        for key, ids in sorted(groups.items()):
            if len(ids) > 1:
                print(f"  {key}: 原有 {len(ids)} 条，删除 {len(ids) - 1} 条，保留 id={ids[-1]}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
