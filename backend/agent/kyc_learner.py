"""KYC 画像对话中持续学习 — 从用户消息中提取 KYC 信号并更新画像

触发策略：仅当消息含风险/期限/资金等关键词时才调 LLM 提取，避免每轮都消耗 token。
提取结果带置信度，高置信度(>=0.7)直接回写 user_profiles，低置信度仅留痕累积。

与 kyc.py 的关系：
- kyc.py        提供 update_kyc_dimension（带留痕 + 高置信回写）
- kyc_learner   提供从自然语言消息中提取信号的能力，调用 kyc.update_kyc_dimension
"""

import json
import logging

from llm_service import _call_llm, MODEL
from db.config import get_config_int, get_config_float, get_config

logger = logging.getLogger(__name__)

# 触发关键词（命中任一才调 LLM，避免每轮都消耗 token）
KYC_SIGNAL_KEYWORDS = [
    # 风险 / 亏损
    "风险", "亏损", "亏", "跌", "回撤", "止损", "承受", "保守", "稳健", "激进", "进取",
    # 期限
    "长期", "短期", "中期", "多久", "几年", "持有",
    # 资金
    "资金", "本金", "万", "投入", "仓位", "加仓", "减仓",
    # 经验
    "新手", "进阶", "资深", "经验", "第一次",
    # 品种
    "指数", "基金", "债券", "股票", "黄金", "定投",
]

_VALID_DIMS = {"risk_tolerance", "investment_horizon", "capital_scale",
               "investment_experience", "loss_tolerance", "focus_assets"}


def should_extract(message: str) -> bool:
    """判断消息是否值得触发 KYC 信号提取（轻量关键词检测，不调 LLM）。"""
    if not message or len(message) < 4:
        return False
    return any(kw in message for kw in KYC_SIGNAL_KEYWORDS)


_EXTRACT_PROMPT = """你是用户投资画像分析助手。从用户的一句话中提取投资 KYC 维度信号。

可提取维度：
- risk_tolerance: 风险偏好（conservative保守 / steady稳健 / balanced平衡 / aggressive进取 / radical激进）
- investment_horizon: 投资期限（short<1年 / medium 1-3年 / long>3年）
- capital_scale: 资金体量（small<10万 / medium 10-100万 / large>100万）
- investment_experience: 投资经验（novice新手 / intermediate进阶 / advanced资深 / professional专业）
- loss_tolerance: 亏损承受度（low<5% / medium 5-15% / high>15%）
- focus_assets: 关注品种（数组，从 index/fund/bond/stock/gold/cash 中选）

用户消息：{message}

规则：
1. 只提取用户明确表达的信号，不要臆测
2. confidence 表示该信号的明确程度（0-1），含糊表达给低分
3. 无任何信号时返回空数组

只输出 JSON：{{"signals":[{{"dimension":"...","value":"...","confidence":0.0-1.0,"evidence":"原文片段"}}]}}"""


def extract_profile_signals(message: str, user_id: str = "default") -> list:
    """用 LLM 从用户消息提取 KYC 信号。返回 signals 列表。"""
    if not should_extract(message):
        return []
    try:
        response = _call_llm(
            caller="kyc_learner",
            model=MODEL,
            messages=[
                {"role": "system", "content": "你是精确的用户投资画像分析助手。只输出 JSON。"},
                {"role": "user", "content": _EXTRACT_PROMPT.format(message=message[:500])},
            ],
            temperature=get_config_float('llm.temperature_eval', 0.1),
            max_tokens=get_config_int('llm.max_tokens_eval_score', 400),
        )
        raw = response.choices[0].message.content.strip()
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw)
        signals = result.get("signals", [])
        return [s for s in signals
                if s.get("dimension") in _VALID_DIMS and s.get("value") not in (None, "", [])]
    except Exception as e:
        logger.error(f"提取 KYC 信号失败: {e}")
        return []


def learn_from_message(message: str, user_id: str = "default") -> int:
    """完整学习流程：检测 → 提取 → 更新画像。返回回写的维度数。

    设计为可在后台线程执行（调用方用 asyncio.to_thread 包装）。
    受 `llm_cost.kyc_learning` 开关控制，默认关闭。
    """
    if get_config("llm_cost.kyc_learning", "false") != "true":
        return 0
    signals = extract_profile_signals(message, user_id)
    if not signals:
        return 0
    from agent.kyc import update_kyc_dimension

    updated = 0
    for s in signals:
        dim = s["dimension"]
        val = s["value"]
        conf = float(s.get("confidence", 0.5))
        # focus_assets 的 value 可能是字符串，统一为列表
        if dim == "focus_assets" and isinstance(val, str):
            val = [v.strip() for v in val.replace("，", ",").split(",") if v.strip()]
        if update_kyc_dimension(user_id, dim, val, source="conversation", confidence=conf):
            updated += 1
    logger.info(f"KYC 学习: 从消息提取 {len(signals)} 个信号，回写 {updated} 个 (user_id={user_id})")
    return updated


def learn_from_eval_hints(hints: list, user_id: str = "default") -> int:
    """从 LLM 评测产出的 user_preference_hints 中学习 KYC 信号。

    hints: llm_evaluations.user_preference_hints（字符串列表）
    """
    if not hints:
        return 0
    from agent.kyc import update_kyc_dimension

    updated = 0
    for hint in hints:
        if not isinstance(hint, str) or not should_extract(hint):
            continue
        # 复用提取逻辑
        signals = extract_profile_signals(hint, user_id)
        for s in signals:
            dim = s["dimension"]
            val = s["value"]
            conf = float(s.get("confidence", 0.5))
            if dim == "focus_assets" and isinstance(val, str):
                val = [v.strip() for v in val.replace("，", ",").split(",") if v.strip()]
            if update_kyc_dimension(user_id, dim, val, source="eval_hint", confidence=conf):
                updated += 1
    return updated
