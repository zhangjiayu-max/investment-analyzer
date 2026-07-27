#!/usr/bin/env python3
"""MIMO 评测套件 — 对话 + 分析 + RAG 全量回归评测。

用法：
    # 全量评测（对话10条 + API 5条 + RAG 65条）
    python3 scripts/run_eval_suite.py

    # 只跑对话评测
    python3 scripts/run_eval_suite.py --only conversation

    # 只跑 API 评测
    python3 scripts/run_eval_suite.py --only api

    # 只跑 RAG 评测
    python3 scripts/run_eval_suite.py --only rag

    # 指定用例编号
    python3 scripts/run_eval_suite.py --only conversation --case 0
    python3 scripts/run_eval_suite.py --only conversation --case 0,1,2

输出：
    data/eval_results/{timestamp}/
    ├── conversation_results.json   # 对话评测结果
    ├── api_results.json            # API 评测结果
    ├── rag_results.json            # RAG 评测结果（复用 rag_eval_suite）
    └── summary.md                  # 汇总报告
"""

import sys
import json
import time
import argparse
import requests
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

BASE_URL = "http://127.0.0.1:8000"
OUTPUT_DIR = Path(__file__).parent.parent.parent / "data" / "eval_results"

# ══════════════════════════════════════════════════════════════
# 评测数据集
# ══════════════════════════════════════════════════════════════

# A. 对话评测用例（覆盖多专家协作场景）
CONVERSATION_CASES = [
    {
        "id": "conv-001",
        "category": "valuation",
        "title": "沪深300估值查询",
        "query": "沪深300现在的估值是多少？处于什么分位？能买吗",
        "expected_topics": ["沪深300", "估值", "分位", "PE", "PB"],
        "expected_specialists": ["valuation_expert"],
        "expected_quality": "必须引用具体PE/PB数值和分位点，给出明确操作建议",
    },
    {
        "id": "conv-002",
        "category": "valuation",
        "title": "恒生科技估值查询",
        "query": "恒生科技指数现在便宜吗？估值分位多少",
        "expected_topics": ["恒生科技", "估值", "分位"],
        "expected_specialists": ["valuation_expert"],
        "expected_quality": "必须引用恒生科技PE/PS数据，说明数据来源（akshare/ttfund）",
    },
    {
        "id": "conv-003",
        "category": "portfolio",
        "title": "持仓补仓建议",
        "query": "我的医药基金亏损20%了，现在该补仓还是割肉？补多少合适",
        "expected_topics": ["医药", "补仓", "亏损", "止损", "加仓"],
        "expected_specialists": ["risk_assessor", "allocation_advisor"],
        "expected_quality": "必须调用query_portfolio查看持仓，调用query_valuation查医药估值，给出具体补仓金额建议",
    },
    {
        "id": "conv-004",
        "category": "portfolio",
        "title": "持仓健康诊断",
        "query": "帮我诊断下我的持仓健康吗？有没有风险",
        "expected_topics": ["持仓", "风险", "集中度", "配置"],
        "expected_specialists": ["risk_assessor"],
        "expected_quality": "必须调用query_portfolio，分析集中度/相关性/风险等级",
    },
    {
        "id": "conv-005",
        "category": "strategy",
        "title": "定投策略建议",
        "query": "现在市场震荡，定投策略怎么调整？低估多投怎么操作",
        "expected_topics": ["定投", "策略", "低估", "配置"],
        "expected_specialists": ["allocation_advisor"],
        "expected_quality": "给出具体定投比例和调整方案，引用估值数据",
    },
    {
        "id": "conv-006",
        "category": "market",
        "title": "市场下跌原因分析",
        "query": "为什么最近市场一直跌？是什么原因导致的",
        "expected_topics": ["市场", "下跌", "原因", "资金"],
        "expected_specialists": ["macro_strategist", "market_analyst"],
        "expected_quality": "分析宏观/资金/政策等多维度原因，不能只说'市场波动'",
    },
    {
        "id": "conv-007",
        "category": "behavioral",
        "title": "暴跌恐慌情绪",
        "query": "市场暴跌了我的基金都在亏，好恐慌要不要全部清仓",
        "expected_topics": ["恐慌", "清仓", "情绪", "行为"],
        "expected_specialists": ["behavioral_advisor", "risk_assessor"],
        "expected_quality": "识别恐慌情绪偏差，给出理性建议，不能简单说'不要恐慌'",
    },
    {
        "id": "conv-008",
        "category": "macro",
        "title": "降准政策影响",
        "query": "央行降准0.5个百分点对市场有什么影响？利好哪些板块",
        "expected_topics": ["降准", "央行", "利好", "板块"],
        "expected_specialists": ["macro_strategist"],
        "expected_quality": "分析降准传导机制，明确受益板块，引用历史案例",
    },
    {
        "id": "conv-009",
        "category": "fund_analysis",
        "title": "指定基金分析",
        "query": "161725 招商中证白酒这只基金怎么样？现在能买吗",
        "expected_topics": ["161725", "白酒", "基金", "估值"],
        "expected_specialists": ["fund_analyst", "valuation_expert"],
        "expected_quality": "穿透基金持仓，查白酒估值，分析基金经理/规模/费率",
    },
    {
        "id": "conv-010",
        "category": "edge",
        "title": "领域外问题",
        "query": "今天天气怎么样",
        "expected_topics": [],
        "expected_specialists": [],
        "expected_quality": "明确告知超出投资领域范围，不强行回答",
    },
]

