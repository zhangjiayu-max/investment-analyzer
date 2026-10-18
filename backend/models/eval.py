"""评测集 Pydantic 模型"""

import json
from typing import Literal
from pydantic import BaseModel, Field, field_validator


class CreateEvalCaseRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    analysis_type: str
    input_params: str = "{}"
    description: str = ""
    expected_quality: str = ""

    @field_validator('input_params')
    @classmethod
    def valid_params(cls, value):
        parsed = json.loads(value)
        if not isinstance(parsed,dict):
            raise ValueError('参数必须为JSON对象')
        return value


class UpdateEvalCaseRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    analysis_type: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = None
    input_params: str | None = None
    expected_quality: str | None = None
    is_active: Literal[0, 1] | None = None

    @field_validator('input_params')
    @classmethod
    def valid_params(cls, value):
        if value is not None and not isinstance(json.loads(value), dict):
            raise ValueError('参数必须为JSON对象')
        return value


class BadCaseToEvalRequest(BaseModel):
    """从 Bad Case 转化为 Eval Case 的请求。"""
    source: Literal["analysis", "chat"]
    source_id: int = Field(gt=0)
    name: str = ""       # 用例名称（可选，默认自动生成）
