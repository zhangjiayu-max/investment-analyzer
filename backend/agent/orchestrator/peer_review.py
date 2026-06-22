"""多模型评审：决策评审、仲裁"""
import json
import logging

from llm_service import _call_llm
from config import ARBITRATION_API_KEY

logger = logging.getLogger(__name__)


# ── 多模型评审 ──

# ── 多模型评审 ──

_PEER_REVIEW_PROMPTS = {
    "suitability": """你是一位投资适当性审查员。请审查以下投资决策是否匹配用户的资金用途、投资期限和风险承受能力。

决策摘要：
{summary}

决策依据：
{rationale}

风险信息：
{risk_json}

用户画像：
{profile_text}

请返回 JSON：
{{
  "verdict": "approve | approve_with_concerns | reject | defer",
  "score": {{"suitability": 0-100}},
  "concerns": ["关注点1", ...],
  "suggestions": ["建议1", ...]
}}""",

    "evidence": """你是一位投资证据审查员。请审查以下投资决策的数据是否新鲜、是否有来源、是否过度依赖单一证据。

决策摘要：
{summary}

证据信息：
{evidence_json}

请返回 JSON：
{{
  "verdict": "approve | approve_with_concerns | reject | defer",
  "score": {{"evidence_quality": 0-100}},
  "concerns": ["关注点1", ...],
  "suggestions": ["建议1", ...]
}}""",

    "counter":"""你是一位投资反方观点审查员。请从"不应该做这笔投资"的角度提出最有力的反对理由。

决策摘要：
{summary}

决策依据：
{rationale}

请返回 JSON：
{{
  "verdict": "approve | approve_with_concerns | reject | defer",
  "score": {{"counter_argument_strength": 0-100}},
  "concerns": ["反对理由1", ...],
  "suggestions": ["风险缓释建议1", ...]
}}""",

    "overconfidence": """你是一位过度自信检测审查员。请检查以下投资决策是否把不确定判断说成确定结论。

决策摘要：
{summary}

决策依据：
{rationale}

置信度：{confidence}

请返回 JSON：
{{
  "verdict": "approve | approve_with_concerns | reject | defer",
  "score": {{"overconfidence_risk": 0-100}},
  "concerns": ["过度自信点1", ...],
  "suggestions": ["措辞修正建议1", ...]
}}""",
}


def run_peer_review(decision: dict, reviewer_type: str) -> dict | None:
    """运行单个维度的评审，返回结构化结果。

    Args:
        decision: 决策记录（dict）
        reviewer_type: suitability / evidence / counter / overconfidence

    Returns:
        {"verdict": ..., "score": ..., "concerns": [...], "suggestions": [...]} 或 None
    """
    from db import get_user_profile
    from agent.kyc import kyc_profile_to_text

    template = _PEER_REVIEW_PROMPTS.get(reviewer_type)
    if not template:
        return None

    profile = get_user_profile("default") or {}
    profile_text = kyc_profile_to_text(profile)

    prompt = template.format(
        summary=decision.get("summary", ""),
        rationale=decision.get("rationale", ""),
        risk_json=json.dumps(decision.get("risk_json", {}), ensure_ascii=False),
        evidence_json=json.dumps(decision.get("evidence_json", {}), ensure_ascii=False),
        profile_text=profile_text,
        confidence=decision.get("confidence", "medium"),
    )

    try:
        result = _call_llm(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=1000,
        )
        # 解析 JSON
        text = result if isinstance(result, str) else str(result)
        # 尝试提取 JSON
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            parsed = json.loads(match.group())
            return {
                "verdict": parsed.get("verdict", "approve"),
                "score": parsed.get("score", {}),
                "concerns": parsed.get("concerns", []),
                "suggestions": parsed.get("suggestions", []),
                "model_name": MODEL,
                "prompt_version": "v1",
            }
    except Exception as e:
        logger.error(f"评审 {reviewer_type} 失败: {e}")
    return None
