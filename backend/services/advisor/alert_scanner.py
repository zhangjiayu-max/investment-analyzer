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
from datetime import datetime, timedelta

from db.config import get_config_bool, get_config_int
from db.portfolio import create_alert, list_holdings
from db.dashboard import (
    list_pending_verification_recommendations,
    auto_verify_pending_recommendations,
)
from db.valuations import get_latest_valuation, get_best_valuation

logger = logging.getLogger(__name__)


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
            create_alert(
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
    """扫描持仓相关指数的估值分位，触达阈值时生成 alert。

    Returns:
        {"alerts_created": int}
    """
    if not _is_enabled("alerts.proactive_scan_enabled", True):
        return {"alerts_created": 0, "skipped": "disabled"}

    low_threshold = _get_int("alerts.valuation_low_threshold", 20)
    high_threshold = _get_int("alerts.valuation_high_threshold", 80)

    # 从持仓中收集关注的指数代码（去重）
    holdings = list_holdings()
    index_codes = {}
    for h in holdings:
        code = h.get("index_code")
        if code and code not in index_codes:
            index_codes[code] = h.get("index_name") or code

    if not index_codes:
        return {"alerts_created": 0}

    alerts_created = 0
    today = datetime.now().strftime("%Y-%m-%d")
    for code, name in index_codes.items():
        try:
            # 修复 P1-2：此前硬编码 enable_online=False 导致本地缺失时无法在线兜底，
            # 9 个指数全部返回 None。改为启用在线兜底（受全局 valuation.online_fallback_enabled 开关控制）
            #
            # 2026-07-20 改造：之前硬编码 metric_type="市盈率"（get_best_valuation 默认值），
            # 但持仓中 6 个指数本地只有"市净率/市销率/股息率" → 必然本地查不到 → 走在线兜底 →
            # akshare 不支持国证 H 系/Wind 88xxxx → 最终 failed → 触发 scan_valuation_failures 预警。
            # 现改为：先查本地该指数有哪些 metric_type，优先用本地已有的；同时传 allow_metric_fallback=True
            # 让 get_best_valuation 在指定 metric_type 查不到时自动 fallback 到本地其他指标。
            preferred_metric = _get_preferred_metric_type(code)
            val = get_best_valuation(
                code,
                metric_type=preferred_metric,
                query_source="alert_scanner",
                enable_online=True,
                allow_metric_fallback=True,
            )
            if not val:
                continue
            percentile = val.get("percentile")
            if percentile is None:
                continue
            # 转浮点（容错字符串）
            try:
                pct = float(percentile)
            except (TypeError, ValueError):
                continue

            if pct < low_threshold:
                # 2026-07-13：低估触发追加趋势验证，避免"低估但仍在跌"陷阱
                trend_hint = ""
                trend_severity = "info"
                try:
                    # 查该指数关联的基金，用基金净值趋势近似
                    related_fund = _find_related_fund_for_index(code, holdings)
                    if related_fund:
                        from services.daily_position_advisor import _calc_trend_score
                        ts, tr = _calc_trend_score(related_fund.get("fund_code", ""), 5)
                        if ts >= 7:
                            trend_hint = f"（近5日{tr}，上车信号强）"
                            trend_severity = "info"
                        elif ts <= 3:
                            trend_hint = f"（近5日{tr}，建议等待企稳）"
                            trend_severity = "info"
                        else:
                            trend_hint = f"（近5日{tr}，可分批建仓）"
                except Exception as e:
                    logger.debug(f"[alert_scanner] 趋势验证 {code} 失败: {e}")

                title = f"{name} 估值进入低估区（分位 {pct:.1f}%）{trend_hint}"
                content = (
                    f"{name}（{code}）当前估值分位 {pct:.1f}%，"
                    f"低于 {low_threshold}% 阈值，可关注加仓机会。{trend_hint}"
                )
                create_alert(
                    alert_type="valuation_low",
                    title=title,
                    content=content,
                    severity=trend_severity,
                    related_fund_code=code,
                    related_fund_name=name,
                    source="alert_scanner",
                )
                _auto_candidate(code, name, "valuation_low", title, content,
                                trend_severity, {"percentile": pct, "metric_type": val.get("metric_type")})
                alerts_created += 1
            elif pct > high_threshold:
                title = f"{name} 估值进入高估区（分位 {pct:.1f}%）"
                content = (
                    f"{name}（{code}）当前估值分位 {pct:.1f}%，"
                    f"高于 {high_threshold}% 阈值，注意减仓风险。"
                )
                create_alert(
                    alert_type="valuation_high",
                    title=title,
                    content=content,
                    severity="warning",
                    related_fund_code=code,
                    related_fund_name=name,
                    source="alert_scanner",
                )
                _auto_candidate(code, name, "valuation_high", title, content,
                                "warning", {"percentile": pct, "metric_type": val.get("metric_type")})
                alerts_created += 1
        except Exception as e:
            logger.debug(f"[alert_scanner] 估值扫描 {code} 失败: {e}")

    logger.info(f"[alert_scanner] 估值阈值扫描生成 {alerts_created} 个 alert")
    return {"alerts_created": alerts_created, "scanned_indexes": len(index_codes)}


# ── P0-B 持仓风险扫描 ────────────────────────────────────


def scan_portfolio_risk() -> dict:
    """扫描持仓集中度和亏损，生成风险 alert。

    增强版：content 不仅包含占比/亏损数据，还包含基金底层持仓的关键风险信息
    （重仓股、可转债占比、资产配置），让预警更有说服力。

    Returns:
        {"alerts_created": int}
    """
    if not _is_enabled("alerts.proactive_scan_enabled", True):
        return {"alerts_created": 0, "skipped": "disabled"}

    concentration_threshold = _get_int("alerts.concentration_threshold", 30)
    loss_threshold = _get_int("alerts.loss_threshold", 15)
    # P0-2 新增：当日跌幅阈值（独立于累计亏损），开关 alerts.daily_drop_scan_enabled 默认开启
    daily_drop_enabled = _is_enabled("alerts.daily_drop_scan_enabled", True)
    daily_drop_threshold = _get_int("alerts.daily_drop_threshold", 3)  # 当日跌幅 ≥3% 触发

    holdings = list_holdings()
    if not holdings:
        return {"alerts_created": 0}

    # 计算总市值
    total_value = 0.0
    for h in holdings:
        try:
            shares = float(h.get("shares") or 0)
            if shares <= 0:
                continue
            current_price = float(h.get("current_price") or 0)
            total_value += shares * current_price
        except (TypeError, ValueError):
            continue

    alerts_created = 0
    if total_value <= 0:
        return {"alerts_created": 0}

    for h in holdings:
        try:
            shares = float(h.get("shares") or 0)
            if shares <= 0:
                continue
            current_price = float(h.get("current_price") or 0)
            cost_price = float(h.get("cost_price") or 0)
            current_value = shares * current_price
            fund_code = h.get("fund_code") or ""
            fund_name = h.get("fund_name") or ""

            weight = current_value / total_value * 100
            alert_triggered = False

            # 集中度检测
            if weight > concentration_threshold:
                alert_triggered = True
                title = f"{fund_name} 占比 {weight:.1f}%，集中度过高"
                content = _build_risk_content(fund_code, fund_name, weight, concentration_threshold, None)
                create_alert(
                    alert_type="concentration_high",
                    title=title,
                    content=content,
                    severity="warning",
                    related_fund_code=fund_code,
                    related_fund_name=fund_name,
                    source="alert_scanner",
                )
                _auto_candidate(fund_code, fund_name, "concentration_high", title, content,
                                "warning", {"weight": weight, "threshold": concentration_threshold})
                alerts_created += 1

            # 亏损检测（累计亏损）
            if cost_price > 0 and not alert_triggered:
                profit_rate = (current_price - cost_price) / cost_price * 100
                if profit_rate < -loss_threshold:
                    alert_triggered = True
                    title = f"{fund_name} 当前亏损 {abs(profit_rate):.1f}%"
                    content = _build_risk_content(fund_code, fund_name, weight, None, profit_rate)
                    create_alert(
                        alert_type="loss_warning",
                        title=title,
                        content=content,
                        severity="warning",
                        related_fund_code=fund_code,
                        related_fund_name=fund_name,
                        source="alert_scanner",
                    )
                    _auto_candidate(fund_code, fund_name, "loss_warning", title, content,
                                    "warning", {"profit_rate": profit_rate, "threshold": loss_threshold})
                    alerts_created += 1

            # P0-2 新增：当日跌幅检测（区别于累计亏损，捕捉"今天突然大跌"）
            # 数据源：holding 的 change_pct 字段（基金当日涨跌幅，由 refresh_all_fund_prices 更新）
            if daily_drop_enabled and not alert_triggered:
                try:
                    change_pct = float(h.get("change_pct") or 0)
                except (TypeError, ValueError):
                    change_pct = 0.0
                # 数据时效性检查：nav_updated_at 必须是今天，避免用昨日数据误报
                nav_updated = h.get("nav_updated_at") or ""
                is_today = nav_updated.startswith(datetime.now().strftime("%Y-%m-%d"))
                if is_today and change_pct <= -daily_drop_threshold:
                    title = f"{fund_name} 当日跌幅 {abs(change_pct):.2f}%"
                    content = (
                        f"{fund_name}（{fund_code}）今日下跌 {abs(change_pct):.2f}%，"
                        f"超过 {daily_drop_threshold}% 当日跌幅阈值。"
                        f"当前持仓市值 {current_value:.0f} 元，占组合 {weight:.1f}%。"
                    )
                    create_alert(
                        alert_type="daily_drop",
                        title=title,
                        content=content,
                        severity="danger" if change_pct <= -5 else "warning",
                        related_fund_code=fund_code,
                        related_fund_name=fund_name,
                        source="alert_scanner",
                    )
                    _auto_candidate(fund_code, fund_name, "daily_drop", title, content,
                                    "danger" if change_pct <= -5 else "warning",
                                    source_snapshot={"change_pct": change_pct, "threshold": daily_drop_threshold})
                    alerts_created += 1
        except Exception as e:
            logger.debug(f"[alert_scanner] 持仓扫描失败 {h.get('fund_code')}: {e}")

    logger.info(f"[alert_scanner] 持仓风险扫描生成 {alerts_created} 个 alert")
    return {"alerts_created": alerts_created, "holdings_scanned": len(holdings)}


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

    alerts_created = 0
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
            # 判断严重等级
            if chg <= -danger_threshold:
                severity = "danger"
                level_desc = f"大跌（≥{danger_threshold}%）"
            else:
                severity = "warning"
                level_desc = f"下跌（≥{warn_threshold}%）"

            title = f"{name} 当日{level_desc}：{chg:.2f}%"
            content = (
                f"{name} 今日下跌 {abs(chg):.2f}%，触发{level_desc}预警。\n"
                f"当前点位：{idx.get('price', 'N/A')}\n"
                f"成交额：{idx.get('volume_yi', 'N/A')} 亿\n"
                f"时间：{today}"
            )
            create_alert(
                alert_type="market_index_drop",
                title=title,
                content=content,
                severity=severity,
                source="alert_scanner",
            )
            # 大盘大跌自动生成决策候选（建议减仓/观望）
            _auto_candidate(
                fund_code="",
                fund_name=name,
                alert_type="market_index_drop",
                title=title,
                content=content,
                severity=severity,
                source_snapshot={"change_pct": chg, "index_name": name},
            )
            alerts_created += 1
            dropped_indexes.append({"name": name, "change_pct": chg, "severity": severity})

    # 如果有多个指数同时大跌，额外生成一条系统性风险预警
    if len(dropped_indexes) >= 3:
        names = "、".join(d["name"] for d in dropped_indexes)
        avg_drop = sum(d["change_pct"] for d in dropped_indexes) / len(dropped_indexes)
        title = f"⚠️ 系统性大跌风险：{len(dropped_indexes)} 大指数齐跌，均值 {avg_drop:.2f}%"
        content = (
            f"今日{names}同时大幅下跌，均值跌幅 {abs(avg_drop):.2f}%。\n"
            f"建议：1) 检查持仓是否触及止损线；2) 评估是否需要减仓控制风险；"
            f"3) 关注南向资金/北向资金流向；4) 查阅当日重大新闻。"
        )
        create_alert(
            alert_type="systemic_market_risk",
            title=title,
            content=content,
            severity="danger",
            source="alert_scanner",
        )
        _auto_candidate(
            fund_code="",
            fund_name="大盘系统性风险",
            alert_type="systemic_market_risk",
            title=title,
            content=content,
            severity="danger",
            source_snapshot={"avg_drop": avg_drop, "dropped_count": len(dropped_indexes)},
        )
        alerts_created += 1

    logger.info(
        f"[alert_scanner] 大盘指数跌幅扫描: {len(indices)} 个指数, "
        f"{len(dropped_indexes)} 个触发预警, 生成 {alerts_created} 个 alert"
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
        create_alert(
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
            stock_strs = [f"{s['stock_name']}({s.get('pct_nav', '?')}%)" for s in stocks[:3]]
            parts.append(f"重仓股：{', '.join(stock_strs)}")

        # 可转债占比（>3% 提示风险）
        bond_summary = fund_data.get("bond_type_summary", {})
        if bond_summary:
            cb_pct = bond_summary.get("可转债", 0)
            if cb_pct and cb_pct > 3:
                parts.append(f"可转债占比 {cb_pct:.1f}%，股市波动时风险较高")

        # 资产配置（股票仓位 > 20% 提示是混合型基金）
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
                parts.append(f"股票仓位 {stock_pct:.0f}%，混合型基金波动大于纯债")

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
                    content: str, severity: str = "info", source_snapshot: dict = None) -> int | None:
    """将高优先级预警自动转为决策候选（去重：同 alert_type + fund_code 14天内不重复）。

    开关：alerts.auto_candidate_enabled（默认 false，遵循项目规范）。
    """
    if not _is_enabled("alerts.auto_candidate_enabled", False):
        return None
    action_map = {
        "valuation_low": "add",
        "valuation_high": "reduce",
        "concentration_high": "rebalance",
        "loss_warning": "add",
    }
    action_type = action_map.get(alert_type, "watch")
    try:
        from db.decisions import create_candidate_from_structured_recommendation
        candidate_id = create_candidate_from_structured_recommendation({
            "source_type": "alert",
            "scenario_type": alert_type,
            "action_type": action_type,
            "target_type": "fund",
            "target_code": fund_code,
            "target_name": fund_name,
            "summary": title[:120],
            "rationale": content,
            "confidence": "medium",
            "dedupe_key": f"alert_{alert_type}_{fund_code}",
            "priority": 2 if severity == "warning" else 3,
            "source_snapshot": source_snapshot or {},
        })
        logger.info(f"[alert_scanner] 自动创建决策候选 #{candidate_id}：{title[:60]}")
        return candidate_id
    except Exception as e:
        logger.debug(f"[alert_scanner] 自动创建决策候选失败 {fund_code}: {e}")
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
                create_alert(
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
                                create_alert(
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
                    create_alert(
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
        create_alert(
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
    """定时扫描主入口，依次执行 8 个扫描函数。

    新增（2026-07-17）：
    - market_index_drop: 大盘指数当日跌幅监控（P0-1）
    - capital_flow: 南向资金异常流出监控（P0-3）
    """
    if not _is_enabled("alerts.proactive_scan_enabled", True):
        return {"skipped": "disabled"}

    results = {
        "scanned_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "verification": {},
        "valuation": {},
        "portfolio": {},
        "watchlist": {},
        "health_score": {},
        "valuation_failures": {},
        "market_index_drop": {},
        "capital_flow": {},
    }
    try:
        results["verification"] = scan_and_verify_recommendations()
    except Exception as e:
        logger.warning(f"[alert_scanner] 建议验证扫描失败: {e}")
        results["verification"] = {"error": str(e)}
    try:
        results["valuation"] = scan_valuation_thresholds()
    except Exception as e:
        logger.warning(f"[alert_scanner] 估值阈值扫描失败: {e}")
        results["valuation"] = {"error": str(e)}
    try:
        results["portfolio"] = scan_portfolio_risk()
    except Exception as e:
        logger.warning(f"[alert_scanner] 持仓风险扫描失败: {e}")
        results["portfolio"] = {"error": str(e)}
    try:
        results["watchlist"] = scan_watchlist_signals()
    except Exception as e:
        logger.warning(f"[alert_scanner] 关注列表扫描失败: {e}")
        results["watchlist"] = {"error": str(e)}
    try:
        results["health_score"] = scan_health_score()
    except Exception as e:
        logger.warning(f"[alert_scanner] 健康分扫描失败: {e}")
        results["health_score"] = {"error": str(e)}
    try:
        results["valuation_failures"] = scan_valuation_failures()
    except Exception as e:
        logger.warning(f"[alert_scanner] 估值失败扫描失败: {e}")
        results["valuation_failures"] = {"error": str(e)}
    # P0-1: 大盘指数当日跌幅监控（新增）
    try:
        results["market_index_drop"] = scan_market_index_drop()
    except Exception as e:
        logger.warning(f"[alert_scanner] 大盘指数跌幅扫描失败: {e}")
        results["market_index_drop"] = {"error": str(e)}
    # P0-3: 资金流向异常监控（新增）
    try:
        results["capital_flow"] = scan_capital_flow_anomaly()
    except Exception as e:
        logger.warning(f"[alert_scanner] 资金流向扫描失败: {e}")
        results["capital_flow"] = {"error": str(e)}
    return results
