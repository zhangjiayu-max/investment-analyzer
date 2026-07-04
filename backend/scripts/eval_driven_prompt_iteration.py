"""评估数据驱动 prompt 迭代闭环脚本。

从 conversation_evaluations 表中提取低分评测，
按维度归因（execution/data/collaboration/response），
输出改进建议汇总，供人工审核后更新对应专家 prompt。

用法:
    python3 backend/scripts/eval_driven_prompt_iteration.py [--threshold 75] [--days 30]
"""
import json
import sqlite3
import argparse
from pathlib import Path
from collections import defaultdict

DB = Path("/Users/xiaoyuer/projects/investment-analyzer/data/valuations.db")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--threshold", type=float, default=75, help="低分阈值（默认75）")
    parser.add_argument("--days", type=int, default=30, help="回溯天数（默认30）")
    args = parser.parse_args()

    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT ce.*, c.title as conv_title, m.content as message_content
        FROM conversation_evaluations ce
        LEFT JOIN conversations c ON ce.conversation_id = c.id
        LEFT JOIN messages m ON ce.message_id = m.id
        WHERE ce.auto_score IS NOT NULL
          AND ce.auto_score < ?
          AND ce.created_at >= datetime('now', ?)
        ORDER BY ce.auto_score ASC
    """, (args.threshold, f"-{args.days} days")).fetchall()

    print(f"\n{'='*70}")
    print(f" 评估驱动 Prompt 迭代报告")
    print(f" 筛选条件: auto_score < {args.threshold}, 近 {args.days} 天")
    print(f" 低分评测: {len(rows)} 条")
    print(f"{'='*70}\n")

    if not rows:
        print("  ✅ 近期无低分评测，prompt 质量达标。")
        conn.close()
        return

    # 按维度归因
    dimension_issues = defaultdict(list)
    all_suggestions = []

    for r in rows:
        breakdown = json.loads(r["auto_score_breakdown"] or "{}")
        suggestions = json.loads(r["suggestions"] or "[]")

        print(f"  #{r['id']} 对话{r['conversation_id']} 分数: {r['auto_score']:.1f}")
        print(f"     标题: {r['conv_title'][:50] if r['conv_title'] else 'N/A'}")
        print(f"     维度: ", end="")
        for dim, score in breakdown.items():
            status = "✅" if score >= 75 else "⚠️" if score >= 50 else "❌"
            print(f"{dim}={score:.0f}{status} ", end="")
            if score < 75:
                dimension_issues[dim].append({
                    "conv_id": r["conversation_id"],
                    "score": score,
                    "conv_title": r["conv_title"],
                })
        print()
        if suggestions:
            for s in suggestions:
                print(f"     建议: {s}")
                all_suggestions.append(s)
        print()

    # 维度归因汇总
    print(f"\n{'='*70}")
    print(" 维度归因汇总")
    print(f"{'='*70}\n")
    for dim, issues in sorted(dimension_issues.items(), key=lambda x: -len(x[1])):
        avg_score = sum(i["score"] for i in issues) / len(issues)
        print(f"  {dim}: {len(issues)} 次低分 (平均 {avg_score:.0f}分)")
        for i in issues[:3]:
            print(f"    - 对话{i['conv_id']}: {i['score']:.0f}分 ({i['conv_title'][:40] if i['conv_title'] else 'N/A'})")

    # 建议去重统计
    print(f"\n{'='*70}")
    print(" 高频改进建议")
    print(f"{'='*70}\n")
    suggestion_counts = defaultdict(int)
    for s in all_suggestions:
        # 去掉 emoji 前缀做归类
        clean = s.lstrip("🔄🤝🔧⚠️✅❌📍💡 ")
        suggestion_counts[clean] += 1
    for s, count in sorted(suggestion_counts.items(), key=lambda x: -x[1]):
        print(f"  [{count}次] {s}")

    # 输出 prompt 改进建议
    print(f"\n{'='*70}")
    print(" Prompt 改进方向（供人工审核）")
    print(f"{'='*70}\n")

    dim_actions = {
        "execution": "检查专家工具调用链路，优化工具描述和参数解析，减少调用失败",
        "data": "检查专家是否引用了具体数据，强化 prompt 中'必须引用数字'的要求",
        "collaboration": "检查交叉审阅/仲裁是否触发，优化冲突检测和专家分工逻辑",
        "response": "检查回答格式和结构，优化 prompt 中的输出规范部分",
    }
    for dim, issues in sorted(dimension_issues.items(), key=lambda x: -len(x[1])):
        if dim in dim_actions:
            print(f"  [{dim}] {dim_actions[dim]}")
            print(f"          影响对话: {', '.join(str(i['conv_id']) for i in issues[:5])}")
            print()

    conn.close()


if __name__ == "__main__":
    main()
