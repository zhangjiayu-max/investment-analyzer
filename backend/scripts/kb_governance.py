"""知识库治理 CLI。

用法（默认全部为预演，加 --apply 才落库）：

    python3 scripts/kb_governance.py --report
    python3 scripts/kb_governance.py --classify
    python3 scripts/kb_governance.py --classify --apply
    python3 scripts/kb_governance.py --all --apply

分步说明：
    --classify   标注 domain / school，修复不可能的 atom_type
    --temporal   补 temporal_type / as_of_date
    --lessons    从真实交易与持仓重建 user_lesson
    --usefulness 重算 usefulness_score（质量先验 + 反馈修正）
    --retrieval  从 rag_logs 统计检索覆盖，找出从未被召回的存量
    --report     只读：治理覆盖率报告
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db._conn import _get_conn  # noqa: E402
from services import knowledge_governance as gov  # noqa: E402
from services import user_lessons  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("kb_governance")


def _print(title: str, payload: dict):
    print(f"\n{'=' * 62}\n{title}\n{'=' * 62}")
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def main() -> int:
    ap = argparse.ArgumentParser(description="知识库治理")
    ap.add_argument("--classify", action="store_true", help="标注 domain/school + 修复 atom_type")
    ap.add_argument("--temporal", action="store_true", help="补 temporal_type/as_of_date")
    ap.add_argument("--lessons", action="store_true", help="重建 user_lesson")
    ap.add_argument("--usefulness", action="store_true", help="重算 usefulness_score")
    ap.add_argument("--retrieval", action="store_true", help="统计检索覆盖")
    ap.add_argument("--index-lessons", action="store_true",
                    help="把 user_lesson 条目写入 FTS + Chroma 索引（新生成的教训可被检索到）")
    ap.add_argument("--report", action="store_true", help="治理覆盖率报告（只读）")
    ap.add_argument("--all", action="store_true", help="依次执行全部治理动作")
    ap.add_argument("--apply", action="store_true", help="实际写入（默认只预演）")
    args = ap.parse_args()

    if not any([args.classify, args.temporal, args.lessons, args.usefulness,
                args.retrieval, args.index_lessons, args.report, args.all]):
        ap.print_help()
        return 1

    dry_run = not args.apply
    conn = _get_conn()  # row_factory 已为 sqlite3.Row

    try:
        # schema 补齐在执行任何读写之前完成：ALTER ADD COLUMN 是纯增列、幂等，
        # 不修改既有数据，因此预演阶段执行也安全（否则 report 读不到新列）。
        added = gov.migrate_schema(conn)
        if added:
            logger.info(f"新增治理字段: {added}（纯增列，不修改既有数据）")

        if args.report:
            _print("治理覆盖率报告", gov.governance_report(conn))

        if args.classify or args.all:
            _print(f"门类与流派标注 (dry_run={dry_run})",
                   gov.classify_entries(conn, dry_run=dry_run))
            gov.invalidate_meta_cache()

        if args.temporal or args.all:
            _print(f"时序标注 (dry_run={dry_run})",
                   gov.annotate_temporal(conn, dry_run=dry_run))

        if args.lessons or args.all:
            _print(f"用户教训重建 (dry_run={dry_run})",
                   user_lessons.sync_lessons(conn, dry_run=dry_run))

        if args.usefulness or args.all:
            _print(f"usefulness 重算 (dry_run={dry_run})",
                   gov.refresh_usefulness(conn, dry_run=dry_run))

        if args.retrieval or args.all:
            _print(f"检索覆盖统计 (dry_run={dry_run})",
                   gov.sync_retrieval_stats(conn, dry_run=dry_run))

        if args.index_lessons or args.all:
            _print(f"教训索引 (dry_run={dry_run})", _index_lessons(conn, dry_run))

        if args.apply:
            print("\n✅ 已落库。")
            print("   书籍/笔记的流派元数据要进入 ChromaDB，需执行 "
                  "`python3 scripts/reindex_vectors.py`（全量重建，耗时较长）。")
            print("   不重建也能生效：检索层从 knowledge_base 实时读取 domain/school。")
        return 0
    finally:
        conn.close()


def _index_lessons(conn, dry_run: bool) -> dict:
    """把 category='user_lesson' 的条目写入 FTS + Chroma，使其可被检索。

    为什么必须单独做：教训条目是直接 INSERT 进 knowledge_base 的，
    绕过了蒸馏脚本的索引环节。不索引就等于写了个没人读得到的文件。
    """
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM knowledge_base WHERE category='user_lesson' ORDER BY id"
    ).fetchall()]
    result = {"lessons": len(ids), "indexed": 0, "failed": [], "dry_run": dry_run}
    if dry_run or not ids:
        return result

    from services.rag import index_knowledge_entry_by_id
    for kid in ids:
        try:
            if index_knowledge_entry_by_id(kid):
                result["indexed"] += 1
            else:
                result["failed"].append(kid)
        except Exception as exc:                      # noqa: BLE001
            logger.warning(f"索引教训 {kid} 失败: {exc}")
            result["failed"].append(kid)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
