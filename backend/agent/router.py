"""智能路由：规则优先 + LLM 兜底，决定调用哪些专家。"""

import hashlib
import json
import logging
import re
import threading
import time
from typing import Optional

from db.config import get_config, get_config_float
from llm_service import _call_llm, MODEL

logger = logging.getLogger(__name__)

# 关键词 → 专家 key 列表（按优先级排序）
# 注意：专家 key 来自 db.agents.load_specialist_agents() 的返回值
_KEYWORD_ROUTES = [
    (["估值", "PE", "PB", "百分位", "低估", "高估"], ["valuation_expert"]),
    (["风险", "回撤", "止损", "亏损", "最大回撤"], ["risk_assessor"]),
    (["配置", "仓位", "股债", "比例", "再平衡"], ["allocation_advisor"]),
    (["持仓", "分散", "穿透", "集中度"], ["allocation_advisor", "wealth_advisor"]),
    (["市场", "大盘", "行情", "走势", "牛市", "熊市"], ["market_analyst"]),
    (["买", "卖", "操作", "定投", "止盈", "加仓", "减仓"], ["allocation_advisor", "risk_assessor", "valuation_expert"]),
    (["文章", "公众号", "解读", "新闻"], ["article_expert"]),
    (["基金", "选基", "基金分析"], ["fund_analyst"]),
    (["行为", "情绪", "心理"], ["behavior_coach"]),
    (["宏观", "经济", "利率", "政策"], ["macro_strategist"]),
    (["反方", "质疑", "风险另一面"], ["counter_argument"]),
]


def _load_specialist_keys() -> dict:
    """加载专家 key 映射，避免循环导入。"""
    try:
        from db.agents import load_specialist_agents
        agents = load_specialist_agents()
        return {key: info for key, info in agents.items() if key != "arbitrator"}
    except Exception as e:
        logger.warning(f"加载专家配置失败: {e}")
        return {}


class SmartRouter:
    """智能路由。规则命中零 LLM 成本；未命中时用 LLM 兜底。"""

    def __init__(self):
        self._cache = {}
        self._cache_ttl = 60  # 路由结果缓存 60 秒
        self._lock = threading.Lock()

    def _cache_key(self, query: str, history_summary: str, context_hash: str) -> str:
        raw = f"{query}|{history_summary}|{context_hash}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def _rule_route(self, query: str) -> Optional[dict]:
        specialists = set()
        for keywords, agents in _KEYWORD_ROUTES:
            if any(kw in query for kw in keywords):
                specialists.update(agents)
        if not specialists:
            return None
        return {
            "complexity": "simple" if len(specialists) == 1 else "medium",
            "specialists": list(specialists),
            "reason": f"关键词路由命中: {', '.join(specialists)}",
            "needs_arbitration": len(specialists) >= 2,
            "route_by": "rule",
        }

    def _llm_fallback_route(self, query: str, history_summary: str, portfolio_summary: str) -> dict:
        specialists = _load_specialist_keys()
        expert_lines = []
        for key, info in specialists.items():
            desc = info.get("description", "") if isinstance(info, dict) else ""
            name = info.get("name", key) if isinstance(info, dict) else key
            expert_lines.append(f"- {key}: {name} — {desc}")
        expert_list = "\n".join(expert_lines)

        prompt = f"""你是投资分析需求路由专家。分析用户问题，返回 JSON。

可选专家：
{expert_list}

请返回严格 JSON：
{{
  "complexity": "simple|medium|complex",
  "specialists": ["专家key1", "专家key2"],
  "refined_query": "优化后的问题(可保持原问题)",
  "reason": "简短理由",
  "needs_arbitration": true/false
}}

历史摘要：{history_summary}
持仓摘要：{portfolio_summary}
当前问题：{query}"""

        try:
            response = _call_llm(
                caller="smart_router",
                model=MODEL,
                messages=[
                    {"role": "system", "content": "你只做路由决策，不输出分析内容。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=get_config_float("llm.temperature_tool", 0.2),
                max_tokens=500,
            )
            content = response.choices[0].message.content or ""
            # 尝试提取 JSON
            match = re.search(r"\{.*\}", content, re.DOTALL)
            if match:
                data = json.loads(match.group(0))
                data["route_by"] = "llm_fallback"
                return data
        except Exception as e:
            logger.warning(f"LLM 路由兜底失败: {e}")

        # 回退：返回通用配置（使用真实专家 key）
        default_keys = [k for k in _load_specialist_keys() if k in ("valuation_expert", "risk_assessor")]
        if not default_keys:
            default_keys = ["valuation_expert", "risk_assessor"]
        return {
            "complexity": "medium",
            "specialists": default_keys,
            "reason": "LLM 路由失败，回退到默认专家",
            "needs_arbitration": True,
            "route_by": "llm_fallback",
        }

    def route(self, query: str, history_summary: str = "", portfolio_summary: str = "",
              target_specialists: Optional[list] = None) -> dict:
        """路由入口。"""
        # 1. 用户显式指定专家：直接尊重用户选择，不过滤（上游已校验或执行时校验）
        if target_specialists:
            return {
                "complexity": "simple" if len(target_specialists) == 1 else "medium",
                "specialists": list(target_specialists),
                "reason": f"用户通过 @mention 指定专家: {', '.join(target_specialists)}",
                "needs_arbitration": len(target_specialists) >= 2,
                "route_by": "mention",
            }

        # 2. 检查缓存（线程安全）
        ctx_hash = hashlib.md5((history_summary + portfolio_summary).encode("utf-8")).hexdigest()[:16]
        cache_key = self._cache_key(query, history_summary, ctx_hash)
        now = time.time()
        with self._lock:
            if cache_key in self._cache:
                cached, ts = self._cache[cache_key]
                if now - ts < self._cache_ttl:
                    return cached

        # 3. 规则路由
        if get_config("router.enabled", "true") == "true":
            rule_result = self._rule_route(query)
            if rule_result:
                with self._lock:
                    self._cache[cache_key] = (rule_result, now)
                return rule_result

        # 4. LLM 兜底
        if get_config("router.use_llm_fallback", "true") == "true":
            llm_result = self._llm_fallback_route(query, history_summary, portfolio_summary)
            with self._lock:
                self._cache[cache_key] = (llm_result, now)
            return llm_result

        # 5. 最终回退
        fallback = {
            "complexity": get_config("router.default_complexity", "medium"),
            "specialists": ["valuation_expert", "risk_assessor"],
            "reason": "路由关闭且未指定专家，使用默认配置",
            "needs_arbitration": True,
            "route_by": "default",
        }
        with self._lock:
            self._cache[cache_key] = (fallback, now)
        return fallback
