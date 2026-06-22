"""重排序器、个性化加权"""
import json
import logging
import re

from .config import get_rag_config, get_rag_config_float

logger = logging.getLogger(__name__)

def _get_reranker():
    """获取 Reranker 模型（延迟加载）。"""
    global _reranker
    if _reranker is None:
        try:
            from sentence_transformers import CrossEncoder
            _reranker = CrossEncoder(_reranker_model_name)
            logger.info(f"Reranker 模型加载成功: {_reranker_model_name}")
        except Exception as e:
            logger.warning(f"Reranker 模型加载失败: {e}")
            _reranker = False
    return _reranker


def rerank_results(query: str, results: list[dict], top_k: int = 5,
                   user_id: str = None) -> list[dict]:
    """对检索结果进行重排序（cross-encoder 相关性 + 用户画像个性化加权）。

    Args:
        query: 用户查询
        results: 检索结果列表
        top_k: 返回前 k 个结果
        user_id: 用户 ID（传入则启用个性化加权：关注品种 boost + 历史高频主题）

    Returns:
        重排序后的结果列表
    """
    if not results or len(results) <= 1:
        return results

    reranker = _get_reranker()
    if not reranker:
        # 无 reranker 时仍可做画像加权
        if user_id:
            _apply_personalization_boost(results, user_id)
            results.sort(key=lambda x: x.get("personal_boost", 0), reverse=True)
        return results[:top_k]

    try:
        # 构建 query-document 对（扩展到 1024 字符，保留更多上下文）
        pairs = [(query, r.get("body", "")[:1024]) for r in results]

        # 计算相关性分数
        scores = reranker.predict(pairs)

        # 将分数添加到结果中
        for i, score in enumerate(scores):
            results[i]["rerank_score"] = float(score)

        # 个性化加权（在 cross-encoder 分数基础上叠加）
        if user_id:
            _apply_personalization_boost(results, user_id)

        # 按总分排序（rerank_score + personal_boost）
        results.sort(key=lambda x: x.get("rerank_score", 0) + x.get("personal_boost", 0), reverse=True)

        return results[:top_k]
    except Exception as e:
        logger.error(f"Rerank 失败: {e}")
        return results[:top_k]


# ── 品种关键词映射（用于个性化加权）──
_ASSET_KEYWORDS = {
    "index": ["指数", "沪深300", "中证500", "创业板", "科创50", "上证50"],
    "fund": ["基金", "ETF", "联接基金"],
    "bond": ["债券", "国债", "债基", "利率债", "信用债"],
    "stock": ["股票", "A股", "个股", "持仓股"],
    "gold": ["黄金", "贵金属", "金价"],
    "cash": ["货币基金", "现金管理", "余额宝"],
}


