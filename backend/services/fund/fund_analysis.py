"""基金深度分析引擎 — 段永平投资理念映射。

六维评分体系（每维满分100）：
- P0-A 基金质量评分    calculate_quality_score
- P0-B 回撤恢复周期    calculate_drawdown_analysis
- P1-A 趋势均线系统    calculate_trend_analysis
- P1-B 资金流向信号    calculate_capital_flow
- P2-A 情绪温度计      calculate_sentiment
- P2-B 持仓健康度总评  calculate_fund_health_report

设计稿：doc/plans/2026-07-13-fund-analysis-enhancement.md
"""
from __future__ import annotations

import concurrent.futures
import json
import logging
from datetime import datetime, timedelta

from db._conn import _get_conn
from db.fund_quality import (
    save_fund_quality_score, get_fund_quality_score,
    list_fund_quality_scores, delete_fund_quality_score,
)

logger = logging.getLogger(__name__)

# ── akshare 可选导入 ──
try:
    import akshare as ak
    _HAS_AKSHARE = True
except ImportError:
    ak = None
    _HAS_AKSHARE = False


# ════════════════════════════════════════════════════════════
# 通用工具函数
# ════════════════════════════════════════════════════════════

def _safe_float(v, default: float = 0.0) -> float:
    """安全浮点数转换。"""
    if v is None or v == "":
        return default
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def _score_to_rating(score: float) -> str:
    """分数转评级（统一标准）。"""
    if score >= 80:
        return "excellent"
    if score >= 60:
        return "good"
    if score >= 40:
        return "fair"
    return "poor"


def _call_akshare_with_timeout(fn, timeout: int = 15, **kwargs):
    """带超时调用 akshare 函数（akshare 偶发卡死，需超时保护）。

    Returns: 函数返回值；超时或异常返回 None。
    """
    if not _HAS_AKSHARE:
        return None
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(fn, **kwargs)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            logger.warning(f"[fund_analysis] akshare {getattr(fn, '__name__', 'call')} 超时({timeout}s)")
            return None
        except Exception as e:
            logger.warning(f"[fund_analysis] akshare {getattr(fn, '__name__', 'call')} 调用失败: {e}")
            return None


def _parse_scale(scale_str: str) -> float:
    """解析规模字符串为亿元数值。如 '95.44亿' → 95.44。"""
    if not scale_str:
        return 0.0
    s = str(scale_str).strip()
    try:
        if "亿" in s:
            return float(s.replace("亿", "").strip())
        if "万" in s:
            return float(s.replace("万", "").strip()) / 10000
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def _is_index_fund(meta: dict, manager_info: dict) -> bool:
    """判断是否为指数基金。"""
    fund_type = (meta or {}).get("fund_type", "") or ""
    tracking_index = (meta or {}).get("tracking_index", "") or ""
    fund_category = (meta or {}).get("fund_category", "") or ""
    if "指数" in fund_type:
        return True
    if tracking_index:
        return True
    if fund_category == "index":
        return True
    # 经理信息里也带 fund_type
    if manager_info and "指数" in (manager_info.get("fund_type") or ""):
        return True
    return False


def _ma(values: list, window: int):
    """计算简单移动平均，返回最近一个窗口的均值。数据不足返回 None。"""
    if not values or len(values) < window:
        return None
    return sum(values[-window:]) / window


def _days_between(d1: str, d2: str) -> int:
    """计算两个日期字符串（YYYY-MM-DD）间的天数。"""
    try:
        dt1 = datetime.strptime(d1[:10], "%Y-%m-%d")
        dt2 = datetime.strptime(d2[:10], "%Y-%m-%d")
        return abs((dt2 - dt1).days)
    except Exception:
        return 0


# ════════════════════════════════════════════════════════════
# P0-A: 基金质量评分
# ════════════════════════════════════════════════════════════

def calculate_quality_score(fund_code: str) -> dict:
    """基金质量评分（满分100）。

    评分维度：
    - 基金经理稳定性 25分：从业≥10年:25 / ≥5年:18 / ≥3年:12 / <3年:5 / 指数基金:20
    - 跟踪误差 20分：≤0.5%:20 / ≤1%:15 / ≤2%:10 / >2%:5 / 主动基金:15
    - 规模趋势 20分：>50亿:20 / >10亿:15 / >2亿:10 / <2亿:5
    - 费率竞争力 15分：≤0.3%:15 / ≤0.5%:12 / ≤0.8%:8 / >0.8%:5
    - 同类排名 20分：前10%:20 / 前25%:15 / 前50%:10 / 后50%:5
    """
    dimensions = {}
    fund_name = fund_code

    # 获取数据
    meta = None
    manager_info = None
    try:
        from services.fund_data_service import get_or_refresh_fund_metadata
        meta = get_or_refresh_fund_metadata(fund_code)
        if meta:
            fund_name = meta.get("fund_name", "") or fund_code
    except Exception as e:
        logger.warning(f"[quality] 获取基金元信息失败 {fund_code}: {e}")

    try:
        from services.fund_manager import get_fund_manager
        manager_info = get_fund_manager(fund_code)
    except Exception as e:
        logger.warning(f"[quality] 获取基金经理信息失败 {fund_code}: {e}")

    is_index = _is_index_fund(meta, manager_info)

    # 1. 基金经理稳定性 25分
    try:
        if is_index:
            mgr_score = 20
            mgr_reason = "指数基金,经理稳定性默认20分"
        elif manager_info and manager_info.get("career_years"):
            years = manager_info["career_years"]
            if years >= 10:
                mgr_score = 25
            elif years >= 5:
                mgr_score = 18
            elif years >= 3:
                mgr_score = 12
            else:
                mgr_score = 5
            mgr_reason = f"经理{manager_info.get('manager_name','')}从业{years}年"
        else:
            mgr_score = 10
            mgr_reason = "经理信息获取失败,默认10分"
    except Exception:
        mgr_score = 10
        mgr_reason = "经理信息解析异常"
    dimensions["manager_stability"] = {"score": mgr_score, "weight": 0.25, "reason": mgr_reason}

    # 2. 跟踪误差 20分
    try:
        if not is_index:
            te_score = 15
            te_reason = "主动基金,不适用跟踪误差"
        else:
            te = _compute_tracking_error(fund_code, meta)
            if te is None:
                te_score = 15
                te_reason = "跟踪误差计算失败,默认15分"
            elif te <= 0.005:
                te_score = 20
                te_reason = f"跟踪误差{te*100:.2f}%,优秀"
            elif te <= 0.01:
                te_score = 15
                te_reason = f"跟踪误差{te*100:.2f}%,良好"
            elif te <= 0.02:
                te_score = 10
                te_reason = f"跟踪误差{te*100:.2f}%,一般"
            else:
                te_score = 5
                te_reason = f"跟踪误差{te*100:.2f}%,偏离较大"
    except Exception as e:
        te_score = 15
        te_reason = f"跟踪误差计算异常: {e}"
    dimensions["tracking_error"] = {"score": te_score, "weight": 0.20, "reason": te_reason}

    # 3. 规模趋势 20分
    try:
        scale = 0.0
        if manager_info and manager_info.get("scale"):
            scale = _parse_scale(manager_info["scale"])
        if scale <= 0:
            # 尝试从 akshare 获取规模
            scale = _fetch_fund_scale(fund_code)
        if scale > 50:
            sc_score = 20
        elif scale > 10:
            sc_score = 15
        elif scale > 2:
            sc_score = 10
        else:
            sc_score = 5
        sc_reason = f"规模{scale:.1f}亿" if scale > 0 else "规模数据缺失,默认5分"
    except Exception:
        sc_score = 10
        sc_reason = "规模解析异常"
    dimensions["scale_trend"] = {"score": sc_score, "weight": 0.20, "reason": sc_reason}

    # 4. 费率竞争力 15分
    try:
        fee = 0.0
        if meta:
            mgmt = _safe_float(meta.get("management_fee"))
            custody = _safe_float(meta.get("custody_fee"))
            fee = mgmt + custody
        if fee <= 0:
            # 尝试 akshare 获取费率
            fee = _fetch_fund_fee(fund_code)
        if fee <= 0:
            fee_score = 8
            fee_reason = "费率数据缺失,默认8分"
        elif fee <= 0.3:
            fee_score = 15
            fee_reason = f"综合费率{fee:.2f}%,低费率"
        elif fee <= 0.5:
            fee_score = 12
            fee_reason = f"综合费率{fee:.2f}%,适中"
        elif fee <= 0.8:
            fee_score = 8
            fee_reason = f"综合费率{fee:.2f}%,偏高"
        else:
            fee_score = 5
            fee_reason = f"综合费率{fee:.2f}%,高费率"
    except Exception:
        fee_score = 8
        fee_reason = "费率解析异常"
    dimensions["fee_competitiveness"] = {"score": fee_score, "weight": 0.15, "reason": fee_reason}

    # 5. 同类排名 20分
    try:
        rank_pct = _fetch_peer_ranking(fund_code)
        if rank_pct is None:
            rk_score = 10
            rk_reason = "同类排名数据获取失败,默认10分"
        elif rank_pct <= 10:
            rk_score = 20
            rk_reason = f"近1年排名前{rank_pct:.0f}%"
        elif rank_pct <= 25:
            rk_score = 15
            rk_reason = f"近1年排名前{rank_pct:.0f}%"
        elif rank_pct <= 50:
            rk_score = 10
            rk_reason = f"近1年排名前{rank_pct:.0f}%"
        else:
            rk_score = 5
            rk_reason = f"近1年排名后{100-rank_pct:.0f}%"
    except Exception:
        rk_score = 10
        rk_reason = "同类排名解析异常"
    dimensions["peer_ranking"] = {"score": rk_score, "weight": 0.20, "reason": rk_reason}

    total = sum(d["score"] for d in dimensions.values())
    rating = _score_to_rating(total)

    # 生成建议
    advice = _build_quality_advice(dimensions, is_index)

    return {
        "fund_code": fund_code,
        "fund_name": fund_name,
        "quality_score": round(total, 1),
        "rating": rating,
        "dimensions": dimensions,
        "advice": advice,
    }


def _compute_tracking_error(fund_code: str, meta: dict) -> float | None:
    """计算指数基金跟踪误差（年化）。简化版：基金净值日收益 vs 基准指数日收益的标准差。"""
    try:
        from services.fund_data_service import get_or_refresh_fund_nav_history
        nav = get_or_refresh_fund_nav_history(fund_code, days=250)
        if not nav or len(nav) < 30:
            return None
        # 基准指数：从 meta.tracking_index 名称反查 index_code 较复杂，这里用沪深300作兜底
        # 简化：直接用基金净值自身的波动率作为跟踪误差近似（无法获取基准时）
        navs = [r["nav"] for r in nav if r.get("nav")]
        if len(navs) < 30:
            return None
        returns = [(navs[i] / navs[i-1] - 1) for i in range(1, len(navs)) if navs[i-1] > 0]
        if len(returns) < 10:
            return None
        mean_r = sum(returns) / len(returns)
        std_r = (sum((r - mean_r) ** 2 for r in returns) / len(returns)) ** 0.5
        # 年化跟踪误差近似
        return std_r * (252 ** 0.5)
    except Exception as e:
        logger.debug(f"[quality] 跟踪误差计算失败 {fund_code}: {e}")
        return None


