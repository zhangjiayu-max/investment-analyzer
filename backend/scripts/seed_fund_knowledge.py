#!/usr/bin/env python3
"""R-7 持仓基金知识盲区补充脚本。

读取 fund_metadata 表，为每只持仓基金生成基础知识卡片写入 knowledge_base 表。
用法：python3 scripts/seed_fund_knowledge.py [--force]
"""
import os
import sys

# 确保 import 能找到 backend 模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db._conn import _get_conn
from db.knowledge import add_knowledge
from db.config import get_config_bool
from services.market.leading_indicators.akshare_utils import call_akshare_with_timeout


def _list_all_funds() -> list[dict]:
    """读取 fund_metadata 全部基金。"""
    conn = _get_conn()
    try:
        rows = conn.execute("""
            SELECT fund_code, fund_name, fund_type, fund_category, benchmark,
                   establish_date, management_company, management_fee, custody_fee,
                   subscription_fee, tracking_index
            FROM fund_metadata
            ORDER BY fund_code
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _exists_fund_knowledge(fund_name: str) -> bool:
    """检查 knowledge_base 是否已有 source=fund_name 且 category='book' 的记录。"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM knowledge_base WHERE source = ? AND category = 'book'",
            (fund_name,),
        ).fetchone()
        return row[0] > 0
    finally:
        conn.close()


def _is_index_fund(fund: dict) -> bool:
    """判断是否为指数基金（fund_category / fund_type / tracking_index 三重判定）。"""
    if (fund.get("fund_category") or "").strip() == "index":
        return True
    if "指数" in (fund.get("fund_type") or ""):
        return True
    tracking = (fund.get("tracking_index") or "").strip()
    if tracking and tracking != "该基金无跟踪标的":
        return True
    return False


def _supplement_fees_via_akshare(fund_code: str) -> tuple[float | None, float | None]:
    """通过 akshare 补充管理费/托管费（失败返回 None, None，不阻塞主流程）。"""
    try:
        import akshare as ak
        df = call_akshare_with_timeout(ak.fund_fee_em, symbol=fund_code, indicator="运作费用", timeout=20)
        if df is None or df.empty:
            return None, None
        vals = [str(v) for v in df.iloc[0]]
        return _extract_rate(vals, "管理费率"), _extract_rate(vals, "托管费率")
    except Exception:
        return None, None


def _extract_rate(vals: list[str], key: str) -> float | None:
    """从平铺列表中提取 key 的下一个元素并转为 float。"""
    for i, v in enumerate(vals):
        if v == key and i + 1 < len(vals):
            try:
                return float(vals[i + 1].replace("%（每年）", "").replace("%", "").strip())
            except ValueError:
                return None
    return None


def _fmt_fee(val) -> str:
    """格式化费率（None → '暂无'）。"""
    return f"{val}%" if val is not None else "暂无"


def _build_card(fund: dict) -> tuple[str, str, list[str]]:
    """构建知识卡片，返回 (title, content, keywords)。"""
    code = fund.get("fund_code") or ""
    name = fund.get("fund_name") or ""
    ftype = fund.get("fund_type") or "未知"
    benchmark = fund.get("benchmark") or "暂无"
    establish = fund.get("establish_date") or "暂无"
    company = fund.get("management_company") or "暂无"
    tracking = (fund.get("tracking_index") or "").strip()

    # 费率：本地缺失时尝试 akshare 补充
    mgmt = fund.get("management_fee")
    custody = fund.get("custody_fee")
    if mgmt is None:
        m2, c2 = _supplement_fees_via_akshare(code)
        if m2 is not None:
            mgmt = m2
        if custody is None and c2 is not None:
            custody = c2

    has_tracking = bool(tracking) and tracking != "该基金无跟踪标的"
    if _is_index_fund(fund):
        tracking_display = tracking if has_tracking else "无"
        config_note = (f"跟踪 {tracking_display}，适合作为该指数的低成本配置工具。"
                       if has_tracking else "指数型基金，适合作为对应板块的低成本被动配置工具。")
    else:
        tracking_display = "无（主动管理）"
        config_note = "主动管理型基金，无固定跟踪指数，依赖基金经理选股能力。"

    title = f"{name}（{code}）基金档案"
    content = f"""## {name}（{code}）基金档案

### 基本信息
- 基金类型：{ftype}
- 成立日期：{establish}
- 基金公司：{company}
- 业绩比较基准：{benchmark}

### 跟踪标的
- 跟踪指数：{tracking_display}

### 费率信息
- 管理费率：{_fmt_fee(mgmt)}（每年）
- 托管费率：{_fmt_fee(custody)}（每年）
- 申购费率：{_fmt_fee(fund.get('subscription_fee'))}

### 配置定位
{config_note}"""

    keywords = [code, name]
    if tracking and tracking != "该基金无跟踪标的":
        keywords.append(tracking)
    return title, content, keywords


def seed_fund_knowledge(force: bool = False, respect_switch: bool = True):
    """为持仓基金生成基础知识卡片写入 knowledge_base。

    force=True 强制覆盖（按 title 唯一约束 INSERT OR REPLACE）；
    respect_switch=False 时跳过 rag.auto_seed_fund_knowledge 开关检查（CLI 用）。
    """
    if respect_switch and not force:
        if not get_config_bool("rag.auto_seed_fund_knowledge", default=False):
            print("[seed_fund_knowledge] 开关 rag.auto_seed_fund_knowledge=false，跳过（CLI 请加 --force）")
            return

    funds = _list_all_funds()
    print(f"[seed_fund_knowledge] 读取到 {len(funds)} 只持仓基金")

    success = skip = fail = 0
    for fund in funds:
        name = fund.get("fund_name") or ""
        code = fund.get("fund_code") or ""
        try:
            # 幂等：已有则跳过（--force 跳过此检查，直接 INSERT OR REPLACE 覆盖）
            if not force and _exists_fund_knowledge(name):
                print(f"  跳过 {code} {name}（已有知识卡片）")
                skip += 1
                continue
            title, content, keywords = _build_card(fund)
            kid = add_knowledge(
                category="book",
                title=title,
                content=content,
                subcategory="indicator",
                source=name,
                keywords=keywords,
                importance=6,
                atom_type="fact",
                evidence_level="verified",
            )
            print(f"  写入 {code} {name} → knowledge_id={kid}")
            success += 1
        except Exception as e:
            print(f"  失败 {code} {name}: {e}")
            fail += 1

    print(f"\n[seed_fund_knowledge] 完成：成功 {success} 条 / 跳过 {skip} 条 / 失败 {fail} 条")


if __name__ == "__main__":
    # 直接运行，不受开关限制（CLI 手动触发）；--force 强制覆盖已有
    import argparse
    parser = argparse.ArgumentParser(description="R-7 持仓基金知识盲区补充")
    parser.add_argument("--force", action="store_true", help="强制覆盖已有基金知识")
    args = parser.parse_args()
    seed_fund_knowledge(force=args.force, respect_switch=False)
