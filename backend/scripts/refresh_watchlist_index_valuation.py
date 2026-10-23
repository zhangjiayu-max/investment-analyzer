#!/usr/bin/env python3
"""刷新关注列表（watchlist）中非宽基指数的估值分位。

背景
----
`POST /api/valuations/fetch-recent` 只覆盖 6 个 A 股宽基指数
（沪深300 / 中证500 / 中证1000 / 创业板指 / 上证50 / 国证2000）；
港股、行业主题等指数（如 930792 中证香港银行投资）不在自动刷新范围内，
导致 watchlist 的 `current_percentile` 长期为空或陈旧，买入时机评分的
valuation 维度会退化成"估值数据缺失"（中性 50 分）。

本脚本补这条链路：

    在线查询（akshare → 天天基金）
      → save_valuation 落库 index_valuations
      → 回写 watchlist.current_percentile

用法
----
    # 预演，只看会做什么（默认）
    python scripts/refresh_watchlist_index_valuation.py

    # 实际写入
    python scripts/refresh_watchlist_index_valuation.py --apply

    # 只处理指定基金
    python scripts/refresh_watchlist_index_valuation.py --apply --fund-code 010365

说明
----
- 幂等：同 index_code + snapshot_date + metric_type 会 UPSERT，重复跑无副作用
- 单位契约：写入时显式声明 percentile_unit="percent"，
  换算统一在 db.valuations._normalize_percentile 完成，不做数值猜测
- 分位窗口（近10年 / 近5年）写入 raw_json._valuation_contract，便于事后审计
- 只动 watchlist 中 index_code 非空的记录，不触碰其他数据
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from db._conn import _get_conn  # noqa: E402
from db.valuations import save_valuation  # noqa: E402


def list_watchlist_index_targets(fund_code: str | None = None) -> list[dict]:
    """取出关注列表中带 index_code 的标的。"""
    conn = _get_conn()
    try:
        sql = """
            SELECT id, fund_code, fund_name, index_code, index_name,
                   current_nav, current_percentile, target_percentile
            FROM watchlist
            WHERE status = 'watching'
              AND index_code IS NOT NULL AND TRIM(index_code) <> ''
        """
        params: tuple = ()
        if fund_code:
            sql += " AND fund_code = ?"
            params = (fund_code,)
        sql += " ORDER BY id"
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def query_online(index_code: str, metric_type: str = "市盈率") -> dict | None:
    """走系统既有的在线兜底链路（与 /api/valuation/online-query 同一实现）。"""
    from db.valuations import _online_fallback

    return _online_fallback(index_code, metric_type, datetime.now(), "watchlist_refresh_script")


def write_percentile_back(item_id: int, percentile: float) -> None:
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE watchlist SET current_percentile = ?, updated_at = datetime('now','localtime') WHERE id = ?",
            (percentile, item_id),
        )
        conn.commit()
    finally:
        conn.close()


def load_existing_raw(index_code: str, metric_type: str, snap: str) -> dict:
    """读已有 raw_json，新 meta 合并时保留历史补充说明，避免覆盖丢信息。"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT raw_json FROM index_valuations "
            "WHERE index_code = ? AND snapshot_date = ? AND metric_type = ?",
            (index_code, snap, metric_type),
        ).fetchone()
    finally:
        conn.close()
    if not row or not row[0]:
        return {}
    try:
        existing = json.loads(row[0])
        return existing if isinstance(existing, dict) else {}
    except (ValueError, TypeError):
        return {}


def main() -> int:
    ap = argparse.ArgumentParser(description="刷新关注列表指数的估值分位")
    ap.add_argument("--apply", action="store_true", help="实际写入数据库（默认仅预演）")
    ap.add_argument("--fund-code", default=None, help="只处理指定基金代码")
    ap.add_argument("--metric-type", default="市盈率", help="估值指标类型，默认市盈率")
    args = ap.parse_args()

    targets = list_watchlist_index_targets(args.fund_code)
    if not targets:
        print("没有需要处理的标的（watchlist 中 index_code 均为空）。")
        return 0

    print(f"待处理 {len(targets)} 条，指标={args.metric_type}，模式={'写入' if args.apply else '预演'}\n")

    ok = skipped = failed = 0
    for item in targets:
        code = item["index_code"].strip()
        label = f"{item['fund_code']} {item['fund_name'][:22]} [{code}]"
        old = item.get("current_percentile")

        try:
            data = query_online(code, args.metric_type)
        except Exception as e:  # 网络/数据源异常不应中断整批
            print(f"  查询异常 {label}: {type(e).__name__} {e}")
            failed += 1
            continue

        if not data or data.get("percentile") is None:
            print(f"  无在线数据 {label}（保持原值 {old}）")
            skipped += 1
            continue

        pct = data["percentile"]
        snap = data.get("snapshot_date") or datetime.now().strftime("%Y-%m-%d")
        print(
            f"  {label}\n"
            f"    在线: PE={data.get('current_value')} 分位={pct} "
            f"窗口={data.get('percentile_window') or '未标注'} 日期={snap}"
        )
        print(f"    本地: 分位 {old} → {pct}")

        if not args.apply:
            ok += 1
            continue

        window = data.get("percentile_window")
        meta = {
            "source": data.get("source") or data.get("data_source"),
            "query_at": datetime.now().isoformat(timespec="seconds"),
            "percentile_window": window,
            "refreshed_by": "refresh_watchlist_index_valuation.py",
        }
        if not window:
            meta["window_note"] = "在线 L2 缓存未携带分位窗口元数据，窗口口径以数据源页面为准"
        raw_meta = {**load_existing_raw(code, args.metric_type, snap), **meta}

        payload = {
            "index_code": code,
            "index_name": item.get("index_name") or data.get("index_name") or code,
            "metric_type": args.metric_type,
            "current_value": data.get("current_value"),
            "current_point": data.get("current_point"),
            "percentile": pct,
            "percentile_unit": "percent",
            "percentile_window": window,
            "history_window": window,
            "roe": data.get("roe"),
            "raw_json": json.dumps(raw_meta, ensure_ascii=False),
        }
        try:
            vid = save_valuation(payload, snapshot_date=snap)
            write_percentile_back(item["id"], pct)
            print(f"    写入 OK: index_valuations.id={vid}")
            ok += 1
        except Exception as e:
            print(f"    写入失败: {type(e).__name__} {e}")
            failed += 1

    mode = "已写入" if args.apply else "预演通过"
    print(f"\n汇总：{mode} {ok} 条，跳过 {skipped} 条，失败 {failed} 条")
    if not args.apply and ok:
        print("加 --apply 参数执行实际写入。")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
