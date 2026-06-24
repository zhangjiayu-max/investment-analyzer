"""Pydantic 输入校验模型 — 覆盖关键金融数据端点。"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional


class TransactionCreate(BaseModel):
    """创建交易记录"""
    fund_code: str = Field(..., min_length=6, max_length=20, description="基金代码")
    transaction_type: str = Field(..., pattern=r"^(buy|sell|convert|dividend)$", description="交易类型")
    amount: float = Field(..., gt=0, description="金额")
    transaction_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$", description="交易日期 YYYY-MM-DD")
    shares: Optional[float] = Field(None, gt=0, description="份额")
    price: Optional[float] = Field(None, gt=0, description="净值")
    holding_id: Optional[int] = Field(None, gt=0, description="持仓ID")
    notes: Optional[str] = Field(None, max_length=500, description="备注")
    fund_name: Optional[str] = Field(None, max_length=100, description="基金名称")
    account: Optional[str] = Field(None, max_length=50, description="账户")

    @field_validator("fund_code")
    @classmethod
    def validate_fund_code(cls, v: str) -> str:
        if not v.replace(".", "").replace("-", "").isalnum():
            raise ValueError("基金代码格式错误")
        return v.strip()


class TransactionConfirm(BaseModel):
    """确认交易"""
    confirmed_price: float = Field(..., gt=0, description="确认净值")
    confirmed_shares: Optional[float] = Field(None, gt=0, description="确认份额")
    confirmed_amount: Optional[float] = Field(None, gt=0, description="确认金额")
    target_fund_code: Optional[str] = Field(None, max_length=20, description="目标基金代码（转换）")
    target_fund_name: Optional[str] = Field(None, max_length=100, description="目标基金名称")
    fee: float = Field(0, ge=0, description="手续费")


class HoldingCreate(BaseModel):
    """创建持仓"""
    fund_code: str = Field(..., min_length=6, max_length=20)
    fund_name: str = Field(..., min_length=1, max_length=100)
    shares: float = Field(..., gt=0, description="份额")
    cost_price: float = Field(..., gt=0, description="成本价")
    current_price: Optional[float] = Field(None, gt=0, description="当前净值")
    account: Optional[str] = Field(None, max_length=50)


class HoldingUpdate(BaseModel):
    """更新持仓"""
    fund_name: Optional[str] = Field(None, max_length=100)
    shares: Optional[float] = Field(None, gt=0)
    cost_price: Optional[float] = Field(None, gt=0)
    current_price: Optional[float] = Field(None, gt=0)
    account: Optional[str] = Field(None, max_length=50)


class DecisionCreate(BaseModel):
    """创建决策"""
    decision_type: str = Field(..., pattern=r"^(buy|sell|hold|adjust|rebalance)$")
    fund_code: Optional[str] = Field(None, max_length=20)
    fund_name: Optional[str] = Field(None, max_length=100)
    content: str = Field(..., min_length=1, max_length=2000, description="决策内容")
    reason: Optional[str] = Field(None, max_length=1000, description="决策理由")
    confidence: Optional[float] = Field(None, ge=0, le=1, description="置信度 0-1")
    source: Optional[str] = Field(None, max_length=50, description="来源")


class DecisionAction(BaseModel):
    """决策行动"""
    action_type: str = Field(..., pattern=r"^(execute|reject|defer)$")
    notes: Optional[str] = Field(None, max_length=500)


class FeedbackCreate(BaseModel):
    """反馈"""
    rating: int = Field(..., ge=1, le=5, description="评分 1-5")
    comment: Optional[str] = Field(None, max_length=1000)
    category: Optional[str] = Field(None, max_length=50)


class ChatMessage(BaseModel):
    """对话消息"""
    content: str = Field(..., min_length=1, max_length=10000, description="消息内容")
    conversation_id: Optional[int] = Field(None, gt=0)
    agent_id: Optional[int] = Field(None, gt=0)


class CompareDiffRequest(BaseModel):
    """对比分析"""
    fund_codes: list[str] = Field(..., min_length=2, max_length=10, description="基金代码列表")
    period: Optional[str] = Field("1y", pattern=r"^(3m|6m|1y|3y|5y)$")


class RollingReturnRequest(BaseModel):
    """滚动收益分析"""
    fund_code: Optional[str] = Field(None, max_length=20)
    window: int = Field(90, ge=7, le=365, description="窗口天数")
    period: str = Field("3y", pattern=r"^(1y|3y|5y)$")


class EvalCaseCreate(BaseModel):
    """评测用例"""
    name: str = Field(..., min_length=1, max_length=200)
    category: str = Field(..., max_length=50)
    input_query: str = Field(..., min_length=1, max_length=5000)
    expected_behavior: Optional[str] = Field(None, max_length=2000)
    scoring_rubric: Optional[str] = Field(None, max_length=2000)


class PromptVersionCreate(BaseModel):
    """Prompt版本"""
    agent_id: int = Field(..., gt=0)
    system_prompt: str = Field(..., min_length=10, max_length=50000)
    version_notes: Optional[str] = Field(None, max_length=500)
