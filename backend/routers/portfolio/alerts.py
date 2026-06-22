"""风险预警：估值预警、回撤预警、集中度预警、加减仓预警"""
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from db import (
    create_alert, list_alerts, get_unread_alert_count, mark_alert_read, delete_alert,
    list_holdings, get_latest_valuation, get_fund_nav_history,
)
try:
    from db import get_config_int
except ImportError:
    get_config_int = None
from db.decisions import create_decision
from models.portfolio import CreateAlertRequest

logger = logging.getLogger(__name__)

router = APIRouter(tags=["portfolio-alerts"])


# ── 风险预警 API ──────────────────────────────────────────

@router.get("/api/portfolio/alerts")
async def list_alerts_api(unread_only: bool = False, limit: int = 50):
    """获取预警列表。"""
    return {"alerts": list_alerts(limit=limit, unread_only=unread_only)}


@router.get("/api/portfolio/alerts/unread-count")
async def unread_alert_count_api():
    """获取未读预警数量。"""
    return {"count": get_unread_alert_count()}


@router.put("/api/portfolio/alerts/{alert_id}/read")
async def mark_alert_read_api(alert_id: int):
    """标记预警为已读。"""
    if not mark_alert_read(alert_id):
        raise HTTPException(404, "预警不存在")
    return {"ok": True}


@router.delete("/api/portfolio/alerts/{alert_id}")
async def delete_alert_api(alert_id: int):
    """删除预警。"""
    if not delete_alert(alert_id):
        raise HTTPException(404, "预警不存在")
    return {"ok": True}


@router.post("/api/portfolio/alerts/generate")
async def generate_alert_api(req: CreateAlertRequest):
    """AI 主动生成预警。"""
    alert_id = create_alert(
        alert_type=req.alert_type,
        title=req.title,
        content=req.content,
        severity=req.severity,
        related_fund_code=req.related_fund_code,
        related_fund_name=req.related_fund_name,
        source=req.source or "system",
    )
    return {"ok": True, "alert_id": alert_id}



# ── 加减仓预警 Agent（方案B：主动调用） ──────────────────────────

class PositionAlertRequest(BaseModel):
    """加减仓预警分析请求。"""
    fund_code: str
    fund_name: str = ""
    action: str = "add"       # add=加仓, reduce=减仓, buy=建仓, sell=清仓
    amount: float = 0
    shares: float = 0


@router.post("/api/portfolio/position-alert")
async def position_alert_api(req: PositionAlertRequest):
    """加减仓预警分析 — 主动调用，返回四维度分析和预警信号。"""
    from agent.position_alert_agent import position_alert_agent

    result = position_alert_agent.analyze_position_change(
        user_id="default",
        fund_code=req.fund_code,
        fund_name=req.fund_name or req.fund_code,
        action=req.action,
        amount=req.amount,
        shares=req.shares,
    )
    return result


