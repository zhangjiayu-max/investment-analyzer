"""P0 主动提醒扫描服务 — 决策闭环验证 + 估值阈值 + 持仓风险 + 关注列表信号 + 健康分预警 + 估值查询失败。

由 app.py 的 lifespan 定时调用（默认每 30 分钟）。

包含 6 个扫描函数：
- scan_and_verify_recommendations(): P0-A 建议到达验证窗口时自动验证，并生成结果 alert
- scan_valuation_thresholds(): P0-B 持仓相关指数估值进入极端区域时生成 alert
- scan_portfolio_risk(): P0-B 持仓集中度/亏损超过阈值时生成 alert
- scan_watchlist_signals(): P0-C 关注列表基金触发目标价/估值低分位/单日大跌上车信号
- scan_health_score(): P0-D 今日健康分低于阈值时生成预警
- scan_valuation_failures(): P0-E 估值查询全部失败时生成预警（闭环兜底监控）

开关：
- alerts.proactive_scan_enabled (默认 true)：总开关
- alerts.valuation_low_threshold (默认 20)：分位 < 阈值触发低估提醒
- alerts.valuation_high_threshold (默认 80)：分位 > 阈值触发高估提醒
- alerts.concentration_threshold (默认 30)：单标的占比 > 阈值触发
- alerts.loss_threshold (默认 15)：单标的亏损 > 阈值触发
- alerts.watchlist_signal_enabled (默认 true)：关注列表信号扫描开关
- alerts.watchlist_drop_threshold (默认 3)：单日跌幅%阈值触发上车提醒
- alerts.health_score_scan_enabled (默认 true)：健康分预警扫描开关
- alerts.health_score_threshold (默认 60)：健康分预警阈值
- alerts.valuation_failure_scan_enabled (默认 true)：估值查询失败预警开关
"""
import logging
from datetime import datetime, timedelta, date
from contextvars import ContextVar
import math

from db.config import get_config_bool, get_config_int
from db.portfolio import list_holdings
from db.alert_lifecycle import create_alert
from db.dashboard import (
    list_pending_verification_recommendations,
    auto_verify_pending_recommendations,
)
from db.valuations import get_latest_valuation, get_best_valuation

logger = logging.getLogger(__name__)


# 每次扫描使用独立计数，避免并发手动巡检与后台扫描混淆。
_SCAN_COUNTS = ContextVar('alert_scan_counts', default=None)


def _emit_alert(**kwargs):
    tracker = _SCAN_COUNTS.get()
    existed = False
    if tracker is not None:
        from db._conn import _get_conn
        conn = _get_conn()
        try:
            existed = conn.execute("SELECT id FROM portfolio_alerts WHERE user_id=? AND alert_type=? AND COALESCE(related_fund_code,'')=? LIMIT 1",
                (kwargs.get('user_id','default'),kwargs['alert_type'],kwargs.get('related_fund_code') or '')).fetchone() is not None
        finally:
            conn.close()
    result = create_alert(**kwargs)
    if tracker is not None:
        tracker['updated' if existed else 'generated'] += 1
    return result


def _number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError,ValueError):
        return None


def _price_fact_is_current(value, as_of=None):
    """净值所属日允许上一交易日；周末不要求自然日当天净值。读取已缓存交易日历，未缓存时以周末回退。"""
    try:
        fact = date.fromisoformat(str(value)[:10]); today = as_of or date.today()
    except (TypeError,ValueError):
        return False
    if fact > today:
        return False
    from mcp import trading_calendar
    cached_days = trading_calendar._trade_days
    def is_trading_day(day):
        return day in cached_days if cached_days else day.weekday() < 5
    latest = today - timedelta(days=1)
    # 交易日收盘前通常仍以昨日确认净值为最新事实；节假日由本地日历处理。
    while not is_trading_day(latest):
        latest -= timedelta(days=1)
    return fact >= latest


def _valuation_current(val):
    if not val or val.get('is_expired') or val.get('is_stale') or val.get('stale'):
        return False
    stamp = val.get('snapshot_date') or val.get('valuation_date') or val.get('date')
    return _price_fact_is_current(stamp)


def _coverage(kinds, expected):
    return {kind:{'expected':expected,'checked':0,'status':'complete','errors':[]} for kind in kinds}


def _gap(coverage, kind, reason):
    coverage[kind]['status']='incomplete'
    coverage[kind]['errors'].append(str(reason))


def _finish_risk_scan(coverage, observed, alerts_created, source='alert_scanner'):
    from db.alert_lifecycle import resolve_completed_scan
    complete = [kind for kind, data in coverage.items()
                if data['status']=='complete' and data['checked']==data['expected']]
    for kind,data in coverage.items():
        if data['checked']<data['expected']:
            data['status']='incomplete'
    resolved = resolve_completed_scan(complete, observed, source=source, success=True,
                                     evidence={'coverage':coverage,'observed_at':datetime.now().isoformat()})
    failed = sum(len(data['errors']) for data in coverage.values())
    return {'alerts_created':alerts_created,'generated':alerts_created,'updated':0,'resolved':resolved,
            'failed':failed,'skipped':sum(data['expected']-data['checked'] for data in coverage.values()),
            'coverage':coverage,'status':'incomplete' if failed or len(complete)<len(coverage) else 'complete'}


def _drawdown_facts(payload):
    records = payload.get('nav_history') if isinstance(payload,dict) else payload
    if not isinstance(records,list):
        return None
    valid = sorted((r for r in records if _number(r.get('nav')) is not None and _number(r.get('nav'))>0 and r.get('date')),
                   key=lambda r:r['date'])
    if len(valid)<10:
        return None
    recent=valid[-30:]; peak=max(float(r['nav']) for r in recent); current=float(recent[-1]['nav'])
    return {'drawdown_pct':(peak-current)/peak*100,'peak':peak,'current':current,'fact_date':recent[-1]['date'],'samples':len(valid)}


def _is_enabled(switch: str, default: bool = True) -> bool:
    try:
        return get_config_bool(switch, default)
    except Exception:
        return default


def _get_int(switch: str, default: int) -> int:
    try:
        return get_config_int(switch, default)
    except Exception:
        return default


def _find_related_fund_for_index(index_code: str, holdings: list) -> dict | None:
    """从持仓列表中找到跟踪该指数的基金，用于趋势验证。"""
    for h in holdings:
        if h.get("index_code") == index_code:
            return h
    return None


# ── P0-A 建议验证扫描 ────────────────────────────────────


def scan_and_verify_recommendations() -> dict:
    """扫描到达验证窗口的建议，自动验证并生成结果 alert。

    Returns:
        {"verified": int, "alerts_created": int}
    """
    if not _is_enabled("alerts.proactive_scan_enabled", True):
        return {"verified": 0, "alerts_created": 0, "skipped": "disabled"}

    today = datetime.now().strftime("%Y-%m-%d")
    pending = list_pending_verification_recommendations(today)
    if not pending:
        return {"verified": 0, "alerts_created": 0}

    # 收集所有需要查价的 index_code
    codes = {r.get("index_code") for r in pending if r.get("index_code")}
    price_map = {}
    for code in codes:
        try:
            price = _fetch_current_price(code)
            if price is not None:
                price_map[code] = price
        except Exception as e:
            logger.debug(f"[alert_scanner] 获取 {code} 价格失败: {e}")

    if not price_map:
        logger.info("[alert_scanner] 无可用价格数据，跳过验证")
        return {"verified": 0, "alerts_created": 0}

    results = auto_verify_pending_recommendations(price_map, today)
    alerts_created = 0
    for r in results:
        try:
            status = r.get("status")
            name = r.get("index_name", "标的")
            change = r.get("change_pct")
            if status == "correct":
                title = f"建议验证正确：{name}"
                content = f"你 {today} 前的建议已验证，{name} 涨跌幅 {change}%，判断正确 ✅"
                severity = "info"
            elif status == "wrong":
                title = f"建议验证偏差：{name}"
                content = f"你 {today} 前的建议已验证，{name} 涨跌幅 {change}%，与判断方向相反 ⚠️"
                severity = "warning"
            else:  # flat
                title = f"建议验证：{name} 波动较小"
                content = f"你 {today} 前的建议已验证，{name} 涨跌幅 {change}%，波动较小未达验证阈值"
                severity = "info"
            _emit_alert(
                alert_type="recommendation_verified",
                title=title,
                content=content,
                severity=severity,
                source="alert_scanner",
            )
            alerts_created += 1
        except Exception as e:
            logger.warning(f"[alert_scanner] 生成验证 alert 失败: {e}")

    logger.info(f"[alert_scanner] 验证 {len(results)} 条建议，生成 {alerts_created} 个 alert")
    return {"verified": len(results), "alerts_created": alerts_created}


