from utils import _safe_float
"""四笔钱归类 + 定投优化器

四笔钱框架（参考有知有行）：
- 活钱管理：货币基金，随时可用
- 稳健理财：纯债基金，1-3年
- 长期投资：混合/股票/指数基金，3年+
- 保险保障：保险类产品

定投优化器：根据估值+恐贪指数动态调整定投金额
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter

from db._conn import _get_conn
from db.portfolio import list_holdings
from db.config import get_config_float
from llm_service import _call_llm, MODEL

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/four-pots", tags=["four-pots"])

_background_tasks: set = set()


def save_four_pots_candidates(result: dict, user_id: str = "default") -> int:
    """把四笔钱建议沉淀为建议候选。"""
    from db.decisions import create_candidate_from_structured_recommendation

    created = 0
    for idx, advice in enumerate(result.get("advice") or []):
        text = str(advice)
        action_type = "cash_reserve" if any(w in text for w in ["活钱", "备用金", "生活费"]) else "rebalance"
        candidate_id = create_candidate_from_structured_recommendation({
            "source_type": "tool",
            "source_id": idx,
            "scenario_type": "four_pots",
            "action_type": action_type,
            "target_type": "portfolio",
            "target_code": "four_pots",
            "target_name": "四笔钱配置",
            "summary": text,
            "reason": text,
            "confidence": "medium",
            "evidence": {"tool": "four_pots", "advice": text},
            "source_snapshot": result,
            "dedupe_key": f"four_pots:{action_type}:{text[:40]}",
            "priority": 7 if action_type == "cash_reserve" else 5,
        }, user_id=user_id)
        if candidate_id:
            created += 1
    return created


def save_dca_candidates(result: dict, user_id: str = "default") -> int:
    """把定投优化建议沉淀为建议候选。"""
    from db.decisions import create_candidate_from_structured_recommendation

    created = 0
    for item in result.get("suggestions") or []:
        fund_code = item.get("fund_code") or ""
        fund_name = item.get("fund_name") or fund_code or "定投标的"
        amount = item.get("suggested_amount")
        if amount is None:
            amount = item.get("final_amount")
        candidate_id = create_candidate_from_structured_recommendation({
            "source_type": "tool",
            "scenario_type": "dca_optimization",
            "action_type": "dca",
            "target_type": "fund",
            "target_code": fund_code,
            "target_name": fund_name,
            "summary": f"{fund_name}：{item.get('action') or item.get('decision') or '优化定投'}",
            "reason": item.get("reason") or "",
            "suggested_amount": amount,
            "confidence": "medium",
            "evidence": {
                "pe_percentile": item.get("pe_percentile"),
                "fear_greed_score": result.get("fear_greed_score") or (result.get("fear_greed") or {}).get("score"),
            },
            "source_snapshot": item,
            "dedupe_key": f"dca_optimization:{fund_code}:pe{(item.get('pe_percentile', 50) or 50) // 10 * 10}",
            "priority": 6,
        }, user_id=user_id)
        if candidate_id:
            created += 1
    return created



# ============ 基金类型归类 ============

# 基金类型映射规则
TYPE_MAP = {
    "货币型": "活钱管理",
    "货币": "活钱管理",
    "活钱": "活钱管理",
    "纯债": "稳健理财",
    "债券型": "稳健理财",
    "债券": "稳健理财",
    "固收": "稳健理财",
    "固收+": "稳健理财",
    "短债": "稳健理财",
    "中短债": "稳健理财",
    "二级债": "稳健理财",
    "股票型": "长期投资",
    "混合型": "长期投资",
    "指数型": "长期投资",
    "ETF": "长期投资",
    "QDII": "长期投资",
    "LOF": "长期投资",
    "FOF": "长期投资",
    "平衡型": "长期投资",
    "灵活配置": "长期投资",
    "偏股": "长期投资",
    "偏债": "稳健理财",
}

# 基金名称关键词推断
NAME_KEYWORDS = {
    "活钱管理": ["货币", "余额宝", "零钱通", "活钱", "现金", "天天"],
    "稳健理财": ["债券", "纯债", "短债", "中短债", "固收", "稳健", "安心", "稳定", "信用"],
    "长期投资": ["沪深300", "中证500", "创业板", "科创", "纳斯达克", "标普", "消费", "医药", "科技", "新能源", "白酒", "半导体"],
}


def classify_fund(fund_name: str, fund_type: str = "") -> str:
    """将基金归类到四笔钱。"""
    # 1. 先按基金类型
    if fund_type:
        for key, pot in TYPE_MAP.items():
            if key in fund_type:
                return pot

    # 2. 再按名称关键词
    name_lower = fund_name.lower()
    for pot, keywords in NAME_KEYWORDS.items():
        for kw in keywords:
            if kw in name_lower:
                return pot

    # 3. 默认归为长期投资
    return "长期投资"


def classify_pots() -> dict:
    """将持仓归类到四笔钱。"""
    holdings = list_holdings() or []
    active = [h for h in holdings if _safe_float(h.get("shares")) > 0]

    pots = {
        "活钱管理": {"total": 0, "items": []},
        "稳健理财": {"total": 0, "items": []},
        "长期投资": {"total": 0, "items": []},
        "保险保障": {"total": 0, "items": []},
    }

    for h in active:
        name = h.get("fund_name", "")
        ftype = h.get("fund_type", "")
        value = _safe_float(h.get("market_value"))

        pot_name = classify_fund(name, ftype)
        pots[pot_name]["total"] += value
        pots[pot_name]["items"].append({
            "fund_name": name,
            "fund_code": h.get("fund_code", ""),
            "market_value": round(value, 2),
            "fund_type": ftype,
        })

    total_value = sum(p["total"] for p in pots.values())

    # 计算占比和建议
    result = {}
    for name, data in pots.items():
        pct = round(data["total"] / total_value * 100, 1) if total_value > 0 else 0
        result[name] = {
            "total": round(data["total"], 2),
            "percentage": pct,
            "count": len(data["items"]),
            "items": data["items"],
        }

    # 配比建议
    advice = []
    if result["活钱管理"]["percentage"] > 30:
        advice.append("活钱管理占比过高（{:.0f}%），可将多余部分转入长期投资".format(result["活钱管理"]["percentage"]))
    if result["活钱管理"]["percentage"] < 5 and total_value > 0:
        advice.append("活钱管理占比过低（{:.0f}%），建议保留3-6个月生活费".format(result["活钱管理"]["percentage"]))
    if result["长期投资"]["percentage"] > 80:
        advice.append("长期投资占比过高（{:.0f}%），注意风险控制".format(result["长期投资"]["percentage"]))
    if result["稳健理财"]["percentage"] < 10 and total_value > 0:
        advice.append("稳健理财占比偏低（{:.0f}%），可适当增加债券基金".format(result["稳健理财"]["percentage"]))

    if not advice:
        advice.append("四笔钱配比合理，继续保持")

    return {
        "pots": result,
        "total_value": round(total_value, 2),
        "advice": advice,
    }


# ============ 定投优化器 ============

async def calc_dca_optimization() -> dict:
    """根据估值和恐贪指数，计算定投优化建议。"""
    # 获取恐贪指数
    from routers.analysis.health_score import calc_fear_greed_index
    fear_greed = await calc_fear_greed_index()
    fg_score = fear_greed.get("score", 50)

    # 获取持仓
    holdings = list_holdings() or []
    active = [h for h in holdings if _safe_float(h.get("shares")) > 0]

    # 获取估值数据
    from db.valuations import list_valuation_indexes, get_latest_valuation
    indexes = list_valuation_indexes()
    val_map = {}
    for idx in indexes:
        code = idx.get("index_code", "")
        name = idx.get("index_name", "")
        latest = get_latest_valuation(code)
        if latest:
            val_map[name] = latest

    # 为每只基金计算建议定投金额
    suggestions = []
    for h in active:
        name = h.get("fund_name", "")
        fund_type = h.get("fund_type", "")

        # 只对权益类基金做定投优化
        if fund_type in ["货币型", "债券型", "纯债"]:
            continue

        # 匹配估值
        matched_val = None
        for vn, vv in val_map.items():
            if vn in name or name in vn:
                matched_val = vv
                break

        # 基础定投金额
        base_amount = 1000

        # 估值调整
        val_multiplier = 1.0
        pe_pct = 0
        if matched_val:
            pe_pct = _safe_float(matched_val.get("pe_percentile"))
            if pe_pct <= 20:
                val_multiplier = 1.8  # 极度低估，加倍
            elif pe_pct <= 35:
                val_multiplier = 1.4
            elif pe_pct <= 50:
                val_multiplier = 1.0
            elif pe_pct <= 70:
                val_multiplier = 0.7
            elif pe_pct <= 85:
                val_multiplier = 0.4
            else:
                val_multiplier = 0.2  # 极度高估，大幅减少

        # 情绪调整
        emotion_multiplier = 1.0
        if fg_score <= 20:
            emotion_multiplier = 1.5  # 极度恐慌，贪婪买入
        elif fg_score <= 40:
            emotion_multiplier = 1.2
        elif fg_score <= 60:
            emotion_multiplier = 1.0
        elif fg_score <= 80:
            emotion_multiplier = 0.7
        else:
            emotion_multiplier = 0.4  # 极度贪婪，减少投入

        # 综合调整
        final_amount = round(base_amount * val_multiplier * emotion_multiplier)

        # 决策建议
        if val_multiplier >= 1.4 and emotion_multiplier >= 1.2:
            action = "加倍定投"
            reason = "低估+恐慌，历史最佳买入区间"
        elif val_multiplier >= 1.0 and emotion_multiplier >= 1.0:
            action = "正常定投"
            reason = "估值合理，情绪中性"
        elif val_multiplier <= 0.4 or emotion_multiplier <= 0.4:
            action = "暂停定投"
            reason = "高估或极度贪婪，等待回调"
        else:
            action = "减少定投"
            reason = "估值偏高或情绪偏热"

        suggestions.append({
            "fund_name": name,
            "fund_code": h.get("fund_code", ""),
            "pe_percentile": pe_pct,
            "base_amount": base_amount,
            "val_multiplier": val_multiplier,
            "emotion_multiplier": emotion_multiplier,
            "suggested_amount": final_amount,
            "action": action,
            "reason": reason,
        })

    # LLM 总结
    summary = ""
    try:
        if suggestions:
            lines = [f"- {s['fund_name']}: {s['action']} {s['suggested_amount']}元（{s['reason']}）" for s in suggestions[:5]]
            prompt = f"""你是定投顾问。根据以下定投优化建议，用3-5句话总结。

