"""指数深度分析 — 从 analysis.py 提取所有路由"""
import asyncio
import json
import logging
import math
import time
import uuid
from datetime import date, datetime

from fastapi import APIRouter, HTTPException

from db import (
    get_analysis_agent, get_analysis_agent_by_name, list_analysis_agents, update_analysis_agent,
    create_analysis_history, list_analysis_history,
    get_analysis_history_item, get_analysis_history_status,
    update_analysis_history, delete_analysis_history,
    save_prompt_version,
    get_latest_valuation, get_valuation_history, get_best_valuation,
    list_holdings,
    create_async_task, update_async_task, get_async_task, get_running_async_task,
    get_config_float, get_config_int,
)
from services.llm_service import _call_llm, MODEL_ANALYSIS
from services.rag import build_rag_context_with_details, log_rag_search  # 保留向后兼容
from models.analysis import AnalysisRunRequest, AnalysisAgentUpdateRequest
from db.agent_analysis_log import create_analysis_log, complete_analysis_log
from ._shared import inject_rag_context, build_portfolio_facts_with_check
from ._agent_contract import require_analysis_agent, complete_text_analysis
from infra.analysis_errors import build_error_details

logger = logging.getLogger(__name__)
router = APIRouter(tags=["analysis-index-analysis"])

_background_tasks = set()


async def drain_index_analysis_tasks(timeout=195):
    """关闭前给报告保留检索及模型的完整等待预算，避免已返回结果丢失。"""
    tasks = {task for task in _background_tasks if not task.done()}
    if not tasks:
        return
    logger.info('服务关闭前等待 %s 个指数分析完成（最多 %s 秒）', len(tasks), timeout)
    _, pending = await asyncio.wait(tasks, timeout=timeout)
    for task in pending:
        task.cancel('服务重启：指数分析超过关闭等待期限，执行已中断，请重试')
    # 等待取消处理落库，保证历史、日志和异步任务具有一致的终态。
    await asyncio.gather(*tasks, return_exceptions=True)


def _date_status(value, stale_days=3):
    """只接受来源日期，不用抓取时间填补缺失的截至日期。"""
    try:
        parsed = date.fromisoformat(str(value)[:10])
        age = (date.today() - parsed).days
        if age < 0:
            return 'unknown'
        return 'stale' if age > stale_days else 'fresh'
    except (TypeError, ValueError):
        return 'unknown'


def _fetch_online_valuation_block(req, latest_local: dict | None) -> str:
    """按请求主指标补查；JSON 保留真实指标、日期和来源，禁止静默换指标。"""
    from db.config import get_config_bool
    if not get_config_bool('tool.online_valuation_query_enabled', True):
        return ''
    if req.metric_type not in ('市盈率', '市净率'):
        return json.dumps({'warning': f'在线源不支持{req.metric_type}，未以其他指标替代'}, ensure_ascii=False)
    stale_days = get_config_int('index_analysis.online_valuation_stale_days', 3)
    if latest_local and _date_status(latest_local.get('snapshot_date'), stale_days) == 'fresh':
        return ''
    try:
        from db.valuations import fetch_online_valuation
        online = fetch_online_valuation(req.index_code, metric_type=req.metric_type, timeout_ms=4000)
        if not online:
            return json.dumps({'warning': f'{req.metric_type}在线估值未获取到'}, ensure_ascii=False)
        # 缺失指标不能证明是请求指标，拒绝把未知口径冒充主指标。
        if online.get('metric_type') != req.metric_type:
            return json.dumps({'warning': f'在线估值返回指标{online.get("metric_type") or "未知"}与主指标不符，已排除'}, ensure_ascii=False)
        return json.dumps({'online': online}, ensure_ascii=False, default=str)
    except Exception as error:
        return json.dumps({'warning': f'在线估值获取失败：{error}'}, ensure_ascii=False)


