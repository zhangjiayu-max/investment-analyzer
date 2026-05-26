"""估值数据保存 — 接收 AI 识别结果，存入 SQLite

用法:
    # 从 JSON 文件保存（自动从 manifest 提取文章日期）
    python save_valuation.py --manifest /path/to/manifest.json --data /path/to/analysis.json

    # 从 stdin 保存
    echo '{"index_code":"931468.CSI",...}' | python save_valuation.py --manifest /path/to/manifest.json

    # 指定日期覆盖
    python save_valuation.py --data /path/to/analysis.json --date 2026-05-23

    # 批量：扫描目录下所有 manifest.json
    python save_valuation.py --scan /path/to/data/images/
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, date
from pathlib import Path


# 数据库路径：优先环境变量 > 当前目录/data/valuations.db
DB_PATH = Path(os.environ.get("VALUATION_DB_PATH", "./data/valuations.db"))


def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """建表。"""
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS index_valuations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            index_code TEXT NOT NULL,
            index_name TEXT,
            snapshot_date TEXT NOT NULL,
            current_point REAL,
            change_pct REAL,
            metric_type TEXT NOT NULL DEFAULT '市盈率',
            current_value REAL,
            percentile REAL,
            danger_value REAL,
            median REAL,
            opportunity_value REAL,
            max_value REAL,
            min_value REAL,
            avg_value REAL,
            zscore REAL,
            source_image TEXT,
            source_url TEXT,
            raw_json TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(index_code, snapshot_date, metric_type)
        )
    """)

    # 兼容旧表：新增列（已存在则忽略）
    _add_column_if_not_exists(conn, "index_valuations", "metric_type", "TEXT")
    _add_column_if_not_exists(conn, "index_valuations", "current_value", "REAL")
    _add_column_if_not_exists(conn, "index_valuations", "percentile", "REAL")
    _add_column_if_not_exists(conn, "index_valuations", "danger_value", "REAL")
    _add_column_if_not_exists(conn, "index_valuations", "median", "REAL")
    _add_column_if_not_exists(conn, "index_valuations", "opportunity_value", "REAL")
    _add_column_if_not_exists(conn, "index_valuations", "max_value", "REAL")
    _add_column_if_not_exists(conn, "index_valuations", "min_value", "REAL")
    _add_column_if_not_exists(conn, "index_valuations", "avg_value", "REAL")
    _add_column_if_not_exists(conn, "index_valuations", "zscore", "REAL")
    conn.commit()
    conn.close()


def _add_column_if_not_exists(conn: sqlite3.Connection, table: str, column: str, col_type: str):
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
    except sqlite3.OperationalError:
        pass


# ── 字段映射（统一 schema）────────────────────────────

VALUE_ALIASES = {
    "当前值": "current_value", "PE-TTM": "current_value", "PE": "current_value",
    "市盈率": "current_value", "pe_ttm": "current_value",
    "PB": "current_value", "市净率": "current_value", "pb": "current_value",
    "分位点": "percentile", "分位": "percentile", "百分位": "percentile",
    "pe_percentile": "percentile", "pb_percentile": "percentile",
    "危险值": "danger_value", "pe_danger": "danger_value", "pb_danger": "danger_value",
    "中位数": "median", "pe_median": "median", "pb_median": "median",
    "机会值": "opportunity_value", "pe_opportunity": "opportunity_value",
    "pb_opportunity": "opportunity_value",
    "最大值": "max_value", "pe_max": "max_value", "pb_max": "max_value",
    "最小值": "min_value", "pe_min": "min_value", "pb_min": "min_value",
    "平均值": "avg_value", "均值": "avg_value",
    "pe_avg": "avg_value", "pb_avg": "avg_value",
    "z分数": "zscore", "Z分数": "zscore",
    "pe_zscore": "zscore", "pb_zscore": "zscore",
    "当前点位": "current_point", "指数点位": "current_point",
    "current_point": "current_point",
    "涨跌幅": "change_pct", "change_pct": "change_pct",
}

# 指标子对象 key → metric_type 映射（从哪个子对象提取就认为是什么类型）
METRIC_SECTION_MAP = {
    "市盈率TTM统计指标": "市盈率",
    "pe_metrics": "市盈率",
    "市净率统计指标": "市净率",
    "pb_metrics": "市净率",
    "市销率统计指标": "市销率",
    "市现率统计指标": "市现率",
    "股息率统计指标": "股息率",
    "风险溢价统计指标": "风险溢价",
}


