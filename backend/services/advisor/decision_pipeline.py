"""投资决策流水线编排 — decision_pipeline。

三模块联动优化 P0-A5（2026-08-01）：见 doc/plans/2026-08-01-三模块联动优化.md

定位：三模块联动的"司令部"。把各自独立的规则引擎串成一条完整决策链：
    ① 发现层（机会雷达：估值科学信号 + 机会卡）
    ② 执行层（智能补仓：金字塔/凯利 sizing + 相关性降权）
    ③ 风控层（组合风险：相关性/β/边际风险贡献）
    ④ 融合层（置信度 + verdict 决策）
    ⑤ 闭环层（止盈计划 + 写入统一决策账本，供回测反哺）
输出结构化「决策卡片」：结论 + 置信度 + 建议金额 + 风控 + 止盈计划 +
数据来源/更新时间 + 风险提示，既供前端展示，也供多智能体对话引用。

设计原则：
- 规则引擎负责"算"，本流水线负责"编排与综合判断"
- 置信度/最低阈值走 system_config（decision_pipeline.*，金融严谨性）
- 每层优雅降级：某层数据缺失不阻断整体，标注 data_quality
- 决策卡片必带数据来源与风险提示（合规）
"""
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# z-score 水平 → 置信度调整
_ZSCORE_CONFIDENCE_ADJ = {"deep_low": 0.15, "low": 0.08, "neutral": 0.0, "high": -0.15, "unknown": 0.0}
_RISK_PENALTY_PER_FLAG = 0.05
_RISK_PENALTY_CAP = 0.20


# ════════════════════════════════════════════════════════════════
# 纯函数层（可单测）
# ════════════════════════════════════════════════════════════════

def compute_confidence(opportunity_score: float | None = None,
                       zscore_level: str | None = None,
                       risk_flags: list | None = None,
                       min_confidence: float = 0.5,
                       regime: str | None = None,
                       ic_confidence: float | None = None) -> float:
    """融合多源信号计算决策置信度（0-1）。

    - 基础：机会雷达评分/100（无则 0.5 中性）
    - 估值修正：z-score 深度低估 +0.15、低估 +0.08、高估 -0.15
    - 风险惩罚：每条风险标记 -0.05（上限 -0.20）
    - P1-R5：regime 联动 — bear regime 下置信度打折（防御性降置信）
    - P1-R4：IC 融合 — final_confidence = base × (1-ic_weight) + ic_confidence × ic_weight
    - 收敛到 [0.05, 0.95]，避免过度自信
    """
    base = (opportunity_score / 100.0) if opportunity_score is not None else 0.5
    adj = _ZSCORE_CONFIDENCE_ADJ.get(zscore_level or "unknown", 0.0)
    penalty = min(len(risk_flags or []) * _RISK_PENALTY_PER_FLAG, _RISK_PENALTY_CAP)
    conf = base + adj - penalty

    # P1-R5：bear regime 下置信度折扣（防御性降置信，避免熊市过度乐观）
    if regime == "bear":
        try:
            from db.config import get_config_float
            discount = get_config_float("opportunity.regime.bear_confidence_discount", 0.9)
        except Exception:
            discount = 0.9
        conf = conf * discount

    # P1-R4：IC 加权置信度融合（ic_confidence 为 None 时不融合，保持原行为）
    if ic_confidence is not None:
        try:
            from db.config import get_config_float
            ic_weight = get_config_float("opportunity.confidence.ic_weight", 0.4)
        except Exception:
            ic_weight = 0.4
        conf = conf * (1.0 - ic_weight) + float(ic_confidence) * ic_weight

    return round(max(0.05, min(0.95, conf)), 3)


def decide_verdict(zscore_level: str | None, confidence: float, min_confidence: float) -> str:
    """综合估值水平与置信度给出 verdict。

    - 高估（high）→ avoid（不追高）
    - 置信度达标且低估/深低估 → can_buy
    - 其余 → watch（观察）
    """
    if zscore_level == "high":
        return "avoid"
    if confidence >= min_confidence and zscore_level in ("deep_low", "low"):
        return "can_buy"
    return "watch"


def confidence_label(confidence: float) -> str:
    if confidence >= 0.7:
        return "高"
    if confidence >= 0.5:
        return "中"
    return "低"


