"""Orchestrator - 主控 Agent,协调各专家 Agent 完成分析"""

import json
import logging
import re
import time
import threading
import concurrent.futures
import asyncio
import hashlib
from datetime import datetime

from services.llm_service import client, MODEL, _call_llm, _parse_tool_args
from agent.multi_agent import run_specialist, run_specialist_with_context, run_cross_review_opinion, _build_portfolio_summary
from agent.blackboard import Blackboard, extract_entry_from_result
from db.agents import (
    load_specialist_agents,
    create_pending_agent_run,
    update_agent_run_status,
    get_completed_agents_for_message,
    cancel_running_agents,
)
from db.config import get_config, get_config_int, get_config_float
from agent.orchestrator_optimizer import OrchestratorOptimizer, ParallelExecutor
from agent.cache import expert_cache
from services.conversation_context import record_entity_snapshots


# ═══════════════════════════════════════════════════════════════
# 增强1: 状态机检查点
# ═══════════════════════════════════════════════════════════════

def _save_checkpoint(conv_id: int, message_id: int, phase: str, state: dict):
    """保存编排检查点到数据库。"""
    if not conv_id or not message_id:
        return
    try:
        from db._conn import _get_conn
        conn = _get_conn()
        conn.execute("""
            INSERT OR REPLACE INTO orchestration_checkpoints (conv_id, message_id, phase, state_json)
            VALUES (?, ?, ?, ?)
        """, (conv_id, message_id, phase, json.dumps(state, ensure_ascii=False)))
        conn.commit()
        conn.close()
        logger.info(f"检查点已保存: conv={conv_id} msg={message_id} phase={phase}")
    except Exception as e:
        logger.warning(f"保存检查点失败: {e}")


def _load_checkpoint(conv_id: int, message_id: int) -> dict | None:
    """从数据库加载最新检查点。"""
    if not conv_id or not message_id:
        return None
    try:
        from db._conn import _get_conn
        conn = _get_conn()
        row = conn.execute("""
            SELECT phase, state_json FROM orchestration_checkpoints
            WHERE conv_id = ? AND message_id = ?
            ORDER BY id DESC LIMIT 1
        """, (conv_id, message_id)).fetchone()
        conn.close()
        if row:
            state = json.loads(row["state_json"])
            state["phase"] = row["phase"]
            return state
    except Exception as e:
        logger.warning(f"加载检查点失败: {e}")
    return None


# ═══════════════════════════════════════════════════════════════
# 增强2: 动态 Agent 选择
# ═══════════════════════════════════════════════════════════════

def _count_keyword(text: str, keywords: list[str]) -> int:
    """统计文本中关键词出现次数。"""
    count = 0
    for kw in keywords:
        count += text.count(kw)
    return count


def _detect_position_over_concentration(result: dict) -> bool:
    """检测持仓集中度问题。"""
    analysis = result.get("analysis", "")
    concentration_kws = ["集中度", "单一持仓", "集中持仓", "重仓", "占比过高"]
    return _count_keyword(analysis, concentration_kws) >= 2


DYNAMIC_SPAWN_RULES = [
    {
        "detect": lambda result: _count_keyword(result.get("analysis", ""), ["风险", "回撤", "波动"]) > 3,
        "suggest": "risk_assessor",
        "reason": "前一专家多次提及风险,建议追加风险评估",
        "priority": "high",
    },
    {
        "detect": lambda result: any(kw in result.get("analysis", "") for kw in ["港股", "美股", "QDII", "纳斯达克", "标普"] ),
        "suggest": "market_analyst",
        "reason": "涉及海外市场,建议追加市场分析",
        "priority": "medium",
    },
    {
        "detect": lambda result: _detect_position_over_concentration(result),
        "suggest": "allocation_advisor",
        "reason": "检测到持仓集中度问题,建议追加配置分析",
        "priority": "high",
    },
]


def _check_dynamic_spawn(result: dict, already_called: set[str]) -> list[dict]:
    """检测是否需要动态追加专家。"""
    suggestions = []
    for rule in DYNAMIC_SPAWN_RULES:
        try:
            if rule["suggest"] not in already_called and rule["detect"](result):
                suggestions.append({
                    "agent_key": rule["suggest"],
                    "reason": rule["reason"],
                    "priority": rule["priority"],
                })
        except Exception:
            pass
    return suggestions


# ═══════════════════════════════════════════════════════════════
# 增强3: SOP 模板
# ═══════════════════════════════════════════════════════════════

SOP_TEMPLATES = {
    "full_diagnosis": {
        "name": "完整持仓诊断",
        "trigger_keywords": ["完整诊断", "全面分析", "整体评估", "持仓体检", "帮我看看整体", "综合诊断"],
        "steps": [
            {"agent": "valuation_expert", "task": "逐只评估持仓估值水位", "order": 1, "group": 0},
            {"agent": "risk_assessor", "task": "基于估值识别主要风险暴露", "order": 1, "group": 0},
            {"agent": "allocation_advisor", "task": "综合估值和风险评估配置合理性", "order": 2, "group": 1},
            {"agent": "risk_assessor", "task": "从行为金融角度审视操作偏差", "order": 2, "group": 1},
        ],
        "cross_review": True,
        "arbitration": True,
        "final_agent": "allocation_advisor",
    },
    "buy_decision": {
        "name": "买入决策检查清单",
        "trigger_keywords": ["可以买", "值得买", "要不要买", "该买入", "能买入", "适合买"],
        "steps": [
            {"agent": "valuation_expert", "task": "判断标的估值水平", "order": 1, "group": 0},
            {"agent": "risk_assessor", "task": "评估买入后组合风险变化", "order": 1, "group": 0},
            {"agent": "allocation_advisor", "task": "计算买入后仓位和现金是否合理", "order": 2, "group": 1},
        ],
        "cross_review": True,
        "arbitration": False,
        "final_agent": "allocation_advisor",
    },
    "sell_decision": {
        "name": "卖出决策检查清单",
        "trigger_keywords": ["可以卖", "要不要卖", "该卖出", "止损", "止盈", "需要卖"],
        "steps": [
            {"agent": "valuation_expert", "task": "判断当前估值是否过高", "order": 1, "group": 0},
            {"agent": "risk_assessor", "task": "评估持有风险vs卖出机会成本", "order": 1, "group": 0},
            {"agent": "risk_assessor", "task": "检查是否情绪化决策", "order": 2, "group": 1},
        ],
        "cross_review": True,
        "arbitration": True,
        "final_agent": "risk_assessor",
    },
    "dca_plan": {
        "name": "定投方案设计",
        "trigger_keywords": ["定投方案", "定投计划", "定投策略", "怎么定投"],
        "steps": [
            {"agent": "valuation_expert", "task": "筛选低估的定投标的", "order": 1, "group": 0},
            {"agent": "allocation_advisor", "task": "基于估值和现金制定金额分配", "order": 2, "group": 1},
        ],
        "cross_review": False,
        "arbitration": False,
        "final_agent": "allocation_advisor",
    },
}


def match_sop_template(query: str, has_portfolio: bool = False) -> dict | None:
    """根据用户查询匹配 SOP 模板(关键词匹配,零 LLM 调用)。"""
    for key, template in SOP_TEMPLATES.items():
        if any(kw in query for kw in template["trigger_keywords"]):
            return template
    # 场景推断:有持仓 + 复杂查询 → 走完整诊断
    if has_portfolio and _is_complex_query(query):
        return SOP_TEMPLATES["full_diagnosis"]
    return None


def _is_complex_query(query: str) -> bool:
    """判断是否为复杂查询(用于 SOP 推断)。"""
    complex_kws = ["分析", "建议", "方案", "策略", "优化", "调整", "怎么看"]
    invest_kws = ["持仓", "基金", "配置", "投资", "组合"]
    has_complex = any(kw in query for kw in complex_kws)
    has_invest = any(kw in query for kw in invest_kws)
    return has_complex and has_invest and len(query) > 15


# ═══════════════════════════════════════════════════════════════
# 增强6: 成本感知路由
# ═══════════════════════════════════════════════════════════════

# 默认模型映射(可被 system_config 覆盖)
# DeepSeek 模型映射
_AGENT_MODEL_MAP_DEEPSEEK = {
    "valuation_expert": "deepseek-v4-flash",
    "allocation_advisor": "deepseek-v4-flash",
    "fund_analyst": "deepseek-v4-flash",
    "risk_assessor": "deepseek-v4-pro",
    "market_analyst": "deepseek-v4-flash",
    "orchestrator": "deepseek-v4-pro",
    "cross_review": "deepseek-v4-flash",
}

# MIMO 模型映射 — MIMO 只有一个模型，所有 Agent 统一使用
_AGENT_MODEL_MAP_MIMO = {
    "valuation_expert": "mimo-v2.5-pro",
    "allocation_advisor": "mimo-v2.5-pro",
    "fund_analyst": "mimo-v2.5-pro",
    "risk_assessor": "mimo-v2.5-pro",
    "market_analyst": "deepseek-v4-flash",
    "orchestrator": "mimo-v2.5-pro",
    "cross_review": "mimo-v2.5-pro",
}

# 兼容别名
AGENT_MODEL_MAP = _AGENT_MODEL_MAP_DEEPSEEK


def _get_model_for_agent(agent_key: str, budget_mode: str = "normal") -> str:
    """根据 agent_key 和预算模式选择模型。优先读 system_config。

    自动感知当前 LLM provider：
    - LLM_PROVIDER=deepseek → 使用 DeepSeek 模型映射
    - LLM_PROVIDER=mimo → 使用 MIMO 模型映射（避免模型名不匹配导致 404）
    """
    from db.config import get_config
    from config import LLM_PROVIDER

    model_map = _AGENT_MODEL_MAP_MIMO if LLM_PROVIDER == "mimo" else _AGENT_MODEL_MAP_DEEPSEEK
    default_model = model_map.get(agent_key, MODEL)

    # conservative 模式：所有 Agent 用同一个省钱模型
    if budget_mode == "conservative":
        default_conservative = "mimo-v2.5-pro" if LLM_PROVIDER == "mimo" else "deepseek-v4-flash"
        configured = get_config("cost_routing.conservative_model", "")
        # 验证配置的模型是否兼容当前 provider
        if configured and _is_model_compatible(configured, LLM_PROVIDER):
            return configured
        return default_conservative

    # 从 system_config 读取(可运行时覆盖)
    config_key = f"cost_routing.{agent_key}_model"
    configured = get_config(config_key, "")
    if configured and _is_model_compatible(configured, LLM_PROVIDER):
        return configured

    return default_model


def _is_model_compatible(model_name: str, provider: str) -> bool:
    """检查模型名是否与当前 provider 兼容。"""
    if not model_name:
        return False
    if provider == "mimo":
        # MIMO 模式下允许 deepseek-v4-flash 用于 market_analyst（跨provider回退）
        return not model_name.startswith("deepseek") or model_name == "deepseek-v4-flash"
    # DeepSeek 模式下不接受 mimo 模型名
    return not model_name.startswith("mimo")


def _is_cost_routing_enabled() -> bool:
    """检查成本路由是否启用。"""
    from db.config import get_config
    return get_config("cost_routing.enabled", "true") == "true"


# ═══════════════════════════════════════════════════════════════
# 增强5: 人在回路(Human-in-the-Loop)
# ═══════════════════════════════════════════════════════════════

# 全局确认等待存储:confirm_id → Event + result
_confirm_store: dict[str, dict] = {}


def _check_human_in_loop_routing(complexity: str, confidence: float, specialist_count: int) -> dict | None:
    """检查是否需要路由确认(complex + 低置信度)。"""
    if complexity == "complex" and confidence < 0.8:
        return {
            "type": "confirm",
            "node": "routing_confirmation",
            "question": f"计划调用 {specialist_count} 个专家分析,预计耗时 2-3 分钟。是否继续?",
            "options": [
                {"value": "continue", "label": "继续"},
                {"value": "simplify", "label": "简化为快速分析"},
            ],
            "timeout": 30,
        }
    return None


def _check_human_in_loop_trade(analysis_text: str) -> dict | None:
    """检查分析结果是否包含买卖建议。"""
    buy_kws = ["建议买入", "可以买入", "推荐买入", "适合买入"]
    sell_kws = ["建议卖出", "应该卖出", "建议减仓", "建议止损", "建议止盈"]
    action = None
    if any(kw in analysis_text for kw in sell_kws):
        action = "卖出"
    elif any(kw in analysis_text for kw in buy_kws):
        action = "买入"
    if action:
        return {
            "type": "confirm",
            "node": "trade_confirmation",
            "question": f"分析建议{action}。是否保存为决策草案?",
            "options": [
                {"value": "save", "label": "保存为决策草案"},
                {"value": "continue", "label": "继续查看"},
                {"value": "ignore", "label": "忽略"},
            ],
            "timeout": 30,
        }
    return None


def _check_human_in_loop_conflict(specialist_results: list) -> dict | None:
    """检查专家结论是否完全冲突。"""
    if len(specialist_results) < 2:
        return None
    # 提取评级方向
    buy_agents = []
    sell_agents = []
    for sr in specialist_results:
        analysis = sr.get("analysis", "")
        agent_name = sr.get("agent", "")
        if any(kw in analysis for kw in ["建议买入", "可以买入", "低估", "适合买入"]):
            buy_agents.append(agent_name)
        elif any(kw in analysis for kw in ["建议卖出", "建议回避", "高估", "应该卖出", "风险过高"]):
            sell_agents.append(agent_name)
    if buy_agents and sell_agents:
        buy_str = "、".join(buy_agents)
        sell_str = "、".join(sell_agents)
        return {
            "type": "confirm",
            "node": "conflict_confirmation",
            "question": f"{buy_str}建议买入,但{sell_str}建议回避。请确认:继续请仲裁裁决 / 我倾向买或不买?",
            "options": [
                {"value": "arbitrate", "label": "请仲裁裁决"},
                {"value": "lean_buy", "label": "我倾向买"},
                {"value": "lean_skip", "label": "我倾向不买"},
            ],
            "timeout": 30,
        }
    return None


def wait_for_confirm(confirm_id: str, timeout: int = 30) -> str:
    """等待用户确认,超时返回默认值 'continue'。"""
    entry = _confirm_store.get(confirm_id)
    if not entry:
        return "continue"
    event = entry["event"]
    event.wait(timeout=timeout)
    return entry.get("result", "continue")


def resolve_confirm(confirm_id: str, user_choice: str):
    """前端调用:用户做出选择后解析确认。"""
    entry = _confirm_store.get(confirm_id)
    if entry:
        entry["result"] = user_choice
        entry["event"].set()


def _execute_specialist_cached(tool_name: str, query: str,
                                cancel_event=None, prebuilt_context: str = "",
                                budget_mode: str = "normal", trace_id: str = "") -> str:
    """带缓存的专家执行。同一专家+同一上下文 5 分钟内复用，支持语义缓存。"""
    from db.config import get_config_int, get_config_float

    # 从 tool_name 提取 agent_key(去掉 consult_ 前缀)
    agent_key = tool_name.replace("consult_", "") if tool_name.startswith("consult_") else tool_name

    # 缺口 4：上下文隔离 — 按 agent_key 过滤上下文（白名单+优先级+预算填充）
    try:
        from services.conversation_context import filter_context_for_agent
        prebuilt_context = filter_context_for_agent(prebuilt_context, agent_key)
    except Exception as _e:
        logger.debug(f"上下文过滤跳过: {_e}")

    context_hash = hashlib.md5(prebuilt_context.encode("utf-8")).hexdigest()[:16] if prebuilt_context else ""

    # 应用配置覆盖默认缓存参数（线程安全）
    try:
        ttl = get_config_int("cache.ttl_minutes", 5) * 60
        threshold = get_config_float("cache.similarity_threshold", 0.92)
        expert_cache.update_config(ttl_seconds=ttl, semantic_threshold=threshold)
    except Exception:
        pass

    cached = expert_cache.get(query, agent_key, context_hash=context_hash)
    if cached is not None:
        logger.info(f"专家缓存命中: {tool_name}")
        cached["_cached"] = True
        return json.dumps(cached, ensure_ascii=False)

    result_str = _execute_specialist(tool_name, query,
                                     cancel_event=cancel_event,
                                     prebuilt_context=prebuilt_context,
                                     budget_mode=budget_mode,
                                     trace_id=trace_id)
    try:
        obj = json.loads(result_str)
        if "error" not in obj:
            expert_cache.put(query, agent_key, obj, context_hash=context_hash)
    except Exception:
        pass
    return result_str


# 全局超时限制(秒)
MAX_ORCHESTRATION_SECONDS = 1800  # 30 分钟
from agent.feedback_learner import get_preference_context
from agent.memory import (
    compress_history_semantic, build_user_memory_context,
    get_token_budget, compress_rag_token_aware, estimate_tokens,
)
from agent.router import SmartRouter
from agent.validator import LightValidator

_router = SmartRouter()
_validator = LightValidator()


def _build_portfolio_summary(prebuilt_context: str = "") -> str:
    """从预构建上下文提取前 4000 字摘要；无参数时回退到 multi_agent 版本。"""
    if prebuilt_context:
        return prebuilt_context[:4000]
    # 回退到 multi_agent.py 的实现（从 DB 读取持仓）
    from agent.multi_agent import _build_portfolio_summary as _build_ps_db
    return _build_ps_db()


def _build_history_summary(history: list) -> str:
    """压缩历史为摘要。"""
    if not history:
        return ""
    recent = history[-3:]
    parts = []
    for msg in recent:
        role = msg.get("role", "")
        content = msg.get("content", "")[:200]
        parts.append(f"{role}: {content}")
    return "\n".join(parts)


def _build_portfolio_context() -> str:
    """构建用户持仓上下文，注入每个专家的 system_prompt。

    包含：基金名、持仓占比、盈亏率、成本价、当前价。
    让专家在推荐加减仓时能看到用户实际持仓，避免"估值低就加仓但不看已亏损"。
    """
    try:
        from db.portfolio import get_portfolio_summary
        summary = get_portfolio_summary()
        if not summary or not summary.get("holdings"):
            return "【当前持仓】无持仓数据"

        # 只展示活跃持仓（份额 > 0），summary["holdings"] 包含已清仓记录
        active = [h for h in summary["holdings"] if (h.get("shares") or 0) > 0]
        if not active:
            return "【当前持仓】无持仓数据"

        lines = ["【当前持仓】"]
        total_cost = summary.get("total_cost", 0)
        total_value = summary.get("total_value", 0)
        total_pnl = total_value - total_cost
        total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0
        lines.append(f"总成本: ¥{total_cost:,.0f} | 总市值: ¥{total_value:,.0f} | 总盈亏: ¥{total_pnl:,.0f} ({total_pnl_pct:+.1f}%)")
        lines.append("")

        for h in active[:10]:  # 最多 10 条
            name = h.get("fund_name", h.get("fund_code", ""))
            code = h.get("fund_code", "")
            shares = h.get("shares", 0)
            cost = h.get("cost_price", 0)
            current = h.get("current_price", 0)
            value = h.get("current_value", shares * current)
            pnl = (current - cost) * shares if cost > 0 else 0
            pnl_pct = ((current - cost) / cost * 100) if cost > 0 else 0
            weight = (value / total_value * 100) if total_value > 0 else 0
            lines.append(
                f"- {name}({code}): 持仓{shares:,.0f}份 | 成本{cost:.4f} | 现价{current:.4f} | "
                f"市值¥{value:,.0f} | 占比{weight:.1f}% | 盈亏¥{pnl:,.0f}({pnl_pct:+.1f}%)"
            )
        return "\n".join(lines)
    except Exception as e:
        logger.warning(f"构建持仓上下文失败: {e}")
        return ""


def _build_dca_rules() -> str:
    """构建 4% 定投法规则和减仓约束，注入估值分析师和资产配置师的 prompt。

    规则来源：daily_advice.base_dca_amount、daily_advice.dca_drop_step_pct、daily_advice.max_dca_steps
    """
    try:
        from db.config import get_config
        base_amount = get_config("daily_advice.base_dca_amount", "500")
        drop_step = get_config("daily_advice.dca_drop_step_pct", "4")
        max_steps = get_config("daily_advice.max_dca_steps", "3")

        return f"""【加减仓规则约束（必须遵守）】

## 4% 定投法（加仓规则）
- 基础定投金额：¥{base_amount}/档
- 跌幅档位：每跌 {drop_step}% 加一档
- 最大档数：{max_steps} 档（即最大加仓 ¥{int(base_amount) * int(max_steps)}）
- 加仓前提：估值百分位 ≤ 35% 且不在补仓冷静期（10 天内）
- 加仓金额计算：¥{base_amount} × 档数（跌幅/{drop_step}%，向下取整，上限 {max_steps} 档）

## 减仓约束（必须遵守）
- 单次减仓不超过该基金持仓的 20%
- 单次建议总减仓金额不超过总资产的 10%
- 禁止同时减仓 2 个以上基金
- 禁止一次性减仓超过 ¥50,000
- 减仓前提：估值百分位 ≥ 80% 且有明确止盈信号

## 禁止事项
- 禁止在未查看用户持仓盈亏的情况下建议加仓
- 禁止建议加仓已超持仓上限（25%）的基金
- 禁止建议减仓亏损基金（除非风险止损场景）"""
    except Exception as e:
        logger.warning(f"构建定投规则失败: {e}")
        return ""


def _build_final_synthesis_prompt(specialist_results: list, routed_specialists: list) -> str:
    """构建最终综合提示，强制 LLM 只引用实际执行了的专家。"""
    executed_keys = {sr.get("agent_key", "") for sr in specialist_results}
    executed_names = {sr.get("agent", "") for sr in specialist_results}
    routed_but_not_executed = [s for s in routed_specialists if s not in executed_keys]

    prompt = (
        "请根据以上各专家的分析结果，给出最终的综合投资建议，"
        "以用户的私人投资顾问视角呈现。\n\n"
        "## 回复结构要求\n"
        "1. 结论先行：第一段直接给出核心判断和操作建议\n"
        "2. 数据支撑：关键数据用表格呈现，必须标注来源\n"
        "3. 操作建议：具体的加减仓建议（金额/比例/时机）\n"
        "4. 风险提示：可能的风险场景和应对措施\n"
        "5. 置信度标注：在结论后标注[高置信度/中置信度/低置信度]\n\n"
        "## 综合要求\n\n"
        "### 1. 用户视角\n"
        "- 使用「我的持仓」「我的方案」「我该怎么做」的结构组织回答\n"
        "- 每条建议都要说明「为什么对你的持仓有意义」，基于用户的成本、盈亏、占比\n"
        "- 开头第一句话直接回应用户的具体问题\n"
        "- 在结论段末尾标注置信度，如：[中置信度]\n\n"
        "### 2. 操作方案\n"
        "- 明确买入/卖出/持有/定投决策，含具体标的名、金额、占比\n"
        "- 说明每项操作的前提条件（如「当 PE 百分位降至 X 时执行」）\n"
        "- 如涉及减仓，必须遵守减仓约束（单基金≤持仓20%、总减仓≤总资产10%、最多2只基金、单次≤5万元）\n\n"
        "### 3. 数据时效性\n"
        "- 涉及估值/行情数据时必须说明数据日期\n"
        "- 若数据超过7天，必须明确提醒用户「该数据基于约X日前快照，可能滞后」\n\n"
        "### 4. 未持有基金推荐\n"
        "- 若用户询问「还有什么可以买的」，可推荐关注列表中未持有但估值合理的基金\n"
        "- 推荐前必须验证基金代码，明确标注为「未持仓参考推荐」\n\n"
        "### 5. 风险提示\n"
        "- 每个操作建议必须附带该操作的风险说明\n"
        "- 说明什么条件下该建议会失效或导致亏损\n"
        "- 结尾给出总体风险判断（高/中/低）\n\n"
        "### 6. 格式要求\n"
        "- 结论先行：先给总体判断（加仓/减仓/持有/观望），并标注[高置信度/中置信度/低置信度]\n"
        "- 具体操作表格：基金 | 操作 | 金额 | 理由 | 前提条件\n"
        "- 风险提示：单独一节说明主要风险点\n"
        "- 数据表格必须标注数据来源和日期"
    )

    if routed_but_not_executed:
        from db.agents import load_specialist_agents
        all_agents = load_specialist_agents()
        missing_names = []
        for key in routed_but_not_executed:
            agent_info = all_agents.get(key, {})
            missing_names.append(agent_info.get("name", key))
        prompt += (
            f"\n\n⚠️ 重要约束：以下专家未被实际调用，禁止在回答中编造其观点或引用其分析："
            f"{', '.join(missing_names)}。"
            f"\n你只能基于上方实际提供了分析结果的专家来综合回答。"
            f"\n如果某些维度（如风险、配置）缺少专家分析，请明确告知用户'该维度暂缺专家分析'，"
            f"不要自行补充。"
        )

    return prompt


def _validate_and_repair(query: str, answer: str, specialist_results: list,
                         prebuilt_context: str, llm_messages: list,
                         trace_id: str = "") -> tuple[str, dict]:
    """轻量验证并尝试修复一次。返回 (answer, validator_result)。"""
    max_attempts = get_config_int("validator.max_repair_attempts", 1)
    if max_attempts <= 0 or get_config("validator.enabled", "true") != "true":
        return answer, {"enabled": False, "passed": True, "issues": []}

    result = _validator.validate(query, answer, specialist_results, prebuilt_context)
    if result["passed"] or result["severity"] != "high":
        return answer, result

    for attempt in range(max_attempts):
        logger.info(f"Validator 发现问题，尝试修复 (attempt {attempt + 1}): {result['issues']}")
        repair_prompt = (
            "请根据以下质检问题修复最终答案，确保建议具体、可执行、数据真实：\n"
            + "\n".join(f"- {issue}" for issue in result["issues"])
        )
        try:
            response = _call_llm(
                caller="orchestrator_repair",
                trace_id=trace_id,
                model=MODEL,
                messages=llm_messages + [
                    {"role": "assistant", "content": answer},
                    {"role": "user", "content": repair_prompt},
                ],
                temperature=get_config_float('llm.temperature_agent', 0.3),
                max_tokens=get_config_int('llm.max_tokens_orchestrator', 8192),
            )
            answer = response.choices[0].message.content or answer
        except Exception as e:
            logger.error(f"修复调用失败: {e}")
            break
        # 修复后再次验证
        result = _validator.validate(query, answer, specialist_results, prebuilt_context)
        if result["passed"]:
            break

    # 幻觉防御：金融数据校验 + Bad case 自动捕获（不阻塞主流程）
    try:
        from agent.prompt_defense import validate_financial_data, auto_capture_bad_case
        fin_check = validate_financial_data(answer)
        if not fin_check["passed"]:
            result.setdefault("issues", []).extend(fin_check["issues"])
            result["severity"] = "high"
            auto_capture_bad_case(answer, fin_check)
    except Exception:
        pass

    return answer, result