# B. API 评测用例（直接调用分析/补仓/估值 API）
API_CASES = [
    {
        "id": "api-001",
        "category": "valuation",
        "title": "统一估值查询 - 沪深300",
        "method": "GET",
        "url": "/api/valuation/unified",
        "params": {"index_code": "000300", "metric_type": "市盈率", "source": "all", "max_days": 7},
        "expected_quality": "返回PE值、分位点、数据来源、数据时间",
    },
    {
        "id": "api-002",
        "category": "valuation",
        "title": "增强策略分析（LLM判断真低估/价值陷阱）",
        "method": "GET",
        "url": "/api/valuation/enhanced-strategy",
        "params": {},
        "expected_quality": "返回多个指数的低估判断+LLM分析理由",
    },
    {
        "id": "api-003",
        "category": "smart_add",
        "title": "全持仓补仓计划",
        "method": "GET",
        "url": "/api/smart-add/plan",
        "params": {},
        "expected_quality": "返回每只基金的补仓信号、建议金额、档位",
    },
    {
        "id": "api-004",
        "category": "smart_add",
        "title": "策略对比模拟",
        "method": "POST",
        "url": "/api/smart-add/simulate",
        "body": {"fund_code": "161725", "monthly_drop_pct": -5.0, "months": 6},
        "expected_quality": "返回4种策略（不补仓/等额定投/金字塔补仓/价值平均法）的对比数据",
    },
    {
        "id": "api-005",
        "category": "market",
        "title": "市场温度",
        "method": "GET",
        "url": "/api/valuation/market-temperature",
        "params": {},
        "expected_quality": "返回市场温度值、历史对比",
    },
]


# ══════════════════════════════════════════════════════════════
# 评测执行器
# ══════════════════════════════════════════════════════════════

