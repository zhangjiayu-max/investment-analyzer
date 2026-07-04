"""分析 6/28 以来 AI 对话内容质量 + 多智能体流程健康度。

输出结构化报告到 stdout。
"""
import json
import sqlite3
import re
from pathlib import Path

DB = Path("/Users/xiaoyuer/projects/investment-analyzer/data/valuations.db")


def get_conn():
    return sqlite3.connect(str(DB))


def load_conversations_since(date_str="2026-06-28"):
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM conversations WHERE created_at >= ? ORDER BY id", (date_str,)
    ).fetchall()
    result = []
    for c in rows:
        msgs = conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY id", (c["id"],)
        ).fetchall()
        result.append({"conv": dict(c), "messages": [dict(m) for m in msgs]})
    conn.close()
    return result


def parse_metadata(meta_str):
    if not meta_str:
        return {}
    try:
        return json.loads(meta_str)
    except Exception:
        return {}


def analyze_content(text):
    """分析单条助手回答的内容质量指标。"""
    if not text:
        return {}
    t = text
    return {
        "length": len(t),
        "has_data_ref": bool(re.search(r"\d+(\.\d+)?%|PE|PB|百分位|分位|收益率|涨跌|持仓", t)),
        "data_refs": len(re.findall(r"\d+(\.\d+)?%", t)),
        "has_structure": bool(re.search(r"^#{1,4}\s|^[-*]\s|^\d+\.\s", t, re.M)),
        "structure_marks": len(re.findall(r"^#{1,4}\s|^[-*]\s|^\d+\.\s", t, re.M)),
        "has_risk_note": bool(re.search(r"风险|提示|注意|止损|回撤|波动", t)),
        "has_action": bool(re.search(r"建议|可以|考虑|操作|加仓|减仓|止盈|止损|定投|配置", t)),
        "has_source": bool(re.search(r"来源|数据来源|原文|根据|参考|文章提到|研报", t)),
        "question_count": t.count("？") + t.count("?"),
        "emoji_count": len(re.findall(r"[\U0001F300-\U0001FAFF\U00002600-\U000027BF]", t)),
    }


def analyze_metadata(meta):
    """从 metadata 提取多智能体流程信息。"""
    info = {
        "has_specialists": False,
        "specialist_count": 0,
        "specialist_names": [],
        "has_cross_review": False,
        "cross_review_count": 0,
        "has_arbitration": False,
        "has_tool_calls": False,
        "tool_call_count": 0,
        "tool_names": [],
        "has_rag": False,
        "rag_count": 0,
        "phase_timings": {},
        "complexity": None,
        "duration_ms": None,
    }
    if not isinstance(meta, dict):
        return info

    # specialist_results
    specs = meta.get("specialist_results") or []
    if specs:
        info["has_specialists"] = True
        info["specialist_count"] = len(specs)
        info["specialist_names"] = [s.get("agent") or s.get("agent_name") or s.get("name") or "?" for s in specs if isinstance(s, dict)]
    # cross review
    cr = meta.get("cross_review_results") or []
    if cr:
        info["has_cross_review"] = True
        info["cross_review_count"] = len(cr)
    # arbitration
    if meta.get("arbitration") or meta.get("has_arbitration"):
        info["has_arbitration"] = True
    # tool calls
    tc = meta.get("tool_calls") or []
    if tc:
        info["has_tool_calls"] = True
        info["tool_call_count"] = len(tc)
        info["tool_names"] = list(set([x.get("name", "?") for x in tc if isinstance(x, dict)]))
    # rag: 优先从 metadata 提取，回退到 tool_calls 中检测 search_knowledge
    rag = meta.get("rag_sources") or meta.get("rag") or []
    if rag:
        info["has_rag"] = True
        info["rag_count"] = len(rag) if isinstance(rag, list) else 0
    elif tc:
        # 从 tool_calls 中检测 search_knowledge 调用
        rag_tools = [x for x in tc if isinstance(x, dict) and x.get("name") == "search_knowledge"]
        if rag_tools:
            info["has_rag"] = True
            info["rag_count"] = len(rag_tools)
    # timings
    pt = meta.get("phase_timings") or {}
    if pt:
        info["phase_timings"] = pt
    info["complexity"] = meta.get("complexity")
    info["duration_ms"] = meta.get("duration_ms") or meta.get("total_duration_ms")
    return info