logger = logging.getLogger(__name__)


# ── 文章缓存(避免 orchestrator 预抓取与 agent 工具双重抓取) ──
_article_cache: dict[str, dict] = {}
_ARTICLE_CACHE_MAX = 32


def _cache_article(url: str, article: dict):
    """缓存文章抓取结果。"""
    if len(_article_cache) >= _ARTICLE_CACHE_MAX:
        # 移除最早的条目
        oldest = next(iter(_article_cache))
        del _article_cache[oldest]
    _article_cache[url] = article


def detect_urls(text: str) -> list[str]:
    """检测文本中的 URL。"""
    url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
    return re.findall(url_pattern, text)


def fetch_article_content(url: str) -> dict | None:
    """同步获取文章内容(用于 orchestrator 中)。优先使用缓存。"""
    # 先查缓存
    if url in _article_cache:
        logger.info(f"文章缓存命中: {url}")
        return _article_cache[url]

    try:
        from services.article_reader import fetch_article
        from concurrent.futures import ThreadPoolExecutor

        # 用线程池运行,每个线程创建独立事件循环
        def _fetch_sync():
            return asyncio.run(fetch_article(url))

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_fetch_sync)
            result = future.result(timeout=30)

        # 缓存成功结果
        if result:
            _cache_article(url, result)
        return result
    except ImportError:
        logger.warning("article_reader 模块未安装")
        return None


def enrich_query_with_article(query: str) -> tuple[str, str]:
    """
    检测查询中的链接,抓取文章内容并注入到查询中。

    返回: (enriched_query, article_context)
    article_context 为失败提示时以 "[抓取失败]" 开头。
    """
    urls = detect_urls(query)
    if not urls:
        return query, ""

    # 只处理第一个链接
    url = urls[0]
    logger.info(f"检测到链接: {url},正在抓取文章内容...")

    article = fetch_article_content(url)
    if not article:
        logger.warning(f"文章抓取失败: {url}")
        fail_hint = (
            f"[抓取失败] 无法获取链接内容: {url}\n"
            "可能原因:链接已失效、被防爬机制拦截、或非文章页面。\n"
            "请引导用户:1) 检查链接是否可正常打开 2) 直接粘贴文章正文。"
        )
        enriched_query = f"{query}\n\n{fail_hint}"
        return enriched_query, fail_hint

    title = article.get("title", "未知标题")
    content = article.get("content_text", "") or article.get("content", "")
    author = article.get("author", "")
    publish_time = article.get("publish_time", "")

    if not content:
        logger.warning(f"文章内容为空: {url}")
        fail_hint = (
            f"[抓取失败] 链接已打开但未提取到正文: {url}\n"
            "请引导用户直接粘贴文章正文。"
        )
        enriched_query = f"{query}\n\n{fail_hint}"
        return enriched_query, fail_hint

    # 截取策略:前 4000 + 后 3000,保留开头背景和结尾结论
    max_chars = 7000
    if len(content) > max_chars:
        head = content[:4000]
        tail = content[-3000:]
        content = f"{head}\n\n...(中间内容省略)...\n\n{tail}"

    # 构建文章上下文
    meta_parts = [f"标题: {title}"]
    if author:
        meta_parts.append(f"作者: {author}")
    if publish_time:
        meta_parts.append(f"发布时间: {publish_time}")
    meta_parts.append(f"来源: {url}")

    article_context = f"""## 参考文章
{chr(10).join(meta_parts)}

{content}"""

    # 注入查询,明确告知 agent 已提供文章内容,避免重复调用 fetch_article
    enriched_query = (
        f"{query}\n\n"
        "请参考以下文章内容进行分析(文章已抓取完毕,无需再次调用 fetch_article 工具):\n"
        f"{article_context}"
    )

    logger.info(f"文章抓取成功: {title} ({len(content)} 字符)")
    return enriched_query, article_context


def get_orchestration_config(key: str, default=None):
    """从数据库读取编排配置。"""
    try:
        from db._conn import _get_conn
        conn = _get_conn()
        row = conn.execute("SELECT value FROM orchestration_config WHERE key = ?", (key,)).fetchone()
        conn.close()
        return row["value"] if row else default
    except Exception:
        return default


class CancelledError(Exception):
    """用户取消执行时抛出。"""
    pass


def _check_cancel(cancel_event: threading.Event | None):
    """检查是否被取消,如果是则抛出 CancelledError。"""
    if cancel_event and cancel_event.is_set():
        raise CancelledError("用户取消了执行")


def _check_timeout(start_time: float):
    """检查是否超时,如果是则抛出异常。"""
    elapsed = time.time() - start_time
    if elapsed > MAX_ORCHESTRATION_SECONDS:
        raise TimeoutError(f"执行超时({int(elapsed)}s > {MAX_ORCHESTRATION_SECONDS}s 限制)")


# ── 智能交叉审阅:检测专家分歧 ──────────────────────────────

def _detect_specialist_disagreement(specialist_results: list) -> bool:
    """检测专家之间是否存在方向性分歧,决定是否需要交叉审阅。

    保守策略:只要有分歧就触发,只在完全一致时跳过。
    纯字符串匹配,无 LLM 调用,零延迟。
    """
    # 使用优化器的快速检测
    return not OrchestratorOptimizer.should_skip_cross_review(specialist_results, "complex")


def get_max_tools_per_turn(complexity: str) -> int:
    """根据复杂度获取单轮工具调用上限，从 DB 配置读取。"""
    try:
        from db.config import get_config_int
        db_val = get_config_int(f"dispatch.max_tool_calls.{complexity}", 0)
        if db_val > 0:
            return db_val
    except Exception:
        pass
    limits = {"simple": 1, "medium": 1, "complex": 3}
    return limits.get(complexity, 3)


def trim_tool_calls(tool_calls: list, allowed_specialists: list[str] | None, max_tools: int) -> list:
    """按白名单和数量上限裁剪 tool calls。"""
    expert_map = build_expert_map()
    allowset = set(allowed_specialists or [])
    trimmed = []
    for tc in tool_calls or []:
        agent_key = expert_map.get(getattr(getattr(tc, "function", None), "name", ""))
        if allowset and agent_key not in allowset:
            logger.info(f"跳过非白名单专家调用: {getattr(getattr(tc, 'function', None), 'name', '')} ({agent_key})")
            continue
        trimmed.append(tc)
        if len(trimmed) >= max_tools:
            break
    return trimmed


def should_run_cross_review(
    specialist_results: list,
    complexity: str,
    conflicts: dict,
    force_skip: bool = False,
    enabled: bool = True,
    min_specialists: int = 2,
    min_severity: str = "medium",
    predicted_need_cross_review: bool = False,
    needs_arbitration: bool = False,
) -> bool:
    """统一判断是否进入交叉审阅。

    决策逻辑（优先级从高到低）：
    1. 实际检测到冲突（已过 severity 分级检查）→ 执行（不被预判否决）
    2. 预判 True + 无实际冲突 → 跳过
    3. 预判 False + 无实际冲突 → 跳过

    Args:
        predicted_need_cross_review: 来自 clarify_requirement 的 LLM 预判。
        needs_arbitration: 来自路由结果，complex 场景通常为 True。
    """
    if not enabled or force_skip:
        return False
    if len(specialist_results) < min_specialists:
        return False

    has_conflicts = conflicts and conflicts.get("detected")

    # severity 分级检查（提前过滤低级别冲突）
    if has_conflicts:
        severity_order = {"low": 0, "medium": 1, "high": 2}
        actual = severity_order.get(conflicts.get("severity", "low"), 0)
        required = severity_order.get(min_severity, 1)
        if actual < required:
            logger.info(f"交叉审阅: 冲突 severity={conflicts.get('severity')} < {min_severity} → 跳过")
            return False

    # 核心修复：实际检测到冲突就执行交叉审阅，不被预判一票否决
    # 优先级：实际冲突检测结果 > LLM 预判 > complexity 分级
    if has_conflicts:
        # 实际检测到冲突（已通过 severity 分级检查），执行交叉审阅
        if complexity == "complex" and needs_arbitration:
            logger.info("交叉审阅: complex+needs_arbitration+实际冲突 → 执行（不被预判否决）")
        elif predicted_need_cross_review:
            logger.info("交叉审阅: 预判=True + 实际冲突=True → 执行")
        else:
            logger.info(f"交叉审阅: 预判=False 但实际检测到冲突(severity={conflicts.get('severity')}) → 执行")
    elif predicted_need_cross_review:
        logger.info("交叉审阅: 预判=True 但实际未检测到冲突 → 跳过")
        return False
    else:
        if complexity == "complex" and needs_arbitration:
            logger.info("交叉审阅: 预判=False，complex+needs_arbitration 但无实际冲突 → 跳过")
        else:
            logger.info("交叉审阅: 预判=False 且无实际冲突 → 跳过")
        return False

    return not OrchestratorOptimizer.should_skip_cross_review(specialist_results, complexity)


# ── 冲突检测:专家评级方向冲突 ──────────────────────────────────

_RATING_KEYWORDS = {
    "买入": ["买入", "加仓", "建仓", "推荐买入", "建议买入", "可以买", "值得买"],
    "卖出": ["卖出", "减仓", "清仓", "止损", "建议卖出", "应该卖", "不建议持有"],
    "持有": ["持有", "观望", "持有观望", "继续持有", "暂不操作"],
}

_OPPOSITE_PAIRS = [
    ("买入", "卖出"),
    ("卖出", "买入"),
]


def _extract_ratings(specialist_results: list) -> list[dict]:
    """从专家结果中提取评级信息。

    返回: [{"agent_key": "...", "agent": "...", "rating": "买入/卖出/持有/未知", "fund": "..."}]
    """
    ratings = []
    for sr in specialist_results:
        if sr.get("is_cross_review"):
            continue
        analysis = sr.get("analysis", "")
        agent_key = sr.get("agent_key", "")
        agent_name = sr.get("agent", agent_key)

        # 检测评级关键词(优先级:买入 > 卖出 > 持有)
        detected_rating = "未知"
        for rating_type in ["买入", "卖出", "持有"]:
            keywords = _RATING_KEYWORDS[rating_type]
            if any(kw in analysis for kw in keywords):
                detected_rating = rating_type
                break

        # 尝试提取基金名称(常见模式:XXX基金、XXX指数)
        import re
        fund_match = re.search(r'[((]([^))]+)[))]|([一-龥]{2,}(?:指数|ETF|基金|LOF))', analysis)
        fund = ""
        if fund_match:
            fund = fund_match.group(1) or fund_match.group(2) or ""

        ratings.append({
            "agent_key": agent_key,
            "agent": agent_name,
            "rating": detected_rating,
            "fund": fund,
        })
    return ratings


def detect_conflicts(specialist_results: list) -> dict:
    """检测专家之间的评级方向冲突和操作建议冲突。

    返回:
        {"detected": bool, "items": [{"type": "rating|action", "expert1": ..., "rating1": ..., "expert2": ..., "rating2": ..., "fund": ...}]}
    """
    ratings = _extract_ratings(specialist_results)
    conflicts = []

    # 1. 评级方向冲突
    for i in range(len(ratings)):
        for j in range(i + 1, len(ratings)):
            r1 = ratings[i]
            r2 = ratings[j]
            if r1["rating"] == "未知" or r2["rating"] == "未知":
                continue
            if r1["rating"] == r2["rating"]:
                continue
            # 检查是否构成方向冲突
            is_conflict = False
            for a, b in _OPPOSITE_PAIRS:
                if (r1["rating"] == a and r2["rating"] == b) or (r1["rating"] == b and r2["rating"] == a):
                    is_conflict = True
                    break
            if is_conflict:
                conflicts.append({
                    "type": "rating",
                    "expert1": r1["agent"],
                    "rating1": r1["rating"],
                    "expert2": r2["agent"],
                    "rating2": r2["rating"],
                    "fund": r1["fund"] or r2["fund"] or "",
                })

    # 2. 操作建议冲突：同一标的被给出相反操作
    action_patterns = {
        "buy": ["买入", "加仓", "定投", "增持", "建仓"],
        "sell": ["卖出", "减仓", "止盈", "清仓", "减持", "止损"],
    }
    fund_code_pattern = r"\b(\d{6}|\d{5,6})\b"

    actions = []
    for sr in specialist_results:
        if sr.get("is_cross_review"):
            continue
        text = sr.get("analysis", "")
        found_codes = re.findall(fund_code_pattern, text)
        if not found_codes:
            continue
        action_type = None
        for act, keywords in action_patterns.items():
            if any(kw in text for kw in keywords):
                action_type = act
                break
        if action_type:
            for code in found_codes:
                actions.append({
                    "agent": sr.get("agent", sr.get("agent_key", "未知")),
                    "action": action_type,
                    "fund": code,
                })

    for i in range(len(actions)):
        for j in range(i + 1, len(actions)):
            a1 = actions[i]
            a2 = actions[j]
            if a1["fund"] == a2["fund"] and a1["action"] != a2["action"]:
                conflicts.append({
                    "type": "action",
                    "expert1": a1["agent"],
                    "rating1": a1["action"],
                    "expert2": a2["agent"],
                    "rating2": a2["action"],
                    "fund": a1["fund"],
                })

    # 计算 severity: action 冲突比 rating 冲突更严重
    action_conflicts = [c for c in conflicts if c["type"] == "action"]
    rating_conflicts = [c for c in conflicts if c["type"] == "rating"]
    if len(action_conflicts) >= 2 or len(conflicts) >= 3:
        severity = "high"
    elif action_conflicts or rating_conflicts:
        severity = "medium"
    else:
        severity = "low"

    return {
        "detected": len(conflicts) > 0,
        "severity": severity,
        "items": conflicts,
    }


# ── LLM 冲突检测(语义级)──────────────────────────────────────

