"""AI 市场分析 Pydantic 模型"""

from pydantic import BaseModel


class AnalysisRunRequest(BaseModel):
    index_code: str = ""
    index_name: str = ""
    agent_id: int | None = None  # 缺省时按名称加载，避免不同数据库 ID 错位


class AnalysisAgentUpdateRequest(BaseModel):
    name: str = None
    description: str = None
    system_prompt: str = None
    is_active: int = None
