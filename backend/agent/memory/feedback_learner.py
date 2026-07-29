"""反馈学习模块 — 从用户反馈中提取偏好，自动更新用户画像"""

import json
import logging

from services.llm_service import _call_llm, MODEL_AUX
from db.config import get_config_int, get_config_float, get_config

logger = logging.getLogger(__name__)


def get_preference_context(user_id: str = "default") -> str:
    """
    获取用户偏好的 prompt 注入文本。

    返回约 300 token 的字符串，格式如：
    <user_preferences>
    [KYC 画像] 风险承受：balanced；投资期限：long；主要目标：积极增值；目标权益比：15%；...
    用户偏好：详细数据支撑，关注估值和风险。
    不喜欢：笼统建议、缺具体数字。
    </user_preferences>
    """
    from db import get_user_profile

    profile = get_user_profile(user_id)
    if not profile:
        return ""

    preferences = profile.get("preferences_json", "{}")
    feedback_summary = profile.get("feedback_summary", "")
    positive = profile.get("positive_patterns", "[]")
    negative = profile.get("negative_patterns", "[]")

    try:
        prefs = json.loads(preferences) if isinstance(preferences, str) else preferences
    except (json.JSONDecodeError, TypeError):
        prefs = {}

    try:
        pos = json.loads(positive) if isinstance(positive, str) else positive
    except (json.JSONDecodeError, TypeError):
        pos = []

    try:
        neg = json.loads(negative) if isinstance(negative, str) else negative
    except (json.JSONDecodeError, TypeError):
        neg = []

    parts = []

    # ── KYC 画像（核心字段，2026-07-29 补充）──
    # 此前只读 preferences_json 等 4 个字段，导致 risk_tolerance/primary_goal 等
    # 核心 KYC 字段从未注入 prompt，LLM 因"看不到用户已填 KYC"而频繁触发澄清（conv 191 案例）
    kyc_lines = _build_kyc_section(profile)
    if kyc_lines:
        parts.append(kyc_lines)

    # 偏好设置
    detail = prefs.get("preferred_detail_level")
    style = prefs.get("preferred_analysis_style")
    topics = prefs.get("topics_of_interest", [])

    if detail or style or topics:
        pref_parts = []
        if detail:
            level_map = {"concise": "简洁扼要", "moderate": "适度详细", "detailed": "详细数据支撑"}
            pref_parts.append(f"详细程度：{level_map.get(detail, detail)}")
        if style:
            style_map = {"data-driven": "数据驱动", "narrative": "叙述分析", "actionable": "可操作建议"}
            pref_parts.append(f"分析风格：{style_map.get(style, style)}")
        if topics:
            pref_parts.append(f"关注领域：{'、'.join(topics[:5])}")
        parts.append("用户偏好：" + "，".join(pref_parts))

    # 正面模式
    if pos:
        parts.append("用户喜欢：" + "；".join(pos[:3]))

    # 负面模式
    if neg:
        parts.append("用户不喜欢：" + "；".join(neg[:3]))

    # 反馈摘要
    if feedback_summary:
        parts.append(feedback_summary[:200])

    if not parts:
        return ""

    return "<user_preferences>\n" + "\n".join(parts) + "\n</user_preferences>"


# ── KYC 字段映射表 ───────────────────────────────────
# 数据库字段值 → 中文可读标签（LLM 友好）
_RISK_TOLERANCE_MAP = {
    "conservative": "保守型", "steady": "稳健型", "balanced": "平衡型",
    "aggressive": "进取型", "radical": "激进型",
}
_INVESTMENT_HORIZON_MAP = {
    "short": "短期（<1年）", "medium": "中期（1-3年）",
    "long": "长期（3-10年）", "very_long": "超长期（>10年）",
}
_INVESTMENT_EXPERIENCE_MAP = {
    "beginner": "新手", "intermediate": "进阶", "advanced": "资深", "expert": "专家",
}
_LOSS_TOLERANCE_MAP = {
    "low": "低（亏损<5%即焦虑）", "medium": "中（可接受10%亏损）",
    "high": "高（可接受20%+亏损）", "very_high": "极高（可接受30%+亏损）",
}
_PRIMARY_GOAL_MAP = {
    "wealth_preservation": "财富保值", "steady_growth": "稳健增值",
    "积极增值": "积极增值", "aggressive_growth": "积极增值",
    "retirement": "养老储备", "education": "教育储备",
}