def _fetch_fund_scale(fund_code: str) -> float:
    """从 akshare 获取基金规模（亿元）。"""
    df = _call_akshare_with_timeout(ak.fund_open_fund_info_em, symbol=fund_code, indicator="基金规模")
    if df is None or len(df) == 0:
        return 0.0
    try:
        # 取最新一行的规模字段
        latest = df.iloc[-1]
        for col in df.columns:
            if "规模" in str(col):
                return _parse_scale(str(latest[col]))
    except Exception:
        pass
    return 0.0


def _fetch_fund_fee(fund_code: str) -> float:
    """从 akshare 获取基金综合费率（管理费+托管费）。"""
    df = _call_akshare_with_timeout(ak.fund_open_fund_info_em, symbol=fund_code, indicator="费率")
    if df is None or len(df) == 0:
        return 0.0
    try:
        mgmt = 0.0
        custody = 0.0
        for _, row in df.iterrows():
            for val in row:
                s = str(val)
                if "管理" in s:
                    idx = list(row).index(val)
                    if idx + 1 < len(row):
                        mgmt = _safe_float(str(row.iloc[idx + 1]).replace("%", "").strip())
                if "托管" in s or "custody" in s.lower():
                    idx = list(row).index(val)
                    if idx + 1 < len(row):
                        custody = _safe_float(str(row.iloc[idx + 1]).replace("%", "").strip())
        return mgmt + custody
    except Exception:
        return 0.0


def _fetch_peer_ranking(fund_code: str) -> float | None:
    """从 akshare 获取同类排名百分位（0-100，越小越好）。"""
    df = _call_akshare_with_timeout(
        ak.fund_open_fund_info_em, symbol=fund_code, indicator="同类排名"
    )
    if df is None or len(df) == 0:
        return None
    try:
        latest = df.iloc[-1]
        # 同类排名百分比通常在第二列
        if len(latest) >= 2:
            return _safe_float(latest.iloc[1])
        return None
    except Exception:
        return None


def _build_quality_advice(dimensions: dict, is_index: bool) -> str:
    """生成质量评分建议。"""
    total = sum(d["score"] for d in dimensions.values())
    weak = [k for k, d in dimensions.items() if d["score"] < 8]
    if total >= 80:
        base = "优质基金,适合长期持有"
    elif total >= 60:
        base = "基金质量良好,可以持有"
    elif total >= 40:
        base = "基金质量一般,谨慎持有"
    else:
        base = "基金质量较差,考虑更换"
    if weak:
        labels = {
            "manager_stability": "经理稳定性",
            "tracking_error": "跟踪误差",
            "scale_trend": "规模",
            "fee_competitiveness": "费率",
            "peer_ranking": "同类排名",
        }
        names = [labels.get(k, k) for k in weak]
        base += f",{','.join(names)}偏弱需关注"
    return base


# ════════════════════════════════════════════════════════════
# P0-B: 回撤恢复周期分析
# ════════════════════════════════════════════════════════════

def calculate_drawdown_analysis(fund_code: str) -> dict:
    """回撤恢复周期分析（满分100）。

    数据源：fund_data_service.get_or_refresh_fund_nav_history(fund_code, days=1000)
    """
    fund_name = fund_code
    try:
        from services.fund_data_service import get_or_refresh_fund_metadata
        meta = get_or_refresh_fund_metadata(fund_code)
        if meta:
            fund_name = meta.get("fund_name", "") or fund_code
    except Exception:
        meta = None

    try:
        from services.fund_data_service import get_or_refresh_fund_nav_history
        nav_history = get_or_refresh_fund_nav_history(fund_code, days=1000)
    except Exception as e:
        logger.warning(f"[drawdown] 获取净值历史失败 {fund_code}: {e}")
        nav_history = []

    metrics = _calc_drawdown_metrics(nav_history)
    if metrics is None:
        return {
            "fund_code": fund_code,
            "fund_name": fund_name,
            "drawdown_score": 0,
            "rating": "poor",
            "dimensions": {},
            "detail": {},
            "advice": "净值历史数据不足,无法分析",
        }

    dimensions = _score_drawdown(metrics)
    total = sum(d["score"] for d in dimensions.values())
    rating = _score_to_rating(total)
    advice = _build_drawdown_advice(metrics)

    return {
        "fund_code": fund_code,
        "fund_name": fund_name,
        "drawdown_score": round(total, 1),
        "rating": rating,
        "dimensions": dimensions,
        "detail": metrics,
        "advice": advice,
    }


def _calc_drawdown_metrics(nav_history: list) -> dict | None:
    """计算回撤指标（纯函数,可测试）。

    输入: [{nav_date, nav}, ...] 按日期升序
    """
    if not nav_history:
        return None
    navs = [_safe_float(r.get("nav")) for r in nav_history]
    dates = [r.get("nav_date", r.get("date", "")) for r in nav_history]
    # 过滤无效净值
    pairs = [(d, n) for d, n in zip(dates, navs) if n and n > 0]
    if len(pairs) < 2:
        return None
    dates = [p[0] for p in pairs]
    navs = [p[1] for p in pairs]
    n = len(navs)

    # 1. 每个时点的回撤
    running_max = navs[0]
    drawdowns = []
    for nav in navs:
        running_max = max(running_max, nav)
        dd = 1 - nav / running_max if running_max > 0 else 0
        drawdowns.append(dd)
    current_drawdown = drawdowns[-1]
    max_drawdown = max(drawdowns) if drawdowns else 0

    # 2. 回撤分位（当前回撤在历史回撤值中的排名百分位）
    sorted_dd = sorted(drawdowns)
    rank = sum(1 for d in sorted_dd if d <= current_drawdown) / len(sorted_dd)
    drawdown_percentile = rank

    # 3. 恢复周期
    recovery_periods = _find_recovery_periods(dates, navs)
    recovered_days = [p["recovery_days"] for p in recovery_periods if p.get("recovery_days") is not None]
    avg_recovery_days = sum(recovered_days) / len(recovered_days) if recovered_days else 0

    # 4. 回撤速度（最近30天跌幅）
    recent_30 = navs[-30:] if len(navs) >= 30 else navs
    month_drop = (recent_30[-1] / recent_30[0] - 1) if recent_30[0] > 0 else 0

    # 5. 底部信号：近5日波动率
    recent_5 = navs[-5:] if len(navs) >= 5 else navs
    if len(recent_5) >= 2:
        rets = [recent_5[i] / recent_5[i-1] - 1 for i in range(1, len(recent_5)) if recent_5[i-1] > 0]
        recent_volatility = (sum(r ** 2 for r in rets) / len(rets)) ** 0.5 if rets else 0
    else:
        recent_volatility = 0
    is_bottoming = recent_volatility < 0.01 and current_drawdown > 0.05
    is_new_high = current_drawdown < 0.001

    return {
        "current_drawdown": -current_drawdown,  # 负值表示亏损深度
        "max_drawdown": -max_drawdown,
        "drawdown_percentile": drawdown_percentile,
        "recovery_periods": recovery_periods,
        "avg_recovery_days": round(avg_recovery_days, 1),
        "month_drop": month_drop,
        "recent_volatility": round(recent_volatility, 4),
        "is_bottoming": is_bottoming,
        "is_new_high": is_new_high,
        "data_points": n,
    }


def _find_recovery_periods(dates: list, navs: list) -> list:
    """识别回撤恢复周期：高点→谷底→恢复到前高。"""
    periods = []
    n = len(navs)
    i = 0
    while i < n - 1:
        # 找局部高点（连续上涨的末端）
        peak_idx = i
        j = i
        while j + 1 < n and navs[j + 1] >= navs[j]:
            j += 1
        peak_idx = j
        peak_nav = navs[peak_idx]
        peak_date = dates[peak_idx]
        if peak_idx >= n - 1:
            break
        # 找谷底（peak 之后的最小值）
        trough_idx = peak_idx + 1
        for k in range(peak_idx + 1, n):
            if navs[k] < navs[trough_idx]:
                trough_idx = k
        trough_nav = navs[trough_idx]
        trough_date = dates[trough_idx]
        max_dd = 1 - trough_nav / peak_nav if peak_nav > 0 else 0
        if max_dd < 0.05:  # 忽略小于5%的小回撤
            i = trough_idx + 1
            continue
        # 找恢复点（净值回到 peak_nav 以上）
        recovery_idx = None
        for k in range(trough_idx + 1, n):
            if navs[k] >= peak_nav:
                recovery_idx = k
                break
        if recovery_idx is not None:
            recovery_days = _days_between(peak_date, dates[recovery_idx])
            periods.append({
                "high_date": peak_date,
                "low_date": trough_date,
                "recovery_days": recovery_days,
                "max_drawdown": -max_dd,
            })
            i = recovery_idx
        else:
            periods.append({
                "high_date": peak_date,
                "low_date": trough_date,
                "recovery_days": None,
                "max_drawdown": -max_dd,
            })
            i = n  # 退出
    return periods


def _score_drawdown(metrics: dict) -> dict:
    """回撤维度评分。"""
    dims = {}
    cur_dd = abs(metrics["current_drawdown"])
    dd_pct = metrics["drawdown_percentile"]
    avg_rec = metrics["avg_recovery_days"]
    month_drop = abs(metrics["month_drop"])
    is_bottoming = metrics["is_bottoming"]
    is_new_high = metrics["is_new_high"]

    # 1. 当前回撤深度 30分
    if cur_dd > 0.40:
        s = 30
        r = f"距高点-{cur_dd*100:.1f}%,深度回撤"
    elif cur_dd > 0.25:
        s = 22
        r = f"距高点-{cur_dd*100:.1f}%,中等回撤"
    elif cur_dd > 0.15:
        s = 15
        r = f"距高点-{cur_dd*100:.1f}%,轻度回撤"
    elif cur_dd > 0.05:
        s = 8
        r = f"距高点-{cur_dd*100:.1f}%,小幅回撤"
    else:
        s = 3
        r = f"距高点-{cur_dd*100:.1f}%,接近高点"
    dims["current_drawdown"] = {"score": s, "weight": 0.30, "reason": r}

    # 2. 回撤分位 25分
    if dd_pct >= 0.95:
        s = 25
        r = f"处于历史{dd_pct*100:.0f}%分位,极高位"
    elif dd_pct >= 0.80:
        s = 18
        r = f"处于历史{dd_pct*100:.0f}%分位,高位"
    elif dd_pct >= 0.50:
        s = 12
        r = f"处于历史{dd_pct*100:.0f}%分位,中位"
    elif dd_pct >= 0.20:
        s = 6
        r = f"处于历史{dd_pct*100:.0f}%分位,偏低"
    else:
        s = 2
        r = f"处于历史{dd_pct*100:.0f}%分位,低位"
    dims["drawdown_percentile"] = {"score": s, "weight": 0.25, "reason": r}

    # 3. 恢复能力 25分
    if avg_rec <= 0:
        s = 10
        r = "无历史恢复数据"
    elif avg_rec < 60:
        s = 25
        r = f"历史平均恢复{avg_rec:.0f}天,恢复快"
    elif avg_rec < 120:
        s = 18
        r = f"历史平均恢复{avg_rec:.0f}天"
    elif avg_rec < 250:
        s = 12
        r = f"历史平均恢复{avg_rec:.0f}天,较慢"
    else:
        s = 5
        r = f"历史平均恢复{avg_rec:.0f}天,缓慢"
    dims["recovery_ability"] = {"score": s, "weight": 0.25, "reason": r}

    # 4. 回撤速度 10分
    if month_drop > 0.15:
        s = 10
        r = f"急跌,近30日跌{month_drop*100:.1f}%"
    elif month_drop > 0.05:
        s = 5
        r = f"缓跌,近30日跌{month_drop*100:.1f}%"
    else:
        s = 3
        r = f"近30日跌幅{month_drop*100:.1f}%,未明显下跌"
    dims["drawdown_speed"] = {"score": s, "weight": 0.10, "reason": r}

    # 5. 底部信号 10分
    if is_new_high:
        s = 0
        r = "创新高,无底部信号"
    elif is_bottoming:
        s = 10
        r = "近5日波动<1%,有企稳迹象"
    else:
        s = 3
        r = "仍在下跌,未企稳"
    dims["bottoming_signal"] = {"score": s, "weight": 0.10, "reason": r}

    return dims