def detect_conflicts_llm(specialist_results: list, query: str, trace_id: str = "") -> dict:
    """用 LLM 提取专家结论和冲突（语义级，非关键词匹配）。

    当 specialist_results >= 2 时调用。把每个专家的 analysis（截断到2000字）拼成 prompt，
    让 LLM 返回结构化 JSON：每个专家的 rating/targets/actions，以及 conflicts 和 consensus。

    Returns:
        {"detected": bool, "severity": "low|medium|high", "items": [...], "consensus": [...]}
    """
    # 过滤掉交叉审阅结果，只分析原始专家
    original_results = [sr for sr in specialist_results if not sr.get("is_cross_review")]
    if len(original_results) < 2:
        return detect_conflicts(specialist_results)

    # 构建专家分析摘要
    expert_sections = []
    for sr in original_results:
        agent_key = sr.get("agent_key", sr.get("agent", "unknown"))
        agent_name = sr.get("agent", agent_key)
        analysis = sr.get("analysis", "")[:8000]  # 截断到8000字
        expert_sections.append(f"### 专家: {agent_name} (agent_key: {agent_key})\n{analysis}")
    experts_text = "\n\n---\n\n".join(expert_sections)

    prompt = f"""分析以下多位专家的分析结论，提取结构化信息。注意：要理解语义，不要只看关键词。
例如"此时割肉是经典错误"应识别为"持有"，"可以适度加仓到2万"应识别为"买入"。

专家列表：
{experts_text}

用户问题：{query}

请返回JSON：
{{
  "experts": [
    {{"agent": "agent_key", "rating": "买入|卖出|持有|未知", "targets": ["基金代码或名称"], "actions": [{{"fund": "xxx", "action": "买入|卖出|持有", "description": "具体描述"}}]}}
  ],
  "conflicts": [
    {{"type": "rating|action", "expert1": "xxx", "expert2": "xxx", "fund": "xxx", "description": "冲突描述", "severity": "low|medium|high"}}
  ],
  "consensus": ["共识点1", "共识点2"]
}}

注意：
- rating 是专家对用户问题的整体方向性建议（买入/卖出/持有/未知）
- targets 是专家分析中涉及的具体基金代码或名称
- actions 是专家建议的具体操作
- conflicts 是不同专家之间的方向性分歧（如一个说买入一个说卖出）
- consensus 是所有专家都认同的观点
- severity: high=方向完全相反(买入vs卖出), medium=部分分歧(买入vs持有), low=措辞差异"""

    # 使用 cross_review_model 配置的模型；同时用 MODEL 作为主用模型（确保不触发 fallback 延迟）
    # cross_review_model 仅作为提示，实际用主用 MODEL 避免模型名不匹配导致 fallback
    try:
        response = _call_llm(
            caller="conflict_detect",
            trace_id=trace_id,
            model=MODEL,
            messages=[
                {"role": "system", "content": "你是投资分析冲突检测专家。请分析多位专家的分析结论，提取结构化信息。只输出JSON，不要其他文字。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=8000,
        )

        raw = response.choices[0].message.content.strip()

        # 去除 markdown 代码块
        if "```" in raw:
            parts = raw.split("```")
            raw = parts[1] if len(parts) > 1 else parts[0]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        # 解析 JSON
        import re
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r'\{[\s\S]*\}', raw)
            if match:
                result = json.loads(match.group())
            else:
                raise ValueError(f"无法解析LLM冲突检测输出: {raw[:200]}")

        # 转换为兼容格式
        conflicts = result.get("conflicts", [])
        consensus = result.get("consensus", [])

        # 计算 severity
        if not conflicts:
            severity = "low"
        else:
            severities = [c.get("severity", "medium") for c in conflicts]
            if "high" in severities:
                severity = "high"
            elif "medium" in severities:
                severity = "medium"
            else:
                severity = "low"

        # 转换 conflicts 为兼容格式
        items = []
        for c in conflicts:
            items.append({
                "type": c.get("type", "rating"),
                "expert1": c.get("expert1", ""),
                "expert2": c.get("expert2", ""),
                "fund": c.get("fund", ""),
                "description": c.get("description", ""),
                "severity": c.get("severity", "medium"),
            })

        return {
            "detected": len(items) > 0,
            "severity": severity,
            "items": items,
            "consensus": consensus,
        }

    except Exception as e:
        logger.warning(f"LLM冲突检测失败: {e}")
        raise


# ── 统一冲突检测入口(优先LLM，降级关键词)──────────────────────

def detect_conflicts_smart(specialist_results: list, query: str = "", trace_id: str = "") -> dict:
    """统一冲突检测入口：优先使用 LLM 语义检测，失败时降级到关键词匹配。"""
    if len(specialist_results) >= 2:
        try:
            conflicts = detect_conflicts_llm(specialist_results, query, trace_id)
            logger.info(f"LLM冲突检测完成: detected={conflicts.get('detected', False)}, severity={conflicts.get('severity', 'low')}")
            return conflicts
        except Exception as e:
            logger.warning(f"LLM冲突检测失败，降级到关键词: {e}")
            conflicts = detect_conflicts(specialist_results)
            conflicts["consensus"] = conflicts.get("consensus", [])
            return conflicts
    else:
        conflicts = detect_conflicts(specialist_results)
        conflicts["consensus"] = conflicts.get("consensus", [])
        return conflicts


# ── Token 预算检查 ──────────────────────────────────────────

def check_token_budget() -> dict:
    """检查今日 token 用量是否超限。

    返回:
        {"ok": bool, "used": int, "limit": int, "pct": float,
         "mode": "normal"|"conservative"|"exceeded"}
    """
    from config import DAILY_TOKEN_LIMIT, TOKEN_WARN_THRESHOLD, TOKEN_BUDGET_BYPASS
    from db import get_today_token_total

    used = get_today_token_total()
    pct = used / DAILY_TOKEN_LIMIT if DAILY_TOKEN_LIMIT > 0 else 0

    if TOKEN_BUDGET_BYPASS:
        return {"ok": True, "used": used, "limit": DAILY_TOKEN_LIMIT, "pct": pct, "mode": "normal"}

    if pct >= 1.0:
        return {"ok": False, "used": used, "limit": DAILY_TOKEN_LIMIT, "pct": pct, "mode": "exceeded"}
    elif pct >= TOKEN_WARN_THRESHOLD:
        return {"ok": True, "used": used, "limit": DAILY_TOKEN_LIMIT, "pct": pct, "mode": "conservative"}
    else:
        return {"ok": True, "used": used, "limit": DAILY_TOKEN_LIMIT, "pct": pct, "mode": "normal"}


# ── 需求澄清 Agent(LLM 版)──────────────────────────────────

def build_clarification_prompt(available_specialists: list[dict] | None = None) -> str:
    """从数据库动态生成需求路由提示词。

    Args:
        available_specialists: 可用专家列表，格式 [{"key": "...", "name": "...", "description": "..."}]
                              如果为 None，则从数据库加载。
    """
    specialists = load_specialist_agents()
    expert_lines = []
    for key, info in specialists.items():
        expert_lines.append(f"- {key}: {info['description']}")
    expert_list = "\n".join(expert_lines)
    keys_json = json.dumps(list(specialists.keys()), ensure_ascii=False)
    specialist_keys = list(specialists.keys())

    return f"""你是投资分析需求路由专家。分析用户问题,返回 JSON。

## 可用专家
{expert_list}

## 复杂度判断规则

### chat - 闲聊/科普(不调用任何专家)
- 纯问候/感谢/道歉("你好"、"谢谢")
- 概念解释("什么是PE"、"解释定投")
- 与投资无关的问题("今天天气")

### simple - 单一数据查询(1个专家)
- 查询单一指标("沪深300估值"、"债市温度")
- 查询持仓概况("我持有什么")

### medium - 分析任务(1个专家)
- **对比分析**("A和B区别"、"A和B哪个好")→ 只用 1 个估值专家
- 单一维度深度分析("白酒估值高吗"、"适合买债券吗")
- **"可以买吗"/"值得买吗"** → 只问估值判断
- 持仓诊断("我的持仓健康吗")
- 简单建议("买点货币基金可以吗")

### complex - 综合决策(2+个专家)
- 需要多维度分析(估值+配置+风险)
- 涉及具体操作建议("帮我做个定投方案")
- 多市场联动分析("美股大跌对A股影响")

## 关键规则
1. **对比类问题只用 1 个专家**,不要触发多 agent
2. **"可以买吗"是 medium**,不是 complex
3. **简单建议类是 medium**,只需 1 个专家
4. **涉及持仓操作（补仓/加仓/减仓/调仓/加减仓）必选 allocation_advisor**，即使用户没明确说"我的持仓"，只要提到"补仓""加仓"等操作词就要选
5. **灾害/事件利好问题**：用户问某事件对什么有利好，必选 macro_strategist，且 specialist_tasks 中注明"搜索受益板块新闻，关键词用板块名而非事件本身"

## 输出格式(只输出JSON)
{{"complexity":"chat|simple|medium|complex","specialists":["expert1"],"reason":"判断原因","refined_query":"优化后的查询","confidence":0.95}}

当 complexity >= medium 时，还需要输出以下字段：
{{"complexity":"medium|complex","specialists":["expert1","expert2"],"specialist_tasks":{{"expert1":"该专家需要解决的具体子问题","expert2":"该专家需要解决的具体子问题"}},"need_cross_review":false,"reason":"判断原因","refined_query":"优化后的查询","confidence":0.95}}

- specialists: 推荐的专家列表，必须从 {specialist_keys} 中选择
- specialist_tasks: 每个专家的具体任务描述（会注入到专家的query中作为上下文），描述该专家需要解决什么子问题
- need_cross_review: 是否需要交叉审阅（≥2个专家且可能产生分歧时为true，如估值专家说买入但风控专家说风险过高）
- reason: 包含专家选择理由和交叉审阅预判理由

示例(simple时不需要这些字段):
Q: 沪深300估值多少
A: {{"complexity":"simple","specialists":["valuation_expert"],"reason":"单一指数估值查询","refined_query":"沪深300当前PE/PB估值和百分位","confidence":0.95}}

示例(complex时需要 specialist_tasks):
Q: 帮我做个定投方案
A: {{"complexity":"complex","specialists":["valuation_expert","allocation_advisor"],"specialist_tasks":{{"valuation_expert":"筛选当前低估的定投标的，给出PE/PB百分位数据","allocation_advisor":"基于估值结果制定定投金额分配方案，考虑仓位和现金比例"}},"need_cross_review":false,"reason":"定投需要估值判断+配置策略，两专家互补不易冲突","refined_query":"基于当前估值的定投方案","confidence":0.92}}

示例(medium时也需要 specialist_tasks):
Q: 白酒估值高吗，可以买吗
A: {{"complexity":"medium","specialists":["valuation_expert"],"specialist_tasks":{{"valuation_expert":"评估白酒指数当前PE/PB百分位，判断是否高估，给出买入/持有/卖出建议"}},"need_cross_review":false,"reason":"单一估值判断，无需交叉审阅","refined_query":"白酒指数估值水平和投资建议","confidence":0.90}}

示例(需要交叉审阅):
Q: 持仓太集中了，帮我看看要不要调整
A: {{"complexity":"complex","specialists":["risk_assessor","allocation_advisor","valuation_expert"],"specialist_tasks":{{"risk_assessor":"评估当前持仓集中度风险和最大回撤","allocation_advisor":"给出分散配置建议和再平衡方案","valuation_expert":"评估重仓标的的估值水平，判断是否高估"}},"need_cross_review":true,"reason":"涉及风险评估vs配置调整，可能产生分歧（如减仓vs加仓），需要交叉审阅","refined_query":"持仓集中度风险评估和配置调整建议","confidence":0.88}}

- specialists 中的值必须是:{keys_json},chat 时为空数组
- confidence 低于 0.7 时系统会降级处理

## 示例

Q: 你好
A: {{"complexity":"chat","specialists":[],"reason":"纯问候","refined_query":"你好","confidence":0.99}}

Q: 沪深300估值多少
A: {{"complexity":"simple","specialists":["valuation_expert"],"reason":"单一指数估值查询","refined_query":"沪深300当前PE/PB估值和百分位","confidence":0.95}}

Q: 红利质量和中证红利有什么区别
A: {{"complexity":"medium","specialists":["valuation_expert"],"reason":"对比分析,只需1个估值专家","refined_query":"红利质量和中证红利的估值对比(PE/PB/百分位/股息率)","confidence":0.90}}

Q: 恒生科技怎么样,可以买吗
A: {{"complexity":"medium","specialists":["valuation_expert"],"reason":"估值判断,不需要多专家","refined_query":"恒生科技指数估值水平和投资建议","confidence":0.90}}

Q: 帮我做个定投方案
A: {{"complexity":"complex","specialists":["valuation_expert","allocation_advisor"],"reason":"定投需要估值+配置策略","refined_query":"基于当前估值的定投方案","confidence":0.92}}

Q: 美股大跌,A股明天会怎么走
A: {{"complexity":"complex","specialists":["market_analyst","valuation_expert","risk_assessor"],"reason":"多市场联动分析,需要多维分析","refined_query":"美股大跌原因、A股走势预判及持仓影响","confidence":0.90}}"""


# ── 基于规则的复杂度预判(零 LLM 调用)────────────────────────

def _classify_complexity_by_rules(query: str, has_portfolio: bool = False, has_watchlist: bool = False) -> str:
    """基于规则预判用户问题的复杂度,避免 LLM 调用。

    返回: "chat" | "simple" | "medium" | "complex"

    设计原则:
    - simple 和 complex 直接走规则(确定性高)
    - medium 需要 LLM 确认(边界情况多)
    """
    text = (query or "").strip()
    if not text:
        return "chat"

    text_lower = text.lower()
    length = len(text)

    # ── 闲聊检测(短消息 + 闲聊关键词 + 无投资内容)──
    chat_keywords = ["你好", "谢谢", "好的", "明白了", "知道了", "嗯",
                     "天气", "笑话", "故事", "晚安", "早上好", "嗨", "hi", "hello", "hey"]
    # 纯问候/感谢/闲聊
    if length <= 10 and any(kw in text_lower for kw in chat_keywords):
        # 确保没有投资关键词
        invest_markers = ["估值", "PE", "PB", "持仓", "买入", "卖出", "基金",
                          "股票", "债券", "定投", "配置", "风险", "收益"]
        if not any(m in text_lower for m in invest_markers):
            return "chat"

    # 极短消息(<=5字),无投资关键词,无疑问词
    if length <= 5:
        has_question = bool(re.search(r'[吗呢??]', text))
        invest_markers = ["估值", "pe", "pb", "持仓", "买入", "卖出", "基金",
                          "股票", "债券", "债市", "定投", "配置"]
        concept_markers = ["什么是", "解释", "原理", "概念", "定义", "含义"]
        if not has_question and not any(m in text_lower for m in invest_markers) \
                and not any(m in text_lower for m in concept_markers):
            return "chat"

    # ── Simple 检测(短查询 + 简单关键词)──
    simple_keywords = ["什么是", "解释", "价格", "多少", "是多少", "查一下",
                       "查询", "最新", "今天", "估值", "百分位", "PE", "PB",
                       "z-score", "债市温度", "温度"]
    # 强制 simple 的前缀关键词(解释/定义类问题,即使包含投资术语也是 simple)
    force_simple_prefixes = ["什么是", "解释", "怎么算", "概念", "原理", "含义"]
    has_force_simple = any(text_lower.startswith(p) for p in force_simple_prefixes)
    has_simple = any(kw in text_lower for kw in simple_keywords)
    if length < 30 and has_simple:
        # 排除:虽然短但包含复杂意图(但强制 simple 前缀跳过排除)
        if not has_force_simple:
            complex_markers = ["分析", "对比", "比较", "建议", "配置", "风险",
                               "方案", "策略", "计划", "加仓", "减仓", "定投"]
            if any(m in text_lower for m in complex_markers):
                # 有疑问词 + 估值关键词 → 可能需要分析,交给 LLM 确认
                has_question = bool(re.search(r'[吗呢??]', text))
                if has_question and any(kw in text_lower for kw in ["估值", "高", "低", "贵", "便宜"]):
                    return "medium"  # 边界情况,交给 LLM
                return "medium"  # 有复杂意图,交给 LLM
        # 有疑问词 + 估值关键词 → 可能需要分析,交给 LLM 确认
        has_question = bool(re.search(r'[吗呢??]', text))
        if has_question and any(kw in text_lower for kw in ["估值", "高", "低", "贵", "便宜"]):
            return "medium"  # 边界情况,交给 LLM
        return "simple"

    # ── Complex 检测(长查询 + 多个投资关键词 + 有持仓数据)──
    complex_keywords = ["分析", "对比", "比较", "建议", "配置", "风险",
                        "方案", "策略", "计划", "加仓", "减仓", "定投",
                        "组合", "仓位", "再平衡", "回撤", "波动"]
    complex_match_count = sum(1 for kw in complex_keywords if kw in text_lower)

    # 长查询 + 多个复杂关键词 → complex
    if length > 100 and complex_match_count >= 2:
        return "complex"

    # 有持仓 + 复杂关键词组合 → complex
    if has_portfolio and complex_match_count >= 2:
        return "complex"

    # 涉及多维度分析的关键词组合 → complex
    multi_dim_triggers = [
        ("分析", ["配置", "风险", "建议"]),  # 分析 + 配置/风险/建议
        ("建议", ["配置", "风险", "组合"]),  # 建议 + 配置/风险/组合
        ("定投", ["方案", "策略", "计划"]),  # 定投 + 方案/策略/计划
    ]
    for primary, secondaries in multi_dim_triggers:
        if primary in text_lower and any(s in text_lower for s in secondaries):
            return "complex"

    # ── 未命中规则 → 返回 medium(需要 LLM 确认)──
    return "medium"


# Clarification 结果缓存(相同查询直接返回缓存结果,节省 2-5s LLM 调用)
_clarification_cache: dict[int, dict] = {}
_CLARIFICATION_CACHE_MAX = 128


def detect_scenario_type(query: str) -> str:
    """确定性识别投资问题场景,用于模板、RAG 和评测分流。"""
    text = query or ""
    if detect_urls(text) or any(word in text for word in ["文章", "作者观点", "这篇", "观点是否", "观点靠谱吗"]):
        return "article_check"
    if any(word in text for word in ["复盘", "回顾", "上次决策", "这次决策效果"]):
        return "decision_review"
    if any(word in text for word in ["什么是", "解释", "怎么算", "概念", "区别", "原理"]):
        return "knowledge_qa"
    if any(word in text for word in ["卖出", "减仓", "止盈", "止损", "退出", "要不要卖"]):
        return "sell_decision"
    if any(word in text for word in ["买入", "加仓", "建仓", "定投", "可以买吗", "值得买吗", "能不能买"]):
        return "buy_decision"
    if any(word in text for word in ["持仓", "组合", "仓位", "集中", "分散", "再平衡", "资产配置"]):
        return "portfolio_review"
    return "general_analysis"


def clarify_requirement(query: str, trace_id: str = "") -> dict:
    """
    分析用户问题,返回需求澄清结果。

    优化策略:先走规则预判(零 LLM 调用),仅在边界情况(medium)时调用 LLM。
    预期节省 2-3 秒首响时间(>70% 的查询可跳过 LLM)。

    返回:
        {
            "complexity": "chat|simple|medium|complex",
            "specialists": ["valuation_expert", ...],
            "reason": "...",
            "refined_query": "..."
        }
    """
    # 检查缓存
    cache_key = hash(query)
    if cache_key in _clarification_cache:
        logger.debug(f"Clarification 缓存命中: {query[:30]}...")
        return _clarification_cache[cache_key]

    # ── Step 0: SOP 模板匹配（增强3，零 LLM 调用）──
    has_portfolio = False
    has_watchlist = False
    try:
        from services.portfolio_context import build_portfolio_summary_line
        portfolio_line = build_portfolio_summary_line()
        has_portfolio = bool(portfolio_line and "无持仓" not in portfolio_line)
    except Exception:
        portfolio_line = ""
    try:
        from db.portfolio import get_watchlist
        watchlist = get_watchlist("default")
        has_watchlist = bool(watchlist)
    except Exception:
        pass

    sop = match_sop_template(query, has_portfolio)
    if sop:
        # SOP 模板：构建 specialist_tasks（从模板 steps 中提取 task）
        sop_specialist_tasks = {}
        for s in sop["steps"]:
            agent_key = s.get("agent", "")
            task_desc = s.get("task", "")
            if agent_key and task_desc:
                sop_specialist_tasks[agent_key] = task_desc

        result_out = {
            "complexity": "complex",
            "specialists": [s["agent"] for s in sop["steps"]],
            "reason": f"匹配到{sop['name']}模板",
            "refined_query": query,
            "confidence": 0.95,
            "scenario_type": detect_scenario_type(query),
            "classification_method": "sop",
            "sop_template": sop,
            "specialist_tasks": sop_specialist_tasks,
            "need_cross_review": sop.get("cross_review", False),
        }
        if len(_clarification_cache) >= _CLARIFICATION_CACHE_MAX:
            _clarification_cache.pop(next(iter(_clarification_cache)))
        _clarification_cache[cache_key] = result_out
        logger.info(f"SOP 匹配: {sop['name']} → {sop['steps']}")
        return result_out

    # ── Step 1: 规则预判（零 LLM 调用）──
        portfolio_line = ""

    try:
        from db.portfolio import get_watchlist
        watchlist = get_watchlist("default")
        has_watchlist = bool(watchlist)
    except Exception:
        pass

    rule_complexity = _classify_complexity_by_rules(query, has_portfolio, has_watchlist)
    logger.info(f"规则预判复杂度: {rule_complexity} (query={query[:50]}..., portfolio={has_portfolio}, watchlist={has_watchlist})")

    # ── Step 2: 非 medium 结果直接走规则路径(跳过 LLM)──
    if rule_complexity in ("chat", "simple", "complex"):
        specialists = route_to_specialists_by_keywords(query, rule_complexity) if rule_complexity != "chat" else []
        result_out = {
            "complexity": rule_complexity,
            "specialists": specialists,
            "reason": f"规则预判({rule_complexity})",
            "refined_query": query,
            "confidence": 0.85,  # 规则预判置信度
            "scenario_type": detect_scenario_type(query),
            "classification_method": "rules",
            "specialist_tasks": {},
            "need_cross_review": False,
        }
        # 缓存结果
        if len(_clarification_cache) >= _CLARIFICATION_CACHE_MAX:
            _clarification_cache.pop(next(iter(_clarification_cache)))
        _clarification_cache[cache_key] = result_out
        return result_out

    # ── Step 3: medium 结果 → 调用 LLM 确认(保留原逻辑)──
    logger.info(f"规则返回 medium,调用 LLM 确认: {query[:50]}...")

    try:
        user_content = query
        if portfolio_line:
            user_content = f"{portfolio_line}\n\n用户问题: {query}"

        response = _call_llm(
            caller="clarify",
            trace_id=trace_id,
            model=MODEL,
            messages=[
                {"role": "system", "content": build_clarification_prompt()},
                {"role": "user", "content": user_content},
            ],
            temperature=get_config_float('llm.temperature_vision', 0.1),
            max_tokens=get_config_int('llm.max_tokens_orchestrator', 8192),
        )

        raw = response.choices[0].message.content.strip()

        # 提取 JSON - 多种容错策略
        # 1. 去除 markdown 代码块
        if "```" in raw:
            parts = raw.split("```")
            raw = parts[1] if len(parts) > 1 else parts[0]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        # 2. 尝试直接解析
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            # 3. 提取第一个 {...}
            import re
            match = re.search(r'\{[^{}]*\}', raw, re.DOTALL)
            if match:
                result = json.loads(match.group())
            else:
                raise

        # 兼容模型返回非标准格式(如 {"需求分析": {...}} )
        if "complexity" not in result:
            # 尝试从嵌套结构中提取
            for key in result:
                if isinstance(result[key], dict) and "complexity" in result[key]:
                    result = result[key]
                    break
            # 仍然没有 → 检查是否有需求类型字段
            if "complexity" not in result:
                # 根据内容推断
                needs = str(result)
                if any(kw in needs for kw in ["买入", "建议", "配置", "决策", "风险"]):
                    result = {"complexity": "complex", "specialists": ["market_analyst", "allocation_advisor"],
                              "reason": "从非标准响应推断", "refined_query": query}
                else:
                    result = {"complexity": "medium", "specialists": ["valuation_expert"],
                              "reason": "从非标准响应推断", "refined_query": query}

        # 验证并设置默认值
        complexity = result.get("complexity", "medium")
        if complexity not in ("chat", "simple", "medium", "complex"):
            complexity = "medium"

        specialists = result.get("specialists", [])
        valid_specialists = list(load_specialist_agents().keys())
        specialists = [s for s in specialists if s in valid_specialists]

        # chat 类型不需要专家
        if complexity == "chat":
            specialists = []
        # 如果没有选择专家,默认选估值专家(chat 除外)
        elif not specialists:
            specialists = ["valuation_expert"]

        # 置信度检查
        confidence = result.get("confidence", 0.8)
        if confidence < 0.7:
            logger.warning(f"澄清置信度过低 ({confidence}),降级为 simple")
            complexity = "simple"
            specialists = ["valuation_expert"]

        # 提取 LLM 规划的专家任务和交叉审阅预判
        specialist_tasks = result.get("specialist_tasks", {})
        need_cross_review = result.get("need_cross_review", False)

        result_out = {
            "complexity": complexity,
            "specialists": specialists,
            "reason": result.get("reason", ""),
            "refined_query": result.get("refined_query", query),
            "confidence": confidence,
            "scenario_type": detect_scenario_type(query),
            "classification_method": "llm",
            "specialist_tasks": specialist_tasks,
            "need_cross_review": need_cross_review,
        }

        # 缓存结果
        if len(_clarification_cache) >= _CLARIFICATION_CACHE_MAX:
            _clarification_cache.pop(next(iter(_clarification_cache)))
        _clarification_cache[cache_key] = result_out

        return result_out

    except Exception as e:
        logger.warning(f"LLM 澄清失败,回退到关键词匹配: {e}")
        # 回退到关键词匹配
        complexity = detect_complexity_by_keywords(query)
        specialists = route_to_specialists_by_keywords(query, complexity)
        return {
            "complexity": complexity,
            "specialists": specialists,
            "reason": "关键词匹配(LLM澄清失败)",
            "refined_query": query,
            "confidence": 0.5,
            "scenario_type": detect_scenario_type(query),
            "classification_method": "keywords_fallback",
            "specialist_tasks": {},
            "need_cross_review": False,
        }


# ── 任务复杂度检测(关键词匹配,作为回退方案)──────────────────────────

def detect_complexity_by_keywords(query: str) -> str:
    """
    检测任务复杂度:simple / medium / complex

    simple: 单一数据查询(如"沪深300估值多少"、"债市温度")
    medium: 需要分析但范围明确(如"白酒估值高吗"、"最近有什么新闻")
    complex: 需要多维度分析(如"白酒能买吗"、"帮我做个定投方案")
    """
    query = query.strip()

    # 复杂任务关键词(需要多专家协作:投资决策+仓位+风险)
    complex_keywords = [
        "加仓", "减仓", "建仓", "清仓",
        "定投", "配置", "组合", "方案", "策略", "计划",
        "风险", "回撤", "波动",
        "持仓", "盈亏", "我的基金", "仓位",
        "怎么分配", "如何配置",
    ]

    # 中等任务关键词(对比分析、单一维度分析)
    medium_keywords = [
        "对比", "比较", "区别", "差异", "哪个好", "选哪个", "还是",
        "怎么样", "怎么看", "值得买", "能买吗", "可以买", "买入",
        "卖出", "持有",
        "现在", "当前", "适合", "应该",
    ]

    # 简单任务关键词(单一数据查询)
    simple_keywords = [
        "估值", "百分位", "PE", "PB", "z-score",
        "债市温度", "温度",
        "多少", "是什么", "查一下", "查询",
        "最新", "今天", "最近",
    ]

    # 闲聊关键词(不需要专家分析)
    chat_keywords = [
        "你好", "谢谢", "好的", "明白了", "知道了",
        "什么是", "解释", "介绍", "定义", "含义",
        "天气", "笑话", "故事",
    ]

    # 检查是否是复杂任务
    complex_score = sum(1 for kw in complex_keywords if kw in query)
    # 检查是否是中等任务
    medium_score = sum(1 for kw in medium_keywords if kw in query)
    # 检查是否是简单任务
    simple_score = sum(1 for kw in simple_keywords if kw in query)
    # 检查是否是闲聊
    chat_score = sum(1 for kw in chat_keywords if kw in query)

    # 如果包含"吗"、"呢"等疑问词
    has_question_mark = bool(re.search(r'[吗呢??]', query))

    # 纯闲聊:短消息 + 闲聊关键词 + 无投资关键词
    if len(query) <= 10 and chat_score > 0 and complex_score == 0 and medium_score == 0 and simple_score == 0:
        return "chat"

    # 很短的消息(<6字),没有投资关键词,也没有疑问词 → chat
    if len(query) <= 5 and complex_score == 0 and medium_score == 0 and simple_score == 0 and not has_question_mark:
        return "chat"

    # 如果只是查询单一指标(很短的查询,且无疑问词),倾向于简单
    if len(query) <= 6 and simple_score > 0 and not has_question_mark and complex_score == 0:
        return "simple"

    # 有疑问词时,需要进一步分析
    if has_question_mark:
        # 包含复杂关键词 → complex
        if complex_score >= 1:
            return "complex"
        # 包含中等关键词 → medium(如"可以买吗"、"A和B区别")
        if medium_score >= 1:
            return "medium"
        # 包含简单关键词但有疑问 → medium(如"估值高吗")
        if simple_score >= 1:
            return "medium"
        # 其他有疑问的 → medium
        return "medium"

    # 无疑问词时
    if complex_score >= 2:
        return "complex"
    elif complex_score >= 1 or medium_score >= 1:
        return "medium"
    elif simple_score >= 1:
        return "medium"
    else:
        return "simple"


def route_to_specialists_by_keywords(query: str, complexity: str = "complex") -> list[str]:
    """根据关键词路由到合适的专家。返回 agent_key 列表。"""
    query = query.strip()
    specialists = []

    # 链接检测 → 文章解读专家
    if detect_urls(query):
        specialists.append("article_expert")
        # 如果只是链接+简单指令,只用文章专家
        query_without_url = re.sub(r'https?://[^\s]+', '', query).strip()
        if len(query_without_url) < 20:
            return specialists

    # 估值相关关键词 → 估值专家
    valuation_keywords = ["估值", "PE", "PB", "百分位", "z-score", "高估", "低估", "贵不贵", "便宜"]
    if any(kw in query for kw in valuation_keywords):
        specialists.append("valuation_expert")

    # 新闻/市场动态关键词 → 择时分析师
    news_keywords = ["新闻", "最新", "动态", "政策", "消息", "市场", "今天", "昨天"]
    if any(kw in query for kw in news_keywords):
        specialists.append("market_analyst")

    # 风险相关关键词 → 风险评估师
    risk_keywords = ["风险", "回撤", "波动", "最大回撤", "重仓", "满仓", "清仓"]
    if any(kw in query for kw in risk_keywords):
        specialists.append("risk_assessor")

    # 债券相关关键词 → 市场分析师 + 资产配置师
    bond_keywords = ["债券", "债市", "国债", "利率债", "信用债", "可转债", "收益率",
                     "久期", "债券基金", "短债", "长债", "纯债", "债基",
                     "资金面", "货币宽松", "加息", "降息", "央行"]
    if any(kw in query for kw in bond_keywords):
        specialists.append("market_analyst")
        if "allocation_advisor" not in specialists:
            specialists.append("allocation_advisor")

    # 配置相关关键词 → 资产配置师
    allocation_keywords = ["配置", "配比", "定投", "股债", "组合"]
    if any(kw in query for kw in allocation_keywords):
        specialists.append("allocation_advisor")

    # 持仓相关关键词 → 风险评估师 + 资产配置师
    portfolio_keywords = ["持仓", "加仓", "减仓", "盈亏", "我的基金", "持有", "仓位"]
    if any(kw in query for kw in portfolio_keywords):
        if "risk_assessor" not in specialists:
            specialists.append("risk_assessor")
        if "allocation_advisor" not in specialists:
            specialists.append("allocation_advisor")

    # 高风险行动建议 → 必要时补风险评估
    action_keywords = [
        "买入", "加仓", "建仓", "卖出", "减仓", "清仓", "追涨", "重仓",
        "满仓", "梭哈", "可以买吗", "要不要买", "要不要卖",
    ]
    if any(kw in query for kw in action_keywords):
        if "risk_assessor" not in specialists:
            specialists.append("risk_assessor")

    # 基金分析关键词 → 基金分析师
    fund_analysis_keywords = ["操作记录", "交易记录", "基金分析", "基金表现", "复盘",
                               "收益怎么样", "赚了", "亏了", "买卖", "操作复盘",
                               "我的操作", "这只基金", "基金持仓"]
    if any(kw in query for kw in fund_analysis_keywords):
        if "fund_analyst" not in specialists:
            specialists.append("fund_analyst")

    # 默认返回估值专家
    if not specialists:
        specialists.append("valuation_expert")

    # ── 根据复杂度上限截断，防止过度调度 ──
    MAX_SPECIALISTS = {
        "simple": 1,
        "medium": 2,
        "complex": 4,
    }
    max_allowed = MAX_SPECIALISTS.get(complexity, 4)
    if len(specialists) > max_allowed:
        # 统一优先级排序：高优先级在前，低优先级在后，其余中间
        HIGH_PRIORITY = ["valuation_expert", "allocation_advisor", "risk_assessor", "market_analyst"]
        LOW_PRIORITY = ["macro_strategist"]
        def _priority_key(s):
            if s in HIGH_PRIORITY:
                return 0
            if s in LOW_PRIORITY:
                return 2
            return 1
        specialists = sorted(specialists, key=_priority_key)[:max_allowed]
        logger.info(f"关键词路由截断: {len(specialists)}/{max_allowed} (保留: {specialists})")

    return specialists


# ── 场景化 RAG 映射 ──────────────────────────────────────

SCENARIO_RAG_MAP = {
    "valuation_expert": {
        "query_suffix": "估值 PE PB 安全边际 内在价值 百分位",
        "content_types": ["book", "valuation", "analysis"],
    },
    "market_analyst": {
        "query_suffix": "市场周期 择时 牛熊 情绪 资金流向",
        "content_types": ["book", "article", "author_article", "valuation"],
    },
    "allocation_advisor": {
        "query_suffix": "资产配置 分散投资 仓位 再平衡 股债比例",
        "content_types": ["book", "valuation", "analysis"],
    },
    "risk_assessor": {
        "query_suffix": "风险 回撤 波动 最大回撤 风险控制",
        "content_types": ["book", "valuation", "analysis"],
    },
    "fund_analyst": {
        "query_suffix": "基金选择 业绩 费用 基金经理 跟踪误差",
        "content_types": ["book", "analysis", "valuation"],
    },
    "article_expert": {
        "query_suffix": "文章解读 观点分析 投资逻辑 研报",
        "content_types": ["article", "author_article", "book"],
    },
}


def build_scenario_rag_context(query: str, specialists: list[str],
                                original_rag_context: str = "") -> str:
    """
    根据命中的专家类型,对原始 RAG 上下文做场景化增强。
    如果原始 RAG 已有内容,补充场景化检索结果;否则全新构建。
    """
    from services.rag import build_rag_context_with_details

    # 收集所有命中专家的场景配置
    scenario_queries = []
    all_content_types = set()
    for specialist in specialists:
        if specialist in SCENARIO_RAG_MAP:
            cfg = SCENARIO_RAG_MAP[specialist]
            scenario_queries.append(cfg["query_suffix"])
            all_content_types.update(cfg["content_types"])

    if not scenario_queries:
        return original_rag_context

    # 构建场景化查询:原始问题 + 场景关键词
    scenario_query = f"{query} {' '.join(scenario_queries)}"

    # 场景化检索(限制 3 条,避免过多)
    result = build_rag_context_with_details(
        scenario_query,
        content_types=list(all_content_types) if all_content_types else None,
        limit=3,
    )
    scenario_context = result.get("context", "")

    # 合并:原始 RAG + 场景化 RAG
    if original_rag_context and scenario_context:
        return f"{original_rag_context}\n\n---\n\n{scenario_context}"
    return scenario_context or original_rag_context


def get_context_config(complexity: str) -> dict:
    """根据复杂度返回上下文配置，max_specialists 从 DB 读取。"""
    try:
        max_key = f"max_specialists.{complexity}"
        from db.config import get_config_int
        db_max = get_config_int(max_key, 0)
    except Exception:
        db_max = 0

    if complexity == "simple":
        max_spec = db_max if db_max > 0 else 1
        return {
            "history_limit": 3,
            "rag_enabled": True,
            "max_specialists": max_spec,
            "rag_max_chars": 800,
        }
    elif complexity == "medium":
        max_spec = db_max if db_max > 0 else 2
        return {
            "history_limit": 5,
            "rag_enabled": True,
            "max_specialists": max_spec,
            "rag_max_chars": 1500,
        }
    else:  # complex
        max_spec = db_max if db_max > 0 else 4
        return {
            "history_limit": 10,
            "rag_enabled": True,
            "max_specialists": max_spec,
            "rag_max_chars": 2500,
        }


def compress_history(history: list, max_messages: int = 10) -> list:
    """
    压缩对话历史:
    - 保留最近 max_messages 条完整消息
    - 更早的消息只保留摘要(第一条用户消息的前50字)
    """
    if len(history) <= max_messages:
        return history

    # 早期消息:只保留摘要
    early_messages = history[:-max_messages]
    recent_messages = history[-max_messages:]

    # 从早期消息中提取关键信息
    summary_parts = []
    for msg in early_messages:
        if msg["role"] == "user":
            # 用户消息:取前50字
            summary_parts.append(f"用户曾问: {msg['content'][:50]}...")
        elif msg["role"] == "assistant":
            # 助手消息:取前30字
            summary_parts.append(f"助手曾答: {msg['content'][:30]}...")

    # 构建摘要消息
    if summary_parts:
        summary = "以下是早期对话摘要(省略了详细内容):\n" + "\n".join(summary_parts[-5:])  # 最多保留5条摘要
        compressed = [{"role": "system", "content": summary}] + recent_messages
    else:
        compressed = recent_messages

    return compressed


def compress_rag_context(rag_context: str, max_chars: int = 2000) -> str:
    """
    压缩 RAG 上下文:
    - 截断到 max_chars 字符
    - 保留完整段落,避免截断在句子中间
    """
    if not rag_context or len(rag_context) <= max_chars:
        return rag_context

    # 截断到最大字符数
    truncated = rag_context[:max_chars]

    # 找到最后一个完整段落(双换行符)
    last_paragraph_end = truncated.rfind("\n\n")
    if last_paragraph_end > max_chars * 0.7:  # 如果截断点在70%以后
        truncated = truncated[:last_paragraph_end]

    return truncated + "\n...(已截断,更多内容请参考知识库)"

# ── Orchestrator 的工具 = 调用各个专家 Agent ──────────────

def build_orchestrator_tools(allowed_specialists: list[str] | None = None) -> list:
    """从数据库动态生成 Orchestrator 可调用的 consult_* 工具定义。"""
    specialists = load_specialist_agents()
    tools = []
    allowset = set(allowed_specialists or [])
    for key, info in specialists.items():
        if allowset and key not in allowset:
            continue
        tools.append({
            "type": "function",
            "function": {
                "name": f"consult_{key}",
                "description": f"咨询{info['name']},{info['description']}",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": f"向{info['name']}提出的具体问题",
                        },
                    },
                    "required": ["query"],
                },
            },
        })
    return tools


