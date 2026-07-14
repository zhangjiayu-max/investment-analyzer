"""持仓数据导入 — 支持 CSV 格式"""

import csv
import io
import logging

from fastapi import APIRouter, UploadFile, File

logger = logging.getLogger(__name__)

router = APIRouter(tags=["portfolio-import"])


@router.post("/api/portfolio/import-csv")
async def import_portfolio_csv(file: UploadFile = File(...)):
    """从 CSV 导入持仓数据。

    支持格式：
    - 天天基金导出: 基金代码,基金名称,持有份额,成本价,当前价,盈亏
    - 蛋卷基金导出: 代码,名称,份额,成本,市值,收益
    - 通用格式: code,name,shares,cost,current
    """
    content = await file.read()
    text = content.decode("utf-8-sig")  # 处理 BOM

    reader = csv.DictReader(io.StringIO(text))
    imported = []
    errors = []

    for i, row in enumerate(reader):
        try:
            code = row.get("基金代码") or row.get("代码") or row.get("code") or ""
            name = row.get("基金名称") or row.get("名称") or row.get("name") or ""
            shares = float(row.get("持有份额") or row.get("份额") or row.get("shares") or 0)
            cost = float(row.get("成本价") or row.get("成本") or row.get("cost") or 0)
            current = float(row.get("当前价") or row.get("市值") or row.get("current") or 0)

            if code and shares > 0:
                imported.append({
                    "fund_code": code.strip(),
                    "fund_name": name.strip(),
                    "shares": shares,
                    "cost_price": cost,
                    "current_price": current,
                })
        except Exception as e:
            errors.append(f"第{i+2}行解析失败: {e}")

    saved = _bulk_upsert_holdings(imported)

    return {
        "ok": True,
        "imported": saved,
        "total_rows": len(imported),
        "errors": errors[:10],
    }


def _bulk_upsert_holdings(holdings: list[dict]) -> int:
    """批量更新持仓（按 user_id + account + fund_code 去重）。"""
    from db._conn import _get_conn
    from config import get_config as _get_cfg

    default_user = _get_cfg('portfolio.default_user_id', 'default')
    default_account = _get_cfg('portfolio.default_account', '花无缺')

    conn = _get_conn()
    saved = 0

    # 检查表是否存在
    table_check = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='portfolio_holdings'"
    ).fetchone()
    if not table_check:
        conn.close()
        return 0

    for h in holdings:
        uid = h.get("user_id") or default_user
        acct = h.get("account") or default_account
        existing = conn.execute(
            "SELECT id FROM portfolio_holdings WHERE fund_code = ? AND user_id = ? AND account = ?",
            (h["fund_code"], uid, acct)
        ).fetchone()
        if existing:
            conn.execute("""
                UPDATE portfolio_holdings SET
                    fund_name = ?, shares = ?, cost_price = ?, current_price = ?,
                    updated_at = datetime('now','localtime')
                WHERE fund_code = ? AND user_id = ? AND account = ?
            """, (h["fund_name"], h["shares"], h["cost_price"], h["current_price"],
                  h["fund_code"], uid, acct))
        else:
            conn.execute("""
                INSERT INTO portfolio_holdings (fund_code, fund_name, shares, cost_price, current_price, user_id, account)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (h["fund_code"], h["fund_name"], h["shares"], h["cost_price"], h["current_price"], uid, acct))
        saved += 1

    conn.commit()
    conn.close()
    return saved
