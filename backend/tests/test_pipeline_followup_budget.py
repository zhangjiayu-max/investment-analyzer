"""反思自纠错和辩论阶段的执行预算回归。"""
import threading
import time
import importlib
import pytest


def test_reflection_rerun_has_bounded_wait_and_cancel(monkeypatch):
    p=importlib.import_module('agent.core.pipeline')
    from agent.state.pipeline_state import PipelineState
    multi_agent=importlib.import_module('agent.core.multi_agent')
    release=threading.Event()
    monkeypatch.setattr(p,'get_config_int',lambda key,default:.02 if key=='agent.specialist_timeout' else default)
    monkeypatch.setattr(multi_agent,'run_specialist',lambda **kw:release.wait(.3))
    started=time.monotonic()
    try:
        with pytest.raises(TimeoutError):
            p._maybe_rerun_specialist(PipelineState(refined_query='q'),'q',
                {'specialists':[{'agent_key':'a','agent':'专家A'}]},
                {'confidence_adjustment':-.5,'missing_perspectives':['专家A']},None,'t',cancel_event=threading.Event())
        assert time.monotonic()-started<.15
    finally:
        release.set()


def test_followup_phase_passes_shared_deadline_and_cancel_to_worker(monkeypatch):
    p=importlib.import_module('agent.core.pipeline')
    from infra.execution_budget import remaining_seconds,ExecutionCancelled
    monkeypatch.setattr(p,'get_config_int',lambda key,default:.02)
    observed=[]
    def phase():
        observed.append(remaining_seconds())
        time.sleep(.03)
        remaining_seconds()
        pytest.fail('second LLM request must not start after phase deadline')
    with pytest.raises(TimeoutError):
        p._run_followup_phase(phase,(),threading.Event(),'pipeline.debate_timeout')
    assert observed and observed[0]<=.02
    cancelled=threading.Event();cancelled.set()
    with pytest.raises((ExecutionCancelled,p._PipelineCancelled)):
        p._run_followup_phase(lambda:pytest.fail('cancelled phase submitted'),(),cancelled,'pipeline.debate_timeout')
