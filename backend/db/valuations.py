"""估值数据 + 指数信息 CRUD。"""

import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import date, datetime, timedelta

from db._conn import _get_conn

logger = logging.getLogger(__name__)

# ── 在线兜底结果内存缓存 ────────────────────────────────────
_online_cache: dict[str, dict] = {}  # key: f"{index_code}:{metric_type}"


def _normalize_percentile(val) -> float | None:
    """P2-4.1: 统一百分位字段为 float 类型（0-100 区间）。

    历史数据存在多种格式，影响评分逻辑（如 dca_add 的估值维度比较）。
    案例 conv 122：akshare 返回 0.7231（0-1 小数），图片解析返回 "84.71%"（0-100），
    格式不一致导致专家看到"百分位 0.7231%"和"百分位 93%"两个矛盾数据。

    规则：
      - None / 空字符串 → None
      - int / float：
        - 0 < val < 1 → 视为小数表示，乘以 100 转百分比（0.7231 → 72.31）
        - 1 ≤ val ≤ 100 → 已是百分比，原样返回
        - val > 100 → 异常值，截断到 100
        - val < 0 → 异常值，截断到 0
      - 字符串 "13.89%" / "13.89" → 13.89（按上述 float 规则处理）
      - 无法解析 → None
    """
    if val is None or val == "":
        return None

    def _to_pct(v: float) -> float:
        """归一化到 0-100 区间。"""
        if 0 < v < 1:
            return round(v * 100, 4)
        if v > 100:
            return 100.0
        if v < 0:
            return 0.0
        return v

    if isinstance(val, (int, float)):
        return _to_pct(float(val))
    if isinstance(val, str):
        try:
            return _to_pct(float(val.replace('%', '').strip()))
        except ValueError:
            return None
    return None


def save_valuation(data: dict, source_image: str = None, source_url: str = None, snapshot_date: str = None) -> int:
    """保存估值数据，返回 id。同指数同日期同类型会更新。"""
    if not snapshot_date:
        snapshot_date = date.today().isoformat()
    if not data.get("index_code"):
        data["index_code"] = "UNKNOWN"
    if not data.get("index_name"):
        data["index_name"] = "未知指数"
    metric_type = data.get("metric_type", "市盈率")

    # P2-4.2: 保存时统一标准化指数代码，避免后缀导致重复数据
    data["index_code"] = normalize_index_code(data["index_code"])

    # P2-4.1: 写入时统一 percentile 为 float
    percentile = _normalize_percentile(data.get("percentile"))

    conn = _get_conn()
    cur = conn.execute("""
        INSERT INTO index_valuations (
            index_code, index_name, snapshot_date,
            current_point, change_pct, metric_type,
            current_value, percentile, danger_value, median,
            opportunity_value, max_value, min_value, avg_value, zscore,
            background_color, source_image, source_url, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            background_color=excluded.background_color,
            source_image=excluded.source_image,
            source_url=excluded.source_url,
            raw_json=excluded.raw_json,
            created_at=datetime('now','localtime')
    """, (
        data.get("index_code"), data.get("index_name"), snapshot_date,
        data.get("current_point"), data.get("change_pct"), metric_type,
        data.get("current_value"), percentile,
        data.get("danger_value"), data.get("median"),
        data.get("opportunity_value"), data.get("max_value"),
        data.get("min_value"), data.get("avg_value"), data.get("zscore"),
        data.get("background_color"),
        source_image, source_url, data.get("raw_json"),
    ))
    if cur.lastrowid:
        valuation_id = cur.lastrowid
    else:
        valuation_id = conn.execute(
            "SELECT id FROM index_valuations WHERE index_code=? AND snapshot_date=?",
            (data.get("index_code"), snapshot_date)
        ).fetchone()[0]
    conn.commit()
    conn.close()
    return valuation_id


def _apply_percentile_normalize(row: dict) -> dict:
    """P2-4.1: 读取容错——把 row['percentile'] 统一为 float 或 None。"""
    if row is None:
        return None
    if "percentile" in row:
        row["percentile"] = _normalize_percentile(row.get("percentile"))
    return row


def get_valuation_history(index_code: str, days: int = 30, metric_type: str = None) -> list[dict]:
    """查询某指数最近 N 天的估值历史。"""
    conn = _get_conn()
    if metric_type:
        rows = conn.execute("""
            SELECT * FROM index_valuations
            WHERE index_code = ? AND metric_type = ?
            ORDER BY snapshot_date DESC LIMIT ?
        """, (index_code, metric_type, days)).fetchall()
    else:
        rows = conn.execute("""
            SELECT * FROM index_valuations
            WHERE index_code = ?
            ORDER BY snapshot_date DESC LIMIT ?
        """, (index_code, days)).fetchall()
    conn.close()
    # P2-4.1: 读取容错，percentile 统一为 float
    return [_apply_percentile_normalize(dict(r)) for r in rows]


def get_latest_valuation(index_code: str, metric_type: str = None, max_days: int = None) -> dict | None:
    """获取某指数最新一条估值。max_days 限制只取最近 N 天内的数据。"""
    from datetime import datetime, timedelta
    conn = _get_conn()
    date_filter = ""
    if max_days:
        cutoff = (datetime.now() - timedelta(days=max_days)).strftime("%Y-%m-%d")
        date_filter = f" AND snapshot_date >= '{cutoff}'"
    if metric_type:
        row = conn.execute(f"""
            SELECT * FROM index_valuations
            WHERE index_code = ? AND metric_type = ?{date_filter}
            ORDER BY snapshot_date DESC LIMIT 1
        """, (index_code, metric_type)).fetchone()
    else:
        row = conn.execute(f"""
            SELECT * FROM index_valuations
            WHERE index_code = ?{date_filter}
            ORDER BY snapshot_date DESC LIMIT 1
        """, (index_code,)).fetchone()
    conn.close()
    # P2-4.1: 读取容错
    return _apply_percentile_normalize(dict(row)) if row else None