def run_conversation_case(case: dict) -> dict:
    """执行单条对话评测（SSE 流式）。

    流程：创建对话 → 发送消息（流式）→ 收集完整响应 → 保存
    """
    result = {
        "case_id": case["id"],
        "category": case["category"],
        "title": case["title"],
        "query": case["query"],
        "expected_quality": case["expected_quality"],
        "expected_topics": case.get("expected_topics", []),
        "expected_specialists": case.get("expected_specialists", []),
        "start_time": datetime.now().isoformat(),
        "status": "running",
    }

    try:
        # 1. 创建对话
        resp = requests.post(
            f"{BASE_URL}/api/conversations",
            json={"title": f"[评测] {case['title']}"},
            timeout=10,
        )
        resp.raise_for_status()
        conv_data = resp.json()
        # 兼容统一响应包装 {"code":0,"data":{"conversation_id":N}} 和裸返回 {"id":N}
        if "data" in conv_data and isinstance(conv_data["data"], dict):
            conv_id = conv_data["data"].get("id") or conv_data["data"].get("conversation_id")
        else:
            conv_id = conv_data.get("id") or conv_data.get("conversation_id")
        result["conv_id"] = conv_id

        if not conv_id:
            result["status"] = "failed"
            result["error"] = f"创建对话失败: {conv_data}"
            return result

        # 2. 发送消息（流式）
        sse_url = f"{BASE_URL}/api/conversations/{conv_id}/messages/stream"
        payload = {"content": case["query"], "target_specialists": [], "images": []}

        assistant_text = ""
        specialist_results = []
        rag_info = {}
        tool_calls = []
        events = []
        t0 = time.time()
        timeout = 300  # 5 分钟超时

        try:
            with requests.post(sse_url, json=payload, stream=True, timeout=timeout) as r:
                r.raise_for_status()
                for line in r.iter_lines(decode_unicode=True):
                    if not line or not line.startswith("data: "):
                        continue
                    try:
                        event = json.loads(line[6:])
                        events.append(event)
                        etype = event.get("type", "")
                        edata = event.get("data", {})

                        # 最终答案（包含 assistant_text + specialist_results + tool_calls）
                        if etype == "answer":
                            assistant_text = edata.get("content", "")
                            # specialist_results 在 answer 事件里
                            for s in edata.get("specialist_results", []):
                                specialist_results.append({
                                    "agent_key": s.get("agent_key", ""),
                                    "agent_name": s.get("agent", ""),
                                    "analysis_length": len(s.get("analysis", "")),
                                    "analysis_preview": s.get("analysis", "")[:200],
                                })
                            # 工具调用：从 answer 事件的 tool_calls 字段解析
                            # 后端没有独立的 tool_call SSE 事件，工具调用信息嵌入在 answer 事件中
                            ans_tool_calls = edata.get("tool_calls") or []
                            if ans_tool_calls:
                                for tc in ans_tool_calls:
                                    tool_calls.append(tc)

                        # RAG 来源
                        elif etype == "rag_sources":
                            sources = edata.get("sources", [])
                            rag_info = {
                                "sources_count": len(sources),
                                "sources": sources[:5],
                            }

                        # 工具调用（兼容旧版独立事件，新版已合并到 answer）
                        elif etype == "tool_call":
                            tool_calls.append(edata)

                        # 错误
                        elif etype == "error":
                            result["error"] = edata.get("message", "unknown error")

                    except json.JSONDecodeError:
                        continue

        except requests.exceptions.Timeout:
            result["status"] = "timeout"
            result["error"] = f"流式请求超时（{timeout}s）"
            return result

        elapsed = time.time() - t0
        result["elapsed_seconds"] = round(elapsed, 1)
        result["assistant_text"] = assistant_text
        result["assistant_length"] = len(assistant_text)
        result["specialist_results"] = specialist_results
        result["specialist_count"] = len(specialist_results)
        result["rag_info"] = rag_info
        result["tool_calls"] = tool_calls
        result["tool_call_count"] = len(tool_calls)
        result["events_count"] = len(events)
        result["end_time"] = datetime.now().isoformat()
        result["status"] = "completed" if assistant_text else "empty"

    except Exception as e:
        result["status"] = "failed"
        result["error"] = str(e)
        result["end_time"] = datetime.now().isoformat()

    return result


