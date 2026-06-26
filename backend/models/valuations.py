"""估值数据相关的 Pydantic 模型。"""

from pydantic import BaseModel
from config import IMAGE_PARSER_MODEL_TYPE


class ParseAndSaveRequest(BaseModel):
    path: str
    model_type: str = IMAGE_PARSER_MODEL_TYPE
    source_url: str | None = None
    snapshot_date: str | None = None


class ParseBatchRequest(BaseModel):
    paths: list[str]
    model_type: str = IMAGE_PARSER_MODEL_TYPE


class ParseDDRequest(BaseModel):
    path: str
    model_type: str = IMAGE_PARSER_MODEL_TYPE


class ParseDDBatchRequest(BaseModel):
    paths: list[str]
    model_type: str = IMAGE_PARSER_MODEL_TYPE