def build_expert_map() -> dict:
    """从数据库动态生成 consult_* 工具名到 agent_key 的映射。"""
    specialists = load_specialist_agents()
    return {f"consult_{key}": key for key in specialists}


def _append_specialist_synthesis_context(llm_messages: list, specialist_results: list) -> None:
    """将专家结论汇总为一条 user 消息，供最终综合阶段使用。"""
    if not specialist_results:
        return
    summary = "\n\n---\n\n".join(
        f"【{sr.get('agent', sr.get('agent_key', '专家'))}】\n{sr.get('analysis', '')}"
        for sr in specialist_results
        if sr.get("analysis")
    )
    if not summary.strip():
        return
    llm_messages.append({
        "role": "user",
        "content": f"以下是各专家的分析结果，请据此给出最终综合建议：\n\n{summary}",
    })

def build_orchestrator_system_prompt() -> str:
    """从数据库动态生成 Orchestrator 的 system prompt。"""
    specialists = load_specialist_agents()
    team_lines = []
    for key, info in specialists.items():
        team_lines.append(f"- {info['icon']} **{info['name']}**:{info['description']}")
    team_list = "\n".join(team_lines)

    return f"""你是投资分析助手的主控(Orchestrator),负责协调各领域专家 Agent 完成投资分析。

## 工作方式
1. 理解用户问题的核心意图
2. 决定需要咨询哪些专家(可同时咨询多个)
3. 收集各专家的分析结果
4. 综合各专家意见,给出最终的投资建议

## 专家团队
{team_list}

## 可用工具与引导
当用户的问题适合使用以下工具时,在回答末尾用 > 💡 提示用户:
- **持仓管理**:用户提到具体基金代码或想记录交易 → 「可以在持仓管理中录入」
- **估值查询**:用户问某指数贵不贵 → 「可以用估值数据查看历史百分位」
- **热点分析**:用户问市场发生了什么 → 「可以运行热点分析获取最新市场动态」
- **健康分**:用户问自己的组合怎么样 → 「可以查看持仓健康分了解综合评估」
- **决策中心**:用户有明确行动意向 → 「可以保存为决策卡片,后续跟踪效果」
- **对比分析**:用户在犹豫两只基金 → 「可以用对比分析功能做详细对比」
- **情景推演**:用户问如果加仓/减仓会怎样 → 「可以用情景推演模拟不同操作的结果」

## 回答原则
- 综合各专家意见,给出明确的判断和建议
- 如果专家意见有分歧,指出分歧点并给出你的倾向
- 引用专家的具体数据和分析
- 给出 actionable 的投资建议

## 回复结构要求（必须遵循）
1. **结论先行**：第一段直接给出核心判断和操作建议，不要铺垫
2. **数据支撑**：关键数据用表格呈现，每个数字必须标注来源（估值数据库/知识库/持仓数据）
3. **操作建议**：具体的加减仓建议（金额/比例/时机），不要模糊的"建议关注"
4. **风险提示**：可能的风险场景和应对措施
5. **置信度标注**：在核心结论后标注 [高置信度/中置信度/低置信度]，让用户知道你有多确定

## 持仓亏损处理原则
当用户持仓出现亏损或连续下跌时,必须参考知识库中的「4%定投法(强化版)」策略:
- 不要简单建议割肉止损,先评估估值水平和基本面
- 如果估值已进入低估区间,建议按4%间隔分批加仓摊低成本
- 计算平均成本和回盈价位,给用户具体的数字参考
- 强调纪律性:按计划执行,不因恐慌改变策略
- 如果基本面恶化(非单纯下跌),才建议止损
- 使用 Markdown 格式,层次清晰

## 专家分析深度要求
- 估值分析师：多维度交叉验证（PE/PB/PS/股息率/风险溢价），不能只报一个指标
- 基金分析师：持仓穿透 + 业绩归因 + 同类对比 + 规模影响
- 风险管理师：压力测试场景 + 极端回撤分析
- 资产配置师：效率前沿分析 + 相关性矩阵 + 再平衡建议"""


def _execute_specialist(tool_name: str, query: str, cancel_event: threading.Event | None = None,
                        prebuilt_context: str = "", budget_mode: str = "normal", trace_id: str = "",
                        timeout_seconds: int = 120) -> str:
    """执行专家 Agent 调用,返回 JSON 字符串结果。

    支持超时、自动重试和降级机制：
    1. 首次调用超时 → 标记 timeout，自动重试 1 次
    2. 重试仍失败 → 降级为简化模式（不调用工具，直接用已有数据生成分析）
    3. 降级仍失败 → 返回 unavailable 占位结果
    """
    agent_key = build_expert_map().get(tool_name)
    if not agent_key:
        return json.dumps({"error": f"未知专家: {tool_name}"}, ensure_ascii=False)

    agent_name = agent_key
    try:
        from db.agents import load_specialist_agents
        agent_name = load_specialist_agents().get(agent_key, {}).get("name", agent_key)
    except Exception:
        pass

    # ── 超时+重试逻辑 ──
    max_retries = 1  # 失败后自动重试 1 次
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            _check_cancel(cancel_event)
            # 增强6: 成本路由 - 根据 agent_key 选择模型
            model = _get_model_for_agent(agent_key, budget_mode) if _is_cost_routing_enabled() else MODEL

            # 超时控制：用线程池 + future 实现超时
            import concurrent.futures as _cf
            with _cf.ThreadPoolExecutor(max_workers=1) as _pool:
                future = _pool.submit(
                    run_specialist, agent_key, query,
                    prebuilt_context=prebuilt_context, model=model, trace_id=trace_id
                )
                try:
                    result = future.result(timeout=timeout_seconds)
                    return json.dumps(result, ensure_ascii=False)
                except _cf.TimeoutError:
                    future.cancel()
                    last_error = TimeoutError(f"专家 {agent_name} 执行超时（{timeout_seconds}秒）")
                    logger.warning(f"[trace:{trace_id}] 专家 {tool_name} 第{attempt+1}次调用超时（{timeout_seconds}s）")
                    if attempt < max_retries:
                        logger.info(f"[trace:{trace_id}] 专家 {tool_name} 准备重试...")
                        continue
                    # 超时后不再重试，直接进入降级
                    break
        except CancelledError:
            raise
        except Exception as e:
            last_error = e
            logger.error(f"[trace:{trace_id}] 专家 {tool_name} 第{attempt+1}次执行异常: {e}")
            if attempt < max_retries:
                logger.info(f"[trace:{trace_id}] 专家 {tool_name} 准备重试...")
                continue
            break

    # ── 降级：简化模式（不调用工具，直接用 system_prompt + query 生成分析）──
    logger.info(f"[trace:{trace_id}] 专家 {tool_name} 进入降级模式（简化分析，不调用工具）")
    try:
        from services.llm_service import _call_llm, MODEL as _MODEL
        from db.agents import load_specialist_agents as _lsa
        _agent = _lsa().get(agent_key, {})
        _sys_prompt = _agent.get("system_prompt", "")
        if not _sys_prompt:
            raise ValueError(f"专家 {agent_key} 无 system_prompt")

        _degraded_response = _call_llm(
            caller=f"specialist_degraded:{agent_key}",
            trace_id=trace_id,
            model=_MODEL,
            messages=[
                {"role": "system", "content": _sys_prompt + "\n\n注意：本次为降级模式，工具不可用，请基于已有数据和专业知识直接给出分析。"},
                {"role": "user", "content": query},
            ],
            temperature=0.3,
            max_tokens=4096,
        )
        _degraded_text = _degraded_response.choices[0].message.content or ""
        _degraded_tokens = _degraded_response.usage.total_tokens if _degraded_response.usage else 0
        logger.info(f"[trace:{trace_id}] 专家 {tool_name} 降级分析完成，tokens={_degraded_tokens}")
        return json.dumps({
            "agent_key": agent_key,
            "agent": agent_name,
            "icon": _agent.get("icon", "⚠️"),
            "analysis": _degraded_text,
            "tool_calls": [],
            "duration_ms": 0,
            "status": "degraded",
        }, ensure_ascii=False)
    except Exception as degraded_e:
        logger.error(f"[trace:{trace_id}] 专家 {tool_name} 降级模式也失败: {degraded_e}")

    # ── 最终兜底：unavailable 占位结果 ──
    _err_msg = f"{type(last_error).__name__}: {last_error}" if last_error else "未知错误"
    return json.dumps({
        "agent_key": agent_key,
        "agent": agent_name,
        "icon": "⚠️",
        "analysis": f"[该专家分析暂时不可用：{type(last_error).__name__ if last_error else '未知错误'}]",
        "tool_calls": [],
        "duration_ms": 0,
        "status": "unavailable",
        "error": _err_msg,
    }, ensure_ascii=False)


# ── 升级二：推理过程可视化（零 LLM 成本，仅格式化已有数据）──


def _extract_conclusion(text: str) -> str:
    """从分析文本中提取结论（关键词后的前 200 字）。"""
    if not text:
        return ""
    for kw in ["最终建议", "结论", "综合建议", "操作建议", "建议"]:
        idx = text.find(kw)
        if idx != -1:
            return text[idx:idx + 200].strip()
    return text[:200].strip()


def _extract_key_points(text: str) -> list[str]:
    """从分析文本中提取关键点（以编号/符号开头的行）。"""
    if not text:
        return []
    points = []
    for line in text.split('\n'):
        stripped = line.strip()
        if stripped.startswith(("1.", "2.", "3.", "4.", "5.", "→", "▶", "●", "•", "- ", "•")):
            if 10 < len(stripped) < 200:
                points.append(stripped)
    return points[:5]


# ── P0-2.1：共享黑板架构 — 专家协同 ──

# 评级关键词（按优先级排序，越具体越先匹配）
_RATING_KEYWORDS_LIST = [
    "建议买入", "建议加仓", "建议持有", "建议减仓", "建议卖出", "建议观望",
    "买入", "加仓", "持有", "减仓", "卖出", "观望", "逢低买入", "逢高减仓",
]


# P1: _extract_structured_conclusion / _format_blackboard_summary 已被
# agent.blackboard.Blackboard + extract_entry_from_result 取代（统一黑板）。
# 辅助函数 _extract_key_points / _extract_conclusion / _RATING_KEYWORDS_LIST 保留，
# 其他模块仍在使用。


# ── P0 全景扫描：在 specialist 执行前提供全局数据 ──

def _run_portfolio_panorama_scan(conn=None) -> str:
    """
    在 specialist 执行前，对用户持仓做全景扫描。
    纯 SQL 查询，零 LLM 成本。

    产出一个统一的「全景上下文」块，注入到 specialist 的 prebuilt_context：
    持仓概况/盈亏/近30天交易/风险信号。

    Returns: markdown 格式，无数据时返回空字符串
    """
    try:
        from db._conn import _get_conn
        from services.portfolio_context import build_portfolio_context
        from db.portfolio import list_transactions, get_portfolio_summary
    except Exception:
        return ""

    close_conn = False
    if conn is None:
        conn = _get_conn()
        close_conn = True

    try:
        import datetime as _dt
        lines = []

        # 1. 持仓概况
        portfolio_ctx = build_portfolio_context()
        if portfolio_ctx and "无持仓" not in portfolio_ctx:
            lines.append("## 当前持仓概况")
            lines.append(portfolio_ctx[:800])

        # 2. 近30天交易摘要
        try:
            txns = list_transactions()
            active_txns = []
            cutoff = _dt.datetime.now() - _dt.timedelta(days=30)
            cutoff_str = cutoff.strftime("%Y-%m-%d")

            for t in txns:
                txn_date = (t.get("transaction_date") or "")[:10]
                if txn_date >= cutoff_str:
                    active_txns.append(t)

            if active_txns:
                acts = {"buy": 0, "sell": 0, "dca": 0}
                fund_names = []
                for t in active_txns:
                    action = (t.get("action") or t.get("type") or "").lower()
                    if action in ("buy", "买入"):
                        acts["buy"] += 1
                    elif action in ("sell", "卖出"):
                        acts["sell"] += 1
                    elif action in ("dca", "定投"):
                        acts["dca"] += 1
                    fname = t.get("fund_name") or t.get("fund_code", "")
                    if fname and fname not in fund_names:
                        fund_names.append(fname)

                parts = []
                if acts["buy"]:
                    parts.append(f"买入{acts['buy']}次")
                if acts["dca"]:
                    parts.append(f"定投{acts['dca']}次")
                if acts["sell"]:
                    parts.append(f"卖出{acts['sell']}次")
                if fund_names:
                    parts.append(f"涉及基金:{','.join(fund_names[:3])}")

                lines.append("## 近30天操作记录")
                lines.append("- 近30天有操作: " + "；".join(parts))
        except Exception:
            pass

        # 3. 风险信号
        try:
            summary = get_portfolio_summary()
            holdings = summary.get("holdings", []) if summary else []
            warnings = []

            for h in holdings:
                shares = h.get("shares", 0) or 0
                if shares <= 0:
                    continue
                pct = h.get("ratio", 0) or 0
                profit_rate = h.get("profit_rate", 0) or 0
                fname = h.get("fund_name", "") or h.get("fund_code", "")

                if profit_rate < -0.15:
                    warnings.append(f"【亏损>15%】{fname} 亏损率{profit_rate:.1%}")
                if pct > 25:
                    warnings.append(f"【集中度>25%】{fname} 占比{pct:.0f}%")

            if warnings:
                lines.append("## ⚠️ 持仓风险信号")
                for w in warnings[:3]:
                    lines.append(f"- {w}")
        except Exception:
            pass

        text = "\n".join(lines)
        return text if len(text) > 50 else ""

    except Exception as e:
        logger.warning(f"全景扫描失败（不阻塞主流程）: {e}")
        return ""
    finally:
        if close_conn:
            try:
                conn.close()
            except Exception:
                pass


def build_reasoning_trail(
    query: str,
    query_rewritten: str,
    complexity: str,
    specialist_results: list[dict],
    arbitration_result: dict | None,
    rag_context: str,
    duration_ms: int = 0,
) -> dict:
    """构建推理过程追踪数据（不新增 LLM 调用，仅结构化已有数据）。"""
    trail = {
        "query": query[:200],
        "query_rewritten": query_rewritten if query_rewritten and query_rewritten != query else None,
        "complexity": complexity,
        "rag": {
            "used": bool(rag_context),
            "context_length": len(rag_context or ""),
        },
        "specialists": [],
        "arbitration": None,
        "timeline": [],
        "total_duration_ms": duration_ms,
    }

    # 专家分析步骤
    for sr in specialist_results or []:
        if sr.get("is_cross_review"):
            continue
        analysis = sr.get("analysis", "")
        step = {
            "agent": sr.get("agent", "unknown"),
            "agent_key": sr.get("agent_key", ""),
            "icon": sr.get("icon", "🤖"),
            "duration_ms": sr.get("duration_ms", 0),
            "conclusion": _extract_conclusion(analysis),
            "key_points": _extract_key_points(analysis),
            "is_arbitration": bool(sr.get("is_arbitration")),
        }
        trail["specialists"].append(step)
        trail["timeline"].append({
            "type": "arbitration" if step["is_arbitration"] else "specialist",
            "agent": step["agent"],
            "icon": step["icon"],
            "duration_ms": step["duration_ms"],
            "conclusion": step["conclusion"][:120] if step["conclusion"] else "",
        })

    # 仲裁步骤（单独标记）
    if arbitration_result:
        trail["arbitration"] = {
            "duration_ms": arbitration_result.get("duration_ms", 0),
            "conclusion": _extract_conclusion(arbitration_result.get("analysis", "")),
            "has_condition_framework": bool(arbitration_result.get("condition_framework")),
        }

    return trail


# ── P1-1：多智能体结论持久化（Bug C 修复）──────────────────────

# 板块关键词（用于在没有明确基金代码时识别分析标的）
_SECTOR_KEYWORDS = [
    "白酒", "医药", "医疗", "新能源", "半导体", "芯片", "消费", "科技",
    "金融", "银行", "券商", "房地产", "军工", "周期", "创业板", "科创板",
    "港股", "美股", "债券", "黄金", "原油", "消费电子", "光伏", "锂电",
    "军工", "人工智能", "机器人", "5G", "通信", "计算机",
]


def _extract_target_subject(query: str, analysis: str = "") -> str:
    """从 query 或分析文本中提取结论标的。

    三级提取：
    1. 6 位数字基金代码（最优先）
    2. 中文板块关键词
    3. 兜底 "整体组合"
    """
    import re as _re
    text = f"{query} {analysis}"

    # 1. 6 位数字基金/股票代码（兼容前后有 1-2 位前缀如 0、1、5、6、11、15、16）
    code_match = _re.search(r'(?<!\d)(\d{6})(?!\d)', text)
    if code_match:
        return code_match.group(1)

    # 2. 板块关键词
    for kw in _SECTOR_KEYWORDS:
        if kw in query:
            return kw

    # 3. 兜底
    return "整体组合"


def _extract_rating(analysis: str) -> str:
    """从分析文本中提取评级关键词。"""
    if not analysis:
        return ""
    for kw in _RATING_KEYWORDS_LIST:
        if kw in analysis:
            return kw.replace("建议", "")
    return ""


def _infer_action_from_rating(rating: str) -> str:
    """把评级关键词映射为标准化 action 字段。"""
    if not rating:
        return ""
    rating_lower = rating.strip()
    if rating_lower in ("买入", "加仓", "逢低买入"):
        return "buy" if rating_lower == "买入" else "increase"
    if rating_lower in ("卖出", "减仓", "逢高减仓"):
        return "sell" if rating_lower == "卖出" else "decrease"
    if rating_lower in ("持有", "观望"):
        return "hold"
    return ""


def _persist_agent_conclusions(
    conversation_id: int,
    message_id: int,
    query: str,
    specialist_results: list[dict],
    arbitration_result: dict | None,
    trace_id: str = "",
) -> int:
    """把多智能体分析的结论持久化到 analysis_conclusions 表。

    设计稿 P1-1：解决 Bug C — ai_dialogue 路径从不持久化结论，token 被消耗但无沉淀。

    Args:
        conversation_id: 对话 ID
        message_id: 消息 ID
        query: 用户原始问题
        specialist_results: 专家分析结果列表
        arbitration_result: 仲裁结论（可为 None）
        trace_id: 追踪 ID

    Returns:
        成功写入的结论条数
    """
    # 1. 开关检查（默认 true）
    try:
        from db.config import get_config_bool
        if not get_config_bool("agent.persist_conclusions", True):
            return 0
    except Exception:
        pass  # 配置读取失败 → 继续执行（默认开启）

    if not conversation_id or not message_id:
        return 0

    # 2. 幂等检查：同 (conversation_id, message_id) 已写过则跳过
    try:
        from db.analysis_conclusions import (
            has_conclusions_for_message,
            save_analysis_conclusion,
            link_conclusion_to_decisions,
        )
        if has_conclusions_for_message(conversation_id, message_id):
            logger.info(
                f"[P1-1] conv={conversation_id} msg={message_id} 已有结论记录，跳过持久化"
            )
            return 0
    except Exception as e:
        logger.warning(f"[trace:{trace_id}] [P1-1] 幂等检查失败（继续写入）: {e}")

    written = 0

    # 3. 写入每个非 cross_review 的专家结论
    for sr in specialist_results or []:
        if sr.get("is_cross_review"):
            continue
        if sr.get("is_arbitration"):
            continue  # 仲裁单独处理

        analysis = sr.get("analysis", "") or ""
        if not analysis or len(analysis) < 50:
            continue  # 内容过短，无沉淀价值

        agent_key = sr.get("agent_key", "unknown")
        agent_name = sr.get("agent", agent_key)

        try:
            target_subject = _extract_target_subject(query, analysis)
            rating = _extract_rating(analysis)
            action = _infer_action_from_rating(rating)
            summary = _extract_conclusion(analysis)[:200]

            # 跳过无信息量的兜底
            if "分析过程遇到问题" in summary:
                continue

            conclusion_id = save_analysis_conclusion(
                source_system="ai_dialogue",
                source_type=f"specialist:{agent_key}",
                source_id=None,
                target_subject=target_subject,
                action=action,
                summary=f"[{agent_name}] {summary}",
                reasoning=analysis[:600],
                key_variables=_extract_key_points(analysis)[:5] or None,
                data_basis=None,
                confidence=0.7,  # 单专家结论默认 0.7
                urgent=0,
                conversation_id=conversation_id,
                message_id=message_id,
            )
            if conclusion_id:
                written += 1
                # P1-3：尝试桥接到相关决策记录
                try:
                    from db.config import get_config_bool as _gcb
                    if _gcb("agent.link_cross_system_refs", True):
                        link_conclusion_to_decisions(conclusion_id, target_subject)
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"[trace:{trace_id}] [P1-1] 写入专家 {agent_key} 结论失败: {e}")
            continue

    # 4. 写入仲裁结论（高置信度、标记 urgent）
    if arbitration_result:
        arb_analysis = arbitration_result.get("analysis", "") or ""
        if arb_analysis and len(arb_analysis) >= 50:
            try:
                target_subject = _extract_target_subject(query, arb_analysis)
                rating = _extract_rating(arb_analysis)
                action = _infer_action_from_rating(rating)
                summary = _extract_conclusion(arb_analysis)[:200]

                conclusion_id = save_analysis_conclusion(
                    source_system="ai_dialogue",
                    source_type="orchestrator",
                    source_id=None,
                    target_subject=target_subject,
                    action=action,
                    summary=f"[仲裁结论] {summary}",
                    reasoning=arb_analysis[:800],
                    key_variables=_extract_key_points(arb_analysis)[:5] or None,
                    data_basis=None,
                    confidence=0.9,  # 仲裁结论高置信度
                    urgent=1,  # 仲裁结论应被高优关注
                    conversation_id=conversation_id,
                    message_id=message_id,
                )
                if conclusion_id:
                    written += 1
                    logger.info(
                        f"[P1-1] conv={conversation_id} msg={message_id} "
                        f"写入 {written} 条结论 (含仲裁)"
                    )
                    # P1-3：仲裁结论桥接
                    try:
                        from db.config import get_config_bool as _gcb
                        if _gcb("agent.link_cross_system_refs", True):
                            link_conclusion_to_decisions(conclusion_id, target_subject)
                    except Exception:
                        pass
            except Exception as e:
                logger.warning(f"[trace:{trace_id}] [P1-1] 写入仲裁结论失败: {e}")

    if written == 0:
        logger.info(f"[P1-1] conv={conversation_id} msg={message_id} 无可写入结论")
    return written


