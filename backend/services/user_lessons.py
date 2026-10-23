"""从真实交易与持仓生成用户教训（user_lesson）。

## 为什么必须重做

`knowledge_base` 里原有 414 条 `atom_type='user_lesson'`，抽样核查后确认
全部是书摘与 AI 工程笔记，没有一条来自用户自己的操作记录。与此同时：

- 没有任何一条记录的 `category='user_lesson'`
- `get_lessons_for_target()` 按 `category='user_lesson'` 查询 → 永远返回空
- 结论：**教训子系统整体空转，从不产出任何内容**

而「用户自己的决策 + 结果」恰恰是大模型永远拿不到、只有本系统才有的东西。
知识库的差异化价值全在这里，却一条都没有。

## 生成原则

1. **只陈述可复核的事实**，不做道德评判。每条教训必须带数字和出处表名。
2. **只写数据支撑得起的结论**。数据缺失就写成「数据缺失」这条教训本身，
   而不是猜一个看起来合理的数字。
3. **幂等**：以 title 为键去重，重复运行不产生副本。
4. **可追溯**：source 字段写明来自哪张表、哪个时间段。
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime

logger = logging.getLogger(__name__)

LESSON_SOURCE_TRANSACTIONS = "portfolio_transactions"
LESSON_SOURCE_HOLDINGS = "portfolio_holdings"


def _as_date(value) -> date | None:
    if not value:
        return None
    text = str(value)[:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _money(value) -> str:
    try:
        return f"{float(value or 0):,.0f}"
    except (TypeError, ValueError):
        return "0"


def _wan(value) -> str:
    try:
        return f"{float(value or 0) / 10000:.2f}"
    except (TypeError, ValueError):
        return "0.00"


def _collect_facts(conn: sqlite3.Connection) -> dict:
    """从实盘表抽取可复核事实（严格排除模拟/系统记账）。"""
    real_tx_where = (
        "COALESCE(is_hypothetical,0)=0 AND COALESCE(is_system,0)=0 "
        "AND COALESCE(status,'confirmed') IN ('confirmed','settled')"
    )

    holdings = [dict(r) for r in conn.execute(
        "SELECT fund_code, fund_name, account, index_name, fund_category, "
        "       shares, cost_price, total_cost, current_value, profit_loss, "
        "       profit_rate, buy_date "
        "FROM portfolio_holdings"
    ).fetchall()]

    buys = conn.execute(
        f"SELECT COUNT(*) n, ROUND(SUM(amount),2) amt, MIN(transaction_date) first_d, "
        f"       MAX(transaction_date) last_d "
        f"FROM portfolio_transactions WHERE transaction_type='buy' AND {real_tx_where}"
    ).fetchone()

    sells = conn.execute(
        f"SELECT COUNT(*) n, ROUND(SUM(amount),2) amt, "
        f"       SUM(CASE WHEN ROUND(COALESCE(amount,0),2) <= 0 THEN 1 ELSE 0 END) zero_amt "
        f"FROM portfolio_transactions WHERE transaction_type='sell' AND {real_tx_where}"
    ).fetchone()

    hypo = conn.execute(
        "SELECT COUNT(*) n, ROUND(SUM(amount),2) amt FROM portfolio_transactions "
        "WHERE COALESCE(is_hypothetical,0)=1"
    ).fetchone()

    per_fund_sells = {
        r["fund_code"]: r["n"] for r in conn.execute(
            f"SELECT fund_code, COUNT(*) n FROM portfolio_transactions "
            f"WHERE transaction_type='sell' AND {real_tx_where} GROUP BY fund_code"
        ).fetchall()
    }

    return {
        "holdings": holdings,
        "buys": dict(buys) if buys else {},
        "sells": dict(sells) if sells else {},
        "hypo": dict(hypo) if hypo else {},
        "per_fund_sells": per_fund_sells,
    }


def build_lessons(conn: sqlite3.Connection) -> list[dict]:
    """生成教训条目列表（尚未落库）。"""
    facts = _collect_facts(conn)
    holdings = facts["holdings"]
    today = date.today()
    lessons: list[dict] = []

    def add(title, content, subcategory, importance, source, keywords):
        lessons.append({
            "category": "user_lesson",
            "subcategory": subcategory,
            "title": title,
            "content": content,
            "source": source,
            "keywords": keywords,
            "importance": importance,
            "atom_type": "user_lesson",
            "evidence_level": "user_memory",
            "as_of_date": today.strftime("%Y-%m-%d"),
            "temporal_type": "dated",
            "domain": "investment",
            "school": "general",
            "limitations": ["基于账户记账数据，不含场外未记录的申赎"],
        })

    active = [h for h in holdings if (h.get("total_cost") or 0) > 0]
    total_cost = sum(h["total_cost"] or 0 for h in active)
    total_value = sum(h["current_value"] or 0 for h in active)
    total_pl = total_value - total_cost
    total_rate = (total_pl / total_cost * 100) if total_cost else 0

    # ── 1. 组合整体状态 ──
    if total_cost:
        add(
            title=f"实盘组合整体浮亏 {total_rate:+.1f}%（成本 {_wan(total_cost)} 万，现值 {_wan(total_value)} 万）",
            content=(
                f"截至 {today:%Y-%m-%d}，有成本的持仓 {len(active)} 只，合计成本 "
                f"{_money(total_cost)} 元，现值 {_money(total_value)} 元，浮亏 "
                f"{_money(total_pl)} 元（{total_rate:+.2f}%）。"
                f"这是账户层面的既定事实，不是估算。"
                f"任何「是否需要调整」的讨论都必须以这个基线为起点，"
                f"而不是以某一次的短期涨跌为起点。"
            ),
            subcategory="组合状态",
            importance=9,
            source=LESSON_SOURCE_HOLDINGS,
            keywords=["组合", "浮亏", "总成本", "现值"],
        )

    # ── 2. 深套持仓缺退出条件 ──
    deep = sorted(
        [h for h in active if (h.get("profit_rate") or 0) < -0.15],
        key=lambda h: h.get("profit_rate") or 0,
    )
    if deep:
        rows = []
        no_sell = []
        for h in deep[:8]:
            row = (f"- {h['fund_name']}（{h['fund_code']}）：浮亏 {h['profit_rate'] * 100:+.1f}%，"
                   f"成本 {_money(h['total_cost'])}，现值 {_money(h['current_value'])}")
            if h.get("buy_date"):
                bd = _as_date(h["buy_date"])
                if bd:
                    row += f"，建仓 {bd.isoformat()}，已持有 {(today - bd).days} 天"
            else:
                row += "，建仓日期未记录"
            rows.append(row)
            if facts["per_fund_sells"].get(h["fund_code"], 0) == 0:
                no_sell.append(h["fund_name"])
        add(
            title=f"{len(deep)} 只持仓浮亏超 15%，其中 {len(no_sell)} 只无任何减仓记录",
            content=(
                "以下持仓浮亏超过 15%：\n" + "\n".join(rows) + "\n\n"
                f"这 {len(deep)} 只里有 {len(no_sell)} 只在交易流水中从未出现过卖出动作。"
                "「浮亏」本身不构成卖出理由，但没有预设退出条件的仓位，"
                "实际退出时点通常由情绪而非纪律决定。"
                "复盘时应先回答：当初买入它的理由，现在哪一个前提已经不成立。"
            ),
            subcategory="交易纪律",
            importance=9,
            source=LESSON_SOURCE_HOLDINGS,
            keywords=["浮亏", "止损", "退出条件", "深套"],
        )

    # ── 3. 仓位集中度（按跟踪指数归并） ──
    if total_cost:
        by_index = {}
        for h in active:
            key = (h.get("index_name") or "").strip() or "无跟踪标的"
            by_index[key] = by_index.get(key, 0) + (h.get("total_cost") or 0)
        top = sorted(by_index.items(), key=lambda kv: kv[1], reverse=True)[:5]
        rows = [
            f"- {k}：{_money(v)} 元，占 {v / total_cost * 100:.1f}%"
            for k, v in top
        ]
        hhi = sum((v / total_cost) ** 2 for _, v in by_index.items())
        add(
            title=f"持仓成本集中度：HHI={hhi:.3f}（等效 {1 / hhi:.1f} 个标的）" if hhi else "持仓集中度",
            content=(
                "按跟踪指数归并后的成本占比前五：\n" + "\n".join(rows) + "\n\n"
                f"集中度 HHI = Σ(单只成本占比²) = {hhi:.4f}，"
                f"等效标的数 = 1/HHI = {1 / hhi:.1f}。"
                f"账户持有 {len(active)} 只有成本的标的，但等效风险敞口只有 {1 / hhi:.1f} 个。"
                "两者差距越大，说明「分散」越接近名义分散：数量多，风险来源少。"
            ),
            subcategory="仓位结构",
            importance=8,
            source=LESSON_SOURCE_HOLDINGS,
            keywords=["集中度", "HHI", "分散", "仓位结构"],
        )

    # ── 4. 交易纪律：赎回笔数与买入笔数严重不对称 ──
    buy_n = facts["buys"].get("n") or 0
    sell_n = facts["sells"].get("n") or 0
    if buy_n:
        ratio = sell_n / buy_n
        add(
            title=f"真实交易记录：买入 {buy_n} 笔、卖出 {sell_n} 笔（卖出/买入 = {ratio:.2%}）",
            content=(
                f"剔除模拟与系统记账后，账户真实买入 {buy_n} 笔、"
                f"合计 {_money(facts['buys'].get('amt'))} 元"
                f"（{facts['buys'].get('first_d')} 至 {facts['buys'].get('last_d')}）；"
                f"卖出 {sell_n} 笔、合计 {_money(facts['sells'].get('amt'))} 元。"
                f"卖出笔数 / 买入笔数 = {ratio:.2%}。\n\n"
                "这个比值反映的是「进得去、出不来得少」的结构性倾向："
                "建仓动作密集，减仓动作稀少。它本身不是错误，"
                "但如果它是无意形成的，就说明缺少双向的计划，"
                "而不是审慎地长期持有。"
            ),
            subcategory="交易纪律",
            importance=8,
            source=LESSON_SOURCE_TRANSACTIONS,
            keywords=["买入笔数", "卖出笔数", "交易频率", "换手"],
        )

    # ── 5. 数据质量：模拟交易混入交易表 ──
    hypo_n = facts["hypo"].get("n") or 0
    if hypo_n:
        add(
            title=f"交易表中存在 {hypo_n} 笔模拟交易（{_wan(facts['hypo'].get('amt'))} 万），统计时必须排除",
            content=(
                f"`portfolio_transactions` 中 `is_hypothetical=1` 的记录共 {hypo_n} 笔，"
                f"金额合计 {_money(facts['hypo'].get('amt'))} 元；"
                f"同一张表里真实交易只有 {buy_n} 笔买入。"
                f"模拟金额是真实买入的 {((facts['hypo'].get('amt') or 0) / max(facts['buys'].get('amt') or 1, 1)):.1f} 倍。\n\n"
                "任何交易频率、持有天数、换手率、止损执行率统计，"
                "只要漏掉 `is_hypothetical=0` 过滤，结论会被模拟数据完全淹没。"
                "见此类指标时先核对过滤条件，再采信数字。"
            ),
            subcategory="数据质量",
            importance=8,
            source=LESSON_SOURCE_TRANSACTIONS,
            keywords=["模拟交易", "is_hypothetical", "数据口径", "统计"],
        )

    # ── 6. 数据质量：买卖记录金额为 0 ──
    zero_amt = facts["sells"].get("zero_amt") or 0
    if zero_amt:
        add(
            title=f"赎回记录中有 {zero_amt} 笔金额为 0，无法用于盈亏核算",
            content=(
                f"真实卖出记录共 {sell_n} 笔，其中 {zero_amt} 笔 `amount=0`，"
                "同时缺少成交份额或成交价。这类记录通常来自转换、分红再投或手工补录，"
                "不携带成交信息。\n\n"
                "影响：任何「已实现盈亏」「卖出胜率」「平均持有天数」的计算，"
                "都会因为这几条空记录而失真或报错。"
                "做卖出侧分析前，必须先剔除 `amount<=0` 的记录并单独列出。"
            ),
            subcategory="数据质量",
            importance=6,
            source=LESSON_SOURCE_TRANSACTIONS,
            keywords=["赎回", "成交金额", "已实现盈亏", "数据质量"],
        )

    # ── 7. 数据质量：建仓日期缺失 ──
    missing_date = [h for h in active if not h.get("buy_date")]
    if missing_date:
        add(
            title=f"{len(missing_date)} 只持仓缺少建仓日期，持有期类结论不可采信",
            content=(
                "以下持仓 `buy_date` 为空，无法计算持有天数、年化收益、"
                "「买入后最大回撤」等一切时间相关指标：\n"
                + "\n".join(
                    f"- {h['fund_name']}（{h['fund_code']}），成本 {_money(h['total_cost'])}"
                    for h in missing_date[:10]
                )
                + "\n\n凡出现「买入 X 天后下跌 Y%」的表述，必须先回溯净值序列确认真实建仓日，"
                "否则极易把一个月前的建仓说成一周前，把正常回撤说成突发暴跌。"
            ),
            subcategory="数据质量",
            importance=7,
            source=LESSON_SOURCE_HOLDINGS,
            keywords=["建仓日期", "buy_date", "持有天数", "数据质量"],
        )

    # ── 8. 数据质量：已清仓仍在持仓表 ──
    cleared = [h for h in holdings if (h.get("total_cost") or 0) == 0 and (h.get("shares") or 0) == 0]
    if cleared:
        add(
            title=f"{len(cleared)} 只已清仓标的仍留在持仓表，会虚增持仓数量",
            content=(
                "以下标的成本与份额均为 0，但仍在 `portfolio_holdings` 中：\n"
                + "\n".join(f"- {h['fund_name']}（{h['fund_code']}）" for h in cleared[:10])
                + f"\n\n持仓表共 {len(holdings)} 行，实际有成本的只有 {len(active)} 只。"
                "引用「持有 N 只基金」时必须用有成本的行数，否则数量会虚高，"
                "并连带影响集中度与分散度的判断。"
            ),
            subcategory="数据质量",
            importance=6,
            source=LESSON_SOURCE_HOLDINGS,
            keywords=["已清仓", "持仓数量", "集中度", "数据质量"],
        )

    # ── 9. 账户结构 ──
    by_account = {}
    for h in active:
        acc = (h.get("account") or "默认账户").strip()
        by_account.setdefault(acc, {"cost": 0, "value": 0, "n": 0})
        by_account[acc]["cost"] += h.get("total_cost") or 0
        by_account[acc]["value"] += h.get("current_value") or 0
        by_account[acc]["n"] += 1
    if len(by_account) > 1 and total_cost:
        rows = []
        for acc, v in sorted(by_account.items(), key=lambda kv: kv[1]["cost"], reverse=True):
            rate = ((v["value"] - v["cost"]) / v["cost"] * 100) if v["cost"] else 0
            rows.append(f"- {acc}：{v['n']} 只，成本 {_money(v['cost'])} 元"
                        f"（占 {v['cost'] / total_cost * 100:.1f}%），浮亏 {rate:+.2f}%")
        add(
            title=f"资金分布在 {len(by_account)} 个账户，单一账户最高占 {max(v['cost'] for v in by_account.values()) / total_cost * 100:.1f}%",
            content=(
                "按账户拆分的成本与盈亏：\n" + "\n".join(rows) + "\n\n"
                "账户是执行单元，风险因子才是风险单元。"
                "讨论「单个账户该不该加仓」之前，必须先把各账户的持仓合并到风险因子维度，"
                "否则会在不同账户里重复押注同一个方向而不自知。"
            ),
            subcategory="仓位结构",
            importance=6,
            source=LESSON_SOURCE_HOLDINGS,
            keywords=["账户", "资金分布", "风险因子"],
        )

    return lessons


def sync_lessons(conn: sqlite3.Connection, dry_run: bool = True) -> dict:
    """生成并写入教训条目。以 title 去重，幂等。"""
    lessons = build_lessons(conn)

    existing = {
        r[0] for r in conn.execute(
            "SELECT title FROM knowledge_base WHERE category='user_lesson'"
        )
    }
    to_insert = [l for l in lessons if l["title"] not in existing]

    result = {
        "generated": len(lessons),
        "new": len(to_insert),
        "skipped_existing": len(lessons) - len(to_insert),
        "preview": [l["title"] for l in to_insert],
        "dry_run": dry_run,
    }
    if dry_run or not to_insert:
        return result

    import json as _json
    with conn:
        conn.executemany(
            """INSERT INTO knowledge_base
               (category, subcategory, title, content, source, keywords, importance,
                atom_type, evidence_level, as_of_date, temporal_type, domain, school,
                limitations, counterpoints, usefulness_score)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [(
                l["category"], l["subcategory"], l["title"], l["content"], l["source"],
                _json.dumps(l["keywords"], ensure_ascii=False), l["importance"],
                l["atom_type"], l["evidence_level"], l["as_of_date"],
                l["temporal_type"], l["domain"], l["school"],
                _json.dumps(l.get("limitations", []), ensure_ascii=False),
                "[]",
                8,  # 与 governance.refresh_usefulness 的先验一致：基线 5 + user_lesson 3
            ) for l in to_insert]
        )
    return result
