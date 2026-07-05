#!/usr/bin/env python3
"""
代码复杂度热力图分析报告生成器
自动运行 radon 分析并生成结构化报告
"""
import json
import subprocess
import re
import os
from collections import defaultdict
from datetime import datetime

BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..")
PROJECT_DIR = os.path.join(BACKEND_DIR, "..")
DOCS_DIR = os.path.join(PROJECT_DIR, "docs")


def run_cmd(cmd: str, cwd: str = None) -> str:
    """运行 shell 命令并返回输出"""
    result = subprocess.run(
        cmd, shell=True, capture_output=True, text=True, cwd=cwd or PROJECT_DIR
    )
    return result.stdout + result.stderr


def parse_cc_output(output: str) -> list[dict]:
    """解析 radon cc 输出，返回函数复杂度列表"""
    results = []
    current_file = None
    for line in output.split("\n"):
        line = line.rstrip()
        if not line:
            continue
        # 文件行: backend/agent/orchestrator.py
        if not line.startswith(" ") and line.endswith(".py"):
            current_file = line.strip()
            continue
        # 函数行:     F 2924:0 orchestrate_stream - F (219)
        m = re.match(
            r"\s+([FMCD])\s+(\d+):(\d+)\s+(\S+)\s+-\s+([ABCDEF])\s+\((\d+)\)",
            line,
        )
        if m and current_file:
            results.append(
                {
                    "file": current_file,
                    "type": m.group(1),  # F=Function, M=Method, C=Class, D=nested
                    "line": int(m.group(2)),
                    "function": m.group(4),
                    "rank": m.group(5),
                    "complexity": int(m.group(6)),
                }
            )
    return results


def parse_mi_output(output: str) -> list[dict]:
    """解析 radon mi 输出"""
    results = []
    for line in output.split("\n"):
        line = line.strip()
        if not line or not line.endswith(")"):
            continue
        m = re.match(r"^(.+?)\s+-\s+([ABCDEF])\s+\(([\d.]+)\)", line)
        if m:
            results.append(
                {
                    "file": m.group(1).strip(),
                    "rank": m.group(2),
                    "mi": float(m.group(3)),
                }
            )
    return results


def get_file_lines() -> list[dict]:
    """获取文件行数"""
    output = run_cmd(
        r'find backend -name "*.py" -exec wc -l {} + | sort -rn | head -25'
    )
    results = []
    for line in output.split("\n"):
        parts = line.strip().split()
        if len(parts) >= 2 and parts[-1] != "total":
            results.append({"file": parts[-1], "lines": int(parts[0])})
    return results


def run_vulture() -> list[dict]:
    """运行 vulture 死代码检测"""
    output = run_cmd(
        "/usr/local/bin/python3 -m vulture backend --min-confidence 80"
    )
    results = []
    for line in output.split("\n"):
        line = line.strip()
        if not line:
            continue
        # backend/agent/orchestrator.py:14: unused import 'cancel_running_agents' (90% confidence)
        m = re.match(r"^(.+?):(\d+):\s+(.+?)\s+\((\d+)%\s+confidence\)", line)
        if m:
            results.append(
                {
                    "file": m.group(1),
                    "line": int(m.group(2)),
                    "issue": m.group(3),
                    "confidence": int(m.group(4)),
                }
            )
    return results