def orchestrate(query: str, history: list, rag_context: str = "", cancel_event: threading.Event | None = None, target_specialists: list[str] = None, conversation_id: int = 0, message_id: int = 0, trace_id: str = "") -> dict:
    """
    Orchestrator 主循环。

    流程:
    1. 检测任务复杂度
    2. 根据复杂度优化上下文
    3. LLM 分析用户意图
    4. 决定调用哪些专家
    5. 执行专家 Agent(每个专家独立完成工具调用)
    6. 将专家结果反馈给 Orchestrator
    7. Orchestrator 综合给出最终建议

    返回:
        {
            "answer": "最终综合建议",
            "specialist_results": [
                {"agent": "估值专家", "icon": "📊", "analysis": "..."},
                ...
            ],
            "tool_calls": [...],
            "turns": 实际轮次数,
            "complexity": "simple/medium/complex"
        }
    """
    start_time = time.time()
    resume_message_id = None
    completed_specialists = set()
    clarification = {}

    # 0. Token 预算检查
    budget = check_token_budget()
    logger.info(f"[trace:{trace_id}] orchestrate 入口 query={query[:50]}... budget={budget['mode']}")
    if budget["mode"] == "exceeded":
        return {
            "answer": f"今日分析额度已用完({budget['used']:,}/{budget['limit']:,} tokens),请明天再来。",
            "specialist_results": [],
            "tool_calls": [],
            "turns": 0,
            "complexity": "simple",
            "error": "token_budget_exceeded",
        }

    # 0.5 链接检测与文章抓取
    article_context = ""
    article_fetch_failed = False
    if detect_urls(query):
        query, article_context = enrich_query_with_article(query)
        if article_context:
            if article_context.startswith("[抓取失败]"):
                article_fetch_failed = True
                logger.warning(f"文章抓取失败: {article_context}")
            else:
                logger.info(f"已注入文章内容到查询中")

    # 0.6 查询改写（多轮对话代词/省略补全，规则优先 LLM 兜底）
    rewrite_meta = {"rewritten": False}
    try:
        from agent.query_rewriter import rewrite_query
        query, rewrite_meta = rewrite_query(query, history)
    except Exception as e:
        logger.warning(f"查询改写失败(不影响主流程): {e}")

    # 1. 需求澄清(使用 LLM 分析问题)
    if target_specialists:
        all_agents = load_specialist_agents()
        valid_specialists = [s for s in target_specialists if s in all_agents]
        if valid_specialists:
            complexity = "simple" if len(valid_specialists) == 1 else "medium"
            route_result = {
                "complexity": complexity,
                "specialists": valid_specialists,
                "reason": f"用户通过 @mention 指定了专家: {', '.join(valid_specialists)}",
                "needs_arbitration": len(valid_specialists) >= 2,
                "route_by": "mention",
                "refined_query": query,
            }
            clarification = route_result
            context_config = get_context_config(complexity)
            token_budget = get_token_budget(complexity)
            refined_query = query
            specialists = valid_specialists
            logger.info(f"@mention 指定专家: {valid_specialists}")
        else:
            logger.warning(f"@mention 指定了无效的 agent_key: {target_specialists},回退到自动路由")
            target_specialists = None

    if not target_specialists:
        # 使用 Smart Router 选择专家和复杂度(规则优先,LLM 兜底)
        history_summary = _build_history_summary(history)
        portfolio_summary = ""  # 后续阶段才构建 prebuilt_context,先用空摘要
        route_result = _router.route(
            query,
            history_summary=history_summary,
            portfolio_summary=portfolio_summary,
            target_specialists=None,
        )
        complexity = route_result["complexity"]
        clarification = route_result
        context_config = get_context_config(complexity)
        token_budget = get_token_budget(complexity)
        logger.info(f"路由结果: {route_result}")

        # 使用 LLM 兜底时可能返回优化后的问题;规则路由时保持原问题
        refined_query = route_result.get("refined_query", query) or query
        specialists = route_result.get("specialists", [])

        # 按复杂度上限截断 specialist 数量
        max_spec = context_config.get("max_specialists", 4)
        if len(specialists) > max_spec:
            logger.info(f"specialists 超出上限({len(specialists)} > {max_spec})，截断")
            specialists = specialists[:max_spec]

        # 恢复模式：排除已完成的专家，避免重复执行
        if resume_message_id and specialists and completed_specialists:
            specialists = [s for s in specialists if s not in completed_specialists]
            if not specialists:
                specialists = list(completed_specialists)[:3]
                logger.info(f"恢复模式:所有路由专家已完成,复用已有结果: {specialists}")
            else:
                logger.info(f"恢复模式:排除已完成专家后剩余: {specialists}")

        # 若未命中任何专家,回退到原 clarify_requirement
        if not specialists:
            clarification = clarify_requirement(query, trace_id=trace_id)
            complexity = clarification["complexity"]
            context_config = get_context_config(complexity)
            token_budget = get_token_budget(complexity)
            logger.info(f"Smart Router 未命中,回退到需求澄清: {clarification}")
            refined_query = clarification.get("refined_query", query)
            specialists = clarification.get("specialists", [])

    # 1.5 场景化 RAG 增强:根据命中的专家类型补充相关书籍知识
    rag_context = build_scenario_rag_context(refined_query, specialists, rag_context)

    # 2. 根据复杂度优化上下文(Token 预算管理)
    system_content = build_orchestrator_system_prompt()

    # RAG 上下文(token 感知截断)
    rag_budget = int(token_budget["total_context"] * token_budget["rag_pct"])
    if rag_context and context_config["rag_enabled"]:
        compressed_rag = compress_rag_token_aware(rag_context, max_tokens=rag_budget)
        system_content += f"\n\n参考信息:\n{compressed_rag}"

    # 注入用户偏好画像(从反馈学习中积累)
    preference_ctx = get_preference_context("default")
    if preference_ctx:
        system_content += f"\n\n{preference_ctx}"

    # 注入 KYC 理财画像(让编排器基于用户画像做路由决策)
    from agent.kyc import kyc_profile_to_text
    kyc_text = kyc_profile_to_text("default")
    if kyc_text:
        system_content += f"\n\n{kyc_text}"

    # 注入跨对话用户记忆
    user_memory = build_user_memory_context("default")
    if user_memory:
        system_content += f"\n\n<user_memory>\n{user_memory}\n</user_memory>"

    # 注入持仓上下文 + 估值上下文(同时构建 prebuilt_context 供 specialist 复用)
    prebuilt_context = ""

    # P0 全景扫描：注入持仓概况+交易记录+风险信号
    try:
        panorama_ctx = _run_portfolio_panorama_scan()
        if panorama_ctx:
            prebuilt_context += panorama_ctx + "\n\n"
    except Exception as e:
        logger.warning(f"全景扫描注入失败（不阻塞主流程）: {e}")

    # Bridge A: 注入24h分析结论上下文
    _, analysis_ctx = _inject_analysis_context(refined_query)
    if analysis_ctx:
        prebuilt_context += analysis_ctx
        try:
            system_content += f"\n\n{analysis_ctx}"
        except Exception:
            pass

    # P1-2：跨对话结论定向复用（同标的命中时注入更强参考，默认关闭）
    try:
        from db.config import get_config_bool, get_config_int
        if get_config_bool("agent.reuse_recent_conclusions", False):
            _target = _extract_target_subject(refined_query or query, "")
            _hours = get_config_int("agent.reuse_conclusions_hours", 48)
            _reuse_ctx = _load_recent_conclusions(_target, hours=_hours, limit=3)
            if _reuse_ctx:
                prebuilt_context += _reuse_ctx
                logger.info(f"[P1-2] 注入同标的历史结论: target={_target}")
    except Exception as _e:
        logger.warning(f"[P1-2] 跨对话复用注入失败（不阻塞主流程）: {_e}")

    # 注入 RAG 知识库上下文到 prebuilt_context(让专家也能参考书籍/文章知识)
    if rag_context:
        compressed_rag_for_specialist = compress_rag_token_aware(rag_context, max_tokens=get_config_int('llm.max_tokens_rag_compress', 1500))
        prebuilt_context += f"## 知识库参考(书籍/文章/技能)\n{compressed_rag_for_specialist}\n\n"

    try:
        from services.portfolio_context import build_portfolio_context, build_valuation_summary
        portfolio_ctx = build_portfolio_context()
        # 始终注入持仓上下文(空持仓时也会明确告知"无持仓",防止 AI 编造)
        system_content += f"\n\n## 用户当前持仓\n{portfolio_ctx}"
        prebuilt_context += f"## 用户当前持仓\n{portfolio_ctx}\n\n"
        valuation_ctx = build_valuation_summary()
        if valuation_ctx:
            system_content += f"\n\n## 当前市场估值\n{valuation_ctx}"
            prebuilt_context += f"## 当前市场估值数据\n{valuation_ctx}\n\n"
    except Exception as e:
        logger.warning(f"注入持仓/估值上下文失败: {e}")

    # 注入债市数据到 prebuilt_context
    try:
        from routers.bond import _fetch_bond_data
        bond_raw = _fetch_bond_data()
        if bond_raw and len(bond_raw) > 1:
            last = bond_raw[-1]
            temp = last.get("degree", "?")
            rate = float(last["yield"]) * 100 if last.get("yield") else "?"
            prebuilt_context += f"## 债市数据\n- 债市温度: {temp}°\n- 10年期国债收益率: {rate}%\n\n"
    except Exception as e:
        logger.warning(f"注入债市数据失败: {e}")

    # 注入近期热点新闻到 prebuilt_context(同步调用,从缓存读取)
    try:
        from routers.dashboard import _hot_topics_cache
        hot_cache = _hot_topics_cache.get("data")
        if hot_cache:
            news_list = hot_cache.get("news", [])[:3]
            if news_list:
                news_lines = "\n".join(f"- {n.get('title', '')}" for n in news_list if n.get("title"))
                prebuilt_context += f"## 今日市场热点\n{news_lines}\n\n"
    except Exception as e:
        logger.warning(f"注入热点新闻失败: {e}")

    llm_messages = [{"role": "system", "content": system_content}]

    # 语义化压缩历史消息(LLM 摘要 + 近期原文)
    history_budget = int(token_budget["total_context"] * token_budget["history_pct"])
    compressed_history = compress_history_semantic(history, max_tokens=history_budget)
    for msg in compressed_history:
        llm_messages.append({"role": msg["role"], "content": msg["content"]})

    # 当前用户问题(使用优化后的问题)
    llm_messages.append({"role": "user", "content": refined_query})
    clarification = clarification if clarification else route_result or {}

    MAX_TURNS = 3 if budget["mode"] == "conservative" else 6
    force_skip_cross_review = budget["mode"] == "conservative"
    specialist_results = []
    all_tool_calls = []
    arbitration_done = False  # 标记仲裁是否已完成,避免重复调用
    route_result = None  # 路由结果，用于最终返回监控

    # 升级3: Plan & Execute 模式（通过 orchestration_config 启停，默认关闭）
    _plan_done = False
    _plan_execute_enabled = get_orchestration_config("plan_execute_enabled", "false") == "true"
    if _plan_execute_enabled:
        try:
            from agent.plan_executor import generate_plan, execute_plan
            available = [
                {"agent_key": k, "name": v["name"], "description": v.get("description", "")}
                for k, v in specialists.items()
            ]
            _plan = generate_plan(
                user_query=query, refined_query=refined_query,
                complexity=complexity, available_specialists=available, trace_id=trace_id,
            )
            logger.info(f"[trace:{trace_id}] Plan & Execute: {len(_plan.steps)} 步计划")
            yield {"type": "plan", "plan": _plan.to_dict()}

            specialist_results, all_tool_calls = execute_plan(
                plan=_plan, prebuilt_context=prebuilt_context, cancel_event=cancel_event,
            )
            _plan_done = True
            logger.info(f"[trace:{trace_id}] Plan & Execute 完成: {len(specialist_results)} 个专家")
        except Exception as e:
            logger.warning(f"[trace:{trace_id}] Plan & Execute 失败，回退 ReAct: {e}")
            specialist_results = []
            all_tool_calls = []

    if not _plan_done:
        # 原有 ReAct Loop（降级路径）

        for turn in range(MAX_TURNS):
            _check_timeout(start_time)
            try:
                response = _call_llm(
                    caller="orchestrator",
                    trace_id=trace_id,
                    model=MODEL,
                    messages=llm_messages,
                    tools=build_orchestrator_tools(specialists),
                    tool_choice="auto",
                    temperature=get_config_float('llm.temperature_agent', 0.3),
                    max_tokens=get_config_int('llm.max_tokens_orchestrator', 8192),
                )
            except Exception as e:
                err_msg = str(e)
                logger.error(f"[trace:{trace_id}] Orchestrator LLM 调用异常 (turn {turn}): {err_msg}")
                if any(kw in err_msg.lower() for kw in ["tool", "function", "reasoning", "thinking"]):
                    logger.warning(f"[trace:{trace_id}] 模型不兼容,回退到普通模式")
                    return _fallback_orchestrate(query, history, rag_context)
                # 网络抖动时，已有专家结果则拼接返回
                if specialist_results:
                    logger.warning(f"[trace:{trace_id}] LLM 汇总失败但已有 {len(specialist_results)} 个专家结果,拼接回退")
                    return _build_fallback_from_specialists(
                        query, refined_query, specialist_results, trace_id, budget)
                raise

            msg = response.choices[0].message

            # 没有工具调用 → 检查是否需要交叉审阅,然后给出最终回答
            if not msg.tool_calls:
                conflicts = detect_conflicts_smart(specialist_results, refined_query, trace_id)

                cross_review_enabled = get_orchestration_config("cross_review_enabled", "true") == "true"
                cross_review_min = int(get_orchestration_config("cross_review_min_specialists", "2"))
                cross_review_min_sev = get_orchestration_config("cross_review_min_severity", "medium")
                # 从 clarification 中获取 LLM 预判的 need_cross_review
                predicted_need_cr = False
                if isinstance(clarification, dict):
                    predicted_need_cr = clarification.get("need_cross_review", False)
                needs_cross_review = should_run_cross_review(
                    specialist_results=specialist_results,
                    complexity=complexity,
                    conflicts=conflicts,
                    force_skip=force_skip_cross_review,
                    enabled=cross_review_enabled,
                    min_specialists=cross_review_min,
                    min_severity=cross_review_min_sev,
                    predicted_need_cross_review=predicted_need_cr,
                    needs_arbitration=route_result.get("needs_arbitration", False) if isinstance(route_result, dict) else False,
                )

                if needs_cross_review:
                    logger.info(f"[trace:{trace_id}] 进入交叉审阅阶段,{len(specialist_results)} 个专家参与")
                    peer_analyses = {sr["agent_key"]: sr["analysis"] for sr in specialist_results}
                    cross_review_results = []
                    # 快照原始专家列表,避免迭代时 append 导致无限循环
                    original_specialists = list(specialist_results)
                    for sr in original_specialists:
                        _check_cancel(cancel_event)
                        _check_timeout(start_time)
                        try:
                            cr_result = run_specialist_with_context(
                                sr["agent_key"], refined_query, peer_analyses, max_turns=2,
                                prebuilt_context=prebuilt_context,
                                model=_get_model_for_agent("cross_review") if _is_cost_routing_enabled() else None,
                                conversation_id=conversation_id, message_id=message_id,
                            )
                            cross_review_results.append(cr_result)
                            specialist_results.append(cr_result)
                            all_tool_calls.extend(cr_result.get("tool_calls", []))
                        except Exception as e:
                            logger.error(f"[trace:{trace_id}] 交叉审阅 {sr['agent_key']} 失败: {e}")

                    # 将交叉审阅结果追加到消息中,让 Orchestrator 做最终综合
                    if cross_review_results:
                        cr_summary = "\n\n---\n\n".join(
                            f"【{cr['agent']}交叉审阅】\n{cr['analysis']}"
                            for cr in cross_review_results
                        )
                        llm_messages.append({
                            "role": "user",
                            "content": f"以下是各专家的交叉审阅结果,请结合 Phase A 和 Phase B 的分析,给出最终综合建议:\n\n{cr_summary}",
                        })
                        try:
                            response = _call_llm(
                                caller="orchestrator",
                                trace_id=trace_id,
                                model=MODEL,
                                messages=llm_messages,
                                temperature=get_config_float('llm.temperature_agent', 0.3),
                                max_tokens=get_config_int('llm.max_tokens_orchestrator', 8192),
                            )
                            answer = response.choices[0].message.content or ""
                        except Exception:
                            answer = msg.content or ""

                        # Phase C: 仲裁(高级模型最终裁决)
                        arbitration_done = False

                        # Light Validator: 输出前质检与修复
                        answer, validator_result = _validate_and_repair(
                            query, answer, specialist_results, prebuilt_context, llm_messages, trace_id=trace_id
                        )

                        duration_ms = int((time.time() - start_time) * 1000)
                        conflicts = detect_conflicts_smart(specialist_results, refined_query, trace_id)
                        _result = {
                            "answer": answer,
                            "specialist_results": specialist_results,
                            "tool_calls": all_tool_calls,
                            "turns": turn + 1,
                            "duration_ms": duration_ms,
                            "complexity": complexity,
                            "cross_review": True,
                            "arbitration": arbitration_done,
                            "conflicts": conflicts,
                            "validator": validator_result,
                            "route_info": route_result,
                            "cache_stats": expert_cache.stats,
                            "condition_framework": next((sr.get("condition_framework", []) for sr in specialist_results if sr.get("is_arbitration")), []),
                            "diverggence_analysis": next((sr.get("diverggence_analysis", "") for sr in specialist_results if sr.get("is_arbitration")), ""),
                            "key_variables": next((sr.get("key_variables", "") for sr in specialist_results if sr.get("is_arbitration")), ""),
                            "reasoning_trail": build_reasoning_trail(query, refined_query, complexity, specialist_results, next((sr for sr in specialist_results if sr.get("is_arbitration")), None), prebuilt_context, duration_ms),
                        }
                        if conversation_id:
                            _schedule_auto_evaluation(conversation_id, message_id, _result)
                            # P1-1：多智能体结论持久化（不阻塞主流程，失败仅 warning）
                            try:
                                _arb = next((sr for sr in specialist_results if sr.get("is_arbitration")), None)
                                _persist_agent_conclusions(
                                    conversation_id, message_id, query,
                                    specialist_results, _arb, trace_id=trace_id,
                                )
                            except Exception as _e:
                                logger.warning(f"[trace:{trace_id}] [P1-1] 结论持久化失败(早返回路径): {_e}")
                        _schedule_tool_eval(query, specialist_results)
                        return _result
                else:
                    if len(specialist_results) >= 2:
                        logger.info(f"[trace:{trace_id}] 专家结论一致或早停关闭，跳过交叉审阅")

                answer = msg.content or ""

                # Light Validator: 输出前质检与修复
                answer, validator_result = _validate_and_repair(
                    query, answer, specialist_results, prebuilt_context, llm_messages, trace_id=trace_id
                )

                duration_ms = int((time.time() - start_time) * 1000)
                conflicts = detect_conflicts_smart(specialist_results, refined_query, trace_id)
                _result = {
                    "answer": answer,
                    "specialist_results": specialist_results,
                    "tool_calls": all_tool_calls,
                    "turns": turn + 1,
                    "duration_ms": duration_ms,
                    "complexity": complexity,
                    "arbitration": arbitration_done,
                    "conflicts": conflicts,
                    "validator": validator_result,
                    "route_info": route_result,
                    "cache_stats": expert_cache.stats,
                    "condition_framework": next((sr.get("condition_framework", []) for sr in specialist_results if sr.get("is_arbitration")), []),
                    "diverggence_analysis": next((sr.get("diverggence_analysis", "") for sr in specialist_results if sr.get("is_arbitration")), ""),
                    "key_variables": next((sr.get("key_variables", "") for sr in specialist_results if sr.get("is_arbitration")), ""),
                    "reasoning_trail": build_reasoning_trail(query, refined_query, complexity, specialist_results, next((sr for sr in specialist_results if sr.get("is_arbitration")), None), prebuilt_context, duration_ms),
                }
                if conversation_id:
                    _schedule_auto_evaluation(conversation_id, message_id, _result)
                _schedule_tool_eval(query, specialist_results)
                return _result

            # 有工具调用 → 执行专家
            assistant_msg = {
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ],
            }

            # MIMO thinking mode: 传递 reasoning_content
            reasoning = None
            if hasattr(msg, "model_extra") and msg.model_extra:
                reasoning = msg.model_extra.get("reasoning_content")
            if not reasoning:
                reasoning = getattr(msg, "reasoning_content", None)
            if reasoning:
                assistant_msg["reasoning_content"] = reasoning

            llm_messages.append(assistant_msg)

            # 并行执行所有专家 Agent
            #
            max_tools = get_max_tools_per_turn(complexity)
            original_tool_count = len(msg.tool_calls or [])
            msg.tool_calls = trim_tool_calls(msg.tool_calls, specialists, max_tools)
            if original_tool_count != len(msg.tool_calls):
                logger.warning(
                    f"Orchestrator 工具调用已裁剪: {original_tool_count} -> {len(msg.tool_calls)} "
                    f"(allowed={specialists}, max={max_tools})"
                )

            # 从 clarification 中提取 specialist_tasks（LLM 规划的专家任务）
            specialist_tasks_map = {}
            if isinstance(clarification, dict):
                specialist_tasks_map = clarification.get("specialist_tasks", {})

            tool_tasks = []
            for tc in msg.tool_calls:
                args = _parse_tool_args(tc.function.arguments, tc.function.name)
                expert_query = args.get("query", query)
                # 注入 specialist_tasks：把 LLM 规划的专家任务拼接到 query 前面作为上下文
                agent_key_nonstream = build_expert_map().get(tc.function.name, "")
                if agent_key_nonstream in specialist_tasks_map and specialist_tasks_map[agent_key_nonstream]:
                    task_desc = specialist_tasks_map[agent_key_nonstream]
                    expert_query = f"[专家任务] {task_desc}\n[用户问题] {expert_query}"
                    logger.info(f"注入专家任务到 {agent_key_nonstream}: {task_desc[:80]}...")
                logger.info(f"Orchestrator → {tc.function.name}: {expert_query[:100]}")
                tool_tasks.append((tc, args, expert_query))

            # 去重：跳过已执行过的专家
            seen_keys = {sr.get("agent_key") for sr in specialist_results}
            deduped_tasks = []
            for tc, args, expert_query in tool_tasks:
                agent_name = tc.function.name
                # 查 agent_key
                expert_map = build_expert_map()
                agent_key = expert_map.get(agent_name, agent_name)
                if agent_key in seen_keys:
                    logger.info(f"跳过重复调用: {agent_name} ({agent_key})")
                    # 用已有结果填充
                    existing = next((sr for sr in specialist_results if sr.get("agent_key") == agent_key), None)
                    if existing:
                        llm_messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": json.dumps({"analysis": existing["analysis"], "agent_key": agent_key, "agent": existing.get("agent", agent_name), "icon": existing.get("icon", "🤖")}, ensure_ascii=False)[:4000],
                        })
                    continue
                deduped_tasks.append((tc, args, expert_query))
            tool_tasks = deduped_tasks
            if not tool_tasks:
                # 所有专家都已执行完，强制进入总结阶段
                llm_messages.append({
                    "role": "user",
                    "content": "所有专家已完成分析，请根据以上结果给出最终综合建议。",
                })
                # 跳过本轮工具执行，进入下一轮（LLM会看到没有工具调用，触发总结）
                continue

            if len(tool_tasks) == 1:
                # 单个专家,直接执行(避免线程池开销)
                tc, args, expert_query = tool_tasks[0]
                result_str = _execute_specialist_cached(tc.function.name, expert_query,
                                                  prebuilt_context=prebuilt_context,
                                                  trace_id=trace_id)
                ordered_results = [result_str]
            else:
                # 多个专家,并行执行
                ordered_results = [None] * len(tool_tasks)
                with concurrent.futures.ThreadPoolExecutor(max_workers=len(tool_tasks)) as executor:
                    future_to_idx = {}
                    for idx, (tc, args, expert_query) in enumerate(tool_tasks):
                        future = executor.submit(
                            _execute_specialist, tc.function.name, expert_query,
                            cancel_event=None, prebuilt_context=prebuilt_context,
                            trace_id=trace_id
                        )
                        future_to_idx[future] = idx

                    for future in concurrent.futures.as_completed(future_to_idx):
                        idx = future_to_idx[future]
                        try:
                            ordered_results[idx] = future.result()
                        except CancelledError:
                            raise
                        except Exception as e:
                            ordered_results[idx] = json.dumps({"error": str(e)}, ensure_ascii=False)

            # 按原始顺序处理结果
            for idx, (tc, args, expert_query) in enumerate(tool_tasks):
                result_str = ordered_results[idx]

                try:
                    result_data = json.loads(result_str)
                except json.JSONDecodeError:
                    result_data = {"raw": result_str}

                if "error" not in result_data:
                    specialist_results.append({
                        "agent_key": result_data.get("agent_key", build_expert_map().get(tc.function.name, "")),
                        "agent": result_data.get("agent", tc.function.name),
                        "icon": result_data.get("icon", "🤖"),
                        "analysis": result_data.get("analysis", ""),
                        "tool_calls": result_data.get("tool_calls", []),
                        "duration_ms": result_data.get("duration_ms", 0),
                    })

                all_tool_calls.append({
                    "name": tc.function.name,
                    "arguments": args,
                    "result_preview": result_str[:300],
                })

                llm_messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_str[:4000],
                })

        # 超过最大轮次,做最后一次总结
    try:
        llm_messages.append({
            "role": "user",
            "content": _build_final_synthesis_prompt(specialist_results, specialists),
        })
        response = _call_llm(
            caller="orchestrator",
            trace_id=trace_id,
            model=MODEL,
            messages=llm_messages,
            temperature=get_config_float('llm.temperature_agent', 0.3),
            max_tokens=get_config_int('llm.max_tokens_orchestrator', 8192),
        )
        final_answer = response.choices[0].message.content or ""
    except Exception:
        final_answer = "分析过程较长,请参考以上各专家的分析结果。"

    duration_ms = int((time.time() - start_time) * 1000)

    # 计算本次 token 用量(从数据库读取本次调用期间的记录)
    try:
        from db._conn import _get_conn
        conn = _get_conn()
        row = conn.execute(
            "SELECT COALESCE(SUM(total_tokens), 0) as total FROM token_usage WHERE created_at >= datetime('now', '-5 minutes')"
        ).fetchone()
        total_tokens = row["total"] if row else 0
        conn.close()
    except Exception:
        total_tokens = 0

    conflicts = detect_conflicts_smart(specialist_results, refined_query, trace_id)
    if conversation_id:
        _schedule_auto_evaluation(conversation_id, message_id, {
            "answer": final_answer, "specialist_results": specialist_results,
            "tool_calls": all_tool_calls, "duration_ms": duration_ms,
            "complexity": complexity, "conflicts": conflicts,
        })
        # P1-1：多智能体结论持久化（不阻塞主流程，失败仅 warning）
        try:
            _arb_final = next((sr for sr in specialist_results if sr.get("is_arbitration")), None)
            _persist_agent_conclusions(
                conversation_id, message_id, query,
                specialist_results, _arb_final, trace_id=trace_id,
            )
        except Exception as _e:
            logger.warning(f"[trace:{trace_id}] [P1-1] 结论持久化失败(终返回路径): {_e}")
    return {
        "answer": final_answer,
        "specialist_results": specialist_results,
        "tool_calls": all_tool_calls,
        "turns": MAX_TURNS,
        "duration_ms": duration_ms,
        "complexity": complexity,
        "token_usage": total_tokens,
        "conflicts": conflicts,
    }


