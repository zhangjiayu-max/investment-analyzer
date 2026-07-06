"""估值数据 + 指数信息 CRUD。"""

from datetime import date, datetime

from db._conn import _get_conn


def _normalize_percentile(val) -> float | None:
    """P2-4.1: 统一百分位字段为 float 类型。

    历史数据存在三种格式：float（13.89）、字符串（"99.22%"）、None，
    影响评分逻辑（如 dca_add 的估值维度比较）。

    规则：
      - None / 空字符串 → None
      - int / float → float(val)
      - 字符串 "13.89%" / "13.89" → 13.89
      - 无法解析 → None
    """
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


def save_valuation(data: dict, source_image: str = None, source_url: str = None, snapshot_date: str = None) -> int:
    """保存估值数据，返回 id。同指数同日期同类型会更新。"""
    if not snapshot_date:
        snapshot_date = date.today().isoformat()
    if not data.get("index_code"):
        data["index_code"] = "UNKNOWN"
    if not data.get("index_name"):
        data["index_name"] = "未知指数"
    metric_type = data.get("metric_type", "市盈率")

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
    """列出所有有估值数据的指数，按 metric_type 分别显示。"""
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
    # P2-4.1: 读取容错
    return [_apply_percentile_normalize(dict(r)) for r in rows]


def list_index_freshness() -> list[dict]:
    """列出所有指数的最新数据日期和距今天数。"""
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
    return [dict(r) for r in rows]


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

    # 去除常见后缀
    code = index_code.strip()
    for suffix in [".SH", ".SZ", ".CSI", ".WI", ".GI", ".HI", ".MI"]:
        if code.upper().endswith(suffix.upper()):
            code = code[:-len(suffix)]
            break

    # 补齐 6 位（前面补 0）
    if code.isdigit() and len(code) < 6:
        code = code.zfill(6)

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


def get_best_valuation(index_code: str, metric_type: str = "市盈率") -> dict | None:
    """获取最佳估值数据（智能降级）。

    降级策略：
    1. 优先使用最近 7 天内的详细数据
    2. 降级到螺丝钉数据（最近 30 天内）
    3. 使用过期数据（如有）

    返回:
        包含估值数据的字典，带 data_source、is_expired、days_old 字段
    """
    from datetime import datetime, timedelta

    # 1. 优先使用最近 7 天内的详细数据
    detailed = get_latest_valuation(index_code, metric_type, max_days=7)
    if detailed:
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
                        detailed["fallback_pb_value"] = pb_data.get("current_value")
                        detailed["fallback_pb_percentile"] = pb_data.get("percentile")
                        logger.info(
                            f"R4 PB替代: {index_code} PE过期{detailed['days_old']}天, "
                            f"使用PB百分位{pb_data['percentile']:.1f}%"
                        )
            except Exception as e:
                logger.debug(f"R4 PB替代检查失败: {e}")

        return detailed

    # 2. 降级到螺丝钉数据（最近 30 天内）
    dd_data = get_latest_dd_valuation_for_index(index_code, metric_type, max_days=30)
    if dd_data:
        dd_data["data_source"] = "螺丝钉"
        dd_data["is_expired"] = False
        dd_data["degraded"] = True
        if dd_data.get("snapshot_date"):
            try:
                days_old = (datetime.now() - datetime.fromisoformat(dd_data["snapshot_date"])).days
                dd_data["days_old"] = days_old
            except Exception:
                dd_data["days_old"] = 0
        return dd_data

    # 3. 使用过期的详细数据（如有）
    detailed_expired = get_latest_valuation(index_code, metric_type, max_days=365)
    if detailed_expired:
        detailed_expired["data_source"] = "manual"
        detailed_expired["is_expired"] = True
        if detailed_expired.get("snapshot_date"):
            try:
                days_old = (datetime.now() - datetime.fromisoformat(detailed_expired["snapshot_date"])).days
                detailed_expired["days_old"] = days_old
            except Exception:
                detailed_expired["days_old"] = 0
        return detailed_expired

    # 4. 无可用数据
    return None


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
