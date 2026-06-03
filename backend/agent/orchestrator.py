"""Orchestrator — 主控 Agent，协调各专家 Agent 完成分析"""

import json
import logging
import re
import time
import threading
import concurrent.futures

from llm_service import client, MODEL, _call_llm, _parse_tool_args
from agent.multi_agent import run_specialist, run_specialist_with_context, run_arbitration
from db.agents import (
    load_specialist_agents,
    create_pending_agent_run,
    update_agent_run_status,
    get_completed_agents_for_message,
    cancel_running_agents,
)
from config import ARBITRATION_API_KEY

# 全局超时限制（秒）
MAX_ORCHESTRATION_SECONDS = 1800  # 30 分钟
from agent.feedback_learner import get_preference_context
from agent.memory import (
    compress_history_semantic, build_user_memory_context,
    get_token_budget, compress_rag_token_aware, estimate_tokens,
)

logger = logging.getLogger(__name__)


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
    """检查是否被取消，如果是则抛出 CancelledError。"""
    if cancel_event and cancel_event.is_set():
        raise CancelledError("用户取消了执行")


def _check_timeout(start_time: float):
    """检查是否超时，如果是则抛出异常。"""
    elapsed = time.time() - start_time
    if elapsed > MAX_ORCHESTRATION_SECONDS:
        raise TimeoutError(f"执行超时（{int(elapsed)}s > {MAX_ORCHESTRATION_SECONDS}s 限制）")


# ── 智能交叉审阅：检测专家分歧 ──────────────────────────────

def _detect_specialist_disagreement(specialist_results: list) -> bool:
    """检测专家之间是否存在方向性分歧，决定是否需要交叉审阅。

    保守策略：只要有分歧就触发，只在完全一致时跳过。
    纯字符串匹配，无 LLM 调用，零延迟。
    """
    if len(specialist_results) < 2:
        return False

    bullish_kw = ["低估", "机会", "建议买", "加仓", "建仓", "适合", "看好", "上行", "配置价值", "值得"]
    bearish_kw = ["高估", "风险高", "不建议买", "减仓", "回避", "谨慎", "看空", "下行", "泡沫", "过热"]

    signals = []
    for sr in specialist_results:
        analysis = sr.get("analysis", "").lower()
        bull_score = sum(1 for kw in bullish_kw if kw in analysis)
        bear_score = sum(1 for kw in bearish_kw if kw in analysis)

        if bull_score > bear_score + 1:
            direction = "bullish"
        elif bear_score > bull_score + 1:
            direction = "bearish"
        else:
            direction = "neutral"
        signals.append(direction)

    directions = set(signals)
    # 所有专家方向一致 → 跳过交叉审阅
    if len(directions) == 1:
        return False
    # 有看多+看空分歧 → 必须交叉审阅
    if "bullish" in directions and "bearish" in directions:
        return True
    # 混合中性+方向性 → 交叉审阅
    return len(directions) > 1


def should_arbitrate(complexity: str, specialist_results: list) -> bool:
    """判断是否需要仲裁 Agent 介入。

    条件（从 orchestration_config 读取）：
    - arbitration_enabled == "true"
    - ARBITRATION_API_KEY 已配置
    - complexity >= arbitration_complexity
    - ≥2 个专家参与分析
    """
    if get_orchestration_config("arbitration_enabled", "true") != "true":
        return False
    if not ARBITRATION_API_KEY:
        return False
    min_complexity = get_orchestration_config("arbitration_complexity", "complex")
    complexity_order = {"simple": 0, "medium": 1, "complex": 2}
    if complexity_order.get(complexity, 0) < complexity_order.get(min_complexity, 2):
        return False
    if len([sr for sr in specialist_results if not sr.get("is_cross_review")]) < 2:
        return False
    return True


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


# ── 需求澄清 Agent（LLM 版）──────────────────────────────────