# ── Bridge A: 分析结论 → AI对话注入 ──────────────────────

def _inject_analysis_context(query: str) -> tuple:
    """从 analysis_conclusions 获取24h内结论，注入到查询和专家上下文中。

    Returns: (enhanced_query, injected_context)
    - enhanced_query: 在原 query 基础上追加结论摘要提示
    - injected_context: 结构化的分析结论上下文，供 orchestrator 和 specialist 参考

    任何失败都不阻塞主流程，返回原 query 和空字符串。
    """
    try:
        from db.analysis_conclusions import get_latest_analysis_conclusions
        conclusions = get_latest_analysis_conclusions(hours=24, limit=5)
        if not conclusions:
            return query, ""

        # 格式化结论为上下文块
        lines = ["## 最近分析结论（24h内，仅供专家参考，非强制采纳）"]
        action_icons = {
            "buy": "📈", "increase": "📈",
            "sell": "📉", "decrease": "📉", "clear": "📉",
            "hold": "⏸️",
        }
        for i, c in enumerate(conclusions, 1):
            icon = action_icons.get((c.get("action") or "").lower(), "📌")
            target = c.get("target_subject", "未知标的")
            summary = c.get("summary", "")
            action = c.get("action", "")
            confidence = c.get("confidence", 0)
            source = c.get("source_type", "")
            action_str = f" [建议: {action}]" if action else ""
            conf_str = f" (置信度: {confidence:.0%})" if confidence else ""
            source_str = f" | 来源: {source}" if source else ""
            lines.append(f"{icon} **{target}**{action_str}{conf_str}{source_str}: {summary}")

        injected_context = "\n".join(lines) + "\n\n"

        # 增强 query：追加简要结论提示
        brief = "；".join(
            f"[{c.get('action', '无操作')}] {c.get('target_subject', '')}: {c.get('summary', '')[:60]}"
            for c in conclusions[:3]
        )
        enhanced_query = f"{query}\n\n[系统提示：以下是最近24h内的分析结论，请参考但不强制采纳] {brief}"

        logger.info(f"[Bridge A] 分析结论注入: {len(conclusions)} 条 → query + prebuilt_context")
        return enhanced_query, injected_context
    except Exception as e:
        logger.warning(f"[Bridge A] 分析结论注入失败（不阻塞主流程）: {e}")
        return query, ""


# ── P1-2：跨对话结论定向复用（同标的命中后注入更强参考）──


def _load_recent_conclusions(
    target_subject: str,
    hours: int = 24,
    limit: int = 3,
) -> str:
    """加载指定标的在 N 小时内的历史分析结论，格式化为 markdown 上下文。

    设计稿 P1-2：解决跨对话结论复用 — 同一基金/板块 24h 内二次提问时，
    把历史结论作为强参考注入到 specialist 的 prebuilt_context，避免重复分析。

    Args:
        target_subject: 结论标的（基金代码或板块名）
        hours: 时间窗口（小时）
        limit: 最大返回条数

    Returns:
        markdown 格式的历史结论上下文，无匹配则返回空字符串
    """
    if not target_subject or target_subject == "整体组合":
        return ""  # 整体组合太泛，不定向复用

    try:
        from db._conn import _get_conn
        conn = _get_conn()
        # LIKE 匹配 target_subject（双向：历史结论可能是 "161725" 或 "白酒"）
        rows = conn.execute(
            """SELECT id, source_type, target_subject, action, summary, reasoning,
                      confidence, urgent, created_at
               FROM analysis_conclusions
               WHERE source_system = 'ai_dialogue'
                 AND (target_subject = ? OR target_subject LIKE ? OR ? LIKE '%' || target_subject || '%')
                 AND created_at >= datetime('now', 'localtime', ?)
               ORDER BY urgent DESC, confidence DESC, created_at DESC
               LIMIT ?""",
            (target_subject, f"%{target_subject}%", target_subject, f"-{hours} hours", limit),
        ).fetchall()
        conn.close()

        if not rows:
            return ""

        action_label = {
            "buy": "买入", "increase": "加仓", "sell": "卖出",
            "decrease": "减仓", "hold": "持有", "": "-",
        }
        lines = [
            f"\n## 历史分析结论（{hours}h 内同标的，强参考）",
            f"目标标的: **{target_subject}**",
            "",
        ]
        for r in rows:
            action = action_label.get((r["action"] or "").lower(), r["action"] or "-")
            conf = r["confidence"] or 0
            urgent_tag = " ⚠️高优" if r["urgent"] else ""
            source = r["source_type"] or ""
            summary = (r["summary"] or "")[:120]
            created = (r["created_at"] or "")[:16]
            lines.append(
                f"- [{action}] (置信度 {conf:.0%}{urgent_tag}) {summary}"
            )
            lines.append(f"  - 来源: {source} | 时间: {created}")
            if r["reasoning"]:
                lines.append(f"  - 理由: {r['reasoning'][:100]}")

        lines.append(
            "\n（注：以上为对该标的的历史分析结论。如市场环境未显著变化，"
            "可在其基础上深化；如已发生变化，应明确指出变化点。）"
        )
        return "\n".join(lines) + "\n\n"
    except Exception as e:
        logger.warning(f"[P1-2] _load_recent_conclusions 失败: {e}")
        return ""


def _stream_precheck(query: str, history: list, rag_context: str, cancel_event: threading.Event | None, resume_from: dict | None, trace_id: str = ""):
    """阶段0-0.5: prompt注入检查、token预算、链接抓取、查询改写、恢复模式。

    生成器：yield 状态/错误事件。调用方需检查返回值是否为 None（表示已 yield 终止事件）。
    返回 dict 或 None:
      - None 表示已 yield 终止性 answer 事件，调用方应直接 return
      - dict 包含: query, refined_query, rewrite_meta, budget, article_context,
        completed_specialists, resumed_results, resume_message_id
    """
    # 0. Prompt 注入防护检查
    from agent.input_sanitizer import check_injection, HIGH_CONFIDENCE_REJECT
    safety = check_injection(query)
    if safety["blocked"]:
        logger.warning(f"[trace:{trace_id}] 注入检测拦截: {query[:100]} | 原因: {safety['reason']}")
        yield {
            "type": "answer",
            "content": HIGH_CONFIDENCE_REJECT,
            "specialist_results": [],
            "tool_calls": [],
            "error": "injection_blocked",
        }
        return
    if safety["confidence"] > 0:
        logger.info(f"注入低置信度告警: {query[:100]} | 模式: {safety['reason']}")

    # 0. Token 预算检查
    budget = check_token_budget()
    logger.info(f"[trace:{trace_id}] orchestrate_stream 入口 query={query[:50]}... budget={budget['mode']}")
    if budget["mode"] == "exceeded":
        yield {
            "type": "answer",
            "content": f"今日分析额度已用完({budget['used']:,}/{budget['limit']:,} tokens),请明天再来。",
            "specialist_results": [],
            "tool_calls": [],
            "error": "token_budget_exceeded",
        }
        return

    # 0.3 链接检测与文章抓取
    article_context = ""
    article_fetch_failed = False
    logger.info(f"[文章检测] query前50字: {query[:50]}, detect_urls={detect_urls(query)}")
    if detect_urls(query):
        yield {"type": "status", "message": "检测到链接,正在抓取文章内容..."}
        query, article_context = enrich_query_with_article(query)
        if article_context:
            if article_context.startswith("[抓取失败]"):
                article_fetch_failed = True
                logger.warning(f"[trace:{trace_id}] 文章抓取失败: {article_context}")
                yield {"type": "status", "message": "文章链接无法访问,将尝试分析,请稍候..."}
            else:
                logger.info(f"[trace:{trace_id}] 已注入文章内容到查询中")
                yield {"type": "status", "message": "文章内容已获取,正在分析..."}

    # 0.4 查询改写（多轮对话代词/省略补全）
    rewrite_meta = {}
    try:
        from agent.query_rewriter import rewrite_query
        query, rewrite_meta = rewrite_query(query, history)
        if rewrite_meta.get("rewritten"):
            yield {"type": "status", "message": f"已补全问题上下文（{rewrite_meta.get('method', '')}）"}
    except Exception as e:
        logger.warning(f"[trace:{trace_id}] 查询改写失败(不影响主流程): {e}")

    # 0.5 恢复模式:从 agent_runs 表查询已完成的专家
    completed_specialists = set()
    resumed_results = []
    resume_message_id = resume_from.get("message_id") if resume_from else None
    if resume_message_id:
        completed_runs = get_completed_agents_for_message(resume_message_id, run_phase='primary')
        # 如果当前消息没有 completed agent_runs，尝试查 retry_of_message_id
        if not completed_runs:
            from db.conversations import _load_metadata
            from db._conn import _get_conn
            conn = _get_conn()
            msg_row = conn.execute("SELECT metadata FROM messages WHERE id = ?", (resume_message_id,)).fetchone()
            conn.close()
            if msg_row:
                meta = json.loads(msg_row["metadata"])
                retry_of = meta.get("retry_of_message_id")
                if retry_of:
                    completed_runs = get_completed_agents_for_message(retry_of, run_phase='primary')
                    if completed_runs:
                        logger.info(f"恢复模式:从 retry_of_message_id={retry_of} 找到 {len(completed_runs)} 个已完成专家")
        for run in completed_runs:
            completed_specialists.add(run["agent_key"])
            resumed_results.append({
                "agent_key": run["agent_key"],
                "agent": run["agent_name"],
                "analysis": run["result"] or "",
                "duration_ms": run["duration_ms"] or 0,
            })
        logger.info(f"恢复模式:已完成的专家 {completed_specialists}")
        if completed_specialists:
            yield {"type": "status", "message": f"正在恢复执行({len(completed_specialists)} 个专家已完成)..."}

    return {
        "query": query,
        "refined_query": query,  # 路由阶段可能进一步改写
        "rewrite_meta": rewrite_meta,
        "budget": budget,
        "article_context": article_context,
        "completed_specialists": completed_specialists,
        "resumed_results": resumed_results,
        "resume_message_id": resume_message_id,
    }


def _stream_route(query: str, history: list, rag_context: str, cancel_event: threading.Event | None,
                  resume_from: dict | None, target_specialists: list[str] | None,
                  completed_specialists: set, resume_message_id: int | None, trace_id: str, start_time: float):
    """阶段1-1.5: 专家路由、复杂度分类、RAG增强。

    生成器：yield 状态/阶段事件。
    返回 dict 或 None:
      - None 表示内部错误（不应发生，但调用方应防御性检查）
      - dict 包含: specialists, complexity, clarification, route_result,
        refined_query, context_config, token_budget, rag_context
    """
    _check_cancel(cancel_event)
    route_result = None
    clarification = {}

    # 如果用户通过 @mention 指定了专家,跳过自动路由
    if target_specialists:
        all_agents = load_specialist_agents()
        valid_specialists = [s for s in target_specialists if s in all_agents]
        if valid_specialists:
            specialist_names = [all_agents[s]["name"] for s in valid_specialists]
            yield {"type": "status", "message": f"已指定专家:{'、'.join(specialist_names)}"}
            complexity = "simple" if len(valid_specialists) == 1 else "medium"
            route_result = {
                "complexity": complexity,
                "specialists": valid_specialists,
                "reason": f"用户通过 @mention 指定了专家: {', '.join(valid_specialists)}",
                "needs_arbitration": len(valid_specialists) >= 2,
                "route_by": "mention",
                "refined_query": query,
            }
            context_config = get_context_config(complexity)
            token_budget = get_token_budget(complexity)
            refined_query = query
            specialists = valid_specialists
            clarification = route_result
            logger.info(f"@mention 指定专家: {valid_specialists}")
        else:
            logger.warning(f"@mention 指定了无效的 agent_key: {target_specialists},回退到自动路由")
            target_specialists = None

    if not target_specialists:
        if not resume_from:
            yield {"type": "phase", "phase": "route", "message": "🔍 正在理解您的问题..."}
        history_summary = _build_history_summary(history)
        portfolio_summary = ""
        route_result = _router.route(
            query,
            history_summary=history_summary,
            portfolio_summary=portfolio_summary,
            target_specialists=None,
        )
        complexity = route_result["complexity"]
        context_config = get_context_config(complexity)
        token_budget = get_token_budget(complexity)
        logger.info(f"路由结果: {route_result}")

        refined_query = route_result.get("refined_query", query) or query
        specialists = route_result.get("specialists", [])

        max_spec = context_config.get("max_specialists", 4)
        if len(specialists) > max_spec:
            logger.info(f"specialists 超出上限({len(specialists)} > {max_spec})，截断")
            specialists = specialists[:max_spec]

        # 恢复模式：排除已完成的专家
        if resume_message_id and specialists and completed_specialists:
            specialists = [s for s in specialists if s not in completed_specialists]
            if not specialists:
                specialists = list(completed_specialists)[:3]
                logger.info(f"恢复模式:所有路由专家已完成,复用已有结果: {specialists}")
            else:
                logger.info(f"恢复模式:排除已完成专家后剩余: {specialists}")

        clarification = route_result
        if not specialists:
            if not resume_from:
                yield {"type": "phase", "phase": "clarify", "message": "🔍 正在理解您的问题..."}
            clarification = clarify_requirement(query, trace_id=trace_id)
            complexity = clarification["complexity"]
            context_config = get_context_config(complexity)
            token_budget = get_token_budget(complexity)
            logger.info(f"Smart Router 未命中,回退到需求澄清: {clarification}")
            refined_query = clarification.get("refined_query", query)
            specialists = clarification.get("specialists", [])

    # 限制专家数量
    max_spec = context_config.get("max_specialists", 3)
    if len(specialists) > max_spec:
        logger.info(f"专家数量限制: {len(specialists)} → {max_spec},丢弃: {specialists[max_spec:]}")
        specialists = specialists[:max_spec]

    # 1.5 场景化 RAG 增强
    rag_context = build_scenario_rag_context(refined_query, specialists, rag_context)

    return {
        "specialists": specialists,
        "complexity": complexity,
        "clarification": clarification,
        "route_result": route_result,
        "refined_query": refined_query,
        "context_config": context_config,
        "token_budget": token_budget,
        "rag_context": rag_context,
    }


def _stream_build_context(refined_query: str, rag_context: str, complexity: str,
                           context_config: dict, token_budget: dict, history: list):
    """阶段2: 构建上下文（token预算、RAG、用户画像、持仓上下文、估值上下文等）。

    纯函数，无 yield。返回 dict:
      - llm_messages: list[dict] — 构建好的消息列表
      - prebuilt_context: str — 供 specialist 复用的上下文
      - system_content: str — 完整的系统提示
    """
    system_content = build_orchestrator_system_prompt()

    # RAG 上下文(token 感知截断)
    rag_budget = int(token_budget["total_context"] * token_budget["rag_pct"])
    if rag_context and context_config["rag_enabled"]:
        compressed_rag = compress_rag_token_aware(rag_context, max_tokens=rag_budget)
        system_content += f"\n\n参考信息:\n{compressed_rag}"

    # 注入用户偏好画像
    preference_ctx = get_preference_context("default")
    if preference_ctx:
        system_content += f"\n\n{preference_ctx}"

    # 注入 KYC 理财画像
    from agent.kyc import kyc_profile_to_text
    kyc_text = kyc_profile_to_text("default")
    if kyc_text:
        system_content += f"\n\n{kyc_text}"

    # 注入跨对话用户记忆
    user_memory = build_user_memory_context("default")
    if user_memory:
        system_content += f"\n\n<user_memory>\n{user_memory}\n</user_memory>"

    # 注入持仓上下文 + 估值上下文(同时构建 prebuilt_context 供 specialist 复用)
    prebuilt_context = ""

    # P0 全景扫描：注入持仓概况+交易记录+风险信号
    try:
        panorama_ctx = _run_portfolio_panorama_scan()
        if panorama_ctx:
            prebuilt_context += panorama_ctx + "\n\n"
    except Exception as e:
        logger.warning(f"全景扫描注入失败（不阻塞主流程）: {e}")

    # Bridge A: 注入24h分析结论上下文
    _, analysis_ctx = _inject_analysis_context(refined_query)
    if analysis_ctx:
        prebuilt_context += analysis_ctx
        try:
            system_content += f"\n\n{analysis_ctx}"
        except Exception:
            pass

    # P1-2：跨对话结论定向复用（同标的命中时注入更强参考，默认关闭）
    try:
        from db.config import get_config_bool, get_config_int
        if get_config_bool("agent.reuse_recent_conclusions", False):
            _target = _extract_target_subject(refined_query or query, "")
            _hours = get_config_int("agent.reuse_conclusions_hours", 24)
            _reuse_ctx = _load_recent_conclusions(_target, hours=_hours, limit=3)
            if _reuse_ctx:
                prebuilt_context += _reuse_ctx
                logger.info(f"[P1-2] 注入同标的历史结论: target={_target}")
    except Exception as _e:
        logger.warning(f"[P1-2] 跨对话复用注入失败（不阻塞主流程）: {_e}")

    # 注入 RAG 知识库上下文到 prebuilt_context
    if rag_context:
        compressed_rag_for_specialist = compress_rag_token_aware(rag_context, max_tokens=get_config_int('llm.max_tokens_rag_compress', 1500))
        prebuilt_context += f"## 知识库参考(书籍/文章/技能)\n{compressed_rag_for_specialist}\n\n"

    try:
        from services.portfolio_context import build_portfolio_context, build_valuation_summary
        portfolio_ctx = build_portfolio_context()
        system_content += f"\n\n## 用户当前持仓\n{portfolio_ctx}"
        prebuilt_context += f"## 用户当前持仓\n{portfolio_ctx}\n\n"
        valuation_ctx = build_valuation_summary()
        if valuation_ctx:
            system_content += f"\n\n## 当前市场估值\n{valuation_ctx}"
            prebuilt_context += f"## 当前市场估值数据\n{valuation_ctx}\n\n"
    except Exception as e:
        logger.warning(f"注入持仓/估值上下文失败: {e}")

    # R2: 注入 DCA 定投规则（从 system_config 读取）
    try:
        from db.config import get_config_int
        base_dca = get_config_int("daily_advice.base_dca_amount", 500)
        step_pct = get_config_int("daily_advice.dca_drop_step_pct", 4)
        max_steps = get_config_int("daily_advice.max_dca_steps", 3)
        add_max_pct = get_config_int("daily_advice.add_valuation_max_percentile", 35)
        reduce_min_pct = get_config_int("daily_advice.reduce_valuation_min_percentile", 80)
        cooldown_days = get_config_int("daily_advice.recent_buy_cooldown_days", 10)
        cooldown_max = get_config_int("daily_advice.recent_buy_max_count", 2)
        max_cash_pct = get_config_int("daily_advice.max_cash_use_pct_per_signal", 10)
        single_pct = get_config_int("daily_advice.default_single_position_pct", 15)

        prebuilt_context += (
            "## 定投与加减仓规则（必须遵守）\n\n"
            "### 4% 定投法\n"
            f"- 基础定投金额: ¥{base_dca}\n"
            f"- 每下跌 {step_pct}% 加一档，每档加 ¥{base_dca}\n"
            f"- 最多加 {max_steps} 档（即最大单次加仓 ¥{base_dca * max_steps}）\n\n"
            "### 加仓条件\n"
            f"- 估值百分位必须 ≤ {add_max_pct}% 才建议加仓\n"
            f"- 加仓前检查冷静期: {cooldown_days} 天内最多买入 {cooldown_max} 次\n"
            f"- 单标的占比达 {single_pct}% 禁止加仓\n"
            f"- 单次建议使用现金上限: 总现金的 {max_cash_pct}%\n\n"
            "### 减仓条件\n"
            f"- 估值百分位 ≥ {reduce_min_pct}% 才建议减仓\n"
            "- 单次减仓幅度不超过该基金持仓的 20%\n"
            "- 单次建议总减仓金额不超过总资产的 10%\n\n"
            "### 禁止行为\n"
            "- 一次性减仓超过 ¥50,000\n"
            "- 单条建议同时减仓 2 个以上基金\n"
            "- 加仓金额超出 4% 定投法计算结果\n"
            "- 对已超仓位上限的基金建议加仓\n\n"
        )
    except Exception as e:
        logger.warning(f"注入 DCA 规则失败: {e}")

    # 注入债市数据到 prebuilt_context
    try:
        from routers.bond import _fetch_bond_data
        bond_raw = _fetch_bond_data()
        if bond_raw and len(bond_raw) > 1:
            last = bond_raw[-1]
            temp = last.get("degree", "?")
            rate = float(last["yield"]) * 100 if last.get("yield") else "?"
            prebuilt_context += f"## 债市数据\n- 债市温度: {temp}°\n- 10年期国债收益率: {rate}%\n\n"
    except Exception as e:
        logger.warning(f"注入债市数据失败: {e}")

    # 注入近期热点新闻到 prebuilt_context
    try:
        from routers.dashboard import _hot_topics_cache
        hot_cache = _hot_topics_cache.get("data")
        if hot_cache:
            news_list = hot_cache.get("news", [])[:3]
            if news_list:
                news_lines = "\n".join(f"- {n.get('title', '')}" for n in news_list if n.get("title"))
                prebuilt_context += f"## 今日市场热点\n{news_lines}\n\n"
    except Exception as e:
        logger.warning(f"注入热点新闻失败: {e}")

    llm_messages = [{"role": "system", "content": system_content}]

    # 语义化压缩历史消息
    history_budget = int(token_budget["total_context"] * token_budget["history_pct"])
    compressed_history = compress_history_semantic(history, max_tokens=history_budget)
    for msg in compressed_history:
        llm_messages.append({"role": msg["role"], "content": msg["content"]})

    llm_messages.append({"role": "user", "content": refined_query})

    # 注入持仓上下文（基金名、占比、盈亏率、成本），避免专家不看盈亏就建议加仓
    portfolio_ctx = _build_portfolio_context()
    if portfolio_ctx:
        prebuilt_context = (prebuilt_context or "") + "\n\n" + portfolio_ctx

    # 注入 4% 定投法规则和减仓约束，避免专家给出随意的加减仓金额
    dca_rules = _build_dca_rules()
    if dca_rules:
        prebuilt_context = (prebuilt_context or "") + "\n\n" + dca_rules

    return {
        "llm_messages": llm_messages,
        "prebuilt_context": prebuilt_context,
        "system_content": system_content,
    }


def _human_in_loop_checks(specialist_results: list, answer: str, conversation_id: int):
    """人在回路检查：冲突检测 + 买卖建议确认。

    生成器：yield 确认事件/状态事件。
    返回修改后的 answer（可能添加用户倾向前缀）。
    """
    conflict_confirm = _check_human_in_loop_conflict(specialist_results)
    if conflict_confirm:
        confirm_id = f"confirm_{conversation_id}_{int(time.time())}"
        conflict_confirm["confirm_id"] = confirm_id
        _confirm_store[confirm_id] = {"event": threading.Event(), "result": ""}
        yield conflict_confirm
        user_choice = wait_for_confirm(confirm_id, timeout=conflict_confirm.get("timeout", 30))
        del _confirm_store[confirm_id]
        if user_choice == "lean_buy":
            answer = "用户倾向买入，综合分析如下：\n" + answer
        elif user_choice == "lean_skip":
            answer = "用户倾向不买，综合分析如下：\n" + answer

    trade_confirm = _check_human_in_loop_trade(answer)
    if trade_confirm:
        confirm_id = f"confirm_{conversation_id}_{int(time.time())}"
        trade_confirm["confirm_id"] = confirm_id
        _confirm_store[confirm_id] = {"event": threading.Event(), "result": ""}
        yield trade_confirm
        user_choice = wait_for_confirm(confirm_id, timeout=trade_confirm.get("timeout", 30))
        del _confirm_store[confirm_id]
        if user_choice == "save":
            yield {"type": "status", "message": "已保存为决策草案"}

    return answer


def _specialists_signature(specialist_results: list) -> tuple:
    """计算原始（非 cross_review/非仲裁）专家列表的签名，用于冲突检测缓存判断。

    detect_conflicts_llm 内部会过滤 is_cross_review 结果，所以只要原始专家集合不变，
    冲突检测的输入就完全相同，输出必然相同 → 可复用缓存。
    """
    original = [sr for sr in specialist_results
                if not sr.get("is_cross_review") and not sr.get("is_arbitration")]
    return tuple(sorted(sr.get("agent_key", "") for sr in original))


def _detect_conflicts_cached(specialist_results: list, query: str, trace_id: str,
                              cache_state: dict) -> dict:
    """带缓存的冲突检测：若原始专家签名未变则复用上次结果，避免重复 LLM 调用。

    cache_state: {"signature": tuple, "result": dict} 在调用方维护
    """
    try:
        from db.config import get_config_bool
        cache_enabled = get_config_bool("agent.conflict_detect_cache", True)
    except Exception:
        cache_enabled = True

    sig = _specialists_signature(specialist_results)
    if cache_enabled and cache_state.get("signature") == sig and cache_state.get("result"):
        logger.info(f"[trace:{trace_id}] conflict_detect 缓存命中（签名={sig}），复用上次结果")
        return cache_state["result"]

    result = detect_conflicts_smart(specialist_results, query, trace_id)
    cache_state["signature"] = sig
    cache_state["result"] = result
    return result


