"""热点分析 + 机会推荐 — 从 dashboard.py 提取"""
import asyncio
import json
import logging
import re
import time

from fastapi import APIRouter, HTTPException

from db import (
    list_valuation_indexes, list_holdings, get_portfolio_diversification,
    get_total_cash_balance, get_analysis_agent,
    create_analysis_history,
    save_recommendations, save_analysis_cache, get_analysis_cache,
    list_recommendations, auto_verify_pending_recommendations,
    save_recommendation_feedback, list_recommendation_feedback,
    adopt_recommendation,
    get_config_int, get_config_float, get_config,
    create_async_task, update_async_task,
)
from db._conn import _get_conn
from services.llm_service import _call_llm, MODEL
from services.market_data import get_index_current_price
from infra.state import track_agent as _track_agent, untrack_agent as _untrack_agent, hot_topics_cache as _hot_topics_cache
from analysis.action_extractor import extract_actions, format_actions_for_response

logger = logging.getLogger(__name__)
router = APIRouter(tags=["analysis-hotspots"])

_background_tasks = set()


@router.post("/api/dashboard/hotspots-analysis")
async def trigger_hotspots_analysis():
    """触发结构化热点分析（异步）。立即返回 task_id，后台执行。"""
    task_id = create_async_task("hotspots_analysis", caller="hotspots_analysis")
    task = asyncio.create_task(_run_hotspots_analysis_async(task_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"task_id": task_id, "status": "running"}


async def _run_hotspots_analysis_async(task_id: int):
    """后台执行热点分析。"""
    try:
        result = await _do_hotspots_analysis()
        update_async_task(task_id, status="done", result=result)
    except Exception as e:
        logging.error(f"热点分析异步任务失败: {e}")
        update_async_task(task_id, status="error", error_msg=str(e))


async def _do_hotspots_analysis():
    """结构化热点分析 — LLM 输出 JSON 推荐。"""
    # 1. 收集今日数据
    from routers.dashboard import get_hot_topics
    news_data = await get_hot_topics()
    news_list = news_data.get("news", [])[:5]
    news_text = "\n".join(
        f"- {n.get('title','')}（{n.get('source','')}）"
        for n in news_list if n.get('title')
    ) if news_list else "暂无新闻"

    # 估值数据 + 可参考指数代码
    try:
        indexes = list_valuation_indexes()
        seen = {}
        for i in indexes:
            code = i.get("index_code", "")
            if code and code not in seen:
                seen[code] = i
        all_indexes = list(seen.values())
        sorted_by_chg = sorted(all_indexes, key=lambda x: x.get('change_pct') if x.get('change_pct') is not None else 0, reverse=True)
        code_ref_text = "\n".join(
            f"- {i['index_name']}（{i['index_code']}）: 当日涨跌={i.get('change_pct',0):+.2f}%, {i.get('metric_type','PE')}={i.get('current_value','?')}, "
            f"百分位={i.get('percentile',100):.0f}%"
            for i in sorted_by_chg
        ) if all_indexes else "暂无指数数据"

        low_val = [i for i in all_indexes if i.get("percentile", 100) < 30]
        high_val = [i for i in all_indexes if i.get("percentile", 100) > 70]
        val_text = (
            f"低估(<30%): {len(low_val)}只, "
            f"高估(>70%): {len(high_val)}只, "
            f"共{len(all_indexes)}只跟踪指数"
        )
    except Exception as e:
        code_ref_text = "暂无"
        val_text = "暂无"

    from config import POLICY_KEYWORDS as policy_keywords
    policy_lines = []
    for n in news_list:
        text = f"{n.get('title','')} {n.get('summary','')}"
        if any(k in text for k in policy_keywords):
            policy_lines.append(
                f"- {n.get('title','')}（{n.get('source','')}）: {n.get('summary','')[:160]}"
            )
    policy_text = "\n".join(policy_lines) if policy_lines else "今日热点新闻中未提取到明确政策线索，需降低政策驱动权重。"

    # 持仓明细 + 概况
    try:
        holdings = list_holdings()
        div = get_portfolio_diversification()
        cash_balance = get_total_cash_balance()

        if holdings:
            holding_lines = []
            for h in holdings[:15]:
                pct = h.get("profit_rate")
                pct_str = f"{pct:+.1%}" if pct is not None else "N/A"
                val = h.get("current_value", 0) or 0
                profit = h.get("profit_loss", 0) or 0
                holding_lines.append(
                    f"- {h['fund_name']}（{h.get('fund_code','')}）: "
                    f"市值{val:.0f}元, 收益率{pct_str}, 盈亏{profit:+.0f}元"
                )
            holding_text = "\n".join(holding_lines)
        else:
            holding_text = "暂无持仓"

        portfolio_text = (
            f"持仓{div.get('holding_count',0)}只基金，"
            f"总市值{div.get('total_value',0):.0f}元，"
            f"累计盈亏{div.get('total_profit',0):+.0f}元，"
            f"可用零钱{cash_balance:.0f}元"
        )
    except Exception:
        holding_text = "暂无"
        portfolio_text = "暂无"

    # 债券
    try:
        from tools import _get_bond_temperature
        bond_raw = json.loads(_get_bond_temperature())
        bond_text = f"债券温度{bond_raw.get('temperature','?')}°，收益率{bond_raw.get('rate','?')}%"
    except Exception:
        bond_text = "暂无"

    # 从 analysis_agents 加载热点分析 prompt
    try:
        agent = get_analysis_agent(13)
        base_prompt = agent["system_prompt"] if agent else ""
    except Exception:
        base_prompt = ""
    if not base_prompt:
        base_prompt = "你是一位专业的A股市场分析专家。请基于以下市场数据分析今日投资机会，输出结构化JSON。\n\n## 输出格式\n返回严格JSON：{\"summary\":\"...\", \"recommendations\":[{\"direction\":\"up|down|watch\",\"index_name\":\"...\",\"index_code\":\"...\",\"reason\":\"...\",\"confidence\":\"high|medium|low\"}]}\n\n## 今日数据："

    prompt = base_prompt + f"""

## 组合约束（系统注入，优先级最高）
"""

    # 注入组合约束
    try:
        from services.portfolio_fact_layer import build_portfolio_facts
        facts = build_portfolio_facts()
        facts_json = json.dumps(facts, ensure_ascii=False, indent=2, default=str)
        prompt += f"""```json
{facts_json}
```

---

"""
    except Exception:
        pass

    prompt += f"""
【今日新闻】（重点关注，这是分析的核心线索）
{news_text}

【政策与未来方向线索】（必须用于机会筛选）
{policy_text}

【可参考指数代码及估值】
{code_ref_text}

【估值分布概览】
{val_text}

【持仓明细】
{holding_text}

【持仓概况】
{portfolio_text}

【债券市场】
{bond_text}

筛选要求：
1. 不要只按估值低排序。机会评分必须综合：政策/产业方向、新闻催化强度、未来 6-24 个月景气度、估值安全边际、与当前持仓/现金的适配度。
2. 对纯粹"低估但缺少催化或政策方向不清"的标的，优先给 watch，不要包装成强机会。
3. 对热门但估值过高、拥挤度高或只有短线消息的标的，必须说明风险，可给 down/watch。
4. reason 需写出政策或未来方向依据；如果没有依据，要明确写"缺少政策/产业趋势支撑"。
5. 返回 JSON 中每个 recommendation 尽量包含 opportunity_score(0-100)、policy_signal、future_direction、valuation_role、risk_note。

请严格按照JSON格式输出分析结果。"""

    uid = f"hotspots_{int(time.time())}"
    _track_agent(uid, "热点分析专家", "市场热点分析")
    try:
        response = await asyncio.wait_for(asyncio.to_thread(lambda: _call_llm(
            caller="hotspots_analysis",
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=get_config_float('llm.temperature_default', 0.3),
            max_tokens=get_config_int('llm.max_tokens_report', 8192),
        )), timeout=120)
        content = response.choices[0].message.content or "{}"
        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        if json_match:
            parsed = json.loads(json_match.group())
        else:
            parsed = json.loads(content)
        recs = parsed.get("recommendations", [])
        index_lookup = {i["index_code"]: i for i in all_indexes} if all_indexes else {}
        for rec in recs:
            code = rec.get("index_code", "")
            if code and code in index_lookup:
                rec["percentile"] = index_lookup[code].get("percentile")
                rec["current_value"] = index_lookup[code].get("current_value")
                rec["metric_type"] = index_lookup[code].get("metric_type")
            rec.setdefault("opportunity_score", 0)
            rec.setdefault("policy_signal", "")
            rec.setdefault("future_direction", "")
            rec.setdefault("valuation_role", "")
            rec.setdefault("risk_note", "")
        if recs:
            try:
                from datetime import datetime
                analysis_id = datetime.now().strftime("hotspots_%Y%m%d_%H%M%S")
                baselines = []
                for rec in recs:
                    bl = get_index_current_price(rec.get("index_code", ""))
                    baselines.append(bl)
                rec_ids = save_recommendations(recs, analysis_id, baselines)
                for i, rid in enumerate(rec_ids):
                    if i < len(recs):
                        recs[i]["id"] = rid
                _conn = _get_conn()
                _conn.execute(
                    "INSERT INTO analysis_history (agent_id, agent_name, prompt_used, news_context, result, token_usage) VALUES (?, ?, ?, ?, ?, ?)",
                    (13, "热点分析专家", base_prompt[:500] if base_prompt else "", news_text[:500], content, 0)
                )
                _conn.commit()
                _conn.close()
            except Exception as e:
                logging.warning(f"保存推荐记录失败: {e}")
        result = {
            "analysis_date": time.strftime("%Y-%m-%d"),
            "summary": parsed.get("summary", ""),
            "recommendations": recs,
            "analysis_text": content,
        }
        # 提取可执行行动
        try:
            holdings = list_holdings() or []
            actions = extract_actions("hotspots", result, holdings)
            result["actions"] = format_actions_for_response(actions)
        except Exception as e:
            logger.warning(f"热点行动提取失败: {e}")
            result["actions"] = []
        if recs:
            try:
                save_analysis_cache("hotspots_latest", result)
            except Exception:
                pass
        return result
    except asyncio.TimeoutError:
        return {"summary": "分析超时，请重试", "recommendations": [], "analysis_text": ""}
    except Exception as e:
        logging.warning(f"热点结构化分析失败: {e}")
        return {"summary": f"分析失败: {str(e)}", "recommendations": [], "analysis_text": ""}
    finally:
        _untrack_agent(uid)


@router.get("/api/dashboard/hotspots-analysis/latest")
async def get_latest_hotspots_analysis():
    """返回最近一次缓存的热点分析结果（刷新页面后还原用）。"""
    cached = get_analysis_cache("hotspots_latest")
    if cached:
        today = time.strftime("%Y-%m-%d")
        if cached.get("analysis_date") != today:
            return {"summary": "", "recommendations": [], "analysis_text": "", "actions": [], "stale": True}
        try:
            conn = _get_conn()
            rows = conn.execute(
                "SELECT id, index_name FROM recommendations WHERE analysis_id LIKE 'hotspots_%' ORDER BY id DESC LIMIT 10"
            ).fetchall()
            conn.close()
            id_map = {r["index_name"]: r["id"] for r in rows}
            for rec in cached.get("recommendations", []):
                if rec.get("index_name") in id_map:
                    rec["id"] = id_map[rec["index_name"]]
        except Exception:
            pass
        try:
            indexes = list_valuation_indexes()
            index_lookup = {}
            for i in indexes:
                code = i.get("index_code", "")
                if code and code not in index_lookup:
                    index_lookup[code] = i
            for rec in cached.get("recommendations", []):
                code = rec.get("index_code", "")
                if code and code in index_lookup and rec.get("percentile") is None:
                    rec["percentile"] = index_lookup[code].get("percentile")
                    rec["current_value"] = index_lookup[code].get("current_value")
                    rec["metric_type"] = index_lookup[code].get("metric_type")
        except Exception:
            pass
        return cached
    try:
        recs = list_recommendations(limit=10)
        if recs:
            return {
                "summary": f"上次分析结果（共{len(recs)}条推荐）",
                "recommendations": recs,
                "analysis_text": "",
                "actions": [],
                "stale": True,
            }
    except Exception:
        pass
    return {"summary": "", "recommendations": [], "analysis_text": "", "actions": []}


@router.post("/api/dashboard/hotspots-relate")
async def hotspots_relate_indexes():
    """热点→指数关联：关键词匹配 + LLM 兜底推理。"""
    from db import list_valuation_indexes, list_holdings
    from routers.dashboard import get_hot_topics

    news_data = await get_hot_topics()
    news_list = news_data.get("news", [])[:6]
    if not news_list:
        return {"items": []}

    indexes = list_valuation_indexes()
    holdings = list_holdings()

    sector_keywords = {
        "半导体": ["芯片", "半导体", "集成电路", "晶圆", "封测"],
        "人工智能": ["AI", "人工智能", "大模型", "算力", "智谱", "GPT", "机器人", "深度学习", "机器学习"],
        "新能源": ["新能源", "光伏", "风电", "储能", "锂电", "电池"],
        "消费": ["消费", "白酒", "食品", "啤酒", "餐饮", "零售", "家电"],
        "医药": ["医药", "医疗", "创新药", "疫苗", "CXO", "中药", "器械"],
        "金融": ["银行", "保险", "券商", "金融", "证券"],
        "地产": ["地产", "房地产", "楼市", "房价", "万科"],
        "军工": ["军工", "国防", "航天", "导弹", "航空"],
        "教育": ["教育", "高考", "培训", "考研", "留学"],
        "体育": ["体育", "世界杯", "奥运", "足球", "赛事", "NBA"],
        "传媒": ["传媒", "游戏", "影视", "短视频", "直播", "出版"],
        "汽车": ["汽车", "新能源车", "电动车", "自动驾驶", "造车"],
        "基建": ["基建", "铁路", "公路", "水利", "城投"],
        "科技": ["科技", "互联网", "云计算", "数据", "5G", "6G", "量子"],
        "农业": ["农业", "种业", "养殖", "猪肉", "粮食"],
        "环保": ["环保", "碳中和", "碳达峰", "绿色", "减排"],
        "有色": ["有色", "铜", "铝", "黄金", "稀土", "锂矿"],
        "化工": ["化工", "石化", "化学", "材料"],
    }

    known_sectors = list(sector_keywords.keys())

    def keyword_match(title, summary):
        text = f"{title} {summary}".lower()
        matched = []
        for sector, keywords in sector_keywords.items():
            for kw in keywords:
                if kw.lower() in text:
                    matched.append(sector)
                    break
        return matched

    async def llm_infer_sectors(title, summary):
        from services.llm_service import _call_llm, MODEL
        prompt = f"""分析以下财经新闻，判断涉及哪些行业/板块。

新闻标题：{title}
新闻摘要：{summary[:200]}

已知行业列表：{', '.join(known_sectors)}

请返回 JSON 格式：
{{"sectors": ["行业1", "行业2"], "reason": "简短说明"}}

要求：
1. sectors 从已知行业中选择，如果没有完全匹配的，可以新增合理的行业名
2. 最多返回 3 个最相关的行业
3. 只返回 JSON，不要其他文字"""

        try:
            response = await asyncio.to_thread(lambda: _call_llm(
                caller="hotspots_relate",
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=get_config_float('llm.temperature_vision', 0.1),
                max_tokens=get_config_int('llm.max_tokens_dashboard_summary', 200),
            ))
            text = response.choices[0].message.content or ""
            import re, json as _json
            match = re.search(r'\{[^{}]+\}', text, re.DOTALL)
            if match:
                parsed = _json.loads(match.group())
                return parsed.get("sectors", [])
        except Exception as e:
            logging.warning(f"[hotspots-relate] LLM 推理失败: {e}")
        return []

    def find_related_indexes(sectors):
        results = []
        for idx in indexes:
            name = (idx.get("index_name") or "").lower()
            for sector in sectors:
                if sector.lower() in name:
                    results.append({
                        "index_code": idx.get("index_code"),
                        "index_name": idx.get("index_name"),
                        "percentile": idx.get("percentile"),
                        "assessment": idx.get("assessment"),
                    })
                    break
        return results

    def find_related_holdings(sectors):
        results = []
        for h in holdings:
            name = (h.get("fund_name") or "").lower()
            for sector in sectors:
                if sector.lower() in name:
                    results.append({
                        "fund_code": h.get("fund_code"),
                        "fund_name": h.get("fund_name"),
                        "current_value": h.get("current_value"),
                    })
                    break
        return results

    llm_tasks = []
    llm_indices = []

    for i, n in enumerate(news_list):
        title = n.get("title", "")
        summary = n.get("summary", "")
        sectors = keyword_match(title, summary)
        if sectors:
            llm_tasks.append(None)
        else:
            llm_tasks.append((title, summary))
            llm_indices.append(i)

    llm_results = {}
    if llm_indices:
        tasks = [llm_infer_sectors(news_list[i]["title"], news_list[i].get("summary", "")) for i in llm_indices]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for idx, result in zip(llm_indices, results):
            llm_results[idx] = result if isinstance(result, list) else []

    items = []
    for i, n in enumerate(news_list):
        title = n.get("title", "")
        summary = n.get("summary", "")
        if llm_tasks[i] is None:
            sectors = keyword_match(title, summary)
            source = "keyword"
        else:
            sectors = llm_results.get(i, [])
            source = "llm"

        related_indexes = find_related_indexes(sectors) if sectors else []
        related_holdings = find_related_holdings(sectors) if sectors else []
        items.append({
            "title": title,
            "sectors": sectors,
            "related_indexes": related_indexes[:5],
            "related_holdings": related_holdings[:3],
            "match_source": source,
        })

    return {"items": items}


@router.get("/api/dashboard/recommendations")
async def list_recommendations_api(limit: int = 50, status: str = ""):
    """列出历史推荐记录。"""
    recs = list_recommendations(limit, status or None)
    return {"recommendations": recs}


@router.get("/api/dashboard/recommendations/auto-verify")
async def auto_verify_recommendations():
    """自动验证 pending 推荐：获取实时行情，与基线比较，更新状态。"""
    from datetime import date

    today = date.today().isoformat()
    conn = _get_conn()
    rows = conn.execute(
        "SELECT DISTINCT index_code FROM recommendations WHERE status = 'pending' "
        "AND baseline_value IS NOT NULL AND (verify_after_date IS NULL OR verify_after_date <= ?)",
        (today,),
    ).fetchall()

    has_watch = conn.execute(
        "SELECT COUNT(*) FROM recommendations WHERE status = 'pending' AND direction = 'watch' "
        "AND baseline_value IS NOT NULL AND (verify_after_date IS NULL OR verify_after_date <= ?)",
        (today,),
    ).fetchone()[0]
    conn.close()

    if not rows:
        return {"ok": True, "verified": 0, "results": []}

    price_map = {}
    for row in rows:
        code = row["index_code"]
        bl = get_index_current_price(code)
        if bl.get("price") is not None:
            price_map[code] = bl["price"]

    if not price_map:
        return {"ok": True, "verified": 0, "results": []}

    benchmark_change = None
    if has_watch:
        try:
            hs300 = get_index_current_price(get_config('index.hs300_code', '000300.SH'))
            if hs300.get("price") and hs300.get("baseline"):
                benchmark_change = (hs300["price"] - hs300["baseline"]) / hs300["baseline"] * 100
        except Exception:
            pass

    results = auto_verify_pending_recommendations(
        price_map, today, benchmark_change_pct=benchmark_change, min_change_threshold=2.0
    )
    return {"ok": True, "verified": len(results), "results": results}


@router.get("/api/dashboard/recommendations/stats")
async def recommendations_stats_api():
    """推荐验证统计（含 watch 对比和平局）。"""
    conn = _get_conn()
    total = conn.execute("SELECT COUNT(*) FROM recommendations").fetchone()[0]
    correct = conn.execute("SELECT COUNT(*) FROM recommendations WHERE status = 'correct'").fetchone()[0]
    wrong = conn.execute("SELECT COUNT(*) FROM recommendations WHERE status = 'wrong'").fetchone()[0]
    flat = conn.execute("SELECT COUNT(*) FROM recommendations WHERE status = 'flat'").fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM recommendations WHERE status = 'pending'").fetchone()[0]

    watch_total = conn.execute("SELECT COUNT(*) FROM recommendations WHERE direction = 'watch'").fetchone()[0]
    watch_correct = conn.execute("SELECT COUNT(*) FROM recommendations WHERE direction = 'watch' AND status = 'correct'").fetchone()[0]
    watch_wrong = conn.execute("SELECT COUNT(*) FROM recommendations WHERE direction = 'watch' AND status = 'wrong'").fetchone()[0]

    today = __import__('datetime').date.today().isoformat()
    pending_not_due = conn.execute(
        "SELECT COUNT(*) FROM recommendations WHERE status = 'pending' AND verify_after_date > ?",
        (today,),
    ).fetchone()[0]
    conn.close()

    total_verified = correct + wrong
    accuracy = round(correct / total_verified * 100, 1) if total_verified > 0 else None
    return {
        "total": total,
        "correct": correct,
        "wrong": wrong,
        "flat": flat,
        "pending": pending,
        "pending_not_due": pending_not_due,
        "verified": total_verified,
        "accuracy": accuracy,
        "watch_total": watch_total,
        "watch_correct": watch_correct,
        "watch_wrong": watch_wrong,
    }


@router.post("/api/dashboard/recommendations/{rec_id}/feedback")
async def create_recommendation_feedback(rec_id: int, body: dict):
    """提交推荐反馈（点赞/点踩/评论）。"""
    fid = save_recommendation_feedback(
        recommendation_id=rec_id,
        rating=body.get("rating", "neutral"),
        tags=body.get("tags", ""),
        comment=body.get("comment", ""),
    )
    return {"ok": True, "id": fid}


@router.get("/api/dashboard/recommendations/feedback")
async def list_feedback_api():
    """列出所有推荐反馈。"""
    return {"feedback": list_recommendation_feedback()}


@router.post("/api/recommendations/{rec_id}/adopt")
async def adopt_recommendation_api(rec_id: int, body: dict):
    """P0-A 决策闭环：用户采纳/不采纳某条建议。

    body: {"adopted": 1=已采纳 | -1=未采纳 | 0=取消}
    """
    adopted = int(body.get("adopted", 0))
    result = adopt_recommendation(rec_id, adopted)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("error", "not found"))
    return result


@router.post("/api/recommendations/verify")
async def trigger_recommendation_verify():
    """手动触发推荐验证。"""
    from analysis.recommendation_verifier import verify_all_pending
    result = verify_all_pending(days_ago=7)
    return result


@router.get("/api/recommendations/stats")
async def get_recommendation_stats_api(days: int = 30):
    """获取推荐准确率统计。"""
    from analysis.recommendation_verifier import get_recommendation_stats
    return get_recommendation_stats(days)
