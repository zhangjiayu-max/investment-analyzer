"""kyc 子包 — 从 kyc 模块重导出所有符号。

保证旧的 ``from agent.kyc import xxx`` 继续可用。
"""
from agent.kyc.kyc import *  # noqa: F401,F403