def _to_float(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        v = v.replace("%", "").replace(",", "").strip()
        try:
            return float(v)
        except ValueError:
            return None
    return None


def _detect_metric_type(raw: dict) -> str:
    """从数据中检测指标类型。优先级：市销率 > 市净率 > 股息率 > 风险溢价 > 市盈率。"""
    mt = raw.get("metric_type") or raw.get("数据类型") or ""
    if "市现率" in mt:
        return "市现率"
    if "市销率" in mt:
        return "市销率"
    if "市净率" in mt or "PB" in mt.upper():
        return "市净率"
    if "股息率" in mt:
        return "股息率"
    if "风险溢价" in mt:
        return "风险溢价"
    if "市盈率" in mt or "PE" in mt.upper():
        return "市盈率"
    # 看子对象：哪个有数据
    sections = ["市现率统计指标", "市销率统计指标", "市净率统计指标",
                 "股息率统计指标", "风险溢价统计指标", "市盈率TTM统计指标"]
    for section_key in sections:
        section = raw.get(section_key, {})
        if section and isinstance(section, dict) and section.get("当前值") is not None:
            return METRIC_SECTION_MAP.get(section_key, "市盈率")
    return "市盈率"


def normalize_data(raw: dict) -> dict:
    """将 AI 输出的各种格式统一映射为新 schema 的 DB 字段。"""
    result = {}

    # 基础字段
    result["index_code"] = raw.get("index_code", raw.get("指数代码", "UNKNOWN"))
    result["index_name"] = raw.get("index_name", raw.get("指数名称", "未知指数"))
    result["current_point"] = _to_float(raw.get("current_point", raw.get("当前点位")))
    result["change_pct"] = _to_float(raw.get("change_pct", raw.get("涨跌幅")))
    result["metric_type"] = _detect_metric_type(raw)

    # 找 metrics 子对象（按优先级查找）
    metrics = (raw.get("市现率统计指标") or raw.get("市销率统计指标")
               or raw.get("市净率统计指标") or raw.get("股息率统计指标")
               or raw.get("风险溢价统计指标") or raw.get("市盈率TTM统计指标")
               or raw.get("metrics", {}))

    # 从 metrics 子对象提取值
    for key, value in metrics.items():
        db_field = VALUE_ALIASES.get(key)
        if db_field and value is not None and db_field not in result:
            result[db_field] = _to_float(value)

    # 顶层直接匹配
    for key, value in raw.items():
        db_field = VALUE_ALIASES.get(key)
        if db_field and value is not None and db_field not in result:
            result[db_field] = _to_float(value)

    return result


def save_one(data: dict, snapshot_date: str = None, source_image: str = None,
             source_url: str = None) -> int:
    """保存一条估值数据，返回 id。同指数同日期同类型会更新。"""
    if not snapshot_date:
        snapshot_date = date.today().isoformat()

    index_code = data.get("index_code", "UNKNOWN")
    index_name = data.get("index_name", "未知指数")
    metric_type = data.get("metric_type", "市盈率")

    conn = _get_conn()
    cur = conn.execute("""
        INSERT INTO index_valuations (
            index_code, index_name, snapshot_date,
            current_point, change_pct, metric_type,
            current_value, percentile, danger_value, median,
            opportunity_value, max_value, min_value, avg_value, zscore,
            source_image, source_url, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(index_code, snapshot_date, metric_type) DO UPDATE SET
            index_name=excluded.index_name,
            current_point=excluded.current_point,
            change_pct=excluded.change_pct,
            current_value=excluded.current_value,
            percentile=excluded.percentile,
            danger_value=excluded.danger_value,
            median=excluded.median,
            opportunity_value=excluded.opportunity_value,
            max_value=excluded.max_value,
            min_value=excluded.min_value,
            avg_value=excluded.avg_value,
            zscore=excluded.zscore,
            source_image=excluded.source_image,
            source_url=excluded.source_url,
            raw_json=excluded.raw_json,
            created_at=datetime('now','localtime')
    """, (
        index_code, index_name, snapshot_date,
        data.get("current_point"), data.get("change_pct"), metric_type,
        data.get("current_value"), data.get("percentile"),
        data.get("danger_value"), data.get("median"),
        data.get("opportunity_value"), data.get("max_value"),
        data.get("min_value"), data.get("avg_value"), data.get("zscore"),
        source_image, source_url, json.dumps(data, ensure_ascii=False),
    ))
    if cur.lastrowid:
        vid = cur.lastrowid
    else:
        vid = conn.execute(
            "SELECT id FROM index_valuations WHERE index_code=? AND snapshot_date=? AND metric_type=?",
            (index_code, snapshot_date, metric_type)
        ).fetchone()[0]
    conn.commit()
    conn.close()
    return vid


def load_manifest_date(manifest_path: str) -> tuple[str | None, str | None]:
    """从 manifest.json 读取 article_date 和 url。"""
    if not manifest_path or not os.path.exists(manifest_path):
        return None, None
    with open(manifest_path, "r", encoding="utf-8") as f:
        m = json.load(f)
    return m.get("article_date"), m.get("url")


def mark_image_analyzed(manifest_path: str, image_path: str):
    """在 manifest 中标记某图片已分析。"""
    if not manifest_path or not os.path.exists(manifest_path):
        return
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    for img in manifest.get("images", []):
        lp = img.get("local_path", "")
        if lp == image_path or os.path.basename(lp) == os.path.basename(image_path):
            img["analyzed"] = True
            break
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


# ── 主流程 ──────────────────────────────────────────


def process_single(data: dict, manifest_path: str = None, snapshot_date: str = None) -> int:
    """处理单条 AI 识别结果。返回入库 id。"""
    # 日期优先级：命令行参数 > 数据自带 > manifest 中的文章日期 > 今天
    if not snapshot_date:
        snapshot_date = data.get("snapshot_date") or data.get("日期")
    if not snapshot_date and manifest_path:
        md, _ = load_manifest_date(manifest_path)
        snapshot_date = md

    normalized = normalize_data(data)

    source_image = data.get("source_image", "")
    source_url = ""
    if manifest_path:
        _, source_url = load_manifest_date(manifest_path)

    vid = save_one(normalized, snapshot_date=snapshot_date,
                   source_image=source_image, source_url=source_url)

    # 更新 manifest 中的 analyzed 标记
    if source_image and manifest_path:
        mark_image_analyzed(manifest_path, source_image)

    return vid


def process_json_file(data_path: str, manifest_path: str = None, snapshot_date: str = None) -> int:
    """处理一个 JSON 文件。支持单条 dict 或 list[dict]。"""
    with open(data_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    if isinstance(raw, list):
        count = 0
        for item in raw:
            vid = process_single(item, manifest_path, snapshot_date)
            count += 1
            print(f"  入库: {item.get('index_code', item.get('指数代码', '?'))} (id={vid})")
        return count
    else:
        vid = process_single(raw, manifest_path, snapshot_date)
        print(f"  入库: {raw.get('index_code', raw.get('指数代码', '?'))} (id={vid})")
        return 1


def process_stdin(manifest_path: str = None, snapshot_date: str = None) -> int:
    """从 stdin 读取 JSON 数据。"""
    raw = json.load(sys.stdin)
    if isinstance(raw, list):
        count = 0
        for item in raw:
            vid = process_single(item, manifest_path, snapshot_date)
            count += 1
            print(f"  入库: {item.get('index_code', item.get('指数代码', '?'))} (id={vid})")
        return count
    else:
        vid = process_single(raw, manifest_path, snapshot_date)
        return 1


def main():
    parser = argparse.ArgumentParser(description="保存 AI 识别的估值数据到 SQLite")
    parser.add_argument("--manifest", help="manifest.json 路径（用于提取文章日期和更新 analyzed 标记）")
    parser.add_argument("--data", help="AI 输出的 JSON 文件路径")
    parser.add_argument("--date", help="指定数据日期 (YYYY-MM-DD)，覆盖自动提取")
    parser.add_argument("--scan", help="扫描目录下所有 manifest.json，从中读取已分析的数据")
    args = parser.parse_args()

    init_db()

    if args.scan:
        # 批量扫描模式
        total = 0
        for root, dirs, files in os.walk(args.scan):
            if "manifest.json" in files:
                mp = os.path.join(root, "manifest.json")
                with open(mp, "r", encoding="utf-8") as f:
                    manifest = json.load(f)
                for img in manifest.get("images", []):
                    if img.get("analyzed") and img.get("parsed_data"):
                        vid = process_single(img["parsed_data"], mp, args.date)
                        total += 1
        print(f"\n共入库 {total} 条")
        return

    if args.data:
        count = process_json_file(args.data, args.manifest, args.date)
    elif not sys.stdin.isatty():
        count = process_stdin(args.manifest, args.date)
    else:
        parser.print_help()
        sys.exit(1)

    print(f"\n共入库 {count} 条估值记录")
    print(f"数据库: {DB_PATH}")


if __name__ == "__main__":
    main()