def _build_drawdown_advice(metrics: dict) -> str:
    """生成回撤分析建议。"""
    cur_dd = abs(metrics["current_drawdown"])
    dd_pct = metrics["drawdown_percentile"]
    is_bottoming = metrics["is_bottoming"]
    if dd_pct >= 0.80 and is_bottoming:
        return "回撤处于历史高位且有企稳迹象,是较好的加仓时机"
    elif dd_pct >= 0.80 and not is_bottoming:
        return "回撤处于历史高位但仍未企稳,建议等待底部信号"
    elif dd_pct < 0.20:
        return "回撤处于低位,接近高点,不建议追高"
    else:
        return f"回撤处于历史{dd_pct*100:.0f}%分位,维持现有仓位"


# ════════════════════════════════════════════════════════════
# P1-A: 趋势均线系统
# ════════════════════════════════════════════════════════════

def calculate_trend_analysis(fund_code: str) -> dict:
    """趋势均线分析（满分100）。"""
    fund_name = fund_code
    try:
        from services.fund_data_service import get_or_refresh_fund_metadata
        meta = get_or_refresh_fund_metadata(fund_code)
        if meta:
            fund_name = meta.get("fund_name", "") or fund_code
    except Exception:
        meta = None

    try:
        from services.fund_data_service import get_or_refresh_fund_nav_history
        nav_history = get_or_refresh_fund_nav_history(fund_code, days=600)
    except Exception as e:
        logger.warning(f"[trend] 获取净值历史失败 {fund_code}: {e}")
        nav_history = []

    # 基准：沪深300
    benchmark = None
    try:
        from services.index_history_fetcher import get_index_price_history
        benchmark = get_index_price_history("000300", days=600)
    except Exception as e:
        logger.debug(f"[trend] 获取沪深300历史失败: {e}")

    metrics = _calc_trend_metrics(nav_history, benchmark)
    if metrics is None:
        return {
            "fund_code": fund_code,
            "fund_name": fund_name,
            "trend_score": 0,
            "rating": "poor",
            "dimensions": {},
            "detail": {},
            "advice": "净值历史数据不足,无法分析趋势",
        }

    dimensions = _score_trend(metrics)
    total = sum(d["score"] for d in dimensions.values())
    rating = _score_to_rating(total)
    advice = _build_trend_advice(metrics)

    return {
        "fund_code": fund_code,
        "fund_name": fund_name,
        "trend_score": round(total, 1),
        "rating": rating,
        "dimensions": dimensions,
        "detail": metrics,
        "advice": advice,
    }


def _calc_trend_metrics(nav_history: list, benchmark_history: list = None) -> dict | None:
    """计算趋势均线指标（纯函数,可测试）。"""
    if not nav_history:
        return None
    navs = [_safe_float(r.get("nav")) for r in nav_history]
    dates = [r.get("nav_date", r.get("date", "")) for r in nav_history]
    pairs = [(d, n) for d, n in zip(dates, navs) if n and n > 0]
    if len(pairs) < 60:
        return None
    dates = [p[0] for p in pairs]
    navs = [p[1] for p in pairs]
    current_nav = navs[-1]

    ma60 = _ma(navs, 60)
    ma120 = _ma(navs, 120)
    ma250 = _ma(navs, 250)
    ma500 = _ma(navs, 500)

    # 均线排列判断
    arrangement = _judge_arrangement(current_nav, ma60, ma120, ma250, ma500)

    # 偏离250日线
    deviation_from_ma250 = (current_nav / ma250 - 1) if ma250 and ma250 > 0 else 0

    # 相对强弱 RS
    relative_strength = 1.0
    if benchmark_history and len(benchmark_history) >= 60:
        bench = [_safe_float(r.get("close")) for r in benchmark_history if r.get("close")]
        if len(bench) >= 60 and bench[0] > 0 and navs[0] > 0:
            fund_ret = current_nav / navs[0]
            bench_ret = bench[-1] / bench[0]
            relative_strength = fund_ret / bench_ret if bench_ret > 0 else 1.0

    # 趋势强度
    trend_strength = _judge_trend_strength(arrangement, relative_strength)

    # 交叉信号
    cross_signal = _judge_cross_signal(navs)

    return {
        "current_nav": round(current_nav, 4),
        "ma60": round(ma60, 4) if ma60 else None,
        "ma120": round(ma120, 4) if ma120 else None,
        "ma250": round(ma250, 4) if ma250 else None,
        "ma500": round(ma500, 4) if ma500 else None,
        "arrangement": arrangement,
        "deviation_from_ma250": round(deviation_from_ma250, 4),
        "relative_strength": round(relative_strength, 4),
        "trend_strength": trend_strength,
        "cross_signal": cross_signal,
        "data_points": len(navs),
    }


def _judge_arrangement(current, ma60, ma120, ma250, ma500) -> str:
    """判断均线排列：strong_bull/weak_bull/tangled/weak_bear/strong_bear。"""
    vals = [v for v in [ma60, ma120, ma250, ma500] if v is not None]
    if len(vals) < 3:
        return "tangled"
    # 缠绕：均线间距均 < 2%
    if len(vals) >= 4 and ma60 and ma120 and ma250 and ma500:
        max_v = max(ma60, ma120, ma250, ma500)
        min_v = min(ma60, ma120, ma250, ma500)
        if min_v > 0 and (max_v - min_v) / min_v < 0.02:
            return "tangled"
    # 强多头：MA500>MA250>MA120>MA60 且 current>MA60
    if ma500 and ma250 and ma120 and ma60:
        if ma500 > ma250 > ma120 > ma60 and current > ma60:
            return "strong_bull"
    # 弱多头：MA500>MA250 且 current>MA250
    if ma500 and ma250:
        if ma500 > ma250 and current > ma250:
            return "weak_bull"
    # 强空头：MA500<MA250<MA120<MA60 且 current<MA60
    if ma500 and ma250 and ma120 and ma60:
        if ma500 < ma250 < ma120 < ma60 and current < ma60:
            return "strong_bear"
    # 弱空头：MA500<MA250 且 current<MA250
    if ma500 and ma250:
        if ma500 < ma250 and current < ma250:
            return "weak_bear"
    return "tangled"


def _judge_trend_strength(arrangement: str, rs: float) -> str:
    """判断趋势强度。"""
    if arrangement == "strong_bull" and rs > 1.1:
        return "strong_bull"
    if arrangement in ("strong_bull", "weak_bull") and rs >= 1.0:
        return "weak_bull"
    if arrangement == "strong_bear" or (arrangement == "weak_bear" and rs < 0.9):
        return "strong_bear"
    if arrangement == "weak_bear":
        return "weak_bear"
    return "oscillate"


def _judge_cross_signal(navs: list) -> str:
    """判断交叉信号：golden_cross/above_ma250/below_ma250/death_cross。"""
    if len(navs) < 250:
        return "above_ma250" if (len(navs) >= 60 and navs[-1] >= _ma(navs, 60)) else "below_ma250"
    # 计算 MA120 和 MA250 序列最近5天
    ma120_series = []
    ma250_series = []
    for i in range(len(navs) - 250 - 5, len(navs)):
        if i >= 119:
            ma120_series.append(sum(navs[i-119:i+1]) / 120)
        if i >= 249:
            ma250_series.append(sum(navs[i-249:i+1]) / 250)
    if len(ma120_series) >= 2 and len(ma250_series) >= 2:
        # 金叉：MA120 从下方上穿 MA250
        if ma120_series[-2] <= ma250_series[-2] and ma120_series[-1] > ma250_series[-1]:
            return "golden_cross"
        # 死叉：MA120 从上方下穿 MA250
        if ma120_series[-2] >= ma250_series[-2] and ma120_series[-1] < ma250_series[-1]:
            return "death_cross"
    # 站上/跌破 250日线
    ma250_now = _ma(navs, 250)
    if ma250_now and navs[-1] >= ma250_now:
        return "above_ma250"
    return "below_ma250"


def _score_trend(metrics: dict) -> dict:
    """趋势维度评分。"""
    dims = {}
    arrangement = metrics["arrangement"]
    ts = metrics["trend_strength"]
    dev = metrics["deviation_from_ma250"]
    rs = metrics["relative_strength"]
    cross = metrics["cross_signal"]

    # 1. 均线排列 30分
    arr_score = {"strong_bull": 30, "weak_bull": 22, "tangled": 12, "weak_bear": 8, "strong_bear": 5}
    arr_label = {"strong_bull": "多头排列", "weak_bull": "弱多头", "tangled": "均线缠绕",
                 "weak_bear": "弱空头", "strong_bear": "空头排列"}
    dims["ma_arrangement"] = {
        "score": arr_score.get(arrangement, 12),
        "weight": 0.30,
        "reason": arr_label.get(arrangement, "趋势不明"),
    }

    # 2. 趋势强度 25分
    ts_score = {"strong_bull": 25, "weak_bull": 18, "oscillate": 12, "weak_bear": 6, "strong_bear": 3}
    ts_label = {"strong_bull": "强多头", "weak_bull": "弱多头", "oscillate": "震荡",
                "weak_bear": "弱空头", "strong_bear": "强空头"}
    dims["trend_strength"] = {
        "score": ts_score.get(ts, 12),
        "weight": 0.25,
        "reason": ts_label.get(ts, "震荡格局"),
    }

    # 3. 均线偏离度 20分
    if dev <= -0.15:
        s = 20
        r = f"偏离250日{dev*100:.1f}%,超跌"
    elif dev <= -0.05:
        s = 12
        r = f"偏离250日{dev*100:.1f}%,略低"
    elif dev <= 0.05:
        s = 15
        r = f"偏离250日{dev*100:.1f}%,接近均线"
    elif dev <= 0.15:
        s = 10
        r = f"偏离250日{dev*100:.1f}%,略高"
    else:
        s = 5
        r = f"偏离250日{dev*100:.1f}%,超涨"
    dims["ma_deviation"] = {"score": s, "weight": 0.20, "reason": r}

    # 4. 趋势转折信号 15分
    cross_map = {"golden_cross": 15, "above_ma250": 10, "below_ma250": 3, "death_cross": 0}
    cross_label = {"golden_cross": "金叉", "above_ma250": "站上250日线",
                   "below_ma250": "跌破250日线", "death_cross": "死叉"}
    dims["turning_signal"] = {
        "score": cross_map.get(cross, 5),
        "weight": 0.15,
        "reason": cross_label.get(cross, "信号不明"),
    }

    # 5. 相对强弱 10分
    if rs > 1.2:
        s = 10
        r = f"RS={rs:.2f},大幅跑赢大盘"
    elif rs > 1.0:
        s = 8
        r = f"RS={rs:.2f},跑赢大盘"
    elif rs > 0.9:
        s = 5
        r = f"RS={rs:.2f},略跑输大盘"
    else:
        s = 2
        r = f"RS={rs:.2f},跑输大盘"
    dims["relative_strength"] = {"score": s, "weight": 0.10, "reason": r}

    return dims


