"""指数 → 基金映射服务（P2 执行落地）

为建议卡片的"去执行"按钮提供候选基金查询：
- 优先在用户持仓中查找跟踪该指数的基金（fund_name 模糊匹配指数名）
- 退回到系统内置的常见指数→基金映射表
- 都没有则返回空列表，前端提示手动输入

内置映射覆盖常见宽基 + 行业指数，可按需扩展。
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


# 系统内置的指数 → 基金映射表（fund_code/fund_name）
# 同一指数可对应多只候选基金（含 ETF + 联接基金）
_INDEX_FUND_MAP = {
    # 宽基指数
    "000300": [
        {"fund_code": "510300", "fund_name": "华泰柏瑞沪深300ETF"},
        {"fund_code": "110020", "fund_name": "易方达沪深300ETF联接A"},
        {"fund_code": "510310", "fund_name": "易方达沪深300ETF"},
    ],
    "000905": [
        {"fund_code": "510500", "fund_name": "南方中证500ETF"},
        {"fund_code": "510510", "fund_name": "南方中证500ETF联接A"},
    ],
    "000852": [
        {"fund_code": "560010", "fund_name": "万家 中证1000ETF"},
        {"fund_code": "006325", "fund_name": "中加中证1000指数A"},
    ],
    "000016": [
        {"fund_code": "510050", "fund_name": "华夏上证50ETF"},
        {"fund_code": "110003", "fund_name": "易方达上证50指数A"},
    ],
    "399006": [
        {"fund_code": "159915", "fund_name": "易方达创业板ETF"},
        {"fund_code": "161022", "fund_name": "富国创业板指数A"},
    ],
    "399300": [
        {"fund_code": "510300", "fund_name": "华泰柏瑞沪深300ETF"},
    ],
    # 行业指数
    "399997": [
        {"fund_code": "161725", "fund_name": "招商中证白酒指数A"},
    ],
    # 930697 是家用电器指数（原映射错误地指向白酒，已修正）
}

# 指数代码 → 中文名关键词（用于持仓 fund_name 模糊匹配）
# P3 冒烟测试发现：原表覆盖不全，港股互联网/恒生科技/医药等持仓匹配失败
_INDEX_NAME_HINTS = {
    "000300": "沪深300",
    "000905": "中证500",
    "000852": "中证1000",
    "000016": "上证50",
    "399006": "创业板",
    "399997": "白酒",
    # 930697 是家用电器指数（原错误地映射到白酒，已修正）
    "930697": "家电",
    # P3 补充：常见行业指数关键词
    "931638": "恒生科技",      # 港股互联网（持仓基金常叫"恒生科技ETF联接"）
    "399975": "证券",
    "931071": "人工智能",
    "930601": "软件",
    "931140": "医药",
    "000922": "红利",
    "H30184": "半导体",
    "000688": "科创",
}


def _normalize_index_code(index_code: str) -> str:
    """归一化指数代码，剥离交易所后缀（.SZ / .SH / .CSI / .HI / .GI）。

    DB 中存的是 '399997.SZ'，但映射表的键是 '399997'。
    P3 冒烟测试发现：不归一化会导致 candidate_funds 全部返回空。
    """
    if not index_code:
        return ""
    code = index_code.strip().upper()
    for suffix in (".SZ", ".SH", ".CSI", ".HI", ".GI"):
        if code.endswith(suffix):
            return code[: -len(suffix)]
    return code


def find_funds_by_index(index_code: str, user_holdings_only: bool = True) -> list[dict]:
    """根据指数代码查找相关基金。

    优先级：
    1. 用户持仓中跟踪该指数的基金（通过 fund_name 模糊匹配指数名）
    2. 系统内置的指数→基金映射表
    3. 返回空列表，前端提示"未找到相关基金，请手动选择"

    Args:
        index_code: 指数代码（如 000300 或 399997.SZ，会自动归一化）
        user_holdings_only: 是否仅从用户持仓中查找（True=优先持仓，False=仅内置表）

    Returns:
        [{"fund_code": "510300", "fund_name": "...", "in_holdings": bool}, ...]
    """
    if not index_code:
        return []

    raw_code = index_code.strip()
    norm_code = _normalize_index_code(raw_code)
    # 查找时同时尝试原始代码和归一化代码
    lookup_codes = [norm_code, raw_code] if norm_code != raw_code else [norm_code]

    name_hint = ""
    for c in lookup_codes:
        if c in _INDEX_NAME_HINTS:
            name_hint = _INDEX_NAME_HINTS[c]
            break

    results: list[dict] = []
    seen_codes: set = set()

    # 1. 用户持仓匹配（仅当 user_holdings_only=True 时）
    if user_holdings_only:
        try:
            from db import list_holdings
            holdings = list_holdings()
            for h in holdings:
                fund_name = (h.get("fund_name") or "").strip()
                fund_code = (h.get("fund_code") or "").strip()
                if not fund_code or fund_code in seen_codes:
                    continue
                # 通过 fund_name 匹配指数名提示词
                if name_hint and name_hint in fund_name:
                    results.append({
                        "fund_code": fund_code,
                        "fund_name": fund_name,
                        "in_holdings": True,
                    })
                    seen_codes.add(fund_code)
        except Exception as e:
            logger.warning(f"查找用户持仓失败: {e}")

    # 2. 内置映射表补充（不重复已加入的 fund_code）
    builtin = []
    for c in lookup_codes:
        if c in _INDEX_FUND_MAP:
            builtin = _INDEX_FUND_MAP[c]
            break
    for item in builtin:
        code = item["fund_code"]
        if code in seen_codes:
            continue
        results.append({
            "fund_code": code,
            "fund_name": item["fund_name"],
            "in_holdings": False,
        })
        seen_codes.add(code)

    return results


def get_candidate_funds_for_recommendation(rec_id: int) -> Optional[dict]:
    """根据 recommendation id 获取候选基金列表 + 建议详情。

    P3 优化：查找优先级调整为
    1. 持仓中 fund_name 模糊匹配（原逻辑）
    2. fund_metadata.tracking_index 精确匹配（P3 新增，需 tracking_index 含指数名）
    3. 内置映射表 _INDEX_FUND_MAP
    4. 已填充的 target_fund_code 补充到首位
    """
    from db._conn import _get_conn

    conn = _get_conn()
    row = conn.execute(
        "SELECT id, index_name, index_code, direction, target_fund_code, target_fund_name "
        "FROM recommendations WHERE id = ?",
        (rec_id,),
    ).fetchone()
    conn.close()
    if not row:
        return None

    rec = dict(row)
    index_code = rec.get("index_code") or ""
    index_name = rec.get("index_name") or ""
    # 如果已填充 target_fund_code，候选列表仍提供（用户可切换）
    candidates = find_funds_by_index(index_code) if index_code else []

    # P3 优化：若模糊匹配为空，尝试用 index_name 在 fund_metadata.tracking_index 精确查找
    if not candidates and index_name:
        candidates = _find_funds_by_tracking_index(index_name)

    # 若已填充 target_fund_code 不在候选中，补充到候选首位
    if rec.get("target_fund_code"):
        existing_codes = {c["fund_code"] for c in candidates}
        if rec["target_fund_code"] not in existing_codes:
            candidates.insert(0, {
                "fund_code": rec["target_fund_code"],
                "fund_name": rec.get("target_fund_name") or rec["target_fund_code"],
                "in_holdings": False,
            })

    return {
        "recommendation_id": rec["id"],
        "index_name": index_name,
        "index_code": index_code,
        "direction": rec.get("direction") or "",
        "target_fund_code": rec.get("target_fund_code"),
        "target_fund_name": rec.get("target_fund_name"),
        "candidate_funds": candidates,
    }


def _find_funds_by_tracking_index(index_name: str) -> list[dict]:
    """通过 fund_metadata.tracking_index 精确查找基金（P3 优化新增）。

    akshare 的 tracking_index 字段是中文名（如"沪深300指数"），
    我们用 LIKE 模糊匹配 index_name 关键词。

    Returns:
        [{"fund_code": str, "fund_name": str, "in_holdings": bool}, ...]
    """
    if not index_name:
        return []
    from db._conn import _get_conn

    # 提取核心关键词（去掉"指数"后缀）
    keyword = index_name.replace("指数", "").strip()
    if not keyword:
        return []

    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT fund_code, fund_name, tracking_index FROM fund_metadata "
            "WHERE tracking_index LIKE ? AND tracking_index != '' "
            "ORDER BY updated_at DESC LIMIT 10",
            (f"%{keyword}%",),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return []

    # 标记是否在持仓中
    try:
        from db import list_holdings
        holdings_codes = {h.get("fund_code") for h in list_holdings()}
    except Exception:
        holdings_codes = set()

    results = []
    seen = set()
    for r in rows:
        code = r[0]
        if code in seen:
            continue
        results.append({
            "fund_code": code,
            "fund_name": r[1],
            "in_holdings": code in holdings_codes,
        })
        seen.add(code)
    return results


def backfill_recommendations_target_fund() -> int:
    """P3 优化：回填存量 recommendations 的 target_fund_code。

    对所有 target_fund_code 为空的 recommendations，尝试用 find_funds_by_index +
    _find_funds_by_tracking_index 查找并填充。

    Returns:
        回填成功的记录数。
    """
    from db._conn import _get_conn

    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, index_name, index_code FROM recommendations "
            "WHERE target_fund_code IS NULL OR target_fund_code = ''"
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return 0

    backfilled = 0
    for r in rows:
        rec_id = r[0]
        index_name = r[1] or ""
        index_code = r[2] or ""

        # 优先用 index_code 查持仓/内置映射
        candidates = find_funds_by_index(index_code) if index_code else []
        # 其次用 index_name 查 fund_metadata.tracking_index
        if not candidates and index_name:
            candidates = _find_funds_by_tracking_index(index_name)
        if not candidates:
            continue

        picked = next((c for c in candidates if c.get("in_holdings")), candidates[0])
        conn = _get_conn()
        try:
            conn.execute(
                "UPDATE recommendations SET target_fund_code = ?, target_fund_name = ? WHERE id = ?",
                (picked["fund_code"], picked["fund_name"], rec_id),
            )
            conn.commit()
        finally:
            conn.close()
        backfilled += 1

    if backfilled > 0:
        logger.info(f"[backfill] 回填 {backfilled} 条 recommendations 的 target_fund_code")
    return backfilled
