"""天天基金 Skills API 客户端。

官方基金数据 API，覆盖基金信息、经理、持仓、净值、指数、债市等。
网关地址：https://skills.tiantianfunds.com/ai-smart-skill-service/openapi/skill/invoke

用法:
    from mcp.ttfund_client import get_ttfund_client
    client = get_ttfund_client()
    info = client.fund_base_info("110011")
"""

import logging
import json
import httpx

logger = logging.getLogger(__name__)

API_GATEWAY = "https://skills.tiantianfunds.com/ai-smart-skill-service/openapi/skill/invoke"
TIMEOUT = 30

# Skill 注册表
SKILLS = {
    "fund_base_info":    {"id": "FUND_BASE_INFOS",        "version": "1.2.0"},
    "fund_manager":      {"id": "FUND_MANAGER_INFO",      "version": "1.0.0"},
    "fund_condition":    {"id": "FUND_CONDITION_SELECT",  "version": "1.2.0"},
    "fund_holding":      {"id": "FUND_HOLDING_INFO",      "version": "1.0.0"},
    "fund_gold":         {"id": "FUND_HUAAN_GOLD_INFO",   "version": "1.0.0"},
    "fund_tg_strategy":  {"id": "FUND_TG_STRATEGY_INFO",  "version": "1.0.0"},
    "fund_index":        {"id": "FUND_INDEX_INFO",        "version": "1.0.0"},
    "fund_nav":          {"id": "FUND_NAV_INFO",          "version": "1.0.0"},
    "fund_portfolio":    {"id": "MODEL_PORTFOLIO",        "version": "1.0.0"},
    "fund_favor":        {"id": "FUND_FAVOR_ZX",          "version": "1.2.0"},
    "bond_market":       {"id": "BOND_MARKET",            "version": "1.0.0"},
    "fund_backtest":     {"id": "FUND_GROUP_BACKTEST",    "version": "1.0.0"},
    "fund_search":       {"id": "FUND_SEARCH",            "version": "1.0.0"},
    "stock_price":       {"id": "FUND_STOCK_PRICE_QUERY", "version": "1.0.0"},
}

_client_instance = None


def get_ttfund_client() -> "TtfundClient":
    """获取全局单例。"""
    global _client_instance
    if _client_instance is None:
        from config import TTFUND_APIKEY
        if not TTFUND_APIKEY:
            raise RuntimeError("TTFUND_APIKEY 未配置，请在 .env 中设置")
        _client_instance = TtfundClient(TTFUND_APIKEY)
    return _client_instance


class TtfundClient:
    def __init__(self, api_key: str, timeout: int = TIMEOUT):
        self._api_key = api_key
        self._timeout = timeout

    def _invoke(self, skill_name: str, params: dict) -> dict:
        """调用指定 skill。"""
        skill = SKILLS.get(skill_name)
        if not skill:
            raise ValueError(f"未知 skill: {skill_name}")

        body = {
            "skill_id": skill["id"],
            "_skill_version": skill["version"],
            **params,
        }
        headers = {
            "Content-Type": "application/json",
            "X-API-Key": self._api_key,
        }
        resp = httpx.post(API_GATEWAY, json=body, headers=headers, timeout=self._timeout)
        resp.raise_for_status()
        return resp.json()

    def _invoke_text(self, skill_name: str, params: dict) -> str:
        """调用 skill 并返回文本结果。"""
        try:
            data = self._invoke(skill_name, params)
            # 提取结果文本
            if isinstance(data, dict):
                # 尝试各种响应格式
                for key in ("data", "result", "content", "message"):
                    val = data.get(key)
                    if isinstance(val, str) and len(val) > 10:
                        return val
                    if isinstance(val, dict):
                        return json.dumps(val, ensure_ascii=False, indent=2)
                return json.dumps(data, ensure_ascii=False, indent=2)
            return str(data)
        except Exception as e:
            logger.warning(f"ttfund {skill_name} 调用失败: {e}")
            return ""

    # ── 业务方法 ──────────────────────────────────────────

    def fund_base_info(self, fund_code: str) -> dict:
        """基金综合信息查询。"""
        return self._invoke("fund_base_info", {"fcode": fund_code})

    def fund_manager(self, manager_name: str) -> str:
        """基金经理信息查询。"""
        return self._invoke_text("fund_manager", {"manager_name": manager_name})

    def fund_holding(self, fund_code: str) -> str:
        """基金重仓股/持仓查询。"""
        return self._invoke_text("fund_holding", {"fcode": fund_code})

    def fund_index(self, index_name: str) -> str:
        """指数行情/估值/成分查询。"""
        return self._invoke_text("fund_index", {"index_id": index_name, "query_scope": "all"})

    def fund_nav(self, fund_code: str) -> str:
        """基金净值历史查询。"""
        return self._invoke_text("fund_nav", {"fcode": fund_code})

    def fund_search(self, keyword: str) -> str:
        """基金搜索。"""
        return self._invoke_text("fund_search", {"query": keyword, "search_type": "fund"})

    def stock_price(self, stock_code: str) -> str:
        """股票实时行情。"""
        return self._invoke_text("stock_price", {"query": stock_code})

    def fund_condition(self, condition: str) -> str:
        """条件选基。"""
        return self._invoke_text("fund_condition", {"condition": condition})

    def bond_market(self) -> str:
        """债市行情。"""
        return self._invoke_text("bond_market", {})

    def fund_gold(self) -> str:
        """黄金行情。"""
        return self._invoke_text("fund_gold", {})
