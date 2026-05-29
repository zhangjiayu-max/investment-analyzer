"""持仓管理相关的 Pydantic 模型。"""

from pydantic import BaseModel


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
    account: str = "花无缺"


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


class CreateTransactionRequest(BaseModel):
    fund_code: str
    transaction_type: str  # 'buy' | 'sell' | 'dividend'
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


class ConfirmTransactionRequest(BaseModel):
    confirmed_price: float
    confirmed_shares: float | None = None
    confirmed_amount: float | None = None


class CreateAlertRequest(BaseModel):
    alert_type: str  # risk_warning | add_position | reduce_position | news_impact | valuation_alert
    title: str
    content: str = None
    severity: str = "info"  # info | warning | danger
    related_fund_code: str = None
    related_fund_name: str = None
    source: str = None


class TagRequest(BaseModel):
    tag: str


class AdjustCashRequest(BaseModel):
    amount: float
    mode: str = "add"  # "add" 存入/支出, "set" 直接设置
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
    scenario: str  # 'market_drop' | 'repair_to_median' | 'repair_to_opportunity'
    parameter: float | None = None  # 市场下跌场景的跌幅百分比