@router.post("/api/portfolio/alerts/scan")
async def scan_portfolio_alerts():
    """持仓风险巡检 — 主动扫描持仓数据生成预警。"""
    from datetime import datetime, timedelta
    from db import get_config_int

    holdings = list_holdings()
    if not holdings:
        return {"ok": True, "generated": 0, "message": "暂无持仓"}

    # 读取可配置阈值
    val_high = get_config_int('alert.valuation_high', 80)       # 高估百分位
    val_low = get_config_int('alert.valuation_low', 20)          # 低估百分位
    drawdown_threshold = get_config_int('alert.drawdown_pct', 10)  # 回撤预警(%)
    concentration_threshold = get_config_int('alert.concentration_pct', 30)  # 集中度(%)
    cash_high_pct = get_config_int('alert.cash_high_pct', 15)    # 现金闲置(%)
    stale_days = get_config_int('alert.stale_days', 5)           # 数据过期(天)
    buy_drop_threshold = get_config_int('alert.buy_drop_pct', 4)  # 补仓后跌幅(%)

    generated = 0
    today = datetime.now().strftime("%Y-%m-%d")
    today_prefix = datetime.now().strftime("%Y-%m-%d")

    # ── 去重：同一天同一类型+同一基金不重复生成 ──
    existing = list_alerts(limit=200)
    existing_keys = set()
    for a in existing:
        if a.get("created_at", "").startswith(today_prefix):
            existing_keys.add(f"{a.get('alert_type')}:{a.get('related_fund_code', '')}")

    def should_create(alert_type, fund_code=""):
        key = f"{alert_type}:{fund_code}"
        if key in existing_keys:
            return False
        existing_keys.add(key)
        return True

    # ── 1. 估值预警 ──
    try:
        for h in holdings:
            code = h.get("fund_code", "")
            name = h.get("fund_name", code)
            if not code:
                continue
            val = get_latest_valuation(code)
            if not val:
                continue
            pct = val.get("percentile")
            metric = val.get("metric_type", "PE")
            if pct is None:
                continue
            if pct >= val_high and should_create("valuation_alert", code):
                create_alert(
                    alert_type="valuation_alert",
                    title=f"{name} 估值偏高（{metric}百分位 {pct}%）",
                    content=f"{name}（{code}）当前{metric}百分位为 {pct}%，已进入高估区间（>{val_high}%）。建议关注是否需要减仓或止盈。",
                    severity="warning",
                    related_fund_code=code,
                    related_fund_name=name,
                    source="system_scan",
                )
                generated += 1
            elif pct <= val_low and should_create("valuation_opportunity", code):
                create_alert(
                    alert_type="valuation_opportunity",
                    title=f"{name} 估值偏低（{metric}百分位 {pct}%）",
                    content=f"{name}（{code}）当前{metric}百分位为 {pct}%，处于低估区间（<{val_low}%）。可考虑逢低加仓。",
                    severity="info",
                    related_fund_code=code,
                    related_fund_name=name,
                    source="system_scan",
                )
                generated += 1
    except Exception as e:
        logger.warning(f"[alert_scan] 估值预警异常: {e}")

    # ── 2. 回撤预警 ──
    try:
        for h in holdings:
            code = h.get("fund_code", "")
            name = h.get("fund_name", code)
            if not code:
                continue
            nav_data = get_fund_nav_history(code, days=60)
            if not nav_data or len(nav_data) < 10:
                continue
            navs = [d.get("nav", 0) for d in nav_data if d.get("nav")]
            if len(navs) < 10:
                continue
            peak = max(navs[-30:]) if len(navs) >= 30 else max(navs)
            current = navs[-1]
            if peak <= 0:
                continue
            drawdown_pct = (peak - current) / peak * 100
            if drawdown_pct >= drawdown_threshold and should_create("drawdown_alert", code):
                create_alert(
                    alert_type="drawdown_alert",
                    title=f"{name} 近期回撤 {drawdown_pct:.1f}%",
                    content=f"{name}（{code}）从近期高点 {peak:.4f} 回撤至 {current:.4f}，跌幅 {drawdown_pct:.1f}%。请评估是否需要止损或加仓。",
                    severity="danger" if drawdown_pct >= drawdown_threshold * 1.5 else "warning",
                    related_fund_code=code,
                    related_fund_name=name,
                    source="system_scan",
                )
                generated += 1
    except Exception as e:
        logger.warning(f"[alert_scan] 回撤预警异常: {e}")

    # ── 3. 集中度预警 ──
    try:
        total_value = sum(h.get("current_value", 0) or 0 for h in holdings)
        if total_value > 0:
            for h in holdings:
                code = h.get("fund_code", "")
                name = h.get("fund_name", code)
                value = h.get("current_value", 0) or 0
                pct = value / total_value * 100
                if pct >= concentration_threshold and should_create("concentration_alert", code):
                    create_alert(
                        alert_type="concentration_alert",
                        title=f"{name} 占比过高（{pct:.1f}%）",
                        content=f"{name}（{code}）占组合总市值 {pct:.1f}%，超过集中度阈值 {concentration_threshold}%。建议适当分散配置。",
                        severity="warning",
                        related_fund_code=code,
                        related_fund_name=name,
                        source="system_scan",
                    )
                    generated += 1
    except Exception as e:
        logger.warning(f"[alert_scan] 集中度预警异常: {e}")

    # ── 4. 现金闲置预警 ──
    try:
        cash_balance = get_cash_balance() or 0
        total_assets = total_value + cash_balance
        if total_assets > 0:
            cash_pct = cash_balance / total_assets * 100
            if cash_pct >= cash_high_pct and should_create("cash_idle"):
                create_alert(
                    alert_type="cash_idle",
                    title=f"现金占比偏高（{cash_pct:.1f}%）",
                    content=f"当前现金余额 ¥{cash_balance:,.0f}，占总资产 {cash_pct:.1f}%，超过 {cash_high_pct}% 阈值。资金闲置会拖低整体收益，建议逐步配置。",
                    severity="info",
                    source="system_scan",
                )
                generated += 1
    except Exception as e:
        logger.warning(f"[alert_scan] 现金预警异常: {e}")

    # ── 5. 数据过期预警 ──
    try:
        stale_funds = []
        cutoff = (datetime.now() - timedelta(days=stale_days)).strftime("%Y-%m-%d")
        for h in holdings:
            updated = h.get("price_updated_at", "") or ""
            if updated < cutoff and h.get("shares", 0) > 0:
                stale_funds.append(h)
        if stale_funds and should_create("stale_data"):
            names = "、".join(h.get("fund_name", h.get("fund_code", "")) for h in stale_funds[:5])
            create_alert(
                alert_type="stale_data",
                title=f"{len(stale_funds)} 只基金数据超过 {stale_days} 天未更新",
                content=f"以下基金净值数据过期：{names}。建议刷新行情数据。",
                severity="info",
                source="system_scan",
            )
            generated += 1
    except Exception as e:
        logger.warning(f"[alert_scan] 数据过期预警异常: {e}")

    # ── 6. 补仓后跌幅预警 ──
    try:
        for h in holdings:
            code = h.get("fund_code", "")
            name = h.get("fund_name", code)
            if not code:
                continue
            last_buy_price = h.get("last_buy_price")
            current_price = h.get("current_price")
            if not last_buy_price or last_buy_price <= 0 or not current_price:
                continue
            drop_pct = (last_buy_price - current_price) / last_buy_price * 100
            if drop_pct >= buy_drop_threshold:
                last_buy_date = h.get("last_buy_date", "")
                if should_create("buy_drop_alert", code):
                    create_alert(
                        alert_type="buy_drop_alert",
                        title=f"{name} 补仓后下跌 {drop_pct:.1f}%",
                        content=f"{name}（{code}）最近一次买入价 {last_buy_price:.4f}（{last_buy_date}），当前净值 {current_price:.4f}，已下跌 {drop_pct:.1f}%（阈值 {buy_drop_threshold}%）。请评估是否继续持有或止损。",
                        severity="danger" if drop_pct >= buy_drop_threshold * 1.5 else "warning",
                        related_fund_code=code,
                        related_fund_name=name,
                        source="system_scan",
                    )
                    generated += 1
    except Exception as e:
        logger.warning(f"[alert_scan] 补仓后跌幅预警异常: {e}")

    return {"ok": True, "generated": generated}