def build_decision_card(fund_code: str, fund_name: str, verdict: str, confidence: float,
                        suggested_amount: float, valuation_signal: dict | None,
                        valuation_percentile: float | None, risk_check: dict | None,
                        exit_plan: dict | None, evidence: list | None,
                        layers: dict | None, theme: str | None = None,
                        decision_id: int | None = None) -> dict:
    """组装结构化决策卡片（含数据来源与风险提示，合规）。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    return {
        "decision_id": decision_id,
        "fund_code": fund_code,
        "fund_name": fund_name,
        "theme": theme,
        "verdict": verdict,
        "confidence": confidence,
        "confidence_label": confidence_label(confidence),
        "suggested_amount": round(suggested_amount or 0, 2),
        "valuation_percentile": valuation_percentile,
        "valuation_signal": valuation_signal,
        "risk_check": risk_check or {},
        "exit_plan": exit_plan or {},
        "evidence": evidence or [],
        "layers": layers or {},
        "data_sources": [
            {"name": "指数估值", "type": "valuation", "update_time": now},
            {"name": "持仓行情", "type": "portfolio", "update_time": now},
            {"name": "组合风险模型", "type": "risk_model", "update_time": now},
        ],
        "risk_disclaimer": (
            "本决策卡片由规则引擎与量化模型生成，仅为投研辅助参考，不构成投资建议。"
            "模型结果依赖历史数据估计，存在误差；市场有风险，投资需谨慎，请结合自身风险承受能力独立决策。"
        ),
        "generated_at": now,
    }


# ════════════════════════════════════════════════════════════════
# 编排层（串联各模块，优雅降级）
# ════════════════════════════════════════════════════════════════

def _discovery_layer(fund_code: str, index_code: str | None) -> dict:
    """① 发现层：估值科学信号 + 估值百分位。"""
    layer = {"valuation_signal": None, "valuation_percentile": None, "data_quality": "unavailable"}
    if not index_code:
        return layer
    try:
        from services.advisor.opportunity_engine import _calc_valuation_zscore
        layer["valuation_signal"] = _calc_valuation_zscore(index_code)
    except Exception as e:
        logger.debug(f"[pipeline] 估值 z-score 失败 {index_code}: {e}")
    try:
        from db.valuations import get_latest_valuation
        val = get_latest_valuation(index_code)
        if val:
            layer["valuation_percentile"] = val.get("percentile")
    except Exception as e:
        logger.debug(f"[pipeline] 估值百分位失败 {index_code}: {e}")
    if layer["valuation_signal"] or layer["valuation_percentile"] is not None:
        layer["data_quality"] = "ok"
    return layer


def _sizing_layer(fund_code: str, user_id: str, holding: dict | None, total_assets: float) -> dict:
    """② 执行层：智能补仓 sizing（含相关性降权），失败时凯利兜底。"""
    layer = {"suggested_amount": 0.0, "source": "none", "correlation_risk": None}
    # 优先取智能补仓计划中该标的的金额（已含 S1 相关性降权）
    try:
        from services.advisor.smart_add_planner import generate_smart_add_plan
        plan_result = generate_smart_add_plan(user_id)
        for p in plan_result.get("plans", []):
            if p.get("fund_code") == fund_code:
                layer["suggested_amount"] = p.get("final_suggested_amount", 0) or 0
                layer["source"] = "smart_add"
                layer["correlation_risk"] = p.get("correlation_risk")
                layer["pre_correlation_amount"] = p.get("pre_correlation_amount")
                return layer
    except Exception as e:
        logger.debug(f"[pipeline] 智能补仓 sizing 失败 {fund_code}: {e}")
    # 兜底：分数凯利 × 总资产
    try:
        corr_risk = (holding or {}).get("_corr_risk")
        if corr_risk and corr_risk.get("kelly"):
            kelly = corr_risk["kelly"]
            layer["suggested_amount"] = round(total_assets * kelly.get("capped", 0), 2)
            layer["source"] = "kelly_fallback"
            layer["correlation_risk"] = corr_risk
    except Exception as e:
        logger.debug(f"[pipeline] 凯利兜底失败 {fund_code}: {e}")
    return layer


def _risk_layer(index_code: str | None, holdings: list[dict], fund_code: str,
                total_assets: float, holding_value: float) -> dict:
    """③ 风控层：组合相关性/β/边际风险贡献。"""
    if not index_code:
        return {"enabled": False, "corr_downweight": 1.0, "flags": ["无指数代码，跳过组合风控"]}
    try:
        from services.advisor.portfolio_risk import assess_add_position_risk
        other_codes = [h.get("index_code") for h in holdings
                       if h.get("index_code") and h.get("fund_code") != fund_code]
        weights = {}
        for h in holdings:
            if h.get("index_code") and h.get("fund_code") != fund_code:
                weights[h["index_code"]] = (h.get("current_value") or 0) / total_assets if total_assets else 0
        return assess_add_position_risk(
            target_index_code=index_code,
            holding_index_codes=other_codes,
            target_weight=holding_value / total_assets if total_assets else 0,
            holding_weights=weights,
        )
    except Exception as e:
        logger.debug(f"[pipeline] 组合风控失败 {fund_code}: {e}")
        return {"enabled": False, "corr_downweight": 1.0, "flags": [f"风控评估异常: {e}"]}


def run_investment_decision(fund_code: str, user_id: str = "default") -> dict:
    """对单标的运行完整投资决策流水线，返回决策卡片并写入决策账本。

    Args:
        fund_code: 目标基金代码（须存在于持仓，以获取指数/成本/现价）
        user_id: 用户

    Returns:
        决策卡片 dict（含 decision_id、verdict、confidence、建议金额、风控、止盈计划）
    """
    try:
        from db.config import get_config_bool, get_config_float, get_config_int
        if not get_config_bool("decision_pipeline.enabled", True):
            return {"error": "decision_pipeline disabled", "enabled": False}
        min_confidence = get_config_float("decision_pipeline.min_confidence", 0.5)
        require_risk = get_config_bool("decision_pipeline.require_risk_check", True)
        review_days = get_config_int("decision_ledger.review_days", 15)
    except Exception:
        min_confidence, require_risk, review_days = 0.5, True, 15

    # 持仓上下文
    holding = None
    try:
        from db.portfolio import get_holding_by_fund, list_holdings, get_portfolio_summary
        holding = get_holding_by_fund(fund_code, user_id)
        holdings = list_holdings(user_id)
        total_assets = (get_portfolio_summary(user_id).get("total_assets") or 0)
    except Exception as e:
        logger.warning(f"[pipeline] 持仓上下文加载失败: {e}")
        return {"error": f"持仓上下文加载失败: {e}", "fund_code": fund_code}
    if not holding:
        return {"error": f"未找到持仓 {fund_code}", "fund_code": fund_code}

    index_code = holding.get("index_code")
    fund_name = holding.get("fund_name") or fund_code
    cost_price = holding.get("cost_price") or 0
    holding_value = holding.get("current_value") or 0

    # ① 发现层
    discovery = _discovery_layer(fund_code, index_code)
    valuation_signal = discovery.get("valuation_signal")
    zscore_level = (valuation_signal or {}).get("level")
    valuation_percentile = discovery.get("valuation_percentile")

    # ③ 风控层（先算，sizing 可复用）
    risk_check = _risk_layer(index_code, holdings, fund_code, total_assets, holding_value)

    # ② 执行层
    holding_with_risk = dict(holding)
    holding_with_risk["_corr_risk"] = risk_check
    sizing = _sizing_layer(fund_code, user_id, holding_with_risk, total_assets)
    suggested_amount = sizing.get("suggested_amount", 0)
    # 若 sizing 未含相关性降权且风控要求，应用风控降权
    if require_risk and sizing.get("source") != "smart_add":
        dw = risk_check.get("corr_downweight", 1.0)
        if dw < 1.0 and suggested_amount > 0:
            sizing["pre_correlation_amount"] = suggested_amount
            suggested_amount = round(suggested_amount * dw, 2)
            sizing["suggested_amount"] = suggested_amount
            sizing["correlation_downweight"] = dw

    # ④ 融合层：置信度 + verdict
    # 机会雷达评分：若有该主题的最新机会卡则取其分，否则用估值百分位折算
    opp_score = _lookup_opportunity_score(holding)
    # P1-R5：取当前 regime，bear 时置信度打折
    try:
        from services.advisor.opportunity_engine import _get_market_state_cached
        regime = _get_market_state_cached().get("regime")
    except Exception:
        regime = None
    # P1-R4：取该标的对应机会卡的 IC 加权置信度（ic_confidence 为 None 时不融合）
    ic_confidence = _lookup_opportunity_ic_confidence(holding)
    confidence = compute_confidence(opp_score, zscore_level, risk_check.get("flags"),
                                    min_confidence, regime, ic_confidence)
    verdict = decide_verdict(zscore_level, confidence, min_confidence)

    # ⑤ 闭环层：止盈计划（P1-S5：传 shares + buy_date 启用扣赎回费净收益计算）
    exit_plan = None
    if cost_price:
        try:
            from services.advisor.exit_loop import build_exit_plan
            exit_plan = build_exit_plan(
                cost_price,
                holding.get("fund_category"),
                shares=holding.get("shares"),
                buy_date=holding.get("buy_date") or holding.get("price_updated_at"),
            )
        except Exception as e:
            logger.debug(f"[pipeline] 止盈计划生成失败: {e}")

    evidence = []
    if valuation_signal and valuation_signal.get("zscore") is not None:
        evidence.append({"type": "valuation", "summary": f"z-score={valuation_signal['zscore']}（{zscore_level}）", "source": "valuation_engine"})
    if valuation_percentile is not None:
        evidence.append({"type": "valuation", "summary": f"估值百分位 {valuation_percentile}%", "source": "index_valuations"})
    for flag in (risk_check.get("flags") or []):
        evidence.append({"type": "risk", "summary": flag, "source": "portfolio_risk"})

    layers = {"discovery": discovery, "sizing": sizing, "risk": risk_check}

    # 写入决策账本
    decision_id = None
    try:
        from db import create_decision
        decision_id = create_decision(
            decision_type="opportunity_buy" if verdict == "can_buy" else "watch",
            source_module="agent_pipeline",
            fund_code=fund_code,
            user_id=user_id,
            fund_name=fund_name,
            index_code=index_code,
            signal_snapshot={
                "valuation_signal": valuation_signal,
                "valuation_percentile": valuation_percentile,
                "opportunity_score": opp_score,
                "entry_price": holding.get("current_price"),
                "current_price": holding.get("current_price"),
                "cost_price": cost_price,
            },
            confidence=confidence,
            verdict=verdict,
            suggested_amount=suggested_amount,
            status="suggested",
            risk_check=risk_check,
            exit_plan=exit_plan,
            review_days=review_days if verdict != "avoid" else None,
        )
    except Exception as e:
        logger.warning(f"[pipeline] 决策账本写入失败 {fund_code}: {e}")

    card = build_decision_card(
        fund_code=fund_code, fund_name=fund_name, verdict=verdict, confidence=confidence,
        suggested_amount=suggested_amount, valuation_signal=valuation_signal,
        valuation_percentile=valuation_percentile, risk_check=risk_check,
        exit_plan=exit_plan, evidence=evidence, layers=layers,
        theme=holding.get("index_name"), decision_id=decision_id,
    )
    return card


def _lookup_opportunity_score(holding: dict) -> float | None:
    """尝试取该标的相关主题的最新机会卡评分（无则 None）。"""
    try:
        from db.opportunities import list_opportunities
        index_name = holding.get("index_name") or ""
        if not index_name:
            return None
        opps = list_opportunities(limit=20)
        for o in opps:
            theme = o.get("theme") or ""
            if theme and theme in index_name:
                return o.get("opportunity_score")
    except Exception:
        pass
    return None


def _lookup_opportunity_ic_confidence(holding: dict) -> float | None:
    """P1-R4：取该标的相关主题最新机会卡的 IC 加权置信度。

    逻辑：
    - 查 theme_opportunity_backtests 表中该主题最新记录的 dim_scores_json
    - 调用 opportunity_engine._calc_ic_confidence 重算（IC 值会随回测样本更新）
    - 开关关闭或无数据 → 返回 None（compute_confidence 不融合）
    """
    try:
        from db.config import get_config_bool
        if not get_config_bool("opportunity.ic.enabled", False):
            return None
        index_name = holding.get("index_name") or ""
        if not index_name:
            return None
        from db.opportunities import list_opportunities
        opps = list_opportunities(limit=20)
        target_theme = None
        for o in opps:
            theme = o.get("theme") or ""
            if theme and theme in index_name:
                target_theme = theme
                break
        if not target_theme:
            return None
        # 查该主题最新的 backtest 记录中的 dim_scores_json
        from db._conn import _get_conn
        import json as _json
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT dim_scores_json FROM theme_opportunity_backtests "
                "WHERE theme = ? AND dim_scores_json IS NOT NULL "
                "ORDER BY created_at DESC LIMIT 1",
                (target_theme,),
            ).fetchone()
        finally:
            conn.close()
        if not row or not row["dim_scores_json"]:
            return None
        dim_scores = _json.loads(row["dim_scores_json"])
        from services.advisor.opportunity_engine import _calc_ic_confidence
        return _calc_ic_confidence(dim_scores)
    except Exception as e:
        logger.debug(f"[pipeline] IC 置信度查询失败: {e}")
        return None