def _get_preferred_metric_type(index_code: str) -> str:
    """查询本地 index_valuations 表中该指数有哪些 metric_type，返回优先选用的指标。

    优先级：市盈率 > 市净率 > 市销率 > 股息率
    本地完全没有时返回"市盈率"（让 get_best_valuation 走在线兜底）。

    2026-07-20 新增：alert_scanner 之前硬编码"市盈率"导致持仓中只有"市净率/市销率"
    的指数本地查不到，被迫走在线兜底，akshare 不支持国证 H 系 → 触发估值失败预警。
    """
    try:
        from db._conn import _get_conn
        from db.valuations import normalize_index_code
        normalized_code = normalize_index_code(index_code)
        conn = _get_conn()
        rows = conn.execute(
            "SELECT DISTINCT metric_type FROM index_valuations WHERE index_code = ? "
            "AND (current_value IS NOT NULL OR percentile IS NOT NULL)",
            (normalized_code,),
        ).fetchall()
        conn.close()
        local_metrics = [r["metric_type"] for r in rows if r["metric_type"]]
        if not local_metrics:
            return "市盈率"
        # 按优先级返回第一个匹配的
        for preferred in ["市盈率", "市净率", "市销率", "股息率"]:
            if preferred in local_metrics:
                return preferred
        return local_metrics[0]
    except Exception as e:
        logger.debug(f"[alert_scanner] 查询本地 metric_type 失败 {index_code}: {e}")
        return "市盈率"


def _fetch_current_price(index_code: str):
    """获取指数当前价格，回退到估值表的 current_value。"""
    if not index_code:
        return None
    # 优先用 akshare 行情
    try:
        from services.market_data import get_index_current_price
        price = get_index_current_price(index_code)
        if price and price > 0:
            return float(price)
    except Exception as e:
        logger.debug(f"[alert_scanner] market_data 取价失败 {index_code}: {e}")
    # 回退：估值表的 current_value（PE/PB 数值，作为相对基线）
    try:
        val = get_latest_valuation(index_code)
        if val and val.get("current_value"):
            return float(val["current_value"])
    except Exception:
        pass
    return None


# ── P0-B 估值阈值扫描 ────────────────────────────────────


def scan_valuation_thresholds() -> dict:
    """当前估值事实；过期/未知仅记覆盖缺口，不转成买入信号。"""
    if not _is_enabled('alerts.proactive_scan_enabled',True):
        return {'alerts_created':0,'skipped':'disabled'}
    low=_get_int('alerts.valuation_low_threshold',15); high=_get_int('alerts.valuation_high_threshold',85)
    indexes={h['index_code']:h.get('index_name') or h['index_code'] for h in list_holdings() if h.get('index_code')}
    kinds=('valuation_low','valuation_high'); coverage=_coverage(kinds,len(indexes)); observed=[]; created=0
    for code,name in indexes.items():
        try:
            val=get_best_valuation(code,metric_type=_get_preferred_metric_type(code),query_source='alert_scanner',
                                   enable_online=True,allow_metric_fallback=True)
            if not _valuation_current(val) or _number(val.get('percentile')) is None:
                raise ValueError(f'{code}估值过期、日期未知或缺少分位')
            pct=float(val['percentile'])
            for kind in kinds: coverage[kind]['checked']+=1
            kind='valuation_low' if pct<low else 'valuation_high' if pct>high else None
            if not kind: continue
            title=f'{name} 估值分位 {pct:.1f}%（'+('低估参考' if kind=='valuation_low' else '高估风险')+'）'
            content=f"{name}（{code}）估值事实日期 {val.get('snapshot_date')}，分位 {pct:.1f}%。低估不等于买点；先核对现金、集中度与数据完整性。"
            _emit_alert(alert_type=kind,title=title,content=content,severity='info' if kind=='valuation_low' else 'warning',
                        related_fund_code=code,related_fund_name=name,source='alert_scanner',fact_date=val.get('snapshot_date'),
                        recovery_condition=f'估值分位回到 {low}%–{high}% 内',evidence={'percentile':pct,'risk_value':100-pct if kind=='valuation_low' else pct})
            observed.append((kind,code)); created+=1
            # 指数代码只作为事实；没有明确基金和金额时保留观察候选。
            _auto_candidate(code,name,kind,title,content,'info' if kind=='valuation_low' else 'warning',{'percentile':pct,'valuation':val})
        except Exception as exc:
            for kind in kinds: _gap(coverage,kind,f'{code}: {exc}')
    return _finish_risk_scan(coverage,observed,created)


# ── P0-B 持仓风险扫描 ────────────────────────────────────


def scan_portfolio_risk() -> dict:
    """按家庭基金代码汇总集中度，各风险独立检测，缺价不算零或亏损。"""
    if not _is_enabled('alerts.proactive_scan_enabled',True):
        return {'alerts_created':0,'skipped':'disabled'}
    concentration=_get_int('alerts.concentration_threshold',30); loss=_get_int('alerts.loss_threshold',15)
    daily=_get_int('alerts.daily_drop_threshold',4); daily_enabled=_is_enabled('alerts.daily_drop_scan_enabled',True)
    holdings=[h for h in list_holdings() if (_number(h.get('shares')) or 0)>0]
    grouped={}
    for h in holdings:
        grouped.setdefault(h.get('fund_code') or '',[]).append(h)
    kinds=['concentration_high','loss_warning']+(['daily_drop'] if daily_enabled else [])
    coverage=_coverage(kinds,len(grouped)); observed=[]; created=0
    complete_prices=all((_number(h.get('current_price')) or 0)>0 and _price_fact_is_current(h.get('price_updated_at')) for h in holdings)
    total=sum(float(h['shares'])*(_number(h.get('current_price')) or 0) for h in holdings)
    for code,rows in grouped.items():
        name=rows[0].get('fund_name') or code
        valid=all((_number(h.get('current_price')) or 0)>0 and _price_fact_is_current(h.get('price_updated_at')) for h in rows)
        if not valid:
            for kind in kinds: _gap(coverage,kind,f'{code}: 净值缺失或过期')
            continue
        value=sum(float(h['shares'])*float(h['current_price']) for h in rows)
        fact_date=min(str(h['price_updated_at'])[:10] for h in rows)
        def emit(kind,title,content,severity,risk_value,recovery):
            nonlocal created
            _emit_alert(alert_type=kind,title=title,content=content,severity=severity,related_fund_code=code,
                        related_fund_name=name,source='alert_scanner',fact_date=fact_date,
                        recovery_condition=recovery,evidence={'risk_value':risk_value,'fact_date':fact_date,'current_value':value})
            observed.append((kind,code)); created+=1
            _auto_candidate(code,name,kind,title,content,severity,{'risk_value':risk_value,'fact_date':fact_date})
        if complete_prices and total>0:
            weight=value/total*100; coverage['concentration_high']['checked']+=1
            if weight>concentration:
                emit('concentration_high',f'{name} 家庭合计占比 {weight:.1f}%，集中度过高',
                     f'同代码跨账户合计市值 {value:.0f} 元，占投资持仓市值 {weight:.1f}%。',
                     'danger' if weight>=concentration*1.5 else 'warning',weight,f'家庭同代码占投资持仓市值 ≤ {concentration}%')
        else:
            _gap(coverage,'concentration_high','组合净值覆盖不足，集中度分母未知')
        costs=[_number(h.get('cost_price')) for h in rows]
        if all(c is not None and c>0 for c in costs):
            cost=sum(float(h['shares'])*c for h,c in zip(rows,costs)); rate=(value-cost)/cost*100
            coverage['loss_warning']['checked']+=1
            if rate<=-loss:
                weight=value/total*100 if total>0 else 0
                emit('loss_warning',f'{name} 当前浮亏 {abs(rate):.1f}%',_build_risk_content(code,name,weight,None,rate),
                     'danger' if rate<=-loss*2 else 'warning',abs(rate),f'浮亏小于 {loss}% 且价格有效')
        else: _gap(coverage,'loss_warning',f'{code}: 成本缺失')
        if daily_enabled:
            changes=[_number(h.get('today_change_pct')) for h in rows]
            if all(v is not None for v in changes):
                # 跨账户同代码只有相同所属日的净值涨跌才可形成统一事实。
                if len({str(h['price_updated_at'])[:10] for h in rows})>1:
                    _gap(coverage,'daily_drop',f'{code}: 跨账户行情日期不一致'); continue
                change=min(changes); coverage['daily_drop']['checked']+=1
                if change<=-daily:
                    emit('daily_drop',f'{name} 净值所属日下跌 {abs(change):.2f}%',
                         f'净值事实日期 {fact_date}，下跌 {abs(change):.2f}%，阈值 {daily}%。跌幅本身不是补仓依据。',
                         'danger' if change<=-5 else 'warning',abs(change),f'最新有效净值单日跌幅小于 {daily}%')
            else: _gap(coverage,'daily_drop',f'{code}: 当日涨跌幅缺失')
    return _finish_risk_scan(coverage,observed,created)


