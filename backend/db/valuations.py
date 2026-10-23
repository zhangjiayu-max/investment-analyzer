"""估值数据 + 指数信息 CRUD。"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import date, datetime, timedelta

from db._conn import _get_conn
from db.valuation_sources import mark_freshness, newest_valuation, image_valuation_rows

logger = logging.getLogger(__name__)


def _run_with_safe_timeout(fn, *args, timeout_s: float, timeout_msg: str = ""):
    """F-akshare（2026-07-23）：带超时执行函数，修复 shutdown(wait=True) zombie 线程 bug。

    替代 `with ThreadPoolExecutor(max_workers=1) as ex:` 模式，
    超时后用 shutdown(wait=False, cancel_futures=True) 立即释放，不等待 zombie 线程。

    Returns: fn 的返回值，超时或异常返回 None。
    """
    from infra.execution_budget import DATA_EXECUTOR, execution_budget, wait_result
    try:
        with execution_budget(timeout_s):
            future = DATA_EXECUTOR.submit(fn,*args)
            return wait_result(future,timeout_s)
    except TimeoutError:
        if timeout_msg:
            logger.warning(timeout_msg)
        return None
    except Exception as exc:
        logger.debug('估值源调用失败: %s',exc)
        return None

# ── 在线兜底结果内存缓存 ────────────────────────────────────
_online_cache: dict[str, dict] = {}  # key: f"{index_code}:{metric_type}"


def _mark_online_source_age(data: dict) -> dict:
    """抓取时间不是估值日期；保留未知日期和真实数据年龄。"""
    data.update(mark_freshness(data))
    return data


def _normalize_percentile(val, *, fractional: bool = False) -> float | None:
    """返回百分数点；只有来源显式声明 ratio 时才令 fractional=True。

    数值大小不决定单位。0.62 默认表示 0.62%，ratio 的 1 表示 100%。
    带 % 的源字符串优先使用其显式单位。
    """
    import math
    if val is None or val == "":
        return None
    try:
        explicit_percent = isinstance(val, str) and '%' in val
        value = float(val.replace('%', '').strip() if isinstance(val, str) else val)
        if not math.isfinite(value):
            return None
        if fractional and not explicit_percent:
            value = round(value * 100, 4)
        return min(100.0, max(0.0, value))
    except (TypeError, ValueError):
        return None


def _percentile_unit(data: dict) -> str:
    """单位来自契约或来源元数据，禁止按数值推断。"""
    unit = data.get('percentile_unit')
    if unit is None:
        unit = 'ratio' if data.get('percentile_scale') == 1 else 'percent'
    if unit not in ('percent', 'ratio'):
        raise ValueError(f'unsupported percentile_unit: {unit}')
    return unit


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
    unit = _percentile_unit(data)
    percentile = _normalize_percentile(data.get("percentile"), fractional=unit == 'ratio')
    # 现有 schema 的 raw_json 同时保留原始提取与可审核的单位/窗口元数据。
    try:
        raw = _json.loads(data.get('raw_json') or '{}')
    except (ValueError, TypeError):
        raw = {'raw_response': data.get('raw_json')}
    if not isinstance(raw, dict):
        raw = {'raw_response': raw}
    raw['_valuation_contract'] = {
        'percentile_unit': 'percent', 'percentile_input_unit': unit,
        'percentile_raw': data.get('percentile'),
        'history_window': data.get('history_window') or data.get('percentile_window'),
        'metric_verified': data.get('metric_verified'),
        'validation_status': data.get('validation_status', 'source_extracted'),
    }
    raw_json = _json.dumps(raw, ensure_ascii=False)

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
        source_image, source_url, raw_json,
    ))
    valuation_id = conn.execute(
        "SELECT id FROM index_valuations WHERE index_code=? AND snapshot_date=? AND metric_type=?",
        (data.get("index_code"), snapshot_date, metric_type)
    ).fetchone()[0]
    conn.commit()
    conn.close()
    return valuation_id


def fetch_recent_wide_base_valuations(indexes: list[tuple[str, str]],
                                     snapshot_date: str | None = None) -> dict:
    """抓取宽基指数 PE 估值并落库，返回 {"fetched": int, "errors": list[str]}。

    单位契约：get_index_valuation().pe_percentile 约定为 0-1 比例
    （见 services/market/market_data.py 文档，与 _fetch_online_sources 一致），
    这里显式声明 percentile_unit='ratio'，交由 save_valuation 统一换算成百分制。
    历史上该路径漏声明单位，导致「47% 分位」被存成 0.47，宽基分位缩小 100 倍。

    越界值视为源端口径变更：跳过并记录错误，不按数值猜尺度。
    """
    from services.market_data import get_index_valuation

    today = snapshot_date or date.today().isoformat()
    fetched = 0
    errors: list[str] = []
    for code, name in indexes:
        try:
            result = get_index_valuation(code)
            raw_pct = result.get("pe_percentile") if result else None
            if raw_pct is None:
                continue
            if (isinstance(raw_pct, bool) or not isinstance(raw_pct, (int, float))
                    or not 0 <= raw_pct <= 1):
                errors.append(f"{name}: pe_percentile 越界 {raw_pct!r}（契约 0-1），已跳过该指数")
                continue
            if code.startswith("399"):
                full_code = f"{code}.SZ"
            elif code.startswith("000"):
                full_code = f"{code}.SH"
            else:
                full_code = code
            save_valuation({
                "index_code": full_code,
                "index_name": result.get("index_name") or name,
                "metric_type": "市盈率",
                "current_value": result.get("pe"),
                "percentile": raw_pct,
                "percentile_unit": "ratio",
                "percentile_window": "近3年",
            }, snapshot_date=today)
            fetched += 1
        except Exception as exc:
            errors.append(f"{name}: {exc}")
    return {"fetched": fetched, "errors": errors}


def _apply_percentile_normalize(row: dict) -> dict:
    """P2-4.1: 读取容错——把 row['percentile'] 统一为 float 或 None。"""
    if row is None:
        return None
    if "percentile" in row:
        # save_valuation 已将数值转换为百分制，读取不能再次放大低于 1% 的值。
        row["percentile"] = _normalize_percentile(row.get("percentile"), fractional=False)
    try:
        metadata = _json.loads(row.get('raw_json') or '{}').get('_valuation_contract', {})
        row.update({key: value for key, value in metadata.items() if key not in row})
    except (TypeError, ValueError, AttributeError):
        pass
    row.update(percentile_unit='percent', percentile_scale=100)
    return row


def _normalize_valuation_result(result: dict | None) -> dict | None:
    """确保所有估值查询出口使用 0-100 百分制。"""
    if result is None:
        return None
    fractional = _percentile_unit(result) == "ratio"
    result["percentile"] = _normalize_percentile(result.get("percentile"), fractional=fractional)
    if "fallback_pb_percentile" in result:
        result["fallback_pb_percentile"] = _normalize_percentile(
            result.get("fallback_pb_percentile"), fractional=False
        )
    result.update(percentile_unit='percent', percentile_scale=100)
    return result


def _index_code_candidates(index_code: str) -> list[str]:
    """查询兼容标准代码和历史带后缀代码。"""
    base = normalize_index_code(index_code)
    return list(dict.fromkeys([
        index_code, base,
        *[f"{base}{suffix}" for suffix in (".WI", ".CSI", ".SH", ".SZ", ".SI", ".GI", ".HI", ".MI", ".CNI")],
    ]))


def get_valuation_history(index_code: str, days: int = 30, metric_type: str = None) -> list[dict]:
    """查询某指数最近 N 天的估值历史。"""
    conn = _get_conn()
    candidates = _index_code_candidates(index_code)
    placeholders = ','.join('?' * len(candidates))
    if metric_type:
        rows = conn.execute(f"""
            SELECT * FROM index_valuations
            WHERE index_code IN ({placeholders}) AND metric_type = ?
            ORDER BY snapshot_date DESC LIMIT ?
        """, (*candidates, metric_type, days)).fetchall()
    else:
        rows = conn.execute(f"""
            SELECT * FROM index_valuations
            WHERE index_code IN ({placeholders})
            ORDER BY snapshot_date DESC LIMIT ?
        """, (*candidates, days)).fetchall()
    conn.close()
    # P2-4.1: 读取容错，percentile 统一为 float
    return [_apply_percentile_normalize(dict(r)) for r in rows]


def get_latest_valuation(index_code: str, metric_type: str = None, max_days: int = None) -> dict | None:
    """获取某指数最新一条估值。max_days 限制只取最近 N 天内的数据。

    后缀兼容：同时匹配带后缀(.WI/.CSI/.SH/.SZ/.SI)和不带后缀的代码，
    避免 normalize_index_code 去后缀后查不到带后缀的库内记录。
    """
    from datetime import datetime, timedelta
    conn = _get_conn()
    date_filter = " AND snapshot_date <= ?"
    if max_days:
        cutoff = (datetime.now() - timedelta(days=max_days)).strftime("%Y-%m-%d")
        date_filter += f" AND snapshot_date >= '{cutoff}'"
    # 构造候选代码列表：原始code + 后缀变体（双向兼容）
    # - 传入不带后缀（H30217）→ 加带后缀变体（H30217.CSI/H30217.WI...）
    # - 传入带后缀（H30217.CSI）→ 加去后缀变体（H30217）
    candidates = _index_code_candidates(index_code)
    placeholders = ','.join('?' * len(candidates))
    if metric_type:
        row = conn.execute(f"""
            SELECT * FROM index_valuations
            WHERE index_code IN ({placeholders}) AND metric_type = ?{date_filter}
            ORDER BY snapshot_date DESC LIMIT 1
        """, (*candidates, metric_type, date.today().isoformat())).fetchone()
    else:
        row = conn.execute(f"""
            SELECT * FROM index_valuations
            WHERE index_code IN ({placeholders}){date_filter}
            ORDER BY snapshot_date DESC LIMIT 1
        """, (*candidates, date.today().isoformat())).fetchone()
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
    """按关键词模糊匹配指数名称或代码。

    按标准化后的指数代码去重，避免 399986 和 399986.SZ 返回两条。
    """
    conn = _get_conn()
    rows = conn.execute("""
        SELECT DISTINCT index_code, index_name
        FROM index_valuations
        WHERE index_name LIKE ? OR index_code LIKE ?
        ORDER BY index_code
    """, (f"%{keyword}%", f"%{keyword}%")).fetchall()
    conn.close()

    # 按标准化代码去重，保留不带后缀的优先（代码更短=更规范）
    seen = {}
    for r in rows:
        d = dict(r)
        norm = normalize_index_code(d["index_code"])
        if norm not in seen:
            seen[norm] = d
        else:
            # 已存在时，优先保留不带后缀的（代码更短）
            if "." not in d["index_code"] and "." in seen[norm]["index_code"]:
                seen[norm] = d
    return list(seen.values())


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


# ── 六亿估值 CRUD ──────────────────────────────────────

def save_liuyi_valuation(data: dict, image_path: str, image_url: str = None) -> int:
    """保存六亿估值解析结果，返回 id。同一图片会更新。"""
    conn = _get_conn()
    conn.execute("""
        INSERT INTO liuyi_valuations (image_path, image_url, update_date, market_temperature, index_count, raw_json)
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
    row = conn.execute("SELECT id FROM liuyi_valuations WHERE image_path = ?", (image_path,)).fetchone()
    conn.commit()
    conn.close()
    return row["id"] if row else 0


