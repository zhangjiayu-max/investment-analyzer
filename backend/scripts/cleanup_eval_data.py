#!/usr/bin/env python3
"""评测集数据清理脚本

用法：
  python3 scripts/cleanup_eval_data.py --dry-run       # 预览（不执行）
  python3 scripts/cleanup_eval_data.py --execute       # 执行全部清理
  python3 scripts/cleanup_eval_data.py --dedup-only    # 仅去重
  python3 scripts/cleanup_eval_data.py --fill-expected # 仅补 expected_quality（需 LLM）

安全措施：
  - 执行前自动备份到 backups/eval_cleanup_YYYYMMDD.db
  - dry-run 输出详细影响范围
  - 执行后输出统计报告
"""
import sys
import os
import sqlite3
import shutil
from datetime import datetime
from pathlib import Path

# 统一使用 db._conn 的数据库路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db._conn import DB_PATH

BACKUP_DIR = DB_PATH.parent.parent / 'backups'


def get_conn():
    if not DB_PATH.exists():
        print(f'❌ 数据库不存在: {DB_PATH}')
        sys.exit(1)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = OFF')  # 清理脚本关闭外键检查，手动控制顺序
    return conn


def backup_db():
    """备份数据库"""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = BACKUP_DIR / f'eval_cleanup_{ts}.db'
    shutil.copy2(str(DB_PATH), str(backup_path))
    print(f'✅ 已备份: {backup_path} ({backup_path.stat().st_size // 1024}KB)')
    return backup_path


# ── analysis_type → agent_type 映射 ──
ANALYSIS_TO_AGENT = {
    'valuation_expert': 'valuation_expert',
    'specialist:valuation_expert': 'valuation_expert',
    'risk_assessor': 'risk_assessor',
    'specialist:risk_assessor': 'risk_assessor',
    'allocation_advisor': 'allocation_advisor',
    'fund_analyst': 'fund_analyst',
    'market_analyst': 'market_analyst',
    'specialist:arbitrator': 'arbitrator',
    'specialist:macro_strategist': 'macro_strategist',
}


def show_stats(conn, label=''):
    """打印当前统计"""
    print(f'\n{"=" * 60}')
    if label:
        print(f'  {label}')
    print(f'{"=" * 60}')
    tables = ['eval_cases', 'eval_runs', 'expert_performance_alerts']
    for tbl in tables:
        try:
            cnt = conn.execute(f'SELECT COUNT(*) FROM {tbl}').fetchone()[0]
            print(f'  {tbl:40s} {cnt:>6d} 条')
        except sqlite3.OperationalError:
            print(f'  {tbl:40s} (表不存在)')


