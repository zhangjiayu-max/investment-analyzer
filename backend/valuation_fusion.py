"""估值融合层 — 规则分 + LLM 二次判断

在 valuation._score_stock 的规则分基础上，调用 LLM 结合行业景气度/市场情绪/用户画像
做二次判断，可质疑或修正规则结论，并输出可解释溯源链（evidence_chain）。

设计动机：纯规则评分（PE/PB 阈值）无法考虑行业景气度、市场情绪、用户画像适配，
融合层让 LLM 在规则分基础上做"二次判断"，给出更立体、可解释的估值结论。
"""

import json
import logging

logger = logging.getLogger(__name__)


def fusion_score(stock_result: dict, kyc_profile: dict = None) -> dict:
    """LLM 融合层：在规则分基础上做二次判断。

    Args:
        stock_result: analyze_stock 的返回 dict（含 score/recommendation/basic_info/price_stats/valuation）
        kyc_profile: 用户 KYC 画像 dict（可选，用于画像适配判断）

    Returns:
        {
            "fusion_score": int,           # 融合后评分 0-100（越低越值得买）
            "fusion_rec": str,             # 融合建议
            "reasons": list[str],          # 调整理由
            "evidence_chain": list[dict],  # 可解释溯源 [{factor, weight, source, direction}]
            "confidence": float,           # 0-1
            "rule_score": int,             # 原规则分（对比用）
            "adjustment": int,             # 融合分 - 规则分（正=上调，负=下调）
        }
    """
    from llm_service import _call_llm, MODEL

    rule_score = stock_result.get("score", 50)
    rule_rec = stock_result.get("recommendation", "")
    name = stock_result.get("name", "")
    price_stats = stock_result.get("price_stats", {})
    valuation = stock_result.get("valuation", {})

    kyc_text = "未知"
    if kyc_profile:
        parts = []
        if kyc_profile.get("risk_tolerance"):
            parts.append(f"风险偏好:{kyc_profile['risk_tolerance']}")
        if kyc_profile.get("loss_tolerance"):
            parts.append(f"亏损承受:{kyc_profile['loss_tolerance']}")
        if kyc_profile.get("investment_horizon"):
            parts.append(f"投资期限:{kyc_profile['investment_horizon']}")
        kyc_text = " ".join(parts) or "未知"

    prompt = f"""你是估值分析融合引擎。规则系统已给出估值评分，请结合更多维度做二次判断。

标的：{name}
规则评分：{rule_score}/100（越低越值得买，规则建议：{rule_rec}）
基本面：PE={valuation.get('pe')} PB={valuation.get('pb')}
价格统计：{json.dumps(price_stats, ensure_ascii=False)[:300]}
用户画像：{kyc_text}

请结合以下维度二次判断（可质疑或修正规则结论）：
1. 行业景气度/基本面趋势（规则未考虑）
2. 市场情绪/资金面（规则未考虑）
3. 用户画像适配（风险偏好/期限是否匹配该标的）

输出 JSON：
{{
  "fusion_score": 0-100整数,
  "fusion_rec": "低估|合理偏低|合理|合理偏高|高估",
  "reasons": ["调整理由1", "调整理由2"],
  "evidence_chain": [
    {{"factor": "因素名", "weight": 0.3, "source": "数据来源", "direction": "看多|看空|中性"}}
  ],
  "confidence": 0.0-1.0
}}

只输出 JSON，不要其他文字。"""

    try:
        response = _call_llm(
            caller="valuation_fusion",
            model=MODEL,
            messages=[
                {"role": "system", "content": "你是估值分析融合引擎。只输出 JSON。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=2000,
        )
        raw = response.choices[0].message.content.strip()
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw)
        fusion_s = int(result.get("fusion_score", rule_score))
        return {
            "fusion_score": max(0, min(100, fusion_s)),
            "fusion_rec": result.get("fusion_rec", rule_rec),
            "reasons": result.get("reasons", []),
            "evidence_chain": result.get("evidence_chain", []),
            "confidence": float(result.get("confidence", 0.5)),
            "rule_score": rule_score,
            "adjustment": fusion_s - rule_score,
        }
    except Exception as e:
        logger.warning(f"估值融合失败: {e}")
        return {
            "fusion_score": rule_score,
            "fusion_rec": rule_rec,
            "reasons": [],
            "evidence_chain": [],
            "confidence": 0.0,
            "rule_score": rule_score,
            "adjustment": 0,
        }
