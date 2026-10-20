# 对话 205：MIMO 直连与连接失败终止

## 原因和方案

对话 `205`、trace `8a562418-e6a` 在 19:05:40–19:06:30 失败。模型请求经过 HTTP 代理，毫秒级返回 `Connection refused`，不是专家阶段超时。macOS 系统代理为 `127.0.0.1:1082`；当前 Shadowrocket 代理进程启动于 19:06:42。httpx 默认通过 `urllib.request.getproxies()` 读取环境变量和 macOS 系统代理。

用户已确认修复：MIMO 通过现有 OpenAI 兼容地址 `/v1/chat/completions` 直接连接。Anthropic 地址提供另一种请求协议，与是否经过本机代理无关。

选用客户端级 `DefaultHttpxClient(trust_env=False)`：文本主用、配置兜底、复用主用的仲裁、流式及图片识别均直连。不修改操作系统代理或其他服务的网络设置。相比仅添加 `NO_PROXY`，客户端级隔离不依赖启动环境；相比迁移 Anthropic 协议，不需要改变现有消息、工具和 thinking 处理。

专家执行保留原始连接异常；并行、顺序、无计划降级路径中，如无任何成功结果且出现模型连接故障，传播异常到流水线终态。普通对话发出终止事件，由编排器转换为错误；澄清续答直接发出路由可落库的终态错误事件，外层不再启动 ReAct。已有有效结果时保留局部结果继续原有综合逻辑；单次调用采用有限重试，当前最多尝试三次。

## 实施和验证

- [x] 增加回归：环境代理与模拟 macOS 系统代理都指向不可用端口，本地 HTTP 模型服务仍能收到文本/流式/仲裁/兜底/视觉请求。
- [x] 先运行回归，确认复现代理连接失败及全专家失败后的重复编排：初始 10 项均失败。
- [x] 为运行时三个 OpenAI 构造入口传入禁用系统代理的 HTTP 客户端；保留原有 timeout、SDK 连接池和重试策略。
- [x] 为三种专家执行路径保留连接异常；无有效结果时终止，覆盖普通和续答入口，并验证部分成功报告仍保留。
- [x] 运行代理回归、预算/错误诊断/视觉/流水线质量回归；用损坏代理环境下的真实 MIMO 小请求验证直连。

验证命令：`python3 -m pytest -q backend/tests/test_llm_direct_connection.py backend/tests/test_execution_deadlines.py backend/tests/test_analysis_error_diagnostics.py backend/tests/test_vision_credentials.py backend/tests/test_pipeline_quality_contract.py backend/tests/test_pipeline_followup_budget.py backend/tests/test_llm_cost_controls.py backend/tests/test_valuation_image_regressions.py --tb=short`。结果：104 passed，2 条既有 Pydantic 弃用提示。

真实连通性：将进程 HTTP_PROXY/HTTPS_PROXY/ALL_PROXY 设为 `http://127.0.0.1:1`，用更新后的主客户端向 `https://token-plan-cn.xiaomimimo.com/v1/` 请求最小文本响应，`trust_env=False`，实际模型 `mimo-v2.6-pro` 返回 `OK`，耗时 19.50 秒。此请求仅验证连通性，不代表完整投资分析延迟。后端已自动重载，`/openapi.json` 返回 HTTP 200。

本次不修改对话 205 的失败记录，不自动重新执行原问题，不提交交易。