恐贪指数：{fg_score}（{fear_greed.get('zone', '中性')}）

定投建议：
{chr(10).join(lines)}

请用通俗语言解释为什么这样建议。不超过150字。"""
            resp = await asyncio.to_thread(lambda: _call_llm(
                caller="dca_optimizer", model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3, max_tokens=500,
            ))
            summary = resp.choices[0].message.content or ""
    except Exception as e:
        logger.warning(f"[dca] LLM总结失败: {e}")

    return {
        "fear_greed_score": fg_score,
        "fear_greed_zone": fear_greed.get("zone", "中性"),
        "suggestions": suggestions,
        "summary": summary,
    }


# ============ API 端点 ============

@router.get("/classify")
async def classify_api():
    """获取四笔钱归类结果。"""
    result = classify_pots()
    try:
        save_four_pots_candidates(result)
    except Exception as e:
        logger.warning(f"[four_pots] 保存建议候选失败: {e}")
    # 提取可执行行动
    try:
        from analysis.action_extractor import extract_actions, format_actions_for_response
        result["actions"] = format_actions_for_response(extract_actions("four_pots", result))
    except Exception as e:
        logger.warning(f"[four_pots] 行动提取失败: {e}")
        result["actions"] = []
    return {"status": "ok", "result": result}


@router.post("/dca-optimization")
async def dca_api():
    """获取定投优化建议。"""
    result = await calc_dca_optimization()
    try:
        save_dca_candidates(result)
    except Exception as e:
        logger.warning(f"[dca] 保存建议候选失败: {e}")
    return {"status": "ok", "result": result}
