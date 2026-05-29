"""Agent 相关的 Pydantic 模型。"""

from pydantic import BaseModel


class CreateAgentRequest(BaseModel):
    name: str
    description: str = ""
    system_prompt: str
    knowledge_scope: str = ""
    icon: str = "robot"


class UpdateAgentRequest(BaseModel):
    name: str = None
    description: str = None
    system_prompt: str = None
    knowledge_scope: str = None
    icon: str = None


class GeneratePromptRequest(BaseModel):
    name: str = ""
    description: str = ""
    current_prompt: str = ""
    mode: str = "optimize"