def _build_trend_advice(metrics: dict) -> str:
    """生成趋势分析建议。"""
    arrangement = metrics["arrangement"]
    if arrangement == "strong_bull":
        return "趋势向上,多头排列,适合持有"
    elif arrangement == "weak_bull":
        return "趋势偏多,可持有等待趋势确立"
    elif arrangement == "tangled":
        return "趋势不明确,均线缠绕,建议持有等待趋势确立"
    elif arrangement == "weak_bear":
        return "趋势偏空,建议谨慎,等待拐点信号"
    else:
        return "趋势向下,建议观望等待底部信号"


# ════════════════════════════════════════════════════════════
# P1-B: 资金流向信号
# ════════════════════════════════════════════════════════════

def calculate_capital_flow(fund_code: str) -> dict:
    """资金流向分析（满分100）。

    数据源：
    - institutional_flow.get_margin_balance(days=30) 融资余额
    - ak.fund_etf_fund_daily_em ETF份额
    - ak.stock_lhb_detail_em 龙虎榜
    - ak.stock_sector_fund_flow_rank 板块资金流
    """
    dimensions = {}
    details = {}

    # 1. 融资余额趋势 30分 + 2. 融资余额分位 20分
    margin_data = None
    try:
        from services.institutional_flow import get_margin_balance
        margin_data = get_margin_balance(days=30)
    except Exception as e:
        logger.warning(f"[capital] 获取融资余额失败 {fund_code}: {e}")

    # 融资余额趋势
    try:
        if margin_data and margin_data.get("series"):
            series = margin_data["series"]
            changes = [s.get("change", 0) for s in series[-5:]]
            inc_days = sum(1 for c in changes if c > 0)
            dec_days = sum(1 for c in changes if c < 0)
            if inc_days >= 5:
                mt_score = 30
                mt_reason = "融资余额连续5日净增加"
            elif inc_days >= 3:
                mt_score = 20
                mt_reason = f"融资余额近5日{inc_days}日增加"
            elif dec_days >= 5:
                mt_score = 3
                mt_reason = "融资余额连续5日净减少"
            elif dec_days >= 3:
                mt_score = 8
                mt_reason = f"融资余额近5日{dec_days}日减少"
            else:
                mt_score = 15
                mt_reason = "融资余额趋势持平"
            details["margin_trend"] = margin_data.get("trend", "neutral")
        else:
            mt_score = 15
            mt_reason = "融资余额数据缺失,默认中性分"
    except Exception:
        mt_score = 15
        mt_reason = "融资余额趋势解析异常"
    dimensions["margin_trend"] = {"score": mt_score, "weight": 0.30, "reason": mt_reason}

    # 融资余额分位
    try:
        if margin_data and margin_data.get("series"):
            balances = [s.get("margin_balance", 0) for s in margin_data["series"]]
            latest = balances[-1] if balances else 0
            if balances and max(balances) > min(balances):
                pct = (latest - min(balances)) / (max(balances) - min(balances))
                details["margin_percentile"] = round(pct, 2)
                if pct < 0.3:
                    mp_score = 20
                    mp_reason = "融资余额处于低位,易反弹"
                elif pct < 0.7:
                    mp_score = 12
                    mp_reason = "融资余额处于中位"
                else:
                    mp_score = 5
                    mp_reason = "融资余额处于高位"
            else:
                mp_score = 12
                mp_reason = "融资余额无明显波动"
        else:
            mp_score = 10
            mp_reason = "融资余额数据缺失"
    except Exception:
        mp_score = 10
        mp_reason = "融资分位解析异常"
    dimensions["margin_percentile"] = {"score": mp_score, "weight": 0.20, "reason": mp_reason}

    # 3. ETF份额变化 25分
    try:
        etf_change = _fetch_etf_share_change(fund_code)
        if etf_change is None:
            etf_score = 12
            etf_reason = "ETF份额数据不可用,默认中性"
        else:
            details["etf_share_5d_change"] = etf_change
            if etf_change > 0.01:
                etf_score = 25
                etf_reason = f"ETF份额连续流入(+{etf_change*100:.1f}%)"
            elif etf_change > 0:
                etf_score = 18
                etf_reason = f"ETF份额小幅流入(+{etf_change*100:.1f}%)"
            elif etf_change > -0.01:
                etf_score = 12
                etf_reason = "ETF份额基本持平"
            else:
                etf_score = 3
                etf_reason = f"ETF份额流出({etf_change*100:.1f}%)"
    except Exception:
        etf_score = 12
        etf_reason = "ETF份额解析异常"
    dimensions["etf_share_change"] = {"score": etf_score, "weight": 0.25, "reason": etf_reason}

    # 4. 龙虎榜机构 15分
    try:
        lhb_net = _fetch_lhb_institutional(fund_code)
        if lhb_net is None:
            lhb_score = 8
            lhb_reason = "无龙虎榜数据"
            details["has_institutional_data"] = False
        elif lhb_net > 0:
            lhb_score = 15
            lhb_reason = f"机构净买入{ lhb_net/1e8:.2f}亿"
            details["has_institutional_data"] = True
        else:
            lhb_score = 3
            lhb_reason = f"机构净卖出{abs(lhb_net)/1e8:.2f}亿"
            details["has_institutional_data"] = True
    except Exception:
        lhb_score = 8
        lhb_reason = "龙虎榜解析异常"
        details["has_institutional_data"] = False
    dimensions["institutional_flow"] = {"score": lhb_score, "weight": 0.15, "reason": lhb_reason}

    # 5. 板块资金流 10分
    try:
        sector_flow = _fetch_sector_flow(fund_code)
        if sector_flow is None:
            sf_score = 5
            sf_reason = "板块资金数据不可用"
        elif sector_flow > 0:
            sf_score = 10
            sf_reason = "板块资金净流入"
        elif sector_flow > -0.01:
            sf_score = 5
            sf_reason = "板块资金持平"
        else:
            sf_score = 2
            sf_reason = "板块资金净流出"
    except Exception:
        sf_score = 5
        sf_reason = "板块资金解析异常"
    dimensions["sector_flow"] = {"score": sf_score, "weight": 0.10, "reason": sf_reason}

    total = sum(d["score"] for d in dimensions.values())
    rating = _score_to_rating(total)
    advice = _build_capital_advice(dimensions)

    return {
        "fund_code": fund_code,
        "capital_score": round(total, 1),
        "rating": rating,
        "dimensions": dimensions,
        "detail": details,
        "advice": advice,
    }


def _fetch_etf_share_change(fund_code: str) -> float | None:
    """获取ETF近5日份额变化率。"""
    # 2026-07-13 修复：fund_etf_fund_daily_em() 不接受 symbol 参数（akshare 1.18.62）
    # 返回全部 ETF 日度数据，需在本地按 fund_code 过滤
    df = _call_akshare_with_timeout(ak.fund_etf_fund_daily_em)
    if df is None or len(df) == 0:
        return None
    try:
        # 按基金代码过滤
        code_col = None
        for col in df.columns:
            if "代码" in str(col) or "code" in str(col).lower():
                code_col = col
                break
        if code_col is not None:
            df = df[df[code_col].astype(str).str.contains(fund_code, na=False)]
        if len(df) == 0:
            return None
        # 取最近5日的份额数据
        recent = df.tail(5)
        share_col = None
        for col in df.columns:
            if "份额" in str(col) or "share" in str(col).lower():
                share_col = col
                break
        if share_col is None:
            return None
        start_share = _safe_float(recent.iloc[0][share_col])
        end_share = _safe_float(recent.iloc[-1][share_col])
        if start_share > 0:
            return (end_share - start_share) / start_share
        return None
    except Exception:
        return None


def _fetch_lhb_institutional(fund_code: str) -> float | None:
    """获取龙虎榜机构净买入额（元）。"""
    # 2026-07-13 修复：stock_lhb_detail_em 接受 start_date/end_date，不接受 symbol
    from datetime import datetime, timedelta
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
    df = _call_akshare_with_timeout(ak.stock_lhb_detail_em, start_date=start_date, end_date=end_date)
    if df is None or len(df) == 0:
        return None
    try:
        net = 0.0
        for _, row in df.iterrows():
            for col in df.columns:
                if "机构" in str(col) and "净" in str(col):
                    net += _safe_float(row[col])
        return net if net != 0 else None
    except Exception:
        return None


def _fetch_sector_flow(fund_code: str) -> float | None:
    """获取板块资金流（简化版,返回净流入率）。"""
    df = _call_akshare_with_timeout(ak.stock_sector_fund_flow_rank, indicator="今日", sector_type="行业资金流")
    if df is None or len(df) == 0:
        return None
    try:
        # 取主力净流入额的均值作为板块资金流向信号
        for col in df.columns:
            if "主力净流入" in str(col):
                vals = [_safe_float(v) for v in df[col]]
                avg = sum(vals) / len(vals) if vals else 0
                return avg / 1e8  # 转亿元
        return None
    except Exception:
        return None


def _build_capital_advice(dimensions: dict) -> str:
    """生成资金流向建议。"""
    total = sum(d["score"] for d in dimensions.values())
    if total >= 70:
        return "资金面偏积极,资金流入迹象明显"
    elif total >= 50:
        return "资金面中性,无明显方向"
    else:
        return "资金面偏弱,资金有流出迹象"


# ════════════════════════════════════════════════════════════
# P2-A: 情绪温度计
# ════════════════════════════════════════════════════════════

