#!/usr/bin/env python3
"""测试盈米且慢 MCP 客户端。"""
import sys, json
sys.path.insert(0, ".")
from backend.mcp.yingmi_client import YingMiClient

api_key = sys.argv[1]
client = YingMiClient(api_key)

# 列出工具总数
tools = client.list_tools()
print(f"可用工具: {len(tools)} 个\n")

# 1. 搜索新闻
print("=" * 50)
print("搜索新闻（白酒）")
print(client.search_news("白酒", 2))

# 2. 基金诊断
print("\n" + "=" * 50)
print("基金诊断（161725 招商中证白酒）")
print(client.get_fund_diagnosis("161725"))

# 3. 市场行情
print("\n" + "=" * 50)
print("最新行情")
print(client.get_latest_quotations())

client.close()