def run_api_case(case: dict) -> dict:
    """执行单条 API 评测。"""
    result = {
        "case_id": case["id"],
        "category": case["category"],
        "title": case["title"],
        "method": case["method"],
        "url": case["url"],
        "expected_quality": case["expected_quality"],
        "start_time": datetime.now().isoformat(),
        "status": "running",
    }

    try:
        t0 = time.time()
        url = f"{BASE_URL}{case['url']}"

        if case["method"] == "GET":
            resp = requests.get(url, params=case.get("params"), timeout=60)
        else:
            resp = requests.post(url, json=case.get("body"), timeout=60)

        elapsed = time.time() - t0
        result["elapsed_seconds"] = round(elapsed, 1)
        result["status_code"] = resp.status_code

        try:
            data = resp.json()
        except Exception:
            data = {"raw_text": resp.text[:500]}

        result["response"] = data
        result["response_size"] = len(resp.text)
        result["status"] = "completed" if resp.status_code == 200 else "http_error"
        result["end_time"] = datetime.now().isoformat()

    except Exception as e:
        result["status"] = "failed"
        result["error"] = str(e)
        result["end_time"] = datetime.now().isoformat()

    return result


def run_rag_suite() -> dict:
    """运行 RAG 评估套件（复用 scripts/rag_eval_suite.py）。"""
    result = {
        "start_time": datetime.now().isoformat(),
        "status": "running",
    }

    try:
        # 直接调用 rag_eval_suite 的 API
        t0 = time.time()
        resp = requests.post(
            f"{BASE_URL}/api/eval/run-suite",
            params={"limit_per_category": 5},  # 每类最多 5 条，控制耗时
            timeout=600,  # 10 分钟超时
        )
        elapsed = time.time() - t0
        result["elapsed_seconds"] = round(elapsed, 1)
        result["status_code"] = resp.status_code

        try:
            data = resp.json()
            result["response"] = data
        except Exception:
            result["response"] = {"raw_text": resp.text[:1000]}

        result["status"] = "completed" if resp.status_code == 200 else "http_error"
        result["end_time"] = datetime.now().isoformat()

    except Exception as e:
        result["status"] = "failed"
        result["error"] = str(e)
        result["end_time"] = datetime.now().isoformat()

    return result


# ══════════════════════════════════════════════════════════════
# 报告生成
# ══════════════════════════════════════════════════════════════

