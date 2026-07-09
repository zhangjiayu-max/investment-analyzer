"""智能路由：规则优先 + 声明式 fallback + LLM 兜底，决定调用哪些专家。"""

import hashlib
import json
import logging
import os
import re
import threading
import time
from typing import Optional

from db.config import get_config, get_config_float
from services.llm_service import _call_llm, MODEL

logger = logging.getLogger(__name__)


# ── P4: 声明式路由配置加载 ──────────────────────

_ROUTER_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "router_config.yaml",
)
_router_config_cache: Optional[dict] = None
_router_config_mtime: float = 0.0


def _load_router_config() -> dict:
    """加载 router_config.yaml（带 mtime 缓存，支持热更新）。"""
    global _router_config_cache, _router_config_mtime
    try:
        mtime = os.path.getmtime(_ROUTER_CONFIG_PATH)
    except OSError:
        return {}

    if _router_config_cache is not None and mtime == _router_config_mtime:
        return _router_config_cache

    try:
        import yaml
        with open(_ROUTER_CONFIG_PATH, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        _router_config_cache = config
        _router_config_mtime = mtime
        logger.debug(f"[router] 加载声明式配置: {len(config.get('routes', []))} 主规则, "
                     f"{len(config.get('declarative_fallback', []))} fallback 规则")
        return config
    except ImportError:
        logger.warning("[router] PyYAML 未安装，声明式 fallback 不可用")
        return {}
    except Exception as e:
        logger.warning(f"[router] 加载 router_config.yaml 失败: {e}")
        return {}

# 关键词 → 专家 key 列表（按优先级排序）
# 注意：专家 key 来自 db.agents.load_specialist_agents() 的返回值
_KEYWORD_ROUTES = [
    (["估值", "PE", "PB", "百分位", "低估", "高估"], ["valuation_expert"]),
    (["风险", "回撤", "止损", "亏损", "最大回撤"], ["risk_assessor"]),
    (["配置", "仓位", "股债", "比例", "再平衡"], ["allocation_advisor"]),
    (["持仓", "分散", "穿透", "集中度"], ["allocation_advisor"]),
    (["市场", "大盘", "行情", "走势", "牛市", "熊市"], ["market_analyst"]),
    (["诊断", "体检", "检查", "全面"], ["valuation_expert", "risk_assessor", "allocation_advisor"]),
    (["买", "卖", "操作", "定投", "止盈", "加仓", "减仓"], ["allocation_advisor", "risk_assessor", "valuation_expert"]),
    (["文章", "公众号", "解读", "新闻"], ["article_expert"]),
    (["基金", "选基", "基金分析"], ["fund_analyst"]),
    (["宏观", "经济", "利率", "政策"], ["macro_strategist"]),
]

_HIGH_RISK_ACTION_KEYWORDS = [
    "清仓", "满仓", "梭哈", "追涨", "杀跌", "恐慌", "很慌", "冲动", "重仓买入",
]


def _is_high_risk_action(query: str) -> bool:
    return any(kw in query for kw in _HIGH_RISK_ACTION_KEYWORDS)


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
    """智能路由。规则命中零 LLM 成本；未命中时用 LLM 兜底。

    MoE 增强：
    - 专家容量追踪（每位专家在时间窗口内的调用次数）
    - 共享专家（高优先级专家始终启用，提供通用上下文）
    - 动态负载均衡（避免单一专家过载）
    """

    # 共享专家：所有复杂查询都会附带，提供通用投资框架
    SHARED_EXPERTS = ["risk_assessor"]  # 风险评估始终参与复杂分析

    # 专家容量上限（滑动窗口 5 分钟内最大调用次数）
    EXPERT_CAPACITY = 10

    def __init__(self):
        self._cache = {}
        self._cache_ttl = 60  # 路由结果缓存 60 秒
        self._lock = threading.Lock()
        # 专家负载追踪：{agent_key: [timestamp1, timestamp2, ...]}
        self._expert_load: dict[str, list[float]] = {}
        # 专家性能追踪：{agent_key: {"avg_duration": float, "success_rate": float, "count": int}}
        self._expert_perf: dict[str, dict] = {}

    def _cache_key(self, query: str, history_summary: str, context_hash: str) -> str:
        raw = f"{query}|{history_summary}|{context_hash}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def _rule_route(self, query: str, portfolio_summary: str = "") -> Optional[dict]:
        specialists = set()
        for keywords, agents in _KEYWORD_ROUTES:
            if any(kw in query for kw in keywords):
                specialists.update(agents)
        if _is_high_risk_action(query):
            specialists.update(["risk_assessor"])

        # 持仓感知路由：根据持仓状况追加专家（零 LLM 成本）
        if portfolio_summary and "无持仓" not in portfolio_summary:
            import re as _re
            # 重仓检测：某基金占比>25% → 追加风险专家
            pct_matches = _re.findall(r'([\d.]+)%', portfolio_summary)
            if pct_matches and max(float(m) for m in pct_matches) > 25:
                specialists.add("risk_assessor")
                logger.info("[router] 持仓感知：检测到重仓(>25%)，追加 risk_assessor")
            # 现金占比高 → 追加配置专家
            cash_match = _re.search(r'现金.*?([\d.]+)%', portfolio_summary)
            if cash_match and float(cash_match.group(1)) > 30:
                specialists.add("allocation_advisor")
                logger.info("[router] 持仓感知：检测到现金占比高(>30%)，追加 allocation_advisor")
            # 债券占比高 → 追加配置专家（债券超 50% 需评估再平衡机会）
            bond_match = _re.search(r'债券.*?([\d.]+)%', portfolio_summary)
            if bond_match and float(bond_match.group(1)) > 50:
                specialists.add("allocation_advisor")
                logger.info(f"[router] 持仓感知：检测到债券占比高({bond_match.group(1)}%>50%)，追加 allocation_advisor")

        if not specialists:
            return None

        # 限制最大专家数为 3；高风险行动优先保留风控专家。
        specialists_list = list(specialists)
        if len(specialists_list) > 3:
            if _is_high_risk_action(query):
                priority_order = [
                    "risk_assessor",
                    "valuation_expert", "allocation_advisor", "market_analyst",
                    "macro_strategist", "fund_analyst",
                    "article_expert",
                ]
                priority = {agent_key: i for i, agent_key in enumerate(priority_order)}
            else:
                # 按 _KEYWORD_ROUTES 中出现的顺序排序(靠前的高优先级)
                priority = {}
                for i, (_, agents) in enumerate(_KEYWORD_ROUTES):
                    for a in agents:
                        if a not in priority:
                            priority[a] = i
            specialists_list.sort(key=lambda x: priority.get(x, 999))
            specialists_list = specialists_list[:3]
            logger.info(f"专家数超过 3 个,按优先级截断为: {specialists_list}")

        return {
            "complexity": "simple" if len(specialists_list) == 1 else ("complex" if len(specialists_list) >= 3 else "medium"),
            "specialists": specialists_list,
            "reason": f"关键词路由命中: {', '.join(specialists_list)}",
            "needs_arbitration": len(specialists_list) >= 2,
            "route_by": "rule",
        }

    def _declarative_fallback_route(self, query: str) -> Optional[dict]:
        """P4: 声明式 fallback 路由 — 用 YAML 配置匹配模糊查询，零 LLM 成本。

        主路由（_rule_route）未命中时调用，命中则返回，未命中返回 None。
        """
        config = _load_router_config()
        if not config:
            return None

        fallback_rules = config.get("declarative_fallback", [])
        for rule in fallback_rules:
            patterns = rule.get("patterns", [])
            if any(p in query for p in patterns):
                experts = rule.get("experts", [])
                if experts:
                    return {
                        "complexity": "simple" if len(experts) == 1 else "medium",
                        "specialists": list(experts),
                        "reason": f"声明式 fallback 命中: {rule.get('name', '')}",
                        "needs_arbitration": len(experts) >= 2,
                        "route_by": "declarative",
                    }
        return None

    def _get_default_experts(self) -> list:
        """P4: 从 YAML 获取默认专家列表。"""
        config = _load_router_config()
        defaults = config.get("default_experts", [])
        if defaults:
            return list(defaults)
        return ["valuation_expert", "risk_assessor"]

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

    def _cleanup_expired_cache(self, now: float):
        """清理过期缓存条目，防止无限增长。"""
        expired = [k for k, (_, ts) in self._cache.items() if now - ts > self._cache_ttl * 2]
        for k in expired:
            del self._cache[k]

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

        # 3. 规则路由（传入 portfolio_summary 启用持仓感知）
        if get_config("router.enabled", "true") == "true":
            rule_result = self._rule_route(query, portfolio_summary)
            if rule_result:
                with self._lock:
                    self._cleanup_expired_cache(now)
                    self._cache[cache_key] = (rule_result, now)
                return rule_result

        # 3.5 P4: 声明式 fallback（零 LLM 成本，YAML 配置可热更新）
        declarative_result = self._declarative_fallback_route(query)
        if declarative_result:
            with self._lock:
                self._cleanup_expired_cache(now)
                self._cache[cache_key] = (declarative_result, now)
            return declarative_result

        # 4. LLM 兜底（声明式未命中时才调用，频率极低）
        if get_config("router.use_llm_fallback", "true") == "true":
            llm_result = self._llm_fallback_route(query, history_summary, portfolio_summary)
            with self._lock:
                self._cleanup_expired_cache(now)
                self._cache[cache_key] = (llm_result, now)
            return llm_result

        # 5. 最终回退（使用 YAML 配置的默认专家）
        default_experts = self._get_default_experts()
        fallback = {
            "complexity": get_config("router.default_complexity", "medium"),
            "specialists": default_experts,
            "reason": "路由关闭且未指定专家，使用默认配置",
            "needs_arbitration": len(default_experts) >= 2,
            "route_by": "default",
        }
        with self._lock:
            self._cleanup_expired_cache(now)
            self._cache[cache_key] = (fallback, now)
        return fallback

    # ── MoE 增强：专家容量追踪与负载均衡 ──────────────────

    def _prune_load_window(self, agent_key: str, now: float) -> list[float]:
        """修剪负载滑动窗口（保留最近 5 分钟）。"""
        if agent_key not in self._expert_load:
            return []
        window = [t for t in self._expert_load[agent_key] if now - t < 300]
        self._expert_load[agent_key] = window
        return window

    def record_expert_call(self, agent_key: str, duration: float = 0, success: bool = True):
        """记录一次专家调用，用于容量与性能追踪。"""
        now = time.time()
        with self._lock:
            self._prune_load_window(agent_key, now)
            self._expert_load.setdefault(agent_key, []).append(now)
            # 性能统计
            perf = self._expert_perf.setdefault(agent_key, {
                "avg_duration": 0, "success_rate": 1.0, "count": 0
            })
            n = perf["count"]
            # 增量平均
            perf["avg_duration"] = (perf["avg_duration"] * n + duration) / (n + 1) if n > 0 else duration
            perf["success_rate"] = (perf["success_rate"] * n + (1.0 if success else 0.0)) / (n + 1)
            perf["count"] = n + 1

    def get_expert_capacity(self, agent_key: str) -> dict:
        """查询专家当前负载。"""
        now = time.time()
        with self._lock:
            window = self._prune_load_window(agent_key, now)
            perf = self._expert_perf.get(agent_key, {})
        return {
            "agent_key": agent_key,
            "current_load": len(window),
            "capacity": self.EXPERT_CAPACITY,
            "load_pct": round(len(window) / self.EXPERT_CAPACITY * 100, 1) if self.EXPERT_CAPACITY > 0 else 0,
            "avg_duration": round(perf.get("avg_duration", 0), 2),
            "success_rate": round(perf.get("success_rate", 1.0), 3),
            "call_count": perf.get("count", 0),
        }

    def apply_capacity_limit(self, specialists: list[str]) -> list[str]:
        """按容量上限过滤专家（超载专家降级处理）。"""
        now = time.time()
        result = []
        with self._lock:
            for s in specialists:
                window = self._prune_load_window(s, now)
                if len(window) < self.EXPERT_CAPACITY:
                    result.append(s)
                else:
                    logger.warning(f"专家 {s} 容量已满 ({len(window)}/{self.EXPERT_CAPACITY})，跳过")
        return result if result else specialists  # 保底：全部满载时仍返回原列表

    def attach_shared_experts(self, specialists: list[str], complexity: str) -> list[str]:
        """复杂查询附带共享专家（风险评估）。"""
        if complexity == "complex":
            for expert in self.SHARED_EXPERTS:
                if expert not in specialists:
                    specialists.append(expert)
                    logger.info(f"附加共享专家: {expert}")
        return specialists