def _stream_handle_no_tool_calls(msg, specialist_results: list, all_tool_calls: list,
                                  refined_query: str, query: str, rag_context: str,
                                  complexity: str, clarification, route_result,
                                  conflicts, force_skip_cross_review: bool,
                                  arbitration_done: bool, prebuilt_context: str,
                                  cancel_event, start_time: float,
                                  conversation_id: int, message_id: int, trace_id: str):
    """处理 LLM 无工具调用时的交叉审阅 + 仲裁 + 最终回答。

    生成器：yield 各类事件。
    返回 True 表示已产出最终 answer 事件（调用方应 return），False 表示继续。
    """
    # 冲突检测缓存：cross_review 前后两次调用的原始专家列表相同，可复用结果
    _conflicts_cache: dict = {}
    conflicts = _detect_conflicts_cached(specialist_results, refined_query, trace_id, _conflicts_cache)
    cross_review_enabled = get_orchestration_config("cross_review_enabled", "true") == "true"
    cross_review_min = int(get_orchestration_config("cross_review_min_specialists", "2"))
    cross_review_min_sev = get_orchestration_config("cross_review_min_severity", "medium")
    predicted_need_cr = False
    if isinstance(clarification, dict):
        predicted_need_cr = clarification.get("need_cross_review", False)
    should_cross_review = should_run_cross_review(
        specialist_results=specialist_results,
        complexity=complexity,
        conflicts=conflicts,
        force_skip=force_skip_cross_review,
        enabled=cross_review_enabled,
        min_specialists=cross_review_min,
        min_severity=cross_review_min_sev,
        predicted_need_cross_review=predicted_need_cr,
        needs_arbitration=route_result.get("needs_arbitration", False) if isinstance(route_result, dict) else False,
    )

    if should_cross_review:
        yield {"type": "status", "message": f"正在进行交叉审阅({len(specialist_results)} 个专家并行互相验证)..."}
        peer_analyses = {sr["agent_key"]: sr["analysis"] for sr in specialist_results}
        cross_review_results = []
        original_specialists = list(specialist_results)

        for sr in original_specialists:
            yield {
                "type": "cross_review_start",
                "agent_key": sr["agent_key"],
                "agent": sr["agent"],
                "icon": sr["icon"],
            }

        def _review_single(sr):
            _check_cancel(cancel_event)
            _check_timeout(start_time)
            peer = {k: v for k, v in peer_analyses.items() if k != sr["agent_key"]}
            # P2-2 优化：cross_review 单轮意见模式（无工具调用，省 2-3 次 LLM per 专家）
            try:
                from db.config import get_config_bool
                opinion_mode = get_config_bool("agent.cross_review_opinion_mode", True)
            except Exception:
                opinion_mode = True

            if opinion_mode:
                return sr, run_cross_review_opinion(
                    sr["agent_key"], refined_query, sr.get("analysis", ""), peer,
                    trace_id=trace_id,
                    model=_get_model_for_agent("cross_review") if _is_cost_routing_enabled() else None,
                    conversation_id=conversation_id, message_id=message_id,
                )
            # 回退：旧 ReAct 模式
            return sr, run_specialist_with_context(
                sr["agent_key"], refined_query, peer, max_turns=2,
                prebuilt_context=prebuilt_context,
                model=_get_model_for_agent("cross_review") if _is_cost_routing_enabled() else None,
                conversation_id=conversation_id, message_id=message_id,
            )

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(len(original_specialists), 3)
        ) as executor:
            futures = {executor.submit(_review_single, sr): sr for sr in original_specialists}
            for future in concurrent.futures.as_completed(futures):
                sr = futures[future]
                try:
                    _, cr_result = future.result(timeout=300)
                    cross_review_results.append(cr_result)
                    specialist_results.append(cr_result)
                    all_tool_calls.extend(cr_result.get("tool_calls", []))
                    yield {
                        "type": "cross_review_done",
                        "agent_key": sr["agent_key"],
                        "agent": sr["agent"],
                        "icon": sr["icon"],
                        "analysis": cr_result["analysis"],
                        "duration_ms": cr_result["duration_ms"],
                    }
                except Exception as e:
                    logger.error(f"交叉审阅 {sr['agent_key']} 失败: {e}")
                    yield {
                        "type": "cross_review_done",
                        "agent_key": sr["agent_key"],
                        "agent": sr["agent"],
                        "icon": sr["icon"],
                        "analysis": f"交叉审阅失败: {e}",
                        "duration_ms": 0,
                    }

        if cross_review_results:
            _check_cancel(cancel_event)
            answer = msg.content or ""

            duration_ms = int((time.time() - start_time) * 1000)
            # 复用缓存：cross_review 只追加了 is_cross_review 结果，原始专家列表未变
            conflicts = _detect_conflicts_cached(specialist_results, refined_query, trace_id, _conflicts_cache)

            if conversation_id and message_id:
                _save_checkpoint(conversation_id, message_id, "cross_review", {
                    "specialist_results": specialist_results,
                    "all_tool_calls": all_tool_calls,
                    "arbitration_done": arbitration_done,
                    "complexity": complexity,
                })

            hil_gen = _human_in_loop_checks(specialist_results, answer, conversation_id)
            while True:
                try:
                    yield next(hil_gen)
                except StopIteration as si:
                    answer = si.value
                    break

            yield {
                "type": "answer",
                "content": answer,
                "specialist_results": specialist_results,
                "tool_calls": all_tool_calls,
                "duration_ms": duration_ms,
                "complexity": complexity,
                "cross_review": True,
                "arbitration": arbitration_done,
                "conflicts": conflicts,
                "condition_framework": next((sr.get("condition_framework", []) for sr in specialist_results if sr.get("is_arbitration")), []),
                "diverggence_analysis": next((sr.get("diverggence_analysis", "") for sr in specialist_results if sr.get("is_arbitration")), ""),
                "key_variables": next((sr.get("key_variables", "") for sr in specialist_results if sr.get("is_arbitration")), ""),
                "reasoning_trail": build_reasoning_trail(query, refined_query, complexity, specialist_results, next((sr for sr in specialist_results if sr.get("is_arbitration")), None), prebuilt_context, duration_ms),
            }
            if conversation_id:
                _schedule_auto_evaluation(conversation_id, message_id, {
                    "answer": answer, "specialist_results": specialist_results,
                    "tool_calls": all_tool_calls, "duration_ms": duration_ms,
                    "complexity": complexity, "conflicts": conflicts,
                    "route_info": route_result, "cache_stats": expert_cache.stats,
                })
            return True

    # 无交叉审阅路径
    answer = msg.content or ""

    duration_ms = int((time.time() - start_time) * 1000)
    # 复用缓存：若仲裁追加了 is_arbitration 结果，签名也只看原始专家，仍可命中
    conflicts = _detect_conflicts_cached(specialist_results, refined_query, trace_id, _conflicts_cache)

    if conversation_id and message_id and specialist_results:
        _save_checkpoint(conversation_id, message_id, "experts", {
            "specialist_results": specialist_results,
            "all_tool_calls": all_tool_calls,
            "arbitration_done": arbitration_done,
            "complexity": complexity,
        })

    hil_gen = _human_in_loop_checks(specialist_results, answer, conversation_id)
    while True:
        try:
            yield next(hil_gen)
        except StopIteration as si:
            answer = si.value
            break

    yield {
        "type": "answer",
        "content": answer,
        "specialist_results": specialist_results,
        "tool_calls": all_tool_calls,
        "duration_ms": duration_ms,
        "arbitration": arbitration_done,
        "conflicts": conflicts,
        "condition_framework": next((sr.get("condition_framework", []) for sr in specialist_results if sr.get("is_arbitration")), []),
        "diverggence_analysis": next((sr.get("diverggence_analysis", "") for sr in specialist_results if sr.get("is_arbitration")), ""),
        "key_variables": next((sr.get("key_variables", "") for sr in specialist_results if sr.get("is_arbitration")), ""),
        "reasoning_trail": build_reasoning_trail(query, refined_query, complexity, specialist_results, next((sr for sr in specialist_results if sr.get("is_arbitration")), None), prebuilt_context, duration_ms),
    }
    if conversation_id:
        _schedule_auto_evaluation(conversation_id, message_id, {
            "answer": answer, "specialist_results": specialist_results,
            "tool_calls": all_tool_calls, "duration_ms": duration_ms,
            "complexity": complexity, "conflicts": conflicts,
            "route_info": route_result, "cache_stats": expert_cache.stats,
        })
    return True


def _stream_final_synthesis(query: str, refined_query: str, specialists: list,
                             specialist_results: list, all_tool_calls: list,
                             llm_messages: list, prebuilt_context: str,
                             complexity: str, arbitration_done: bool,
                             route_result, conflicts, perf_metrics: dict,
                             cancel_event, start_time: float,
                             conversation_id: int, message_id: int, trace_id: str):
    """阶段4-5: 最终综合（流式生成）、Validator、后处理、人在回路、答案输出。

    生成器：yield reasoning_chunk/answer_chunk/status/answer 事件。
    """
    _check_cancel(cancel_event)
    try:
        # 注入 KYC 画像
        try:
            from agent.kyc import kyc_profile_to_text
            kyc_text = kyc_profile_to_text("default")
            if kyc_text:
                llm_messages.append({"role": "system", "content": f"在综合各专家意见给出最终建议时,请务必结合用户的投资画像:\n{kyc_text}"})
        except Exception:
            pass
        llm_messages.append({
            "role": "user",
            "content": _build_final_synthesis_prompt(specialist_results, specialists),
        })
        final_answer = ""
        try:
            from services.llm_service import _call_llm_stream
            for chunk in _call_llm_stream(
                caller="orchestrator",
                trace_id=trace_id,
                model=MODEL,
                messages=llm_messages,
                temperature=get_config_float('llm.temperature_agent', 0.3),
                max_tokens=get_config_int('llm.max_tokens_orchestrator', 8192),
            ):
                if chunk.get("reasoning"):
                    yield {"type": "reasoning_chunk", "content": chunk["reasoning"], "agent": "orchestrator"}
                if chunk.get("content"):
                    final_answer += chunk["content"]
                    yield {"type": "answer_chunk", "content": chunk["content"]}
        except CancelledError:
            raise
        except Exception as stream_err:
            logger.warning(f"[trace:{trace_id}] 流式生成失败,回退非流式: {stream_err}")
            if not final_answer:
                response = _call_llm(
                    caller="orchestrator",
                    trace_id=trace_id,
                    model=MODEL,
                    messages=llm_messages,
                    temperature=get_config_float('llm.temperature_agent', 0.3),
                    max_tokens=get_config_int('llm.max_tokens_orchestrator', 8192),
                )
                final_answer = response.choices[0].message.content or ""
        if not final_answer:
            final_answer = "分析过程较长,请参考以上各专家的分析结果。"
    except CancelledError:
        raise
    except Exception:
        final_answer = "分析过程较长,请参考以上各专家的分析结果。"

    # Light Validator
    try:
        final_answer, validator_result = _validate_and_repair(
            query, final_answer, specialist_results, prebuilt_context, llm_messages, trace_id=trace_id
        )
        if validator_result.get("issues") and not validator_result.get("passed"):
            logger.info(f"流式输出前 Validator 发现 {len(validator_result['issues'])} 个问题，已尝试修复")
    except Exception as e:
        logger.warning(f"流式 Validator 调用失败: {e}")
        validator_result = {"enabled": True, "passed": True, "issues": []}

    duration_ms = int((time.time() - start_time) * 1000)
    perf_metrics["phases"]["total"] = duration_ms
    perf_metrics["complexity"] = complexity
    perf_metrics["specialist_count"] = len([s for s in specialist_results if not s.get("is_cross_review")])

    try:
        if conversation_id > 0 and message_id > 0:
            from agent.orchestrator_optimizer import log_performance_metrics
            log_performance_metrics(conversation_id, message_id, perf_metrics)
    except Exception as e:
        logger.warning(f"记录性能指标失败: {e}")

    # 增强4: 实体记忆
    try:
        for sr in specialist_results:
            analysis = sr.get("analysis", "")
            if analysis and not sr.get("is_cross_review"):
                record_entity_snapshots(analysis, source="analysis", source_id=conversation_id or 0)
        if final_answer:
            record_entity_snapshots(final_answer, source="analysis", source_id=conversation_id or 0)
    except Exception as e:
        logger.warning(f"记录实体记忆失败: {e}")

    conflicts = detect_conflicts_smart(specialist_results, refined_query, trace_id)

    if conversation_id and message_id and specialist_results:
        _save_checkpoint(conversation_id, message_id, "final", {
            "specialist_results": specialist_results,
            "all_tool_calls": all_tool_calls,
            "arbitration_done": arbitration_done,
            "complexity": complexity,
        })

    # 人在回路
    hil_gen = _human_in_loop_checks(specialist_results, final_answer, conversation_id)
    while True:
        try:
            yield next(hil_gen)
        except StopIteration as si:
            final_answer = si.value
            break

    if conversation_id:
        _schedule_auto_evaluation(conversation_id, message_id, {
            "answer": final_answer,
            "specialist_results": specialist_results,
            "tool_calls": all_tool_calls,
            "duration_ms": duration_ms,
            "complexity": complexity,
            "perf_metrics": perf_metrics,
            "conflicts": conflicts,
            "route_info": route_result,
            "cache_stats": expert_cache.stats,
            "validator": validator_result if 'validator_result' in locals() else {"enabled": False},
        })
        # P1-1：多智能体结论持久化（不阻塞主流程，失败仅 warning）
        try:
            _arb_stream = next((sr for sr in specialist_results if sr.get("is_arbitration")), None)
            _persist_agent_conclusions(
                conversation_id, message_id, query,
                specialist_results, _arb_stream, trace_id=trace_id,
            )
        except Exception as _e:
            logger.warning(f"[trace:{trace_id}] [P1-1] 流式结论持久化失败: {_e}")

    yield {
        "type": "answer",
        "content": final_answer,
        "specialist_results": specialist_results,
        "tool_calls": all_tool_calls,
        "duration_ms": duration_ms,
        "complexity": complexity,
        "perf_metrics": perf_metrics,
        "conflicts": conflicts,
    }


def _pipeline_phase_message(phase: str) -> str:
    """将 Pipeline 阶段名转换为前端可读消息。"""
    messages = {
        "preprocess": "理解问题中...",
        "info_gather": "收集信息中...",
        "planning": "制定分析计划...",
        "execution": "专家分析中...",
        "synthesis": "综合结论中...",
        "memory": "保存记忆...",
        "completed": "已完成",
        "failed": "执行失败",
        "cancelled": "已取消",
    }
    return messages.get(phase, f"阶段: {phase}")