def _apply_personalization_boost(results: list[dict], user_id: str):
    """根据用户画像（关注品种）和检索历史（高频主题）对结果加权。

    boost 叠加到 result["personal_boost"]，与 cross-encoder 分数相加排序。
    """
    if not user_id or not results:
        return

    def _parse_list(raw):
        if isinstance(raw, list):
            return raw
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, list) else []
            except Exception:
                return [raw] if raw else []
        return []

    # 1. 关注品种关键词
    focus_kw = set()
    try:
        from agent.kyc import get_kyc_profile
        profile = get_kyc_profile(user_id)
        focus = profile.get("focus_assets", [])
        if isinstance(focus, str):
            import json as _json
            try:
                focus = _json.loads(focus)
            except Exception:
                focus = []
        for asset in (focus or []):
            for kw in _ASSET_KEYWORDS.get(asset, []):
                focus_kw.add(kw)
    except Exception:
        pass

    # 2. 历史高频检索主题（rag_logs）
    hot_topics = set()
    try:
        from db import _get_conn
        conn = _get_conn()
        rows = conn.execute(
            "SELECT DISTINCT query FROM rag_logs WHERE query IS NOT NULL AND query != '' "
            "ORDER BY created_at DESC LIMIT 30"
        ).fetchall()
        conn.close()
        for r in rows:
            q = r["query"] if isinstance(r, dict) else (r[0] if r else "")
            for w in str(q).replace("，", " ").split():
                if len(w) >= 2:
                    hot_topics.add(w)
    except Exception:
        pass

    # 3. 反馈驱动的正向/负向模式
    positive_kw = set()
    negative_kw = set()
    profile_kw = set()
    behavior_kw = set()
    try:
        from db.dashboard import get_user_profile
        up = get_user_profile(user_id)
        if up:
            for field, target in [("positive_patterns", positive_kw), ("negative_patterns", negative_kw)]:
                for p in _parse_list(up.get(field, "[]")):
                    if isinstance(p, str) and len(p) >= 2:
                        target.add(p)
            for field in ("fund_usage", "primary_goal", "liquidity_needs", "liabilities_summary"):
                value = up.get(field)
                if isinstance(value, str) and value.strip():
                    profile_kw.add(value.strip())
                    for token in re.split(r"[\s,，。；;、]+", value.strip()):
                        if len(token) >= 2:
                            profile_kw.add(token)
            for bias in _parse_list(up.get("behavior_biases", "[]")):
                if isinstance(bias, str) and bias.strip():
                    behavior_kw.add(bias.strip())
                    for token in re.split(r"[\s,，。；;、]+", bias.strip()):
                        if len(token) >= 2:
                            behavior_kw.add(token)
    except Exception:
        pass

    # 3b. RAG 反馈回流：从 rag_feedback 表聚合最近 30 天的赞/踩数据
    fb_positive_ids = set()
    fb_negative_types = set()
    fb_positive_types = set()
    try:
        from db.eval import get_rag_feedback_stats
        fb_stats = get_rag_feedback_stats(user_id, days=30)
        fb_positive_ids = set(fb_stats.get("positive_ids", []))
        fb_negative_types = set(fb_stats.get("negative_types", {}).keys())
        fb_positive_types = set(fb_stats.get("positive_types", {}).keys())
    except Exception:
        pass

    # 应用加权（温和叠加，避免压过相关性）
    for r in results:
        metadata_text = " ".join([
            " ".join(r.get("limitations") or []) if isinstance(r.get("limitations"), list) else str(r.get("limitations") or ""),
            " ".join(r.get("counterpoints") or []) if isinstance(r.get("counterpoints"), list) else str(r.get("counterpoints") or ""),
            str(r.get("atom_type") or ""),
            str(r.get("evidence_level") or ""),
        ])
        body = ((r.get("body", "") or "") + " " + (r.get("title", "") or "") + " " + metadata_text).lower()
        boost = 0.0
        reasons = []
        if focus_kw:
            hit_focus = sum(1 for kw in focus_kw if kw.lower() in body)
            boost += hit_focus * 0.3  # 每命中一个关注品种词 +0.3
            if hit_focus:
                reasons.append("focus_assets")
        if hot_topics:
            hit_hot = sum(1 for w in hot_topics if w.lower() in body)
            boost += min(hit_hot * 0.1, 0.5)  # 历史主题加权，上限 0.5
            if hit_hot:
                reasons.append("history_topic")
        if positive_kw:
            hit_positive = sum(1 for kw in positive_kw if kw.lower() in body)
            boost += hit_positive * 0.2
            if hit_positive:
                reasons.append("positive_pattern")
        if negative_kw:
            hit_negative = sum(1 for kw in negative_kw if kw.lower() in body)
            boost -= hit_negative * 0.1
            if hit_negative:
                reasons.append("negative_pattern")
        if profile_kw:
            hit_profile = sum(1 for kw in profile_kw if kw.lower() in body)
            boost += min(hit_profile * 0.18, 0.6)
            if hit_profile:
                reasons.append("fund_usage")
        if behavior_kw:
            hit_behavior = sum(1 for kw in behavior_kw if kw.lower() in body)
            boost += min(hit_behavior * 0.16, 0.5)
            if hit_behavior:
                reasons.append("behavior_bias")
        # 反馈回流：赞过的知识 ID 正向加权，踩过的内容类型负向加权
        kid = r.get("reference_id")
        ct = r.get("content_type") or ""
        if kid and fb_positive_ids and int(kid) in fb_positive_ids:
            boost += 0.15
            reasons.append("feedback_liked")
        if ct and fb_negative_types and ct in fb_negative_types:
            boost -= 0.1
            reasons.append("feedback_disliked")
        if ct and fb_positive_types and ct in fb_positive_types:
            boost += 0.05
            reasons.append("feedback_type_liked")
        atom_type = r.get("atom_type") or ""
        evidence_level = r.get("evidence_level") or ""
        if atom_type in {"rule", "principle", "checklist", "user_lesson"}:
            boost += 0.12
            reasons.append("evidence_atom")
        if evidence_level in {"principle", "user_memory", "strong", "verified"}:
            boost += 0.08
            if "evidence_atom" not in reasons:
                reasons.append("evidence_atom")
        if r.get("counterpoints"):
            boost += 0.06
            if "evidence_atom" not in reasons:
                reasons.append("evidence_atom")
        r["personal_boost"] = boost
        r["personal_reasons"] = sorted(set(reasons))