def analyze():
    """主分析函数"""
    print("=" * 80)
    print("  代码复杂度热力图分析报告")
    print(f"  生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    # 1. 圈复杂度
    print("\n📊 正在运行 radon cc (圈复杂度分析)...")
    cc_output = run_cmd(
        "/usr/local/bin/python3 -m radon cc backend -s -n C"
    )
    cc_results = parse_cc_output(cc_output)

    # 2. 可维护性指数
    print("📊 正在运行 radon mi (可维护性指数)...")
    mi_output = run_cmd("/usr/local/bin/python3 -m radon mi backend -s")
    mi_results = parse_mi_output(mi_output)

    # 3. 文件行数
    print("📊 正在统计文件行数...")
    file_lines = get_file_lines()

    # 4. 死代码检测
    print("📊 正在运行 vulture (死代码检测)...")
    vulture_results = run_vulture()

    # === 分析和聚合 ===

    # 按文件聚合复杂度
    file_complexity = defaultdict(list)
    for item in cc_results:
        file_complexity[item["file"]].append(item)

    # 高风险文件（5个重点关注 + 额外发现）
    high_risk_targets = [
        "backend/agent/orchestrator.py",
        "backend/db/portfolio.py",
        "backend/services/rag.py",
        "backend/db/decisions.py",
        "backend/tools/__init__.py",
    ]

    high_risk_files = []
    for f in high_risk_targets:
        funcs = file_complexity.get(f, [])
        lines = next((x["lines"] for x in file_lines if x["file"] == f), 0)
        mi = next((x for x in mi_results if x["file"] == f), None)
        top_funcs = sorted(funcs, key=lambda x: x["complexity"], reverse=True)[:5]
        avg_rank = (
            max(x["rank"] for x in funcs) if funcs else "N/A"
        )  # 最差等级
        high_risk_files.append(
            {
                "file": f,
                "lines": lines,
                "worst_complexity_rank": avg_rank,
                "mi": mi["mi"] if mi else None,
                "mi_rank": mi["rank"] if mi else "N/A",
                "total_complex_functions": len(funcs),
                "top_functions": [
                    {
                        "function": x["function"],
                        "line": x["line"],
                        "rank": x["rank"],
                        "complexity": x["complexity"],
                    }
                    for x in top_funcs
                ],
            }
        )

    # 额外高风险文件（不在重点关注列表但复杂度很高）
    extra_risk_files = []
    for f, funcs in file_complexity.items():
        if f in high_risk_targets:
            continue
        f_lines = next((x["lines"] for x in file_lines if x["file"] == f), 0)
        max_complexity = max(x["complexity"] for x in funcs) if funcs else 0
        if max_complexity >= 40 or f_lines >= 800:
            mi = next((x for x in mi_results if x["file"] == f), None)
            top_funcs = sorted(funcs, key=lambda x: x["complexity"], reverse=True)[:3]
            extra_risk_files.append(
                {
                    "file": f,
                    "lines": f_lines,
                    "max_complexity": max_complexity,
                    "mi": mi["mi"] if mi else None,
                    "mi_rank": mi["rank"] if mi else "N/A",
                    "top_functions": [
                        {
                            "function": x["function"],
                            "line": x["line"],
                            "rank": x["rank"],
                            "complexity": x["complexity"],
                        }
                        for x in top_funcs
                    ],
                }
            )
    extra_risk_files.sort(key=lambda x: x["max_complexity"], reverse=True)

    # Top 30 复杂函数
    top_complex_functions = sorted(
        cc_results, key=lambda x: x["complexity"], reverse=True
    )[:30]

    # F 级函数（最高优先级拆分）
    f_rank_functions = [x for x in cc_results if x["rank"] == "F"]
    f_rank_functions.sort(key=lambda x: x["complexity"], reverse=True)

    # E 级函数
    e_rank_functions = [x for x in cc_results if x["rank"] == "E"]
    e_rank_functions.sort(key=lambda x: x["complexity"], reverse=True)

    # MI 最低的文件
    mi_sorted = sorted(mi_results, key=lambda x: x["mi"])
    worst_mi = mi_sorted[:15]

    # 死代码统计
    vulture_by_file = defaultdict(list)
    for v in vulture_results:
        vulture_by_file[v["file"]].append(v)

    # === 生成建议 ===
    recommendations = []

    for func in f_rank_functions[:10]:
        recommendations.append(
            f"🔴 [{func['file']}:{func['line']}] {func['function']}() "
            f"复杂度 F({func['complexity']})，必须立即拆分"
        )

    for func in e_rank_functions[:10]:
        recommendations.append(
            f"🟠 [{func['file']}:{func['line']}] {func['function']}() "
            f"复杂度 E({func['complexity']})，应优先拆分"
        )

    for mi_item in worst_mi[:5]:
        if mi_item["mi"] < 10:
            recommendations.append(
                f"🟡 [{mi_item['file']}] 可维护性指数 {mi_item['mi']:.1f} "
                f"(等级 {mi_item['rank']})，文件过大或过于复杂，建议拆分模块"
            )

    for f in high_risk_files:
        if f["lines"] > 2000:
            recommendations.append(
                f"🔵 [{f['file']}] {f['lines']} 行，建议按功能域拆分为多个子模块"
            )

    # 死代码建议
    non_test_vulture = [
        v for v in vulture_results if "tests/" not in v["file"]
    ]
    if non_test_vulture:
        recommendations.append(
            f"🟣 检测到 {len(non_test_vulture)} 处死代码（非测试），"
            f"涉及 {len(set(v['file'] for v in non_test_vulture))} 个文件，建议清理"
        )

    # === 构建最终报告 ===
    report = {
        "generated_at": datetime.now().isoformat(),
        "project": "investment-analyzer/backend",
        "summary": {
            "total_python_files": len(file_lines),
            "total_lines": next(
                (x["lines"] for x in file_lines if x["file"] == "total"),
                sum(x["lines"] for x in file_lines),
            ),
            "functions_above_C": len(cc_results),
            "f_rank_functions": len(f_rank_functions),
            "e_rank_functions": len(e_rank_functions),
            "files_with_mi_below_10": len(
                [x for x in mi_results if x["mi"] < 10]
            ),
            "dead_code_items": len(vulture_results),
            "dead_code_non_test": len(non_test_vulture),
        },
        "high_risk_files": high_risk_files,
        "extra_risk_files": extra_risk_files[:10],
        "top_complex_functions": [
            {
                "file": x["file"],
                "function": x["function"],
                "line": x["line"],
                "rank": x["rank"],
                "complexity": x["complexity"],
            }
            for x in top_complex_functions
        ],
        "f_rank_functions": [
            {
                "file": x["file"],
                "function": x["function"],
                "line": x["line"],
                "complexity": x["complexity"],
            }
            for x in f_rank_functions
        ],
        "e_rank_functions": [
            {
                "file": x["file"],
                "function": x["function"],
                "line": x["line"],
                "complexity": x["complexity"],
            }
            for x in e_rank_functions
        ],
        "maintainability_index_worst": [
            {"file": x["file"], "mi": round(x["mi"], 2), "rank": x["rank"]}
            for x in worst_mi
        ],
        "dead_code": {
            "total": len(vulture_results),
            "non_test": len(non_test_vulture),
            "by_file": {
                f: [{"line": v["line"], "issue": v["issue"], "confidence": v["confidence"]} for v in items]
                for f, items in sorted(vulture_by_file.items(), key=lambda x: -len(x[1]))
            },
        },
        "recommendations": recommendations,
    }

    return report


def generate_markdown(report: dict) -> str:
    """生成 Markdown 报告"""
    md = []
    md.append("# 📊 代码复杂度热力图分析报告")
    md.append("")
    md.append(f"> 生成时间: {report['generated_at']}")
    md.append(f"> 项目: `{report['project']}`")
    md.append("")

    # 摘要
    s = report["summary"]
    md.append("## 📈 总览")
    md.append("")
    md.append(f"| 指标 | 数值 |")
    md.append(f"|------|------|")
    md.append(f"| Python 文件数 | {s['total_python_files']} |")
    md.append(f"| 总代码行数 | {s['total_lines']:,} |")
    md.append(f"| C级以上复杂函数 | {s['functions_above_C']} |")
    md.append(f"| 🔴 F级函数（必须拆分） | {s['f_rank_functions']} |")
    md.append(f"| 🟠 E级函数（应优先拆分） | {s['e_rank_functions']} |")
    md.append(f"| MI < 10 的文件 | {s['files_with_mi_below_10']} |")
    md.append(f"| 死代码项（非测试） | {s['dead_code_non_test']} |")
    md.append("")

    # 热力图 - 高风险文件
    md.append("## 🔥 高风险文件热力图")
    md.append("")
    md.append("### 重点关注文件（5大巨型文件）")
    md.append("")
    md.append("| 文件 | 行数 | 最差复杂度 | MI | MI等级 | C+函数数 |")
    md.append("|------|------|-----------|-----|--------|----------|")
    for f in report["high_risk_files"]:
        mi_str = f"{f['mi']:.1f}" if f["mi"] is not None else "N/A"
        md.append(
            f"| `{f['file']}` | {f['lines']:,} | {f['worst_complexity_rank']} | "
            f"{mi_str} | {f['mi_rank']} | {f['total_complex_functions']} |"
        )
    md.append("")

    # 每个高风险文件的 top 函数
    for f in report["high_risk_files"]:
        md.append(f"#### `{f['file']}` Top 复杂函数")
        md.append("")
        md.append("| 函数 | 行号 | 等级 | 复杂度 |")
        md.append("|------|------|------|--------|")
        for func in f["top_functions"]:
            md.append(
                f"| `{func['function']}()` | {func['line']} | "
                f"{func['rank']} | {func['complexity']} |"
            )
        md.append("")

    # 额外高风险文件
    if report["extra_risk_files"]:
        md.append("### 其他高风险文件（行数≥800 或 复杂度≥40）")
        md.append("")
        md.append("| 文件 | 行数 | 最大复杂度 | MI | MI等级 |")
        md.append("|------|------|-----------|-----|--------|")
        for f in report["extra_risk_files"]:
            mi_str = f"{f['mi']:.1f}" if f["mi"] is not None else "N/A"
            md.append(
                f"| `{f['file']}` | {f['lines']:,} | "
                f"{f['max_complexity']} | {mi_str} | {f['mi_rank']} |"
            )
        md.append("")

    # F级函数
    md.append("## 🔴 F级函数（复杂度 51+，必须立即拆分）")
    md.append("")
    md.append("| 文件 | 函数 | 行号 | 复杂度 |")
    md.append("|------|------|------|--------|")
    for f in report["f_rank_functions"]:
        md.append(
            f"| `{f['file']}` | `{f['function']}()` | {f['line']} | "
            f"**{f['complexity']}** |"
        )
    md.append("")

    # E级函数
    md.append("## 🟠 E级函数（复杂度 21-30，应优先拆分）")
    md.append("")
    md.append("| 文件 | 函数 | 行号 | 复杂度 |")
    md.append("|------|------|------|--------|")
    for f in report["e_rank_functions"]:
        md.append(
            f"| `{f['file']}` | `{f['function']}()` | {f['line']} | "
            f"{f['complexity']} |"
        )
    md.append("")

    # 可维护性最差
    md.append("## 💀 可维护性指数最差的文件（Top 15）")
    md.append("")
    md.append("| 文件 | MI | 等级 |")
    md.append("|------|-----|------|")
    for f in report["maintainability_index_worst"]:
        md.append(f"| `{f['file']}` | {f['mi']} | {f['rank']} |")
    md.append("")

    # 死代码
    dc = report["dead_code"]
    md.append("## 🧹 死代码检测（vulture, confidence ≥ 80%）")
    md.append("")
    md.append(
        f"共检测到 **{dc['total']}** 处死代码，其中非测试代码 **{dc['non_test']}** 处。"
    )
    md.append("")
    md.append("### 非测试文件死代码详情")
    md.append("")
    for f, items in dc["by_file"].items():
        if "tests/" in f:
            continue
        md.append(f"#### `{f}`")
        md.append("")
        md.append("| 行号 | 问题 | 置信度 |")
        md.append("|------|------|--------|")
        for item in items:
            md.append(
                f"| {item['line']} | {item['issue']} | {item['confidence']}% |"
            )
        md.append("")

    # 建议
    md.append("## ✅ 拆分建议（按优先级排序）")
    md.append("")
    for i, rec in enumerate(report["recommendations"], 1):
        md.append(f"{i}. {rec}")
    md.append("")

    # 复杂度等级参考
    md.append("## 📖 复杂度等级参考")
    md.append("")
    md.append("| 等级 | 圈复杂度 | 含义 |")
    md.append("|------|----------|------|")
    md.append("| A | 1-5 | 简单，低风险 |")
    md.append("| B | 6-10 | 较简单，低风险 |")
    md.append("| C | 11-15 | 中等复杂度，中等风险 |")
    md.append("| D | 16-20 | 较复杂，较高风险 |")
    md.append("| E | 21-30 | 高复杂度，高风险 |")
    md.append("| F | 31+ | 极高复杂度，必须拆分 |")
    md.append("")
    md.append("| MI 等级 | MI 值 | 含义 |")
    md.append("|---------|-------|------|")
    md.append("| A | 20+ | 高可维护性 |")
    md.append("| B | 10-19 | 中等可维护性 |")
    md.append("| C | <10 | 低可维护性 |")
    md.append("")

    return "\n".join(md)


def main():
    report = analyze()

    # 控制台输出 JSON 摘要
    print("\n" + "=" * 80)
    print("  分析完成 - JSON 报告")
    print("=" * 80)
    print(json.dumps(report, ensure_ascii=False, indent=2))

    # 生成 Markdown 报告
    markdown = generate_markdown(report)

    # 保存到 docs 目录
    os.makedirs(DOCS_DIR, exist_ok=True)
    md_path = os.path.join(DOCS_DIR, "complexity-report-2026-07-05.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(markdown)

    # 同时保存 JSON
    json_path = os.path.join(DOCS_DIR, "complexity-report-2026-07-05.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"\n✅ Markdown 报告已保存: {md_path}")
    print(f"✅ JSON 报告已保存: {json_path}")

    # 打印关键发现摘要
    print("\n" + "=" * 80)
    print("  📋 关键发现摘要")
    print("=" * 80)
    s = report["summary"]
    print(f"  • 总代码行数: {s['total_lines']:,}")
    print(f"  • F级函数（必须拆分）: {s['f_rank_functions']} 个")
    print(f"  • E级函数（应优先拆分）: {s['e_rank_functions']} 个")
    print(f"  • MI < 10 的文件: {s['files_with_mi_below_10']} 个")
    print(f"  • 死代码（非测试）: {s['dead_code_non_test']} 处")
    print(f"\n  Top 5 最需拆分的函数:")
    for i, f in enumerate(report["f_rank_functions"][:5], 1):
        print(f"    {i}. [{f['file']}:{f['line']}] {f['function']}() — 复杂度 {f['complexity']}")
    print(f"\n  共生成 {len(report['recommendations'])} 条拆分建议")


if __name__ == "__main__":
    main()
