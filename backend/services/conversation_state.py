"""对话态追踪 — 多轮对话不丢上下文。

从最近消息中提取对话状态，注入到每轮LLM调用的system message前。
"""
from __future__ import annotations


def build_conversation_state(
    conversation_id: int,
    current_user_message: str,
    messages: list[dict],
) -> dict:
    """从最近消息中提取对话状态，用于多轮上下文追踪。

    返回:
    {
        "asked_topics": ["沪深300", "中证500"],
        "last_analysis_type": "valuation",
        "pending_comparison": False,
        "mentioned_actions": ["买入", "定投"],
        "user_focus": "估值对比",
    }
    """
    state = {
        "asked_topics": [],
        "last_analysis_type": None,
        "pending_comparison": False,
        "mentioned_actions": [],
        "user_focus": None,
    }
    if not messages:
        return state

    # 合并最近5条消息内容
    recent_text = " ".join(
        (m.get("content") or "") for m in messages[-5:] if m.get("content")
    )
    combined = (recent_text + " " + (current_user_message or "")).strip()
    if not combined:
        return state

    # 常见指数/基金主题关键词
    topic_keywords = [
        "沪深300", "中证500", "中证1000", "创业板", "科创50",
        "白酒", "医药", "消费", "科技", "新能源", "半导体",
        "军工", "煤炭", "银行", "券商", "红利", "债券",
        "恒生", "纳斯达克", "标普", "纳指", "德指",
        "中证白酒", "医药50", "中证红利", "电力指数", "中证中药",
        "食品饮料", "有色金属", "房地产", "计算机", "传媒",
    ]
    for kw in topic_keywords:
        if kw in combined and kw not in state["asked_topics"]:
            state["asked_topics"].append(kw)

    # 分析类型检测
    if any(w in combined for w in ["估值", "PE", "PB", "分位", "百分位"]):
        state["last_analysis_type"] = "valuation"
    elif any(w in combined for w in ["对比", "哪个好", "vs", "还是", "比较"]):
        state["last_analysis_type"] = "comparison"
        state["pending_comparison"] = True
    elif any(w in combined for w in ["风险", "回撤", "波动"]):
        state["last_analysis_type"] = "risk"
    elif any(w in combined for w in ["配置", "仓位", "比例"]):
        state["last_analysis_type"] = "allocation"

    # 提及的操作
    action_keywords = ["买入", "卖出", "定投", "加仓", "减仓", "清仓", "止盈", "止损"]
    for ak in action_keywords:
        if ak in combined and ak not in state["mentioned_actions"]:
            state["mentioned_actions"].append(ak)

    # 用户关注点
    if state["pending_comparison"]:
        state["user_focus"] = "对比决策"
    elif state["last_analysis_type"]:
        state["user_focus"] = state["last_analysis_type"]

    return state


def format_conversation_state(state: dict) -> str:
    """将对话状态格式化为注入文本。"""
    if not state or not state.get("asked_topics"):
        return ""
    lines = ["【对话上下文提示】"]
    if state.get("asked_topics"):
        lines.append(f"本轮已讨论: {', '.join(state['asked_topics'])}")
    if state.get("last_analysis_type"):
        type_labels = {
            "valuation": "估值分析",
            "comparison": "对比分析",
            "risk": "风险评估",
            "allocation": "资产配置",
        }
        label = type_labels.get(state["last_analysis_type"], state["last_analysis_type"])
        lines.append(f"上一轮分析类型: {label}")
    if state.get("pending_comparison"):
        lines.append("⚠️ 用户正在做对比，请直接给出偏好判断，不要说'各有优劣'")
    if state.get("mentioned_actions"):
        lines.append(f"提及的操作: {', '.join(state['mentioned_actions'])}")
    return "\n".join(lines)