def list_liuyi_valuations() -> list[dict]:
    """列出所有六亿估值记录（按时间倒序）。"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, image_path, image_url, update_date, market_temperature, index_count, created_at FROM liuyi_valuations ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_liuyi_valuation(liuyi_id: int) -> dict | None:
    """获取单条六亿估值记录详情（含完整指数列表）。"""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM liuyi_valuations WHERE id = ?", (liuyi_id,)).fetchone()
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


def get_liuyi_parsed_image_paths() -> set[str]:
    """获取所有已解析的六亿估值图片路径集合。"""
    conn = _get_conn()
    rows = conn.execute("SELECT image_path FROM liuyi_valuations").fetchall()
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
    """按真实来源日期查询螺丝钉；缺代码时仅通过唯一名称/别名匹配。"""
    code = normalize_index_code(index_code)
    rows = [mark_freshness(row) for row in image_valuation_rows('螺丝钉')
            if row['index_code'] == code and row['metric_type'] == metric_type]
    return newest_valuation([row for row in rows if row['date_verified'] and row['days_old'] <= max_days], metric_type)


def list_available_valuation_indexes() -> list[dict]:
    """供分析与对话发现所有本地估值来源，保持每个指标独立。"""
    rows = [{**row, 'snapshot_date': row['latest_date']} for row in list_valuation_indexes()]
    rows.extend(image_valuation_rows())
    grouped = {}
    for row in rows:
        key = (normalize_index_code(row['index_code']), row['metric_type'])
        best = newest_valuation([grouped.get(key), row], row['metric_type'])
        if best:
            grouped[key] = {**best, 'index_code': key[0], 'latest_date': best.get('snapshot_date')}
    return list(grouped.values())


def get_best_valuation(
    index_code: str, metric_type: str = "市盈率", query_source: str = "unknown",
    trace_id: str = None, enable_online: bool = True, allow_metric_fallback: bool = False,
    conv_id: int = None, message_id: int = None, agent_name: str = None,
    user_query: str = None, cache_hit: int = 0,
) -> dict | None:
    """同指数同指标选最新来源；超过3天先补查在线，再返回明确标记的历史参考。"""
    start_ts = datetime.now()
    index_code = normalize_index_code(index_code)
    local = get_latest_valuation(index_code, metric_type)
    if local:
        local = {**local, 'data_source': 'manual', 'percentile_scale': 100}
    images = [row for row in image_valuation_rows()
              if row['index_code'] == index_code and row['metric_type'] == metric_type]
    best = newest_valuation([local, *images], metric_type)
    metadata = dict(conv_id=conv_id, message_id=message_id, agent_name=agent_name,
                    user_query=user_query, cache_hit=cache_hit)
    if (not best or best['is_expired']) and enable_online:
        online = _online_fallback(index_code, metric_type, start_ts, query_source, trace_id, **metadata)
        best = newest_valuation([best, online], metric_type)
    # 只有主指标完全缺失才允许显式换指标，不用新PB覆盖旧PE的口径。
    if not best and allow_metric_fallback:
        for fallback_type in ('市净率', '市销率', '股息率'):
            if fallback_type == metric_type:
                continue
            fallback = get_best_valuation(index_code, fallback_type, query_source, trace_id,
                                          enable_online=False, **metadata)
            if fallback and not fallback['is_expired']:
                best = {**fallback, 'fallback_metric_type': fallback_type, 'original_metric_type': metric_type}
                break
    source = 'failed' if enable_online else 'local_failed_online_disabled'
    if best:
        best = _normalize_valuation_result(best)
        source = {'manual': 'leiniuniu', '螺丝钉': 'dd_luosiding', '六亿': 'liuyi'}.get(
            best.get('data_source'), best.get('source') or 'online')
        if best['is_expired']:
            source = 'expired_' + source
            best['data_freshness_hint'] = '估值日期过期或未知，仅供历史参考，不能据此判断当前低估或给出确定买卖结论。'
    _log_valuation_query(index_code, (best or {}).get('index_name'), query_source, source,
                         int(bool(best and best.get('data_source') != 'manual')),
                         int(bool(best and best['is_expired'])),
                         int((datetime.now()-start_ts).total_seconds()*1000), trace_id,
                         None if best else 'no usable valuation data', **metadata)
    return best


def save_online_valuation(index_code: str, metric_type: str, data: dict, source: str) -> None:
    """保存在线兜底查询结果到 valuations_online 表（L2 持久化缓存）。

    Args:
        index_code: 指数代码（已 normalize）
        metric_type: 指标类型
        data: 在线查询返回的估值数据 dict
        source: "akshare" / "ttfund"
    """
    percentile = _normalize_percentile(data.get('percentile'), fractional=_percentile_unit(data) == 'ratio')
    conn = _get_conn()
    try:
        conn.execute("""
            INSERT OR REPLACE INTO valuations_online
            (index_code, metric_type, current_value, percentile, current_point,
             index_name, snapshot_date, source, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
        """, (
            index_code, metric_type,
            data.get("current_value"),
            percentile,
            data.get("current_point"),
            data.get("index_name"),
            data.get("snapshot_date"),
            source,
        ))
        conn.commit()
    except Exception as e:
        logger.warning(f"保存在线估值失败: {e}")
    finally:
        conn.close()


def get_online_valuation_cached(index_code: str, metric_type: str, max_days: int = 7) -> dict | None:
    """从 valuations_online 表读取 L2 缓存（默认7天TTL）。

    Args:
        index_code: 指数代码（已 normalize）
        metric_type: 指标类型
        max_days: 缓存有效期天数，默认7天

    Returns:
        估值数据 dict（带 data_source/cached 字段），无缓存或过期返回 None
    """
    conn = _get_conn()
    try:
        row = conn.execute("""
            SELECT * FROM valuations_online
            WHERE index_code = ? AND metric_type = ?
              AND created_at >= datetime('now', ?)
            ORDER BY created_at DESC LIMIT 1
        """, (index_code, metric_type, f"-{max_days} days")).fetchone()
        if not row:
            return None
        row = dict(row)
        row["data_source"] = f"online_{row.get('source', 'unknown')}"
        row["cached"] = True
        row["percentile_scale"] = 100
        row["percentile_unit"] = "percent"
        _mark_online_source_age(row)
        return row
    except Exception as e:
        logger.warning(f"读取在线估值缓存失败: {e}")
        return None
    finally:
        conn.close()


_ONLINE_LOCKS = [threading.Lock() for _ in range(32)]
_ONLINE_FAILURES = {}


def _online_fallback(index_code, metric_type, start_ts, query_source, trace_id=None, **kwargs):
    """同key单在途；失败仅缓存30秒，本地新数据仍优先读取。"""
    from db._conn import DB_PATH
    key = (str(DB_PATH), index_code, metric_type)
    lock = _ONLINE_LOCKS[hash(key) % len(_ONLINE_LOCKS)]
    if not lock.acquire(timeout=1):
        return None
    try:
        now = time.monotonic()
        if _ONLINE_FAILURES.get(key, 0) > now:
            return None
        result = _online_fallback_uncached(index_code, metric_type, start_ts,
                                           query_source, trace_id, **kwargs)
        if result is None:
            if len(_ONLINE_FAILURES) > 1024:
                for old_key, expiry in list(_ONLINE_FAILURES.items()):
                    if expiry <= now:
                        _ONLINE_FAILURES.pop(old_key, None)
            _ONLINE_FAILURES[key] = time.monotonic() + 30
        else:
            _ONLINE_FAILURES.pop(key, None)
        return result
    finally:
        lock.release()


def _online_fallback_uncached(index_code: str, metric_type: str, start_ts: datetime,
                     query_source: str, trace_id: str = None, **metadata) -> dict | None:
    """缓存TTL与来源日期分别校验；旧缓存不阻断真实来源刷新。"""
    from db.config import get_config_bool, get_config_int
    if not get_config_bool("valuation.online_fallback_enabled", True):
        return None
    key = f"{index_code}:{metric_type}"
    cached = _online_cache.get(key)
    ttl = get_config_int("valuation.online_cache_ttl", 3600)
    if cached and (datetime.now()-cached['_cached_at']).total_seconds() < ttl:
        cached = mark_freshness({k: value for k, value in cached.items() if k != '_cached_at'})
        if not cached['is_expired']:
            return {**cached, 'cached': True}
    l2 = get_online_valuation_cached(index_code, metric_type,
                                    max_days=get_config_int('valuation.online_cache_ttl_days', 7))
    if l2 and not l2['is_expired']:
        return l2
    timeout_ms = get_config_int('valuation.online_fallback_timeout_ms', 5000)
    fetched = _fetch_online_sources(index_code, metric_type, timeout_ms)
    result = newest_valuation([fetched, l2, cached], metric_type)
    if result and fetched is not None:
        provider = result.get('source', '').removesuffix('_online')
        if provider in ('akshare', 'ttfund'):
            save_online_valuation(index_code, metric_type, result, provider)
    return result


def _fetch_online_sources(index_code: str, metric_type: str, timeout_ms: int) -> dict | None:
    """第一来源过期/无日期时继续查询第二来源；失败保留可核验的最新历史数据。"""
    if metric_type not in ('市盈率', '市净率'):
        return None
    candidates = []
    for provider, fn in (('akshare', _query_akshare_valuation), ('ttfund', _query_ttfund_valuation)):
        data = _run_with_safe_timeout(fn, index_code, metric_type, timeout_ms,
            timeout_s=max(timeout_ms / 1000.0, 1.0),
            timeout_msg=f'估值查询超时 {provider} {index_code}')
        if not data or data.get('metric_type') != metric_type:
            continue
        data = mark_freshness({**data, 'data_source': 'online', 'source': provider + '_online',
                               'percentile_scale': data.get('percentile_scale', 100)})
        data['percentile'] = _normalize_percentile(data.get('percentile'), fractional=_percentile_unit(data) == 'ratio')
        data['percentile_unit'] = 'percent'
        data['percentile_scale'] = 100
        candidates.append(data)
        if not data['is_expired']:
            break
    best = newest_valuation(candidates, metric_type)
    if best:
        _online_cache[f'{index_code}:{metric_type}'] = {**best, '_cached_at': datetime.now()}
    return best


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
            "percentile_unit": "ratio",  # market_data 的百分位契约固定为 0-1
            "snapshot_date": result.get("date"),
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
        # 天天基金契约为百分数点，0.62 就是 0.62%，不得按数值切换单位。
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
            "percentile_unit": "percent",
            "snapshot_date": valuation.get("update_date") or valuation.get("date") or quote.get("quote_time"),
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
    """主动补查在线来源；来源日期未知或过期的缓存不代表最新估值。"""
    from db.config import get_config_bool, get_config_int
    if not get_config_bool('tool.online_valuation_query_enabled', True):
        return None
    index_code = normalize_index_code(index_code)
    key = f'{index_code}:{metric_type}'
    cached = _online_cache.get(key)
    if cached:
        cached = mark_freshness({k: value for k, value in cached.items() if k != '_cached_at'})
        original = _online_cache[key]
        if ((datetime.now()-original['_cached_at']).total_seconds()
                < get_config_int('valuation.online_cache_ttl', 3600) and not cached['is_expired']):
            return {**cached, 'cached': True}
    fetched = _fetch_online_sources(index_code, metric_type, timeout_ms)
    result = newest_valuation([fetched, cached], metric_type)
    if result and fetched is None:
        result['cached'] = True
    return result


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
                         latency_ms: int, trace_id: str, error_msg: str,
                         conv_id: int = None, message_id: int = None,
                         agent_name: str = None, user_query: str = None,
                         cache_hit: int = 0):
    """记录估值查询监控日志。

    扩展字段：conv_id/message_id/agent_name/user_query/cache_hit，
    用于把估值查询关联回具体对话、专家和用户问题。
    """
    try:
        from db.config import get_config_bool
        if not get_config_bool("valuation.monitoring_enabled", True):
            return
        conn = _get_conn()
        conn.execute("""
            INSERT INTO valuation_query_logs
                (index_code, index_name, query_source, final_source,
                 degraded, is_expired, latency_ms, trace_id, error_msg,
                 conv_id, message_id, agent_name, user_query, cache_hit)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (index_code, index_name, query_source, final_source,
              degraded, is_expired, latency_ms, trace_id, error_msg,
              conv_id, message_id, agent_name,
              (user_query or "")[:500], cache_hit))
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
