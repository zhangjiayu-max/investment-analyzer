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
            val = get_best_valuation(code, query_source="alert_scanner", enable_online=False)
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
                alerts_created += 1
        except Exception as e:
            logger.debug(f"[alert_scanner] 估值扫描 {code} 失败: {e}")

    logger.info(f"[alert_scanner] 估值阈值扫描生成 {alerts_created} 个 alert")
    return {"alerts_created": alerts_created, "scanned_indexes": len(index_codes)}


# ── P0-B 持仓风险扫描 ────────────────────────────────────


def scan_portfolio_risk() -> dict:
    """扫描持仓集中度和亏损，生成风险 alert。

    Returns:
        {"alerts_created": int}
    """
    if not _is_enabled("alerts.proactive_scan_enabled", True):
        return {"alerts_created": 0, "skipped": "disabled"}

    concentration_threshold = _get_int("alerts.concentration_threshold", 30)
    loss_threshold = _get_int("alerts.loss_threshold", 15)

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

            # 集中度检测
            weight = current_value / total_value * 100
            if weight > concentration_threshold:
                title = f"{fund_name} 占比 {weight:.1f}%，集中度过高"
                content = (
                    f"{fund_name}（{fund_code}）当前占组合 {weight:.1f}%，"
                    f"超过 {concentration_threshold}% 阈值，建议关注分散化风险。"
                )
                create_alert(
                    alert_type="concentration_high",
                    title=title,
                    content=content,
                    severity="warning",
                    related_fund_code=fund_code,
                    related_fund_name=fund_name,
                    source="alert_scanner",
                )
                alerts_created += 1

            # 亏损检测
            if cost_price > 0:
                profit_rate = (current_price - cost_price) / cost_price * 100
                if profit_rate < -loss_threshold:
                    title = f"{fund_name} 当前亏损 {abs(profit_rate):.1f}%"
                    content = (
                        f"{fund_name}（{fund_code}）当前亏损 {abs(profit_rate):.1f}%，"
                        f"超过 -{loss_threshold}% 阈值，建议关注是否止损或加仓。"
                    )
                    create_alert(
                        alert_type="loss_warning",
                        title=title,
                        content=content,
                        severity="warning",
                        related_fund_code=fund_code,
                        related_fund_name=fund_name,
                        source="alert_scanner",
                    )
                    alerts_created += 1
        except Exception as e:
            logger.debug(f"[alert_scanner] 持仓扫描失败 {h.get('fund_code')}: {e}")

    logger.info(f"[alert_scanner] 持仓风险扫描生成 {alerts_created} 个 alert")
    return {"alerts_created": alerts_created, "holdings_scanned": len(holdings)}


# ── P0-C 关注列表信号扫描 ────────────────────────────────────


def _is_alert_recently_created(alert_type: str, related_fund_code: str, hours: int = 24) -> bool:
    """判断最近 N 小时内是否已生成过同类型 alert，避免重复打扰。"""
    from db._conn import _get_conn
    try:
        conn = _get_conn()
        threshold = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
        row = conn.execute(
            "SELECT id FROM alerts WHERE alert_type = ? AND related_fund_code = ? AND created_at >= ? LIMIT 1",
            (alert_type, related_fund_code, threshold),
        ).fetchone()
        conn.close()
        return row is not None
    except Exception:
        return False


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
    """P0-E 扫描估值查询全部失败的指数，生成预警 alert（闭环兜底监控）。

    检查最近 24 小时内 valuation_query_logs 中 final_source='failed' 的记录，
    对每个失败指数生成一条预警（同指数 24 小时内不重复）。

    Returns:
        {"alerts_created": int, "failed_indexes": int}
    """
    if not _is_enabled("alerts.valuation_failure_scan_enabled", True):
        return {"alerts_created": 0, "skipped": "disabled"}

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
        return {"alerts_created": 0, "error": str(e)}

    if not rows:
        return {"alerts_created": 0, "failed_indexes": 0}

    alerts_created = 0
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
        # 24 小时内不重复（按 normalize 后的 code 去重）
        if _is_alert_recently_created("valuation_query_failed", code, hours=24):
            continue
        try:
            create_alert(
                alert_type="valuation_query_failed",
                title=f"估值数据缺失：{name}",
                content=(
                    f"{name}（{code}）在本地表和在线渠道（akshare/天天基金）均无法获取估值数据，"
                    f"建议检查指数代码映射或手动录入估值图片。"
                ),
                severity="warning",
                related_fund_code=code,
                related_fund_name=name,
                source="alert_scanner",
            )
            alerts_created += 1
        except Exception as e:
            logger.debug(f"[alert_scanner] 估值失败 alert 创建失败 {code}: {e}")

    logger.info(f"[alert_scanner] 估值查询失败扫描：{len(rows)} 个指数失败，生成 {alerts_created} 个 alert")
    return {"alerts_created": alerts_created, "failed_indexes": len(rows)}


def run_periodic_scan() -> dict:
    """定时扫描主入口，依次执行 6 个扫描函数。"""
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
    return results