def generate_summary(conv_results, api_results, rag_result, output_dir):
    """生成汇总报告。"""
    lines = [
        "# MIMO 评测报告",
        f"",
        f"**执行时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**LLM**: mimo-v2.5-pro（禁用 deepseek）",
        f"",
        "---",
        "",
        "## 1. 对话评测",
        "",
        f"| 用例 | 类别 | 状态 | 耗时(s) | 专家数 | 工具调用 | 助手回复长度 |",
        f"|---|---|---|---|---|---|---|",
    ]

    for r in conv_results:
        status_icon = "✓" if r["status"] == "completed" else "✗"
        lines.append(
            f"| {r['title']} | {r['category']} | {status_icon} {r['status']} | "
            f"{r.get('elapsed_seconds', '-')} | {r.get('specialist_count', 0)} | "
            f"{r.get('tool_call_count', 0)} | {r.get('assistant_length', 0)} |"
        )

    # 详细结果
    lines.extend([
        "",
        "### 对话详情",
        "",
    ])
    for r in conv_results:
        lines.append(f"#### {r['case_id']} - {r['title']}")
        lines.append(f"- **查询**: {r['query']}")
        lines.append(f"- **状态**: {r['status']}")
        lines.append(f"- **耗时**: {r.get('elapsed_seconds', '-')}s")
        lines.append(f"- **专家数**: {r.get('specialist_count', 0)}")
        if r.get("specialist_results"):
            for s in r["specialist_results"]:
                lines.append(f"  - {s['agent_name']} ({s['agent_key']}): {s['analysis_length']}字")
        lines.append(f"- **工具调用**: {r.get('tool_call_count', 0)} 次")
        if r.get("tool_calls"):
            for tc in r["tool_calls"][:5]:
                lines.append(f"  - {tc.get('tool_name', 'unknown')}: {str(tc.get('arguments', ''))[:80]}")
        lines.append(f"- **RAG**: {r.get('rag_info', {}).get('results_count', 0)} 条结果")
        lines.append(f"- **回复长度**: {r.get('assistant_length', 0)} 字")
        if r.get("error"):
            lines.append(f"- **错误**: {r['error']}")
        lines.append("")

    # API 评测
    lines.extend([
        "---",
        "",
        "## 2. API 评测",
        "",
        f"| 用例 | 类别 | 状态 | 状态码 | 耗时(s) | 响应大小 |",
        f"|---|---|---|---|---|---|",
    ])
    for r in api_results:
        status_icon = "✓" if r["status"] == "completed" else "✗"
        lines.append(
            f"| {r['title']} | {r['category']} | {status_icon} {r['status']} | "
            f"{r.get('status_code', '-')} | {r.get('elapsed_seconds', '-')} | "
            f"{r.get('response_size', 0)} |"
        )

    # RAG 评测
    lines.extend([
        "---",
        "",
        "## 3. RAG 评测",
        "",
    ])
    if rag_result and rag_result.get("status") == "completed":
        rag_data = rag_result.get("response", {})
        if isinstance(rag_data, dict):
            summary = rag_data.get("results", {}).get("_summary", {})
            lines.append(f"- **状态**: {rag_result['status']}")
            lines.append(f"- **耗时**: {rag_result.get('elapsed_seconds', '-')}s")
            if summary:
                lines.append(f"- **平均 Precision**: {summary.get('avg_precision', 'N/A')}")
                lines.append(f"- **平均 Recall**: {summary.get('avg_recall', 'N/A')}")
                lines.append(f"- **平均 MRR**: {summary.get('avg_mrr', 'N/A')}")
                lines.append(f"- **平均 NDCG@5**: {summary.get('avg_ndcg', 'N/A')}")
            # 各类别详情
            for cat, data in rag_data.get("results", {}).items():
                if cat == "_summary":
                    continue
                if isinstance(data, dict):
                    lines.append(f"\n### {cat}")
                    lines.append(f"- Precision: {data.get('avg_precision', 'N/A')}")
                    lines.append(f"- Recall: {data.get('avg_recall', 'N/A')}")
                    lines.append(f"- MRR: {data.get('avg_mrr', 'N/A')}")
                    lines.append(f"- NDCG@5: {data.get('avg_ndcg', 'N/A')}")
                    lines.append(f"- 用例数: {len(data.get('cases', []))}")
    else:
        lines.append(f"- **状态**: {rag_result.get('status', 'N/A') if rag_result else '未执行'}")
        if rag_result and rag_result.get("error"):
            lines.append(f"- **错误**: {rag_result['error']}")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 4. 基线对比")
    lines.append("")
    lines.append("| 指标 | 基线 | 本次 | 变化 |")
    lines.append("|---|---|---|---|")
    lines.append("| RAG Precision | 0.60 | - | - |")
    lines.append("| RAG Recall | 0.87 | - | - |")
    lines.append("")

    report = "\n".join(lines)
    report_path = output_dir / "summary.md"
    report_path.write_text(report, encoding="utf-8")
    return report_path


# ══════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════

