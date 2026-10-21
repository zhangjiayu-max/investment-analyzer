"""编排检查点的可达性回归。

对话长期存在一个隐蔽失效：`_save_checkpoint("experts_done")` 被写在
`for turn` 循环**之外**，而正常完成路径在循环内 `if ntc_done: return` 处直接退出，
永远走不到那行。结果 `orchestration_checkpoints` 长期为 0 行，
"中断后恢复"在主流路径上是空转。

这两个测试分别锁住「写入通路可用」和「调用点在正常路径上可达」。
"""
import ast
import pathlib


def _orchestrate_stream_ast():
    src = pathlib.Path(__file__).resolve().parents[1].joinpath(
        "agent/core/orchestrator.py").read_text()
    tree = ast.parse(src)
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "orchestrate_stream")
    loop = next(n for n in ast.walk(fn)
                if isinstance(n, ast.For) and isinstance(n.target, ast.Name)
                and n.target.id == "turn")
    return fn, loop


def test_experts_done_checkpoint_is_called_inside_react_loop():
    """调用点必须在循环内：正常完成在循环里 return，循环外的存档拿不到专家结果。"""
    fn, loop = _orchestrate_stream_ast()
    calls = [n.lineno for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "_checkpoint_experts_done"]
    in_loop = [line for line in calls if loop.lineno <= line <= loop.end_lineno]
    assert in_loop, (
        f"checkpoint 未在 ReAct 循环内调用（调用行={calls}，循环={loop.lineno}-{loop.end_lineno}），"
        "正常完成路径会跳过存档")


def test_checkpoint_call_precedes_the_early_return():
    """在 `if ntc_done: return` 之前必须先存档，否则该分支仍然跳过。"""
    fn, loop = _orchestrate_stream_ast()
    checkpoint_lines = [n.lineno for n in ast.walk(loop)
                        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                        and n.func.id == "_checkpoint_experts_done"]
    # ntc_done 分支的 return 是循环内唯一「专家已就绪后的正常退出」
    return_lines = [n.lineno for n in ast.walk(loop)
                    if isinstance(n, ast.Return) and loop.lineno <= n.lineno <= loop.end_lineno]
    assert checkpoint_lines and return_lines
    assert min(checkpoint_lines) < max(return_lines)


def test_checkpoint_write_path_persists_row():
    """写入通路本身可用：表存在、序列化不炸、能按 conv+msg 取回。"""
    from agent.core.orchestrator import _save_checkpoint, _load_checkpoint

    state = {"specialist_results": [{"agent_key": "risk_assessor", "analysis": "风险分析"}],
             "all_tool_calls": [], "complexity": "complex"}
    _save_checkpoint(9001, 9002, "experts_done", state)

    loaded = _load_checkpoint(9001, 9002)
    assert loaded is not None
    assert loaded["phase"] == "experts_done"
    assert loaded["specialist_results"][0]["agent_key"] == "risk_assessor"


def test_checkpoint_skips_empty_identity():
    """缺 conv/message id 时静默返回，不抛异常、不写脏行。"""
    from agent.core.orchestrator import _save_checkpoint

    _save_checkpoint(0, 0, "experts_done", {"specialist_results": []})
    _save_checkpoint(None, None, "experts_done", {"specialist_results": []})
