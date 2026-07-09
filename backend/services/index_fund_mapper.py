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
    "930697": [
        {"fund_code": "161725", "fund_name": "招商中证白酒指数A"},
    ],
    # 中证白酒有时用 399997 或 930697，两者都映射到同一只基金
}

# 指数代码 → 中文名（用于持仓 fund_name 模糊匹配）
_INDEX_NAME_HINTS = {
    "000300": "沪深300",
    "000905": "中证500",
    "000852": "中证1000",
    "000016": "上证50",
    "399006": "创业板",
    "399997": "白酒",
    "930697": "白酒",
}


def find_funds_by_index(index_code: str, user_holdings_only: bool = True) -> list[dict]:
    """根据指数代码查找相关基金。

    优先级：
    1. 用户持仓中跟踪该指数的基金（通过 fund_name 模糊匹配指数名）
    2. 系统内置的指数→基金映射表
    3. 返回空列表，前端提示"未找到相关基金，请手动选择"

    Args:
        index_code: 指数代码（如 000300）
        user_holdings_only: 是否仅从用户持仓中查找（True=优先持仓，False=仅内置表）

    Returns:
        [{"fund_code": "510300", "fund_name": "...", "in_holdings": bool}, ...]
    """
    if not index_code:
        return []

    index_code = index_code.strip()
    name_hint = _INDEX_NAME_HINTS.get(index_code, "")
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
    builtin = _INDEX_FUND_MAP.get(index_code, [])
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

    返回:
        {
            "recommendation_id": int,
            "index_name": str,
            "index_code": str,
            "direction": str,
            "target_fund_code": str | None,  # 已填充的基金代码
            "target_fund_name": str | None,
            "candidate_funds": [...],
        }
        None 表示 recommendation 不存在。
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
    # 如果已填充 target_fund_code，候选列表仍提供（用户可切换）
    candidates = find_funds_by_index(index_code) if index_code else []
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
        "index_name": rec.get("index_name") or "",
        "index_code": index_code,
        "direction": rec.get("direction") or "",
        "target_fund_code": rec.get("target_fund_code"),
        "target_fund_name": rec.get("target_fund_name"),
        "candidate_funds": candidates,
    }