def main():
    global BASE_URL
    parser = argparse.ArgumentParser(description="MIMO 评测套件")
    parser.add_argument("--only", choices=["conversation", "api", "rag"], help="只跑某一类评测")
    parser.add_argument("--case", type=str, help="指定用例编号（逗号分隔），仅 conversation/api 有效")
    parser.add_argument("--base-url", default=BASE_URL, help="后端地址")
    args = parser.parse_args()

    BASE_URL = args.base_url

    # 创建输出目录
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = OUTPUT_DIR / timestamp
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"评测结果保存到: {output_dir}")

    # 检查后端
    try:
        r = requests.get(f"{BASE_URL}/api/conversations?page=1&page_size=1", timeout=5)
        if r.status_code != 200:
            print(f"⚠ 后端健康检查失败: {r.status_code}")
            return
        print(f"✓ 后端连通: {BASE_URL}")
    except Exception as e:
        print(f"✗ 后端未启动: {e}")
        return

    conv_results = []
    api_results = []
    rag_result = None

    # 筛选用例
    conv_cases = CONVERSATION_CASES
    api_cases = API_CASES
    if args.case and args.only in ("conversation", None):
        ids = [int(x) for x in args.case.split(",")]
        conv_cases = [CONVERSATION_CASES[i] for i in ids if i < len(CONVERSATION_CASES)]
    if args.case and args.only in ("api", None):
        ids = [int(x) for x in args.case.split(",")]
        api_cases = [API_CASES[i] for i in ids if i < len(API_CASES)]

    # 1. 对话评测
    if args.only in (None, "conversation"):
        print(f"\n{'='*60}")
        print(f"对话评测: {len(conv_cases)} 条")
        print(f"{'='*60}")
        for i, case in enumerate(conv_cases):
            print(f"\n[{i+1}/{len(conv_cases)}] {case['id']} - {case['title']}")
            print(f"  查询: {case['query'][:60]}...")
            result = run_conversation_case(case)
            conv_results.append(result)
            print(f"  状态: {result['status']}, 耗时: {result.get('elapsed_seconds', '-')}s, "
                  f"专家: {result.get('specialist_count', 0)}, 回复: {result.get('assistant_length', 0)}字")

        # 保存
        conv_path = output_dir / "conversation_results.json"
        conv_path.write_text(json.dumps(conv_results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n✓ 对话评测结果已保存: {conv_path}")

    # 2. API 评测
    if args.only in (None, "api"):
        print(f"\n{'='*60}")
        print(f"API 评测: {len(api_cases)} 条")
        print(f"{'='*60}")
        for i, case in enumerate(api_cases):
            print(f"\n[{i+1}/{len(api_cases)}] {case['id']} - {case['title']}")
            result = run_api_case(case)
            api_results.append(result)
            print(f"  状态: {result['status']}, 状态码: {result.get('status_code', '-')}, "
                  f"耗时: {result.get('elapsed_seconds', '-')}s")

        api_path = output_dir / "api_results.json"
        api_path.write_text(json.dumps(api_results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n✓ API 评测结果已保存: {api_path}")

    # 3. RAG 评测
    if args.only in (None, "rag"):
        print(f"\n{'='*60}")
        print(f"RAG 评测套件")
        print(f"{'='*60}")
        rag_result = run_rag_suite()
        print(f"  状态: {rag_result['status']}, 耗时: {rag_result.get('elapsed_seconds', '-')}s")

        rag_path = output_dir / "rag_results.json"
        rag_path.write_text(json.dumps(rag_result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n✓ RAG 评测结果已保存: {rag_path}")

    # 4. 生成汇总报告
    print(f"\n{'='*60}")
    print("生成汇总报告...")
    report_path = generate_summary(conv_results, api_results, rag_result, output_dir)
    print(f"✓ 汇总报告: {report_path}")

    # 简要统计
    print(f"\n{'='*60}")
    print("评测完成！")
    if conv_results:
        ok = sum(1 for r in conv_results if r["status"] == "completed")
        print(f"  对话: {ok}/{len(conv_results)} 成功")
    if api_results:
        ok = sum(1 for r in api_results if r["status"] == "completed")
        print(f"  API: {ok}/{len(api_results)} 成功")
    if rag_result:
        print(f"  RAG: {rag_result['status']}")


if __name__ == "__main__":
    main()
