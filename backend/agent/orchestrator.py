"""Orchestrator — 主控 Agent，协调各专家 Agent 完成分析"""

import json
import logging
import re
import time
import threading
import concurrent.futures
import asyncio

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
from db.config import get_config_int, get_config_float
from agent.orchestrator_optimizer import OrchestratorOptimizer, ParallelExecutor

# 全局超时限制（秒）
MAX_ORCHESTRATION_SECONDS = 1800  # 30 分钟
from agent.feedback_learner import get_preference_context
from agent.memory import (
    compress_history_semantic, build_user_memory_context,
    get_token_budget, compress_rag_token_aware, estimate_tokens,
)

logger = logging.getLogger(__name__)


# ── 文章缓存（避免 orchestrator 预抓取与 agent 工具双重抓取） ──
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
    """同步获取文章内容（用于 orchestrator 中）。优先使用缓存。"""
    # 先查缓存
    if url in _article_cache:
        logger.info(f"文章缓存命中: {url}")
        return _article_cache[url]

    try:
        from article_reader import fetch_article
        # 在后台线程中创建新的事件循环运行异步函数
        # （不能用 asyncio.get_event_loop()，后台线程没有默认事件循环）
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(fetch_article(url))
        except Exception as e:
            logger.warning(f"获取文章失败: {e}")
            return None
        finally:
            loop.close()

        # 缓存成功结果
        if result:
            _cache_article(url, result)
        return result
    except ImportError:
        logger.warning("article_reader 模块未安装")
        return None


