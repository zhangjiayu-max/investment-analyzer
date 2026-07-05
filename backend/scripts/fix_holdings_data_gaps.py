"""
批量补录 portfolio_holdings 的 index_code 和 buy_date 字段
1. index_code: 通过 index_name 模糊匹配 index_valuations 表
2. buy_date: 从 portfolio_transactions 表取最早买入日期
"""
import sqlite3
import re

DB_PATH = "/Users/xiaoyuer/projects/investment-analyzer/data/valuations.db"

# 持仓 index_name → 估值表 index_name 的映射（手工对齐）
INDEX_NAME_MAP = {
    "中证白酒指数": "中证白酒",
    "恒生科技指数": "恒生科技",
    "中证全指医疗器械指数": "医疗器械",
    "中证800制药与生物科技指数": "医药50",
    "中证畜牧养殖指数": "中证农业",
    "中证银行指数": "中证银行",
    "中证港股通互联网指数": "港股互联网",
    "中证主要消费红利指数": "消费红利",
    "中债7-10年国开行债券全价(总值)指数": None,  # 债券指数，估值表里没有
    "中证全指房地产指数": "房地产",
    "中证红利质量指数": "红利质量",
}

# 持仓 index_name → index_code 的直接映射（从估值表提取）
INDEX_CODE_MAP = {
    "中证白酒": "399997.SZ",
    "恒生科技": None,  # 估值表里没有直接对应的 code
    "医疗器械": "399989.SZ",
    "医药50": None,
    "中证农业": "000949.CSI",
    "中证银行": "399986.SZ",
    "港股互联网": None,
    "消费红利": None,
    "房地产": "882011.WI",
    "红利质量": None,
    "中证红利": "000922.CSI",
    "机器人": None,
    "沪深300": "399300.SZ",
}


def get_index_code_from_valuations(conn, index_name_val: str) -> str | None:
    """从 index_valuations 表查 index_code"""
    # 直接匹配
    mapped = INDEX_NAME_MAP.get(index_name_val)
    if mapped is None:
        return None  # 债券类或无跟踪标的
    if mapped:
        cur = conn.execute(
            "SELECT DISTINCT index_code FROM index_valuations WHERE index_name = ?", (mapped,)
        )
        row = cur.fetchone()
        if row:
            return row[0]
    # 模糊匹配
    cur = conn.execute(
        "SELECT DISTINCT index_code, index_name FROM index_valuations WHERE index_name LIKE ?",
        (f"%{index_name_val.replace('指数', '').replace('中证', '').strip()}%",),
    )
    rows = cur.fetchall()
    if len(rows) == 1:
        return rows[0][0]
    return None


def get_buy_date_from_transactions(conn, holding_id: int) -> str | None:
    """从 portfolio_transactions 取最早买入日期"""
    cur = conn.execute(
        """SELECT MIN(transaction_date) FROM portfolio_transactions 
           WHERE holding_id = ? AND transaction_type IN ('buy', 'buy_in', '申购')""",
        (holding_id,),
    )
    row = cur.fetchone()
    return row[0] if row and row[0] else None


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # 取所有持仓
    cur = conn.execute(
        "SELECT id, fund_code, fund_name, index_code, index_name, buy_date FROM portfolio_holdings"
    )
    holdings = cur.fetchall()

    updates = []
    for h in holdings:
        hid = h["id"]
        fund_code = h["fund_code"]
        fund_name = h["fund_name"]
        cur_index_code = h["index_code"]
        cur_index_name = h["index_name"]
        cur_buy_date = h["buy_date"]

        updates_for_holding = {"id": hid}

        # 补 index_code
        if not cur_index_code and cur_index_name and cur_index_name != "该基金无跟踪标的":
            matched_code = get_index_code_from_valuations(conn, cur_index_name)
            if matched_code:
                updates_for_holding["index_code"] = matched_code
                print(f"  [{hid}] {fund_code} index_code → {matched_code} (from index_name={cur_index_name})")

        # 补 buy_date
        if not cur_buy_date:
            first_buy = get_buy_date_from_transactions(conn, hid)
            if first_buy:
                updates_for_holding["buy_date"] = first_buy
                print(f"  [{hid}] {fund_code} buy_date → {first_buy} (from transactions)")

        if len(updates_for_holding) > 1:
            updates.append(updates_for_holding)

    # 执行更新
    updated_count = 0
    for u in updates:
        set_clauses = []
        params = []
        for k, v in u.items():
            if k == "id":
                continue
            set_clauses.append(f"{k} = ?")
            params.append(v)
        params.append(u["id"])
        sql = f"UPDATE portfolio_holdings SET {', '.join(set_clauses)}, updated_at = datetime('now','localtime') WHERE id = ?"
        conn.execute(sql, params)
        updated_count += 1

    conn.commit()
    conn.close()

    print(f"\n✅ 完成：{updated_count}/{len(holdings)} 条持仓已更新 index_code 或 buy_date")

    # 验证结果
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(
        "SELECT id, fund_code, fund_name, index_code, index_name, buy_date FROM portfolio_holdings ORDER BY id"
    )
    print("\n更新后：")
    print(f"{'ID':>3} {'基金代码':<8} {'基金名称':<30} {'index_code':<12} {'buy_date':<12}")
    print("-" * 85)
    for row in cur.fetchall():
        print(f"{row[0]:>3} {row[1]:<8} {row[2]:<30} {row[3] or '(空)':<12} {row[5] or '(空)':<12}")
    conn.close()


if __name__ == "__main__":
    main()
