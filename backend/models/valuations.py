"""估值数据相关的 Pydantic 模型。"""

from pydantic import BaseModel


class ParseAndSaveRequest(BaseModel):
    path: str
    model_type: str = "mimo"
    source_url: str | None = None
    snapshot_date: str | None = None


class ParseBatchRequest(BaseModel):
    paths: list[str]
    model_type: str = "mimo"


class ParseDDRequest(BaseModel):
    path: str
    model_type: str = "mimo"


class ParseDDBatchRequest(BaseModel):
    paths: list[str]
    model_type: str = "mimo"
