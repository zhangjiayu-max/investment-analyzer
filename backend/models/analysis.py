"""AI 市场分析 Pydantic 模型"""

from typing import Literal

from pydantic import BaseModel


class AnalysisRunRequest(BaseModel):
    index_code: str = ""
    index_name: str = ""
    metric_type: Literal["市盈率", "市净率", "市销率", "市销率TTM", "股息率"] = "市净率"
    agent_id: int | None = None  # 缺省时按名称加载，避免不同数据库 ID 错位


class AnalysisAgentUpdateRequest(BaseModel):
    name: str = None
    description: str = None
    system_prompt: str = None
    is_active: int = None