def calculate_sentiment(fund_code: str) -> dict:
    """情绪温度分析（满分100）。

    数据源：
    - market_data.get_market_overview() 涨跌家数
    - ak.stock_zh_a_spot_em 换手率
    - index_price_history 波动率
    """
    dimensions = {}

    # 1. 换手率分位 25分
    turnover_pct = _fetch_turnover_percentile()
    try:
        if turnover_pct is None:
            t_score = 12
            t_reason = "换手率数据缺失"
        elif turnover_pct < 0.2:
            t_score = 25
            t_reason = f"换手率处于{turnover_pct*100:.0f}%分位,交投清淡(恐慌)"
        elif turnover_pct < 0.5:
            t_score = 18
            t_reason = f"换手率处于{turnover_pct*100:.0f}%分位,偏低"
        elif turnover_pct < 0.8:
            t_score = 12
            t_reason = f"换手率处于{turnover_pct*100:.0f}%分位,中位"
        else:
            t_score = 5
            t_reason = f"换手率处于{turnover_pct*100:.0f}%分位,偏高(过热)"
    except Exception:
        t_score = 12
        t_reason = "换手率解析异常"
    dimensions["turnover_percentile"] = {"score": t_score, "weight": 0.25, "reason": t_reason}

    # 2. 涨跌家数比 20分
    ad_ratio = _fetch_advance_decline()
    try:
        if ad_ratio is None:
            ad_score = 10
            ad_reason = "涨跌家数数据缺失"
        else:
            up_ratio, limit_up, limit_down = ad_ratio
            if limit_down > 50:
                ad_score = 20
                ad_reason = f"跌停{limit_down}家,恐慌情绪"
            elif up_ratio < 0.3:
                ad_score = 15
                ad_reason = f"上涨家数占比{up_ratio*100:.0f}%,偏弱"
            elif up_ratio > 0.7 and limit_up > 50:
                ad_score = 3
                ad_reason = f"涨停{limit_up}家,过热"
            elif 0.4 <= up_ratio <= 0.6:
                ad_score = 10
                ad_reason = "涨跌均衡"
            else:
                ad_score = 8
                ad_reason = f"上涨家数占比{up_ratio*100:.0f}%"
    except Exception:
        ad_score = 10
        ad_reason = "涨跌家数解析异常"
    dimensions["advance_decline"] = {"score": ad_score, "weight": 0.20, "reason": ad_reason}

    # 3. 波动率分位 20分
    vol_pct = _fetch_volatility_percentile()
    try:
        if vol_pct is None:
            v_score = 10
            v_reason = "波动率数据缺失"
        elif vol_pct > 0.8:
            v_score = 20
            v_reason = f"波动率处于{vol_pct*100:.0f}%分位,高位(恐慌)"
        elif vol_pct > 0.5:
            v_score = 15
            v_reason = f"波动率处于{vol_pct*100:.0f}%分位,偏高"
        elif vol_pct > 0.2:
            v_score = 10
            v_reason = f"波动率处于{vol_pct*100:.0f}%分位,中位"
        else:
            v_score = 5
            v_reason = f"波动率处于{vol_pct*100:.0f}%分位,低位"
    except Exception:
        v_score = 10
        v_reason = "波动率解析异常"
    dimensions["volatility_percentile"] = {"score": v_score, "weight": 0.20, "reason": v_reason}

    # 4. 热度词频 15分
    news_sent = _fetch_news_sentiment()
    try:
        if news_sent is None:
            n_score = 8
            n_reason = "新闻情绪数据缺失"
        elif news_sent < -0.3:
            n_score = 15
            n_reason = "负面新闻上升"
        elif news_sent > 0.3:
            n_score = 3
            n_reason = "正面新闻上升"
        else:
            n_score = 8
            n_reason = "新闻情绪正常"
    except Exception:
        n_score = 8
        n_reason = "新闻情绪解析异常"
    dimensions["news_sentiment"] = {"score": n_score, "weight": 0.15, "reason": n_reason}

    # 5. 恐贪综合 20分
    fear_greed = calculate_fear_greed_index({
        "turnover_percentile": turnover_pct,
        "advance_decline": ad_ratio,
        "volatility_percentile": vol_pct,
        "news_sentiment": news_sent,
    })
    try:
        if fear_greed <= 20:
            fg_score = 20
            fg_reason = f"恐贪指数{fear_greed},极度恐惧"
        elif fear_greed <= 40:
            fg_score = 15
            fg_reason = f"恐贪指数{fear_greed},恐惧"
        elif fear_greed <= 60:
            fg_score = 10
            fg_reason = f"恐贪指数{fear_greed},中性"
        elif fear_greed <= 80:
            fg_score = 5
            fg_reason = f"恐贪指数{fear_greed},贪婪"
        else:
            fg_score = 0
            fg_reason = f"恐贪指数{fear_greed},极度贪婪"
    except Exception:
        fg_score = 10
        fg_reason = "恐贪指数解析异常"
    dimensions["fear_greed"] = {"score": fg_score, "weight": 0.20, "reason": fg_reason}

    total = sum(d["score"] for d in dimensions.values())
    rating = _score_to_rating(total)
    advice = _build_sentiment_advice(fear_greed)

    return {
        "fund_code": fund_code,
        "sentiment_score": round(total, 1),
        "rating": rating,
        "fear_greed_index": fear_greed,
        "fear_greed_label": _fear_greed_label(fear_greed),
        "dimensions": dimensions,
        "advice": advice,
    }


def calculate_fear_greed_index(market_data: dict) -> int:
    """恐贪指数 = 加权平均(换手率分位反转 + 涨跌比反转 + 波动率分位 + 新闻情绪)。

    返回 0-100，0=极度恐惧，100=极度贪婪。

    逻辑（逆向指标）：
    - 换手率越低 → 越恐惧 → 分数越低
    - 下跌家数越多 → 越恐惧 → 分数越低
    - 波动率越高 → 越恐惧 → 分数越低
    - 负面新闻越多 → 越恐惧 → 分数越低
    """
    weighted_sum = 0.0
    weight_used = 0.0

    # 换手率：低换手=恐惧(低分)。换手率分位直接映射（低分位→低分）
    tp = market_data.get("turnover_percentile")
    if tp is not None:
        weighted_sum += tp * 100 * 0.30
        weight_used += 0.30

    # 涨跌比：上涨多→贪婪(高分)
    ad = market_data.get("advance_decline")
    if ad and isinstance(ad, tuple) and len(ad) >= 1:
        up_ratio = ad[0]
        weighted_sum += up_ratio * 100 * 0.25
        weight_used += 0.25
    elif ad is not None and isinstance(ad, (int, float)):
        weighted_sum += float(ad) * 0.25
        weight_used += 0.25

    # 波动率分位反转：高波动→恐惧(低分)
    vp = market_data.get("volatility_percentile")
    if vp is not None:
        weighted_sum += (1 - vp) * 100 * 0.25
        weight_used += 0.25

    # 新闻情绪：正面→贪婪
    ns = market_data.get("news_sentiment")
    if ns is not None:
        # ns 范围 -1~1,映射到 0~100
        weighted_sum += (ns + 1) * 50 * 0.20
        weight_used += 0.20

    if weight_used <= 0:
        return 50  # 无数据返回中性

    # 按已用权重归一化到 0-100
    total = weighted_sum / weight_used
    total = max(0, min(100, int(round(total))))
    return total


def _fear_greed_label(score: int) -> str:
    """恐贪指数标签。"""
    if score <= 20:
        return "极度恐惧"
    elif score <= 40:
        return "恐惧"
    elif score <= 60:
        return "中性"
    elif score <= 80:
        return "贪婪"
    else:
        return "极度贪婪"


def _fetch_turnover_percentile() -> float | None:
    """获取市场换手率分位（0-1）。"""
    df = _call_akshare_with_timeout(ak.stock_zh_a_spot_em)
    if df is None or len(df) == 0:
        return None
    try:
        turnover_col = None
        for col in df.columns:
            if "换手" in str(col):
                turnover_col = col
                break
        if turnover_col is None:
            return None
        turnovers = [_safe_float(v) for v in df[turnover_col]]
        turnovers = [t for t in turnovers if t > 0]
        if not turnovers:
            return None
        avg_turnover = sum(turnovers) / len(turnovers)
        # 简化映射：<0.5%→0.1, 1%→0.3, 3%→0.6, 5%+→0.9
        if avg_turnover < 0.5:
            return 0.1
        elif avg_turnover < 1.0:
            return 0.3
        elif avg_turnover < 3.0:
            return 0.6
        else:
            return 0.9
    except Exception:
        return None


def _fetch_advance_decline() -> tuple | None:
    """获取涨跌家数（up_ratio, limit_up, limit_down）。"""
    try:
        from services.market_data import get_market_overview
        overview = get_market_overview()
        breadth = overview.get("breadth", {})
        if not breadth:
            return None
        up = _safe_float(breadth.get("up"))
        down = _safe_float(breadth.get("down"))
        limit_up = _safe_float(breadth.get("limit_up"))
        limit_down = _safe_float(breadth.get("limit_down"))
        total = up + down
        up_ratio = up / total if total > 0 else 0.5
        return (up_ratio, limit_up, limit_down)
    except Exception:
        return None


def _fetch_volatility_percentile() -> float | None:
    """获取沪深300波动率分位（0-1）。"""
    try:
        from services.index_history_fetcher import get_index_price_history
        hist = get_index_price_history("000300", days=365)
        if not hist or len(hist) < 30:
            return None
        closes = [_safe_float(r.get("close")) for r in hist if r.get("close")]
        if len(closes) < 30:
            return None
        # 计算近20日波动率
        recent = closes[-20:]
        rets = [(recent[i] / recent[i-1] - 1) for i in range(1, len(recent)) if recent[i-1] > 0]
        if not rets:
            return None
        vol_now = (sum(r ** 2 for r in rets) / len(rets)) ** 0.5
        # 计算过去1年滚动20日波动率序列
        vols = []
        for i in range(20, len(closes)):
            window = closes[i-20:i]
            r = [(window[j] / window[j-1] - 1) for j in range(1, len(window)) if window[j-1] > 0]
            if r:
                vols.append((sum(x ** 2 for x in r) / len(r)) ** 0.5)
        if not vols:
            return None
        vols.sort()
        rank = sum(1 for v in vols if v <= vol_now) / len(vols)
        return rank
    except Exception:
        return None


def _fetch_news_sentiment() -> float | None:
    """获取新闻情绪（-1~1,负面~正面）。简化版。"""
    try:
        from services.event_radar import _fetch_news_from_akshare
        news = _fetch_news_from_akshare(limit=20)
        if not news:
            return None
        # 简单关键词分析
        neg_words = ["跌", "暴跌", "崩盘", "危机", "风险", "警告", "下跌", "熊市"]
        pos_words = ["涨", "暴涨", "突破", "利好", "机会", "牛市", "上涨", "新高"]
        neg_count = 0
        pos_count = 0
        for n in news:
            title = n.get("news_title", "") + n.get("news_summary", "")
            if any(w in title for w in neg_words):
                neg_count += 1
            if any(w in title for w in pos_words):
                pos_count += 1
        total = neg_count + pos_count
        if total == 0:
            return 0.0
        return (pos_count - neg_count) / total
    except Exception:
        return None