def _empty_valuation_evidence(metric):
    return {'primary_metric': metric, 'valuation_asof': None, 'freshness': 'missing',
            'data_quality': 'insufficient', 'latest': None, 'online': None, 'sources': [], 'history_groups': [],
            'history_observation_count': 0, 'warnings': [], 'news': []}


def _valuation_source(row):
    # 来源机构必须由显式元数据证明；图片文件名、文章 URL 不代表机构身份。
    try:
        raw = json.loads(row.get('raw_json') or '{}')
    except (ValueError, TypeError):
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    provider = row.get('provider') or raw.get('provider') or row.get('source') or raw.get('source')
    if provider in ('manual', 'online', 'unknown', 'index_valuations') or not isinstance(provider, str):
        provider = None
    return {'source': provider or '来源未标注', 'provider': provider,
            'source_url': row.get('source_url') or None,
            'source_image': row.get('source_image') or None,
            'asof': row.get('snapshot_date') or row.get('date') or None,
            'metric_type': row.get('metric_type'),
            'unit': '%' if row.get('metric_type') == '股息率' else '倍'}


def _sanitize_valuation_row(row):
    """非法数值保留为缺失证据；低于1%的真实百分位不再次放大。"""
    row = dict(row)
    for field in ('current_value', 'percentile', 'current_point', 'change_pct', 'danger_value',
                  'median', 'opportunity_value', 'max_value', 'min_value', 'avg_value', 'zscore'):
        if field not in row:
            continue
        try:
            value = float(row[field]) if not isinstance(row[field], bool) else math.nan
        except (ValueError, TypeError, OverflowError):
            value = math.nan
        valid = math.isfinite(value)
        if field == 'current_value':
            valid = valid and value > 0
        elif field == 'percentile':
            valid = valid and 0 <= value <= 100
        row[field] = value if valid else None
    return row


def _collect_valuation_evidence(req):
    """主指标按来源分组；days 是观测条数上限，不解释为日历天数。"""
    evidence = _empty_valuation_evidence(req.metric_type)
    if not req.index_code:
        evidence['warnings'].append('未指定指数代码，缺少主指标估值')
        return evidence
    latest = get_best_valuation(req.index_code, metric_type=req.metric_type,
                                query_source="index_analysis", enable_online=False)
    history = get_valuation_history(req.index_code, days=60, metric_type=req.metric_type)
    # 即使上游数据不符合契约，也不能将别的指标混入主指标序列。
    history = [_sanitize_valuation_row(row) for row in history if row.get('metric_type') == req.metric_type]
    usable_history = [row for row in history if row.get('current_value') is not None and row.get('percentile') is not None
                      and _date_status(row.get('snapshot_date')) != 'unknown']
    if len(usable_history) < len(history):
        evidence['warnings'].append('部分历史估值数值或日期无效，已从可用历史观测中排除')
    history = usable_history
    if latest:
        latest = _sanitize_valuation_row(latest)
    if latest and latest.get('metric_type') != req.metric_type:
        latest = None
        evidence['warnings'].append('库内最新估值指标不匹配，已排除')
    grouped = {}
    for row in sorted(history, key=lambda row: row.get('snapshot_date') or ''):
        provenance = _valuation_source(row)
        key = (req.metric_type, provenance['provider'])
        grouped.setdefault(key, {'metric_type': req.metric_type, 'source': provenance['source'],
                                  'comparable': provenance['provider'] is not None,
                                  'observations': []})['observations'].append(row)
    evidence['history_groups'] = list(grouped.values())
    evidence['history_observation_count'] = len(history)
    online_block = _fetch_online_valuation_block(req, latest)
    if online_block:
        online_result = json.loads(online_block)
        if online_result.get('warning'):
            evidence['warnings'].append(online_result['warning'])
        evidence['online'] = _sanitize_valuation_row(online_result['online']) if online_result.get('online') else None
    online = evidence['online']
    candidates = [row for row in (latest, online) if row]
    for row in candidates:
        evidence['sources'].append(_valuation_source(row))
    dated = [row for row in candidates if _date_status(row.get('snapshot_date') or row.get('date')) != 'unknown']
    selected = max(dated, key=lambda row: row.get('snapshot_date') or row.get('date')) if dated else (latest or online)
    evidence['latest'] = selected
    if selected:
        evidence['valuation_asof'] = selected.get('snapshot_date') or selected.get('date') or None
        evidence['freshness'] = _date_status(evidence['valuation_asof'], get_config_int('index_analysis.online_valuation_stale_days', 3))
        if selected.get('current_value') is None:
            evidence['warnings'].append(f'{req.metric_type}当前值缺失或无效')
        if selected.get('percentile') is None:
            evidence['warnings'].append(f'{req.metric_type}历史分位缺失或无效，不可推算低估等级')
        if (evidence['freshness'] == 'fresh' and selected.get('current_value') is not None
                and selected.get('percentile') is not None):
            evidence['data_quality'] = 'sufficient'
        if evidence['freshness'] != 'fresh':
            evidence['warnings'].append(f'主指标估值日期{evidence["valuation_asof"] or "未知"}，状态{evidence["freshness"]}；仅供历史参考')
    else:
        evidence['warnings'].append(f'{req.metric_type}估值缺失，数据不足，不能判断当前低估或给出买卖结论')
    if online and _date_status(online.get('snapshot_date') or online.get('date')) == 'unknown':
        evidence['warnings'].append('在线估值缺少有效来源日期，不能视为最新数据')
    if any(not group['comparable'] for group in grouped.values()):
        evidence['warnings'].append('部分估值来源未标注，不能确认历史口径一致，不得据此计算连续趋势')
    if len(grouped) > 1:
        evidence['warnings'].append('历史按指标与机构来源分组，跨来源百分位不拼接为连续趋势')
    return evidence