def list_valuation_indexes() -> list[dict]:
    """列出所有有估值数据的指数，按 metric_type 分别显示。

    按标准化后的指数代码去重，避免 000922 和 000922.CSI 被当作不同指数。
    """
    conn = _get_conn()
    rows = conn.execute("""
        SELECT iv.index_code, iv.index_name, iv.metric_type,
               iv.current_value, iv.percentile,
               iv.snapshot_date as latest_date,
               cnt.record_count
        FROM index_valuations iv
        INNER JOIN (
            SELECT index_code, metric_type, MAX(snapshot_date) as max_date
            FROM index_valuations
            GROUP BY index_code, metric_type
        ) latest ON iv.index_code = latest.index_code
                  AND iv.metric_type = latest.metric_type
                  AND iv.snapshot_date = latest.max_date
        INNER JOIN (
            SELECT index_code, metric_type, COUNT(*) as record_count
            FROM index_valuations
            GROUP BY index_code, metric_type
        ) cnt ON iv.index_code = cnt.index_code AND iv.metric_type = cnt.metric_type
        ORDER BY iv.index_code, iv.metric_type
    """).fetchall()
    conn.close()

    # 按标准化后的指数代码 + metric_type 去重，保留最新数据
    seen = {}
    for r in rows:
        d = _apply_percentile_normalize(dict(r))
        norm_code = normalize_index_code(d["index_code"])
        key = (norm_code, d["metric_type"])

        if key not in seen or d["latest_date"] > seen[key]["latest_date"]:
            seen[key] = d

    return list(seen.values())


def list_index_freshness() -> list[dict]:
    """列出所有指数的最新数据日期和距今天数。

    按标准化后的指数代码分组，合并同指数不同后缀的记录，
    避免 000922 和 000922.CSI 被当作不同指数。
    """
    conn = _get_conn()
    today = date.today().isoformat()

    rows = conn.execute("""
        SELECT index_code, index_name, MAX(snapshot_date) as latest_date,
               (julianday(?) - julianday(MAX(snapshot_date))) as stale_days
        FROM index_valuations
        GROUP BY index_code, index_name
        ORDER BY stale_days DESC
    """, (today,)).fetchall()
    conn.close()

    result = []
    seen_codes = {}

    for row in rows:
        r = dict(row)
        norm_code = normalize_index_code(r["index_code"])

        if norm_code in seen_codes:
            existing = seen_codes[norm_code]
            if r["latest_date"] > existing["latest_date"]:
                existing["index_code"] = r["index_code"]
                existing["index_name"] = r["index_name"]
                existing["latest_date"] = r["latest_date"]
                existing["stale_days"] = r["stale_days"]
        else:
            seen_codes[norm_code] = r
            result.append(r)

    result.sort(key=lambda x: x["stale_days"], reverse=True)
    return result


