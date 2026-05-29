"""AI 市场分析 Pydantic 模型"""

from pydantic import BaseModel


class AnalysisRunRequest(BaseModel):
    index_code: str = ""
    index_name: str = ""
    agent_id: int = 9  # 默认使用"指数深度分析师"


class AnalysisAgentUpdateRequest(BaseModel):
    name: str = None
    description: str = None
    system_prompt: str = None
    is_active: int = None