def build_clarification_prompt() -> str:
    """从数据库动态生成需求路由提示词。"""
    specialists = load_specialist_agents()
    expert_lines = []
    for key, info in specialists.items():
        expert_lines.append(f"- {key}: {info['description']}")
    expert_list = "\n".join(expert_lines)
    keys_json = json.dumps(list(specialists.keys()), ensure_ascii=False)

    return f"""你是投资分析需求路由专家。分析用户问题，返回 JSON。

## 可用专家
{expert_list}

## 复杂度判断规则（按优先级）

### chat - 闲聊/科普（不调用任何专家）
判断条件（满足任一即为 chat）：
1. 纯问候/感谢/道歉（"你好"、"谢谢"、"抱歉"）
2. 概念解释（"什么是PE"、"解释定投"、"Z分数什么意思"）
3. 系统功能询问（"你能做什么"、"怎么用"）
4. 与投资无关的问题（"今天天气"、"推荐电影"）
5. 观点讨论但不需要数据支持（"你觉得价值投资怎么样"）

### simple - 单一数据查询（1个专家，无需深度分析）
判断条件：
1. 查询单一指标（"沪深300估值"、"债市温度"）
2. 查询单一基金信息（"161725怎么样"）
3. 查询持仓概况（"我持有什么"、"仓位多少"）
4. 简单事实查询（"最近有什么新闻"）

### medium - 分析任务（1-2个专家，需要分析判断）
判断条件：
1. 对比分析（"A和B哪个好"、"红利质量vs中证红利"）
2. 单一维度深度分析（"白酒估值高吗"、"现在适合买债券吗"）
3. 持仓诊断（"我的持仓健康吗"、"需要调仓吗"）
4. 基金筛选（"推荐几只低估值基金"）

### complex - 综合决策（2+个专家，多维分析）
判断条件（必须同时满足）：
1. 需要多维度分析（估值+配置+风险）
2. 涉及具体操作建议（买入/卖出/定投方案）
3. 需要个性化定制（基于用户持仓/偏好）

典型场景：
- "帮我做个定投方案"
- "我现在应该怎么配置"
- "分析一下我的持仓并给建议"

## 关键区分规则

1. **"可以买吗"/"值得买吗"** → medium（只问估值判断，不需要配置建议）
2. **"帮我做个方案"/"应该怎么配置"** → complex（需要多维分析）
3. **对比类问题** → medium（只需估值专家对比数据）
4. **持仓查询** → simple（只查数据，不分析）
5. **持仓诊断** → medium（需要分析，但不需要多专家协作）

## 输出格式（只输出JSON，无其他文字）
{{"complexity":"chat|simple|medium|complex","specialists":["expert1"],"reason":"判断原因","refined_query":"优化后的查询","confidence":0.95}}

注意：
- specialists 数组中的值必须是以下之一：{keys_json}，chat 时为空数组
- confidence 表示判断置信度（0-1），低于 0.7 时系统会降级处理

## 示例

Q: 你好
A: {{"complexity":"chat","specialists":[],"reason":"纯问候","refined_query":"你好","confidence":0.99}}

Q: 什么是PE
A: {{"complexity":"chat","specialists":[],"reason":"概念解释，无需查数据","refined_query":"PE（市盈率）的定义","confidence":0.98}}

Q: 谢谢
A: {{"complexity":"chat","specialists":[],"reason":"闲聊","refined_query":"谢谢","confidence":0.99}}

Q: 沪深300估值多少
A: {{"complexity":"simple","specialists":["valuation_expert"],"reason":"单一指数估值查询","refined_query":"沪深300当前PE/PB估值和百分位","confidence":0.95}}

Q: 债市温度多少
A: {{"complexity":"simple","specialists":["market_analyst"],"reason":"单一数据查询","refined_query":"当前债市温度指标","confidence":0.95}}

Q: 我持有什么基金
A: {{"complexity":"simple","specialists":[],"reason":"持仓查询，无需分析","refined_query":"查看当前持仓列表","confidence":0.90}}

Q: 红利质量和中证红利有什么区别
A: {{"complexity":"medium","specialists":["valuation_expert"],"reason":"两个指数的对比分析","refined_query":"红利质量和中证红利的估值对比（PE/PB/百分位/股息率）","confidence":0.90}}

Q: 白酒估值高吗
A: {{"complexity":"medium","specialists":["valuation_expert"],"reason":"单一板块估值分析","refined_query":"白酒板块当前估值水平和历史百分位","confidence":0.85}}

Q: 现在适合买债券吗
A: {{"complexity":"medium","specialists":["market_analyst"],"reason":"债券买入时机判断，需要债市温度","refined_query":"当前债市温度和债券投资建议","confidence":0.85}}

Q: 我的持仓健康吗
A: {{"complexity":"medium","specialists":["allocation_advisor"],"reason":"持仓诊断分析","refined_query":"持仓健康度诊断和优化建议","confidence":0.85}}

Q: 帮我做个定投方案
A: {{"complexity":"complex","specialists":["valuation_expert","allocation_advisor"],"reason":"定投需要估值+配置策略","refined_query":"基于当前估值的定投方案","confidence":0.92}}

Q: 我现在应该怎么配置资产
A: {{"complexity":"complex","specialists":["valuation_expert","allocation_advisor","risk_assessor"],"reason":"资产配置需要多维分析","refined_query":"基于当前市场和个人持仓的资产配置建议","confidence":0.90}}

Q: 分析一下我的持仓并给建议
A: {{"complexity":"complex","specialists":["allocation_advisor","risk_assessor"],"reason":"持仓分析需要配置+风险评估","refined_query":"持仓分析和优化建议","confidence":0.88}}"""


# Clarification 结果缓存（相同查询直接返回缓存结果，节省 2-5s LLM 调用）
_clarification_cache: dict[int, dict] = {}
_CLARIFICATION_CACHE_MAX = 128