def _build_sentiment_advice(fear_greed: int) -> str:
    """生成情绪温度建议。"""
    if fear_greed <= 20:
        return "市场极度恐惧,逆向思维可考虑分批布局"
    elif fear_greed <= 40:
        return "市场情绪偏恐惧,可考虑分批加仓低估品种"
    elif fear_greed <= 60:
        return "市场情绪中性,维持现有配置"
    elif fear_greed <= 80:
        return "市场情绪偏贪婪,注意控制仓位"
    else:
        return "市场极度贪婪,考虑分批止盈"


# ════════════════════════════════════════════════════════════
# P2-B: 持仓健康度总评
# ════════════════════════════════════════════════════════════

def _valuation_percentile_to_score(percentile: float) -> float:
    """估值分位转评分。PE分位 ≤20% → 90分, ≤40% → 75分, ≤60% → 60分, ≤80% → 40分, >80% → 20分。"""
    if percentile is None:
        return 50
    if percentile <= 20:
        return 90
    elif percentile <= 40:
        return 75
    elif percentile <= 60:
        return 60
    elif percentile <= 80:
        return 40
    else:
        return 20


def _get_fund_valuation_percentile(fund_code: str) -> float | None:
    """获取基金对应指数的估值分位。"""
    try:
        from db.portfolio import get_holding_by_fund
        from db.valuations import get_best_valuation
        index_code = None
        holding = get_holding_by_fund(fund_code)
        if holding:
            index_code = holding.get("index_code")
        if not index_code:
            # 从 fund_metadata.tracking_index 反查
            try:
                from services.fund_data_service import get_fund_metadata
                meta = get_fund_metadata(fund_code)
                if meta and meta.get("tracking_index"):
                    # tracking_index 是中文名,尝试匹配 index_info
                    from db.valuations import list_valuation_indexes
                    for idx in list_valuation_indexes():
                        if idx.get("index_name") and idx["index_name"] in meta["tracking_index"]:
                            index_code = idx.get("index_code")
                            break
            except Exception:
                pass
        if not index_code:
            return None
        val = get_best_valuation(index_code, enable_online=False)
        if val and val.get("percentile") is not None:
            return _safe_float(val["percentile"])
        return None
    except Exception as e:
        logger.warning(f"[health] 获取估值分位失败 {fund_code}: {e}")
        return None


def calculate_fund_health_report(fund_code: str, force_refresh: bool = False) -> dict:
    """七维体检报告。

    综合评分 = 质量评分 × 0.17 + 回撤评分 × 0.15 + 趋势评分 × 0.15
             + 资金评分 × 0.13 + 情绪评分 × 0.12 + 估值评分 × 0.15 + 基本面评分 × 0.13
    （债基/无股票持仓时用6维：质量0.20/回撤0.20/趋势0.15/资金0.15/情绪0.10/估值0.20）
    """
    # 缓存策略：24小时内不重复计算（除非 force_refresh）
    if not force_refresh:
        try:
            cached = get_fund_quality_score(fund_code)
            if cached:
                try:
                    computed = datetime.strptime(cached["computed_at"], "%Y-%m-%d %H:%M:%S")
                    if (datetime.now() - computed).total_seconds() < 24 * 3600:
                        # 返回缓存（含 detail）
                        return cached.get("detail") or _build_cached_report(cached)
                except Exception:
                    pass
        except Exception:
            pass

    # 计算前六维
    quality = calculate_quality_score(fund_code)
    drawdown = calculate_drawdown_analysis(fund_code)
    trend = calculate_trend_analysis(fund_code)
    capital = calculate_capital_flow(fund_code)
    sentiment = calculate_sentiment(fund_code)

    # 估值维度
    pe_percentile = _get_fund_valuation_percentile(fund_code)
    valuation_score = _valuation_percentile_to_score(pe_percentile)
    valuation_level = "low" if (pe_percentile or 50) <= 30 else ("high" if (pe_percentile or 50) > 70 else "mid")

    fund_name = quality.get("fund_name", fund_code)
    quality_score = quality["quality_score"]
    drawdown_score = drawdown["drawdown_score"]
    trend_score = trend["trend_score"]
    capital_score = capital["capital_score"]
    sentiment_score = sentiment["sentiment_score"]

    # 第7维：基本面（债基/无持仓时降级为6维）
    fundamental = calculate_fundamental_score(fund_code)
    fundamental_score = fundamental.get("fundamental_score")
    has_fundamental = fundamental_score is not None

    # 调仓动作
    holding_changes = analyze_holding_changes(fund_code)

    # 综合评分（加权，7维 vs 6维）
    if has_fundamental:
        total_score = (
            quality_score * 0.17
            + drawdown_score * 0.15
            + trend_score * 0.15
            + capital_score * 0.13
            + sentiment_score * 0.12
            + valuation_score * 0.15
            + fundamental_score * 0.13
        )
    else:
        # 债基/无持仓：6维
        total_score = (
            quality_score * 0.20
            + drawdown_score * 0.20
            + trend_score * 0.15
            + capital_score * 0.15
            + sentiment_score * 0.10
            + valuation_score * 0.20
        )
    total_score = round(total_score, 1)
    rating = _score_to_rating(total_score)

    # 决策矩阵（加入基本面因子）
    decision = _build_decision_matrix(
        quality_score=quality_score,
        trend_metrics=trend.get("detail", {}),
        drawdown_metrics=drawdown.get("detail", {}),
        valuation_level=valuation_level,
        sentiment_score=sentiment_score,
        fear_greed=sentiment.get("fear_greed_index", 50),
        fundamental_rating=fundamental.get("rating") if has_fundamental else None,
    )

    # 段永平视角
    duan_view = _build_duanyongping_view(quality_score, valuation_level, decision["trend_direction"], fundamental.get("rating") if has_fundamental else None)

    # 构建报告
    report = {
        "quality": {"score": round(quality_score, 1), "rating": quality["rating"], "label": "基金质量"},
        "drawdown": {"score": round(drawdown_score, 1), "rating": drawdown["rating"], "label": "回撤恢复"},
        "trend": {"score": round(trend_score, 1), "rating": trend["rating"], "label": "趋势均线"},
        "capital": {"score": round(capital_score, 1), "rating": capital["rating"], "label": "资金流向"},
        "sentiment": {"score": round(sentiment_score, 1), "rating": sentiment["rating"], "label": "情绪温度"},
        "valuation": {"score": round(valuation_score, 1), "rating": _score_to_rating(valuation_score), "label": "估值水位"},
    }
    if has_fundamental:
        report["fundamental"] = {"score": round(fundamental_score, 1), "rating": fundamental["rating"], "label": "基本面"}

    # 大师理念矩阵（6位大师多视角决策，需在report构建后调用）
    try:
        from services.master_perspectives import build_master_perspectives_matrix
        master_perspectives = build_master_perspectives_matrix(report, {
            "quality": quality,
            "drawdown": drawdown,
            "trend": trend,
            "capital": capital,
            "sentiment": sentiment,
            "valuation": {"pe_percentile": pe_percentile, "score": valuation_score},
            "fundamental": fundamental if has_fundamental else None,
            "holding_changes": holding_changes,
        })
    except Exception as e:
        logger.warning(f"[health] 大师理念矩阵构建失败 {fund_code}: {e}")
        master_perspectives = {"masters": [], "consensus": {}}

    # 大师决策落库（action≠hold才记录，用于T+N回测验证）
    try:
        from db.master_decision_history import save_master_decision
        baseline_price = _safe_float(current_price) if 'current_price' in dir() else None
        for m in master_perspectives.get("masters", []):
            if m.get("action") != "hold" and m.get("score") is not None:
                save_master_decision(
                    master_key=m["master_key"],
                    master_name=m["master_name"],
                    fund_code=fund_code,
                    fund_name=fund_name,
                    action=m["action"],
                    score=m.get("score"),
                    confidence=m.get("score", 50) / 100.0,
                    reason=m.get("reason", ""),
                    snapshot=m,
                    baseline_price=baseline_price,
                )
    except Exception as e:
        logger.debug(f"[health] 大师决策落库失败 {fund_code}: {e}")

    advice = _build_health_advice(decision, report)

    result = {
        "fund_code": fund_code,
        "fund_name": fund_name,
        "total_score": total_score,
        "rating": rating,
        "report": report,
        "decision_matrix": decision,
        "duan_yongping_view": duan_view,
        "master_perspectives": master_perspectives,
        "advice": advice,
        # 详细维度数据
        "details": {
            "quality": quality,
            "drawdown": drawdown,
            "trend": trend,
            "capital": capital,
            "sentiment": sentiment,
            "valuation": {"pe_percentile": pe_percentile, "score": valuation_score},
            "fundamental": fundamental if has_fundamental else None,
            "holding_changes": holding_changes,
        },
    }

    # 保存缓存
    try:
        save_fund_quality_score(
            fund_code, fund_name,
            quality_score=quality_score,
            drawdown_score=drawdown_score,
            trend_score=trend_score,
            capital_score=capital_score,
            sentiment_score=sentiment_score,
            total_score=total_score,
            rating=rating,
            detail=result,
            advice=advice,
        )
    except Exception as e:
        logger.warning(f"[health] 保存基金质量评分缓存失败 {fund_code}: {e}")

    return result


def _build_cached_report(cached: dict) -> dict:
    """从缓存记录重建报告。"""
    return {
        "fund_code": cached.get("fund_code"),
        "fund_name": cached.get("fund_name"),
        "total_score": cached.get("total_score", 0),
        "rating": cached.get("rating"),
        "report": {},
        "advice": cached.get("advice"),
        "cached": True,
    }