def dedup_eval_cases(conn, dry_run=True):
    """去重 eval_cases，按 input_params 保留最早一条"""
    print('\n── 去重 eval_cases ──')

    # 找重复
    dupes = conn.execute('''
        SELECT input_params, COUNT(*) as cnt, MIN(id) as keep_id,
               GROUP_CONCAT(id) as all_ids
        FROM eval_cases
        GROUP BY input_params
        HAVING cnt > 1
        ORDER BY cnt DESC
    ''').fetchall()

    total_dupes = sum(r['cnt'] - 1 for r in dupes)
    print(f'  发现 {len(dupes)} 组重复，共 {total_dupes} 条需删除')

    if dupes:
        print(f'  Top 5 重复:')
        for r in dupes[:5]:
            preview = r['input_params'][:60].replace('\n', ' ')
            print(f'    [{r["cnt"]}次] id={r["keep_id"]} (保留) | 删除: {r["all_ids"][len(str(r["keep_id"]))+1:]} | {preview}')

    if dry_run:
        print(f'  [DRY-RUN] 预计 {conn.execute("SELECT COUNT(*) FROM eval_cases").fetchone()[0]} → {conn.execute("SELECT COUNT(*) FROM eval_cases").fetchone()[0] - total_dupes} 条')
        return total_dupes

    # 执行删除
    keep_ids = set()
    for r in conn.execute('SELECT MIN(id) as keep_id FROM eval_cases GROUP BY input_params').fetchall():
        keep_ids.add(r['keep_id'])

    # 识别所有引用 eval_cases(id) 的表
    fk_tables = conn.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table' AND sql LIKE '%eval_cases%'
        AND name != 'eval_cases'
    """).fetchall()
    fk_table_names = [r['name'] for r in fk_tables]
    print(f'  关联表: {fk_table_names}')

    # 先清理所有引用表中的孤儿记录
    for tbl in fk_table_names:
        try:
            cols = [r['name'] for r in conn.execute(f'PRAGMA table_info({tbl})').fetchall()]
            case_col = 'case_id' if 'case_id' in cols else ('eval_case_id' if 'eval_case_id' in cols else None)
            if case_col:
                deleted = conn.execute(
                    f'DELETE FROM {tbl} WHERE {case_col} NOT IN (SELECT id FROM eval_cases)'
                ).rowcount
                if deleted:
                    print(f'    清理 {tbl}: {deleted} 条孤儿记录')
        except sqlite3.OperationalError as e:
            print(f'    跳过 {tbl}: {e}')

    # 再删 eval_cases
    placeholders = ','.join('?' * len(keep_ids))
    deleted_cases = conn.execute(
        f'DELETE FROM eval_cases WHERE id NOT IN ({placeholders})',
        list(keep_ids)
    ).rowcount
    conn.commit()
    print(f'  ✅ 已删除 {deleted_cases} 条重复用例')
    return deleted_cases


def cleanup_error_runs(conn, dry_run=True):
    """清理错误运行"""
    print('\n── 清理错误运行 ──')
    errors = conn.execute("""
        SELECT id, error_msg FROM eval_runs
        WHERE error_msg IN ('id', "name 'cancel_event' is not defined")
           OR error_msg LIKE '%不支持的分析类型%'
           OR (error_msg LIKE '%执行超时%' AND duration_ms > 600000)
    """).fetchall()
    print(f'  发现 {len(errors)} 条错误运行')
    for r in errors[:5]:
        print(f'    id={r["id"]} err={r["error_msg"][:50]}')

    if dry_run or not errors:
        return len(errors)

    deleted = conn.execute("""
        DELETE FROM eval_runs
        WHERE error_msg IN ('id', "name 'cancel_event' is not defined")
           OR error_msg LIKE '%不支持的分析类型%'
           OR (error_msg LIKE '%执行超时%' AND duration_ms > 600000)
    """).rowcount
    conn.commit()
    print(f'  ✅ 已删除 {deleted} 条错误运行')
    return deleted


def dedup_alerts(conn, dry_run=True):
    """清理重复告警"""
    print('\n── 清理重复告警 ──')
    try:
        dupes = conn.execute("""
            SELECT conversation_id, agent_key, alert_type, COUNT(*) as cnt
            FROM expert_performance_alerts
            GROUP BY conversation_id, agent_key, alert_type
            HAVING cnt > 1
        """).fetchall()
        total = sum(r['cnt'] - 1 for r in dupes)
        print(f'  发现 {len(dupes)} 组重复告警，共 {total} 条需删除')

        if dry_run or not dupes:
            return total

        deleted = conn.execute("""
            DELETE FROM expert_performance_alerts
            WHERE rowid NOT IN (
                SELECT MIN(rowid) FROM expert_performance_alerts
                GROUP BY conversation_id, agent_key, alert_type
            )
        """).rowcount
        conn.commit()
        print(f'  ✅ 已删除 {deleted} 条重复告警')
        return deleted
    except sqlite3.OperationalError as e:
        print(f'  ⚠️ {e}')
        return 0


def fill_agent_type(conn, dry_run=True):
    """补全 agent_type"""
    print('\n── 补全 agent_type ──')
    empty = conn.execute("SELECT COUNT(*) FROM eval_cases WHERE agent_type IS NULL OR agent_type = ''").fetchone()[0]
    print(f'  {empty} 条用例 agent_type 为空')

    to_update = []
    for row in conn.execute("SELECT id, analysis_type FROM eval_cases WHERE agent_type IS NULL OR agent_type = ''").fetchall():
        agent_type = ANALYSIS_TO_AGENT.get(row['analysis_type'], '')
        if agent_type:
            to_update.append((agent_type, row['id']))

    print(f'  可补全 {len(to_update)} 条（剩余 {empty - len(to_update)} 条 analysis_type 无映射）')

    if dry_run or not to_update:
        return len(to_update)

    for agent_type, case_id in to_update:
        conn.execute('UPDATE eval_cases SET agent_type = ? WHERE id = ?', (agent_type, case_id))
    conn.commit()
    print(f'  ✅ 已补全 {len(to_update)} 条 agent_type')
    return len(to_update)


def main():
    import argparse
    parser = argparse.ArgumentParser(description='评测集数据清理')
    parser.add_argument('--dry-run', action='store_true', help='预览不执行')
    parser.add_argument('--execute', action='store_true', help='执行清理')
    parser.add_argument('--dedup-only', action='store_true', help='仅去重')
    parser.add_argument('--fill-expected', action='store_true', help='补 expected_quality（需 LLM，未实现）')
    args = parser.parse_args()

    if not any([args.dry_run, args.execute, args.dedup_only, args.fill_expected]):
        parser.print_help()
        return

    conn = get_conn()
    show_stats(conn, '清理前')

    if args.fill_expected:
        print('\n⚠️ --fill-expected 需 LLM 调用，请在前端开启 llm_cost.fill_bad_case_expected 开关后使用')
        return

    dry_run = args.dry_run or not args.execute

    if args.execute:
        backup_db()

    # 执行清理
    dedup_eval_cases(conn, dry_run=dry_run)
    if not args.dedup_only:
        cleanup_error_runs(conn, dry_run=dry_run)
        dedup_alerts(conn, dry_run=dry_run)
        fill_agent_type(conn, dry_run=dry_run)

    show_stats(conn, '清理后' if not dry_run else '预览完成（未执行）')

    if dry_run:
        print('\n💡 确认无误后执行: python3 scripts/cleanup_eval_data.py --execute')

    conn.close()


if __name__ == '__main__':
    main()
