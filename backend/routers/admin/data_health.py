"""数据新鲜度监控 — 各数据源健康状态"""

from fastapi import APIRouter
from db._conn import _get_conn

router = APIRouter()


@router.get("/api/data-health")
async def get_data_health():
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

    finally:
        conn.close()

    return {"health": health}


def _days_since(date_str):
    """计算距今天数，返回整数；无法解析返回 999"""
    if not date_str:
        return 999
    from datetime import datetime

    try:
        d = datetime.strptime(str(date_str)[:10], "%Y-%m-%d")
        return (datetime.now() - d).days
    except Exception:
        return 999