# ── 亏损+低估联合预警（2026-07-30 conv#194 补强）──────────────────────────
#
# 设计动机：scan_portfolio_risk 的 loss_warning 仅看亏损，scan_valuation_thresholds
# 的 valuation_low 仅看指数估值，两者不交叉。深套+低估的标的（如白酒亏损-26%+估值分位3%）
# 是"必须在低估时补仓才能回本"的典型场景，需要专门的联合预警主动提示补仓窗口。
# 与 smart_add_planner 的信号A（金字塔补仓：亏损≤-10% + 估值分位<60%）逻辑一致，
# 但本预警面向"用户主动感知"，smart_add_planner 面向"补仓计划生成"。


def scan_loss_plus_undervalued() -> dict:
    """严重亏损与低估并列展示；不承诺回本，不把风险事实变成买点。"""
    if not _is_enabled('alerts.loss_plus_undervalued_enabled',True):
        return {'alerts_created':0,'skipped':'disabled'}
    from db.config import get_config_float
    loss=get_config_float('alerts.loss_plus_undervalued_loss_threshold',-15.0)
    threshold=get_config_float('alerts.loss_plus_undervalued_valuation_threshold',30.0)
    holdings=[h for h in list_holdings() if (_number(h.get('shares')) or 0)>0]
    coverage=_coverage(['loss_plus_undervalued'],len(holdings)); observed=[]; created=0
    for h in holdings:
        code=h.get('fund_code') or ''; name=h.get('fund_name') or code; kind='loss_plus_undervalued'
        try:
            price=_number(h.get('current_price')); cost=_number(h.get('cost_price'))
            if not price or not cost or cost<=0 or not _price_fact_is_current(h.get('price_updated_at')):
                raise ValueError('价格/成本缺失或价格过期')
            rate=(price-cost)/cost*100; index=h.get('index_code')
            if rate>loss or not index or '债' in (h.get('fund_type') or '') or '债' in name:
                coverage[kind]['checked']+=1; continue
            val=get_best_valuation(index,metric_type=_get_preferred_metric_type(index),query_source='alert_scanner_loss_plus_undervalued',
                                   enable_online=False,allow_metric_fallback=True)
            if not _valuation_current(val) or _number(val.get('percentile')) is None:
                raise ValueError('估值缺失、过期或日期未知，等待补齐数据')
            pct=float(val['percentile']); coverage[kind]['checked']+=1
            if pct>=threshold: continue
            z=_number(val.get('zscore')); ztext=f'{z:.2f}' if z is not None else '未提供（可选证据）'
            title=f'{name} 浮亏{abs(rate):.1f}%、估值分位{pct:.1f}%：待复核'
            content=(f"{name}（{code}）当前浮亏 {abs(rate):.1f}%，对应指数 {index} 估值分位 {pct:.1f}%。"
                     f"净值日期 {h.get('price_updated_at')}；估值日期 {val.get('snapshot_date')}；z-score：{ztext}。"
                     '低估与亏损可以同时持续，不保证未来回本。先核对集中度、现金和家庭资金用途，再决定观察、减仓或评估新增投资。')
            _emit_alert(alert_type=kind,title=title,content=content,severity='warning',related_fund_code=code,
                        related_fund_name=name,source='alert_scanner',fact_date=str(h['price_updated_at'])[:10],
                        recovery_condition=f'浮亏小于 {abs(loss)}% 或新鲜估值分位回到 {threshold}% 以上',
                        evidence={'risk_value':abs(rate),'percentile':pct,'valuation_date':val.get('snapshot_date')})
            observed.append((kind,code)); created+=1
            _auto_candidate(code,name,kind,title,content,'warning',{'profit_rate':rate,'valuation':val})
        except Exception as exc: _gap(coverage,kind,f'{code}: {exc}')
    return _finish_risk_scan(coverage,observed,created)


# ── P0-1 大盘指数当日跌幅监控 ──────────────────────────────


# 监控的核心大盘指数（代码 → 显示名）
_MARKET_INDEX_MONITOR = {
    "000001": "上证指数",
    "399001": "深证成指",
    "399006": "创业板指",
    "000688": "科创50",
    "000300": "沪深300",
    "000905": "中证500",
    "000852": "中证1000",
}


