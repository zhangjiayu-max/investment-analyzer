"""估值数据利用监测日报脚本。

用法:
    cd backend
    python -m scripts.valuation_usage_report --days 7

输出示例:
    估值数据利用监测日报 (最近7天)
    ================================
    工具查询次数: 1,234
      - 雷牛牛命中: 67.3%
      - 螺丝钉命中: 18.5%
      - 在线兜底: 12.1%
      - 失败: 2.1%

    上下文注入次数: 89
      - 平均覆盖指数: 42
      - 包含过期数据: 12 次

    专家回答引用检测: 356 条
      - 明确引用估值: 78.4%
      - 给了数据但未引用: 15.7% (medium risk)
      - 未给数据却引用: 5.9% (high risk)
"""
import argparse
import sys
from pathlib import Path

# 将 backend 加入路径，支持 python -m scripts.xxx 调用
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.valuation.valuation_monitor import get_valuation_usage_report


def _pct(n: int, total: int) -> str:
    return f"{n / total * 100:.1f}%" if total > 0 else "0.0%"


def main():
    parser = argparse.ArgumentParser(description="估值数据利用监测日报")
    parser.add_argument("--days", type=int, default=7, help="统计天数 (1-90)")
    parser.add_argument("--json", action="store_true", help="输出原始 JSON")
    args = parser.parse_args()

    report = get_valuation_usage_report(days=args.days)

    if args.json:
        import json
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    tool = report.get("tool_queries", {})
    ctx = report.get("context_injections", {})
    ref = report.get("reference_checks", {})
    risks = report.get("risks", [])

    total_tool = tool.get("total", 0)
    source_counts = tool.get("source_counts", {})

    lines = [
        f"估值数据利用监测日报 (最近{report.get('days', args.days)}天)",
        "=" * 40,
        f"工具查询次数: {total_tool:,}",
    ]
    for source, cnt in sorted(source_counts.items(), key=lambda x: -x[1]):
        lines.append(f"  - {source}: {cnt:,} ({_pct(cnt, total_tool)})")
    online = tool.get("online_fallback_count", 0)
    failed = tool.get("failed_count", 0)
    cache_hits = tool.get("cache_hit_count", 0)
    lines.append(f"  - 在线兜底: {online:,} ({_pct(online, total_tool)})")
    lines.append(f"  - 失败: {failed:,} ({_pct(failed, total_tool)})")
    lines.append(f"  - 缓存命中: {cache_hits:,} ({_pct(cache_hits, total_tool)})")

    lines.append("")
    ctx_total = ctx.get("total", 0)
    lines.append(f"上下文注入次数: {ctx_total:,}")
    lines.append(f"  - 平均覆盖指数: {ctx.get('avg_index_count', 0):.1f}")
    lines.append(f"  - 包含过期数据: {ctx.get('expired_count', 0)} 次")

    lines.append("")
    ref_total = ref.get("total", 0)
    ref_count = ref.get("ref_count", 0)
    medium = ref.get("medium_risk", 0)
    high = ref.get("high_risk", 0)
    lines.append(f"专家/综合报告引用检测: {ref_total:,} 条")
    lines.append(f"  - 明确引用估值: {ref_count:,} ({ref.get('ref_rate', 0)}%)")
    lines.append(f"  - 给了数据但未引用: {medium:,} ({_pct(medium, ref_total)}) (medium risk)")
    lines.append(f"  - 未给数据却引用: {high:,} ({_pct(high, ref_total)}) (high risk)")

    if risks:
        lines.append("")
        lines.append("幻觉风险 Top:")
        for i, r in enumerate(risks[:10], 1):
            conv_id = r.get("conv_id") or "-"
            agent = r.get("agent_name", "unknown")
            atype = r.get("analysis_type", "unknown")
            conf = r.get("confidence", "unknown")
            sample = (r.get("sample_text") or "")[:80].replace("\n", " ")
            lines.append(f"  {i}. conv#{conv_id} / {agent}({atype}): [{conf}] {sample}")

    print("\n".join(lines))


if __name__ == "__main__":
    main()