def clarify_requirement(query: str) -> dict:
    """
    使用 LLM 分析用户问题，返回需求澄清结果。

    返回:
        {
            "complexity": "simple|medium|complex",
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

    try:
        # 注入持仓摘要，让澄清 Agent 知道用户持有什么
        try:
            from portfolio_context import build_portfolio_summary_line
            portfolio_line = build_portfolio_summary_line()
        except Exception:
            portfolio_line = ""

        user_content = query
        if portfolio_line:
            user_content = f"{portfolio_line}\n\n用户问题: {query}"

        response = _call_llm(
            caller="clarify",
            model=MODEL,
            messages=[
                {"role": "system", "content": build_clarification_prompt()},
                {"role": "user", "content": user_content},
            ],
            temperature=0.1,
            max_tokens=2000,
        )

        raw = response.choices[0].message.content.strip()

        # 提取 JSON — 多种容错策略
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

        # 兼容模型返回非标准格式（如 {"需求分析": {...}} ）
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
        # 如果没有选择专家，默认选估值专家（chat 除外）
        elif not specialists:
            specialists = ["valuation_expert"]

        # 置信度检查
        confidence = result.get("confidence", 0.8)
        if confidence < 0.7:
            logger.warning(f"澄清置信度过低 ({confidence})，降级为 simple")
            complexity = "simple"
            specialists = ["valuation_expert"]

        result_out = {
            "complexity": complexity,
            "specialists": specialists,
            "reason": result.get("reason", ""),
            "refined_query": result.get("refined_query", query),
            "confidence": confidence,
        }

        # 缓存结果
        if len(_clarification_cache) >= _CLARIFICATION_CACHE_MAX:
            _clarification_cache.pop(next(iter(_clarification_cache)))
        _clarification_cache[cache_key] = result_out

        return result_out

    except Exception as e:
        logger.warning(f"需求澄清失败，回退到关键词匹配: {e}")
        # 回退到关键词匹配
        complexity = detect_complexity_by_keywords(query)
        specialists = route_to_specialists_by_keywords(query)
        return {
            "complexity": complexity,
            "specialists": specialists,
            "reason": "关键词匹配（LLM澄清失败）",
            "refined_query": query,
        }


# ── 任务复杂度检测（关键词匹配，作为回退方案）──────────────────────────

def detect_complexity_by_keywords(query: str) -> str:
    """
    检测任务复杂度：simple / medium / complex

    simple: 单一数据查询（如"沪深300估值多少"、"债市温度"）
    medium: 需要分析但范围明确（如"白酒估值高吗"、"最近有什么新闻"）
    complex: 需要多维度分析（如"白酒能买吗"、"帮我做个定投方案"）
    """
    query = query.strip()

    # 复杂任务关键词（需要多专家协作：投资决策+仓位+风险）
    complex_keywords = [
        "加仓", "减仓", "建仓", "清仓",
        "定投", "配置", "组合", "方案", "策略", "计划",
        "风险", "回撤", "波动",
        "持仓", "盈亏", "我的基金", "仓位",
        "怎么分配", "如何配置",
    ]

    # 中等任务关键词（对比分析、单一维度分析）
    medium_keywords = [
        "对比", "比较", "区别", "差异", "哪个好", "选哪个", "还是",
        "怎么样", "怎么看", "值得买", "能买吗", "可以买", "买入",
        "卖出", "持有",
        "现在", "当前", "适合", "应该",
    ]

    # 简单任务关键词（单一数据查询）
    simple_keywords = [
        "估值", "百分位", "PE", "PB", "z-score",
        "债市温度", "温度",
        "多少", "是什么", "查一下", "查询",
        "最新", "今天", "最近",
    ]

    # 闲聊关键词（不需要专家分析）
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
    has_question_mark = bool(re.search(r'[吗呢？?]', query))

    # 纯闲聊：短消息 + 闲聊关键词 + 无投资关键词
    if len(query) <= 10 and chat_score > 0 and complex_score == 0 and medium_score == 0 and simple_score == 0:
        return "chat"

    # 很短的消息（<6字），没有投资关键词，也没有疑问词 → chat
    if len(query) <= 5 and complex_score == 0 and medium_score == 0 and simple_score == 0 and not has_question_mark:
        return "chat"

    # 如果只是查询单一指标（很短的查询，且无疑问词），倾向于简单
    if len(query) <= 6 and simple_score > 0 and not has_question_mark and complex_score == 0:
        return "simple"

    # 有疑问词时，需要进一步分析
    if has_question_mark:
        # 包含复杂关键词 → complex
        if complex_score >= 1:
            return "complex"
        # 包含中等关键词 → medium（如"可以买吗"、"A和B区别"）
        if medium_score >= 1:
            return "medium"
        # 包含简单关键词但有疑问 → medium（如"估值高吗"）
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


def route_to_specialists_by_keywords(query: str) -> list[str]:
    """根据关键词路由到合适的专家。返回 agent_key 列表。"""
    query = query.strip()
    specialists = []

    # 估值相关关键词 → 估值专家
    valuation_keywords = ["估值", "PE", "PB", "百分位", "z-score", "高估", "低估", "贵不贵", "便宜"]
    if any(kw in query for kw in valuation_keywords):
        specialists.append("valuation_expert")

    # 新闻/市场动态关键词 → 择时分析师
    news_keywords = ["新闻", "最新", "动态", "政策", "消息", "市场", "今天", "昨天"]
    if any(kw in query for kw in news_keywords):
        specialists.append("market_analyst")

    # 风险相关关键词 → 风险评估师
    risk_keywords = ["风险", "回撤", "波动", "最大回撤"]
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

    return specialists


def get_context_config(complexity: str) -> dict:
    """根据复杂度返回上下文配置。"""
    if complexity == "simple":
        return {
            "history_limit": 3,      # 只保留最近3条历史
            "rag_enabled": False,    # 简单查询不需要RAG
            "max_specialists": 1,    # 只调用1个专家
            "rag_max_chars": 0,      # RAG上下文最大字符数
        }
    elif complexity == "medium":
        return {
            "history_limit": 5,
            "rag_enabled": True,
            "max_specialists": 2,
            "rag_max_chars": 1500,   # RAG上下文压缩到1500字符
        }
    else:  # complex
        return {
            "history_limit": 10,
            "rag_enabled": True,
            "max_specialists": 5,
            "rag_max_chars": 2500,   # RAG上下文压缩到2500字符
        }


def compress_history(history: list, max_messages: int = 10) -> list:
    """
    压缩对话历史：
    - 保留最近 max_messages 条完整消息
    - 更早的消息只保留摘要（第一条用户消息的前50字）
    """
    if len(history) <= max_messages:
        return history

    # 早期消息：只保留摘要
    early_messages = history[:-max_messages]
    recent_messages = history[-max_messages:]

    # 从早期消息中提取关键信息
    summary_parts = []
    for msg in early_messages:
        if msg["role"] == "user":
            # 用户消息：取前50字
            summary_parts.append(f"用户曾问: {msg['content'][:50]}...")
        elif msg["role"] == "assistant":
            # 助手消息：取前30字
            summary_parts.append(f"助手曾答: {msg['content'][:30]}...")

    # 构建摘要消息
    if summary_parts:
        summary = "以下是早期对话摘要（省略了详细内容）：\n" + "\n".join(summary_parts[-5:])  # 最多保留5条摘要
        compressed = [{"role": "system", "content": summary}] + recent_messages
    else:
        compressed = recent_messages

    return compressed


def compress_rag_context(rag_context: str, max_chars: int = 2000) -> str:
    """
    压缩 RAG 上下文：
    - 截断到 max_chars 字符
    - 保留完整段落，避免截断在句子中间
    """
    if not rag_context or len(rag_context) <= max_chars:
        return rag_context

    # 截断到最大字符数
    truncated = rag_context[:max_chars]

    # 找到最后一个完整段落（双换行符）
    last_paragraph_end = truncated.rfind("\n\n")
    if last_paragraph_end > max_chars * 0.7:  # 如果截断点在70%以后
        truncated = truncated[:last_paragraph_end]

    return truncated + "\n...(已截断，更多内容请参考知识库)"

# ── Orchestrator 的工具 = 调用各个专家 Agent ──────────────

def build_orchestrator_tools() -> list:
    """从数据库动态生成 Orchestrator 可调用的 consult_* 工具定义。"""
    specialists = load_specialist_agents()
    tools = []
    for key, info in specialists.items():
        tools.append({
            "type": "function",
            "function": {
                "name": f"consult_{key}",
                "description": f"咨询{info['name']}，{info['description']}",
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

def build_orchestrator_system_prompt() -> str:
    """从数据库动态生成 Orchestrator 的 system prompt。"""
    specialists = load_specialist_agents()
    team_lines = []
    for key, info in specialists.items():
        team_lines.append(f"- {info['icon']} **{info['name']}**：{info['description']}")
    team_list = "\n".join(team_lines)

    return f"""你是投资分析助手的主控（Orchestrator），负责协调各领域专家 Agent 完成投资分析。

