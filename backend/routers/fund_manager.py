"""基金经理路由 — /api/fund-manager/*"""

from fastapi import APIRouter, HTTPException

from fund_manager import get_fund_manager, check_manager_change, batch_get_managers
from db import list_holdings, _get_conn

router = APIRouter(tags=["fund-manager"])


@router.get("/api/fund-manager/{fund_code}")
async def get_fund_manager_api(fund_code: str):
    """获取指定基金的基金经理信息。"""
    info = get_fund_manager(fund_code)
    if not info:
        raise HTTPException(404, f"未找到基金 {fund_code} 的经理信息")
    return {"ok": True, "data": info}


@router.get("/api/fund-manager/portfolio/overview")
async def portfolio_manager_overview(user_id: str = "default"):
    """获取持仓基金的经理概览 + 变更检测。"""
    holdings = [h for h in list_holdings(user_id) if (h.get("shares") or 0) > 0]
    if not holdings:
        return {"ok": True, "managers": [], "changes": [], "total": 0}

    fund_codes = [h["fund_code"] for h in holdings]
    managers = batch_get_managers(fund_codes)

    result = []
    changes = []

    for h in holdings:
        code = h["fund_code"]
        info = managers.get(code, {})
        stored_manager = h.get("manager_name", "")

        item = {
            "fund_code": code,
            "fund_name": h.get("fund_name", ""),
            "manager_name": info.get("manager_name", ""),
            "company": info.get("company", ""),
            "career_years": info.get("career_years"),
            "total_scale": info.get("total_scale"),
            "best_return": info.get("best_return"),
            "fund_type": info.get("fund_type", ""),
            # 持仓盈亏（来自持仓数据）
            "shares": h.get("shares", 0),
            "cost_price": h.get("cost_price", 0),
            "current_price": h.get("current_price", 0),
            "profit_loss": round(h.get("profit_loss", 0), 2),
            "profit_rate": round((h.get("profit_rate", 0) or 0) * 100, 2),
            "current_value": round(h.get("current_value", 0), 2),
        }
        result.append(item)

        # 变更检测
        if stored_manager and info.get("manager_name"):
            change = check_manager_change(code, stored_manager)
            if change:
                changes.append(change)

        # 更新持仓表中的经理信息
        if info.get("manager_name") and info["manager_name"] != stored_manager:
            try:
                conn = _get_conn()
                conn.execute(
                    "UPDATE portfolio_holdings SET manager_name = ?, manager_company = ? WHERE fund_code = ? AND user_id = ?",
                    (info["manager_name"], info.get("company", ""), code, user_id),
                )
                conn.commit()
                conn.close()
            except Exception as e:
                pass

    return {
        "ok": True,
        "managers": result,
        "changes": changes,
        "total": len(result),
    }