def _normalize_news_item(item, default_source):
    return {'title': str(item.get('title') or '').strip(),
            'summary': str(item.get('summary') or item.get('snippet') or '')[:500],
            'url': item.get('url') or item.get('link') or item.get('newsUrl') or None,
            'published_at': item.get('published_at') or item.get('publishTime') or item.get('publish_time') or item.get('time') or item.get('date') or None,
            'source': item.get('source') or item.get('sources') or default_source}


def _collect_index_news(req):
    """保留财经搜索及 MCP 新闻原始日期、链接；无日期不能冒充今日新闻。"""
    items, warnings, raw_context = [], [], []
    index_label = req.index_name or req.index_code
    try:
        from tools import execute_tool
        raw = execute_tool('web_search', {'query': f'{index_label} 最新消息 政策', 'max_results': 5})
        if raw:
            try:
                parsed = json.loads(raw) if isinstance(raw, str) else raw
                rows = parsed if isinstance(parsed, list) else parsed.get('results', parsed.get('items', []))
                items.extend(_normalize_news_item(item, 'web_search') for item in rows if isinstance(item, dict))
                if isinstance(parsed, dict) and parsed.get('error'):
                    warnings.append(f'新闻搜索失败：{parsed["error"]}')
            except (ValueError, TypeError, AttributeError):
                raw_context.append(str(raw))
                warnings.append('部分新闻仅有文本，发布时间及链接未核验')
    except Exception as error:
        warnings.append(f'新闻搜索失败：{error}')
    try:
        from mcp.yingmi_client import get_yingmi_client
        mcp = get_yingmi_client()
        keywords = [index_label, 'A股 市场']
        for word in keywords:
            try:
                raw = mcp.call_tool('SearchFinancialNews', {'keyword': word, 'pageSize': 3})
                for content in raw.get('content', []) if isinstance(raw, dict) else []:
                    if content.get('type') != 'text':
                        continue
                    parsed = json.loads(content['text'])
                    if parsed.get('success'):
                        rows = parsed.get('data', {}).get('items', [])
                        items.extend(_normalize_news_item(item, '盈米 MCP') for item in rows if isinstance(item, dict))
            except Exception as error:
                warnings.append(f'MCP 新闻检索失败：{error}')
    except Exception as error:
        warnings.append(f'MCP 新闻不可用：{error}')
    unique = {}
    for item in items:
        if item['title']:
            unique.setdefault((item['title'], item['url']), item)
    items = list(unique.values())[:12]
    if not items:
        warnings.append('新闻证据缺失，不能编造近期事件或政策')
    elif not any(_date_status(item['published_at']) == 'fresh' for item in items):
        warnings.append('未获取到日期可核验的近期新闻，历史新闻不能作为今日事件')
    if any(not item['url'] or not item['published_at'] for item in items):
        warnings.append('部分新闻缺少链接或发布时间，引用时需标明未核验')
    context = '\n'.join(f'- {item["title"]}（来源：{item["source"]}；发布时间：{item["published_at"] or "未知"}；链接：{item["url"] or "缺失"}）：{item["summary"]}' for item in items)
    return {'items': items, 'context': '\n'.join([context, *raw_context]).strip(), 'warnings': warnings}