def enrich_query_with_article(query: str) -> tuple[str, str]:
    """
    检测查询中的链接，抓取文章内容并注入到查询中。

    返回: (enriched_query, article_context)
    article_context 为失败提示时以 "[抓取失败]" 开头。
    """
    urls = detect_urls(query)
    if not urls:
        return query, ""

    # 只处理第一个链接
    url = urls[0]
    logger.info(f"检测到链接: {url}，正在抓取文章内容...")

    article = fetch_article_content(url)
    if not article:
        logger.warning(f"文章抓取失败: {url}")
        fail_hint = (
            f"[抓取失败] 无法获取链接内容: {url}\n"
            "可能原因：链接已失效、被防爬机制拦截、或非文章页面。\n"
            "请引导用户：1) 检查链接是否可正常打开 2) 直接粘贴文章正文。"
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

    # 截取策略：前 2000 + 后 1500，保留开头背景和结尾结论
    max_chars = 3500
    if len(content) > max_chars:
        head = content[:2000]
        tail = content[-1500:]
        content = f"{head}\n\n...（中间内容省略）...\n\n{tail}"

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

    # 注入查询，明确告知 agent 已提供文章内容，避免重复调用 fetch_article
    enriched_query = (
        f"{query}\n\n"
        "请参考以下文章内容进行分析（文章已抓取完毕，无需再次调用 fetch_article 工具）：\n"
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
    # 使用优化器的快速检测
    return not OrchestratorOptimizer.should_skip_cross_review(specialist_results, "complex")


def should_arbitrate(complexity: str, specialist_results: list) -> bool:
    """判断是否需要仲裁 Agent 介入。

    条件（从 orchestration_config 读取）：
    - arbitration_enabled == "true"
    - ARBITRATION_API_KEY 已配置
    - complexity >= arbitration_complexity
    - ≥2 个专家参与分析
    """
    # 使用优化器的快速检测
    if OrchestratorOptimizer.should_skip_arbitration(specialist_results, complexity):
        return False

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

## 复杂度判断规则

### chat - 闲聊/科普（不调用任何专家）
- 纯问候/感谢/道歉（"你好"、"谢谢"）
- 概念解释（"什么是PE"、"解释定投"）
- 与投资无关的问题（"今天天气"）

### simple - 单一数据查询（1个专家）
- 查询单一指标（"沪深300估值"、"债市温度"）
- 查询持仓概况（"我持有什么"）

### medium - 分析任务（1个专家）
- **对比分析**（"A和B区别"、"A和B哪个好"）→ 只用 1 个估值专家
- 单一维度深度分析（"白酒估值高吗"、"适合买债券吗"）
- **"可以买吗"/"值得买吗"** → 只问估值判断
- 持仓诊断（"我的持仓健康吗"）
- 简单建议（"买点货币基金可以吗"）

### complex - 综合决策（2+个专家）
- 需要多维度分析（估值+配置+风险）
- 涉及具体操作建议（"帮我做个定投方案"）
- 多市场联动分析（"美股大跌对A股影响"）

## 关键规则
1. **对比类问题只用 1 个专家**，不要触发多 agent
2. **"可以买吗"是 medium**，不是 complex
3. **简单建议类是 medium**，只需 1 个专家

## 输出格式（只输出JSON）
{{"complexity":"chat|simple|medium|complex","specialists":["expert1"],"reason":"判断原因","refined_query":"优化后的查询","confidence":0.95}}

- specialists 中的值必须是：{keys_json}，chat 时为空数组
- confidence 低于 0.7 时系统会降级处理

## 示例

Q: 你好
A: {{"complexity":"chat","specialists":[],"reason":"纯问候","refined_query":"你好","confidence":0.99}}

Q: 沪深300估值多少
A: {{"complexity":"simple","specialists":["valuation_expert"],"reason":"单一指数估值查询","refined_query":"沪深300当前PE/PB估值和百分位","confidence":0.95}}

Q: 红利质量和中证红利有什么区别
A: {{"complexity":"medium","specialists":["valuation_expert"],"reason":"对比分析，只需1个估值专家","refined_query":"红利质量和中证红利的估值对比（PE/PB/百分位/股息率）","confidence":0.90}}

Q: 恒生科技怎么样，可以买吗
A: {{"complexity":"medium","specialists":["valuation_expert"],"reason":"估值判断，不需要多专家","refined_query":"恒生科技指数估值水平和投资建议","confidence":0.90}}

Q: 帮我做个定投方案
A: {{"complexity":"complex","specialists":["valuation_expert","allocation_advisor"],"reason":"定投需要估值+配置策略","refined_query":"基于当前估值的定投方案","confidence":0.92}}

Q: 美股大跌，A股明天会怎么走
A: {{"complexity":"complex","specialists":["market_analyst","valuation_expert","risk_assessor"],"reason":"多市场联动分析，需要多维分析","refined_query":"美股大跌原因、A股走势预判及持仓影响","confidence":0.90}}"""


# ── 基于规则的复杂度预判（零 LLM 调用）────────────────────────

def _classify_complexity_by_rules(query: str, has_portfolio: bool = False, has_watchlist: bool = False) -> str:
    """基于规则预判用户问题的复杂度，避免 LLM 调用。

    返回: "chat" | "simple" | "medium" | "complex"

    设计原则：
    - simple 和 complex 直接走规则（确定性高）
    - medium 需要 LLM 确认（边界情况多）
    """
    text = (query or "").strip()
    if not text:
        return "chat"

    text_lower = text.lower()
    length = len(text)

    # ── 闲聊检测（短消息 + 闲聊关键词 + 无投资内容）──
    chat_keywords = ["你好", "谢谢", "好的", "明白了", "知道了", "嗯",
                     "天气", "笑话", "故事", "晚安", "早上好", "嗨", "hi", "hello", "hey"]
    # 纯问候/感谢/闲聊
    if length <= 10 and any(kw in text_lower for kw in chat_keywords):
        # 确保没有投资关键词
        invest_markers = ["估值", "PE", "PB", "持仓", "买入", "卖出", "基金",
                          "股票", "债券", "定投", "配置", "风险", "收益"]
        if not any(m in text_lower for m in invest_markers):
            return "chat"

    # 极短消息（<=5字），无投资关键词，无疑问词
    if length <= 5:
        has_question = bool(re.search(r'[吗呢？?]', text))
        invest_markers = ["估值", "pe", "pb", "持仓", "买入", "卖出", "基金",
                          "股票", "债券", "债市", "定投", "配置"]
        concept_markers = ["什么是", "解释", "原理", "概念", "定义", "含义"]
        if not has_question and not any(m in text_lower for m in invest_markers) \
                and not any(m in text_lower for m in concept_markers):
            return "chat"

    # ── Simple 检测（短查询 + 简单关键词）──
    simple_keywords = ["什么是", "解释", "价格", "多少", "是多少", "查一下",
                       "查询", "最新", "今天", "估值", "百分位", "PE", "PB",
                       "z-score", "债市温度", "温度"]
    # 强制 simple 的前缀关键词（解释/定义类问题，即使包含投资术语也是 simple）
    force_simple_prefixes = ["什么是", "解释", "怎么算", "概念", "原理", "含义"]
    has_force_simple = any(text_lower.startswith(p) for p in force_simple_prefixes)
    has_simple = any(kw in text_lower for kw in simple_keywords)
    if length < 30 and has_simple:
        # 排除：虽然短但包含复杂意图（但强制 simple 前缀跳过排除）
        if not has_force_simple:
            complex_markers = ["分析", "对比", "比较", "建议", "配置", "风险",
                               "方案", "策略", "计划", "加仓", "减仓", "定投"]
            if any(m in text_lower for m in complex_markers):
                # 有疑问词 + 估值关键词 → 可能需要分析，交给 LLM 确认
                has_question = bool(re.search(r'[吗呢？?]', text))
                if has_question and any(kw in text_lower for kw in ["估值", "高", "低", "贵", "便宜"]):
                    return "medium"  # 边界情况，交给 LLM
                return "medium"  # 有复杂意图，交给 LLM
        # 有疑问词 + 估值关键词 → 可能需要分析，交给 LLM 确认
        has_question = bool(re.search(r'[吗呢？?]', text))
        if has_question and any(kw in text_lower for kw in ["估值", "高", "低", "贵", "便宜"]):
            return "medium"  # 边界情况，交给 LLM
        return "simple"

    # ── Complex 检测（长查询 + 多个投资关键词 + 有持仓数据）──
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

    # ── 未命中规则 → 返回 medium（需要 LLM 确认）──
    return "medium"


# Clarification 结果缓存（相同查询直接返回缓存结果，节省 2-5s LLM 调用）
_clarification_cache: dict[int, dict] = {}
_CLARIFICATION_CACHE_MAX = 128


def detect_scenario_type(query: str) -> str:
    """确定性识别投资问题场景，用于模板、RAG 和评测分流。"""
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


def clarify_requirement(query: str) -> dict:
    """
    分析用户问题，返回需求澄清结果。

    优化策略：先走规则预判（零 LLM 调用），仅在边界情况（medium）时调用 LLM。
    预期节省 2-3 秒首响时间（>70% 的查询可跳过 LLM）。

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

    # ── Step 1: 规则预判（零 LLM 调用）──
    has_portfolio = False
    has_watchlist = False
    try:
        from portfolio_context import build_portfolio_summary_line
        portfolio_line = build_portfolio_summary_line()
        # 如果持仓摘要不是"无持仓"，说明有持仓数据
        has_portfolio = bool(portfolio_line and "无持仓" not in portfolio_line)
    except Exception:
        portfolio_line = ""

    try:
        from db.portfolio import get_watchlist
        watchlist = get_watchlist("default")
        has_watchlist = bool(watchlist)
    except Exception:
        pass

    rule_complexity = _classify_complexity_by_rules(query, has_portfolio, has_watchlist)
    logger.info(f"规则预判复杂度: {rule_complexity} (query={query[:50]}..., portfolio={has_portfolio}, watchlist={has_watchlist})")

    # ── Step 2: 非 medium 结果直接走规则路径（跳过 LLM）──
    if rule_complexity in ("chat", "simple", "complex"):
        specialists = route_to_specialists_by_keywords(query) if rule_complexity != "chat" else []
        result_out = {
            "complexity": rule_complexity,
            "specialists": specialists,
            "reason": f"规则预判（{rule_complexity}）",
            "refined_query": query,
            "confidence": 0.85,  # 规则预判置信度
            "scenario_type": detect_scenario_type(query),
            "classification_method": "rules",
        }
        # 缓存结果
        if len(_clarification_cache) >= _CLARIFICATION_CACHE_MAX:
            _clarification_cache.pop(next(iter(_clarification_cache)))
        _clarification_cache[cache_key] = result_out
        return result_out

    # ── Step 3: medium 结果 → 调用 LLM 确认（保留原逻辑）──
    logger.info(f"规则返回 medium，调用 LLM 确认: {query[:50]}...")

    try:
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
            temperature=get_config_float('llm.temperature_vision', 0.1),
            max_tokens=get_config_int('llm.max_tokens_orchestrator', 8192),
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
            "scenario_type": detect_scenario_type(query),
            "classification_method": "llm",
        }

        # 缓存结果
        if len(_clarification_cache) >= _CLARIFICATION_CACHE_MAX:
            _clarification_cache.pop(next(iter(_clarification_cache)))
        _clarification_cache[cache_key] = result_out

        return result_out

    except Exception as e:
        logger.warning(f"LLM 澄清失败，回退到关键词匹配: {e}")
        # 回退到关键词匹配
        complexity = detect_complexity_by_keywords(query)
        specialists = route_to_specialists_by_keywords(query)
        return {
            "complexity": complexity,
            "specialists": specialists,
            "reason": "关键词匹配（LLM澄清失败）",
            "refined_query": query,
            "confidence": 0.5,
            "scenario_type": detect_scenario_type(query),
            "classification_method": "keywords_fallback",
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

    # 链接检测 → 文章解读专家
    if detect_urls(query):
        specialists.append("article_expert")
        # 如果只是链接+简单指令，只用文章专家
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

    # 行为偏差关键词 → 行为金融辅导师
    behavior_keywords = [
        "追涨", "杀跌", "恐慌", "很慌", "焦虑", "忍不住", "冲动",
        "补亏", "回本", "重仓", "满仓", "梭哈", "频繁交易", "踏空",
    ]
    if any(kw in query for kw in behavior_keywords):
        specialists.append("behavior_coach")

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

    # 高风险行动建议 → 反方观点审查员，必要时补风险评估和行为教练
    action_keywords = [
        "买入", "加仓", "建仓", "卖出", "减仓", "清仓", "追涨", "重仓",
        "满仓", "梭哈", "可以买吗", "要不要买", "要不要卖",
    ]
    if any(kw in query for kw in action_keywords):
        if "counter_argument" not in specialists:
            specialists.append("counter_argument")
        if "risk_assessor" not in specialists:
            specialists.append("risk_assessor")
    if any(kw in query for kw in ["追涨", "杀跌", "恐慌", "很慌", "重仓", "满仓", "梭哈", "冲动"]):
        if "behavior_coach" not in specialists:
            specialists.append("behavior_coach")

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
    "behavior_coach": {
        "query_suffix": "行为金融 心理偏差 追涨杀跌 损失厌恶 过度自信 情绪",
        "content_types": ["book", "analysis"],
    },
    "counter_argument": {
        "query_suffix": "反方观点 反例 失效条件 风险边界 决策清单 安全边际",
        "content_types": ["book", "analysis", "valuation"],
    },
}


def build_scenario_rag_context(query: str, specialists: list[str],
                                original_rag_context: str = "") -> str:
    """
    根据命中的专家类型，对原始 RAG 上下文做场景化增强。
    如果原始 RAG 已有内容，补充场景化检索结果；否则全新构建。
    """
    from rag import build_rag_context_with_details

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

    # 构建场景化查询：原始问题 + 场景关键词
    scenario_query = f"{query} {' '.join(scenario_queries)}"

    # 场景化检索（限制 3 条，避免过多）
    result = build_rag_context_with_details(
        scenario_query,
        content_types=list(all_content_types) if all_content_types else None,
        limit=3,
    )
    scenario_context = result.get("context", "")

    # 合并：原始 RAG + 场景化 RAG
    if original_rag_context and scenario_context:
        return f"{original_rag_context}\n\n---\n\n{scenario_context}"
    return scenario_context or original_rag_context


def get_context_config(complexity: str) -> dict:
    """根据复杂度返回上下文配置。"""
    if complexity == "simple":
        return {
            "history_limit": 3,      # 只保留最近3条历史
            "rag_enabled": True,     # 简单查询也启用 RAG（轻量级）
            "max_specialists": 1,    # 只调用1个专家
            "rag_max_chars": 800,    # 轻量级 RAG 上下文
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


def orchestrate(query: str, history: list, rag_context: str = "", cancel_event: threading.Event | None = None, target_specialists: list[str] = None) -> dict:
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

    # 1. 需求澄清（使用 LLM 分析问题）
    if target_specialists:
        all_agents = load_specialist_agents()
        valid_specialists = [s for s in target_specialists if s in all_agents]
        if valid_specialists:
            clarification = {
                "complexity": "simple" if len(valid_specialists) == 1 else "medium",
                "specialists": valid_specialists,
                "reason": f"用户通过 @mention 指定了专家: {', '.join(valid_specialists)}",
                "refined_query": query,
                "confidence": 1.0,
            }
            complexity = clarification["complexity"]
            context_config = get_context_config(complexity)
            token_budget = get_token_budget(complexity)
            refined_query = query
            specialists = valid_specialists
            logger.info(f"@mention 指定专家: {valid_specialists}")
        else:
            logger.warning(f"@mention 指定了无效的 agent_key: {target_specialists}，回退到自动路由")
            target_specialists = None

    if not target_specialists:
        clarification = clarify_requirement(query)
        complexity = clarification["complexity"]
        context_config = get_context_config(complexity)
        token_budget = get_token_budget(complexity)
        logger.info(f"需求澄清: {clarification}")

        # 使用澄清后的问题（如果有优化）
        refined_query = clarification.get("refined_query", query)
        specialists = clarification.get("specialists", [])

    # 1.5 场景化 RAG 增强：根据命中的专家类型补充相关书籍知识
    rag_context = build_scenario_rag_context(refined_query, specialists, rag_context)

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

    # 注入 KYC 理财画像（让编排器基于用户画像做路由决策）
    from agent.kyc import kyc_profile_to_text
    kyc_text = kyc_profile_to_text("default")
    if kyc_text:
        system_content += f"\n\n{kyc_text}"

    # 注入跨对话用户记忆
    user_memory = build_user_memory_context("default")
    if user_memory:
        system_content += f"\n\n<user_memory>\n{user_memory}\n</user_memory>"

    # 注入持仓上下文 + 估值上下文（同时构建 prebuilt_context 供 specialist 复用）
    prebuilt_context = ""

    # 注入 RAG 知识库上下文到 prebuilt_context（让专家也能参考书籍/文章知识）
    if rag_context:
        compressed_rag_for_specialist = compress_rag_token_aware(rag_context, max_tokens=get_config_int('llm.max_tokens_rag_compress', 1500))
        prebuilt_context += f"## 知识库参考（书籍/文章/技能）\n{compressed_rag_for_specialist}\n\n"

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
    arbitration_done = False  # 标记仲裁是否已完成，避免重复调用

    for turn in range(MAX_TURNS):
        _check_timeout(start_time)
        try:
            response = _call_llm(
                caller="orchestrator",
                model=MODEL,
                messages=llm_messages,
                tools=build_orchestrator_tools(),
                tool_choice="auto",
                temperature=get_config_float('llm.temperature_agent', 0.3),
                max_tokens=get_config_int('llm.max_tokens_orchestrator', 8192),
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
                            temperature=get_config_float('llm.temperature_agent', 0.3),
                            max_tokens=get_config_int('llm.max_tokens_orchestrator', 8192),
                        )
                        answer = response.choices[0].message.content or ""
                    except Exception:
                        answer = msg.content or ""

                    # Phase C: 仲裁（高级模型最终裁决）
                    arbitration_done = False
                    if should_arbitrate(complexity, specialist_results):
                        logger.info("进入仲裁阶段（Phase C）")
                        arb_result = run_arbitration(refined_query, specialist_results, rag_context)
                        specialist_results.append(arb_result)
                        answer = arb_result["analysis"]
                        all_tool_calls.append({"name": "arbitration", "arguments": {"query": refined_query},
                                               "result_preview": arb_result["analysis"][:300]})
                        arbitration_done = True

                    duration_ms = int((time.time() - start_time) * 1000)
                    return {
                        "answer": answer,
                        "specialist_results": specialist_results,
                        "tool_calls": all_tool_calls,
                        "turns": turn + 1,
                        "duration_ms": duration_ms,
                        "complexity": complexity,
                        "cross_review": True,
                        "arbitration": arbitration_done,
                    }

            answer = msg.content or ""

            # Phase C: 仲裁（高级模型最终裁决，无交叉审阅时也可触发）
            if not arbitration_done and should_arbitrate(complexity, specialist_results):
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
                "arbitration": arbitration_done,
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
            temperature=get_config_float('llm.temperature_agent', 0.3),
            max_tokens=get_config_int('llm.max_tokens_orchestrator', 8192),
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


def orchestrate_stream(query: str, history: list, rag_context: str = "", cancel_event: threading.Event | None = None, resume_from: dict | None = None, conversation_id: int = 0, message_id: int = 0, trace_id: str = "", target_specialists: list[str] = None):
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
        conversation_id: 对话 ID，用于创建 agent_runs 记录
        message_id: 消息 ID，用于创建 agent_runs 记录
        trace_id: 追踪 ID，用于关联执行记录
    """
    start_time = time.time()

    # 性能监控
    perf_metrics = {
        "start_time": start_time,
        "phases": {},
    }

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

    # 0.3 链接检测与文章抓取
    article_context = ""
    article_fetch_failed = False
    if detect_urls(query):
        yield {"type": "status", "message": "检测到链接，正在抓取文章内容..."}
        query, article_context = enrich_query_with_article(query)
        if article_context:
            if article_context.startswith("[抓取失败]"):
                article_fetch_failed = True
                logger.warning(f"文章抓取失败: {article_context}")
                yield {"type": "status", "message": "文章链接无法访问，将尝试分析，请稍候..."}
            else:
                logger.info(f"已注入文章内容到查询中")
                yield {"type": "status", "message": "文章内容已获取，正在分析..."}

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

    # 如果用户通过 @mention 指定了专家，跳过自动路由
    if target_specialists:
        all_agents = load_specialist_agents()
        valid_specialists = [s for s in target_specialists if s in all_agents]
        if valid_specialists:
            specialist_names = [all_agents[s]["name"] for s in valid_specialists]
            yield {"type": "status", "message": f"已指定专家：{'、'.join(specialist_names)}"}
            clarification = {
                "complexity": "simple" if len(valid_specialists) == 1 else "medium",
                "specialists": valid_specialists,
                "reason": f"用户通过 @mention 指定了专家: {', '.join(valid_specialists)}",
                "refined_query": query,
                "confidence": 1.0,
            }
            complexity = clarification["complexity"]
            context_config = get_context_config(complexity)
            token_budget = get_token_budget(complexity)
            refined_query = query
            specialists = valid_specialists
            logger.info(f"@mention 指定专家: {valid_specialists}")
        else:
            # 指定的 agent_key 无效，回退到自动路由
            logger.warning(f"@mention 指定了无效的 agent_key: {target_specialists}，回退到自动路由")
            target_specialists = None

    if not target_specialists:
        if not resume_from:
            yield {"type": "status", "message": "正在理解您的问题..."}
        clarification = clarify_requirement(query)
        complexity = clarification["complexity"]
        context_config = get_context_config(complexity)
        token_budget = get_token_budget(complexity)
        logger.info(f"需求澄清: {clarification}")

        # 使用澄清后的问题（如果有优化）
        refined_query = clarification.get("refined_query", query)
        specialists = clarification.get("specialists", [])

    # 性能监控：需求澄清耗时
    perf_metrics["phases"]["clarification"] = int((time.time() - start_time) * 1000)

    # 1.5 场景化 RAG 增强：根据命中的专家类型补充相关书籍知识
    rag_context = build_scenario_rag_context(refined_query, specialists, rag_context)

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

    # 注入 KYC 理财画像（让编排器基于用户画像做路由决策）
    from agent.kyc import kyc_profile_to_text
    kyc_text = kyc_profile_to_text("default")
    if kyc_text:
        system_content += f"\n\n{kyc_text}"

    # 注入跨对话用户记忆
    user_memory = build_user_memory_context("default")
    if user_memory:
        system_content += f"\n\n<user_memory>\n{user_memory}\n</user_memory>"

    # 注入持仓上下文 + 估值上下文（同时构建 prebuilt_context 供 specialist 复用）
    prebuilt_context = ""

    # 注入 RAG 知识库上下文到 prebuilt_context（让专家也能参考书籍/文章知识）
    if rag_context:
        compressed_rag_for_specialist = compress_rag_token_aware(rag_context, max_tokens=get_config_int('llm.max_tokens_rag_compress', 1500))
        prebuilt_context += f"## 知识库参考（书籍/文章/技能）\n{compressed_rag_for_specialist}\n\n"

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
        "scenario_type": clarification.get("scenario_type", detect_scenario_type(query)),
        "reason": clarification.get("reason", ""),
        "refined_query": refined_query if refined_query != query else "",
    }

    MAX_TURNS = 3 if budget["mode"] == "conservative" else 6
    force_skip_cross_review = budget["mode"] == "conservative"
    specialist_results = []
    all_tool_calls = []
    arbitration_done = False  # 标记仲裁是否已完成，避免重复调用

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
                temperature=get_config_float('llm.temperature_agent', 0.3),
                max_tokens=get_config_int('llm.max_tokens_orchestrator', 8192),
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

                # 优化：跳过中间的综合 LLM 调用，直接进入仲裁
                if cross_review_results:
                    _check_cancel(cancel_event)
                    # 直接使用交叉审阅结果，不进行额外的 LLM 调用
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
                        arbitration_done = True
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
                        "arbitration": arbitration_done,
                    }
                    return

            answer = msg.content or ""

            # Phase C: 仲裁（高级模型最终裁决，无交叉审阅时也可触发）
            if not arbitration_done and should_arbitrate(complexity, specialist_results):
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
                arbitration_done = True
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
                "arbitration": arbitration_done,
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
                conversation_id=conversation_id,
                message_id=message_id,
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
        # 注入 KYC 画像，让最终综合答案"懂用户"（轻量主控综合层）
        try:
            from agent.kyc import kyc_profile_to_text
            kyc_text = kyc_profile_to_text("default")
            if kyc_text:
                llm_messages.append({"role": "system", "content": f"在综合各专家意见给出最终建议时，请务必结合用户的投资画像：\n{kyc_text}"})
        except Exception:
            pass
        llm_messages.append({
            "role": "user",
            "content": "请根据以上各专家的分析结果，给出最终的综合投资建议。",
        })
        final_answer = ""
        # 流式生成：逐 chunk 推送思考过程 + 答案增量
        try:
            from llm_service import _call_llm_stream
            for chunk in _call_llm_stream(
                caller="orchestrator",
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
            # 流式失败 → 回退非流式（仅当尚未产出内容时）
            logger.warning(f"流式生成失败，回退非流式: {stream_err}")
            if not final_answer:
                response = _call_llm(
                    caller="orchestrator",
                    model=MODEL,
                    messages=llm_messages,
                    temperature=get_config_float('llm.temperature_agent', 0.3),
                    max_tokens=get_config_int('llm.max_tokens_orchestrator', 8192),
                )
                final_answer = response.choices[0].message.content or ""
        if not final_answer:
            final_answer = "分析过程较长，请参考以上各专家的分析结果。"
    except CancelledError:
        raise
    except Exception:
        final_answer = "分析过程较长，请参考以上各专家的分析结果。"

    duration_ms = int((time.time() - start_time) * 1000)

    # 性能监控：记录总耗时
    perf_metrics["phases"]["total"] = duration_ms
    perf_metrics["complexity"] = complexity
    perf_metrics["specialist_count"] = len([s for s in specialist_results if not s.get("is_cross_review")])

    # 记录性能指标（异步，不阻塞）
    try:
        if conversation_id > 0 and message_id > 0:
            from agent.orchestrator_optimizer import log_performance_metrics
            log_performance_metrics(conversation_id, message_id, perf_metrics)
    except Exception as e:
        logger.warning(f"记录性能指标失败: {e}")

    yield {
        "type": "answer",
        "content": final_answer,
        "specialist_results": specialist_results,
        "tool_calls": all_tool_calls,
        "duration_ms": duration_ms,
        "complexity": complexity,
        "perf_metrics": perf_metrics,  # 添加性能指标到返回结果
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
