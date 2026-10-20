# 指数分析取消故障修复与验证

## 故障证据

- 原始记录：`log_b702670a8e18`，分析历史 #218，创业板指 399006。
- 2026-10-20 05:51:04，WatchFiles 检测到 Python 测试文件变更，触发热重载。`app.shutdown()` 遍历 `asyncio.all_tasks()` 直接取消所有任务，导致指数分析等待被取消，还误取消了 ASGI 生命周期任务。
- 05:52:17，同一 trace 的同步模型调用仍然返回，消耗 23,317 tokens，但等待者已经取消，报告未保存。
- 当天更早的 #215、#216、#217 是独立的模型连接错误，异常链为 `APIConnectionError → ConnectError → Connection refused`，不能混同为取消故障。既有日志不能进一步确认被拒绝的具体网络节点，本次真实请求验证模型连接已恢复。

## 修复

- 应用启动的常驻后台任务显式注册；关闭时仅取消并等待这些任务，不触碰 ASGI/uvicorn 任务。
- 关闭资源前等待当前指数分析完成，最多 195 秒，覆盖并行数据检索最多 12 秒和模型分析最多 180 秒。
- 超期后仅取消尚未完成的指数任务，写入明确的“服务重启”原因，并等待分析历史、分析日志、异步任务的错误状态落库。
- 保留工作区此前已有的模型重试与错误摘要改动，以及其他无关改动。

## 自动化验证

新增测试先复现旧关闭逻辑造成的 `CancelledError`，修复后通过。

```sh
python3 -m pytest -q backend/tests/test_analysis_shutdown.py backend/tests/test_index_analysis_contract.py backend/tests/test_analysis_text_completion.py backend/tests/test_analysis_error_diagnostics.py backend/tests/test_analysis_route_health.py backend/tests/test_async_tasks_stale.py backend/tests/test_execution_deadlines.py backend/tests/test_llm_cost_controls.py --tb=short
```

结果：162 passed，12 个既有 Pydantic/FastAPI 废弃提示。独立代码审查未发现阻塞问题，独立重跑新增用例 3 passed。

## 真实模型与热重载验证

- 通过本机运行中的 API 触发创业板指市净率分析，在证据收集结束、模型仍执行时触碰 `backend/app.py` 触发真实 WatchFiles 热重载。
- 05:57:27，服务记录“服务关闭前等待 1 个指数分析完成（最多 195 秒）”。
- 新分析历史 #219、异步任务 #482、日志 `log_bf3d5c4c803d` 均成功结束，状态为 `done`。
- 耗时 133,831 ms，报告 4,443 字符，三处 token_usage 均为 23,605。
- 日志确认 `Application shutdown complete.`；重启后 `GET /api/analysis/history/219` 返回 `done` 和完整 4,443 字符报告。

范围：保护正常优雅关闭和此次复现的热重载；操作系统强杀进程无法执行关闭回调。旧 #218 保留真实失败历史，新 #219 提供重跑后的报告。
