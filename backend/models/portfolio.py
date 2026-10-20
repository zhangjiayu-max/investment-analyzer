"""持仓管理相关的 Pydantic 模型。"""

from typing import Literal
from pydantic import BaseModel, Field, model_validator


class CreateHoldingRequest(BaseModel):
    fund_code: str
    fund_name: str
    shares: float = 0
    cost_price: float = None
    current_price: float = None
    index_code: str = None
    index_name: str = None
    buy_date: str = None
    notes: str = None
    account: str = ""  # 空字符串表示使用配置中的默认账户


class UpdateHoldingRequest(BaseModel):
    fund_name: str = None
    shares: float = None
    cost_price: float = None
    current_price: float = None
    index_code: str = None
    index_name: str = None
    buy_date: str = None
    notes: str = None
    account: str = None
    # 2026-08-03 修复：开放基准成本字段编辑，用于修复历史脏数据导致的 profit_rate 虚高
    # 当持仓 has_base_position=1 但 base_total_cost 缺失时，profit_rate 会被错误放大
    # 用户可通过编辑接口补全 base_total_cost（= 初始投入本金）+ base_shares（= 初始份额）
    base_shares: float = None
    base_total_cost: float = None
    has_base_position: int = None


class CreateTransactionRequest(BaseModel):
    fund_code: str
    transaction_type: str  # 'buy' | 'sell' | 'dividend' | 'convert'
    amount: float = 0      # 买入金额 / 卖出时可为0（pending）
    transaction_date: str
    shares: float | None = None
    price: float | None = None
    holding_id: int | None = None
    notes: str | None = None
    status: str | None = None     # 'pending' | 'confirmed' | None(默认confirmed)
    submitted_shares: float | None = None  # 卖出时提交的份额
    submitted_amount: float | None = None  # 买入时提交的金额
    transaction_time: str | None = None    # HH:MM 格式，如 14:30
    fund_name: str | None = None           # 新建买入时基金名称
    account: str | None = None             # 账户：花无缺/小鱼儿
    funding_source: Literal['internal', 'external'] | None = None
    cash_account: str | None = None        # 明确资金归属，历史交易不追扣


class ConfirmTransactionRequest(BaseModel):
    confirmed_price: float = Field(gt=0, allow_inf_nan=False)
    confirmed_shares: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    confirmed_amount: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    target_fund_code: str | None = None
    target_fund_name: str | None = None
    target_price: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    target_shares: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    fee: float = Field(default=0, ge=0, allow_inf_nan=False)


class SellPreviewRequest(BaseModel):
    shares: float = Field(gt=0, allow_inf_nan=False)  # 拟卖出份额


class CreateAlertRequest(BaseModel):
    alert_type: str  # risk_warning | add_position | reduce_position | news_impact | valuation_alert
    title: str
    content: str = None
    severity: str = "info"  # info | warning | danger
    related_fund_code: str = None
    related_fund_name: str = None
    source: str = None


class AcknowledgeAlertRequest(BaseModel):
    status: str  # acknowledged（已采纳）| ignored（已忽略）


class TagRequest(BaseModel):
    tag: str


class AdjustCashRequest(BaseModel):
    amount: float = Field(allow_inf_nan=False)
    mode: Literal['add', 'set'] = "add"  # "add" 存入/支出, "set" 直接设置
    user_id: str = "default"


class PortfolioAiAnalysisRequest(BaseModel):
    question: str = ""


class FeedbackRequest(BaseModel):
    feedback: str
    note: str = ""


class PanoramaAnalysisRequest(BaseModel):
    """全景诊断请求。"""
    pass  # 无参数，基于当前持仓分析


class DeepDiveRequest(BaseModel):
    """单基金深度分析请求。"""
    pass  # holding_id 通过路径参数传入


class TradeReviewRequest(BaseModel):
    """交易复盘请求。"""
    start_date: str | None = None
    end_date: str | None = None


class WhatIfRequest(BaseModel):
    """情景推演请求。"""
    scenario: Literal['market_drop', 'repair_to_median', 'repair_to_opportunity',
                      'rate_cut', 'rate_hike', 'policy_stimulus', 'liquidity_tighten', 'custom']
    parameter: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    custom_scenario: str | None = None  # 自定义情景描述（scenario='custom'时使用）

    @model_validator(mode='after')
    def validate_scenario(self):
        if self.scenario == 'custom' and not (self.custom_scenario or '').strip():
            raise ValueError('请填写自定义情景描述')
        if self.scenario == 'market_drop' and self.parameter is not None and self.parameter > 100:
            raise ValueError('市场跌幅不能超过 100%')
        return self


class StressTestRequest(BaseModel):
    """确定性压力测试请求。"""
    scenario: str = "market_drop_20"
    custom_shocks: dict | None = None  # 自定义冲击系数 {"cash": 0, "equity": -0.3}
