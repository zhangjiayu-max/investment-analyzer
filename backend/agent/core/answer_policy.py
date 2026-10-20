"""按用户意图加载数据库中的回答边界与归因职责配置。"""

import json
import logging
import re

logger = logging.getLogger(__name__)

ATTRIBUTION_ROLE_ORDER = (
    "market_analyst", "risk_assessor", "macro_strategist", "industry_fundamentalist",
    "fund_analyst", "valuation_expert", "allocation_advisor",
)
_ACTION_VERBS = r"(?:买入|买|卖出|卖|加仓|减仓|补仓|建仓|清仓|止盈|止损|定投|调仓|换仓|抄底|上车|下车)"


def user_intent_text(query: str) -> str:
    """职责说明不应改变原问题的交易意图。"""
    return (query or "").split("\n\n【专家职责】", 1)[0]


def has_attribution_intent(query: str) -> bool:
    query = user_intent_text(query)
    return bool(re.search(r"为什么|为何|怎么回事|原因|驱动|归因|导致|做空", query))


def has_explicit_action_intent(query: str) -> bool:
    """区分用户的操作诉求与资金净买卖、机构卖出等行情叙述。"""
    query = user_intent_text(query)
    request = re.search(
        rf"(?:我|我们|帮我|建议|是否|要不要|该不该|能不能|可不可以|可以|应该|需要|准备|打算|计划|如何|怎么|能|该|想)[^，,。？！?！\n]{{0,8}}{_ACTION_VERBS}",
        query.replace("怎么回事", ""),
    )
    if request:
        return True
    for clause in re.split(r"[，,。？！?！\n]", query):
        if not has_attribution_intent(clause) and re.search(
            rf"{_ACTION_VERBS}.{{0,16}}(?:吗|合适|可行|建议|多少|方案)", clause,
        ):
            return True
    if has_attribution_intent(query):
        return False
    # 保留简短指令与持仓诊断类已有路由；市场净买卖不属于用户交易指令。
    market_text = re.sub(r"(?:净买入|净卖出|净买|净卖|卖空|买盘|卖盘|卖压|回购|买卖额)", "", query)
    if re.search(r"(?:外资|南向|北向|机构|主力|资金).{0,10}(?:买入|卖出)", market_text):
        return False
    return bool(re.search(_ACTION_VERBS, market_text) or any(
        kw in query for kw in ("配置分析", "持仓分析", "持仓配置", "整体持仓", "配置建议", "仓位调整", "配置调整", "组合优化")
    ))


def order_attribution_specialists(specialists: list[str]) -> list[str]:
    """在任何人数截断前保留市场证据与反证职责，顺序确定。"""
    priority = {key: i for i, key in enumerate(ATTRIBUTION_ROLE_ORDER)}
    return sorted(dict.fromkeys(specialists), key=lambda key: (priority.get(key, 99), key))


def _load_policy(name: str) -> str:
    # 缺表/尚未执行迁移时读取同一份种子；运行时绝不写库。
    from db.analysis import ANSWER_POLICY_PRESETS, get_analysis_agent_by_name
    try:
        agent = get_analysis_agent_by_name(name)
        if agent and agent.get("system_prompt"):
            return agent["system_prompt"]
    except Exception as exc:
        logger.warning("回答策略配置加载失败 %s: %s", name, exc)
    return next((prompt for key, _, prompt in ANSWER_POLICY_PRESETS if key == name), "")


def build_answer_policy(query: str) -> str:
    """专家、交叉审阅、综合入口共用；配置由 analysis_agents 管理。"""
    parts = [_load_policy("投资回答证据边界")]
    if has_explicit_action_intent(query):
        parts.append(_load_policy("交易建议回答约束"))
    elif has_attribution_intent(query):
        parts.append(_load_policy("行情归因回答约束"))
    else:
        parts.append(_load_policy("解释型回答约束"))
    return "\n\n" + "\n\n".join(part for part in parts if part)


def get_attribution_subquery(agent_key: str, query: str) -> str:
    """职责文案与回答约束一样可在 analysis_agents 中编辑。"""
    try:
        roles = json.loads(_load_policy("行情归因职责分工"))
    except (TypeError, ValueError):
        logger.warning("行情归因职责分工配置不是有效 JSON")
        return ""
    return roles.get(agent_key, roles.get("default", ""))