def _build_kyc_section(profile: dict) -> str:
    """从 user_profiles 行构建 KYC 画像段。

    读取 7 个核心字段：风险承受/投资期限/主要目标/目标权益比/关注资产/行为偏差/损失容忍。
    若字段为空则跳过，避免输出"None"或空值。
    """
    kyc_parts = []

    risk = profile.get("risk_tolerance")
    if risk:
        kyc_parts.append(f"风险承受：{_RISK_TOLERANCE_MAP.get(risk, risk)}")

    horizon = profile.get("investment_horizon")
    if horizon:
        kyc_parts.append(f"投资期限：{_INVESTMENT_HORIZON_MAP.get(horizon, horizon)}")

    goal = profile.get("primary_goal")
    if goal:
        kyc_parts.append(f"投资目标：{_PRIMARY_GOAL_MAP.get(goal, goal)}")

    equity_ratio = profile.get("target_equity_ratio")
    if equity_ratio is not None and equity_ratio != "":
        try:
            ratio = float(equity_ratio)
            kyc_parts.append(f"目标权益比：{ratio:.0f}%")
        except (ValueError, TypeError):
            pass

    focus = profile.get("focus_assets")
    if focus:
        try:
            focus_list = json.loads(focus) if isinstance(focus, str) else focus
            if focus_list:
                focus_map = {"index": "指数", "fund": "基金", "bond": "债券",
                             "gold": "黄金", "stock": "股票", "crypto": "数字货币"}
                focus_zh = [focus_map.get(a, a) for a in focus_list]
                kyc_parts.append(f"关注资产：{'、'.join(focus_zh)}")
        except (json.JSONDecodeError, TypeError):
            pass

    biases = profile.get("behavior_biases")
    if biases:
        try:
            bias_list = json.loads(biases) if isinstance(biases, str) else biases
            if bias_list:
                kyc_parts.append(f"行为偏差：{'、'.join(bias_list[:4])}")
        except (json.JSONDecodeError, TypeError):
            pass

    loss_tol = profile.get("loss_tolerance")
    if loss_tol:
        kyc_parts.append(f"损失容忍：{_LOSS_TOLERANCE_MAP.get(loss_tol, loss_tol)}")

    exp = profile.get("investment_experience")
    if exp:
        kyc_parts.append(f"投资经验：{_INVESTMENT_EXPERIENCE_MAP.get(exp, exp)}")

    if not kyc_parts:
        return ""

    return "[KYC 画像] " + "；".join(kyc_parts)


def update_user_profile_from_feedback(user_id: str, feedback_type: str,
                                       note: str = "", input_summary: str = "") -> None:
    """
    从反馈事件中学习，更新用户画像。

    策略：
    - 每 5 次反馈或每次 negative 反馈时，调用 LLM 更新画像
    - 正面反馈积累到一定量后也触发更新

    受 `llm_cost.feedback_learning` 开关控制，默认关闭。
    """
    if get_config("llm_cost.feedback_learning", "false") != "true":
        return
    from db import increment_feedback_count, get_user_profile, update_user_profile

    count = increment_feedback_count(user_id)
    is_negative = feedback_type == "unhelpful"

    # 负面反馈立即更新，正面反馈每 5 次更新一次
    if not is_negative and count % 5 != 0:
        return

    profile = get_user_profile(user_id)
    if not profile:
        return

    current_prefs = profile.get("preferences_json", "{}")
    current_summary = profile.get("feedback_summary", "")
    current_positive = profile.get("positive_patterns", "[]")
    current_negative = profile.get("negative_patterns", "[]")

    # 构建 LLM 更新提示
    signal = f"反馈类型: {feedback_type}\n"
    if input_summary:
        signal += f"用户问题: {input_summary[:200]}\n"
    if note:
        signal += f"用户备注: {note}\n"

    prompt = f"""你是一个用户偏好分析助手。根据用户的反馈信号，更新用户画像。

当前画像:
- 偏好设置: {current_prefs}
- 正面模式: {current_positive}
- 负面模式: {current_negative}
- 反馈摘要: {current_summary}

新的反馈信号:
{signal}

请输出 JSON 更新（只输出 JSON，不要其他文字）:
{{
  "preferences": {{
    "preferred_detail_level": "concise|moderate|detailed",
    "preferred_analysis_style": "data-driven|narrative|actionable",
    "topics_of_interest": ["领域1", "领域2"]
  }},
  "positive_patterns": ["用户喜欢的特点1", "用户喜欢的特点2"],
  "negative_patterns": ["用户不喜欢的特点1"],
  "feedback_summary": "一句话总结用户的偏好特点"
}}"""

    try:
        response = _call_llm(
            caller="feedback_learner",
            model=MODEL_AUX,
            messages=[
                {"role": "system", "content": "你是一个精确的用户偏好分析助手。只输出 JSON。"},
                {"role": "user", "content": prompt},
            ],
            temperature=get_config_float('llm.temperature_eval', 0.2),
            max_tokens=get_config_int('llm.max_tokens_eval_score', 500),
        )

        raw = response.choices[0].message.content.strip()
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        result = json.loads(raw)

        # 更新画像
        new_prefs = result.get("preferences", {})
        if new_prefs:
            update_user_profile(user_id, preferences_json=json.dumps(new_prefs, ensure_ascii=False))

        new_positive = result.get("positive_patterns", [])
        if new_positive:
            # 合并去重，保留最近 10 条
            merged_pos = list(dict.fromkeys(new_positive + (json.loads(current_positive) if isinstance(current_positive, str) else current_positive)))
            update_user_profile(user_id, positive_patterns=json.dumps(merged_pos[:10], ensure_ascii=False))

        new_negative = result.get("negative_patterns", [])
        if new_negative:
            merged_neg = list(dict.fromkeys(new_negative + (json.loads(current_negative) if isinstance(current_negative, str) else current_negative)))
            update_user_profile(user_id, negative_patterns=json.dumps(merged_neg[:10], ensure_ascii=False))

        new_summary = result.get("feedback_summary", "")
        if new_summary:
            update_user_profile(user_id, feedback_summary=new_summary)

        logger.info(f"用户画像已更新 (user_id={user_id}, feedback_count={count})")

    except Exception as e:
        logger.error(f"更新用户画像失败: {e}")