def search_indexes_by_keyword(keyword: str) -> list[dict]:
    """按关键词模糊匹配指数名称或代码。"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT DISTINCT index_code, index_name
        FROM index_valuations
        WHERE index_name LIKE ? OR index_code LIKE ?
        ORDER BY index_code
    """, (f"%{keyword}%", f"%{keyword}%")).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_index_info(index_code: str) -> dict | None:
    """查询指数信息缓存。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM index_info WHERE index_code = ?", (index_code,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def save_index_info(index_code: str, index_name: str, info: str) -> int:
    """保存指数信息（UPSERT）。"""
    conn = _get_conn()
    conn.execute("""
        INSERT INTO index_info (index_code, index_name, info)
        VALUES (?, ?, ?)
        ON CONFLICT(index_code) DO UPDATE SET
            index_name = excluded.index_name,
            info = excluded.info,
            created_at = datetime('now','localtime')
    """, (index_code, index_name, info))
    conn.commit()
    row = conn.execute(
        "SELECT id FROM index_info WHERE index_code = ?", (index_code,)
    ).fetchone()
    conn.close()
    return row["id"] if row else 0


def get_valuation_by_image(image_path: str) -> dict | None:
    """按图片路径查找估值数据。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM index_valuations WHERE source_image = ? ORDER BY created_at DESC LIMIT 1",
        (image_path,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ══════════════════════════════════════════════════════
# 螺丝钉估值 CRUD
# ══════════════════════════════════════════════════════

import json as _json


def save_dd_valuation(data: dict, image_path: str, image_url: str = None) -> int:
    """保存螺丝钉估值解析结果，返回 id。同一图片会更新。"""
    conn = _get_conn()
    conn.execute("""
        INSERT INTO dd_valuations (image_path, image_url, update_date, market_temperature, index_count, raw_json)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(image_path) DO UPDATE SET
            image_url=excluded.image_url,
            update_date=excluded.update_date,
            market_temperature=excluded.market_temperature,
            index_count=excluded.index_count,
            raw_json=excluded.raw_json,
            created_at=datetime('now','localtime')
    """, (
        image_path, image_url,
        data.get("update_date"),
        data.get("market_temperature"),
        data.get("count", len(data.get("data", []))),
        _json.dumps(data, ensure_ascii=False),
    ))
    row = conn.execute("SELECT id FROM dd_valuations WHERE image_path = ?", (image_path,)).fetchone()
    conn.commit()
    conn.close()
    return row["id"] if row else 0


def list_dd_valuations() -> list[dict]:
    """列出所有螺丝钉估值记录（按时间倒序）。"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, image_path, image_url, update_date, market_temperature, index_count, created_at FROM dd_valuations ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_dd_valuation(dd_id: int) -> dict | None:
    """获取单条螺丝钉估值记录详情（含完整指数列表）。"""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM dd_valuations WHERE id = ?", (dd_id,)).fetchone()
    conn.close()
    if not row:
        return None
    result = dict(row)
    if result.get("raw_json"):
        try:
            result["parsed_data"] = _json.loads(result["raw_json"])
        except Exception:
            result["parsed_data"] = None
    return result


def get_dd_parsed_image_paths() -> set[str]:
    """获取所有已解析的 DD 图片路径集合。"""
    conn = _get_conn()
    rows = conn.execute("SELECT image_path FROM dd_valuations").fetchall()
    conn.close()
    return {row["image_path"] for row in rows}


# ── 指数代码映射 CRUD ──────────────────────────────────────

def save_index_code_mapping(index_code: str, index_name: str, aliases: list = None, sina_code: str = None):
    """保存指数代码映射。"""
    conn = _get_conn()
    conn.execute("""
        INSERT INTO index_code_mapping (index_code, index_name, aliases, sina_code)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(index_code) DO UPDATE SET
            index_name=excluded.index_name,
            aliases=excluded.aliases,
            sina_code=excluded.sina_code
    """, (index_code, index_name, _json.dumps(aliases or [], ensure_ascii=False), sina_code))
    conn.commit()
    conn.close()


def get_index_code_mapping(index_code: str) -> dict | None:
    """获取指数代码映射。"""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM index_code_mapping WHERE index_code = ?", (index_code,)).fetchone()
    conn.close()
    if row:
        result = dict(row)
        if result.get("aliases"):
            try:
                result["aliases"] = _json.loads(result["aliases"])
            except Exception:
                result["aliases"] = []
        return result
    return None


def list_index_code_mappings() -> list[dict]:
    """列出所有指数代码映射。"""
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM index_code_mapping ORDER BY index_code").fetchall()
    conn.close()
    result = []
    for row in rows:
        d = dict(row)
        if d.get("aliases"):
            try:
                d["aliases"] = _json.loads(d["aliases"])
            except Exception:
                d["aliases"] = []
        result.append(d)
    return result


def normalize_index_code(index_code: str, index_name: str = None) -> str:
    """标准化指数代码（去除后缀，统一格式）。"""
    if not index_code:
        return index_code

    code = index_code.strip()

    # 处理代码和后缀之间有空格的情况（如 N225 GI -> N225）
    # 匹配模式：代码 + 空格 + 后缀
    for suffix in ["SH", "SZ", "CSI", "WI", "GI", "HI", "MI", "CNI"]:
        if code.upper().endswith(" " + suffix.upper()):
            code = code[:-(len(suffix) + 1)]
            break

    # 去除常见后缀（带点号）
    for suffix in [".SH", ".SZ", ".CSI", ".WI", ".GI", ".HI", ".MI", ".CNI"]:
        if code.upper().endswith(suffix.upper()):
            code = code[:-len(suffix)]
            break

    # 去除多余空格
    code = code.replace(" ", "")

    # 补齐 6 位（前面补 0）
    if code.isdigit() and len(code) < 6:
        code = code.zfill(6)

    # 统一为大写（处理 h30533 和 H30533）
    code = code.upper()

    return code


# ── 螺丝钉数据查询（支持按指数代码）──────────────────────────────

def get_latest_dd_valuation_for_index(index_code: str, metric_type: str = "市盈率", max_days: int = 30) -> dict | None:
    """获取螺丝钉估值中某个指数的最新数据。

    参数:
        index_code: 指数代码
        metric_type: 指标类型（市盈率/市净率）
        max_days: 最大有效天数

    返回:
        包含估值数据的字典，或 None
    """
    conn = _get_conn()

    # 标准化指数代码
    normalized_code = normalize_index_code(index_code)

    # 查询最近的螺丝钉记录
    rows = conn.execute("""
        SELECT * FROM dd_valuations
        WHERE update_date IS NOT NULL
        ORDER BY update_date DESC
        LIMIT 10
    """).fetchall()
    conn.close()

    from datetime import datetime, timedelta
    cutoff_date = (datetime.now() - timedelta(days=max_days)).strftime("%Y-%m-%d")

    for row in rows:
        dd = dict(row)
        if dd.get("update_date") and dd["update_date"] < cutoff_date:
            continue  # 数据过期

        if not dd.get("raw_json"):
            continue

        try:
            parsed = _json.loads(dd["raw_json"])
            if not parsed.get("ok"):
                continue

            # 在指数列表中查找匹配的指数
            for item in parsed.get("data", []):
                item_code = normalize_index_code(item.get("index_code", ""))
                item_name = item.get("index_name", "")

                # 代码匹配或名称匹配
                code_match = item_code == normalized_code
                name_match = index_name and item_name and (
                    index_name in item_name or item_name in index_name
                )

                if code_match or name_match:
                    # 根据指标类型返回数据
                    if metric_type == "市盈率" and item.get("pe") is not None:
                        return {
                            "index_code": item_code,
                            "index_name": item_name,
                            "metric_type": "市盈率",
                            "current_value": item["pe"],
                            "percentile": item.get("pe_percentile"),
                            "snapshot_date": dd["update_date"],
                            "source": "螺丝钉",
                            "dd_id": dd["id"],
                            "background_color": item.get("background_color"),
                            "valuation_status": item.get("valuation_status"),
                        }
                    elif metric_type == "市净率" and item.get("pb") is not None:
                        return {
                            "index_code": item_code,
                            "index_name": item_name,
                            "metric_type": "市净率",
                            "current_value": item["pb"],
                            "percentile": item.get("pb_percentile"),
                            "snapshot_date": dd["update_date"],
                            "source": "螺丝钉",
                            "dd_id": dd["id"],
                            "background_color": item.get("background_color"),
                            "valuation_status": item.get("valuation_status"),
                        }
        except Exception:
            continue

    return None


def get_best_valuation(
    index_code: str,
    metric_type: str = "市盈率",
    query_source: str = "unknown",
    trace_id: str = None,
    enable_online: bool = True,
    allow_metric_fallback: bool = False,
) -> dict | None:
    """获取最佳估值数据（智能降级 + 在线兜底 + 监控日志）。

    降级策略：
    1. 优先使用最近 7 天内的详细数据（雷牛牛）
    2. 降级到螺丝钉数据（最近 30 天内）
    3. 使用过期数据（365 天内，标记 is_expired）
    4. 在线兜底：akshare 中证官方 → 天天基金 MCP（受 valuation.online_fallback_enabled 开关控制）

    Args:
        index_code: 指数代码（支持带后缀格式如 000922.CSI / H30217.CSI，内部自动 normalize）
        metric_type: 指标类型（市盈率/市净率）
        query_source: 查询来源（portfolio/valuation_page/agent/chat/alert_scanner），用于监控
        trace_id: 追踪ID，用于监控
        enable_online: 是否启用在线兜底。批量调用场景（如 smart_add_planner 循环多个持仓）
                       应传 False 以避免累积超时。默认 True。
        allow_metric_fallback: 是否允许 metric_type 自动 fallback。
                              当指定 metric_type 本地查不到时，按预设顺序自动尝试其他 metric_type。
                              fallback 顺序：市盈率 → 市净率 → 市销率 → 股息率。
                              默认 False（不破坏现有调用），alert_scanner 等场景传 True。
                              返回结果中新增 fallback_metric_type 字段标注实际命中的指标。

    返回:
        包含估值数据的字典，带 data_source、is_expired、days_old 字段；
        在线兜底结果额外带 source: "akshare"/"ttfund"，不入库。
    """
    start_ts = datetime.now()
    final_source = "failed"
    degraded = 0
    is_expired = 0
    error_msg = None

    # 入口统一 normalize：去除 .CSI/.WI/.SH 等后缀，兼容持仓表带后缀的指数代码
    index_code = normalize_index_code(index_code)

    # 1. 优先使用最近 7 天内的详细数据
    detailed = get_latest_valuation(index_code, metric_type, max_days=7)
    # 修复：本地数据所有关键字段都为 null 时，不应返回空记录，应继续降级到在线兜底
    if detailed and (detailed.get("current_value") is not None or detailed.get("percentile") is not None):
        detailed["data_source"] = "manual"
        detailed["is_expired"] = False
        if detailed.get("snapshot_date"):
            try:
                days_old = (datetime.now() - datetime.fromisoformat(detailed["snapshot_date"])).days
                detailed["days_old"] = days_old
            except Exception:
                detailed["days_old"] = 0

        # R4: PE过期超过14天时，尝试PB替代
        if metric_type == "市盈率" and detailed.get("days_old", 0) > 14:
            try:
                pb_data = get_latest_valuation(index_code, "市净率", max_days=7)
                if pb_data and pb_data.get("percentile") is not None:
                    pb_days_old = 0
                    if pb_data.get("snapshot_date"):
                        try:
                            pb_days_old = (datetime.now() - datetime.fromisoformat(pb_data["snapshot_date"])).days
                        except Exception:
                            pass
                    if pb_days_old <= 7:
                        detailed["percentile"] = pb_data["percentile"]
                        detailed["percentile_source"] = f"PB替代(PE过期{detailed['days_old']}天)"
                        # P3-6: 周期股 PE/PB 可能反向，标注警告
                        detailed["pb_substitute_warning"] = "PE过期使用PB替代，周期股PE/PB可能反向，注意估值判断偏差"
                        detailed["fallback_pb_value"] = pb_data.get("current_value")
                        detailed["fallback_pb_percentile"] = pb_data.get("percentile")
                        logger.info(
                            f"R4 PB替代: {index_code} PE过期{detailed['days_old']}天, "
                            f"使用PB百分位{pb_data['percentile']:.1f}%"
                        )
            except Exception as e:
                logger.debug(f"R4 PB替代检查失败: {e}")

        final_source = "leiniuniu"
        _log_valuation_query(index_code, detailed.get("index_name"), query_source, final_source,
                             0, 0, int((datetime.now() - start_ts).total_seconds() * 1000), trace_id, None)
        return detailed

    # 2. 降级到螺丝钉数据（最近 30 天内）
    dd_data = get_latest_dd_valuation_for_index(index_code, metric_type, max_days=30)
    if dd_data:
        dd_data["data_source"] = "螺丝钉"
        dd_data["is_expired"] = False
        dd_data["degraded"] = True
        degraded = 1
        if dd_data.get("snapshot_date"):
            try:
                days_old = (datetime.now() - datetime.fromisoformat(dd_data["snapshot_date"])).days
                dd_data["days_old"] = days_old
            except Exception:
                dd_data["days_old"] = 0
        final_source = "dd_luosiding"
        _log_valuation_query(index_code, dd_data.get("index_name"), query_source, final_source,
                             degraded, 0, int((datetime.now() - start_ts).total_seconds() * 1000), trace_id, None)
        return dd_data

    # 3. 使用过期的详细数据（如有）
    detailed_expired = get_latest_valuation(index_code, metric_type, max_days=365)
    # 同样检查关键字段是否为 null
    if detailed_expired and (detailed_expired.get("current_value") is not None or detailed_expired.get("percentile") is not None):
        detailed_expired["data_source"] = "manual"
        detailed_expired["is_expired"] = True
        is_expired = 1
        if detailed_expired.get("snapshot_date"):
            try:
                days_old = (datetime.now() - datetime.fromisoformat(detailed_expired["snapshot_date"])).days
                detailed_expired["days_old"] = days_old
            except Exception:
                detailed_expired["days_old"] = 0
        final_source = "expired_leiniuniu"
        _log_valuation_query(index_code, detailed_expired.get("index_name"), query_source, final_source,
                             0, is_expired, int((datetime.now() - start_ts).total_seconds() * 1000), trace_id, None)
        return detailed_expired

    # 3.5 metric_type fallback：本地指定 metric_type 查不到时，尝试本地其他 metric_type
    #     场景：alert_scanner 默认查"市盈率"，但持仓中很多指数本地只有"市净率/市销率/股息率"
    #     此步骤不触发在线兜底（避免误超时），仅用本地已有数据，命中后直接返回
    if allow_metric_fallback:
        for fallback_type in ["市净率", "市销率", "股息率"]:
            if fallback_type == metric_type:
                continue
            fb_data = get_latest_valuation(index_code, fallback_type, max_days=7)
            if fb_data and (fb_data.get("current_value") is not None or fb_data.get("percentile") is not None):
                fb_data["data_source"] = "manual"
                fb_data["is_expired"] = False
                fb_data["fallback_metric_type"] = fallback_type
                fb_data["original_metric_type"] = metric_type
                if fb_data.get("snapshot_date"):
                    try:
                        days_old = (datetime.now() - datetime.fromisoformat(fb_data["snapshot_date"])).days
                        fb_data["days_old"] = days_old
                    except Exception:
                        fb_data["days_old"] = 0
                final_source = f"leiniuniu_fallback_{fallback_type}"
                _log_valuation_query(index_code, fb_data.get("index_name"), query_source, final_source,
                                     0, 0, int((datetime.now() - start_ts).total_seconds() * 1000), trace_id, None)
                logger.info(
                    f"[valuation] {index_code} metric_type fallback：{metric_type} 无数据，"
                    f"改用 {fallback_type} 命中（query_source={query_source}）"
                )
                return fb_data

    # 4. 在线兜底：akshare → 天天基金（仅在 enable_online=True 时触发）
    if enable_online:
        online_result = _online_fallback(index_code, metric_type, start_ts, query_source, trace_id)
        if online_result:
            return online_result
        # 在线兜底已启用但全部失败 → 真正需要告警
        error_msg = "all sources failed (local + akshare + ttfund)"
        failed_name = _lookup_index_name(index_code)
        _log_valuation_query(index_code, failed_name, query_source, "failed",
                             0, 0, int((datetime.now() - start_ts).total_seconds() * 1000), trace_id, error_msg)
        logger.warning(f"[valuation] {index_code} 估值查询全部失败 ({query_source})")
        return None
    else:
        # 在线兜底被禁用（批量场景）→ 本地表缺失，但在线渠道可能可查到，不应触发告警
        error_msg = "local sources failed, online fallback disabled"
        failed_name = _lookup_index_name(index_code)
        _log_valuation_query(index_code, failed_name, query_source, "local_failed_online_disabled",
                             0, 0, int((datetime.now() - start_ts).total_seconds() * 1000), trace_id, error_msg)
        logger.info(f"[valuation] {index_code} 本地估值缺失，在线兜底已禁用 ({query_source})")
        return None


def _online_fallback(index_code: str, metric_type: str, start_ts: datetime,
                     query_source: str, trace_id: str = None) -> dict | None:
    """在线兜底：akshare → 天天基金。结果仅内存缓存，不入库。

    使用 ThreadPoolExecutor 实现真正的超时控制，避免 akshare/MCP 调用卡住主线程。
    """
    from db.config import get_config_bool, get_config_int

    if not get_config_bool("valuation.online_fallback_enabled", True):
        return None

    cache_ttl = get_config_int("valuation.online_cache_ttl", 3600)
    cache_key = f"{index_code}:{metric_type}"
    cached = _online_cache.get(cache_key)
    if cached:
        if (datetime.now() - cached["_cached_at"]).total_seconds() < cache_ttl:
            logger.debug(f"[valuation] {index_code} 命中在线缓存 (source={cached.get('source')})")
            final_source = cached.get("source", "online_cached")
            _log_valuation_query(index_code, cached.get("index_name"), query_source, final_source,
                                 1, 0, int((datetime.now() - start_ts).total_seconds() * 1000), trace_id, None)
            return {k: v for k, v in cached.items() if k != "_cached_at"}
        else:
            del _online_cache[cache_key]

    timeout_ms = get_config_int("valuation.online_fallback_timeout_ms", 5000)
    timeout_s = max(timeout_ms / 1000.0, 1.0)

    # 渠道1：akshare 中证官方（带真正超时）
    try:
        with ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(_query_akshare_valuation, index_code, metric_type, timeout_ms)
            try:
                online_data = fut.result(timeout=timeout_s)
            except FuturesTimeoutError:
                logger.warning(f"[valuation] akshare 查询超时 {index_code} ({timeout_s}s)")
                online_data = None
        if online_data:
            online_data["data_source"] = "online"
            online_data["source"] = "akshare"
            online_data["is_expired"] = False
            online_data["degraded"] = True
            online_data["days_old"] = 0
            online_data["_cached_at"] = datetime.now()
            _online_cache[cache_key] = online_data
            result = {k: v for k, v in online_data.items() if k != "_cached_at"}
            final_source = "akshare"
            _log_valuation_query(index_code, result.get("index_name"), query_source, final_source,
                                 1, 0, int((datetime.now() - start_ts).total_seconds() * 1000), trace_id, None)
            logger.info(f"[valuation] {index_code} 在线兜底成功 (akshare, {query_source})")
            return result
    except Exception as e:
        logger.debug(f"[valuation] akshare 兜底失败 {index_code}: {e}")

    # 渠道2：天天基金 MCP（带真正超时）
    try:
        with ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(_query_ttfund_valuation, index_code, metric_type, timeout_ms)
            try:
                online_data = fut.result(timeout=timeout_s)
            except FuturesTimeoutError:
                logger.warning(f"[valuation] ttfund 查询超时 {index_code} ({timeout_s}s)")
                online_data = None
        if online_data:
            online_data["data_source"] = "online"
            online_data["source"] = "ttfund"
            online_data["is_expired"] = False
            online_data["degraded"] = True
            online_data["days_old"] = 0
            online_data["_cached_at"] = datetime.now()
            _online_cache[cache_key] = online_data
            result = {k: v for k, v in online_data.items() if k != "_cached_at"}
            final_source = "ttfund"
            _log_valuation_query(index_code, result.get("index_name"), query_source, final_source,
                                 1, 0, int((datetime.now() - start_ts).total_seconds() * 1000), trace_id, None)
            logger.info(f"[valuation] {index_code} 在线兜底成功 (ttfund, {query_source})")
            return result
    except Exception as e:
        logger.debug(f"[valuation] ttfund 兜底失败 {index_code}: {e}")

    return None


def _query_akshare_valuation(index_code: str, metric_type: str, timeout_ms: int) -> dict | None:
    """通过 akshare 查询中证官方估值。

    修复：原仅用 stock_zh_index_value_csindex（中证指数公司专用），对港股/海外指数无效。
    新增乐咕乐股接口作为补充，覆盖更多 A 股宽基指数。

    2026-07-20 优化：对已知 akshare 中证官方不支持的代码提前返回 None，避免每次浪费
    0.2-0.7s 网络请求（实测 HSTECH/HSI 港股代码、882011 Wind 全指均返回 404）。
    """
    # 已知 akshare 中证官方不支持的代码（实测返回 404）
    # 港股指数：HSTECH/HSI/HSCEI（中证官方接口只覆盖 A 股）
    # Wind 88xxxx：Wind 自有代码，中证官方无数据
    _AKSHARE_UNSUPPORTED_PREFIXES = ("HSTECH", "HSI", "HSCEI", "DJI", "SPX", "IXIC", "N225")
    _AKSHARE_UNSUPPORTED_CODES = {"882011"}  # Wind 全指房地产
    if index_code in _AKSHARE_UNSUPPORTED_CODES or index_code.startswith(_AKSHARE_UNSUPPORTED_PREFIXES):
        logger.debug(f"[valuation] akshare 已知不支持 {index_code}（港股/Wind 代码），跳过直接返回 None")
        return None

    from services.market_data import get_index_valuation
    try:
        result = get_index_valuation(index_code)
        if not result:
            return None
        # 适配字段（akshare 返回 pe/pb/pe_percentile/pb_percentile/dividend_yield）
        percentile = None
        current_value = None
        if metric_type == "市盈率":
            percentile = result.get("pe_percentile")
            current_value = result.get("pe")
        elif metric_type == "市净率":
            percentile = result.get("pb_percentile")
            current_value = result.get("pb")
        # 修复：akshare 对港股指数返回空结果（current_value 和 percentile 都为 None），
        # 应返回 None 让 _online_fallback 继续尝试 ttfund 兜底
        if current_value is None and percentile is None:
            logger.info(f"[valuation] akshare 返回空数据 {index_code}，降级到 ttfund")
            return None
        return {
            "index_code": index_code,
            "index_name": result.get("index_name") or index_code,
            "metric_type": metric_type,
            "current_value": current_value,
            "percentile": percentile,
            "snapshot_date": result.get("date") or datetime.now().strftime("%Y-%m-%d"),
            "percentile_window": "近3年",  # P2-5: 标注百分位口径
            "dividend_yield": result.get("dividend_yield"),
        }
    except Exception as e:
        logger.debug(f"[valuation] akshare 查询失败 {index_code}: {e}")
        return None


def _query_ttfund_valuation(index_code: str, metric_type: str, timeout_ms: int) -> dict | None:
    """通过天天基金 MCP 查询估值。

    修正：_invoke 返回 {success, errorCode, data} 结构，估值数据在 data.valuation 中。
    之前错误地取 result.get("valuation") 导致一直返回 None。

    修复：港股指数（如 HSTECH）用代码查询不识别，增加代码→名称映射 + 名称重试。
    """
    # 指数代码 → 名称映射（天天基金用名称查更准）
    # 2026-07-20 扩展：从 7 个港股代码扩展到覆盖持仓所有 17 个指数
    _CODE_NAME_MAP = {
        # ── 港股/海外（原有）──
        "HSTECH": "恒生科技",
        "HSI": "恒生指数",
        "HSCEI": "恒生中国企业指数",
        "DJI": "道琼斯",
        "SPX": "标普500",
        "IXIC": "纳斯达克",
        "N225": "日经225",
        # ── 国证 H 系（新增）──
        "H30094": "中证主要消费红利",
        "H30217": "中证全指医疗器械",
        "H30590": "中证机器人",
        # ── Wind 88xxxx（新增）──
        "882011": "中证全指房地产",
        # ── 中证 931xxx（新增）──
        "931140": "中证800医药",
        "931468": "中证红利质量",
        "931638": "中证港股通互联网",
        # ── 中证 000xxx（新增）──
        "000922": "中证红利",
        "000949": "中证畜牧养殖",
        # ── 中证 399xxx（新增）──
        "399997": "中证白酒",
        "399986": "中证银行",
    }

    def _extract_valuation(result, idx_code):
        """从 ttfund 返回中提取估值数据。"""
        if not result or not result.get("success"):
            return None
        data = result.get("data", {}) or {}
        valuation = data.get("valuation", {}) or {}
        index_profile = data.get("index_profile", {}) or {}
        quote = data.get("quote", {}) or {}
        # 估值数据为空时返回 None（触发名称重试）
        has_valuation_data = any(v is not None for v in valuation.values()) if valuation else False
        has_quote_data = any(v is not None for v in quote.values()) if quote else False
        if not has_valuation_data and not has_quote_data:
            return None
        percentile = None
        current_value = None
        if metric_type == "市盈率":
            percentile = valuation.get("pe_percentile_10y")
            current_value = valuation.get("pe_ttm")
        elif metric_type == "市净率":
            percentile = valuation.get("pb_percentile_10y")
            current_value = valuation.get("pb")
        # 天天基金百分位是 0-100 格式，统一转为 0-1
        if percentile is not None and percentile > 1.0:
            percentile = round(percentile / 100.0, 4)
        index_name = (quote.get("index_name") or index_profile.get("index_name")
                      or index_profile.get("full_index_name") or idx_code)
        # 关键修复：如果 current_value 和 percentile 都为 None，说明数据不完整，返回 None 触发重试
        if current_value is None and percentile is None:
            return None
        return {
            "index_code": idx_code,
            "index_name": index_name,
            "metric_type": metric_type,
            "current_value": current_value,
            "percentile": percentile,
            "snapshot_date": quote.get("quote_time") or datetime.now().strftime("%Y-%m-%d"),
            "percentile_window": "近10年",
            "dividend_yield": valuation.get("dividend_yield"),
            "roe": valuation.get("roe"),
        }

    try:
        from mcp.ttfund_client import get_ttfund_client
        tc = get_ttfund_client()

        # 第一步：用原始代码查询
        result = tc._invoke("fund_index", {
            "index_id": index_code,
            "query_scope": "valuation",
        })
        extracted = _extract_valuation(result, index_code)
        if extracted:
            return extracted

        # 第二步：代码查不到 → 用名称重试（港股/海外指数）
        index_name = _CODE_NAME_MAP.get(index_code)
        if index_name:
            logger.info(f"[valuation] ttfund 用代码 {index_code} 查不到，尝试用名称 {index_name} 重试")
            result2 = tc._invoke("fund_index", {
                "index_id": index_name,
                "query_scope": "valuation",
            })
            extracted2 = _extract_valuation(result2, index_code)
            if extracted2:
                return extracted2

        return None
    except Exception as e:
        logger.debug(f"[valuation] ttfund 查询失败 {index_code}: {e}")
        return None


def fetch_online_valuation(index_code: str, metric_type: str = "市盈率",
                            timeout_ms: int = 6000) -> dict | None:
    """主动查询在线最新估值（akshare 中证官方）。

    与自动兜底 _online_fallback 的区别：
    - 不受 valuation.online_fallback_enabled 开关控制（这是用户/专家主动调用）
    - 受 tool.online_valuation_query_enabled 开关控制（默认 true）
    - 复用 akshare 数据源 + 内存缓存（1小时TTL）
    - 带真正超时控制（ThreadPoolExecutor）

    Args:
        index_code: 指数代码（支持带后缀，内部 normalize）
        metric_type: 市盈率/市净率
        timeout_ms: 超时毫秒（默认 6 秒，略长于自动兜底的 5 秒）

    Returns:
        估值字典，带 source="akshare_online" 标识；失败返回 None
    """
    from db.config import get_config_bool, get_config_int
    try:
        if not get_config_bool("tool.online_valuation_query_enabled", True):
            logger.info(f"[valuation] 主动在线查询已禁用 (tool.online_valuation_query_enabled=false) {index_code}")
            return None
    except Exception:
        pass

    index_code = normalize_index_code(index_code)

    # 复用在线缓存（与自动兜底共享）
    cache_ttl = get_config_int("valuation.online_cache_ttl", 3600)
    cache_key = f"{index_code}:{metric_type}"
    cached = _online_cache.get(cache_key)
    if cached:
        if (datetime.now() - cached["_cached_at"]).total_seconds() < cache_ttl:
            result = {k: v for k, v in cached.items() if k != "_cached_at"}
            result["source"] = "akshare_online_cached"
            logger.debug(f"[valuation] 主动在线查询命中缓存 {index_code}")
            return result
        else:
            del _online_cache[cache_key]

    timeout_s = max(timeout_ms / 1000.0, 1.0)

    # 渠道1：akshare 中证官方
    try:
        with ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(_query_akshare_valuation, index_code, metric_type, timeout_ms)
            try:
                online_data = fut.result(timeout=timeout_s)
            except FuturesTimeoutError:
                logger.warning(f"[valuation] 主动在线查询 akshare 超时 {index_code} ({timeout_s}s)")
                online_data = None
        if online_data:
            online_data["data_source"] = "online"
            online_data["source"] = "akshare_online"
            online_data["is_expired"] = False
            online_data["days_old"] = 0
            # 统一 percentile 为 0-100 格式（akshare 返回 0-1 小数）
            pct_raw = online_data.get("percentile")
            if pct_raw is not None and pct_raw <= 1.0:
                online_data["percentile"] = round(pct_raw * 100, 2)
            online_data["_cached_at"] = datetime.now()
            _online_cache[cache_key] = online_data
            result = {k: v for k, v in online_data.items() if k != "_cached_at"}
            logger.info(f"[valuation] 主动在线查询成功(akshare) {index_code} ({metric_type}={result.get('current_value')}, 分位={result.get('percentile')}%)")
            return result
    except Exception as e:
        logger.warning(f"[valuation] 主动在线查询 akshare 失败 {index_code}: {e}")

    # 渠道2：天天基金 MCP（akshare 查不到或超时时兜底）
    try:
        with ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(_query_ttfund_valuation, index_code, metric_type, timeout_ms)
            try:
                online_data = fut.result(timeout=timeout_s)
            except FuturesTimeoutError:
                logger.warning(f"[valuation] 主动在线查询 ttfund 超时 {index_code} ({timeout_s}s)")
                online_data = None
        if online_data:
            online_data["data_source"] = "online"
            online_data["source"] = "ttfund_online"
            online_data["is_expired"] = False
            online_data["days_old"] = 0
            # 天天基金 percentile 已在 _query_ttfund_valuation 转为 0-1，这里统一转 0-100
            pct_raw = online_data.get("percentile")
            if pct_raw is not None and pct_raw <= 1.0:
                online_data["percentile"] = round(pct_raw * 100, 2)
            online_data["_cached_at"] = datetime.now()
            _online_cache[cache_key] = online_data
            result = {k: v for k, v in online_data.items() if k != "_cached_at"}
            logger.info(f"[valuation] 主动在线查询成功(ttfund) {index_code} ({metric_type}={result.get('current_value')}, 分位={result.get('percentile')}%)")
            return result
    except Exception as e:
        logger.warning(f"[valuation] 主动在线查询 ttfund 失败 {index_code}: {e}")
    return None


def _lookup_index_name(index_code: str) -> str | None:
    """反查指数名称：从本地估值表、持仓表、螺丝钉数据中查找。"""
    if not index_code:
        return None
    try:
        conn = _get_conn()
        # 1. 本地估值表
        row = conn.execute(
            "SELECT index_name FROM index_valuations WHERE index_code = ? LIMIT 1",
            (index_code,)
        ).fetchone()
        if row and row["index_name"]:
            conn.close()
            return row["index_name"]
        # 2. 持仓表（基金跟踪指数）
        row = conn.execute(
            "SELECT fund_name FROM portfolio_holdings WHERE index_code LIKE ? LIMIT 1",
            (f"%{index_code}%",)
        ).fetchone()
        if row and row["fund_name"]:
            conn.close()
            return row["fund_name"]
        conn.close()
    except Exception:
        pass
    return None


def _log_valuation_query(index_code: str, index_name: str, query_source: str,
                         final_source: str, degraded: int, is_expired: int,
                         latency_ms: int, trace_id: str, error_msg: str):
    """记录估值查询监控日志。"""
    try:
        from db.config import get_config_bool
        if not get_config_bool("valuation.monitoring_enabled", True):
            return
        conn = _get_conn()
        conn.execute("""
            INSERT INTO valuation_query_logs
                (index_code, index_name, query_source, final_source,
                 degraded, is_expired, latency_ms, trace_id, error_msg)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (index_code, index_name, query_source, final_source,
              degraded, is_expired, latency_ms, trace_id, error_msg))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug(f"[valuation] 写监控日志失败: {e}")


