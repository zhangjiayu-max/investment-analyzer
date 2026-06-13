"""东方财富妙想 API 客户端。

官方 AI 金融分析 API，比 akshare 更稳定。
API 文档：https://marketing.dfcfw.com/res/download/A620260529IDA2EL.md

功能：
- stockHotspot: 市场热点发现
- financialSearch: 金融资讯搜索（研报、公告、新闻）
- stockDiagnosis: 股票综合诊断
- fundDiagnosis: 基金诊断
- stockScreener: 智能选股
- financeData: 金融数据查询
- macroData: 宏观数据查询
- financialAssistant: 金融问答

用法:
    from mcp.eastmoney_client import get_eastmoney_client
    client = get_eastmoney_client()
    hotspot = client.stock_hotspot("今日AI概念股热度")
"""

import logging
import httpx

logger = logging.getLogger(__name__)

API_BASE = "https://ai-saas.eastmoney.com/proxy/"
TIMEOUT = 60  # 秒

# API 端点
ENDPOINTS = {
    "hotspot": "app-robo-advisor-api/assistant/hotspot-discovery",
    "search": "b/mcp/tool/searchNews",
    "stock_diagnosis": "app-robo-advisor-api/assistant/stock-analysis",
    "fund_diagnosis": "app-robo-advisor-api/assistant/fund-analysis",
    "screener": "b/mcp/tool/selectSecurity",
    "finance_data": "b/mcp/tool/searchFinanceData",
    "macro_data": "b/mcp/tool/searchMacroData",
    "assistant": "app-robo-advisor-api/assistant/ask",
    "comparable": "app-robo-advisor-api/assistant/comparable-company-analysis",
    "earnings": "app-robo-advisor-api/assistant/write/performance/comment",
    "industry": "app-robo-advisor-api/assistant/write/industry/research",
    "topic": "app-robo-advisor-api/assistant/write/thematic/research",
}

_client_instance = None


def get_eastmoney_client() -> "EastMoneyClient":
    """获取全局单例。"""
    global _client_instance
    if _client_instance is None:
        from config import EASTMONEY_API_KEY
        if not EASTMONEY_API_KEY:
            raise RuntimeError("EASTMONEY_API_KEY 未配置，请在 .env 中设置 MX_APIKEY")
        _client_instance = EastMoneyClient(EASTMONEY_API_KEY)
    return _client_instance


class EastMoneyClient:
    def __init__(self, api_key: str, timeout: int = TIMEOUT):
        self._api_key = api_key
        self._timeout = timeout

    def _post(self, endpoint: str, body: dict) -> dict:
        """发送 POST 请求。"""
        url = f"{API_BASE}{endpoint}"
        headers = {
            "Content-Type": "application/json",
            "MX_APIKEY": self._api_key,
        }
        resp = httpx.post(url, json=body, headers=headers, timeout=self._timeout)
        resp.raise_for_status()
        return resp.json()

    def _extract_text(self, data: dict) -> str:
        """从响应中提取文本内容。"""
        if not data:
            return ""
        d = data.get("data", data)
        if isinstance(d, dict):
            for key in ("displayData", "llmSearchResponse", "searchResponse"):
                if isinstance(d.get(key), str):
                    return d[key]
        if isinstance(d, str):
            return d
        return str(data)

    def stock_hotspot(self, query: str) -> str:
        """市场热点发现。"""
        try:
            data = self._post(ENDPOINTS["hotspot"], {"question": query})
            return self._extract_text(data)
        except Exception as e:
            logger.warning(f"东方财富热点查询失败: {e}")
            return ""

    def financial_search(self, query: str) -> str:
        """金融资讯搜索（研报、公告、新闻）。"""
        try:
            data = self._post(ENDPOINTS["search"], {"question": query})
            return self._extract_text(data)
        except Exception as e:
            logger.warning(f"东方财富资讯搜索失败: {e}")
            return ""

    def stock_diagnosis(self, query: str) -> str:
        """股票综合诊断。"""
        try:
            data = self._post(ENDPOINTS["stock_diagnosis"], {"question": query})
            return self._extract_text(data)
        except Exception as e:
            logger.warning(f"东方财富股票诊断失败: {e}")
            return ""

    def fund_diagnosis(self, query: str) -> str:
        """基金诊断。"""
        try:
            data = self._post(ENDPOINTS["fund_diagnosis"], {"question": query})
            return self._extract_text(data)
        except Exception as e:
            logger.warning(f"东方财富基金诊断失败: {e}")
            return ""

    def stock_screener(self, query: str) -> str:
        """智能选股。"""
        try:
            data = self._post(ENDPOINTS["screener"], {"question": query})
            return self._extract_text(data)
        except Exception as e:
            logger.warning(f"东方财富选股失败: {e}")
            return ""

    def finance_data(self, query: str) -> dict:
        """金融数据查询。"""
        try:
            return self._post(ENDPOINTS["finance_data"], {"question": query})
        except Exception as e:
            logger.warning(f"东方财富数据查询失败: {e}")
            return {}

    def macro_data(self, query: str) -> dict:
        """宏观数据查询。"""
        try:
            return self._post(ENDPOINTS["macro_data"], {"question": query})
        except Exception as e:
            logger.warning(f"东方财富宏观数据失败: {e}")
            return {}

    def financial_assistant(self, query: str, deep_think: bool = False) -> str:
        """金融问答。"""
        try:
            body = {"question": query}
            if deep_think:
                body["deepThink"] = True
            data = self._post(ENDPOINTS["assistant"], body)
            return self._extract_text(data)
        except Exception as e:
            logger.warning(f"东方财富问答失败: {e}")
            return ""

    def comparable_analysis(self, query: str) -> str:
        """可比公司分析。"""
        try:
            data = self._post(ENDPOINTS["comparable"], {"question": query})
            return self._extract_text(data)
        except Exception as e:
            logger.warning(f"东方财富可比分析失败: {e}")
            return ""

    def industry_research(self, query: str) -> str:
        """行业研究报告（耗时较长，约20分钟）。"""
        try:
            data = self._post(ENDPOINTS["industry"], {"question": query})
            return self._extract_text(data)
        except Exception as e:
            logger.warning(f"东方财富行业研报失败: {e}")
            return ""
