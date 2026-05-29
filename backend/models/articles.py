"""文章相关的 Pydantic 模型。"""

from pydantic import BaseModel


class ExtractUrlRequest(BaseModel):
    url: str