## 工作方式
1. 理解用户问题的核心意图
2. 决定需要咨询哪些专家（可同时咨询多个）
3. 收集各专家的分析结果
4. 综合各专家意见，给出最终的投资建议

## 专家团队
{team_list}

## 回答原则
- 综合各专家意见，给出明确的判断和建议
- 如果专家意见有分歧，指出分歧点并给出你的倾向
- 引用专家的具体数据和分析
- 给出 actionable 的投资建议

## 持仓亏损处理原则
当用户持仓出现亏损或连续下跌时，必须参考知识库中的「4%定投法（强化版）」策略：
- 不要简单建议割肉止损，先评估估值水平和基本面
- 如果估值已进入低估区间，建议按4%间隔分批加仓摊低成本
- 计算平均成本和回盈价位，给用户具体的数字参考
- 强调纪律性：按计划执行，不因恐慌改变策略
- 如果基本面恶化（非单纯下跌），才建议止损
- 使用 Markdown 格式，层次清晰"""


def _execute_specialist(tool_name: str, query: str, cancel_event: threading.Event | None = None,
                        prebuilt_context: str = "") -> str:
    """执行专家 Agent 调用，返回 JSON 字符串结果。"""
    agent_key = build_expert_map().get(tool_name)
    if not agent_key:
        return json.dumps({"error": f"未知专家: {tool_name}"}, ensure_ascii=False)

    try:
        _check_cancel(cancel_event)
        result = run_specialist(agent_key, query, prebuilt_context=prebuilt_context)
        return json.dumps(result, ensure_ascii=False)
    except CancelledError:
        raise
    except Exception as e:
        logger.error(f"专家 {tool_name} 执行异常: {e}")
        return json.dumps({"error": f"专家执行失败: {e}"}, ensure_ascii=False)


def orchestrate(query: str, history: list, rag_context: str = "", cancel_event: threading.Event | None = None) -> dict:
    """
    Orchestrator 主循环。

    流程：
    1. 检测任务复杂度
    2. 根据复杂度优化上下文
    3. LLM 分析用户意图
    4. 决定调用哪些专家
    5. 执行专家 Agent（每个专家独立完成工具调用）
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
    total_tokens = 0  # 累计 token 用量

    # 0. Token 预算检查
    budget = check_token_budget()
    logger.info(f"Token 预算: {budget['used']}/{budget['limit']} ({budget['pct']:.0%}) mode={budget['mode']}")
    if budget["mode"] == "exceeded":
        return {
            "answer": f"今日分析额度已用完（{budget['used']:,}/{budget['limit']:,} tokens），请明天再来。",
            "specialist_results": [],
            "tool_calls": [],
            "turns": 0,
            "complexity": "simple",
            "error": "token_budget_exceeded",
        }

    # 1. 需求澄清（使用 LLM 分析问题）
    clarification = clarify_requirement(query)
    complexity = clarification["complexity"]
    context_config = get_context_config(complexity)
    token_budget = get_token_budget(complexity)
    logger.info(f"需求澄清: {clarification}")

    # 使用澄清后的问题（如果有优化）
    refined_query = clarification.get("refined_query", query)

    # 2. 根据复杂度优化上下文（Token 预算管理）
    system_content = build_orchestrator_system_prompt()

    # RAG 上下文（token 感知截断）
    rag_budget = int(token_budget["total_context"] * token_budget["rag_pct"])
    if rag_context and context_config["rag_enabled"]:
        compressed_rag = compress_rag_token_aware(rag_context, max_tokens=rag_budget)
        system_content += f"\n\n参考信息：\n{compressed_rag}"

    # 注入用户偏好画像（从反馈学习中积累）
    preference_ctx = get_preference_context("default")
    if preference_ctx:
        system_content += f"\n\n{preference_ctx}"

    # 注入跨对话用户记忆
    user_memory = build_user_memory_context("default")
    if user_memory:
        system_content += f"\n\n<user_memory>\n{user_memory}\n</user_memory>"

    # 注入持仓上下文 + 估值上下文（同时构建 prebuilt_context 供 specialist 复用）
    prebuilt_context = ""
    try:
        from portfolio_context import build_portfolio_context, build_valuation_summary
        portfolio_ctx = build_portfolio_context()
        # 始终注入持仓上下文（空持仓时也会明确告知"无持仓"，防止 AI 编造）
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

    # 注入近期热点新闻到 prebuilt_context（同步调用，从缓存读取）
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

    # 语义化压缩历史消息（LLM 摘要 + 近期原文）
    history_budget = int(token_budget["total_context"] * token_budget["history_pct"])
    compressed_history = compress_history_semantic(history, max_tokens=history_budget)
    for msg in compressed_history:
        llm_messages.append({"role": msg["role"], "content": msg["content"]})

    # 当前用户问题（使用优化后的问题）
    llm_messages.append({"role": "user", "content": refined_query})

    MAX_TURNS = 3 if budget["mode"] == "conservative" else 6
    force_skip_cross_review = budget["mode"] == "conservative"
    specialist_results = []
    all_tool_calls = []

    for turn in range(MAX_TURNS):
        _check_timeout(start_time)
        try:
            response = _call_llm(
                caller="orchestrator",
                model=MODEL,
                messages=llm_messages,
                tools=build_orchestrator_tools(),
                tool_choice="auto",
                temperature=0.3,
                max_tokens=2000,
            )
        except Exception as e:
            err_msg = str(e)
            logger.error(f"Orchestrator LLM 调用异常 (turn {turn}): {err_msg}")
            if any(kw in err_msg.lower() for kw in ["tool", "function", "reasoning", "thinking"]):
                logger.warning("模型不兼容，回退到普通模式")
                return _fallback_orchestrate(query, history, rag_context)
            raise

        msg = response.choices[0].message

        # 没有工具调用 → 检查是否需要交叉审阅，然后给出最终回答
        if not msg.tool_calls:
            # Phase B: 交叉审阅（仅 complex 且 >=2 个专家且存在分歧时触发）
            if complexity == "complex" and len(specialist_results) >= 2 and not force_skip_cross_review and _detect_specialist_disagreement(specialist_results):
                logger.info(f"进入交叉审阅阶段，{len(specialist_results)} 个专家参与")
                peer_analyses = {sr["agent_key"]: sr["analysis"] for sr in specialist_results}
                cross_review_results = []
                # 快照原始专家列表，避免迭代时 append 导致无限循环
                original_specialists = list(specialist_results)
                for sr in original_specialists:
                    _check_cancel(cancel_event)
                    _check_timeout(start_time)
                    try:
                        cr_result = run_specialist_with_context(
                            sr["agent_key"], refined_query, peer_analyses, max_turns=2,
                            prebuilt_context=prebuilt_context
                        )
                        cross_review_results.append(cr_result)
                        specialist_results.append(cr_result)
                        all_tool_calls.extend(cr_result.get("tool_calls", []))
                    except Exception as e:
                        logger.error(f"交叉审阅 {sr['agent_key']} 失败: {e}")

                # 将交叉审阅结果追加到消息中，让 Orchestrator 做最终综合
                if cross_review_results:
                    cr_summary = "\n\n---\n\n".join(
                        f"【{cr['agent']}交叉审阅】\n{cr['analysis']}"
                        for cr in cross_review_results
                    )
                    llm_messages.append({
                        "role": "user",
                        "content": f"以下是各专家的交叉审阅结果，请结合 Phase A 和 Phase B 的分析，给出最终综合建议：\n\n{cr_summary}",
                    })
                    try:
                        response = _call_llm(
                            caller="orchestrator",
                            model=MODEL,
                            messages=llm_messages,
                            temperature=0.3,
                            max_tokens=2000,
                        )
                        answer = response.choices[0].message.content or ""
                    except Exception:
                        answer = msg.content or ""

                    # Phase C: 仲裁（高级模型最终裁决）
                    if should_arbitrate(complexity, specialist_results):
                        logger.info("进入仲裁阶段（Phase C）")
                        arb_result = run_arbitration(refined_query, specialist_results, rag_context)
                        specialist_results.append(arb_result)
                        answer = arb_result["analysis"]
                        all_tool_calls.append({"name": "arbitration", "arguments": {"query": refined_query},
                                               "result_preview": arb_result["analysis"][:300]})

                    duration_ms = int((time.time() - start_time) * 1000)
                    return {
                        "answer": answer,
                        "specialist_results": specialist_results,
                        "tool_calls": all_tool_calls,
                        "turns": turn + 1,
                        "duration_ms": duration_ms,
                        "complexity": complexity,
                        "cross_review": True,
                        "arbitration": should_arbitrate(complexity, specialist_results),
                    }

            answer = msg.content or ""

            # Phase C: 仲裁（高级模型最终裁决，无交叉审阅时也可触发）
            if should_arbitrate(complexity, specialist_results):
                logger.info("进入仲裁阶段（Phase C）")
                arb_result = run_arbitration(refined_query, specialist_results, rag_context)
                specialist_results.append(arb_result)
                answer = arb_result["analysis"]
                all_tool_calls.append({"name": "arbitration", "arguments": {"query": refined_query},
                                       "result_preview": arb_result["analysis"][:300]})

            duration_ms = int((time.time() - start_time) * 1000)
            return {
                "answer": answer,
                "specialist_results": specialist_results,
                "tool_calls": all_tool_calls,
                "turns": turn + 1,
                "duration_ms": duration_ms,
                "complexity": complexity,
                "arbitration": should_arbitrate(complexity, specialist_results),
            }

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
        tool_tasks = []
        for tc in msg.tool_calls:
            args = _parse_tool_args(tc.function.arguments, tc.function.name)
            expert_query = args.get("query", query)
            logger.info(f"Orchestrator → {tc.function.name}: {expert_query[:100]}")
            tool_tasks.append((tc, args, expert_query))

        if len(tool_tasks) == 1:
            # 单个专家，直接执行（避免线程池开销）
            tc, args, expert_query = tool_tasks[0]
            result_str = _execute_specialist(tc.function.name, expert_query,
                                              prebuilt_context=prebuilt_context)
            ordered_results = [result_str]
        else:
            # 多个专家，并行执行
            ordered_results = [None] * len(tool_tasks)
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(tool_tasks)) as executor:
                future_to_idx = {}
                for idx, (tc, args, expert_query) in enumerate(tool_tasks):
                    future = executor.submit(
                        _execute_specialist, tc.function.name, expert_query,
                        cancel_event=None, prebuilt_context=prebuilt_context
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

    # 超过最大轮次，做最后一次总结
    try:
        llm_messages.append({
            "role": "user",
            "content": "请根据以上各专家的分析结果，给出最终的综合投资建议。",
        })
        response = _call_llm(
            caller="orchestrator",
            model=MODEL,
            messages=llm_messages,
            temperature=0.3,
            max_tokens=2000,
        )
        final_answer = response.choices[0].message.content or ""
    except Exception:
        final_answer = "分析过程较长，请参考以上各专家的分析结果。"

    duration_ms = int((time.time() - start_time) * 1000)

    # 计算本次 token 用量（从数据库读取本次调用期间的记录）
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

    return {
        "answer": final_answer,
        "specialist_results": specialist_results,
        "tool_calls": all_tool_calls,
        "turns": MAX_TURNS,
        "duration_ms": duration_ms,
        "complexity": complexity,
        "token_usage": total_tokens,
    }


def orchestrate_stream(query: str, history: list, rag_context: str = "", cancel_event: threading.Event | None = None, resume_from: dict | None = None):
    """
    Orchestrator 的流式版本，通过生成器逐步返回事件。

    事件类型：
    - {"type": "specialist_start", "agent_key": "...", "agent": "...", "icon": "..."}
    - {"type": "specialist_done", "agent_key": "...", "agent": "...", "icon": "...", "analysis": "...", "duration_ms": ...}
    - {"type": "status", "message": "..."}
    - {"type": "answer_chunk", "content": "..."}
    - {"type": "answer", "content": "...", "specialist_results": [...], "tool_calls": [...], "complexity": "..."}

    参数:
        cancel_event: 可选的取消事件，设置后会尽快终止执行
        resume_from: 恢复数据，包含 message_id 用于查询已完成的 agent
    """
    start_time = time.time()

    # 0. Token 预算检查
    budget = check_token_budget()
    logger.info(f"Token 预算: {budget['used']}/{budget['limit']} ({budget['pct']:.0%}) mode={budget['mode']}")
    if budget["mode"] == "exceeded":
        yield {
            "type": "answer",
            "content": f"今日分析额度已用完（{budget['used']:,}/{budget['limit']:,} tokens），请明天再来。",
            "specialist_results": [],
            "tool_calls": [],
            "error": "token_budget_exceeded",
        }
        return

    # 0.5 恢复模式：从 agent_runs 表查询已完成的专家
    completed_specialists = set()
    resumed_results = []
    resume_message_id = resume_from.get("message_id") if resume_from else None
    if resume_message_id:
        completed_runs = get_completed_agents_for_message(resume_message_id)
        for run in completed_runs:
            completed_specialists.add(run["agent_key"])
            resumed_results.append({
                "agent_key": run["agent_key"],
                "agent": run["agent_name"],
                "analysis": run["result"] or "",
                "duration_ms": run["duration_ms"] or 0,
            })
        logger.info(f"恢复模式：已完成的专家 {completed_specialists}")
        if completed_specialists:
            yield {"type": "status", "message": f"正在恢复执行（{len(completed_specialists)} 个专家已完成）..."}

    # 1. 需求澄清（使用 LLM 分析问题）
    _check_cancel(cancel_event)
    if not resume_from:
        yield {"type": "status", "message": "正在理解您的问题..."}
    clarification = clarify_requirement(query)
    complexity = clarification["complexity"]
    context_config = get_context_config(complexity)
    token_budget = get_token_budget(complexity)
    logger.info(f"需求澄清: {clarification}")

    # 使用澄清后的问题（如果有优化）
    refined_query = clarification.get("refined_query", query)

    # 2. 根据复杂度优化上下文（Token 预算管理）
    system_content = build_orchestrator_system_prompt()

    # RAG 上下文（token 感知截断）
    rag_budget = int(token_budget["total_context"] * token_budget["rag_pct"])
    if rag_context and context_config["rag_enabled"]:
        compressed_rag = compress_rag_token_aware(rag_context, max_tokens=rag_budget)
        system_content += f"\n\n参考信息：\n{compressed_rag}"

    # 注入用户偏好画像（从反馈学习中积累）
    preference_ctx = get_preference_context("default")
    if preference_ctx:
        system_content += f"\n\n{preference_ctx}"

    # 注入跨对话用户记忆
    user_memory = build_user_memory_context("default")
    if user_memory:
        system_content += f"\n\n<user_memory>\n{user_memory}\n</user_memory>"

    # 注入持仓上下文 + 估值上下文（同时构建 prebuilt_context 供 specialist 复用）
    prebuilt_context = ""
    try:
        from portfolio_context import build_portfolio_context, build_valuation_summary
        portfolio_ctx = build_portfolio_context()
        # 始终注入持仓上下文（空持仓时也会明确告知"无持仓"，防止 AI 编造）
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

    # 注入近期热点新闻到 prebuilt_context（同步调用，从缓存读取）
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

    # 语义化压缩历史消息（LLM 摘要 + 近期原文）
    history_budget = int(token_budget["total_context"] * token_budget["history_pct"])
    compressed_history = compress_history_semantic(history, max_tokens=history_budget)
    for msg in compressed_history:
        llm_messages.append({"role": msg["role"], "content": msg["content"]})

    # 使用优化后的问题
    llm_messages.append({"role": "user", "content": refined_query})

    # 根据复杂度显示不同的状态消息
    if complexity == "simple":
        yield {"type": "status", "message": f"正在分析问题... ({clarification.get('reason', '')})"}
    elif complexity == "medium":
        yield {"type": "status", "message": f"正在咨询专家... ({clarification.get('reason', '')})"}
    else:
        yield {"type": "status", "message": f"正在协调多个专家... ({clarification.get('reason', '')})"}

    # 发送执行计划给前端
    yield {
        "type": "plan",
        "complexity": complexity,
        "reason": clarification.get("reason", ""),
        "refined_query": refined_query if refined_query != query else "",
    }

    MAX_TURNS = 3 if budget["mode"] == "conservative" else 6
    force_skip_cross_review = budget["mode"] == "conservative"
    specialist_results = []
    all_tool_calls = []

    # 恢复模式：添加已有结果
    if resumed_results:
        specialist_results.extend(resumed_results)
        logger.info(f"恢复模式：已加载 {len(resumed_results)} 个专家结果")

    for turn in range(MAX_TURNS):
        _check_cancel(cancel_event)
        _check_timeout(start_time)
        try:
            response = _call_llm(
                caller="orchestrator",
                model=MODEL,
                messages=llm_messages,
                tools=build_orchestrator_tools(),
                tool_choice="auto",
                temperature=0.3,
                max_tokens=2000,
            )
        except CancelledError:
            raise
        except Exception as e:
            err_msg = str(e)
            logger.error(f"Orchestrator LLM 调用异常 (turn {turn}): {err_msg}")
            if any(kw in err_msg.lower() for kw in ["tool", "function", "reasoning", "thinking"]):
                logger.warning("模型不兼容，回退到普通模式")
                yield {"type": "status", "message": "模型不支持工具调用，切换到普通模式..."}
                result = _fallback_orchestrate(query, history, rag_context)
                yield {
                    "type": "answer",
                    "content": result["answer"],
                    "specialist_results": [],
                    "tool_calls": [],
                }
                return
            raise

        msg = response.choices[0].message

        # 没有工具调用 → 检查是否需要交叉审阅，然后给出最终回答
        if not msg.tool_calls:
            # Phase B: 交叉审阅（从 orchestration_config 读取配置）
            cross_review_enabled = get_orchestration_config("cross_review_enabled", "true") == "true"
            cross_review_min = int(get_orchestration_config("cross_review_min_specialists", "2"))
            cross_review_trigger = get_orchestration_config("cross_review_trigger", "disagreement")
            should_cross_review = (
                cross_review_enabled
                and not force_skip_cross_review
                and len(specialist_results) >= cross_review_min
                and (
                    cross_review_trigger == "always"
                    or (cross_review_trigger == "disagreement" and _detect_specialist_disagreement(specialist_results))
                )
            )
            if should_cross_review:
                yield {"type": "status", "message": f"正在进行交叉审阅（{len(specialist_results)} 个专家互相验证）..."}
                peer_analyses = {sr["agent_key"]: sr["analysis"] for sr in specialist_results}
                cross_review_results = []
                # 快照原始专家列表，避免迭代时 append 导致无限循环
                original_specialists = list(specialist_results)
                for sr in original_specialists:
                    _check_cancel(cancel_event)
                    _check_timeout(start_time)
                    yield {
                        "type": "cross_review_start",
                        "agent_key": sr["agent_key"],
                        "agent": sr["agent"],
                        "icon": sr["icon"],
                    }
                    try:
                        cr_result = run_specialist_with_context(
                            sr["agent_key"], refined_query, peer_analyses, max_turns=2,
                            prebuilt_context=prebuilt_context
                        )
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

                # 将交叉审阅结果追加到消息中，做最终综合
                if cross_review_results:
                    _check_cancel(cancel_event)
                    yield {"type": "status", "message": "正在综合所有分析结果..."}
                    cr_summary = "\n\n---\n\n".join(
                        f"【{cr['agent']}交叉审阅】\n{cr['analysis']}"
                        for cr in cross_review_results
                    )
                    llm_messages.append({
                        "role": "user",
                        "content": f"以下是各专家的交叉审阅结果，请结合 Phase A 和 Phase B 的分析，给出最终综合建议：\n\n{cr_summary}",
                    })
                    try:
                        response = _call_llm(
                            caller="orchestrator",
                            model=MODEL,
                            messages=llm_messages,
                            temperature=0.3,
                            max_tokens=2000,
                        )
                        answer = response.choices[0].message.content or ""
                    except Exception:
                        answer = msg.content or ""

                    # Phase C: 仲裁（高级模型最终裁决）
                    if should_arbitrate(complexity, specialist_results):
                        _check_cancel(cancel_event)
                        yield {"type": "status", "message": "正在由仲裁法官做最终裁决..."}
                        yield {
                            "type": "specialist_start",
                            "agent_key": "arbitrator",
                            "agent": "仲裁法官",
                            "icon": "⚖️",
                        }
                        arb_result = run_arbitration(refined_query, specialist_results, rag_context)
                        specialist_results.append(arb_result)
                        all_tool_calls.append({"name": "arbitration", "arguments": {"query": refined_query},
                                               "result_preview": arb_result["analysis"][:300]})
                        answer = arb_result["analysis"]
                        yield {
                            "type": "specialist_done",
                            "agent_key": "arbitrator",
                            "agent": "仲裁法官",
                            "icon": "⚖️",
                            "analysis": arb_result["analysis"],
                            "duration_ms": arb_result["duration_ms"],
                            "is_arbitration": True,
                        }

                    duration_ms = int((time.time() - start_time) * 1000)
                    yield {
                        "type": "answer",
                        "content": answer,
                        "specialist_results": specialist_results,
                        "tool_calls": all_tool_calls,
                        "duration_ms": duration_ms,
                        "complexity": complexity,
                        "cross_review": True,
                        "arbitration": should_arbitrate(complexity, specialist_results),
                    }
                    return

            answer = msg.content or ""

            # Phase C: 仲裁（高级模型最终裁决，无交叉审阅时也可触发）
            if should_arbitrate(complexity, specialist_results):
                _check_cancel(cancel_event)
                yield {"type": "status", "message": "正在由仲裁法官做最终裁决..."}
                yield {
                    "type": "specialist_start",
                    "agent_key": "arbitrator",
                    "agent": "仲裁法官",
                    "icon": "⚖️",
                }
                arb_result = run_arbitration(refined_query, specialist_results, rag_context)
                specialist_results.append(arb_result)
                all_tool_calls.append({"name": "arbitration", "arguments": {"query": refined_query},
                                       "result_preview": arb_result["analysis"][:300]})
                answer = arb_result["analysis"]
                yield {
                    "type": "specialist_done",
                    "agent_key": "arbitrator",
                    "agent": "仲裁法官",
                    "icon": "⚖️",
                    "analysis": arb_result["analysis"],
                    "duration_ms": arb_result["duration_ms"],
                    "is_arbitration": True,
                }

            duration_ms = int((time.time() - start_time) * 1000)
            yield {
                "type": "answer",
                "content": answer,
                "specialist_results": specialist_results,
                "tool_calls": all_tool_calls,
                "duration_ms": duration_ms,
                "arbitration": should_arbitrate(complexity, specialist_results),
            }
            return

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

        # 解析所有 tool call 参数
        tool_tasks = []
        skipped_tasks = []
        for tc in msg.tool_calls:
            args = _parse_tool_args(tc.function.arguments, tc.function.name)
            expert_query = args.get("query", query)
            agent_key = build_expert_map().get(tc.function.name, "")
            agent_info = load_specialist_agents().get(agent_key, {})

            # 恢复模式：跳过已完成的专家
            if agent_key in completed_specialists:
                logger.info(f"跳过已完成的专家: {agent_key}")
                skipped_tasks.append((tc, args, expert_query, agent_key, agent_info))
                continue

            logger.info(f"Orchestrator → {tc.function.name}: {expert_query[:100]}")
            tool_tasks.append((tc, args, expert_query, agent_key, agent_info))

        # 恢复模式：为跳过的专家添加 tool response
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

        # 如果所有专家都已完成，直接进入下一轮（让 LLM 综合结果）
        if not tool_tasks:
            yield {"type": "status", "message": "所有专家已完成，正在综合结果..."}
            continue

        # 创建 pending 状态的 agent 执行记录
        agent_run_ids = {}
        for tc, args, expert_query, agent_key, agent_info in tool_tasks:
            run_id = create_pending_agent_run(
                conversation_id=conv_id,
                message_id=stream_msg_id,
                agent_key=agent_key,
                agent_name=agent_info.get("name", tc.function.name),
                query=expert_query[:500],
                trace_id=trace_id,
            )
            agent_run_ids[agent_key] = run_id

        # 通知前端：专家开始工作
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
            """线程回调：专家完成后将结果放入队列。"""
            try:
                result_str = future.result()
            except CancelledError:
                result_str = json.dumps({"error": "cancelled"}, ensure_ascii=False)
            except Exception as e:
                result_str = json.dumps({"error": str(e)}, ensure_ascii=False)
            result_queue.put((idx, tc, args, agent_key, agent_info, result_str))

        if len(tool_tasks) == 1:
            # 单个专家，直接执行
            tc, args, expert_query, agent_key, agent_info = tool_tasks[0]
            result_str = _execute_specialist(tc.function.name, expert_query, cancel_event,
                                              prebuilt_context=prebuilt_context)
            result_queue.put((0, tc, args, agent_key, agent_info, result_str))
        else:
            # 多个专家，并行执行
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(tool_tasks)) as executor:
                for idx, (tc, args, expert_query, agent_key, agent_info) in enumerate(tool_tasks):
                    future = executor.submit(
                        _execute_specialist, tc.function.name, expert_query,
                        cancel_event=cancel_event, prebuilt_context=prebuilt_context
                    )
                    future.add_done_callback(
                        lambda f, idx=idx, tc=tc, args=args, ak=agent_key, ai=agent_info:
                        _on_specialist_complete(idx, tc, args, ak, ai, f)
                    )

        # 收集结果，yield specialist_done 事件
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
                specialist_results.append(specialist_result)

                # 更新 agent 执行记录为 completed
                update_agent_run_status(
                    agent_run_ids.get(agent_key),
                    "completed",
                    result=result_data.get("analysis", "")[:2000],
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
                update_agent_run_status(
                    agent_run_ids.get(agent_key),
                    "failed",
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

    # 超过最大轮次，做最后一次总结
    _check_cancel(cancel_event)
    try:
        llm_messages.append({
            "role": "user",
            "content": "请根据以上各专家的分析结果，给出最终的综合投资建议。",
        })
        response = _call_llm(
            caller="orchestrator",
            model=MODEL,
            messages=llm_messages,
            temperature=0.3,
            max_tokens=2000,
        )
        final_answer = response.choices[0].message.content or ""
    except CancelledError:
        raise
    except Exception:
        final_answer = "分析过程较长，请参考以上各专家的分析结果。"

    duration_ms = int((time.time() - start_time) * 1000)

    yield {
        "type": "answer",
        "content": final_answer,
        "specialist_results": specialist_results,
        "tool_calls": all_tool_calls,
        "duration_ms": duration_ms,
        "complexity": complexity,
    }


def _fallback_orchestrate(query: str, history: list, rag_context: str = "") -> dict:
    """当模型不支持 function calling 时，回退到普通对话模式。"""
    from llm_service import chat_with_agent

    answer = chat_with_agent(build_orchestrator_system_prompt(), history + [{"role": "user", "content": query}], rag_context)
    return {
        "answer": answer,
        "specialist_results": [],
        "tool_calls": [],
        "turns": 1,
        "fallback": True,
    }