async def _bounded_data_call(label, call, timeout=8):
    """同步外部检索在线程执行，等待有界；超时后晚到结果不会写回分析。"""
    try:
        return await asyncio.wait_for(asyncio.to_thread(call), timeout=timeout), ''
    except asyncio.TimeoutError:
        return None, f'{label}超时，相关数据缺失'
    except Exception as error:
        return None, f'{label}获取失败：{error}'


def _analysis_history_dto(item):
    """既有历史字段保留；新记录从估值上下文中恢复结构化证据。"""
    item = dict(item)
    try:
        stored = json.loads(item.get('valuation_context') or '{}')
        evidence = stored.get('evidence') if isinstance(stored, dict) else None
    except (ValueError, TypeError):
        evidence = None
    item['evidence'] = evidence
    item['metric_type'] = evidence.get('primary_metric') if evidence else None
    return item


@router.post("/api/analysis/run")
async def run_analysis(req: AnalysisRunRequest):
    """触发 AI 指数深度分析（后台异步执行）。"""
    agent = require_analysis_agent(
        "指数深度分析师",
        lambda name: get_analysis_agent(req.agent_id) if req.agent_id is not None else get_analysis_agent_by_name(name),
    )

    from db.async_tasks import start_analysis_task
    req.index_code = req.index_code.strip()
    req.index_name = req.index_name.strip()
    initial_evidence = _empty_valuation_evidence(req.metric_type)
    started = start_analysis_task(
        "index_analysis", "index_deep_analysis",
        {"index_code": req.index_code, "index_name": req.index_name, "metric_type": req.metric_type, "agent_id": agent["id"]},
        "analysis_history", dict(index_code=req.index_code, index_name=req.index_name,
                                 agent_id=agent["id"], agent_name=agent["name"],
                                 prompt_used=agent.get("system_prompt", "")[:500],
                                 news_context="", valuation_context=json.dumps({"evidence": initial_evidence}, ensure_ascii=False), result="", token_usage=0))
    started.update(index_code=req.index_code, metric_type=req.metric_type, evidence=initial_evidence)
    if started["reused"]:
        # 幂等复用也返回原任务元数据，便于前端恢复对应指数/指标的轮询。
        try:
            existing_task = get_async_task(started["task_id"])
            existing_result = existing_task.get("result") if existing_task else None
            if isinstance(existing_result, str):
                existing_result = json.loads(existing_result or "{}")
            if isinstance(existing_result, dict):
                started.update({k: existing_result[k] for k in ("index_code", "metric_type", "evidence") if k in existing_result})
        except (ValueError, TypeError, KeyError):
            pass
        return started
    history_id, async_task_id = started["id"], started["task_id"]
    update_async_task(async_task_id, result={"history_id": history_id, "index_code": req.index_code,
                                          "metric_type": req.metric_type, "evidence": initial_evidence})
    task = asyncio.create_task(_run_index_analysis_async(history_id, req.dict(), agent, async_task_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return started


async def _run_index_analysis_async(history_id: int, req_data: dict, agent: dict, async_task_id: int = None):
    """覆盖整个后台流程的异常边界，确保来源记录和任务都能终止。"""
    trace_id = f"log_{uuid.uuid4().hex[:12]}"
    start_ts = time.time()
    try:
        await _execute_index_analysis(history_id, req_data, agent, async_task_id, trace_id, start_ts)
    except (Exception, asyncio.CancelledError) as error:
        error_msg = str(error) or "分析任务已取消"
        details = getattr(error, 'analysis_error_details', None) or build_error_details(
            error, stage='background_analysis', trace_id=trace_id,
            elapsed_ms=int((time.time() - start_ts) * 1000))
        if isinstance(error, asyncio.CancelledError) and str(error):
            details['summary'] = error_msg
        logger.error(f"指数分析后台失败 history_id={history_id}: {error_msg}")
        updates = [
            ("analysis_history", lambda: update_analysis_history(history_id, status="error", error_msg=error_msg)),
            ("analysis_log", lambda: complete_analysis_log(
                trace_id=trace_id, status="error", duration_ms=int((time.time() - start_ts) * 1000),
                error_msg=error_msg, error_details=details)),
        ]
        if async_task_id is not None:
            updates.append(("async_task", lambda: update_async_task(async_task_id, status="error", error_msg=error_msg)))
        for target, update in updates:
            try:
                update()
            except Exception as persistence_error:
                logger.error(f"指数分析错误状态写入失败 {target}: {persistence_error}")
        if isinstance(error, asyncio.CancelledError):
            raise


async def _execute_index_analysis(history_id: int, req_data: dict, agent: dict, async_task_id: int,
                                  trace_id: str, _start_ts: float):
    """执行分析；估值、新闻等可选数据保留各自降级策略。"""
    req = AnalysisRunRequest(**req_data)
    index_label = req.index_name or req.index_code

    # 分析记录埋点（running）
    try:
        create_analysis_log(
            trace_id=trace_id, agent_id=agent.get("id"), agent_name=agent.get("name", "指数深度分析师"),
            analysis_type="index_analysis", source_table="analysis_history",
            source_id=history_id, query=f"分析 {index_label}",
            input_summary=f"指数:{req.index_code}",
        )
    except Exception as _e:
        logger.warning(f"create_analysis_log 失败: {_e}")

    # 独立检索并行执行，工作线程避免阻塞 FastAPI 事件循环。
    valuation_data, rag_data, news_data, holdings_data, facts_block = await asyncio.gather(
        _bounded_data_call('估值', lambda: _collect_valuation_evidence(req), timeout=10),
        _bounded_data_call('知识库', lambda: inject_rag_context(
            base_query='指数分析 估值', extra_keywords=req.index_name, caller='index_analysis'), timeout=5),
        _bounded_data_call('新闻', lambda: _collect_index_news(req), timeout=12),
        _bounded_data_call('持仓', list_holdings, timeout=5),
        # 事实层构建失败属于现有治理契约中的任务失败，交由外层终止边界处理。
        asyncio.wait_for(asyncio.to_thread(build_portfolio_facts_with_check), timeout=5),
    )
    evidence = valuation_data[0] or _empty_valuation_evidence(req.metric_type)
    news = news_data[0] or {'context': '', 'items': [], 'warnings': []}
    evidence['news'] = news['items']
    evidence['warnings'].extend(news['warnings'])
    evidence['warnings'].extend(warning for _, warning in (valuation_data, rag_data, news_data, holdings_data) if warning)
    if not rag_data[0]:
        evidence['warnings'].append('知识库参考缺失')
    if evidence['freshness'] == 'missing' and not evidence['warnings']:
        evidence['warnings'].append('主指标估值缺失，数据不足')
    evidence['generated_at'] = datetime.now().astimezone().isoformat(timespec='seconds')
    valuation_context = json.dumps({'evidence': evidence}, ensure_ascii=False, indent=2, default=str)
    rag_context = rag_data[0] or ''
    news_context = news['context']

    # 4. 用户持仓数据
    portfolio_context = ""

    try:
        all_holdings = holdings_data[0] or []
        if all_holdings and (req.index_code or req.index_name):
            matched = [h for h in all_holdings if h.get("index_code") and h["index_code"] == req.index_code]
            if req.index_name:
                keywords = req.index_name.replace("指数", "").strip()
                if keywords:  # 修复：空 keywords 会使 "" in fund_name 恒为 True，错误匹配全部持仓
                    fuzzy_matched = [h for h in all_holdings
                                     if h.get("fund_name") and keywords in h["fund_name"]
                                     and h not in matched]
                    matched.extend(fuzzy_matched)
            if matched:
                portfolio_lines = []
                total_value = sum(h.get("current_value", 0) for h in all_holdings if h.get("current_value"))
                for h in matched:
                    val = h.get("current_value", 0)
                    pnl = h.get("profit_loss", 0)
                    rate = h.get("profit_rate", 0)
                    pct = round(val / total_value * 100, 1) if total_value > 0 else 0
                    status = "盈利" if pnl > 0 else ("亏损" if pnl < 0 else "持平")
                    portfolio_lines.append(
                        f"- {h['fund_name']}({h.get('fund_code','')})："
                        f"市值 ¥{val:,.0f}，占总资产 {pct}%，"
                        f"盈亏 ¥{pnl:,.0f}（{rate:+.2f}%），{status}"
                    )
                portfolio_context = (
                    f"用户当前持有 {len(matched)} 只与{index_label}相关的基金：\n"
                    + "\n".join(portfolio_lines)
                )
    except Exception as e:
        logger.warning(f"持仓数据获取失败: {e}")

    # 6. 拼装 prompt
    full_prompt = agent["system_prompt"]
    full_prompt += (
        f"\n\n## 本次证据使用约束\n主指标：{req.metric_type}。估值截至：{evidence['valuation_asof'] or '未知'}；"
        f"新鲜度：{evidence['freshness']}。历史为观测记录数，不是连续天数。"
        "禁止将不同指标、机构来源的百分位拼接为同一趋势；股息率高分位方向与PE/PB相反。"
        "禁止编造估值、日期、今日行情或新闻。缺失、过期、日期未知的数据只能支持历史或条件分析，"
        "不能据此断言当前低估、当前机会或给出确定的买卖结论。引用新闻须附原日期和链接；"
        "新闻无日期则标注未核验。报告开头说明数据限制。\n"
        + "\n".join(evidence['warnings'])
    )

    # 注入组合约束（按字段语义分段标注，防止 LLM 把历史决策/市场数据误认为持仓）
    if facts_block:
        try:
            facts = json.loads(facts_block)
            # 仅注入真实持仓相关字段 + 约束红线，剔除易致幻的历史字段
            portfolio_relevant = {
                "持仓快照": facts.get("snapshot", {}),
                "组合约束": facts.get("constraints", {}),
            }
            # 市场参考数据单独标注，明确不是持仓
            market_ref = {
                "市场指数估值参考": facts.get("market", {}).get("indices", []),
                "市场状态": facts.get("market_state", {}),
            }
            full_prompt += (
                f"\n\n## 组合约束（系统注入，仅约束加仓红线）\n```json\n"
                f"{json.dumps(portfolio_relevant, ensure_ascii=False, indent=2, default=str)}\n```"
                f"\n\n## 市场参考数据（仅作参考，**不是用户持仓**，禁止当作持仓引用）\n```json\n"
                f"{json.dumps(market_ref, ensure_ascii=False, indent=2, default=str)}\n```"
            )
            # recent_decisions / recent_analyses 不注入（历史数据易致幻觉）
        except Exception:
            # 降级：原整块注入但加显著警告
            full_prompt += (
                f"\n\n## 组合约束（系统注入）\n```json\n{facts_block}\n```\n"
                "⚠️ 注意：以下 JSON 中的 recent_decisions/market 字段不是用户持仓，禁止当作持仓引用。"
            )

    full_prompt += """

## 重要提示：关联性分析
- 如果没有直接关于该指数的新闻，**不要说"没有相关新闻"就结束**
- 分析 A 股大盘、相关板块、宏观政策对该指数的**间接影响**
- 对于港股指数：A 股科技板块动态、南向资金流向、中美关系、美联储政策都会影响
- 对于行业指数：上下游产业链新闻、政策风向、资金轮动都是重要信号
- **从已有新闻中提炼与该指数相关的信息**，而不是只看标题是否包含指数名称"""
    if valuation_context:
        full_prompt += f"\n\n<valuation_data>\n指数估值数据（{index_label}）：\n{valuation_context}\n</valuation_data>"
    if rag_context:
        full_prompt += f"\n\n<knowledge_base>\n知识库检索结果（与{index_label}相关的历史分析和文章）：{rag_context}\n</knowledge_base>"
    if portfolio_context:
        full_prompt += f"\n\n<user_portfolio>\n用户持仓情况（与{index_label}相关）：\n{portfolio_context}\n</user_portfolio>"
    if news_context:
        full_prompt += f"\n\n<latest_news>\n{index_label}相关新闻与政策：\n{news_context}\n</latest_news>"

    # 在模型调用前保存确定性证据，模型失败时历史仍可审计数据时效与来源。
    update_analysis_history(history_id, valuation_context=valuation_context, news_context=news_context)
    try:
        result_text, token_usage = await asyncio.wait_for(complete_text_analysis(
            _call_llm,
            caller="index_deep_analysis",
            model=MODEL_ANALYSIS,
            messages=[
                {"role": "system", "content": full_prompt},
                {"role": "user", "content": f"请对 {index_label} 进行深度分析。"},
            ],
            temperature=get_config_float('llm.temperature_default', 0.3),
            max_tokens=get_config_int('llm.max_tokens_report', 8192),
            trace_id=trace_id,
        ), timeout=180)
        source_names = ', '.join(dict.fromkeys(source['source'] for source in evidence['sources'])) or '缺失'
        quality_label = '数据不足，仅供历史/条件分析' if evidence['data_quality'] != 'sufficient' else '证据截至日期如下'
        evidence_header = (
            f"> {quality_label}。主指标：{req.metric_type}；估值截至：{evidence['valuation_asof'] or '未知'}；来源：{source_names}。\n"
        )
        if evidence['warnings']:
            evidence_header += '> 数据限制：' + '；'.join(evidence['warnings']) + '。\n'
        result_text = evidence_header + '\n' + result_text
    except Exception as e:
        details = getattr(e, 'analysis_error_details', None) or build_error_details(
            e, stage='model_analysis', caller='index_deep_analysis', trace_id=trace_id,
            model=MODEL_ANALYSIS, elapsed_ms=int((time.time() - _start_ts) * 1000))
        raw = str(e) or ""
        # 连接类异常裸信息仅为 "Connection error."，无法还原原因，改用可读摘要；
        # 其余异常（截断/数据错误等）保留原始可读信息。完整异常链已写入 error_details。
        if isinstance(details, dict) and details.get('category') in {
                'connection_refused', 'proxy_error', 'dns_error', 'timeout',
                'connection_interrupted', 'tls_error', 'connection_error'}:
            error_msg = details.get('summary') or raw
        else:
            error_msg = raw
        error_msg = error_msg or "模型分析超时，请稍后重试"
        logger.error('[trace:%s] 指数分析失败 history_id=%s diagnostics=%s',
                     trace_id, history_id, json.dumps(details, ensure_ascii=False))
        update_analysis_history(history_id, status="error", error_msg=error_msg)
        try:
            complete_analysis_log(
                trace_id=trace_id, status="error",
                duration_ms=int((time.time() - _start_ts) * 1000),
                error_msg=error_msg,
                error_details=details,
            )
        except Exception as _e:
            logger.warning(f"complete_analysis_log 失败: {_e}")
        if async_task_id:
            update_async_task(async_task_id, status="error", error_msg=error_msg)
        return

    update_analysis_history(
        history_id,
        prompt_used=full_prompt,
        news_context=news_context,
        valuation_context=valuation_context,
        result=result_text,
        token_usage=token_usage,
        status="done",
        error_msg="",
    )
    try:
        complete_analysis_log(
            trace_id=trace_id, status="done",
            duration_ms=int((time.time() - _start_ts) * 1000),
            token_usage=token_usage,
        )
    except Exception as _e:
        logger.warning(f"complete_analysis_log 失败: {_e}")
    if async_task_id:
        update_async_task(async_task_id, status="done", result={"history_id": history_id, "index_code": req.index_code, "metric_type": req.metric_type, "evidence": evidence, "result": result_text, "token_usage": token_usage}, token_usage=token_usage)

    async def _auto_evaluate():
        try:
            from agent.eval_scorer import evaluate_llm_output
            await evaluate_llm_output(
                query=f"分析 {req.index_name}({req.index_code}) 的估值",
                output=result_text,
                context=f"新闻: {news_context[:300]}\n估值: {valuation_context[:300]}",
                target_type="analysis",
                target_id=history_id,
                analysis_type="index_analysis",
            )
        except Exception as e:
            logger.warning(f"自动质量评估失败: {e}")
    asyncio.create_task(_auto_evaluate())


@router.get("/api/analysis/history")
async def get_analysis_history_api(index_code: str = "", limit: int = 50):
    """获取分析历史列表。"""
    return {"history": [_analysis_history_dto(item) for item in list_analysis_history(index_code or None, limit)]}


@router.get("/api/analysis/history/{history_id}")
async def get_analysis_history_detail_api(history_id: int):
    """获取单条分析历史详情。"""
    item = get_analysis_history_item(history_id)
    if not item:
        raise HTTPException(404, "记录不存在")
    return _analysis_history_dto(item)


@router.get("/api/analysis/history/{history_id}/status")
async def get_analysis_history_status_api(history_id: int):
    """查询指数深度分析执行状态。"""
    item = get_analysis_history_status(history_id)
    if not item:
        raise HTTPException(404, "记录不存在")
    item = _analysis_history_dto(item)
    return {
        "id": item["id"],
        "index_code": item.get("index_code", ""),
        "metric_type": item["metric_type"],
        "evidence": item["evidence"],
        "status": item.get("status", "done"),
        "result": item.get("result", ""),
        "token_usage": item.get("token_usage", 0),
        "error": item.get("error_msg", ""),
    }


@router.delete("/api/analysis/history/{history_id}")
async def delete_analysis_history_api(history_id: int):
    """删除分析历史。"""
    delete_analysis_history(history_id)
    return {"ok": True}


@router.get("/api/analysis-agents")
async def list_analysis_agents_api():
    """获取分析 Agent 列表。"""
    return {"agents": list_analysis_agents()}


@router.put("/api/analysis-agents/{agent_id}")
async def update_analysis_agent_api(agent_id: int, req: AnalysisAgentUpdateRequest):
    """更新分析 Agent 配置。"""
    kwargs = {k: v for k, v in req.dict().items() if v is not None}
    if not kwargs:
        raise HTTPException(400, "无更新内容")
    current = get_analysis_agent(agent_id, include_inactive=True)
    if not current:
        raise HTTPException(404, 'Agent 不存在')
    prompt_changed = False
    if 'system_prompt' in kwargs:
        if current and kwargs['system_prompt'] != current.get('system_prompt'):
            save_prompt_version(agent_id, 'analysis', current['system_prompt'])
            prompt_changed = True
    update_analysis_agent(agent_id, **kwargs)
    if prompt_changed:
        try:
            from agent.regression import run_regression_tests
            asyncio.create_task(run_regression_tests(agent_id, "analysis"))
        except Exception as e:
            logger.warning(f"触发回归测试失败: {e}")
    return {"ok": True}