def _build_decision_matrix(
    quality_score: float,
    trend_metrics: dict,
    drawdown_metrics: dict,
    valuation_level: str,
    sentiment_score: float,
    fear_greed: int,
    fundamental_rating: str = None,
) -> dict:
    """段永平决策矩阵（含基本面因子）。

    - strong_buy: 质量≥good + 基本面≥good + 趋势上行 + 估值低 + 情绪偏恐
    - dca: 质量≥good + 基本面≥good + 估值低 + (趋势不明 OR 回撤高位)
    - hold: 质量≥good + 估值中 OR 趋势上行 + 估值中
    - reduce: 估值高 OR 质量差 OR 基本面差 OR 趋势下行+估值中
    - wait: 趋势下行 + 回撤未企稳 + 估值不低；基本面差即使估值低也判wait（价值陷阱）
    """
    quality_rating = _score_to_rating(quality_score)
    quality_good = quality_score >= 60  # good 及以上
    quality_poor = quality_score < 40

    # 基本面因子
    fundamental_poor = fundamental_rating == "poor"
    fundamental_excellent = fundamental_rating == "excellent"
    fundamental_good = fundamental_rating in ("good", "excellent")

    trend_direction = trend_metrics.get("arrangement", "tangled")
    trend_up = trend_direction in ("strong_bull", "weak_bull")
    trend_down = trend_direction in ("strong_bear", "weak_bear")
    trend_tangled = trend_direction == "tangled"

    drawdown_pct = drawdown_metrics.get("drawdown_percentile", 0)
    drawdown_high = drawdown_pct >= 0.5
    is_bottoming = drawdown_metrics.get("is_bottoming", False)
    is_new_high = drawdown_metrics.get("is_new_high", False)

    fear = fear_greed <= 40 or sentiment_score >= 60  # 情绪偏恐（恐贪低或情绪分高）

    # 决策优先级（基本面差优先拦截）
    # 注意：fundamental_rating 为 None 时（债基/6维模式），走旧4因子逻辑
    has_fundamental = fundamental_rating is not None
    if quality_poor:
        action = "reduce"
        reason = "基金质量较差,建议减仓或更换"
    elif has_fundamental and fundamental_poor and valuation_level == "low":
        # 价值陷阱预警：估值低但基本面差
        action = "wait"
        reason = "⚠️价值陷阱预警：估值低但重仓股基本面差,不建议抄底"
    elif has_fundamental and fundamental_poor:
        action = "reduce"
        reason = "重仓股基本面差,建议减仓规避风险"
    elif valuation_level == "high":
        action = "reduce"
        reason = "估值偏高,建议分批减仓"
    elif trend_down and not is_bottoming and valuation_level != "low":
        action = "wait"
        reason = "趋势下行+回撤未企稳+估值不低,建议等待拐点"
    elif quality_good and trend_up and valuation_level == "low" and fear and (not has_fundamental or fundamental_good):
        action = "strong_buy"
        reason = "质量良好" + ("+基本面良好" if has_fundamental else "") + "+趋势上行+估值低+情绪偏恐,强烈加仓"
    elif quality_good and valuation_level == "low" and (trend_tangled or drawdown_high) and (not has_fundamental or fundamental_good):
        action = "dca"
        reason = "质量良好" + ("+基本面良好" if has_fundamental else "") + "+估值偏低+" + ("趋势不明" if trend_tangled else "回撤高位") + ",适合定投"
    elif quality_good and (valuation_level == "mid" or trend_up):
        action = "hold"
        reason = "质量良好+" + ("估值中位" if valuation_level == "mid" else "趋势上行") + ",持有"
    elif valuation_level == "mid" and trend_down:
        action = "reduce"
        reason = "趋势下行+估值中位,建议减仓"
    else:
        action = "hold"
        reason = "维持现有仓位观察"

    action_labels = {
        "strong_buy": "强烈加仓",
        "dca": "定投加仓",
        "hold": "持有",
        "reduce": "减仓",
        "wait": "等待",
    }

    return {
        "quality_rating": quality_rating,
        "fundamental_rating": fundamental_rating,
        "trend_direction": trend_direction,
        "valuation_level": valuation_level,
        "action": action,
        "action_label": action_labels.get(action, action),
        "reason": reason,
    }


def _build_duanyongping_view(quality_score: float, valuation_level: str, trend_direction: str, fundamental_rating: str = None) -> str:
    """段永平视角文案（含基本面）。"""
    # 好生意（基金质量）
    if quality_score >= 70:
        business = "好生意"
    elif quality_score >= 50:
        business = "尚可的生意"
    else:
        business = "一般的生意"
    # 好公司（基本面，段永平"买股票就是买公司"）
    if fundamental_rating == "excellent":
        company = "好公司"
    elif fundamental_rating == "good":
        company = "质地良好的公司"
    elif fundamental_rating == "fair":
        company = "质地一般的公司"
    elif fundamental_rating == "poor":
        company = "质地差的公司"
    else:
        company = ""
    # 好价格
    if valuation_level == "low":
        price = "好价格(低估)"
    elif valuation_level == "high":
        price = "价格偏高(高估)"
    else:
        price = "价格合理(中估)"
    # 趋势
    if trend_direction in ("strong_bull", "weak_bull"):
        trend_text = "趋势向上"
    elif trend_direction in ("strong_bear", "weak_bear"):
        trend_text = "趋势待反转"
    else:
        trend_text = "趋势待确立"

    parts = [business]
    if company:
        parts.append(company)
    parts.append(price)
    parts.append(trend_text)
    return "+".join(parts)


def _build_health_advice(decision: dict, report: dict) -> str:
    """生成体检报告综合建议。"""
    action = decision["action"]
    reason = decision["reason"]
    weak_dims = [k for k, v in report.items() if v["score"] < 50]
    labels = {k: v["label"] for k, v in report.items()}
    weak_text = ""
    if weak_dims:
        weak_text = "。" + "、".join(labels[k] for k in weak_dims) + "偏弱需关注"
    return f"{reason}。{decision['action_label']}为主{weak_text}。"


# ════════════════════════════════════════════════════════════
# 批量计算
# ════════════════════════════════════════════════════════════

def batch_calculate_fund_health(fund_codes: list, force_refresh: bool = False) -> list:
    """批量计算基金体检报告。"""
    results = []
    for code in fund_codes:
        try:
            r = calculate_fund_health_report(code, force_refresh=force_refresh)
            results.append(r)
        except Exception as e:
            logger.error(f"[batch] 基金 {code} 分析失败: {e}")
            results.append({"fund_code": code, "error": str(e)})
    return results


def refresh_fund_quality_scores(fund_codes: list = None) -> dict:
    """刷新基金质量评分。

    Args:
        fund_codes: 指定基金代码列表,为空则刷新所有持仓+关注
    Returns:
        {"ok": True, "count": N}
    """
    if not fund_codes:
        # 刷新所有持仓+关注
        codes = set()
        try:
            from db.portfolio import list_holdings
            for h in list_holdings():
                if h.get("fund_code"):
                    codes.add(h["fund_code"])
        except Exception:
            pass
        try:
            from db.watchlist import list_watchlist
            for w in list_watchlist():
                if w.get("fund_code"):
                    codes.add(w["fund_code"])
        except Exception:
            pass
        fund_codes = list(codes)

    count = 0
    for code in fund_codes:
        try:
            calculate_fund_health_report(code, force_refresh=True)
            count += 1
        except Exception as e:
            logger.error(f"[refresh] 基金 {code} 刷新失败: {e}")
    return {"ok": True, "count": count}


# ════════════════════════════════════════════════════════════
# 第一阶段扩展：基本面深度维度（第7维）
# ════════════════════════════════════════════════════════════