def main():
    convs = load_conversations_since("2026-06-28")
    print(f"\n{'='*70}")
    print(f" 6/28 以来 AI 对话质量分析（共 {len(convs)} 个对话）")
    print(f"{'='*70}\n")

    # ── 1. 对话概览 + 用户问题 ──
    print("【1. 对话概览与用户问题】")
    print("-" * 70)
    for item in convs:
        c = item["conv"]
        user_msgs = [m for m in item["messages"] if m["role"] == "user"]
        asst_msgs = [m for m in item["messages"] if m["role"] == "assistant"]
        u_text = user_msgs[0]["content"] if user_msgs else ""
        a_text = asst_msgs[0]["content"] if asst_msgs else ""
        print(f"  #{c['id']} [{c['created_at'][:10]}] {c['title'][:40]}")
        print(f"     用户问题({len(u_text)}字): {u_text[:90].replace(chr(10),' ')}...")
        print(f"     助手回答({len(a_text)}字)")
    print()

    # ── 2. 助手回答内容质量指标 ──
    print("【2. 助手回答内容质量指标】")
    print("-" * 70)
    print(f"{'cid':>4} {'日期':<11} {'长度':>5} {'数据引用':>8} {'结构化':>7} {'风险提示':>8} {'操作建议':>8} {'来源标注':>8} {'emoji':>6}")
    all_metrics = []
    for item in convs:
        c = item["conv"]
        for m in item["messages"]:
            if m["role"] != "assistant":
                continue
            mt = analyze_content(m["content"])
            mt["cid"] = c["id"]
            mt["day"] = c["created_at"][:10]
            all_metrics.append(mt)
            print(f"#{c['id']:>3} {c['created_at'][:10]} {mt['length']:>5} "
                  f"{'是' if mt['has_data_ref'] else '否':>8} "
                  f"{mt['structure_marks']:>7} "
                  f"{'是' if mt['has_risk_note'] else '否':>8} "
                  f"{'是' if mt['has_action'] else '否':>8} "
                  f"{'是' if mt['has_source'] else '否':>8} "
                  f"{mt['emoji_count']:>6}")
    print()

    # 聚合
    if all_metrics:
        print("【3. 质量趋势聚合】")
        print("-" * 70)
        avg_len = sum(m["length"] for m in all_metrics) / len(all_metrics)
        pct_data = sum(1 for m in all_metrics if m["has_data_ref"]) / len(all_metrics) * 100
        pct_struct = sum(1 for m in all_metrics if m["has_structure"]) / len(all_metrics) * 100
        pct_risk = sum(1 for m in all_metrics if m["has_risk_note"]) / len(all_metrics) * 100
        pct_action = sum(1 for m in all_metrics if m["has_action"]) / len(all_metrics) * 100
        pct_source = sum(1 for m in all_metrics if m["has_source"]) / len(all_metrics) * 100
        avg_emoji = sum(m["emoji_count"] for m in all_metrics) / len(all_metrics)
        print(f"  平均回答长度: {avg_len:.0f} 字")
        print(f"  引用数据占比: {pct_data:.0%}")
        print(f"  结构化排版占比: {pct_struct:.0%} (平均结构标记 {sum(m['structure_marks'] for m in all_metrics)/len(all_metrics):.1f} 处)")
        print(f"  含风险提示占比: {pct_risk:.0%}")
        print(f"  含操作建议占比: {pct_action:.0%}")
        print(f"  标注来源占比: {pct_source:.0%}")
        print(f"  平均 emoji 数: {avg_emoji:.1f}")
        print()

    # ── 4. 多智能体流程健康度 ──
    print("【4. 多智能体流程健康度（从 metadata 提取）】")
    print("-" * 70)
    print(f"{'cid':>4} {'日期':<11} {'专家数':>6} {'交叉审阅':>8} {'仲裁':>6} {'工具调用':>8} {'RAG':>6} {'复杂度':>8} {'耗时':>8}")
    flow_stats = {"spec": 0, "xrv": 0, "arb": 0, "tool": 0, "rag": 0}
    for item in convs:
        c = item["conv"]
        for m in item["messages"]:
            if m["role"] != "assistant":
                continue
            meta = parse_metadata(m["metadata"])
            fi = analyze_metadata(meta)
            if fi["has_specialists"]: flow_stats["spec"] += 1
            if fi["has_cross_review"]: flow_stats["xrv"] += 1
            if fi["has_arbitration"]: flow_stats["arb"] += 1
            if fi["has_tool_calls"]: flow_stats["tool"] += 1
            if fi["has_rag"]: flow_stats["rag"] += 1
            dur = fi["duration_ms"]
            dur_str = f"{dur/1000:.1f}s" if dur else "-"
            print(f"#{c['id']:>3} {c['created_at'][:10]} "
                  f"{fi['specialist_count'] or '-':>6} "
                  f"{'是' if fi['has_cross_review'] else '否':>8} "
                  f"{'是' if fi['has_arbitration'] else '否':>6} "
                  f"{fi['tool_call_count'] or '-':>8} "
                  f"{'是' if fi['has_rag'] else '否':>6} "
                  f"{fi['complexity'] or '-':>8} "
                  f"{dur_str:>8}")
            if fi["specialist_names"]:
                print(f"      参与专家: {', '.join(fi['specialist_names'][:5])}")
            if fi["tool_names"]:
                print(f"      调用工具: {', '.join(fi['tool_names'][:6])}")
    n = len(all_metrics)
    print()
    print(f"  流程触发率（{n}条回答）:")
    print(f"    专家协作: {flow_stats['spec']}/{n} ({flow_stats['spec']/n*100:.0f}%)")
    print(f"    交叉审阅: {flow_stats['xrv']}/{n} ({flow_stats['xrv']/n*100:.0f}%)")
    print(f"    仲裁阶段: {flow_stats['arb']}/{n} ({flow_stats['arb']/n*100:.0f}%)")
    print(f"    工具调用: {flow_stats['tool']}/{n} ({flow_stats['tool']/n*100:.0f}%)")
    print(f"    RAG 检索: {flow_stats['rag']}/{n} ({flow_stats['rag']/n*100:.0f}%)")
    print()

    # ── 5. 与 6/28 之前对比（取 6/20-6/27 的样本）──
    print("【5. 与 6/28 之前对比（6/20-6/27 样本）】")
    print("-" * 70)
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    old_rows = conn.execute(
        """SELECT m.* FROM messages m JOIN conversations c ON m.conversation_id=c.id
           WHERE m.role='assistant' AND c.created_at>='2026-06-20' AND c.created_at<'2026-06-28'
           ORDER BY m.id LIMIT 15""", ()).fetchall()
    old_metrics = [analyze_content(r["content"]) for r in old_rows if r["content"]]
    if old_metrics:
        print(f"  6/20-6/27 样本: {len(old_metrics)} 条")
        print(f"    平均长度: {sum(m['length'] for m in old_metrics)/len(old_metrics):.0f} 字")
        print(f"    引用数据: {sum(1 for m in old_metrics if m['has_data_ref'])/len(old_metrics)*100:.0f}%")
        print(f"    结构化: {sum(1 for m in old_metrics if m['has_structure'])/len(old_metrics)*100:.0f}%")
        print(f"    风险提示: {sum(1 for m in old_metrics if m['has_risk_note'])/len(old_metrics)*100:.0f}%")
        print(f"    操作建议: {sum(1 for m in old_metrics if m['has_action'])/len(old_metrics)*100:.0f}%")
        print(f"    来源标注: {sum(1 for m in old_metrics if m['has_source'])/len(old_metrics)*100:.0f}%")
        print(f"    emoji: {sum(m['emoji_count'] for m in old_metrics)/len(old_metrics):.1f}")
    # 评估数据对比
    old_eval = conn.execute(
        """SELECT COUNT(*) AS c, AVG(auto_score) AS avg_s FROM conversation_evaluations ce
           JOIN conversations c ON ce.conversation_id=c.id WHERE c.created_at>='2026-06-20' AND c.created_at<'2026-06-28'"""
    ).fetchone()
    new_eval = conn.execute(
        """SELECT COUNT(*) AS c, AVG(auto_score) AS avg_s FROM conversation_evaluations ce
           JOIN conversations c ON ce.conversation_id=c.id WHERE c.created_at>='2026-06-28'"""
    ).fetchone()
    print(f"\n  质量评估数据:")
    print(f"    6/20-6/27 自动评估: {old_eval['c']} 条, 平均分 {old_eval['avg_s']:.1f}" if old_eval['c'] and old_eval['avg_s'] else f"    6/20-6/27 自动评估: {old_eval['c']} 条")
    print(f"    6/28后  自动评估: {new_eval['c']} 条")
    conn.close()
    print()

    # ── 6. 内容质量采样（首尾各一条完整内容）──
    print("【6. 内容质量采样（最早 + 最近各一条完整回答）】")
    print("-" * 70)
    if convs:
        for label, item in [("最早", convs[0]), ("最近", convs[-1])]:
            c = item["conv"]
            asst = [m for m in item["messages"] if m["role"] == "assistant"]
            if asst:
                print(f"\n  ── {label} #{c['id']} [{c['created_at'][:10]}] ──")
                print(f"  问题: {[m['content'] for m in item['messages'] if m['role']=='user'][0][:120]}")
                print(f"  回答(前800字):")
                print("  " + asst[0]["content"][:800].replace("\n", "\n  "))
    print()
    print("=" * 70)


if __name__ == "__main__":
    main()
