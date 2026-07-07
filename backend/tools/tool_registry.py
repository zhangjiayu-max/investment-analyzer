"""全局工具注册中心 — 启动时初始化一次，运行时只读。

用途：
- 集中管理所有 33 个工具定义
- 按 agent 的工具列表过滤，返回 OpenAI function calling 格式
- 启动时同步到 tool_registry 表（审计）
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class ToolDefinition:
    """单个工具定义"""

    __slots__ = ("name", "description", "parameters", "category", "enabled", "timeout")

    def __init__(self, name: str, description: str = "", parameters: dict = None,
                 category: str = "general", enabled: bool = True, timeout: int = 30):
        self.name = name
        self.description = description
        self.parameters = parameters or {}
        self.category = category
        self.enabled = enabled
        self.timeout = timeout


class ToolRegistry:
    """全局工具注册中心 — 单例模式"""

    _instance: Optional["ToolRegistry"] = None
    _tools: dict[str, ToolDefinition] = {}
    _openai_tools: list[dict] = []

    @classmethod
    def get_instance(cls) -> "ToolRegistry":
        if cls._instance is None:
            raise RuntimeError("ToolRegistry 未初始化，请先调用 ToolRegistry.initialize()")
        return cls._instance

    @classmethod
    def initialize(cls, db_path: str = None) -> "ToolRegistry":
        """启动时调用一次，加载所有工具并同步到 DB。"""
        if cls._instance is not None:
            return cls._instance

        cls._instance = cls()
        cls._instance._load_builtin_tools()
        if db_path:
            cls._instance._sync_to_db(db_path)
        logger.info(f"ToolRegistry 初始化完成，已注册 {len(cls._instance._tools)} 个工具")
        return cls._instance

    def _load_builtin_tools(self):
        """从 tools/__init__.py 的 TOOLS 列表加载所有内置工具。"""
        from tools import TOOLS
        for t in TOOLS:
            fn = t["function"]
            self._tools[fn["name"]] = ToolDefinition(
                name=fn["name"],
                description=fn.get("description", ""),
                parameters=fn.get("parameters", {}),
            )
        self._rebuild_openai_tools()

    def _sync_to_db(self, db_path: str):
        """将工具注册到 tool_registry 表（新增工具自动 INSERT OR IGNORE）。"""
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        for name, td in self._tools.items():
            conn.execute(
                "INSERT OR IGNORE INTO tool_registry (name, category, description) VALUES (?, ?, ?)",
                (name, td.category, td.description),
            )
        conn.commit()
        conn.close()

    def _rebuild_openai_tools(self):
        """重建 OpenAI function calling 格式的工具列表。"""
        self._openai_tools = []
        for td in self._tools.values():
            if td.enabled:
                self._openai_tools.append({
                    "type": "function",
                    "function": {
                        "name": td.name,
                        "description": td.description,
                        "parameters": td.parameters,
                    },
                })

    def get_tools_for_agent(self, tool_names: list[str]) -> list[dict]:
        """根据 agent 的工具名列表，返回过滤后的 OpenAI 格式工具列表。"""
        name_set = set(tool_names)
        return [t for t in self._openai_tools
                if t["function"]["name"] in name_set]

    def get_all_tools(self) -> list[dict]:
        """返回所有已启用的工具（OpenAI 格式）。"""
        return self._openai_tools

    def list_tool_names(self) -> list[str]:
        """返回所有工具名称列表。"""
        return [t["function"]["name"] for t in self._openai_tools]

    def get_tool(self, name: str) -> Optional[ToolDefinition]:
        return self._tools.get(name)

    def enable_tool(self, name: str):
        td = self._tools.get(name)
        if td:
            td.enabled = True
            self._rebuild_openai_tools()

    def disable_tool(self, name: str):
        td = self._tools.get(name)
        if td:
            td.enabled = False
            self._rebuild_openai_tools()