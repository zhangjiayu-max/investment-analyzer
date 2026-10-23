"""修复 fetch_recent_valuations 写入的「比例制当百分制」脏数据。

背景（2026-10-22 定位）：
    `/api/valuations/fetch-recent`（app.py）把 `get_index_valuation().pe_percentile`
    直接写进 index_valuations.percentile。该字段是 0-1 比例
    （market_data.py:263 文档 + 消费端 watchlist.py 的 ×100 可证），
    而 save_valuation 在未声明 percentile_unit 时按 percent 落库，
    于是「47% 分位」被存成 0.47，导致宽基估值分位被缩小 100 倍，
    进而污染每日建议、机会筛选与再平衡结论。

写入端已在 app.py 声明 percentile_unit='ratio' 并加了越界守卫。
本脚本负责修复历史脏数据，遵循项目「禁止按数值猜尺度」的治理原则——
只处理同时满足以下全部证据条件的记录：

    1. raw_json._valuation_contract.percentile_input_unit == 'percent'
       （写入端没有声明 ratio，属于契约缺省）
    2. percentile_raw == percentile（值原样透传，没有任何换算）
    3. 0 < percentile < 1（落进比例制区间）
    4. 该记录是「无来源的计算值」：source_image 与 source_url 均为空，
       且契约里 metric_verified / history_window 均为空。
       图片解析入库的记录必定带 source_image 或 source_url，
       且会声明历史窗口，据此可与计算值区分。
    5. 同一 (index_code, metric_type) 组内存在 percentile >= 1.5 的历史记录
       （证明该组其余数据用的是百分制，本组不是「真实小于 1%」的口径）

条件 4 + 5 是排他证据：恒生科技市销率的 0.31 虽然也小于 1，
但它来自图片源（history_window=10Y、metric_verified=true、带 source_image），
是真实的 0.31% 分位，不在修复范围内。

用法：
    cd backend && python3 -m scripts.fix_fetch_recent_percentile_ratio            # 预览
    cd backend && python3 -m scripts.fix_fetch_recent_percentile_ratio --apply    # 执行
    cd backend && python3 -m scripts.fix_fetch_recent_percentile_ratio --apply --backup data/backups/xxx.db
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db._conn import _get_conn  # noqa: E402

# 同组历史出现 >= 该值，说明该组用的是百分制，而不是「真实不足 1%」
_PERCENT_EVIDENCE_THRESHOLD = 1.5
# 组内至少要有这么多条记录，才有资格用历史口径作证据
_MIN_GROUP_ROWS = 5

REPAIR_SOURCE = (
    "fetch-recent 单位契约修复：get_index_valuation().pe_percentile 为 0-1 比例"
    "（market_data.py 文档 + watchlist.py 消费端 ×100），"
    "写入时未声明 percentile_unit 被按 percent 落库"
)


def _contract_of(raw_json: str | None) -> dict:
    try:
        raw = json.loads(raw_json or "{}")
    except (TypeError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    contract = raw.get("_valuation_contract")
    return contract if isinstance(contract, dict) else {}


def find_candidates(conn: sqlite3.Connection) -> tuple[list[dict], list[dict]]:
    """返回 (可修复记录, 因证据不足而跳过的记录)。"""
    rows = conn.execute(
        "SELECT id, index_code, index_name, metric_type, snapshot_date, "
        "       current_value, percentile, raw_json, created_at, source_image, source_url "
        "  FROM index_valuations "
        " WHERE percentile IS NOT NULL AND percentile > 0 AND percentile < 1 "
        " ORDER BY index_code, metric_type, snapshot_date"
    ).fetchall()

    group_cache: dict[tuple[str, str], tuple[int, float | None]] = {}
    fixable, skipped = [], []
    for row in rows:
        row = dict(row)
        key = (row["index_code"], row["metric_type"])
        if key not in group_cache:
            stat = conn.execute(
                "SELECT COUNT(*), MAX(percentile) FROM index_valuations "
                " WHERE index_code=? AND metric_type=? AND percentile IS NOT NULL",
                key,
            ).fetchone()
            group_cache[key] = (stat[0] or 0, stat[1])
        group_count, group_max = group_cache[key]

        contract = _contract_of(row.get("raw_json"))
        input_unit = contract.get("percentile_input_unit")
        raw_value = contract.get("percentile_raw")

        reasons = []
        if input_unit != "percent":
            # 已显式声明 ratio（新版写入端）或来源不明 → 不是本 bug 的受害者
            reasons.append(f"输入单位不是契约缺省的 percent（{input_unit!r}）")
        if raw_value is None or float(raw_value) != float(row["percentile"]):
            reasons.append(f"原值与落库值不等（raw={raw_value!r}）")
        # 排他证据：图片/来源解析入库的记录带 source_image 或 source_url，
        # 且会声明历史窗口；无来源的计算值才可能是 fetch-recent 写的。
        has_origin = bool(row.get("source_image")) or bool(row.get("source_url"))
        if has_origin:
            reasons.append("带 source_image/source_url，属于有来源的解析数据，不做换算")
        elif contract.get("metric_verified") is not None or contract.get("history_window"):
            reasons.append("契约声明了 metric_verified/history_window，来自有窗口的解析源")
        if group_count < _MIN_GROUP_ROWS:
            reasons.append(f"组内记录仅 {group_count} 条，不足以证明百分制口径")
        elif group_max is None or group_max < _PERCENT_EVIDENCE_THRESHOLD:
            reasons.append(f"组内历史最大分位仅 {group_max}，可能是真实低分位口径")
        if reasons:
            skipped.append({**row, "reasons": reasons})
            continue

        fixable.append({
            "id": row["id"],
            "index_code": row["index_code"],
            "index_name": row["index_name"],
            "metric_type": row["metric_type"],
            "snapshot_date": row["snapshot_date"],
            "current_value": row["current_value"],
            "before": row["percentile"],
            "after": round(float(row["percentile"]) * 100, 4),
            "raw_json": row.get("raw_json"),
            "created_at": row.get("created_at"),
            "group_max_percentile": group_max,
        })
    return fixable, skipped


def _patched_raw_json(raw_json: str | None, before: float) -> str:
    """把契约里的输入单位纠正为 ratio，保留原始值作为审计痕迹。"""
    try:
        raw = json.loads(raw_json or "{}")
    except (TypeError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {"raw_response": raw}
    contract = raw.get("_valuation_contract")
    contract = dict(contract) if isinstance(contract, dict) else {}
    contract.update({
        "percentile_unit": "percent",
        "percentile_input_unit": "ratio",
        "percentile_raw": before,
        "repaired_from_ratio": True,
        "repair_source": REPAIR_SOURCE,
        "repaired_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    raw["_valuation_contract"] = contract
    return json.dumps(raw, ensure_ascii=False)


def apply_fixes(conn: sqlite3.Connection, candidates: list[dict], backup_path: Path) -> dict:
    if backup_path.exists():
        raise ValueError(f"备份路径已存在，拒绝覆盖: {backup_path}")
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(backup_path) as backup:
        conn.backup(backup)

    updated = 0
    if conn.in_transaction:
        conn.rollback()
    conn.execute("BEGIN IMMEDIATE")
    try:
        for item in candidates:
            cursor = conn.execute(
                "UPDATE index_valuations SET percentile=?, raw_json=? "
                " WHERE id=? AND percentile=?",
                (item["after"], _patched_raw_json(item.get("raw_json"), item["before"]),
                 item["id"], item["before"]),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"记录在审核后发生变化，已回滚: id={item['id']}")
            updated += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"updated": updated, "backup": str(backup_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description="修复 fetch-recent 比例制分位脏数据")
    parser.add_argument("--apply", action="store_true", help="执行修复（默认仅预览）")
    parser.add_argument("--backup", type=Path, default=None, help="提交前全库备份路径")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = parser.parse_args()

    conn = _get_conn()
    try:
        candidates, skipped = find_candidates(conn)
        if args.json:
            payload = {
                "mode": "apply" if args.apply else "dry-run",
                "candidates": candidates,
                "skipped": [{k: v for k, v in s.items() if k != "raw_json"} for s in skipped],
            }
            if args.apply and candidates:
                backup = args.backup or Path(
                    "data/backups/valuations-percentile-%s.db" % datetime.now().strftime("%Y%m%d%H%M%S")
                )
                payload.update(apply_fixes(conn, candidates, backup))
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0

        print(f"扫描到 {len(candidates)} 条比例制脏数据，{len(skipped)} 条因证据不足跳过")
        for item in candidates:
            print(f"  id={item['id']:5d} {item['index_code']:8s} {item['metric_type']:4s} "
                  f"{item['snapshot_date']} 值={item['current_value']} "
                  f"分位 {item['before']} -> {item['after']}")
        if skipped:
            print("\n跳过（证据不足，不做值推断）：")
            for item in skipped:
                print(f"  id={item['id']:5d} {item['index_code']:8s} {item['metric_type']:4s} "
                      f"分位={item['percentile']} | {'; '.join(item['reasons'])}")
        if not candidates:
            print("\n无需修复")
            return 0
        if not args.apply:
            print("\n[dry-run] 未写入。加 --apply 执行（会先自动备份）")
            return 0
        backup = args.backup or Path(
            "data/backups/valuations-percentile-%s.db" % datetime.now().strftime("%Y%m%d%H%M%S")
        )
        result = apply_fixes(conn, candidates, backup)
        print(f"\n修复完成：{result['updated']} 条，备份 {result['backup']}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
