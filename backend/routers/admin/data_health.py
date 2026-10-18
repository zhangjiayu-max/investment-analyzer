"""数据新鲜度监控 — 各数据源健康状态"""

from fastapi import APIRouter
from db._conn import _get_conn

router = APIRouter()


@router.get("/api/data-health")
def get_data_health():
    """返回各数据源的健康状态（最后更新时间、记录数、距今天数）"""
    conn = _get_conn()
    health = {}

    try:
        # 1. 估值数据
        val = conn.execute(
            "SELECT MAX(snapshot_date) as latest, COUNT(*) as cnt FROM index_valuations"
        ).fetchone()
        health["valuations"] = {
            "label": "估值数据",
            "latest": val["latest"],
            "count": val["cnt"],
            "stale_days": _days_since(val["latest"]),
        }

        # 2. 持仓数据
        holdings = conn.execute("SELECT COUNT(*) as cnt FROM portfolio_holdings").fetchone()
        latest_holding = conn.execute(
            "SELECT MAX(price_updated_at) as latest FROM portfolio_holdings"
        ).fetchone()
        health["holdings"] = {
            "label": "持仓数据",
            "latest": latest_holding["latest"],
            "count": holdings["cnt"],
            "stale_days": _days_since(latest_holding["latest"]),
        }

        # 3. 文章
        articles = conn.execute(
            "SELECT MAX(created_at) as latest, COUNT(*) as cnt FROM articles"
        ).fetchone()
        health["articles"] = {
            "label": "文章",
            "latest": articles["latest"],
            "count": articles["cnt"],
            "stale_days": _days_since(articles["latest"]),
        }

        # 4. 知识库
        kb = conn.execute(
            "SELECT MAX(created_at) as latest, COUNT(*) as cnt FROM knowledge_base"
        ).fetchone()
        health["knowledge"] = {
            "label": "知识库",
            "latest": kb["latest"],
            "count": kb["cnt"],
            "stale_days": _days_since(kb["latest"]),
        }

        # 5. 对话记录
        convs = conn.execute(
            "SELECT MAX(created_at) as latest, COUNT(*) as cnt FROM conversations"
        ).fetchone()
        health["conversations"] = {
            "label": "对话记录",
            "latest": convs["latest"],
            "count": convs["cnt"],
            "stale_days": _days_since(convs["latest"]),
        }

        # 6. 决策档案
        decisions = conn.execute(
            "SELECT MAX(created_at) as latest, COUNT(*) as cnt FROM decision_records"
        ).fetchone()
        health["decisions"] = {
            "label": "决策档案",
            "latest": decisions["latest"],
            "count": decisions["cnt"],
            "stale_days": _days_since(decisions["latest"]),
        }

        # 7. 作者文章
        author = conn.execute(
            "SELECT MAX(created_at) as latest, COUNT(*) as cnt FROM author_articles"
        ).fetchone()
        health["author_articles"] = {
            "label": "作者文章",
            "latest": author["latest"],
            "count": author["cnt"],
            "stale_days": _days_since(author["latest"]),
        }

        # 8. 荐股记录
        reco = conn.execute(
            "SELECT MAX(created_at) as latest, COUNT(*) as cnt FROM recommendations"
        ).fetchone()
        health["recommendations"] = {
            "label": "荐股记录",
            "latest": reco["latest"],
            "count": reco["cnt"],
            "stale_days": _days_since(reco["latest"]),
        }

        # 9. 债市数据
        bond = conn.execute(
            "SELECT MAX(created_at) as latest, COUNT(*) as cnt FROM bond_market_data"
        ).fetchone()
        health["bond_market"] = {
            "label": "债市数据",
            "latest": bond["latest"],
            "count": bond["cnt"],
            "stale_days": _days_since(bond["latest"]),
        }

        # 10. 自选基金
        watchlist = conn.execute(
            "SELECT MAX(updated_at) as latest, COUNT(*) as cnt FROM watchlist"
        ).fetchone()
        health["watchlist"] = {
            "label": "自选基金",
            "latest": watchlist["latest"],
            "count": watchlist["cnt"],
            "stale_days": _days_since(watchlist["latest"]),
        }

        # 持仓覆盖以每个持仓为分母，避免一条新行情掩盖其余缺失。
        holding_rows = conn.execute("SELECT id, fund_code, fund_name, price_updated_at FROM portfolio_holdings").fetchall()
        health["holdings"]["coverage"] = _coverage([
            {"key": str(r["id"]), "label": r["fund_name"] or r["fund_code"], "latest": r["price_updated_at"]}
            for r in holding_rows
        ])
    finally:
        conn.close()

    from db.valuations import list_index_freshness
    index_rows = list_index_freshness()
    # 本地配置的指数集合包含尚无估值的标的。
    conn = _get_conn()
    try:
        expected = conn.execute("SELECT index_code, index_name FROM index_code_mapping").fetchall()
    finally:
        conn.close()
    from db.valuations import normalize_index_code
    indexed = {normalize_index_code(r['index_code']): r for r in index_rows}
    for row in expected:
        indexed.setdefault(normalize_index_code(row['index_code']), dict(row))
    health['valuations']['coverage'] = _coverage([
        {'key': r['index_code'], 'label': r['index_name'], 'latest': r.get('latest_date')}
        for r in indexed.values()
    ])
    health['knowledge']['freshness_kind'] = 'reference_content'
    health['bond_market']['scope'] = 'local_cache_only'
    reference = {'articles', 'knowledge', 'conversations', 'decisions', 'author_articles', 'recommendations', 'watchlist'}
    for key, item in health.items():
        item['freshness_kind'] = 'reference_content' if key in reference else 'market_data'
        if not item['count']:
            item['status'] = 'empty'
        elif 'coverage' in item:
            cov = item['coverage']
            item['status'] = 'fresh' if cov['fresh'] == cov['expected'] else 'partial' if cov['fresh'] else 'stale'
        elif item['stale_days'] is None:
            item['status'] = 'invalid_date'
        elif key in reference:
            item['status'] = 'available'
        else:
            item['status'] = 'fresh' if item['stale_days'] < 3 else 'aging' if item['stale_days'] < 7 else 'stale'
    return {"health": health}


def _days_since(date_str):
    """计算距今天数，返回整数；缺失或无法解析返回 None"""
    if not date_str:
        return None
    from datetime import datetime

    try:
        d = datetime.strptime(str(date_str)[:10], "%Y-%m-%d")
        return max(0, (datetime.now() - d).days)
    except Exception:
        return None


def _coverage(rows):
    counts = {'expected': len(rows), 'fresh': 0, 'stale': 0, 'missing': 0, 'invalid': 0}
    details = []
    for row in rows:
        days = _days_since(row['latest'])
        status = 'missing' if not row['latest'] else 'invalid' if days is None else 'fresh' if days < 3 else 'stale'
        counts[status] += 1
        details.append({**row, 'stale_days': days, 'status': status})
    return {**counts, 'ratio': counts['fresh'] / len(rows) if rows else None, 'items': details}