def get_valuation_query_stats(days: int = 7) -> dict:
    """获取估值查询监控统计（用于前端数据健康卡片）。"""
    try:
        conn = _get_conn()
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        # 总体统计
        total_row = conn.execute(
            "SELECT COUNT(*) as cnt FROM valuation_query_logs WHERE created_at >= ?", (since,)
        ).fetchone()
        total = total_row["cnt"] if total_row else 0

        # 各数据源命中数
        source_rows = conn.execute("""
            SELECT final_source, COUNT(*) as cnt
            FROM valuation_query_logs
            WHERE created_at >= ?
            GROUP BY final_source
            ORDER BY cnt DESC
        """, (since,)).fetchall()

        # 失败的指数列表（最近 days 天）
        failed_rows = conn.execute("""
            SELECT DISTINCT index_code, index_name
            FROM valuation_query_logs
            WHERE created_at >= ? AND final_source = 'failed'
            ORDER BY created_at DESC
            LIMIT 20
        """, (since,)).fetchall()

        # 平均耗时
        latency_row = conn.execute(
            "SELECT AVG(latency_ms) as avg_ms FROM valuation_query_logs WHERE created_at >= ?", (since,)
        ).fetchone()

        # 在线兜底触发次数
        online_row = conn.execute("""
            SELECT COUNT(*) as cnt FROM valuation_query_logs
            WHERE created_at >= ? AND final_source IN ('akshare', 'ttfund')
        """, (since,)).fetchone()

        conn.close()

        source_counts = {r["final_source"]: r["cnt"] for r in source_rows}
        return {
            "total": total,
            "source_counts": source_counts,
            "source_percent": {
                k: round(v / total * 100, 1) if total > 0 else 0
                for k, v in source_counts.items()
            },
            "online_fallback_count": online_row["cnt"] if online_row else 0,
            "failed_indexes": [dict(r) for r in failed_rows],
            "avg_latency_ms": round(latency_row["avg_ms"], 1) if latency_row and latency_row["avg_ms"] else 0,
            "days": days,
        }
    except Exception as e:
        logger.warning(f"[valuation] 获取监控统计失败: {e}")
        return {"total": 0, "source_counts": {}, "source_percent": {},
                "online_fallback_count": 0, "failed_indexes": [], "avg_latency_ms": 0, "days": days}


def get_latest_market_temperature() -> dict | None:
    """获取最新的市场温度数据。"""
    conn = _get_conn()
    row = conn.execute("""
        SELECT id, update_date, market_temperature, created_at
        FROM dd_valuations
        WHERE market_temperature IS NOT NULL
        ORDER BY update_date DESC
        LIMIT 1
    """).fetchone()
    conn.close()

    if not row:
        return None

    result = dict(row)

    # 判断市场温度状态
    temp = result.get("market_temperature")
    if temp is not None:
        if temp < 30:
            result["status"] = "低温"
            result["description"] = "市场温度较低，可能是布局机会"
        elif temp < 70:
            result["status"] = "适中"
            result["description"] = "市场温度适中，可正常配置"
        else:
            result["status"] = "高温"
            result["description"] = "市场温度较高，注意控制仓位"

    return result
