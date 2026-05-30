"""评测集 Pydantic 模型"""

from pydantic import BaseModel


class CreateEvalCaseRequest(BaseModel):
    name: str
    analysis_type: str
    input_params: str = "{}"
    description: str = ""
    expected_quality: str = ""


class UpdateEvalCaseRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    input_params: str | None = None
    expected_quality: str | None = None
    is_active: int | None = None


class BadCaseToEvalRequest(BaseModel):
    """从 Bad Case 转化为 Eval Case 的请求。"""
    source: str          # "analysis" | "chat"
    source_id: int       # 原始记录 ID
    name: str = ""       # 用例名称（可选，默认自动生成）