def scan_market_index_drop() -> dict:
    """扫描核心大盘指数当日跌幅，捕捉系统性大跌风险。

    数据源：services/market/market_data.py:get_market_overview()
    （内部调用 akshare stock_zh_index_spot_sina，含 change_pct 字段）

    开关：alerts.market_index_drop_scan_enabled（默认开启，属预警系统核心能力）
    阈值：alerts.market_index_warn_threshold（默认 2，跌幅≥2% 触发 warning）
          alerts.market_index_danger_threshold（默认 4，跌幅≥4% 触发 danger）

    优化：聚合为 1 条 alert（而非 7 条分散 alert），避免预警列表被大盘指数刷屏。
    - 若有 ≥1 个指数触发阈值，生成 1 条聚合 alert（alert_type=market_index_drop）
    - severity 取最严重的指数等级
    - 若 ≥3 个指数齐跌，severity 升级为 danger，并在 content 中标注系统性风险
    - 决策候选也只生成 1 条

    Returns:
        {"alerts_created": int, "scanned": int, "dropped_indexes": [...]}
    """
    if not _is_enabled("alerts.market_index_drop_scan_enabled", True):
        return {"alerts_created": 0, "skipped": "disabled"}

    warn_threshold = _get_int("alerts.market_index_warn_threshold", 2)
    danger_threshold = _get_int("alerts.market_index_danger_threshold", 4)

    try:
        from services.market_data import get_market_overview
        overview = get_market_overview()
    except Exception as e:
        logger.warning(f"[alert_scanner] 大盘指数行情获取失败: {e}")
        return {"alerts_created": 0, "error": str(e)}

    indices = overview.get("indices", [])
    if not indices:
        logger.info("[alert_scanner] 大盘指数行情为空，跳过跌幅扫描")
        return {"alerts_created": 0, "scanned": 0}

    dropped_indexes = []
    today = datetime.now().strftime("%Y-%m-%d")

    for idx in indices:
        name = idx.get("name", "")
        change_pct = idx.get("change_pct")
        if change_pct is None:
            continue
        try:
            chg = float(change_pct)
        except (TypeError, ValueError):
            continue

        if chg <= -warn_threshold:
            if chg <= -danger_threshold:
                severity = "danger"
            else:
                severity = "warning"
            dropped_indexes.append({
                "name": name,
                "change_pct": chg,
                "severity": severity,
                "price": idx.get("price"),
                "volume_yi": idx.get("volume_yi"),
            })

    # 聚合：若有触发阈值的指数，只生成 1 条 alert（而非每个指数 1 条）
    if dropped_indexes:
        # severity 取最严重等级；≥3 个齐跌升级为 danger（系统性风险）
        has_danger = any(d["severity"] == "danger" for d in dropped_indexes)
        is_systemic = len(dropped_indexes) >= 3
        severity = "danger" if (has_danger or is_systemic) else "warning"

        # 按跌幅排序，构造聚合 title/content
        sorted_drops = sorted(dropped_indexes, key=lambda x: x["change_pct"])
        if len(sorted_drops) == 1:
            d = sorted_drops[0]
            title = f"{d['name']} 当日下跌 {d['change_pct']:.2f}%"
            content_lines = [
                f"{d['name']} 今日下跌 {abs(d['change_pct']):.2f}%。",
                f"当前点位：{d.get('price', 'N/A')}",
                f"成交额：{d.get('volume_yi', 'N/A')} 亿",
            ]
        else:
            names_str = "、".join(f"{d['name']}({d['change_pct']:.2f}%)" for d in sorted_drops)
            avg_drop = sum(d["change_pct"] for d in dropped_indexes) / len(dropped_indexes)
            title = f"大盘指数聚合预警：{len(dropped_indexes)} 个指数下跌，均值 {avg_drop:.2f}%"
            content_lines = [
                f"今日 {names_str} 同时下跌，均值跌幅 {abs(avg_drop):.2f}%。",
            ]
            if is_systemic:
                content_lines.append("⚠️ 系统性大跌风险：≥3 个指数齐跌。")
            content_lines.append("各指数详情：")
            for d in sorted_drops:
                content_lines.append(
                    f"  · {d['name']}：{d['change_pct']:.2f}%（{d['severity']}）"
                    f"  点位 {d.get('price', 'N/A')}  成交 {d.get('volume_yi', 'N/A')}亿"
                )

        content_lines.append(f"时间：{today}")
        if is_systemic:
            content_lines.append("建议：1) 检查持仓是否触及止损线；2) 评估是否需要减仓；3) 关注资金流向；4) 查阅重大新闻。")
        content = "\n".join(content_lines)

        _emit_alert(
            alert_type="market_index_drop",
            title=title,
            content=content,
            severity=severity,
            source="alert_scanner",
        )
        # 决策候选也聚合为 1 条
        _auto_candidate(
            fund_code="",
            fund_name="大盘指数" if not is_systemic else "大盘系统性风险",
            alert_type="market_index_drop",
            title=title,
            content=content,
            severity=severity,
            source_snapshot={
                "dropped_count": len(dropped_indexes),
                "avg_drop": avg_drop if len(dropped_indexes) > 1 else dropped_indexes[0]["change_pct"],
                "is_systemic": is_systemic,
                "indices": [{"name": d["name"], "change_pct": d["change_pct"]} for d in dropped_indexes],
            },
        )
        alerts_created = 1
    else:
        alerts_created = 0

    logger.info(
        f"[alert_scanner] 大盘指数跌幅扫描: {len(indices)} 个指数, "
        f"{len(dropped_indexes)} 个触发预警, 生成 {alerts_created} 个聚合 alert"
    )
    return {
        "alerts_created": alerts_created,
        "scanned": len(indices),
        "dropped_indexes": dropped_indexes,
    }


# ── P0-3 资金流向异常监控 ──────────────────────────────────


def scan_capital_flow_anomaly() -> dict:
    """扫描南向资金异常流出，捕捉主力撤退信号。

    数据源：services/market/southbound_capital.py:get_southbound_capital_signal()
    （已有现成实现，此前从未被 alert_scanner 引用）

    开关：alerts.capital_flow_scan_enabled（默认开启）
    触发条件：signal 为 bearish_resonance（持续净流出 + 强度中/强）

    Returns:
        {"alerts_created": int, "signal": str, "trend": str}
    """
    if not _is_enabled("alerts.capital_flow_scan_enabled", True):
        return {"alerts_created": 0, "skipped": "disabled"}

    try:
        from services.market.southbound_capital import get_southbound_capital_signal
        signal_data = get_southbound_capital_signal()
    except Exception as e:
        logger.warning(f"[alert_scanner] 南向资金信号获取失败: {e}")
        return {"alerts_created": 0, "error": str(e)}

    signal = signal_data.get("signal", "neutral")
    trend = signal_data.get("trend", "neutral")
    strength = signal_data.get("strength", "weak")

    alerts_created = 0

    # 仅在"持续净流出 + 强度中/强"时触发预警
    if signal == "bearish_resonance":
        recent_5d = signal_data.get("recent_5d_net_yi", 0.0)
        recent_20d = signal_data.get("recent_20d_net_yi", 0.0)
        title = f"南向资金持续净流出（{strength}），主力撤退信号"
        content = (
            f"南向资金信号：{signal_data.get('advice', '')}\n"
            f"趋势：{trend}（{strength}）\n"
            f"近 5 日净额：{recent_5d:.2f} 亿\n"
            f"近 20 日净额：{recent_20d:.2f} 亿\n"
            f"建议：港股持仓注意减仓风险，A 股关注资金面共振下行风险。"
        )
        _emit_alert(
            alert_type="capital_outflow",
            title=title,
            content=content,
            severity="warning" if strength == "moderate" else "danger",
            source="alert_scanner",
        )
        _auto_candidate(
            fund_code="",
            fund_name="南向资金",
            alert_type="capital_outflow",
            title=title,
            content=content,
            severity="warning" if strength == "moderate" else "danger",
            source_snapshot={"signal": signal, "recent_5d": recent_5d, "recent_20d": recent_20d},
        )
        alerts_created += 1

    logger.info(
        f"[alert_scanner] 资金流向扫描: signal={signal}, trend={trend}, "
        f"strength={strength}, alerts={alerts_created}"
    )
    return {
        "alerts_created": alerts_created,
        "signal": signal,
        "trend": trend,
        "strength": strength,
    }