def orchestrate_stream(query: str, history: list, rag_context: str = "", cancel_event: threading.Event | None = None, resume_from: dict | None = None, conversation_id: int = 0, message_id: int = 0, trace_id: str = "", target_specialists: list[str] = None):
    """
    Orchestrator 的流式版本,通过生成器逐步返回事件。

    事件类型:
    - {"type": "specialist_start", "agent_key": "...", "agent": "...", "icon": "..."}
    - {"type": "specialist_done", "agent_key": "...", "agent": "...", "icon": "...", "analysis": "...", "duration_ms": ...}
    - {"type": "status", "message": "..."}
    - {"type": "answer_chunk", "content": "..."}
    - {"type": "answer", "content": "...", "specialist_results": [...], "tool_calls": [...], "complexity": "..."}

    参数:
        cancel_event: 可选的取消事件,设置后会尽快终止执行
        resume_from: 恢复数据,包含 message_id 用于查询已完成的 agent
        conversation_id: 对话 ID,用于创建 agent_runs 记录
        message_id: 消息 ID,用于创建 agent_runs 记录
        trace_id: 追踪 ID,用于关联执行记录
    """
    start_time = time.time()

    # 性能监控
    perf_metrics = {
        "start_time": start_time,
        "phases": {},
    }

    # ── Pipeline 主路径（config 控制，默认关闭）──
    # 启用后走 6 阶段确定性流水线，失败自动降级到下方 ReAct 逻辑
    try:
        from agent.pipeline import is_pipeline_enabled, should_use_pipeline, run_pipeline
        if is_pipeline_enabled() and should_use_pipeline(query, history):
            pipeline_completed = False
            pipeline_degrade = False
            try:
                for evt in run_pipeline(
                    query=query,
                    history=history,
                    conversation_id=conversation_id,
                    message_id=message_id,
                    trace_id=trace_id,
                    cancel_event=cancel_event,
                ):
                    evt_type = evt.get("type", "")
                    # 事件映射：pipeline → orchestrator 兼容格式
                    if evt_type == "phase_start":
                        phase_name = evt.get("phase", "")
                        yield {"type": "phase", "phase": phase_name,
                               "message": _pipeline_phase_message(phase_name)}
                    elif evt_type == "phase_end":
                        pass  # 阶段结束事件不转发，减少前端噪音
                    elif evt_type == "simple_chat":
                        yield {"type": "status", "message": "快速回复中..."}
                    elif evt_type == "clarification":
                        yield {"type": "status", "message": f"需要澄清: {evt.get('question', '')}"}
                    elif evt_type == "plan_generated":
                        yield {"type": "plan", "complexity": evt.get("plan", {}).get("complexity", "medium"),
                               "scenario_type": "", "reason": "Pipeline 计划",
                               "refined_query": evt.get("plan", {}).get("refined_query", "")}
                    elif evt_type == "specialist_start":
                        yield evt  # 直接转发
                    elif evt_type == "specialist_done":
                        yield evt  # 直接转发
                    elif evt_type == "answer":
                        pipeline_completed = True
                        yield evt  # 直接转发最终回答
                    elif evt_type == "terminated":
                        pipeline_completed = True
                        yield {"type": "status", "message": f"已终止: {evt.get('reason', '')}"}
                        return
                    elif evt_type == "error":
                        logger.warning(f"[orchestrator] Pipeline 失败，降级到 ReAct: {evt.get('error', '')}")
                        pipeline_degrade = True
                        yield {"type": "status", "message": "Pipeline 异常，切换到标准模式..."}
                        break
                    elif evt_type == "degrade":
                        logger.info("[orchestrator] Pipeline 主动降级到 ReAct")
                        pipeline_degrade = True
                        yield {"type": "status", "message": "切换到标准模式..."}
                        break
                    else:
                        yield evt  # 未知事件直接转发
            except Exception as e:
                logger.exception(f"[orchestrator] Pipeline 异常，降级到 ReAct: {e}")
                pipeline_degrade = True
                yield {"type": "status", "message": "Pipeline 异常，切换到标准模式..."}

            if pipeline_completed:
                return  # Pipeline 成功完成，不再走 ReAct
            # pipeline_degrade=True 或 Pipeline 未完成 → 继续走下方 ReAct 逻辑
            logger.info("[orchestrator] Pipeline 降级，继续 ReAct 流程")
    except ImportError:
        pass  # pipeline 模块不可用，走 ReAct

    # ── 阶段0-0.5: 预处理（注入检查、token预算、链接抓取、查询改写、恢复模式）──
    precheck_gen = _stream_precheck(query, history, rag_context, cancel_event, resume_from, trace_id=trace_id)
    precheck_result = None
    while True:
        try:
            evt = next(precheck_gen)
            if evt.get("type") == "answer":
                yield evt  # 终止性事件（注入拦截 / token超限）
                return
            yield evt
        except StopIteration as si:
            precheck_result = si.value
            break

    if precheck_result is None:
        return

    query = precheck_result["query"]
    refined_query = precheck_result["refined_query"]
    budget = precheck_result["budget"]
    completed_specialists = precheck_result["completed_specialists"]
    resumed_results = precheck_result["resumed_results"]
    resume_message_id = precheck_result["resume_message_id"]

    # ── 阶段1-1.5: 专家路由、复杂度分类、RAG增强 ──
    route_gen = _stream_route(query, history, rag_context, cancel_event, resume_from,
                             target_specialists, completed_specialists, resume_message_id,
                             trace_id, start_time)
    route_result_data = None
    while True:
        try:
            evt = next(route_gen)
            yield evt
        except StopIteration as si:
            route_result_data = si.value
            break

    if route_result_data is None:
        return

    specialists = route_result_data["specialists"]
    complexity = route_result_data["complexity"]
    clarification = route_result_data["clarification"]
    route_result = route_result_data["route_result"]
    refined_query = route_result_data["refined_query"]
    context_config = route_result_data["context_config"]
    token_budget = route_result_data["token_budget"]
    rag_context = route_result_data["rag_context"]

    perf_metrics["phases"]["routing"] = int((time.time() - start_time) * 1000)

    # ── 阶段2: 构建上下文 ──
    ctx_data = _stream_build_context(refined_query, rag_context, complexity,
                                     context_config, token_budget, history)
    llm_messages = ctx_data["llm_messages"]
    prebuilt_context = ctx_data["prebuilt_context"]
    system_content = ctx_data["system_content"]

    # 根据复杂度显示不同的状态消息
    if complexity == "simple":
        yield {"type": "phase", "phase": "analyze", "message": f"📊 正在分析问题... ({clarification.get('reason', '')})"}
    elif complexity == "medium":
        yield {"type": "phase", "phase": "consult", "message": f"🤝 正在咨询专家... ({clarification.get('reason', '')})"}
    else:
        yield {"type": "phase", "phase": "coordinate", "message": f"⚡ 正在协调多个专家... ({clarification.get('reason', '')})"}

    # 发送执行计划给前端
    yield {
        "type": "plan",
        "complexity": complexity,
        "scenario_type": clarification.get("scenario_type", detect_scenario_type(query)),
        "reason": clarification.get("reason", ""),
        "refined_query": refined_query if refined_query != query else "",
    }

    # 增强1: 检查点恢复 — 尝试从 checkpoint 恢复（跳过已完成的阶段）
    checkpoint_state = None
    if conversation_id and message_id:
        checkpoint_state = _load_checkpoint(conversation_id, message_id)
        if checkpoint_state:
            cp_phase = checkpoint_state.get("phase", "")
            logger.info(f"检查点恢复: phase={cp_phase}")
            yield {"type": "status", "message": f"检测到检查点 ({cp_phase}),正在恢复..."}
            # 如果检查点在 cross_review 或 arbitrate 阶段，跳过专家执行
            if cp_phase in ("cross_review", "arbitrate"):
                specialist_results = checkpoint_state.get("specialist_results", [])
                all_tool_calls = checkpoint_state.get("all_tool_calls", [])
                arbitration_done = checkpoint_state.get("arbitration_done", False)
                completed_specialists.update(sr.get("agent_key", "") for sr in specialist_results)

    # 增强5: 人在回路 — 路由确认（complex + 低置信度时暂停）
    routing_confirm = _check_human_in_loop_routing(complexity, clarification.get("confidence", 1.0), len(specialists))
    if routing_confirm:
        confirm_id = f"confirm_{conversation_id}_{int(time.time())}"
        routing_confirm["confirm_id"] = confirm_id
        _confirm_store[confirm_id] = {"event": threading.Event(), "result": ""}
        yield routing_confirm
        user_choice = wait_for_confirm(confirm_id, timeout=routing_confirm.get("timeout", 30))
        del _confirm_store[confirm_id]
        if user_choice == "simplify":
            complexity = "simple"
            specialists = specialists[:1] if specialists else ["valuation_expert"]
            clarification["complexity"] = "simple"
            yield {"type": "status", "message": "已简化为快速分析"}

    MAX_TURNS = 3 if budget["mode"] == "conservative" else 6
    force_skip_cross_review = budget["mode"] == "conservative"
    # P0-2.1：共享黑板架构 — 2-3 专家串行执行，后执行者能看到前序结论（默认开启）
    shared_blackboard_enabled = get_orchestration_config("shared_blackboard_enabled", "true") == "true"
    specialist_results = []
    all_tool_calls = []
    arbitration_done = False  # 标记仲裁是否已完成,避免重复调用
    conflicts = {}  # 初始化冲突检测结果，后续在无工具调用分支中更新
    already_called = set()  # 增强2: 动态选角防循环

    # 恢复模式:添加已有结果
    if resumed_results:
        specialist_results.extend(resumed_results)
        already_called.update(sr.get("agent_key", "") for sr in resumed_results)
        logger.info(f"恢复模式:已加载 {len(resumed_results)} 个专家结果")

    # 从检查点恢复的专家结果也要加入 already_called
    if checkpoint_state and checkpoint_state.get("specialist_results"):
        for sr in checkpoint_state["specialist_results"]:
            if sr.get("agent_key") and sr["agent_key"] not in already_called:
                specialist_results.append(sr)
                already_called.add(sr["agent_key"])

    for turn in range(MAX_TURNS):
        _check_cancel(cancel_event)
        _check_timeout(start_time)
        try:
            response = _call_llm(
                caller="orchestrator",
                trace_id=trace_id,
                model=MODEL,
                messages=llm_messages,
                tools=build_orchestrator_tools(specialists),
                tool_choice="auto",
                temperature=get_config_float('llm.temperature_agent', 0.3),
                max_tokens=get_config_int('llm.max_tokens_orchestrator', 8192),
            )
        except CancelledError:
            raise
        except Exception as e:
            err_msg = str(e)
            logger.error(f"Orchestrator LLM 调用异常 (turn {turn}): {err_msg}")
            if any(kw in err_msg.lower() for kw in ["tool", "function", "reasoning", "thinking"]):
                logger.warning("模型不兼容,回退到普通模式")
                yield {"type": "status", "message": "模型不支持工具调用,切换到普通模式..."}
                result = _fallback_orchestrate(query, history, rag_context)
                if conversation_id:
                    _schedule_auto_evaluation(conversation_id, message_id, {
                        "answer": result["answer"], "specialist_results": [],
                        "tool_calls": [], "duration_ms": duration_ms,
                        "complexity": complexity, "conflicts": {},
                    })
                yield {
                    "type": "answer",
                    "content": result["answer"],
                    "specialist_results": [],
                    "tool_calls": [],
                }
                return
            # 网络抖动时，已有专家结果则拼接返回
            if specialist_results:
                logger.warning(f"LLM 汇总失败但已有 {len(specialist_results)} 个专家结果(stream),拼接回退")
                fb_result = _build_fallback_from_specialists(
                    query, refined_query, specialist_results, trace_id, budget
                )
                if conversation_id:
                    _schedule_auto_evaluation(conversation_id, message_id, {
                        "answer": fb_result["answer"], "specialist_results": specialist_results,
                        "tool_calls": [tc for sr in specialist_results for tc in sr.get("tool_calls", [])],
                        "duration_ms": duration_ms, "complexity": complexity, "conflicts": conflicts,
                    })
                yield {
                    "type": "answer",
                    "content": fb_result["answer"],
                    "specialist_results": specialist_results,
                    "tool_calls": [tc for sr in specialist_results for tc in sr.get("tool_calls", [])],
                    "partial": True,
                }
                return
            raise

        msg = response.choices[0].message

        # 没有工具调用 → 检查是否需要交叉审阅,然后给出最终回答
        if not msg.tool_calls:
            ntc_gen = _stream_handle_no_tool_calls(
                msg, specialist_results, all_tool_calls,
                refined_query, query, rag_context,
                complexity, clarification, route_result,
                conflicts, force_skip_cross_review, arbitration_done,
                prebuilt_context, cancel_event, start_time,
                conversation_id, message_id, trace_id)
            ntc_done = False
            while True:
                try:
                    yield next(ntc_gen)
                except StopIteration as si:
                    ntc_done = si.value
                    break
            if ntc_done:
                return
            # ntc_done == False 不会发生（函数总是返回 True），但防御性处理
            arbitration_done = True  # 函数内部已处理仲裁

        # 有工具调用 → 执行专家
        assistant_msg = {
            "role": "assistant",
            "content": msg.content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ],
        }

        # MIMO thinking mode
        reasoning = None
        if hasattr(msg, "model_extra") and msg.model_extra:
            reasoning = msg.model_extra.get("reasoning_content")
        if not reasoning:
            reasoning = getattr(msg, "reasoning_content", None)
        if reasoning:
            assistant_msg["reasoning_content"] = reasoning

        llm_messages.append(assistant_msg)

        max_tools = get_max_tools_per_turn(complexity)
        original_tool_count = len(msg.tool_calls or [])
        msg.tool_calls = trim_tool_calls(msg.tool_calls, specialists, max_tools)
        if original_tool_count != len(msg.tool_calls):
            logger.warning(
                f"Orchestrator(stream) 工具调用已裁剪: {original_tool_count} -> {len(msg.tool_calls)} "
                f"(allowed={specialists}, max={max_tools})"
            )

        # 解析所有 tool call 参数
        # 从 clarification 中提取 specialist_tasks（LLM 规划的专家任务）
        specialist_tasks_map = {}
        need_cross_review_flag = False
        if isinstance(clarification, dict):
            specialist_tasks_map = clarification.get("specialist_tasks", {})
            need_cross_review_flag = clarification.get("need_cross_review", False)

        tool_tasks = []
        skipped_tasks = []
        for tc in msg.tool_calls:
            args = _parse_tool_args(tc.function.arguments, tc.function.name)
            expert_query = args.get("query", query)
            agent_key = build_expert_map().get(tc.function.name, "")
            agent_info = load_specialist_agents().get(agent_key, {})

            # 注入 specialist_tasks：把 LLM 规划的专家任务拼接到 query 前面作为上下文
            if agent_key in specialist_tasks_map and specialist_tasks_map[agent_key]:
                task_desc = specialist_tasks_map[agent_key]
                expert_query = f"[专家任务] {task_desc}\n[用户问题] {expert_query}"
                logger.info(f"注入专家任务到 {agent_key}: {task_desc[:80]}...")

            # 恢复模式:跳过已完成的专家
            if agent_key in completed_specialists:
                logger.info(f"跳过已完成的专家: {agent_key}")
                skipped_tasks.append((tc, args, expert_query, agent_key, agent_info))
                continue

            logger.info(f"Orchestrator → {tc.function.name}: {expert_query[:100]}")
            tool_tasks.append((tc, args, expert_query, agent_key, agent_info))

        # 恢复模式:为跳过的专家添加 tool response
        for tc, args, expert_query, agent_key, agent_info in skipped_tasks:
            # 从已有结果中找到对应的分析
            existing_result = next(
                (sr for sr in resumed_results if sr.get("agent_key") == agent_key),
                None
            )
            if existing_result:
                all_tool_calls.append({
                    "name": tc.function.name,
                    "arguments": args,
                    "result_preview": existing_result.get("analysis", "")[:300],
                })
                llm_messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": existing_result.get("analysis", "")[:4000],
                })

        # 如果所有专家都已完成,直接进入下一轮(让 LLM 综合结果)
        if not tool_tasks:
            yield {"type": "status", "message": "所有专家已完成,正在综合结果..."}
            continue

        # 创建 pending 状态的 agent 执行记录
        agent_run_ids = {}
        for tc, args, expert_query, agent_key, agent_info in tool_tasks:
            run_id = create_pending_agent_run(
                conversation_id=conversation_id,
                message_id=message_id,
                agent_key=agent_key,
                agent_name=agent_info.get("name", tc.function.name),
                query=expert_query[:8000],
                trace_id=trace_id,
            )
            agent_run_ids[agent_key] = run_id

        # 通知前端:专家开始工作
        _check_cancel(cancel_event)
        for tc, args, expert_query, agent_key, agent_info in tool_tasks:
            update_agent_run_status(agent_run_ids[agent_key], "running")
            yield {
                "type": "specialist_start",
                "agent_key": agent_key,
                "agent": agent_info.get("name", tc.function.name),
                "icon": agent_info.get("icon", "🤖"),
            }

        # 并行执行所有专家
        import queue
        result_queue = queue.Queue()

        def _on_specialist_complete(idx, tc, args, agent_key, agent_info, future):
            """线程回调:专家完成后将结果放入队列。"""
            try:
                result_str = future.result()
            except CancelledError:
                result_str = json.dumps({"error": "cancelled"}, ensure_ascii=False)
            except Exception as e:
                result_str = json.dumps({"error": str(e)}, ensure_ascii=False)
            result_queue.put((idx, tc, args, agent_key, agent_info, result_str))

        if len(tool_tasks) == 1:
            # 单个专家,直接执行
            tc, args, expert_query, agent_key, agent_info = tool_tasks[0]
            result_str = _execute_specialist_cached(tc.function.name, expert_query, cancel_event,
                                              prebuilt_context=prebuilt_context,
                                              trace_id=trace_id)
            result_queue.put((0, tc, args, agent_key, agent_info, result_str))
        elif shared_blackboard_enabled and 2 <= len(tool_tasks) <= 3:
            # P1：统一黑板 — 使用 Blackboard 类（与 Pipeline 路径一致）
            # 2-3 专家串行执行，后执行者能看到前序结构化结论
            bb = Blackboard(max_entries=6)
            for idx, (tc, args, expert_query, agent_key, agent_info) in enumerate(tool_tasks):
                _check_cancel(cancel_event)
                _check_timeout(start_time)
                # 注入黑板中已完成专家的结论摘要（排除自己）
                bb_summary = bb.to_context_text(exclude_agent=agent_key)
                ctx = prebuilt_context + bb_summary if bb_summary else prebuilt_context
                result_str = _execute_specialist_cached(
                    tc.function.name, expert_query, cancel_event,
                    prebuilt_context=ctx, trace_id=trace_id,
                )
                result_queue.put((idx, tc, args, agent_key, agent_info, result_str))
                # 提取结构化结论写入黑板，供后续专家参考
                try:
                    result_dict = json.loads(result_str)
                    if "error" not in result_dict:
                        agent_name = agent_info.get("name", agent_key) if isinstance(agent_info, dict) else agent_key
                        entry = extract_entry_from_result(
                            agent_key=agent_key,
                            agent_name=agent_name,
                            result=result_dict,
                            tokens_used=result_dict.get("tokens_used", 0),
                        )
                        bb.write(entry)
                except Exception as _e:
                    logger.debug(f"[blackboard] 提取 {agent_key} 结论失败: {_e}")
        else:
            # 多个专家,并行执行（4+ 专家保持并行，黑板未启用时也走此路）
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(tool_tasks)) as executor:
                for idx, (tc, args, expert_query, agent_key, agent_info) in enumerate(tool_tasks):
                    future = executor.submit(
                        _execute_specialist, tc.function.name, expert_query,
                        cancel_event=cancel_event, prebuilt_context=prebuilt_context,
                        trace_id=trace_id
                    )
                    future.add_done_callback(
                        lambda f, idx=idx, tc=tc, args=args, ak=agent_key, ai=agent_info:
                        _on_specialist_complete(idx, tc, args, ak, ai, f)
                    )

        # 收集结果,yield specialist_done 事件
        completed = 0
        ordered_results = [None] * len(tool_tasks)
        while completed < len(tool_tasks):
            _check_cancel(cancel_event)
            try:
                idx, tc, args, agent_key, agent_info, result_str = result_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            ordered_results[idx] = (tc, args, result_str)
            completed += 1

            try:
                result_data = json.loads(result_str)
            except json.JSONDecodeError:
                result_data = {"raw": result_str}

            if "error" not in result_data:
                specialist_result = {
                    "agent_key": result_data.get("agent_key", agent_key),
                    "agent": result_data.get("agent", agent_info.get("name", "")),
                    "icon": result_data.get("icon", agent_info.get("icon", "🤖")),
                    "analysis": result_data.get("analysis", ""),
                    "tool_calls": result_data.get("tool_calls", []),
                    "duration_ms": result_data.get("duration_ms", 0),
                }
                # 清理 analysis 中的 tool_calls 原始标签，保证前端只看到 md 内容
                if "analysis" in specialist_result:
                    analysis = specialist_result["analysis"]
                    analysis = re.sub(r"<tool_calls>.*?</tool_calls>", "", analysis, flags=re.DOTALL)
                    analysis = re.sub(r"<tool_call>.*?</tool_call>", "", analysis, flags=re.DOTALL)
                    analysis = re.sub(r"<invoke name=.*?</invoke>", "", analysis, flags=re.DOTALL)
                    analysis = re.sub(r"<function=.*?</function>", "", analysis, flags=re.DOTALL)
                    analysis = re.sub(r"<parameter=.*?</parameter>", "", analysis, flags=re.DOTALL)
                    analysis = re.sub(r"[\{].*?requestId.*?[\}]", "", analysis, flags=re.DOTALL)
                    analysis = re.sub(r"\n{3,}", "\n\n", analysis).strip()
                    specialist_result["analysis"] = analysis
                specialist_results.append(specialist_result)

                # 更新 agent 执行记录：正常完成记 completed，降级记 completed+degraded 标记
                _run_status = "completed"
                if result_data.get("status") == "degraded":
                    _run_status = "completed"  # 降级也算完成，结果可用
                    logger.info(f"[trace:{trace_id}] 专家 {agent_key} 以降级模式完成")
                update_agent_run_status(
                    agent_run_ids.get(agent_key),
                    _run_status,
                    result=result_data.get("analysis", "")[:8000],
                    duration_ms=result_data.get("duration_ms", 0),
                )

                yield {
                    "type": "specialist_done",
                    "agent_key": specialist_result["agent_key"],
                    "agent": specialist_result["agent"],
                    "icon": specialist_result["icon"],
                    "analysis": specialist_result["analysis"],
                    "duration_ms": specialist_result["duration_ms"],
                }
            else:
                # 更新 agent 执行记录为 failed
                _fail_status = "failed"
                if "timeout" in str(result_data.get("error", "")).lower():
                    _fail_status = "timeout"
                update_agent_run_status(
                    agent_run_ids.get(agent_key),
                    _fail_status,
                    error_message=result_data.get("error", "未知错误"),
                )

        # 按原始顺序 append tool response 到 llm_messages
        for idx, (tc, args, result_str) in enumerate(ordered_results):
            if result_str is None:
                result_str = json.dumps({"error": "执行未完成"}, ensure_ascii=False)

            all_tool_calls.append({
                "name": tc.function.name,
                "arguments": args,
                "result_preview": result_str[:300],
            })

            llm_messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result_str[:4000],
            })

        yield {"type": "status", "message": "正在综合各专家意见..."}

        # 增强2: 动态 Agent 选择 — 检测是否需要追加专家（预算门控）
        _check_cancel(cancel_event)
        max_total = context_config.get("max_specialists", 4)
        dynamic_spawn_enabled = get_orchestration_config("dynamic_spawn_enabled", "false") == "true"
        spawn_proposals = []  # 记录提案，即使不执行也写入日志
        for sr in list(specialist_results):
            suggestions = _check_dynamic_spawn(sr, already_called)
            for s in suggestions:
                agent_key = s["agent_key"]
                if agent_key in already_called:
                    continue
                # 预算检查：总专家数是否超限
                if len(already_called) >= max_total:
                    spawn_proposals.append({**s, "executed": False, "reason_skipped": "over_budget"})
                    logger.info(f"动态追加提案被拒(预算已满 {max_total}): {agent_key} — {s['reason']}")
                    continue
                # 配置开关：默认不自动执行
                if not dynamic_spawn_enabled:
                    spawn_proposals.append({**s, "executed": False, "reason_skipped": "disabled"})
                    logger.info(f"动态追加提案被拒(功能未开启): {agent_key} — {s['reason']}")
                    continue
                already_called.add(agent_key)
                agent_info = load_specialist_agents().get(agent_key, {})
                yield {
                    "type": "dynamic_spawn",
                    "agent_key": agent_key,
                    "agent": agent_info.get("name", agent_key),
                    "icon": agent_info.get("icon", "🤖"),
                    "reason": s["reason"],
                }
                yield {"type": "specialist_start", "agent_key": agent_key,
                       "agent": agent_info.get("name", agent_key), "icon": agent_info.get("icon", "🤖")}
                try:
                    model = _get_model_for_agent(agent_key) if _is_cost_routing_enabled() else MODEL
                    spawn_result = run_specialist(agent_key, refined_query,
                                                  prebuilt_context=prebuilt_context, model=model,
                                                  trace_id=trace_id)
                    specialist_results.append(spawn_result)
                    all_tool_calls.append({"name": f"consult_{agent_key}",
                                           "arguments": {"query": refined_query},
                                           "result_preview": spawn_result.get("analysis", "")[:300]})
                    yield {
                        "type": "specialist_done",
                        "agent_key": spawn_result.get("agent_key", agent_key),
                        "agent": spawn_result.get("agent", agent_info.get("name", "")),
                        "icon": spawn_result.get("icon", agent_info.get("icon", "🤖")),
                        "analysis": spawn_result.get("analysis", ""),
                        "duration_ms": spawn_result.get("duration_ms", 0),
                    }
                except Exception as e:
                    logger.error(f"动态追加专家 {agent_key} 执行失败: {e}")

        # 增强1: 检查点存档 — Phase A 专家执行完成
        if conversation_id and message_id and specialist_results:
            _save_checkpoint(conversation_id, message_id, "experts", {
                "specialist_results": specialist_results,
                "all_tool_calls": all_tool_calls,
                "arbitration_done": arbitration_done,
                "complexity": complexity,
            })

    # 超过最大轮次,做最后一次总结
    yield from _stream_final_synthesis(
        query, refined_query, specialists, specialist_results, all_tool_calls,
        llm_messages, prebuilt_context, complexity, arbitration_done,
        route_result, conflicts, perf_metrics,
        cancel_event, start_time,
        conversation_id, message_id, trace_id)
    return


# ═══════════════════════════════════════════════════════════════
# 胶水1: 对话结束自动评测
# ═══════════════════════════════════════════════════════════════

_eval_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="auto_eval"
)

LOW_SCORE_THRESHOLD = 60


# ── 自动评测成本控制 ──
_auto_eval_daily_count = 0
_auto_eval_daily_date = ""
_AUTO_EVAL_DAILY_LIMIT = 5  # 每天最多自动评测 5 条（成本可控）
_AUTO_EVAL_MIN_COMPLEXITY = "medium"  # 只评测 medium/complex，跳过 simple


def _reset_daily_eval_count_if_needed():
    """每天重置自动评测计数。"""
    global _auto_eval_daily_count, _auto_eval_daily_date
    today = datetime.now().strftime("%Y-%m-%d")
    if _auto_eval_daily_date != today:
        _auto_eval_daily_count = 0
        _auto_eval_daily_date = today


def _schedule_auto_evaluation(conv_id: int, msg_id: int, result: dict):
    """异步调度对话质量评测（不阻塞主流程）。

    成本控制策略：
    1. 配置开关 llm_cost.auto_conversation_eval
    2. 只评测 medium/complex（跳过 simple 省成本）
    3. 每日上限 10 条（防死循环/防严重消耗 token）
    4. 去重：同一消息只评测一次（has_evaluation）
    5. 先规则评估（无 LLM 调用），仅低分才追加 LLM 评估
    """
    global _auto_eval_daily_count
    if not conv_id:
        return
    if get_config("llm_cost.auto_conversation_eval", "false") != "true":
        logger.debug("对话结束自动评测已关闭（llm_cost.auto_conversation_eval=false）")
        return

    # 每日上限
    _reset_daily_eval_count_if_needed()
    if _auto_eval_daily_count >= _AUTO_EVAL_DAILY_LIMIT:
        logger.debug(f"自动评测已达每日上限({_AUTO_EVAL_DAILY_LIMIT})，跳过")
        return

    # 只评测 medium/complex
    complexity = "medium"
    if isinstance(result, dict):
        complexity = result.get("complexity", "medium")
    complexity_order = {"simple": 0, "medium": 1, "complex": 2}
    if complexity_order.get(complexity, 1) < complexity_order.get(_AUTO_EVAL_MIN_COMPLEXITY, 1):
        logger.debug(f"复杂度={complexity}<{_AUTO_EVAL_MIN_COMPLEXITY}，跳过自动评测")
        return

    _auto_eval_daily_count += 1
    logger.info(f"调度自动评测: conv={conv_id} msg={msg_id} complexity={complexity} (今日第{_auto_eval_daily_count}条)")
    _eval_executor.submit(_run_auto_evaluation_sync, conv_id, msg_id, result)


def _schedule_tool_eval(query: str, specialist_results: list[dict]):
    """异步调度工具调用质量评估（升级三，不阻塞主流程）。"""
    try:
        from agent.tool_tracker import evaluate_tool_calls_async
        _eval_executor.submit(evaluate_tool_calls_async, query, specialist_results)
    except Exception as e:
        logger.debug(f"工具调用评估调度失败（不影响主流程）: {e}")


def _run_auto_evaluation_sync(conv_id: int, msg_id: int, result: dict):
    """在独立线程中运行评测，避免阻塞 orchestrator 线程池。"""
    try:
        loop = asyncio.new_event_loop()
        loop.run_until_complete(_do_auto_evaluation(conv_id, msg_id, result))
    except Exception as e:
        logger.error(f"自动评测失败: conv={conv_id} msg={msg_id} — {e}")
    finally:
        loop.close()


async def _do_auto_evaluation(conv_id: int, msg_id: int, result: dict):
    """执行自动评测：规则评估 → 写入DB → 低分走进化流程。"""
    import json as _json
    from db.conversations import has_evaluation
    from agent.conversation_evaluator import ConversationQualityEvaluator
    from agent.conversation_evolution import process_conversation_evaluation
    from db.eval import create_conversation_evaluation

    # 去重：同一消息只评测一次
    if has_evaluation(conv_id, msg_id):
        logger.debug(f"跳过重复评测: conv={conv_id} msg={msg_id}")
        return

    evaluator = ConversationQualityEvaluator()
    try:
        evaluation = evaluator.evaluate(
            conv_id, msg_id,
            trigger_evolution=False,
            use_llm=False,  # 先规则评估，快
        )
    except Exception as e:
        logger.error(f"规则评估失败: conv={conv_id} — {e}")
        return

    # ── 写入评测结果到 DB ──
    try:
        dims_breakdown = {}
        for dim in evaluation.dimensions:
            if isinstance(dim, dict):
                dims_breakdown[dim["name"]] = {"score": dim.get("score", 0), "details": dim.get("metrics", {})}
            elif hasattr(dim, "name"):
                dims_breakdown[dim.name] = {"score": dim.score, "details": dim.details}

        create_conversation_evaluation(
            conversation_id=conv_id,
            message_id=msg_id,
            auto_score=evaluation.auto_score,
            auto_score_breakdown=_json.dumps(dims_breakdown, ensure_ascii=False),
            complexity=getattr(evaluation, "metadata", {}).get("complexity", "medium"),
            specialist_count=getattr(evaluation, "metadata", {}).get("specialist_count", 0),
            duration_ms=getattr(evaluation, "metadata", {}).get("duration_ms", 0),
            has_cross_review=getattr(evaluation, "metadata", {}).get("has_cross_review", False),
            has_arbitration=getattr(evaluation, "metadata", {}).get("has_arbitration", False),
            duplicate_calls=getattr(evaluation, "metadata", {}).get("duplicate_calls", 0),
            suggestions=_json.dumps(evaluation.suggestions, ensure_ascii=False),
        )
        logger.info(f"自动评测写入DB: conv={conv_id} score={evaluation.auto_score:.1f}")
    except Exception as e:
        logger.error(f"评测结果写入DB失败: conv={conv_id} — {e}")

    ev = {
        "auto_score": evaluation.auto_score,
        "auto_score_breakdown": dims_breakdown,
        "suggestions": evaluation.suggestions,
    }

    # 触发进化流程（Bad Case 标记、反馈学习等）
    try:
        await process_conversation_evaluation(conv_id, ev)
    except Exception as e:
        logger.error(f"进化流程失败: conv={conv_id} — {e}")

    # 低分追加 LLM 评估（更深分析）
    if evaluation.auto_score < LOW_SCORE_THRESHOLD:
        try:
            llm_eval = evaluator.evaluate(
                conv_id, msg_id,
                trigger_evolution=False,
                use_llm=True,
            )
            await process_conversation_evaluation(conv_id, {
                "auto_score": llm_eval.auto_score,
                "auto_score_breakdown": {
                    dim.name: dim.score for dim in llm_eval.dimensions
                },
                "suggestions": llm_eval.suggestions,
            })
        except Exception as e:
            logger.error(f"LLM 评估失败: conv={conv_id} — {e}")





def _build_fallback_from_specialists(query, refined_query, specialist_results, trace_id, budget):
    """当 LLM 汇总失败（如网络抖动）时，用已有专家结果拼接答案。"""
    sections = []
    for sr in specialist_results:
        agent_key = sr.get("agent_key", "unknown")
        agent_name = sr.get("agent", agent_key)
        icon = sr.get("icon", "\U0001f916")
        analysis = sr.get("analysis", "")
        sections.append(f"## {icon} {agent_name}\n\n{analysis}\n\n")
    answer = "由于服务暂时拥堵，未能自动汇总各方分析。以下是各专家独立观点：\n\n" + "\n---\n".join(sections)
    answer += "\n\n---\n> \u26a0\ufe0f 由于网络波动，综合汇总未能完成，以上是各专家的独立分析。建议重新发送问题获取完整汇总。"
    answer = re.sub(r"<tool_calls>.*?</tool_calls>", "", answer, flags=re.DOTALL)
    answer = re.sub(r"<tool_call>.*?</tool_call>", "", answer, flags=re.DOTALL)
    answer = re.sub(r"<invoke name=.*?</invoke>", "", answer, flags=re.DOTALL)
    answer = re.sub(r"<function=.*?</function>", "", answer, flags=re.DOTALL)
    answer = re.sub(r"<parameter=.*?</parameter>", "", answer, flags=re.DOTALL)
    answer = re.sub(r"\n{3,}", "\n\n", answer).strip()
    return {
        "answer": answer,
        "specialist_results": specialist_results,
        "tool_calls": [tc for sr in specialist_results for tc in sr.get("tool_calls", [])],
        "turns": 1,
        "fallback": True,
        "partial": True,
    }

def _fallback_orchestrate(query: str, history: list, rag_context: str = "") -> dict:
    """当模型不支持 function calling 时,回退到普通对话模式。"""
    from services.llm_service import chat_with_agent

    answer = chat_with_agent(build_orchestrator_system_prompt(), history + [{"role": "user", "content": query}], rag_context)
    return {
        "answer": answer,
        "specialist_results": [],
        "tool_calls": [],
        "turns": 1,
        "fallback": True,
    }


# ── 多模型评审 ──

_PEER_REVIEW_PROMPTS = {
    "suitability": """你是一位投资适当性审查员。请审查以下投资决策是否匹配用户的资金用途、投资期限和风险承受能力。

决策摘要:
{summary}

决策依据:
{rationale}

风险信息:
{risk_json}

用户画像:
{profile_text}

请返回 JSON:
{{
  "verdict": "approve | approve_with_concerns | reject | defer",
  "score": {{"suitability": 0-100}},
  "concerns": ["关注点1", ...],
  "suggestions": ["建议1", ...]
}}""",

    "evidence": """你是一位投资证据审查员。请审查以下投资决策的数据是否新鲜、是否有来源、是否过度依赖单一证据。

决策摘要:
{summary}

证据信息:
{evidence_json}

请返回 JSON:
{{
  "verdict": "approve | approve_with_concerns | reject | defer",
  "score": {{"evidence_quality": 0-100}},
  "concerns": ["关注点1", ...],
  "suggestions": ["建议1", ...]
}}""",

    "counter":"""你是一位投资反方观点审查员。请从"不应该做这笔投资"的角度提出最有力的反对理由。

决策摘要:
{summary}

决策依据:
{rationale}

请返回 JSON:
{{
  "verdict": "approve | approve_with_concerns | reject | defer",
  "concerns": ["反对理由1", ...],
  "suggestions": ["风险缓释建议1", ...]
}}""",

    "overconfidence": """你是一位过度自信检测审查员。请检查以下投资决策是否把不确定判断说成确定结论。

决策摘要:
{summary}

决策依据:
{rationale}

置信度:{confidence}

请返回 JSON:
{{
  "verdict": "approve | approve_with_concerns | reject | defer",
  "score": {{"overconfidence_risk": 0-100}},
  "concerns": ["过度自信点1", ...],
  "suggestions": ["措辞修正建议1", ...]
}}""",
}


def run_peer_review(decision: dict, reviewer_type: str) -> dict | None:
    """运行单个维度的评审,返回结构化结果。

    Args:
        decision: 决策记录(dict)
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
            temperature=get_config_float('llm.temperature_agent', 0.3),
            max_tokens=get_config_int('llm.max_tokens_orchestrator_summary', 1000),
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