def _fetch_stock_financials(stock_code: str) -> dict | None:
    """获取个股财务指标（带90天缓存）。

    Returns:
        {roe, gross_margin, net_margin, debt_ratio, rev_growth, profit_growth,
         roe_history, industry} 或 None
    """
    import json as _json
    from db.portfolio import get_analysis_cache, save_analysis_cache

    cache_key = f"stock_financial_{stock_code}"
    cached = get_analysis_cache(cache_key)
    if cached:
        # 检查90天TTL
        created = cached.get("_created_at", "")
        try:
            created_dt = datetime.strptime(created, "%Y-%m-%d %H:%M:%S")
            if (datetime.now() - created_dt).days < 90:
                return cached.get("data")
        except Exception:
            pass

    if not _HAS_AKSHARE:
        return None

    # 调 akshare 财务指标接口
    start_year = str(datetime.now().year - 1)
    df = _call_akshare_with_timeout(
        ak.stock_financial_analysis_indicator,
        timeout=20,
        symbol=stock_code,
        start_year=start_year,
    )
    if df is None or len(df) == 0:
        return None

    try:
        # 取最近4个季度的数据
        df = df.head(4).copy()
        # akshare 列名可能因版本不同有差异，做兼容处理
        def _get_col(row, *names):
            for n in names:
                if n in row and row[n] is not None:
                    return row[n]
            return 0

        latest = df.iloc[0]  # 最近一期
        roe = _safe_float(_get_col(latest, "加权净资产收益率(%)", "净资产收益率(%)", "ROE(%)"))
        gross_margin = _safe_float(_get_col(latest, "销售毛利率(%)", "毛利率(%)"))
        net_margin = _safe_float(_get_col(latest, "销售净利率(%)", "净利率(%)"))
        debt_ratio = _safe_float(_get_col(latest, "资产负债率(%)"))
        rev_growth = _safe_float(_get_col(latest, "主营业务收入增长率(%)", "营收同比增长(%)"))
        profit_growth = _safe_float(_get_col(latest, "净利润增长率(%)", "归母净利润同比增长(%)"))

        # ROE 历史序列（4季度）
        roe_history = []
        for _, r in df.iterrows():
            roe_history.append(_safe_float(_get_col(r, "加权净资产收益率(%)", "净资产收益率(%)", "ROE(%)")))

        # 行业（从 stock_individual_info_em 获取，失败不影响主流程）
        industry = ""
        try:
            info_df = _call_akshare_with_timeout(
                ak.stock_individual_info_em, timeout=10, symbol=stock_code
            )
            if info_df is not None and len(info_df) > 0:
                for _, r in info_df.iterrows():
                    if "行业" in str(r.get("item", "")):
                        industry = str(r.get("value", ""))
                        break
        except Exception:
            pass

        data = {
            "roe": roe,
            "gross_margin": gross_margin,
            "net_margin": net_margin,
            "debt_ratio": debt_ratio,
            "rev_growth": rev_growth,
            "profit_growth": profit_growth,
            "roe_history": roe_history,
            "industry": industry,
        }

        # 写缓存
        save_analysis_cache(cache_key, {"data": data, "_created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
        return data
    except Exception as e:
        logger.warning(f"[fundamental] 解析财务指标失败 {stock_code}: {e}")
        return None


def _safe_int(v, default: int = 0) -> int:
    """安全转int，处理NaN/None。"""
    try:
        f = float(v)
        if f != f:  # NaN check
            return default
        return int(f)
    except (ValueError, TypeError):
        return default


def _score_profitability(roe: float, gross_margin: float, net_margin: float) -> tuple[float, str]:
    """盈利能力评分（0-100）。"""
    # NaN 安全处理
    roe = _safe_float(roe) if roe == roe else 0  # NaN check
    gross_margin = _safe_float(gross_margin) if gross_margin == gross_margin else 0
    net_margin = _safe_float(net_margin) if net_margin == net_margin else 0

    # ROE 评分（40分满分）
    if roe >= 20:
        roe_s, roe_r = 40, f"ROE {_safe_int(roe)}%+，盈利能力极强"
    elif roe >= 15:
        roe_s, roe_r = 32, f"ROE {_safe_int(roe)}%，盈利能力强"
    elif roe >= 10:
        roe_s, roe_r = 24, f"ROE {_safe_int(roe)}%，盈利能力中等"
    elif roe >= 5:
        roe_s, roe_r = 16, f"ROE {_safe_int(roe)}%，盈利能力偏弱"
    else:
        roe_s, roe_r = 8, f"ROE {_safe_int(roe)}%，盈利能力弱"

    # 毛利率评分（30分满分）
    if gross_margin >= 50:
        gm_s = 30
    elif gross_margin >= 30:
        gm_s = 22
    elif gross_margin >= 20:
        gm_s = 16
    else:
        gm_s = 8

    # 净利率评分（30分满分）
    if net_margin >= 20:
        nm_s = 30
    elif net_margin >= 10:
        nm_s = 22
    elif net_margin >= 5:
        nm_s = 16
    else:
        nm_s = 8

    score = roe_s + gm_s + nm_s
    reason = f"{roe_r}，毛利率{_safe_int(gross_margin)}%，净利率{_safe_int(net_margin)}%"
    return score, reason


def _score_growth(rev_growth: float, profit_growth: float, history_growth: list = None) -> tuple[float, str]:
    """成长性评分（0-100）。"""
    # NaN 安全处理
    rev_growth = _safe_float(rev_growth) if rev_growth == rev_growth else 0
    profit_growth = _safe_float(profit_growth) if profit_growth == profit_growth else 0

    # 营收增速（50分）
    if rev_growth >= 30:
        rev_s = 50
    elif rev_growth >= 20:
        rev_s = 40
    elif rev_growth >= 10:
        rev_s = 30
    elif rev_growth >= 0:
        rev_s = 20
    else:
        rev_s = 8

    # 净利润增速（50分）
    if profit_growth >= 30:
        prof_s = 50
    elif profit_growth >= 20:
        prof_s = 40
    elif profit_growth >= 10:
        prof_s = 30
    elif profit_growth >= 0:
        prof_s = 20
    else:
        prof_s = 8

    score = rev_s + prof_s

    # 连续下滑扣分
    if history_growth and len(history_growth) >= 2:
        if all(g < 0 for g in history_growth[:2]):
            score -= 5

    score = max(0, min(100, score))
    reason = f"营收增速{_safe_int(rev_growth)}%，净利润增速{_safe_int(profit_growth)}%"
    if score < 30:
        reason += "，增速下滑需关注"
    return score, reason


def _score_solvency(debt_ratio: float, industry: str = "") -> tuple[float, str]:
    """偿债能力评分（0-100）。金融行业特殊处理。"""
    # NaN 安全处理
    debt_ratio = _safe_float(debt_ratio) if debt_ratio == debt_ratio else 0

    # 金融行业负债率高是常态，特殊处理
    is_financial = any(kw in industry for kw in ("银行", "保险", "证券", "金融"))

    if is_financial:
        # 金融行业用相对评分
        if debt_ratio >= 90:
            score = 70
        elif debt_ratio >= 80:
            score = 80
        else:
            score = 60
        reason = f"金融行业，资产负债率{_safe_int(debt_ratio)}%（行业特性）"
    else:
        if debt_ratio < 40:
            score = 90
        elif debt_ratio < 60:
            score = 70
        elif debt_ratio < 70:
            score = 50
        else:
            score = 30
        reason = f"资产负债率{_safe_int(debt_ratio)}%"

    return score, reason


def _score_stability(roe_history: list) -> tuple[float, str]:
    """稳定性评分（ROE 4季度标准差，0-100）。"""
    if not roe_history or len(roe_history) < 2:
        return 50, "历史数据不足，默认中分"

    try:
        mean_roe = sum(roe_history) / len(roe_history)
        variance = sum((x - mean_roe) ** 2 for x in roe_history) / len(roe_history)
        std = variance ** 0.5

        if std < 2:
            score = 90
        elif std < 5:
            score = 70
        elif std < 10:
            score = 50
        else:
            score = 30

        reason = f"ROE 4季度标准差{std:.1f}%"
        return score, reason
    except Exception:
        return 50, "稳定性计算异常，默认中分"


def _score_valuation_from_pe(stock_code: str) -> tuple[float, str]:
    """个股估值评分（基于当前PE绝对值，0-100）。

    简化方案：用当前PE绝对值评分，不做历史分位（个股历史PE数据获取复杂）。
    """
    if not _HAS_AKSHARE:
        return 50, "akshare不可用，默认中分"

    try:
        df = _call_akshare_with_timeout(
            ak.stock_zh_a_spot_em, timeout=10
        )
        if df is None or len(df) == 0:
            return 50, "行情数据获取失败，默认中分"

        row = df[df["代码"] == stock_code]
        if len(row) == 0:
            return 50, f"未找到{stock_code}行情，默认中分"

        pe = _safe_float(row.iloc[0].get("市盈率-动态", 0))

        # 亏损股PE为负或极大，直接低分
        if pe <= 0 or pe > 200:
            return 20, f"PE={pe}，亏损或异常，估值评分低"

        if pe < 15:
            score = 90
        elif pe < 25:
            score = 75
        elif pe < 40:
            score = 60
        elif pe < 60:
            score = 40
        else:
            score = 20

        reason = f"PE={int(pe)}"
        return score, reason
    except Exception as e:
        logger.warning(f"[fundamental] 个股估值评分失败 {stock_code}: {e}")
        return 50, "估值评分异常，默认中分"


def _score_stock_fundamentals(stock_code: str) -> dict:
    """个股5维基本面评分。"""
    fin = _fetch_stock_financials(stock_code)
    if not fin:
        return {
            "stock_code": stock_code,
            "stock_name": "",
            "profitability": {"score": 50, "reason": "财务数据缺失"},
            "growth": {"score": 50, "reason": "财务数据缺失"},
            "solvency": {"score": 50, "reason": "财务数据缺失"},
            "stability": {"score": 50, "reason": "财务数据缺失"},
            "valuation": {"score": 50, "reason": "财务数据缺失"},
            "total": 50.0,
            "rating": "fair",
        }

    profitability, prof_reason = _score_profitability(
        fin["roe"], fin["gross_margin"], fin["net_margin"]
    )
    growth, growth_reason = _score_growth(
        fin["rev_growth"], fin["profit_growth"], fin.get("roe_history")
    )
    solvency, solvency_reason = _score_solvency(fin["debt_ratio"], fin.get("industry", ""))
    stability, stability_reason = _score_stability(fin.get("roe_history", []))
    valuation, val_reason = _score_valuation_from_pe(stock_code)

    total = (
        profitability * 0.30 + growth * 0.25 + solvency * 0.15
        + stability * 0.15 + valuation * 0.15
    )

    return {
        "stock_code": stock_code,
        "profitability": {"score": profitability, "reason": prof_reason},
        "growth": {"score": growth, "reason": growth_reason},
        "solvency": {"score": solvency, "reason": solvency_reason},
        "stability": {"score": stability, "reason": stability_reason},
        "valuation": {"score": valuation, "reason": val_reason},
        "total": round(total, 1),
        "rating": _score_to_rating(total),
    }


def calculate_fundamental_score(fund_code: str) -> dict:
    """基金基本面评分 = Σ(个股5维分 × 持仓占比)。

    数据降级：
    - 无持仓数据 → 默认50分
    - 单股评分失败 → 该股50分，不影响其他
    """
    try:
        from db.portfolio import get_fund_holdings
        holdings = get_fund_holdings(fund_code)
    except Exception as e:
        logger.warning(f"[fundamental] 获取持仓失败 {fund_code}: {e}")
        return _default_fundamental(fund_code, "持仓数据获取失败")

    top_stocks = holdings.get("top_stocks", [])
    if not top_stocks:
        return _default_fundamental(fund_code, "无股票持仓")

    # 判断是否债基（无股票持仓或基金类别为债）
    fund_category = ""
    try:
        from services.fund_data_service import get_or_refresh_fund_metadata
        meta = get_or_refresh_fund_metadata(fund_code)
        if meta:
            fund_category = meta.get("fund_category", "") or ""
    except Exception:
        pass

    if fund_category in ("bond", "纯债", "混合债") and not top_stocks:
        return {
            "fund_code": fund_code,
            "fundamental_score": None,
            "rating": None,
            "stock_scores": [],
            "top10_coverage": 0,
            "advice": "债基无股票持仓，跳过基本面维度",
        }

    # 并行评分Top10重仓股
    stock_scores = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(_score_stock_fundamentals, s["stock_code"]): s
            for s in top_stocks
            if s.get("stock_code")
        }
        for future in concurrent.futures.as_completed(futures):
            stock = futures[future]
            try:
                score = future.result()
                score["pct_nav"] = stock.get("pct_nav", 0)
                score["stock_name"] = stock.get("stock_name", "")
                stock_scores.append(score)
            except Exception as e:
                logger.warning(f"[fundamental] 个股评分失败 {stock.get('stock_code')}: {e}")
                stock_scores.append({
                    "stock_code": stock.get("stock_code", ""),
                    "stock_name": stock.get("stock_name", ""),
                    "pct_nav": stock.get("pct_nav", 0),
                    "profitability": {"score": 50, "reason": "评分失败"},
                    "growth": {"score": 50, "reason": "评分失败"},
                    "solvency": {"score": 50, "reason": "评分失败"},
                    "stability": {"score": 50, "reason": "评分失败"},
                    "valuation": {"score": 50, "reason": "评分失败"},
                    "total": 50.0,
                    "rating": "fair",
                })

    # 按持仓占比加权（归一化到Top10总占比）
    total_weight = sum(s["pct_nav"] for s in stock_scores if s["pct_nav"] > 0)
    if total_weight == 0:
        return _default_fundamental(fund_code, "持仓占比缺失")

    weighted_score = sum(
        s["total"] * (s["pct_nav"] / total_weight)
        for s in stock_scores
    )

    # 按持仓占比排序展示
    stock_scores.sort(key=lambda x: x.get("pct_nav", 0), reverse=True)

    return {
        "fund_code": fund_code,
        "fundamental_score": round(weighted_score, 1),
        "rating": _score_to_rating(weighted_score),
        "stock_scores": stock_scores,
        "top10_coverage": round(total_weight, 1),
        "advice": _fundamental_advice(weighted_score, stock_scores, total_weight),
    }


def _default_fundamental(fund_code: str, reason: str = "") -> dict:
    """基本面评分降级默认值。"""
    return {
        "fund_code": fund_code,
        "fundamental_score": 50.0,
        "rating": "fair",
        "stock_scores": [],
        "top10_coverage": 0,
        "advice": f"基本面数据不可用（{reason}），默认中分",
    }


def _fundamental_advice(score: float, stock_scores: list, coverage: float) -> str:
    """生成基本面维度的建议文案。"""
    if score >= 70:
        base = f"重仓股基本面良好（{score}分），盈利能力强"
    elif score >= 50:
        base = f"重仓股基本面一般（{score}分），部分个股需关注"
    else:
        base = f"重仓股基本面偏弱（{score}分），建议警惕价值陷阱"

    # 集中度提示
    if coverage >= 70:
        base += f"。Top10集中度{coverage}%偏高，个股风险需关注"
    elif coverage >= 50:
        base += f"。Top10集中度{coverage}%适中"

    # 弱势个股提示
    weak_stocks = [s for s in stock_scores if s["total"] < 40]
    if weak_stocks:
        names = "、".join(s.get("stock_name", s["stock_code"]) for s in weak_stocks[:3])
        base += f"。弱势股：{names}"

    return base


def analyze_holding_changes(fund_code: str) -> dict:
    """对比基金本季度 vs 上季度持仓，输出调仓动作。

    Returns:
        {has_history, current_quarter, prev_quarter, changes: [...], summary}
    """
    try:
        from db.fund_holdings_snapshot import compare_fund_holdings
        return compare_fund_holdings(fund_code)
    except Exception as e:
        logger.warning(f"[fundamental] 调仓分析失败 {fund_code}: {e}")
        return {
            "has_history": False,
            "current_quarter": None,
            "prev_quarter": None,
            "changes": [],
            "summary": f"调仓分析失败: {e}",
        }