def _build_risk_content(fund_code: str, fund_name: str, weight: float,
                        concentration_threshold: float | None,
                        profit_rate: float | None) -> str:
    """构建增强版预警 content，包含基金底层持仓的关键风险信息。

    从 get_fund_holdings() 获取重仓股、可转债占比、资产配置，
    让预警不仅有结论还有论据。调用 akshare 超时 5s，失败则退回原逻辑。
    """
    parts = []

    # 基础信息
    if concentration_threshold is not None:
        parts.append(f"{fund_name}（{fund_code}）当前占组合 {weight:.1f}%，超过 {concentration_threshold}% 阈值")
    if profit_rate is not None:
        parts.append(f"{fund_name}（{fund_code}）当前亏损 {abs(profit_rate):.1f}%")

    # 尝试获取基金底层持仓数据（超时 5s）
    try:
        import concurrent.futures
        from db.portfolio import get_fund_holdings

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(get_fund_holdings, fund_code)
            try:
                fund_data = future.result(timeout=5)
            except concurrent.futures.TimeoutError:
                fund_data = None
                logger.debug(f"[alert_scanner] 获取 {fund_code} 持仓数据超时，退回基础内容")
    except Exception as e:
        fund_data = None
        logger.debug(f"[alert_scanner] 获取 {fund_code} 持仓数据失败: {e}")

    if fund_data:
        # 股票重仓（top 3）
        stocks = fund_data.get("top_stocks", [])
        if stocks:
            import math
            stock_strs = []
            for stock in stocks:
                name = str(stock.get("stock_name") or "").strip()
                if not name:
                    continue
                try:
                    pct = float(stock.get("pct_nav"))
                except (TypeError, ValueError):
                    pct = None
                suffix = f"({pct:g}%)" if pct is not None and math.isfinite(pct) and 0 <= pct <= 100 else ""
                stock_strs.append(f"{name}{suffix}")
                if len(stock_strs) == 3:
                    break
            if stock_strs:
                parts.append(f"重仓股：{', '.join(stock_strs)}")

        # 可转债占比（>3% 提示风险）
        bond_summary = fund_data.get("bond_type_summary", {})
        if bond_summary:
            cb_pct = bond_summary.get("可转债", 0)
            if cb_pct and cb_pct > 3:
                parts.append(f"可转债占比 {cb_pct:.1f}%，股市波动时风险较高")

        # 资产配置只说明权益敞口，不能仅凭股票仓位推断基金类型。
        alloc = fund_data.get("asset_allocation", [])
        if alloc:
            stock_pct = None
            for a in alloc:
                t = a.get("type", "")
                if "股票" in t or "权益" in t:
                    try:
                        stock_pct = float(str(a.get("pct", "0")).replace("%", ""))
                    except (ValueError, TypeError):
                        pass
                    break
            if stock_pct and stock_pct > 20:
                parts.append(f"股票仓位 {stock_pct:.0f}%，权益资产价格波动会影响净值")

    # 结论
    if concentration_threshold is not None:
        parts.append("建议关注分散化风险")
    elif profit_rate is not None:
        parts.append("建议关注是否止损或低位补仓")

    return "；".join(parts) + "。"


# ── P0-C 关注列表信号扫描 ────────────────────────────────────


def _is_alert_recently_created(alert_type: str, related_fund_code: str, hours: int = 24) -> bool:
    """判断最近 N 小时内是否已生成过同类型 alert，避免重复打扰。

    2026-07-20 修复：原 SQL 查询 `alerts` 表（不存在），实际表名是 `portfolio_alerts`，
    导致 SQL 抛异常被 except 吞掉，永远返回 False，去重完全失效。
    """
    from db._conn import _get_conn
    try:
        conn = _get_conn()
        threshold = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
        row = conn.execute(
            "SELECT id FROM portfolio_alerts WHERE alert_type = ? AND related_fund_code = ? AND created_at >= ? LIMIT 1",
            (alert_type, related_fund_code, threshold),
        ).fetchone()
        conn.close()
        return row is not None
    except Exception:
        return False


def _auto_candidate(fund_code: str, fund_name: str, alert_type: str, title: str,
                    content: str, severity: str = 'info', source_snapshot: dict = None) -> int | None:
    """观察为默认结果；任何有明确请求金额的新增候选必须通过最终组合风控。"""
    if not _is_enabled('alerts.auto_candidate_enabled',False): return None
    snapshot=source_snapshot or {}
    action={'valuation_high':'reduce','concentration_high':'rebalance'}.get(alert_type,'watch')
    requested=_number(snapshot.get('requested_amount')) or 0
    if alert_type in ('valuation_low','loss_plus_undervalued') and requested>0:
        try:
            from services.portfolio.decision_guardrails import build_decision_snapshot, evaluate_add_decision
            context=build_decision_snapshot()
            guard=evaluate_add_decision(fund_code,requested,context=context)
            snapshot={**snapshot,'decision_guardrails':guard}
            if guard['status']=='allowed' and guard.get('executable_amount',0)>0:
                action='add'
        except Exception as exc:
            snapshot={**snapshot,'decision_guardrails':{'status':'wait_for_data','reasons':[str(exc)],'executable_amount':0}}
    try:
        from db.decisions import create_candidate_from_structured_recommendation
        return create_candidate_from_structured_recommendation({
            'source_type':'alert','scenario_type':alert_type,'action_type':action,'target_type':'fund',
            'target_code':fund_code,'target_name':fund_name,'summary':title[:120],'rationale':content,
            'confidence':'medium','dedupe_key':f'alert_{alert_type}_{fund_code}',
            'priority':5 if severity=='danger' else 3,'source_snapshot':snapshot,
            'suggested_amount':snapshot.get('decision_guardrails',{}).get('executable_amount',0) if action=='add' else None,
        })
    except Exception as exc:
        logger.warning('预警候选保存失败 %s: %s',fund_code,exc)
        return None


