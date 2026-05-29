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
