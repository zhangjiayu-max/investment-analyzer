"""盈米且慢 MCP Streamable HTTP 客户端。

Endpoint: https://stargate.yingmi.com/mcp/v2
协议: MCP Streamable HTTP (JSON-RPC 2.0)
请求头: x-api-key + Content-Type: application/json + Accept: application/json, text/event-stream

用法:
  from yingmi_client import get_yingmi_client
  client = get_yingmi_client()
  result = client.call_tool("SearchFinancialNews", {"keyword": "白酒", "pageSize": 3})
"""

import logging

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://stargate.yingmi.com/mcp/v2"
MCP_PROTOCOL_VERSION = "2024-11-05"

_client_instance = None


def get_yingmi_client() -> "YingMiClient":
    """获取全局单例。"""
    global _client_instance
    if _client_instance is None:
        from config import YINGMI_API_KEY
        if not YINGMI_API_KEY:
            raise RuntimeError("YINGMI_API_KEY 未配置，请在 .env 中设置")
        _client_instance = YingMiClient(YINGMI_API_KEY)
    return _client_instance


class YingMiClient:
    def __init__(self, api_key: str, timeout: int = 30):
        self._api_key = api_key
        self._client = httpx.Client(timeout=timeout)
        self._initialized = False

    # ── 内部方法 ──────────────────────────────────

    def _post(self, body: dict) -> dict | None:
        resp = self._client.post(
            BASE_URL,
            json=body,
            headers={
                "x-api-key": self._api_key,
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
        )
        # 202 表示已接受但无响应体（通知类消息），返回 None
        if resp.status_code == 202:
            return None
        if resp.status_code != 200:
            raise RuntimeError(f"盈米 MCP 请求失败 ({resp.status_code}): {resp.text}")
        return resp.json()

    def _ensure_initialized(self):
        if self._initialized:
            return
        # Step 1: initialize
        init_resp = self._post({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "investment-analyzer", "version": "0.1.0"},
            },
        })
        if "error" in init_resp:
            raise RuntimeError(f"初始化失败: {init_resp['error']}")
        # Step 2: notifications/initialized
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self._initialized = True
        logger.info("盈米 MCP 会话已建立")

    def _extract_text(self, result: dict) -> str:
        """从 MCP 返回的 content 数组中提取纯文本。"""
        parts = []
        for item in result.get("content", []):
            if item.get("type") == "text":
                parts.append(item["text"])
        return "\n".join(parts)

    # ── 公开方法 ──────────────────────────────────

    def list_tools(self) -> list[dict]:
        """列出所有可用工具。"""
        self._ensure_initialized()
        result = self._post({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/list",
            "params": {},
        })
        return result.get("result", {}).get("tools", [])

    def call_tool(self, tool_name: str, arguments: dict | None = None) -> dict:
        """调用盈米 MCP 工具，返回原始 result 字典。"""
        self._ensure_initialized()
        body = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments or {}},
        }
        result = self._post(body)
        if "error" in result:
            raise RuntimeError(f"工具调用失败: {result['error']}")
        return result.get("result", {})

    def call_tool_text(self, tool_name: str, arguments: dict | None = None) -> str:
        """调用工具并返回纯文本结果。"""
        result = self.call_tool(tool_name, arguments)
        return self._extract_text(result)

    # ── 便捷方法 ──────────────────────────────────

    def search_news(self, keyword: str, page_size: int = 10) -> str:
        """搜索财经资讯。"""
        return self.call_tool_text("SearchFinancialNews", {
            "keyword": keyword,
            "pageSize": page_size,
        })

    def get_fund_diagnosis(self, fund_name_or_code: str) -> str:
        """获取基金诊断信息（风险评价、估值、业绩指标等）。"""
        return self.call_tool_text("GetFundDiagnosis", {
            "fundNameOrCode": fund_name_or_code,
        })

    def get_hot_topics(self, keyword: str = "") -> str:
        """分析市场热点。"""
        params = {}
        if keyword:
            params["keyword"] = keyword
        return self.call_tool_text("SearchHotTopic", params)

    def get_latest_quotations(self) -> str:
        """获取最新市场行情解读。"""
        return self.call_tool_text("GetLatestQuotations")

    def diagnose_portfolio(self, fund_codes: list[str]) -> str:
        """诊断基金组合（资产配置、相关性、回测表现）。"""
        return self.call_tool_text("DiagnoseFundPortfolio", {
            "fundCodes": fund_codes,
        })

    def search_funds(self, keyword: str, page: int = 1, page_size: int = 20) -> str:
        """搜索基金。"""
        return self.call_tool_text("SearchFunds", {
            "keyword": keyword,
            "page": page,
            "pageSize": page_size,
        })

    def close(self):
        self._client.close()