def scan_watchlist_signals() -> dict:
    """扫描关注列表基金，触发目标价/估值低分位/单日大跌上车信号。

    三类信号：
    1. target_price 到位：current_nav <= target_price → "目标价到位"
    2. target_percentile 到位：当前指数估值分位 <= target_percentile → "估值低分位到位"
    3. 单日大跌：change_pct <= -watchlist_drop_threshold（默认 -3%） → "单日大跌关注"

    Returns:
        {"alerts_created": int, "watchlist_scanned": int}
    """
    if not _is_enabled("alerts.watchlist_signal_enabled", True):
        return {"alerts_created": 0, "skipped": "disabled"}

    from db.watchlist import list_watchlist
    from db.portfolio import fetch_fund_nav

    drop_threshold = _get_int("alerts.watchlist_drop_threshold", 3)  # 单日跌幅阈值（%）

    items = list_watchlist(status="watching")
    if not items:
        return {"alerts_created": 0, "watchlist_scanned": 0}

    alerts_created = 0
    for item in items:
        fund_code = item.get("fund_code") or ""
        fund_name = item.get("fund_name") or ""
        if not fund_code:
            continue

        # 获取最新净值（优先用 watchlist 表中的 current_nav，缺失或太旧则重新拉取）
        current_nav = item.get("current_nav")
        nav_updated = item.get("nav_updated_at") or ""
        need_refresh = not current_nav or (
            nav_updated and nav_updated < (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        )
        change_pct = None
        if need_refresh:
            try:
                nav_data = fetch_fund_nav(fund_code)
                if nav_data:
                    current_nav = nav_data.get("nav")
                    change_pct = nav_data.get("change_pct")
            except Exception as e:
                logger.debug(f"[alert_scanner] 关注基金 {fund_code} 取净值失败: {e}")
                continue
        elif item.get("current_nav"):
            current_nav = float(item["current_nav"])

        if not current_nav:
            continue

        target_price = item.get("target_price")
        target_percentile = item.get("target_percentile")

        # 信号1：目标价到位
        if target_price and current_nav <= float(target_price):
            if _is_alert_recently_created("watchlist_target_price", fund_code, hours=24):
                continue
            try:
                title = f"关注基金 {fund_name} 已达目标价"
                content = (
                    f"{fund_name}（{fund_code}）当前净值 {current_nav:.4f}，"
                    f"已跌破目标价 {float(target_price):.4f}，可关注上车机会。"
                )
                _emit_alert(
                    alert_type="watchlist_target_price",
                    title=title,
                    content=content,
                    severity="info",
                    related_fund_code=fund_code,
                    related_fund_name=fund_name,
                    source="alert_scanner",
                )
                alerts_created += 1
                logger.info(f"[alert_scanner] 关注基金 {fund_code} 目标价到位信号已生成")
            except Exception as e:
                logger.warning(f"[alert_scanner] 生成目标价信号失败 {fund_code}: {e}")

        # 信号2：估值低分位到位
        if target_percentile:
            index_code = item.get("index_code") or ""
            if index_code:
                try:
                    val = get_latest_valuation(index_code)
                    if val and val.get("percentile") is not None:
                        try:
                            pct = float(val["percentile"])
                        except (TypeError, ValueError):
                            pct = None
                        if pct is not None and pct <= float(target_percentile):
                            if _is_alert_recently_created("watchlist_target_percentile", fund_code, hours=24):
                                pass
                            else:
                                index_name = item.get("index_name") or index_code
                                title = f"关注基金 {fund_name} 估值进入低分位"
                                content = (
                                    f"{fund_name}（{fund_code}）跟踪指数 {index_name} "
                                    f"当前估值分位 {pct:.1f}%，低于目标分位 "
                                    f"{float(target_percentile):.1f}%，可关注上车机会。"
                                )
                                _emit_alert(
                                    alert_type="watchlist_target_percentile",
                                    title=title,
                                    content=content,
                                    severity="info",
                                    related_fund_code=fund_code,
                                    related_fund_name=fund_name,
                                    source="alert_scanner",
                                )
                                alerts_created += 1
                                logger.info(f"[alert_scanner] 关注基金 {fund_code} 估值低分位信号已生成")
                except Exception as e:
                    logger.debug(f"[alert_scanner] 估值分位检查失败 {fund_code}: {e}")

        # 信号3：单日大跌
        if change_pct is not None and change_pct <= -drop_threshold:
            if _is_alert_recently_created("watchlist_big_drop", fund_code, hours=24):
                pass
            else:
                try:
                    title = f"关注基金 {fund_name} 单日下跌 {abs(change_pct):.1f}%"
                    content = (
                        f"{fund_name}（{fund_code}）最新净值 {current_nav:.4f}，"
                        f"单日下跌 {abs(change_pct):.1f}%，超过 -{drop_threshold}% 阈值，"
                        f"可能迎来上车机会，请结合估值和趋势判断。"
                    )
                    _emit_alert(
                        alert_type="watchlist_big_drop",
                        title=title,
                        content=content,
                        severity="info",
                        related_fund_code=fund_code,
                        related_fund_name=fund_name,
                        source="alert_scanner",
                    )
                    alerts_created += 1
                    logger.info(f"[alert_scanner] 关注基金 {fund_code} 单日大跌信号已生成")
                except Exception as e:
                    logger.warning(f"[alert_scanner] 生成大跌信号失败 {fund_code}: {e}")

    logger.info(
        f"[alert_scanner] 关注列表信号扫描：扫描 {len(items)} 只基金，生成 {alerts_created} 个 alert"
    )
    return {"alerts_created": alerts_created, "watchlist_scanned": len(items)}


# ── P0-2 关注列表退出信号扫描（2026-07-21 新增）───────────────────────────


def scan_watchlist_exit_signals() -> dict:
    """扫描关注列表已上车基金的止盈/止损触发，生成 alert。

    与 watchlist_patrol_api 的区别：
    - patrol 是用户主动调用，5 分钟缓存
    - 这里是定时扫描（每 30 分钟），触发后立即生成 portfolio_alerts
    - 24h 去重（fund_code + alert_type）

    开关：alerts.watchlist_exit_scan_enabled（默认 true）
    扫描对象：status='bought' 且 entry_price 非空的项

    Returns:
        {"alerts_created": int, "scanned": int, "profit_targets": int, "stop_losses": int}
    """
    if not _is_enabled("alerts.watchlist_exit_scan_enabled", True):
        return {"alerts_created": 0, "skipped": "disabled"}

    from db.watchlist import list_watchlist
    from db.portfolio import fetch_fund_nav

    items = list_watchlist()  # 全部
    bought_items = [i for i in items if i.get("status") == "bought" and i.get("entry_price")]
    if not bought_items:
        return {"alerts_created": 0, "scanned": 0, "profit_targets": 0, "stop_losses": 0}

    alerts_created = 0
    profit_targets = 0
    stop_losses = 0

    for item in bought_items:
        fund_code = item.get("fund_code") or ""
        fund_name = item.get("fund_name") or fund_code
        entry_price = float(item.get("entry_price") or 0)
        if entry_price <= 0:
            continue

        # 获取最新净值（优先用 watchlist.current_nav，旧了再拉取）
        current_nav = item.get("current_nav")
        nav_updated = item.get("nav_updated_at") or ""
        need_refresh = not current_nav or (
            nav_updated and nav_updated < (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        )
        if need_refresh:
            try:
                nav_data = fetch_fund_nav(fund_code)
                if nav_data and nav_data.get("nav"):
                    current_nav = float(nav_data["nav"])
            except Exception as e:
                logger.debug(f"[alert_scanner] 退出扫描取净值失败 {fund_code}: {e}")
                continue

        if not current_nav:
            continue
        current_nav = float(current_nav)

        pnl_pct = (current_nav - entry_price) / entry_price * 100
        target_profit = item.get("target_profit_pct")
        stop_loss = item.get("stop_loss_pct")

        # 信号1：止盈触发
        if target_profit and pnl_pct >= float(target_profit):
            if _is_alert_recently_created("watchlist_profit_target", fund_code, hours=24):
                continue
            try:
                title = f"关注基金 {fund_name} 已达止盈目标"
                content = (
                    f"{fund_name}（{fund_code}）当前净值 {current_nav:.4f}，"
                    f"买入价 {entry_price:.4f}，已涨 {pnl_pct:.1f}%，"
                    f"达到止盈目标 {float(target_profit):.1f}%，可考虑获利了结。"
                )
                _emit_alert(
                    alert_type="watchlist_profit_target",
                    title=title,
                    content=content,
                    severity="info",
                    related_fund_code=fund_code,
                    related_fund_name=fund_name,
                    source="alert_scanner_exit",
                )
                alerts_created += 1
                profit_targets += 1
                logger.info(f"[alert_scanner] 关注基金 {fund_code} 止盈信号已生成（pnl={pnl_pct:.1f}%）")
            except Exception as e:
                logger.warning(f"[alert_scanner] 生成止盈信号失败 {fund_code}: {e}")

        # 信号2：止损触发
        elif stop_loss and pnl_pct <= -float(stop_loss):
            if _is_alert_recently_created("watchlist_stop_loss", fund_code, hours=24):
                continue
            try:
                title = f"关注基金 {fund_name} 触发止损"
                content = (
                    f"{fund_name}（{fund_code}）当前净值 {current_nav:.4f}，"
                    f"买入价 {entry_price:.4f}，已跌 {abs(pnl_pct):.1f}%，"
                    f"触发止损线 -{float(stop_loss):.1f}%，建议止损离场或重新评估。"
                )
                _emit_alert(
                    alert_type="watchlist_stop_loss",
                    title=title,
                    content=content,
                    severity="warning",
                    related_fund_code=fund_code,
                    related_fund_name=fund_name,
                    source="alert_scanner_exit",
                )
                alerts_created += 1
                stop_losses += 1
                logger.info(f"[alert_scanner] 关注基金 {fund_code} 止损信号已生成（pnl={pnl_pct:.1f}%）")
            except Exception as e:
                logger.warning(f"[alert_scanner] 生成止损信号失败 {fund_code}: {e}")

    logger.info(
        f"[alert_scanner] 关注列表退出信号扫描：扫描 {len(bought_items)} 只已上车基金，"
        f"生成 {alerts_created} 个 alert（止盈 {profit_targets} / 止损 {stop_losses}）"
    )
    return {
        "alerts_created": alerts_created,
        "scanned": len(bought_items),
        "profit_targets": profit_targets,
        "stop_losses": stop_losses,
    }


# ── P0-D 健康分预警扫描 ────────────────────────────────────


def scan_health_score() -> dict:
    """扫描今日健康分，低于阈值时生成预警 alert。

    开关：alerts.health_score_scan_enabled (默认 true)
    阈值：alerts.health_score_threshold (默认 60，满分100)

    Returns:
        {"alerts_created": int, "score": int | None}
    """
    if not _is_enabled("alerts.health_score_scan_enabled", True):
        return {"alerts_created": 0, "skipped": "disabled"}

    threshold = _get_int("alerts.health_score_threshold", 60)
    today = datetime.now().strftime("%Y-%m-%d")

    try:
        from db.health_score import get_health_score
        score_data = get_health_score(today)
        if not score_data:
            return {"alerts_created": 0, "score": None, "reason": "今日无健康分数据"}
    except Exception as e:
        logger.debug(f"[alert_scanner] 获取健康分失败: {e}")
        return {"alerts_created": 0, "score": None, "error": str(e)}

    total_score = score_data.get("total_score", 0)
    try:
        total_score = int(total_score)
    except (TypeError, ValueError):
        return {"alerts_created": 0, "score": None, "error": "score invalid"}

    if total_score >= threshold:
        return {"alerts_created": 0, "score": total_score, "reason": f"健康分 {total_score} >= {threshold}"}

    # 健康分低于阈值，生成预警
    if _is_alert_recently_created("health_score_low", "", hours=24):
        return {"alerts_created": 0, "score": total_score, "reason": "24小时内已生成过预警"}

    # 解析各维度分数
    dim_scores = {
        "质量": score_data.get("score_quality"),
        "分散度": score_data.get("score_diversification"),
        "估值": score_data.get("score_valuation"),
        "行为": score_data.get("score_behavior"),
        "风险": score_data.get("score_risk"),
    }
    dim_str = " / ".join(
        f"{k}:{v}" for k, v in dim_scores.items() if v is not None
    )

    try:
        title = f"理财健康分偏低：{total_score}分"
        content = (
            f"今日理财健康分 {total_score} 分，低于预警阈值 {threshold} 分。\n"
            f"各维度：{dim_str}\n"
            f"建议查看健康分页面了解详细诊断。"
        )
        _emit_alert(
            alert_type="health_score_low",
            title=title,
            content=content,
            severity="warning",
            source="alert_scanner",
        )
        logger.info(f"[alert_scanner] 健康分预警已生成：{total_score} < {threshold}")
        return {"alerts_created": 1, "score": total_score}
    except Exception as e:
        logger.warning(f"[alert_scanner] 生成健康分预警失败: {e}")
        return {"alerts_created": 0, "score": total_score, "error": str(e)}


# ── 主入口 ────────────────────────────────────


def scan_valuation_failures() -> dict:
    """P0-E 扫描估值查询全部失败的指数，仅写日志不生成 alert。

    2026-07-20 改造：用户反馈"估值没查到就别预警提醒了"。
    原设计主动 create_alert 推送到右上角铃铛，对终端用户无价值（用户看到"估值数据缺失"
    也无法处理），属于开发侧监控误暴露给终端用户。现改为只写 warning 日志，便于
    开发侧排查；同时用模块级 set 做进程内去重（同指数同进程不重复刷屏）。

    检查最近 24 小时内 valuation_query_logs 中 final_source='failed' 的记录。

    Returns:
        {"failed_indexes": int, "logged_indexes": int}
    """
    if not _is_enabled("alerts.valuation_failure_scan_enabled", True):
        return {"failed_indexes": 0, "skipped": "disabled"}

    from db._conn import _get_conn
    try:
        conn = _get_conn()
        since = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        rows = conn.execute("""
            SELECT DISTINCT index_code, index_name
            FROM valuation_query_logs
            WHERE created_at >= ? AND final_source = 'failed' AND index_code IS NOT NULL
            ORDER BY created_at DESC
            LIMIT 10
        """, (since,)).fetchall()
        conn.close()
    except Exception as e:
        logger.debug(f"[alert_scanner] 估值失败扫描查询失败: {e}")
        return {"failed_indexes": 0, "error": str(e)}

    if not rows:
        return {"failed_indexes": 0, "logged_indexes": 0}

    # 模块级去重 set：记录本次进程已写过日志的 code，避免同进程内重复刷屏
    # （原 _is_alert_recently_created 检查 portfolio_alerts 表，但不写 alert 后失效）
    global _VALUATION_FAILURE_LOGGED_CODES
    if '_VALUATION_FAILURE_LOGGED_CODES' not in globals():
        _VALUATION_FAILURE_LOGGED_CODES = set()

    logged = 0
    for row in rows:
        code = row["index_code"]
        name = row["index_name"]
        # name 为空时反查持仓表补全指数名称
        if not name:
            try:
                from db.valuations import _lookup_index_name
                name = _lookup_index_name(code) or code
            except Exception:
                name = code
        # 进程内去重：同 code 不重复写日志
        if code in _VALUATION_FAILURE_LOGGED_CODES:
            continue
        logger.warning(
            f"[alert_scanner] 估值查询失败 {code} ({name})：本地表和在线渠道"
            f"（akshare/天天基金）均无法获取估值数据，建议检查指数代码映射或手动录入估值图片"
        )
        _VALUATION_FAILURE_LOGGED_CODES.add(code)
        logged += 1

    logger.info(f"[alert_scanner] 估值查询失败扫描：{len(rows)} 个指数失败，写入 {logged} 条 warning 日志")
    return {"failed_indexes": len(rows), "logged_indexes": logged}


def run_periodic_scan() -> dict:
    """统一扫描契约；失败和未覆盖不会被解释为已排除风险。"""
    from db.alert_lifecycle import record_scan
    stamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    result={'generated':0,'updated':0,'resolved':0,'failed':0,'skipped':0,
            'coverage':{},'last_scan':stamp,'scanned_at':stamp,'status':'complete'}
    if not _is_enabled('alerts.proactive_scan_enabled',True):
        result.update(skipped=1,status='skipped'); record_scan(result); return result
    scans={
        'verification':scan_and_verify_recommendations,'valuation':scan_valuation_thresholds,
        'portfolio':scan_portfolio_risk,'loss_plus_undervalued':scan_loss_plus_undervalued,
        'watchlist':scan_watchlist_signals,'health_score':scan_health_score,
        'valuation_failures':scan_valuation_failures,'market_index_drop':scan_market_index_drop,
        'capital_flow':scan_capital_flow_anomaly,'watchlist_exit':scan_watchlist_exit_signals,
        'detailed':scan_detailed_risks,
    }
    for name,scan in scans.items():
        counts={'generated':0,'updated':0}; token=_SCAN_COUNTS.set(counts)
        try:
            data=scan() or {}
            if data.get('error'):
                status='failed'; failed=max(1,data.get('failed',0))
            elif data.get('failed') or data.get('status')=='incomplete':
                status='incomplete'; failed=max(1,data.get('failed',0))
            elif data.get('skipped') or data.get('score','present') is None:
                status='skipped'; failed=0
            else:
                # 未提供完整覆盖证明的旧扫描器保留明确边界，不自动恢复。
                status='complete' if data.get('coverage') else 'limited'; failed=0
            data.update(generated=counts['generated'],updated=counts['updated'])
            result[name]=data
            result['generated']+=counts['generated']; result['updated']+=counts['updated']
            result['resolved']+=int(data.get('resolved',0)); result['failed']+=failed
            result['skipped']+=1 if status in ('skipped','limited') else 0
            result['coverage'][name]={'status':status,'details':data.get('coverage') or {},
                                      'reason':data.get('error') or data.get('reason') or data.get('skipped')}
        except Exception as exc:
            logger.warning('风险扫描 %s 失败: %s',name,exc)
            result[name]={'error':str(exc)}; result['failed']+=1
            result['coverage'][name]={'status':'failed','reason':str(exc)}
        finally:
            _SCAN_COUNTS.reset(token)
    if result['failed'] or result['skipped']: result['status']='incomplete'
    record_scan(result)
    return result


def scan_detailed_risks(*, holdings=None, nav_history_loader=None, valuation_loader=None, cash_loader=None):
    """细粒度扫描，按类别记录覆盖；错误不会解除旧风险，也不会生成补仓信号。"""
    from db.alert_lifecycle import record_scan
    if holdings is None: holdings=list_holdings()
    if nav_history_loader is None:
        from db.portfolio import get_fund_nav_history
        nav_history_loader=get_fund_nav_history
    if valuation_loader is None: valuation_loader=get_latest_valuation
    if cash_loader is None:
        from db.portfolio import get_cash_balance
        cash_loader=get_cash_balance
    rows=[h for h in holdings if (_number(h.get('shares')) or 0)>0]
    by_code={}
    for h in rows: by_code.setdefault(h.get('fund_code') or '',[]).append(h)
    kinds=('drawdown_alert','buy_drop_alert','valuation_alert','valuation_opportunity','concentration_alert')
    coverage=_coverage(kinds,len(by_code))
    coverage.update(_coverage(('stale_data','cash_idle'),1))
    observed=[]; created=0
    counts=_SCAN_COUNTS.get()
    if counts is None: counts={'generated':0,'updated':0}
    token=_SCAN_COUNTS.set(counts)
    threshold=_get_int('alert.drawdown_pct',10); buy_threshold=_get_int('alert.buy_drop_pct',4)
    concentration=_get_int('alert.concentration_pct',30); low=_get_int('alert.valuation_low',20); high=_get_int('alert.valuation_high',80)
    stale_days=_get_int('alert.stale_days',5); cash_high=_get_int('alert.cash_high_pct',15)
    def emit(kind,code,name,title,content,severity='warning',fact_date=None,value=None,recovery=None):
        nonlocal created
        _emit_alert(alert_type=kind,title=title,content=content,severity=severity,related_fund_code=code or None,
                    related_fund_name=name,source='system_scan',fact_date=fact_date,recovery_condition=recovery,
                    evidence={'risk_value':value,'fact_date':fact_date})
        observed.append((kind,code)); created+=1
    prices_complete=all((_number(h.get('current_price')) or 0)>0 and _price_fact_is_current(h.get('price_updated_at')) for h in rows)
    total=sum(float(h['shares'])*(_number(h.get('current_price')) or 0) for h in rows)
    try:
        for code,group in by_code.items():
            h=group[0]; name=h.get('fund_name') or code
            # 回撤历史按实际 dict 协议解包，并按所属日期排序。
            try:
                facts=_drawdown_facts(nav_history_loader(code,days=60))
                if not facts or not _price_fact_is_current(facts['fact_date']):
                    raise ValueError('历史样本不足或最新净值已过期')
                coverage['drawdown_alert']['checked']+=1
                if facts['drawdown_pct']>=threshold:
                    value=facts['drawdown_pct']
                    emit('drawdown_alert',code,name,f'{name} 近期回撤 {value:.1f}%',
                         f"近30个有效净值样本高点 {facts['peak']:.4f}，最新 {facts['current']:.4f}，回撤 {value:.1f}%。先评估损失承受能力，回撤不等于买点。",
                         'danger' if value>=threshold*1.5 else 'warning',facts['fact_date'],value,f'有效历史净值回撤小于 {threshold}%')
            except Exception as exc: _gap(coverage,'drawdown_alert',f'{code}: {exc}')
            # 估值以持仓关联指数为代码，缺乏新鲜估值只能等待。
            try:
                index=h.get('index_code')
                if not index: raise ValueError('未关联指数，无法判断估值')
                val=valuation_loader(index)
                if not _valuation_current(val) or _number(val.get('percentile')) is None:
                    raise ValueError('估值缺失、日期未知或过期')
                pct=float(val['percentile']); coverage['valuation_alert']['checked']+=1; coverage['valuation_opportunity']['checked']+=1
                kind='valuation_alert' if pct>=high else 'valuation_opportunity' if pct<=low else None
                if kind:
                    emit(kind,code,name,f'{name} 估值分位 {pct:.1f}%（事实参考）',
                         f'对应指数 {index}，估值分位 {pct:.1f}%。低估不代表买点，新增投资需先经过组合风控。',
                         'warning' if kind=='valuation_alert' else 'info',val.get('snapshot_date'),pct if kind=='valuation_alert' else 100-pct,
                         f'新鲜估值分位回到 {low}%–{high}% 内')
            except Exception as exc:
                for kind in ('valuation_alert','valuation_opportunity'): _gap(coverage,kind,f'{code}: {exc}')
            # 家庭同代码合并集中度。
            if prices_complete and total>0:
                coverage['concentration_alert']['checked']+=1
                weight=sum(float(x['shares'])*float(x['current_price']) for x in group)/total*100
                if weight>=concentration:
                    emit('concentration_alert',code,name,f'{name} 家庭合计占比 {weight:.1f}%',
                         f'同代码跨账户占投资持仓市值 {weight:.1f}%，阈值 {concentration}%。请复核分散配置与交易成本。',
                         'danger' if weight>=concentration*1.5 else 'warning',str(h.get('price_updated_at'))[:10],weight,
                         f'家庭合计占投资持仓市值低于 {concentration}%')
            else: _gap(coverage,'concentration_alert',f'{code}: 行情覆盖不足，集中度分母未知')
            # 各账户最近买入价独立比较，聚合同代码最严重一项。
            try:
                drops=[]
                for x in group:
                    buy=_number(x.get('last_buy_price')); price=_number(x.get('current_price'))
                    if buy is None or buy<=0:
                        # 缺少买入证据不等于该风险已恢复；明确等待补齐。
                        raise ValueError('最近买入价格缺失，无法复核买后跌幅')
                    if not price or not _price_fact_is_current(x.get('price_updated_at')):
                        raise ValueError('买入后价格缺失或过期')
                    drops.append(((buy-price)/buy*100,x))
                coverage['buy_drop_alert']['checked']+=1
                if drops:
                    value,x=max(drops,key=lambda pair:pair[0])
                    if value>=buy_threshold:
                        emit('buy_drop_alert',code,name,f'{name} 买入后下跌 {value:.1f}%',
                             f"最近买入日期 {x.get('last_buy_date') or '未提供'}，成交价 {x['last_buy_price']:.4f}，当前净值 {x['current_price']:.4f}。跌幅仅用于损失复核，不构成补仓依据。",
                             'danger' if value>=buy_threshold*1.5 else 'warning',str(x.get('price_updated_at'))[:10],value,
                             f'最新净值相对买入价跌幅小于 {buy_threshold}%')
            except Exception as exc: _gap(coverage,'buy_drop_alert',f'{code}: {exc}')
        # 过期事实本身可被可靠检查，不代表价格风险已覆盖。
        stale=[h for h in rows if not h.get('price_updated_at') or str(h['price_updated_at'])[:10]<(date.today()-timedelta(days=stale_days)).isoformat()]
        coverage['stale_data']['checked']=1
        if stale:
            emit('stale_data','','家庭组合',f'{len(stale)} 条持仓净值日期缺失或过期',
                 '、'.join(h.get('fund_name') or h.get('fund_code') or '未知基金' for h in stale)+'；请先补齐行情，再判断风险与新增投资。',
                 'warning',date.today().isoformat(),len(stale),'所有持仓净值日期已补齐且不再过期')
        try:
            cash=_number(cash_loader())
            if cash is None or cash<0 or not prices_complete: raise ValueError('现金或组合价格覆盖不完整')
            coverage['cash_idle']['checked']=1
            if total+cash>0 and cash/(total+cash)*100>=cash_high:
                pct=cash/(total+cash)*100
                emit('cash_idle','','家庭组合',f'现金占总资产 {pct:.1f}%：核对用途',
                     f'登记现金 {cash:,.0f} 元。先保留家庭必要开支、应急金与近期目标资金，再评估可投资余额。现金占比高不构成买入指令。',
                     'info',date.today().isoformat(),pct,f'已核对现金用途；现金占比低于 {cash_high}% 后自动恢复')
        except Exception as exc: _gap(coverage,'cash_idle',exc)
        result=_finish_risk_scan(coverage,observed,created,source='system_scan')
        result.update(generated=counts['generated'],updated=counts['updated'],last_scan=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        result['ok']=result['status']=='complete'
        record_scan(result,source='detailed')
        return result
    finally:
        _SCAN_COUNTS.reset(token)
